"""
tests/test_telegram_small_trade_display.py

Covers a display bug found live 2026-07-16: a real, legitimate liquidity-
capped trade (size_usd=0.05, realized_pnl=0.0042 — a genuine trade, not
the ZERO_APPROVED_SIZE bug, confirmed via VPS logs showing 0 such
rejections in the relevant window) rendered in Telegram as "x0" /
"$0.00", indistinguishable from what a real zero-size bug would look
like. `_fmt_qty`/`_fmt_usd` fix the rounding so small-but-real trades
stay visibly nonzero. See DECISIONS.md/SESSIONS.md Session 26 addendum.
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


class TestFmtQty:
    def test_whole_quantities_show_cleanly(self):
        assert TelegramNotificationAgent._fmt_qty(10.0) == "10"
        assert TelegramNotificationAgent._fmt_qty(5.0) == "5"

    def test_fractional_quantities_stay_visible(self):
        assert TelegramNotificationAgent._fmt_qty(0.05) == "0.05"
        assert TelegramNotificationAgent._fmt_qty(81.36) == "81.36"

    def test_never_rounds_a_real_small_quantity_to_zero(self):
        assert TelegramNotificationAgent._fmt_qty(0.05) != "0"


class TestFmtUsd:
    def test_normal_amounts_show_two_decimals(self):
        assert TelegramNotificationAgent._fmt_usd(0.60) == "0.60"
        assert TelegramNotificationAgent._fmt_usd(10.5011) == "10.50"

    def test_subcent_amounts_show_more_precision(self):
        assert TelegramNotificationAgent._fmt_usd(0.0042) == "0.0042"

    def test_never_rounds_a_real_subcent_amount_to_zero(self):
        assert TelegramNotificationAgent._fmt_usd(0.0042) != "0.00"

    def test_exact_zero_still_shows_as_zero(self):
        assert TelegramNotificationAgent._fmt_usd(0.0) == "0.00"


class TestLiveMessageDoesNotLookLikeZeroSizeBug:
    @pytest.mark.asyncio
    async def test_small_real_trade_message_shows_nonzero_values(self):
        agent = _make_agent()
        await agent._handle_trade_executed(
            TradeExecutedEvent(
                trade_id="7c0f9b9d-full-uuid",
                strategy="S1_REBALANCING",
                platform_legs=[
                    {"market_id": "KXLOWTPHX-26JUL16-B84.5", "side": "YES", "filled_price": 0.17, "quantity": 0.05},
                    {"market_id": "KXLOWTPHX-26JUL16-B84.5", "side": "NO", "filled_price": 0.72, "quantity": 0.05},
                ],
                total_fee_paid=0.0009,
                expected_pnl_usd=0.0042,
                paper_mode=True,
            )
        )
        text = agent._outbound_queue.put.await_args.args[0]
        assert "x0.05" in text
        assert "$0.0042" in text  # expected PnL, not rounded to $0.00
        assert "$0.0009" in text  # fee, not rounded to $0.00
