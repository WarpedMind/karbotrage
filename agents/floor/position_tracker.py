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

Phase 2 (this implementation):
  - Subscribes to TradeExecutedEvent → updates deployed capital and
    open positions list; publishes updated snapshot immediately.
  - Subscribes to TradeResolvedEvent → frees capital, realises P&L,
    removes closed position; publishes updated snapshot.
  - Subscribes to LegFailureEvent → unwinds position and frees capital;
    publishes updated snapshot.
  - Daily reset at UTC midnight: clears _daily_pnl and _daily_trades.
    Checked on every 30s loop iteration — no separate task.

Known remaining gap (Phase 3):
  - correlation_score is permanently 0.0. Requires cross-position
    correlation analysis which is out of scope for Phase 2.
  - TradeResolvedEvent is never emitted yet (execution layer not wired).
    Positions never close and _total_capital never updates until
    the execution layer is fixed. Must address before live trading.
"""

import asyncio
from datetime import date, datetime, timezone

import structlog

from karbot.core.config import KarbotConfig
from karbot.core.events import (
    EventBus,
    LegFailureEvent,
    PositionSnapshot,
    TradeExecutedEvent,
    TradeResolvedEvent,
)

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
        self._last_reset_date  = datetime.now(timezone.utc).date()

    def register_subscriptions(self) -> None:
        self.bus.subscribe(TradeExecutedEvent, self._on_trade_executed)
        self.bus.subscribe(TradeResolvedEvent, self._on_trade_resolved)
        self.bus.subscribe(LegFailureEvent,    self._on_leg_failure)

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
            self._maybe_daily_reset()
            await self._publish_snapshot()

    # ── Daily reset ───────────────────────────────────────────────────────────

    def _maybe_daily_reset(self) -> None:
        """Check if UTC date has rolled over; if so, reset daily counters."""
        today = datetime.now(timezone.utc).date()
        if today != self._last_reset_date:
            self._daily_pnl    = 0.0
            self._daily_trades = 0
            self._last_reset_date = today
            log.info(
                "POSITION_TRACKER_DAILY_RESET",
                total_capital=f"{self._total_capital:.2f}",
                deployed=f"{self._deployed_capital:.2f}",
                open_positions=len(self._open_positions),
            )

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_trade_executed(self, event: TradeExecutedEvent) -> None:
        if not event.platform_legs:
            log.warning(
                "trade_executed_missing_platform_legs",
                trade_id=event.trade_id,
            )
            return

        try:
            capital_used = sum(
                leg["filled_price"] * leg["quantity"]
                for leg in event.platform_legs
            )
        except (KeyError, TypeError) as exc:
            log.warning(
                "trade_executed_invalid_leg_fields",
                trade_id=event.trade_id,
                error=str(exc),
            )
            return

        self._deployed_capital += capital_used
        self._open_positions.append({
            "trade_id":         event.trade_id,
            "opportunity_id":   event.opportunity_id,
            "strategy":         event.strategy,
            "capital_deployed": capital_used,
            "expected_pnl_usd": event.expected_pnl_usd,
            "paper_mode":       event.paper_mode,
            "opened_at":        event.timestamp.isoformat(),
        })
        self._daily_trades += 1
        await self._publish_snapshot()

    async def _on_trade_resolved(self, event: TradeResolvedEvent) -> None:
        position = next(
            (p for p in self._open_positions if p["trade_id"] == event.trade_id),
            None,
        )
        if position is None:
            log.warning("trade_resolved_unknown_trade_id", trade_id=event.trade_id)
            return

        self._deployed_capital = max(
            0.0, self._deployed_capital - position["capital_deployed"]
        )
        self._daily_pnl     += event.realized_pnl
        self._total_capital += event.realized_pnl
        self._open_positions.remove(position)
        await self._publish_snapshot()

    async def _on_leg_failure(self, event: LegFailureEvent) -> None:
        position = next(
            (p for p in self._open_positions if p["trade_id"] == event.trade_id),
            None,
        )
        if position is None:
            log.warning("leg_failure_unknown_trade_id", trade_id=event.trade_id)
            return

        self._deployed_capital = max(
            0.0, self._deployed_capital - position["capital_deployed"]
        )
        self._open_positions.remove(position)
        log.warning("position_unwound_on_leg_failure", trade_id=event.trade_id)
        await self._publish_snapshot()

    # ── Snapshot builder ──────────────────────────────────────────────────────

    async def _publish_snapshot(self) -> None:
        free = self._total_capital - self._deployed_capital
        unrealized_pnl = sum(
            p.get("expected_pnl_usd", 0.0) for p in self._open_positions
        )
        await self.bus.publish(PositionSnapshot(
            source               = self.AGENT_NAME,
            total_capital_usd    = self._total_capital,
            deployed_capital_usd = self._deployed_capital,
            free_capital_usd     = free,
            open_positions       = list(self._open_positions),
            unrealized_pnl_usd   = unrealized_pnl,
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
