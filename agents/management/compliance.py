"""
agents/management/compliance.py — Compliance Officer Agent
Karbot Rage! | WallStRobotics | v2.0

CANNOT BE DISABLED. Runs in paper and live mode.

Responsibilities:
  1. IRS dual-track trade logging (Kalshi CSV / Polymarket CSV Phase 2)
  2. Append-only audit trail (JSONL — every event, every day)
  3. Regulatory monitoring (CFTC RSS, Federal Register — every 6 hours)
  4. Compliance action log (documents all operator responses)
  5. REGULATORY_HALT enforcement (hard refusal to start if flag is set)
  6. Daily compliance checkpoint

Regulatory context (May 2026):
  - CFTC Letter 26-15 (May 19 2026): New cooperation policy — voluntary
    self-reporting + full cooperation + remediation = path to declination.
    These logs ARE the compliance record. Treat them accordingly.
  - CFTC enforcement priorities: insider trading (#1), manipulation,
    wash trading. CFTC using AI surveillance on prediction markets.
  - Karbot Rage! uses only public data. Arbitrage only. No MNPI.

Tax context (May 2026):
  - IRS has issued NO formal guidance on prediction market classification.
  - Three possible positions: ordinary income, short-term cap gains, §1256.
  - AVOID gambling income classification (OBBBA 90% loss cap, Jan 1 2026).
  - This agent logs all fields needed for any position. CPA decides.
  - Kalshi does NOT issue comprehensive 1099-B. This CSV is the tax record.
"""

import asyncio
import csv
import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from core.events import (
    EventBus,
    TradeExecutedEvent,
    LegFailureEvent,
    RejectedOpportunityEvent,
    RegulatoryAlertEvent,
)
from karbot.core.config import KarbotConfig

logger = logging.getLogger(__name__)

SYSTEM_VERSION = "1.0.0"
LOGS_DIR = Path("logs")

# ── CSV schemas ────────────────────────────────────────────────────────────
KALSHI_CSV_HEADERS = [
    "trade_id", "timestamp_utc", "platform", "market_id",
    "market_description", "side", "contracts", "price_paid",
    "price_received", "fees_paid", "cost_basis", "proceeds",
    "gain_loss", "hold_duration_seconds", "trade_mode", "status", "notes",
]

POLYMARKET_CSV_HEADERS = [
    "trade_id", "timestamp_utc", "platform", "market_id",
    "market_description", "side", "usdc_amount", "price_paid",
    "price_received", "gas_fees_usd", "cost_basis_usdc",
    "proceeds_usdc", "gain_loss_usd", "hold_duration_seconds",
    "trade_mode", "status", "wallet_address", "tx_hash", "notes",
]


