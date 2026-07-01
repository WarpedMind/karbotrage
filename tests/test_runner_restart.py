"""
tests/test_runner_restart.py — karbot_runner._run_supervised_with_restart

Covers the Session 20 feature: capped automatic restart of a supervised
agent task at the runner level, independent of any operator response.

- A crashed agent restarts after a fixed delay, up to `restart_max_count`
  restarts within a rolling `restart_window_minutes` window.
- Exceeding the budget stops auto-restart permanently and publishes a
  CRITICAL Telegram alert via the event bus (distinct wording from the
  Tier 1 feed-down alert) rather than a direct call into the Telegram agent.
- This restart logic is general (keyed on a coro_factory + agent_name, not
  PriceWatcher-specific) but is only wired to PriceWatcher in karbot_runner.py.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.events import EventBus, TelegramNotificationEvent
from karbot_runner import _run_supervised_with_restart


class _CrashingAgent:
    """Test double whose run() crashes a fixed number of times, then hangs
    forever (simulating a healthy long-running agent) if restarts remain."""

    def __init__(self, crash_count: int):
        self.crash_count = crash_count
        self.call_count = 0

    async def run(self):
        self.call_count += 1
        if self.call_count <= self.crash_count:
            raise RuntimeError(f"simulated crash #{self.call_count}")
        # Simulate a healthy long-running agent after crashes stop.
        await asyncio.sleep(3600)


async def _collect_published(bus: EventBus, published: list) -> None:
    """Capture every published event without needing a running bus.run() loop."""
    original_publish = bus.publish

    async def _wrapper(event):
        published.append(event)
        await original_publish(event)

    bus.publish = _wrapper


@pytest.mark.asyncio
async def test_four_crashes_in_window_suppresses_fourth_restart_and_alerts():
    """4 crashes within the rolling window → 4th restart suppressed, CRITICAL alert fires."""
    bus = EventBus()
    published = []
    await _collect_published(bus, published)

    agent = _CrashingAgent(crash_count=4)

    task = asyncio.create_task(
        _run_supervised_with_restart(
            "TestAgent",
            agent.run,
            bus,
            restart_delay_seconds=0,
            restart_max_count=3,
            restart_window_minutes=60,
        )
    )
    await asyncio.wait_for(task, timeout=5.0)

    # 4 crash attempts total (call_count), but only 3 restarts permitted —
    # the 4th crash trips the budget and the function returns without a
    # 4th restart attempt being scheduled.
    assert agent.call_count == 4

    critical_alerts = [
        e for e in published
        if isinstance(e, TelegramNotificationEvent) and e.tier == 1
    ]
    assert len(critical_alerts) == 1
    assert "AUTO-RECOVERY EXHAUSTED" in critical_alerts[0].message
    assert "TestAgent" in critical_alerts[0].message


@pytest.mark.asyncio
async def test_two_crashes_in_window_restart_normally_no_critical_alert():
    """2 crashes within the window → both restart normally, no CRITICAL alert."""
    bus = EventBus()
    published = []
    await _collect_published(bus, published)

    agent = _CrashingAgent(crash_count=2)

    task = asyncio.create_task(
        _run_supervised_with_restart(
            "TestAgent",
            agent.run,
            bus,
            restart_delay_seconds=0,
            restart_max_count=3,
            restart_window_minutes=60,
        )
    )

    # After 2 crashes + 2 restarts, the 3rd call to run() hangs (simulated
    # healthy agent) — cancel the supervising task once we've observed that.
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert agent.call_count == 3  # 2 crashes + 1 successful (long-running) start

    critical_alerts = [
        e for e in published
        if isinstance(e, TelegramNotificationEvent) and e.tier == 1
    ]
    assert len(critical_alerts) == 0


@pytest.mark.asyncio
async def test_restart_uses_configured_delay():
    """The restart_delay_seconds value is actually awaited between attempts."""
    from unittest.mock import patch

    bus = EventBus()
    agent = _CrashingAgent(crash_count=1)

    sleep_calls = []
    real_sleep = asyncio.sleep

    async def _tracking_sleep(seconds):
        sleep_calls.append(seconds)
        if seconds != 30:
            await real_sleep(seconds)   # let the test's own waits behave normally

    with patch("karbot_runner.asyncio.sleep", side_effect=_tracking_sleep):
        task = asyncio.create_task(
            _run_supervised_with_restart(
                "TestAgent",
                agent.run,
                bus,
                restart_delay_seconds=30,
                restart_max_count=3,
                restart_window_minutes=60,
            )
        )
        await real_sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert 30 in sleep_calls
