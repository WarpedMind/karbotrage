"""Kalshi strike conventions as intervals, and the logical relations between them.

S5b's whole claim is that one market's YES implies another's. That claim comes
from arithmetic on strikes, so the arithmetic has to be exactly right, and the
conventions are not uniform. Surveyed across 12,000 live open markets on
2026-08-02:

    strike_type         n      floor    cap     meaning
    greater            6438    yes      no      value >  floor
    structured         3282    1724/1558 no     HETEROGENEOUS -- see below
    between            1408    yes      yes     floor <= value <= cap
    custom              569    no       no      categorical, no numeric strike
    less                105    no       YES     value <  cap
    (none)              100    no       no      no strike
    greater_or_equal     98    yes      no      value >= floor

Two traps are live in that table:

1. **``less`` carries its threshold in ``cap_strike`` and leaves ``floor_strike``
   null -- the opposite convention from ``greater``.** Session 31 shipped a bug
   here: reading ``floor_strike`` returned ``None``, ``None`` was skipped
   silently, and that removed the entire low tail of every ladder -- a fifth of
   the sample, systematically the same region of every distribution -- while the
   validation still printed a perfect 100% match. Confirmed again here: 105/105
   ``less`` markets have a cap and no floor.

2. **``structured`` is not one thing.** With a ``floor_strike`` it is a
   threshold ("Xander Bogaerts records 2+ RBIs", floor 1.5). Without one it is
   categorical ("Los Angeles D wins"). Treating the first as ``greater`` is an
   *inference*, not a documented convention, so intervals derived that way are
   marked ``inferred`` and are barred from disjointness claims -- an open upper
   ray can never be disjoint from another open upper ray, so implication is the
   only relation an inferred interval can contribute, and implication is in turn
   gated on empirical settled-history confirmation in ``qualify``. A wrong guess
   there costs coverage, never a false candidate.

Anything else -- ``custom``, ``None``, ``structured`` with no strike -- yields no
interval at all. Callers must count those as skips by reason rather than passing
them through; ``scan`` treats an unrecognised ``strike_type`` as a hard error.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# strike_types that map to an interval. Anything outside this set is unknown
# and must be surfaced, not guessed at.
KNOWN_STRIKE_TYPES = frozenset(
    {"greater", "greater_or_equal", "less", "between", "structured", "custom", ""}
)

# The subset that carries no usable numeric interval.
NON_NUMERIC_STRIKE_TYPES = frozenset({"custom", ""})


@dataclass(frozen=True)
class Interval:
    """The set of settlement values for which a market resolves YES.

    ``lo``/``hi`` may be infinite. ``lo_closed``/``hi_closed`` record whether the
    endpoint itself resolves YES -- ``greater`` (open) and ``greater_or_equal``
    (closed) differ by exactly that, and on an integer-valued underlying like
    temperature the difference is a whole outcome.
    """

    lo: float
    hi: float
    lo_closed: bool
    hi_closed: bool
    inferred: bool = False

    def contains(self, value: float) -> bool:
        if value < self.lo or (value == self.lo and not self.lo_closed):
            return False
        if value > self.hi or (value == self.hi and not self.hi_closed):
            return False
        return True


def interval_for(market: dict) -> Optional[Interval]:
    """The YES-interval for one market, or ``None`` if it has no numeric strike.

    Returns ``None`` rather than raising for the categorical types; the caller
    decides whether that is a routine skip or a problem. Raises for a
    ``strike_type`` outside ``KNOWN_STRIKE_TYPES``, because a new convention
    appearing on the exchange is exactly the kind of change that should stop the
    scan rather than be silently absorbed into an existing branch.
    """
    strike_type = market.get("strike_type") or ""
    if strike_type not in KNOWN_STRIKE_TYPES:
        raise ValueError(f"unknown strike_type {strike_type!r} on {market.get('ticker')}")

    floor = _as_float(market.get("floor_strike"))
    cap = _as_float(market.get("cap_strike"))

    if strike_type == "greater":
        return None if floor is None else Interval(floor, math.inf, False, False)
    if strike_type == "greater_or_equal":
        return None if floor is None else Interval(floor, math.inf, True, False)
    if strike_type == "less":
        # cap_strike, NOT floor_strike. See module docstring, trap 1.
        return None if cap is None else Interval(-math.inf, cap, False, False)
    if strike_type == "between":
        if floor is None or cap is None:
            return None
        return Interval(floor, cap, True, True)
    if strike_type == "structured":
        # Threshold-shaped only when a floor is present; see trap 2.
        if floor is None:
            return None
        return Interval(floor, math.inf, False, False, inferred=True)
    return None  # custom / unset: categorical


def _as_float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _lo_before(a: Interval, b: Interval) -> bool:
    """Is a's lower bound at or above b's? (i.e. does b start no later than a)"""
    if b.lo < a.lo:
        return True
    if b.lo > a.lo:
        return False
    # equal bounds: b must be no stricter than a
    return b.lo_closed or not a.lo_closed


def _hi_after(a: Interval, b: Interval) -> bool:
    if b.hi > a.hi:
        return True
    if b.hi < a.hi:
        return False
    return b.hi_closed or not a.hi_closed


def implies(a: Interval, b: Interval) -> bool:
    """True when YES(a) logically forces YES(b), i.e. ``a`` is contained in ``b``.

    This is the S5b relation. The trade it licenses is buy YES(b) + NO(a):
    a true -> b true, so $1 + $0; a false and b true -> $1 + $1; both false ->
    $0 + $1. Payout is at least $1 in every branch, which is what makes it
    riskless *given the relation*.

    An interval is not treated as implying itself -- identical intervals are
    the signature of two different metrics sharing an event (two teams at the
    same handicap), not of a real relation.
    """
    if a == b or (a.lo, a.hi, a.lo_closed, a.hi_closed) == (b.lo, b.hi, b.lo_closed, b.hi_closed):
        return False
    return _lo_before(a, b) and _hi_after(a, b)


def disjoint(a: Interval, b: Interval) -> bool:
    """True when no settlement value satisfies both -- so they cannot both be YES.

    Licenses buy NO(a) + NO(b): at most one YES means at least one NO pays, so
    the payout is at least $1. This is the two-leg case of the S5a NO-basket,
    derived from strike structure instead of the event flag.

    **Inferred intervals are excluded.** ``structured`` markets are mapped to an
    upper ray by inference rather than by a documented convention, and while a
    wrong inference cannot create a false *implication* between two rays, it
    could create a false *disjointness* against a bounded interval. Declining
    here costs coverage and cannot cost correctness.
    """
    if a.inferred or b.inferred:
        return False
    if a.hi < b.lo or b.hi < a.lo:
        return True
    if a.hi == b.lo:
        return not (a.hi_closed and b.lo_closed)
    if b.hi == a.lo:
        return not (b.hi_closed and a.lo_closed)
    return False


def settles_yes(interval: Interval, value: float) -> bool:
    """Whether a settlement value resolves this market YES.

    Used only by ``qualify`` to replay history. Session 31 proved this rule
    against 7,565 real settled markets at 7,565/7,565 exact, across the three
    strike types that existed in that sample.
    """
    return interval.contains(value)
