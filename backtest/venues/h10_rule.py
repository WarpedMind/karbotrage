"""H10 rule primitives (Session 39): Kalshi BTC/ETH perp funding carry. Pure functions,
pinned by tests/test_backtest_h10_rule.py. Offline only, never imported by the live path.

Frozen in DECISIONS.md Session 38; implementation choices in
backtest/reports/session39_declared_before_results.txt (items 9-17).

Sign (Kalshi help center, "How Funding Works"): a positive rate means long holders pay
short holders, so the SHORT perp leg receives rate x mark x contracts.
"""

from __future__ import annotations

PERP_FEE = 0.0012        # 12.0 bps per side, tier 0 taker
HEDGE_FEE = 0.0010       # 10 bps per side on the spot leg (frozen)
HEDGE_CARRY = 0.0025     # 0.25%/yr holding cost on the spot leg
LOOKBACK = 21            # funding periods = 7 days
MULT = {"KXBTCPERP": 1.31, "KXETHPERP": 1.41}
LEVERAGE = {"KXBTCPERP": 6.38, "KXETHPERP": 4.87}
MARGIN_CALL_SHARE = 0.80


def signal(rates_up_to_decision: list[float]) -> bool:
    """Hold the carry next week iff the mean of the last 21 rates (decision rate included) is > 0."""
    last = rates_up_to_decision[-LOOKBACK:]
    return len(last) == LOOKBACK and sum(last) / LOOKBACK > 0


def short_funding(rate: float, mark: float, contracts: float) -> float:
    """Cash to the short leg for one funding period (negative = the short pays)."""
    return rate * mark * contracts


def week_pnl(*, entering: bool, exiting: bool, p_bid0: float, p_ask0: float, p_bid1: float, p_ask1: float,
             s0: float, s1: float, perp_qty: float, spot_qty: float, funding: float,
             hedge_fee: float = HEDGE_FEE, days: float = 7.0) -> float:
    """Dollar P&L of one held week of short perp + long spot.

    Entering: sell the perp at the closing bid, pay perp + hedge fees. Continuing: start at mid, no cost.
    Exiting: buy the perp back at the closing ask, pay fees. Continuing into next week: end at mid.
    """
    p0 = p_bid0 if entering else (p_bid0 + p_ask0) / 2
    p1 = p_ask1 if exiting else (p_bid1 + p_ask1) / 2
    pnl = perp_qty * (p0 - p1) + spot_qty * (s1 - s0) + funding
    if entering:
        pnl -= PERP_FEE * perp_qty * p0 + hedge_fee * spot_qty * s0
    if exiting:
        pnl -= PERP_FEE * perp_qty * p1 + hedge_fee * spot_qty * s1
    pnl -= HEDGE_CARRY * days / 365 * spot_qty * s0
    return pnl


def capital(ticker: str, spot_notional: float) -> float:
    return MULT[ticker] * spot_notional


def tbill_week(dtb3_pct: float, days: float = 7.0) -> float:
    """DTB3 is quoted on a discount basis, actual/360."""
    return dtb3_pct / 100 * days / 360


def margin_buffer(ticker: str) -> float:
    """Posted perp margin as a fraction of notional: 2 / leverage estimate (declared item 11)."""
    return 2 / LEVERAGE[ticker]


def adverse_excursion(start_price: float, ask_highs: list[float]) -> float:
    """Cumulative worst rise against a short, as a fraction of the starting price."""
    return max((h - start_price) / start_price for h in ask_highs) if ask_highs else 0.0


def is_margin_call(ticker: str, excursion: float) -> bool:
    return excursion > MARGIN_CALL_SHARE * margin_buffer(ticker)
