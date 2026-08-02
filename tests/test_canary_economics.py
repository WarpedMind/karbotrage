"""
tests/test_canary_economics.py

Basket economics. This is where a thin arbitrage actually dies, and where the
project's expensive bugs have historically lived: pricing the wrong side of the
book (Session 26), underestimating a ceil'd fee on small orders (Session 28),
and approving fractional contract sizes the exchange cannot fill (Session 28).

The per-order round-up is the load-bearing detail for S5a. At one contract,
Kalshi's ``ceil(0.07 x C x P x (1-P))`` is a **1 cent floor per leg** across
almost the whole price range, so an N-leg basket pays at least N cents per
contract-set before it earns anything -- and that floor amortises away with
size. A basket evaluator that ignores either half of that is wrong in opposite
directions at opposite sizes.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from canary.economics import (
    Leg,
    assert_fee_model_agrees,
    basket_fee,
    evaluate_basket,
    max_contracts,
)


class TestFees:
    def test_one_contract_costs_a_full_cent_on_a_cheap_leg(self):
        """0.07 x 1 x 0.04 x 0.96 = $0.0027 raw, which rounds UP to $0.01."""
        assert basket_fee([Leg("A", "yes", 0.04, 100)], 1) == 0.01

    def test_the_cent_floor_amortises_with_size(self):
        """100 contracts of the same leg pay $0.27, not $1.00."""
        fee = basket_fee([Leg("A", "yes", 0.04, 1000)], 100)
        assert abs(fee - 0.27) < 1e-9
        assert fee < 100 * 0.01

    def test_fee_is_charged_once_per_leg_per_order(self):
        legs = [Leg(f"L{i}", "yes", 0.04, 100) for i in range(10)]
        assert basket_fee(legs, 1) == 0.10

    def test_fee_is_symmetric_in_price(self):
        """P x (1-P) means a leg costs the same fee priced as YES at p or NO at
        1-p, so the side never needs threading into the fee call."""
        assert basket_fee([Leg("A", "yes", 0.30, 10)], 7) == basket_fee(
            [Leg("A", "no", 0.70, 10)], 7
        )

    def test_agrees_with_the_live_trading_path_fee_model(self):
        """If the live KalshiFeeModel is wrong, this must be wrong the same way
        rather than accidentally right."""
        assert assert_fee_model_agrees() is None


class TestSizing:
    def test_size_is_the_minimum_leg_depth_floored_to_whole_contracts(self):
        legs = [Leg("A", "yes", 0.3, 12.7), Leg("B", "no", 0.4, 200.0)]
        assert max_contracts(legs) == 12

    def test_a_leg_with_no_depth_makes_the_basket_unfillable(self):
        legs = [Leg("A", "yes", 0.3, 0.0), Leg("B", "no", 0.4, 500.0)]
        assert max_contracts(legs) == 0

    def test_sub_one_depth_is_zero_not_fractional(self):
        """Session 28 found the live path approving a 0.05-contract trade that
        could not exist on Kalshi. Nothing here may reproduce that."""
        assert max_contracts([Leg("A", "yes", 0.3, 0.9)]) == 0


class TestBasketEvaluation:
    def test_a_real_edge_survives_fees_at_size(self):
        # Two legs totalling $0.90 for a guaranteed $1.00, 500 deep each.
        legs = [Leg("A", "yes", 0.45, 500), Leg("B", "no", 0.45, 500)]
        econ = evaluate_basket(legs, payout_per_set=1.0)
        assert econ.is_candidate
        assert econ.cost_per_set == 0.90
        assert econ.max_contracts == 500
        # 10 cents gross, but the near-the-money fee is ~1.73c per contract per
        # leg -- see test_near_the_money_fee_is_not_small -- so ~6.5c survives.
        assert econ.net_per_set_at_max == pytest.approx(0.0653, abs=5e-4)

    def test_near_the_money_fee_is_not_small(self):
        """Pinned because the first draft of these tests called it 'tiny'.

        0.07 x P x (1-P) peaks at P=0.50, where it is 1.75c per contract. A
        two-leg basket near the money therefore needs more than ~3.5c of gross
        edge to break even AT ANY SIZE -- the per-order round-up is irrelevant
        here, the raw fraction dominates. The cent floor only bites at the
        extremes, where the raw fee falls below a cent.
        """
        assert basket_fee([Leg("A", "yes", 0.50, 10_000)], 10_000) == pytest.approx(175.0)
        assert basket_fee([Leg("A", "yes", 0.02, 10_000)], 10_000) == pytest.approx(13.72)

    def test_an_edge_smaller_than_the_fee_is_not_a_candidate(self):
        """A half-cent edge on two near-the-money legs is hopeless: the fee
        alone is ~3.5c per contract-set. No amount of depth rescues it."""
        legs = [Leg("A", "yes", 0.4975, 5000), Leg("B", "no", 0.4975, 5000)]
        econ = evaluate_basket(legs, payout_per_set=1.0)
        assert econ.net_per_set_at_one < 0
        assert not econ.is_candidate
        assert econ.net_total_at_max <= 0

    def test_a_longshot_edge_below_the_cent_floor_is_rescued_by_depth(self):
        """Where the round-up DOES decide the outcome: cheap legs whose raw fee
        is under a cent. At one contract the ceil'd 2c of fees eats a 1.5c edge;
        at 5000 contracts the same fees are ~0.44c per set and it clears."""
        legs = [Leg("A", "yes", 0.025, 5000), Leg("B", "no", 0.96, 5000)]
        econ = evaluate_basket(legs, payout_per_set=1.0)
        assert econ.cost_per_set == pytest.approx(0.985)
        assert econ.fee_at_one == 0.02
        assert econ.net_per_set_at_one < 0, "underwater at one contract"
        assert econ.is_candidate, "profitable at the size actually available"
        assert econ.net_total_at_max > 0

    def test_no_basket_payout_is_n_minus_one(self):
        """Buying NO on N mutually exclusive legs: at most one YES, so at least
        N-1 legs pay a dollar."""
        legs = [Leg(f"L{i}", "no", 0.90, 1000) for i in range(5)]
        econ = evaluate_basket(legs, payout_per_set=4.0)
        assert econ.cost_per_set == 4.50
        assert not econ.is_candidate  # $4.50 for a guaranteed $4.00
        cheap = [Leg(f"L{i}", "no", 0.70, 1000) for i in range(5)]
        econ2 = evaluate_basket(cheap, payout_per_set=4.0)
        assert econ2.is_candidate  # $3.50 for a guaranteed $4.00

    def test_a_leg_priced_at_one_means_an_empty_book_not_a_free_contract(self):
        assert evaluate_basket([Leg("A", "yes", 1.0, 10)], 1.0) is None
        assert evaluate_basket([Leg("A", "yes", 0.0, 10)], 1.0) is None

    def test_zero_legs_or_zero_payout_is_not_a_basket(self):
        assert evaluate_basket([], 1.0) is None
        assert evaluate_basket([Leg("A", "yes", 0.4, 10)], 0.0) is None

    def test_unfillable_basket_is_never_a_candidate_however_good_the_price(self):
        """Edge on zero available contracts is not edge. This is the depth
        lesson from Session 26 -- a '47% edge' backed by one contract."""
        legs = [Leg("A", "yes", 0.10, 0.0), Leg("B", "no", 0.10, 5000)]
        econ = evaluate_basket(legs, payout_per_set=1.0)
        assert econ.cost_per_set == 0.20
        assert econ.max_contracts == 0
        assert not econ.is_candidate
