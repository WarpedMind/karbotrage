"""Mapping Kalshi daily-temperature series to the NWS station they settle on.

Kalshi's ``rules_primary`` names the settlement site, but only sometimes names
it unambiguously. Compare:

    KXHIGHLAX  "...recorded in Los Angeles Airport, CA..."      -> KLAX, clear
    KXHIGHCHI  "...recorded at Chicago Midway, IL..."           -> KMDW, clear
    KXHIGHTDAL "...recorded at Dallas..."                       -> KDFW or KDAL?
    KXHIGHTHOU "...recorded at Houston..."                      -> KIAH or KHOU?
    KXHIGHTDC  "...recorded at Washington DC..."                -> KDCA or KIAD?

Those are not cosmetic differences. DFW and Dallas Love routinely differ by a
degree or two, and IAH vs Hobby more than that -- enough to swamp any claimed
forecast edge. Guessing here would produce a model that is subtly wrong in a
way no test would catch.

So the mapping is **resolved empirically, not asserted**: for each candidate
station, the NWS Climatological Report daily highs are pulled from Iowa State's
IEM archive and compared, day for day, against Kalshi's own
``expiration_value``. The correct station matches on essentially every day; the
wrong one does not.

WHICH IEM ENDPOINT MATTERS -- a trap worth recording. IEM exposes two daily
temperature feeds and they disagree:

    /cgi-bin/request/daily.py   (ASOS-derived)   KLAX 2026-08-01 -> 79 F
    /json/cli.py                (parsed NWS CLI) KLAX 2026-08-01 -> 80 F

Kalshi's ``expiration_value`` for that day is 80.00. Kalshi settles on the CLI
product -- exactly as its rules say -- and the ASOS daily feed, which is the
more obvious-looking endpoint, is off by a degree. Kalshi's own
``rules_secondary`` warns about precisely this ("rounding and conversion
nuances"). Use ``json/cli.py``.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Dict, List, Optional, Sequence, Tuple

import requests

IEM_CLI = "https://mesonet.agron.iastate.edu/json/cli.py"
USER_AGENT = "karbotrage-backtest/0.1 (research; contact: tomgrow@gmail.com)"
CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")

#: Kalshi daily-HIGH temperature series -> candidate NWS/ICAO stations.
#: The first entry is the reading suggested by the rules text; the rest are the
#: plausible alternatives that ``resolve_stations`` discriminates between.
CANDIDATES: Dict[str, Tuple[str, ...]] = {
    "KXHIGHLAX": ("KLAX",),
    "KXHIGHNY": ("KNYC", "KLGA", "KJFK", "KEWR"),
    "KXHIGHCHI": ("KMDW", "KORD"),
    "KXHIGHAUS": ("KAUS", "KATT"),
    "KXHIGHMIA": ("KMIA", "KFLL"),
    "KXHIGHDEN": ("KDEN", "KAPA", "KBKF"),
    "KXHIGHPHIL": ("KPHL",),
    "KXHIGHTATL": ("KATL", "KFTY", "KPDK"),
    "KXHIGHTBOS": ("KBOS",),
    "KXHIGHTDAL": ("KDFW", "KDAL"),
    "KXHIGHTDC": ("KDCA", "KIAD", "KBWI"),
    "KXHIGHTHOU": ("KIAH", "KHOU"),
    "KXHIGHTLV": ("KLAS",),
    "KXHIGHTMIN": ("KMSP",),
    "KXHIGHTNOLA": ("KMSY", "KNEW"),
    "KXHIGHTOKC": ("KOKC", "KPWA"),
    "KXHIGHTSATX": ("KSAT",),
    "KXHIGHTSFO": ("KSFO", "KOAK"),
}


def cli_daily_highs(
    station: str,
    year: int,
    *,
    session: Optional[requests.Session] = None,
    use_cache: bool = True,
) -> Dict[dt.date, int]:
    """Daily high temperatures from the parsed NWS CLI product, one year."""
    path = os.path.join(CACHE_DIR, "cli", f"{station}.{year}.json")
    if use_cache and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    else:
        http = session or requests
        resp = http.get(
            IEM_CLI,
            params={"station": station, "year": year},
            headers={"User-Agent": USER_AGENT},
            timeout=90,
        )
        if resp.status_code != 200:
            return {}
        raw = resp.json().get("results", [])
        if use_cache:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(raw, fh)

    out: Dict[dt.date, int] = {}
    for row in raw:
        high = row.get("high")
        if high is None:
            continue
        try:
            out[dt.date.fromisoformat(row["valid"])] = int(high)
        except (KeyError, ValueError, TypeError):
            continue
    return out


def score_candidate(
    observed: Dict[dt.date, float], station_highs: Dict[dt.date, int]
) -> dict:
    """Agreement between Kalshi's settlement values and one station's CLI highs."""
    common = [d for d in observed if d in station_highs]
    if not common:
        return {"n": 0, "exact": 0, "exact_rate": None, "mae": None, "bias": None}
    diffs = [station_highs[d] - observed[d] for d in common]
    exact = sum(1 for v in diffs if abs(v) < 1e-9)
    return {
        "n": len(common),
        "exact": exact,
        "exact_rate": exact / len(common),
        "mae": sum(abs(v) for v in diffs) / len(diffs),
        "bias": sum(diffs) / len(diffs),
    }


def resolve_stations(
    observed_by_series: Dict[str, Dict[dt.date, float]],
    *,
    years: Sequence[int] = (2026,),
    min_exact_rate: float = 0.90,
    session: Optional[requests.Session] = None,
) -> Dict[str, dict]:
    """Pick the station whose CLI highs reproduce Kalshi's settlement values.

    Returns per-series diagnostics including the runner-up, so an ambiguous
    result is visible rather than being silently resolved by argmax.
    """
    sess = session or requests.Session()
    out: Dict[str, dict] = {}
    for series, observed in observed_by_series.items():
        results = []
        for station in CANDIDATES.get(series, ()):
            highs: Dict[dt.date, int] = {}
            for year in years:
                highs.update(cli_daily_highs(station, year, session=sess))
            results.append((station, score_candidate(observed, highs)))
        results.sort(
            key=lambda item: (
                -(item[1]["exact_rate"] or -1),
                item[1]["mae"] if item[1]["mae"] is not None else 1e9,
            )
        )
        best = results[0] if results else (None, {})
        runner = results[1] if len(results) > 1 else None
        out[series] = {
            "station": best[0] if (best[1].get("exact_rate") or 0) >= min_exact_rate else None,
            "best": best,
            "runner_up": runner,
            "all": results,
        }
    return out
