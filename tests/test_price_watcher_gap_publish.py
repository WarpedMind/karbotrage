"""
tests/test_price_watcher_gap_publish.py

Covers a live-confirmed bug (2026-07-13): _handle_kalshi_delta ignored
apply_delta's return value and published a PriceUpdateEvent built from the
book's stale, pre-gap prices on the very delta that first detected a
sequence gap. ArbScanner then priced S1 "opportunities" off that stale
data, producing net_pct values of 20-60% (live) against a realistic 1-5%
benchmark. Fixed by checking apply_delta's return value and skipping the
publish (requesting a fresh snapshot instead) when a gap is detected.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.floor.price_watcher import PriceWatcherAgent, OrderBook


def _make_agent() -> PriceWatcherAgent:
    config = MagicMock()
    secrets = MagicMock()
    secrets.kalshi_api_key_id = "test-key-id"
    secrets.kalshi_private_key_path = "/fake/path.pem"
    bus = MagicMock()
    bus.publish = AsyncMock()
    agent = PriceWatcherAgent(config=config, secrets=secrets, event_bus=bus)
    agent._request_snapshot = AsyncMock()
    return agent


def _delta_msg(market_id: str, side: str, price: float, delta: float, seq: int) -> dict:
    return {
        "seq": seq,
        "msg": {
            "market_ticker": market_id,
            "side": side,
            "price_dollars": price,
            "delta_fp": delta,
        },
    }


class TestOrderBookApplyDelta:
    def test_apply_delta_returns_false_on_gap_and_leaves_book_unchanged(self):
        book = OrderBook("KXTEST", "kalshi")
        book.apply_delta("bid", 0.50, 100, seq=1)
        assert book.apply_delta("bid", 0.99, 100, seq=5) is False
        assert book.needs_reset is True
        # Book state must be untouched by the failed delta — still reflects
        # the pre-gap price, not the corrupt/skipped one.
        assert book.best_bid == 0.50


class TestHandleKalshiDeltaSkipsStalePublish:
    @pytest.mark.asyncio
    async def test_gap_on_delta_does_not_publish_stale_price_event(self):
        agent = _make_agent()
        market_id = "KXTEST-GAP"

        # First delta establishes a clean baseline at seq=1.
        await agent._handle_kalshi_delta(
            "kalshi", _delta_msg(market_id, "yes", 0.10, 100, seq=1)
        )
        assert agent.bus.publish.await_count == 1

        # Second delta arrives with a sequence gap (seq should be 2).
        await agent._handle_kalshi_delta(
            "kalshi", _delta_msg(market_id, "yes", 0.90, 100, seq=9)
        )

        # The gap delta must NOT trigger a second publish — publishing here
        # was the live bug: it sent a PriceUpdateEvent built from the stale
        # pre-gap book, which ArbScanner then priced as a phantom opportunity.
        assert agent.bus.publish.await_count == 1
        agent._request_snapshot.assert_awaited_once_with(market_id)
        assert agent._books[market_id].needs_reset is True

    @pytest.mark.asyncio
    async def test_clean_sequential_deltas_publish_every_time(self):
        agent = _make_agent()
        market_id = "KXTEST-CLEAN"

        await agent._handle_kalshi_delta(
            "kalshi", _delta_msg(market_id, "yes", 0.10, 100, seq=1)
        )
        await agent._handle_kalshi_delta(
            "kalshi", _delta_msg(market_id, "yes", 0.11, 50, seq=2)
        )

        assert agent.bus.publish.await_count == 2
        agent._request_snapshot.assert_not_awaited()
