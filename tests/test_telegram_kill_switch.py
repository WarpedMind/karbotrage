"""
tests/test_telegram_kill_switch.py

Covers a gap found by Fable's Session 28 review, independently confirmed
by reading the code: KillSwitchEvent and RiskGate._on_kill_switch were
fully implemented, but nothing in the codebase ever published the event
or called activate_kill_switch() — the "nuclear option" had no trigger.
Wired the Telegram operator channel as the trigger source (matching the
event's own "TELEGRAM" field comment), gated behind the sender-auth fix
from the same session so only the real operator can reach it.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.events import EventBus, KillSwitchEvent
from karbot.core.config import KarbotConfig, TelegramConfig
from agents.notifications.telegram_agent import TelegramNotificationAgent
from agents.floor.risk_gate import RiskGateAgent


def _make_agent() -> TelegramNotificationAgent:
    config = KarbotConfig()
    config.telegram = TelegramConfig(enabled=True)
    bus = EventBus()
    agent = TelegramNotificationAgent(bus=bus, config=config)
    agent.register_subscriptions()
    agent.bus.publish = AsyncMock()
    agent._outbound_queue.put = AsyncMock(wraps=agent._outbound_queue.put)
    return agent


class TestKillSwitchTrigger:
    @pytest.mark.asyncio
    async def test_exact_phrase_publishes_kill_switch_event(self):
        agent = _make_agent()
        await agent._handle_operator_reply("EMERGENCY KILL SWITCH")
        published = [c.args[0] for c in agent.bus.publish.await_args_list]
        kill_events = [e for e in published if isinstance(e, KillSwitchEvent)]
        assert len(kill_events) == 1
        assert kill_events[0].triggered_by == "TELEGRAM"

    @pytest.mark.asyncio
    async def test_phrase_is_case_insensitive(self):
        agent = _make_agent()
        await agent._handle_operator_reply("emergency kill switch")
        published = [c.args[0] for c in agent.bus.publish.await_args_list]
        assert any(isinstance(e, KillSwitchEvent) for e in published)

    @pytest.mark.asyncio
    async def test_ordinary_message_does_not_trigger_it(self):
        agent = _make_agent()
        await agent._handle_operator_reply("yes")
        published = [c.args[0] for c in agent.bus.publish.await_args_list]
        assert not any(isinstance(e, KillSwitchEvent) for e in published)

    @pytest.mark.asyncio
    async def test_kill_switch_sends_confirmation_message(self):
        agent = _make_agent()
        await agent._handle_operator_reply("EMERGENCY KILL SWITCH")
        text = agent._outbound_queue.put.await_args.args[0]
        assert "KILL SWITCH" in text.upper()

    @pytest.mark.asyncio
    async def test_kill_switch_does_not_also_resolve_a_pending_permission_request(self):
        """The kill switch phrase must short-circuit before the normal
        yes/no permission-resolution path — it's not a permission reply."""
        agent = _make_agent()
        agent._pending_requests["req-1"] = MagicMock()
        await agent._handle_operator_reply("EMERGENCY KILL SWITCH")
        assert "req-1" in agent._pending_requests


class TestRiskGateHonorsKillSwitch:
    @pytest.mark.asyncio
    async def test_kill_switch_event_sets_active_flag(self):
        gate = RiskGateAgent(config=KarbotConfig(), event_bus=EventBus())
        assert gate._kill_switch_active is False
        await gate._on_kill_switch(
            KillSwitchEvent(triggered_by="TELEGRAM", reason="test")
        )
        assert gate._kill_switch_active is True
