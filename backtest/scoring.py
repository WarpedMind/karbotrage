"""Scoring: Brier score, skill against the market, reliability, and a block
bootstrap that respects how little independent information this sample holds.

THE SAMPLE-SIZE TRAP, stated up front because it is the easiest way to fool
yourself here. A daily-high city-day is a ladder of six markets forming an
exhaustive partition -- exactly one resolves YES. Those six outcomes are one
observation of one temperature, not six independent draws. Worse, all ~18
cities on the same calendar day share the same synoptic weather pattern, so
even city-days are correlated across cities within a date.

So the honest unit of independent information is the **date**, and every
confidence statement in the report comes from a bootstrap that resamples whole
dates. Naively treating ~7,500 markets as n=7,500 would shrink the error bars
by roughly a factor of nine and turn noise into a publishable edge. That is the
statistical form of the same mistake as S1's: a number that looks strong
because the thing generating it was counted wrong.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple


@dataclass
class Observation:
    """One scored market at one evaluation time."""

    key: str  # market ticker
    block: str  # bootstrap block -- the local date
    series: str
    outcome: int  # 0/1
    model_p: float
    market_p: float  # mid, the calibration baseline
    market_ask: Optional[float] = None  # executable YES price
    market_bid: Optional[float] = None
    lead_hours: Optional[float] = None
    extra: dict = field(default_factory=dict)


def brier(pairs: Iterable[Tuple[float, int]]) -> Optional[float]:
    total = 0.0
    n = 0
    for p, y in pairs:
        total += (p - y) ** 2
        n += 1
    return total / n if n else None


def log_loss(pairs: Iterable[Tuple[float, int]], eps: float = 1e-6) -> Optional[float]:
    total = 0.0
    n = 0
    for p, y in pairs:
        q = min(max(p, eps), 1.0 - eps)
        total += -(y * math.log(q) + (1 - y) * math.log(1.0 - q))
        n += 1
    return total / n if n else None


def skill_score(model: float, reference: float) -> Optional[float]:
    """Brier skill score: >0 means the model beats the reference."""
    if reference is None or model is None or reference <= 0:
        return None
    return 1.0 - model / reference


def reliability_table(
    pairs: Sequence[Tuple[float, int]], bins: int = 10
) -> List[dict]:
    """Reliability (calibration) curve as a table.

    Deliberately a table rather than a plot: it keeps this package free of
    matplotlib, and the numbers are what a reader actually needs to judge
    whether the extremes are overconfident.
    """
    buckets: List[List[Tuple[float, int]]] = [[] for _ in range(bins)]
    for p, y in pairs:
        idx = min(int(p * bins), bins - 1)
        buckets[idx].append((p, y))
    out = []
    for i, bucket in enumerate(buckets):
        lo, hi = i / bins, (i + 1) / bins
        if not bucket:
            out.append(
                {"lo": lo, "hi": hi, "n": 0, "mean_p": None, "freq": None, "gap": None}
            )
            continue
        mean_p = sum(p for p, _ in bucket) / len(bucket)
        freq = sum(y for _, y in bucket) / len(bucket)
        out.append(
            {
                "lo": lo,
                "hi": hi,
                "n": len(bucket),
                "mean_p": mean_p,
                "freq": freq,
                "gap": mean_p - freq,
            }
        )
    return out


def block_bootstrap_delta(
    observations: Sequence[Observation],
    stat: Callable[[Sequence[Observation]], Optional[float]],
    *,
    n_resamples: int = 2000,
    seed: int = 20260802,
) -> dict:
    """Bootstrap ``stat`` by resampling whole blocks (dates) with replacement.

    Returns the point estimate, a percentile CI, and the share of resamples on
    the wrong side of zero -- which is the number that decides gate G2, not the
    point estimate.
    """
    by_block: Dict[str, List[Observation]] = {}
    for obs in observations:
        by_block.setdefault(obs.block, []).append(obs)
    blocks = list(by_block.values())
    if len(blocks) < 3:
        return {"point": stat(observations), "n_blocks": len(blocks), "ci": None}

    rng = random.Random(seed)
    point = stat(observations)
    draws: List[float] = []
    for _ in range(n_resamples):
        sample: List[Observation] = []
        for _ in range(len(blocks)):
            sample.extend(blocks[rng.randrange(len(blocks))])
        value = stat(sample)
        if value is not None:
            draws.append(value)
    draws.sort()
    if not draws:
        return {"point": point, "n_blocks": len(blocks), "ci": None}

    def pct(q: float) -> float:
        idx = min(max(int(q * (len(draws) - 1)), 0), len(draws) - 1)
        return draws[idx]

    return {
        "point": point,
        "n_blocks": len(blocks),
        "n_obs": len(observations),
        "ci": (pct(0.025), pct(0.975)),
        "p_le_zero": sum(1 for d in draws if d <= 0) / len(draws),
        "p_ge_zero": sum(1 for d in draws if d >= 0) / len(draws),
    }


def brier_delta(observations: Sequence[Observation]) -> Optional[float]:
    """Market Brier minus model Brier. Positive means the model is better."""
    m = brier((o.model_p, o.outcome) for o in observations)
    k = brier((o.market_p, o.outcome) for o in observations)
    if m is None or k is None:
        return None
    return k - m


def summarise(observations: Sequence[Observation]) -> dict:
    model = brier((o.model_p, o.outcome) for o in observations)
    market = brier((o.market_p, o.outcome) for o in observations)
    base_rate = (
        sum(o.outcome for o in observations) / len(observations) if observations else None
    )
    climo = (
        brier((base_rate, o.outcome) for o in observations)
        if base_rate is not None
        else None
    )
    return {
        "n": len(observations),
        "n_blocks": len({o.block for o in observations}),
        "base_rate": base_rate,
        "brier_model": model,
        "brier_market": market,
        "brier_climatology": climo,
        "skill_vs_market": skill_score(model, market),
        "skill_vs_climatology": skill_score(model, climo),
        "logloss_model": log_loss((o.model_p, o.outcome) for o in observations),
        "logloss_market": log_loss((o.market_p, o.outcome) for o in observations),
    }
