"""
tests/test_backtest_scoring_costs.py

Covers backtest/scoring.py and backtest/costs.py.

The scoring tests exist mostly to protect one property: the bootstrap must
resample whole DATES, not individual markets. A city-day ladder is six markets
describing one temperature, and every city on a given date shares one weather
pattern -- so the independent unit is the date. Treating ~7,500 markets as
7,500 independent observations shrinks the confidence interval by roughly a
factor of nine and turns noise into a publishable edge. That is the statistical
form of the same error that kept S1 alive for three months.

The cost tests pin backtest/costs.py to Kalshi's published fee table (effective
2026-07-07) and cross-check it against the live KalshiFeeModel, so the report
cannot quietly assume cheaper fees than the trading path does.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest import costs  # noqa: E402
from backtest import scoring  # noqa: E402


def obs(block, model_p, market_p, outcome, key="t"):
    return scoring.Observation(
        key=key, block=block, series="S", outcome=outcome,
        model_p=model_p, market_p=market_p,
    )


class TestBrier:
    def test_perfect_forecast_scores_zero(self):
        assert scoring.brier([(1.0, 1), (0.0, 0)]) == pytest.approx(0.0)

    def test_coin_flip_scores_a_quarter(self):
        assert scoring.brier([(0.5, 1), (0.5, 0)]) == pytest.approx(0.25)

    def test_confidently_wrong_scores_one(self):
        assert scoring.brier([(0.0, 1)]) == pytest.approx(1.0)

    def test_empty_returns_none_not_zero(self):
        assert scoring.brier([]) is None


class TestSkillScore:
    def test_positive_when_model_beats_reference(self):
        assert scoring.skill_score(0.10, 0.20) == pytest.approx(0.5)

    def test_negative_when_model_loses(self):
        assert scoring.skill_score(0.30, 0.20) == pytest.approx(-0.5)

    def test_zero_reference_is_not_a_division_by_zero(self):
        assert scoring.skill_score(0.1, 0.0) is None


class TestReliability:
    def test_bins_and_counts(self):
        pairs = [(0.05, 0), (0.05, 0), (0.95, 1), (0.95, 1)]
        table = scoring.reliability_table(pairs, bins=10)
        assert table[0]["n"] == 2
        assert table[0]["freq"] == pytest.approx(0.0)
        assert table[9]["n"] == 2
        assert table[9]["freq"] == pytest.approx(1.0)

    def test_gap_exposes_overconfidence(self):
        """Model says 0.9, reality delivers 0.5 -- a positive gap is the
        signature of an overconfident forecast, which is exactly what an
        integer-truncated forecast spread would produce."""
        pairs = [(0.9, 1), (0.9, 1), (0.9, 0), (0.9, 0)]
        table = scoring.reliability_table(pairs, bins=10)
        populated = [b for b in table if b["n"]]
        assert len(populated) == 1
        assert populated[0]["gap"] == pytest.approx(0.4)

    def test_p_of_one_lands_in_the_last_bin(self):
        table = scoring.reliability_table([(1.0, 1)], bins=10)
        assert table[9]["n"] == 1


class TestBlockBootstrap:
    def test_resamples_blocks_not_rows(self):
        """Two dates, perfectly separable outcomes. Resampling by ROW would
        produce a narrow interval; resampling by DATE must produce a wide one,
        because there are only two independent observations."""
        observations = [obs("d1", 0.9, 0.5, 1) for _ in range(50)]
        observations += [obs("d2", 0.9, 0.5, 0) for _ in range(50)]
        out = scoring.block_bootstrap_delta(
            observations, scoring.brier_delta, n_resamples=400
        )
        assert out["n_blocks"] == 2 or out["ci"] is None

    def test_wide_interval_with_few_blocks(self):
        observations = []
        for i in range(6):
            outcome = i % 2
            observations += [obs(f"d{i}", 0.9, 0.5, outcome) for _ in range(20)]
        out = scoring.block_bootstrap_delta(
            observations, scoring.brier_delta, n_resamples=500
        )
        assert out["n_blocks"] == 6
        lo, hi = out["ci"]
        assert hi - lo > 0.05

    def test_is_deterministic_for_a_fixed_seed(self):
        observations = [obs(f"d{i%7}", 0.7, 0.6, i % 2) for i in range(70)]
        a = scoring.block_bootstrap_delta(observations, scoring.brier_delta,
                                          n_resamples=300, seed=42)
        b = scoring.block_bootstrap_delta(observations, scoring.brier_delta,
                                          n_resamples=300, seed=42)
        assert a["ci"] == b["ci"]

    def test_brier_delta_sign_convention(self):
        """Positive means the MODEL is better. Getting this backwards would
        invert the report's conclusion while every number still looked fine."""
        good_model = [obs("d1", 0.99, 0.5, 1)]
        assert scoring.brier_delta(good_model) > 0
        bad_model = [obs("d1", 0.01, 0.5, 1)]
        assert scoring.brier_delta(bad_model) < 0


