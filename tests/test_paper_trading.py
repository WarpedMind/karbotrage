"""
tests/test_paper_trading.py — Paper trading end-to-end test suite

Three scenarios exercised directly against the event bus (no subprocess):

  Scenario 1 (happy path): profitable Kalshi price → ArbScanner detects S1
    opportunity → RiskGate approves → PaperExecutor fills → ComplianceOfficer
    logs trade. Assert 1 CSV row + 1 TradeExecutedEvent audit entry.

  Scenario 2 (rejection): same prices on a different market but PositionSnapshot
    has 90% capital deployed → RiskGate rejects (MAX_CAPITAL_LOCKED) →
    ComplianceOfficer logs rejection. Assert 1 RejectedOpportunityEvent audit
    entry + zero CSV rows.

  Scenario 3 (no opportunity): prices sum > 1.0 → ArbScanner stays silent →
    no downstream events → zero entries in either file.

Notes on fixture prices:
  The Kalshi fee model in arb_scanner.py estimates ~14% round-trip fees on a
  $1 contract pair. Combined bid of 0.47+0.51 = 0.98 leaves only 2% gross
  profit which is eaten by fees. The fixture uses YES=0.40, NO=0.40 (sum=0.80,
  gross=20%, net≈5.7%) so the pipeline actually fires. Scenario 3 uses
  YES=0.52, NO=0.51 (sum=1.03) to confirm the scanner stays silent above 1.0.
"""

import asyncio
import csv
import json
import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path regardless of cwd
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.events import (
    EventBus,
    PriceUpdateEvent,
    PositionSnapshot,
    TradeExecutedEvent,
    TradeResolvedEvent,
)
from karbot.core.config import KarbotConfig, SystemConfig
from agents.floor.arb_scanner import ArbScanner
from agents.floor.risk_gate import RiskGate
from agents.floor.paper_executor import PaperExecutor
from agents.floor.position_tracker import PositionTracker
from agents.management.compliance import ComplianceOfficer

FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "paper_test_prices.json"

# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_fixture(index: int) -> dict:
    with open(FIXTURE) as f:
        return json.load(f)[index]


def _count_csv_rows(csv_path: Path) -> int:
    """Count data rows (excluding header)."""
    with open(csv_path) as f:
        return sum(1 for _ in csv.DictReader(f))


def _audit_entries_of_type(audit_path: Path, event_type: str) -> list:
    entries = []
    with open(audit_path) as f:
        for line in f:
            line = line.strip()
            if line:
                entry = json.loads(line)
                if entry.get("event_type") == event_type:
                    entries.append(entry)
    return entries


async def _run_pipeline(bus: EventBus, agents: list) -> None:
    """Register subscriptions and start the bus dispatcher."""
    for agent in agents:
        agent.register_subscriptions()
    return asyncio.create_task(bus.run(), name="test_bus")


async def _inject_price(bus: EventBus, entry: dict) -> None:
    await bus.publish(PriceUpdateEvent(
        source        = "test",
        platform      = entry["platform"],
        market_id     = entry["market_id"],
        yes_bid       = float(entry["yes_bid"]),
        yes_ask       = float(entry["yes_ask"]),
        no_bid        = float(entry["no_bid"]),
        no_ask        = float(entry["no_ask"]),
        volume_24h    = float(entry.get("volume_24h", 0.0)),
        open_interest = int(entry.get("open_interest", 0)),
        sequence_num  = int(entry.get("sequence_num", 0)),
    ))


