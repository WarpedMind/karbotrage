"""
tests/test_position_tracker.py — PositionTracker Phase 2 test suite

Nine tests covering:
  1.  Startup snapshot published before any events
  2.  TradeExecutedEvent updates deployed capital, open positions, daily_trades
  3.  Two trades stack correctly
  4.  TradeResolvedEvent frees capital and updates daily_pnl
  5.  LegFailureEvent unwinds position and frees capital
  6.  Capital never goes negative (resolve on unknown trade_id)
  7.  Risk Gate sees accurate capital (integration — the Phase 2 acceptance test)
  8.  Daily reset clears _daily_pnl and _daily_trades
  9.  Missing platform_legs handled gracefully (WARNING, state unchanged)

Test 7 is the critical integration test: it proves the Phase 2 fix works
end-to-end.  A trade that deploys 45% of capital must cause the subsequent
OpportunityEvent to be rejected by Risk Gate with reason=MAX_CAPITAL_LOCKED.

Pattern: tests call register_subscriptions() but NOT run() — the infinite
loop is never started.  State is driven purely via bus.publish() events.
"""

import asyncio
import sys
from datetime import date
from pathlib import Path

import pytest
from structlog.testing import capture_logs

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.events import (
    EventBus,
    LegFailureEvent,
    OpportunityEvent,
    PositionSnapshot,
    RejectedOpportunityEvent,
    TradeExecutedEvent,
    TradeResolvedEvent,
)
from karbot.core.config import KarbotConfig
from agents.floor.position_tracker import PositionTracker, PAPER_DEFAULT_CAPITAL
from agents.floor.risk_gate import RiskGate


# ── Helpers ───────────────────────────────────────────────────────────────────

def _two_leg_trade(trade_id="t1", price=0.40, qty=100, expected_pnl=50.0):
    """TradeExecutedEvent with two legs. Capital used = price * qty * 2."""
    return TradeExecutedEvent(
        source="test",
        trade_id=trade_id,
        opportunity_id=f"opp-{trade_id}",
        strategy="S1_REBALANCING",
        platform_legs=[
            {
                "platform": "kalshi", "market_id": "m1", "side": "YES",
                "ordered_price": price, "filled_price": price,
                "quantity": qty, "fee_paid": 0.0,
            },
            {
                "platform": "kalshi", "market_id": "m1", "side": "NO",
                "ordered_price": price, "filled_price": price,
                "quantity": qty, "fee_paid": 0.0,
            },
        ],
        total_fee_paid=0.0,
        expected_pnl_usd=expected_pnl,
        paper_mode=True,
    )


def _make_opportunity():
    """Minimal S1 OpportunityEvent. Only check_1 (capital) should reject it."""
    return OpportunityEvent(
        source="test",
        strategy="S1_REBALANCING",
        legs=[{
            "platform": "kalshi", "market_id": "m1",
            "side": "YES", "price": 0.40, "quantity": 10,
        }],
        gross_profit_pct=20.0,
        estimated_fees_pct=14.0,
        estimated_slippage_pct=0.1,
        net_profit_pct=5.7,
        confidence="HIGH",
        capital_required_usd=0.0,   # 0 so check_2 never fires
    )


# ── Test 1: Startup snapshot ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_startup_snapshot_published():
    """run() publishes a PositionSnapshot before any events arrive."""
    config  = KarbotConfig()
    bus     = EventBus()
    tracker = PositionTracker(bus=bus, config=config)

    snapshots = []
    async def _collect(ev):
        snapshots.append(ev)
    bus.subscribe(PositionSnapshot, _collect)

    bus_task = asyncio.create_task(bus.run())
    run_task = asyncio.create_task(tracker.run())

    await asyncio.sleep(0.1)

    run_task.cancel()
    bus_task.cancel()
    await asyncio.gather(run_task, bus_task, return_exceptions=True)

    assert len(snapshots) >= 1, "No PositionSnapshot was published at startup"
    snap = snapshots[0]
    assert snap.deployed_capital_usd == 0.0
    assert snap.free_capital_usd == pytest.approx(PAPER_DEFAULT_CAPITAL)


# ── Test 2: Trade executed updates state ──────────────────────────────────────

