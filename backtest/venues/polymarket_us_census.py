"""Census of the Polymarket US (QCX LLC, CFTC DCM) market universe.

Offline, read-only, public endpoints, no auth. Never imported by the live path.

Answers the venue-screen questions that a fee schedule cannot:
  * how many markets exist, by category, open vs closed;
  * how much of that is non-sports (where Session 36 says competition is thinner);
  * whether settled markets carry quote history (`/v1/price-history`, whose
    `longPrice` is the best ask and `shortPrice` is 1 - best bid) far enough back
    for a quote-based kill test.

Run:
    karbotrage_env/bin/python -m backtest.venues.polymarket_us_census
Writes backtest/reports/venue_polymarket_us_census.json (summary only).
"""

from __future__ import annotations

import collections
import json
import random
import time
from pathlib import Path

import requests

BASE = "https://gateway.polymarket.us"
REPORT = Path(__file__).resolve().parents[1] / "reports" / "venue_polymarket_us_census.json"
PAGE = 200
PAUSE = 0.08  # stay well under the documented 20 req/s public limit


def get(path: str, params: dict) -> dict:
    for attempt in range(5):
        r = requests.get(BASE + path, params=params, timeout=30)
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"rate limited: {path}")


CATEGORIES = ["sports", "politics", "culture", "crypto", "macro", "climate",
              "technology", "finance", "science", "geopolitics"]


def all_markets(category: str) -> list[dict]:
    # The `closed` query filter was found NOT to partition the universe (closed=true
    # returned only sports), so page each category unfiltered and read the
    # market's own `closed` field instead.
    out, offset = [], 0
    while True:
        page = get("/v1/markets", {"limit": PAGE, "offset": offset, "categories": category})
        rows = page.get("markets", [])
        out.extend(rows)
        if len(rows) < PAGE:
            return out
        offset += PAGE
        time.sleep(PAUSE)


def history_points(slug: str, start: int, end: int) -> list[dict]:
    page = get(
        "/v1/price-history",
        {"symbol": slug, "timestamp.startTimestamp": start, "timestamp.endTimestamp": end, "fidelity": 1},
    )
    return page.get("history", [])


def iso_to_ts(s: str) -> int:
    return int(time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone)


def main() -> None:
    summary: dict = {"fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    rows = []
    for cat in CATEGORIES:
        rows.extend(all_markets(cat))
    seen = {}
    for m in rows:
        seen[m["id"]] = m
    rows = list(seen.values())
    summary["total_markets"] = len(rows)
    summary["by_category_closed_vs_open"] = {
        cat: {
            "closed": sum(1 for m in rows if m.get("category") == cat and m.get("closed")),
            "open": sum(1 for m in rows if m.get("category") == cat and not m.get("closed")),
        }
        for cat in CATEGORIES
    }

    # End-date range of closed non-sports markets: how much settled history exists.
    closed_ns = [m for m in rows if m.get("closed") and (m.get("category") or "") != "sports" and m.get("endDate")]
    ends = sorted(m["endDate"][:10] for m in closed_ns)
    summary["closed_non_sports_end_date_range"] = [ends[0], ends[-1]] if ends else None
    summary["closed_non_sports_by_month"] = dict(sorted(collections.Counter(e[:7] for e in ends).items()))

    # Quote-history coverage: seeded sample of closed non-sports markets, 24h window
    # ending 24h before endDate. Count markets with any points and with a two-sided
    # book (ask + (1-bid) > ... i.e. longPrice < 1 and shortPrice < 1 and not 0.5/0.5 seed).
    rng = random.Random(3838)
    sample = rng.sample(closed_ns, min(60, len(closed_ns)))
    cov = {"sampled": len(sample), "with_points": 0, "with_two_sided_point": 0, "median_points": None}
    spreads, counts = [], []
    for m in sample:
        end = iso_to_ts(m["endDate"])
        try:
            pts = history_points(m["slug"], end - 48 * 3600, end - 24 * 3600)
        except Exception as exc:  # noqa: BLE001 - census, count and move on
            cov.setdefault("errors", []).append(f"{m['slug']}: {exc}"[:160])
            continue
        counts.append(len(pts))
        if pts:
            cov["with_points"] += 1
        two = [p for p in pts if 0 < p.get("longPrice", 1) < 1 and 0 < p.get("shortPrice", 1) < 1]
        if two:
            cov["with_two_sided_point"] += 1
            last = two[-1]
            # longPrice = ask(YES); shortPrice = 1 - bid(YES)  ->  spread = long + short - 1
            spreads.append(round(last["longPrice"] + last["shortPrice"] - 1, 4))
        time.sleep(PAUSE)
    counts.sort()
    spreads.sort()
    cov["median_points"] = counts[len(counts) // 2] if counts else None
    cov["spread_p25_med_p75"] = (
        [spreads[len(spreads) // 4], spreads[len(spreads) // 2], spreads[3 * len(spreads) // 4]] if spreads else None
    )
    summary["quote_history_sample"] = cov

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
