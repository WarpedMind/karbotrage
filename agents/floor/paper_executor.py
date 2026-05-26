"""
agents/floor/paper_executor.py — Paper Trading Executor
Karbot Rage! | WallStRobotics | Phase 1

Thin BaseAgent that closes the paper trading loop.
Only active when config.paper_mode is True.

Subscribes to:  ApprovedOpportunityEvent
Publishes:      TradeExecutedEvent (paper_mode=True)

No API calls. No state. Assumes full fill at opportunity leg prices.
"""

import asyncio
import uuid
from datetime import datetime, timezone

import structlog

from karbot.core.config import KarbotConfig
from karbot.core.events import (
    EventBus,
    ApprovedOpportunityEvent,
    TradeExecutedEvent,
    TradeResolvedEvent,
    Priority,
)

log = structlog.get_logger(__name__)


class PaperExecutor:
    """
    Paper trading executor — closes the arb loop without touching real APIs.

    Activated only in paper mode. On every ApprovedOpportunityEvent, simulates
    an immediate full fill at the opportunity's leg prices and emits a
    TradeExecutedEvent for the ComplianceOfficer to record.
    """

    AGENT_NAME = "paper_executor"

    def __init__(self, bus: EventBus, config: KarbotConfig):
        self.bus = bus
        self.config = config

    def register_subscriptions(self) -> None:
        if self.config.paper_mode:
            self.bus.subscribe(ApprovedOpportunityEvent, self._on_approved)
            log.info("paper_executor_subscribed", paper_mode=True)
        else:
            log.info("paper_executor_inactive", reason="not paper_mode")

    async def run(self) -> None:
        log.info("PaperExecutor running", paper_mode=self.config.paper_mode)
        while True:
            await asyncio.sleep(60)

    async def _on_approved(self, event: ApprovedOpportunityEvent) -> None:
        if not self.config.paper_mode:
            return

        opp = event.opportunity
        if opp is None:
            log.warning("paper_executor_null_opportunity", event_id=event.event_id)
            return

        now = datetime.now(timezone.utc).isoformat()

        filled_legs = []
        for leg in opp.legs:
            filled_legs.append({
                "platform":      leg.get("platform", "kalshi"),
                "market_id":     leg.get("market_id", ""),
                "side":          leg.get("side", ""),
                "ordered_price": leg.get("price", 0.0),
                "filled_price":  leg.get("price", 0.0),  # paper: assume full fill
                "quantity":      event.approved_size,
                "fee_paid":      leg.get("fee_estimate", 0.0) * event.approved_size,
                "fill_time":     now,
            })

        total_fees = sum(l["fee_paid"] for l in filled_legs)
        expected_pnl = (opp.net_profit_pct / 100) * event.approved_size

        trade_event = TradeExecutedEvent(
            source           = self.AGENT_NAME,
            priority         = Priority.HIGH,
            trade_id         = str(uuid.uuid4()),
            opportunity_id   = opp.opportunity_id,
            strategy         = opp.strategy,
            platform_legs    = filled_legs,
            total_fee_paid   = total_fees,
            expected_pnl_usd = expected_pnl,
            paper_mode       = True,
        )

        log.info(
            "paper_trade_executed",
            strategy=opp.strategy,
            approved_size=event.approved_size,
            expected_pnl=round(expected_pnl, 4),
            legs=len(filled_legs),
        )

        await self.bus.publish(trade_event)

        # Schedule paper resolution after the configured delay
        delay = self.config.system.paper_resolution_delay_seconds
        first_market_id = opp.legs[0].get("market_id", "") if opp.legs else ""
        trade_id = trade_event.trade_id

        async def _resolve():
            await asyncio.sleep(delay)
            resolved = TradeResolvedEvent(
                source=self.AGENT_NAME,
                priority=Priority.HIGH,
                trade_id=trade_id,
                market_id=first_market_id,
                platform="kalshi",
                resolution="YES",
                realized_pnl=expected_pnl,
                holding_period_hours=delay / 3600,
            )
            await self.bus.publish(resolved)
            log.info(
                "paper_trade_resolved",
                trade_id=trade_id,
                realized_pnl=round(expected_pnl, 4),
                delay_seconds=delay,
            )

        asyncio.create_task(_resolve(), name=f"paper_resolve_{trade_id[:8]}")
