"""
agents/notifications/telegram_agent.py — Telegram Notification Agent
Karbot Rage! | WallStRobotics

Handles all outbound Telegram messaging and operator permission requests.
Sits on the event bus; does not touch the trading hot path.

Design decisions:
  - Polling (getUpdates) over webhook: VPS does not expose public inbound
    ports. Polling is consistent with that posture, zero extra infra.
  - Single-operator reply resolution: any "yes"/"no" reply resolves the
    oldest pending permission request (FIFO). Only one operator exists in
    Phase 1 — multi-request concurrency is not a real scenario.
    Revisit when Regulatory Intelligence Agent generates concurrent requests.
  - Rate limit: 1 outbound message per second (well below Telegram's 30/s
    hard limit — kept low to avoid operator noise).
  - Eastern Time: fixed UTC-4 offset (EDT). Acceptable for May–Nov;
    switches to EST in November but timestamp accuracy is not critical here.
"""

import asyncio
import datetime
import logging
import os
from typing import Dict

import aiohttp

from core.events import (
    EventBus,
    LegFailureEvent,
    RegulatoryAlertEvent,
    RejectedOpportunityEvent,
    TelegramNotificationEvent,
    TelegramPermissionRequestEvent,
    TelegramPermissionResponseEvent,
    TradeExecutedEvent,
)
from karbot.core.config import KarbotConfig

logger = logging.getLogger(__name__)


