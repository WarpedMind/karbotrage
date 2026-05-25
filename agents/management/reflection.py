"""
karbot/agents/management/reflection.py
────────────────────────────────────────
Reflection Agent — Management Layer

The system's learning engine. Runs nightly at 2:00 AM ET.
Reviews all completed trades, updates source credibility scores,
discovers new correlations, optimizes strategy weights, and
generates human-readable performance narratives.

This is what makes Karbot Rage! improve over time.
Without this agent, the system is static. With it, the system
gets smarter every day — building a proprietary knowledge base
that compounds in value and cannot be replicated without
running the same system for the same duration.

Inspired by:
  - FinMem's layered memory architecture (IEEE 2026)
  - Memento's memory-based continual learning (ArXiv 2025)
  - TradingGroup's self-reflection mechanism (ArXiv 2025)
"""

from __future__ import annotations

import asyncio
import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
import anthropic
import structlog

from karbot.core.config import KarbotConfig
from karbot.core.events import (
    EventBus, StrategyWeightUpdateEvent, CorrelationDiscoveryEvent,
    ComplianceAlertEvent, AgentHeartbeat, Priority
)

log = structlog.get_logger(__name__)


@dataclass
class PerformanceMetrics:
    """Performance metrics for a strategy over a time window."""
    strategy:        str
    period_days:     int
    trade_count:     int   = 0
    win_count:       int   = 0
    total_pnl:       float = 0.0
    avg_net_pct:     float = 0.0
    win_rate:        float = 0.0
    sharpe:          float = 0.0
    max_drawdown:    float = 0.0
    edge_trend:      str   = "STABLE"   # IMPROVING | STABLE | DECLINING


@dataclass
class SourceCredibility:
    """Track record for a specific source on a specific topic."""
    source_id:        str
    topic_category:   str
    correct:          int   = 0
    total:            int   = 0
    scs_score:        float = 0.5
    trend:            str   = "STABLE"


