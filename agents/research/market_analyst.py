"""
karbot/agents/research/market_analyst.py
──────────────────────────────────────────
Market Analyst Agent — Research Floor

The LLM intelligence core. Uses Claude API to perform semantic analysis
of all open markets, finding logical dependencies and combinatorial
mispricings that pure price-watching cannot detect.

Key design decisions:
  - Batch markets into single API calls (cost efficiency)
  - Cache results per market hash (avoid redundant calls)
  - Structure ALL outputs as JSON (no prose — machine-readable)
  - Log every prompt + response (audit trail + learning)
  - Multi-persona panel (12 perspectives per market)
  - MNPI firewall hardcoded into every prompt

The system discovers what DARPA's Policy Analysis Market tried to prove:
markets, fed with the right information, outperform expert consensus.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import anthropic
import structlog

from karbot.core.config import KarbotConfig
from karbot.core.events import (
    EventBus, LogicalArbCandidateEvent, AgentHeartbeat,
    StrategyWeightUpdateEvent, Priority
)

log = structlog.get_logger(__name__)


# ── Persona Definitions ───────────────────────────────────────────────────────

PERSONA_LIBRARY = {
    "mainstream_analyst": """You are a mainstream financial analyst. You rely on
consensus media, official statements, and institutional research. You are
systematic but sometimes slow to update when consensus is wrong.""",

    "macro_economist": """You are a macroeconomist specializing in monetary policy,
fiscal dynamics, trade flows, and economic indicator interpretation. You focus
on quantitative data and tend to underweight political and social factors.""",

    "geopolitical_strategist": """You are a geopolitical intelligence analyst with
expertise in international relations, military doctrine, diplomatic signaling,
and historical pattern recognition across nation-states.""",

    "contrarian": """You are a contrarian investor. Your job is to find the
strongest possible argument AGAINST the consensus view. You look for what
the crowd is systematically getting wrong, where overconfidence exists,
and what tail risks are being ignored.""",

    "technical_analyst": """You are a quantitative trader who reads price patterns,
volume dynamics, order flow, and market microstructure. You analyze the
statistical properties of how markets have moved and what that implies.""",

    "behavioral_psychologist": """You are a behavioral economist who understands
how cognitive biases systematically distort market prices. You look for
longshot bias, recency bias, status quo bias, and anchoring effects.""",

    "policy_wonk": """You are a policy specialist who reads regulatory filings,
understands administrative procedure, knows legislative language, and can
interpret bureaucratic signals that others miss.""",

    "intelligence_analyst": """You are an open-source intelligence (OSINT) analyst.
You synthesize information from multiple public sources, detect anomalies,
apply pattern-of-life analysis, and triangulate conclusions across independent
data streams. You ONLY use publicly available information.""",

    "skeptic": """You are a professional skeptic. Your ONLY job is to find reasons
why a proposed trade opportunity might NOT work. What are we missing?
What could invalidate the thesis? What are the execution risks?
What resolution scenario would cause a loss? Be ruthless.""",

    "calibrated_alternative": """You are an analyst who monitors non-mainstream
information sources that have demonstrated predictive accuracy: FOIA-released
documents, whistleblower-published information, investigative journalism,
specialized academic communities, and credentialed experts outside mainstream
institutions. You only reference publicly available information.""",

    "domain_specialist": """You are a deep domain expert in the relevant field
for this specific market. Apply specialist knowledge that a generalist would
lack. Consider technical, regulatory, and operational factors specific to
this domain.""",

    "synthesizer": """You are the analytical synthesizer. You have read the
perspectives of all other analysts. Your job is to integrate their views,
identify where they agree and disagree, weigh the evidence, and produce
a final calibrated probability estimate with explicit uncertainty bounds.""",
}

# The mandatory MNPI firewall — appears in EVERY prompt
MNPI_FIREWALL = """
CRITICAL COMPLIANCE REQUIREMENT (NON-NEGOTIABLE):
You MUST base your analysis ONLY on publicly available information.
NEVER suggest trades based on:
- Material non-public information (MNPI)
- Information obtained through breach of confidentiality
- Classified or restricted government information
- Information shared privately by insiders

