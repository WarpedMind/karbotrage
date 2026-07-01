"""
karbot_runner.py — Karbot Rage! Agent Runner
WallStRobotics | Phase 1: Kalshi-only, paper trading

This is the NEW entry point. It starts all agents as concurrent asyncio tasks
and lets the event bus drive the system. It does NOT call agents directly.

Legacy path: main.py + execution/engine.py (intentionally deferred)

CLI flags:
  --mode paper|live          Override trading mode (default: from config.yaml)
  --mock-prices <path>       Swap in MockPriceWatcher + PaperExecutor for
                             end-to-end paper trading tests
  --exit-after-test          Exit cleanly after MockPriceWatcher signals done
                             (2-second settling delay for in-flight events)
"""

from dotenv import load_dotenv
load_dotenv()  # loads .env if present; no-op if absent; real env vars always win

import argparse
import asyncio
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Callable, Awaitable, List

# Core infrastructure
from core.events import EventBus, TelegramNotificationEvent
from karbot.core.config import KarbotConfig

# Phase 1 agents — Trading Floor
from agents.floor.price_watcher import PriceWatcher
from agents.floor.arb_scanner import ArbScanner
from agents.floor.risk_gate import RiskGate
from agents.floor.position_tracker import PositionTracker
from agents.floor.paper_executor import PaperExecutor

# Phase 1 agents — Research Floor
from agents.research.market_analyst import MarketAnalyst
from agents.research.regulatory_intelligence import RegulatoryIntelligenceAgent

# Phase 1 agents — Management (always-on)
from agents.management.reflection import ReflectionAgent
from agents.management.compliance import ComplianceOfficer

# Notification layer — last in roster; all other agents must be running first
from agents.notifications.telegram_agent import TelegramAgent

# Phase 2 agents — DO NOT instantiate yet
# from agents.floor.execution_agent import ExecutionAgent
# from agents.research.news_analyst import NewsAnalyst
# from agents.research.sentiment_agent import SentimentAgent
# from agents.research.geopolitical_agent import GeopoliticalAgent
# from agents.research.options_signal import OptionsSignalAgent
# from agents.research.whale_tracker import WhaleTracker
# from agents.research.resolution_verifier import ResolutionVerifier
# from agents.management.portfolio_manager import PortfolioManager

logger = logging.getLogger(__name__)


async def _run_supervised(agent_name: str, coro) -> None:
    """Run an agent coroutine; catch and log crashes so other agents keep running."""
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(f"Agent {agent_name!r} crashed — runner continues with remaining agents")


