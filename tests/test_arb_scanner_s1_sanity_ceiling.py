"""
tests/test_arb_scanner_s1_sanity_ceiling.py

Covers two related S1 fixes made together on 2026-07-13:

1. The bid/ask sign fix (DECISIONS.md, "S1 arb formula uses BID prices for
   both legs of a BUY trade"): _check_s1_rebalancing now reads
   event.yes_ask/event.no_ask (real executable buy prices) instead of
   event.yes_bid/event.no_bid (prices other participants are willing to
   pay, not prices this system can buy at).
2. The sanity ceiling (s1_max_net_profit_pct): a real S1 arb on a liquid
   market doesn't exceed low single digits. Anything above the ceiling is
   rejected and logged loudly rather than traded on blindly.
3. Liquidity capping: max_fillable_qty is capped to the size actually
   resting at the quoted ask price on each leg, not assumed unlimited —
   live-confirmed 2026-07-13 that a mathematically valid edge can be
   backed by as little as 1 contract.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from unittest.mock import MagicMock

from agents.floor.arb_scanner import ArbScannerAgent
from core.events import PriceUpdateEvent
from karbot.core.config import KarbotConfig


def _make_scanner() -> ArbScannerAgent:
    config = KarbotConfig()
    # Session 28: S1 is off by default (canary mode) — these tests cover
    # the pricing/ceiling/liquidity mechanics themselves, which is a
    # separate concern from canary mode (see test_arb_scanner_s1_canary_mode.py).
    config.strategies.s1_canary_mode = False
    return ArbScannerAgent(config=config, event_bus=MagicMock())


def _price_event(
    market_id: str,
    yes_ask: float,
    no_ask: float,
    yes_ask_size: float = 500.0,
    no_ask_size: float = 500.0,
) -> PriceUpdateEvent:
    return PriceUpdateEvent(
        source="test",
        platform="kalshi",
        market_id=market_id,
        yes_ask=yes_ask,
        no_ask=no_ask,
        yes_ask_depth=[(yes_ask, yes_ask_size)],
        no_ask_depth=[(no_ask, no_ask_size)],
    )


class TestS1AskBasedPricing:
    def test_realistic_small_edge_is_approved(self):
        scanner = _make_scanner()
        # combined_cost = 0.82 -> gross 18%; the model's flat ~14% round-trip
        # fee (KalshiFeeModel) eats most of that, leaving a realistic ~3.7%
        # net edge — comfortably under the ceiling, above the min-profit floor.
        event = _price_event("KXTEST-REAL", yes_ask=0.42, no_ask=0.40)
        opp = scanner._check_s1_rebalancing(event)
        assert opp is not None
        assert opp.net_profit_pct <= scanner._cfg_s.s1_max_net_profit_pct
        # Legs must quote the real executable ask prices, not bids.
        assert opp.legs[0]["price"] == 0.42
        assert opp.legs[1]["price"] == 0.40

    def test_bid_prices_alone_no_longer_produce_a_false_opportunity(self):
        """The exact regression this fix closes: yes_bid=0.23/no_bid=0.30
        was reported live as a +47% "opportunity" by the old bid-based
        formula. The real executable cost (yes_ask=1-no_bid=0.70,
        no_ask=1-yes_bid=0.77) is a 47% guaranteed LOSS, so with ask prices
        set correctly, no opportunity should be produced."""
        scanner = _make_scanner()
        event = _price_event("KXTEST-REGRESSION", yes_ask=0.70, no_ask=0.77)
        opp = scanner._check_s1_rebalancing(event)
        assert opp is None


class TestS1SanityCeiling:
    def test_implausible_spread_is_rejected(self):
        scanner = _make_scanner()
        # combined_cost = 0.40 -> gross 60%, the exact magnitude observed
        # live from a stale/thin book on 2026-07-13.
        event = _price_event("KXTEST-STALE", yes_ask=0.10, no_ask=0.30)
        opp = scanner._check_s1_rebalancing(event)
        assert opp is None

    def test_ceiling_is_configurable(self):
        config = KarbotConfig()
        config.strategies.s1_max_net_profit_pct = 5.0
        scanner = ArbScannerAgent(config=config, event_bus=MagicMock())
        # gross ~10%, would clear a looser ceiling but not this tighter one
        event = _price_event("KXTEST-CONFIG", yes_ask=0.45, no_ask=0.45)
        opp = scanner._check_s1_rebalancing(event)
        assert opp is None


class TestS1LiquidityCap:
    def test_max_fillable_qty_is_the_smaller_of_the_two_legs(self):
        scanner = _make_scanner()
        event = _price_event(
            "KXTEST-THIN", yes_ask=0.42, no_ask=0.40,
            yes_ask_size=500.0, no_ask_size=1.0,
        )
        opp = scanner._check_s1_rebalancing(event)
        assert opp is not None
        assert opp.max_fillable_qty == 1.0

    def test_zero_depth_produces_no_opportunity(self):
        scanner = _make_scanner()
        event = _price_event(
            "KXTEST-EMPTY", yes_ask=0.42, no_ask=0.40,
            yes_ask_size=0.0, no_ask_size=500.0,
        )
        opp = scanner._check_s1_rebalancing(event)
        assert opp is None

    def test_missing_depth_data_produces_no_opportunity(self):
        """A PriceUpdateEvent with no depth info at all (e.g. old code path
        or malformed data) must not be treated as unlimited liquidity."""
        scanner = _make_scanner()
        event = PriceUpdateEvent(
            source="test", platform="kalshi", market_id="KXTEST-NODEPTH",
            yes_ask=0.42, no_ask=0.40,
        )
        opp = scanner._check_s1_rebalancing(event)
        assert opp is None
