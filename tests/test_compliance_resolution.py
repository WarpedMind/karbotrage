"""
tests/test_compliance_resolution.py — ComplianceOfficer trade DB logging

Tests:
  1. Two CSV leg-rows for a trade_id get gain_loss = realized_pnl/2,
     status = "RESOLVED", hold_duration_seconds updated.
  2. Unmatched trade_id logs a warning and does not raise or corrupt the CSV.
  3. compliance.db trades row is updated correctly on resolution.
  4. TradeResolvedEvent is written to audit_trail.jsonl.
"""

import asyncio
import csv
import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.events import (
    EventBus,
    PriceUpdateEvent,
    TradeExecutedEvent,
    TradeResolvedEvent,
    PositionSnapshot,
)
from karbot.core.config import KarbotConfig, SystemConfig
from agents.floor.arb_scanner import ArbScanner
from agents.floor.risk_gate import RiskGate
from agents.floor.paper_executor import PaperExecutor
from agents.management.compliance import ComplianceOfficer, KALSHI_CSV_HEADERS

FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "paper_test_prices.json"


# ── helpers ────────────────────────────────────────────────────────────────────

def _load_fixture(index: int) -> dict:
    with open(FIXTURE) as f:
        return json.load(f)[index]


def _csv_rows(csv_path: Path) -> list:
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def _audit_entries(audit_path: Path, event_type: str) -> list:
    entries = []
    with open(audit_path) as f:
        for line in f:
            line = line.strip()
            if line:
                entry = json.loads(line)
                if entry.get("event_type") == event_type:
                    entries.append(entry)
    return entries


async def _run_pipeline(bus: EventBus, agents: list):
    for agent in agents:
        agent.register_subscriptions()
    return asyncio.create_task(bus.run(), name="test_bus")


# ── Test 1: CSV rows updated correctly ────────────────────────────────────────

@pytest.mark.asyncio
async def test_trade_resolved_updates_csv_gain_loss(tmp_path, monkeypatch):
    """
    After TradeExecutedEvent (2 legs) + TradeResolvedEvent with known realized_pnl,
    both CSV rows should have gain_loss = realized_pnl/2 and status = "RESOLVED".
    """
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("agents.management.compliance.LOGS_DIR", logs_dir)

    config = KarbotConfig(system=SystemConfig(paper_mode=True, paper_resolution_delay_seconds=1))
    bus = EventBus()

    arb   = ArbScanner(bus=bus, config=config)
    gate  = RiskGate(bus=bus, config=config)
    exec_ = PaperExecutor(bus=bus, config=config)
    comp  = ComplianceOfficer(bus=bus, config=config)

    executed_events = []
    async def _capture_executed(ev):
        executed_events.append(ev)
    bus.subscribe(TradeExecutedEvent, _capture_executed)

    bus_task = await _run_pipeline(bus, [arb, gate, exec_, comp])

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

    # Inject profitable price → should produce TradeExecutedEvent with 2 legs
    entry = _load_fixture(0)
    await bus.publish(__import__("core.events", fromlist=["PriceUpdateEvent"]).PriceUpdateEvent(
        source="test",
        platform=entry["platform"],
        market_id=entry["market_id"],
        yes_bid=float(entry["yes_bid"]),
        yes_ask=float(entry["yes_ask"]),
        no_bid=float(entry["no_bid"]),
        no_ask=float(entry["no_ask"]),
        volume_24h=float(entry.get("volume_24h", 0.0)),
        open_interest=int(entry.get("open_interest", 0)),
        sequence_num=int(entry.get("sequence_num", 0)),
    ))
    await asyncio.sleep(0.4)

    assert len(executed_events) == 1, f"Expected 1 TradeExecutedEvent, got {len(executed_events)}"
    trade_id = executed_events[0].trade_id

    kalshi_csv = logs_dir / "kalshi_trades.csv"
    rows_before = _csv_rows(kalshi_csv)
    assert len(rows_before) == 2, f"Expected 2 leg rows at fill time, got {len(rows_before)}"
    assert all(r["status"] == "FILLED" for r in rows_before)
    assert all(float(r["gain_loss"]) == 0.0 for r in rows_before)

    # Wait for 1s paper resolution delay + buffer
    await asyncio.sleep(2.0)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    rows_after = _csv_rows(kalshi_csv)
    resolved_rows = [r for r in rows_after if r["trade_id"] == trade_id]
    assert len(resolved_rows) == 2, f"Expected 2 resolved rows, got {len(resolved_rows)}"

    pnl_values = [float(r["gain_loss"]) for r in resolved_rows]
    hold_values = [float(r["hold_duration_seconds"]) for r in resolved_rows]

    assert pnl_values[0] == pytest.approx(pnl_values[1], rel=1e-5), (
        f"Both legs should have equal gain_loss split, got {pnl_values}"
    )
    total_pnl = sum(pnl_values)
    assert total_pnl > 0, f"Total gain_loss should be positive, got {total_pnl}"
    assert all(s == "RESOLVED" for s in [r["status"] for r in resolved_rows]), (
        f"All resolved rows should have status=RESOLVED"
    )
    assert all(h > 0 for h in hold_values), (
        f"hold_duration_seconds should be > 0 after resolution, got {hold_values}"
    )


