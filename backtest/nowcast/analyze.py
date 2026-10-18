"""C10: run the pre-registered H7 / H8 tests (DECISIONS.md Session 37).

    karbotrage_env/bin/python -m backtest.nowcast.analyze            # H7, H8, CLI-vs-ASOS; writes h7_entries.json
    karbotrage_env/bin/python -m backtest.nowcast.analyze capacity   # after `fetch trades`

Nothing here is tuned. Every threshold lives in ``rule.py`` and was frozen
before the first outcome was computed.
"""

from __future__ import annotations

import collections
import csv
import datetime as dt
import json
import math
import os
import random
import statistics
import sys

from backtest.kalshi_history import parse_event_day
from backtest.nowcast.fetch import CACHE, FIRST_DAY, STATIONS, iso
from backtest.nowcast.rule import (
    STATION_STD_OFFSET, Rung, RunningMax, first_entry_h7, first_entry_h8,
    metar_temp_f, net_return, taker_fee,
)

ALPHA = 0.05 / 3
PERIODS = [("P1", dt.date(2026, 5, 22), dt.date(2026, 7, 29)),
           ("P2", dt.date(2026, 7, 30), dt.date(2026, 9, 27)),
           ("P3", dt.date(2026, 9, 28), dt.date(2099, 1, 1))]


def load_obs():
    obs, notgroup, total = {}, 0, 0
    for st in set(STATIONS.values()):
        rows = []
        with open(os.path.join(CACHE, "asos", f"{st}.csv"), newline="") as fh:
            for r in csv.DictReader(fh):
                t = metar_temp_f(r["metar"])
                total += 1
                if t is None:
                    continue
                if " T0" not in r["metar"] and " T1" not in r["metar"]:
                    notgroup += 1
                ts = int(dt.datetime.strptime(r["valid"], "%Y-%m-%d %H:%M")
                         .replace(tzinfo=dt.timezone.utc).timestamp())
                rows.append((ts, t))
        obs[st] = sorted(rows)
    return obs, notgroup, total


def day_start_utc(day: dt.date, station: str) -> int:
    midnight = int(dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).timestamp())
    return midnight - STATION_STD_OFFSET[station] * 3600


def boot(bydate, n=10000, seed=37):
    ks = sorted(bydate)
    allv = [v for k in ks for v in bydate[k]]
    if not allv:
        return None
    rnd = random.Random(seed)
    bs = []
    for _ in range(n):
        s = c = 0.0
        for _ in ks:
            vs = bydate[ks[rnd.randrange(len(ks))]]
            s += sum(vs)
            c += len(vs)
        bs.append(s / c)
    bs.sort()
    return {"mean": sum(allv) / len(allv), "lo": bs[int(0.025 * n)], "hi": bs[int(0.975 * n)],
            "p": sum(1 for x in bs if x <= 0) / n, "n": len(allv), "dates": len(ks)}


def fmt(b):
    if not b:
        return "no entries"
    return (f"{100*b['mean']:+.3f}%  95%CI[{100*b['lo']:+.3f}, {100*b['hi']:+.3f}]  "
            f"one-sided p={b['p']:.4f}  entries={b['n']}  dates={b['dates']}")


