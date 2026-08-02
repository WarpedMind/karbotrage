"""
tests/test_backtest_probability.py

Covers backtest/probability.py and backtest/kalshi_history.py -- the step that
turns an NBM temperature forecast into a Kalshi market probability.

This is where the S6 harness could most easily fool itself, and every test here
corresponds to a specific way of being wrong by half a degree or by a sign:

* Kalshi settles on a WHOLE-DEGREE observation, and "greater than 85" means
  "86 or above" (Kalshi's own yes_sub_title says so). The threshold in
  continuous terms is therefore 85.5, not 85. Skipping that continuity
  correction shifts every probability by roughly half a forecast standard
  deviation, because the spread on these markets is only 1-3 F. Half a sigma is
  not a rounding detail -- it is larger than any edge being claimed.

* "less" markets carry their threshold in cap_strike and leave floor_strike
  NULL, the opposite of "greater". Reading floor_strike returns None, and the
  None was originally being skipped silently -- which removed 1,255 of 7,560
  markets, i.e. the entire low tail of every city-day ladder, while every
  printed diagnostic still looked clean.

* An ask of 1.00 against a bid of 0.00 is the absence of a market, not a 50%
  probability. Letting it through as a mid injects fake baseline error exactly
  where the model would look best.

The settlement-rule tests below are cross-checked against real Kalshi data by
backtest/resolve_and_verify.py, which replays settles_yes() against all 7,565
settled markets and requires a 100% match before any modelling runs.
"""

import math
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest import kalshi_history as kh  # noqa: E402
from backtest import probability as prob  # noqa: E402


class TestNormalCdf:
    def test_matches_known_values(self):
        assert prob.norm_cdf(0.0) == pytest.approx(0.5)
        assert prob.norm_cdf(1.0) == pytest.approx(0.8413447, abs=1e-6)
        assert prob.norm_cdf(-1.96) == pytest.approx(0.0249979, abs=1e-6)

    def test_symmetry(self):
        for z in (0.3, 1.1, 2.7):
            assert prob.norm_cdf(z) + prob.norm_cdf(-z) == pytest.approx(1.0)


class TestContinuityCorrection:
    def test_greater_threshold_is_half_a_degree_below_the_next_integer(self):
        """'greater than 85' resolves YES iff the reported high is 86+, i.e.
        the continuous threshold is 85.5. With mu exactly 85.5 the answer must
        be 0.5 -- if the correction were missing it would be well off."""
        p = prob.gaussian_probability("greater", 85, None, mu=85.5, sigma=2.0)
        assert p == pytest.approx(0.5, abs=1e-9)

    def test_missing_correction_would_shift_materially_at_realistic_spread(self):
        """Guard on the magnitude, not just the direction: at the sigma these
        markets actually have, the half-degree is worth ~10 probability points."""
        corrected = prob.gaussian_probability("greater", 85, None, mu=85.0, sigma=2.0)
        naive = 1.0 - prob.norm_cdf((85.0 - 85.0) / 2.0)  # no +1, no -0.5
        assert abs(corrected - naive) > 0.08

    def test_less_threshold_mirrors_greater(self):
        """'less than 78' resolves YES iff the high is 77 or below."""
        p = prob.gaussian_probability("less", None, 78, mu=77.5, sigma=2.0)
        assert p == pytest.approx(0.5, abs=1e-9)


