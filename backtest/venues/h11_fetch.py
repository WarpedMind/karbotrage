"""Data for H11 (Kalshi <-> Polymarket US same-station weather crossings), Session 39.

Offline, read-only, public endpoints, no auth. Never imported by the live path.
Caches under backtest/cache/venues/ (gitignored). The pre-registration is
DECISIONS.md Session 38; nothing here computes a result.

    karbotrage_env/bin/python -m backtest.venues.h11_fetch pm_markets   # Polymarket US climate universe
    karbotrage_env/bin/python -m backtest.venues.h11_fetch pm_history   # minute quotes per rung, <=24h windows
    karbotrage_env/bin/python -m backtest.venues.h11_fetch kalshi       # hourly candles not cached by Session 37

Kalshi candles for event days 2026-05-22 -> 10-17 are reused from Session 37's
cache (backtest/cache/nowcast/candles/, same [end_ts, yes_bid, yes_ask] rows);
only earlier days are fetched here, from the historical tier (~2 req/s).
Failures are appended to backtest/cache/venues/failed.txt and counted by the
analysis, never silently dropped.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "..", "cache", "venues")
NOWCAST = os.path.join(HERE, "..", "cache", "nowcast")
PMUS = "https://gateway.polymarket.us"

PAIRS = {"KXHIGHNY": "nychigh", "KXHIGHTSFO": "sfohigh", "KXHIGHMIA": "miahigh",
         "KXHIGHCHI": "mdwhigh", "KXHIGHLAX": "laxhigh", "KXHIGHPHIL": "phlhigh"}
FIRST_DAY = dt.date(2026, 4, 22)
LAST_DAY = dt.date(2026, 10, 17)
SESSION37_FIRST_DAY = dt.date(2026, 5, 22)
WINDOW = 24 * 3600  # custom price-history windows must be <= 24h

_lock = threading.Lock()
_pm_lock = threading.Lock()
_pm_last = [0.0]
PM_GAP = 1 / 14  # documented public limit is 20 req/s/IP; stay well under it


def iso(t: str) -> int:
    return int(dt.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp())


def pm_event_day(slug: str, city: str) -> dt.date | None:
    m = re.match(rf"tc-temp-{city}-(\d{{4}}-\d\d-\d\d)-", slug)
    return dt.date.fromisoformat(m[1]) if m else None


def _fail(what: str) -> None:
    with _lock:
        with open(os.path.join(CACHE, "failed.txt"), "a") as fh:
            fh.write(what + "\n")


def _atomic(fn: str, obj) -> None:
    with open(fn + ".part", "w") as fh:
        json.dump(obj, fh)
    os.replace(fn + ".part", fn)


def pm_get(path: str, params: dict, tries: int = 8):
    last = None
    for i in range(tries):
        with _pm_lock:
            wait = _pm_last[0] + PM_GAP - time.time()
            if wait > 0:
                time.sleep(wait)
            _pm_last[0] = time.time()
        try:
            r = requests.get(PMUS + path, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = repr(e)
        time.sleep(min(2 ** i, 16))
    raise RuntimeError(f"{path} {params} -> {last}")


def fetch_pm_markets() -> None:
    out, off = [], 0
    while True:
        rows = pm_get("/v1/markets", {"limit": 200, "offset": off, "categories": "climate"}).get("markets", [])
        out.extend(rows)
        if len(rows) < 200:
            break
        off += 200
    _atomic(os.path.join(CACHE, "pm_climate_markets.json"), out)
    print("polymarket us climate markets:", len(out))


def h11_pm_markets() -> list[dict]:
    """Daily-high rungs on the six cities with an event day inside the H11 window."""
    out = []
    for m in json.load(open(os.path.join(CACHE, "pm_climate_markets.json"))):
        for city in PAIRS.values():
            d = pm_event_day(m["slug"], city)
            if d and FIRST_DAY <= d <= LAST_DAY:
                out.append({**m, "_city": city, "_day": d.isoformat()})
    return out


def fetch_pm_history() -> None:
    os.makedirs(os.path.join(CACHE, "pm_history"), exist_ok=True)

    def work(m):
        fn = os.path.join(CACHE, "pm_history", m["slug"] + ".json")
        if os.path.exists(fn):
            return
        start, end = iso(m["startDate"]), iso(m["endDate"])
        pts, t = {}, start
        try:
            while t < end:
                hi = min(t + WINDOW, end)
                d = pm_get("/v1/price-history", {"symbol": m["slug"], "timestamp.startTimestamp": t,
                                                  "timestamp.endTimestamp": hi, "fidelity": 1})
                for p in d.get("history", []):
                    pts[p["timestamp"]] = [p["timestamp"], p.get("longPrice"), p.get("shortPrice")]
                t = hi
        except RuntimeError as e:
            _fail("pm_history " + m["slug"] + " " + str(e))
            return
        _atomic(fn, [pts[k] for k in sorted(pts)])

    todo = h11_pm_markets()
    print("polymarket rungs", len(todo), flush=True)
    with ThreadPoolExecutor(8) as ex:
        for i, _ in enumerate(ex.map(work, todo)):
            if i % 500 == 0:
                print("pm_history", i, flush=True)


def kalshi_candle_path(ticker: str) -> str | None:
    for d in (os.path.join(NOWCAST, "candles"), os.path.join(CACHE, "kalshi_candles")):
        fn = os.path.join(d, ticker + ".json")
        if os.path.exists(fn):
            return fn
    return None


def h11_kalshi_markets() -> list[dict]:
    from backtest.kalshi_history import parse_event_day
    ms = json.load(open(os.path.join(NOWCAST, "markets.json")))["markets"]
    out = []
    for m in ms:
        if m["ticker"].split("-")[0] not in PAIRS:
            continue
        d = parse_event_day(m["event_ticker"])
        if d and FIRST_DAY <= d <= LAST_DAY:
            out.append({**m, "_day": d.isoformat()})
    return out


def fetch_kalshi() -> None:
    # Reuses Session 37's paced Kalshi client (live <=5/s, historical <=2/s).
    from backtest.nowcast.fetch import _close, kget
    os.makedirs(os.path.join(CACHE, "kalshi_candles"), exist_ok=True)

    def work(m):
        if kalshi_candle_path(m["ticker"]):
            return
        fn = os.path.join(CACHE, "kalshi_candles", m["ticker"] + ".json")
        ser = m["ticker"].split("-")[0]
        op, cl = iso(m["open_time"]), iso(m["close_time"])
        path = (f"/historical/markets/{m['ticker']}/candlesticks" if m["tier"] == "hist"
                else f"/series/{ser}/markets/{m['ticker']}/candlesticks")
        try:
            d = kget(path, {"start_ts": op, "end_ts": cl + 3600, "period_interval": 60})
        except RuntimeError as e:
            _fail("kalshi_candles " + m["ticker"] + " " + str(e))
            return
        rows = [[c["end_period_ts"], _close(c.get("yes_bid")), _close(c.get("yes_ask"))]
                for c in d.get("candlesticks", [])]
        _atomic(fn, rows)

    todo = [m for m in h11_kalshi_markets() if not kalshi_candle_path(m["ticker"])]
    print("kalshi markets needing candles", len(todo),
          "(of which before Session 37's first day:",
          sum(dt.date.fromisoformat(m["_day"]) < SESSION37_FIRST_DAY for m in todo), ")", flush=True)
    with ThreadPoolExecutor(4) as ex:
        for i, _ in enumerate(ex.map(work, todo)):
            if i % 200 == 0:
                print("kalshi", i, flush=True)


if __name__ == "__main__":
    os.makedirs(CACHE, exist_ok=True)
    {"pm_markets": fetch_pm_markets, "pm_history": fetch_pm_history, "kalshi": fetch_kalshi}[sys.argv[1]]()
