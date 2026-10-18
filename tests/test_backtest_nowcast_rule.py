"""Pins the C10 decision rule frozen in DECISIONS.md Session 37."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.nowcast.rule import (  # noqa: E402
    Rung, RunningMax, ask_for, first_entry_h7, first_entry_h8, metar_temp_f,
    net_return, two_sided,
)


# --- strike conventions -----------------------------------------------------

def test_greater_means_floor_plus_one():
    r = Rung("greater", 85, None)          # "greater than 85" = 86 or above
    assert r.decided_side(85.9) is None
    assert r.decided_side(86.0) == "yes"


def test_less_reads_cap_strike_not_floor():
    r = Rung("less", None, 80)             # "less than 80" = 79 or below
    assert r.decided_side(79.9) is None
    assert r.decided_side(80.0) == "no"


def test_between_decided_no_only_once_above_the_bucket():
    r = Rung("between", 86, 87)            # 86-87 inclusive
    assert r.decided_side(87.9) is None    # still inside / could settle 87
    assert r.decided_side(88.0) == "no"


def test_running_max_never_decides_yes_on_between_or_less():
    # a rising max can only rule a bucket OUT, never in
    assert Rung("between", 86, 87).decided_side(86.5) is None
    assert Rung("less", None, 80).decided_side(60.0) is None


# --- METAR parsing ----------------------------------------------------------

def test_metar_t_group_unrounded():
    m = "KAUS 011753Z 25007KT 10SM FEW100 35/22 A2994 RMK AO2 SLP125 T03500217 10350"
    assert metar_temp_f(m) == pytest.approx(95.0)
    m = "KAUS 010053Z 18006KT 10SM 34/19 A2988 RMK AO2 T03440194"
    assert metar_temp_f(m) == pytest.approx(93.92)


def test_metar_without_t_group_uses_lower_bound():
    m = "KXYZ 011753Z 25007KT 10SM 30/22 A2994"
    assert metar_temp_f(m) == pytest.approx((30 - 0.5) * 9 / 5 + 32)


def test_metar_negative_t_group():
    assert metar_temp_f("KMSP 011753Z 00000KT M05/M10 RMK T10501100") == pytest.approx(23.0)


# --- observation timestamp cutoff ------------------------------------------

def test_observation_must_be_ten_minutes_old():
    day_start = 1_000_000
    end = day_start + 3600 * 15
    rm = RunningMax([(end - 540, 99.0), (end - 600, 90.0)], day_start)
    assert rm.as_of(end) == 90.0           # the :51 report is only 9 min old


def test_previous_climate_day_observations_excluded():
    day_start = 1_000_000
    rm = RunningMax([(day_start - 60, 99.0), (day_start + 3600, 70.0)], day_start)
    assert rm.as_of(day_start + 7200) == 70.0


# --- two-sided book and entry ----------------------------------------------

def test_two_sided_filter():
    assert two_sided(0.01, 0.02)
    assert not two_sided(0.0, 0.02)
    assert not two_sided(0.5, 1.0)
    assert not two_sided(0.5, 0.5)
    assert not two_sided(None, 0.5)


def test_no_ask_is_one_minus_yes_bid():
    assert ask_for("no", 0.03, 0.05) == pytest.approx(0.97)
    assert ask_for("yes", 0.03, 0.05) == 0.05


def test_first_entry_skips_one_sided_books_and_respects_close():
    day_start = 0
    rm = RunningMax([(3600, 90.0)], day_start)
    r = Rung("greater", 85, None)
    candles = [(7200, 0.0, 0.99), (10800, 0.97, 0.98), (14400, 0.98, 0.99)]
    e = first_entry_h7(r, candles, rm, close_ts=20000)
    assert (e.ts, e.side, e.price) == (10800, "yes", 0.98)
    assert first_entry_h7(r, candles, rm, close_ts=9000) is None


def test_h8_needs_evening_and_margin():
    day_start = 0
    rm = RunningMax([(3600, 80.0)], day_start)
    r = Rung("greater", 82, None)          # YES needs 83+; 83 >= 80 + 3
    candles = [(18 * 3600, 0.02, 0.05), (19 * 3600, 0.02, 0.05)]
    e = first_entry_h8(r, candles, rm, close_ts=24 * 3600, day_start=day_start)
    assert (e.ts, e.side, e.price) == (19 * 3600, "no", pytest.approx(0.98))
    assert first_entry_h8(Rung("greater", 81, None), candles, rm, 24 * 3600, 0) is None


def test_net_return_includes_fee():
    p = 0.99
    assert net_return(p, True) == pytest.approx((1 - p - 0.07 * p * (1 - p)) / (p + 0.07 * p * (1 - p)))
    assert net_return(p, False) == pytest.approx(-1.0)
