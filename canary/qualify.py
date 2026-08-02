"""Series qualification: what settled history actually proves about a series.

This module exists because of one live example. ``KXMLBSPREAD-26AUG021340CWSTB``
holds eight markets in a single event, all ``strike_type=greater``, covering
**two different metrics** -- Tampa Bay's winning margin and Chicago's:

    ...-TB4    greater  floor=3.5   "Tampa Bay wins by over 3.5 runs"
    ...-CWS4   greater  floor=3.5   "Chicago WS wins by over 3.5 runs"

Pure interval arithmetic on that event will happily "prove" that Tampa Bay
winning by 4+ implies Chicago winning by 3+, and then price a riskless
arbitrage on it. That is Session 29's trap with a sharper edge: Session 29 found
78 apparent sum-to-one baskets and confirmed 0 of 78 were real, all of them
ladders misidentified by grouping on ``event_ticker`` without checking whether
the markets were comparable at all.

Text heuristics were tried and rejected. Stripping numbers from
``rules_primary`` separates the two teams correctly but also splits a legitimate
weather ladder, because ``less``/``between``/``greater`` markets phrase their
rules differently ("is less than 78" vs "is between 78-79"). An
``expiration_value`` identity test was tried too and is **wrong**: measured
across 123 settled ``KXMLBSPREAD`` events, all markets in an event share one
``expiration_value`` despite being on different metrics.

So the rule is: **structure proposes, history disposes.** Interval arithmetic
generates candidate relations; a relation is only usable if it has never once
been violated across the series' real settled outcomes. On KXMLBSPREAD the
cross-team implications are falsified immediately -- if Tampa Bay won by 4,
Chicago did not win at all -- and the series is excluded wholesale.

Exclusion is at series granularity, which is coarse: one mixed-metric event
disqualifies a series even for its well-behaved pairs. That is deliberate. A
false positive here manufactures a confident stream of fake arbitrage; a false
negative costs coverage in a process whose entire output is a log file.

### On the evidence thresholds
``MIN_SETTLED_EVENTS`` is a **logging filter, not a risk control**, and it must
never be promoted into one. A relation that held in *n* independent settled
events with zero violations still carries an upper 95% bound on its failure
probability of roughly ``3/n`` (the rule of three). At the default of 30 that
bound is ~10% -- nowhere near "riskless". The bound is computed and written into
every profile and every logged candidate precisely so that nobody can read
"qualified" as "proven". Nothing in this package trades, so the number's only
job is to keep the candidate log from filling with noise.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import requests

from canary import kalshi_rest
from canary.strikes import Interval, disjoint, implies, interval_for

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
PROFILE_PATH = os.path.join(CACHE_DIR, "series_profiles.json")

# See "On the evidence thresholds" above -- this is a noise filter, and the
# rule-of-three bound it implies is recorded alongside every verdict.
MIN_SETTLED_EVENTS = 30
MIN_PAIR_TESTS = 30

CONFIRMED = "confirmed"
REFUTED = "refuted"
INSUFFICIENT = "insufficient_evidence"


@dataclass
class SeriesProfile:
    """What the settled record proves about one series. Every field is measured."""

    series: str
    built_at: str
    settled_events_seen: int
    settled_events_used: int
    markets_used: int
    yes_count_dist: Dict[str, int] = field(default_factory=dict)
    # Verdicts, each CONFIRMED / REFUTED / INSUFFICIENT
    exclusive: str = INSUFFICIENT
    exhaustive: str = INSUFFICIENT
    implication: str = INSUFFICIENT
    disjointness: str = INSUFFICIENT
    implication_pairs_tested: int = 0
    implication_violations: int = 0
    disjoint_pairs_tested: int = 0
    disjoint_violations: int = 0
    # Reconciliation: every event and market not used, counted by reason.
    skips: Dict[str, int] = field(default_factory=dict)
    failure_bound_95: Optional[float] = None
    # How often this series settles on something other than yes/no -- a
    # cancelled game or an unplayed match. Kalshi resolves those to a "fair
    # price" rather than refunding or zeroing, so whether the basket guarantee
    # survives depends entirely on whether those fair prices sum to $1. They
    # do; see scalar_sum_to_one. Rate still carried onto every candidate.
    non_binary_settlement_events: int = 0
    non_binary_settlement_rate: Optional[float] = None
    # Of those cancelled events, how many had fair prices summing to $1 (which
    # preserves both basket guarantees) versus not-or-unverifiable. Measured
    # across 8 series: 243/243 sum to one. A violation here IS a real hole and
    # disqualifies the series' baskets.
    scalar_sum_to_one_ok: int = 0
    scalar_sum_to_one_violations: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "SeriesProfile":
        return cls(**raw)

    @property
    def allows_yes_basket(self) -> bool:
        """A YES-basket pays $1 only if at least one leg must resolve YES.

        Strictly, exhaustiveness alone licenses the guarantee. This requires
        exclusivity too -- i.e. a genuine partition, exactly one YES every time
        -- and the extra condition is deliberate.

        "At least one YES in n settled events" means different things for
        different shapes. On a temperature partition it is structural: the
        buckets tile the outcome space. On a *nested* ladder ("over 1 run",
        "over 2 runs", ...) it is a coincidence of the sample -- the bottom rung
        is almost always YES, so 40 clean events look identical to a structural
        guarantee, right up until a 0-0 game settles every leg NO and the
        basket pays nothing. A partition has no such tail. Requiring
        exactly-one costs the nested-ladder YES-basket, which was never
        economically plausible anyway (eight nested YES legs cost far more than
        the dollar they pay), and removes a concrete, foreseeable failure.
        """
        return (
            self.exhaustive == CONFIRMED
            and self.exclusive == CONFIRMED
            and self.scalar_settlement_safe
        )

    @property
    def allows_no_basket(self) -> bool:
        """A NO-basket pays $(N-1) only if at most one leg can resolve YES."""
        return self.exclusive == CONFIRMED and self.scalar_settlement_safe

    @property
    def scalar_settlement_safe(self) -> bool:
        """Do this series' cancelled events preserve the sum-to-one invariant?

        Both basket guarantees rest on it, and both fail together if it breaks:
        a YES-basket pays ``sum(settlement)`` and a NO-basket pays
        ``sum(1 - settlement) = N - sum(settlement)``. Measured across 8 series,
        243 of 243 cancelled events sum to exactly $1.00 -- but this is checked
        per series rather than assumed globally, because a single counterexample
        would be a real hole and an unverifiable event counts against.
        """
        return self.scalar_sum_to_one_violations == 0

    @property
    def allows_implication(self) -> bool:
        return self.implication == CONFIRMED

    @property
    def allows_disjoint_pair(self) -> bool:
        return self.disjointness == CONFIRMED


def _rule_of_three(n: int) -> Optional[float]:
    """Upper 95% bound on failure probability after n clean trials."""
    return None if n <= 0 else round(3.0 / n, 4)


def _result(market: dict) -> Optional[str]:
    result = market.get("result")
    return result if result in ("yes", "no") else None


def _is_finalized_non_binary(market: dict) -> bool:
    """A market that finished settling on something other than yes/no.

    Kalshi reports these as ``result: "scalar"`` with ``status: "finalized"``
    and ``market_type: "binary"`` -- a cancelled event. Observed live on a
    postponed baseball game (KXMLBGAME-26JUN251945AZSTL) and a tennis match
    never played (KXATPMATCH-26JUL28MICMCD, whose rules require "after a ball
    has been played"). Every leg of the event carries it. Measured on the live
    archive: 0.7% of KXMLBGAME events and **4.1% of KXATPMATCH events**.

    This is not the same as a settlement still in flight, and conflating the two
    is how a basket's payout guarantee quietly stops being true.

    **RESOLVED, and in the guarantee's favour** -- see ``scalar_sum_to_one``.
    """
    return (
        market.get("result") not in ("yes", "no")
        and market.get("status") == "finalized"
    )


def scalar_sum_to_one(markets: List[dict]) -> Optional[bool]:
    """Do a cancelled event's legs settle to fair prices summing to $1?

    This is the whole basket guarantee under cancellation, and it was an open
    question until it was measured. Kalshi's own ``rules_secondary`` says a
    cancelled match "will resolve to a **fair price** in accordance with the
    rules" -- so it is neither a refund at cost nor a zero, and the payout
    depends entirely on whether those fair prices preserve the sum-to-one
    invariant that both baskets rely on.

    They do. Every leg carries ``settlement_value_dollars``, and across **243
    scalar-settled events in 8 series (236 two-leg, 7 three-leg), 243 sum to
    exactly $1.00** -- zero violations, zero unverifiable, reconciled. So a
    YES-basket still pays ``sum(settlement) = $1`` and a NO-basket still pays
    ``sum(1 - settlement) = $(N-1)``: exactly the binary guarantee.

    Returns ``None`` when a leg has no settlement value, because "could not
    check" must never be recorded as "checked and fine".
    """
    total = 0.0
    for market in markets:
        raw = market.get("settlement_value_dollars")
        if raw in (None, ""):
            return None
        try:
            total += float(raw)
        except (TypeError, ValueError):
            return None
    return abs(total - 1.0) < 1e-6


def build_profile(
    series: str,
    *,
    session: Optional[requests.Session] = None,
) -> SeriesProfile:
    """Replay a series' settled history and record what it proves.

    Counts every skip by reason and reconciles them against the total seen, so
    a shrunken sample cannot hide behind a clean-looking rate. Session 31's
    ``less``/``cap_strike`` bug printed "7,565/7,565 matched" while silently
    dropping 1,255 markets; it was caught only because a total failed to add up.
    """
    sess = session or kalshi_rest.make_session()
    by_event = kalshi_rest.settled_markets_by_event(sess, series)

    skips: Counter = Counter()
    yes_counts: Counter = Counter()
    events_used = 0
    markets_used = 0
    imp_tested = imp_bad = 0
    dis_tested = dis_bad = 0
    non_binary_events = 0
    scalar_ok = scalar_bad = 0

    for event_ticker, markets in by_event.items():
        if len(markets) < 2:
            skips["event_single_market"] += 1
            continue
        results = [_result(m) for m in markets]
        if any(r is None for r in results):
            # Either way the event is dropped from the YES-count distribution --
            # a part-settled event would distort the evidence for exclusivity
            # and exhaustiveness. But the two reasons mean very different
            # things, and labelling both "unsettled" is what hid the void rate
            # on the first live run.
            if any(_is_finalized_non_binary(m) for m in markets):
                skips["event_non_binary_settlement"] += 1
                non_binary_events += 1
                # A cancelled event still honours the basket guarantee IF its
                # fair prices sum to $1. Check rather than assume; an
                # unverifiable event counts against, never for.
                verdict = scalar_sum_to_one(markets)
                if verdict is True:
                    scalar_ok += 1
                else:
                    scalar_bad += 1
            else:
                skips["event_settlement_in_flight"] += 1
            continue

        events_used += 1
        markets_used += len(markets)
        yes_counts[sum(1 for r in results if r == "yes")] += 1

        intervals: List[Optional[Interval]] = []
        for market in markets:
            try:
                intervals.append(interval_for(market))
            except ValueError:
                skips["market_unknown_strike_type"] += 1
                intervals.append(None)
        n_no_interval = sum(1 for iv in intervals if iv is None)
        if n_no_interval:
            skips["market_no_numeric_strike"] += n_no_interval

        for i, iv_a in enumerate(intervals):
            if iv_a is None:
                continue
            for j, iv_b in enumerate(intervals):
                if i == j or iv_b is None:
                    continue
                if implies(iv_a, iv_b):
                    imp_tested += 1
                    if results[i] == "yes" and results[j] != "yes":
                        imp_bad += 1
                # disjoint() is symmetric; test each unordered pair once
                elif j > i and disjoint(iv_a, iv_b):
                    dis_tested += 1
                    if results[i] == "yes" and results[j] == "yes":
                        dis_bad += 1

    profile = SeriesProfile(
        series=series,
        built_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        settled_events_seen=len(by_event),
        settled_events_used=events_used,
        markets_used=markets_used,
        yes_count_dist={str(k): v for k, v in sorted(yes_counts.items())},
        implication_pairs_tested=imp_tested,
        implication_violations=imp_bad,
        disjoint_pairs_tested=dis_tested,
        disjoint_violations=dis_bad,
        skips=dict(skips),
        failure_bound_95=_rule_of_three(events_used),
        non_binary_settlement_events=non_binary_events,
        non_binary_settlement_rate=(
            round(non_binary_events / len(by_event), 5) if by_event else None
        ),
        scalar_sum_to_one_ok=scalar_ok,
        scalar_sum_to_one_violations=scalar_bad,
    )

    enough_events = events_used >= MIN_SETTLED_EVENTS
    counts = list(yes_counts.elements())

    if not enough_events:
        profile.exclusive = profile.exhaustive = INSUFFICIENT
    else:
        profile.exclusive = CONFIRMED if max(counts) <= 1 else REFUTED
        profile.exhaustive = CONFIRMED if min(counts) >= 1 else REFUTED

    profile.implication = _verdict(enough_events, imp_tested, imp_bad)
    profile.disjointness = _verdict(enough_events, dis_tested, dis_bad)
    return profile


def _verdict(enough_events: bool, tested: int, violations: int) -> str:
    """A relation is confirmed only by evidence that could have refuted it.

    Zero tests is ``INSUFFICIENT``, never ``CONFIRMED``. A vacuous pass is the
    exact shape of the Session 31 bug: a validation reporting success over a
    sample it had quietly emptied.
    """
    if violations > 0:
        return REFUTED
    if not enough_events or tested < MIN_PAIR_TESTS:
        return INSUFFICIENT
    return CONFIRMED


class ProfileStore:
    """Disk-backed cache of series profiles.

    Profiles are rebuilt when older than ``max_age_days`` -- a series' structure
    can change (Kalshi adds strikes, renames, or retires a ladder), and a stale
    ``confirmed`` is more dangerous than no profile at all.
    """

    def __init__(self, path: str = PROFILE_PATH, max_age_days: float = 7.0):
        self.path = path
        self.max_age_days = max_age_days
        self._profiles: Dict[str, SeriesProfile] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return
        for series, data in raw.items():
            try:
                self._profiles[series] = SeriesProfile.from_dict(data)
            except TypeError:
                # A profile written by an older schema; rebuild rather than
                # half-load it.
                continue

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".part"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({s: p.to_dict() for s, p in self._profiles.items()}, fh, indent=1)
        os.replace(tmp, self.path)

    def _is_fresh(self, profile: SeriesProfile) -> bool:
        try:
            built = dt.datetime.fromisoformat(profile.built_at)
        except ValueError:
            return False
        if built.tzinfo is None:
            built = built.replace(tzinfo=dt.timezone.utc)
        age = dt.datetime.now(dt.timezone.utc) - built
        return age.total_seconds() < self.max_age_days * 86400

    def has_fresh(self, series: str) -> bool:
        cached = self._profiles.get(series)
        return cached is not None and self._is_fresh(cached)

    def get(
        self,
        series: str,
        *,
        session: Optional[requests.Session] = None,
        allow_fetch: bool = True,
    ) -> Optional[SeriesProfile]:
        """The series' profile, building it from settled history if needed.

        ``allow_fetch=False`` returns whatever is cached (possibly stale,
        possibly nothing) without going to the network. The sweep uses that to
        bound how many new profiles one pass will build: the open universe has
        2,503 distinct multi-market series, so building them all at once would
        be thousands of paginated requests in a single sweep. Instead the cache
        fills over successive sweeps and the coverage gap is reported rather
        than hidden.
        """
        cached = self._profiles.get(series)
        if cached is not None and self._is_fresh(cached):
            return cached
        if not allow_fetch:
            return cached
        profile = build_profile(series, session=session)
        self._profiles[series] = profile
        return profile

    def all(self) -> Dict[str, SeriesProfile]:
        return dict(self._profiles)
