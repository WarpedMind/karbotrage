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
from typing import Dict

import aiohttp

from core.events import (
    EventBus,
    FeedHealthEvent,
    LegFailureEvent,
    RejectedOpportunityEvent,
    TelegramNotificationEvent,
    TelegramPermissionRequestEvent,
    TelegramPermissionResponseEvent,
    TradeExecutedEvent,
    TradeResolvedEvent,
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
        self._token = config.secrets.telegram_bot_token
        self._chat_id = config.secrets.telegram_chat_id

        if config.telegram.enabled and (not self._token or not self._chat_id):
            logger.warning(
                "TelegramAgent: enabled=True but credentials not configured "
                "— messages will be silently dropped"
            )

        self._outbound_queue: asyncio.Queue = asyncio.Queue()

        # Single-operator request tracking (FIFO resolution)
        self._pending_requests: Dict[str, TelegramPermissionRequestEvent] = {}
        self._request_expiry: Dict[str, float] = {}
        self._last_update_id: int = 0

        # Feed health transition tracking (platform → last known connected state).
        # Alerts fire only on connected→disconnected or disconnected→connected
        # transitions, not on every FeedHealthEvent while still down.
        self._feed_connected: Dict[str, bool] = {}

    def register_subscriptions(self):
        self.bus.subscribe(TelegramNotificationEvent, self._handle_notification)
        self.bus.subscribe(TelegramPermissionRequestEvent, self._handle_permission_request)
        self.bus.subscribe(LegFailureEvent, self._handle_leg_failure)
        self.bus.subscribe(TradeExecutedEvent, self._handle_trade_executed)
        self.bus.subscribe(TradeResolvedEvent, self._handle_trade_resolved)
        self.bus.subscribe(RejectedOpportunityEvent, self._handle_rejected_opportunity)
        self.bus.subscribe(FeedHealthEvent, self._handle_feed_health)
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
            logger.debug("TelegramAgent: no credentials — message dropped")
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

    @staticmethod
    def _fmt_qty(qty: float) -> str:
        """Format a leg quantity. Fixed 2026-07-16: `:.0f` rounded a real,
        liquidity-capped 0.05-contract trade to "x0", which read as if a
        zero-size trade had somehow executed despite the ZERO_APPROVED_SIZE
        rejection (it hadn't — see DECISIONS.md/SESSIONS.md Session 26).
        `.4g` shows whole quantities cleanly (10 -> "10") while keeping real
        sub-1 quantities visible (0.05 -> "0.05")."""
        return f"{qty:.4g}"

    @staticmethod
    def _fmt_usd(amount: float) -> str:
        """Format a dollar amount. Sub-cent amounts are legitimate on very
        small liquidity-capped trades (e.g. $0.0042) — `:.2f` alone would
        collapse them to a misleading "$0.00"."""
        if amount != 0 and abs(amount) < 0.01:
            return f"{amount:.4f}"
        return f"{amount:.2f}"

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

    async def _handle_feed_health(self, event: FeedHealthEvent):
        """Tier 1 — always sends, ignores any future mute state.

        Fires only on a connected→disconnected or disconnected→connected
        transition for platform="kalshi" — not on every FeedHealthEvent
        published while the feed remains down (the agent's own health
        monitor and reconnect-retry loop can republish FeedHealthEvent(
        connected=False) repeatedly for the same continuous outage).
        """
        if event.platform != "kalshi":
            return

        was_connected = self._feed_connected.get(event.platform)
        self._feed_connected[event.platform] = event.connected

        # No transition (unknown→known-state on startup, or same state repeated).
        if was_connected is None or was_connected == event.connected:
            return

        if not self.config.telegram.enabled:
            return

        timestamp = self._et_timestamp()
        if event.connected:
            text = (
                f"🚨 KARBOT RAGE! CRITICAL\n"
                f"FEED RECOVERED\n"
                f"Platform: {event.platform}\n"
                f"{timestamp}"
            )
        else:
            error_line = f"Error: {event.error}\n" if event.error else ""
            text = (
                f"🚨 KARBOT RAGE! CRITICAL\n"
                f"FEED DOWN\n"
                f"Platform: {event.platform}\n"
                f"{error_line}"
                f"{timestamp}"
            )
        await self._outbound_queue.put(text)

    async def _handle_trade_executed(self, event: TradeExecutedEvent):
        """Tier 2 — respects notify_on_trade flag."""
        if not self.config.telegram.enabled:
            return
        if not self.config.telegram.notify_on_trade:
            return
        prefix = "📋 PAPER TRADE OPENED" if event.paper_mode else "✅ TRADE OPENED"
        market_id = event.platform_legs[0]["market_id"] if event.platform_legs else "?"
        legs_summary = " / ".join(
            f"{leg.get('side', '?')} @{leg.get('filled_price', 0):.2f} x{self._fmt_qty(leg.get('quantity', 0))}"
            for leg in event.platform_legs
        ) or "?"
        text = (
            f"{prefix}\n"
            f"ID: {event.trade_id[:8]}\n"
            f"Strategy: {event.strategy or '?'}\n"
            f"Market: {market_id}\n"
            f"Legs: {legs_summary}\n"
            f"Expected PnL: ${self._fmt_usd(event.expected_pnl_usd)} (estimate, not final)\n"
            f"Fees: ${self._fmt_usd(event.total_fee_paid)}\n"
            f"{self._et_timestamp()}"
        )
        await self._outbound_queue.put(text)

    async def _handle_trade_resolved(self, event: TradeResolvedEvent):
        """Tier 2 — respects notify_on_trade flag. The realized-outcome
        counterpart to _handle_trade_executed's entry message; without this,
        the operator only ever saw the pre-resolution *estimate*, never what
        the trade actually settled at."""
        if not self.config.telegram.enabled:
            return
        if not self.config.telegram.notify_on_trade:
            return
        emoji = "🟢" if event.realized_pnl >= 0 else "🔴"
        text = (
            f"{emoji} TRADE RESOLVED\n"
            f"ID: {event.trade_id[:8]}\n"
            f"Market: {event.market_id}\n"
            f"Outcome: {event.resolution or '?'}\n"
            f"Realized PnL: ${self._fmt_usd(event.realized_pnl)}\n"
            f"Held: {event.holding_period_hours:.2f}h\n"
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