# ── Scenario 1: Happy path ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scenario1_happy_path(tmp_path, monkeypatch):
    """
    Scenario 1: profitable price → arb detected → approved → paper fill logged.

    Setup:  PositionSnapshot with $9k free capital (well below 40% cap).
    Expect: 1 row in kalshi_trades.csv, 1 TradeExecutedEvent in audit_trail.
    """
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("agents.management.compliance.LOGS_DIR", logs_dir)

    config = KarbotConfig()  # paper_mode=True, phase=1
    bus = EventBus()

    arb  = ArbScanner(bus=bus, config=config)
    gate = RiskGate(bus=bus, config=config)
    exec_ = PaperExecutor(bus=bus, config=config)
    comp  = ComplianceOfficer(bus=bus, config=config)

    bus_task = await _run_pipeline(bus, [arb, gate, exec_, comp])

    # Satisfy RiskGate check_1: provide a PositionSnapshot with plenty of free capital
    await bus.publish(PositionSnapshot(
        source                = "test",
        total_capital_usd     = 10_000.0,
        deployed_capital_usd  = 1_000.0,   # 10% deployed < 40% limit
        free_capital_usd      = 9_000.0,
        correlation_score     = 0.1,
        daily_pnl_usd         = 0.0,
        daily_trades          = 0,
    ))
    await asyncio.sleep(0.05)  # let PositionSnapshot reach RiskGate

    await _inject_price(bus, _load_fixture(0))

    await asyncio.sleep(0.5)  # allow full pipeline: price → arb → gate → exec → compliance

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    kalshi_csv   = logs_dir / "kalshi_trades.csv"
    audit_trail  = logs_dir / "audit_trail.jsonl"

    assert kalshi_csv.exists(), "kalshi_trades.csv was not created"
    assert audit_trail.exists(), "audit_trail.jsonl was not created"

    rows = _count_csv_rows(kalshi_csv)
    # S1 arb has 2 legs (YES + NO), so 2 rows — one per leg per IRS record
    assert rows == 2, f"Expected 2 trade rows (one per leg), got {rows}"

    trade_entries = _audit_entries_of_type(audit_trail, "TradeExecutedEvent")
    assert len(trade_entries) == 1, (
        f"Expected 1 TradeExecutedEvent in audit_trail, got {len(trade_entries)}"
    )
    assert trade_entries[0]["trade_mode"] == "PAPER"


# ── Scenario 2: Rejection ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scenario2_rejection(tmp_path, monkeypatch):
    """
    Scenario 2: same profitable price on a different market, but capital is
    exhausted — RiskGate rejects with MAX_CAPITAL_LOCKED.

    Setup:  PositionSnapshot with 90% deployed (> 40% max_capital_locked_pct).
    Expect: 1 RejectedOpportunityEvent in audit_trail, 0 rows in kalshi_trades.
    """
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("agents.management.compliance.LOGS_DIR", logs_dir)

    config = KarbotConfig()
    bus = EventBus()

    arb  = ArbScanner(bus=bus, config=config)
    gate = RiskGate(bus=bus, config=config)
    comp  = ComplianceOfficer(bus=bus, config=config)
    # PaperExecutor not needed — nothing should be approved

    bus_task = await _run_pipeline(bus, [arb, gate, comp])

    # Saturate capital: 90% deployed exceeds 40% limit → check_1 fails
    await bus.publish(PositionSnapshot(
        source                = "test",
        total_capital_usd     = 10_000.0,
        deployed_capital_usd  = 9_000.0,   # 90% > 40% limit
        free_capital_usd      = 1_000.0,
        correlation_score     = 0.1,
        daily_pnl_usd         = 0.0,
        daily_trades          = 0,
    ))
    await asyncio.sleep(0.05)

    await _inject_price(bus, _load_fixture(1))  # market KALSHI-TEST-002

    await asyncio.sleep(0.5)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    kalshi_csv  = logs_dir / "kalshi_trades.csv"
    audit_trail = logs_dir / "audit_trail.jsonl"

    assert kalshi_csv.exists(), "kalshi_trades.csv was not created"

    rows = _count_csv_rows(kalshi_csv)
    assert rows == 0, f"Expected 0 trade rows, got {rows} (trade was incorrectly approved)"

    rejected_entries = _audit_entries_of_type(audit_trail, "RejectedOpportunityEvent")
    assert len(rejected_entries) == 1, (
        f"Expected 1 RejectedOpportunityEvent in audit_trail, got {len(rejected_entries)}"
    )


