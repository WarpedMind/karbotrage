"""C9 / H9: run the pre-registered test (DECISIONS.md Session 37).

    karbotrage_env/bin/python -m backtest.consistency.analyze
"""

from __future__ import annotations

import collections
import json
import os
import re
import statistics

from backtest.consistency.fetch import CACHE
from backtest.consistency.rule import (
    fit, implied_win, ladder_points, net_return, qualifies, two_sided,
)
from backtest.nowcast.analyze import boot, fmt
from backtest.nowcast.fetch import iso

ALPHA = 0.05 / 3


def load(ticker):
    fn = os.path.join(CACHE, "candles", ticker + ".json")
    if not os.path.exists(fn):
        return None
    return {ts: (b, a) for ts, b, a in json.load(open(fn))}


def evaluate(e, counts):
    ev_prefix = lambda m: m["event_ticker"] + "-"
    teams = {}
    for m in e["game"]:
        code = m["ticker"][len(ev_prefix(m)):]
        if code != "TIE":
            teams[code] = m
    if len(teams) != 2:
        counts["game market not 2 teams"] += 1
        return None
    rungs = []
    for m in e["spread"]:
        code = re.sub(r"\d+$", "", m["ticker"][len(ev_prefix(m)):])
        if code not in teams or m.get("floor_strike") is None:
            counts["spread rung unmatched to a team (dropped)"] += 1
            continue
        rungs.append((code, float(m["floor_strike"]), m["ticker"]))
    allm = list(teams.values()) + e["spread"]
    cutoff = min(x for m in allm for x in (iso(m["close_time"]), iso(m.get("expected_expiration_time")),
                                           iso(m.get("settlement_ts")), iso(m.get("occurrence_datetime"))) if x) - 7200
    gc = {c: load(m["ticker"]) for c, m in teams.items()}
    if any(v is None for v in gc.values()):
        counts["missing game candles (excluded)"] += 1
        return None
    rc = {t: load(t) for _, _, t in rungs}
    draws = e["group"] == "uncovered"
    team_a, team_b = sorted(teams)
    first_desc, entry = None, None
    for ts in sorted(set(gc[team_a]) | set(gc[team_b])):
        if ts > cutoff:
            break
        quoted = [(code, k, *(rc[t].get(ts, (None, None)) if rc.get(t) else (None, None)))
                  for code, k, t in rungs]
        ab = fit(ladder_points(quoted, team_a))
        if not ab:
            continue
        pa, pb = implied_win(ab, draws)
        imp = {team_a: pa, team_b: pb}
        if first_desc is None:
            ba, aa = gc[team_a].get(ts, (None, None))
            if two_sided(ba, aa):
                first_desc = ((ba + aa) / 2, pa, teams[team_a]["result"] == "yes")
        best = None
        for code in (team_a, team_b):
            bid, ask = gc[code].get(ts, (None, None))
            if not two_sided(bid, ask):
                continue
            gap = qualifies(ask, imp[code])
            if gap > 0 and (best is None or gap > best[0]):
                best = (gap, code, ask)
        if best:
            gap, code, ask = best
            won = teams[code]["result"] == "yes"
            entry = {"ret": net_return(ask, won), "won": won, "price": ask, "gap": gap,
                     "implied": imp[code], "ts": ts}
            break
    day = (teams[team_a].get("settlement_ts") or teams[team_a]["close_time"])[:10]
    return {"day": day, "entry": entry, "desc": first_desc, "group": e["group"], "base": e["base"]}


def summarize(name, res, out):
    ents = [r for r in res if r["entry"]]
    bydate = collections.defaultdict(list)
    for r in ents:
        bydate[r["day"]].append(r["entry"]["ret"])
    out.append(f"\n== {name} ==\nevents evaluated with a usable fit: "
               f"{sum(1 for r in res if r['desc'])} of {len(res)}; entries {len(ents)}")
    pooled = boot(bydate)
    out.append(f"pooled  {fmt(pooled)}")
    days = sorted({r["day"] for r in res})
    t = len(days) // 3
    per = []
    for i, (a, b) in enumerate([(days[0], days[t - 1]), (days[t], days[2 * t - 1]), (days[2 * t], days[-1])]):
        sub = {k: v for k, v in bydate.items() if a <= k <= b}
        bp = boot(sub)
        per.append(bp)
        out.append(f"period{i+1} {a}..{b}  {fmt(bp)}")
    if ents:
        e = [r["entry"] for r in ents]
        out.append(f"win rate {sum(x['won'] for x in e)/len(e):.3f}; mean ask {statistics.mean(x['price'] for x in e):.3f}; "
                   f"mean implied {statistics.mean(x['implied'] for x in e):.3f}; mean gap after fee+3c "
                   f"{statistics.mean(x['gap'] for x in e):.3f}")
    d = [r["desc"] for r in res if r["desc"]]
    if d:
        bm = statistics.mean((m - o) ** 2 for m, _, o in d)
        bi = statistics.mean((p - o) ** 2 for _, p, o in d)
        out.append(f"descriptive Brier at first fitted candle, team A: game mid {bm:.4f} vs ladder-implied {bi:.4f} "
                   f"(n={len(d)})")
    return pooled, per


def main():
    data = json.load(open(os.path.join(CACHE, "events.json")))
    counts = collections.Counter()
    res = {"covered": [], "uncovered": []}
    for e in data["events"]:
        r = evaluate(e, counts)
        if r:
            res[e["group"]].append(r)
    failed = 0
    fp = os.path.join(CACHE, "failed.txt")
    if os.path.exists(fp):
        failed = sum(1 for _ in open(fp))
    out = [f"accounting: {dict(counts)}; failed candle fetches: {failed}"]
    pooled, per = summarize("H9 — UNCOVERED (soccer), the test", res["uncovered"], out)
    ok = pooled and pooled["mean"] > 0 and pooled["p"] < ALPHA and all(p and p["mean"] > 0 for p in per)
    out.append(f"verdict (alpha={ALPHA:.4f}, all periods > 0): {'PASS' if ok else 'FAIL'}")
    summarize("covered control (NFL/NCAAF/MLB), not claimed", res["covered"], out)
    for base in ("KXNFL", "KXNCAAF", "KXMLB"):
        rb = [r for r in res["covered"] if r["base"] == base and r["entry"]]
        if rb:
            out.append(f"  {base}: entries {len(rb)}, mean {100*statistics.mean(r['entry']['ret'] for r in rb):+.2f}%")
    text = "\n".join(out)
    print(text)
    open(os.path.join(os.path.dirname(CACHE), "..", "reports", "consistency_h9.txt"), "w").write(text + "\n")


if __name__ == "__main__":
    main()
