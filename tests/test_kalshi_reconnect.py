"""
tests/test_kalshi_reconnect.py — _kalshi_connection_loop retry/before_sleep behavior

Covers the Session 19 fix: tenacity's before_sleep_log(logger, "WARNING") is
written for stdlib logging.Logger and calls logger.log("WARNING", ...) — a
string level. structlog's BoundLogger.log() expects an int level and raises
TypeError("'<' not supported between instances of 'str' and 'int'") on the
first retry attempt, which propagates out of tenacity's retry machinery
itself. This meant @retry on _kalshi_connection_loop never actually retried
— confirmed live via a Kalshi WS disconnect at 07:42 UTC that killed the
price feed for ~6 hours with zero retry attempts logged.

Fix: a custom before_sleep callback (_log_before_sleep) compatible with
structlog, passed as before_sleep=_log_before_sleep instead of
before_sleep_log(log, "WARNING").
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import tenacity
import websockets.exceptions

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
    bus.publish = AsyncMock()
    return PriceWatcherAgent(config=config, secrets=secrets, event_bus=bus)


@pytest.mark.asyncio
async def test_kalshi_connection_loop_retries_and_succeeds_after_failure():
    """First connect() raises ConnectionClosedError, second succeeds.

    Confirms the before_sleep callback does NOT raise (the structlog
    TypeError bug) and that tenacity's retry actually proceeds to a second,
    successful attempt instead of crashing out on the first failure.
    """
    agent = _make_agent()

    mock_client = MagicMock()
    mock_client.connect = AsyncMock(
        side_effect=[
            websockets.exceptions.ConnectionClosedError(None, None),
            None,  # second call succeeds
        ]
    )
    mock_client.subscribe_markets = AsyncMock()
    mock_client.listen = AsyncMock()

    with patch(
        "agents.floor.price_watcher.KalshiWebSocketClient",
        return_value=mock_client,
    ), patch.object(
        agent, "_fetch_active_kalshi_markets", AsyncMock(return_value=[])
    ), patch(
        "asyncio.sleep", AsyncMock(return_value=None)
    ):
        # tenacity's AsyncRetrying sleeps via asyncio.sleep for backoff between
        # attempts; patched to instant so the real stop/wait/retry logic still
        # runs but the test doesn't block on real exponential backoff.
        await agent._kalshi_connection_loop()

    assert mock_client.connect.call_count == 2
    assert mock_client.listen.call_count == 1


@pytest.mark.asyncio
async def test_kalshi_connection_loop_gives_up_after_max_attempts():
    """Confirms stop_after_attempt(10) still terminates retries and the
    failure propagates as tenacity.RetryError (documented, current behavior
    — not changed here; see the NOTE comment above _kalshi_connection_loop
    for the open architectural question about agent-level restart)."""
    agent = _make_agent()

    mock_client = MagicMock()
    mock_client.connect = AsyncMock(
        side_effect=websockets.exceptions.ConnectionClosedError(None, None)
    )
    mock_client.subscribe_markets = AsyncMock()
    mock_client.listen = AsyncMock()

    with patch(
        "agents.floor.price_watcher.KalshiWebSocketClient",
        return_value=mock_client,
    ), patch.object(
        agent, "_fetch_active_kalshi_markets", AsyncMock(return_value=[])
    ), patch(
        "asyncio.sleep", AsyncMock(return_value=None)
    ):
        with pytest.raises(tenacity.RetryError):
            await agent._kalshi_connection_loop()

    assert mock_client.connect.call_count == 10