# ── Scenario 3: No opportunity ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scenario3_no_opportunity(tmp_path, monkeypatch):
    """
    Scenario 3: prices sum to 1.03 — no arb profit possible, scanner stays silent.

    Setup:  No PositionSnapshot needed (no opportunity will reach RiskGate).
    Expect: 0 rows in kalshi_trades.csv, no TradeExecuted or Rejected entries
            in audit_trail.
    """
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("agents.management.compliance.LOGS_DIR", logs_dir)

    config = KarbotConfig()
    bus = EventBus()

    arb  = ArbScanner(bus=bus, config=config)
    gate = RiskGate(bus=bus, config=config)
    exec_ = PaperExecutor(bus=bus, config=config)
    comp  = ComplianceOfficer(bus=bus, config=config)

    bus_task = await _run_pipeline(bus, [arb, gate, exec_, comp])

    await _inject_price(bus, _load_fixture(2))  # sum=1.03 — no arb

    await asyncio.sleep(0.3)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    kalshi_csv  = logs_dir / "kalshi_trades.csv"
    audit_trail = logs_dir / "audit_trail.jsonl"

    assert kalshi_csv.exists(), "kalshi_trades.csv was not created"

    rows = _count_csv_rows(kalshi_csv)
    assert rows == 0, f"Expected 0 trade rows, got {rows}"

    trade_entries    = _audit_entries_of_type(audit_trail, "TradeExecutedEvent")
    rejected_entries = _audit_entries_of_type(audit_trail, "RejectedOpportunityEvent")

    assert len(trade_entries) == 0, (
        f"Expected no TradeExecutedEvents, got {len(trade_entries)}"
    )
    assert len(rejected_entries) == 0, (
        f"Expected no RejectedOpportunityEvents, got {len(rejected_entries)}"
        " (price sum > 1.0 should not reach RiskGate)"
    )


# ── Scenario 4: Paper trade resolves after delay ───────────────────────────────

@pytest.mark.asyncio
async def test_paper_trade_resolves_after_delay(tmp_path, monkeypatch):
    """
    Scenario 4: TradeResolvedEvent fires after paper_resolution_delay_seconds.

    Setup:  1-second resolution delay; happy-path price → trade executes.
    Expect: TradeResolvedEvent emitted with correct trade_id and positive realized_pnl.
            PositionTracker deployed_capital returns to 0.0 after resolution.
            PositionTracker open_positions is empty after resolution.
            PositionTracker total_capital increased by realized_pnl.
    """
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("agents.management.compliance.LOGS_DIR", logs_dir)

    config = KarbotConfig(system=SystemConfig(paper_mode=True, paper_resolution_delay_seconds=1))
    bus = EventBus()

    tracker = PositionTracker(bus=bus, config=config)
    arb     = ArbScanner(bus=bus, config=config)
    gate    = RiskGate(bus=bus, config=config)
    exec_   = PaperExecutor(bus=bus, config=config)
    comp    = ComplianceOfficer(bus=bus, config=config)

    resolved_events = []
    async def _collect_resolved(ev):
        resolved_events.append(ev)
    bus.subscribe(TradeResolvedEvent, _collect_resolved)

    executed_events = []
    async def _collect_executed(ev):
        executed_events.append(ev)
    bus.subscribe(TradeExecutedEvent, _collect_executed)

    bus_task = await _run_pipeline(bus, [tracker, arb, gate, exec_, comp])

    # Provide PositionSnapshot so RiskGate can approve
    await bus.publish(PositionSnapshot(
        source="test",
        total_capital_usd=10_000.0,
        deployed_capital_usd=1_000.0,
        free_capital_usd=9_000.0,
        correlation_score=0.1,
        daily_pnl_usd=0.0,
        daily_trades=0,
    ))
    await asyncio.sleep(0.05)

    await _inject_price(bus, _load_fixture(0))
    await asyncio.sleep(0.3)  # pipeline: price → arb → gate → exec

    assert len(executed_events) == 1, (
        f"Expected 1 TradeExecutedEvent before resolution, got {len(executed_events)}"
    )
    executed_trade_id = executed_events[0].trade_id

    # Wait for 1s resolution delay + buffer
    await asyncio.sleep(2.0)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    assert len(resolved_events) == 1, (
        f"Expected 1 TradeResolvedEvent, got {len(resolved_events)}"
    )
    assert resolved_events[0].trade_id == executed_trade_id
    assert resolved_events[0].realized_pnl > 0, (
        "TradeResolvedEvent should have positive realized_pnl"
    )

    # PositionTracker state after resolution
    assert tracker._deployed_capital == pytest.approx(0.0), (
        f"deployed_capital should be 0.0 after resolution, got {tracker._deployed_capital}"
    )
    assert len(tracker._open_positions) == 0, (
        "open_positions should be empty after resolution"
    )
    assert tracker._total_capital > 10_000.0, (
        f"total_capital should have grown after resolution, got {tracker._total_capital}"
    )


