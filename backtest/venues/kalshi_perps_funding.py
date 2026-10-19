"""Descriptive screen of Kalshi perpetual-futures funding (venue screen, Session 38).

Offline, read-only, public endpoints, no auth. Never imported by the live path.
NOT the kill test: no alpha is spent here. It answers "is there enough carry on
the table to be worth a pre-registered test at all?"

Carry trade being screened: short the perp, hold the underlying (or the reverse
when funding is negative), collect funding. Positive funding_rate = longs pay
shorts (standard convention; to be confirmed against Kalshi's rulebook before
any test is frozen).

Run:  karbotrage_env/bin/python -m backtest.venues.kalshi_perps_funding
Writes backtest/reports/venue_kalshi_perps_funding.txt
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://external-api.kalshi.com/trade-api/v2/margin"
REPORT = Path(__file__).resolve().parents[1] / "reports" / "venue_kalshi_perps_funding.txt"
PERIODS_PER_YEAR = 3 * 365  # 8-hour funding, measured from consecutive funding_time


def get(path: str, params: dict | None = None) -> dict:
    for attempt in range(5):
        r = requests.get(BASE + path, params=params or {}, timeout=30)
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(path)


def main() -> None:
    markets = get("/markets", {"limit": 200})["markets"]
    lines = [f"fetched {datetime.now(timezone.utc):%Y-%m-%dT%H:%MZ}; {len(markets)} perp markets", ""]
    hdr = (f"{'ticker':14} {'from':10} {'n':>4} {'mean bp/8h':>10} {'ann %':>7} {'|ann| %':>8} "
           f"{'%>0':>5} {'%=0':>5} {'last30d ann%':>12} {'spread bp':>9} {'24h $vol':>11} {'OI $':>10}")
    lines.append(hdr)
    rows = []
    for m in markets:
        t = m["ticker"]
        fr = sorted(get("/funding_rates/historical", {"ticker": t})["funding_rates"], key=lambda x: x["funding_time"])
        rates = [float(x["funding_rate"]) for x in fr]
        if not rates:
            continue
        bid, ask = float(m["bid"]), float(m["ask"])
        spread_bp = (ask - bid) / ((ask + bid) / 2) * 1e4 if bid > 0 else float("nan")
        last30 = rates[-90:]
        row = dict(
            ticker=t, start=fr[0]["funding_time"][:10], n=len(rates),
            mean_bp=statistics.mean(rates) * 1e4,
            ann=statistics.mean(rates) * PERIODS_PER_YEAR * 100,
            abs_ann=statistics.mean(abs(r) for r in rates) * PERIODS_PER_YEAR * 100,
            pos=sum(r > 0 for r in rates) / len(rates) * 100,
            zero=sum(r == 0 for r in rates) / len(rates) * 100,
            last30=statistics.mean(last30) * PERIODS_PER_YEAR * 100,
            spread_bp=spread_bp,
            vol=float(m.get("volume_24h_notional_value_dollars") or 0),
            oi=float(m.get("open_interest_notional_value_dollars") or 0),
        )
        rows.append(row)
        time.sleep(0.15)
    rows.sort(key=lambda r: -r["vol"])
    for r in rows:
        lines.append(f"{r['ticker']:14} {r['start']:10} {r['n']:>4} {r['mean_bp']:>10.3f} {r['ann']:>7.2f} "
                     f"{r['abs_ann']:>8.2f} {r['pos']:>5.0f} {r['zero']:>5.0f} {r['last30']:>12.2f} "
                     f"{r['spread_bp']:>9.1f} {r['vol']:>11,.0f} {r['oi']:>10,.0f}")
    lines += ["", "ann % = mean funding per 8h x 1095, signed (positive = longs pay shorts).",
              "|ann| % = what a side-switching oracle would collect; an upper bound, not a strategy."]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
