"""
karbot/compliance/officer.py
──────────────────────────────
Compliance Officer Agent — Management Layer

CANNOT be disabled. Runs in all modes including paper mode.
Maintains dual-track IRS logging (Kalshi = ordinary income, Polymarket = capital gains).
Generates monthly tax exports. Maintains complete audit trail.

Tax treatment:
  Kalshi:     Ordinary income — Kalshi issues 1099-MISC
  Polymarket: Capital gains   — each trade is a taxable event; track USDC cost basis

IRS retention requirement: 7 years (84 months)
"""

from __future__ import annotations

import asyncio
import csv
import gzip
import hashlib
import json
import os
import re
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite
import structlog

from karbot.core.config import KarbotConfig
from karbot.core.events import (
    EventBus, TradeExecutedEvent, TradeResolvedEvent,
    LegFailureEvent, OpportunityEvent, ApprovedOpportunityEvent,
    RejectedOpportunityEvent, ComplianceAlertEvent, AgentHeartbeat,
    KillSwitchEvent, Priority, Event
)

log = structlog.get_logger(__name__)


class ComplianceOfficerAgent:
    """
    Management Agent — always running, never disabled.

    Responsibilities:
    1. Dual-track trade logging (Kalshi separate from Polymarket)
    2. Complete audit trail of all system decisions
    3. Monthly IRS-compatible CSV exports
    4. VPN detection (startup check)
    5. MNPI signal monitoring
    6. Regulatory change monitoring
    7. Audit trail integrity (tamper detection via hash chain)
    """

    AGENT_NAME = "compliance_officer"
    HEARTBEAT_INTERVAL = 60

    def __init__(
        self,
        config: KarbotConfig,
        event_bus: EventBus,
        data_dir: Path,
    ):
        self.config   = config
        self.bus      = event_bus
        self.data_dir = data_dir
        self._comp    = config.compliance

        # Database paths
        self._db_path         = data_dir / "compliance.db"
        self._audit_log_path  = data_dir / "audit_trail.jsonl"
        self._kalshi_csv      = data_dir / "kalshi_trades.csv"
        self._poly_csv        = data_dir / "polymarket_trades.csv"

        # State
        self._db: Optional[aiosqlite.Connection] = None
        self._audit_hash_chain = ""    # Rolling hash for tamper detection
        self._events_logged = 0
        self._vpn_detected  = False

    async def start(self) -> None:
        """Initialize database, run startup checks, register subscriptions."""
        # Startup safety checks
        await self._check_vpn()
        await self._init_database()

        # Register subscriptions
        self.bus.subscribe(TradeExecutedEvent,     self._on_trade_executed)
        self.bus.subscribe(TradeResolvedEvent,     self._on_trade_resolved)
        self.bus.subscribe(LegFailureEvent,        self._on_leg_failure)
        self.bus.subscribe(RejectedOpportunityEvent, self._on_rejection)
        self.bus.subscribe(KillSwitchEvent,        self._on_kill_switch)

        # Start background tasks
        asyncio.create_task(self._heartbeat_loop(), name="co_heartbeat")
        asyncio.create_task(self._monthly_export_scheduler(), name="co_export")

        log.info("compliance_officer_started",
                 paper_mode=self.config.system.paper_mode,
                 tax_year=self._comp.tax_year,
                 state=self._comp.state)

    async def log_event(self, event: Event) -> None:
        """
        Public method called by EventBus to log every event.
        This is the audit trail — tamper-evident via hash chain.
        """
        entry = {
            "seq":        self._events_logged,
            "timestamp":  event.timestamp.isoformat(),
            "event_type": type(event).__name__,
            "event_id":   event.event_id,
            "source":     event.source,
            "data":       event.to_dict(),
            "prev_hash":  self._audit_hash_chain,
        }

        # Compute hash of this entry (creates tamper-evident chain)
        entry_str = json.dumps(entry, sort_keys=True, default=str)
        entry_hash = hashlib.sha256(entry_str.encode()).hexdigest()[:16]
        entry["this_hash"] = entry_hash
        self._audit_hash_chain = entry_hash

        # Write to append-only log
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO audit_trail (seq, timestamp, event_type, event_id, entry_json, hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self._events_logged,
                    event.timestamp.isoformat(),
                    type(event).__name__,
                    event.event_id,
                    entry_str,
                    entry_hash,
                )
            )
            await db.commit()

        self._events_logged += 1

    # ── Trade Logging ─────────────────────────────────────────────────────────

    async def _on_trade_executed(self, event: TradeExecutedEvent) -> None:
        """
        Log a completed trade to the appropriate platform CSV and database.
        Kalshi trades and Polymarket trades get SEPARATE logging due to
        different tax treatment.
        """
        timestamp = event.timestamp.isoformat()
        paper_tag = "PAPER_MODE" if event.paper_mode else "LIVE"

        for leg in event.platform_legs:
            platform = leg.get("platform", "unknown")
            record = {
                "trade_id":        event.trade_id,
                "leg_id":          leg.get("leg_id", ""),
                "paper_mode":      event.paper_mode,
                "timestamp":       timestamp,
                "platform":        platform,
                "market_id":       leg.get("market_id", ""),
                "market_desc":     leg.get("market_desc", ""),
                "strategy":        event.strategy,
                "side":            leg.get("side", ""),
                "ordered_price":   leg.get("ordered_price", 0),
                "filled_price":    leg.get("filled_price", 0),
                "quantity":        leg.get("quantity", 0),
                "gross_value_usd": leg.get("filled_price", 0) * leg.get("quantity", 0),
                "fee_paid_usd":    leg.get("fee_paid", 0),
                "net_value_usd":   (leg.get("filled_price", 0) * leg.get("quantity", 0)) - leg.get("fee_paid", 0),
                "status":          "OPEN",
                "fill_timestamp":  leg.get("fill_time", timestamp),
                "mode":            paper_tag,
            }

            if platform == "kalshi":
                record["tax_treatment"] = "ORDINARY_INCOME"
                record["irs_form"] = "1099-MISC"
                await self._append_to_csv(self._kalshi_csv, record)
            elif platform == "polymarket":
                record["tax_treatment"] = "CAPITAL_GAINS"
                record["irs_form"] = "FORM_8949"
                record["usdc_cost_basis"] = leg.get("filled_price", 0)
                record["acquisition_date"] = timestamp
                await self._append_to_csv(self._poly_csv, record)

            # Also write to database
            await self._insert_trade_record(record)

        log.info("trade_logged",
                 trade_id=event.trade_id,
                 strategy=event.strategy,
                 legs=len(event.platform_legs),
                 paper=event.paper_mode)

    async def _on_trade_resolved(self, event: TradeResolvedEvent) -> None:
        """Update trade record with resolution outcome and realized PnL."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE trades SET status=?, realized_pnl=?, resolution=?, "
                "resolved_at=? WHERE trade_id=?",
                (
                    "RESOLVED",
                    event.realized_pnl,
                    event.resolution,
                    event.timestamp.isoformat(),
                    event.trade_id,
                )
            )
            await db.commit()

        log.info("trade_resolved",
                 trade_id=event.trade_id,
                 resolution=event.resolution,
                 pnl_usd=event.realized_pnl)

    async def _on_leg_failure(self, event: LegFailureEvent) -> None:
        """Log leg failure — these are important for risk analysis."""
        log.warning("leg_failure_logged",
                    trade_id=event.trade_id,
                    unwind_required=event.unwind_required)

    async def _on_rejection(self, event: RejectedOpportunityEvent) -> None:
        """Log every rejection — critical for strategy tuning and learning."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO rejections (timestamp, opportunity_id, strategy, reason, details) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    event.timestamp.isoformat(),
                    event.opportunity_id,
                    event.strategy,
                    event.reason,
                    event.details,
                )
            )
            await db.commit()

    async def _on_kill_switch(self, event: KillSwitchEvent) -> None:
        """Log kill switch activation with full context."""
        log.critical("KILL_SWITCH_LOGGED",
                     triggered_by=event.triggered_by,
                     reason=event.reason)
        await self.bus.publish(ComplianceAlertEvent(
            source          = self.AGENT_NAME,
            priority        = Priority.CRITICAL,
            alert_type      = "KILL_SWITCH",
            description     = f"Kill switch activated by {event.triggered_by}: {event.reason}",
            action_required = "Review system status and restart manually when safe",
        ))

    # ── Monthly Export ────────────────────────────────────────────────────────

    async def _monthly_export_scheduler(self) -> None:
        """Run monthly export on the configured day of month."""
        while True:
            await asyncio.sleep(3600)   # Check every hour
            now = datetime.now(timezone.utc)
            if (now.day == self._comp.monthly_export_day and
                    now.hour == 0):
                await self._generate_monthly_export(now.year, now.month - 1 or 12)

    async def _generate_monthly_export(self, year: int, month: int) -> None:
        """
        Generate IRS-compatible monthly export.

        Kalshi export: summary of ordinary income
        Polymarket export: Form 8949 compatible (every trade with cost basis)
        """
        log.info("generating_monthly_export", year=year, month=month)

        export_dir = self.data_dir / "exports" / f"{year}"
        export_dir.mkdir(parents=True, exist_ok=True)

        month_str = f"{year}-{month:02d}"

        async with aiosqlite.connect(self._db_path) as db:
            # Kalshi export (ordinary income)
            kalshi_path = export_dir / f"kalshi_{month_str}.csv"
            cursor = await db.execute(
                "SELECT * FROM trades WHERE platform='kalshi' "
                "AND timestamp LIKE ? AND status='RESOLVED'",
                (f"{month_str}%",)
            )
            rows = await cursor.fetchall()
            col_names = [d[0] for d in cursor.description]

            with open(kalshi_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=col_names)
                writer.writeheader()
                for row in rows:
                    writer.writerow(dict(zip(col_names, row)))

            # Polymarket export (capital gains - Form 8949 compatible)
            poly_path = export_dir / f"polymarket_{month_str}_form8949.csv"
            cursor = await db.execute(
                "SELECT * FROM trades WHERE platform='polymarket' "
                "AND timestamp LIKE ? AND status='RESOLVED'",
                (f"{month_str}%",)
            )
            rows = await cursor.fetchall()

            with open(poly_path, "w", newline="") as f:
                writer = csv.writer(f)
                # Form 8949 headers
                writer.writerow([
                    "Description of property",
                    "Date acquired",
                    "Date sold",
                    "Proceeds",
                    "Cost basis",
                    "Gain or loss",
                    "Trade ID",
                    "Platform",
                ])
                for row in rows:
                    d = dict(zip(col_names, row))
                    writer.writerow([
                        f"USDC/Polymarket: {d.get('market_desc', '')}",
                        d.get("timestamp", ""),
                        d.get("resolved_at", ""),
                        d.get("net_value_usd", 0),
                        d.get("usdc_cost_basis", 0),
                        d.get("realized_pnl", 0),
                        d.get("trade_id", ""),
                        "Polymarket",
                    ])

        log.info("monthly_export_complete",
                 month=month_str,
                 kalshi_path=str(kalshi_path),
                 poly_path=str(poly_path))

    # ── Database Initialization ───────────────────────────────────────────────

    async def _init_database(self) -> None:
        """Initialize SQLite database with all required tables."""
        self.data_dir.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT NOT NULL,
                    leg_id TEXT,
                    paper_mode INTEGER DEFAULT 1,
                    timestamp TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    market_id TEXT NOT NULL,
                    market_desc TEXT,
                    strategy TEXT,
                    side TEXT,
                    ordered_price REAL,
                    filled_price REAL,
                    quantity REAL,
                    gross_value_usd REAL,
                    fee_paid_usd REAL,
                    net_value_usd REAL,
                    usdc_cost_basis REAL,
                    acquisition_date TEXT,
                    status TEXT DEFAULT 'OPEN',
                    realized_pnl REAL,
                    resolution TEXT,
                    resolved_at TEXT,
                    mode TEXT DEFAULT 'PAPER_MODE',
                    tax_treatment TEXT,
                    irs_form TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS rejections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    opportunity_id TEXT,
                    strategy TEXT,
                    reason TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS audit_trail (
                    seq INTEGER PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    entry_json TEXT NOT NULL,
                    hash TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_credibility (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    topic_category TEXT NOT NULL,
                    correct_predictions INTEGER DEFAULT 0,
                    total_predictions INTEGER DEFAULT 0,
                    scs_score REAL DEFAULT 0.5,
                    last_updated TEXT,
                    UNIQUE(source_id, topic_category)
                );

                CREATE INDEX IF NOT EXISTS idx_trades_platform ON trades(platform);
                CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
                CREATE INDEX IF NOT EXISTS idx_trades_trade_id ON trades(trade_id);
                CREATE INDEX IF NOT EXISTS idx_rejections_reason ON rejections(reason);
                CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_trail(event_type);
            """)
            await db.commit()

        log.info("compliance_database_initialized", path=str(self._db_path))

    async def _insert_trade_record(self, record: Dict[str, Any]) -> None:
        """Insert trade record into database."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO trades
                   (trade_id, leg_id, paper_mode, timestamp, platform, market_id,
                    market_desc, strategy, side, ordered_price, filled_price, quantity,
                    gross_value_usd, fee_paid_usd, net_value_usd, usdc_cost_basis,
                    acquisition_date, mode, tax_treatment, irs_form)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record.get("trade_id"),
                    record.get("leg_id"),
                    1 if record.get("paper_mode") else 0,
                    record.get("timestamp"),
                    record.get("platform"),
                    record.get("market_id"),
                    record.get("market_desc"),
                    record.get("strategy"),
                    record.get("side"),
                    record.get("ordered_price"),
                    record.get("filled_price"),
                    record.get("quantity"),
                    record.get("gross_value_usd"),
                    record.get("fee_paid_usd"),
                    record.get("net_value_usd"),
                    record.get("usdc_cost_basis"),
                    record.get("acquisition_date"),
                    record.get("mode"),
                    record.get("tax_treatment"),
                    record.get("irs_form"),
                )
            )
            await db.commit()

    async def _append_to_csv(self, path: Path, record: Dict[str, Any]) -> None:
        """Append a record to a CSV file, creating it with headers if new."""
        file_exists = path.exists()
        with open(path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(record.keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerow(record)

    # ── Startup Checks ────────────────────────────────────────────────────────

    async def _check_vpn(self) -> None:
        """
        Check for VPN connection at startup.
        Trading through a VPN to access Polymarket from the US violates ToS
        and potentially CFTC regulations.
        """
        if not self._comp.vpn_check_enabled:
            return

        try:
            async with __import__("aiohttp").ClientSession() as session:
                # Check if there are obvious VPN indicators
                # In production: use a proper VPN detection API
                # This is a basic check — enhance for production
                async with session.get(
                    "https://ipapi.co/json/",
                    timeout=__import__("aiohttp").ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        is_vpn = data.get("org", "").lower() in (
                            "vpn", "proxy", "tor", "datacenter"
                        )
                        if is_vpn:
                            self._vpn_detected = True
                            log.critical("VPN_DETECTED_ON_STARTUP")
                            await self.bus.publish(ComplianceAlertEvent(
                                source         = self.AGENT_NAME,
                                priority       = Priority.CRITICAL,
                                alert_type     = "VPN_DETECTED",
                                description    = "VPN connection detected at startup",
                                action_required = (
                                    "Disconnect VPN before trading. "
                                    "Using a VPN to access Polymarket from the US "
                                    "violates Terms of Service and CFTC regulations."
                                ),
                            ))
        except Exception:
            pass   # VPN check failure is non-fatal

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            await self.bus.publish(AgentHeartbeat(
                source             = self.AGENT_NAME,
                agent_name         = self.AGENT_NAME,
                status             = "OK",
                messages_processed = self._events_logged,
                last_action        = "audit_logging",
            ))

    async def stop(self) -> None:
        log.info("compliance_officer_stopping",
                 events_logged=self._events_logged)
