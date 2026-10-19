"""H10 in-sample half — Kalshi BTC/ETH perp funding carry (Session 39). CONTAMINATED.

The funding means for this window were seen in Session 38 before the rule was frozen, so this run
is a plumbing check of the rule and data, not the binding read. The binding read is the combined
in-sample + forward window at the ~2026-11-30 checkpoint (DECISIONS.md Session 38).

Offline, public endpoints, no auth. Never imported by the live path.

    karbotrage_env/bin/python -m backtest.venues.h10_carry fetch
    karbotrage_env/bin/python -m backtest.venues.h10_carry analyze [YYYY-MM-DD last decision Monday]
Writes backtest/reports/venue_h10_carry_insample.txt
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import sys
import time

import requests

from backtest.venues import h10_rule as R
from backtest.venues.h11_rule import bootstrap_mean

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "..", "cache", "venues", "h10")
REPORT = os.path.join(HERE, "..", "reports", "venue_h10_carry_insample.txt")
KALSHI = "https://external-api.kalshi.com/trade-api/v2/margin"
COINBASE = "https://api.exchange.coinbase.com"
SPOT = {"KXBTCPERP": "BTC-USD", "KXETHPERP": "ETH-USD"}
FROM = dt.datetime(2026, 6, 3, tzinfo=dt.timezone.utc)
FIRST_DECISION = dt.datetime(2026, 6, 15, 20, tzinfo=dt.timezone.utc)
UTC = dt.timezone.utc


def _get(url, params):
    for i in range(8):
        try:
            r = requests.get(url, params=params, timeout=60, headers={"User-Agent": "karbotrage-research/0.1"})
            if r.status_code == 200:
                return r
        except requests.RequestException:
            pass
        time.sleep(min(2 ** i, 16))
    raise RuntimeError(f"{url} {params}")


def fetch():
    os.makedirs(CACHE, exist_ok=True)
    now = int(time.time())
    for t in SPOT:
        fr = _get(KALSHI + "/funding_rates/historical", {"ticker": t}).json()["funding_rates"]
        json.dump(sorted(fr, key=lambda x: x["funding_time"]), open(os.path.join(CACHE, f"funding.{t}.json"), "w"))
        rows, s = {}, int(FROM.timestamp())
        while s < now:
            e = min(s + 20 * 86400, now)
            for c in _get(KALSHI + f"/markets/{t}/candlesticks",
                          {"start_ts": s, "end_ts": e, "period_interval": 60}).json().get("candlesticks", []):
                rows[c["end_period_ts"]] = [c["end_period_ts"], float(c["bid"]["close"]), float(c["ask"]["close"]),
                                            float(c["ask"]["high"]), float(c["ask"]["open"])]
            s = e
            time.sleep(0.3)
        json.dump([rows[k] for k in sorted(rows)], open(os.path.join(CACHE, f"perp.{t}.json"), "w"))
        spot, s = {}, int(FROM.timestamp())
        while s < now:
            e = min(s + 250 * 3600, now)
            for c in _get(COINBASE + f"/products/{SPOT[t]}/candles",
                          {"granularity": 3600, "start": dt.datetime.fromtimestamp(s, UTC).isoformat(),
                           "end": dt.datetime.fromtimestamp(e, UTC).isoformat()}).json():
                spot[c[0]] = c[4]  # bucket START -> close
            s = e
            time.sleep(0.4)
        json.dump(spot, open(os.path.join(CACHE, f"spot.{SPOT[t]}.json"), "w"))
        print(t, len(fr), "funding rows;", len(rows), "perp candles;", len(spot), "spot candles", flush=True)
    r = _get("https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": "DTB3", "cosd": "2026-05-01"})
    open(os.path.join(CACHE, "DTB3.csv"), "w").write(r.text)
    print("DTB3 rows", r.text.count("\n"))


def ts(t: str) -> int:
    return int(dt.datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp())


def load(t):
    fr = json.load(open(os.path.join(CACHE, f"funding.{t}.json")))
    fund = [(ts(x["funding_time"]), float(x["funding_rate"]), float(x["mark_price"])) for x in fr]
    perp = {r[0]: r for r in json.load(open(os.path.join(CACHE, f"perp.{t}.json")))}
    spot = {int(k): v for k, v in json.load(open(os.path.join(CACHE, f"spot.{SPOT[t]}.json"))).items()}
    return fund, perp, spot


def load_dtb3():
    out = []
    for row in csv.DictReader(io.StringIO(open(os.path.join(CACHE, "DTB3.csv")).read())):
        v = row.get("DTB3") or ""
        if v.strip() not in ("", "."):
            out.append((dt.date.fromisoformat(row["observation_date"]), float(v)))
    return out


def dtb3_on(series, d: dt.date) -> float:
    return [v for day, v in series if day <= d][-1]


def run_asset(t, decisions, dtb3, held_override=None, hedge_fee=R.HEDGE_FEE, margin_apy=0.0):
    fund, perp, spot = load(t)
    held = held_override or [R.signal([r for (ft, r, _) in fund if ft <= int(d.timestamp())]) for d in decisions]
    missing_funding = sum(1 for d in decisions if int(d.timestamp()) not in {f[0] for f in fund})
    weeks, qty = [], None
    for i in range(len(decisions) - 1):
        t0 = int(decisions[i].timestamp()) + 3600
        t1 = int(decisions[i + 1].timestamp()) + 3600
        dtb = dtb3_on(dtb3, decisions[i].date())
        w = {"start": decisions[i].date().isoformat(), "held": held[i], "excess": 0.0, "dtb3": dtb}
        if held[i]:
            entering = i == 0 or not held[i - 1]
            exiting = not held[i + 1]
            c0, c1 = perp[t0], perp[t1]
            s0, s1 = spot[t0 - 3600], spot[t1 - 3600]
            if entering:
                qty = (1 / c0[1], 1 / s0)
            funding = sum(R.short_funding(r, m, qty[0]) for (ft, r, m) in fund if t0 < ft <= t1)
            n_fund = sum(1 for (ft, _, _) in fund if t0 < ft <= t1)
            pnl = R.week_pnl(entering=entering, exiting=exiting, p_bid0=c0[1], p_ask0=c0[2], p_bid1=c1[1],
                             p_ask1=c1[2], s0=s0, s1=s1, perp_qty=qty[0], spot_qty=qty[1], funding=funding,
                             hedge_fee=hedge_fee)
            cap = R.capital(t, qty[1] * s0)
            pnl += margin_apy * 7 / 365 * (cap - qty[1] * s0)
            r = pnl / cap
            p_start = c0[1] if entering else (c0[1] + c0[2]) / 2
            highs = [perp[k][3] for k in sorted(perp) if t0 < k <= t1]
            hour_moves = [(perp[k][3] - perp[k][4]) / perp[k][4] for k in sorted(perp) if t0 < k <= t1]
            exc = R.adverse_excursion(p_start, highs)
            w.update(entering=entering, exiting=exiting, ret=r, excess=r - R.tbill_week(dtb),
                     funding_on_cap=funding / cap, n_fund=n_fund, excursion=exc,
                     hour_move=max(hour_moves) if hour_moves else 0.0, margin_call=R.is_margin_call(t, exc),
                     n_candles=len(highs))
        weeks.append(w)
    return weeks, held, missing_funding


def summarise(xs):
    b = bootstrap_mean(xs)
    b["ann"] = b["mean"] * 52 if b["mean"] is not None else None
    return b


def analyze(last_decision: str | None = None):
    last = dt.datetime.fromisoformat((last_decision or "2026-10-19") + "T20:00:00+00:00")
    decisions, d = [], FIRST_DECISION
    while d <= last:
        decisions.append(d)
        d += dt.timedelta(days=7)
    dtb3 = load_dtb3()
    L = [f"H10 IN-SAMPLE HALF — CONTAMINATED (funding means seen in Session 38). Plumbing check, NOT the "
         f"binding read. Run {dt.datetime.now(UTC):%Y-%m-%dT%H:%MZ}.",
         f"Decisions (Monday 20:00 UTC): {decisions[0]:%Y-%m-%d} .. {decisions[-1]:%Y-%m-%d} -> "
         f"{len(decisions) - 1} weeks. Sign: positive rate = longs pay shorts (Kalshi help center).",
         "Kill (binding version, at the checkpoint): combined ann. excess <= 0; p >= 0.0167; any third <= 0; "
         "forward 6 weeks <= 0; any margin call; ann. excess < 2.0% (operator-accepted floor).", ""]
    variants = {"FROZEN": {}, "SENS spot fee 60 bps/side (unverified)": {"hedge_fee": 0.0060},
                "SENS + Kalshi margin interest 3.25% APY": {"margin_apy": 0.0325}}
    for label, kw in variants.items():
        per = {}
        for t in SPOT:
            per[t] = run_asset(t, decisions, dtb3, **kw)
        n = len(decisions) - 1
        ew = [(per["KXBTCPERP"][0][i]["excess"] + per["KXETHPERP"][0][i]["excess"]) / 2 for i in range(n)]
        s = summarise(ew)
        L += ["=" * 100, label, "=" * 100]
        L.append(f"  equal-weight weekly excess: mean {s['mean'] * 100:.4f}%/wk = {s['ann'] * 100:.2f}%/yr; "
                 f"95% CI/yr [{s['lo'] * 5200:.2f}%, {s['hi'] * 5200:.2f}%]; one-sided P(mean<=0) = {s['p_le_0']:.4f}")
        thirds = [("weeks 1-8", 0, 8), ("weeks 9-16", 8, 16), ("weeks 17-18 (partial third)", 16, n)]
        for name, a, b in thirds:
            seg = ew[a:b]
            if seg:
                L.append(f"    {name:28} mean {sum(seg) / len(seg) * 5200:.2f}%/yr  (n={len(seg)})")
        for t in SPOT:
            weeks, held, miss = per[t]
            hs = [w for w in weeks if w["held"]]
            ss = summarise([w["excess"] for w in weeks])
            L.append(f"  {t}: held {len(hs)}/{n} weeks; ann. excess {ss['ann'] * 100:.2f}%/yr "
                     f"(P={ss['p_le_0']:.4f}); missing decision-time funding rows {miss}; next-decision signal "
                     f"{held[-1]}")
            if label == "FROZEN":
                L.append(f"    {'week':10} {'held':>4} {'in':>3} {'out':>3} {'ret%':>7} {'fund%':>6} {'excess%':>8} "
                         f"{'n_f':>3} {'cand':>4} {'worst cum%':>10} {'worst 1h%':>9} {'call':>4}")
                for w in weeks:
                    if w["held"]:
                        L.append(f"    {w['start']:10} {'Y':>4} {'Y' if w['entering'] else '':>3} "
                                 f"{'Y' if w['exiting'] else '':>3} {w['ret'] * 100:7.3f} {w['funding_on_cap'] * 100:6.3f} "
                                 f"{w['excess'] * 100:8.3f} {w['n_fund']:>3} {w['n_candles']:>4} "
                                 f"{w['excursion'] * 100:10.2f} {w['hour_move'] * 100:9.2f} {'YES' if w['margin_call'] else '':>4}")
                    else:
                        L.append(f"    {w['start']:10} {'':>4}  flat (T-bills)")
                L.append(f"    margin buffer {R.margin_buffer(t) * 100:.1f}% of notional; call threshold "
                         f"{R.MARGIN_CALL_SHARE * R.margin_buffer(t) * 100:.1f}%; margin calls: "
                         f"{sum(w.get('margin_call', False) for w in weeks)}")
        if label == "FROZEN":
            static = {t: run_asset(t, decisions, dtb3, held_override=[True] * (len(decisions) - 1) + [False])[0]
                      for t in SPOT}
            sew = [(static["KXBTCPERP"][i]["excess"] + static["KXETHPERP"][i]["excess"]) / 2 for i in range(n)]
            st = summarise(sew)
            L.append(f"  static always-on carry (enter week 1, exit after week {n}): {st['ann'] * 100:.2f}%/yr "
                     f"excess, P(mean<=0) = {st['p_le_0']:.4f}; BTC "
                     f"{summarise([w['excess'] for w in static['KXBTCPERP']])['ann'] * 100:.2f}%, ETH "
                     f"{summarise([w['excess'] for w in static['KXETHPERP']])['ann'] * 100:.2f}%")
        L.append("")
    text = "\n".join(L) + "\n"
    open(REPORT, "w").write(text)
    print(text)


if __name__ == "__main__":
    if sys.argv[1] == "fetch":
        fetch()
    else:
        analyze(sys.argv[2] if len(sys.argv) > 2 else None)
