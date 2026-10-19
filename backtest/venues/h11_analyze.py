"""H11 — Kalshi <-> Polymarket US same-station weather crossings (Session 39).

Runs the test pre-registered in DECISIONS.md Session 38, with the implementation
choices declared in backtest/reports/session39_declared_before_results.txt.
Offline only; reads the caches written by backtest.venues.h11_fetch.

    karbotrage_env/bin/python -m backtest.venues.h11_analyze
Writes backtest/reports/venue_h11_crossings.txt
"""

from __future__ import annotations

import collections
import datetime as dt
import json
import os
import statistics

from backtest.venues import h11_rule as R
from backtest.venues.h11_fetch import (CACHE, FIRST_DAY, LAST_DAY, PAIRS, h11_kalshi_markets,
                                       h11_pm_markets, kalshi_candle_path)
from backtest.venues.weather_cross_snapshot import INF, kalshi_interval

REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports", "venue_h11_crossings.txt")
PERIODS = [("P1", dt.date(2026, 4, 22), dt.date(2026, 6, 30)),
           ("P2", dt.date(2026, 7, 1), dt.date(2026, 8, 31)),
           ("P3", dt.date(2026, 9, 1), dt.date(2026, 10, 17))]
ALL_DATES = [FIRST_DAY + dt.timedelta(i) for i in range((LAST_DAY - FIRST_DAY).days + 1)]


def pm_settled_yes(m):
    op = json.loads(m.get("outcomePrices") or "[]")
    if op == ["1", "0"]:
        return True
    if op == ["0", "1"]:
        return False
    return None


def load():
    kal = collections.defaultdict(dict)  # (series, day) -> interval -> market
    for m in h11_kalshi_markets():
        iv = kalshi_interval(m)
        if iv:
            kal[(m["ticker"].split("-")[0], m["_day"])][iv] = m
    pm = collections.defaultdict(dict)
    pm_unparsed = 0
    for m in h11_pm_markets():
        iv = R.pm_title_interval(m.get("title"))
        if iv is None:
            pm_unparsed += 1
            continue
        pm[(m["_city"], m["_day"])][iv] = m
    return kal, pm, pm_unparsed


def pm_points(slug):
    fn = os.path.join(CACHE, "pm_history", slug + ".json")
    if not os.path.exists(fn):
        return None
    pts = json.load(open(fn))
    return [p[0] for p in pts], pts


def run(strict: bool, kalshi_fee=None, kal=None, pm=None):
    """Returns per-date profit, episodes and descriptive collections for one variant."""
    city_of = PAIRS
    by_date = collections.defaultdict(float)
    by_date_best = collections.defaultdict(float)
    eps_all, mids, counts = [], [], collections.Counter()
    settle = []
    joint_dates = collections.defaultdict(set)
    for (series, day), krungs in sorted(kal.items()):
        city = city_of[series]
        prungs = pm.get((city, day))
        if not prungs:
            counts["kalshi_rungs_no_pm_event"] += len(krungs)
            continue
        joint_dates[day].add(city)
        matched = set(krungs) & set(prungs)
        counts["matched_rungs"] += len(matched)
        counts["kalshi_rungs_unmatched"] += len(set(krungs) - matched)
        counts["pm_rungs_unmatched"] += len(set(prungs) - matched)
        for iv in sorted(matched):
            km, pmm = krungs[iv], prungs[iv]
            ky = {"yes": True, "no": False}.get(km["result"])
            py = pm_settled_yes(pmm)
            settle.append((series, day, iv, km["result"], py))
            cp = kalshi_candle_path(km["ticker"])
            pp = pm_points(pmm["slug"])
            if cp is None:
                counts["matched_rung_missing_kalshi_candles"] += 1
                continue
            if pp is None:
                counts["matched_rung_missing_pm_history"] += 1
                continue
            ts_list, pts = pp
            hours_a, hours_b = [], []
            for t, kb, ka in json.load(open(cp)):
                counts["kalshi_candles"] += 1
                pq = R.pm_quote(R.pair_point(ts_list, pts, t), strict=strict)
                if not R.kalshi_two_sided(kb, ka) or pq is None:
                    hours_a.append((t, None))
                    hours_b.append((t, None))
                    continue
                pb, pa = pq
                counts["paired_obs"] += 1
                a, b = R.basket_costs(kb, ka, pb, pa, kalshi_fee=kalshi_fee)
                counts["crossing_hours_A"] += R.crosses(a)
                counts["crossing_hours_B"] += R.crosses(b)
                hours_a.append((t, a))
                hours_b.append((t, b))
                mids.append(abs((kb + ka) / 2 - (pb + pa) / 2))
            for basket, hours in (("A", hours_a), ("B", hours_b)):
                for e in R.episodes(hours):
                    e.update(series=series, day=day, iv=iv, basket=basket, ticker=km["ticker"], slug=pmm["slug"])
                    eps_all.append(e)
                    by_date[day] += R.locked_profit(e["first"])
                    by_date_best[day] += R.locked_profit(e["best"])
    return dict(by_date=by_date, by_date_best=by_date_best, episodes=eps_all, mids=mids, counts=counts,
                settle=settle, joint_dates=joint_dates)


