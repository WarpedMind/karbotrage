"""Rule tests for H10 (Session 39): Kalshi perp funding carry. Offline only.

Pins the frozen parameters (DECISIONS.md Session 38) and the sign convention confirmed from
Kalshi's help center: a positive funding rate means longs pay shorts.
"""

import pytest

from backtest.venues import h10_rule as R


def test_frozen_parameters():
    assert R.PERP_FEE == 0.0012 and R.HEDGE_FEE == 0.0010 and R.HEDGE_CARRY == 0.0025
    assert R.MULT == {"KXBTCPERP": 1.31, "KXETHPERP": 1.41}
    assert R.capital("KXBTCPERP", 100.0) == pytest.approx(131.0)


def test_signal_needs_21_rates_and_a_positive_mean():
    assert R.signal([0.0001] * 21)
    assert not R.signal([0.0001] * 20)               # not enough history
    assert not R.signal([0.0] * 21)                  # mean must be strictly > 0
    assert not R.signal([0.0003] * 10 + [-0.0004] * 11)
    assert R.signal([-1.0] * 5 + [0.0001] * 21)      # only the last 21 count


def test_positive_rate_pays_the_short():
    assert R.short_funding(0.0001, 8.0, 100) == pytest.approx(0.08)
    assert R.short_funding(-0.0001, 8.0, 100) == pytest.approx(-0.08)


def test_continuing_week_is_marked_at_mid_with_no_trading_cost():
    pnl = R.week_pnl(entering=False, exiting=False, p_bid0=99, p_ask0=101, p_bid1=99, p_ask1=101,
                     s0=100, s1=100, perp_qty=0.01, spot_qty=0.01, funding=0.0)
    assert pnl == pytest.approx(-R.HEDGE_CARRY * 7 / 365 * 1.0)


def test_round_trip_pays_spread_and_both_fees():
    pnl = R.week_pnl(entering=True, exiting=True, p_bid0=99, p_ask0=101, p_bid1=99, p_ask1=101,
                     s0=100, s1=100, perp_qty=1 / 99, spot_qty=0.01, funding=0.0)
    spread = (99 - 101) / 99
    fees = R.PERP_FEE * (1 + 101 / 99) + 2 * R.HEDGE_FEE
    assert pnl == pytest.approx(spread - fees - R.HEDGE_CARRY * 7 / 365)


def test_hedged_price_move_cancels():
    # perp and spot both rise 10%: short loses, long gains, net ~0 before costs
    pnl = R.week_pnl(entering=False, exiting=False, p_bid0=100, p_ask0=100, p_bid1=110, p_ask1=110,
                     s0=100, s1=110, perp_qty=0.01, spot_qty=0.01, funding=0.0)
    assert pnl == pytest.approx(-R.HEDGE_CARRY * 7 / 365)


def test_tbill_discount_basis():
    assert R.tbill_week(3.6) == pytest.approx(0.036 * 7 / 360)


def test_margin_call_proxy():
    buf = R.margin_buffer("KXBTCPERP")
    assert buf == pytest.approx(2 / 6.38)
    assert R.adverse_excursion(100, [101, 125, 103]) == pytest.approx(0.25)
    assert R.is_margin_call("KXBTCPERP", 0.81 * buf)
    assert not R.is_margin_call("KXBTCPERP", 0.79 * buf)