async def _run_supervised_with_restart(
    agent_name: str,
    coro_factory: Callable[[], Awaitable],
    bus: EventBus,
    restart_delay_seconds: int,
    restart_max_count: int,
    restart_window_minutes: int,
) -> None:
    """
    Run an agent, restarting it after a fixed delay if it crashes, up to a
    capped number of restarts within a rolling time window.

    Unlike `_run_supervised` (fire-once, log-and-continue), this keeps
    relaunching `coro_factory()` after each non-cancellation crash. If more
    than `restart_max_count` restarts occur within any rolling
    `restart_window_minutes` window, auto-restart stops permanently for this
    agent and a CRITICAL Telegram alert is published via the event bus
    (never a direct call into the Telegram agent — event-bus architecture
    is canonical).

    General enough to reuse for other supervised agents in the future, but
    wired only to PriceWatcher in this session per explicit instruction.

    `coro_factory` must return a fresh awaitable on each call — a coroutine
    object can only be awaited once, so `agent.run()` (not `agent.run`) must
    be called anew for every restart attempt.
    """
    restart_timestamps: List[float] = []

    while True:
        try:
            await coro_factory()
            # run() returned normally (should not happen for long-running
            # agents, but treat it the same as a crash: nothing left to
            # supervise otherwise).
            logger.warning(f"Agent {agent_name!r} exited without error — not restarting")
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(f"Agent {agent_name!r} crashed")

        now = time.monotonic()
        window_seconds = restart_window_minutes * 60
        restart_timestamps = [t for t in restart_timestamps if now - t < window_seconds]

        if len(restart_timestamps) >= restart_max_count:
            logger.error(
                f"Agent {agent_name!r} exceeded {restart_max_count} restarts "
                f"within {restart_window_minutes} minutes — auto-recovery exhausted, "
                f"agent left stopped, manual intervention required"
            )
            await bus.publish(TelegramNotificationEvent(
                message=(
                    f"AUTO-RECOVERY EXHAUSTED for {agent_name}\n"
                    f"{restart_max_count}+ restarts within {restart_window_minutes} "
                    f"minutes — agent stopped, manual intervention required."
                ),
                tier=1,
                event_source="karbot_runner",
            ))
            return

        restart_timestamps.append(now)
        logger.warning(
            f"Restarting {agent_name!r} in {restart_delay_seconds}s "
            f"(restart {len(restart_timestamps)}/{restart_max_count} in window)"
        )
        await asyncio.sleep(restart_delay_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Karbot Rage! Agent Runner — WallStRobotics"
    )
    parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default=None,
        help="Override trading mode (default: read from config.yaml)",
    )
    parser.add_argument(
        "--mock-prices",
        dest="mock_prices",
        metavar="PATH",
        default=None,
        help="Path to JSON fixture. Swaps in MockPriceWatcher + PaperExecutor.",
    )
    parser.add_argument(
        "--exit-after-test",
        action="store_true",
        default=False,
        help="Exit cleanly after MockPriceWatcher signals done (2s settling delay).",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace = None):
    """
    Start all Phase 1 agents and run until shutdown signal.
    Agents communicate exclusively via the event bus.
    This function does not orchestrate agent logic — agents self-manage.
    """

    if args is None:
        args = argparse.Namespace(mock_prices=None, exit_after_test=False, mode=None)

    # --- 1. Load config ---
    config = KarbotConfig.from_yaml("config.yaml")
    logger.info(f"Config loaded | phase={config.phase} | paper_mode={config.paper_mode}")

    # Log the resolved state of every subsystem enable/disable flag. This is
    # the single place an operator can confirm what's actually active after
    # a restart without grepping source code — added after Telegram alerting
    # (feed-down, restart-exhaustion) went undetected across three live
    # deploys because telegram.enabled defaulted to False and no config.yaml
    # existed on the VPS to override it. A wrong or missing config.yaml is a
    # silent no-op, not an error — this line closes that gap.
    logger.info(
        "config_resolved | "
        f"telegram_enabled={config.telegram.enabled} | "
        f"kalshi_ws_enabled={config.data_feeds.kalshi_ws_enabled} | "
        f"polymarket_ws_enabled={config.data_feeds.polymarket_ws_enabled} | "
        f"regulatory_intelligence_enabled={config.regulatory_intelligence.enabled} | "
        f"paper_mode={config.paper_mode} | "
        f"phase={config.phase}"
    )

    # Phase 1 hard guard — belt and suspenders beyond KarbotConfig.__init__
    if not config.paper_mode:
        logger.warning("LIVE MODE DETECTED — paper trading must run successfully first")
        # Do not raise here — KarbotConfig already enforces phase invariants.
        # This log is for operator awareness only.

    # --- 2. Instantiate event bus ---
    bus = EventBus()
    logger.info("Event bus initialized")

    # --- 3. Instantiate Phase 1 agents ---
    if args.mock_prices:
        from agents.floor.mock_price_watcher import MockPriceWatcher
        mock_watcher = MockPriceWatcher(bus=bus, config=config, fixture_path=args.mock_prices)
        agents = [
            # PositionTracker MUST be first: its run() publishes the startup
            # PositionSnapshot before MockPriceWatcher emits any prices, so
            # Risk Gate has a snapshot when the first OpportunityEvent arrives.
            PositionTracker(bus=bus, config=config),
            mock_watcher,
            ArbScanner(bus=bus, config=config),
            RiskGate(bus=bus, config=config),
            PaperExecutor(bus=bus, config=config),
            # Research Floor
            MarketAnalyst(bus=bus, config=config),
            RegulatoryIntelligenceAgent(bus=bus, config=config),
            # Management (always-on)
            ReflectionAgent(bus=bus, config=config),
            ComplianceOfficer(bus=bus, config=config),
            # TelegramAgent last: notification layer, all other agents subscribe first
            TelegramAgent(bus=bus, config=config),
        ]
        logger.info(f"Mock mode: MockPriceWatcher + PaperExecutor active | fixture={args.mock_prices}")
    else:
        agents = [
            # PositionTracker first: publishes startup PositionSnapshot before
            # PriceWatcher begins emitting market data.
            PositionTracker(bus=bus, config=config),
            PriceWatcher(bus=bus, config=config),
            ArbScanner(bus=bus, config=config),
            RiskGate(bus=bus, config=config),
            # PaperExecutor: simulates fills in paper mode; self-disables in live mode
            PaperExecutor(bus=bus, config=config),
            # Research Floor
            MarketAnalyst(bus=bus, config=config),
            RegulatoryIntelligenceAgent(bus=bus, config=config),
            # Management (always-on)
            ReflectionAgent(bus=bus, config=config),
            ComplianceOfficer(bus=bus, config=config),   # always-on, cannot be disabled
            # TelegramAgent last: notification layer, all other agents subscribe first
            TelegramAgent(bus=bus, config=config),
        ]

    # --- 4. Register each agent's event subscriptions ---
    for agent in agents:
        agent.register_subscriptions()
        logger.info(f"Registered: {agent.__class__.__name__}")

    # --- 5. Start all agents as concurrent asyncio tasks ---
    tasks = []
    for agent in agents:
        agent_name = agent.__class__.__name__
        if isinstance(agent, PriceWatcher):
            # PriceWatcher gets capped auto-restart at the runner level (on top
            # of its own internal tenacity retry) rather than dying permanently
            # once its internal retry budget is exhausted. See KarbotConfig
            # .system.agent_restart_* for the delay/budget/window defaults.
            task = asyncio.create_task(
                _run_supervised_with_restart(
                    agent_name,
                    agent.run,
                    bus,
                    config.system.agent_restart_delay_seconds,
                    config.system.agent_restart_max_count,
                    config.system.agent_restart_window_minutes,
                ),
                name=agent_name,
            )
        else:
            task = asyncio.create_task(
                _run_supervised(agent_name, agent.run()),
                name=agent_name,
            )
        tasks.append(task)
        logger.info(f"Started task: {agent_name}")

    # Always run the bus dispatcher
    bus_task = asyncio.create_task(bus.run(), name="EventBus")

    logger.info(f"Karbot Rage! running | {len(tasks)} agents active | Phase {config.phase}")

    # --- 6. Wait for all tasks (or test completion) ---
    if args.mock_prices and args.exit_after_test:
        # Wait for MockPriceWatcher to finish emitting, then settle and exit
        try:
            await asyncio.wait_for(mock_watcher.done_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("MockPriceWatcher did not complete within 30s timeout")

        logger.info("MockPriceWatcher done — waiting 2s for in-flight events to settle")
        await asyncio.sleep(2.0)

        logger.info("Settling complete — shutting down cleanly")
        bus_task.cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(bus_task, *tasks, return_exceptions=True)
        # Also cancel any background sub-tasks spawned by agents
        # (heartbeat loops, analysis loops, etc.) so the event loop
        # closes cleanly without "Task was destroyed" warnings.
        remaining = [t for t in asyncio.all_tasks()
                     if not t.done() and t is not asyncio.current_task()]
        if remaining:
            for t in remaining:
                t.cancel()
            await asyncio.gather(*remaining, return_exceptions=True)
        logger.info("All agents stopped cleanly (test mode exit)")
        return

    try:
        await asyncio.gather(bus_task, *tasks, return_exceptions=True)
    except asyncio.CancelledError:
        logger.info("Shutdown signal received — cancelling agent tasks")
        bus_task.cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(bus_task, *tasks, return_exceptions=True)
        logger.info("All agents stopped cleanly")


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ]
    )


def handle_shutdown(loop, tasks):
    """Cancel all tasks on SIGINT/SIGTERM."""
    logger.info("Shutdown signal received")
    for task in asyncio.all_tasks(loop):
        task.cancel()


async def shutdown(loop):
    """Graceful shutdown — cancel all running tasks."""
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()


if __name__ == "__main__":
    setup_logging()
    logger.info("Karbot Rage! starting up — WallStRobotics")

    args = parse_args()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Register signal handlers for clean shutdown
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(shutdown(loop))
        )

    try:
        loop.run_until_complete(run(args))
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt — shutting down")
    finally:
        loop.close()
        logger.info("Karbot Rage! stopped")
