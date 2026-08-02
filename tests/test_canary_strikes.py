"""
tests/test_canary_strikes.py

Kalshi strike conventions and the logical relations S5b derives from them.

The conventions here are not symmetric and the asymmetry has already cost this
project once: Session 31 read ``floor_strike`` on a ``less`` market, got
``None``, skipped it silently, and removed the entire low tail of every ladder
from a validation that still printed 100%. Every convention below is pinned to a
real market observed live on 2026-08-02.

The disjointness tests matter more than they look. Disjointness is what licenses
buying NO on two legs for a guaranteed dollar; getting it wrong on integer-valued
underlyings (temperature ladders leave real-valued gaps between buckets) or on
inferred intervals would manufacture arbitrage that does not exist.
"""

import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from canary.strikes import Interval, disjoint, implies, interval_for, settles_yes


# Real markets, KXHIGHLAX-26AUG02, pulled live 2026-08-02.
LAX_BELOW_78 = {"ticker": "KXHIGHLAX-26AUG02-T78", "strike_type": "less",
                "floor_strike": None, "cap_strike": 78}
LAX_78_79 = {"ticker": "KXHIGHLAX-26AUG02-B78.5", "strike_type": "between",
             "floor_strike": 78, "cap_strike": 79}
LAX_80_81 = {"ticker": "KXHIGHLAX-26AUG02-B80.5", "strike_type": "between",
             "floor_strike": 80, "cap_strike": 81}
LAX_ABOVE_85 = {"ticker": "KXHIGHLAX-26AUG02-T85", "strike_type": "greater",
                "floor_strike": 85, "cap_strike": None}
# Real market, KXMLBRFI, greater_or_equal with floor 1 -> "either team scores".
RFI = {"ticker": "KXMLBRFI-x", "strike_type": "greater_or_equal",
       "floor_strike": 1, "cap_strike": None}
# Real market shapes for the heterogeneous 'structured' bucket.
STRUCTURED_THRESHOLD = {"ticker": "KXMLBRBI-x-2", "strike_type": "structured",
                        "floor_strike": 1.5, "cap_strike": None}
STRUCTURED_CATEGORICAL = {"ticker": "KXMLBGAME-x-LAD", "strike_type": "structured",
                          "floor_strike": None, "cap_strike": None}


class TestConventions:
    def test_less_reads_cap_strike_not_floor_strike(self):
        """The Session 31 bug, pinned. 'less' has a cap and a null floor."""
        iv = interval_for(LAX_BELOW_78)
        assert iv is not None, "a 'less' market must yield an interval"
        assert iv.lo == -math.inf
        assert iv.hi == 78 and not iv.hi_closed
        # "77 or below": 77 yes, 78 no.
        assert settles_yes(iv, 77)
        assert not settles_yes(iv, 78)

    def test_greater_is_strict(self):
        iv = interval_for(LAX_ABOVE_85)
        assert settles_yes(iv, 86)
        assert not settles_yes(iv, 85), "'over 85' must exclude 85 itself"

    def test_greater_or_equal_includes_the_endpoint(self):
        """The one-degree difference between greater and greater_or_equal is a
        whole outcome on an integer-valued underlying."""
        iv = interval_for(RFI)
        assert settles_yes(iv, 1)
        assert not settles_yes(iv, 0)

    def test_between_is_inclusive_both_ends(self):
        iv = interval_for(LAX_78_79)
        assert settles_yes(iv, 78) and settles_yes(iv, 79)
        assert not settles_yes(iv, 77) and not settles_yes(iv, 80)

    def test_structured_with_floor_is_a_threshold_but_marked_inferred(self):
        iv = interval_for(STRUCTURED_THRESHOLD)
        assert iv is not None and iv.inferred is True
        assert settles_yes(iv, 2) and not settles_yes(iv, 1)

    def test_structured_without_a_strike_is_categorical(self):
        assert interval_for(STRUCTURED_CATEGORICAL) is None

    def test_custom_and_missing_types_yield_no_interval(self):
        assert interval_for({"ticker": "x", "strike_type": "custom"}) is None
        assert interval_for({"ticker": "x"}) is None

    def test_unknown_strike_type_raises_rather_than_guessing(self):
        """A new convention appearing on the exchange must stop the scan, not be
        absorbed into whichever branch looks closest."""
        with pytest.raises(ValueError):
            interval_for({"ticker": "x", "strike_type": "brand_new_thing"})


class TestImplication:
    def test_narrower_greater_implies_wider_greater(self):
        hi = Interval(85, math.inf, False, False)
        lo = Interval(70, math.inf, False, False)
        assert implies(hi, lo)
        assert not implies(lo, hi)

    def test_between_implies_containing_greater(self):
        assert implies(interval_for(LAX_80_81), Interval(70, math.inf, False, False))

    def test_between_implies_containing_less(self):
        assert implies(interval_for(LAX_78_79), Interval(-math.inf, 90, False, False))

    def test_identical_intervals_do_not_imply_each_other(self):
        """Two markets with the same interval in one event are the signature of
        two different metrics sharing an event (two teams at the same handicap),
        not of a real relation. KXMLBSPREAD does exactly this live."""
        tb = {"ticker": "..-TB4", "strike_type": "greater", "floor_strike": 3.5}
        cws = {"ticker": "..-CWS4", "strike_type": "greater", "floor_strike": 3.5}
        assert not implies(interval_for(tb), interval_for(cws))

    def test_endpoint_strictness_decides_containment(self):
        """(85, inf) is contained in [85, inf) but not the reverse."""
        strict = Interval(85, math.inf, False, False)
        closed = Interval(85, math.inf, True, False)
        assert implies(strict, closed)
        assert not implies(closed, strict)

    def test_overlapping_but_uncontained_intervals_do_not_imply(self):
        assert not implies(Interval(70, 80, True, True), Interval(75, 90, True, True))


class TestDisjointness:
    def test_adjacent_temperature_buckets_are_disjoint(self):
        """[78,79] and [80,81] share no value even though the reals between them
        belong to neither -- disjointness is about intersection, not coverage."""
        assert disjoint(interval_for(LAX_78_79), interval_for(LAX_80_81))

    def test_below_and_above_the_ladder_are_disjoint(self):
        assert disjoint(interval_for(LAX_BELOW_78), interval_for(LAX_ABOVE_85))

    def test_touching_closed_endpoints_are_not_disjoint(self):
        assert not disjoint(Interval(70, 80, True, True), Interval(80, 90, True, True))

    def test_touching_with_one_open_endpoint_is_disjoint(self):
        assert disjoint(Interval(70, 80, True, False), Interval(80, 90, True, True))

    def test_two_upper_rays_are_never_disjoint(self):
        assert not disjoint(
            Interval(3.5, math.inf, False, False), Interval(1.5, math.inf, False, False)
        )

    def test_inferred_intervals_are_barred_from_disjointness(self):
        """A 'structured' interval is an inference. A wrong inference cannot
        create a false implication between two upper rays, but it could create a
        false disjointness against a bounded interval -- so it is refused."""
        inferred = Interval(90, math.inf, False, False, inferred=True)
        bounded = Interval(70, 80, True, True)
        assert not disjoint(inferred, bounded)
        assert not disjoint(bounded, inferred)
        # ... while the same shapes without the inferred flag are disjoint.
        assert disjoint(Interval(90, math.inf, False, False), bounded)

    def test_inferred_intervals_may_still_imply(self):
        a = Interval(3.5, math.inf, False, False, inferred=True)
        b = Interval(1.5, math.inf, False, False, inferred=True)
        assert implies(a, b)
