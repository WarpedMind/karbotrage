"""Why does the market beat the NBM model? Two very different answers.

A negative calibration result is only useful if it says *which* thing failed,
because the two possibilities imply opposite follow-ups:

  (a) **The market's point forecast is better.** The market knows something
      about the expected temperature that a single NBM run does not -- later
      observations, other model guidance, human judgement. Then there is no
      cheap fix: a better error model around the same mean cannot recover an
      inferior mean, and the direction is genuinely dead for this data source.

  (b) **The point forecasts are comparable but NBM's published uncertainty is
      wrong.** The text bulletins publish the spread as an integer degree, so a
      true spread of 2.3 F is published as 2. Then the failure is in the
      probability conversion, not the forecast, and the concrete follow-up is a
      properly estimated per-station, per-lead error distribution (or the GRIB2
      float fields) rather than abandoning the idea.

This script measures both.

The market's implied expected temperature is recovered from the city-day
ladder, which is an exhaustive, mutually exclusive partition (confirmed on all
1,261 real city-days: exactly one YES each). Normalising the ladder's YES
prices gives a discrete distribution over temperature buckets; its mean is the
market's implied point forecast.

Run:  karbotrage_env/bin/python -m backtest.diagnose_gap
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from . import probability as prob

#: Half-width assumed for the open-ended tail rungs of a ladder when turning it
#: into a discrete distribution. The tails are "<= a" and ">= b"; representing
#: them at a-1.5 and b+1.5 matches the typical 2 F bucket width used elsewhere
#: on the ladder. It shifts the implied mean only when the tails carry real
#: probability, and the sensitivity is reported below rather than hidden.
TAIL_OFFSET = 1.5


def bucket_center(strike_type: str, floor_strike, cap_strike) -> Optional[float]:
    if strike_type == "between":
        if floor_strike is None or cap_strike is None:
            return None
        return (float(floor_strike) + float(cap_strike)) / 2.0
    if strike_type == "greater":
        if floor_strike is None:
            return None
        return float(floor_strike) + TAIL_OFFSET
    if strike_type == "less":
        if cap_strike is None:
            return None
        return float(cap_strike) - TAIL_OFFSET
    return None


def implied_mean(rungs: List[Tuple[float, float]]) -> Optional[float]:
    """rungs: list of (probability, bucket_center). Normalised, then averaged."""
    total = sum(p for p, _ in rungs)
    if total <= 0.0:
        return None
    return sum(p * c for p, c in rungs) / total


def main() -> int:
    path = os.path.join(os.path.dirname(__file__), "cache", "rows.json")
    if not os.path.exists(path):
        print(f"missing {path} -- run backtest.run_calibration first")
        return 1
    with open(path, "r", encoding="utf-8") as fh:
        rows = json.load(fh)

    # Group into ladders: one city-day at one lead is one ladder.
    ladders: Dict[Tuple[str, str, int], List[dict]] = defaultdict(list)
    for r in rows:
        ladders[(r["series"], r["local_day"], r["lead_hours"])].append(r)

    print("=" * 78)
    print("  WHY THE MARKET WINS: point forecast vs uncertainty")
    print("=" * 78)

    by_lead: Dict[int, Dict[str, List[float]]] = defaultdict(
        lambda: {"nbm": [], "mkt": [], "sd_pub": [], "tail": []}
    )

    for (series, day, lead), rungs in ladders.items():
        observed = rungs[0]["observed_high"]
        mu = rungs[0]["mu"]
        sd = rungs[0]["sd"]
        if observed is None or mu is None:
            continue
        pairs = []
        tail_mass = 0.0
        for r in rungs:
            center = bucket_center(r["strike_type"], r["floor_strike"], r["cap_strike"])
            if center is None or r["market_mid"] is None:
                continue
            pairs.append((r["market_mid"], center))
            if r["strike_type"] in ("greater", "less"):
                tail_mass += r["market_mid"]
        if len(pairs) < 4:
            continue
        mkt_mu = implied_mean(pairs)
        if mkt_mu is None:
            continue
        by_lead[lead]["nbm"].append(mu - observed)
        by_lead[lead]["mkt"].append(mkt_mu - observed)
        by_lead[lead]["tail"].append(tail_mass / max(sum(p for p, _ in pairs), 1e-9))
        if sd is not None:
            by_lead[lead]["sd_pub"].append(float(sd))

    def stat(vals: List[float]) -> Tuple[float, float, float]:
        n = len(vals)
        bias = sum(vals) / n
        mae = sum(abs(v) for v in vals) / n
        rmse = math.sqrt(sum(v * v for v in vals) / n)
        return bias, mae, rmse

    print("\n  (a) POINT FORECAST -- error against the settled high, in degrees F")
    print(f"    {'lead':>5} {'n':>6}  {'source':<8} {'bias':>7} {'MAE':>7} {'RMSE':>7}")
    for lead in sorted(by_lead):
        d = by_lead[lead]
        for tag, key in (("NBM", "nbm"), ("market", "mkt")):
            b, m, rm = stat(d[key])
            print(f"    {lead:5d} {len(d[key]):6d}  {tag:<8} {b:+7.2f} {m:7.2f} {rm:7.2f}")

    print("\n  (b) UNCERTAINTY -- NBM's published spread vs its own realised error")
    print(f"    {'lead':>5}  {'published SD':>12}  {'realised RMSE':>13}  {'ratio':>7}")
    for lead in sorted(by_lead):
        d = by_lead[lead]
        if not d["sd_pub"]:
            continue
        pub = sum(d["sd_pub"]) / len(d["sd_pub"])
        _, _, rm = stat(d["nbm"])
        print(f"    {lead:5d}  {pub:12.2f}  {rm:13.2f}  {rm / pub:7.2f}")
    print("\n    A ratio above 1 means the published spread is TOO NARROW, i.e.")
    print("    every probability derived from it is overconfident. NBM's text")
    print("    bulletins publish the spread as an INTEGER degree, so this is a")
    print("    live suspect and not merely a modelling choice.")

    print("\n  Sensitivity: mean probability mass sitting in the open-ended")
    print("  tail rungs, where the TAIL_OFFSET assumption bites:")
    for lead in sorted(by_lead):
        t = by_lead[lead]["tail"]
        print(f"    lead {lead:3d}h: {sum(t) / len(t):.3f}")

    print("\n" + "=" * 78)
    for lead in sorted(by_lead):
        d = by_lead[lead]
        _, nbm_mae, _ = stat(d["nbm"])
        _, mkt_mae, _ = stat(d["mkt"])
        verdict = (
            "MARKET has the better point forecast"
            if mkt_mae < nbm_mae - 0.05
            else "point forecasts are comparable"
            if abs(mkt_mae - nbm_mae) <= 0.05
            else "NBM has the better point forecast"
        )
        print(f"  lead {lead:3d}h: {verdict} "
              f"(NBM MAE {nbm_mae:.2f} vs market {mkt_mae:.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