def _audit_json_default(obj):
    """Custom JSON serializer for audit trail entries.

    Handles datetime (from event dataclasses) and Enum types (Priority).
    Falls back to str() so we never silently drop data.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "name"):   # Enum (e.g. Priority.HIGH)
        return obj.name
    return str(obj)


class ComplianceOfficer:
    """
    Always-on compliance agent. Cannot be disabled.

    Maintains IRS-grade trade logs, append-only audit trail,
    regulatory monitoring with keyword alerting, documented compliance
    action history, and REGULATORY_HALT enforcement.

    Under CFTC Letter 26-15 (May 2026), voluntary self-reporting +
    full cooperation + remediation = path to declination. These logs
    are the evidence of good-faith operation from day one.
    """

    def __init__(self, bus: EventBus, config: KarbotConfig):
        self.bus = bus
        self.config = config
        self.trade_mode = "PAPER" if config.paper_mode else "LIVE"

        # Check REGULATORY_HALT before doing anything else
        self._check_regulatory_halt()

        self._kalshi_csv = LOGS_DIR / "kalshi_trades.csv"
        self._polymarket_csv = LOGS_DIR / "polymarket_trades.csv"
        self._audit_trail = LOGS_DIR / "audit_trail.jsonl"
        self._compliance_actions = LOGS_DIR / "compliance_actions.jsonl"

        self._ensure_log_files()
        self._last_summary_date = ""

        # Document startup as a compliance action
        self._log_compliance_action(
            action_type="SYSTEM_STARTUP",
            description=(
                f"ComplianceOfficer initialized | "
                f"mode={self.trade_mode} | version={SYSTEM_VERSION}"
            ),
            triggered_by="startup",
        )
        logger.info(
            f"ComplianceOfficer initialized | "
            f"mode={self.trade_mode} | version={SYSTEM_VERSION}"
        )

    # ── Startup safety ─────────────────────────────────────────────────────

    def _check_regulatory_halt(self):
        """
        Refuse to initialize if REGULATORY_HALT is set in config.
        Set this flag manually after reading regulatory guidance that
        requires halting. Bot will not start until cleared.
        """
        halt = getattr(self.config, "regulatory_halt", False)
        halt_reason = getattr(self.config, "regulatory_halt_reason", "")
        if halt:
            msg = (
                "\n\n"
                "╔══════════════════════════════════════════════════════╗\n"
                "║           REGULATORY HALT — SYSTEM STOPPED           ║\n"
                "╠══════════════════════════════════════════════════════╣\n"
                f"║  Reason: {halt_reason:<44} ║\n"
                "║                                                      ║\n"
                "║  The operator set REGULATORY_HALT = true in          ║\n"
                "║  config.yaml after reviewing regulatory guidance.    ║\n"
                "║                                                      ║\n"
                "║  To resume:                                          ║\n"
                "║  1. Read the relevant regulatory guidance            ║\n"
                "║  2. Consult legal counsel if needed                  ║\n"
                "║  3. Set regulatory_halt: false in config.yaml        ║\n"
                "║  4. Document your decision in compliance_actions     ║\n"
                "║  5. Restart                                          ║\n"
                "╚══════════════════════════════════════════════════════╝\n"
            )
            logger.critical(msg)
            raise SystemExit(1)

    def _ensure_log_files(self):
        """Create logs directory and all required files if absent."""
        LOGS_DIR.mkdir(exist_ok=True)

        # Kalshi CSV — active Phase 1 tax record
        if not self._kalshi_csv.exists():
            with open(self._kalshi_csv, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=KALSHI_CSV_HEADERS).writeheader()
            logger.info(f"Created {self._kalshi_csv}")

        # Polymarket CSV — Phase 2 only, headers only for now
        if not self._polymarket_csv.exists():
            with open(self._polymarket_csv, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=POLYMARKET_CSV_HEADERS).writeheader()
            logger.info(
                f"Created {self._polymarket_csv} "
                f"(Phase 2 gate — headers only, no data)"
            )

        # All other log files
        for path in (self._audit_trail, self._compliance_actions):
            if not path.exists():
                path.touch()
                logger.info(f"Created {path}")

    # ── Registration and main loop ─────────────────────────────────────────

    def register_subscriptions(self):
        """Subscribe to all compliance-relevant events."""
        self.bus.subscribe(TradeExecutedEvent, self.handle_trade_executed)
        self.bus.subscribe(LegFailureEvent, self.handle_leg_failure)
        self.bus.subscribe(RejectedOpportunityEvent, self.handle_rejected)
        self.bus.subscribe(RegulatoryAlertEvent, self.handle_regulatory_alert)
        logger.info("ComplianceOfficer subscriptions registered")

    async def run(self):
        """
        Main loop — runs forever.
        Trade logging is event-driven via subscriptions.
        Periodic task: daily compliance summary.
        Regulatory monitoring is handled by RegulatoryIntelligenceAgent.
        """
        logger.info("ComplianceOfficer running — cannot be disabled")
        while True:
            await self._daily_summary_if_due()
            await asyncio.sleep(60)

    # ── Event handlers ─────────────────────────────────────────────────────

    async def handle_trade_executed(self, event: TradeExecutedEvent):
        """Log completed trade to kalshi_trades.csv and audit trail."""
        try:
            row = self._build_trade_row(event)
            self._append_kalshi_csv(row)
            self._append_audit(
                event_type="TradeExecutedEvent",
                platform=getattr(event, "platform", "KALSHI"),
                market_id=getattr(event, "market_id", ""),
                payload=self._safe_dict(event),
            )
            logger.info(
                f"[COMPLIANCE] Trade logged | "
                f"market={getattr(event, 'market_id', '?')} | "
                f"mode={self.trade_mode} | "
                f"gain_loss={row.get('gain_loss', '?')}"
            )
        except Exception as e:
            logger.error(
                f"[COMPLIANCE] CRITICAL: Failed to log trade: {e}",
                exc_info=True,
            )

    async def handle_leg_failure(self, event: LegFailureEvent):
        """
        Log leg failure — critical for audit trail and tax records.
        Also important for demonstrating the system responded
        appropriately to failed trades (CFTC audit defense).
        """
        try:
            row = self._build_failure_row(event)
            self._append_kalshi_csv(row)
            self._append_audit(
                event_type="LegFailureEvent",
                platform=getattr(event, "platform", "KALSHI"),
                market_id=getattr(event, "market_id", ""),
                payload=self._safe_dict(event),
            )
            logger.warning(
                f"[COMPLIANCE] Leg failure logged | "
                f"market={getattr(event, 'market_id', '?')}"
            )
        except Exception as e:
            logger.error(
                f"[COMPLIANCE] Failed to log leg failure: {e}",
                exc_info=True,
            )

    async def handle_rejected(self, event: RejectedOpportunityEvent):
        """
        Log rejected opportunities to audit trail.
        Important for: strategy tuning AND demonstrating the system
        actively rejected questionable trades (CFTC defense).
        """
        try:
            self._append_audit(
                event_type="RejectedOpportunityEvent",
                platform=getattr(event, "platform", "KALSHI"),
                market_id=getattr(event, "market_id", ""),
                payload=self._safe_dict(event),
            )
        except Exception as e:
            logger.error(
                f"[COMPLIANCE] Failed to log rejection: {e}",
                exc_info=True,
            )

    async def handle_regulatory_alert(self, event: RegulatoryAlertEvent):
        """
        Log RegulatoryAlertEvent from RegulatoryIntelligenceAgent.
        Records to compliance_actions.jsonl and audit trail.
        This is the compliance record that demonstrates good-faith
        regulatory monitoring under CFTC Letter 26-15.
        """
        try:
            self._log_compliance_action(
                action_type="REGULATORY_ALERT",
                description=(
                    f"Urgency {event.urgency} | {event.summary or event.raw_title}"
                ),
                triggered_by="regulatory_intelligence_agent",
                details={
                    "urgency": event.urgency,
                    "source_url": event.source_url,
                    "affected": event.affected,
                    "recommended_action": event.recommended_action,
                    "raw_title": event.raw_title,
                    "cycle_type": event.cycle_type,
                },
            )
            self._append_audit(
                event_type="RegulatoryAlertEvent",
                platform="REGULATORY",
                market_id="",
                payload=self._safe_dict(event),
            )
            if event.urgency >= 4:
                logger.warning(
                    f"[COMPLIANCE] Regulatory alert urgency={event.urgency} logged | "
                    f"{event.summary or event.raw_title}"
                )
            else:
                logger.info(
                    f"[COMPLIANCE] Regulatory alert urgency={event.urgency} logged"
                )
        except Exception as e:
            logger.error(
                f"[COMPLIANCE] Failed to log regulatory alert: {e}",
                exc_info=True,
            )

    # ── Compliance action log ──────────────────────────────────────────────

    def _log_compliance_action(
        self,
        action_type: str,
        description: str,
        triggered_by: str,
        details: dict = None,
    ):
        """
        Log an operator/system compliance action to
        compliance_actions.jsonl.

        This file documents WHAT THE SYSTEM DID in response to
        compliance events. Under CFTC Letter 26-15, demonstrated
        good-faith compliance effort and prompt remediation are
        the path to declination if a violation is ever discovered.
        This log is that evidence.

        action_type values:
          SYSTEM_STARTUP       — bot started
          REGULATORY_HALT_SET  — operator set halt flag
          REGULATORY_HALT_CLEARED — operator cleared halt flag
          TRADING_PAUSED       — manual pause initiated
          TRADING_RESUMED      — manual pause cleared
          ALERT_REVIEWED       — operator documented alert review
          SELF_REPORT_INITIATED — operator initiated CFTC self-report
          REMEDIATION_COMPLETED — operator documented fix
          CONFIG_CHANGE        — compliance-relevant config changed
        """
        entry = {
            "action_id": str(uuid.uuid4()),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "action_type": action_type,
            "description": description,
            "triggered_by": triggered_by,
            "trade_mode": self.trade_mode,
            "system_version": SYSTEM_VERSION,
            "details": details or {},
        }
        with open(self._compliance_actions, "a") as f:
            f.write(json.dumps(entry) + "\n")

    # ── Daily summary ──────────────────────────────────────────────────────

    async def _daily_summary_if_due(self):
        """Write daily compliance checkpoint at midnight UTC."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today == self._last_summary_date:
            return
        self._last_summary_date = today
        self._append_audit(
            event_type="DailySummary",
            platform="ALL",
            market_id="",
            payload={
                "date": today,
                "trade_mode": self.trade_mode,
                "system_version": SYSTEM_VERSION,
                "note": "Daily compliance checkpoint",
            },
        )
        logger.info(f"[COMPLIANCE] Daily checkpoint: {today}")

    # ── CSV and audit trail ────────────────────────────────────────────────

    def _build_trade_row(self, event) -> dict:
        contracts = getattr(event, "contracts", 0)
        price_paid = getattr(event, "price_paid", 0.0)
        price_received = getattr(event, "price_received", 0.0)
        fees = getattr(event, "fees_paid", 0.0)
        cost_basis = round(contracts * price_paid, 6)
        proceeds = round(contracts * price_received, 6)
        gain_loss = round(proceeds - cost_basis - fees, 6)
        return {
            "trade_id": getattr(event, "trade_id", str(uuid.uuid4())),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "platform": getattr(event, "platform", "KALSHI"),
            "market_id": getattr(event, "market_id", ""),
            "market_description": getattr(event, "market_description", ""),
            "side": getattr(event, "side", ""),
            "contracts": contracts,
            "price_paid": price_paid,
            "price_received": price_received,
            "fees_paid": fees,
            "cost_basis": cost_basis,
            "proceeds": proceeds,
            "gain_loss": gain_loss,
            "hold_duration_seconds": getattr(
                event, "hold_duration_seconds", 0
            ),
            "trade_mode": self.trade_mode,
            "status": getattr(event, "status", "FILLED"),
            "notes": getattr(event, "notes", ""),
        }

    def _build_failure_row(self, event) -> dict:
        return {
            "trade_id": getattr(event, "trade_id", str(uuid.uuid4())),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "platform": getattr(event, "platform", "KALSHI"),
            "market_id": getattr(event, "market_id", ""),
            "market_description": getattr(event, "market_description", ""),
            "side": getattr(event, "side", ""),
            "contracts": getattr(event, "contracts", 0),
            "price_paid": getattr(event, "price_paid", 0.0),
            "price_received": 0.0,
            "fees_paid": getattr(event, "fees_paid", 0.0),
            "cost_basis": 0.0,
            "proceeds": 0.0,
            "gain_loss": 0.0,
            "hold_duration_seconds": 0,
            "trade_mode": self.trade_mode,
            "status": "LEG_FAILURE",
            "notes": getattr(event, "reason", "leg_failure"),
        }

    def _append_kalshi_csv(self, row: dict):
        """Append-only. Never overwrite. This is the IRS tax record."""
        with open(self._kalshi_csv, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=KALSHI_CSV_HEADERS).writerow(row)

    def _append_audit(
        self, event_type: str, platform: str,
        market_id: str, payload: dict
    ):
        """Append one JSON line to audit_trail.jsonl."""
        entry = {
            "log_id": str(uuid.uuid4()),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "trade_mode": self.trade_mode,
            "platform": platform,
            "market_id": market_id,
            "payload": payload,
            "system_version": SYSTEM_VERSION,
            "agent": "ComplianceOfficer",
        }
        with open(self._audit_trail, "a") as f:
            f.write(json.dumps(entry, default=_audit_json_default) + "\n")

    @staticmethod
    def _safe_dict(event) -> dict:
        try:
            return asdict(event)
        except Exception:
            try:
                return vars(event)
            except Exception:
                return {"error": "could_not_serialize_event"}
