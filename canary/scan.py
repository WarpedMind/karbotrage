"""One sweep of the open Kalshi universe for S5a/S5b arbitrage candidates.

Two stages, and the second one is not optional.

**Stage 1 -- bulk sweep.** ``/events?with_nested_markets=true`` gives every open
event, its ``mutually_exclusive`` flag, and every child market's asks and depths
in one paginated call. Cheap, and the only way to look at the whole exchange.

**Stage 2 -- re-confirm every candidate against the live order book.** The bulk
snapshot is accurate at the instant it is read and stale within seconds:
measured back-to-back it agreed with ``/orderbook`` on 16/16 markets, but a
snapshot held ~10 seconds while other requests ran saw an actively traded market
move underneath it (``KXMLBSPREAD-...-CWS6``: yes_bid 0.10 -> 0.14, size 3 ->
2071). A full sweep takes tens of seconds. So by the time the arithmetic runs,
the earliest pages describe a book that no longer exists -- and "an arbitrage
that exists only in a stale view of the book" is the precise thing S1 spent its
life trading. Every candidate is therefore re-priced leg by leg from
``/markets/{ticker}/orderbook`` before it is written as real.

Candidates that evaporate on re-check are still logged, as
``vanished_on_recheck``. That ratio is itself the measurement: it separates
"there is a real resting arbitrage on Kalshi" from "our view of the book is
noisy", which is the exact question Session 29 could not answer from a single
snapshot.

Nothing here publishes an event, sizes a position, or places an order.
"""

from __future__ import annotations

import datetime as dt
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Tuple

import requests

from canary import kalshi_rest
from canary.economics import BasketEconomics, Leg, evaluate_basket
from canary.qualify import ProfileStore, SeriesProfile
from canary.strikes import Interval, disjoint, implies, interval_for

KIND_YES_BASKET = "s5a_yes_basket"
KIND_NO_BASKET = "s5a_no_basket"
KIND_IMPLICATION = "s5b_implication"
KIND_DISJOINT_PAIR = "s5b_disjoint_pair"

STATUS_CONFIRMED = "confirmed"
STATUS_VANISHED = "vanished_on_recheck"
STATUS_SNAPSHOT_ONLY = "snapshot_only"


@dataclass
class Candidate:
    ts: str
    kind: str
    series: str
    event_ticker: str
    n_legs: int
    economics: dict
    evidence: dict
    status: str = STATUS_SNAPSHOT_ONLY
    recheck: Optional[dict] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SweepReport:
    """Reconciled totals for one sweep.

    Deliberately a reconciliation rather than a rate. Session 31's most
    expensive bug reported "7,565/7,565 matched" while silently checking 6,305
    of 7,566 -- a perfect-looking number covering a 17% omission. ``events_seen``
    must equal ``events_evaluated`` plus the sum of ``event_skips``, and
    ``check()`` says so out loud.
    """

    started_at: str
    duration_s: float = 0.0
    events_seen: int = 0
    events_evaluated: int = 0
    # Event DISPOSITIONS -- every event lands in exactly one of these or is
    # evaluated. These are what must reconcile.
    event_skips: Dict[str, int] = field(default_factory=dict)
    # Observations made WHILE evaluating an event (a basket leg with a
    # one-sided book, a market with an unusable strike). An evaluated event can
    # produce several of these or none, so they are counted separately -- mixing
    # them into event_skips is what broke the first live reconciliation.
    evaluation_notes: Dict[str, int] = field(default_factory=dict)
    markets_seen: int = 0
    series_seen: int = 0
    profiles_built: int = 0
    candidates: Dict[str, int] = field(default_factory=dict)
    confirmed: Dict[str, int] = field(default_factory=dict)
    vanished: Dict[str, int] = field(default_factory=dict)
    recheck_requests: int = 0
    errors: List[str] = field(default_factory=list)

    def check(self) -> Optional[str]:
        accounted = self.events_evaluated + sum(self.event_skips.values())
        if accounted != self.events_seen:
            return (
                f"sweep does not reconcile: seen={self.events_seen} "
                f"evaluated={self.events_evaluated} "
                f"skipped={sum(self.event_skips.values())} (total {accounted})"
            )
        return None

    def to_dict(self) -> dict:
        out = asdict(self)
        out["record"] = "sweep"
        out["reconciles"] = self.check() is None
        return out


