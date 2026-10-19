"""H11 rule primitives (Session 39): pure functions, pinned by tests/test_backtest_h11_rule.py.

Frozen in DECISIONS.md Session 38; implementation choices in
backtest/reports/session39_declared_before_results.txt. Offline only, never
imported by the live path.

    basket A = Kalshi YES ask + (1 - PM YES bid)    pays exactly $1
    basket B = PM YES ask   + (1 - Kalshi YES bid)  pays exactly $1
    net      = gross + 0.07*p*(1-p) per Kalshi leg + 0.0695*p*(1-p) per PM leg
    crossing = net < $1.000
"""

from __future__ import annotations

import bisect
import math
import random

from backtest.venues.weather_cross_snapshot import INF, pm_interval

KALSHI_THETA = 0.07
PM_THETA = 0.0695
CONTRACTS = 10  # assumed size per episode; neither source records depth
PAIR_WINDOW = 60  # seconds: PM point must be in [candle_end - 60, candle_end]
EPS = 1e-9  # float dust guard, as in canary.economics


def pm_title_interval(title: str) -> tuple[int, int] | None:
    """Parse a Polymarket US rung title, tolerating the degree-sign variant ("56° to 57°")."""
    return pm_interval((title or "").replace("°", ""))


def fee(theta: float, p: float) -> float:
    return theta * p * (1 - p)


def kalshi_fee_ceil(p: float, n: int = CONTRACTS) -> float:
    """Sensitivity only: Kalshi's per-order round-up to the cent, per contract, on an n-lot."""
    return math.ceil(KALSHI_THETA * n * p * (1 - p) * 100 - EPS) / 100 / n


def kalshi_two_sided(bid, ask) -> bool:
    return bid is not None and ask is not None and 0 < bid < ask < 1


def pm_quote(point, strict: bool = True):
    """(bid, ask) from a price-history point [ts, longPrice, shortPrice], or None if unusable.

    longPrice is the best YES ask; shortPrice is 1 - best YES bid. A 1c bid (shortPrice 0.99) is the
    placeholder for a missing bid and an ask of 0.99+ is no ask (pre-registered). ``strict`` also drops
    locked or crossed points (bid >= ask), e.g. the 0.5/0.5 listing seed (declared deviation 1).
    """
    if point is None or point[1] is None or point[2] is None:
        return None
    # round: 1 - 0.99 is 0.01000000000000001 in floating point, which would let the placeholder through
    ask, bid = float(point[1]), round(1 - float(point[2]), 6)
    if not (ask < 0.99 and bid > 0.01):
        return None
    if strict and not bid < ask - EPS:
        return None
    return bid, ask


def basket_costs(kb: float, ka: float, pb: float, pa: float, kalshi_fee=None):
    """Net cost of baskets A and B for $1 of guaranteed payout."""
    kf = kalshi_fee or (lambda p: fee(KALSHI_THETA, p))
    a = ka + (1 - pb) + kf(ka) + fee(PM_THETA, 1 - pb)
    b = pa + (1 - kb) + fee(PM_THETA, pa) + kf(1 - kb)
    return a, b


def crosses(net: float) -> bool:
    return net < 1 - EPS


def pair_point(ts_list: list[int], points: list, t: int):
    """Latest PM point with t - 60 <= ts <= t, else None."""
    i = bisect.bisect_right(ts_list, t) - 1
    if i >= 0 and ts_list[i] >= t - PAIR_WINDOW:
        return points[i]
    return None


def episodes(hours: list[tuple[int, float | None]]) -> list[dict]:
    """Group consecutive crossing hours. ``hours`` = sorted (candle_end, net cost or None if unobservable).

    An hour ends an episode unless it is observed AND crosses AND is exactly 3600 s after the previous
    crossing hour. Profit uses the first hour's cost (declared choice 4); ``best`` is an upper bound.
    """
    out, cur, prev = [], None, None
    for t, net in hours:
        if net is not None and crosses(net):
            if cur is not None and prev is not None and t - prev == 3600:
                cur["hours"] += 1
                cur["best"] = min(cur["best"], net)
            else:
                cur = {"start": t, "first": net, "best": net, "hours": 1}
                out.append(cur)
            prev = t
        else:
            cur, prev = None, None
    return out


def locked_profit(net: float, n: int = CONTRACTS) -> float:
    return (1 - net) * n


def is_partition(intervals: list[tuple[int, int]]) -> bool:
    """True if the integer intervals are disjoint and cover (-INF, INF) with no gap."""
    iv = sorted(intervals)
    if not iv or iv[0][0] != -INF or iv[-1][1] != INF:
        return False
    return all(iv[i + 1][0] == iv[i][1] + 1 for i in range(len(iv) - 1))


def bootstrap_mean(values: list[float], n: int = 10000, seed: int = 39) -> dict:
    """Resample whole units (dates or weeks); one-sided P(mean <= 0)."""
    k = len(values)
    if k == 0:
        return {"n": 0, "mean": None, "lo": None, "hi": None, "p_le_0": None}
    rng = random.Random(seed)
    means = sorted(sum(values[rng.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return {"n": k, "mean": sum(values) / k, "lo": means[int(0.025 * n)], "hi": means[int(0.975 * n) - 1],
            "p_le_0": sum(m <= 0 for m in means) / n}
