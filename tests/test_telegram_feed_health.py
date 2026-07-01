"""
tests/test_telegram_feed_health.py — TelegramNotificationAgent x FeedHealthEvent

Covers the Session 20 feature: an immediate Tier 1 Telegram alert when the
Kalshi feed goes down, and a distinct "recovered" alert when it comes back —
flowing through the existing FeedHealthEvent + outbound-queue pattern (no
new direct call from price_watcher.py into Telegram).

Alerts must fire only on a connected→disconnected or disconnected→connected
transition, not on every FeedHealthEvent published while an outage continues
(the agent's own health monitor can republish connected=False repeatedly for
one continuous outage).
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.events import EventBus, FeedHealthEvent
from karbot.core.config import KarbotConfig, TelegramConfig
from agents.notifications.telegram_agent import TelegramNotificationAgent


def _make_agent() -> TelegramNotificationAgent:
    config = KarbotConfig()
    config.telegram = TelegramConfig(enabled=True)
    bus = EventBus()
    agent = TelegramNotificationAgent(bus=bus, config=config)
    agent.register_subscriptions()
    return agent


@pytest.mark.asyncio
async def test_feed_down_triggers_exactly_one_alert_per_outage():
    """Multiple connected=False events during one continuous outage → one alert."""
    agent = _make_agent()
    agent._outbound_queue.put = AsyncMock(wraps=agent._outbound_queue.put)

    # Establish an initial known-connected state (no alert — no prior state).
    await agent._handle_feed_health(FeedHealthEvent(platform="kalshi", connected=True))
    assert agent._outbound_queue.put.call_count == 0

    # First disconnect — alert fires.
    await agent._handle_feed_health(
        FeedHealthEvent(platform="kalshi", connected=False, error="connection reset")
    )
    assert agent._outbound_queue.put.call_count == 1

    # Repeated connected=False events for the same continuous outage — no more alerts.
    await agent._handle_feed_health(FeedHealthEvent(platform="kalshi", connected=False))
    await agent._handle_feed_health(FeedHealthEvent(platform="kalshi", connected=False))
    assert agent._outbound_queue.put.call_count == 1

    sent_text = agent._outbound_queue.put.call_args_list[0].args[0]
    assert "FEED DOWN" in sent_text
    assert "kalshi" in sent_text
    assert "connection reset" in sent_text


@pytest.mark.asyncio
async def test_feed_recovery_triggers_distinct_alert():
    """connected=True after an outage → a distinct 'recovered' alert."""
    agent = _make_agent()
    agent._outbound_queue.put = AsyncMock(wraps=agent._outbound_queue.put)

    await agent._handle_feed_health(FeedHealthEvent(platform="kalshi", connected=True))
    await agent._handle_feed_health(FeedHealthEvent(platform="kalshi", connected=False))
    assert agent._outbound_queue.put.call_count == 1
    down_text = agent._outbound_queue.put.call_args_list[0].args[0]
    assert "FEED DOWN" in down_text

    await agent._handle_feed_health(FeedHealthEvent(platform="kalshi", connected=True))
    assert agent._outbound_queue.put.call_count == 2
    recovered_text = agent._outbound_queue.put.call_args_list[1].args[0]
    assert "FEED RECOVERED" in recovered_text
    assert "FEED DOWN" not in recovered_text

    # Repeated connected=True after recovery — no further alerts.
    await agent._handle_feed_health(FeedHealthEvent(platform="kalshi", connected=True))
    assert agent._outbound_queue.put.call_count == 2


@pytest.mark.asyncio
async def test_non_kalshi_platform_ignored():
    """FeedHealthEvent for a non-kalshi platform must not trigger an alert."""
    agent = _make_agent()
    agent._outbound_queue.put = AsyncMock(wraps=agent._outbound_queue.put)

    await agent._handle_feed_health(FeedHealthEvent(platform="polymarket", connected=True))
    await agent._handle_feed_health(FeedHealthEvent(platform="polymarket", connected=False))

    assert agent._outbound_queue.put.call_count == 0


@pytest.mark.asyncio
async def test_disabled_telegram_does_not_queue_message():
    config = KarbotConfig()
    config.telegram = TelegramConfig(enabled=False)
    bus = EventBus()
    agent = TelegramNotificationAgent(bus=bus, config=config)
    agent.register_subscriptions()
    agent._outbound_queue.put = AsyncMock(wraps=agent._outbound_queue.put)

    await agent._handle_feed_health(FeedHealthEvent(platform="kalshi", connected=True))
    await agent._handle_feed_health(FeedHealthEvent(platform="kalshi", connected=False))

    assert agent._outbound_queue.put.call_count == 0