class TestStrikeTypes:
    def test_less_uses_cap_strike_not_floor_strike(self):
        """Regression: reading floor_strike for a 'less' market returns None,
        which silently dropped the low tail of every ladder."""
        assert prob.gaussian_probability("less", None, 78, mu=70.0, sigma=2.0) is not None
        assert prob.settles_yes("less", None, 78, 77) is True
        assert prob.settles_yes("less", None, 78, 78) is False

    def test_greater_uses_floor_strike(self):
        assert prob.settles_yes("greater", 85, None, 86) is True
        assert prob.settles_yes("greater", 85, None, 85) is False

    def test_between_is_inclusive_on_both_ends(self):
        assert prob.settles_yes("between", 84, 85, 84) is True
        assert prob.settles_yes("between", 84, 85, 85) is True
        assert prob.settles_yes("between", 84, 85, 83) is False
        assert prob.settles_yes("between", 84, 85, 86) is False

    def test_unknown_strike_type_returns_none_rather_than_guessing(self):
        assert prob.settles_yes("mystery", 84, 85, 84) is None
        assert prob.gaussian_probability("mystery", 84, 85, 84.0, 2.0) is None


class TestLadderConsistency:
    def test_a_full_ladder_probability_sums_to_one(self):
        """A city-day ladder is an exhaustive partition -- confirmed on all
        1,261 real city-days, exactly one YES each. The model's probabilities
        must therefore also sum to 1, or the probability model is internally
        inconsistent regardless of how well it scores."""
        mu, sigma = 80.0, 3.0
        legs = [
            ("less", None, 76),
            ("between", 76, 77),
            ("between", 78, 79),
            ("between", 80, 81),
            ("between", 82, 83),
            ("greater", 83, None),
        ]
        total = sum(
            prob.gaussian_probability(t, f, c, mu, sigma) for t, f, c in legs
        )
        assert total == pytest.approx(1.0, abs=1e-9)

    def test_ladder_settles_exactly_once_for_any_observation(self):
        legs = [
            ("less", None, 76),
            ("between", 76, 77),
            ("between", 78, 79),
            ("between", 80, 81),
            ("between", 82, 83),
            ("greater", 83, None),
        ]
        for observed in range(60, 100):
            wins = sum(
                1 for t, f, c in legs if prob.settles_yes(t, f, c, observed)
            )
            assert wins == 1, (observed, wins)


class TestSigmaFloor:
    def test_zero_spread_does_not_produce_a_point_mass(self):
        """NBM publishes the spread as an INTEGER, so a real spread below 0.5 F
        is published as 0. Treated literally that makes every probability
        exactly 0 or 1 and the Brier score a lottery on rounding."""
        p = prob.gaussian_probability("greater", 85, None, mu=85.0, sigma=0.0)
        assert 0.0 < p < 1.0

    def test_floor_is_half_the_quantisation_step(self):
        assert prob.SD_FLOOR == 0.5


class TestQuantileModel:
    def _dist(self):
        rows = {
            "TXNMN": [80],
            "TXNSD": [3],
            "TXNP1": [76],
            "TXNP2": [78],
            "TXNP5": [80],
            "TXNP7": [82],
            "TXNP9": [84],
        }
        return prob.distribution_from_rows(rows, 0)

    def test_distribution_extracted(self):
        d = self._dist()
        assert d.mu == 80.0
        assert d.sigma == 3.0
        assert d.has_quantiles
        assert [t for _, t in d.quantiles] == [76.0, 78.0, 80.0, 82.0, 84.0]

    def test_cdf_is_monotone_decreasing_in_threshold(self):
        d = self._dist()
        vals = [prob.quantile_p_at_least(d, k) for k in range(70, 92)]
        assert all(v is not None for v in vals)
        for a, b in zip(vals, vals[1:]):
            assert b <= a + 1e-12

    def test_median_quantile_is_near_one_half(self):
        d = self._dist()
        p = prob.quantile_p_at_least(d, 80)  # P(T >= 79.5), median at 80
        assert 0.5 <= p <= 0.75

    def test_probabilities_stay_in_range_far_into_the_tails(self):
        d = self._dist()
        for k in (40, 60, 100, 130):
            p = prob.quantile_p_at_least(d, k)
            assert 0.0 <= p <= 1.0

    def test_tied_integer_quantiles_do_not_divide_by_zero(self):
        """At a 1 F spread the published integer quantiles are routinely equal,
        which is a vertical CDF segment."""
        rows = {
            "TXNMN": [70], "TXNSD": [1],
            "TXNP1": [69], "TXNP2": [69], "TXNP5": [70],
            "TXNP7": [71], "TXNP9": [71],
        }
        d = prob.distribution_from_rows(rows, 0)
        for k in range(65, 76):
            p = prob.quantile_p_at_least(d, k)
            assert p is not None and 0.0 <= p <= 1.0

    def test_missing_mean_returns_none(self):
        assert prob.distribution_from_rows({"TXNMN": [None]}, 0) is None
        assert prob.distribution_from_rows({}, 0) is None


