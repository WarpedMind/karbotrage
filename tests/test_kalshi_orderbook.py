"""
tests/test_kalshi_orderbook.py — Kalshi WS message → order book translation

Covers _handle_kalshi_snapshot()/_handle_kalshi_delta() and OrderBook in
agents/floor/price_watcher.py against the REAL Kalshi WS schema, confirmed
live on 2026-06-28 (the prior code assumed a schema that does not exist on
the wire — `market_ticker` at the top level and `yes.bids`/`yes.asks` —
which caused every snapshot/delta to be silently dropped at the market_id
check before anything else ran):

  - payload is nested under msg["msg"], not at the top level
  - snapshot: yes_dollars_fp / no_dollars_fp are flat [price, contracts]
    resting-bid lists; no_dollars_fp bids become derived YES asks at (1-p)
  - delta: one change per message (price_dollars, delta_fp, side) where
    delta_fp is a RELATIVE change to the existing size, not an absolute
    level — confirmed via a live matched +N/-N pair when an order moved
    between price levels (KXCS2GAME-...-AIM: -523.00 @ 0.02, +523.00 @ 0.08)
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
    return PriceWatcherAgent(config=config, secrets=secrets, event_bus=bus)


# ── OrderBook.apply_delta — relative semantics ──────────────────────────────

def test_apply_delta_adds_to_existing_size():
    book = OrderBook("TEST", "kalshi")
    book.apply_delta("bid", 0.50, 100.0, seq=1)
    book.apply_delta("bid", 0.50, 50.0, seq=2)
    assert book.bids[0.50] == 150.0


def test_apply_delta_removes_level_when_size_hits_zero():
    book = OrderBook("TEST", "kalshi")
    book.apply_delta("bid", 0.50, 100.0, seq=1)
    book.apply_delta("bid", 0.50, -100.0, seq=2)
    assert 0.50 not in book.bids


def test_apply_delta_clamps_negative_size_to_removal():
    book = OrderBook("TEST", "kalshi")
    book.apply_delta("bid", 0.50, 100.0, seq=1)
    book.apply_delta("bid", 0.50, -250.0, seq=2)
    assert 0.50 not in book.bids


def test_apply_delta_matched_move_between_price_levels():
    """Mirrors the live KXCS2GAME-...-AIM example exactly."""
    book = OrderBook("TEST", "kalshi")
    book.apply_delta("bid", 0.02, 523.0, seq=1)
    book.apply_delta("bid", 0.02, -523.0, seq=2)
    book.apply_delta("bid", 0.08, 523.0, seq=3)
    assert 0.02 not in book.bids
    assert book.bids[0.08] == 523.0


# ── _handle_kalshi_snapshot — real schema ───────────────────────────────────

@pytest.mark.asyncio
async def test_snapshot_parses_nested_payload_and_derives_asks_from_no_side():
    agent = _make_agent()
    msg = {
        "type": "orderbook_snapshot",
        "sid": 1,
        "seq": 2,
        "msg": {
            "market_ticker": "KXITFWMATCH-26JUN28MAQVAN-VAN",
            "market_id": "3a09202e-e6cd-4d61-bf31-71d0b54e5fb3",
            "yes_dollars_fp": [["0.4700", "22.00"], ["0.4800", "251.00"]],
            "no_dollars_fp": [["0.3200", "474.00"], ["0.3600", "14.00"]],
        },
    }

    await agent._handle_kalshi_snapshot("kalshi", msg)

    book = agent._books["KXITFWMATCH-26JUN28MAQVAN-VAN"]
    assert book.bids == {0.47: 22.0, 0.48: 251.0}
    # NO bid at 0.32 -> derived YES ask at 1 - 0.32 = 0.68
    assert book.asks == {0.68: 474.0, 0.64: 14.0}
    assert book.sequence == 2


@pytest.mark.asyncio
async def test_snapshot_ignored_when_market_ticker_missing():
    agent = _make_agent()
    msg = {"type": "orderbook_snapshot", "seq": 1, "msg": {}}

    await agent._handle_kalshi_snapshot("kalshi", msg)

    assert agent._books == {}


# ── _handle_kalshi_delta — real schema ───────────────────────────────────────

@pytest.mark.asyncio
async def test_delta_yes_side_updates_bid_book():
    agent = _make_agent()
    snapshot_msg = {
        "type": "orderbook_snapshot", "seq": 1,
        "msg": {"market_ticker": "KXTEST-1", "yes_dollars_fp": [], "no_dollars_fp": []},
    }
    await agent._handle_kalshi_snapshot("kalshi", snapshot_msg)

    delta_msg = {
        "type": "orderbook_delta",
        "seq": 2,
        "msg": {
            "market_ticker": "KXTEST-1",
            "price_dollars": "0.0200",
            "delta_fp": "523.00",
            "side": "yes",
        },
    }
    await agent._handle_kalshi_delta("kalshi", delta_msg)

    book = agent._books["KXTEST-1"]
    assert book.bids[0.02] == 523.0


@pytest.mark.asyncio
async def test_delta_no_side_updates_derived_ask_book():
    agent = _make_agent()
    snapshot_msg = {
        "type": "orderbook_snapshot", "seq": 1,
        "msg": {"market_ticker": "KXTEST-2", "yes_dollars_fp": [], "no_dollars_fp": []},
    }
    await agent._handle_kalshi_snapshot("kalshi", snapshot_msg)

    delta_msg = {
        "type": "orderbook_delta",
        "seq": 2,
        "msg": {
            "market_ticker": "KXTEST-2",
            "price_dollars": "0.5200",
            "delta_fp": "-250.00",
            "side": "no",
        },
    }
    # First add size so the subsequent negative delta has something to remove
    await agent._handle_kalshi_delta("kalshi", {
        "type": "orderbook_delta", "seq": 2,
        "msg": {"market_ticker": "KXTEST-2", "price_dollars": "0.5200",
                 "delta_fp": "250.00", "side": "no"},
    })
    await agent._handle_kalshi_delta("kalshi", {
        "type": "orderbook_delta", "seq": 3,
        "msg": {"market_ticker": "KXTEST-2", "price_dollars": "0.5200",
                 "delta_fp": "-250.00", "side": "no"},
    })

    book = agent._books["KXTEST-2"]
    # NO bid at 0.52 -> derived YES ask at 1 - 0.52 = 0.48; net delta = 0 -> removed
    assert 0.48 not in book.asks


@pytest.mark.asyncio
async def test_delta_ignored_when_market_ticker_missing():
    agent = _make_agent()
    msg = {"type": "orderbook_delta", "seq": 1, "msg": {}}

    await agent._handle_kalshi_delta("kalshi", msg)

    assert agent._books == {}


@pytest.mark.asyncio
async def test_delta_unknown_side_does_not_raise():
    agent = _make_agent()
    snapshot_msg = {
        "type": "orderbook_snapshot", "seq": 1,
        "msg": {"market_ticker": "KXTEST-3", "yes_dollars_fp": [], "no_dollars_fp": []},
    }
    await agent._handle_kalshi_snapshot("kalshi", snapshot_msg)

    delta_msg = {
        "type": "orderbook_delta", "seq": 2,
        "msg": {"market_ticker": "KXTEST-3", "price_dollars": "0.50",
                 "delta_fp": "10.00", "side": "unknown"},
    }
    await agent._handle_kalshi_delta("kalshi", delta_msg)  # must not raise

    book = agent._books["KXTEST-3"]
    assert book.bids == {}
    assert book.asks == {}
