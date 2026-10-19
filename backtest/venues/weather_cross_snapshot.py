"""Live snapshot: Kalshi vs Polymarket US daily-high ladders on the SAME NWS station.

Offline, read-only, public endpoints, no auth. Never imported by the live path.
This is a screen (like Session 36's C4 sports snapshot), not the kill test: one
snapshot can kill "the venues are routinely apart" but cannot prove the opposite.

Both venues settle on the NWS CLI for the same five stations (Polymarket US
weather FAQ; backtest/resolved_stations.json for Kalshi):
    KNYC KXHIGHNY / nychigh, KSFO KXHIGHTSFO / sfohigh, KMIA KXHIGHMIA / miahigh,
    KMDW KXHIGHCHI / mdwhigh, KLAX KXHIGHLAX / laxhigh, KPHL KXHIGHPHIL / phlhigh
    (PHL is listed by Polymarket US but missing from its FAQ station table).

For every rung whose integer-°F YES set is identical on both venues:
    basket A = Kalshi YES ask + (1 - PM YES bid)   -> pays exactly $1
    basket B = PM YES ask + (1 - Kalshi YES bid)   -> pays exactly $1
Net cost adds both venues' taker fees (Kalshi 0.07, Polymarket US 0.0695, x p(1-p)).

Run:  karbotrage_env/bin/python -m backtest.venues.weather_cross_snapshot 2026-10-19
"""

from __future__ import annotations

import datetime as dt
import math
import re
import sys
import time

import requests

from backtest.venues.polymarket_us_census import get as pm_get

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
PMUS = "https://gateway.polymarket.us"
PAIRS = {"KXHIGHNY": "nychigh", "KXHIGHTSFO": "sfohigh", "KXHIGHMIA": "miahigh",
         "KXHIGHCHI": "mdwhigh", "KXHIGHLAX": "laxhigh", "KXHIGHPHIL": "phlhigh"}
INF = 10 ** 6


def kalshi_interval(m: dict) -> tuple[int, int] | None:
    st, f, c = m.get("strike_type"), m.get("floor_strike"), m.get("cap_strike")
    if st == "greater" and f is not None:
        return (int(f) + 1, INF)
    if st == "less" and c is not None:
        return (-INF, int(c) - 1)
    if st == "between" and f is not None and c is not None:
        return (int(f), int(c))
    return None


def pm_interval(title: str) -> tuple[int, int] | None:
    # TRAP: parse the TITLE, not the slug. Slug `gte60lt61f` is titled "60 to 61"
    # (inclusive of 61), so reading the slug literally is off by one degree.
    if m := re.fullmatch(r"(\d+) to (\d+)", title.strip()):
        return (int(m[1]), int(m[2]))
    if m := re.fullmatch(r"(\d+) or above", title.strip()):
        return (int(m[1]), INF)
    if m := re.fullmatch(r"(\d+) or below", title.strip()):
        return (-INF, int(m[1]))
    return None


def fee(theta: float, p: float) -> float:
    return theta * p * (1 - p)


def money(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("value")
    return float(v)


def main(day: str) -> None:
    d = dt.date.fromisoformat(day)
    kal_suffix = d.strftime("%y%b%d").upper()  # 26OCT19
    rows = []
    pm_climate = requests.get(f"{PMUS}/v1/markets", params={"limit": 200, "categories": "climate",
                              "endDateMin": f"{day}T00:00:00Z"}, timeout=30).json()["markets"]
    for series, city in PAIRS.items():
        km = requests.get(f"{KALSHI}/markets", params={"event_ticker": f"{series}-{kal_suffix}", "limit": 100},
                          timeout=30).json().get("markets", [])
        kal = {}
        for m in km:
            iv = kalshi_interval(m)
            if iv:
                kal[iv] = (float(m.get("yes_bid_dollars") or 0), float(m.get("yes_ask_dollars") or 0), m["ticker"])
        pm_markets = [m for m in pm_climate if m["slug"].startswith(f"tc-temp-{city}-{day}-")]
        for m in pm_markets:
            iv = pm_interval(m.get("title") or "")
            if iv is None or iv not in kal:
                continue
            b = pm_get(f"/v1/markets/{m['slug']}/bbo", {})["marketData"]
            pb, pa = money(b.get("bestBid")), money(b.get("bestAsk"))
            kb, ka, kt = kal[iv]
            rows.append((city, iv, kt, kb, ka, pb, pa))
            time.sleep(0.15)
    print(f"{'city':8} {'interval':>12} {'K bid/ask':>11} {'PM bid/ask':>11} {'A gross':>8} {'A net':>7} {'B gross':>8} {'B net':>7}")
    best = math.inf
    for city, iv, kt, kb, ka, pb, pa in rows:
        lo = "" if iv[0] == -INF else iv[0]
        hi = "" if iv[1] == INF else iv[1]
        a = b_ = None
        if 0 < ka < 1 and pb is not None and pb > 0:
            a = ka + (1 - pb)
            an = a + fee(0.07, ka) + fee(0.0695, 1 - pb)
        if pa is not None and 0 < pa < 1 and kb > 0:
            b_ = pa + (1 - kb)
            bn = b_ + fee(0.0695, pa) + fee(0.07, 1 - kb)
        fmt = lambda x: "  -   " if x is None else f"{x:.3f}"
        print(f"{city:8} {str(lo)+'..'+str(hi):>12} {kb:.2f}/{ka:.2f}  {fmt(pb)}/{fmt(pa)} "
              f"{fmt(a):>8} {fmt(an if a else None):>7} {fmt(b_):>8} {fmt(bn if b_ else None):>7}")
        for x in ((an if a else None), (bn if b_ else None)):
            if x is not None:
                best = min(best, x)
    print(f"\nmatched rungs: {len(rows)}; cheapest net basket for a guaranteed $1: {best:.3f}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else (dt.date.today() + dt.timedelta(days=1)).isoformat())
