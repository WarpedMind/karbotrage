"""
karbot_runner.py — Karbot Rage! Agent Runner
WallStRobotics | Phase 1: Kalshi-only, paper trading

This is the NEW entry point. It starts all agents as concurrent asyncio tasks
and lets the event bus drive the system. It does NOT call agents directly.

Legacy path: main.py + execution/engine.py (intentionally deferred)
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

# Core infrastructure
from core.events import EventBus
from karbot.core.config import KarbotConfig

# Phase 1 agents — Trading Floor
from agents.floor.price_watcher import PriceWatcher
from agents.floor.arb_scanner import ArbScanner
from agents.floor.risk_gate import RiskGate

# Phase 1 agents — Research Floor
from agents.research.market_analyst import MarketAnalyst

# Phase 1 agents — Management (always-on)
from agents.management.reflection import ReflectionAgent
from agents.management.compliance import ComplianceOfficer

# Phase 2 agents — DO NOT instantiate yet
# from agents.floor.execution_agent import ExecutionAgent
# from agents.floor.position_tracker import PositionTracker
# from agents.research.news_analyst import NewsAnalyst
# from agents.research.sentiment_agent import SentimentAgent
# from agents.research.geopolitical_agent import GeopoliticalAgent
# from agents.research.options_signal import OptionsSignalAgent
# from agents.research.whale_tracker import WhaleTracker
# from agents.research.resolution_verifier import ResolutionVerifier
# from agents.management.portfolio_manager import PortfolioManager

logger = logging.getLogger(__name__)


async def run():
    """
    Start all Phase 1 agents and run until shutdown signal.
    Agents communicate exclusively via the event bus.
    This function does not orchestrate agent logic — agents self-manage.
    """

    # --- 1. Load config ---
    config = KarbotConfig.from_yaml("config.yaml")
    logger.info(f"Config loaded | phase={config.phase} | paper_mode={config.paper_mode}")

    # Phase 1 hard guard — belt and suspenders beyond KarbotConfig.__init__
    if not config.paper_mode:
        logger.warning("LIVE MODE DETECTED — paper trading must run successfully first")
        # Do not raise here — KarbotConfig already enforces phase invariants.
        # This log is for operator awareness only.

    # --- 2. Instantiate event bus ---
    bus = EventBus()
    logger.info("Event bus initialized")

    # --- 3. Instantiate Phase 1 agents ---
    agents = [
        PriceWatcher(bus=bus, config=config),
        ArbScanner(bus=bus, config=config),
        RiskGate(bus=bus, config=config),
        MarketAnalyst(bus=bus, config=config),
        ReflectionAgent(bus=bus, config=config),
        ComplianceOfficer(bus=bus, config=config),   # always-on, cannot be disabled
    ]

    # --- 4. Register each agent's event subscriptions ---
    for agent in agents:
        agent.register_subscriptions()
        logger.info(f"Registered: {agent.__class__.__name__}")

    # --- 5. Start all agents as concurrent asyncio tasks ---
    tasks = []
    for agent in agents:
        task = asyncio.create_task(agent.run(), name=agent.__class__.__name__)
        tasks.append(task)
        logger.info(f"Started task: {agent.__class__.__name__}")

    logger.info(f"Karbot Rage! running | {len(tasks)} agents active | Phase {config.phase}")

    # --- 6. Wait for all tasks (runs indefinitely until shutdown) ---
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Shutdown signal received — cancelling agent tasks")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
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

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Register signal handlers for clean shutdown
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(shutdown(loop))
        )

    try:
        loop.run_until_complete(run())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt — shutting down")
    finally:
        loop.close()
        logger.info("Karbot Rage! stopped")