def pm_ladder_sums(pm, strict=True):
    """Descriptive: Polymarket US's own YES-basket (sum of YES asks + fees) on each city-day, hourly."""
    below, obs, best = 0, 0, None
    for (city, day), rungs in pm.items():
        if not R.is_partition(list(rungs)):
            continue
        series = [p for p in (pm_points(m["slug"]) for m in rungs.values())]
        if any(s is None for s in series):
            continue
        ends = sorted({(t // 3600) * 3600 for s in series for t in s[0]})
        for t in ends:
            asks = []
            for ts_list, pts in series:
                p = R.pair_point(ts_list, pts, t)
                if p is None or p[1] is None or not (0 < float(p[1]) < 0.99):
                    asks = None
                    break
                asks.append(float(p[1]))
            if asks is None:
                continue
            obs += 1
            net = sum(a + R.fee(R.PM_THETA, a) for a in asks)
            best = net if best is None else min(best, net)
            below += R.crosses(net)
    return obs, below, best


def summarise(res, dates):
    vals = [res["by_date"].get(d.isoformat(), 0.0) for d in dates]
    out = {"all": R.bootstrap_mean(vals)}
    for name, lo, hi in PERIODS:
        pv = [res["by_date"].get(d.isoformat(), 0.0) for d in dates if lo <= d <= hi]
        out[name] = {"n": len(pv), "mean": sum(pv) / len(pv) if pv else None, "total": sum(pv)}
    return out


def fmt_iv(iv):
    lo = "" if iv[0] == -INF else iv[0]
    hi = "" if iv[1] == INF else iv[1]
    return f"{lo}..{hi}"


def _seed_bid(pts, t):
    """True if the PM bid at t is still the listing seed: shortPrice has been exactly 0.5 since the first point."""
    for p in pts:
        if p[0] > t:
            break
        if p[2] != 0.5:
            return False
    return True


def diagnose(res):
    """POST-HOC descriptive breakdown of the binding episodes. Not part of the pre-registered test; it can
    only explain the binding number, never rescue it (every flag below marks an episode as LESS credible)."""
    L = ["=" * 100, "POST-HOC DIAGNOSTIC (not part of the test): where the binding $/day comes from", "=" * 100]
    groups = collections.defaultdict(lambda: [0, 0.0])
    bands = collections.defaultdict(lambda: [0, 0.0])
    cache = {}
    for e in res["episodes"]:
        if e["slug"] not in cache:
            cache[e["slug"]] = json.load(open(os.path.join(CACHE, "pm_history", e["slug"] + ".json")))
        pts = cache[e["slug"]]
        seed = e["basket"] == "A" and _seed_bid(pts, e["start"])
        first_ts = pts[0][0] if pts else e["start"]
        early = e["start"] - first_ts < 6 * 3600
        key = "A, PM bid still the 0.5 listing seed" if seed else (
            "within 6h of first PM quote point" if early else "other")
        prof = R.locked_profit(e["first"])
        groups[key][0] += 1
        groups[key][1] += prof
        band = ("net < 0.90" if e["first"] < 0.90 else "0.90-0.97" if e["first"] < 0.97 else "0.97-1.00")
        if not seed:
            bands[band][0] += 1
            bands[band][1] += prof
    n_days = len(ALL_DATES)
    for k, (c, p) in sorted(groups.items()):
        L.append(f"  {k:40} episodes {c:>5}  locked ${p:9.2f}  = ${p / n_days:.3f}/day")
    L.append("  excluding seed-bid episodes, by first-hour net cost:")
    for k, (c, p) in sorted(bands.items()):
        L.append(f"    {k:12} episodes {c:>5}  locked ${p:9.2f}  = ${p / n_days:.3f}/day")
    mia = [e for e in res["episodes"] if e["series"] == "KXHIGHMIA" and e["day"] == "2026-08-29"]
    L.append(f"  episodes on the mis-settled KXHIGHMIA 2026-08-29 event: {len(mia)}")
    for e in mia:
        L.append(f"    {fmt_iv(e['iv'])} basket {e['basket']} hours {e['hours']} first {e['first']:.4f}")
    return L


def main():
    kal, pm, pm_unparsed = load()
    lines = [f"H11 — Kalshi <-> Polymarket US same-station daily-high crossings. Run "
             f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%MZ}.",
             f"Window: event dates {FIRST_DAY} -> {LAST_DAY} ({len(ALL_DATES)} dates). "
             f"Size per episode: {R.CONTRACTS} contracts (assumed). Kill: mean < $10/day, p >= 0.0167, "
             "any period mean = 0, any matched rung settled differently.", ""]
    failed = os.path.join(CACHE, "failed.txt")
    lines.append(f"fetch failures listed in failed.txt: "
                 f"{sum(1 for _ in open(failed)) if os.path.exists(failed) else 0}")
    lines.append(f"Polymarket rungs with unparseable titles (excluded): {pm_unparsed}")

    variants = [("BINDING (strict: PM bid < ask required)", dict(strict=True)),
                ("LITERAL as written (PM locked/crossed points allowed)", dict(strict=False)),
                ("SENSITIVITY strict + Kalshi fee ceil'd to the cent on 10 lots",
                 dict(strict=True, kalshi_fee=R.kalshi_fee_ceil))]
    results = {}
    for label, kw in variants:
        res = run(kal=kal, pm=pm, **kw)
        results[label] = res
        c = res["counts"]
        s = summarise(res, ALL_DATES)
        joint = [d for d in ALL_DATES if res["joint_dates"].get(d.isoformat())]
        sj = summarise(res, joint)
        lines += ["", "=" * 100, label, "=" * 100]
        for k in ("matched_rungs", "kalshi_rungs_unmatched", "pm_rungs_unmatched", "kalshi_rungs_no_pm_event",
                  "matched_rung_missing_kalshi_candles", "matched_rung_missing_pm_history", "kalshi_candles",
                  "paired_obs", "crossing_hours_A", "crossing_hours_B"):
            lines.append(f"  {k:40} {c[k]:>10,}")
        lines.append(f"  {'episodes':40} {len(res['episodes']):>10,}")
        lines.append(f"  {'dates with >=1 jointly listed city':40} {len(joint):>10,}")
        a = s["all"]
        lines.append("")
        lines.append(f"  mean locked $/day, all {a['n']} dates: {a['mean']:.4f}   95% CI [{a['lo']:.4f}, {a['hi']:.4f}]"
                     f"   one-sided P(mean<=0) = {a['p_le_0']:.4f}")
        for name, _, _ in PERIODS:
            p = s[name]
            lines.append(f"    {name}: n={p['n']:>3} dates  mean ${p['mean']:.4f}/day  total ${p['total']:.2f}")
        aj = sj["all"]
        if aj["n"]:
            lines.append(f"  sensitivity, jointly listed dates only (n={aj['n']}): mean {aj['mean']:.4f}/day, "
                         f"P(mean<=0) = {aj['p_le_0']:.4f}")
        best_vals = [res["by_date_best"].get(d.isoformat(), 0.0) for d in ALL_DATES]
        lines.append(f"  upper bound, best hour of each episode: mean ${sum(best_vals) / len(best_vals):.4f}/day")
        by_city = collections.defaultdict(float)
        for e in res["episodes"]:
            by_city[PAIRS[e["series"]]] += R.locked_profit(e["first"])
        lines.append("  locked $ by city: " + ", ".join(f"{k} {v:.2f}" for k, v in sorted(by_city.items())))
        eps = sorted(res["episodes"], key=lambda e: e["first"])
        if eps:
            lines.append(f"  episodes: hours median {statistics.median(e['hours'] for e in eps)}, "
                         f"max {max(e['hours'] for e in eps)}; first-hour net cost median "
                         f"{statistics.median(e['first'] for e in eps):.4f}")
            lines.append("  cheapest 15 episodes (first-hour net cost):")
            for e in eps[:15]:
                lines.append(f"    {e['day']} {PAIRS[e['series']]:8} {fmt_iv(e['iv']):>8} basket {e['basket']} "
                             f"start {dt.datetime.fromtimestamp(e['start'], dt.timezone.utc):%m-%dT%H}Z "
                             f"hours {e['hours']:>2} first {e['first']:.4f} best {e['best']:.4f}  {e['ticker']}")

    binding = results[variants[0][0]]
    lines += ["", "=" * 100, "DESCRIPTIVE (binding variant)", "=" * 100]
    mism = [x for x in binding["settle"] if x[4] is None or x[3] not in ("yes", "no")
            or (x[3] == "yes") != x[4]]
    lines.append(f"matched rungs compared at settlement: {len(binding['settle'])}; settled differently or "
                 f"unresolved on either venue: {len(mism)}")
    for x in mism[:30]:
        lines.append(f"    {x[1]} {x[0]} {fmt_iv(x[2])} kalshi={x[3]} pm_yes={x[4]}")
    md = sorted(binding["mids"])
    if md:
        q = lambda f: md[min(len(md) - 1, int(f * len(md)))]
        lines.append(f"|Kalshi mid - PM mid| on {len(md):,} paired obs: p50 {q(.5):.4f}  p75 {q(.75):.4f}  "
                     f"p90 {q(.9):.4f}  p99 {q(.99):.4f}  max {md[-1]:.4f}; share 0: "
                     f"{sum(x < 1e-9 for x in md) / len(md):.3f}")
    obs, below, best = pm_ladder_sums(pm)
    lines.append(f"Polymarket US own YES-basket (sum of YES asks + fees), hourly marks with every rung quoted: "
                 f"{obs:,} obs, {below} below $1, cheapest {best if best is None else round(best, 4)}")
    lines += [""] + diagnose(binding)
    text = "\n".join(lines) + "\n"
    open(REPORT, "w").write(text)
    print(text)


if __name__ == "__main__":
    main()
