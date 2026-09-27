"""
tests/test_health_monitor.py — HealthMonitorAgentImpl

Closes the KNOWN DEBT item: AgentHeartbeat events were dead-lettered with no
subscriber. Covers:
- Learning an agent's cadence dynamically from real AgentHeartbeat traffic
  (never alerting on an agent that has never published one).
- A single silence alert per continuous outage, not one per check tick.
- A distinct recovery alert when heartbeats resume, and no further alerts
  after that.

Per project convention (see feedback: test event-driven agents via
register_subscriptions() + direct event/method calls, never agent.run()),
`_check_silence(now)` is called directly with an injected monotonic time
instead of sleeping through `_monitor_loop`.
"""

import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from karbot.core.events import EventBus, AgentHeartbeat
from karbot.core.config import KarbotConfig
from agents.monitoring.health_monitor import HealthMonitorAgentImpl


def _make_agent(silence_threshold=180) -> HealthMonitorAgentImpl:
    config = KarbotConfig()
    config.system.health_monitor_silence_threshold_seconds = silence_threshold
    bus = EventBus()
    agent = HealthMonitorAgentImpl(bus=bus, config=config)
    agent.register_subscriptions()
    agent.bus.publish = AsyncMock(wraps=agent.bus.publish)
    return agent


@pytest.mark.asyncio
async def test_never_alerts_on_an_agent_it_has_never_seen():
    agent = _make_agent()
    await agent._check_silence(now=time.monotonic() + 10_000)
    assert agent.bus.publish.call_count == 0


@pytest.mark.asyncio
async def test_silence_past_threshold_triggers_exactly_one_alert():
    agent = _make_agent(silence_threshold=180)
    t0 = time.monotonic()
    await agent._on_heartbeat(AgentHeartbeat(source="risk_gate", agent_name="risk_gate"))

    # Well within threshold — no alert.
    await agent._check_silence(now=t0 + 60)
    assert agent.bus.publish.call_count == 0

    # Past threshold — one alert.
    await agent._check_silence(now=t0 + 200)
    assert agent.bus.publish.call_count == 1
    event = agent.bus.publish.call_args_list[0].args[0]
    assert event.tier == 1
    assert "AGENT SILENT" in event.message
    assert "risk_gate" in event.message

    # Repeated checks during the same continuous outage — no more alerts.
    await agent._check_silence(now=t0 + 300)
    await agent._check_silence(now=t0 + 400)
    assert agent.bus.publish.call_count == 1


@pytest.mark.asyncio
async def test_recovery_triggers_distinct_alert_then_stops():
    agent = _make_agent(silence_threshold=180)
    t0 = time.monotonic()
    await agent._on_heartbeat(AgentHeartbeat(source="risk_gate", agent_name="risk_gate"))
    await agent._check_silence(now=t0 + 200)
    assert agent.bus.publish.call_count == 1

    # Heartbeat resumes — distinct recovery alert.
    await agent._on_heartbeat(AgentHeartbeat(source="risk_gate", agent_name="risk_gate"))
    assert agent.bus.publish.call_count == 2
    recovered = agent.bus.publish.call_args_list[1].args[0]
    assert "AGENT RECOVERED" in recovered.message
    assert "AGENT SILENT" not in recovered.message

    # Further heartbeats while healthy — no more alerts.
    await agent._on_heartbeat(AgentHeartbeat(source="risk_gate", agent_name="risk_gate"))
    assert agent.bus.publish.call_count == 2


@pytest.mark.asyncio
async def test_two_agents_tracked_independently():
    """Seed _last_seen directly (rather than via _on_heartbeat, which stamps
    real time.monotonic() internally) so the two agents' ages can differ
    deterministically under one injected `now`."""
    agent = _make_agent(silence_threshold=180)
    now = time.monotonic()
    agent._last_seen["risk_gate"] = now - 200      # stale — past the 180s threshold
    agent._last_seen["price_watcher"] = now - 10   # fresh — well within it

    await agent._check_silence(now=now)

    assert agent.bus.publish.call_count == 1
    event = agent.bus.publish.call_args_list[0].args[0]
    assert "risk_gate" in event.message
    assert "price_watcher" not in event.message


@pytest.mark.asyncio
async def test_falls_back_to_source_when_agent_name_blank():
    """AgentHeartbeat.agent_name is expected to be set, but if a caller only
    sets the base Event.source field, tracking should still work."""
    agent = _make_agent()
    await agent._on_heartbeat(AgentHeartbeat(source="market_analyst", agent_name=""))
    assert "market_analyst" in agent._last_seen
