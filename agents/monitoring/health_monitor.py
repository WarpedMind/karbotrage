"""
agents/monitoring/health_monitor.py
────────────────────────────────────
Health Monitor Agent — closes the KNOWN DEBT item flagged since Session 15:
AgentHeartbeat events were being dead-lettered every ~60s with nobody
subscribed to handle them.

Design:
- Tracks last-seen wall-clock time per agent_name, learned dynamically from
  whichever agents actually publish AgentHeartbeat. Not every agent does
  (PositionTracker, PaperExecutor, ComplianceOfficer, RegulatoryIntelligence
  and TelegramAgent currently do not) — this agent only ever watches names
  it has actually seen a heartbeat from, so it never false-alarms on an
  agent that was never wired to publish one in the first place.
- A background loop periodically checks every known agent's last-seen time
  against a silence threshold. On first detection of silence it fires a
  single Tier 1 Telegram alert (via the existing generic
  TelegramNotificationEvent(tier=1, ...) path — the same mechanism
  karbot_runner.py already uses for "AUTO-RECOVERY EXHAUSTED", not a new
  event type) and marks that agent alerted so repeated checks during one
  continuous outage don't spam. When a heartbeat resumes for an
  already-alerted agent, a single distinct recovery alert fires and the
  alerted flag clears — the same transition-only pattern Session 20 used
  for FeedHealthEvent.
- The silence-check logic (`_check_silence`) is separated from the sleep
  loop specifically so tests can drive it directly with an injected `now`
  instead of sleeping — see tests/test_health_monitor.py.
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict

import structlog

from karbot.core.config import KarbotConfig
from karbot.core.events import EventBus, AgentHeartbeat, TelegramNotificationEvent

log = structlog.get_logger(__name__)


class HealthMonitorAgentImpl:
    """
    Watches AgentHeartbeat traffic on the bus and alerts when a
    previously-heartbeating agent goes silent for longer than
    `health_monitor_silence_threshold_seconds`.
    """

    AGENT_NAME = "health_monitor"

    def __init__(self, bus: EventBus, config: KarbotConfig):
        self.bus = bus
        self.config = config
        self._check_interval = config.system.health_monitor_check_interval_seconds
        self._silence_threshold = config.system.health_monitor_silence_threshold_seconds

        # agent_name -> last time (time.monotonic()) a heartbeat was seen
        self._last_seen: Dict[str, float] = {}
        # agent_name -> True while a silence alert is outstanding (unrecovered)
        self._alerted: Dict[str, bool] = {}

        self._checks_run = 0

    def register_subscriptions(self) -> None:
        self.bus.subscribe(AgentHeartbeat, self._on_heartbeat)

    async def _on_heartbeat(self, event: AgentHeartbeat) -> None:
        name = event.agent_name or event.source
        if not name:
            return
        self._last_seen[name] = time.monotonic()

        if self._alerted.get(name):
            self._alerted[name] = False
            await self.bus.publish(TelegramNotificationEvent(
                source=self.AGENT_NAME,
                message=f"AGENT RECOVERED: {name}\nHeartbeat resumed.",
                tier=1,
                event_source=self.AGENT_NAME,
            ))
            log.info("health_monitor_agent_recovered", agent_name=name)

    async def _check_silence(self, now: float) -> None:
        """Testable core: given the current monotonic time, alert on any
        known agent whose last heartbeat is older than the silence
        threshold and that isn't already flagged as alerted."""
        self._checks_run += 1
        for name, last_seen in list(self._last_seen.items()):
            silent_for = now - last_seen
            if silent_for > self._silence_threshold and not self._alerted.get(name):
                self._alerted[name] = True
                await self.bus.publish(TelegramNotificationEvent(
                    source=self.AGENT_NAME,
                    message=(
                        f"AGENT SILENT: {name}\n"
                        f"No heartbeat for {silent_for:.0f}s "
                        f"(threshold {self._silence_threshold}s)."
                    ),
                    tier=1,
                    event_source=self.AGENT_NAME,
                ))
                log.error("health_monitor_agent_silent", agent_name=name,
                          silent_for=silent_for)

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(self._check_interval)
            await self._check_silence(time.monotonic())

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "checks_run": self._checks_run,
            "known_agents": len(self._last_seen),
            "currently_alerted": sum(1 for v in self._alerted.values() if v),
        }


# ── karbot_runner.py-compatible stub ─────────────────────────────────────────

class HealthMonitor(HealthMonitorAgentImpl):
    """BaseAgent-conforming class used by karbot_runner.py."""

    def __init__(self, bus: EventBus, config: KarbotConfig):
        super().__init__(bus=bus, config=config)

    async def run(self) -> None:
        log.info("health_monitor_started",
                  check_interval=self._check_interval,
                  silence_threshold=self._silence_threshold)
        await self._monitor_loop()
