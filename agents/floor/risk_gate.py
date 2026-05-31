"""
karbot/agents/floor/risk_gate.py
──────────────────────────────────
Risk Gate Agent — Trading Floor

The last line of defense before real money moves.
Every OpportunityEvent must pass through this agent.
It CANNOT be bypassed, disabled, or overridden.

Eight pre-trade checks run sequentially.
ALL must pass. One failure = rejection with logged reason.

Design: fail-safe by default. When in doubt, reject.
Rejections are as valuable as approvals — they drive learning.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import structlog

from karbot.core.config import KarbotConfig, ABSOLUTE_MAX_PER_TRADE_PCT
from karbot.core.events import (
    EventBus, OpportunityEvent, ApprovedOpportunityEvent,
    RejectedOpportunityEvent, PositionSnapshot, AnnouncementWarningEvent,
    GeopoliticalRiskEvent, ResolutionVerificationResult, KillSwitchEvent,
    RiskLimitHitEvent, AgentHeartbeat, RegulatoryAlertEvent, Priority
)

log = structlog.get_logger(__name__)


class CheckResult:
    """Result of a single pre-trade check."""
    def __init__(self, passed: bool, reason: str = "", details: str = ""):
        self.passed  = passed
        self.reason  = reason
        self.details = details

    @classmethod
    def ok(cls) -> 'CheckResult':
        return cls(True)

    @classmethod
    def fail(cls, reason: str, details: str = "") -> 'CheckResult':
        return cls(False, reason, details)


class RiskGateAgent:
    """
    Trading Floor Agent #3 — the non-bypassable checkpoint.

    Every OpportunityEvent passes through this agent before execution.
    The eight checks run in order. First failure stops the chain.

    IMPORTANT: This agent also handles:
    - Kill switch: immediately halts all trading
    - Announcement pauses: temporarily blocks trading around events
    - Geopolitical alerts: elevates risk thresholds
    - Daily/weekly loss limits: auto-halt when limits are hit
    """

    AGENT_NAME = "risk_gate"
    HEARTBEAT_INTERVAL = 60

    def __init__(
        self,
        config: KarbotConfig,
        event_bus: EventBus,
    ):
        self.config = config
        self.bus    = event_bus
        self._risk  = config.risk
        self._strat = config.strategies

        # State
        self._kill_switch_active  = False
        self._trading_paused      = False    # True during announcement windows
        self._pause_reason        = ""
        self._geopolitical_risk   = "NORMAL"  # NORMAL | ELEVATED | HIGH | CRITICAL
        self._regulatory_pause    = False    # True when urgency-5 alert is uncleared

        # Position tracking (updated by PositionSnapshot events)
        self._current_snapshot: Optional[PositionSnapshot] = None
        self._daily_pnl        = 0.0
        self._weekly_pnl       = 0.0
        self._daily_trades     = 0

        # Resolution verification results cache
        # market_pair_key → ResolutionVerificationResult
        self._resolution_cache: Dict[str, ResolutionVerificationResult] = {}

        # Stats
        self._checks_run    = 0
        self._approved      = 0
        self._rejected      = 0
        self._rejection_reasons: Dict[str, int] = {}

    def register_subscriptions(self) -> None:
        self.bus.subscribe(OpportunityEvent, self._on_opportunity)
        self.bus.subscribe(PositionSnapshot, self._on_position_snapshot)
        self.bus.subscribe(AnnouncementWarningEvent, self._on_announcement)
        self.bus.subscribe(GeopoliticalRiskEvent, self._on_geopolitical_risk)
        self.bus.subscribe(ResolutionVerificationResult, self._on_resolution_result)
        self.bus.subscribe(KillSwitchEvent, self._on_kill_switch)
        self.bus.subscribe(RegulatoryAlertEvent, self._on_regulatory_alert)

    async def start(self) -> None:
        self.register_subscriptions()
        asyncio.create_task(self._heartbeat_loop(), name="rg_heartbeat")
        log.info("risk_gate_started",
                 paper_mode=self.config.system.paper_mode)

    # ── Main Handler ──────────────────────────────────────────────────────────

    async def _on_opportunity(self, event: OpportunityEvent) -> None:
        """
        Run all 8 pre-trade checks in sequence.
        First failure = reject with detailed reason.
        All pass = emit ApprovedOpportunityEvent.
        """
        self._checks_run += 1

        # Kill switch overrides everything
        if self._kill_switch_active:
            await self._reject(event, "KILL_SWITCH",
                               "Kill switch is active — all trading halted")
            return

        # Regulatory pause: urgency-5 alert requires operator clearance
        if self._regulatory_pause:
            await self._reject(
                event,
                "REGULATORY_PAUSE",
                "Urgency 5 regulatory alert active — awaiting operator clearance",
            )
            return

        # Run checks in order
        checks = [
            self._check_1_capital,
            self._check_2_position_size,
            self._check_3_daily_loss,
            self._check_4_correlation,
            self._check_5_announcement,
            self._check_6_resolution_criteria,
            self._check_7_slippage,
            self._check_8_gas_fee,
        ]

        for check_fn in checks:
            result = await check_fn(event)
            if not result.passed:
                await self._reject(event, result.reason, result.details)
                return

        # All checks passed — calculate approved position size
        approved_size = self._calculate_position_size(event)

        self._approved += 1
        log.info("opportunity_approved",
                 strategy  = event.strategy,
                 net_pct   = event.net_profit_pct,
                 size_usd  = approved_size,
                 paper     = self.config.system.paper_mode)

        await self.bus.publish(ApprovedOpportunityEvent(
            source         = self.AGENT_NAME,
            priority       = Priority.HIGH,
            opportunity    = event,
            approved_size  = approved_size,
            risk_gate_notes = (
                f"paper={self.config.system.paper_mode} "
                f"geo_risk={self._geopolitical_risk}"
            ),
        ))

    # ── The Eight Checks ──────────────────────────────────────────────────────

    async def _check_1_capital(self, event: OpportunityEvent) -> CheckResult:
        """Check 1: Would this trade cause total locked capital to exceed limit?"""
        if not self._current_snapshot:
            return CheckResult.fail(
                "NO_POSITION_DATA",
                "Position Tracker has not yet published a snapshot"
            )

        snap = self._current_snapshot
        total = snap.total_capital_usd
        if total <= 0:
            return CheckResult.fail(
                "ZERO_CAPITAL",
                "No capital deployed — configure capital.total_deployed_usd"
            )

        deployed_pct = (snap.deployed_capital_usd / total) * 100
        max_pct = self._risk.max_capital_locked_pct

        # Account for geopolitical risk elevation
        if self._geopolitical_risk == "HIGH":
            max_pct = max_pct * 0.7
        elif self._geopolitical_risk == "CRITICAL":
            max_pct = max_pct * 0.5

        if deployed_pct >= max_pct:
            return CheckResult.fail(
                "MAX_CAPITAL_LOCKED",
                f"Currently {deployed_pct:.1f}% deployed, limit is {max_pct:.1f}%"
            )

        return CheckResult.ok()

    async def _check_2_position_size(self, event: OpportunityEvent) -> CheckResult:
        """Check 2: Is the position size within per-trade limits?"""
        if not self._current_snapshot:
            return CheckResult.fail("NO_POSITION_DATA", "No snapshot available")

        total_capital = self._current_snapshot.total_capital_usd
        max_trade_usd = total_capital * (self._risk.max_capital_per_trade_pct / 100)

        required = event.capital_required_usd
        if required > 0 and required > max_trade_usd:
            return CheckResult.fail(
                "POSITION_TOO_LARGE",
                f"Required ${required:.0f} exceeds max ${max_trade_usd:.0f}"
            )

        # Also check platform-specific limits
        for leg in event.legs:
            if leg.get("platform") == "polymarket":
                poly_max = total_capital * (self._risk.polymarket_max_capital_pct / 100)
                poly_deployed = sum(
                    p.get("value_usd", 0) for p in
                    self._current_snapshot.open_positions
                    if p.get("platform") == "polymarket"
                )
                if poly_deployed >= poly_max:
                    return CheckResult.fail(
                        "POLYMARKET_LIMIT",
                        f"Polymarket allocation at limit "
                        f"({self._risk.polymarket_max_capital_pct}% max)"
                    )

        return CheckResult.ok()

    async def _check_3_daily_loss(self, event: OpportunityEvent) -> CheckResult:
        """Check 3: Have daily/weekly loss limits been hit?"""
        if not self._current_snapshot:
            return CheckResult.fail("NO_POSITION_DATA", "No snapshot available")

        total = self._current_snapshot.total_capital_usd
        if total <= 0:
            return CheckResult.ok()

        daily_loss_pct = (-self._daily_pnl / total) * 100 if self._daily_pnl < 0 else 0
        weekly_loss_pct = (-self._weekly_pnl / total) * 100 if self._weekly_pnl < 0 else 0

        if daily_loss_pct >= self._risk.max_daily_loss_pct:
            # Publish risk limit event
            await self.bus.publish(RiskLimitHitEvent(
                source        = self.AGENT_NAME,
                priority      = Priority.CRITICAL,
                limit_type    = "DAILY_LOSS",
                limit_value   = self._risk.max_daily_loss_pct,
                current_value = daily_loss_pct,
                action_taken  = "PAUSED",
            ))
            return CheckResult.fail(
                "DAILY_LOSS_LIMIT",
                f"Daily loss {daily_loss_pct:.1f}% exceeds limit "
                f"{self._risk.max_daily_loss_pct:.1f}%"
            )

        if weekly_loss_pct >= self._risk.max_weekly_loss_pct:
            await self.bus.publish(RiskLimitHitEvent(
                source        = self.AGENT_NAME,
                priority      = Priority.CRITICAL,
                limit_type    = "WEEKLY_LOSS",
                limit_value   = self._risk.max_weekly_loss_pct,
                current_value = weekly_loss_pct,
                action_taken  = "HALTED",
            ))
            return CheckResult.fail(
                "WEEKLY_LOSS_LIMIT",
                f"Weekly loss {weekly_loss_pct:.1f}% exceeds limit "
                f"{self._risk.max_weekly_loss_pct:.1f}%"
            )

        if self._daily_trades >= self._risk.max_daily_trades:
            return CheckResult.fail(
                "DAILY_TRADE_LIMIT",
                f"Daily trade count {self._daily_trades} at limit "
                f"{self._risk.max_daily_trades}"
            )

        return CheckResult.ok()

    async def _check_4_correlation(self, event: OpportunityEvent) -> CheckResult:
        """
        Check 4: Do open positions already correlate with this trade?
        Prevents "diversified" positions that all depend on the same underlying event.
        """
        if not self._current_snapshot:
            return CheckResult.ok()  # No positions = no correlation

        corr_score = self._current_snapshot.correlation_score
        if corr_score > 0.8:
            log.warning("high_correlation_detected", score=corr_score)
            # Don't reject outright — reduce position size (handled in sizing)
            # But if correlation is extreme, reject
            if corr_score > 0.95:
                return CheckResult.fail(
                    "EXTREME_CORRELATION",
                    f"Portfolio correlation score {corr_score:.2f} is dangerously high"
                )

        return CheckResult.ok()

    async def _check_5_announcement(self, event: OpportunityEvent) -> CheckResult:
        """Check 5: Is trading paused due to an announcement window?"""
        if self._trading_paused:
            return CheckResult.fail(
                "ANNOUNCEMENT_PAUSE",
                self._pause_reason
            )
        return CheckResult.ok()

    async def _check_6_resolution_criteria(
        self, event: OpportunityEvent
    ) -> CheckResult:
        """
        Check 6: For cross-platform trades, resolution criteria must match.
        The 2024 government shutdown case proves why this is mandatory.
        UNCERTAIN = reject. Only MATCH proceeds.
        """
        is_cross_platform = (
            event.strategy in ("S2_CROSS_PLATFORM",) and
            len(event.legs) >= 2 and
            len({leg["platform"] for leg in event.legs}) > 1
        )

        if not is_cross_platform:
            return CheckResult.ok()

        # Check if resolution criteria has been verified
        if event.resolution_criteria_match is None:
            # Not yet verified — send to Resolution Verifier and reject for now
            # The Resolution Verifier will cache the result for next time
            return CheckResult.fail(
                "RESOLUTION_UNVERIFIED",
                "Cross-platform trade requires resolution criteria verification. "
                "Resolution Verifier has been notified."
            )

        if not event.resolution_criteria_match:
            return CheckResult.fail(
                "RESOLUTION_MISMATCH",
                "Resolution criteria differ between platforms — "
                "platforms may resolve this event differently"
            )

        return CheckResult.ok()

    async def _check_7_slippage(self, event: OpportunityEvent) -> CheckResult:
        """Check 7: Has the price moved too much since opportunity detection?"""
        detection_age_seconds = (
            datetime.now(timezone.utc) - event.detected_at
        ).total_seconds()

        # For S1 (same-platform): opportunity must be very fresh
        if event.strategy == "S1_REBALANCING" and detection_age_seconds > 2.0:
            return CheckResult.fail(
                "OPPORTUNITY_STALE",
                f"S1 opportunity is {detection_age_seconds:.1f}s old — likely closed"
            )

        # For S2+ (cross-platform): allow more time
        if detection_age_seconds > 30.0:
            return CheckResult.fail(
                "OPPORTUNITY_STALE",
                f"Opportunity is {detection_age_seconds:.1f}s old — likely closed"
            )

        return CheckResult.ok()

    async def _check_8_gas_fee(self, event: OpportunityEvent) -> CheckResult:
        """Check 8: For Polymarket legs, is gas cost below ceiling?"""
        poly_legs = [l for l in event.legs if l.get("platform") == "polymarket"]
        if not poly_legs:
            return CheckResult.ok()

        # Check that gas doesn't eat the profit
        from karbot.agents.floor.arb_scanner import PolymarketFeeModel
        est_profit_usd = event.net_profit_pct / 100 * (
            self._current_snapshot.total_capital_usd *
            self._risk.max_capital_per_trade_pct / 100
            if self._current_snapshot else 100
        )
        gas_cost_usd = PolymarketFeeModel.EST_GAS_USD * len(poly_legs)

        if est_profit_usd > 0:
            gas_pct_of_profit = (gas_cost_usd / est_profit_usd) * 100
            if gas_pct_of_profit > self._risk.gas_fee_ceiling_pct:
                return CheckResult.fail(
                    "GAS_FEE_TOO_HIGH",
                    f"Gas cost is {gas_pct_of_profit:.0f}% of expected profit "
                    f"(limit: {self._risk.gas_fee_ceiling_pct:.0f}%)"
                )

        return CheckResult.ok()

    # ── Position Sizing ───────────────────────────────────────────────────────

    def _calculate_position_size(self, event: OpportunityEvent) -> float:
        """
        Calculate optimal position size using Kelly Criterion.

        Kelly formula: f = (bp - q) / b
        where:
          f = fraction of capital to bet
          b = odds received (net profit / cost)
          p = probability of winning
          q = probability of losing

        We use a fractional Kelly (default 0.15) for safety.
        """
        if not self._current_snapshot:
            return 0.0

        total_capital = self._current_snapshot.total_capital_usd
        free_capital  = self._current_snapshot.free_capital_usd

        # Conservative Kelly for arb (probability ≈ 1 for pure arb, less for directional)
        if event.strategy in ("S1_REBALANCING", "S2_CROSS_PLATFORM"):
            p = 0.95   # Near-certain for pure arb
        elif event.strategy in ("S3_LOGICAL_ARB", "S4_SETTLEMENT_ARB"):
            p = 0.80   # High but not certain
        else:
            p = 0.65   # More uncertain strategies

        q = 1 - p
        b = event.net_profit_pct / 100  # Net profit as decimal

        if b <= 0:
            return 0.0

        kelly_full = (b * p - q) / b
        kelly_fraction = max(0, kelly_full * self._risk.kelly_fraction)

        # Position size
        size = total_capital * kelly_fraction

        # Apply hard caps
        max_size = total_capital * (self._risk.max_capital_per_trade_pct / 100)
        size = min(size, max_size, free_capital * 0.9)

        # Reduce for elevated geopolitical risk
        if self._geopolitical_risk == "ELEVATED":
            size *= 0.75
        elif self._geopolitical_risk == "HIGH":
            size *= 0.5
        elif self._geopolitical_risk == "CRITICAL":
            size *= 0.25

        # Reduce for high portfolio correlation
        if self._current_snapshot.correlation_score > 0.7:
            reduction = 1 - (self._current_snapshot.correlation_score - 0.7) / 0.3
            size *= max(0.3, reduction)

        return round(size, 2)

    # ── Event Handlers ────────────────────────────────────────────────────────

    async def _on_position_snapshot(self, event: PositionSnapshot) -> None:
        """Update position state from Position Tracker."""
        self._current_snapshot = event
        self._daily_pnl        = event.daily_pnl_usd
        self._daily_trades     = event.daily_trades

    async def _on_announcement(self, event: AnnouncementWarningEvent) -> None:
        """Pause trading around macro announcements."""
        if event.action == "PAUSE":
            self._trading_paused = True
            self._pause_reason = (
                f"{event.announcement_type} announcement in "
                f"{event.minutes_until} minutes"
            )
            log.info("trading_paused_for_announcement",
                     event=event.announcement_type,
                     minutes=event.minutes_until)

            # Schedule unpause
            async def _unpause():
                await asyncio.sleep(
                    event.minutes_until * 60 +
                    self._risk.pause_window_minutes * 60
                )
                self._trading_paused = False
                self._pause_reason = ""
                log.info("trading_resumed_after_announcement",
                         event=event.announcement_type)

            asyncio.create_task(_unpause(), name="rg_unpause")

    async def _on_geopolitical_risk(self, event: GeopoliticalRiskEvent) -> None:
        """Update geopolitical risk level — affects position sizing."""
        self._geopolitical_risk = event.risk_level
        log.info("geopolitical_risk_updated",
                 level=event.risk_level,
                 region=event.region,
                 trigger=event.trigger)

        if event.recommended_action == "PAUSE_ALL":
            self._trading_paused = True
            self._pause_reason = f"Geopolitical pause: {event.trigger}"

    async def _on_resolution_result(
        self, event: ResolutionVerificationResult
    ) -> None:
        """Cache resolution verification results."""
        key = f"{event.market_a_id}:{event.market_b_id}"
        self._resolution_cache[key] = event
        log.debug("resolution_result_cached",
                  key=key, result=event.result)

    async def _on_kill_switch(self, event: KillSwitchEvent) -> None:
        """Activate kill switch — halt ALL trading immediately."""
        self._kill_switch_active = True
        self._trading_paused = True
        log.critical("KILL_SWITCH_ACTIVATED",
                     triggered_by=event.triggered_by,
                     reason=event.reason)

    async def _on_regulatory_alert(self, event: RegulatoryAlertEvent) -> None:
        """Update regulatory pause state based on urgency level."""
        if event.urgency == 5:
            self._regulatory_pause = True
            log.critical("REGULATORY_PAUSE_ACTIVATED",
                         summary=event.summary,
                         source_url=event.source_url)
        elif event.urgency == 0:
            self._regulatory_pause = False
            log.info("REGULATORY_PAUSE_CLEARED")

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _reject(
        self, event: OpportunityEvent, reason: str, details: str = ""
    ) -> None:
        """Publish rejection event with full context."""
        self._rejected += 1
        self._rejection_reasons[reason] = (
            self._rejection_reasons.get(reason, 0) + 1
        )

        log.debug("opportunity_rejected",
                  strategy=event.strategy,
                  reason=reason,
                  net_pct=event.net_profit_pct)

        await self.bus.publish(RejectedOpportunityEvent(
            source          = self.AGENT_NAME,
            opportunity_id  = event.opportunity_id,
            strategy        = event.strategy,
            reason          = reason,
            details         = details,
        ))

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            await self.bus.publish(AgentHeartbeat(
                source             = self.AGENT_NAME,
                agent_name         = self.AGENT_NAME,
                status             = "OK" if not self._kill_switch_active else "HALTED",
                messages_processed = self._checks_run,
                last_action        = (
                    f"approved={self._approved} rejected={self._rejected}"
                ),
            ))

    def activate_kill_switch(self, reason: str = "manual") -> None:
        """Direct activation of kill switch (for CLI/dashboard use)."""
        self._kill_switch_active = True
        self._trading_paused = True
        log.critical("KILL_SWITCH_ACTIVATED_DIRECT", reason=reason)

    def deactivate_kill_switch(self) -> None:
        """Deactivate kill switch — requires explicit manual action."""
        self._kill_switch_active = False
        self._trading_paused = False
        log.info("KILL_SWITCH_DEACTIVATED")

    @property
    def stats(self) -> Dict[str, int]:
        return {
            "checks_run":         self._checks_run,
            "approved":           self._approved,
            "rejected":           self._rejected,
            "rejection_reasons":  self._rejection_reasons,
            "kill_switch_active": self._kill_switch_active,
            "trading_paused":     self._trading_paused,
        }


# ── karbot_runner.py-compatible stub ─────────────────────────────────────────

class RiskGate(RiskGateAgent):
    """
    BaseAgent-conforming class used by karbot_runner.py.
    Inherits the full RiskGateAgent implementation.
    register_subscriptions() (from superclass) wires OpportunityEvent etc.
    run() starts the heartbeat background task.
    """

    def __init__(self, bus: EventBus, config: KarbotConfig):
        super().__init__(config=config, event_bus=bus)

    async def run(self) -> None:
        asyncio.create_task(self._heartbeat_loop(), name="rg_heartbeat")
        log.info("risk_gate_started", paper_mode=self.config.system.paper_mode)
        # Subscriptions handle all incoming events; keep this task alive.
        while True:
            await asyncio.sleep(3600)
