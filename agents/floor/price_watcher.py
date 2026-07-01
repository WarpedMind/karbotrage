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
from cryptography.hazmat.primitives.asymmetric.padding import PSS, MGF1
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type
)

from karbot.core.config import KarbotConfig
from karbot.core.events import (
    EventBus, PriceUpdateEvent, BookSnapshotEvent,
    FeedHealthEvent, AgentHeartbeat, Priority
)

log = structlog.get_logger(__name__)


def _log_before_sleep(retry_state) -> None:
    """tenacity before_sleep callback compatible with structlog.

    tenacity's built-in before_sleep_log(logger, "WARNING") is written for
    stdlib logging.Logger and calls logger.log("WARNING", ...) — passing the
    level as a string. structlog's BoundLogger.log() expects an int level and
    does `if level < min_level`, raising TypeError("'<' not supported between
    instances of 'str' and 'int'") on every retry attempt. That TypeError
    propagated out of tenacity's retry machinery itself, so @retry below never
    actually retried — confirmed live (2026-06-30 07:42 UTC Kalshi WS
    disconnect, ~6 hours dead with zero retry attempts logged).
    """
    log.warning(
        "kalshi_reconnect_retry",
        attempt=retry_state.attempt_number,
        wait_seconds=getattr(retry_state.next_action, "sleep", None),
    )


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
    Generate RSA-PSS/SHA-256 signed headers for the Kalshi API.

    Signature covers: {timestamp_ms}{HTTP_METHOD}{url_path}
    (no query string, no host, no body).
    Docs: https://api.elections.kalshi.com/trade-api/v2
    """
    ts = str(int(time.time() * 1000))
    msg = (ts + method + path).encode("utf-8")
    sig = private_key.sign(
        msg, PSS(mgf=MGF1(hashes.SHA256()), salt_length=PSS.MAX_LENGTH), hashes.SHA256()
    )
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

    def apply_delta(self, side: str, price: float, delta: float, seq: int) -> bool:
        """
        Apply a single incremental delta update.

        `delta` is a RELATIVE change to the existing size at `price`, not an
        absolute level (confirmed empirically against live Kalshi WS traffic —
        e.g. a level moving from one price to another arrives as a matched
        +N/-N pair, not as two absolute-size messages). The resulting size is
        clamped at 0; a level that reaches zero is removed.

        Returns True if applied cleanly, False if a sequence gap was detected.
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
        new_size = book.get(price, 0.0) + delta

        if new_size <= 0:
            book.pop(price, None)    # Remove level
        else:
            book[price] = new_size   # Add or update level

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

    Kalshi Auth: RSA-PSS + SHA-256.  Private key is loaded once at
    construction and reused for every signed request.
    Docs: https://api.elections.kalshi.com/trade-api/v2
    """

    WS_URL  = "wss://api.elections.kalshi.com/trade-api/ws/v2"
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
        self._seen_first_delta: set = set()
        self._reset_requested: Dict[str, float] = {}  # market_id → monotonic time of last snapshot request
        self._snapshot_request_id_counter: int = 0  # monotonic counter for WS "id" correlation field
        self._rest_session: Optional[aiohttp.ClientSession] = None  # shared across _request_snapshot calls

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
        if self._rest_session is not None and not self._rest_session.closed:
            await self._rest_session.close()
            self._rest_session = None
        log.info("price_watcher_stopped")

    def _get_rest_session(self) -> aiohttp.ClientSession:
        """Shared aiohttp session for REST snapshot fetches.

        Created once and reused across all _request_snapshot calls instead
        of a new ClientSession() per call — gap events can fire across many
        markets within the same second, and creating a new session per call
        under that load is wasteful. Closed in stop() so nothing leaks
        across restarts.
        """
        if self._rest_session is None or self._rest_session.closed:
            self._rest_session = aiohttp.ClientSession()
        return self._rest_session

    # NOTE (Session 19, current behavior — dies permanently after 10 failed
    # attempts): once stop_after_attempt(10) is genuinely exhausted (10 real
    # failed reconnect attempts), the exception propagates out of this
    # coroutine, _run_supervised in karbot_runner.py logs the crash, and the
    # PriceWatcher agent is dead until an operator runs
    # `systemctl restart karbot`. There is no agent-level auto-restart after
    # cooldown. Whether that's acceptable (operator gets paged via Telegram
    # and restarts manually) or whether _run_supervised should itself restart
    # a dead PriceWatcher after a cooldown is an open architectural question
    # — flagged for operator decision, not resolved here.
    @retry(
        stop=stop_after_attempt(10),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception_type((websockets.exceptions.WebSocketException,
                                       ConnectionError, OSError)),
        before_sleep=_log_before_sleep,
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
            await self._handle_health_change("kalshi", False, 0.0, error=str(e))
            raise

    KALSHI_MARKETS_PAGE_CAP = 20  # safety bound on cursor-following, ~4000 markets

    async def _fetch_active_kalshi_markets(self) -> List[str]:
        """
        Fetch open Kalshi markets via REST API using RSA-signed headers.
        Uses mve_filter=exclude to skip multi-variable event (combo) markets —
        confirmed live that these dominate the unfiltered catalog (12,000+
        consecutive KXMVE* results with zero volume) and bury every standard
        market past any reasonable page depth. Follows the response `cursor`
        across pages as a secondary safeguard.
        Filters to markets with meaningful volume (>100, field volume_24h_fp).
        """
        rest_path = "/trade-api/v2/markets"
        private_key = _load_kalshi_private_key(self.secrets.kalshi_private_key_path)
        url = "https://api.elections.kalshi.com" + rest_path

        all_markets: List[Dict] = []
        cursor: Optional[str] = None

        async with aiohttp.ClientSession() as session:
            for _ in range(self.KALSHI_MARKETS_PAGE_CAP):
                auth = _build_kalshi_auth_headers(
                    self.secrets.kalshi_api_key_id, private_key, "GET", rest_path
                )
                auth["Content-Type"] = "application/json"

                params = {"status": "open", "limit": 200, "mve_filter": "exclude"}
                if cursor:
                    params["cursor"] = cursor

                async with session.get(url, headers=auth, params=params) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        log.error("kalshi_markets_fetch_failed",
                                  status=resp.status, body=body[:300])
                        break

                    data = await resp.json()
                    all_markets.extend(data.get("markets", []))
                    cursor = data.get("cursor")
                    if not cursor:
                        break

        active = []
        for m in all_markets:
            try:
                volume = float(m.get("volume_24h_fp", 0))
            except (TypeError, ValueError):
                continue
            if volume > 100:
                active.append(m["ticker"])

        log.info("kalshi_markets_fetched", count=len(active),
                  total=len(all_markets))
        return active

    async def _handle_kalshi_snapshot(self, platform: str, msg: Dict) -> None:
        """
        Process a full order book snapshot.

        Real Kalshi schema (confirmed live, 2026-06-28): the payload is
        nested under `msg["msg"]`, not at the top level. `yes_dollars_fp`
        and `no_dollars_fp` are each a flat list of [price, contracts]
        resting BID orders — Kalshi's book only carries bids per side.
        A resting NO bid at price p is equivalent to a YES ask at (1 - p),
        consistent with to_price_event()'s existing no_bid/no_ask math.
        """
        payload = msg.get("msg", {})
        market_id = payload.get("market_ticker", "")
        if not market_id:
            return

        if market_id not in self._books:
            self._books[market_id] = OrderBook(market_id, platform)

        book = self._books[market_id]
        bids = [(float(price), float(contracts))
                for price, contracts in payload.get("yes_dollars_fp", [])]
        asks = [(round(1.0 - float(price), 2), float(contracts))
                for price, contracts in payload.get("no_dollars_fp", [])]
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
        """
        Process an incremental order book delta — hot path, must be fast.

        Real Kalshi schema (confirmed live, 2026-06-28): one delta per
        message, nested under msg["msg"]: price_dollars, delta_fp (a
        RELATIVE change to apply, not an absolute size — confirmed via a
        matched +N/-N pair when a resting order moved between price
        levels), and side ("yes"/"no"). A "no" side delta updates the
        derived YES ask book at price (1 - price_dollars); a "yes" side
        delta updates the YES bid book directly.
        """
        payload = msg.get("msg", {})
        market_id = payload.get("market_ticker", "")
        if not market_id:
            return

        self._msg_counts[platform] += 1
        self._last_msg_times[platform] = time.monotonic()

        # Initialize book if first message (may happen before snapshot)
        if market_id not in self._books:
            self._books[market_id] = OrderBook(market_id, platform)

        book = self._books[market_id]

        # If book needs reset (sequence gap), request a fresh snapshot
        if book.needs_reset:
            log.debug("book_needs_reset", market=market_id)
            await self._request_snapshot(market_id)
            return

        seq    = msg.get("seq", 0)
        price  = float(payload.get("price_dollars", 0))
        delta  = float(payload.get("delta_fp", 0))
        side   = payload.get("side", "")

        if side == "yes":
            book.apply_delta("bid", price, delta, seq)
        elif side == "no":
            book.apply_delta("ask", round(1.0 - price, 2), delta, seq)
        else:
            log.warning("kalshi_delta_unknown_side", market=market_id, side=side)
            return

        if platform not in self._seen_first_delta:
            self._seen_first_delta.add(platform)
            log.info("kalshi_first_price_update", market=market_id, side=side)

        # Publish price update — this is the hot path
        await self.bus.publish(book.to_price_event(platform))

    KALSHI_REST_SNAPSHOT_TIMEOUT_SECS = 5.0

    async def _request_snapshot(self, market_id: str) -> None:
        """Fetch a fresh order book snapshot via REST on sequence gap detection.

        Session 18 originally implemented this via a WS re-subscribe, on the
        assumption that Kalshi would respond to a duplicate subscribe with a
        fresh orderbook_snapshot. Session 21's live wire capture confirmed
        that assumption was wrong: Kalshi responds to a duplicate subscribe
        with {"type": "ok", "id": N} — a plain ack, never a snapshot — and
        Kalshi's own docs state snapshot delivery only happens on the
        *initial* subscribe to a channel, not on re-subscribing to an
        already-subscribed market. The WS re-subscribe path could not work
        as designed; replaced (Session 22) with a direct REST fetch.

        GET /trade-api/v2/markets/{ticker}/orderbook, no query params
        (omitting `depth` returns all levels). Response:
          {"orderbook_fp": {"yes_dollars": [[price_str, count_str], ...],
                             "no_dollars":  [[price_str, count_str], ...]}}
        Same bid-only-per-side structure as the WS snapshot payload, but
        string values — cast to float before building (price, size) tuples.

        NO authentication — per Kalshi's own docs (pending live confirmation
        this session, see SESSIONS.md Session 23). Session 22 added auth
        headers here defensively, without empirical verification; that per-call
        RSA-PSS signing (_build_kalshi_auth_headers) plus a per-call private
        key file read (_load_kalshi_private_key) is blocking, synchronous
        work executed inside this async function. Under real load
        (~13,761 book_needs_reset/15min, ~1,073 throttled-through calls),
        that blocking work stacked up on the event loop long enough that
        the WS listen loop couldn't respond to Kalshi's ping frames within
        ping_timeout=10s, Kalshi tore down the transport, and the next
        recv() crashed with AttributeError: 'NoneType' object has no
        attribute 'resume_reading' — 3 crashes in ~8 minutes, exhausting the
        Session 20 restart budget and leaving PriceWatcher permanently
        stopped. Do not reintroduce per-call blocking crypto/file I/O here.

        This REST response carries no sequence number, so `apply_snapshot`
        is called with seq=0 (sentinel). `OrderBook.apply_delta`'s gap check
        is `if seq != self.sequence + 1 and self.sequence != 0` — with
        self.sequence reset to 0, that second condition is False, so the
        very next delta is accepted regardless of its own seq value and
        `self.sequence` naturally realigns to whatever Kalshi sends next.
        No special-casing needed downstream.

        Throttled to one REST fetch per market per 10 seconds to avoid
        hammering the endpoint when gap events fire repeatedly on the same
        market. Uses a shared aiohttp.ClientSession (see _get_rest_session)
        rather than creating a new session per call. On any failure
        (non-200, network error, timeout), logs a warning and returns
        without calling apply_snapshot — `_gap_detected` stays True, so the
        next delta on this market will retrigger a throttled retry rather
        than crash the connection loop.
        """
        # Rate limit: skip if we requested a reset for this market within the last 10s
        _THROTTLE_SECS = 10.0
        last = self._reset_requested.get(market_id, 0.0)
        if time.monotonic() - last < _THROTTLE_SECS:
            log.debug("book_reset_throttled", market=market_id)
            return

        # Guard: client must exist and be connected
        if self._kalshi_client is None or not self._kalshi_client._connected:
            log.warning("book_reset_skipped_no_connection", market=market_id)
            return

        self._reset_requested[market_id] = time.monotonic()

        # Kept for continuity/logging correlation, but no longer load-bearing
        # for this recovery path — no WS message is sent here anymore.
        self._snapshot_request_id_counter += 1

        rest_path = f"/trade-api/v2/markets/{market_id}/orderbook"
        url = "https://api.elections.kalshi.com" + rest_path
        try:
            session = self._get_rest_session()
            timeout = aiohttp.ClientTimeout(total=self.KALSHI_REST_SNAPSHOT_TIMEOUT_SECS)
            async with session.get(url, timeout=timeout) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("book_reset_rest_failed", market=market_id,
                                status=resp.status, body=body[:300])
                    return
                data = await resp.json()
        except Exception as e:
            log.warning("book_reset_rest_failed", market=market_id, error=str(e))
            return

        orderbook = data.get("orderbook_fp", {})
        bids = [(float(price), float(count))
                for price, count in orderbook.get("yes_dollars", [])]
        asks = [(round(1.0 - float(price), 2), float(count))
                for price, count in orderbook.get("no_dollars", [])]

        book = self._books.get(market_id)
        if book is None:
            book = OrderBook(market_id, "kalshi")
            self._books[market_id] = book

        book.apply_snapshot(bids, asks, seq=0)
        log.info("book_snapshot_requested", market=market_id)

    async def _handle_health_change(
        self, platform: str, connected: bool, latency_ms: float, error: str = ""
    ) -> None:
        """Publish feed health event."""
        rate = self._calculate_msg_rate(platform)
        await self.bus.publish(FeedHealthEvent(
            source       = "price_watcher",
            platform     = platform,
            connected    = connected,
            latency_ms   = latency_ms,
            message_rate_per_sec = rate,
            error        = error,
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

        log.info("PriceWatcher: starting Kalshi WS connection")
        await self.start()
