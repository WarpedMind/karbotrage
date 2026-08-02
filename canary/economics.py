"""Basket economics: what a multi-leg arbitrage actually nets after fees and depth.

S5a and S5b are the same trade at different arities, so they share one evaluator.
A basket is N legs bought simultaneously, each at the **ask**, with a payout that
is guaranteed to be at least ``payout_per_set`` dollars per contract-set no
matter how the event resolves. The three things that kill a thin basket, in the
order they bite:

1. **The ask, not the mid and not the bid.** Every price here is what this
   system would pay to buy. Session 26's root cause was computing an arb from
   bid prices -- a live book quoted at yes_bid 0.23 / no_bid 0.30 scored as
   "+47% profit" when the real executable cost was $1.47 for a $1 payout.

2. **The fee is ceil'd per order and there are N orders.** Kalshi charges
   ``round up(M x 0.07 x C x P x (1-P))`` to the cent, per order, M defaulting
   to 1 for takers. At one contract that is a **1 cent floor per leg** on any
   price between roughly 0.0015 and 0.9985 -- so a 10-leg basket pays at least
   10 cents per contract-set before it has earned anything. That floor
   amortises with size: 100 contracts of a 4-cent leg pay $0.27 total, i.e.
   $0.0027 each, not $0.01 each. Basket edge is therefore **size-dependent**,
   and quoting it at one contract understates it while quoting it at unlimited
   size overstates it. Both numbers are reported.

   A convenience worth knowing rather than rediscovering: ``P x (1-P)`` is
   symmetric, so a leg's fee is identical whether it is priced as YES at ``p``
   or NO at ``1-p``. The side does not need to be threaded into the fee call.

3. **Depth.** Edge on one contract is not edge. The size is the floor of the
   smallest per-leg depth, because a basket only exists if every leg fills.

The fee function is imported from ``backtest.costs`` rather than reimplemented,
and ``assert_fee_model_agrees`` cross-checks that against the live
``KalshiFeeModel`` the trading path uses. If the live model is wrong, this
should be wrong in the same direction rather than accidentally right.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence

from backtest.costs import assert_matches_live_fee_model, taker_fee_dollars


@dataclass(frozen=True)
class Leg:
    """One purchase in a basket.

    ``depth`` is the quantity resting at ``price`` on the side being bought.
    For a YES leg that is ``yes_ask_size_fp``; for a NO leg it is
    ``yes_bid_size_fp`` -- the confusingly named field, because a NO ask is a
    resting YES bid. See ``kalshi_rest`` for the live confirmation.
    """

    ticker: str
    side: str  # "yes" | "no"
    price: float
    depth: float

    def __post_init__(self) -> None:
        if self.side not in ("yes", "no"):
            raise ValueError(f"leg side must be yes/no, got {self.side!r}")


@dataclass
class BasketEconomics:
    payout_per_set: float
    cost_per_set: float
    max_contracts: int
    fee_at_one: float
    net_per_set_at_one: float
    fee_at_max: float
    net_total_at_max: float
    net_per_set_at_max: float
    legs: List[dict] = field(default_factory=list)

    @property
    def is_candidate(self) -> bool:
        """Profitable at a size that is actually available.

        Deliberately *not* "profitable at one contract": a basket whose edge
        exists only below the per-order fee floor is not tradeable, and one
        that is unprofitable at one contract but profitable at 500 is.
        """
        return self.max_contracts >= 1 and self.net_total_at_max > 0.0

    def to_dict(self) -> dict:
        out = asdict(self)
        out["is_candidate"] = self.is_candidate
        return out


def basket_fee(legs: Sequence[Leg], contracts: int) -> float:
    """Total fee for buying ``contracts`` of every leg -- one ceil'd order each."""
    return sum(taker_fee_dollars(leg.price, contracts) for leg in legs)


def max_contracts(legs: Sequence[Leg]) -> int:
    """Largest whole basket that every leg can fill at its quoted price.

    Kalshi trades whole contracts, so this floors. Session 28 found the live
    path had been approving fractional sizes (a 0.05-contract trade) that could
    not exist on the exchange; nothing here should be able to reproduce that.
    """
    if not legs:
        return 0
    return max(0, int(math.floor(min(leg.depth for leg in legs))))


def evaluate_basket(
    legs: Sequence[Leg], payout_per_set: float
) -> Optional[BasketEconomics]:
    """Net economics of a basket guaranteed to pay at least ``payout_per_set``.

    Returns ``None`` for a structurally invalid basket (no legs, or a leg with a
    price outside (0,1) -- which on Kalshi means one side of the book is empty,
    not that the contract is free).
    """
    if not legs or payout_per_set <= 0:
        return None
    for leg in legs:
        if not (0.0 < leg.price < 1.0):
            return None

    cost_per_set = sum(leg.price for leg in legs)
    size = max_contracts(legs)

    fee_one = basket_fee(legs, 1)
    net_one = payout_per_set - cost_per_set - fee_one

    if size >= 1:
        fee_max = basket_fee(legs, size)
        net_total_max = size * (payout_per_set - cost_per_set) - fee_max
        net_per_set_max = net_total_max / size
    else:
        fee_max = 0.0
        net_total_max = 0.0
        net_per_set_max = 0.0

    return BasketEconomics(
        payout_per_set=round(payout_per_set, 6),
        cost_per_set=round(cost_per_set, 6),
        max_contracts=size,
        fee_at_one=round(fee_one, 6),
        net_per_set_at_one=round(net_one, 6),
        fee_at_max=round(fee_max, 6),
        net_total_at_max=round(net_total_max, 6),
        net_per_set_at_max=round(net_per_set_max, 6),
        legs=[asdict(leg) for leg in legs],
    )


def assert_fee_model_agrees() -> Optional[str]:
    """Cross-check the canary's fee arithmetic against the live trading path.

    Returns a description of the first disagreement, or ``None`` if they agree
    (or if the live model could not be imported, which is the normal case when
    running this package standalone). Called once at process start and logged,
    so a divergence surfaces as a fact rather than as a silently different
    profitability threshold.
    """
    return assert_matches_live_fee_model()
