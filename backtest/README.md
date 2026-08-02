# `backtest/` — offline calibration harness

**Nothing in this package may be imported by the live trading path.** It exists
to answer one question before any strategy code is written:

> Is an external model, converted to a probability, better calibrated than the
> Kalshi price itself?

## Headline result (Session 31, 2026-08-02)

**No — decisively, for NOAA/NBM temperature forecasts against Kalshi's daily-high
markets. Gate G2 FAILS.** The full reasoning is in DECISIONS.md's Session 31
entry; the raw output is in `reports/`.

| | lead 12h | lead 24h | lead 30h |
|---|---|---|---|
| Brier, NBM model | 0.2013 | 0.1795 | 0.1713 |
| Brier, **market price** | **0.1757** | **0.1612** | **0.1567** |
| Brier skill vs market | −0.146 | −0.114 | −0.093 |
| P(model no better than market) | 1.000 | 1.000 | 1.000 |

(contested markets only — market mid in [0.05, 0.95]; test split; 36 dates;
95% CIs from a bootstrap resampling whole **dates**, not markets)

And the measured reason, which matters more than the verdict:

| | NBM | market-implied |
|---|---|---|
| point-forecast MAE @12h | 1.59 °F | **1.27 °F** |
| point-forecast MAE @24h | 1.77 °F | **1.47 °F** |

NBM's published *uncertainty* is close to correct (published SD 2.32 vs realised
RMSE 2.16, ratio 0.93). The deficit is in the **forecast itself**, not in the
probability conversion — so a better error model cannot recover it.

## Running it

Order matters: each step is a gate on the next.

```bash
karbotrage_env/bin/python -m backtest.resolve_and_verify
```
Proves the ground truth. Replays the settlement rule against all 7,565 settled
markets (must reproduce 100%), checks each city-day ladder is an exhaustive
partition, and resolves each Kalshi series to its NWS station empirically.

```bash
karbotrage_env/bin/python -m backtest.verify_alignment
```
Proves the NBM valid-time → Kalshi local-day mapping against real settlements
rather than assuming it.

```bash
karbotrage_env/bin/python -m backtest.run_calibration --rows-out backtest/cache/rows.json
karbotrage_env/bin/python -m backtest.run_calibration --rows-in backtest/cache/rows.json --contested-only
karbotrage_env/bin/python -m backtest.diagnose_gap
```
The report itself. The first invocation downloads ~5 GB of NBM bulletins and
~7,500 candlestick series (roughly 20 minutes); everything is cached under
`backtest/cache/` (gitignored) and subsequent runs with `--rows-in` are instant.

## Data sources, all public and unauthenticated

| leg | source | notes |
|---|---|---|
| forecast | `noaa-nbm-grib2-pds.s3.amazonaws.com` `.../text/blend_nbstx.tCCz` | plain ASCII station bulletins — **no GRIB2 decoder needed** |
| forecast (quantiles) | `.../text/blend_nbptx.tCCz` | carries `TXNMN`, `TXNSD` **and** `TXNP1/2/5/7/9` |
| outcome | Kalshi `GET /markets?status=settled` | `expiration_value` **is** the observed daily high |
| market price | Kalshi `GET /series/{s}/markets/{t}/candlesticks` | hourly, carries `yes_bid` **and** `yes_ask` |
| station cross-check | `mesonet.agron.iastate.edu/json/cli.py` | parsed NWS CLI product |

## Traps found the hard way — read before extending

* **Kalshi `less` markets carry the threshold in `cap_strike`, not
  `floor_strike`.** Reading `floor_strike` yields `None` and silently drops the
  entire low tail of every ladder (1,255 of 7,560 markets) while every printed
  diagnostic still looks clean. `verify_strike_logic` now counts skips by reason.
* **Use IEM's `json/cli.py`, not `cgi-bin/request/daily.py`.** The ASOS daily
  feed disagrees with the NWS CLI product Kalshi settles on — KLAX 2026-08-01
  reads 79 °F there and 80 °F in the CLI product, which is the value Kalshi used.
* **"Greater than 85" means "86 or above"** (Kalshi's own `yes_sub_title`). The
  continuous threshold is 85.5. At the 2 °F spreads these markets have, skipping
  that continuity correction moves every probability by ~10 points.
* **Station names in the rules are ambiguous and the obvious guess is wrong.**
  "Houston" resolves to **KHOU (Hobby)**, not KIAH. Resolved empirically in
  `stations.py`; never guess.
* **NBM publishes no daytime-max at 6 h lead.** The 18Z cycle's 00Z-valid `TXN`
  column is null, so **12 h is the shortest lead that exists**.
* **These markets only trade for ~42 hours.** There is no market price at all
  beyond ~36 h of lead, so long-range forecast skill is untestable here.
* **The independent unit is the DATE, not the market.** Six ladder rungs describe
  one temperature and all 18 cities share a synoptic pattern. Every confidence
  statement here bootstraps whole dates; treating 7,500 markets as 7,500
  observations would shrink the intervals ~9× and turn noise into an edge.

## Seasonal limitation

Kalshi's weather history begins 2026-05-22. The sample is **late spring and
summer only**. Nothing here generalises to winter, and that is unproven rather
than merely untested.