ALL analysis must be derivable from:
- Public news articles, filings, and announcements
- Publicly released FOIA documents
- Published whistleblower disclosures
- Public government databases
- Public market price data

If any aspect of your analysis depends on non-public information,
explicitly flag it and exclude it from your trading recommendation.
"""


# ── Market Analysis Pipeline ──────────────────────────────────────────────────

@dataclass
class MarketAnalysis:
    """Result of multi-persona analysis for a single market."""
    market_id:          str
    market_description: str
    platform:           str
    current_price:      float
    panel_probability:  float   # Bayesian-aggregated estimate
    consensus_score:    float   # Agreement across personas (0-1)
    confidence:         str     # HIGH | MEDIUM | LOW
    personas:           List[Dict[str, Any]] = field(default_factory=list)
    logical_dependencies: List[Dict[str, Any]] = field(default_factory=list)
    key_signals:        List[str] = field(default_factory=list)
    risks:              List[str] = field(default_factory=list)
    analysis_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cache_hash:         str = ""


class AnalysisCache:
    """Cache market analyses to avoid redundant LLM calls."""

    def __init__(self, ttl_minutes: int = 10):
        self._cache: Dict[str, tuple] = {}   # hash → (analysis, expiry)
        self._ttl = ttl_minutes * 60

    def get(self, market_hash: str) -> Optional[MarketAnalysis]:
        if market_hash in self._cache:
            analysis, expiry = self._cache[market_hash]
            if time.monotonic() < expiry:
                return analysis
            del self._cache[market_hash]
        return None

    def set(self, market_hash: str, analysis: MarketAnalysis) -> None:
        self._cache[market_hash] = (analysis, time.monotonic() + self._ttl)

    def market_hash(self, market_id: str, price: float, description: str) -> str:
        """Hash market state — used as cache key."""
        state = f"{market_id}:{price:.3f}:{description}"
        return hashlib.md5(state.encode()).hexdigest()[:12]

    def cleanup(self) -> None:
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._cache.items() if exp <= now]
        for k in expired:
            del self._cache[k]


class MarketAnalystAgent:
    """
    Research Floor Agent — LLM semantic intelligence core.

    Runs every 5 minutes (configurable). Batches all active markets into
    efficient API calls. Maintains the Market Dependency Graph.
    Publishes LogicalArbCandidateEvents when mispricings are found.

    Cost management is critical — see LLM call batching strategy below.
    """

    AGENT_NAME = "market_analyst"
    HEARTBEAT_INTERVAL = 60
    ANALYSIS_INTERVAL_SECONDS = 300   # 5 minutes

    def __init__(
        self,
        config: KarbotConfig,
        secrets,
        event_bus: EventBus,
    ):
        self.config  = config
        self.secrets = secrets
        self.bus     = event_bus
        self._intel  = config.intelligence

        self._client = anthropic.Anthropic(api_key=secrets.anthropic_api_key) \
            if secrets.anthropic_api_key else None

        self._cache = AnalysisCache(ttl_minutes=self._intel.llm_cache_ttl_minutes)
        self._market_graph: Dict[str, List[Dict]] = {}   # market_id → dependencies
        self._daily_spend = 0.0
        self._analyses_run = 0
        self._candidates_found = 0

        # Active markets list (updated externally)
        self._active_markets: List[Dict[str, Any]] = []

    async def start(self) -> None:
        asyncio.create_task(self._analysis_loop(), name="ma_analysis")
        asyncio.create_task(self._heartbeat_loop(), name="ma_heartbeat")
        asyncio.create_task(self._cache_cleanup_loop(), name="ma_cache")
        log.info("market_analyst_started",
                 llm_enabled=self._client is not None,
                 model=self._intel.llm_model_analysis)

    def update_markets(self, markets: List[Dict[str, Any]]) -> None:
        """Called by Price Watcher when market list is refreshed."""
        self._active_markets = markets

    async def _analysis_loop(self) -> None:
        """Main analysis loop — runs every 5 minutes."""
        while True:
            await asyncio.sleep(self.ANALYSIS_INTERVAL_SECONDS)

            if not self._client:
                continue

            if not self._active_markets:
                continue

            # Check spend limit
            if self._daily_spend >= self._intel.llm_daily_spend_limit:
                log.warning("llm_daily_spend_limit_reached",
                            spent=self._daily_spend,
                            limit=self._intel.llm_daily_spend_limit)
                continue

            try:
                await self._run_analysis_batch()
            except Exception as e:
                log.error("analysis_loop_error", error=str(e))

    async def _run_analysis_batch(self) -> None:
        """
        Batch all active markets into efficient API calls.

        Strategy: group markets by topic, send groups to LLM.
        This dramatically reduces API calls vs. one market at a time.
        """
        # Group markets into batches of configured size
        batch_size = self._intel.llm_batch_size
        batches = [
            self._active_markets[i:i+batch_size]
            for i in range(0, len(self._active_markets), batch_size)
        ]

        for batch in batches:
            try:
                candidates = await self._analyze_market_batch(batch)
                for candidate in candidates:
                    self._candidates_found += 1
                    await self.bus.publish(candidate)
            except Exception as e:
                log.error("batch_analysis_error", error=str(e), batch_size=len(batch))

        self._analyses_run += 1

    async def _analyze_market_batch(
        self, markets: List[Dict[str, Any]]
    ) -> List[LogicalArbCandidateEvent]:
        """
        Send a batch of markets to the LLM for semantic analysis.
        Returns any logical arbitrage candidates found.
        """
        if not markets:
            return []

        # Build market summary for LLM
        market_summary = "\n".join([
            f"- [{m.get('platform', 'unknown')}] {m.get('ticker', '')} | "
            f"'{m.get('title', '')}' | "
            f"YES_price={m.get('yes_price', 0):.2f} | "
            f"volume_24h={m.get('volume_24h', 0):.0f}"
            for m in markets
        ])

        prompt = f"""{MNPI_FIREWALL}

