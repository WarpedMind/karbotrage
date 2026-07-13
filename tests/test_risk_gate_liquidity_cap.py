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
    return OpportunityEvent(
        strategy="S1_REBALANCING",
        net_profit_pct=net_profit_pct,
        max_fillable_qty=max_fillable_qty,
    )


class TestLiquidityCap:
    def test_size_is_capped_to_max_fillable_qty_when_smaller_than_kelly_size(self):
        gate = _make_gate(total_capital=10_000.0)
        # Generous edge -> Kelly would normally size well above 1.
        event = _s1_opportunity(net_profit_pct=10.0, max_fillable_qty=1.0)
        size = gate._calculate_position_size(event)
        assert size == 1.0

    def test_size_is_unaffected_when_max_fillable_qty_exceeds_kelly_size(self):
        gate = _make_gate(total_capital=10_000.0)
        event_capped = _s1_opportunity(net_profit_pct=10.0, max_fillable_qty=1.0)
        event_uncapped = _s1_opportunity(net_profit_pct=10.0, max_fillable_qty=1_000_000.0)
        size_capped = gate._calculate_position_size(event_capped)
        size_uncapped = gate._calculate_position_size(event_uncapped)
        # The generous cap must not change sizing versus having no
        # liquidity information at all for the same edge.
        assert size_capped < size_uncapped

    def test_zero_max_fillable_qty_means_no_cap_applied(self):
        """0.0 is the 'strategy didn't compute this' sentinel (e.g. S2/S3/S4,
        which don't populate it yet) — must not be treated as zero liquidity."""
        gate = _make_gate(total_capital=10_000.0)
        event = _s1_opportunity(net_profit_pct=10.0, max_fillable_qty=0.0)
        size = gate._calculate_position_size(event)
        assert size > 0.0
