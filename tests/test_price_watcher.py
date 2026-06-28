"""
tests/test_price_watcher.py — Kalshi market fetch + volume filter

Covers _fetch_active_kalshi_markets() in agents/floor/price_watcher.py:
  - follows the `cursor` field across pages instead of stopping at page 1
  - filters on the real `volume_24h_fp` field (string, needs float()), not
    the nonexistent `volume_24h`/`volume` fields
  - excludes markets with missing/malformed volume instead of raising
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.floor.price_watcher import PriceWatcherAgent


def _make_agent() -> PriceWatcherAgent:
    config = MagicMock()
    secrets = MagicMock()
    secrets.kalshi_api_key_id = "test-key-id"
    secrets.kalshi_private_key_path = "/fake/path.pem"
    bus = MagicMock()
    return PriceWatcherAgent(config=config, secrets=secrets, event_bus=bus)


class _FakeResponse:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _fake_session(pages: list) -> MagicMock:
    """pages: list of (status, payload) tuples returned in order per .get() call."""
    session = MagicMock()
    responses = [_FakeResponse(status, payload) for status, payload in pages]

    def get(*args, **kwargs):
        return responses.pop(0)

    session.get = MagicMock(side_effect=get)

    class _SessionCtx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    return _SessionCtx()


@pytest.mark.asyncio
async def test_paginates_until_cursor_exhausted_and_filters_by_volume_24h_fp():
    page1 = {
        "cursor": "abc123",
        "markets": [
            {"ticker": "KXMVE-DEAD-1", "volume_24h_fp": "0.00"},
            {"ticker": "KXMVE-DEAD-2", "volume_24h_fp": "0.00"},
        ],
    }
    page2 = {
        "cursor": "",
        "markets": [
            {"ticker": "KXHIGHNY-26JUN28-T84", "volume_24h_fp": "150.00"},
            {"ticker": "KXMVE-DEAD-3", "volume_24h_fp": "0.00"},
        ],
    }

    agent = _make_agent()

    with patch("agents.floor.price_watcher._load_kalshi_private_key", return_value=MagicMock()), \
         patch("agents.floor.price_watcher._build_kalshi_auth_headers", return_value={}), \
         patch("aiohttp.ClientSession", return_value=_fake_session([(200, page1), (200, page2)])):
        result = await agent._fetch_active_kalshi_markets()

    assert result == ["KXHIGHNY-26JUN28-T84"]


@pytest.mark.asyncio
async def test_excludes_market_with_missing_or_malformed_volume():
    page = {
        "cursor": "",
        "markets": [
            {"ticker": "KXOK", "volume_24h_fp": "200.00"},
            {"ticker": "KXMISSING"},
            {"ticker": "KXBAD", "volume_24h_fp": "n/a"},
            {"ticker": "KXNONE", "volume_24h_fp": None},
        ],
    }

    agent = _make_agent()

    with patch("agents.floor.price_watcher._load_kalshi_private_key", return_value=MagicMock()), \
         patch("agents.floor.price_watcher._build_kalshi_auth_headers", return_value={}), \
         patch("aiohttp.ClientSession", return_value=_fake_session([(200, page)])):
        result = await agent._fetch_active_kalshi_markets()

    assert result == ["KXOK"]


@pytest.mark.asyncio
async def test_stops_on_non_200_response():
    page = {"status": "error"}

    agent = _make_agent()

    with patch("agents.floor.price_watcher._load_kalshi_private_key", return_value=MagicMock()), \
         patch("agents.floor.price_watcher._build_kalshi_auth_headers", return_value={}), \
         patch("aiohttp.ClientSession", return_value=_fake_session([(500, page)])):
        result = await agent._fetch_active_kalshi_markets()

    assert result == []
