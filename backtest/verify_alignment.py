"""Step 2: prove the NBM valid-time -> Kalshi local-day mapping empirically.

NBM publishes max/min temperature on alternating 12-hour windows. The value
valid at 00Z is *believed* to be the daytime max of the preceding 12Z-00Z
window, which for a CONUS station falls inside local calendar day
``valid_date - 1``. That belief is the single most dangerous assumption in this
harness: a one-day misalignment produces a model that still looks sane -- July
highs correlate strongly day to day -- but is forecasting the wrong event, and
the calibration report would read as "no edge" for entirely the wrong reason.

So it is tested rather than assumed. For every 00Z valid time in the horizon,
the forecast is scored against the observed high under both mappings:

    H1  local_day = valid_date - 1     (the 12Z-00Z daytime-max window)
    H2  local_day = valid_date         (the naive same-date reading)

The correct mapping shows a small bias and an MAE consistent with a real
short-range forecast error (~1-3 F). The wrong one shows day-to-day
climatological noise (~4-8 F in summer). The separation is not subtle.

Ground truth is Kalshi's own ``expiration_value``, i.e. the exact number the
market settled against -- not a proxy observation feed.

Run:  karbotrage_env/bin/python -m backtest.verify_alignment
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from typing import Dict, List

import requests

from . import kalshi_history as kh
from . import nbm_text as nbm
from . import stations as st

SAMPLE_CYCLE_HOUR = 12
N_SAMPLE_DAYS = 14


def _load_observed() -> Dict[str, Dict[dt.date, float]]:
    sess = requests.Session()
    out = {}
    for series in sorted(st.CANDIDATES):
        mkts = kh.to_weather_markets(kh.settled_markets(series, session=sess))
        if mkts:
            out[series] = kh.observed_high_by_day(mkts)
    return out


def main() -> int:
    map_path = os.path.join(os.path.dirname(__file__), "resolved_stations.json")
    with open(map_path, "r", encoding="utf-8") as fh:
        series_to_station = json.load(fh)

    observed = _load_observed()
    all_days = sorted({d for obs in observed.values() for d in obs})
    # Spread the probe days across the whole sample rather than taking a
    # contiguous block -- a single synoptic pattern could otherwise make both
    # hypotheses look equally good.
    step = max(len(all_days) // N_SAMPLE_DAYS, 1)
    probe_days = all_days[::step][:N_SAMPLE_DAYS]

    stations = sorted(set(series_to_station.values()))
    station_to_series = {v: k for k, v in series_to_station.items()}

    print("=" * 78)
    print("GATE 3 - NBM VALID TIME -> KALSHI LOCAL DAY")
    print("=" * 78)
    print(f"  product=NBS cycle={SAMPLE_CYCLE_HOUR:02d}Z  probe cycles={len(probe_days)}")
    print(f"  stations={len(stations)}")

    # errors[hypothesis][lead_bucket] -> list of (forecast - observed)
    errors: Dict[str, Dict[int, List[float]]] = {"H1": {}, "H2": {}}

    sess = requests.Session()
    for cycle_day in probe_days:
        try:
            text = nbm.fetch_bulletin(cycle_day, SAMPLE_CYCLE_HOUR, "nbs", session=sess)
        except (nbm.NbmFetchError, requests.RequestException) as exc:
            print(f"  [skip] {cycle_day}: {exc}")
            continue
        parsed = nbm.parse_bulletin(text, stations)
        for station, fc in parsed.items():
            series = station_to_series[station]
            obs = observed.get(series, {})
            txn = fc.rows.get("TXN")
            if not txn:
                continue
            for i, valid in enumerate(fc.valid):
                if valid.hour != 0 or txn[i] is None:
                    continue
                lead = fc.fhr[i]
                for tag, day in (
                    ("H1", valid.date() - dt.timedelta(days=1)),
                    ("H2", valid.date()),
                ):
                    if day in obs:
                        errors[tag].setdefault(lead, []).append(float(txn[i]) - obs[day])

    def stats(vals: List[float]) -> str:
        if not vals:
            return "     --"
        n = len(vals)
        bias = sum(vals) / n
        mae = sum(abs(v) for v in vals) / n
        rmse = (sum(v * v for v in vals) / n) ** 0.5
        return f"n={n:4d} bias={bias:+5.2f} mae={mae:4.2f} rmse={rmse:4.2f}"

    print()
    print(f"  {'lead(h)':>8}  {'H1: local_day = valid_date - 1':<36}  H2: local_day = valid_date")
    leads = sorted(set(errors["H1"]) | set(errors["H2"]))
    for lead in leads:
        print(
            f"  {lead:>8}  {stats(errors['H1'].get(lead, [])):<36}  "
            f"{stats(errors['H2'].get(lead, []))}"
        )

    pooled1 = [v for vals in errors["H1"].values() for v in vals]
    pooled2 = [v for vals in errors["H2"].values() for v in vals]
    print()
    print(f"  POOLED H1  {stats(pooled1)}")
    print(f"  POOLED H2  {stats(pooled2)}")

    if not pooled1 or not pooled2:
        print("\n  INCONCLUSIVE - no overlapping data.")
        return 1
    mae1 = sum(abs(v) for v in pooled1) / len(pooled1)
    mae2 = sum(abs(v) for v in pooled2) / len(pooled2)
    winner = "H1" if mae1 < mae2 else "H2"
    print(f"\n  WINNER: {winner}  (MAE {min(mae1, mae2):.2f} vs {max(mae1, mae2):.2f})")
    if winner != "H1":
        print("  WARNING: the assumed mapping in nbm_text.daytime_max_valid_time is WRONG.")
        return 1
    print("  PASS - daytime max valid at 00Z belongs to local day (valid_date - 1).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
