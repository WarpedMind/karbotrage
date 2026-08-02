"""Phase 1 deliverable: the calibration report.

The question is NOT "is NOAA accurate?" (it is). The question is:

    is NOAA, converted to a probability, better calibrated than the Kalshi
    price itself, out of sample, after costs?

A model that beats a coin flip but loses to the market has no edge. The market
price is the baseline, and it is a strong one.

WHAT IS SCORED
--------------
For every settled Kalshi daily-high market, at every NBM cycle whose forecast
was published before the evaluation timestamp:

    model_p   P(YES) from the NBM forecast for that station and local day
    market_p  the YES mid at the same timestamp   (calibration baseline)
    market_ask the executable YES price           (used only for net edge)
    outcome   Kalshi's own ``result``

Three models, in increasing order of how much they could fool you:

    gaussian    Normal(TXNMN, TXNSD), integer-rounding corrected. Zero fitted
                parameters, so it is out-of-sample by construction.
    quantile    the published TXNP1/2/5/7/9 suite interpolated. Also zero
                fitted parameters.
    calibrated  gaussian with three globally fitted numbers -- a bias, a spread
                multiplier and a spread floor. Fitted on the FIRST half of the
                dates and scored only on the SECOND half. This is the one that
                can overfit, so it is the one whose train/test split is
                enforced by date, never by market.

Every confidence statement comes from a bootstrap that resamples whole DATES,
because the ~7,500 markets are not 7,500 independent observations -- see
scoring.py.

Run:  karbotrage_env/bin/python -m backtest.run_calibration
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import requests

from . import costs
from . import kalshi_history as kh
from . import nbm_text as nbm
from . import probability as prob
from . import scoring
from . import stations as st

#: NBM cycles used. NBS publishes hourly, but 00Z and 12Z between them cover
#: leads of 12, 24, 36, 48, 60 and 72 hours to a 00Z-valid daytime max, which
#: spans the entire ~42-hour window in which these markets actually trade.
#: MEASURED, 2026-08-02: the 18Z cycle carries NO daytime-max value at FHR 6
#: (TXN is null for the 00Z-valid column), so 12 hours is the SHORTEST lead at
#: which an NBM daytime-max forecast exists at all. That matters for reading
#: the calibration result: the model is not losing to the market because it was
#: handed stale data, it is losing at the freshest forecast NOAA publishes.
CYCLE_HOURS = [0, 12, 18]

#: Publication lag applied to a cycle before its forecast is allowed to be
#: "known". MEASURED, not assumed -- see ``measure_publication_lag``.
DEFAULT_LAG_HOURS = 2.0


def measure_publication_lag(
    sample_days: Sequence[dt.date], product: str = "nbs"
) -> Optional[float]:
    """Observed hours between a cycle's nominal time and its S3 upload.

    Letting a strategy see a forecast before it was published is the classic way
    to manufacture backtest edge. This measures the real lag from the bucket's
    Last-Modified headers and takes the maximum, so the evaluation timestamp is
    conservative rather than optimistic.
    """
    lags = []
    for day in sample_days:
        for hour in CYCLE_HOURS:
            published = nbm.object_last_modified(day, hour, product)
            if published is None:
                continue
            cycle = dt.datetime(
                day.year, day.month, day.day, hour, tzinfo=dt.timezone.utc
            )
            lags.append((published - cycle).total_seconds() / 3600.0)
    if not lags:
        return None
    return max(lags)


def build_observations(
    *,
    lag_hours: float,
    min_volume: float,
    verbose: bool = True,
) -> Tuple[List[dict], dict]:
    """Assemble every (market, cycle) evaluation point with model and market probs."""
    map_path = os.path.join(os.path.dirname(__file__), "resolved_stations.json")
    with open(map_path, "r", encoding="utf-8") as fh:
        series_to_station: Dict[str, str] = json.load(fh)

    sess = requests.Session()
    markets_by_series: Dict[str, List[kh.WeatherMarket]] = {}
    for series in sorted(series_to_station):
        markets_by_series[series] = kh.to_weather_markets(
            kh.settled_markets(series, session=sess)
        )

    all_days = sorted(
        {m.local_day for mkts in markets_by_series.values() for m in mkts}
    )
    stations = sorted(set(series_to_station.values()))

    # Cycles needed: for local day D valid at 00Z D+1, a cycle at hour h on day
    # X contributes if 00Z(D+1) is in its horizon.
    cycle_days = sorted({d - dt.timedelta(days=k) for d in all_days for k in (0, 1, 2)})
    forecasts: Dict[Tuple[dt.date, int], Dict[str, nbm.StationForecast]] = {}
    total = len(cycle_days) * len(CYCLE_HOURS)
    done = 0
    for day in cycle_days:
        for hour in CYCLE_HOURS:
            done += 1
            try:
                forecasts[(day, hour)] = nbm.fetch_stations(
                    day, hour, stations, "nbs", session=sess
                )
            except (nbm.NbmFetchError, requests.RequestException) as exc:
                forecasts[(day, hour)] = {}
                if verbose:
                    print(f"  [nbm miss] {day} {hour:02d}Z: {exc}")
            if verbose and done % 25 == 0:
                print(f"  [nbm] {done}/{total} cycles")

    rows: List[dict] = []
    stats = {
        "markets": 0,
        "no_forecast": 0,
        "no_price": 0,
        "low_volume": 0,
        "evaluated": 0,
    }

    # Prefetch candlesticks concurrently. One market is one HTTP round-trip and
    # there are ~7,500 of them; serially that is over half an hour of waiting on
    # the network for a job that is otherwise pure arithmetic.
    wanted = [
        (series, m)
        for series, mkts in markets_by_series.items()
        for m in mkts
        if m.expiration_value is not None and m.volume >= min_volume
    ]
    candle_cache: Dict[str, List[dict]] = {}
    from concurrent.futures import ThreadPoolExecutor

    def _pull(item):
        series, m = item
        # Each worker gets its own Session; requests.Session is not documented
        # as thread-safe and sharing one here has produced connection-pool
        # corruption in the wild.
        local = requests.Session()
        try:
            return m.ticker, kh.candlesticks(
                series, m.ticker, m.open_ts, m.close_ts, 60, session=local
            )
        except Exception:
            return m.ticker, []

    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, (ticker, candles) in enumerate(pool.map(_pull, wanted), 1):
            candle_cache[ticker] = candles
            if verbose and i % 500 == 0:
                print(f"  [candles] {i}/{len(wanted)}")

    for series, mkts in markets_by_series.items():
        station = series_to_station[series]
        for m in mkts:
            stats["markets"] += 1
            if m.expiration_value is None:
                continue
            if m.volume < min_volume:
                stats["low_volume"] += 1
                continue
            valid = nbm.daytime_max_valid_time(m.local_day)
            candles = candle_cache.get(m.ticker, [])
            for (cday, chour), parsed in forecasts.items():
                fc = parsed.get(station)
                if fc is None:
                    continue
                lead = fc.lead_hours_to(valid)
                if lead is None:
                    continue
                idx = fc.valid.index(valid)
                if not fc.rows.get("TXN") or fc.rows["TXN"][idx] is None:
                    continue

                known_at = fc.cycle + dt.timedelta(hours=lag_hours)
                ts = int(known_at.timestamp())
                if ts < m.open_ts or ts > m.close_ts:
                    continue

                mid = kh.yes_price_at(candles, ts, "mid")
                ask = kh.yes_price_at(candles, ts, "ask")
                bid = kh.yes_price_at(candles, ts, "bid")
                if mid is None:
                    stats["no_price"] += 1
                    continue

                rows.append(
                    {
                        "ticker": m.ticker,
                        "series": series,
                        "station": station,
                        "local_day": m.local_day.isoformat(),
                        "strike_type": m.strike_type,
                        "floor_strike": m.floor_strike,
                        "cap_strike": m.cap_strike,
                        "outcome": m.outcome,
                        "observed_high": m.expiration_value,
                        "lead_hours": lead,
                        "cycle": fc.cycle.isoformat(),
                        "mu": float(fc.rows["TXN"][idx]),
                        "sd": (
                            float(fc.rows["XND"][idx])
                            if fc.rows.get("XND") and fc.rows["XND"][idx] is not None
                            else None
                        ),
                        "market_mid": mid,
                        "market_ask": ask,
                        "market_bid": bid,
                        "volume": m.volume,
                    }
                )
                stats["evaluated"] += 1

    return rows, stats


# ── models ────────────────────────────────────────────────────────────────────


def model_gaussian(row: dict, bias: float = 0.0, sd_scale: float = 1.0,
                   sd_floor: float = prob.SD_FLOOR) -> Optional[float]:
    sd = row["sd"] if row["sd"] is not None else 1.0
    sigma = max(sd * sd_scale, sd_floor)
    return prob.gaussian_probability(
        row["strike_type"], row["floor_strike"], row["cap_strike"],
        row["mu"] + bias, sigma,
    )


def fit_calibration(
    rows: Sequence[dict],
) -> Tuple[float, float, float]:
    """Fit (bias, sd_scale, sd_floor) by grid search on mean Brier score.

    Three parameters, fitted globally rather than per-station, on a coarse grid.
    Deliberately crude: with ~600 independent city-days in the training half,
    a richer model (per-station bias, per-lead spread) would fit the sample
    rather than the physics, and the whole point of this exercise is to avoid
    producing a number that only exists in-sample.
    """
    best = None
    for bias_i in range(-20, 21, 2):
        bias = bias_i / 10.0
        for scale_i in range(6, 41, 2):
            scale = scale_i / 10.0
            for floor_i in range(5, 31, 5):
                floor = floor_i / 10.0
                pairs = []
                for r in rows:
                    p = model_gaussian(r, bias, scale, floor)
                    if p is not None:
                        pairs.append((prob.clamp(p), r["outcome"]))
                score = scoring.brier(pairs)
                if score is not None and (best is None or score < best[0]):
                    best = (score, bias, scale, floor)
    if best is None:
        return 0.0, 1.0, prob.SD_FLOOR
    return best[1], best[2], best[3]


def model_quantile(row: dict, nbp_dist: Optional[prob.Distribution]) -> Optional[float]:
    if nbp_dist is None:
        return None
    return prob.quantile_probability(
        row["strike_type"], row["floor_strike"], row["cap_strike"], nbp_dist
    )


# ── report ────────────────────────────────────────────────────────────────────


def _fmt(value, spec="{:.4f}"):
    return "  --  " if value is None else spec.format(value)


def print_summary(title: str, obs: Sequence[scoring.Observation]) -> dict:
    s = scoring.summarise(obs)
    print(f"\n  {title}")
    print(f"    n markets      : {s['n']}")
    print(f"    n dates(blocks): {s['n_blocks']}")
    print(f"    base rate      : {_fmt(s['base_rate'])}")
    print(f"    Brier model    : {_fmt(s['brier_model'])}")
    print(f"    Brier MARKET   : {_fmt(s['brier_market'])}   <- the baseline")
    print(f"    Brier climo    : {_fmt(s['brier_climatology'])}")
    print(f"    skill vs market: {_fmt(s['skill_vs_market'], '{:+.4f}')}")
    print(f"    logloss model  : {_fmt(s['logloss_model'])}")
    print(f"    logloss market : {_fmt(s['logloss_market'])}")
    return s


def print_reliability(obs: Sequence[scoring.Observation], label: str) -> None:
    print(f"\n  Reliability - {label}")
    print(f"    {'bin':>12}  {'n':>6}  {'mean p':>7}  {'observed':>8}  {'gap':>7}")
    table = scoring.reliability_table([(o.model_p, o.outcome) for o in obs])
    for b in table:
        if not b["n"]:
            continue
        print(
            f"    {b['lo']:.1f}-{b['hi']:.1f}  {b['n']:6d}  {b['mean_p']:7.3f}  "
            f"{b['freq']:8.3f}  {b['gap']:+7.3f}"
        )


def print_per_series(obs: Sequence[scoring.Observation]) -> None:
    """Per-city breakdown, with the multiplicity caveat stated in the output.

    Eighteen cities means eighteen chances for one to look good on noise. At
    alpha=0.05 the expected number of spurious "winners" is close to one, so a
    single strong city is not evidence of anything. SIGNAL_REGISTER.md's gate
    requires a Bonferroni-style correction and replication across periods
    before any candidate signal is allowed to influence a position; this table
    is descriptive only and does not clear that bar.
    """
    by_series: Dict[str, List[scoring.Observation]] = {}
    for o in obs:
        by_series.setdefault(o.series, []).append(o)
    print("\n  Per-series (DESCRIPTIVE ONLY - 18 comparisons, uncorrected)")
    print(f"    {'series':14s} {'n':>5} {'Brier model':>12} {'Brier mkt':>10} "
          f"{'delta':>9}")
    for series in sorted(by_series):
        sub = by_series[series]
        bm = scoring.brier((o.model_p, o.outcome) for o in sub)
        bk = scoring.brier((o.market_p, o.outcome) for o in sub)
        print(f"    {series:14s} {len(sub):5d} {bm:12.4f} {bk:10.4f} "
              f"{bk - bm:+9.4f}")
    print("    A single city beating the market here is expected by chance;")
    print("    Bonferroni at 18 comparisons demands p < 0.0028, not p < 0.05.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-volume", type=float, default=0.0,
                        help="drop markets with less than this total volume")
    parser.add_argument("--lag-hours", type=float, default=None,
                        help="override the measured NBM publication lag")
    parser.add_argument("--rows-out", default=None,
                        help="write the assembled evaluation rows to this JSON path")
    parser.add_argument("--rows-in", default=None,
                        help="reuse previously assembled rows and skip all fetching")
    parser.add_argument("--contested-only", action="store_true",
                        help="restrict to markets the market itself prices as "
                             "genuinely uncertain (mid in [0.05, 0.95])")
    args = parser.parse_args(argv)

    print("=" * 78)
    print("KARBOT RAGE - S6 CALIBRATION REPORT (NOAA/NBM vs KALSHI)")
    print("=" * 78)

    mismatch = costs.assert_matches_live_fee_model()
    print(f"\n  fee model cross-check vs live KalshiFeeModel: "
          f"{'MISMATCH -> ' + mismatch if mismatch else 'agrees'}")

    if args.rows_in:
        with open(args.rows_in, "r", encoding="utf-8") as fh:
            rows = json.load(fh)
        stats = {"markets": len(rows), "low_volume": 0, "no_price": 0,
                 "evaluated": len(rows)}
        print(f"\n  reusing {len(rows)} rows from {args.rows_in}")
        return _analyse(rows, stats, args)

    if args.lag_hours is not None:
        lag = args.lag_hours
        print(f"  NBM publication lag: {lag:.2f} h (overridden)")
    else:
        probe = [dt.date(2026, 6, 5), dt.date(2026, 7, 5), dt.date(2026, 7, 25)]
        measured = measure_publication_lag(probe)
        lag = measured if measured is not None else DEFAULT_LAG_HOURS
        print(f"  NBM publication lag: {lag:.2f} h "
              f"({'measured from S3 Last-Modified' if measured else 'default'})")

    print("\n  Assembling evaluation rows ...")
    rows, stats = build_observations(lag_hours=lag, min_volume=args.min_volume)
    if args.rows_out:
        with open(args.rows_out, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
        print(f"  rows written to {args.rows_out}")
    return _analyse(rows, stats, args)


def _analyse(rows, stats, args) -> int:
    print(f"\n  markets seen      : {stats['markets']}")
    print(f"  dropped low volume: {stats['low_volume']}")
    print(f"  dropped no price  : {stats['no_price']}")
    print(f"  evaluation rows   : {stats['evaluated']}")

    if not rows:
        print("\n  NO DATA - stopping.")
        return 1

    if args.contested_only:
        before = len(rows)
        rows = [r for r in rows if 0.05 <= r["market_mid"] <= 0.95]
        print(f"\n  CONTESTED-ONLY cut: {before} -> {len(rows)} rows "
              f"(market mid in [0.05, 0.95])")
        print("  Rationale: most ladder rungs are far out of the money and both")
        print("  the model and the market price them near 0. Those markets")
        print("  dominate a pooled Brier score while carrying no decision")
        print("  content and no tradeable size.")

    dates = sorted({r["local_day"] for r in rows})
    split = dates[len(dates) // 2]
    train = [r for r in rows if r["local_day"] < split]
    test = [r for r in rows if r["local_day"] >= split]
    print(f"\n  date range        : {dates[0]} .. {dates[-1]}  ({len(dates)} dates)")
    print(f"  train/test split  : < {split}  ->  train={len(train)} test={len(test)}")
    print("  SEASON            : late spring / summer only. Nothing here")
    print("                      generalises to winter; that is unproven.")

    leads = sorted({r["lead_hours"] for r in rows})
    print(f"  leads present (h) : {leads}")

    bias, sd_scale, sd_floor = fit_calibration(train)
    print(f"\n  fitted on TRAIN   : bias={bias:+.2f} F  sd_scale={sd_scale:.2f}  "
          f"sd_floor={sd_floor:.2f} F")

    results = {}
    for lead in leads:
        subset = [r for r in test if r["lead_hours"] == lead]
        if len(subset) < 50:
            continue
        print("\n" + "-" * 78)
        print(f"  LEAD {lead} HOURS   (test split only, n={len(subset)})")
        print("-" * 78)

        for name, fn in (
            ("gaussian (0 fitted params)", lambda r: model_gaussian(r)),
            ("calibrated (3 params, fit on train)",
             lambda r: model_gaussian(r, bias, sd_scale, sd_floor)),
        ):
            obs = []
            for r in subset:
                p = fn(r)
                if p is None:
                    continue
                obs.append(
                    scoring.Observation(
                        key=r["ticker"],
                        block=r["local_day"],
                        series=r["series"],
                        outcome=r["outcome"],
                        model_p=prob.clamp(p),
                        market_p=r["market_mid"],
                        market_ask=r["market_ask"],
                        market_bid=r["market_bid"],
                        lead_hours=r["lead_hours"],
                    )
                )
            if not obs:
                continue
            summary = print_summary(name, obs)
            boot = scoring.block_bootstrap_delta(obs, scoring.brier_delta)
            ci = boot.get("ci")
            print(f"    Brier delta (market - model), block-bootstrapped by DATE:")
            print(f"      point   : {_fmt(boot['point'], '{:+.5f}')}")
            if ci:
                print(f"      95% CI  : [{ci[0]:+.5f}, {ci[1]:+.5f}]  "
                      f"over {boot['n_blocks']} dates")
                print(f"      P(model no better than market) = {boot['p_le_zero']:.3f}")
            results[(lead, name)] = (summary, boot)
            if "calibrated" in name:
                print_reliability(obs, f"MODEL {name}, lead {lead}h")
                market_obs = [
                    scoring.Observation(
                        key=o.key, block=o.block, series=o.series,
                        outcome=o.outcome, model_p=o.market_p, market_p=o.market_p,
                    )
                    for o in obs
                ]
                print_reliability(market_obs, f"MARKET price, lead {lead}h")
                print_per_series(obs)

    print("\n" + "=" * 78)
    print("  NET-OF-COST EDGE (gate G3)")
    print("=" * 78)
    report_net_edge(test, bias, sd_scale, sd_floor)

    return 0


def report_net_edge(
    rows: Sequence[dict], bias: float, sd_scale: float, sd_floor: float
) -> None:
    """Apply the executable price and the ceil'd taker fee to the model's calls.

    Scores only the trades the model would actually take, at the ask, with the
    fee charged -- not the whole sample at the mid.
    """
    print(f"\n    {'lead':>5}  {'thresh':>6}  {'trades':>7}  {'gross EV/c':>11}  "
          f"{'fee/c':>7}  {'net EV/c':>9}  {'net/capital':>11}  {'realised/c':>11}")
    for lead in sorted({r["lead_hours"] for r in rows}):
        subset = [r for r in rows if r["lead_hours"] == lead]
        for threshold in (0.02, 0.05, 0.10):
            n = 0
            gross = 0.0
            fee = 0.0
            capital = 0.0
            realised = 0.0
            for r in subset:
                p = model_gaussian(r, bias, sd_scale, sd_floor)
                if p is None:
                    continue
                yes_ask = r["market_ask"]
                no_ask = None if r["market_bid"] is None else 1.0 - r["market_bid"]
                # Buy YES if the model is far enough above the YES ask;
                # buy NO if the model is far enough below (1 - NO ask).
                if yes_ask is not None and p - yes_ask > threshold:
                    econ = costs.evaluate_yes_trade(p, yes_ask, 1)
                    win = r["outcome"] == 1
                elif no_ask is not None and (1.0 - p) - no_ask > threshold:
                    econ = costs.evaluate_no_trade(p, no_ask, 1)
                    win = r["outcome"] == 0
                else:
                    continue
                if econ is None:
                    continue
                n += 1
                gross += econ.gross_ev
                fee += econ.fee
                capital += econ.entry_price
                realised += (1.0 if win else 0.0) - econ.entry_price - econ.fee
            if n == 0:
                continue
            print(
                f"    {lead:5d}  {threshold:6.2f}  {n:7d}  {gross / n:+11.4f}  "
                f"{fee / n:7.4f}  {(gross - fee) / n:+9.4f}  "
                f"{(gross - fee) / capital:+11.4f}  {realised / n:+11.4f}"
            )
    print("\n    'realised/c' is the ACTUAL settled P&L per contract on those")
    print("    trades -- the model's own EV is a claim, this is the outcome.")


if __name__ == "__main__":
    sys.exit(main())
