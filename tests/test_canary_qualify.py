"""
tests/test_canary_qualify.py

Series qualification: what settled history proves, and what it refuses to.

The case this module exists for is live and specific. ``KXMLBSPREAD`` puts eight
``greater`` markets in one event covering **two different metrics** (each team's
winning margin), at overlapping strikes. Interval arithmetic alone will "prove"
that Tampa Bay winning by 4+ implies Chicago winning by 3+, then price a
riskless arbitrage on it. Only the settled record refutes that, so the settled
record is the gate.

Two other properties are pinned here because they are the ways a gate like this
silently stops gating:
- a relation with **zero tests** must never come back confirmed (Session 31's
  bug was a validation reporting success over a sample it had quietly emptied);
- every event not used must be **counted by reason**, so a shrunken sample
  cannot hide behind a clean rate.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from canary import kalshi_rest, qualify
from canary.qualify import CONFIRMED, INSUFFICIENT, REFUTED, build_profile


def _market(ticker, event, strike_type, result, floor=None, cap=None):
    return {
        "ticker": ticker,
        "event_ticker": event,
        "strike_type": strike_type,
        "floor_strike": floor,
        "cap_strike": cap,
        "result": result,
    }


def weather_events(n=40):
    """A temperature ladder: exhaustive, mutually exclusive, all buckets disjoint.

    Mirrors KXHIGHLAX-26AUG02 exactly: one 'less', four 'between' buckets, one
    'greater'. Session 31 verified 1,261/1,261 real city-days had exactly one YES.
    """
    out = {}
    for i in range(n):
        event = f"KXHIGHX-DAY{i:02d}"
        high = 76 + (i % 12)  # sweeps below, through, and above the ladder
        specs = [
            ("T78", "less", None, 78),
            ("B78.5", "between", 78, 79),
            ("B80.5", "between", 80, 81),
            ("B82.5", "between", 82, 83),
            ("B84.5", "between", 84, 85),
            ("T85", "greater", 85, None),
        ]
        markets = []
        for suffix, st, floor, cap in specs:
            if st == "less":
                yes = high < cap
            elif st == "greater":
                yes = high > floor
            else:
                yes = floor <= high <= cap
            markets.append(
                _market(f"{event}-{suffix}", event, st, "yes" if yes else "no", floor, cap)
            )
        out[event] = markets
    return out


def spread_events(n=40):
    """Two metrics in one event -- the KXMLBSPREAD shape. Must be refuted."""
    out = {}
    for i in range(n):
        event = f"KXMLBSPREAD-G{i:02d}"
        margin = (i % 9) - 4  # positive: home wins by margin; negative: away
        markets = []
        for thresh in (1.5, 2.5, 3.5, 4.5):
            markets.append(
                _market(f"{event}-HOME{thresh}", event, "greater",
                        "yes" if margin > thresh else "no", floor=thresh)
            )
            markets.append(
                _market(f"{event}-AWAY{thresh}", event, "greater",
                        "yes" if -margin > thresh else "no", floor=thresh)
            )
        out[event] = markets
    return out


def nested_total_events(n=40):
    """A single-metric nested ladder ('over N runs'): implications all hold,
    but many legs are YES at once, so it is neither exclusive nor exhaustive."""
    out = {}
    for i in range(n):
        event = f"KXMLBTOTAL-G{i:02d}"
        total = 3 + (i % 10)
        markets = [
            _market(f"{event}-O{t}", event, "greater",
                    "yes" if total > t else "no", floor=t + 0.5)
            for t in range(1, 9)
        ]
        out[event] = markets
    return out


@pytest.fixture
def patched(monkeypatch):
    def install(events):
        monkeypatch.setattr(
            kalshi_rest, "settled_markets_by_event", lambda sess, series, **kw: events
        )
        monkeypatch.setattr(
            qualify.kalshi_rest, "settled_markets_by_event",
            lambda sess, series, **kw: events,
        )
        monkeypatch.setattr(qualify.kalshi_rest, "make_session", lambda: object())
    return install


class TestWeatherLadder:
    def test_exhaustive_and_exclusive_are_confirmed(self, patched):
        patched(weather_events())
        p = build_profile("KXHIGHX")
        assert p.yes_count_dist == {"1": 40}
        assert p.exclusive == CONFIRMED
        assert p.exhaustive == CONFIRMED
        assert p.allows_yes_basket and p.allows_no_basket

    def test_disjointness_is_confirmed_by_real_tests(self, patched):
        patched(weather_events())
        p = build_profile("KXHIGHX")
        assert p.disjoint_pairs_tested >= qualify.MIN_PAIR_TESTS
        assert p.disjoint_violations == 0
        assert p.disjointness == CONFIRMED

    def test_implication_is_insufficient_not_confirmed_when_untestable(self, patched):
        """Every bucket in a partition is disjoint from every other, so no
        containment pair exists. Zero tests must read INSUFFICIENT -- a vacuous
        pass is exactly the failure this gate is meant to prevent."""
        patched(weather_events())
        p = build_profile("KXHIGHX")
        assert p.implication_pairs_tested == 0
        assert p.implication == INSUFFICIENT
        assert not p.allows_implication

    def test_rule_of_three_bound_is_recorded_so_qualified_is_not_read_as_proven(
        self, patched
    ):
        patched(weather_events(40))
        p = build_profile("KXHIGHX")
        assert p.failure_bound_95 == pytest.approx(3.0 / 40, abs=1e-4)


class TestMixedMetricSpread:
    def test_cross_metric_implications_are_refuted(self, patched):
        """The KXMLBSPREAD trap. If the home team won by 4, the away team did
        not win at all -- so 'home by 4+' implying 'away by 2+' is falsified by
        the record, and the series is disqualified."""
        patched(spread_events())
        p = build_profile("KXMLBSPREAD")
        assert p.implication_pairs_tested > 0
        assert p.implication_violations > 0
        assert p.implication == REFUTED
        assert not p.allows_implication

    def test_it_is_neither_exclusive_nor_exhaustive(self, patched):
        patched(spread_events())
        p = build_profile("KXMLBSPREAD")
        assert p.exclusive == REFUTED
        assert p.exhaustive == REFUTED
        assert not p.allows_yes_basket and not p.allows_no_basket


class TestNestedLadder:
    def test_single_metric_implications_are_confirmed(self, patched):
        patched(nested_total_events())
        p = build_profile("KXMLBTOTAL")
        assert p.implication_violations == 0
        assert p.implication_pairs_tested >= qualify.MIN_PAIR_TESTS
        assert p.implication == CONFIRMED
        assert p.allows_implication

    def test_a_nested_ladder_is_not_a_basket(self, patched):
        """Several 'over N' legs are YES simultaneously, so the NO-basket
        guarantee fails outright.

        The YES-basket is subtler and is why ``allows_yes_basket`` requires a
        partition rather than mere exhaustiveness. This fixture's totals never
        fall below 3, so the bottom rung is always YES and the series measures
        as ``exhaustive == CONFIRMED`` -- indistinguishable from a structural
        guarantee over 40 events, and false the first time a 0-0 game settles.
        """
        patched(nested_total_events())
        p = build_profile("KXMLBTOTAL")
        assert p.exclusive == REFUTED
        assert p.exhaustive == CONFIRMED, "the empirical regularity really does hold here"
        assert not p.allows_yes_basket, "but a non-partition must not license a basket"
        assert not p.allows_no_basket


class TestEvidenceThresholds:
    def test_too_little_history_qualifies_nothing(self, patched):
        patched(weather_events(5))
        p = build_profile("KXHIGHX")
        assert p.settled_events_used == 5
        assert p.exclusive == INSUFFICIENT
        assert p.exhaustive == INSUFFICIENT
        assert p.disjointness == INSUFFICIENT
        assert not any(
            [p.allows_yes_basket, p.allows_no_basket,
             p.allows_implication, p.allows_disjoint_pair]
        )

    def test_a_single_violation_refutes_regardless_of_sample_size(self, patched):
        events = weather_events(40)
        # Corrupt one day: make two disjoint buckets both resolve YES.
        victim = events["KXHIGHX-DAY00"]
        victim[1]["result"] = "yes"
        victim[2]["result"] = "yes"
        patched(events)
        p = build_profile("KXHIGHX")
        assert p.disjoint_violations >= 1
        assert p.disjointness == REFUTED

    def test_unsettled_events_are_dropped_whole_and_counted(self, patched):
        """A part-settled event would distort the YES-count distribution, which
        is the evidence for exclusivity. Drop it entirely, and say so."""
        events = weather_events(40)
        events["KXHIGHX-DAY00"][0]["result"] = ""
        patched(events)
        p = build_profile("KXHIGHX")
        assert p.settled_events_seen == 40
        assert p.settled_events_used == 39
        assert p.skips.get("event_settlement_in_flight") == 1
        assert p.non_binary_settlement_events == 0

    def test_a_voided_event_is_measured_not_filed_under_unsettled(self, patched):
        """The live find that this split exists for.

        Kalshi finalizes a postponed game or an unplayed match as
        ``result: "scalar"``, ``status: "finalized"`` on every leg. Filed under
        'unsettled' it vanishes, and the profile then reports
        ``exhaustive: confirmed`` while the basket's guaranteed dollar quietly
        fails on 4.1% of real ATP events. It has to come back as a measured rate.
        """
        events = weather_events(40)
        for m in events["KXHIGHX-DAY00"]:
            m["result"] = "scalar"
            m["status"] = "finalized"
        patched(events)
        p = build_profile("KXHIGHX")
        assert p.skips.get("event_non_binary_settlement") == 1
        assert p.skips.get("event_settlement_in_flight") is None
        assert p.non_binary_settlement_events == 1
        assert p.non_binary_settlement_rate == pytest.approx(1 / 40)
        # Still qualifies on the 39 binary events -- the caveat is carried on
        # the candidate, not used to silently disqualify the series.
        assert p.exhaustive == CONFIRMED

    def test_totals_reconcile(self, patched):
        """seen == used + skipped. Session 31's bug survived because a rate
        looked perfect while a total did not add up."""
        events = weather_events(40)
        events["KXHIGHX-DAY00"][0]["result"] = ""
        events["SOLO"] = [_market("SOLO-A", "SOLO", "greater", "yes", floor=1)]
        patched(events)
        p = build_profile("KXHIGHX")
        assert p.settled_events_used + sum(p.skips.values()) >= p.settled_events_seen
        event_level = ("event_single_market", "event_settlement_in_flight",
                       "event_non_binary_settlement")
        assert p.settled_events_used + sum(
            p.skips.get(k, 0) for k in event_level
        ) == p.settled_events_seen