# ── Scenario 5: Full paper P&L cycle with two trades ──────────────────────────

@pytest.mark.asyncio
async def test_full_paper_pnl_cycle(tmp_path, monkeypatch):
    """
    Scenario 5: Two trades execute and both resolve. Verify cumulative P&L.

    Expect: total_capital increased by sum of both realized_pnl values.
            deployed_capital is 0.0 after both resolve.
            open_positions is empty after both resolve.
    """
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("agents.management.compliance.LOGS_DIR", logs_dir)

    config = KarbotConfig(system=SystemConfig(paper_mode=True, paper_resolution_delay_seconds=1))
    bus = EventBus()

    tracker = PositionTracker(bus=bus, config=config)
    arb     = ArbScanner(bus=bus, config=config)
    gate    = RiskGate(bus=bus, config=config)
    exec_   = PaperExecutor(bus=bus, config=config)
    comp    = ComplianceOfficer(bus=bus, config=config)

    resolved_events = []
    async def _collect_resolved(ev):
        resolved_events.append(ev)
    bus.subscribe(TradeResolvedEvent, _collect_resolved)

    bus_task = await _run_pipeline(bus, [tracker, arb, gate, exec_, comp])

    initial_capital = tracker._total_capital  # PAPER_DEFAULT_CAPITAL = 10_000

    # Inject two profitable price events (fixtures 0 and 1 are both 0.40/0.40)
    await bus.publish(PositionSnapshot(
        source="test",
        total_capital_usd=10_000.0,
        deployed_capital_usd=0.0,
        free_capital_usd=10_000.0,
        correlation_score=0.0,
        daily_pnl_usd=0.0,
        daily_trades=0,
    ))
    await asyncio.sleep(0.05)

    await _inject_price(bus, _load_fixture(0))   # KALSHI-TEST-001
    await asyncio.sleep(0.2)

    # After first trade, publish fresh snapshot so second trade can also be approved
    await bus.publish(PositionSnapshot(
        source="test",
        total_capital_usd=10_000.0,
        deployed_capital_usd=tracker._deployed_capital,
        free_capital_usd=10_000.0 - tracker._deployed_capital,
        correlation_score=0.0,
        daily_pnl_usd=0.0,
        daily_trades=1,
    ))
    await asyncio.sleep(0.05)

    await _inject_price(bus, _load_fixture(1))   # KALSHI-TEST-002
    await asyncio.sleep(0.2)

    # Wait for both 1-second resolution timers + buffer
    await asyncio.sleep(2.5)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    assert len(resolved_events) == 2, (
        f"Expected 2 TradeResolvedEvents, got {len(resolved_events)}"
    )

    total_realized = sum(e.realized_pnl for e in resolved_events)
    assert total_realized > 0, "Sum of realized_pnl should be positive"

    assert tracker._deployed_capital == pytest.approx(0.0), (
        f"deployed_capital should be 0.0 after both resolve, got {tracker._deployed_capital}"
    )
    assert len(tracker._open_positions) == 0, (
        "open_positions should be empty after both trades resolve"
    )
    assert tracker._total_capital == pytest.approx(initial_capital + total_realized), (
        f"total_capital should be {initial_capital + total_realized:.4f}, "
        f"got {tracker._total_capital:.4f}"
    )
