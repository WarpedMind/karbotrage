"""
tests/test_telegram_sender_auth.py

Covers a security bug found by Fable's Session 28 review and independently
confirmed by reading the code: TelegramNotificationAgent._poll_updates
processed every Telegram message it received as an authoritative operator
command with no check on who sent it. Any user who found the bot (bots
are publicly messageable by default) could resolve pending permission
requests or, since the default regulatory clear phrase is committed in
this public repo, clear a regulatory trading halt. Fixed with
_is_authorized_sender(), checked before dispatching to
_handle_operator_reply.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.events import EventBus
from karbot.core.config import KarbotConfig, TelegramConfig
from agents.notifications.telegram_agent import TelegramNotificationAgent


def _make_agent(chat_id="403478944") -> TelegramNotificationAgent:
    config = KarbotConfig()
    config.telegram = TelegramConfig(enabled=True)
    config.secrets.telegram_bot_token = "fake-token"
    config.secrets.telegram_chat_id = chat_id
    bus = EventBus()
    return TelegramNotificationAgent(bus=bus, config=config)


class TestSenderAuth:
    def test_message_from_configured_chat_id_is_authorized(self):
        agent = _make_agent(chat_id="403478944")
        msg = {"chat": {"id": 403478944}, "text": "yes"}
        assert agent._is_authorized_sender(msg) is True

    def test_message_from_a_stranger_is_rejected(self):
        agent = _make_agent(chat_id="403478944")
        msg = {"chat": {"id": 999999999}, "text": "CLEAR REGULATORY HOLD"}
        assert agent._is_authorized_sender(msg) is False

    def test_message_with_no_chat_field_is_rejected(self):
        agent = _make_agent(chat_id="403478944")
        assert agent._is_authorized_sender({"text": "yes"}) is False

    def test_string_vs_int_chat_id_still_matches(self):
        """Telegram's chat.id is a JSON int; config values may be loaded
        as strings from the environment — must compare equal regardless."""
        agent = _make_agent(chat_id="403478944")
        msg = {"chat": {"id": 403478944}, "text": "yes"}
        assert agent._is_authorized_sender(msg) is True