# ── Test 2: Unmatched trade_id — warning, no raise, CSV untouched ─────────────

@pytest.mark.asyncio
async def test_trade_resolved_unmatched_trade_id(tmp_path, monkeypatch, caplog):
    """
    A TradeResolvedEvent for an unknown trade_id should log a warning
    and not raise or corrupt the CSV.
    """
    import logging
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("agents.management.compliance.LOGS_DIR", logs_dir)

    config = KarbotConfig()
    bus = EventBus()
    comp = ComplianceOfficer(bus=bus, config=config)
    comp.register_subscriptions()

    bus_task = asyncio.create_task(bus.run())

    # Write a known CSV row for a different trade_id so we can confirm it's untouched
    other_trade_id = "other-trade-123"
    kalshi_csv = logs_dir / "kalshi_trades.csv"
    with open(kalshi_csv, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=KALSHI_CSV_HEADERS)
        writer.writerow({h: "" for h in KALSHI_CSV_HEADERS} | {
            "trade_id": other_trade_id,
            "status": "FILLED",
            "gain_loss": "0.0",
        })

    rows_before = _csv_rows(kalshi_csv)
    assert len(rows_before) == 1

    with caplog.at_level(logging.WARNING):
        await bus.publish(TradeResolvedEvent(
            trade_id="nonexistent-trade-xyz",
            market_id="KALSHI-TEST-FAKE",
            platform="kalshi",
            resolution="YES",
            realized_pnl=5.0,
            holding_period_hours=1.0,
        ))
        await asyncio.sleep(0.3)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    rows_after = _csv_rows(kalshi_csv)
    assert len(rows_after) == 1, "CSV should be unchanged"
    assert rows_after[0]["trade_id"] == other_trade_id, "Existing row should be untouched"
    assert rows_after[0]["status"] == "FILLED", "Existing row status should be untouched"

    warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("trade_resolved_no_matching_rows" in m for m in warning_msgs), (
        f"Expected warning about no matching rows; got: {warning_msgs}"
    )


# ── Test 3: compliance.db trades row updated on resolution ────────────────────

