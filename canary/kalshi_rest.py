"""Public, unauthenticated Kalshi REST access for the canary.

Everything here was verified live on 2026-08-02 against
``api.elections.kalshi.com``; the notes below record what was measured rather
than what the docs imply.

**One sweep primitive.** ``GET /events?with_nested_markets=true&status=open``
returns, per event, the ``mutually_exclusive`` flag *and* every child market
carrying ``yes_ask_dollars``, ``no_ask_dollars``, ``strike_type``,
``floor_strike``/``cap_strike``, ``yes_ask_size_fp`` and ``yes_bid_size_fp``.
A 200-event page came back in ~0.14s. That is the whole input to S5a and S5b in
a single paginated call.

**Depth field naming is a trap -- CONFIRMED LIVE.** There is no
``no_ask_size_fp``. On Kalshi's unified bid-only book a NO ask *is* a resting
YES bid at ``1 - price``, so the quantity available for BUYING NO at ``no_ask``
is ``yes_bid_size_fp``, and the quantity for BUYING YES at ``yes_ask`` is
``yes_ask_size_fp`` (which is itself the resting NO-bid quantity). Checked
against ``/markets/{t}/orderbook`` on 16 live markets: exact agreement on both
price and size in every case. Using the same-named field for both sides -- the
obvious reading -- would size the NO leg off the wrong side of the book, which
is the Session 26 bug class.

**The bulk snapshot goes stale in seconds -- CONFIRMED LIVE.** Read
back-to-back against the orderbook, the list endpoint agreed exactly 16/16. But
holding a snapshot for ~10 seconds while doing other HTTP saw an actively
traded market move under it (``KXMLBSPREAD-...-CWS6``: yes_bid 0.10 -> 0.14,
size 3 -> 2071). A full sweep takes tens of seconds, so the earliest pages are
already stale when the arithmetic runs. Every candidate must therefore be
re-confirmed leg-by-leg via ``orderbook_top`` before it is believed. A
candidate that exists only in a stale view of the book is exactly what S1 spent
a year trading.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

import requests

API = "https://api.elections.kalshi.com/trade-api/v2"
USER_AGENT = "karbotrage-canary/0.1 (research; contact: tomgrow@gmail.com)"


class KalshiRestError(RuntimeError):
    pass


def make_session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT})
    return sess


def get(
    session: requests.Session,
    path: str,
    params: Optional[dict] = None,
    *,
    tries: int = 5,
    timeout: float = 30.0,
) -> dict:
    """GET with exponential backoff on the retryable statuses.

    Raises rather than returning partial data on exhaustion. A swallowed 429
    that returns an empty page would silently shrink the universe being
    scanned, and a scanner that quietly stops looking at half the market is
    worse than one that stops loudly.
    """
    delay = 1.0
    last = None
    for _ in range(tries):
        resp = session.get(f"{API}{path}", params=params or {}, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        last = f"HTTP {resp.status_code}: {resp.text[:200]}"
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(delay)
            delay = min(delay * 2, 20.0)
            continue
        break
    raise KalshiRestError(f"{path} failed after {tries} tries: {last}")


def iter_open_events(
    session: requests.Session,
    *,
    page_limit: int = 200,
    max_pages: int = 100,
) -> Iterator[dict]:
    """Every open event with its markets nested, following the cursor.

    ``max_pages`` is a runaway guard, not a sample cap -- if it is ever hit the
    caller is told, because a silently truncated sweep would understate
    candidate frequency, which is the one number this whole process exists to
    measure.
    """
    cursor = None
    pages = 0
    while pages < max_pages:
        params = {
            "limit": page_limit,
            "status": "open",
            "with_nested_markets": "true",
        }
        if cursor:
            params["cursor"] = cursor
        data = get(session, "/events", params)
        for event in data.get("events", []):
            yield event
        cursor = data.get("cursor")
        pages += 1
        if not cursor:
            return
    raise KalshiRestError(
        f"iter_open_events hit max_pages={max_pages} with a cursor still set; "
        "the sweep would have been silently truncated"
    )


@dataclass(frozen=True)
class BookTop:
    """Top of book for one market, from the authoritative orderbook endpoint.

    ``yes_ask``/``no_ask`` are the prices this system would pay to BUY, derived
    from the opposite side's best bid -- Kalshi publishes bids only.
    """

    ticker: str
    yes_bid: Optional[float]
    yes_bid_qty: float
    no_bid: Optional[float]
    no_bid_qty: float

    @property
    def yes_ask(self) -> Optional[float]:
        return None if self.no_bid is None else round(1.0 - self.no_bid, 6)

    @property
    def yes_ask_qty(self) -> float:
        return self.no_bid_qty

    @property
    def no_ask(self) -> Optional[float]:
        return None if self.yes_bid is None else round(1.0 - self.yes_bid, 6)

    @property
    def no_ask_qty(self) -> float:
        return self.yes_bid_qty


def _best_bid(levels) -> Tuple[Optional[float], float]:
    best_price = None
    best_qty = 0.0
    for entry in levels or []:
        try:
            price, qty = float(entry[0]), float(entry[1])
        except (TypeError, ValueError, IndexError):
            continue
        if best_price is None or price > best_price:
            best_price, best_qty = price, qty
    return best_price, best_qty


def orderbook_top(session: requests.Session, ticker: str) -> BookTop:
    """Authoritative top of book. Used to re-confirm every candidate leg.

    ``orderbook_fp`` holds ``yes_dollars`` and ``no_dollars``, each a list of
    ``[price, quantity]`` **bids only**, ascending. The best bid is therefore
    the last/highest entry, not the first -- reading index 0 would take the
    worst price in the book.
    """
    data = get(session, f"/markets/{ticker}/orderbook")
    fp = data.get("orderbook_fp") or data.get("orderbook") or {}
    yes_bid, yes_qty = _best_bid(fp.get("yes_dollars"))
    no_bid, no_qty = _best_bid(fp.get("no_dollars"))
    return BookTop(
        ticker=ticker,
        yes_bid=yes_bid,
        yes_bid_qty=yes_qty,
        no_bid=no_bid,
        no_bid_qty=no_qty,
    )


def settled_markets_by_event(
    session: requests.Session, series_ticker: str, *, max_pages: int = 5
) -> Dict[str, List[dict]]:
    """Settled markets for a series, grouped by ``event_ticker``.

    Feeds ``qualify``: the settled record is the only evidence that actually
    proves how a series' markets relate to each other. Structure proposes a
    relation; history is what disposes of it.

    ``max_pages`` of 1,000 markets is a deliberate bound rather than the whole
    archive. Qualification needs ~30 settled events; five pages supplies
    hundreds even for a six-market ladder, and the open universe holds 2,503
    distinct multi-market series (measured 2026-08-02), so an unbounded fetch
    per series would be tens of thousands of requests against a public API for
    evidence that stops improving long before then.
    """
    out: Dict[str, List[dict]] = {}
    cursor = None
    for _ in range(max_pages):
        params = {"limit": 1000, "status": "settled", "series_ticker": series_ticker}
        if cursor:
            params["cursor"] = cursor
        data = get(session, "/markets", params)
        for market in data.get("markets", []):
            out.setdefault(market.get("event_ticker", ""), []).append(market)
        cursor = data.get("cursor")
        if not cursor:
            break
    return out
