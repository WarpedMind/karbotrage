"""Step 1 of the calibration harness: prove the ground truth before modelling it.

Two hard gates, both of which must pass before a single probability is computed:

1. **Strike logic.** Replay the settlement rule against every settled market's
   real ``result`` using its real ``expiration_value``. Any mismatch means the
   interpretation of ``strike_type``/``floor_strike``/``cap_strike`` is wrong,
   and everything downstream is a well-calibrated model of the wrong event.
2. **Station identity.** Match Kalshi's settlement values day-for-day against
   candidate stations' NWS CLI highs, so "Dallas" resolves to a specific
   station on evidence rather than on a guess.

Run:  karbotrage_env/bin/python -m backtest.resolve_and_verify
"""

from __future__ import annotations

import json
import os
import sys

import requests

from . import kalshi_history as kh
from . import probability as prob
from . import stations as st


def main() -> int:
    sess = requests.Session()
    observed_by_series = {}
    markets_by_series = {}
    all_markets = []

    print("=" * 78)
    print("PULLING SETTLED KALSHI DAILY-HIGH MARKETS")
    print("=" * 78)
    for series in sorted(st.CANDIDATES):
        raw = kh.settled_markets(series, session=sess)
        mkts = kh.to_weather_markets(raw)
        if not mkts:
            print(f"  {series:14s} no settled markets")
            continue
        markets_by_series[series] = mkts
        all_markets.extend(mkts)
        observed_by_series[series] = kh.observed_high_by_day(mkts)
        days = sorted({m.local_day for m in mkts})
        print(
            f"  {series:14s} markets={len(mkts):5d}  city-days={len(days):4d}  "
            f"{days[0]} .. {days[-1]}  obs_days={len(observed_by_series[series])}"
        )

    print()
    print("=" * 78)
    print("GATE 1 - STRIKE LOGIC REPLAYED AGAINST REAL SETTLEMENTS")
    print("=" * 78)
    check = prob.verify_strike_logic(all_markets)
    print(f"  markets seen    : {check['seen']}")
    print(f"  markets checked : {check['total']}")
    print(f"  reproduced      : {check['matched']}")
    print(f"  MISMATCHES      : {check['mismatch']}")
    print(f"  skipped         : {check['skipped'] or 'none'}")
    for stype, (n, ok) in sorted(check["by_type"].items()):
        print(f"    {stype:9s} {ok}/{n}")
    for f in check["failures"]:
        print(f"    FAIL {f}")
    unhandled = {k: v for k, v in check["skipped"].items() if k.startswith("unhandled:")}
    missing = [m.ticker for m in all_markets if m.expiration_value is None]
    if missing:
        # Data absence, not a logic gap -- reported by name so it stays visible.
        print(f"  markets with no expiration_value: {missing}")
    if check["mismatch"] or unhandled:
        print("\n  STOP: strike interpretation is wrong or incomplete.")
        return 1
    print("  PASS")

    # Ladders should be an exhaustive partition: exactly one YES per city-day.
    print()
    print("  Ladder partition check (exactly one YES per city-day):")
    bad = 0
    by_day = {}
    for m in all_markets:
        by_day.setdefault((m.series, m.local_day), []).append(m)
    for (series, day), mkts in sorted(by_day.items()):
        yes = sum(m.outcome for m in mkts)
        if yes != 1:
            bad += 1
            if bad <= 10:
                print(f"    {series} {day}: {yes} YES out of {len(mkts)}")
    print(f"    city-days={len(by_day)}  violations={bad}")

    print()
    print("=" * 78)
    print("GATE 2 - STATION IDENTITY FROM NWS CLI HIGHS")
    print("=" * 78)
    resolved = st.resolve_stations(observed_by_series, session=sess)
    mapping = {}
    for series in sorted(resolved):
        info = resolved[series]
        station, score = info["best"]
        runner = info["runner_up"]
        rr = ""
        if runner:
            rs = runner[1]
            rr = (
                f"   (runner-up {runner[0]}: exact={rs['exact_rate']:.0%} "
                f"mae={rs['mae']:.2f})"
                if rs.get("exact_rate") is not None
                else f"   (runner-up {runner[0]}: no data)"
            )
        if score.get("exact_rate") is None:
            print(f"  {series:14s} -> {station}  NO CLI DATA{rr}")
            continue
        flag = "OK " if info["station"] else "AMBIGUOUS"
        print(
            f"  {series:14s} -> {station:5s} {flag} exact={score['exact_rate']:.1%} "
            f"n={score['n']:3d} mae={score['mae']:.2f} bias={score['bias']:+.2f}{rr}"
        )
        if info["station"]:
            mapping[series] = info["station"]

    out_path = os.path.join(os.path.dirname(__file__), "resolved_stations.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, indent=1, sort_keys=True)
    print(f"\n  resolved {len(mapping)}/{len(resolved)} series -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