class TelegramNotificationAgent:
    """
    Full implementation of the Telegram notification layer.

    Subscribes to system events and forwards relevant ones to Telegram.
    Also runs a polling loop to receive operator replies for permission
    requests.

    When config.telegram.enabled is False, all send methods no-op and
    the polling loop does not start. The agent remains startable in all
    modes (required by runner).
    """

    def __init__(self, bus: EventBus, config: KarbotConfig):
        self.bus = bus
        self.config = config
        self._token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        if config.telegram.enabled and (not self._token or not self._chat_id):
            logger.warning(
                "TelegramAgent: enabled=True but TELEGRAM_BOT_TOKEN or "
                "TELEGRAM_CHAT_ID not set — messages will be silently dropped"
            )

        self._outbound_queue: asyncio.Queue = asyncio.Queue()

        # Single-operator request tracking (FIFO resolution)
        self._pending_requests: Dict[str, TelegramPermissionRequestEvent] = {}
        self._request_expiry: Dict[str, float] = {}
        self._last_update_id: int = 0

    def register_subscriptions(self):
        self.bus.subscribe(TelegramNotificationEvent, self._handle_notification)
        self.bus.subscribe(TelegramPermissionRequestEvent, self._handle_permission_request)
        self.bus.subscribe(RegulatoryAlertEvent, self._handle_regulatory_alert)
        self.bus.subscribe(LegFailureEvent, self._handle_leg_failure)
        self.bus.subscribe(TradeExecutedEvent, self._handle_trade_executed)
        self.bus.subscribe(RejectedOpportunityEvent, self._handle_rejected_opportunity)
        logger.info("TelegramAgent subscriptions registered")

    async def run(self):
        if not self.config.telegram.enabled:
            logger.debug("TelegramAgent: disabled — polling inactive, notifications no-op")
            while True:
                await asyncio.sleep(60)

        logger.info("TelegramAgent: enabled — starting outbound queue and polling loop")
        await asyncio.gather(
            self._drain_queue(),
            self._poll_updates(),
        )

    # ── Outbound queue drainer ─────────────────────────────────────────────────

    async def _drain_queue(self):
        """Send queued messages at 1/s to stay well clear of Telegram limits."""
        while True:
            try:
                message = await self._outbound_queue.get()
                await self._send_message(message)
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"TelegramAgent: drain error: {e}")

    async def _send_message(self, text: str):
        if not self._token or not self._chat_id:
            logger.debug(f"TelegramAgent: no credentials — dropped: {text[:80]}")
            return
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={"chat_id": self._chat_id, "text": text},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"TelegramAgent: send failed HTTP {resp.status}")
        except Exception as e:
            logger.warning(f"TelegramAgent: send error: {e}")

    # ── Polling loop ───────────────────────────────────────────────────────────

    async def _poll_updates(self):
        """
        Poll getUpdates every 3 seconds. Dispatches operator text replies
        to _handle_operator_reply. Also checks for expired permission requests
        on each iteration.
        """
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    await self._check_expired_requests()
                    url = f"https://api.telegram.org/bot{self._token}/getUpdates"
                    params = {"offset": self._last_update_id + 1, "timeout": 0}
                    async with session.get(
                        url,
                        params=params,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for update in data.get("result", []):
                                self._last_update_id = max(
                                    self._last_update_id, update["update_id"]
                                )
                                msg = update.get("message", {})
                                if "text" in msg:
                                    await self._handle_operator_reply(msg["text"])
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.warning(f"TelegramAgent: polling error: {e}")

                await asyncio.sleep(3)

    async def _check_expired_requests(self):
        loop = asyncio.get_running_loop()
        now = loop.time()
        for req_id in list(self._request_expiry.keys()):
            if now >= self._request_expiry[req_id]:
                req = self._pending_requests.pop(req_id, None)
                self._request_expiry.pop(req_id, None)
                if req:
                    default_approved = req.default_on_timeout == "approve"
                    await self.bus.publish(TelegramPermissionResponseEvent(
                        request_id=req_id,
                        approved=default_approved,
                        source="timeout",
                    ))
                    logger.info(
                        f"TelegramAgent: request {req_id} timed out — "
                        f"resolved {default_approved} (default)"
                    )

    async def _handle_operator_reply(self, text: str):
        """
        Publish TelegramPermissionResponseEvent for every operator message so
        subscribers (e.g. RegulatoryIntelligenceAgent) can inspect response_text.
        When a FIFO permission request is pending, also resolve it with yes/no.
        """
        reply = text.strip().lower()
        approved = reply in ("yes", "y", "approve", "approved")

        if self._pending_requests:
            req_id = next(iter(self._pending_requests))
            self._pending_requests.pop(req_id)
            self._request_expiry.pop(req_id, None)
            await self.bus.publish(TelegramPermissionResponseEvent(
                request_id=req_id,
                approved=approved,
                source="operator",
                response_text=text,
            ))
            logger.info(
                f"TelegramAgent: operator resolved request {req_id} — approved={approved}"
            )
        else:
            # No pending request — still publish so phrase-checkers can see it
            await self.bus.publish(TelegramPermissionResponseEvent(
                request_id="",
                approved=False,
                source="operator",
                response_text=text,
            ))

    # ── Timestamp ──────────────────────────────────────────────────────────────

    def _et_timestamp(self) -> str:
        utc_now = datetime.datetime.now(datetime.timezone.utc)
        et = utc_now.astimezone(datetime.timezone(datetime.timedelta(hours=-4)))
        return et.strftime("%Y-%m-%d %H:%M ET")

    # ── Event handlers ─────────────────────────────────────────────────────────

    async def _handle_notification(self, event: TelegramNotificationEvent):
        if not self.config.telegram.enabled:
            return
        if event.tier == 1:
            text = f"🚨 KARBOT RAGE! CRITICAL\n{event.message}\n{self._et_timestamp()}"
        else:
            text = f"{event.message}\n{self._et_timestamp()}"
        await self._outbound_queue.put(text)

    async def _handle_permission_request(self, event: TelegramPermissionRequestEvent):
        if not self.config.telegram.enabled:
            return
        loop = asyncio.get_running_loop()
        self._pending_requests[event.request_id] = event
        self._request_expiry[event.request_id] = loop.time() + event.timeout_seconds
        text = (
            f"❓ PERMISSION REQUIRED\n"
            f"From: {event.requesting_agent}\n"
            f"Question: {event.question}\n"
            f"Reply 'yes' to approve or 'no' to deny.\n"
            f"Timeout: {event.timeout_seconds}s (default: {event.default_on_timeout})\n"
            f"{self._et_timestamp()}"
        )
        await self._outbound_queue.put(text)

    async def _handle_regulatory_alert(self, event: RegulatoryAlertEvent):
        """Tier 1 — always sends regardless of tier config (belt and suspenders)."""
        if not self.config.telegram.enabled:
            return
        kw = ", ".join(event.matched_keywords) if event.matched_keywords else "see logs"
        text = (
            f"🚨 KARBOT RAGE! CRITICAL\n"
            f"REGULATORY ALERT\n"
            f"Source: {event.source_name}\n"
            f"Keywords: {kw}\n"
            f"Review logs/regulatory_alerts.txt immediately.\n"
            f"{self._et_timestamp()}"
        )
        await self._outbound_queue.put(text)

    async def _handle_leg_failure(self, event: LegFailureEvent):
        """Tier 1 — always sends."""
        if not self.config.telegram.enabled:
            return
        text = (
            f"🚨 KARBOT RAGE! CRITICAL\n"
            f"LEG FAILURE\n"
            f"Trade: {event.trade_id}\n"
            f"Unwind required: {event.unwind_required}\n"
            f"{self._et_timestamp()}"
        )
        await self._outbound_queue.put(text)

    async def _handle_trade_executed(self, event: TradeExecutedEvent):
        """Tier 2 — respects notify_on_trade flag."""
        if not self.config.telegram.enabled:
            return
        if not self.config.telegram.notify_on_trade:
            return
        prefix = "📋 PAPER TRADE" if event.paper_mode else "✅ TRADE"
        text = (
            f"{prefix}\n"
            f"ID: {event.trade_id}\n"
            f"PnL: ${event.expected_pnl_usd:.2f}\n"
            f"Fees: ${event.total_fee_paid:.2f}\n"
            f"{self._et_timestamp()}"
        )
        await self._outbound_queue.put(text)

    async def _handle_rejected_opportunity(self, event: RejectedOpportunityEvent):
        """Tier 2 — respects notify_on_rejection flag."""
        if not self.config.telegram.enabled:
            return
        if not self.config.telegram.notify_on_rejection:
            return
        text = (
            f"⏭️ SKIPPED\n"
            f"Opportunity: {event.opportunity_id}\n"
            f"Reason: {event.reason}\n"
            f"{self._et_timestamp()}"
        )
        await self._outbound_queue.put(text)


class TelegramAgent(TelegramNotificationAgent):
    """BaseAgent-conforming runner stub. run() delegates to full implementation."""

    async def run(self):
        await super().run()