@pytest.mark.asyncio
async def test_trade_executed_updates_state():
    """TradeExecutedEvent with two legs updates deployed capital and open positions."""
    config  = KarbotConfig()
    bus     = EventBus()
    tracker = PositionTracker(bus=bus, config=config)
    tracker.register_subscriptions()

    snapshots = []
    async def _collect(ev):
        snapshots.append(ev)
    bus.subscribe(PositionSnapshot, _collect)

    bus_task = asyncio.create_task(bus.run())

    # Two legs: 0.40 * 100 per leg → 40 + 40 = 80 capital deployed
    await bus.publish(_two_leg_trade("t1", price=0.40, qty=100))
    await asyncio.sleep(0.1)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    expected_capital = 0.40 * 100 * 2   # 80.0
    assert tracker._deployed_capital == pytest.approx(expected_capital)
    assert len(tracker._open_positions) == 1
    assert tracker._open_positions[0]["trade_id"] == "t1"
    assert tracker._daily_trades == 1

    assert len(snapshots) >= 1
    last_snap = snapshots[-1]
    assert last_snap.deployed_capital_usd == pytest.approx(expected_capital)


# ── Test 3: Two trades stack correctly ────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_trades_stack_correctly():
    """Two TradeExecutedEvents: deployed_capital is the sum of both."""
    config  = KarbotConfig()
    bus     = EventBus()
    tracker = PositionTracker(bus=bus, config=config)
    tracker.register_subscriptions()

    bus_task = asyncio.create_task(bus.run())

    await bus.publish(_two_leg_trade("t1", price=0.40, qty=100))   # 80.0
    await bus.publish(_two_leg_trade("t2", price=0.50, qty=50))    # 50.0
    await asyncio.sleep(0.1)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    assert tracker._deployed_capital == pytest.approx(80.0 + 50.0)
    assert len(tracker._open_positions) == 2


# ── Test 4: Trade resolved frees capital ──────────────────────────────────────

@pytest.mark.asyncio
async def test_trade_resolved_frees_capital():
    """Execute then resolve a trade: deployed goes to 0, daily_pnl updated."""
    config  = KarbotConfig()
    bus     = EventBus()
    tracker = PositionTracker(bus=bus, config=config)
    tracker.register_subscriptions()

    bus_task = asyncio.create_task(bus.run())

    await bus.publish(_two_leg_trade("t1", price=0.40, qty=100))   # deploys 80
    await asyncio.sleep(0.05)

    await bus.publish(TradeResolvedEvent(
        source="test",
        trade_id="t1",
        market_id="m1",
        platform="kalshi",
        resolution="YES",
        realized_pnl=50.0,
        holding_period_hours=2.0,
    ))
    await asyncio.sleep(0.1)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    assert tracker._deployed_capital == pytest.approx(0.0)
    assert len(tracker._open_positions) == 0
    assert tracker._daily_pnl == pytest.approx(50.0)


# ── Test 5: Leg failure unwinds position ──────────────────────────────────────

@pytest.mark.asyncio
async def test_leg_failure_unwinds_position():
    """Execute then fail a trade: deployed_capital returns to 0, position removed."""
    config  = KarbotConfig()
    bus     = EventBus()
    tracker = PositionTracker(bus=bus, config=config)
    tracker.register_subscriptions()

    bus_task = asyncio.create_task(bus.run())

    await bus.publish(_two_leg_trade("t1", price=0.40, qty=100))   # deploys 80
    await asyncio.sleep(0.05)

    await bus.publish(LegFailureEvent(
        source="test",
        trade_id="t1",
        opportunity_id="opp-t1",
        failed_leg={"platform": "kalshi", "market_id": "m1"},
        filled_legs=[],
        unwind_required=True,
    ))
    await asyncio.sleep(0.1)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    assert tracker._deployed_capital == pytest.approx(0.0)
    assert len(tracker._open_positions) == 0


# ── Test 6: Capital never goes negative ───────────────────────────────────────

@pytest.mark.asyncio
async def test_capital_never_goes_negative():
    """Resolving an unknown trade_id: WARNING logged, deployed_capital stays >= 0."""
    config  = KarbotConfig()
    bus     = EventBus()
    tracker = PositionTracker(bus=bus, config=config)
    tracker.register_subscriptions()
    # Deliberately do NOT publish a TradeExecutedEvent first

    bus_task = asyncio.create_task(bus.run())

    await bus.publish(TradeResolvedEvent(
        source="test",
        trade_id="t-never-existed",
        market_id="m1",
        platform="kalshi",
        resolution="YES",
        realized_pnl=100.0,
        holding_period_hours=1.0,
    ))
    await asyncio.sleep(0.1)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    assert tracker._deployed_capital >= 0.0


