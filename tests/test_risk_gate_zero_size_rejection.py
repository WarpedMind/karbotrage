"""
tests/test_risk_gate_zero_size_rejection.py

Covers a bug live-confirmed 2026-07-13: after all 8 pre-trade checks
passed, _on_opportunity called _calculate_position_size and used the
result directly with no check that it was actually positive. Kelly
criterion returns 0 for edges too thin to size (or the 2026-07-13
liquidity cap can independently zero it out), so a "$0 position" was
still logged as opportunity_approved and published as an
ApprovedOpportunityEvent, which PaperExecutor then executed pointlessly.
Fixed: reject with ZERO_APPROVED_SIZE instead of approving a non-positive
size.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.floor.risk_gate import RiskGateAgent, CheckResult
from core.events import OpportunityEvent, ApprovedOpportunityEvent, RejectedOpportunityEvent
from karbot.core.config import KarbotConfig


def _make_gate_with_all_checks_passing() -> RiskGateAgent:
    config = KarbotConfig()
    gate = RiskGateAgent(config=config, event_bus=MagicMock())
    gate.bus.publish = AsyncMock()
    for check_name in (
        "_check_1_capital", "_check_2_position_size", "_check_3_daily_loss",
        "_check_4_correlation", "_check_5_announcement",
        "_check_6_resolution_criteria", "_check_7_slippage", "_check_8_gas_fee",
    ):
        setattr(gate, check_name, AsyncMock(return_value=CheckResult.ok()))
    return gate


class TestZeroSizeRejection:
    @pytest.mark.asyncio
    async def test_zero_approved_size_is_rejected_not_approved(self):
        gate = _make_gate_with_all_checks_passing()
        gate._calculate_position_size = MagicMock(return_value=0.0)
        event = OpportunityEvent(strategy="S1_REBALANCING", net_profit_pct=1.0)

        await gate._on_opportunity(event)

        published_types = [type(c.args[0]) for c in gate.bus.publish.await_args_list]
        assert ApprovedOpportunityEvent not in published_types
        assert RejectedOpportunityEvent in published_types
        rejected = next(c.args[0] for c in gate.bus.publish.await_args_list
                         if isinstance(c.args[0], RejectedOpportunityEvent))
        assert rejected.reason == "ZERO_APPROVED_SIZE"

    @pytest.mark.asyncio
    async def test_positive_approved_size_still_approves_normally(self):
        gate = _make_gate_with_all_checks_passing()
        gate._calculate_position_size = MagicMock(return_value=250.0)
        event = OpportunityEvent(strategy="S1_REBALANCING", net_profit_pct=10.0)

        await gate._on_opportunity(event)

        published_types = [type(c.args[0]) for c in gate.bus.publish.await_args_list]
        assert ApprovedOpportunityEvent in published_types
        assert RejectedOpportunityEvent not in published_types
        approved = next(c.args[0] for c in gate.bus.publish.await_args_list
                         if isinstance(c.args[0], ApprovedOpportunityEvent))
        assert approved.approved_size == 250.0
