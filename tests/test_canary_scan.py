"""
tests/test_canary_scan.py

The sweep: leg construction, candidate detection, and the order-book re-check.

Three things are pinned here because getting any of them wrong produces a
confident stream of fake arbitrage rather than an error:

1. **Which field is the depth for a NO leg.** There is no ``no_ask_size_fp``.
   A NO ask is a resting YES bid at ``1 - price``, so the quantity behind it is
   ``yes_bid_size_fp`` -- the field with the opposite name. Confirmed live
   against ``/orderbook`` on 16 markets.
2. **An incomplete basket is skipped, never partially priced.** Dropping a leg
   with a one-sided book does not make the basket cheaper, it makes the payout
   guarantee false.
3. **Every candidate is re-priced against the live book before it counts.** The
   bulk snapshot is stale within seconds on traded markets, and "an arbitrage
   that exists only in a stale view of the book" is precisely what S1 traded.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from canary.kalshi_rest import BookTop
from canary.qualify import CONFIRMED, INSUFFICIENT, SeriesProfile
from canary.scan import (
    KIND_DISJOINT_PAIR,
    KIND_IMPLICATION,
    KIND_NO_BASKET,
    KIND_YES_BASKET,
    STATUS_CONFIRMED,
    STATUS_VANISHED,
    Candidate,
    evaluate_event,
    no_leg_from_snapshot,
    reconfirm,
    sweep,
    yes_leg_from_snapshot,
)


def profile(**overrides) -> SeriesProfile:
    base = dict(
        series="KXTEST",
        built_at="2026-08-02T00:00:00+00:00",
        settled_events_seen=40,
        settled_events_used=40,
        markets_used=240,
        exclusive=INSUFFICIENT,
        exhaustive=INSUFFICIENT,
        implication=INSUFFICIENT,
        disjointness=INSUFFICIENT,
        failure_bound_95=0.075,
    )
    base.update(overrides)
    return SeriesProfile(**base)


def market(ticker, yes_ask, no_ask, yes_ask_size=1000, yes_bid_size=1000, **extra):
    m = {
        "ticker": ticker,
        "status": "active",
        "yes_ask_dollars": f"{yes_ask:.4f}",
        "no_ask_dollars": f"{no_ask:.4f}",
        "yes_ask_size_fp": f"{yes_ask_size:.2f}",
        "yes_bid_size_fp": f"{yes_bid_size:.2f}",
        "strike_type": "custom",
    }
    m.update(extra)
    return m


def event(series, ticker, markets, mutually_exclusive=True):
    return {
        "series_ticker": series,
        "event_ticker": ticker,
        "mutually_exclusive": mutually_exclusive,
        "markets": markets,
    }


class TestLegConstruction:
    def test_yes_leg_uses_the_ask_and_the_ask_size(self):
        leg = yes_leg_from_snapshot(market("A", 0.30, 0.72, yes_ask_size=42, yes_bid_size=9))
        assert leg.side == "yes" and leg.price == 0.30 and leg.depth == 42

    def test_no_leg_uses_the_no_ask_but_the_YES_BID_size(self):
        """The naming trap. Reading a 'no_*' size field here would size the leg
        off the wrong side of the book -- the Session 26 bug class."""
        leg = no_leg_from_snapshot(market("A", 0.30, 0.72, yes_ask_size=42, yes_bid_size=9))
        assert leg.side == "no" and leg.price == 0.72
        assert leg.depth == 9, "NO depth is yes_bid_size_fp, not yes_ask_size_fp"

    def test_a_one_sided_book_yields_no_leg(self):
        assert yes_leg_from_snapshot(market("A", 1.00, 0.02)) is None
        assert no_leg_from_snapshot(market("A", 0.02, 1.00)) is None

    def test_prices_are_never_taken_from_the_bid(self):
        m = market("A", 0.30, 0.72)
        m["yes_bid_dollars"] = "0.2800"
        m["no_bid_dollars"] = "0.7000"
        assert yes_leg_from_snapshot(m).price == 0.30
        assert no_leg_from_snapshot(m).price == 0.72


class TestBasketDetection:
    def test_cheap_yes_basket_on_an_exhaustive_series_is_a_candidate(self):
        ev = event("KXTEST", "E1", [
            market("A", 0.30, 0.72), market("B", 0.30, 0.72), market("C", 0.30, 0.72),
        ])
        found, _, _skipped = evaluate_event(ev, profile(exhaustive=CONFIRMED, exclusive=CONFIRMED))
        assert [c.kind for c in found] == [KIND_YES_BASKET]
        assert found[0].economics["cost_per_set"] == pytest.approx(0.90)
        assert found[0].evidence["basis"] == "exhaustive_from_settled_history"

    def test_a_yes_basket_is_not_priced_when_the_series_is_not_exhaustive(self):
        ev = event("KXTEST", "E1", [market("A", 0.30, 0.72), market("B", 0.30, 0.72)])
        found, _, _skipped = evaluate_event(ev, profile())
        assert found == []

    def test_no_basket_uses_n_minus_one_payout(self):
        ev = event("KXTEST", "E1", [market(t, 0.35, 0.60) for t in "ABCD"])
        found, _, _skipped = evaluate_event(ev, profile(exclusive=CONFIRMED))
        assert [c.kind for c in found] == [KIND_NO_BASKET]
        assert found[0].economics["payout_per_set"] == 3.0
        assert found[0].economics["cost_per_set"] == pytest.approx(2.40)

    def test_an_incomplete_basket_is_skipped_and_counted_not_partially_priced(self):
        """Leg C has no YES offer. Pricing A+B alone would look like a $0.60
        basket for a guaranteed $1 -- and the guarantee would be false.

        C is priced 1.00/0.99 rather than 1.00/0.02 so that the NO-basket on the
        same event is genuinely unprofitable ($2.43 for a guaranteed $2) and
        this test isolates the YES-basket behaviour it is named for.
        """
        ev = event("KXTEST", "E1", [
            market("A", 0.30, 0.72), market("B", 0.30, 0.72), market("C", 1.00, 0.99),
        ])
        found, reasons, _skipped = evaluate_event(ev, profile(exhaustive=CONFIRMED, exclusive=CONFIRMED))
        assert found == []
        assert reasons["yes_basket_incomplete_book"] == 1

    def test_an_efficiently_priced_event_yields_nothing(self):
        """Both baskets must be checked: three legs at yes_ask 0.40 cost $1.20
        for a guaranteed $1, and the same legs' NO side costs $2.10 for a
        guaranteed $2. The first draft of this fixture priced NO at 0.62, which
        is $1.86 for $2 -- a real NO-basket arbitrage that the scanner correctly
        found and the test wrongly called a failure.
        """
        ev = event("KXTEST", "E1", [market(t, 0.40, 0.70) for t in "ABC"])
        found, _, _skipped = evaluate_event(ev, profile(exhaustive=CONFIRMED, exclusive=CONFIRMED))
        assert found == []

    def test_inactive_legs_disqualify_the_event(self):
        """Reported as a DISPOSITION, not an evaluation note.

        The distinction is the fix for the first live sweep's failed
        reconciliation: an event that is never evaluated must land in exactly
        one skip bucket, while an evaluated event may raise several notes or
        none. Counting both in one place made 8,608 events account for 8,631.
        """
        ev = event("KXTEST", "E1", [
            market("A", 0.30, 0.72), market("B", 0.30, 0.72, status="closed"),
        ])
        found, notes, skipped = evaluate_event(
            ev, profile(exhaustive=CONFIRMED, exclusive=CONFIRMED)
        )
        assert found == []
        assert skipped == "event_fewer_than_two_active_markets"
        assert not notes


class TestPairDetection:
    def test_implication_pair_buys_yes_on_the_wider_and_no_on_the_narrower(self):
        """A: value > 85 (narrow) implies B: value > 70 (wide).

        The prices encode the actual mispricing, which is worth stating because
        the first draft of this test used plausible-looking numbers that were
        not an arbitrage at all. The condition ``yes_ask(B) + no_ask(A) < 1``
        rearranges to ``yes_ask(B) < yes_bid(A)``: the WIDER market -- the one
        that is by construction at least as likely -- has to be offered below
        the narrower one's bid. Here B asks 0.40 while A bids 0.45.
        """
        ev = event("KXTEST", "E1", [
            market("A", 0.50, 0.55, strike_type="greater", floor_strike=85),
            market("B", 0.40, 0.65, strike_type="greater", floor_strike=70),
        ])
        found, _, _skipped = evaluate_event(ev, profile(implication=CONFIRMED))
        assert [c.kind for c in found] == [KIND_IMPLICATION]
        legs = {leg["ticker"]: leg["side"] for leg in found[0].economics["legs"]}
        assert legs == {"B": "yes", "A": "no"}
        assert found[0].economics["cost_per_set"] == pytest.approx(0.95)

    def test_no_implication_candidate_when_the_series_is_refuted(self):
        ev = event("KXTEST", "E1", [
            market("A", 0.20, 0.10, strike_type="greater", floor_strike=85),
            market("B", 0.20, 0.10, strike_type="greater", floor_strike=70),
        ])
        found, _, _skipped = evaluate_event(ev, profile())
        assert found == []

    def test_disjoint_pair_buys_no_on_both(self):
        ev = event("KXTEST", "E1", [
            market("A", 0.30, 0.45, strike_type="between", floor_strike=78, cap_strike=79),
            market("B", 0.30, 0.45, strike_type="between", floor_strike=80, cap_strike=81),
        ])
        found, _, _skipped = evaluate_event(ev, profile(disjointness=CONFIRMED))
        assert [c.kind for c in found] == [KIND_DISJOINT_PAIR]
        assert all(leg["side"] == "no" for leg in found[0].economics["legs"])

    def test_identical_strikes_in_one_event_produce_nothing(self):
        """Two teams at the same handicap. The KXMLBSPREAD shape must not
        generate a relation even if its series were somehow qualified."""
        ev = event("KXTEST", "E1", [
            market("TB4", 0.10, 0.10, strike_type="greater", floor_strike=3.5),
            market("CWS4", 0.10, 0.10, strike_type="greater", floor_strike=3.5),
        ])
        found, _, _skipped = evaluate_event(
            ev, profile(implication=CONFIRMED, disjointness=CONFIRMED)
        )
        assert found == []


class TestReconfirmation:
    def _candidate(self):
        return Candidate(
            ts="2026-08-02T00:00:00+00:00", kind=KIND_YES_BASKET, series="KXTEST",
            event_ticker="E1", n_legs=2,
            economics={
                "payout_per_set": 1.0,
                "legs": [
                    {"ticker": "A", "side": "yes", "price": 0.40, "depth": 100.0},
                    {"ticker": "B", "side": "yes", "price": 0.40, "depth": 100.0},
                ],
            },
            evidence={},
        )

    def test_a_candidate_that_still_prices_is_confirmed(self, monkeypatch):
        books = {
            "A": BookTop("A", yes_bid=0.35, yes_bid_qty=50, no_bid=0.60, no_bid_qty=200),
            "B": BookTop("B", yes_bid=0.35, yes_bid_qty=50, no_bid=0.60, no_bid_qty=200),
        }
        out = reconfirm(self._candidate(), object(), book_cache=books)
        assert out.status == STATUS_CONFIRMED
        # yes_ask = 1 - no_bid = 0.40 on both legs, so still $0.80 for $1.
        assert out.recheck["cost_per_set"] == pytest.approx(0.80)
        assert out.recheck["max_contracts"] == 200

    def test_a_candidate_that_moved_against_us_is_marked_vanished_not_dropped(self):
        """Kept, because the survival RATE is the measurement -- it separates
        'real resting arbitrage' from 'our view of the book is noisy'."""
        books = {
            "A": BookTop("A", yes_bid=0.35, yes_bid_qty=50, no_bid=0.45, no_bid_qty=200),
            "B": BookTop("B", yes_bid=0.35, yes_bid_qty=50, no_bid=0.45, no_bid_qty=200),
        }
        out = reconfirm(self._candidate(), object(), book_cache=books)
        assert out.status == STATUS_VANISHED
        assert out.recheck["cost_per_set"] == pytest.approx(1.10)

    def test_a_leg_whose_book_emptied_vanishes(self):
        books = {
            "A": BookTop("A", yes_bid=0.35, yes_bid_qty=50, no_bid=None, no_bid_qty=0),
            "B": BookTop("B", yes_bid=0.35, yes_bid_qty=50, no_bid=0.60, no_bid_qty=200),
        }
        out = reconfirm(self._candidate(), object(), book_cache=books)
        assert out.status == STATUS_VANISHED
        assert "no yes offer" in out.recheck["reason"]

    def test_reconfirm_uses_the_live_book_not_the_snapshot_depth(self):
        books = {
            "A": BookTop("A", yes_bid=0.35, yes_bid_qty=50, no_bid=0.60, no_bid_qty=7),
            "B": BookTop("B", yes_bid=0.35, yes_bid_qty=50, no_bid=0.60, no_bid_qty=200),
        }
        out = reconfirm(self._candidate(), object(), book_cache=books)
        assert out.recheck["max_contracts"] == 7, "depth must come from the live book"


class TestSweepReconciliation:
    def test_totals_reconcile_and_skips_are_named(self):
        class Store:
            def __init__(self, mapping):
                self.mapping = mapping

            def has_fresh(self, series):
                return series in self.mapping

            def get(self, series, **kw):
                return self.mapping.get(series)

        events = [
            event("KXGOOD", "E1", [market(t, 0.30, 0.72) for t in "ABC"]),
            event("KXUNQUAL", "E2", [market(t, 0.30, 0.72) for t in "AB"]),
            event("KXGOOD", "E3", [market("A", 0.30, 0.72)]),  # single market
            {"event_ticker": "E4", "markets": []},              # no series
        ]
        store = Store({
            "KXGOOD": profile(series="KXGOOD", exhaustive=CONFIRMED, exclusive=CONFIRMED),
            "KXUNQUAL": profile(series="KXUNQUAL"),
        })
        found, report = sweep(
            session=object(), store=store, events=events, reconfirm_candidates=False
        )
        assert report.check() is None, report.check()
        assert report.events_seen == 4
        assert report.events_evaluated == 1
        assert report.event_skips["event_series_not_qualified"] == 1
        assert report.event_skips["event_fewer_than_two_markets"] == 1
        assert report.event_skips["event_no_series_ticker"] == 1
        assert report.candidates == {KIND_YES_BASKET: 1}
        assert len(found) == 1

    def test_evaluation_notes_do_not_break_the_reconciliation(self):
        """Regression for the first live sweep. An evaluated event that also
        raises a note must be counted ONCE, as evaluated."""
        class Store:
            def has_fresh(self, series):
                return True

            def get(self, series, **kw):
                return profile(series=series, exhaustive=CONFIRMED, exclusive=CONFIRMED)

        ev = event("KXGOOD", "E1", [
            market("A", 0.30, 0.72), market("B", 0.30, 0.72), market("C", 1.00, 0.99),
        ])
        _, report = sweep(session=object(), store=Store(), events=[ev],
                          reconfirm_candidates=False)
        assert report.events_evaluated == 1
        assert report.evaluation_notes["yes_basket_incomplete_book"] == 1
        assert report.event_skips == {}
        assert report.check() is None

    def test_profile_budget_defers_rather_than_drops(self):
        """With 2,503 multi-market series live, one sweep cannot qualify them
        all. Events whose series is not yet profiled must be COUNTED as
        deferred, not silently skipped -- otherwise coverage looks complete
        while most of the exchange is unexamined."""
        class BudgetStore:
            def __init__(self):
                self.built = []

            def has_fresh(self, series):
                return series in self.built

            def get(self, series, allow_fetch=True, **kw):
                if not allow_fetch:
                    return profile(series=series) if series in self.built else None
                self.built.append(series)
                return profile(series=series)

        store = BudgetStore()
        events = [
            event(f"KX{i}", f"E{i}", [market(t, 0.30, 0.72) for t in "AB"])
            for i in range(10)
        ]
        found, report = sweep(
            session=object(), store=store, events=events,
            reconfirm_candidates=False, max_new_profiles=3,
        )
        assert len(store.built) == 3
        assert report.profiles_built == 3
        assert report.event_skips["event_profile_not_yet_built"] == 7
        assert report.check() is None

    def test_profile_budget_is_spent_on_the_highest_volume_series_first(self):
        """The first live sweep spent its whole budget on KXNEXTNATOSECGEN,
        KXNEWPOPE and other 'who will be next' series with zero settled events,
        and evaluated nothing. Volume ordering is what fixes that."""
        class BudgetStore:
            def __init__(self):
                self.built = []

            def has_fresh(self, series):
                return series in self.built

            def get(self, series, allow_fetch=True, **kw):
                if not allow_fetch:
                    return profile(series=series) if series in self.built else None
                self.built.append(series)
                return profile(series=series)

        def vol_market(ticker, volume):
            return market(ticker, 0.30, 0.72, volume_24h_fp=f"{volume:.2f}")

        store = BudgetStore()
        events = [
            event("KXDEAD", "E1", [vol_market("A", 0), vol_market("B", 0)]),
            event("KXQUIET", "E2", [vol_market("A", 5), vol_market("B", 5)]),
            event("KXBUSY", "E3", [vol_market("A", 9000), vol_market("B", 9000)]),
        ]
        sweep(session=object(), store=store, events=events,
              reconfirm_candidates=False, max_new_profiles=1)
        assert store.built == ["KXBUSY"]

    def test_a_broken_reconciliation_is_detected(self):
        from canary.scan import SweepReport

        bad = SweepReport(started_at="x", events_seen=10, events_evaluated=3,
                          event_skips={"a": 2})
        assert bad.check() is not None
        assert bad.to_dict()["reconciles"] is False
