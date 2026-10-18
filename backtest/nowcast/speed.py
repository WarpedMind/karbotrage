"""Descriptive, NOT a pre-registered test (no alpha spent): is C10 a speed contest?

H7 found the decided side almost never on offer at the first hourly close after
the deciding METAR. This asks what happened inside that hour: on a seeded sample
of decided live-tier markets, how much did takers buy of the decided side below
$1 after the deciding observation, and how fast?

    karbotrage_env/bin/python -m backtest.nowcast.speed
"""

from __future__ import annotations

import datetime as dt
import json
import os
import random
import statistics

from backtest.kalshi_history import parse_event_day
from backtest.nowcast.analyze import day_start_utc, load_obs
from backtest.nowcast.fetch import CACHE, FIRST_DAY, STATIONS, iso, kget
from backtest.nowcast.rule import Rung

SAMPLE = 400
WINDOW = 3 * 3600


def deciding_obs(rung, obs, ds, close):
    cur = None
    for t, v in obs:
        if t < ds or t > close:
            continue
        cur = v if cur is None else max(cur, v)
        side = rung.decided_side(cur)
        if side:
            return t, side
    return None


def main():
    obs, _, _ = load_obs()
    meta = json.load(open(os.path.join(CACHE, "markets.json")))["markets"]
    cands = []
    for m in meta:
        day = parse_event_day(m["event_ticker"])
        if m["tier"] != "live" or m["result"] not in ("yes", "no") or not day or day < FIRST_DAY:
            continue
        st = STATIONS[m["ticker"].split("-")[0]]
        ds = day_start_utc(day, st)
        close = iso(m["close_time"])
        d = deciding_obs(Rung(m["strike_type"], m["floor_strike"], m["cap_strike"]), obs[st], ds, close)
        if d and d[0] < close - 3600:
            cands.append((m, d[0], d[1]))
    random.Random(37).shuffle(cands)
    cands = cands[:SAMPLE]
    os.makedirs(os.path.join(CACHE, "speed"), exist_ok=True)
    rows = []
    for m, t0, side in cands:
        fn = os.path.join(CACHE, "speed", m["ticker"] + ".json")
        if os.path.exists(fn):
            trades = json.load(open(fn))
        else:
            d = kget("/markets/trades", {"ticker": m["ticker"], "min_ts": t0 - 3600,
                                         "max_ts": t0 + WINDOW, "limit": 1000})
            trades = [[iso(x["created_time"]), float(x["count_fp"]), float(x["yes_price_dollars"]),
                       x["taker_side"]] for x in d.get("trades", [])]
            json.dump(trades, open(fn, "w"))
        won = (side == "yes") == (m["result"] == "yes")
        after = [(ts - t0, c, (p if side == "yes" else 1 - p)) for ts, c, p, tk in trades
                 if tk == side and ts > t0]
        before = [c for ts, c, p, tk in trades if tk == side and t0 - 3600 < ts <= t0]
        notional = sum(c * p for _, c, p in after if p < 0.995)
        first = min((s for s, _, p in after if p < 0.995), default=None)
        rows.append({"won": won, "notional": notional, "first_s": first, "n_after": len(after),
                     "before_contracts": sum(before),
                     "pnl": sum(c * ((1 if won else 0) - p - 0.07 * p * (1 - p)) for _, c, p in after if p < 0.995)})
    hit = [r for r in rows if r["notional"] > 0]
    days = len({parse_event_day(m["event_ticker"]) for m, _, _ in cands})
    lines = ["\n== speed check (descriptive, not a test) ==",
             f"decided live-tier markets sampled: {len(rows)} (seed 37) over {days} dates",
             f"decided side bought by takers below 99.5c within {WINDOW//3600}h after the deciding METAR: "
             f"{len(hit)} markets ({100*len(hit)/max(1,len(rows)):.1f}%)",
             f"notional ${sum(r['notional'] for r in rows):.2f} total; taker P&L on it "
             f"${sum(r['pnl'] for r in rows):.2f}"]
    if hit:
        fs = sorted(r["first_s"] for r in hit)
        lines.append(f"first such trade after the METAR valid time: median {statistics.median(fs)/60:.1f} min, "
                     f"p25 {fs[len(fs)//4]/60:.1f}, p75 {fs[3*len(fs)//4]/60:.1f}")
    lines.append(f"decided side lost on {sum(1 for r in rows if not r['won'])} of {len(rows)} sampled markets")
    text = "\n".join(lines)
    print(text)
    open(os.path.join(os.path.dirname(CACHE), "..", "reports", "nowcast_h7_h8.txt"), "a").write(text + "\n")


if __name__ == "__main__":
    main()
