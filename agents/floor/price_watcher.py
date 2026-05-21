"""
karbot/agents/floor/price_watcher.py
─────────────────────────────────────
Price Watcher Agent — Trading Floor

Maintains persistent WebSocket connections to all enabled platforms.
Reconstructs full order books from incremental delta updates.
Publishes normalized PriceUpdateEvents on every tick.

This is the most latency-critical agent in the system.
All logic here must be O(1) or O(log n) — no heavy computation.

CRITICAL: BookBuilder must correctly apply deltas in sequence.
A bug here silently corrupts ALL downstream pricing.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import structlog
import websockets
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log
)

from karbot.core.config import KarbotConfig
from karbot.core.events import (
    EventBus, PriceUpdateEvent, BookSnapshotEvent,
    FeedHealthEvent, AgentHeartbeat, Priority
)

log = structlog.get_logger(__name__)


# ── Order Book Reconstruction ─────────────────────────────────────────────────

@dataclass
class OrderBookLevel:
    price: float
    size:  float


class OrderBook:
    """
    Maintains a full order book for a single market.
    Applies incremental WebSocket delta updates correctly.

    The key insight: WebSocket feeds deliver CHANGES, not full state.
    We must accumulate deltas to reconstruct the full book.
    Sequence numbers ensure we detect and handle gaps.
    """

    def __init__(self, market_id: str, platform: str):
        self.market_id  = market_id
        self.platform   = platform
        self.bids:       Dict[float, float] = {}   # price → size
        self.asks:       Dict[float, float] = {}
        self.sequence:   int   = 0
        self.last_update: float = 0.0
        self._gap_detected = False

    def apply_snapshot(self, bids: List[Tuple], asks: List[Tuple], seq: int) -> None:
        """Full book reset from snapshot. Called on connect/reconnect."""
        self.bids = {price: size for price, size in bids}
        self.asks = {price: size for price, size in asks}
        self.sequence = seq
        self.last_update = time.monotonic()
        self._gap_detected = False
        log.debug("book_snapshot_applied",
                  market=self.market_id,
                  bid_levels=len(self.bids),
                  ask_levels=len(self.asks))

    def apply_delta(self, side: str, price: float, size: float, seq: int) -> bool:
        """
        Apply a single delta update.
        Returns True if applied cleanly, False if sequence gap detected.
        """
        # Check for sequence gaps
        if seq != self.sequence + 1 and self.sequence != 0:
            log.warning("sequence_gap_detected",
                        market=self.market_id,
                        expected=self.sequence + 1,
                        received=seq)
            self._gap_detected = True
            return False

        book = self.bids if side == "bid" else self.asks

        if size == 0:
            book.pop(price, None)    # Remove level
        else:
            book[price] = size       # Add or update level

        self.sequence = seq
        self.last_update = time.monotonic()
        return True

    @property
    def best_bid(self) -> Optional[float]:
        return max(self.bids.keys()) if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return min(self.asks.keys()) if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        bb, ba = self.best_bid, self.best_ask
        if bb is not None and ba is not None:
            return (bb + ba) / 2
        return None

    @property
    def needs_reset(self) -> bool:
        """True if a sequence gap was detected — needs snapshot refresh."""
        return self._gap_detected

    def to_price_event(self, platform: str) -> PriceUpdateEvent:
        """Convert current book state to a PriceUpdateEvent."""
        # For Kalshi binary contracts: YES bid/ask and NO bid/ask
        # YES price + NO price should ≈ 1.00 in efficient market
        yes_bid = self.best_bid or 0.0
        yes_ask = self.best_ask or 0.0

        return PriceUpdateEvent(
            source       = f"price_watcher_{platform}",
            platform     = platform,
            market_id    = self.market_id,
            yes_bid      = yes_bid,
            yes_ask      = yes_ask,
            no_bid       = round(1.0 - yes_ask, 4),  # NO bid = 1 - YES ask
            no_ask       = round(1.0 - yes_bid, 4),  # NO ask = 1 - YES bid
            sequence_num = self.sequence,
        )


# ── Kalshi WebSocket Client ───────────────────────────────────────────────────

class KalshiWebSocketClient:
    """
    Connects to Kalshi WebSocket API.
    Handles authentication, reconnection, and message routing.

    Kalshi Auth: HMAC-SHA256 signed requests.
    Docs: https://trading-api.kalshi.com/trade-api/v2
    """

    WS_URL = "wss://trading-api.kalshi.com/trade-api/ws/v2"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        on_price_update: Any,
        on_snapshot: Any,
        on_health: Any,
    ):
        self.api_key       = api_key
        self.api_secret    = api_secret
        self._on_price     = on_price_update
        self._on_snapshot  = on_snapshot
        self._on_health    = on_health
        self._ws           = None
        self._connected    = False
        self._subscribed_markets: set = set()
        self._msg_count    = 0
        self._last_msg_time = time.monotonic()

    def _auth_headers(self) -> Dict[str, str]:
        """Generate HMAC-SHA256 authentication headers for Kalshi."""
        import hmac, hashlib, time as _time
        ts = str(int(_time.time() * 1000))
        msg = ts + "GET" + "/trade-api/ws/v2"
        sig = hmac.new(
            self.api_secret.encode(),
            msg.encode(),
            hashlib.sha256
        ).hexdigest()
        return {
            "KALSHI-ACCESS-KEY":       self.api_key,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }

    async def connect(self) -> None:
        """Connect and authenticate to Kalshi WebSocket."""
        headers = self._auth_headers()
        self._ws = await websockets.connect(
            self.WS_URL,
            extra_headers=headers,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
        )
        self._connected = True
        log.info("kalshi_ws_connected")
        await self._on_health("kalshi", True, 0.0)

    async def subscribe_markets(self, market_ids: List[str]) -> None:
        """Subscribe to orderbook updates for specific markets."""
        for market_id in market_ids:
            if market_id not in self._subscribed_markets:
                msg = {
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["orderbook_delta"],
                        "market_tickers": [market_id]
                    }
                }
                await self._ws.send(json.dumps(msg))
                self._subscribed_markets.add(market_id)

    async def listen(self) -> None:
        """Process incoming messages indefinitely."""
        async for raw_msg in self._ws:
            self._msg_count += 1
            self._last_msg_time = time.monotonic()
            try:
                msg = json.loads(raw_msg)
                await self._route_message(msg)
            except json.JSONDecodeError:
                log.error("kalshi_ws_bad_json", msg=raw_msg[:200])
            except Exception as e:
                log.error("kalshi_ws_message_error", error=str(e))

    async def _route_message(self, msg: Dict) -> None:
        """Route incoming message to appropriate handler."""
        msg_type = msg.get("type", "")

        if msg_type == "orderbook_snapshot":
            await self._on_snapshot("kalshi", msg)
        elif msg_type == "orderbook_delta":
            await self._on_price("kalshi", msg)
        elif msg_type == "subscribed":
            log.debug("kalshi_subscribed", channels=msg.get("params", {}).get("channels"))
        elif msg_type == "error":
            log.error("kalshi_ws_error", error=msg.get("msg", "unknown"))

    async def disconnect(self) -> None:
        if self._ws:
            await self._ws.close()
        self._connected = False


# ── Price Watcher Agent ───────────────────────────────────────────────────────

class PriceWatcherAgent:
    """
    Trading Floor Agent #1 — the data foundation.

    Responsibilities:
    - Maintain WebSocket connections to all enabled platforms
    - Reconstruct order books from incremental deltas
    - Detect and recover from sequence gaps
    - Publish normalized PriceUpdateEvents on every tick
    - Monitor feed health and alert on silence

    Speed requirement: Must publish within 5ms of receiving a WebSocket message.
    No blocking I/O, no LLM calls, no external HTTP in the hot path.
    """

    AGENT_NAME = "price_watcher"
    HEARTBEAT_INTERVAL = 60      # seconds
    HEALTH_CHECK_INTERVAL = 30   # seconds
    SILENCE_ALERT_THRESHOLD = 30 # seconds — alert if no messages

    def __init__(
        self,
        config: KarbotConfig,
        secrets,
        event_bus: EventBus,
    ):
        self.config     = config
        self.secrets    = secrets
        self.bus        = event_bus
        self._books:    Dict[str, OrderBook] = {}     # market_id → OrderBook
        self._running   = False
        self._tasks:    List[asyncio.Task] = []
        self._kalshi_client: Optional[KalshiWebSocketClient] = None
        self._msg_counts: Dict[str, int] = defaultdict(int)
        self._last_msg_times: Dict[str, float] = defaultdict(float)

    async def start(self) -> None:
        """Start all feed connections."""
        self._running = True
        log.info("price_watcher_starting")

        tasks = [
            asyncio.create_task(self._heartbeat_loop(), name="pw_heartbeat"),
            asyncio.create_task(self._health_monitor(), name="pw_health"),
        ]

        if self.config.data_feeds.kalshi_ws_enabled:
            tasks.append(
                asyncio.create_task(
                    self._kalshi_connection_loop(), name="pw_kalshi"
                )
            )

        if (self.config.data_feeds.polymarket_ws_enabled and
                self.config.system.paper_mode is False and
                self.config.capital.phase >= 2):
            tasks.append(
                asyncio.create_task(
                    self._polymarket_connection_loop(), name="pw_polymarket"
                )
            )

        self._tasks = tasks
        await asyncio.gather(*tasks)

    async def stop(self) -> None:
        """Graceful shutdown."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._kalshi_client:
            await self._kalshi_client.disconnect()
        log.info("price_watcher_stopped")

    @retry(
        stop=stop_after_attempt(10),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception_type((websockets.exceptions.WebSocketException,
                                       ConnectionError, OSError)),
        before_sleep=before_sleep_log(log, "WARNING"),
    )
    async def _kalshi_connection_loop(self) -> None:
        """Connect to Kalshi WebSocket with automatic reconnection."""
        try:
            self._kalshi_client = KalshiWebSocketClient(
                api_key    = self.secrets.kalshi_api_key,
                api_secret = self.secrets.kalshi_api_secret,
                on_price_update = self._handle_kalshi_delta,
                on_snapshot     = self._handle_kalshi_snapshot,
                on_health       = self._handle_health_change,
            )
            await self._kalshi_client.connect()

            # Subscribe to all active markets
            markets = await self._fetch_active_kalshi_markets()
            await self._kalshi_client.subscribe_markets(markets)

            # Listen for messages
            await self._kalshi_client.listen()

        except Exception as e:
            log.error("kalshi_ws_error", error=str(e))
            await self._handle_health_change("kalshi", False, 0.0)
            raise

    async def _fetch_active_kalshi_markets(self) -> List[str]:
        """Fetch list of active Kalshi markets via REST API."""
        headers = {
            "Authorization": f"Bearer {self.secrets.kalshi_api_key}",
            "Content-Type": "application/json",
        }
        url = "https://trading-api.kalshi.com/trade-api/v2/markets"
        params = {
            "status": "open",
            "limit": 200,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    markets = data.get("markets", [])
                    # Filter by minimum volume
                    active = [
                        m["ticker"]
                        for m in markets
                        if m.get("volume_24h", 0) > 100
                    ]
                    log.info("kalshi_markets_fetched", count=len(active))
                    return active
                else:
                    log.error("kalshi_markets_fetch_failed", status=resp.status)
                    return []

    async def _handle_kalshi_snapshot(self, platform: str, msg: Dict) -> None:
        """Process a full order book snapshot."""
        market_id = msg.get("market_ticker", "")
        if not market_id:
            return

        if market_id not in self._books:
            self._books[market_id] = OrderBook(market_id, platform)

        book = self._books[market_id]
        bids = [(level["price"], level["quantity"])
                for level in msg.get("yes", {}).get("bids", [])]
        asks = [(level["price"], level["quantity"])
                for level in msg.get("yes", {}).get("asks", [])]
        seq  = msg.get("seq", 0)

        book.apply_snapshot(bids, asks, seq)

        # Publish snapshot event
        await self.bus.publish(BookSnapshotEvent(
            source    = f"price_watcher_{platform}",
            platform  = platform,
            market_id = market_id,
            bids      = bids,
            asks      = asks,
        ))

        # Also publish initial price event
        await self.bus.publish(book.to_price_event(platform))

    async def _handle_kalshi_delta(self, platform: str, msg: Dict) -> None:
        """Process an incremental order book delta — hot path, must be fast."""
        market_id = msg.get("market_ticker", "")
        if not market_id:
            return

        self._msg_counts[platform] += 1
        self._last_msg_times[platform] = time.monotonic()

        # Initialize book if first message (may happen before snapshot)
        if market_id not in self._books:
            self._books[market_id] = OrderBook(market_id, platform)

        book = self._books[market_id]

        # If book needs reset (sequence gap), request snapshot
        if book.needs_reset:
            log.warning("book_needs_reset", market=market_id)
            # In production: request snapshot from REST API
            # For now: drop message and let reconnection handle it
            return

        # Apply delta
        for change in msg.get("yes", {}).get("bids", []):
            book.apply_delta("bid", change["price"], change["quantity"],
                             msg.get("seq", 0))
        for change in msg.get("yes", {}).get("asks", []):
            book.apply_delta("ask", change["price"], change["quantity"],
                             msg.get("seq", 0))

        # Publish price update — this is the hot path
        await self.bus.publish(book.to_price_event(platform))

    async def _handle_health_change(
        self, platform: str, connected: bool, latency_ms: float
    ) -> None:
        """Publish feed health event."""
        rate = self._calculate_msg_rate(platform)
        await self.bus.publish(FeedHealthEvent(
            source       = "price_watcher",
            platform     = platform,
            connected    = connected,
            latency_ms   = latency_ms,
            message_rate_per_sec = rate,
        ))

    def _calculate_msg_rate(self, platform: str) -> float:
        """Calculate messages per second over last 60 seconds."""
        # Simplified: would use a sliding window in production
        return float(self._msg_counts.get(platform, 0)) / 60.0

    async def _polymarket_connection_loop(self) -> None:
        """
        Connect to Polymarket CLOB WebSocket.
        NOTE: Verify against CTF Exchange V2 API (updated April 2026).
        This is a placeholder — requires Polymarket API key setup.
        """
        log.info("polymarket_ws_connecting")
        # TODO: Implement Polymarket CLOB WebSocket
        # Reference: https://clob.polymarket.com/
        # Post CTF Exchange V2 — native USDC, new CLOB architecture
        # Will be implemented in Phase 2
        await asyncio.sleep(float('inf'))   # Placeholder

    async def _health_monitor(self) -> None:
        """
        Monitor feed health. Alert if any feed is silent for too long.
        This is the agent's own health guard — separate from the Health Monitor Agent.
        """
        while self._running:
            await asyncio.sleep(self.HEALTH_CHECK_INTERVAL)

            for platform, last_time in self._last_msg_times.items():
                silence = time.monotonic() - last_time
                if silence > self.SILENCE_ALERT_THRESHOLD:
                    log.warning("feed_silent",
                                platform=platform,
                                silent_seconds=silence)
                    await self._handle_health_change(platform, False, 0.0)

    async def _heartbeat_loop(self) -> None:
        """Publish heartbeat so Health Monitor knows we're alive."""
        while self._running:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            await self.bus.publish(AgentHeartbeat(
                source          = self.AGENT_NAME,
                agent_name      = self.AGENT_NAME,
                status          = "OK",
                messages_processed = sum(self._msg_counts.values()),
                last_action     = "price_update",
            ))

    @property
    def book_count(self) -> int:
        return len(self._books)

    @property
    def all_books(self) -> Dict[str, OrderBook]:
        return self._books
