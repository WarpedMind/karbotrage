"""C9 rule, frozen in DECISIONS.md Session 37 (H9) before any C9 data was fetched.

Pure functions only. A spread ladder ("A wins by over k") gives points on the
survival function of A's margin; a probit-linear fit through them is a normal
margin distribution, read off at x = 0.5 (soccer, whole-goal margins with a
draw) or x = 0 (no draws), and compared with the game-winner market's ask.
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Iterable, List, Optional, Sequence, Tuple

_N = NormalDist()
MARGIN = 0.03        # required edge over the ask, after the taker fee
MID_LO, MID_HI = 0.02, 0.98
MIN_POINTS = 3


def two_sided(bid, ask) -> bool:
    return bid is not None and ask is not None and 0 < bid < ask < 1


def taker_fee(p: float) -> float:
    return 0.07 * p * (1.0 - p)


def ladder_points(rungs: Iterable[Tuple[str, float, Optional[float], Optional[float]]],
                  team_a: str) -> List[Tuple[float, float]]:
    """``rungs``: (team, k, yes_bid, yes_ask) for "team wins by over k".

    A's own rungs give (k, mid); the opponent's give (-k, 1 - mid).
    """
    pts = []
    for team, k, bid, ask in rungs:
        if not two_sided(bid, ask):
            continue
        mid = (bid + ask) / 2.0
        if not (MID_LO <= mid <= MID_HI):
            continue
        pts.append((k, mid) if team == team_a else (-k, 1.0 - mid))
    return pts


def fit(points: Sequence[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    """OLS of probit(P) on x. Returns (a, b) with b < 0, else None."""
    if len(points) < MIN_POINTS:
        return None
    xs = [x for x, _ in points]
    ys = [_N.inv_cdf(p) for _, p in points]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    if b >= 0:
        return None
    return my - b * mx, b


def implied_win(ab: Tuple[float, float], draws: bool) -> Tuple[float, float]:
    """(P(A wins), P(B wins)). With draws, a win is margin >= 1 (x = 0.5)."""
    a, b = ab
    if draws:
        return _N.cdf(a + 0.5 * b), 1.0 - _N.cdf(a - 0.5 * b)
    pa = _N.cdf(a)
    return pa, 1.0 - pa


def qualifies(ask: float, implied: float) -> float:
    """Gap (> 0 means qualifies) = implied − ask − fee − margin."""
    return implied - ask - taker_fee(ask) - MARGIN


def net_return(p: float, won: bool) -> float:
    f = taker_fee(p)
    return ((1.0 if won else 0.0) - p - f) / (p + f)
