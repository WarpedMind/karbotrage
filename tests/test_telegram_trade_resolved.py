"""
tests/test_telegram_trade_resolved.py — TelegramNotificationAgent x TradeResolvedEvent

Covers a gap flagged by the operator (2026-07-13): TelegramNotificationAgent
previously subscribed to TradeExecutedEvent (the entry, with an *estimated*
expected_pnl_usd) but never to TradeResolvedEvent — the operator was seeing
pre-resolution estimates and had no way to tell when a trade actually
settled or what it settled at. Added _handle_trade_resolved to close that
gap, and made both messages more verbose (market, strategy, legs) per the
same feedback that the existing messages were hard to interpret.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.events import EventBus, TradeExecutedEvent, TradeResolvedEvent
from karbot.core.config import KarbotConfig, TelegramConfig
from agents.notifications.telegram_agent import TelegramNotificationAgent


def _make_agent() -> TelegramNotificationAgent:
    config = KarbotConfig()
    config.telegram = TelegramConfig(enabled=True)
    bus = EventBus()
    agent = TelegramNotificationAgent(bus=bus, config=config)
    agent.register_subscriptions()
    agent._outbound_queue.put = AsyncMock(wraps=agent._outbound_queue.put)
    return agent


@pytest.mark.asyncio
async def test_trade_resolved_sends_a_message_with_realized_pnl():
    agent = _make_agent()
    await agent._handle_trade_resolved(
        TradeResolvedEvent(
            trade_id="abc12345-full-uuid",
            market_id="KXTEST-MARKET",
            platform="kalshi",
            resolution="YES",
            realized_pnl=12.34,
            holding_period_hours=1.5,
        )
    )
    agent._outbound_queue.put.assert_awaited_once()
    text = agent._outbound_queue.put.await_args.args[0]
    assert "RESOLVED" in text
    assert "KXTEST-MARKET" in text
    assert "12.34" in text
    assert "YES" in text


@pytest.mark.asyncio
async def test_trade_resolved_respects_disabled_flag():
    config = KarbotConfig()
    config.telegram = TelegramConfig(enabled=False)
    bus = EventBus()
    agent = TelegramNotificationAgent(bus=bus, config=config)
    agent.register_subscriptions()
    agent._outbound_queue.put = AsyncMock(wraps=agent._outbound_queue.put)

    await agent._handle_trade_resolved(
        TradeResolvedEvent(trade_id="x", market_id="y", realized_pnl=1.0)
    )
    agent._outbound_queue.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_trade_executed_message_includes_market_and_strategy():
    agent = _make_agent()
    await agent._handle_trade_executed(
        TradeExecutedEvent(
            trade_id="def67890-full-uuid",
            strategy="S1_REBALANCING",
            platform_legs=[
                {"market_id": "KXTEST-M", "side": "YES", "filled_price": 0.42, "quantity": 500},
                {"market_id": "KXTEST-M", "side": "NO", "filled_price": 0.40, "quantity": 500},
            ],
            total_fee_paid=15.0,
            expected_pnl_usd=9.0,
            paper_mode=True,
        )
    )
    agent._outbound_queue.put.assert_awaited_once()
    text = agent._outbound_queue.put.await_args.args[0]
    assert "S1_REBALANCING" in text
    assert "KXTEST-M" in text
    assert "estimate" in text.lower()
