"""
tests/test_arb_scanner_s1_candidate_visibility.py

Covers the near-miss visibility logging added 2026-07-13 in response to
the operator asking how long to wait before judging whether S1 is a
viable strategy. Before this, a candidate with gross_pct>0 but net_pct
below the trading threshold was silently discarded — zero signal on how
close real markets are getting. s1_candidate_seen now logs every such
candidate (gross_pct<=0 stays silent — that's the overwhelmingly common
case in an efficient market, see DECISIONS.md Session 26, and logging it
would be noisy without being informative).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.floor.arb_scanner import ArbScannerAgent
from core.events import PriceUpdateEvent
from karbot.core.config import KarbotConfig


def _make_scanner() -> ArbScannerAgent:
    config = KarbotConfig()
    return ArbScannerAgent(config=config, event_bus=MagicMock())


def _price_event(market_id, yes_ask, no_ask, size=500.0) -> PriceUpdateEvent:
    return PriceUpdateEvent(
        source="test", platform="kalshi", market_id=market_id,
        yes_ask=yes_ask, no_ask=no_ask,
        yes_ask_depth=[(yes_ask, size)], no_ask_depth=[(no_ask, size)],
    )


class TestS1CandidateVisibility:
    def test_near_miss_below_threshold_is_logged(self):
        scanner = _make_scanner()
        # combined_cost = 0.98 -> gross 2%, positive but almost certainly
        # below the Kelly-relevant min-profit floor after fees+slippage.
        event = _price_event("KXTEST-NEARMISS", yes_ask=0.49, no_ask=0.49)
        with patch("agents.floor.arb_scanner.log") as mock_log:
            opp = scanner._check_s1_rebalancing(event)
        assert opp is None
        mock_log.info.assert_called_once()
        call_kwargs = mock_log.info.call_args.kwargs
        assert mock_log.info.call_args.args[0] == "s1_candidate_seen"
        assert call_kwargs["market"] == "KXTEST-NEARMISS"
        assert call_kwargs["cleared_min_profit"] is False

    def test_passing_candidate_is_also_logged(self):
        scanner = _make_scanner()
        event = _price_event("KXTEST-PASS", yes_ask=0.45, no_ask=0.45)
        with patch("agents.floor.arb_scanner.log") as mock_log:
            opp = scanner._check_s1_rebalancing(event)
        assert opp is not None
        mock_log.info.assert_called_once()
        assert mock_log.info.call_args.kwargs["cleared_min_profit"] is True

    def test_negative_gross_is_not_logged(self):
        """The dominant, structurally-expected case in an efficient market
        (yes_ask+no_ask >= 1) must stay silent — logging it would be noise,
        not signal."""
        scanner = _make_scanner()
        event = _price_event("KXTEST-NOEDGE", yes_ask=0.55, no_ask=0.55)
        with patch("agents.floor.arb_scanner.log") as mock_log:
            opp = scanner._check_s1_rebalancing(event)
        assert opp is None
        mock_log.info.assert_not_called()
