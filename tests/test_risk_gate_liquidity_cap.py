"""
tests/test_risk_gate_liquidity_cap.py

Covers the 2026-07-13 fix wiring ArbScanner's max_fillable_qty (real order
book depth at the quoted price) into RiskGate's Kelly-criterion position
sizing. Previously, RiskGate sized purely off capital and reported
net_profit_pct with no awareness of how many contracts were actually
available — a $500 position could be sized against a quote backed by a
single contract. See DECISIONS.md Session 26 for the full investigation.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.floor.risk_gate import RiskGateAgent
from core.events import OpportunityEvent, PositionSnapshot
from karbot.core.config import KarbotConfig


def _make_gate(total_capital: float = 10_000.0) -> RiskGateAgent:
    config = KarbotConfig()
    gate = RiskGateAgent(config=config, event_bus=MagicMock())
    gate._current_snapshot = PositionSnapshot(
        total_capital_usd=total_capital,
        deployed_capital_usd=0.0,
        free_capital_usd=total_capital,
        correlation_score=0.0,
    )
    return gate


def _s1_opportunity(net_profit_pct: float, max_fillable_qty: float) -> OpportunityEvent:
    """
    A realistic S1 opportunity: two legs quoting executable ASK prices.

    These tests originally built an OpportunityEvent with NO legs, which
    passed because the old sizing never looked at what it was buying — it
    returned Kelly dollars and nothing downstream converted them. That is
    precisely the unit bug fixed 2026-08-02 (Session 30). Sizing now divides
    a dollar budget by real per-contract cost, so legs are mandatory.
    """
    return OpportunityEvent(
        strategy="S1_REBALANCING",
        net_profit_pct=net_profit_pct,
        max_fillable_qty=max_fillable_qty,
        legs=[
            {"platform": "kalshi", "market_id": "TEST", "side": "YES", "price": 0.45},
            {"platform": "kalshi", "market_id": "TEST", "side": "NO",  "price": 0.45},
        ],
    )


class TestLiquidityCap:
    def test_size_is_capped_to_max_fillable_qty_when_smaller_than_capital(self):
        gate = _make_gate(total_capital=10_000.0)
        # Capital alone would permit hundreds of contracts; depth permits 1.
        event = _s1_opportunity(net_profit_pct=10.0, max_fillable_qty=1.0)
        assert gate._calculate_position_size(event) == 1

    def test_size_is_larger_when_max_fillable_qty_is_generous(self):
        gate = _make_gate(total_capital=10_000.0)
        capped = _s1_opportunity(net_profit_pct=10.0, max_fillable_qty=1.0)
        uncapped = _s1_opportunity(net_profit_pct=10.0, max_fillable_qty=1_000_000.0)
        assert gate._calculate_position_size(capped) < gate._calculate_position_size(uncapped)

    def test_zero_max_fillable_qty_means_no_cap_applied(self):
        """0.0 is the 'strategy didn't compute this' sentinel (e.g. S2/S3/S4,
        which don't populate it yet) — must not be treated as zero liquidity."""
        gate = _make_gate(total_capital=10_000.0)
        event = _s1_opportunity(net_profit_pct=10.0, max_fillable_qty=0.0)
        assert gate._calculate_position_size(event) > 0

    def test_size_respects_the_per_trade_capital_cap(self):
        """The uncapped size must be exactly what the 5% per-trade cap buys at
        the real basket cost — $10,000 x 5% = $500 budget / $0.90 per
        contract = 555 whole contracts, not 500 'dollars' read as contracts."""
        gate = _make_gate(total_capital=10_000.0)
        event = _s1_opportunity(net_profit_pct=10.0, max_fillable_qty=0.0)
        assert gate._calculate_position_size(event) == int(500.0 // 0.90)
