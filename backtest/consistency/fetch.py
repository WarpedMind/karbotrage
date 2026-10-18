"""C9 data: settled game + spread events (live tier) and their hourly candles.

    karbotrage_env/bin/python -m backtest.consistency.fetch events
    karbotrage_env/bin/python -m backtest.consistency.fetch candles

Caches under backtest/cache/consistency/. Reuses the paced Kalshi client from
``backtest.nowcast.fetch``; failures go to ``failed.txt``, never dropped silently.
"""

from __future__ import annotations

import collections
import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor

from backtest.nowcast.fetch import iso, kget

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "..", "cache", "consistency")
LO, HI = iso("2026-08-19T00:00:00Z"), iso("2026-10-18T00:00:00Z")
COVERED = ["KXNFL", "KXNCAAF", "KXMLB"]
KNOCKOUT = ["CUP", "POKAL", "COPA", "COUPE", "COPPA", "TACA", "SUPERCUP", "FINALISSIMA",
            "UCL", "UEL", "UECL", "CLUBWC", "CONMEBOL", "CONCACAF", "AFC", "WC", "FIFA", "ASEAN"]
SEED = 3737
KEEP = ["ticker", "event_ticker", "open_time", "close_time", "expected_expiration_time",
        "settlement_ts", "occurrence_datetime", "result", "strike_type", "floor_strike",
        "yes_sub_title"]


def pairs():
    ser = json.load(open(os.path.join(HERE, "..", "cache", "tape", "universe.json")))["series"]
    out = []
    for s in sorted(ser):
        if s.endswith("SPREAD") and s[:-6] + "GAME" in ser:
            out.append(s[:-6])
    return out


def settled(series):
    ms, cur = [], None
    for _ in range(40):
        p = {"series_ticker": series, "status": "settled", "limit": 1000,
             "min_close_ts": LO, "max_close_ts": HI}
        if cur:
            p["cursor"] = cur
        d = kget("/markets", p)
        ms += [{k: m.get(k) for k in KEEP} for m in d.get("markets", [])]
        cur = d.get("cursor")
        if not cur or not d.get("markets"):
            break
    return ms


def fetch_events():
    os.makedirs(CACHE, exist_ok=True)
    rnd = random.Random(SEED)
    chosen, notes = [], {}
    for base in pairs():
        covered = base in COVERED
        if not covered and any(t in base for t in KNOCKOUT):
            continue
        try:
            g = settled(base + "GAME")
        except RuntimeError as e:
            notes[base] = "game listing failed: " + str(e)
            continue
        gev = collections.defaultdict(list)
        for m in g:
            gev[m["event_ticker"].split("-", 1)[1]].append(m)
        has_tie = any(m["ticker"].endswith("-TIE") for m in g)
        if not covered and not has_tie:
            continue  # uncovered group is soccer only: 3-way game markets
        if not gev:
            continue
        try:
            sp = settled(base + "SPREAD")
        except RuntimeError as e:
            notes[base] = "spread listing failed: " + str(e)
            continue
        sev = collections.defaultdict(list)
        for m in sp:
            sev[m["event_ticker"].split("-", 1)[1]].append(m)
        common = sorted(set(gev) & set(sev))
        rnd.shuffle(common)
        cap = 150 if covered else 40
        for suf in common[:cap]:
            chosen.append({"base": base, "group": "covered" if covered else "uncovered",
                           "suffix": suf, "game": gev[suf], "spread": sev[suf]})
        notes[base] = f"{len(common)} paired events, {min(cap, len(common))} sampled"
        print(base, notes[base], flush=True)
    json.dump({"events": chosen, "notes": notes}, open(os.path.join(CACHE, "events.json"), "w"))
    print("events", len(chosen))


def _close(q):
    if not q:
        return None
    v = q.get("close", q.get("close_dollars"))
    return float(v) if v not in (None, "") else None


def fetch_candles():
    evs = json.load(open(os.path.join(CACHE, "events.json")))["events"]
    os.makedirs(os.path.join(CACHE, "candles"), exist_ok=True)
    tasks = []
    for e in evs:
        for m in e["game"] + e["spread"]:
            if not m["ticker"].endswith("-TIE"):
                tasks.append((e["base"] + ("GAME" if m in e["game"] else "SPREAD"), m))

    def work(t):
        ser, m = t
        fn = os.path.join(CACHE, "candles", m["ticker"] + ".json")
        if os.path.exists(fn):
            return
        end = min(x for x in (iso(m["close_time"]), iso(m.get("expected_expiration_time")),
                              iso(m.get("settlement_ts"))) if x)
        try:
            d = kget(f"/series/{ser}/markets/{m['ticker']}/candlesticks",
                     {"start_ts": iso(m["open_time"]), "end_ts": end, "period_interval": 60})
        except RuntimeError as err:
            with open(os.path.join(CACHE, "failed.txt"), "a") as fh:
                fh.write(m["ticker"] + " " + str(err) + "\n")
            return
        rows = [[c["end_period_ts"], _close(c.get("yes_bid")), _close(c.get("yes_ask"))]
                for c in d.get("candlesticks", [])]
        with open(fn + ".part", "w") as fh:
            json.dump(rows, fh)
        os.replace(fn + ".part", fn)

    print("candle tasks", len(tasks), flush=True)
    with ThreadPoolExecutor(12) as ex:
        for i, _ in enumerate(ex.map(work, tasks)):
            if i % 1000 == 0:
                print("candles", i, flush=True)


if __name__ == "__main__":
    {"events": fetch_events, "candles": fetch_candles}[sys.argv[1]]()