class TestVerifyStrikeLogic:
    class _M:
        def __init__(self, ticker, stype, floor, cap, exp, result):
            self.ticker = ticker
            self.strike_type = stype
            self.floor_strike = floor
            self.cap_strike = cap
            self.expiration_value = exp
            self.result = result

        @property
        def outcome(self):
            return 1 if self.result == "yes" else 0

    def test_counts_unhandled_rather_than_dropping_silently(self):
        markets = [
            self._M("A", "greater", 85, None, 86.0, "yes"),
            self._M("B", "mystery", 85, None, 86.0, "no"),
        ]
        out = prob.verify_strike_logic(markets)
        assert out["seen"] == 2
        assert out["total"] == 1
        assert out["skipped"] == {"unhandled:mystery": 1}

    def test_detects_an_inverted_interpretation(self):
        markets = [self._M("A", "greater", 85, None, 84.0, "yes")]
        out = prob.verify_strike_logic(markets)
        assert out["mismatch"] == 1
        assert out["failures"]


class TestEventDayParsing:
    def test_parses_kalshi_event_ticker(self):
        import datetime as dt

        assert kh.parse_event_day("KXHIGHLAX-26AUG01") == dt.date(2026, 8, 1)
        assert kh.parse_event_day("KXHIGHTDAL-26MAY22") == dt.date(2026, 5, 22)

    def test_rejects_malformed(self):
        assert kh.parse_event_day("KXHIGHLAX") is None
        assert kh.parse_event_day("KXHIGHLAX-26XXX01") is None
        assert kh.parse_event_day("KXHIGHLAX-2026AUG01") is None


class TestPriceExtraction:
    def _candle(self, ts, bid, ask):
        return {
            "end_period_ts": ts,
            "yes_bid": {"close_dollars": f"{bid:.4f}"},
            "yes_ask": {"close_dollars": f"{ask:.4f}"},
        }

    def test_uses_last_bar_at_or_before_timestamp(self):
        candles = [
            self._candle(100, 0.20, 0.22),
            self._candle(200, 0.30, 0.32),
            self._candle(300, 0.40, 0.42),
        ]
        assert kh.yes_price_at(candles, 250, "mid") == pytest.approx(0.31)
        assert kh.yes_price_at(candles, 100, "mid") == pytest.approx(0.21)

    def test_returns_none_before_first_bar(self):
        assert kh.yes_price_at([self._candle(100, 0.2, 0.22)], 50) is None

    def test_bid_and_ask_sides(self):
        candles = [self._candle(100, 0.20, 0.24)]
        assert kh.yes_price_at(candles, 100, "bid") == pytest.approx(0.20)
        assert kh.yes_price_at(candles, 100, "ask") == pytest.approx(0.24)

    def test_rejects_a_zero_to_one_quote_as_no_market(self):
        """0.00/1.00 is the absence of a two-sided market. Admitting it as a
        mid of 0.50 would hand the model a free win against a baseline that
        never existed."""
        candles = [self._candle(100, 0.0, 1.0)]
        assert kh.yes_price_at(candles, 100, "mid") is None

    def test_rejects_a_crossed_or_zero_width_quote(self):
        assert kh.yes_price_at([self._candle(100, 0.5, 0.5)], 100) is None
        assert kh.yes_price_at([self._candle(100, 0.6, 0.4)], 100) is None
