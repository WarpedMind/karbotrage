"""Rule tests for H11 (Session 39): Kalshi <-> Polymarket US weather crossings. Offline only.

Pins the pre-registered arithmetic (DECISIONS.md Session 38) and the choices declared in
backtest/reports/session39_declared_before_results.txt before any result was computed.
"""

import pytest

from backtest.venues import h11_rule as R
from backtest.venues.weather_cross_snapshot import INF


def test_degree_sign_titles_parse_like_plain_titles():
    assert R.pm_title_interval("56° to 57°") == R.pm_title_interval("56 to 57") == (56, 57)
    assert R.pm_title_interval("55° or below") == (-INF, 55)
    assert R.pm_title_interval("64° or above") == (64, INF)


def test_pm_quote_reads_long_as_ask_and_short_as_one_minus_bid():
    assert R.pm_quote([0, 0.15, 0.88]) == pytest.approx((0.12, 0.15))


def test_pm_quote_drops_the_one_cent_placeholder_bid_and_missing_ask():
    assert R.pm_quote([0, 0.13, 0.99]) is None   # bid 0.01 = placeholder
    assert R.pm_quote([0, 0.99, 0.50]) is None   # ask 0.99 = no ask


def test_pm_quote_strict_drops_the_half_half_listing_seed():
    seed = [0, 0.5, 0.5]                          # bid = ask = 0.50
    assert R.pm_quote(seed, strict=True) is None
    assert R.pm_quote(seed, strict=False) == pytest.approx((0.5, 0.5))


def test_basket_costs_match_the_pre_registered_formula():
    a, b = R.basket_costs(kb=0.58, ka=0.59, pb=0.58, pa=0.59)
    assert a == pytest.approx(0.59 + 0.42 + 0.07 * 0.59 * 0.41 + 0.0695 * 0.42 * 0.58)
    assert b == pytest.approx(0.59 + 0.42 + 0.0695 * 0.59 * 0.41 + 0.07 * 0.42 * 0.58)
    assert not R.crosses(a) and not R.crosses(b)


def test_a_real_crossing_is_detected():
    # Kalshi ask 0.40, PM bid 0.50: gross 0.90, fees ~3.5c -> net < $1
    a, _ = R.basket_costs(kb=0.39, ka=0.40, pb=0.50, pa=0.52)
    assert R.crosses(a)
    assert R.locked_profit(a) == pytest.approx((1 - a) * 10)


def test_break_even_is_not_a_crossing():
    assert not R.crosses(1.0)
    assert not R.crosses(1.0 - 1e-12)


def test_kalshi_fee_ceil_rounds_up_per_order():
    # 10 lots at 0.50: 0.07*10*0.25 = 0.175 -> $0.18 -> 0.018 per contract
    assert R.kalshi_fee_ceil(0.50) == pytest.approx(0.018)


def test_pair_point_takes_latest_point_within_60s_before_candle_end():
    ts = [100, 3540, 3590, 3610]
    pts = [[t, 0.2, 0.8] for t in ts]
    assert R.pair_point(ts, pts, 3600)[0] == 3590
    assert R.pair_point(ts, pts, 3700) is None    # 3610 is 90s before
    assert R.pair_point(ts, pts, 50) is None


def test_episodes_need_consecutive_observed_crossing_hours():
    h = [(0, 0.98), (3600, 0.97), (7200, None), (10800, 0.99), (14400, 1.01), (18000, 0.95), (25200, 0.96)]
    eps = R.episodes(h)
    assert [(e["start"], e["hours"]) for e in eps] == [(0, 2), (10800, 1), (18000, 1), (25200, 1)]
    assert eps[0]["first"] == 0.98 and eps[0]["best"] == 0.97


def test_partition_check():
    assert R.is_partition([(-INF, 59), (60, 61), (62, 63), (64, INF)])
    assert not R.is_partition([(-INF, 59), (62, 63), (64, INF)])
    assert not R.is_partition([(60, 61), (62, INF)])


def test_bootstrap_all_zero_days_cannot_pass():
    b = R.bootstrap_mean([0.0] * 50, n=200)
    assert b["mean"] == 0 and b["p_le_0"] == 1.0
