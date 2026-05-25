"""
agents/management/compliance.py
────────────────────────────────
Compliance Officer Agent — Management Layer

Always-on, cannot be disabled. Monitors all trade events for regulatory
compliance. Phase 1 stub — subscriptions wired in next session.

Phase 1: stub that satisfies karbot_runner.py BaseAgent interface.
Phase 2: wire to TradeExecutedEvent, IRS dual-track logging.
"""

import asyncio
import logging

from karbot.core.config import KarbotConfig
from karbot.core.events import EventBus

log = logging.getLogger(__name__)


class ComplianceOfficer:
    """
    Management Agent — always-on compliance monitor.
    Cannot be disabled. Stub for Phase 1.
    """

    AGENT_NAME = "compliance_officer"

    def __init__(self, bus: EventBus, config: KarbotConfig):
        self.bus = bus
        self.config = config

    def register_subscriptions(self):
        pass

    async def run(self):
        log.info("ComplianceOfficer stub running (not yet implemented)")
        while True:
            await asyncio.sleep(60)