@pytest.mark.asyncio
async def test_trade_resolved_updates_db(tmp_path, monkeypatch):
    """
    TradeResolvedEvent should UPDATE the trades row in compliance.db:
    status='RESOLVED', resolved_at set, realized_pnl and holding_period_hours updated.
    """
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("agents.management.compliance.LOGS_DIR", logs_dir)

    config = KarbotConfig()
    bus = EventBus()
    comp = ComplianceOfficer(bus=bus, config=config)
    comp.register_subscriptions()

    # Pre-seed a trade row in the DB
    db_path = logs_dir / "compliance.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY,
        trade_id TEXT,
        opportunity_id TEXT,
        strategy TEXT,
        platform TEXT,
        market_id TEXT,
        side TEXT,
        ordered_price REAL,
        filled_price REAL,
        quantity REAL,
        fee_paid REAL,
        expected_pnl_usd REAL,
        realized_pnl REAL,
        paper_mode INTEGER DEFAULT 1,
        status TEXT DEFAULT "OPEN",
        timestamp TEXT,
        opened_at TEXT,
        resolved_at TEXT,
        holding_period_hours REAL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.execute(
        "INSERT INTO trades (trade_id, status, realized_pnl, platform) VALUES (?, ?, ?, ?)",
        ("test-trade-db-001", "OPEN", 0.0, "kalshi"),
    )
    conn.commit()
    conn.close()

    bus_task = asyncio.create_task(bus.run())

    await bus.publish(TradeResolvedEvent(
        trade_id="test-trade-db-001",
        market_id="KALSHI-TEST-001",
        platform="kalshi",
        resolution="YES",
        realized_pnl=42.75,
        holding_period_hours=2.5,
    ))
    await asyncio.sleep(0.5)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT status, realized_pnl, holding_period_hours, resolved_at "
        "FROM trades WHERE trade_id = ?",
        ("test-trade-db-001",),
    ).fetchone()
    conn.close()

    assert row is not None, "Trade row should exist in DB"
    status, realized_pnl, holding_period_hours, resolved_at = row
    assert status == "RESOLVED", f"Expected status=RESOLVED, got {status}"
    assert realized_pnl == pytest.approx(42.75), f"Expected realized_pnl=42.75, got {realized_pnl}"
    assert holding_period_hours == pytest.approx(2.5), f"Expected 2.5h, got {holding_period_hours}"
    assert resolved_at is not None and resolved_at != "", "resolved_at should be set"


# ── Test 4: TradeResolvedEvent written to audit_trail.jsonl ───────────────────

@pytest.mark.asyncio
async def test_trade_resolved_written_to_audit_trail(tmp_path, monkeypatch):
    """
    TradeResolvedEvent should produce a TradeResolvedEvent entry in audit_trail.jsonl.
    """
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("agents.management.compliance.LOGS_DIR", logs_dir)

    config = KarbotConfig()
    bus = EventBus()
    comp = ComplianceOfficer(bus=bus, config=config)
    comp.register_subscriptions()

    bus_task = asyncio.create_task(bus.run())

    await bus.publish(TradeResolvedEvent(
        trade_id="audit-trail-test-001",
        market_id="KALSHI-TEST-001",
        platform="kalshi",
        resolution="YES",
        realized_pnl=10.0,
        holding_period_hours=0.5,
    ))
    await asyncio.sleep(0.3)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    audit_trail = logs_dir / "audit_trail.jsonl"
    entries = _audit_entries(audit_trail, "TradeResolvedEvent")
    assert len(entries) == 1, f"Expected 1 TradeResolvedEvent audit entry, got {len(entries)}"
    assert entries[0]["payload"]["trade_id"] == "audit-trail-test-001"


# ── Test 5: TradeExecutedEvent inserts a FILLED row in compliance.db ──────────

