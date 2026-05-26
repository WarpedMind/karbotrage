"""
agents/floor/position_tracker.py — Position Tracker Agent
Karbot Rage! | WallStRobotics | Phase 1

Owns the canonical view of deployed capital and open positions.
Publishes PositionSnapshot events so the Risk Gate can enforce capital limits.

Phase 1 scope (minimal but correct):
  - Publishes a startup snapshot immediately in run() so Risk Gate is
    unblocked from the very first opportunity. Without this, all
    opportunities are rejected with NO_POSITION_DATA.
  - Re-publishes every 30s to keep the snapshot fresh.
  - Capital defaults to PAPER_DEFAULT_CAPITAL when config does not
    specify a non-zero total_deployed_usd (common in dev/test runs).

Phase 2 todo (do not implement yet):
  - Subscribe to TradeExecutedEvent and update deployed capital / open positions
  - Subscribe to TradeResolvedEvent and realise P&L
  - Track correlation across open positions
"""

import asyncio

import structlog

from karbot.core.config import KarbotConfig
from karbot.core.events import EventBus, PositionSnapshot

log = structlog.get_logger(__name__)

# Used when config.capital.total_deployed_usd is 0 (unconfigured) and
# we are in paper mode.  Gives the Risk Gate enough runway to approve trades
# without operator having to set a capital figure before their first test run.
PAPER_DEFAULT_CAPITAL = 10_000.0   # USD

SNAPSHOT_INTERVAL = 30             # seconds between periodic re-publishes


class PositionTracker:
    """
    Trading Floor Agent — capital ledger and position registry.

    BaseAgent-conforming: __init__(bus, config), register_subscriptions(), run().
    """

    AGENT_NAME = "position_tracker"

    def __init__(self, bus: EventBus, config: KarbotConfig):
        self.bus    = bus
        self.config = config

        # Determine starting capital
        configured = config.capital.total_deployed_usd
        if configured > 0:
            self._total_capital = configured
        elif config.paper_mode:
            self._total_capital = PAPER_DEFAULT_CAPITAL
            log.info(
                "position_tracker_using_paper_default",
                capital=PAPER_DEFAULT_CAPITAL,
                reason="config.capital.total_deployed_usd not set",
            )
        else:
            # Live mode with no capital configured — start at 0 so Risk Gate
            # immediately forces the operator to configure it.
            self._total_capital = 0.0
            log.warning(
                "position_tracker_zero_capital",
                reason="config.capital.total_deployed_usd=0 in live mode",
            )

        self._deployed_capital = 0.0
        self._daily_pnl        = 0.0
        self._daily_trades     = 0
        self._open_positions   = []

    def register_subscriptions(self) -> None:
        # Phase 1: no subscriptions yet.
        # Phase 2: subscribe to TradeExecutedEvent, TradeResolvedEvent.
        pass

    async def run(self) -> None:
        # ── Startup snapshot — MUST be published before the main loop ──────────
        # This is the fix for NO_POSITION_DATA rejections in the runner.
        # Risk Gate cannot approve any opportunity until it has received at least
        # one PositionSnapshot.  Publishing here, at the very top of run(),
        # ensures the snapshot reaches Risk Gate before the first PriceUpdateEvent
        # is dispatched, provided PositionTracker is started before MockPriceWatcher
        # (enforced by agent list order in karbot_runner.py).
        await self._publish_snapshot()
        log.info(
            "position_tracker_started",
            total_capital=self._total_capital,
            paper_mode=self.config.paper_mode,
        )

        # ── Periodic re-publish loop ────────────────────────────────────────────
        while True:
            await asyncio.sleep(SNAPSHOT_INTERVAL)
            await self._publish_snapshot()

    async def _publish_snapshot(self) -> None:
        free = self._total_capital - self._deployed_capital
        await self.bus.publish(PositionSnapshot(
            source               = self.AGENT_NAME,
            total_capital_usd    = self._total_capital,
            deployed_capital_usd = self._deployed_capital,
            free_capital_usd     = free,
            open_positions       = list(self._open_positions),
            unrealized_pnl_usd   = 0.0,
            correlation_score    = 0.0,
            daily_pnl_usd        = self._daily_pnl,
            daily_trades         = self._daily_trades,
        ))
        log.debug(
            "position_snapshot_published",
            total=self._total_capital,
            deployed=self._deployed_capital,
            free=free,
        )
