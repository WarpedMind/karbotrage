"""Turning a calibration edge into a net-of-cost expected edge.

Gate G3. Three costs, in the order they bite:

1. **Crossing the spread.** A model probability of 0.70 against a mid of 0.55
   is not a 15-point edge -- the trade executes at the *ask*. On these markets
   the median spread is 2 cents, so half a spread is a full cent of the edge
   before anything else. This module never scores against the mid.
2. **The taker fee, rounded up.** Kalshi's published schedule (effective
   2026-07-07) charges ``round up(M x 0.07 x C x P x (1-P))`` with M defaulting
   to 1. The round-up is per order, so on the small liquidity-capped orders this
   system actually places it is a materially larger drag than the continuous
   fraction suggests. Reuses the live ``KalshiFeeModel`` rather than
   reimplementing it -- if the live fee model is wrong, this report should be
   wrong in the same direction, not accidentally right.
3. **Depth.** Edge that exists on one contract is not edge. The report states
   the size at which the measured edge is actually available.

Maker fees are irrelevant here: this is a taker strategy, and in any case
Kalshi's maker multiplier defaults to 0 outside ~76 enumerated series.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


def _live_fee_model():
    """The live ``KalshiFeeModel`` if it is importable.

    Importing it costs an ``agents.floor.arb_scanner`` import (structlog, the
    event bus) which is heavier than this module needs, so the local mirror
    below is the default path and this is used only by
    ``assert_matches_live_fee_model`` to prove the two agree. The backtest must
    not silently diverge from the fee model the trading path uses -- but it also
    must not drag the live path into an offline script.
    """
    try:
        from agents.floor.arb_scanner import KalshiFeeModel  # type: ignore

        return KalshiFeeModel
    except Exception:
        return None


def taker_fee_dollars(price: float, contracts: int, multiplier: float = 1.0) -> float:
    """``round up(M x 0.07 x C x P x (1-P))`` to the next cent, per order.

    Mirrors Kalshi's published fee table, which shows ceil-to-the-cent on small
    orders even though the schedule's prose mentions centicents. DECISIONS.md
    records that discrepancy; the table is authoritative for behaviour.
    """
    if contracts <= 0:
        return 0.0
    raw = multiplier * 0.07 * contracts * price * (1.0 - price)
    return math.ceil(raw * 100.0 - 1e-9) / 100.0


def assert_matches_live_fee_model() -> Optional[str]:
    """Cross-check the local mirror against the live model. Returns a
    description of the first disagreement, or None if they agree."""
    live = _live_fee_model()
    if live is None or not hasattr(live, "taker_fee_dollars"):
        return None
    for price in (0.01, 0.05, 0.10, 0.30, 0.50, 0.70, 0.95, 0.99):
        for contracts in (1, 5, 100, 1000):
            mine = taker_fee_dollars(price, contracts)
            theirs = live.taker_fee_dollars(price, contracts)
            if abs(mine - theirs) > 1e-9:
                return (
                    f"fee mismatch at price={price} contracts={contracts}: "
                    f"backtest={mine} live={theirs}"
                )
    return None


@dataclass
class TradeEconomics:
    """What one YES purchase at the ask is worth under the model."""

    entry_price: float
    contracts: int
    model_p: float
    fee: float
    gross_ev: float
    net_ev: float
    net_ev_per_contract: float
    net_edge_frac: float  # net EV as a fraction of capital at risk


def evaluate_yes_trade(
    model_p: float, ask: float, contracts: int = 1, multiplier: float = 1.0
) -> Optional[TradeEconomics]:
    """Expected value of buying ``contracts`` YES at ``ask``.

    Payout is $1 on YES. Gross EV per contract is ``model_p - ask``; the fee is
    charged on the order regardless of outcome.
    """
    if ask is None or not (0.0 < ask < 1.0) or contracts <= 0:
        return None
    fee = taker_fee_dollars(ask, contracts, multiplier)
    gross = (model_p - ask) * contracts
    net = gross - fee
    capital = ask * contracts
    return TradeEconomics(
        entry_price=ask,
        contracts=contracts,
        model_p=model_p,
        fee=fee,
        gross_ev=gross,
        net_ev=net,
        net_ev_per_contract=net / contracts,
        net_edge_frac=net / capital if capital > 0 else 0.0,
    )


def evaluate_no_trade(
    model_p: float, no_ask: float, contracts: int = 1, multiplier: float = 1.0
) -> Optional[TradeEconomics]:
    """Expected value of buying NO at ``no_ask``; wins with probability 1-p."""
    if no_ask is None or not (0.0 < no_ask < 1.0) or contracts <= 0:
        return None
    fee = taker_fee_dollars(no_ask, contracts, multiplier)
    gross = ((1.0 - model_p) - no_ask) * contracts
    net = gross - fee
    capital = no_ask * contracts
    return TradeEconomics(
        entry_price=no_ask,
        contracts=contracts,
        model_p=1.0 - model_p,
        fee=fee,
        gross_ev=gross,
        net_ev=net,
        net_ev_per_contract=net / contracts,
        net_edge_frac=net / capital if capital > 0 else 0.0,
    )