@pytest.mark.asyncio
async def test_trade_executed_inserts_db_row(tmp_path, monkeypatch):
    """
    After TradeExecutedEvent, compliance.db should contain a row with
    status='FILLED' and the correct trade_id.
    """
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("agents.management.compliance.LOGS_DIR", logs_dir)

    config = KarbotConfig(system=SystemConfig(paper_mode=True, paper_resolution_delay_seconds=300))
    bus = EventBus()

    arb   = ArbScanner(bus=bus, config=config)
    gate  = RiskGate(bus=bus, config=config)
    exec_ = PaperExecutor(bus=bus, config=config)
    comp  = ComplianceOfficer(bus=bus, config=config)

    executed_events = []
    async def _capture(ev):
        executed_events.append(ev)
    bus.subscribe(TradeExecutedEvent, _capture)

    for agent in [arb, gate, exec_, comp]:
        agent.register_subscriptions()
    bus_task = asyncio.create_task(bus.run())

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

    entry = _load_fixture(0)
    await bus.publish(PriceUpdateEvent(
        source="test",
        platform=entry["platform"],
        market_id=entry["market_id"],
        yes_bid=float(entry["yes_bid"]),
        yes_ask=float(entry["yes_ask"]),
        no_bid=float(entry["no_bid"]),
        no_ask=float(entry["no_ask"]),
        volume_24h=float(entry.get("volume_24h", 0.0)),
        open_interest=int(entry.get("open_interest", 0)),
        sequence_num=int(entry.get("sequence_num", 0)),
    ))
    await asyncio.sleep(0.4)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    assert len(executed_events) == 1, f"Expected 1 TradeExecutedEvent, got {len(executed_events)}"
    trade_id = executed_events[0].trade_id

    db_path = logs_dir / "compliance.db"
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT trade_id, status, realized_pnl FROM trades WHERE trade_id = ?",
        (trade_id,),
    ).fetchone()
    conn.close()

    assert row is not None, f"Expected a DB row for trade_id={trade_id}"
    assert row[0] == trade_id
    assert row[1] == "FILLED", f"Expected status=FILLED, got {row[1]}"
    assert row[2] == pytest.approx(0.0), f"Expected realized_pnl=0.0 at fill, got {row[2]}"


# ── Test 6: TradeExecuted then TradeResolved — same DB row transitions ────────

@pytest.mark.asyncio
async def test_trade_executed_then_resolved_db_lifecycle(tmp_path, monkeypatch):
    """
    After TradeExecutedEvent + TradeResolvedEvent (1s delay), the same DB row
    should show status='RESOLVED' and realized_pnl > 0.
    """
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("agents.management.compliance.LOGS_DIR", logs_dir)

    config = KarbotConfig(system=SystemConfig(paper_mode=True, paper_resolution_delay_seconds=1))
    bus = EventBus()

    arb   = ArbScanner(bus=bus, config=config)
    gate  = RiskGate(bus=bus, config=config)
    exec_ = PaperExecutor(bus=bus, config=config)
    comp  = ComplianceOfficer(bus=bus, config=config)

    executed_events = []
    async def _capture(ev):
        executed_events.append(ev)
    bus.subscribe(TradeExecutedEvent, _capture)

    for agent in [arb, gate, exec_, comp]:
        agent.register_subscriptions()
    bus_task = asyncio.create_task(bus.run())

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

    entry = _load_fixture(0)
    await bus.publish(PriceUpdateEvent(
        source="test",
        platform=entry["platform"],
        market_id=entry["market_id"],
        yes_bid=float(entry["yes_bid"]),
        yes_ask=float(entry["yes_ask"]),
        no_bid=float(entry["no_bid"]),
        no_ask=float(entry["no_ask"]),
        volume_24h=float(entry.get("volume_24h", 0.0)),
        open_interest=int(entry.get("open_interest", 0)),
        sequence_num=int(entry.get("sequence_num", 0)),
    ))
    await asyncio.sleep(0.4)

    assert len(executed_events) == 1
    trade_id = executed_events[0].trade_id

    # Confirm FILLED row exists before resolution
    db_path = logs_dir / "compliance.db"
    conn = sqlite3.connect(str(db_path))
    pre = conn.execute(
        "SELECT status FROM trades WHERE trade_id = ?", (trade_id,)
    ).fetchone()
    conn.close()
    assert pre is not None and pre[0] == "FILLED"

    # Wait for 1s resolution delay + buffer
    await asyncio.sleep(2.0)

    bus_task.cancel()
    await asyncio.gather(bus_task, return_exceptions=True)

    conn = sqlite3.connect(str(db_path))
    post = conn.execute(
        "SELECT status, realized_pnl, resolved_at FROM trades WHERE trade_id = ?",
        (trade_id,),
    ).fetchone()
    conn.close()

    assert post is not None
    status, realized_pnl, resolved_at = post
    assert status == "RESOLVED", f"Expected RESOLVED, got {status}"
    assert realized_pnl > 0, f"Expected realized_pnl > 0, got {realized_pnl}"
    assert resolved_at is not None and resolved_at != "", "resolved_at should be set"
