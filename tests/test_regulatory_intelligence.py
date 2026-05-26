"""
tests/test_regulatory_intelligence.py — Regulatory Intelligence Agent test suite

Tests all core behaviors using mocked Claude API responses and event bus
injection. No real HTTP calls to CFTC or Federal Register. No real Claude API
calls. No Telegram messages sent.

Tests:
  1.  Keyword pre-filter gates correctly — no keyword match → no Claude API call
  2.  Overflow queue — items exceeding per-cycle cap held for next cycle
  3.  Urgency 1-2 — no TelegramNotificationEvent published
  4.  Urgency 3 — TelegramNotificationEvent published, _regulatory_pause stays False
  5.  Urgency 5 — RegulatoryAlertEvent(urgency=5), _regulatory_pause set True,
      Risk Gate rejects next opportunity with REGULATORY_PAUSE
  6.  Operator clear — correct phrase → _regulatory_pause cleared →
      RegulatoryAlertEvent(urgency=0) → Risk Gate resumes approvals
  7.  Deduplication — same URL twice → Claude called only once
  8.  Daily cap — after daily cap reached, further calls blocked + Telegram alert
  9.  Circuit breaker — N calls in window → breaker trips + Telegram + no more calls
  10. ComplianceOfficer logs RegulatoryAlertEvent → entry in compliance_actions.jsonl
  11. Bad API response — malformed JSON → urgency defaults to 3, no crash
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.events import (
    EventBus,
    OpportunityEvent,
    PositionSnapshot,
    RegulatoryAlertEvent,
    TelegramNotificationEvent,
    TelegramPermissionResponseEvent,
    Priority,
)
from karbot.core.config import KarbotConfig, RegulatoryIntelligenceConfig
from agents.research.regulatory_intelligence import RegulatoryIntelligenceAgentImpl
from agents.floor.risk_gate import RiskGate
from agents.management.compliance import ComplianceOfficer, LOGS_DIR


# ── Fixtures and helpers ──────────────────────────────────────────────────────

def _make_config(**ri_overrides) -> KarbotConfig:
    """Build a KarbotConfig with test-friendly regulatory_intelligence settings."""
    ri = RegulatoryIntelligenceConfig(
        enabled=True,
        regulatory_ai_calls_per_cycle=ri_overrides.get("regulatory_ai_calls_per_cycle", 5),
        regulatory_ai_daily_cap=ri_overrides.get("regulatory_ai_daily_cap", 50),
        regulatory_circuit_breaker_calls=ri_overrides.get("regulatory_circuit_breaker_calls", 20),
        regulatory_circuit_breaker_window_minutes=ri_overrides.get(
            "regulatory_circuit_breaker_window_minutes", 10
        ),
        regulatory_cost_per_call_usd=0.003,
        regulatory_clear_phrase=ri_overrides.get(
            "regulatory_clear_phrase", "CLEAR REGULATORY HOLD"
        ),
        regulatory_keywords=["prediction market", "CFTC", "enforcement", "insider trading"],
        weekly_sweep_day="sunday",
        weekly_sweep_hour_utc=6,
        poll_interval_hours=6,
    )
    config = KarbotConfig()
    config.regulatory_intelligence = ri
    return config


def _make_claude_mock(urgency: int = 3, **extra) -> AsyncMock:
    """Return a mock AsyncAnthropic client that returns a fixed urgency response."""
    response_data = {
        "urgency": urgency,
        "urgency_reasoning": "Test reasoning",
        "summary": f"Test summary (urgency {urgency})",
        "affected": "unclear",
        "recommended_action": "Test recommendation",
        "karbot_specific_notes": "none",
        **extra,
    }
    mock_content = MagicMock()
    mock_content.text = json.dumps(response_data)

    mock_response = MagicMock()
    mock_response.content = [mock_content]

    mock_messages = AsyncMock()
    mock_messages.create = AsyncMock(return_value=mock_response)

    mock_client = MagicMock()
    mock_client.messages = mock_messages

    return mock_client


def _matching_item(n: int = 0) -> dict:
    """Item that passes the keyword pre-filter."""
    return {
        "title": f"CFTC enforcement action on prediction market {n}",
        "url": f"https://cftc.gov/item/{n}",
        "summary": "New CFTC enforcement action regarding prediction market contracts.",
    }


def _non_matching_item() -> dict:
    """Item that does NOT pass the keyword pre-filter."""
    return {
        "title": "Federal Reserve interest rate decision",
        "url": "https://federalreserve.gov/item/1",
        "summary": "The Federal Reserve announced its latest interest rate decision.",
    }


async def _run_bus_briefly(bus: EventBus, seconds: float = 0.15) -> None:
    """Run the bus for a short time then cancel."""
    task = asyncio.create_task(bus.run())
    await asyncio.sleep(seconds)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_keyword_prefilter_gates_non_matching():
    """Item with no keyword match never reaches the Claude API mock."""
    bus = EventBus()
    config = _make_config()
    mock_claude = _make_claude_mock(urgency=1)

    agent = RegulatoryIntelligenceAgentImpl(bus=bus, config=config, claude_client=mock_claude)
    agent.register_subscriptions()

    # Run one cycle with a non-matching item
    await agent._run_cycle_with_items([_non_matching_item()], weekly_sweep=False)

    mock_claude.messages.create.assert_not_called()


@pytest.mark.asyncio
async def test_overflow_queue_holds_excess_items():
    """Items beyond per-cycle cap go to overflow queue, not dropped."""
    bus = EventBus()
    config = _make_config(regulatory_ai_calls_per_cycle=2)
    mock_claude = _make_claude_mock(urgency=1)

    agent = RegulatoryIntelligenceAgentImpl(bus=bus, config=config, claude_client=mock_claude)
    agent.register_subscriptions()

    items = [_matching_item(i) for i in range(5)]

    async with asyncio.timeout(5):
        task = asyncio.create_task(bus.run())
        await agent._run_cycle_with_items(items, weekly_sweep=False)
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Only 2 calls (cap), 3 in overflow
    assert mock_claude.messages.create.call_count == 2
    assert agent._overflow_queue.qsize() == 3


@pytest.mark.asyncio
async def test_urgency_1_2_no_telegram():
    """Urgency 1-2 items do not publish TelegramNotificationEvent."""
    bus = EventBus()
    config = _make_config()
    mock_claude = _make_claude_mock(urgency=2)

    agent = RegulatoryIntelligenceAgentImpl(bus=bus, config=config, claude_client=mock_claude)
    agent.register_subscriptions()

    telegram_events = []
    bus.subscribe(TelegramNotificationEvent, lambda e: telegram_events.append(e))

    async with asyncio.timeout(5):
        task = asyncio.create_task(bus.run())
        await agent._run_cycle_with_items([_matching_item()], weekly_sweep=False)
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert len(telegram_events) == 0
    assert agent._regulatory_pause is False


@pytest.mark.asyncio
async def test_urgency_3_publishes_telegram_no_pause():
    """Urgency 3 publishes TelegramNotificationEvent; _regulatory_pause stays False."""
    bus = EventBus()
    config = _make_config()
    mock_claude = _make_claude_mock(urgency=3)

    agent = RegulatoryIntelligenceAgentImpl(bus=bus, config=config, claude_client=mock_claude)
    agent.register_subscriptions()

    telegram_events = []
    bus.subscribe(TelegramNotificationEvent, lambda e: telegram_events.append(e))

    async with asyncio.timeout(5):
        task = asyncio.create_task(bus.run())
        await agent._run_cycle_with_items([_matching_item()], weekly_sweep=False)
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert len(telegram_events) == 1
    assert agent._regulatory_pause is False


@pytest.mark.asyncio
async def test_urgency_5_pauses_risk_gate():
    """
    Urgency 5 → RegulatoryAlertEvent(urgency=5) published, _regulatory_pause = True,
    Risk Gate rejects next opportunity with reason=REGULATORY_PAUSE.
    """
    bus = EventBus()
    config = _make_config()
    config.system.paper_mode = True
    mock_claude = _make_claude_mock(urgency=5)

    agent = RegulatoryIntelligenceAgentImpl(bus=bus, config=config, claude_client=mock_claude)
    agent.register_subscriptions()

    risk_gate = RiskGate(bus=bus, config=config)
    risk_gate.register_subscriptions()

    regulatory_alerts = []
    rejected_events = []
    bus.subscribe(RegulatoryAlertEvent, lambda e: regulatory_alerts.append(e))
    from core.events import RejectedOpportunityEvent
    bus.subscribe(RejectedOpportunityEvent, lambda e: rejected_events.append(e))

    async with asyncio.timeout(5):
        task = asyncio.create_task(bus.run())

        # Trigger urgency-5 alert
        await agent._run_cycle_with_items([_matching_item()], weekly_sweep=False)
        await asyncio.sleep(0.1)

        # Now publish an opportunity — Risk Gate must reject it
        snapshot = PositionSnapshot(
            total_capital_usd=10_000,
            deployed_capital_usd=0,
            free_capital_usd=10_000,
        )
        await bus.publish(snapshot)
        await asyncio.sleep(0.05)

        opp = OpportunityEvent(
            strategy="S1_REBALANCING",
            net_profit_pct=5.0,
            capital_required_usd=100,
        )
        await bus.publish(opp)
        await asyncio.sleep(0.1)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert agent._regulatory_pause is True
    assert any(e.urgency == 5 for e in regulatory_alerts)
    assert any(e.reason == "REGULATORY_PAUSE" for e in rejected_events)


@pytest.mark.asyncio
async def test_operator_clear_resumes_risk_gate():
    """
    Correct clear phrase → _regulatory_pause cleared → RegulatoryAlertEvent(urgency=0)
    published → Risk Gate resumes approvals.
    """
    bus = EventBus()
    config = _make_config()
    config.system.paper_mode = True

    agent = RegulatoryIntelligenceAgentImpl(bus=bus, config=config)
    agent._regulatory_pause = True  # Pre-set the pause state
    agent.register_subscriptions()

    risk_gate = RiskGate(bus=bus, config=config)
    risk_gate.register_subscriptions()

    regulatory_alerts = []
    from core.events import ApprovedOpportunityEvent, RejectedOpportunityEvent
    approved_events = []
    rejected_events = []
    bus.subscribe(RegulatoryAlertEvent, lambda e: regulatory_alerts.append(e))
    bus.subscribe(ApprovedOpportunityEvent, lambda e: approved_events.append(e))
    bus.subscribe(RejectedOpportunityEvent, lambda e: rejected_events.append(e))

    async with asyncio.timeout(5):
        task = asyncio.create_task(bus.run())

        # Pre-seed RiskGate with a position snapshot
        await bus.publish(PositionSnapshot(
            total_capital_usd=10_000,
            deployed_capital_usd=0,
            free_capital_usd=10_000,
        ))
        # Also seed RiskGate's _regulatory_pause via its subscription
        await bus.publish(RegulatoryAlertEvent(
            source="test",
            urgency=5,
            summary="test pause",
        ))
        await asyncio.sleep(0.05)

        # Send the correct clear phrase
        await bus.publish(TelegramPermissionResponseEvent(
            request_id="",
            approved=False,
            source="operator",
            response_text="CLEAR REGULATORY HOLD",
        ))
        await asyncio.sleep(0.1)

        # Now an opportunity should be approved (not REGULATORY_PAUSE rejected)
        opp = OpportunityEvent(
            strategy="S1_REBALANCING",
            net_profit_pct=5.0,
            capital_required_usd=100,
        )
        await bus.publish(opp)
        await asyncio.sleep(0.1)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert agent._regulatory_pause is False
    assert any(e.urgency == 0 for e in regulatory_alerts)
    # Opportunity should NOT be rejected for REGULATORY_PAUSE after clear
    assert not any(e.reason == "REGULATORY_PAUSE" for e in rejected_events)


@pytest.mark.asyncio
async def test_deduplication_same_url_once():
    """Same URL submitted twice → Claude called only once."""
    bus = EventBus()
    config = _make_config()
    mock_claude = _make_claude_mock(urgency=1)

    agent = RegulatoryIntelligenceAgentImpl(bus=bus, config=config, claude_client=mock_claude)
    agent.register_subscriptions()

    item = _matching_item()

    async with asyncio.timeout(5):
        task = asyncio.create_task(bus.run())
        # First cycle
        await agent._run_cycle_with_items([item], weekly_sweep=False)
        await asyncio.sleep(0.05)
        # Second cycle — same URL, should be deduped
        await agent._run_cycle_with_items([item], weekly_sweep=False)
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert mock_claude.messages.create.call_count == 1


@pytest.mark.asyncio
async def test_daily_cap_blocks_calls_and_alerts():
    """After daily cap is reached, further calls are blocked and Telegram alert sent."""
    bus = EventBus()
    config = _make_config(regulatory_ai_daily_cap=2)
    mock_claude = _make_claude_mock(urgency=1)

    agent = RegulatoryIntelligenceAgentImpl(bus=bus, config=config, claude_client=mock_claude)
    agent.register_subscriptions()

    telegram_events = []
    bus.subscribe(TelegramNotificationEvent, lambda e: telegram_events.append(e))

    async with asyncio.timeout(5):
        task = asyncio.create_task(bus.run())

        # First cycle: 2 items → hits the cap
        items = [_matching_item(i) for i in range(3)]
        await agent._run_cycle_with_items(items, weekly_sweep=False)
        await asyncio.sleep(0.1)

        # Second cycle: fresh items → cap already reached
        more_items = [_matching_item(i + 10) for i in range(2)]
        await agent._run_cycle_with_items(more_items, weekly_sweep=False)
        await asyncio.sleep(0.1)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Cap-hit Telegram warning must have been published
    cap_warnings = [e for e in telegram_events if "daily cap" in e.message.lower()]
    assert len(cap_warnings) >= 1
    # Total API calls must not exceed the cap
    assert mock_claude.messages.create.call_count <= 2


@pytest.mark.asyncio
async def test_circuit_breaker_trips_and_blocks():
    """N calls within the window → circuit breaker trips, Telegram alert, no more calls."""
    bus = EventBus()
    config = _make_config(
        regulatory_circuit_breaker_calls=3,
        regulatory_circuit_breaker_window_minutes=10,
        regulatory_ai_calls_per_cycle=10,
        regulatory_ai_daily_cap=100,
    )
    mock_claude = _make_claude_mock(urgency=1)

    agent = RegulatoryIntelligenceAgentImpl(bus=bus, config=config, claude_client=mock_claude)
    agent.register_subscriptions()

    telegram_events = []
    bus.subscribe(TelegramNotificationEvent, lambda e: telegram_events.append(e))

    async with asyncio.timeout(5):
        task = asyncio.create_task(bus.run())

        # 3 calls trips the breaker (threshold == 3 already in window)
        items = [_matching_item(i) for i in range(5)]
        await agent._run_cycle_with_items(items, weekly_sweep=False)
        await asyncio.sleep(0.1)

        # Breaker is tripped; this cycle should make zero new calls
        more_items = [_matching_item(i + 10) for i in range(3)]
        await agent._run_cycle_with_items(more_items, weekly_sweep=False)
        await asyncio.sleep(0.1)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert agent._circuit_breaker_tripped is True
    breaker_alerts = [e for e in telegram_events if "circuit breaker" in e.message.lower()]
    assert len(breaker_alerts) >= 1
    # After breaker trips no new calls are made
    assert mock_claude.messages.create.call_count == 3


@pytest.mark.asyncio
async def test_compliance_officer_logs_regulatory_alert(tmp_path):
    """Urgency 5 RegulatoryAlertEvent → entry in compliance_actions.jsonl."""
    bus = EventBus()
    config = _make_config()

    # Redirect log files to tmp_path
    import agents.management.compliance as compliance_module
    original_logs_dir = compliance_module.LOGS_DIR
    compliance_module.LOGS_DIR = tmp_path

    try:
        officer = ComplianceOfficer(bus=bus, config=config)
        officer.register_subscriptions()

        async with asyncio.timeout(5):
            task = asyncio.create_task(bus.run())

            await bus.publish(RegulatoryAlertEvent(
                source="regulatory_intelligence",
                urgency=5,
                summary="Critical CFTC enforcement action against prediction market.",
                source_url="https://cftc.gov/test",
                affected="yes",
                recommended_action="Halt trading immediately.",
                raw_title="CFTC v. Test",
                cycle_type="6h",
            ))
            await asyncio.sleep(0.1)

            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        compliance_actions = tmp_path / "compliance_actions.jsonl"
        assert compliance_actions.exists()
        lines = compliance_actions.read_text().strip().splitlines()
        # Filter to REGULATORY_ALERT entries only (SYSTEM_STARTUP is also there)
        alerts = [
            json.loads(line)
            for line in lines
            if json.loads(line).get("action_type") == "REGULATORY_ALERT"
        ]
        assert len(alerts) == 1
        assert alerts[0]["details"]["urgency"] == 5

    finally:
        compliance_module.LOGS_DIR = original_logs_dir


@pytest.mark.asyncio
async def test_bad_api_response_defaults_to_urgency_3():
    """Malformed JSON from Claude API → urgency defaults to 3, no crash."""
    bus = EventBus()
    config = _make_config()

    # Claude returns unparseable text
    mock_content = MagicMock()
    mock_content.text = "This is not JSON at all { broken"
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_messages = AsyncMock()
    mock_messages.create = AsyncMock(return_value=mock_response)
    mock_client = MagicMock()
    mock_client.messages = mock_messages

    agent = RegulatoryIntelligenceAgentImpl(bus=bus, config=config, claude_client=mock_client)
    agent.register_subscriptions()

    regulatory_alerts = []
    bus.subscribe(RegulatoryAlertEvent, lambda e: regulatory_alerts.append(e))

    async with asyncio.timeout(5):
        task = asyncio.create_task(bus.run())
        await agent._run_cycle_with_items([_matching_item()], weekly_sweep=False)
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert len(regulatory_alerts) == 1
    assert regulatory_alerts[0].urgency == 3
