"""Interval-parsing tests for the Session 38 venue-screen probes (offline only).

The Polymarket US weather slug is off by one degree if read literally:
`gte60lt61f` is the bucket titled "60 to 61", inclusive of 61. These tests pin
the title-based parser and its agreement with Kalshi's strike conventions.
"""

from backtest.venues.weather_cross_snapshot import INF, kalshi_interval, pm_interval


def test_pm_title_bucket_is_inclusive_both_ends():
    assert pm_interval("60 to 61") == (60, 61)


def test_pm_title_tails():
    assert pm_interval("59 or below") == (-INF, 59)
    assert pm_interval("68 or above") == (68, INF)


def test_pm_unparseable_title_is_none():
    assert pm_interval("New York") is None
    assert pm_interval("") is None


def test_kalshi_conventions_match_session_31():
    # greater f -> high >= f+1 ; less c -> high <= c-1 ; between f..c inclusive
    assert kalshi_interval({"strike_type": "greater", "floor_strike": 67}) == (68, INF)
    assert kalshi_interval({"strike_type": "less", "cap_strike": 60}) == (-INF, 59)
    assert kalshi_interval({"strike_type": "between", "floor_strike": 60, "cap_strike": 61}) == (60, 61)


def test_same_ladder_rung_matches_across_venues():
    k = kalshi_interval({"strike_type": "between", "floor_strike": 62, "cap_strike": 63})
    assert k == pm_interval("62 to 63")
