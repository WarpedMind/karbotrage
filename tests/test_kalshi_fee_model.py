"""
tests/test_kalshi_fee_model.py

Covers the fee model fix (2026-07-13): KalshiFeeModel previously used a
flat 14%-of-trade-value approximation. Kalshi's real, published taker fee
is 0.07 * price * (1 - price) per contract — confirmed against Kalshi's
official fee schedule (fetched 2026-07-13). The flat model overstated
real fees by roughly 4-8x for a typical near-the-money contract, likely
causing the system to reject genuinely profitable small edges. See
DECISIONS.md Session 26 for the full investigation.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.floor.arb_scanner import KalshiFeeModel


class TestTakerFeeFraction:
    def test_fee_peaks_at_50_cents(self):
        fee_50 = KalshiFeeModel.taker_fee_fraction(0.50)
        fee_10 = KalshiFeeModel.taker_fee_fraction(0.10)
        fee_90 = KalshiFeeModel.taker_fee_fraction(0.90)
        assert fee_50 > fee_10
        assert fee_50 > fee_90

    def test_50_cent_fee_matches_published_rate(self):
        # 0.07 * 0.5 * 0.5 = 0.0175 -> 1.75%, Kalshi's published peak rate.
        assert round(KalshiFeeModel.taker_fee_fraction(0.50), 4) == 0.0175

    def test_fee_symmetric_around_50_cents(self):
        fee_20 = round(KalshiFeeModel.taker_fee_fraction(0.20), 10)
        fee_80 = round(KalshiFeeModel.taker_fee_fraction(0.80), 10)
        assert fee_20 == fee_80

    def test_fee_near_zero_at_extremes(self):
        assert KalshiFeeModel.taker_fee_fraction(0.01) < 0.001
        assert KalshiFeeModel.taker_fee_fraction(0.99) < 0.001

    def test_fee_is_zero_outside_valid_price_range(self):
        assert KalshiFeeModel.taker_fee_fraction(0.0) == 0.0
        assert KalshiFeeModel.taker_fee_fraction(1.0) == 0.0

    def test_old_flat_14_percent_no_longer_applies(self):
        # The old model returned a flat 0.14 (14%) fraction regardless of
        # price. The real model must differ substantially from that at any
        # normal contract price — this is the regression this fix closes.
        for price in (0.10, 0.30, 0.50, 0.70, 0.90):
            total = KalshiFeeModel.estimate_fee_pct(price, price)
            assert total < 0.07, f"fee at price={price} too close to old flat model: {total}"


class TestEstimateFeePctUsesRealPerLegPrices:
    def test_total_is_sum_of_both_legs(self):
        total = KalshiFeeModel.estimate_fee_pct(0.30, 0.60)
        expected = KalshiFeeModel.taker_fee_fraction(0.30) + KalshiFeeModel.taker_fee_fraction(0.60)
        assert total == expected

    def test_asymmetric_legs_are_not_double_counted_evenly(self):
        # A near-extreme-price leg should contribute much less fee than a
        # near-50c leg, not an even split of some flat total.
        total = KalshiFeeModel.estimate_fee_pct(0.05, 0.50)
        cheap_leg_fee = KalshiFeeModel.taker_fee_fraction(0.05)
        expensive_leg_fee = KalshiFeeModel.taker_fee_fraction(0.50)
        assert cheap_leg_fee < expensive_leg_fee
        assert total == cheap_leg_fee + expensive_leg_fee