def _f(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def yes_leg_from_snapshot(market: dict) -> Optional[Leg]:
    """Buy YES at the ask; depth is ``yes_ask_size_fp``."""
    price = _f(market.get("yes_ask_dollars"))
    depth = _f(market.get("yes_ask_size_fp")) or 0.0
    if price is None or not (0.0 < price < 1.0):
        return None
    return Leg(ticker=market["ticker"], side="yes", price=price, depth=depth)


def no_leg_from_snapshot(market: dict) -> Optional[Leg]:
    """Buy NO at the ask; depth is ``yes_bid_size_fp``.

    Not a typo and not a guess: a NO ask is a resting YES bid at ``1 - price``,
    so the quantity behind it is the YES bid size. There is no
    ``no_ask_size_fp`` field. Confirmed against ``/orderbook`` on 16 live
    markets -- see ``kalshi_rest``.
    """
    price = _f(market.get("no_ask_dollars"))
    depth = _f(market.get("yes_bid_size_fp")) or 0.0
    if price is None or not (0.0 < price < 1.0):
        return None
    return Leg(ticker=market["ticker"], side="no", price=price, depth=depth)


def _leg_from_book(top: kalshi_rest.BookTop, side: str) -> Optional[Leg]:
    price = top.yes_ask if side == "yes" else top.no_ask
    depth = top.yes_ask_qty if side == "yes" else top.no_ask_qty
    if price is None or not (0.0 < price < 1.0):
        return None
    return Leg(ticker=top.ticker, side=side, price=price, depth=depth)


def _evidence(profile: SeriesProfile, event: dict, basis: str) -> dict:
    """What licenses this candidate's payout guarantee, stated explicitly.

    ``api_mutually_exclusive`` is recorded alongside the empirical verdict
    rather than used as the gate. A disagreement between the flag and the
    settled record is worth seeing: Session 29's 78 false baskets all came from
    trusting event grouping over evidence.
    """
    return {
        "basis": basis,
        "series": profile.series,
        "settled_events_used": profile.settled_events_used,
        "failure_bound_95": profile.failure_bound_95,
        "exclusive": profile.exclusive,
        "exhaustive": profile.exhaustive,
        "implication": profile.implication,
        "disjointness": profile.disjointness,
        "implication_pairs_tested": profile.implication_pairs_tested,
        "disjoint_pairs_tested": profile.disjoint_pairs_tested,
        "api_mutually_exclusive": bool(event.get("mutually_exclusive")),
        # The measured share of this series' settled events that finalized on
        # something other than yes/no -- a postponed game, an unplayed match.
        # On one of those, NO basket pays its guaranteed amount. 4.1% on ATP
        # matches. Attached to every candidate so the guarantee is never read
        # as unconditional.
        "non_binary_settlement_rate": profile.non_binary_settlement_rate,
    }


def _active(market: dict) -> bool:
    status = market.get("status")
    return status in (None, "", "active", "open")


class EventOutcome(NamedTuple):
    """What happened to one event.

    ``skipped`` is a *disposition*: set when the event was not evaluated at all,
    and it is what the sweep reconciles against. ``notes`` are observations made
    while evaluating -- an event can produce several or none, so counting them
    as dispositions overstates the total. Mixing the two is precisely what broke
    the first live sweep's reconciliation (8,631 accounted against 8,608 seen).
    """

    candidates: List["Candidate"]
    notes: Counter
    skipped: Optional[str] = None


def evaluate_event(
    event: dict, profile: SeriesProfile, *, now: Optional[str] = None
) -> EventOutcome:
    """All S5a/S5b candidates in one event, from snapshot prices."""
    ts = now or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    reasons: Counter = Counter()
    out: List[Candidate] = []
    markets = [m for m in (event.get("markets") or []) if _active(m)]
    if len(markets) < 2:
        return EventOutcome(out, reasons, "event_fewer_than_two_active_markets")

    series = profile.series
    event_ticker = event.get("event_ticker", "")

    # ---- S5a: whole-event baskets -------------------------------------
    # A basket needs every leg. A missing or one-sided leg does not make the
    # basket cheaper, it makes the payout guarantee false -- so an incomplete
    # basket is skipped, never partially priced.
    if profile.allows_yes_basket:
        legs = [yes_leg_from_snapshot(m) for m in markets]
        if all(legs):
            econ = evaluate_basket(legs, payout_per_set=1.0)
            if econ and econ.is_candidate:
                out.append(
                    Candidate(
                        ts=ts, kind=KIND_YES_BASKET, series=series,
                        event_ticker=event_ticker, n_legs=len(legs),
                        economics=econ.to_dict(),
                        evidence=_evidence(profile, event, "exhaustive_from_settled_history"),
                    )
                )
        else:
            reasons["yes_basket_incomplete_book"] += 1

    if profile.allows_no_basket:
        legs = [no_leg_from_snapshot(m) for m in markets]
        if all(legs):
            # At most one YES => at least N-1 of the NO legs pay $1.
            econ = evaluate_basket(legs, payout_per_set=float(len(legs) - 1))
            if econ and econ.is_candidate:
                out.append(
                    Candidate(
                        ts=ts, kind=KIND_NO_BASKET, series=series,
                        event_ticker=event_ticker, n_legs=len(legs),
                        economics=econ.to_dict(),
                        evidence=_evidence(profile, event, "exclusive_from_settled_history"),
                    )
                )
        else:
            reasons["no_basket_incomplete_book"] += 1

    # ---- S5b: pairwise relations --------------------------------------
    if not (profile.allows_implication or profile.allows_disjoint_pair):
        return EventOutcome(out, reasons)

    intervals: List[Optional[Interval]] = []
    for market in markets:
        try:
            intervals.append(interval_for(market))
        except ValueError:
            reasons["market_unknown_strike_type"] += 1
            intervals.append(None)

    for i, iv_a in enumerate(intervals):
        if iv_a is None:
            continue
        for j, iv_b in enumerate(intervals):
            if i == j or iv_b is None:
                continue
            if profile.allows_implication and implies(iv_a, iv_b):
                # a implies b: buy YES(b) + NO(a). Payout >= $1 in every branch.
                leg_b = yes_leg_from_snapshot(markets[j])
                leg_a = no_leg_from_snapshot(markets[i])
                if leg_a and leg_b:
                    econ = evaluate_basket([leg_b, leg_a], payout_per_set=1.0)
                    if econ and econ.is_candidate:
                        out.append(
                            Candidate(
                                ts=ts, kind=KIND_IMPLICATION, series=series,
                                event_ticker=event_ticker, n_legs=2,
                                economics=econ.to_dict(),
                                evidence=_evidence(
                                    profile, event, "implication_from_settled_history"
                                ),
                            )
                        )
            elif (
                j > i
                and profile.allows_disjoint_pair
                and disjoint(iv_a, iv_b)
            ):
                # cannot both be YES: buy NO(a) + NO(b). Payout >= $1.
                leg_a = no_leg_from_snapshot(markets[i])
                leg_b = no_leg_from_snapshot(markets[j])
                if leg_a and leg_b:
                    econ = evaluate_basket([leg_a, leg_b], payout_per_set=1.0)
                    if econ and econ.is_candidate:
                        out.append(
                            Candidate(
                                ts=ts, kind=KIND_DISJOINT_PAIR, series=series,
                                event_ticker=event_ticker, n_legs=2,
                                economics=econ.to_dict(),
                                evidence=_evidence(
                                    profile, event, "disjointness_from_settled_history"
                                ),
                            )
                        )
    return EventOutcome(out, reasons)


def reconfirm(
    candidate: Candidate,
    session: requests.Session,
    *,
    book_cache: Optional[Dict[str, kalshi_rest.BookTop]] = None,
) -> Candidate:
    """Re-price a candidate from the live order book and record what happened.

    Mutates and returns the candidate with ``status`` set and ``recheck``
    carrying the re-priced economics. A candidate that no longer prices as an
    arbitrage is kept, not discarded -- the survival rate is the measurement.
    """
    cache = book_cache if book_cache is not None else {}
    legs: List[Leg] = []
    for raw in candidate.economics.get("legs", []):
        ticker, side = raw["ticker"], raw["side"]
        top = cache.get(ticker)
        if top is None:
            top = kalshi_rest.orderbook_top(session, ticker)
            cache[ticker] = top
        leg = _leg_from_book(top, side)
        if leg is None:
            candidate.status = STATUS_VANISHED
            candidate.recheck = {"reason": f"leg {ticker} has no {side} offer"}
            return candidate
        legs.append(leg)

    econ = evaluate_basket(legs, payout_per_set=candidate.economics["payout_per_set"])
    if econ is None:
        candidate.status = STATUS_VANISHED
        candidate.recheck = {"reason": "basket no longer priceable"}
        return candidate

    candidate.recheck = econ.to_dict()
    candidate.status = STATUS_CONFIRMED if econ.is_candidate else STATUS_VANISHED
    return candidate


def series_volume(events: Iterable[dict]) -> Dict[str, float]:
    """Total 24h contract volume of each series' open multi-market events.

    Used to decide which series get qualified first while the profile cache
    fills. The first live sweep spent its entire budget on the 60 series the
    events endpoint happens to return first -- ``KXNEXTNATOSECGEN``,
    ``KXNEWPOPE``, ``KXXISUCCESSOR`` and the like -- every one of which is a
    long-horizon "who will be next" market with **zero settled events**, so
    none could qualify and the sweep evaluated nothing at all. Volume is the
    principled ordering: a series with no volume cannot be traded even if a
    mispricing appeared in it.
    """
    totals: Dict[str, float] = {}
    for event in events:
        series = event.get("series_ticker") or ""
        markets = event.get("markets") or []
        if not series or len(markets) < 2:
            continue
        total = 0.0
        for market in markets:
            total += _f(market.get("volume_24h_fp")) or 0.0
        totals[series] = totals.get(series, 0.0) + total
    return totals


def sweep(
    *,
    session: Optional[requests.Session] = None,
    store: Optional[ProfileStore] = None,
    events: Optional[Iterable[dict]] = None,
    reconfirm_candidates: bool = True,
    max_reconfirm: int = 200,
    max_new_profiles: int = 60,
) -> Tuple[List[Candidate], SweepReport]:
    """One full pass: prioritise, qualify, evaluate, re-confirm.

    ``events`` may be supplied to evaluate a fixed set (used by tests);
    otherwise the whole open universe is pulled -- 8,625 open events across
    2,503 multi-market series, measured 2026-08-02.

    The universe is streamed **twice**: once to tally per-series volume without
    holding 76,000 market dicts in memory, and once to evaluate. Two passes of
    ~43 pages cost about twenty extra seconds against a five-minute interval,
    which is a better trade than the memory an in-process materialisation would
    need on a small VPS.

    ``max_new_profiles`` bounds how many series this pass qualifies from
    settled history. Building all 2,503 at once would be thousands of paginated
    requests; instead the cache fills over successive sweeps, highest-volume
    first, and the events not yet covered are counted as
    ``event_profile_not_yet_built``. Coverage is a reported number, not an
    assumption.
    """
    sess = session or kalshi_rest.make_session()
    profiles = store if store is not None else ProfileStore()
    started = time.monotonic()
    report = SweepReport(
        started_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    )
    skips: Counter = Counter()
    notes: Counter = Counter()
    found: List[Candidate] = []
    series_seen = set()
    profiles_built = 0

    materialised = list(events) if events is not None else None

    def stream() -> Iterable[dict]:
        if materialised is not None:
            return materialised
        return kalshi_rest.iter_open_events(sess)

    # Pass 1: decide which unqualified series are worth this sweep's budget.
    volumes = series_volume(stream())
    wanted = sorted(
        (s for s in volumes if not profiles.has_fresh(s)),
        key=lambda s: volumes[s],
        reverse=True,
    )[:max_new_profiles]
    for series in wanted:
        try:
            profiles.get(series, session=sess)
            profiles_built += 1
        except kalshi_rest.KalshiRestError as exc:
            report.errors.append(f"profile {series}: {exc}")

    # Pass 2: evaluate against whatever is qualified now.
    for event in stream():
        report.events_seen += 1
        markets = event.get("markets") or []
        report.markets_seen += len(markets)
        series = event.get("series_ticker") or ""
        if not series:
            skips["event_no_series_ticker"] += 1
            continue
        series_seen.add(series)
        if len(markets) < 2:
            skips["event_fewer_than_two_markets"] += 1
            continue

        if not profiles.has_fresh(series):
            skips["event_profile_not_yet_built"] += 1
            continue
        profile = profiles.get(series, session=sess, allow_fetch=False)
        if profile is None:
            skips["event_no_profile"] += 1
            continue
        if not (
            profile.allows_yes_basket
            or profile.allows_no_basket
            or profile.allows_implication
            or profile.allows_disjoint_pair
        ):
            skips["event_series_not_qualified"] += 1
            continue

        outcome = evaluate_event(event, profile)
        notes.update(outcome.notes)
        if outcome.skipped:
            skips[outcome.skipped] += 1
            continue
        report.events_evaluated += 1
        found.extend(outcome.candidates)

    report.series_seen = len(series_seen)
    report.profiles_built = profiles_built
    report.event_skips = dict(skips)
    report.evaluation_notes = dict(notes)
    for candidate in found:
        report.candidates[candidate.kind] = report.candidates.get(candidate.kind, 0) + 1

    if reconfirm_candidates and found:
        book_cache: Dict[str, kalshi_rest.BookTop] = {}
        for candidate in found[:max_reconfirm]:
            try:
                reconfirm(candidate, sess, book_cache=book_cache)
            except kalshi_rest.KalshiRestError as exc:
                candidate.status = STATUS_SNAPSHOT_ONLY
                candidate.recheck = {"reason": f"recheck failed: {exc}"}
                report.errors.append(f"recheck {candidate.event_ticker}: {exc}")
        report.recheck_requests = len(book_cache)
        for candidate in found:
            bucket = (
                report.confirmed
                if candidate.status == STATUS_CONFIRMED
                else report.vanished
            )
            bucket[candidate.kind] = bucket.get(candidate.kind, 0) + 1

    report.duration_s = round(time.monotonic() - started, 2)
    return found, report
