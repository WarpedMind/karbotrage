"""
agents/floor/mock_price_watcher.py — Test Fixture Price Watcher
Karbot Rage! | WallStRobotics

Reads a JSON fixture file and emits PriceUpdateEvents onto the bus,
exactly as the real PriceWatcher would. Sets an asyncio.Event when done
so the runner can exit cleanly after the test scenarios are processed.

Only used in test/CI runs via --mock-prices <path>.
"""

import asyncio
import json
from pathlib import Path

import structlog

from karbot.core.config import KarbotConfig
from karbot.core.events import EventBus, PriceUpdateEvent

log = structlog.get_logger(__name__)


class MockPriceWatcher:
    """
    Replay fixture prices onto the event bus.

    Constructor:
        bus          — shared event bus
        config       — KarbotConfig (unused but required by BaseAgent interface)
        fixture_path — path to JSON file containing list of price entries

    Each JSON entry must include:
        platform, market_id, yes_bid, yes_ask, no_bid, no_ask,
        volume_24h, open_interest, sequence_num

    Signals completion via .done_event (asyncio.Event) after all entries
    are published so --exit-after-test can wait on it.
    """

    AGENT_NAME = "mock_price_watcher"

    def __init__(self, bus: EventBus, config: KarbotConfig, fixture_path: str):
        self.bus = bus
        self.config = config
        self.fixture_path = fixture_path
        self.done_event: asyncio.Event = asyncio.Event()

    def register_subscriptions(self) -> None:
        pass  # Publisher only — no subscriptions

    async def run(self) -> None:
        fixture = Path(self.fixture_path)
        if not fixture.exists():
            log.error("mock_fixture_not_found", path=self.fixture_path)
            self.done_event.set()
            return

        with open(fixture) as f:
            entries = json.load(f)

        # Brief pause before emitting prices.  PositionTracker.run() publishes
        # its startup PositionSnapshot synchronously (no await before the first
        # publish call), but the EventBus still needs one event-loop iteration to
        # dispatch it to RiskGate._on_position_snapshot.  Without this delay the
        # first PriceUpdateEvent can arrive at ArbScanner before the snapshot has
        # been dispatched, causing the resulting OpportunityEvent to be rejected
        # with NO_POSITION_DATA.  0.1 s is sufficient — dispatch takes < 1 ms.
        await asyncio.sleep(0.1)

        log.info("mock_price_watcher_starting", total_entries=len(entries))

        for entry in entries:
            yes_ask = float(entry["yes_ask"])
            no_ask  = float(entry["no_ask"])
            event = PriceUpdateEvent(
                source        = self.AGENT_NAME,
                platform      = entry["platform"],
                market_id     = entry["market_id"],
                yes_bid       = float(entry["yes_bid"]),
                yes_ask       = yes_ask,
                no_bid        = float(entry["no_bid"]),
                no_ask        = no_ask,
                volume_24h    = float(entry.get("volume_24h", 0.0)),
                open_interest = int(entry.get("open_interest", 0)),
                sequence_num  = int(entry.get("sequence_num", 0)),
                # Depth defaults to a generous size unless a fixture entry
                # explicitly sets yes_ask_size/no_ask_size to test thin
                # liquidity — S1 requires real depth (see 2026-07-13 fix,
                # DECISIONS.md) and treats missing depth as zero liquidity.
                yes_ask_depth = [(yes_ask, float(entry.get("yes_ask_size", 1000.0)))],
                no_ask_depth  = [(no_ask, float(entry.get("no_ask_size", 1000.0)))],
            )
            await self.bus.publish(event)
            log.info("mock_price_published",
                     market_id=entry["market_id"],
                     yes_bid=event.yes_bid,
                     no_bid=event.no_bid)
            await asyncio.sleep(0.05)  # small gap so the bus can dispatch each event

        log.info("mock_price_watcher_done", entries_published=len(entries))
        self.done_event.set()