class ReflectionAgentImpl:
    """
    Management Agent — Nightly Learning Engine.

    The 9-step nightly cycle:
    1. Trade review (last 24 hours)
    2. Signal attribution (which signals predicted correctly?)
    3. Persona calibration (accuracy per persona per topic)
    4. Source calibration (SCS updates)
    5. Pattern distillation (extract generalizable rules)
    6. Prompt optimization (flag prompts for tournament)
    7. Anomaly flagging (unexpected events)
    8. Strategy weight update
    9. Performance narrative generation

    Memory architecture:
      - Episodic Memory: every trade in SQLite (permanent)
      - Semantic Memory: distilled patterns in JSON (versioned)
      - Correlation Database: tested signal-outcome relationships
    """

    AGENT_NAME = "reflection_agent"
    HEARTBEAT_INTERVAL = 60
    RUN_HOUR_ET = 2   # 2:00 AM Eastern Time

    def __init__(
        self,
        config: KarbotConfig,
        secrets,
        event_bus: EventBus,
        data_dir: Path,
    ):
        self.config   = config
        self.secrets  = secrets
        self.bus      = event_bus
        self.data_dir = data_dir

        self._client = anthropic.Anthropic(api_key=secrets.anthropic_api_key) \
            if secrets.anthropic_api_key else None

        self._db_path       = data_dir / "compliance.db"
        self._semantic_path = data_dir / "semantic_memory.json"
        self._correlation_path = data_dir / "correlation_database.json"

        self._runs_completed = 0
        self._last_run: Optional[datetime] = None
        self._semantic_memory: Dict[str, Any] = self._load_semantic_memory()

    async def start(self) -> None:
        asyncio.create_task(self._nightly_scheduler(), name="ref_scheduler")
        asyncio.create_task(self._heartbeat_loop(), name="ref_heartbeat")
        log.info("reflection_agent_started",
                 next_run="2:00 AM ET",
                 semantic_rules=len(self._semantic_memory.get("rules", [])))

    # ── Nightly Scheduler ─────────────────────────────────────────────────────

    async def _nightly_scheduler(self) -> None:
        """Run the reflection cycle nightly at 2:00 AM ET."""
        while True:
            await asyncio.sleep(3600)   # Check every hour

            now = datetime.now(timezone.utc)
            # 2:00 AM ET = 7:00 AM UTC (roughly, ignoring DST)
            if now.hour == 7 and now.minute < 5:
                # Don't run twice in the same hour
                if (self._last_run is None or
                        (now - self._last_run).total_seconds() > 3600):
                    log.info("reflection_cycle_starting",
                             timestamp=now.isoformat())
                    try:
                        await self._run_reflection_cycle()
                        self._last_run = now
                        self._runs_completed += 1
                    except Exception as e:
                        log.error("reflection_cycle_error", error=str(e))

    async def run_now(self) -> None:
        """Manually trigger reflection cycle (for testing/development)."""
        log.info("reflection_cycle_manual_trigger")
        await self._run_reflection_cycle()

    # ── The 9-Step Reflection Cycle ───────────────────────────────────────────

    async def _run_reflection_cycle(self) -> None:
        """Execute the complete nightly reflection cycle."""
        period_start = datetime.now(timezone.utc) - timedelta(hours=24)

        # Step 1: Trade Review
        log.info("reflection_step_1_trade_review")
        trades = await self._get_resolved_trades(period_start)

        # Step 2: Signal Attribution
        log.info("reflection_step_2_signal_attribution")
        signal_performance = await self._attribute_signals(trades)

        # Step 3: Persona Calibration
        log.info("reflection_step_3_persona_calibration")
        persona_accuracy = await self._calibrate_personas(trades)

        # Step 4: Source Calibration (SCS updates)
        log.info("reflection_step_4_source_calibration")
        scs_updates = await self._update_source_credibility(trades)

        # Step 5: Pattern Distillation
        log.info("reflection_step_5_pattern_distillation")
        new_patterns = await self._distill_patterns(trades)

        # Step 6: Prompt Optimization Flags
        log.info("reflection_step_6_prompt_optimization")
        prompt_flags = await self._flag_prompt_improvements(persona_accuracy)

        # Step 7: Anomaly Flagging
        log.info("reflection_step_7_anomaly_flagging")
        anomalies = await self._detect_anomalies(trades)

        # Step 8: Strategy Weight Update
        log.info("reflection_step_8_strategy_weights")
        strategy_weights = await self._update_strategy_weights(trades)

        # Step 9: Performance Narrative
        log.info("reflection_step_9_narrative")
        narrative = await self._generate_narrative(
            trades, strategy_weights, new_patterns, anomalies
        )

        # Publish results
        await self.bus.publish(StrategyWeightUpdateEvent(
            source                   = self.AGENT_NAME,
            strategy_weights         = strategy_weights,
            source_scs_updates       = scs_updates,
            persona_accuracy_updates = persona_accuracy,
            performance_narrative    = narrative,
        ))

        # Update semantic memory
        self._update_semantic_memory(new_patterns)

        log.info("reflection_cycle_complete",
                 trades_reviewed=len(trades),
                 patterns_found=len(new_patterns),
                 narrative_length=len(narrative))

    # ── Step Implementations ──────────────────────────────────────────────────

    async def _get_resolved_trades(
        self, since: datetime
    ) -> List[Dict[str, Any]]:
        """Fetch all resolved trades from the period."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM trades
                   WHERE status='RESOLVED'
                   AND resolved_at >= ?
                   ORDER BY resolved_at ASC""",
                (since.isoformat(),)
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def _attribute_signals(
        self, trades: List[Dict]
    ) -> Dict[str, float]:
        """
        Determine which signals were present before successful vs. failed trades.
        This is the core learning step — attribution tells us what actually works.
        """
        signal_scores: Dict[str, List[float]] = {}

        # For each trade, check what signals were in the audit trail
        async with aiosqlite.connect(self._db_path) as db:
            for trade in trades:
                trade_time = trade.get("timestamp", "")
                pnl = trade.get("realized_pnl", 0)
                outcome = 1.0 if pnl > 0 else 0.0

                # Fetch audit events from 72 hours before this trade
                cursor = await db.execute(
                    """SELECT event_type, entry_json FROM audit_trail
                       WHERE timestamp < ? AND timestamp >= ?
                       AND event_type IN ('NewsSignalEvent', 'SmartMoneySignalEvent',
                                          'ImpliedProbabilityDivergenceEvent',
                                          'GeopoliticalRiskEvent')
                       ORDER BY timestamp DESC LIMIT 50""",
                    (trade_time, trade.get("timestamp", ""))
                )
                rows = await cursor.fetchall()

                for event_type, _ in rows:
                    if event_type not in signal_scores:
                        signal_scores[event_type] = []
                    signal_scores[event_type].append(outcome)

        # Calculate signal accuracy
        signal_accuracy = {}
        for signal_type, outcomes in signal_scores.items():
            if len(outcomes) >= 5:   # Minimum sample for reliability
                accuracy = sum(outcomes) / len(outcomes)
                signal_accuracy[signal_type] = accuracy

        return signal_accuracy

    async def _calibrate_personas(
        self, trades: List[Dict]
    ) -> Dict[str, float]:
        """Update persona accuracy scores based on trade outcomes."""
        # In production: compare each persona's pre-trade probability estimate
        # against the actual outcome. This requires storing persona outputs
        # in the audit trail (which we do).

        # Simplified for Phase 1: return neutral scores
        # Full implementation requires joining persona outputs to trade outcomes
        persona_accuracy = {
            persona: 0.5 for persona in [
                "mainstream_analyst", "macro_economist", "geopolitical_strategist",
                "contrarian", "technical_analyst", "behavioral_psychologist",
                "policy_wonk", "intelligence_analyst", "skeptic",
                "calibrated_alternative", "domain_specialist", "synthesizer"
            ]
        }

        # TODO: Full implementation in Phase 2
        # Will join audit_trail persona_output records with trade outcomes
        # and compute accuracy per persona per topic category

        return persona_accuracy

    async def _update_source_credibility(
        self, trades: List[Dict]
    ) -> Dict[str, float]:
        """
        Update Source Credibility Scores.
        When a trade is based on a signal from a source, and the trade
        resolves correctly, that source gets credit.
        """
        scs_updates: Dict[str, float] = {}

        # TODO: Full implementation requires:
        # 1. Storing source attribution on each trade
        # 2. Joining source attribution to trade outcomes
        # 3. Updating SCS per source per topic

        # For Phase 1: placeholder
        return scs_updates

    async def _distill_patterns(
        self, trades: List[Dict]
    ) -> List[Dict[str, Any]]:
        """
        Use LLM to distill generalizable patterns from trade history.
        These patterns become part of Semantic Memory.
        """
        if not self._client or not trades:
            return []

        # Prepare trade summary for LLM
        winning_trades = [t for t in trades if (t.get("realized_pnl", 0) or 0) > 0]
        losing_trades  = [t for t in trades if (t.get("realized_pnl", 0) or 0) <= 0]

        trade_summary = (
            f"Winning trades ({len(winning_trades)}): "
            f"{json.dumps([{k: t.get(k) for k in ['strategy','market_id','platform','realized_pnl']} for t in winning_trades[:5]], default=str)}\n"
            f"Losing trades ({len(losing_trades)}): "
            f"{json.dumps([{k: t.get(k) for k in ['strategy','market_id','platform','realized_pnl']} for t in losing_trades[:5]], default=str)}"
        )

        prompt = f"""You are analyzing trading performance data to extract generalizable patterns.

TRADE SUMMARY (last 24 hours):
{trade_summary}

EXISTING PATTERNS IN MEMORY:
{json.dumps(self._semantic_memory.get('rules', [])[:5], indent=2)}

TASK: Identify 1-3 NEW generalizable patterns from today's data that would
help improve future trading decisions. Focus on:
- Which market types performed well/poorly?
- Were there timing patterns (time of day, days before announcement)?
- Were there liquidity patterns (high vs. low volume markets)?
- Were there strategy-specific patterns?

Respond in this EXACT JSON format:
{{
  "patterns": [
    {{
      "pattern_id": "unique_string",
      "description": "Human-readable pattern description",
      "actionable_rule": "Specific rule to apply in future",
      "confidence": 0.0,
      "evidence": "What in today's data supports this"
    }}
  ]
}}

If no new patterns are evident, return: {{"patterns": []}}
Return ONLY valid JSON.
"""

        try:
            response = self._client.messages.create(
                model      = self._intel_model,
                max_tokens = 1000,
                messages   = [{"role": "user", "content": prompt}],
            )
            data = json.loads(response.content[0].text.strip())
            return data.get("patterns", [])
        except Exception as e:
            log.error("pattern_distillation_error", error=str(e))
            return []

    async def _flag_prompt_improvements(
        self, persona_accuracy: Dict[str, float]
    ) -> List[str]:
        """Flag personas whose prompts may need improvement."""
        flags = []
        for persona, accuracy in persona_accuracy.items():
            if accuracy < 0.45:   # Below random — prompt may be miscalibrated
                flags.append(persona)
        return flags

    async def _detect_anomalies(
        self, trades: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Detect unexpected patterns that warrant human review."""
        anomalies = []

        # Anomaly 1: Unusually high rejection rate
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT reason, COUNT(*) as cnt FROM rejections "
                "WHERE timestamp >= ? GROUP BY reason ORDER BY cnt DESC LIMIT 5",
                ((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),)
            )
            rejections = await cursor.fetchall()
            total_rejections = sum(r[1] for r in rejections)

            if total_rejections > 100:
                anomalies.append({
                    "type": "HIGH_REJECTION_RATE",
                    "description": f"{total_rejections} rejections in 24 hours",
                    "top_reasons": [{"reason": r[0], "count": r[1]}
                                    for r in rejections],
                })

        # Anomaly 2: Strategy performance divergence
        if trades:
            strategies = {}
            for trade in trades:
                s = trade.get("strategy", "unknown")
                if s not in strategies:
                    strategies[s] = []
                pnl = trade.get("realized_pnl", 0) or 0
                strategies[s].append(pnl)

            for strategy, pnls in strategies.items():
                if len(pnls) >= 3:
                    avg = sum(pnls) / len(pnls)
                    if avg < 0:
                        anomalies.append({
                            "type": "STRATEGY_LOSING",
                            "description": f"Strategy {strategy} averaged ${avg:.2f} PnL",
                            "trade_count": len(pnls),
                        })

        return anomalies

    async def _update_strategy_weights(
        self, trades: List[Dict]
    ) -> Dict[str, float]:
        """
        Update strategy weights based on recent performance.
        Better-performing strategies get higher weight → more capital allocation.
        """
        strategy_pnl: Dict[str, List[float]] = {}

        for trade in trades:
            s = trade.get("strategy", "")
            if s:
                if s not in strategy_pnl:
                    strategy_pnl[s] = []
                pnl = trade.get("realized_pnl", 0) or 0
                strategy_pnl[s].append(pnl)

        weights = {
            "S1_REBALANCING":   1.0,
            "S2_CROSS_PLATFORM": 1.0,
            "S3_LOGICAL_ARB":   1.0,
            "S4_SETTLEMENT_ARB": 1.0,
        }

        for strategy, pnls in strategy_pnl.items():
            if len(pnls) >= 5:
                avg_pnl = sum(pnls) / len(pnls)
                # Normalize to weight (positive avg → weight up, negative → weight down)
                if avg_pnl > 0:
                    weights[strategy] = min(2.0, 1.0 + avg_pnl / 10)
                else:
                    weights[strategy] = max(0.1, 1.0 + avg_pnl / 10)

        return weights

    async def _generate_narrative(
        self,
        trades: List[Dict],
        weights: Dict[str, float],
        patterns: List[Dict],
        anomalies: List[Dict],
    ) -> str:
        """
        Generate a human-readable performance narrative.
        This appears on the dashboard and gives the operator insight
        into what the system learned overnight.
        """
        if not self._client:
            return self._generate_simple_narrative(trades, weights)

        trade_count = len(trades)
        total_pnl   = sum((t.get("realized_pnl", 0) or 0) for t in trades)
        win_rate    = (
            sum(1 for t in trades if (t.get("realized_pnl", 0) or 0) > 0) / trade_count
            if trade_count > 0 else 0
        )

        prompt = f"""You are writing a daily performance report for an automated
prediction market arbitrage system. Write 3-4 sentences summarizing the day.

DATA:
- Trades resolved: {trade_count}
- Total realized PnL: ${total_pnl:.2f}
- Win rate: {win_rate:.1%}
- Strategy weights updated: {json.dumps(weights)}
- New patterns found: {len(patterns)}
- Anomalies detected: {len(anomalies)}
- Anomaly details: {json.dumps(anomalies[:3]) if anomalies else "None"}

Write a concise, factual narrative. Tone: professional analyst. No hype.
If performance was poor, say so clearly. If anomalies were detected, mention them.
Include one specific observation about what the data suggests for tomorrow.
"""

        try:
            response = self._client.messages.create(
                model      = self._intel_model,
                max_tokens = 300,
                messages   = [{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()
        except Exception:
            return self._generate_simple_narrative(trades, weights)

    def _generate_simple_narrative(
        self, trades: List[Dict], weights: Dict[str, float]
    ) -> str:
        """Fallback narrative without LLM."""
        count = len(trades)
        total = sum((t.get("realized_pnl", 0) or 0) for t in trades)
        return (
            f"Reflection cycle complete. {count} trades reviewed. "
            f"Total PnL: ${total:.2f}. "
            f"Strategy weights updated based on performance data."
        )

    # ── Semantic Memory ───────────────────────────────────────────────────────

    def _load_semantic_memory(self) -> Dict[str, Any]:
        """Load semantic memory from disk."""
        try:
            if self._semantic_path.exists():
                with open(self._semantic_path) as f:
                    return json.load(f)
        except Exception as e:
            log.error("semantic_memory_load_error", error=str(e))
        return {"rules": [], "version": 0, "last_updated": None}

    def _update_semantic_memory(self, new_patterns: List[Dict]) -> None:
        """Add new patterns to semantic memory and save."""
        if not new_patterns:
            return

        # Avoid duplicates (by pattern_id)
        existing_ids = {r.get("pattern_id") for r in self._semantic_memory.get("rules", [])}
        truly_new = [p for p in new_patterns if p.get("pattern_id") not in existing_ids]

        if truly_new:
            self._semantic_memory.setdefault("rules", []).extend(truly_new)
            self._semantic_memory["version"] = self._semantic_memory.get("version", 0) + 1
            self._semantic_memory["last_updated"] = datetime.now(timezone.utc).isoformat()

            try:
                with open(self._semantic_path, "w") as f:
                    json.dump(self._semantic_memory, f, indent=2)
                log.info("semantic_memory_updated",
                         new_patterns=len(truly_new),
                         total_rules=len(self._semantic_memory["rules"]))
            except Exception as e:
                log.error("semantic_memory_save_error", error=str(e))

    @property
    def _intel_model(self) -> str:
        return self.config.intelligence.llm_model_analysis

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            await self.bus.publish(AgentHeartbeat(
                source             = self.AGENT_NAME,
                agent_name         = self.AGENT_NAME,
                status             = "OK",
                messages_processed = self._runs_completed,
                last_action        = (
                    f"last_run={self._last_run.isoformat() if self._last_run else 'never'}"
                ),
            ))


# ── karbot_runner.py-compatible stub ─────────────────────────────────────────

class ReflectionAgent:
    """Stub conforming to the BaseAgent interface for karbot_runner.py."""

    def __init__(self, bus: EventBus, config: KarbotConfig):
        self.bus = bus
        self.config = config

    def register_subscriptions(self):
        pass

    async def run(self):
        log.info("ReflectionAgent stub running (not yet implemented)")
        while True:
            await asyncio.sleep(60)
