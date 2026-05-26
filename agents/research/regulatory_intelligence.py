"""
agents/research/regulatory_intelligence.py — Regulatory Intelligence Agent
Karbot Rage! | WallStRobotics

Watches CFTC RSS feeds and the Federal Register.
Uses a keyword pre-filter to control Claude API costs.
Sends surviving items to Claude Sonnet for urgency assessment (1-5) and
plain-English interpretation.
Routes results to the operator via Telegram with escalating response
requirements based on urgency.

Replaces the polling loop that was formerly in ComplianceOfficer.

Cost controls:
  - Per-cycle cap: regulatory_ai_calls_per_cycle (default 10)
  - Daily hard cap: regulatory_ai_daily_cap (default 50)
  - Circuit breaker: N calls in M minutes → stop + Telegram + restart required
  - Overflow queue: items exceeding per-cycle cap held for next cycle

Urgency routing:
  1-2  → log only, no Telegram
  3    → TelegramNotificationEvent (FYI, tier 2)
  4    → TelegramNotificationEvent (alert, tier 1) — acknowledgment requested
  5    → TelegramNotificationEvent (critical) + _regulatory_pause = True
  0    → operator cleared the hold (published after receiving clear phrase)
"""

from __future__ import annotations

import asyncio
import json
import logging
import xml.etree.ElementTree as ET
from collections import deque
from datetime import date, datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional, Set

import aiohttp

from core.events import (
    EventBus,
    RegulatoryAlertEvent,
    TelegramNotificationEvent,
    TelegramPermissionResponseEvent,
    Priority,
)
from karbot.core.config import KarbotConfig

logger = logging.getLogger(__name__)

# ── Regulatory sources (same as former ComplianceOfficer polling) ─────────────

REGULATORY_SOURCES = [
    {
        "name": "CFTC Press Releases",
        "url": "https://www.cftc.gov/PressRoom/PressReleases/rss.xml",
        "type": "rss",
    },
    {
        "name": "CFTC Speeches & Testimony",
        "url": "https://www.cftc.gov/PressRoom/SpeechesTestimony/rss.xml",
        "type": "rss",
    },
    {
        "name": "Federal Register — CFTC Rules",
        "url": (
            "https://www.federalregister.gov/api/v1/articles.json"
            "?agencies[]=commodity-futures-trading-commission"
            "&per_page=5&order=newest"
        ),
        "type": "json",
    },
]

AGENT_NAME = "regulatory_intelligence"


