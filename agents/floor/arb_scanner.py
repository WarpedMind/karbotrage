"""
karbot/agents/floor/arb_scanner.py
────────────────────────────────────
Arb Scanner Agent — Trading Floor

The fastest agent in the system. Subscribes to every PriceUpdateEvent
and runs all enabled strategy checks on every tick.

Speed requirement: detect and publish in under 5ms from price update.
No blocking I/O. No LLM calls. Pure in-memory math.

Strategy implementations:
  S1: Single-market YES+NO rebalancing (same platform)
  S2: Cross-platform simple arbitrage (Kalshi ↔ Polymarket)
  S3: Logical/semantic arb (triggered by LogicalArbCandidateEvents from Market Analyst)
  S4: Settlement arb (triggered by NewsSignalEvents from News Analyst)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import structlog

from karbot.core.config import KarbotConfig
from karbot.core.events import (
    EventBus, PriceUpdateEvent, OpportunityEvent,
    LogicalArbCandidateEvent, NewsSignalEvent,
    AgentHeartbeat, StrategyWeightUpdateEvent, Priority
)

log = structlog.get_logger(__name__)


# ── Fee models ────────────────────────────────────────────────────────────────

class KalshiFeeModel:
    """
    Kalshi's real published taker fee schedule (confirmed 2026-07-13 against
    Kalshi's official fee schedule/help center): fee per contract =
    round(0.07 * price * (1 - price), 2) dollars, where price is the
    contract price in dollars. Peaks at 1.75% on a 50c contract, falls
    toward zero at the extremes (near 1c or 99c). Maker orders (resting
    limit orders) pay ~25% of the taker rate — not modeled here, since S1
    always crosses the spread (prices off the ask — see DECISIONS.md
    Session 26, "S1 arb formula uses BID prices for both legs of a BUY
    trade"), a taker action, so the taker rate applies.

    Replaces a prior flat-14%-of-trade-value approximation (found
    2026-07-13 while assessing strategy viability after the S1 pricing
    fix) that overstated real fees by roughly 4-8x for a typical
    near-the-money contract, likely causing the system to reject
    genuinely profitable small edges as "not enough to cover fees."
    """
    TAKER_FEE_MULTIPLIER = 0.07

    @classmethod
    def taker_fee_fraction(cls, price: float) -> float:
        """Fee as a fraction of $1 face value for one contract at `price`."""
        if price <= 0.0 or price >= 1.0:
            return 0.0
        return cls.TAKER_FEE_MULTIPLIER * price * (1.0 - price)

    @classmethod
    def estimate_fee_pct(cls, yes_price: float, no_price: float) -> float:
        """Estimate total taker fee (both legs) as a fraction of $1 face value."""
        return cls.taker_fee_fraction(yes_price) + cls.taker_fee_fraction(no_price)


class PolymarketFeeModel:
    """
    Polymarket fee: 2% on winning positions only.
    Gas fees on Polygon also apply.
    """
    WIN_FEE_PCT = 0.02    # 2% on winning side payout
    EST_GAS_USD = 0.15    # Estimated gas cost per transaction (post CTF V2)

    @classmethod
    def estimate_fee_pct(cls, position_size_usd: float) -> float:
        """Estimate total fee as percentage of trade value."""
        if position_size_usd <= 0:
            return 1.0
        # Win fee: on average you win 50% of trades
        avg_win_fee = cls.WIN_FEE_PCT * 0.5
        gas_pct     = cls.EST_GAS_USD / position_size_usd
        return avg_win_fee + gas_pct


# ── Opportunity cache ─────────────────────────────────────────────────────────

class OpportunityCache:
    """
    Prevents publishing duplicate opportunities within a short window.
    Without this, every price tick would flood the bus with the same opportunity.
    """

    def __init__(self, ttl_seconds: float = 5.0):
        self._cache: Dict[str, float] = {}   # opp_key → expiry_time
        self._ttl = ttl_seconds

    def seen(self, key: str) -> bool:
        now = time.monotonic()
        if key in self._cache and self._cache[key] > now:
            return True
        return False

    def mark(self, key: str) -> None:
        self._cache[key] = time.monotonic() + self._ttl

    def _cleanup(self) -> None:
        now = time.monotonic()
        expired = [k for k, v in self._cache.items() if v <= now]
        for k in expired:
            del self._cache[k]

    def _opp_key(self, strategy: str, market_ids: List[str]) -> str:
        return f"{strategy}:{'|'.join(sorted(market_ids))}"


# ── Arb Scanner Agent ─────────────────────────────────────────────────────────

class ArbScannerAgent:
    """
    Trading Floor Agent #2 — opportunity detection.

    Subscribes to:
      - PriceUpdateEvent (from Price Watcher)
      - LogicalArbCandidateEvent (from Market Analyst)
      - NewsSignalEvent (from News Analyst, for settlement arb)
      - StrategyWeightUpdateEvent (from Reflection Agent)

    Publishes:
      - OpportunityEvent (to Risk Gate)
    """

    AGENT_NAME = "arb_scanner"
    HEARTBEAT_INTERVAL = 60
    CACHE_CLEANUP_INTERVAL = 60

    def __init__(
        self,
        config: KarbotConfig,
        event_bus: EventBus,
    ):
        self.config = config
        self.bus    = event_bus
        self._cfg_s = config.strategies

        # Price cache: platform → market_id → latest PriceUpdateEvent
        self._prices: Dict[str, Dict[str, PriceUpdateEvent]] = {
            "kalshi":    {},
            "polymarket": {},
        }

        # Logical arb candidates from Market Analyst
        self._logical_candidates: List[LogicalArbCandidateEvent] = []

        # Opportunity dedup cache
        self._opp_cache = OpportunityCache(ttl_seconds=5.0)

        # Strategy weights (updated by Reflection Agent)
        self._strategy_weights: Dict[str, float] = {
            "S1": 1.0, "S2": 1.0, "S3": 1.0,
            "S4": 1.0, "S5": 0.0, "S6": 1.0,
        }

        # Stats
        self._scans = 0
        self._opportunities_found = 0
        self._last_opportunity_time: Optional[float] = None

    def register_subscriptions(self) -> None:
        """Register all event subscriptions with the bus."""
        self.bus.subscribe(PriceUpdateEvent, self._on_price_update)
        self.bus.subscribe(LogicalArbCandidateEvent, self._on_logical_candidate)
        self.bus.subscribe(NewsSignalEvent, self._on_news_signal)
        self.bus.subscribe(StrategyWeightUpdateEvent, self._on_weight_update)

    async def start(self) -> None:
        self.register_subscriptions()
        asyncio.create_task(self._heartbeat_loop(), name="arb_heartbeat")
        asyncio.create_task(self._cache_cleanup_loop(), name="arb_cleanup")
        log.info("arb_scanner_started")

    # ── Event Handlers ────────────────────────────────────────────────────────

    async def _on_price_update(self, event: PriceUpdateEvent) -> None:
        """
        HOT PATH — called on every price tick.
        Update price cache, then run all enabled strategies.
        Target: complete in under 5ms.
        """
        # Update cache
        self._prices[event.platform][event.market_id] = event
        self._scans += 1

        # Run strategies (in priority order)
        opportunities = []

        if self._cfg_s.s1_rebalancing_enabled:
            opp = self._check_s1_rebalancing(event)
            if opp:
                opportunities.append(opp)

        if (self._cfg_s.s2_cross_platform_enabled and
                self.config.capital.phase >= 2):
            opp = self._check_s2_cross_platform(event)
            if opp:
                opportunities.append(opp)

        # Publish all found opportunities
        for opp in opportunities:
            self._opportunities_found += 1
            self._last_opportunity_time = time.monotonic()
            await self.bus.publish(opp)

    async def _on_logical_candidate(self, event: LogicalArbCandidateEvent) -> None:
        """Receive logical arb candidate from Market Analyst."""
        self._logical_candidates.append(event)
        # Trim old candidates (keep last 100)
        if len(self._logical_candidates) > 100:
            self._logical_candidates = self._logical_candidates[-100:]

        # Check if this is currently viable
        opp = self._check_s3_logical(event)
        if opp:
            self._opportunities_found += 1
            await self.bus.publish(opp)

    async def _on_news_signal(self, event: NewsSignalEvent) -> None:
        """Check settlement arb opportunities from news signals."""
        if not self._cfg_s.s4_settlement_arb_enabled:
            return
        if not event.is_settlement_arb:
            return

        for market_id in event.relevant_markets:
            opp = self._check_s4_settlement(event, market_id)
            if opp:
                self._opportunities_found += 1
                await self.bus.publish(opp)

    async def _on_weight_update(self, event: StrategyWeightUpdateEvent) -> None:
        """Update strategy weights from Reflection Agent."""
        self._strategy_weights.update(event.strategy_weights)
        log.info("strategy_weights_updated", weights=self._strategy_weights)

    # ── Strategy Implementations ──────────────────────────────────────────────

    def _check_s1_rebalancing(
        self, event: PriceUpdateEvent
    ) -> Optional[OpportunityEvent]:
        """
        S1: Single-market YES+NO rebalancing.

        Core logic:
          YES_ask + NO_ask < 1.00 means you can BUY both and guarantee $1 payout
          Net profit = (1.00 - YES_ask - NO_ask) - fees - slippage

        Uses ASK prices, not bid prices — bids are what other participants
        are willing to pay, not prices this system can buy at. A prior
        version of this function used yes_bid/no_bid directly, which
        inverts the sign of the real, executable P&L (see DECISIONS.md,
        Session 26, "S1 arb formula uses BID prices for both legs of a BUY
        trade" — fixed 2026-07-13).

        This is the safest strategy: both legs on same platform, no leg risk.
        """
        if event.platform != "kalshi":
            return None   # S1 on Kalshi only for Phase 1

        yes_ask = event.yes_ask
        no_ask  = event.no_ask

        if yes_ask <= 0 or no_ask <= 0:
            return None

        combined_cost = yes_ask + no_ask

        # Gross profit
        gross_pct = (1.0 - combined_cost) * 100

        if gross_pct <= 0:
            return None

        # Fee estimation — per-leg, since Kalshi's real fee is price-dependent
        # (peaks near a 50c price, falls toward zero at the extremes) and the
        # two legs are rarely priced the same.
        yes_fee_frac = KalshiFeeModel.taker_fee_fraction(yes_ask)
        no_fee_frac  = KalshiFeeModel.taker_fee_fraction(no_ask)
        fee_pct = (yes_fee_frac + no_fee_frac) * 100
        slippage_pct = self.config.risk.max_slippage_pct

        net_pct = gross_pct - fee_pct - slippage_pct

        # Viability visibility: log every candidate that clears zero gross
        # spread, regardless of whether it clears the trading threshold.
        # Added 2026-07-13 in response to the operator asking how long to
        # wait before judging whether S1 is a viable strategy — before this,
        # a near-miss (e.g. net_pct=3% against a ~5.3% Kelly floor) was
        # silently discarded with zero visibility into how close real
        # markets are getting. Real markets structurally keep
        # yes_ask+no_ask >= 1 most of the time (see DECISIONS.md Session 26),
        # so gross_pct>0 candidates should be rare — this should not be
        # noisy in practice. INFO level (not DEBUG) so it's visible in
        # production without re-enabling debug logging (see this same
        # session's disk-fill outage for why DEBUG stays off in prod).
        log.info("s1_candidate_seen",
                 market=event.market_id,
                 gross_pct=round(gross_pct, 3),
                 fee_pct=round(fee_pct, 3),
                 net_pct=round(net_pct, 3),
                 cleared_min_profit=net_pct >= self._cfg_s.s1_min_net_profit_pct,
                 min_required=self._cfg_s.s1_min_net_profit_pct)

        if net_pct < self._cfg_s.s1_min_net_profit_pct:
            return None

        if net_pct > self._cfg_s.s1_max_net_profit_pct:
            # A real S1 arb on a liquid market doesn't exceed low single
            # digits. This size almost always means the order book is
            # stale/corrupt or the quote is backed by negligible depth, not
            # a genuine opportunity — log loudly and skip rather than trade
            # on it blindly.
            log.warning("s1_opportunity_exceeds_sanity_ceiling",
                        market=event.market_id,
                        net_pct=net_pct,
                        yes_ask=yes_ask,
                        no_ask=no_ask,
                        ceiling=self._cfg_s.s1_max_net_profit_pct)
            return None

        # Liquidity: cap size to what's actually resting at the quoted ask
        # price on each leg. Top-of-book only, not a multi-level walk —
        # live-confirmed 2026-07-13 that a mathematically valid edge can be
        # backed by as little as 1 contract; sizing off the quote alone
        # (as this function previously did) trades against liquidity that
        # doesn't exist.
        yes_ask_size = event.yes_ask_depth[0][1] if event.yes_ask_depth else 0.0
        no_ask_size  = event.no_ask_depth[0][1] if event.no_ask_depth else 0.0
        max_fillable_qty = min(yes_ask_size, no_ask_size)

        if max_fillable_qty <= 0:
            return None

        # Dedup check
        opp_key = f"S1:{event.market_id}"
        if self._opp_cache.seen(opp_key):
            return None
        self._opp_cache.mark(opp_key)

        if self._cfg_s.s1_canary_mode:
            # See DECISIONS.md/CLAUDE.md Session 28: S1 is structurally
            # impossible on a real Kalshi book (a resting yes_ask+no_ask<1
            # is a crossed book, which cannot rest). This is real data —
            # useful for spotting book-reconstruction bugs — but not a
            # tradeable opportunity. Log it and stop here; never publish
            # an OpportunityEvent that RiskGate/PaperExecutor could act on.
            log.info("s1_opportunity_found_canary_only",
                     market=event.market_id,
                     net_pct=net_pct,
                     yes_ask=yes_ask,
                     no_ask=no_ask,
                     max_fillable_qty=max_fillable_qty)
            return None

        log.debug("s1_opportunity_found",
                  market=event.market_id,
                  net_pct=net_pct,
                  yes_ask=yes_ask,
                  no_ask=no_ask,
                  max_fillable_qty=max_fillable_qty)

        return OpportunityEvent(
            source            = self.AGENT_NAME,
            priority          = Priority.HIGH,
            strategy          = "S1_REBALANCING",
            legs              = [
                {
                    "platform":   "kalshi",
                    "market_id":  event.market_id,
                    "side":       "YES",
                    "price":      yes_ask,
                    "quantity":   0,    # Sized by Risk Gate based on capital
                    "fee_estimate": yes_fee_frac,
                },
                {
                    "platform":   "kalshi",
                    "market_id":  event.market_id,
                    "side":       "NO",
                    "price":      no_ask,
                    "quantity":   0,
                    "fee_estimate": no_fee_frac,
                },
            ],
            gross_profit_pct      = gross_pct,
            estimated_fees_pct    = fee_pct,
            estimated_slippage_pct = slippage_pct,
            net_profit_pct        = net_pct,
            confidence            = self._confidence_from_net(net_pct),
            detected_at           = datetime.now(timezone.utc),
            max_fillable_qty      = max_fillable_qty,
        )

    def _check_s2_cross_platform(
        self, event: PriceUpdateEvent
    ) -> Optional[OpportunityEvent]:
        """
        S2: Cross-platform simple arbitrage.

        Logic:
          Buy YES on platform A (where YES is cheaper)
          Buy NO on platform B (where NO is cheaper)
          Combined cost < $1.00 → guaranteed profit

        Risk: leg risk (one side fills, other doesn't).
        Mitigation: Resolution Verifier must confirm criteria match before execution.
        """
        if self.config.capital.phase < 2:
            return None

        # Find matching market on the other platform
        my_platform = event.platform
        other_platform = "polymarket" if my_platform == "kalshi" else "kalshi"

        # Look for same market on other platform
        # In production: use market matcher to align equivalent markets
        # Simplified: look for exact market_id match (won't always work cross-platform)
        other_prices = self._prices.get(other_platform, {})
        other_event  = other_prices.get(event.market_id)

        if not other_event:
            return None

        # Cross-platform: buy YES on cheaper platform, NO on other
        # Option A: YES on event.platform + NO on other
        cost_a = event.yes_bid + other_event.no_bid
        profit_a = (1.0 - cost_a) * 100 if cost_a < 1.0 else 0

        # Option B: NO on event.platform + YES on other
        cost_b = event.no_bid + other_event.yes_bid
        profit_b = (1.0 - cost_b) * 100 if cost_b < 1.0 else 0

        best_profit = max(profit_a, profit_b)
        if best_profit <= 0:
            return None

        # Fees from both platforms
        kalshi_fee = KalshiFeeModel.estimate_fee_pct(0.5, 0.5) * 100
        poly_fee   = PolymarketFeeModel.estimate_fee_pct(100) * 100
        total_fee  = kalshi_fee + poly_fee
        slippage   = self.config.risk.max_slippage_pct * 2  # Two platforms

        net_pct = best_profit - total_fee - slippage

        if net_pct < self._cfg_s.s2_min_net_profit_pct:
            return None

        opp_key = f"S2:{event.market_id}"
        if self._opp_cache.seen(opp_key):
            return None
        self._opp_cache.mark(opp_key)

        if profit_a >= profit_b:
            legs = [
                {"platform": my_platform,    "market_id": event.market_id,
                 "side": "YES", "price": event.yes_bid, "quantity": 0, "fee_estimate": kalshi_fee/200},
                {"platform": other_platform, "market_id": event.market_id,
                 "side": "NO",  "price": other_event.no_bid, "quantity": 0, "fee_estimate": poly_fee/200},
            ]
        else:
            legs = [
                {"platform": my_platform,    "market_id": event.market_id,
                 "side": "NO",  "price": event.no_bid, "quantity": 0, "fee_estimate": kalshi_fee/200},
                {"platform": other_platform, "market_id": event.market_id,
                 "side": "YES", "price": other_event.yes_bid, "quantity": 0, "fee_estimate": poly_fee/200},
            ]

        return OpportunityEvent(
            source                   = self.AGENT_NAME,
            priority                 = Priority.HIGH,
            strategy                 = "S2_CROSS_PLATFORM",
            legs                     = legs,
            gross_profit_pct         = best_profit,
            estimated_fees_pct       = total_fee,
            estimated_slippage_pct   = slippage,
            net_profit_pct           = net_pct,
            confidence               = self._confidence_from_net(net_pct),
            resolution_criteria_match = None,    # Must be verified by Resolution Verifier
            detected_at              = datetime.now(timezone.utc),
        )

    def _check_s3_logical(
        self, candidate: LogicalArbCandidateEvent
    ) -> Optional[OpportunityEvent]:
        """
        S3: Logical/semantic arbitrage.

        Example: "Trump wins" at 35% but "Republican wins" at 32%.
        Since Trump is a Republican, Republican wins >= Trump wins.
        The 3-point gap is a logical impossibility → arbitrage.

        These persist longer than pure price arb (minutes to hours vs. seconds).
        Speed is less critical; reasoning quality is critical.
        """
        if not self._cfg_s.s3_logical_arb_enabled:
            return None

        # Check if implied edge still exists in current prices
        market_a_price = self._get_current_price(
            candidate.market_a_platform, candidate.market_a_id, "YES"
        )
        market_b_price = self._get_current_price(
            candidate.market_b_platform, candidate.market_b_id, "YES"
        )

        if market_a_price is None or market_b_price is None:
            return None

        # Recalculate edge with current prices
        # If A implies B, then P(B) >= P(A). If P(B) < P(A), buy B.
        if candidate.relationship == "A_IMPLIES_B":
            if market_b_price >= market_a_price:
                return None  # Price already correct
            edge_pct = (market_a_price - market_b_price) * 100
        else:
            return None  # Other relationships handled differently

        if edge_pct < self._cfg_s.s3_min_edge_pct:
            return None

        # Confidence check
        if (self._cfg_s.s3_min_confidence == "HIGH" and
                candidate.llm_confidence < 0.8):
            return None

        opp_key = f"S3:{candidate.market_a_id}:{candidate.market_b_id}"
        if self._opp_cache.seen(opp_key):
            return None
        self._opp_cache.mark(opp_key)

        return OpportunityEvent(
            source           = self.AGENT_NAME,
            strategy         = "S3_LOGICAL_ARB",
            legs             = [
                {
                    "platform":  candidate.market_b_platform,
                    "market_id": candidate.market_b_id,
                    "side":      "YES",
                    "price":     market_b_price,
                    "quantity":  0,
                    "reasoning": candidate.logical_constraint,
                }
            ],
            gross_profit_pct     = edge_pct,
            estimated_fees_pct   = KalshiFeeModel.estimate_fee_pct(market_b_price, 0) * 100,
            estimated_slippage_pct = self.config.risk.max_slippage_pct,
            net_profit_pct       = edge_pct * 0.7,   # Conservative estimate
            confidence           = "HIGH" if candidate.llm_confidence > 0.8 else "MEDIUM",
            detected_at          = datetime.now(timezone.utc),
        )

    def _check_s4_settlement(
        self, news: NewsSignalEvent, market_id: str
    ) -> Optional[OpportunityEvent]:
        """
        S4: Settlement arbitrage.
        Outcome is effectively known from news but market hasn't fully repriced.
        """
        if not self._cfg_s.s4_settlement_arb_enabled:
            return None

        # Find current price for this market
        for platform in ["kalshi", "polymarket"]:
            price_event = self._prices.get(platform, {}).get(market_id)
            if not price_event:
                continue

            # If outcome is YES and market price is below 90%, there may be edge
            if news.impact_direction == "BULLISH" and price_event.yes_bid < 0.90:
                edge_pct = (0.95 - price_event.yes_bid) * 100
                if edge_pct > 2.0:
                    return OpportunityEvent(
                        source           = self.AGENT_NAME,
                        strategy         = "S4_SETTLEMENT_ARB",
                        legs             = [{
                            "platform":  platform,
                            "market_id": market_id,
                            "side":      "YES",
                            "price":     price_event.yes_bid,
                            "quantity":  0,
                        }],
                        gross_profit_pct     = edge_pct,
                        estimated_fees_pct   = 1.0,
                        estimated_slippage_pct = 0.5,
                        net_profit_pct       = edge_pct - 1.5,
                        confidence           = news.confidence >= 0.8 and "HIGH" or "MEDIUM",
                        detected_at          = datetime.now(timezone.utc),
                    )

        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_current_price(
        self, platform: str, market_id: str, side: str
    ) -> Optional[float]:
        """Get current bid price for a market/side combination."""
        price_event = self._prices.get(platform, {}).get(market_id)
        if not price_event:
            return None
        return price_event.yes_bid if side == "YES" else price_event.no_bid

    @staticmethod
    def _confidence_from_net(net_pct: float) -> str:
        """Convert net profit percentage to confidence level."""
        if net_pct >= 2.0:
            return "HIGH"
        elif net_pct >= 0.5:
            return "MEDIUM"
        else:
            return "LOW"

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            await self.bus.publish(AgentHeartbeat(
                source             = self.AGENT_NAME,
                agent_name         = self.AGENT_NAME,
                status             = "OK",
                messages_processed = self._scans,
                last_action        = f"found_{self._opportunities_found}_opportunities",
            ))

    async def _cache_cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(self.CACHE_CLEANUP_INTERVAL)
            self._opp_cache._cleanup()
            # Also prune old logical candidates (older than 6 hours)
            # TODO: implement time-based pruning

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "scans":               self._scans,
            "opportunities_found": self._opportunities_found,
            "markets_tracked":     sum(len(v) for v in self._prices.values()),
            "strategy_weights":    self._strategy_weights,
        }


# ── karbot_runner.py-compatible stub ─────────────────────────────────────────

class ArbScanner(ArbScannerAgent):
    """
    BaseAgent-conforming class used by karbot_runner.py.
    Inherits the full ArbScannerAgent implementation.
    register_subscriptions() (from superclass) wires PriceUpdateEvent etc.
    run() starts the heartbeat and cache-cleanup background tasks.
    """

    def __init__(self, bus: EventBus, config: KarbotConfig):
        super().__init__(config=config, event_bus=bus)

    async def run(self) -> None:
        asyncio.create_task(self._heartbeat_loop(),      name="arb_heartbeat")
        asyncio.create_task(self._cache_cleanup_loop(),  name="arb_cache_cleanup")
        log.info("arb_scanner_started", paper_mode=self.config.system.paper_mode)
        # Subscriptions handle all incoming events; keep this task alive.
        while True:
            await asyncio.sleep(3600)