def report(name, ents, out):
    bydate = collections.defaultdict(list)
    for e in ents:
        bydate[e["day"]].append(e["ret"])
    pooled = boot(bydate)
    out.append(f"\n== {name} ==\npooled      {fmt(pooled)}")
    per = {}
    for pn, a, b in PERIODS:
        sub = {k: v for k, v in bydate.items() if a <= dt.date.fromisoformat(k) <= b}
        per[pn] = boot(sub)
        out.append(f"{pn} {min(sub) if sub else '-'}..{max(sub) if sub else '-'}  {fmt(per[pn])}")
    oos = {k: v for k, v in bydate.items()
           if not (PERIODS[1][1] <= dt.date.fromisoformat(k) <= PERIODS[1][2])}
    out.append(f"P1+P3 (OOS) {fmt(boot(oos))}")
    if ents:
        wins = sum(1 for e in ents if e["won"])
        out.append(f"win rate {wins}/{len(ents)} = {wins/len(ents):.4f}; mean entry price "
                   f"{statistics.mean(e['price'] for e in ents):.4f}; median entry price "
                   f"{statistics.median(e['price'] for e in ents):.3f}")
        sens = []
        for e in ents:
            fee = math.ceil(taker_fee(e["price"]) * 100 * 100 - 1e-9) / 100 / 100
            sens.append((e["day"], net_return(e["price"], e["won"], fee)))
        sb = collections.defaultdict(list)
        for d, r in sens:
            sb[d].append(r)
        out.append(f"sensitivity, fee rounded up per 100-contract order: {fmt(boot(sb, 2000))}")
        holds = [(e["settle_ts"] - e["ts"]) / 3600 for e in ents if e["settle_ts"]]
        out.append(f"hold to settlement: median {statistics.median(holds):.1f}h, "
                   f"p90 {sorted(holds)[int(0.9*len(holds))]:.1f}h")
        by_price = collections.Counter(round(e["price"], 2) for e in ents)
        out.append("entry price distribution (top): " +
                   ", ".join(f"{p:.2f}:{c}" for p, c in by_price.most_common(8)))
        loc = collections.Counter(int((e["ts"] - e["day_start"]) // 3600) for e in ents)
        out.append("entry candle-end hour LST: " +
                   " ".join(f"{h}:{loc[h]}" for h in sorted(loc)))
    passed = (pooled is not None and pooled["mean"] > 0 and pooled["p"] < ALPHA
              and all(per[p] is not None and per[p]["mean"] > 0 for p in per))
    out.append(f"verdict vs pre-registered criterion 1 (alpha={ALPHA:.4f}, all periods > 0): "
               f"{'PASS' if passed else 'FAIL'}")
    return pooled, per


def main():
    meta = json.load(open(os.path.join(CACHE, "markets.json")))
    obs, notgroup, total_obs = load_obs()
    out = [f"observations parsed: {total_obs}; without a T group (lower bound used): {notgroup}",
           f"unresolved KXHIGH* series excluded: {meta['unresolved_series']}"]
    counts = collections.Counter()
    rm_cache, h7, h8, daylevel = {}, [], [], {}
    decided_seen = collections.Counter()
    for m in meta["markets"]:
        day = parse_event_day(m["event_ticker"])
        if day is None or day < FIRST_DAY:
            counts["outside window"] += 1
            continue
        if m["result"] not in ("yes", "no"):
            counts["not yes/no (excluded)"] += 1
            continue
        ser = m["ticker"].split("-")[0]
        st = STATIONS[ser]
        ds = day_start_utc(day, st)
        close = iso(m["close_time"])
        if not (ds + 86400 - 60 <= close <= ds + 86400):
            counts["close_time not 23:59-24:00 LST"] += 1
        fn = os.path.join(CACHE, "candles", m["ticker"] + ".json")
        if not os.path.exists(fn):
            counts["no candle file (excluded)"] += 1
            continue
        candles = json.load(open(fn))
        if not candles:
            counts["empty candles (excluded)"] += 1
            continue
        counts["in sample"] += 1
        key = (st, day)
        if key not in rm_cache:
            rm_cache[key] = RunningMax(obs[st], ds)
            day_obs = [v for t, v in obs[st] if ds <= t < ds + 86400]
            daylevel[key] = {"final_max": max(day_obs) if day_obs else None, "n": len(day_obs),
                             "cli": None}
        if m.get("expiration_value") not in (None, ""):
            daylevel[key]["cli"] = float(m["expiration_value"])
        rm = rm_cache[key]
        rung = Rung(m["strike_type"], m["floor_strike"], m["cap_strike"])
        won_yes = m["result"] == "yes"
        # how often a decided rung had NO two-sided book (market already knew)
        for ts, yb, ya in candles:
            if ts <= close and rung.decided_side(rm.as_of(ts)):
                decided_seen["decided candle-hours"] += 1
                if not (yb is not None and ya is not None and 0 < yb < ya < 1):
                    decided_seen["...of which book not two-sided"] += 1
        base = {"ticker": m["ticker"], "day": day.isoformat(), "series": ser, "close_ts": close,
                "day_start": ds, "settle_ts": iso(m.get("settlement_ts"))}
        for rule, sink in ((lambda: first_entry_h7(rung, candles, rm, close), h7),
                           (lambda: first_entry_h8(rung, candles, rm, close, ds), h8)):
            e = rule()
            if e:
                won = (e.side == "yes") == won_yes
                sink.append(dict(base, ts=e.ts, side=e.side, price=e.price, won=won,
                                 ret=net_return(e.price, won)))
    out.append("sample accounting: " + json.dumps(dict(counts)))
    out.append("decided rungs: " + json.dumps(dict(decided_seen)))

    report("H7 primary — decided rungs, first two-sided candle", h7, out)
    lost = [e for e in h7 if not e["won"]]
    out.append(f"\n(a) CLI-vs-ASOS on H7 entries: decided side LOST {len(lost)} of {len(h7)}"
               + (f" ({100*len(lost)/len(h7):.3f}%)" if h7 else ""))
    for e in lost[:40]:
        out.append(f"    LOST {e['ticker']} side={e['side']} price={e['price']:.2f} "
                   f"entry {dt.datetime.fromtimestamp(e['ts'], dt.timezone.utc):%Y-%m-%d %H:%MZ}")
    gain = sum((1 - e["price"] - taker_fee(e["price"])) for e in h7 if e["won"])
    loss = sum((e["price"] + taker_fee(e["price"])) for e in lost)
    out.append(f"    per-contract $: wins earned {gain:.2f}, losses cost {loss:.2f}")
    dl = [v for v in daylevel.values() if v["cli"] is not None and v["final_max"] is not None]
    danger = [v for v in dl if v["final_max"] >= v["cli"] + 1]
    diffs = collections.Counter(math.floor(v["cli"] - v["final_max"]) for v in dl)
    out.append(f"    day level: {len(dl)} city-days; final METAR max >= CLI+1 (dangerous) on "
               f"{len(danger)} ({100*len(danger)/max(1,len(dl)):.2f}%); "
               f"floor(CLI - METAR max) distribution {dict(sorted(diffs.items()))}")
    thin = sum(1 for v in daylevel.values() if v["n"] < 20)
    out.append(f"    city-days with < 20 METARs in the climate day: {thin} of {len(daylevel)}")

    report("H8 secondary — post-19:00 LST, YES region >= max + 3F, buy NO", h8, out)

    json.dump(h7, open(os.path.join(CACHE, "h7_entries.json"), "w"))
    json.dump(h8, open(os.path.join(CACHE, "h8_entries.json"), "w"))
    text = "\n".join(out)
    print(text)
    os.makedirs(os.path.join(os.path.dirname(CACHE), "..", "reports"), exist_ok=True)
    open(os.path.join(os.path.dirname(CACHE), "..", "reports", "nowcast_h7_h8.txt"), "w").write(text + "\n")


def capacity():
    h7 = json.load(open(os.path.join(CACHE, "h7_entries.json")))
    perday = collections.defaultdict(float)
    profit = collections.defaultdict(float)
    missing = trunc = 0
    # every in-sample event date counts, including dates with no entry (pre-registered)
    meta = json.load(open(os.path.join(CACHE, "markets.json")))["markets"]
    days = sorted({parse_event_day(m["event_ticker"]).isoformat() for m in meta
                   if m["result"] in ("yes", "no") and parse_event_day(m["event_ticker"])
                   and parse_event_day(m["event_ticker"]) >= FIRST_DAY})
    for e in h7:
        fn = os.path.join(CACHE, "trades", e["ticker"] + ".json")
        if not os.path.exists(fn):
            missing += 1
            continue
        d = json.load(open(fn))
        trunc += d["trunc"]
        for ts, cnt, yp, taker in d["trades"]:
            if not (e["ts"] < ts <= e["close_ts"]) or taker != e["side"]:
                continue
            p = yp if e["side"] == "yes" else 1 - yp
            if p <= e["price"] + 1e-9:
                perday[e["day"]] += cnt * p
                profit[e["day"]] += cnt * ((1 if e["won"] else 0) - p - taker_fee(p))
    vals = [perday.get(d, 0.0) for d in days]
    pv = [profit.get(d, 0.0) for d in days]
    lines = [f"\n== capacity proxy (H7 entries) ==",
             f"entries {len(h7)}, trade files missing {missing}, truncated {trunc}",
             f"notional taken by takers on the decided side at <= entry price, after signal:",
             f"  mean ${statistics.mean(vals):.2f}/day, median ${statistics.median(vals):.2f}/day "
             f"over {len(days)} dates (zero days included); max ${max(vals):.2f}",
             f"  realised P&L on that notional: mean ${statistics.mean(pv):.2f}/day, "
             f"median ${statistics.median(pv):.2f}/day, total ${sum(pv):.2f}",
             f"pre-registered criterion 3 (mean >= $20/day): "
             f"{'PASS' if statistics.mean(vals) >= 20 else 'FAIL'}"]
    for pn, a, b in PERIODS:
        sub = [perday.get(d, 0.0) for d in days if a <= dt.date.fromisoformat(d) <= b]
        if sub:
            lines.append(f"  {pn}: mean ${statistics.mean(sub):.2f}/day over {len(sub)} dates")
    text = "\n".join(lines)
    print(text)
    open(os.path.join(os.path.dirname(CACHE), "..", "reports", "nowcast_h7_h8.txt"), "a").write(text + "\n")


if __name__ == "__main__":
    capacity() if sys.argv[1:] == ["capacity"] else main()
