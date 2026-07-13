"""
tests/test_arb_scanner_s1_sanity_ceiling.py

Covers the S1 sanity ceiling added 2026-07-13 (defense-in-depth alongside
the price_watcher.py stale-publish fix — see test_price_watcher_gap_publish.py).
Live confirmed: opportunity_approved events showing net_pct of 20.7%-61.7%
against a realistic 1-5% S1 benchmark, traced to stale order-book data.
s1_max_net_profit_pct rejects (and loudly logs) anything that implausible
rather than silently trading on it, while leaving normal small edges
untouched.
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
    return ArbScannerAgent(config=config, event_bus=MagicMock())


def _price_event(market_id: str, yes_bid: float, no_bid: float) -> PriceUpdateEvent:
    return PriceUpdateEvent(
        source="test",
        platform="kalshi",
        market_id=market_id,
        yes_bid=yes_bid,
        yes_ask=yes_bid,
        no_bid=no_bid,
        no_ask=no_bid,
    )


class TestS1SanityCeiling:
    def test_realistic_small_edge_is_approved(self):
        scanner = _make_scanner()
        # combined_cost = 0.82 -> gross 18%; the model's flat ~14% round-trip
        # fee (KalshiFeeModel) eats most of that, leaving a realistic ~3.7%
        # net edge — comfortably under the ceiling, above the min-profit floor.
        event = _price_event("KXTEST-REAL", yes_bid=0.42, no_bid=0.40)
        opp = scanner._check_s1_rebalancing(event)
        assert opp is not None
        assert opp.net_profit_pct <= scanner._cfg_s.s1_max_net_profit_pct

    def test_implausible_stale_book_spread_is_rejected(self):
        scanner = _make_scanner()
        # combined_cost = 0.40 -> gross 60%, the exact magnitude observed
        # live from a stale/corrupt book on 2026-07-13.
        event = _price_event("KXTEST-STALE", yes_bid=0.10, no_bid=0.30)
        opp = scanner._check_s1_rebalancing(event)
        assert opp is None

    def test_ceiling_is_configurable(self):
        config = KarbotConfig()
        config.strategies.s1_max_net_profit_pct = 5.0
        scanner = ArbScannerAgent(config=config, event_bus=MagicMock())
        # gross ~10%, would clear a looser ceiling but not this tighter one
        event = _price_event("KXTEST-CONFIG", yes_bid=0.45, no_bid=0.45)
        opp = scanner._check_s1_rebalancing(event)
        assert opp is None
