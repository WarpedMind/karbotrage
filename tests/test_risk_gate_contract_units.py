"""
tests/test_risk_gate_contract_units.py

Covers the 2026-08-02 (Session 30) unit-system fix: RiskGate now sizes in
INTEGER CONTRACTS rather than returning Kelly dollars that downstream agents
silently consumed as a contract count.

Background (DECISIONS.md Session 28 entry 3, Session 30): the old
_calculate_position_size returned `total_capital * kelly_fraction` — dollars —
which PaperExecutor wrote straight into each leg's "quantity" and
PositionTracker booked as `price x quantity`. It only ever balanced because an
S1 YES+NO pair costs ~$1, making dollars and contracts coincide numerically by
accident of that strategy's shape. A single-leg position breaks it by 1/price.

Also covers the two sizing models: cap-based for riskless strategies (Kelly at
a hardcoded p=0.95 imposed a hidden ~5.26% minimum edge, rejecting exactly the
small edges that are the only plausible ones) and the Kelly closed form
f* = (p - c)/(1 - c) for statistical strategies fed a real model probability.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.floor.arb_scanner import KalshiFeeModel
from agents.floor.risk_gate import RiskGateAgent
from core.events import OpportunityEvent, PositionSnapshot
from karbot.core.config import KarbotConfig


def _make_gate(total_capital: float = 10_000.0, free: float = None) -> RiskGateAgent:
    gate = RiskGateAgent(config=KarbotConfig(), event_bus=MagicMock())
    gate._current_snapshot = PositionSnapshot(
        total_capital_usd=total_capital,
        deployed_capital_usd=0.0,
        free_capital_usd=total_capital if free is None else free,
        correlation_score=0.0,
    )
    return gate


def _opportunity(strategy: str, leg_prices, **kw) -> OpportunityEvent:
    return OpportunityEvent(
        strategy=strategy,
        legs=[{"platform": "kalshi", "market_id": "M", "side": "YES", "price": p}
              for p in leg_prices],
        **kw,
    )


class TestIntegerContractUnits:
    def test_size_is_an_integer_contract_count(self):
        gate = _make_gate()
        size = gate._calculate_position_size(
            _opportunity("S1_REBALANCING", [0.45, 0.45], net_profit_pct=5.0))
        assert isinstance(size, int)
        assert size == int(500.0 // 0.90)

    def test_single_leg_position_is_not_off_by_one_over_price(self):
        """The exact failure the old code had: at a $0.30 contract, a $500
        budget buys 1666 contracts, not '500'. The old dollars-as-contracts
        reading understated size by a factor of 1/price (3.3x here)."""
        gate = _make_gate()
        size = gate._calculate_position_size(
            _opportunity("S6_MODEL_DIVERGENCE", [0.30],
                         model_probability=0.95))
        assert size == pytest.approx(int(500.0 // 0.30), abs=1)
        assert size != 500

    def test_sub_one_contract_sizes_to_zero(self):
        """Kalshi trades whole contracts, minimum 1. Session 27's 0.05-contract
        paper trade could not have existed live."""
        gate = _make_gate(total_capital=10.0)   # 5% cap = $0.50 budget
        size = gate._calculate_position_size(
            _opportunity("S1_REBALANCING", [0.60, 0.60], net_profit_pct=5.0))
        assert size == 0

    def test_missing_leg_prices_size_to_zero_rather_than_guessing(self):
        gate = _make_gate()
        assert gate._calculate_position_size(
            _opportunity("S1_REBALANCING", [], net_profit_pct=5.0)) == 0


class TestRisklessSizingIgnoresEdgeMagnitude:
    def test_tiny_edge_is_not_rejected_by_a_hidden_kelly_floor(self):
        """The whole point of the fix: a 0.6% net edge on a riskless basket
        must size positively. The old Kelly p=0.95 path returned 0 for
        anything under ~5.26%, silently overriding s1_min_net_profit_pct."""
        gate = _make_gate()
        assert gate._calculate_position_size(
            _opportunity("S1_REBALANCING", [0.45, 0.45], net_profit_pct=0.6)) > 0

    def test_riskless_size_does_not_scale_with_claimed_edge(self):
        """Riskless payoff is locked at fill; caps bind, not edge magnitude."""
        gate = _make_gate()
        small = gate._calculate_position_size(
            _opportunity("S1_REBALANCING", [0.45, 0.45], net_profit_pct=0.6))
        large = gate._calculate_position_size(
            _opportunity("S1_REBALANCING", [0.45, 0.45], net_profit_pct=12.0))
        assert small == large


class TestStatisticalKellySizing:
    def test_kelly_uses_the_model_probability_closed_form(self):
        """f* = (p - c)/(1 - c); at p=0.80, c=0.50 that is 0.60, scaled by the
        configured fractional Kelly (0.15) -> 9% of capital."""
        gate = _make_gate(total_capital=10_000.0)
        size = gate._calculate_position_size(
            _opportunity("S6_MODEL_DIVERGENCE", [0.50], model_probability=0.80))
        expected_usd = min(500.0, 10_000.0 * ((0.80 - 0.50) / 0.50) * 0.15)
        assert size == int(expected_usd // 0.50)

    def test_no_edge_when_model_probability_does_not_beat_price(self):
        """p <= c means the market already prices it at or above the model."""
        gate = _make_gate()
        assert gate._calculate_position_size(
            _opportunity("S6_MODEL_DIVERGENCE", [0.60], model_probability=0.55)) == 0

    def test_missing_model_probability_sizes_to_zero_not_a_default(self):
        """A variance-bearing trade with no probability must not fall back to a
        hardcoded pseudo-probability — that was the Session 28 bug."""
        gate = _make_gate()
        assert gate._calculate_position_size(
            _opportunity("S6_MODEL_DIVERGENCE", [0.50])) == 0

    def test_bigger_model_edge_sizes_larger(self):
        gate = _make_gate(total_capital=1_000_000.0)
        weak = gate._calculate_position_size(
            _opportunity("S6_MODEL_DIVERGENCE", [0.50], model_probability=0.55))
        strong = gate._calculate_position_size(
            _opportunity("S6_MODEL_DIVERGENCE", [0.50], model_probability=0.75))
        assert 0 < weak < strong


class TestKalshiFeeRounding:
    def test_fee_rounds_up_to_the_next_cent(self):
        """Published table: 1 contract at $0.10 pays $0.01 (continuous model
        would say $0.0063), and at $0.50 pays $0.02."""
        assert KalshiFeeModel.taker_fee_dollars(0.10, 1) == pytest.approx(0.01)
        assert KalshiFeeModel.taker_fee_dollars(0.50, 1) == pytest.approx(0.02)

    def test_fee_matches_published_table_at_100_contracts(self):
        """Published table, 100 contracts: $0.50 -> $1.75, $0.10 -> $0.63,
        $0.30 -> $1.47. These amortise the round-up almost entirely."""
        assert KalshiFeeModel.taker_fee_dollars(0.50, 100) == pytest.approx(1.75)
        assert KalshiFeeModel.taker_fee_dollars(0.10, 100) == pytest.approx(0.63)
        assert KalshiFeeModel.taker_fee_dollars(0.30, 100) == pytest.approx(1.47)

    def test_round_up_dominates_on_tiny_orders(self):
        """A 1-contract fill at 10c pays 10% of face value in fees — the
        continuous model says 0.63%. This is why small liquidity-capped orders
        must be priced with the ceil'd fee (Session 28)."""
        continuous = KalshiFeeModel.taker_fee_fraction(0.10) * 1
        real = KalshiFeeModel.taker_fee_dollars(0.10, 1)
        assert real > continuous * 1.5

    def test_zero_and_boundary_prices_are_free(self):
        assert KalshiFeeModel.taker_fee_dollars(0.0, 100) == 0.0
        assert KalshiFeeModel.taker_fee_dollars(1.0, 100) == 0.0
        assert KalshiFeeModel.taker_fee_dollars(0.5, 0) == 0.0
