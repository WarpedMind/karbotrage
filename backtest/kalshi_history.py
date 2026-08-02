"""Kalshi historical market data for the offline calibration harness.

Three things are pulled, all from the public REST API with no authentication
(CONFIRMED LIVE 2026-08-02):

1. ``GET /markets?status=settled&series_ticker=...`` -- settled markets. These
   carry ``result`` (yes/no), ``strike_type``, ``floor_strike``/``cap_strike``,
   and -- the find that simplified this whole harness -- ``expiration_value``,
   the **actual observed settlement number**. For a temperature market that is
   the observed daily high in whole degrees F. There is no need to reconstruct
   the outcome by bracketing the ladder, and no need for a separate
   observations feed: Kalshi publishes the number it settled against.
2. ``GET /series/{s}/markets/{t}/candlesticks`` -- hourly OHLC carrying
   ``yes_bid`` and ``yes_ask`` separately. Having both matters: the market
   baseline for calibration is the mid, but anything claiming tradeable edge
   must cross to the ask. Scoring a strategy at the mid and calling it
   executable is the exact bug class that invalidated S1.
3. ``GET /series?category=Climate and Weather`` -- series discovery.

CONFIRMED LIVE, and load-bearing for the sample design: a daily-high event's
markets open ~42 hours before close (e.g. KXHIGHLAX-26AUG01 opened
2026-07-31T14:00Z, closed 2026-08-02T07:59Z). So there is **no market price at
all** beyond roughly 36 hours of lead. Comparing a 5-day NBM forecast against
"the market" is not possible for these series -- the market does not exist yet.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

import requests

API = "https://api.elections.kalshi.com/trade-api/v2"
USER_AGENT = "karbotrage-backtest/0.1 (research; contact: tomgrow@gmail.com)"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")


class KalshiHistoryError(RuntimeError):
    pass


def _get(session: requests.Session, path: str, params: dict, *, tries: int = 5) -> dict:
    """GET with backoff. Kalshi rate-limits, and a 429 swallowed as an empty
    result would silently shrink the sample -- which is a far worse failure than
    a slow run, so this raises rather than returning partial data."""
    delay = 1.0
    last = None
    for _ in range(tries):
        resp = session.get(
            f"{API}{path}", params=params, headers={"User-Agent": USER_AGENT}, timeout=60
        )
        if resp.status_code == 200:
            return resp.json()
        last = f"HTTP {resp.status_code}: {resp.text[:200]}"
        if resp.status_code in (429, 500, 502, 503, 504):
            time.sleep(delay)
            delay = min(delay * 2, 20.0)
            continue
        break
    raise KalshiHistoryError(f"{path} failed: {last}")


def _cache_read(name: str):
    path = os.path.join(CACHE_DIR, "kalshi", name)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return None


def _cache_write(name: str, obj) -> None:
    path = os.path.join(CACHE_DIR, "kalshi", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
    os.replace(tmp, path)


def settled_markets(
    series_ticker: str,
    *,
    session: Optional[requests.Session] = None,
    use_cache: bool = True,
) -> List[dict]:
    """Every settled market for one series, following the cursor to the end."""
    name = f"settled.{series_ticker}.json"
    if use_cache:
        cached = _cache_read(name)
        if cached is not None:
            return cached

    sess = session or requests.Session()
    out: List[dict] = []
    cursor = None
    for _ in range(50):
        params = {"limit": 1000, "status": "settled", "series_ticker": series_ticker}
        if cursor:
            params["cursor"] = cursor
        data = _get(sess, "/markets", params)
        out.extend(data.get("markets", []))
        cursor = data.get("cursor")
        if not cursor:
            break
    if use_cache:
        _cache_write(name, out)
    return out


def candlesticks(
    series_ticker: str,
    market_ticker: str,
    start_ts: int,
    end_ts: int,
    period_interval: int = 60,
    *,
    session: Optional[requests.Session] = None,
    use_cache: bool = True,
) -> List[dict]:
    """Hourly OHLC bars for one market.

    ``period_interval`` is in minutes; 60 gives hourly bars, which is the right
    granularity here because NBM only issues a new forecast every 6 hours.
    """
    name = f"candles.{market_ticker}.{period_interval}.json"
    if use_cache:
        cached = _cache_read(name)
        if cached is not None:
            return cached
    sess = session or requests.Session()
    data = _get(
        sess,
        f"/series/{series_ticker}/markets/{market_ticker}/candlesticks",
        {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval},
    )
    out = data.get("candlesticks", [])
    if use_cache:
        _cache_write(name, out)
    return out


@dataclass
class WeatherMarket:
    """One settled Kalshi daily-temperature market, normalised.

    ``yes_if`` states the settlement condition explicitly rather than leaving it
    implicit in ``strike_type``. That matters more than it looks: four of the
    eighteen daily-high series (KXHIGHTDAL, KXHIGHTHOU, KXHIGHTNOLA,
    KXHIGHTSATX) phrase their threshold markets as **"less than"**, so assuming
    "greater" -- which the highest-volume series KXHIGHLAX does use -- would
    invert the outcome on a fifth of the sample while still looking sane.
    """

    ticker: str
    series: str
    event_ticker: str
    local_day: dt.date
    strike_type: str  # greater | less | between
    floor_strike: Optional[float]
    cap_strike: Optional[float]
    result: str  # yes | no
    expiration_value: Optional[float]  # observed daily high, whole degrees F
    open_ts: int
    close_ts: int
    volume: float

    @property
    def outcome(self) -> int:
        return 1 if self.result == "yes" else 0

    @property
    def yes_if(self) -> str:
        if self.strike_type == "greater":
            return f"high >= {self.floor_strike + 1:.0f}"
        if self.strike_type == "less":
            # "less" carries its threshold in cap_strike, not floor_strike.
            return f"high <= {self.cap_strike - 1:.0f}"
        return f"{self.floor_strike:.0f} <= high <= {self.cap_strike:.0f}"


def _parse_ts(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_event_day(event_ticker: str) -> Optional[dt.date]:
    """``KXHIGHLAX-26AUG01`` -> ``date(2026, 8, 1)``."""
    parts = event_ticker.split("-")
    if len(parts) < 2:
        return None
    token = parts[1]
    if len(token) != 7:
        return None
    try:
        year = 2000 + int(token[:2])
        month = _MONTHS[token[2:5]]
        day = int(token[5:])
        return dt.date(year, month, day)
    except (KeyError, ValueError):
        return None


def to_weather_markets(raw: Iterable[dict]) -> List[WeatherMarket]:
    out = []
    for m in raw:
        day = parse_event_day(m.get("event_ticker", ""))
        if day is None:
            continue
        if m.get("result") not in ("yes", "no"):
            continue
        exp = m.get("expiration_value")
        try:
            exp_val = float(exp) if exp not in (None, "") else None
        except (TypeError, ValueError):
            exp_val = None
        try:
            volume = float(m.get("volume_fp") or 0.0)
        except (TypeError, ValueError):
            volume = 0.0
        out.append(
            WeatherMarket(
                ticker=m["ticker"],
                series=m["ticker"].split("-")[0],
                event_ticker=m["event_ticker"],
                local_day=day,
                strike_type=m.get("strike_type", ""),
                floor_strike=m.get("floor_strike"),
                cap_strike=m.get("cap_strike"),
                result=m["result"],
                expiration_value=exp_val,
                open_ts=_parse_ts(m["open_time"]),
                close_ts=_parse_ts(m["close_time"]),
                volume=volume,
            )
        )
    return out


def observed_high_by_day(markets: Iterable[WeatherMarket]) -> Dict[dt.date, float]:
    """The settled daily high per local day, from ``expiration_value``.

    Every market in a city-day ladder reports the same ``expiration_value``, so
    this also serves as an internal consistency check: a day where the ladder
    disagrees with itself is dropped rather than silently averaged.
    """
    by_day: Dict[dt.date, set] = {}
    for m in markets:
        if m.expiration_value is None:
            continue
        by_day.setdefault(m.local_day, set()).add(m.expiration_value)
    out = {}
    for day, vals in by_day.items():
        if len(vals) == 1:
            out[day] = next(iter(vals))
    return out


def yes_price_at(candles: List[dict], ts: int, side: str = "mid") -> Optional[float]:
    """The YES price from the last bar closing at or before ``ts``.

    ``side`` is ``bid``, ``ask`` or ``mid``. Bars with no two-sided quote are
    skipped -- an ask of 1.00 against a bid of 0.00 is the absence of a market,
    not a 50% probability, and letting it through as a mid of 0.50 would inject
    fake baseline error exactly where the model looks best.
    """
    best = None
    for c in candles:
        if c.get("end_period_ts", 0) > ts:
            continue
        bid = c.get("yes_bid", {}).get("close_dollars")
        ask = c.get("yes_ask", {}).get("close_dollars")
        if bid is None or ask is None:
            continue
        try:
            b, a = float(bid), float(ask)
        except (TypeError, ValueError):
            continue
        if a <= b or (b <= 0.0 and a >= 1.0):
            continue
        best = (b, a)
    if best is None:
        return None
    b, a = best
    if side == "bid":
        return b
    if side == "ask":
        return a
    return (b + a) / 2.0