# ── Test 7: Risk Gate sees accurate capital (integration) ─────────────────────

@pytest.mark.asyncio
async def test_risk_gate_sees_accurate_capital():
    """
    Integration test — the Phase 2 acceptance test.

    Sequence:
      1. Deploy 45% of $10,000 via TradeExecutedEvent (4,500 USD).
      2. PositionTracker publishes updated PositionSnapshot: deployed=4,500.
      3. RiskGate receives snapshot (deployed_pct=45% > max_capital_locked_pct=40%).
      4. Next OpportunityEvent is rejected with reason=MAX_CAPITAL_LOCKED.

    Before Phase 2, _deployed_capital was always 0 so check_1 always passed
    regardless of real trades. This test cannot pass on the Phase 1 stub.
    """
    config  = KarbotConfig()    # max_capital_locked_pct = 40.0%
    bus     = EventBus()
    tracker = PositionTracker(bus=bus, config=config)   # PAPER_DEFAULT_CAPITAL = 10_000
    gate    = RiskGate(bus=bus, config=config)

    tracker.register_subscriptions()
    gate.register_subscriptions()

    rejections = []
    async def _collect_rejection(ev):
        rejections.append(ev)
    bus.subscribe(RejectedOpportunityEvent, _collect_rejection)

    bus_task = asyncio.create_task(bus.run())

    # Deploy 45% of 10_000 = 4_500.
    # Two legs at price=0.45, qty=5_000 → 0.45 * 5_000 * 2 legs = 4_500.
    # deployed_pct = 4_500 / 10_000 * 100 = 45% which exceeds the 40% limit.
    await bus.publish(_two_leg_trade("t1", price=0.45, qty=5_000))

    # Wait for: TradeExecutedEvent → PositionTracker handler → PositionSnapshot
    # → RiskGate _on_position_snapshot.  All in-process; 0.15s is generous.
    await asyncio.sleep(0.15)

    await bus.publish(_make_opportunity())
    await asyncio.sleep(0.15)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    assert len(rejections) >= 1, (
        "Expected at least one RejectedOpportunityEvent — none received"
    )
    reasons = [r.reason for r in rejections]
    assert "MAX_CAPITAL_LOCKED" in reasons, (
        f"Expected MAX_CAPITAL_LOCKED rejection, got: {reasons}"
    )


# ── Test 8: Daily reset ───────────────────────────────────────────────────────

def test_daily_reset():
    """Daily reset clears _daily_trades and _daily_pnl when UTC date rolls over."""
    config  = KarbotConfig()
    bus     = EventBus()
    tracker = PositionTracker(bus=bus, config=config)

    tracker._daily_trades    = 7
    tracker._daily_pnl       = 250.0
    # Simulate day-change by backdating the last reset
    tracker._last_reset_date = date(2026, 5, 25)

    tracker._maybe_daily_reset()

    assert tracker._daily_trades == 0
    assert tracker._daily_pnl    == 0.0


# ── Test 9: Missing platform_legs handled gracefully ─────────────────────────

@pytest.mark.asyncio
async def test_missing_platform_legs_handled_gracefully():
    """TradeExecutedEvent with empty platform_legs: WARNING logged, state unchanged."""
    config  = KarbotConfig()
    bus     = EventBus()
    tracker = PositionTracker(bus=bus, config=config)
    tracker.register_subscriptions()

    bus_task = asyncio.create_task(bus.run())

    with capture_logs() as cap_logs:
        await bus.publish(TradeExecutedEvent(
            source="test",
            trade_id="t-bad",
            opportunity_id="opp-bad",
            strategy="S1_REBALANCING",
            platform_legs=[],   # empty — should trigger WARNING
            total_fee_paid=0.0,
            expected_pnl_usd=0.0,
            paper_mode=True,
        ))
        await asyncio.sleep(0.1)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    assert tracker._deployed_capital == 0.0,  "State must not change on bad event"
    assert len(tracker._open_positions) == 0, "No position must be added"
    assert tracker._daily_trades == 0,         "Trade count must not increment"

    warning_events = [l for l in cap_logs if l.get("log_level") == "warning"]
    assert any(
        l.get("event") == "trade_executed_missing_platform_legs"
        for l in warning_events
    ), f"Expected warning 'trade_executed_missing_platform_legs' in logs: {cap_logs}"
