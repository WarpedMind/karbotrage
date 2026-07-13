"""
karbot/core/events.py
─────────────────────
The central event bus. All inter-agent communication flows through here.
No agent calls another agent directly — everything is publish/subscribe.

Design principles:
  - Typed events (dataclasses with strict schemas)
  - All events logged to audit trail automatically
  - Zero network overhead (in-process asyncio queues)
  - Dead-letter queue for unhandled events
  - Replay capability for backtesting
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, Awaitable, Dict, List, Optional, Tuple, Type, TypeVar

import structlog

log = structlog.get_logger(__name__)

# ── Event priority levels ─────────────────────────────────────────────────────

class Priority(Enum):
    CRITICAL = 0   # Kill switch, system failures
    HIGH     = 1   # Trade execution, risk limit hits
    NORMAL   = 2   # Price updates, opportunity detection
    LOW      = 3   # Background intelligence, analytics


# ── Base event ────────────────────────────────────────────────────────────────

@dataclass
class Event:
    """Base class for all system events. Every event is immutable after creation."""
    event_id:   str      = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source:     str      = ""
    priority:   Priority = Priority.NORMAL

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d['event_type'] = self.__class__.__name__
        d['timestamp']  = self.timestamp.isoformat()
        d['priority']   = self.priority.name
        return d


# ── Trading Floor Events ──────────────────────────────────────────────────────

@dataclass
class PriceUpdateEvent(Event):
    """Published by Price Watcher Agent on every order book tick."""
    platform:       str   = ""          # "kalshi" | "polymarket"
    market_id:      str   = ""
    yes_bid:        float = 0.0
    yes_ask:        float = 0.0
    no_bid:         float = 0.0
    no_ask:         float = 0.0
    volume_24h:     float = 0.0
    open_interest:  int   = 0
    sequence_num:   int   = 0           # For detecting gaps in WebSocket stream

    # Order book depth behind yes_bid / no_bid, top levels sorted best-price
    # first: [(price, size), ...]. Lets strategies size against real
    # available liquidity instead of assuming the full order can fill at the
    # single best-quoted price — live-confirmed 2026-07-13 that a "3% edge"
    # top-of-book quote can be backed by as little as 1 contract.
    yes_bid_depth:  List[Tuple[float, float]] = field(default_factory=list)
    no_bid_depth:   List[Tuple[float, float]] = field(default_factory=list)


@dataclass
class BookSnapshotEvent(Event):
    """Full order book snapshot (published on reconnect/startup)."""
    platform:  str             = ""
    market_id: str             = ""
    bids:      List[tuple]     = field(default_factory=list)   # [(price, size), ...]
    asks:      List[tuple]     = field(default_factory=list)
    timestamp: datetime        = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FeedHealthEvent(Event):
    """Published when WebSocket connection status changes."""
    platform:   str  = ""
    connected:  bool = False
    latency_ms: float = 0.0
    message_rate_per_sec: float = 0.0
    error:      str  = ""   # underlying error message, if disconnect was caused by an exception


@dataclass
class OpportunityEvent(Event):
    """
    Standardized opportunity object. ALL strategies output this.
    The Risk Gate only receives this type — it never knows which strategy produced it.
    """
    priority:                  Priority = Priority.HIGH

    # Identity
    opportunity_id:            str   = field(default_factory=lambda: str(uuid.uuid4()))
    strategy:                  str   = ""    # S1_REBALANCING | S2_CROSS_PLATFORM | S3_LOGICAL | etc.

    # Trade details
    legs: List[Dict[str, Any]] = field(default_factory=list)
    # Each leg: {platform, market_id, market_desc, side, price, quantity, fee_estimate}

    # Economics (AFTER fee calculation)
    gross_profit_pct:          float = 0.0
    estimated_fees_pct:        float = 0.0
    estimated_slippage_pct:    float = 0.0
    net_profit_pct:            float = 0.0

    # Confidence
    confidence:                str   = "LOW"   # HIGH | MEDIUM | LOW
    persona_consensus_score:   float = 0.0     # 0-1, from persona panel aggregation

    # Metadata
    detected_at:               datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expected_resolution:       Optional[datetime] = None
    resolution_criteria_match: Optional[bool] = None  # Required for cross-platform
    capital_required_usd:      float = 0.0

    # Liquidity: max quantity (contracts — same unit RiskGate.approved_size /
    # PaperExecutor's leg "quantity" already use) the order book can actually
    # support at prices that still clear s1_min_net_profit_pct, computed by
    # walking real book depth rather than assuming unlimited size at the
    # top-of-book quote. 0.0 = not computed by this strategy (no cap applied
    # by Risk Gate).
    max_fillable_qty:          float = 0.0


@dataclass
class ApprovedOpportunityEvent(Event):
    """Emitted by Risk Gate when all pre-trade checks pass."""
    priority:      Priority = Priority.HIGH
    opportunity:   Optional[OpportunityEvent] = None
    approved_size: float = 0.0     # May be reduced from original
    risk_gate_notes: str = ""


@dataclass
class RejectedOpportunityEvent(Event):
    """Emitted by Risk Gate with explicit rejection reason (critical for learning)."""
    opportunity_id: str = ""
    strategy:       str = ""
    reason:         str = ""       # Which check failed
    details:        str = ""       # Specific values that triggered rejection


@dataclass
class TradeExecutedEvent(Event):
    """Emitted by Execution Agent after both legs fill successfully."""
    priority:      Priority = Priority.HIGH
    trade_id:      str = field(default_factory=lambda: str(uuid.uuid4()))
    opportunity_id: str = ""
    strategy:      str = ""
    platform_legs: List[Dict[str, Any]] = field(default_factory=list)
    # Each leg: {platform, market_id, side, ordered_price, filled_price, quantity, fee_paid, fill_time}
    total_fee_paid:  float = 0.0
    expected_pnl_usd: float = 0.0
    paper_mode:      bool = True


@dataclass
class LegFailureEvent(Event):
    """Emitted when a leg fails to fill within timeout — triggers unwind."""
    priority:        Priority = Priority.HIGH
    trade_id:        str = ""
    opportunity_id:  str = ""
    failed_leg:      Dict[str, Any] = field(default_factory=dict)
    filled_legs:     List[Dict[str, Any]] = field(default_factory=list)
    unwind_required: bool = True


@dataclass
class TradeResolvedEvent(Event):
    """Emitted when a prediction market resolves and PnL is finalized."""
    trade_id:       str   = ""
    market_id:      str   = ""
    platform:       str   = ""
    resolution:     str   = ""   # "YES" | "NO" | "DISPUTE"
    realized_pnl:   float = 0.0
    holding_period_hours: float = 0.0


@dataclass
class PositionSnapshot(Event):
    """Published by Position Tracker every 30s and on request."""
    total_capital_usd:     float = 0.0
    deployed_capital_usd:  float = 0.0
    free_capital_usd:      float = 0.0
    open_positions:        List[Dict[str, Any]] = field(default_factory=list)
    unrealized_pnl_usd:    float = 0.0
    correlation_score:     float = 0.0    # 0=uncorrelated, 1=maximally correlated
    daily_pnl_usd:         float = 0.0
    daily_trades:          int   = 0


# ── Risk Events ───────────────────────────────────────────────────────────────

@dataclass
class RiskLimitHitEvent(Event):
    """Emitted when any risk limit is triggered."""
    priority:    Priority = Priority.CRITICAL
    limit_type:  str  = ""    # DAILY_LOSS | WEEKLY_LOSS | MAX_POSITIONS | etc.
    limit_value: float = 0.0
    current_value: float = 0.0
    action_taken: str = ""   # PAUSED | HALTED | REDUCED


@dataclass
class KillSwitchEvent(Event):
    """The nuclear option. Everything stops."""
    priority:  Priority = Priority.CRITICAL
    triggered_by: str = ""    # DASHBOARD | CLI | TELEGRAM | AUTOMATIC
    reason:    str = ""


# ── Research Floor Events ─────────────────────────────────────────────────────

@dataclass
class NewsSignalEvent(Event):
    """Published by News Analyst Agent when relevant news detected."""
    headline:           str   = ""
    source:             str   = ""
    source_credibility: float = 0.5     # 0-1 SCS score for this source/topic
    relevant_markets:   List[str] = field(default_factory=list)
    impact_direction:   str   = "NEUTRAL"   # BULLISH | BEARISH | NEUTRAL
    confidence:         float = 0.0
    is_settlement_arb:  bool  = False       # Is outcome effectively known?
    article_url:        str   = ""


@dataclass
class AnnouncementWarningEvent(Event):
    """Published N minutes before a scheduled macro announcement."""
    announcement_type: str      = ""    # CPI | FOMC | NFP | GDP | etc.
    scheduled_time:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    minutes_until:     int      = 0
    affected_markets:  List[str] = field(default_factory=list)
    action:            str      = "PAUSE"   # PAUSE | CAUTION


@dataclass
class LogicalArbCandidateEvent(Event):
    """Published by Market Analyst when LLM finds a logical dependency mispricing."""
    market_a_id:       str   = ""
    market_a_platform: str   = ""
    market_a_price:    float = 0.0
    market_b_id:       str   = ""
    market_b_platform: str   = ""
    market_b_price:    float = 0.0
    relationship:      str   = ""     # "A_IMPLIES_B" | "MUTUALLY_EXCLUSIVE" | etc.
    logical_constraint: str  = ""     # Human-readable explanation
    implied_edge_pct:  float = 0.0
    llm_confidence:    float = 0.0
    llm_reasoning:     str   = ""


@dataclass
class SmartMoneySignalEvent(Event):
    """Published by Whale Tracker when a high-accuracy wallet establishes a position."""
    wallet_address:       str   = ""
    wallet_win_rate:      float = 0.0   # Historical accuracy
    wallet_trade_count:   int   = 0     # Number of trades used to calculate win rate
    market_id:            str   = ""
    platform:             str   = ""
    position_side:        str   = ""    # "YES" | "NO"
    position_size_usd:    float = 0.0
    current_market_price: float = 0.0


@dataclass
class ImpliedProbabilityDivergenceEvent(Event):
    """Published by Options Signal Agent when options market diverges from prediction market."""
    underlying_asset:      str   = ""
    options_implied_prob:  float = 0.0   # From options chain
    prediction_market_prob: float = 0.0  # From Kalshi/Polymarket
    divergence_pct:        float = 0.0
    prediction_market_id:  str   = ""
    prediction_platform:   str   = ""
    options_expiry:        Optional[datetime] = None
    direction:             str   = ""    # "OPTIONS_HIGHER" | "PREDICTION_HIGHER"


@dataclass
class ResolutionVerificationResult(Event):
    """Published by Resolution Verifier — required before any cross-platform trade."""
    market_a_id:   str  = ""
    market_a_platform: str = ""
    market_b_id:   str  = ""
    market_b_platform: str = ""
    result:        str  = "UNCERTAIN"   # MATCH | MISMATCH | UNCERTAIN
    confidence:    float = 0.0
    explanation:   str  = ""
    key_differences: str = ""    # If MISMATCH, what specifically differs


@dataclass
class GeopoliticalRiskEvent(Event):
    """Published by Geopolitical Agent when risk level changes."""
    region:           str   = ""
    risk_level:       str   = "NORMAL"   # NORMAL | ELEVATED | HIGH | CRITICAL
    risk_score:       float = 0.0        # 0-1
    affected_categories: List[str] = field(default_factory=list)
    trigger:          str   = ""
    recommended_action: str = "NONE"    # NONE | REDUCE_SIZE | PAUSE_CATEGORY | PAUSE_ALL


@dataclass
class CorrelationDiscoveryEvent(Event):
    """Published by Correlation Engine when a new signal-outcome relationship is confirmed."""
    signal_type:        str   = ""
    outcome_category:   str   = ""
    correlation_coeff:  float = 0.0
    p_value:            float = 0.0
    sample_size:        int   = 0
    lag_hours:          float = 0.0     # How far ahead signal leads outcome
    is_butterfly:       bool  = False   # Was this an unexpected cross-domain finding?
    description:        str   = ""


# ── Management Events ─────────────────────────────────────────────────────────

@dataclass
class StrategyWeightUpdateEvent(Event):
    """Published by Reflection Agent after nightly review."""
    strategy_weights:    Dict[str, float] = field(default_factory=dict)
    source_scs_updates:  Dict[str, float] = field(default_factory=dict)
    persona_accuracy_updates: Dict[str, float] = field(default_factory=dict)
    performance_narrative: str = ""


@dataclass
class AgentHeartbeat(Event):
    """Every agent publishes this every 60s. Health Monitor watches for silence."""
    agent_name:      str   = ""
    status:          str   = "OK"    # OK | DEGRADED | ERROR
    messages_processed: int = 0
    last_action:     str   = ""
    error_count:     int   = 0


# ── Compliance Events ─────────────────────────────────────────────────────────

@dataclass
class ComplianceAlertEvent(Event):
    """Published by Compliance Officer when something needs human attention."""
    priority:    Priority = Priority.HIGH
    alert_type:  str = ""    # MNPI_SUSPICION | REGULATORY_CHANGE | VPN_DETECTED | etc.
    description: str = ""
    action_required: str = ""


@dataclass
class RegulatoryAlertEvent(Event):
    """Published by RegulatoryIntelligenceAgent on AI-assessed regulatory items."""
    priority:           Priority = Priority.HIGH
    source_name:        str = ""
    source_url:         str = ""
    matched_keywords:   List[str] = field(default_factory=list)
    alert_count:        int = 0
    # Fields populated by RegulatoryIntelligenceAgent (AI-assessed)
    urgency:            int = 0           # 0=cleared, 1=low … 5=critical
    summary:            str = ""
    affected:           str = ""          # "yes" | "no" | "unclear"
    recommended_action: str = ""
    raw_title:          str = ""
    cycle_type:         str = ""          # "6h" | "weekly"


# ── Notification / Operator Events ────────────────────────────────────────────

@dataclass
class TelegramNotificationEvent(Event):
    """Published by any agent to request a Telegram message to the operator."""
    message:      str = ""
    tier:         int = 2        # 1=critical (always send), 2=trade-level, 3=digest
    event_source: str = ""       # name of the publishing agent


@dataclass
class TelegramPermissionRequestEvent(Event):
    """Published by any agent to request operator permission via Telegram."""
    priority:          Priority = Priority.HIGH
    request_id:        str = field(default_factory=lambda: str(uuid.uuid4()))
    requesting_agent:  str = ""
    question:          str = ""
    timeout_seconds:   int = 300
    default_on_timeout: str = "deny"   # "deny" or "approve"


@dataclass
class TelegramPermissionResponseEvent(Event):
    """Published by TelegramAgent when operator replies or timeout fires.

    Uses inherited `source` field (from Event) to carry "operator" or "timeout".
    response_text carries the operator's raw Telegram message so subscribers
    (e.g., RegulatoryIntelligenceAgent) can inspect it for specific phrases.
    """
    request_id:    str  = ""
    approved:      bool = False
    response_text: str  = ""


# ── Event Bus ─────────────────────────────────────────────────────────────────

Handler = Callable[[Event], Awaitable[None]]
T = TypeVar("T", bound=Event)


class EventBus:
    """
    Central publish/subscribe event bus.

    Features:
    - Type-safe subscriptions
    - Priority queuing (CRITICAL events processed first)
    - Automatic audit logging of all events
    - Dead-letter queue for unhandled events
    - Backpressure detection (warns if queue depth exceeds threshold)
    - Replay mode for backtesting
    """

    def __init__(self, audit_logger=None, replay_mode: bool = False):
        self._handlers: Dict[type, List[Handler]] = defaultdict(list)
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._running = False
        self._audit_logger = audit_logger
        self._replay_mode = replay_mode
        self._dead_letters: List[Event] = []
        self._processed_count = 0
        self._error_count = 0
        self._seq = 0   # Monotonic counter used as tiebreaker in priority queue.

    def subscribe(self, event_type: Type[T], handler: Handler) -> None:
        """Register a handler for a specific event type."""
        self._handlers[event_type].append(handler)
        log.debug("handler_registered",
                  event_type=event_type.__name__,
                  handler=handler.__qualname__)

    def subscribe_many(self, subscriptions: Dict[Type[Event], List[Handler]]) -> None:
        """Register multiple handlers at once (for agent initialization)."""
        for event_type, handlers in subscriptions.items():
            for handler in handlers:
                self.subscribe(event_type, handler)

    async def publish(self, event: Event) -> None:
        """
        Publish an event. Returns immediately — processing is async.
        Priority events jump to the front of the queue.
        """
        # Warn on queue depth
        qsize = self._queue.qsize()
        if qsize > 1000:
            log.warning("event_queue_deep", depth=qsize, event_type=type(event).__name__)

        self._seq += 1
        # 3-tuple: (priority, seq, event). The sequence number breaks ties between
        # same-priority events so heapq never has to compare event objects.
        await self._queue.put((event.priority.value, self._seq, event))

        # Audit log every event
        if self._audit_logger:
            await self._audit_logger.log_event(event)

    async def publish_sync(self, event: Event) -> None:
        """For use from synchronous contexts. Same as publish but blocking-safe."""
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(self.publish(event))
        else:
            await self.publish(event)

    async def run(self) -> None:
        """Main event processing loop. Runs until stopped."""
        self._running = True
        log.info("event_bus_started")

        while self._running:
            try:
                _, _, event = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0
                )
                await self._dispatch(event)
                self._queue.task_done()
                self._processed_count += 1
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._error_count += 1
                log.error("event_bus_dispatch_error", error=str(e))

    async def _dispatch(self, event: Event) -> None:
        """Dispatch event to all registered handlers."""
        handlers = self._handlers.get(type(event), [])

        if not handlers:
            self._dead_letters.append(event)
            log.debug("dead_letter", event_type=type(event).__name__,
                      event_id=event.event_id)
            return

        # Run all handlers concurrently
        results = await asyncio.gather(
            *[self._safe_call(h, event) for h in handlers],
            return_exceptions=True
        )

        for result in results:
            if isinstance(result, Exception):
                self._error_count += 1
                log.error("handler_error",
                          event_type=type(event).__name__,
                          error=str(result))

    async def _safe_call(self, handler: Handler, event: Event) -> None:
        """Call handler with error isolation."""
        try:
            await handler(event)
        except Exception as e:
            log.error("handler_exception",
                      handler=handler.__qualname__,
                      event_type=type(event).__name__,
                      error=str(e))
            raise

    async def stop(self) -> None:
        """Graceful shutdown — drain the queue first."""
        log.info("event_bus_stopping",
                 queued_events=self._queue.qsize())
        self._running = False
        # Drain remaining events (with timeout)
        try:
            await asyncio.wait_for(self._queue.join(), timeout=5.0)
        except asyncio.TimeoutError:
            log.warning("event_bus_drain_timeout",
                        remaining=self._queue.qsize())
        log.info("event_bus_stopped",
                 total_processed=self._processed_count,
                 total_errors=self._error_count)

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "processed": self._processed_count,
            "errors":    self._error_count,
            "queued":    self._queue.qsize(),
            "dead_letters": len(self._dead_letters),
            "subscribers": {k.__name__: len(v)
                           for k, v in self._handlers.items()},
        }