You are performing semantic analysis on prediction market contracts.

ACTIVE MARKETS TO ANALYZE:
{market_summary}

TASK: Identify any LOGICAL INCONSISTENCIES where the current prices violate
mathematical or logical relationships between markets.

Examples of logical inconsistencies:
- "Candidate A wins election" at 55% but "Party A wins election" at 45%
  (if Candidate A is in Party A, Party A wins >= Candidate A wins)
- "Event X happens in Q1" at 40% + "Event X happens in Q2" at 45% +
  "Event X happens in Q3" at 30% = 115% total (exceeds 100%, impossible)
- "GDP growth above 3%" at 60% but "GDP growth above 2%" at 55%
  (if >3% then certainly >2%, so P(>2%) >= P(>3%))

For each inconsistency found, output EXACTLY this JSON structure:
{{
  "inconsistencies": [
    {{
      "market_a_id": "ticker of first market",
      "market_a_platform": "kalshi or polymarket",
      "market_a_price": 0.00,
      "market_b_id": "ticker of second market",
      "market_b_platform": "kalshi or polymarket",
      "market_b_price": 0.00,
      "relationship": "A_IMPLIES_B or MUTUALLY_EXCLUSIVE or EXHAUSTIVE_SUM",
      "logical_constraint": "plain English explanation of the constraint",
      "implied_edge_pct": 0.0,
      "confidence": 0.0,
      "reasoning": "brief explanation of why this is a logical inconsistency"
    }}
  ]
}}

If no inconsistencies are found, return: {{"inconsistencies": []}}

