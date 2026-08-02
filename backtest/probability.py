"""Turning an NBM temperature forecast into a Kalshi market probability.

This is the modelling step DECISIONS.md flagged as "easy to underestimate", and
it has three separate places to get a half-degree wrong. All three are handled
explicitly here rather than being left to a library:

1. **The observation is an integer.** The NWS Climatological Report publishes a
   whole-degree daily high, and Kalshi settles on that number -- confirmed by
   ``expiration_value`` being ``"80.00"`` for KXHIGHLAX-26AUG01 and by IEM's
   parse of the same CLI product also reading 80. So the underlying continuous
   temperature ``T`` maps to the settled value ``H = round(T)``.

2. **"greater than 85" means "86 or above".** Kalshi's own ``yes_sub_title`` on
   ``KXHIGHLAX-26AUG01-T85`` reads ``86 or above`` -- so the YES condition is
   ``H >= 86``, i.e. ``T >= 85.5``, not ``T > 85``. Skipping this continuity
   correction shifts every probability by half a standard deviation on these
   markets, because the forecast spread is small (TXNSD is typically 1-3 F).
   Half a sigma is not a rounding detail; it is the entire claimed edge.

3. **Not every series is "greater".** Four of eighteen daily-high series phrase
   their threshold markets as "less than". ``market_probability`` dispatches on
   ``strike_type`` and never assumes a direction.

``verify_strike_logic`` exists to prove points 2 and 3 rather than assert them:
it replays the interpretation against every settled market's real ``result``
using the real ``expiration_value``. If the interpretation is wrong anywhere,
that check fails loudly instead of quietly producing a well-calibrated-looking
model of the wrong event.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

#: NBP bulletin rows carrying the max/min temperature distribution, mapped to
#: the cumulative probability each percentile row represents.
QUANTILE_ROWS: Sequence[Tuple[str, float]] = (
    ("TXNP1", 0.10),
    ("TXNP2", 0.25),
    ("TXNP5", 0.50),
    ("TXNP7", 0.75),
    ("TXNP9", 0.90),
)

MEAN_ROW = "TXNMN"
SD_ROW = "TXNSD"

#: Floor on the forecast standard deviation, in degrees F.
#:
#: NOT a tuning knob and NOT fitted -- it exists because the text bulletins
#: publish TXNSD as an **integer**, so a genuine spread of 0.4 F and one of 1.4 F
#: are both published as "1", and a spread of 0.4 F would be published as "0",
#: implying a point mass. A zero sigma makes every probability exactly 0 or 1
#: and would make the Brier score a lottery on rounding. 0.5 F is half the
#: quantisation step, i.e. the smallest spread the integer encoding can even
#: represent as distinct from zero.
SD_FLOOR = 0.5


def norm_cdf(z: float) -> float:
    """Standard normal CDF. ``math.erf`` is exact enough and avoids pulling
    scipy into a trading bot's dependency set for one function."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _p_at_least(threshold_int: float, mu: float, sigma: float) -> float:
    """P(round(T) >= threshold_int) = P(T >= threshold_int - 0.5)."""
    sigma = max(sigma, SD_FLOOR)
    return 1.0 - norm_cdf((threshold_int - 0.5 - mu) / sigma)


def gaussian_probability(
    strike_type: str,
    floor_strike: Optional[float],
    cap_strike: Optional[float],
    mu: float,
    sigma: float,
) -> Optional[float]:
    """P(YES) under ``T ~ Normal(mu, sigma)`` with integer-rounding correction."""
    sigma = max(sigma, SD_FLOOR)
    if strike_type == "greater":
        if floor_strike is None:
            return None
        return _p_at_least(floor_strike + 1, mu, sigma)
    if strike_type == "less":
        # NOTE: "less" markets carry the threshold in cap_strike and leave
        # floor_strike null -- the opposite of "greater". Reading floor_strike
        # here returns None and silently drops the entire low tail of every
        # ladder (1,255 of 7,560 markets in the 18-series sample), which biases
        # the study toward the strikes the market prices most confidently.
        if cap_strike is None:
            return None
        # YES iff H <= cap_strike - 1, i.e. NOT (H >= cap_strike)
        return 1.0 - _p_at_least(cap_strike, mu, sigma)
    if strike_type == "between":
        if floor_strike is None or cap_strike is None:
            return None
        return _p_at_least(floor_strike, mu, sigma) - _p_at_least(
            cap_strike + 1, mu, sigma
        )
    return None


def settles_yes(
    strike_type: str,
    floor_strike: Optional[float],
    cap_strike: Optional[float],
    observed_high: float,
) -> Optional[bool]:
    """The settlement rule, written out so it can be tested against reality."""
    h = round(observed_high)
    if strike_type == "greater":
        if floor_strike is None:
            return None
        return h >= floor_strike + 1
    if strike_type == "less":
        if cap_strike is None:
            return None
        return h <= cap_strike - 1
    if strike_type == "between":
        if floor_strike is None or cap_strike is None:
            return None
        return floor_strike <= h <= cap_strike
    return None


