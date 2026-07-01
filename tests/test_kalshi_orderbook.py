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

import json
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


# ── Sequence gap → needs_reset → apply_snapshot resets flag ─────────────────

def test_sequence_gap_sets_needs_reset_and_snapshot_clears_it():
    """apply_delta with a gap returns False and sets needs_reset; apply_snapshot clears it."""
    book = OrderBook("KXTEST-GAP", "kalshi")
    # Bootstrap sequence to 5
    book.apply_delta("bid", 0.50, 100.0, seq=1)
    book.sequence = 5

    # Gap: deliver seq=7 instead of 6
    result = book.apply_delta("bid", 0.50, 10.0, seq=7)
    assert result is False
    assert book.needs_reset is True

    # Receiving a snapshot resets the flag
    book.apply_snapshot(bids=[(0.50, 100.0)], asks=[], seq=8)
    assert book.needs_reset is False


# ── _request_snapshot throttling ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_request_snapshot_throttled_second_call_suppressed():
    """Two _request_snapshot calls within 10s on the same market → only one WS send."""
    agent = _make_agent()

    mock_ws = AsyncMock()
    mock_client = MagicMock()
    mock_client._connected = True
    mock_client._ws = mock_ws
    agent._kalshi_client = mock_client

    await agent._request_snapshot("KXTEST-THROTTLE")
    await agent._request_snapshot("KXTEST-THROTTLE")   # within 10s → suppressed

    assert mock_ws.send.call_count == 1


@pytest.mark.asyncio
async def test_request_snapshot_throttle_resets_after_window():
    """A second call after >10s IS sent."""
    agent = _make_agent()

    mock_ws = AsyncMock()
    mock_client = MagicMock()
    mock_client._connected = True
    mock_client._ws = mock_ws
    agent._kalshi_client = mock_client

    # Simulate the first call happening 11 seconds ago
    agent._reset_requested["KXTEST-WINDOW"] = time.monotonic() - 11.0

    await agent._request_snapshot("KXTEST-WINDOW")

    assert mock_ws.send.call_count == 1


# ── _request_snapshot no-ops when client is None ─────────────────────────────

@pytest.mark.asyncio
async def test_request_snapshot_noop_when_client_none():
    """_request_snapshot returns silently when _kalshi_client is None."""
    agent = _make_agent()
    # _kalshi_client is None by default — should not raise

    await agent._request_snapshot("KXTEST-NOCLIENT")   # must not raise

    # No _reset_requested entry written (we returned before the throttle update)
    assert "KXTEST-NOCLIENT" not in agent._reset_requested


# ── _request_snapshot uses a unique "id" per call (not hardcoded) ───────────

@pytest.mark.asyncio
async def test_request_snapshot_uses_distinct_id_per_market():
    """Each _request_snapshot call across different markets must use a distinct
    "id" value — a shared hardcoded id lets Kalshi's response correlation
    conflate concurrent resets across markets (root cause of the 10.2%
    book_snapshot_applied completion rate observed on the VPS 2026-06-30)."""
    agent = _make_agent()

    mock_ws = AsyncMock()
    mock_client = MagicMock()
    mock_client._connected = True
    mock_client._ws = mock_ws
    agent._kalshi_client = mock_client

    await agent._request_snapshot("KXTEST-IDA")
    await agent._request_snapshot("KXTEST-IDB")

    sent_ids = [json.loads(call.args[0])["id"] for call in mock_ws.send.call_args_list]
    assert len(sent_ids) == 2
    assert sent_ids[0] != sent_ids[1]
    assert all(i != 99 for i in sent_ids)


@pytest.mark.asyncio
async def test_request_snapshot_id_increments_monotonically():
    """The id counter increments across successive (non-throttled) calls."""
    agent = _make_agent()

    mock_ws = AsyncMock()
    mock_client = MagicMock()
    mock_client._connected = True
    mock_client._ws = mock_ws
    agent._kalshi_client = mock_client

    await agent._request_snapshot("KXTEST-INC1")
    agent._reset_requested["KXTEST-INC2"] = time.monotonic() - 11.0
    await agent._request_snapshot("KXTEST-INC2")

    sent_ids = [json.loads(call.args[0])["id"] for call in mock_ws.send.call_args_list]
    assert sent_ids == sorted(sent_ids)
    assert sent_ids[1] > sent_ids[0]


# ── book_needs_reset log level (Fix 2: noise reduction) ─────────────────────

@pytest.mark.asyncio
async def test_book_needs_reset_logs_at_debug_not_warning():
    """The book_needs_reset call site in _handle_kalshi_delta must log at debug,
    not warning — it fires on every delta received while a market awaits
    snapshot recovery (2.17M warning lines/day observed on the VPS), unlike
    sequence_gap_detected in apply_delta() which fires once per gap episode
    and must remain at warning."""
    import agents.floor.price_watcher as pw_module

    agent = _make_agent()
    snapshot_msg = {
        "type": "orderbook_snapshot", "seq": 1,
        "msg": {"market_ticker": "KXTEST-LOGLEVEL", "yes_dollars_fp": [], "no_dollars_fp": []},
    }
    await agent._handle_kalshi_snapshot("kalshi", snapshot_msg)

    book = agent._books["KXTEST-LOGLEVEL"]
    book.sequence = 5
    book.apply_delta("bid", 0.50, 10.0, seq=7)   # gap → needs_reset True
    assert book.needs_reset is True

    with patch.object(pw_module.log, "debug") as mock_debug, \
         patch.object(pw_module.log, "warning") as mock_warning:
        delta_msg = {
            "type": "orderbook_delta", "seq": 8,
            "msg": {"market_ticker": "KXTEST-LOGLEVEL", "price_dollars": "0.50",
                     "delta_fp": "1.00", "side": "yes"},
        }
        await agent._handle_kalshi_delta("kalshi", delta_msg)

        debug_events = [call.args[0] for call in mock_debug.call_args_list]
        warning_events = [call.args[0] for call in mock_warning.call_args_list]

        assert "book_needs_reset" in debug_events
        assert "book_needs_reset" not in warning_events


def test_sequence_gap_detected_still_logs_at_warning():
    """apply_delta()'s sequence_gap_detected log must remain at warning level —
    it fires once per gap episode (False→True transition), unlike
    book_needs_reset which fires per-delta and was moved to debug."""
    import agents.floor.price_watcher as pw_module

    book = OrderBook("KXTEST-GAPWARN", "kalshi")
    book.apply_delta("bid", 0.50, 100.0, seq=1)
    book.sequence = 5

    with patch.object(pw_module.log, "warning") as mock_warning:
        result = book.apply_delta("bid", 0.50, 10.0, seq=7)
        assert result is False

        warning_events = [call.args[0] for call in mock_warning.call_args_list]
        assert "sequence_gap_detected" in warning_events
