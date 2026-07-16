"""
tests/test_arb_scanner_s1_canary_mode.py

Covers Session 28's finding: S1 is structurally impossible on a real
Kalshi order book (a resting yes_ask+no_ask<1 is algebraically a crossed
book, which the matching engine mints into contract pairs instantly —
it cannot rest). Verified live: 0/778 real markets show a crossed book,
and all 5 of Session 27's paper trades correlate exactly with a
sequence_gap_detected event on that market. s1_canary_mode (default
True) keeps detection/logging running as a data-quality signal but
stops _check_s1_rebalancing from ever returning a real OpportunityEvent
that RiskGate/PaperExecutor could act on. See DECISIONS.md Session 28.
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


def _price_event(market_id, yes_ask, no_ask, size=500.0) -> PriceUpdateEvent:
    return PriceUpdateEvent(
        source="test", platform="kalshi", market_id=market_id,
        yes_ask=yes_ask, no_ask=no_ask,
        yes_ask_depth=[(yes_ask, size)], no_ask_depth=[(no_ask, size)],
    )


class TestS1CanaryMode:
    def test_canary_mode_default_is_true(self):
        assert KarbotConfig().strategies.s1_canary_mode is True

    def test_canary_mode_never_returns_a_tradeable_opportunity(self):
        config = KarbotConfig()
        assert config.strategies.s1_canary_mode is True
        scanner = ArbScannerAgent(config=config, event_bus=MagicMock())
        # A clearly "profitable"-looking candidate that would have traded
        # before this fix.
        event = _price_event("KXTEST-CANARY", yes_ask=0.45, no_ask=0.45)
        opp = scanner._check_s1_rebalancing(event)
        assert opp is None

    def test_canary_mode_disabled_restores_old_behavior(self):
        """Escape hatch for once the underlying reconstruction bug is
        actually fixed and re-verified — must still be possible to get a
        real OpportunityEvent back."""
        config = KarbotConfig()
        config.strategies.s1_canary_mode = False
        scanner = ArbScannerAgent(config=config, event_bus=MagicMock())
        event = _price_event("KXTEST-LIVE", yes_ask=0.45, no_ask=0.45)
        opp = scanner._check_s1_rebalancing(event)
        assert opp is not None
        assert opp.strategy == "S1_REBALANCING"