def verify_strike_logic(markets: Iterable) -> dict:
    """Replay ``settles_yes`` against every market's real ``result``.

    Returns a summary rather than raising, so the caller can print the failures.
    A mismatch rate above zero means the strike interpretation is wrong and
    every downstream number is meaningless -- treat it as a hard stop.
    """
    total = 0
    matched = 0
    seen = 0
    skipped: dict = {}
    failures: List[Tuple[str, str, float, str]] = []
    by_type: dict = {}
    for m in markets:
        seen += 1
        if m.expiration_value is None:
            skipped["no_expiration_value"] = skipped.get("no_expiration_value", 0) + 1
            continue
        predicted = settles_yes(
            m.strike_type, m.floor_strike, m.cap_strike, m.expiration_value
        )
        if predicted is None:
            # Counted, never silent. A skip that nobody counts is how the
            # "less"-uses-cap_strike bug removed a fifth of the sample while
            # every printed number still looked clean.
            key = f"unhandled:{m.strike_type}"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        total += 1
        stat = by_type.setdefault(m.strike_type, [0, 0])
        stat[0] += 1
        if predicted == bool(m.outcome):
            matched += 1
            stat[1] += 1
        elif len(failures) < 25:
            failures.append(
                (m.ticker, m.strike_type, m.expiration_value, m.result)
            )
    return {
        "seen": seen,
        "total": total,
        "matched": matched,
        "mismatch": total - matched,
        "skipped": skipped,
        "by_type": by_type,
        "failures": failures,
    }


@dataclass
class Distribution:
    """A station-day forecast distribution extracted from one NBP block."""

    mu: float
    sigma: float
    quantiles: List[Tuple[float, float]]  # (cumulative prob, temperature)

    @property
    def has_quantiles(self) -> bool:
        return len(self.quantiles) >= 2


def distribution_from_rows(
    rows: dict, index: int, *, sigma_scale: float = 1.0, bias: float = 0.0
) -> Optional[Distribution]:
    """Pull mean/sd/quantiles for one valid-time column out of a parsed block."""
    mean_row = rows.get(MEAN_ROW)
    sd_row = rows.get(SD_ROW)
    if not mean_row or index >= len(mean_row) or mean_row[index] is None:
        return None
    mu = float(mean_row[index]) + bias
    sigma = SD_FLOOR
    if sd_row and index < len(sd_row) and sd_row[index] is not None:
        sigma = max(float(sd_row[index]), SD_FLOOR)
    sigma *= sigma_scale

    quantiles: List[Tuple[float, float]] = []
    for label, p in QUANTILE_ROWS:
        row = rows.get(label)
        if row and index < len(row) and row[index] is not None:
            quantiles.append((p, float(row[index]) + bias))
    quantiles.sort()
    return Distribution(mu=mu, sigma=max(sigma, SD_FLOOR), quantiles=quantiles)


def quantile_p_at_least(dist: Distribution, threshold_int: float) -> Optional[float]:
    """P(round(T) >= threshold_int) from the published quantiles.

    Interpolates the CDF linearly between published quantile points and falls
    back to Gaussian tails outside [P10, P90], anchored so the tail is
    continuous with the interpolated body.

    The published quantiles are **integers**, so ties are common (P10 and P25 of
    69 and 69 is normal when the spread is 1 F). A tie is a vertical CDF
    segment; the code steps past ties rather than dividing by a zero width.
    """
    if not dist.has_quantiles:
        return None
    x = threshold_int - 0.5
    qs = dist.quantiles
    lo_p, lo_t = qs[0]
    hi_p, hi_t = qs[-1]

    if x <= lo_t:
        # Lower tail: Gaussian shape, rescaled so it meets F(lo_t) == lo_p exactly.
        return 1.0 - _tail_low(x, lo_t, lo_p, dist.sigma)
    if x >= hi_t:
        return _tail_high(x, hi_t, hi_p, dist.sigma)

    for i in range(len(qs) - 1):
        p0, t0 = qs[i]
        p1, t1 = qs[i + 1]
        if t0 <= x <= t1:
            if t1 == t0:
                return 1.0 - p1
            frac = (x - t0) / (t1 - t0)
            cdf = p0 + frac * (p1 - p0)
            return 1.0 - cdf
    return None


def _tail_low(x: float, lo_t: float, lo_p: float, sigma: float) -> float:
    """CDF below the lowest published quantile, scaled to meet it exactly."""
    sigma = max(sigma, SD_FLOOR)
    ref = norm_cdf(0.0)
    val = norm_cdf((x - lo_t) / sigma)
    return max(min(lo_p * (val / ref), lo_p), 0.0)


def _tail_high(x: float, hi_t: float, hi_p: float, sigma: float) -> float:
    """P(T >= x) above the highest published quantile, meeting it exactly."""
    sigma = max(sigma, SD_FLOOR)
    ref = 1.0 - norm_cdf(0.0)
    val = 1.0 - norm_cdf((x - hi_t) / sigma)
    tail = (1.0 - hi_p) * (val / ref)
    return max(min(tail, 1.0 - hi_p), 0.0)


def quantile_probability(
    strike_type: str,
    floor_strike: Optional[float],
    cap_strike: Optional[float],
    dist: Distribution,
) -> Optional[float]:
    """P(YES) from the published quantile suite."""
    if not dist.has_quantiles:
        return None
    if strike_type == "greater":
        if floor_strike is None:
            return None
        return quantile_p_at_least(dist, floor_strike + 1)
    if strike_type == "less":
        if cap_strike is None:
            return None
        p = quantile_p_at_least(dist, cap_strike)
        return None if p is None else 1.0 - p
    if strike_type == "between":
        if floor_strike is None or cap_strike is None:
            return None
        a = quantile_p_at_least(dist, floor_strike)
        b = quantile_p_at_least(dist, cap_strike + 1)
        if a is None or b is None:
            return None
        return a - b
    return None


def clamp(p: float, eps: float = 1e-6) -> float:
    return min(max(p, eps), 1.0 - eps)
