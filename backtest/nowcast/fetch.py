"""Data for C10, all public and unauthenticated. Caches under backtest/cache/nowcast/.

    karbotrage_env/bin/python -m backtest.nowcast.fetch markets   # settled KXHIGH* (live + historical tier)
    karbotrage_env/bin/python -m backtest.nowcast.fetch asos      # IEM METARs per station
    karbotrage_env/bin/python -m backtest.nowcast.fetch candles   # hourly bid/ask, open -> close
    karbotrage_env/bin/python -m backtest.nowcast.fetch trades    # capacity proxy, H7 entries only

Kalshi requests are paced per route: live <= 5/s, historical <= 2/s (the candle
route 429s above ~8/s from one IP, Session 36; the historical tier lower still). A fetch that still fails after retries is written
to ``failed.txt`` and reported by the analysis -- never silently dropped.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

API = "https://api.elections.kalshi.com/trade-api/v2"
IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "..", "cache", "nowcast")
STATIONS = json.load(open(os.path.join(HERE, "..", "resolved_stations.json")))
FIRST_DAY = dt.date(2026, 5, 22)

_local = threading.local()
_lock = threading.Lock()
_last = {"live": 0.0, "hist": 0.0}
# The historical-tier routes throttle harder than the live ones: MEASURED 2026-10-18,
# 2 req/s clean on /historical/markets/{t}/candlesticks while ~7/s drew 429s.
_GAP = {"live": 0.2, "hist": 0.5}


def iso(t):
    return int(dt.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()) if t else None


def _sess():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers["User-Agent"] = "karbotrage-research/0.1"
    return _local.s


_route_locks = {"live": threading.Lock(), "hist": threading.Lock()}


def _pace(route):
    with _route_locks[route]:
        wait = _last[route] + _GAP[route] - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[route] = time.time()


def kget(path, params, tries=12, allow_404=False):
    last = None
    for i in range(tries):
        _pace("hist" if path.startswith("/historical") else "live")
        try:
            r = _sess().get(API + path, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
            if r.status_code == 404:
                if allow_404:
                    return {}
                break
        except requests.RequestException as e:
            last = repr(e)
        time.sleep(min(1.0 * (i + 1), 8))
    raise RuntimeError(f"{path} {params} -> {last}")


def _fail(what):
    with _lock:
        with open(os.path.join(CACHE, "failed.txt"), "a") as fh:
            fh.write(what + "\n")


KEEP = ["ticker", "event_ticker", "open_time", "close_time", "settlement_ts", "result",
        "strike_type", "floor_strike", "cap_strike", "expiration_value", "volume_fp"]


def fetch_markets():
    out = {}
    lo = iso(FIRST_DAY.isoformat() + "T00:00:00Z")
    for ser in sorted(STATIONS):
        for tier, path in (("live", "/markets"), ("hist", "/historical/markets")):
            cur = None
            for _ in range(60):
                p = {"limit": 1000, "series_ticker": ser}
                if tier == "live":
                    p["status"] = "settled"
                if cur:
                    p["cursor"] = cur
                d = kget(path, p)
                ms = d.get("markets", [])
                for m in ms:
                    rec = {k: m.get(k) for k in KEEP}
                    rec["tier"] = tier
                    out.setdefault(m["ticker"], rec)
                cur = d.get("cursor")
                # historical tier ignores close-time filters; it pages newest-first
                if not cur or not ms or (tier == "hist" and iso(ms[-1]["close_time"]) < lo):
                    break
        print(ser, "markets so far", len(out), flush=True)
    # any KXHIGH* series that exists but has no resolved station is named, not guessed
    try:
        d = kget("/series", {"category": "Climate and Weather"})
        extra = sorted(s["ticker"] for s in d.get("series", [])
                       if s["ticker"].startswith("KXHIGH") and s["ticker"] not in STATIONS)
    except RuntimeError as e:
        extra = ["series listing failed: " + str(e)]
    json.dump({"markets": list(out.values()), "unresolved_series": extra},
              open(os.path.join(CACHE, "markets.json"), "w"))
    print("total", len(out), "unresolved KXHIGH series:", extra)


def fetch_asos():
    os.makedirs(os.path.join(CACHE, "asos"), exist_ok=True)
    end = dt.date.today() + dt.timedelta(days=1)
    start = FIRST_DAY - dt.timedelta(days=1)
    for st in sorted(set(STATIONS.values())):
        fn = os.path.join(CACHE, "asos", f"{st}.csv")
        if os.path.exists(fn):
            continue
        params = [("station", st[1:]), ("data", "tmpf"), ("data", "metar"),
                  ("year1", start.year), ("month1", start.month), ("day1", start.day),
                  ("year2", end.year), ("month2", end.month), ("day2", end.day),
                  ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"),
                  ("missing", "M"), ("trace", "T"), ("direct", "no"),
                  ("report_type", "3"), ("report_type", "4")]
        for i in range(6):
            try:
                r = requests.get(IEM, params=params, timeout=300)
                if r.status_code == 200 and r.text.startswith("station"):
                    open(fn, "w").write(r.text)
                    print(st, r.text.count("\n"), "rows", flush=True)
                    break
            except requests.RequestException:
                pass
            time.sleep(5 * (i + 1))
        else:
            _fail("asos " + st)
            print("FAILED", st)
        time.sleep(1)


def _atomic(fn, obj):
    with open(fn + ".part", "w") as fh:
        json.dump(obj, fh)
    os.replace(fn + ".part", fn)


def _close(q):
    # historical tier {"close": "0.14"}; live tier {"close_dollars": "0.14"} (Session 36)
    if not q:
        return None
    v = q.get("close", q.get("close_dollars"))
    return float(v) if v not in (None, "") else None


def fetch_candles():
    ms = json.load(open(os.path.join(CACHE, "markets.json")))["markets"]
    os.makedirs(os.path.join(CACHE, "candles"), exist_ok=True)

    def work(m):
        fn = os.path.join(CACHE, "candles", m["ticker"] + ".json")
        if os.path.exists(fn):
            return
        ser = m["ticker"].split("-")[0]
        op, cl = iso(m["open_time"]), iso(m["close_time"])
        path = (f"/historical/markets/{m['ticker']}/candlesticks" if m["tier"] == "hist"
                else f"/series/{ser}/markets/{m['ticker']}/candlesticks")
        try:
            d = kget(path, {"start_ts": op, "end_ts": cl + 3600, "period_interval": 60})
        except RuntimeError as e:
            _fail("candles " + m["ticker"] + " " + str(e))
            return
        rows = [[c["end_period_ts"], _close(c.get("yes_bid")), _close(c.get("yes_ask"))]
                for c in d.get("candlesticks", [])]
        _atomic(fn, rows)

    from backtest.kalshi_history import parse_event_day
    todo = [m for m in ms if m["result"] in ("yes", "no")
            and (parse_event_day(m["event_ticker"]) or FIRST_DAY) >= FIRST_DAY]
    # interleave tiers so threads queued on the slow historical route don't idle the live one
    live = [m for m in todo if m["tier"] == "live"]
    hist = [m for m in todo if m["tier"] == "hist"]
    todo = [m for pair in zip(live, hist) for m in pair] + live[len(hist):] + hist[len(live):]
    print("markets", len(todo), flush=True)
    with ThreadPoolExecutor(12) as ex:
        for i, _ in enumerate(ex.map(work, todo)):
            if i % 1000 == 0:
                print("candles", i, flush=True)


def fetch_trades():
    """Trades for H7 entry markets only, signal candle -> close. Both tiers queried."""
    entries = json.load(open(os.path.join(CACHE, "h7_entries.json")))
    os.makedirs(os.path.join(CACHE, "trades"), exist_ok=True)

    def work(e):
        fn = os.path.join(CACHE, "trades", e["ticker"] + ".json")
        if os.path.exists(fn):
            return
        seen, trades, trunc = set(), [], False
        try:
            for path in ("/historical/trades", "/markets/trades"):
                cur = None
                for _ in range(100):
                    p = {"ticker": e["ticker"], "min_ts": e["ts"], "max_ts": e["close_ts"], "limit": 1000}
                    if cur:
                        p["cursor"] = cur
                    d = kget(path, p, allow_404=True)
                    for x in d.get("trades", []):
                        if x["trade_id"] in seen:
                            continue
                        seen.add(x["trade_id"])
                        trades.append([iso(x["created_time"]), float(x["count_fp"]),
                                       float(x["yes_price_dollars"]), x["taker_side"]])
                    cur = d.get("cursor")
                    if not cur or not d.get("trades"):
                        cur = None
                        break
                trunc = trunc or bool(cur)
        except RuntimeError as err:
            _fail("trades " + e["ticker"] + " " + str(err))
            return
        _atomic(fn, {"trunc": trunc, "trades": trades})

    print("entries", len(entries), flush=True)
    with ThreadPoolExecutor(12) as ex:
        for i, _ in enumerate(ex.map(work, entries)):
            if i % 500 == 0:
                print("trades", i, flush=True)


if __name__ == "__main__":
    os.makedirs(CACHE, exist_ok=True)
    {"markets": fetch_markets, "asos": fetch_asos, "candles": fetch_candles,
     "trades": fetch_trades}[sys.argv[1]]()