class RegulatoryIntelligenceAgentImpl:
    """
    Full implementation.

    Two classes per project convention: this is the implementation,
    RegulatoryIntelligenceAgent is the BaseAgent-conforming runner stub.
    """

    def __init__(self, bus: EventBus, config: KarbotConfig, claude_client=None):
        self.bus = bus
        self.config = config
        self._ri = config.regulatory_intelligence

        # Lazily initialised Claude client; inject in tests via this attribute
        self._claude = claude_client

        # ── Deduplication ─────────────────────────────────────────────────
        self._processed_urls: Set[str] = set()

        # ── Overflow queue ────────────────────────────────────────────────
        # Items exceeding per-cycle cap wait here for the next cycle.
        # Each entry is (item_dict, cycle_type_str).
        self._overflow_queue: asyncio.Queue = asyncio.Queue()

        # ── Cost controls ─────────────────────────────────────────────────
        _now = datetime.now(timezone.utc)
        self._daily_call_count: int = 0
        self._daily_call_reset_date: date = _now.date()
        self._circuit_breaker_timestamps: Deque[datetime] = deque()
        self._circuit_breaker_tripped: bool = False
        self._monthly_call_count: int = 0
        self._monthly_start_date: date = _now.date().replace(day=1)

        # ── Scheduling ────────────────────────────────────────────────────
        self._last_6h_check: datetime = datetime.min.replace(tzinfo=timezone.utc)
        self._last_weekly_sweep: date = date.min

        # ── Operator gate ─────────────────────────────────────────────────
        self._regulatory_pause: bool = False

    # ── BaseAgent interface ───────────────────────────────────────────────────

    def register_subscriptions(self) -> None:
        self.bus.subscribe(TelegramPermissionResponseEvent, self._on_permission_response)
        logger.info("RegulatoryIntelligenceAgent subscriptions registered")

    async def run(self) -> None:
        if not self._ri.enabled:
            logger.info("RegulatoryIntelligenceAgent disabled — sleeping forever")
            while True:
                await asyncio.sleep(3600)

        logger.info("RegulatoryIntelligenceAgent started")
        while True:
            try:
                now = datetime.now(timezone.utc)
                self._maybe_reset_daily_count(now)

                if self._is_weekly_sweep_due(now):
                    await self._run_cycle(weekly_sweep=True)
                    self._last_weekly_sweep = now.date()
                    self._last_6h_check = now
                elif self._is_6h_cycle_due(now):
                    await self._run_cycle(weekly_sweep=False)
                    self._last_6h_check = now
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    f"RegulatoryIntelligenceAgent cycle error: {e}", exc_info=True
                )
            await asyncio.sleep(60)

    # ── Scheduling helpers ────────────────────────────────────────────────────

    def _is_6h_cycle_due(self, now: datetime) -> bool:
        hours_since = (now - self._last_6h_check).total_seconds() / 3600
        return hours_since >= self._ri.poll_interval_hours

    def _is_weekly_sweep_due(self, now: datetime) -> bool:
        day_name = now.strftime("%A").lower()
        return (
            day_name == self._ri.weekly_sweep_day.lower()
            and now.hour == self._ri.weekly_sweep_hour_utc
            and now.date() > self._last_weekly_sweep
        )

    # ── Cost control helpers ──────────────────────────────────────────────────

    def _maybe_reset_daily_count(self, now: datetime) -> None:
        today = now.date()
        if today <= self._daily_call_reset_date:
            return

        n = self._daily_call_count
        cost = self._ri.regulatory_cost_per_call_usd
        days_elapsed = max(1, (today - self._monthly_start_date).days)
        daily_avg = self._monthly_call_count / days_elapsed
        m = self._monthly_call_count

        logger.info(
            f"REGULATORY_AI_DAILY_SUMMARY "
            f"calls_today={n} "
            f"estimated_cost_today=${n * cost:.4f} "
            f"projected_monthly=${daily_avg * 30:.2f} "
            f"calls_this_month={m}"
        )

        self._daily_call_count = 0
        self._daily_call_reset_date = today

        if today.day == 1:
            self._monthly_call_count = 0
            self._monthly_start_date = today

    def _cap_reached(self) -> bool:
        return self._daily_call_count >= self._ri.regulatory_ai_daily_cap

    def _check_circuit_breaker(self, now: datetime) -> bool:
        """Prune expired timestamps and return True if breaker should trip."""
        window = timedelta(minutes=self._ri.regulatory_circuit_breaker_window_minutes)
        while (
            self._circuit_breaker_timestamps
            and (now - self._circuit_breaker_timestamps[0]) > window
        ):
            self._circuit_breaker_timestamps.popleft()
        return len(self._circuit_breaker_timestamps) >= self._ri.regulatory_circuit_breaker_calls

    # ── Cycle logic ───────────────────────────────────────────────────────────

    async def _run_cycle(self, weekly_sweep: bool = False) -> None:
        items = await self._fetch_items()
        await self._run_cycle_with_items(items, weekly_sweep=weekly_sweep)

    async def _run_cycle_with_items(
        self, items: List[Dict[str, str]], weekly_sweep: bool = False
    ) -> None:
        """
        Run one full cycle using pre-fetched items.
        Separated from _run_cycle so tests can inject items without HTTP.
        """
        cycle_type = "weekly" if weekly_sweep else "6h"
        logger.info(f"Regulatory intelligence cycle starting | type={cycle_type}")

        # Process items left over from the previous cycle first
        await self._drain_overflow_queue()

        # Deduplicate
        new_items = [i for i in items if i["url"] not in self._processed_urls]

        # Keyword pre-filter (skipped for weekly sweep)
        if weekly_sweep:
            flagged = new_items
        else:
            flagged = [
                i for i in new_items
                if self._keyword_matches(i["title"] + " " + i["summary"])
            ]
            skipped = len(new_items) - len(flagged)
            if skipped > 0:
                logger.debug(
                    f"Keyword pre-filter: {skipped}/{len(new_items)} items skipped"
                )

        # Split into this-cycle and overflow
        cap = self._ri.regulatory_ai_calls_per_cycle
        to_process = flagged[:cap]
        overflow = flagged[cap:]

        for item in overflow:
            await self._overflow_queue.put((item, cycle_type))
        if overflow:
            logger.info(
                f"{len(overflow)} items queued to overflow for next cycle"
            )

        for item in to_process:
            self._processed_urls.add(item["url"])
            await self._process_item(item, cycle_type=cycle_type)

    async def _drain_overflow_queue(self) -> None:
        cap = self._ri.regulatory_ai_calls_per_cycle
        processed = 0
        while (
            not self._overflow_queue.empty()
            and processed < cap
            and not self._circuit_breaker_tripped
        ):
            if self._cap_reached():
                break
            try:
                item, original_cycle_type = self._overflow_queue.get_nowait()
                self._processed_urls.add(item["url"])
                await self._process_item(item, cycle_type=original_cycle_type)
                processed += 1
            except asyncio.QueueEmpty:
                break

    async def _process_item(self, item: dict, cycle_type: str) -> None:
        if self._circuit_breaker_tripped:
            logger.warning("Circuit breaker tripped — skipping item")
            return

        if self._cap_reached():
            logger.warning(
                f"Regulatory AI daily cap hit ({self._daily_call_count}). "
                "No further API calls until midnight UTC."
            )
            await self.bus.publish(TelegramNotificationEvent(
                source=AGENT_NAME,
                message=(
                    "⚠️ Regulatory AI daily cap hit. "
                    "No further API calls until midnight UTC."
                ),
                tier=1,
            ))
            return

        # Check circuit breaker before making the API call
        now = datetime.now(timezone.utc)
        if self._check_circuit_breaker(now):
            self._circuit_breaker_tripped = True
            logger.critical(
                "Regulatory Intelligence circuit breaker tripped. "
                "Runner restart required."
            )
            await self.bus.publish(TelegramNotificationEvent(
                source=AGENT_NAME,
                message=(
                    "🚨 Regulatory Intelligence circuit breaker tripped. "
                    "Runner restart required."
                ),
                tier=1,
            ))
            return

        # Call Claude API
        result = await self._call_claude_api(item)

        # Record the call
        self._daily_call_count += 1
        self._monthly_call_count += 1
        self._circuit_breaker_timestamps.append(datetime.now(timezone.utc))

        await self._route_by_urgency(result, item, cycle_type=cycle_type)

    # ── Claude API ────────────────────────────────────────────────────────────

    async def _call_claude_api(self, item: dict) -> dict:
        try:
            import anthropic as _anthropic
        except ImportError:
            logger.error("anthropic package not installed — cannot call Claude API")
            return self._fallback_result(item)

        if self._claude is None:
            self._claude = _anthropic.AsyncAnthropic(
                api_key=self.config.secrets.anthropic_api_key
            )

        raw_title = item.get("title", "")
        source_url = item.get("url", "")
        summary_text = item.get("summary", "")

        system_prompt = (
            "You are a regulatory compliance analyst for a CFTC-regulated "
            "prediction market trading system. Your job is to assess regulatory "
            "documents for relevance and urgency. Respond only in valid JSON. "
            "No preamble, no markdown, no explanation outside the JSON."
        )
        user_message = (
            "Assess this regulatory item for a CFTC-regulated prediction market "
            "arbitrage system called Karbot Rage! that trades only on Kalshi "
            "(a licensed exchange), uses only public data, and has no human "
            "discretion in trade execution.\n\n"
            f"Title: {raw_title}\n"
            f"Source: {source_url}\n"
            f"Summary: {summary_text}\n\n"
            "Respond with this exact JSON structure:\n"
            "{\n"
            '  "urgency": <integer 1-5>,\n'
            '  "urgency_reasoning": "<one sentence explaining the score>",\n'
            '  "summary": "<one sentence plain-English summary suitable for Telegram>",\n'
            '  "affected": "<yes|no|unclear>",\n'
            '  "recommended_action": "<one sentence operator recommendation>",\n'
            '  "karbot_specific_notes": "<specific implications, or \'none\'>"\n'
            "}"
        )

        raw_text = ""
        try:
            response = await self._claude.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=500,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            raw_text = response.content[0].text if response.content else ""
            parsed = json.loads(raw_text)
            if not isinstance(parsed.get("urgency"), int):
                parsed["urgency"] = int(parsed.get("urgency", 3))
            return parsed
        except json.JSONDecodeError as e:
            logger.error(
                f"Claude API JSON parse error: {e} | raw={raw_text[:200]}"
            )
            return self._fallback_result(item)
        except Exception as e:
            logger.error(f"Claude API call failed: {e}", exc_info=True)
            return self._fallback_result(item)

    @staticmethod
    def _fallback_result(item: dict) -> dict:
        return {
            "urgency": 3,
            "urgency_reasoning": "API error or parse failure — defaulting to urgency 3",
            "summary": item.get("title", "Unknown regulatory item"),
            "affected": "unclear",
            "recommended_action": "Review manually",
            "karbot_specific_notes": "none",
        }

    # ── Urgency routing ───────────────────────────────────────────────────────

    async def _route_by_urgency(
        self, result: dict, item: dict, cycle_type: str
    ) -> None:
        urgency = result.get("urgency", 3)
        summary = result.get("summary", item.get("title", ""))
        source_url = item.get("url", "")
        raw_title = item.get("title", "")
        affected = result.get("affected", "unclear")
        recommended_action = result.get("recommended_action", "")

        # Always publish RegulatoryAlertEvent so ComplianceOfficer logs it
        await self.bus.publish(RegulatoryAlertEvent(
            source=AGENT_NAME,
            priority=Priority.HIGH,
            urgency=urgency,
            summary=summary,
            source_url=source_url,
            affected=affected,
            recommended_action=recommended_action,
            raw_title=raw_title,
            cycle_type=cycle_type,
        ))

        if urgency <= 2:
            logger.info(
                f"Regulatory item urgency={urgency} (low) — logged only | "
                f"title={raw_title[:80]}"
            )
            return

        if urgency == 3:
            await self.bus.publish(TelegramNotificationEvent(
                source=AGENT_NAME,
                message=(
                    f"ℹ️ Regulatory item (urgency {urgency}/5): {summary}\n"
                    f"Source: {source_url}\n"
                    f"Recommendation: {recommended_action}"
                ),
                tier=2,
            ))

        elif urgency == 4:
            await self.bus.publish(TelegramNotificationEvent(
                source=AGENT_NAME,
                message=(
                    f"⚠️ Regulatory alert (urgency {urgency}/5): {summary}\n"
                    f"Affected: {affected}\n"
                    f"Source: {source_url}\n"
                    f"Recommendation: {recommended_action}\n"
                    f"Please acknowledge receipt."
                ),
                tier=1,
            ))
            logger.warning(
                f"Urgency 4 regulatory alert — acknowledgment requested | "
                f"{raw_title[:80]}"
            )

        elif urgency >= 5:
            self._regulatory_pause = True
            logger.critical(
                f"URGENCY 5 REGULATORY ALERT — trading paused | {summary}"
            )
            await self.bus.publish(TelegramNotificationEvent(
                source=AGENT_NAME,
                message=(
                    f"🚨 URGENCY 5 REGULATORY ALERT — TRADING PAUSED\n"
                    f"{summary}\n"
                    f"Affected: {affected}\n"
                    f"Source: {source_url}\n"
                    f"Recommendation: {recommended_action}\n"
                    f"Send '{self._ri.regulatory_clear_phrase}' to resume trading."
                ),
                tier=1,
            ))

    # ── Operator clear flow ───────────────────────────────────────────────────

    async def _on_permission_response(
        self, event: TelegramPermissionResponseEvent
    ) -> None:
        if not self._regulatory_pause:
            return

        response_text = getattr(event, "response_text", "")
        clear_phrase = self._ri.regulatory_clear_phrase

        if response_text.strip().upper() != clear_phrase.strip().upper():
            return

        self._regulatory_pause = False
        logger.info("Regulatory hold cleared by operator via Telegram")

        await self.bus.publish(RegulatoryAlertEvent(
            source=AGENT_NAME,
            priority=Priority.HIGH,
            urgency=0,
            summary="Operator cleared regulatory hold",
        ))
        await self.bus.publish(TelegramNotificationEvent(
            source=AGENT_NAME,
            message="✅ Regulatory hold cleared by operator. Trading resumed.",
            tier=1,
        ))

    # ── Feed fetching and parsing ─────────────────────────────────────────────

    async def _fetch_items(self) -> List[Dict[str, str]]:
        items: List[Dict[str, str]] = []
        async with aiohttp.ClientSession() as session:
            for source in REGULATORY_SOURCES:
                try:
                    async with session.get(
                        source["url"],
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status != 200:
                            logger.warning(
                                f"[REGULATORY] {source['name']} "
                                f"returned HTTP {resp.status}"
                            )
                            continue
                        if source["type"] == "rss":
                            text = await resp.text()
                            items.extend(self._parse_rss(text))
                        elif source["type"] == "json":
                            data = await resp.json()
                            items.extend(self._parse_federal_register(data))
                except asyncio.TimeoutError:
                    logger.warning(f"[REGULATORY] Timeout: {source['name']}")
                except Exception as e:
                    logger.warning(
                        f"[REGULATORY] Error fetching {source['name']}: {e}"
                    )
        return items

    def _parse_rss(self, text: str) -> List[Dict[str, str]]:
        items: List[Dict[str, str]] = []
        try:
            root = ET.fromstring(text)
            for item in root.iter("item"):
                title = item.findtext("title") or ""
                link = item.findtext("link") or ""
                description = item.findtext("description") or ""
                if link:
                    items.append({"title": title, "url": link, "summary": description})
        except ET.ParseError as e:
            logger.warning(f"RSS parse error: {e}")
        return items

    def _parse_federal_register(self, data: dict) -> List[Dict[str, str]]:
        items: List[Dict[str, str]] = []
        for article in data.get("results", []):
            url = article.get("html_url", "")
            title = article.get("title", "")
            summary = article.get("abstract", "") or article.get("excerpt", "") or ""
            if url:
                items.append({"title": title, "url": url, "summary": summary})
        return items

    def _keyword_matches(self, text: str) -> bool:
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in self._ri.regulatory_keywords)


# ── BaseAgent-conforming runner stub ──────────────────────────────────────────

class RegulatoryIntelligenceAgent(RegulatoryIntelligenceAgentImpl):
    """Stub conforming to the BaseAgent interface for karbot_runner.py."""

    def __init__(self, bus: EventBus, config: KarbotConfig):
        super().__init__(bus=bus, config=config)
