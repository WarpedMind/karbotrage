"""Pins the C9 (H9) ladder-fit rule frozen in DECISIONS.md Session 37."""

import sys
from pathlib import Path
from statistics import NormalDist

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.consistency.rule import (  # noqa: E402
    fit, implied_win, ladder_points, qualifies, taker_fee,
)

N = NormalDist()


def _ladder(mu, sd, ks=(1.5, 2.5, 3.5)):
    """Exact two-sided quotes from a normal margin, 1c wide."""
    rungs = []
    for k in ks:
        pa = 1 - N.cdf((k - mu) / sd)        # P(A margin > k)
        pb = N.cdf((-k - mu) / sd)           # P(B margin > k) = P(A margin < -k)
        rungs.append(("A", k, pa - 0.005, pa + 0.005))
        rungs.append(("B", k, pb - 0.005, pb + 0.005))
    return rungs


def test_opponent_rungs_mirror_to_negative_x():
    pts = ladder_points([("B", 2.5, 0.10, 0.12)], "A")
    assert pts == [(-2.5, pytest.approx(0.89))]


def test_one_sided_and_extreme_rungs_dropped():
    assert ladder_points([("A", 1.5, 0.0, 0.05)], "A") == []
    assert ladder_points([("A", 9.5, 0.001, 0.01)], "A") == []      # mid < 0.02


def test_fit_recovers_normal_and_reads_off_win_probability():
    mu, sd = 0.6, 1.7
    ab = fit(ladder_points(_ladder(mu, sd), "A"))
    pa_nodraw, _ = implied_win(ab, draws=False)
    pa_draw, pb_draw = implied_win(ab, draws=True)
    assert pa_nodraw == pytest.approx(1 - N.cdf((0 - mu) / sd), abs=0.01)
    assert pa_draw == pytest.approx(1 - N.cdf((0.5 - mu) / sd), abs=0.01)   # margin >= 1
    assert pb_draw == pytest.approx(N.cdf((-0.5 - mu) / sd), abs=0.01)      # margin <= -1
    assert pa_draw + pb_draw < 1                                            # draw mass left over


def test_fit_needs_three_points_and_negative_slope():
    assert fit([(1.5, 0.3), (2.5, 0.2)]) is None
    assert fit([(1.5, 0.2), (2.5, 0.3), (3.5, 0.4)]) is None


def test_threshold_includes_fee_and_three_cents():
    ask = 0.40
    assert qualifies(ask, ask + taker_fee(ask) + 0.03 + 0.001) > 0
    assert qualifies(ask, ask + taker_fee(ask) + 0.03 - 0.001) < 0
