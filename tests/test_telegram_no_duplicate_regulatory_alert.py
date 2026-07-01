"""
tests/test_telegram_no_duplicate_regulatory_alert.py — Session 25 fix

RegulatoryIntelligenceAgent's _route_by_urgency already publishes correct,
well-formatted TelegramNotificationEvent messages branched by urgency (3=info,
4=acknowledgment-required, 5=trading-paused). It also always publishes
RegulatoryAlertEvent for every item (regardless of urgency) so
ComplianceOfficer can log it. TelegramNotificationAgent used to ALSO
subscribe to RegulatoryAlertEvent directly, producing a second, duplicate
Telegram message per regulatory item — and that second message was broken:
it read event.source_name/event.matched_keywords, which the publisher never
populates (always empty), and told the operator to check
logs/regulatory_alerts.txt, a file deleted in an earlier session. Labeling
every routine urgency-3 FYI as "CRITICAL" also degraded trust in the one
alert that matters most (urgency 5).

Fix: removed TelegramNotificationAgent's RegulatoryAlertEvent subscription
and its _handle_regulatory_alert method entirely. RegulatoryAlertEvent still
publishes (for ComplianceOfficer) -- only the Telegram side was removed.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.events import EventBus, RegulatoryAlertEvent
from karbot.core.config import KarbotConfig, TelegramConfig
from agents.notifications.telegram_agent import TelegramNotificationAgent


def _make_agent() -> TelegramNotificationAgent:
    config = KarbotConfig()
    config.telegram = TelegramConfig(enabled=True)
    bus = EventBus()
    agent = TelegramNotificationAgent(bus=bus, config=config)
    agent.register_subscriptions()
    return agent


def test_telegram_agent_has_no_regulatory_alert_handler():
    """_handle_regulatory_alert must not exist on TelegramNotificationAgent."""
    assert not hasattr(TelegramNotificationAgent, "_handle_regulatory_alert")


def test_telegram_agent_does_not_subscribe_to_regulatory_alert_event():
    """RegulatoryAlertEvent must have no registered handlers on this bus after
    TelegramNotificationAgent.register_subscriptions() runs (in isolation --
    no other agent subscribed in this test)."""
    agent = _make_agent()

    handlers = agent.bus._handlers.get(RegulatoryAlertEvent, [])
    assert handlers == []


@pytest.mark.asyncio
async def test_publishing_regulatory_alert_event_does_not_queue_telegram_message():
    """Publishing a RegulatoryAlertEvent must not cause
    TelegramNotificationAgent to queue any outbound message -- it has no
    subscription to react to it anymore."""
    agent = _make_agent()

    await agent.bus.publish(RegulatoryAlertEvent(
        source="regulatory_intelligence",
        urgency=5,
        summary="Test regulatory item",
    ))

    # Drain whatever the bus queue holds by dispatching manually, since no
    # bus.run() task is active in this test -- there is simply no handler
    # registered for RegulatoryAlertEvent to have been called.
    assert agent.bus._handlers.get(RegulatoryAlertEvent, []) == []
    assert agent._outbound_queue.empty()
