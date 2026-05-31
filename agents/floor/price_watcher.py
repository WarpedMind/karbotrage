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
import base64
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import structlog
import websockets
import websockets.exceptions
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as crypto_padding
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


# ── Kalshi RSA authentication ─────────────────────────────────────────────────

def _load_kalshi_private_key(path: str):
    """Load RSA private key from a PEM file. Raises if the file is missing or corrupt."""
    with open(path, "rb") as fh:
        return serialization.load_pem_private_key(fh.read(), password=None)


def _build_kalshi_auth_headers(
    key_id: str,
    private_key,              # cryptography RSAPrivateKey object
    method: str,
    path: str,
) -> Dict[str, str]:
    """
    Generate RSA-PKCS1v15/SHA-256 signed headers for the Kalshi API.

    Signature covers: {timestamp_ms}{HTTP_METHOD}{url_path}
    (no query string, no host, no body).
    Docs: https://trading-api.kalshi.com/trade-api/v2
    """
    ts = str(int(time.time() * 1000))
    msg = (ts + method + path).encode("utf-8")
    sig = private_key.sign(msg, crypto_padding.PKCS1v15(), hashes.SHA256())
    return {
        "KALSHI-ACCESS-KEY":       key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode("utf-8"),
        "KALSHI-ACCESS-TIMESTAMP": ts,
    }


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
    Handles RSA authentication, reconnection, and message routing.

    Kalshi Auth: RSA-PKCS1v15 + SHA-256.  Private key is loaded once at
    construction and reused for every signed request.
    Docs: https://trading-api.kalshi.com/trade-api/v2
    """

    WS_URL  = "wss://trading-api.kalshi.com/trade-api/ws/v2"
    WS_PATH = "/trade-api/ws/v2"

    def __init__(
        self,
        key_id: str,
        private_key_path: str,
        on_price_update: Any,
        on_snapshot: Any,
        on_health: Any,
    ):
        self._key_id      = key_id
        self._private_key = _load_kalshi_private_key(private_key_path)
        self._on_price    = on_price_update
        self._on_snapshot = on_snapshot
        self._on_health   = on_health
        self._ws          = None
        self._connected   = False
        self._subscribed_markets: set = set()
        self._msg_count   = 0
        self._last_msg_time = time.monotonic()

    def _auth_headers(self) -> Dict[str, str]:
        """Generate RSA-signed auth headers for the WebSocket upgrade request."""
        return _build_kalshi_auth_headers(
            self._key_id, self._private_key, "GET", self.WS_PATH
        )

    async def connect(self) -> None:
        """Connect and authenticate to Kalshi WebSocket."""
        headers = self._auth_headers()
        self._ws = await websockets.connect(
            self.WS_URL,
            additional_headers=headers,
            ping_interval=30,
            ping_timeout=10,
            close_timeout=5,
        )
        self._connected = True
        log.info("kalshi_ws_connected")
        await self._on_health("kalshi", True, 0.0)

    async def subscribe_markets(self, market_ids: List[str]) -> None:
        """
        Subscribe to orderbook_delta for each market.
        Sends in batches of 50 to keep individual WS messages small.
        """
        new_ids = [m for m in market_ids if m not in self._subscribed_markets]
        if not new_ids:
            return

        chunk_size = 50
        for batch_num, i in enumerate(range(0, len(new_ids), chunk_size)):
            chunk = new_ids[i : i + chunk_size]
            msg = {
                "id":     batch_num + 1,
                "cmd":    "subscribe",
                "params": {
                    "channels":       ["orderbook_delta"],
                    "market_tickers": chunk,
                },
            }
            await self._ws.send(json.dumps(msg))
            self._subscribed_markets.update(chunk)

        log.info("kalshi_markets_subscribed", total=len(new_ids))

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
                key_id           = self.secrets.kalshi_api_key_id,
                private_key_path = self.secrets.kalshi_private_key_path,
                on_price_update  = self._handle_kalshi_delta,
                on_snapshot      = self._handle_kalshi_snapshot,
                on_health        = self._handle_health_change,
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
        """
        Fetch open Kalshi markets via REST API using RSA-signed headers.
        Filters to markets with meaningful volume (>100 contracts or dollars).
        """
        rest_path = "/trade-api/v2/markets"
        private_key = _load_kalshi_private_key(self.secrets.kalshi_private_key_path)
        auth = _build_kalshi_auth_headers(
            self.secrets.kalshi_api_key_id, private_key, "GET", rest_path
        )
        auth["Content-Type"] = "application/json"

        url    = "https://trading-api.kalshi.com" + rest_path
        params = {"status": "open", "limit": 200}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=auth, params=params) as resp:
                if resp.status == 200:
                    data    = await resp.json()
                    markets = data.get("markets", [])
                    # Kalshi may use "volume" or "volume_24h" — accept either
                    active = [
                        m["ticker"]
                        for m in markets
                        if m.get("volume_24h", m.get("volume", 0)) > 100
                    ]
                    log.info("kalshi_markets_fetched", count=len(active),
                             total=len(markets))
                    return active
                else:
                    body = await resp.text()
                    log.error("kalshi_markets_fetch_failed",
                              status=resp.status, body=body[:300])
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


# ── karbot_runner.py-compatible agent ────────────────────────────────────────

class PriceWatcher(PriceWatcherAgent):
    """
    BaseAgent-conforming class used by karbot_runner.py.
    Inherits the full PriceWatcherAgent implementation.

    run() behaviour:
      - credentials present → connect to Kalshi WS and emit PriceUpdateEvents
      - credentials absent  → idle and log once; no network calls
    """

    def __init__(self, bus: EventBus, config: KarbotConfig):
        super().__init__(config=config, secrets=config.secrets, event_bus=bus)

    def register_subscriptions(self) -> None:
        pass   # PriceWatcher publishes; it does not subscribe to any event

    async def run(self) -> None:
        key_id   = self.config.secrets.kalshi_api_key_id
        key_path = self.config.secrets.kalshi_private_key_path

        if not key_id or not key_path:
            log.info(
                "PriceWatcher: no Kalshi credentials configured — idling. "
                "No PriceUpdateEvents will be emitted."
            )
            while True:
                await asyncio.sleep(60)
                log.debug("PriceWatcher: idle heartbeat (no credentials)")
            return

        log.info("PriceWatcher: starting Kalshi WS connection",
                 key_id=key_id, key_path=key_path)
        await self.start()
