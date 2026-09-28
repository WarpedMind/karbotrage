"""
tests/test_backtest_tape_rules.py

Pins the entry rules behind Session 36's headline result (DECISIONS.md): H5 buys
the side whose ASK is in [0.70, 0.85) at the first two-sided hourly candle before
the cutoff; H6 adds a 3c spread cap. These rules are what turned a +6.8% trade-tape
"edge" into a -15% loss, so a silent change to them would change the conclusion.

Offline research code (backtest/tape) -- never imported by the live path
(tests/test_canary_isolation.py guards that).
"""

import os
import sys
from pathlib import Path

import pytest

TAPE = Path(__file__).parent.parent / "backtest" / "tape"
if str(TAPE) not in sys.path:
    sys.path.insert(0, str(TAPE))

import h5  # noqa: E402
_H5_ENTRIES = h5.entries
import h6  # noqa: E402  (rebinds h5.entries to the H6 rule on import)
h5.entries = _H5_ENTRIES


def _mkt(result, candles, cutoff=10_000):
    return {"m": {"result": result}, "cutoff": cutoff, "candles": candles}


def _fee(p):
    return 0.07 * p * (1 - p)


def test_h5_buys_yes_when_yes_ask_in_band_and_scores_net_of_fee():
    d = _mkt("yes", [[100, 0.74, 0.76]])
    (ret, ts, p), = _H5_ENTRIES(d)
    assert p == pytest.approx(0.76)
    assert ret == pytest.approx((1 - 0.76 - _fee(0.76)) / (0.76 + _fee(0.76)))


def test_h5_buys_no_at_one_minus_yes_bid():
    # yes bid 0.22 => NO ask 0.78, in band; YES ask 0.25 is not
    d = _mkt("yes", [[100, 0.22, 0.25]])
    (ret, ts, p), = _H5_ENTRIES(d)
    assert p == pytest.approx(0.78)
    assert ret == pytest.approx((0 - 0.78 - _fee(0.78)) / (0.78 + _fee(0.78)))


def test_h5_takes_only_the_first_qualifying_candle_before_cutoff():
    d = _mkt("no", [[300, 0.70, 0.72], [100, 0.50, 0.52], [200, 0.78, 0.80], [20_000, 0.80, 0.81]])
    out = _H5_ENTRIES(d)
    assert len(out) == 1 and out[0][1] == 200


def test_h5_ignores_one_sided_books_and_candles_after_cutoff():
    d = _mkt("yes", [[100, 0.0, 0.80], [200, 0.79, 1.0], [20_000, 0.78, 0.80]])
    assert _H5_ENTRIES(d) == []


def test_h5_skips_hour_when_both_sides_qualify():
    # yes ask 0.75 and NO ask 1-0.20 = 0.80: a 55c-wide book, ambiguous -> skip
    d = _mkt("yes", [[100, 0.20, 0.75]])
    assert _H5_ENTRIES(d) == []


def test_h6_rejects_wide_books_that_h5_accepts():
    wide = _mkt("yes", [[100, 0.60, 0.76]])      # 16c spread
    assert len(_H5_ENTRIES(wide)) == 1
    assert h6.entries(wide) == []
    tight = _mkt("yes", [[100, 0.73, 0.76]])     # 3c spread, allowed
    assert len(h6.entries(tight)) == 1


def test_importing_the_rules_does_not_change_working_directory():
    before = os.getcwd()
    import importlib
    importlib.reload(h5)
    h5.entries = _H5_ENTRIES
    assert os.getcwd() == before