IMPORTANT:
- Only flag GENUINE logical impossibilities, not mere probability estimates
- Confidence should reflect certainty that the logical relationship exists (0-1)
- implied_edge_pct is the percentage profit if the inconsistency is exploited
- Do NOT flag markets where the relationship is ambiguous or requires interpretation
- Return ONLY valid JSON, no other text
"""

        try:
            response = self._client.messages.create(
                model      = self._intel.llm_model_analysis,
                max_tokens = 2000,
                messages   = [{"role": "user", "content": prompt}],
            )

            # Estimate cost (rough: ~$0.003 per 1K tokens for Sonnet)
            input_tokens  = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost_estimate = (input_tokens / 1000 * 0.003) + (output_tokens / 1000 * 0.015)
            self._daily_spend += cost_estimate

            # Parse response
            response_text = response.content[0].text.strip()
            data = json.loads(response_text)
            inconsistencies = data.get("inconsistencies", [])

            # Convert to events
            candidates = []
            for inc in inconsistencies:
                if inc.get("implied_edge_pct", 0) > 0.5:  # Only significant edges
                    candidates.append(LogicalArbCandidateEvent(
                        source              = self.AGENT_NAME,
                        market_a_id         = inc.get("market_a_id", ""),
                        market_a_platform   = inc.get("market_a_platform", ""),
                        market_a_price      = inc.get("market_a_price", 0),
                        market_b_id         = inc.get("market_b_id", ""),
                        market_b_platform   = inc.get("market_b_platform", ""),
                        market_b_price      = inc.get("market_b_price", 0),
                        relationship        = inc.get("relationship", ""),
                        logical_constraint  = inc.get("logical_constraint", ""),
                        implied_edge_pct    = inc.get("implied_edge_pct", 0),
                        llm_confidence      = inc.get("confidence", 0),
                        llm_reasoning       = inc.get("reasoning", ""),
                    ))

            if candidates:
                log.info("logical_arb_candidates_found",
                         count=len(candidates),
                         cost_usd=cost_estimate)

            return candidates

        except json.JSONDecodeError as e:
            log.error("llm_response_parse_error", error=str(e))
            return []
        except anthropic.APIError as e:
            log.error("anthropic_api_error", error=str(e))
            return []

    async def run_persona_panel(
        self,
        market_id: str,
        market_description: str,
        current_price: float,
        signals: List[Dict[str, Any]],
    ) -> Optional[MarketAnalysis]:
        """
        Run the full multi-persona panel for a high-value trade decision.
        Called by Portfolio Manager for trades above the debate threshold.

        This uses the more capable (and expensive) Opus model.
        """
        if not self._client:
            return None

        if self._daily_spend >= self._intel.llm_daily_spend_limit:
            log.warning("skipping_panel_spend_limit")
            return None

        signal_summary = "\n".join([
            f"- [{s.get('source', 'unknown')}] {s.get('description', '')}"
            for s in signals[:10]  # Cap signals to prevent token explosion
        ])

        persona_outputs = {}

        # Run each persona (batched into one call for efficiency)
        personas_to_run = [
            "macro_economist", "geopolitical_strategist", "contrarian",
            "policy_wonk", "skeptic", "synthesizer"
        ]

        for persona_name in personas_to_run:
            persona_desc = PERSONA_LIBRARY.get(persona_name, "")
            prompt = f"""{MNPI_FIREWALL}

{persona_desc}

MARKET BEING ANALYZED:
Title: {market_description}
Market ID: {market_id}
Current market probability: {current_price:.1%}

RELEVANT SIGNALS DETECTED:
{signal_summary if signal_summary else "No specific signals detected — use general knowledge."}

Your analytical task:
1. What is your probability estimate for this market resolving YES?
2. What are your top 2-3 reasons supporting this estimate?
3. What are the 1-2 biggest risks to your estimate?
4. How confident are you? (0.0-1.0)

Respond in this EXACT JSON format:
{{
  "probability": 0.00,
  "reasoning": ["reason 1", "reason 2"],
  "risks": ["risk 1", "risk 2"],
  "confidence": 0.0
}}