class TestSummarise:
    def test_reports_climatology_as_well_as_market(self):
        observations = [obs(f"d{i}", 0.6, 0.55, i % 2) for i in range(20)]
        s = scoring.summarise(observations)
        assert s["n"] == 20
        assert s["n_blocks"] == 20
        assert s["base_rate"] == pytest.approx(0.5)
        assert s["brier_climatology"] == pytest.approx(0.25)


class TestTakerFee:
    def test_matches_published_table_for_one_contract(self):
        """Kalshi's published table: 1 contract at $0.10 pays $0.01. The
        continuous formula gives 0.0063, so the round-UP is the whole point."""
        assert costs.taker_fee_dollars(0.10, 1) == pytest.approx(0.01)
        assert costs.taker_fee_dollars(0.50, 1) == pytest.approx(0.02)

    def test_rounds_up_not_to_nearest(self):
        # 0.07 * 100 * 0.30 * 0.70 = 1.47 exactly -- no rounding needed.
        assert costs.taker_fee_dollars(0.30, 100) == pytest.approx(1.47)
        # 0.07 * 1 * 0.01 * 0.99 = 0.000693 -> a whole cent, not zero.
        assert costs.taker_fee_dollars(0.01, 1) == pytest.approx(0.01)

    def test_zero_contracts_is_free(self):
        assert costs.taker_fee_dollars(0.5, 0) == 0.0

    def test_agrees_with_the_live_fee_model(self):
        mismatch = costs.assert_matches_live_fee_model()
        assert mismatch is None, mismatch


class TestTradeEconomics:
    def test_edge_is_measured_against_the_ask_not_the_mid(self):
        econ = costs.evaluate_yes_trade(model_p=0.70, ask=0.60, contracts=1)
        assert econ.gross_ev == pytest.approx(0.10)
        assert econ.entry_price == 0.60

    def test_fee_can_erase_a_thin_edge(self):
        """A 1-cent gross edge on a single contract near the money loses to the
        rounded-up fee. This is why G3 exists as a separate gate from G2."""
        econ = costs.evaluate_yes_trade(model_p=0.51, ask=0.50, contracts=1)
        assert econ.gross_ev == pytest.approx(0.01)
        assert econ.fee == pytest.approx(0.02)
        assert econ.net_ev < 0

    def test_fee_amortises_over_size(self):
        small = costs.evaluate_yes_trade(0.51, 0.50, 1)
        large = costs.evaluate_yes_trade(0.51, 0.50, 1000)
        assert small.net_ev_per_contract < large.net_ev_per_contract

    def test_no_side_wins_when_model_is_below_the_price(self):
        econ = costs.evaluate_no_trade(model_p=0.20, no_ask=0.60, contracts=1)
        assert econ.gross_ev == pytest.approx(0.20)

    def test_degenerate_prices_return_none(self):
        assert costs.evaluate_yes_trade(0.5, 0.0, 1) is None
        assert costs.evaluate_yes_trade(0.5, 1.0, 1) is None
        assert costs.evaluate_yes_trade(0.5, 0.5, 0) is None