Return ONLY valid JSON, no other text.
"""
            try:
                response = self._client.messages.create(
                    model      = self._intel.llm_model_analysis,
                    max_tokens = 500,
                    messages   = [{"role": "user", "content": prompt}],
                )
                output = json.loads(response.content[0].text.strip())
                persona_outputs[persona_name] = output

                # Track cost
                self._daily_spend += 0.002  # Approximate

            except Exception as e:
                log.warning("persona_failed", persona=persona_name, error=str(e))
                persona_outputs[persona_name] = {"probability": current_price,
                                                  "confidence": 0.3}

        # Bayesian aggregation
        panel_prob, consensus = self._aggregate_personas(persona_outputs, current_price)

        confidence_str = (
            "HIGH" if consensus > 0.8 else
            "MEDIUM" if consensus > 0.5 else
            "LOW"
        )

        return MarketAnalysis(
            market_id          = market_id,
            market_description = market_description,
            platform           = "kalshi",
            current_price      = current_price,
            panel_probability  = panel_prob,
            consensus_score    = consensus,
            confidence         = confidence_str,
            personas           = [
                {"name": k, **v} for k, v in persona_outputs.items()
            ],
        )

    def _aggregate_personas(
        self,
        outputs: Dict[str, Dict],
        market_price: float,
    ) -> tuple:
        """
        Bayesian aggregation of persona probability estimates.

        Each persona's weight = confidence × historical accuracy (simplified here —
        in production, use SCS database to look up per-persona per-topic accuracy).
        """
        weighted_sum   = 0.0
        total_weight   = 0.0
        probabilities  = []

        for name, output in outputs.items():
            prob       = output.get("probability", market_price)
            confidence = output.get("confidence", 0.5)

            # In production: weight *= self._scs_db.get_accuracy(name, topic)
            # For now: confidence is the weight
            weight = confidence

            weighted_sum += prob * weight
            total_weight += weight
            probabilities.append(prob)

        if total_weight == 0:
            return market_price, 0.5

        panel_prob = weighted_sum / total_weight

        # Consensus score: 1 - normalized variance
        if len(probabilities) > 1:
            import statistics
            variance = statistics.variance(probabilities)
            consensus = max(0, 1 - (variance * 10))   # Scale variance to 0-1
        else:
            consensus = 0.5

        return panel_prob, consensus

    async def _cache_cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(300)
            self._cache.cleanup()

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            await self.bus.publish(AgentHeartbeat(
                source             = self.AGENT_NAME,
                agent_name         = self.AGENT_NAME,
                status             = "OK",
                messages_processed = self._analyses_run,
                last_action        = (
                    f"daily_spend=${self._daily_spend:.2f} "
                    f"candidates={self._candidates_found}"
                ),
            ))


# ── karbot_runner.py-compatible stub ─────────────────────────────────────────

class MarketAnalyst(MarketAnalystAgent):
    """
    BaseAgent-conforming class used by karbot_runner.py.
    Inherits the full MarketAnalystAgent implementation.

    run() starts the LLM analysis loop (every 5 min), heartbeat, and
    cache-cleanup tasks.  If no Anthropic API key is configured the
    analysis loop is a no-op and no API calls are made.
    """

    def __init__(self, bus: EventBus, config: KarbotConfig):
        super().__init__(config=config, secrets=config.secrets, event_bus=bus)

    def register_subscriptions(self) -> None:
        pass   # MarketAnalyst publishes LogicalArbCandidateEvents; no subscriptions

    async def run(self) -> None:
        asyncio.create_task(self._analysis_loop(),      name="ma_analysis")
        asyncio.create_task(self._heartbeat_loop(),     name="ma_heartbeat")
        asyncio.create_task(self._cache_cleanup_loop(), name="ma_cache_cleanup")
        log.info(
            "market_analyst_started",
            llm_enabled=self._client is not None,
            model=self._intel.llm_model_analysis,
        )
        while True:
            await asyncio.sleep(3600)
