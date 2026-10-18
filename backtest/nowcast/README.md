# `backtest/nowcast/` — C10, weather post-peak stale quotes (Session 37)

**Offline research only. Nothing here may be imported by the live trading path**
(`tests/test_canary_isolation.py` guards it). Stdlib + `requests`, public
unauthenticated Kalshi REST and the IEM ASOS archive. Data lands in
`backtest/cache/nowcast/` (gitignored).

The question:

> Once a station's observed temperature has already settled a ladder rung,
> is the decided side still on offer below its value, after Kalshi's fee?

The rule and the kill criteria were frozen in **DECISIONS.md Session 37**
before any outcome was computed. The result is recorded there too.

## Run order

```bash
P=karbotrage_env/bin/python
$P -m backtest.nowcast.fetch markets    # settled KXHIGH* markets, live + historical tier
$P -m backtest.nowcast.fetch asos       # IEM METARs, one request per station
$P -m backtest.nowcast.fetch candles    # hourly bid/ask, open -> close (~16k requests, ~40 min)
$P -m backtest.nowcast.analyze          # H7, H8, CLI-vs-ASOS; writes h7_entries.json
$P -m backtest.nowcast.fetch trades     # trades for H7 entry markets (capacity proxy)
$P -m backtest.nowcast.analyze capacity
```
Output goes to `backtest/reports/nowcast_h7_h8.txt`. Failed fetches are listed
in `backtest/cache/nowcast/failed.txt` and the analysis counts excluded markets.

## Definitions

* **Running max.** Max METAR temperature from local-standard midnight to
  `candle end − 10 min`. Routine and special METARs only (`report_type=3,4`).
* **Decided rung.** `greater f`: YES once max ≥ f+1. `between f..c`: NO once
  max ≥ c+1. `less c`: NO once max ≥ c. Nothing else can be decided by a
  running max, because the settled high can only be at or above it.
* **Entry.** First hourly candle (closing quotes) with a decided side and a
  two-sided book, `0 < yes_bid < yes_ask < 1`. Buy YES at `yes_ask`, NO at
  `1 − yes_bid`. Return `(payout − p − fee)/(p + fee)`, `fee = 0.07·p·(1−p)`.
* **Independent unit = event date.** Bootstrap whole dates, as everywhere in
  `backtest/`.

## Traps found the hard way

* **IEM's `tmpf` column is rounded to whole °F.** It cannot tell 85.6 °F from
  86.4 °F, which is exactly the distinction a threshold rung turns on. Parse the
  METAR remark `T` group (tenths of °C) instead. Without a `T` group, use the
  body temperature minus 0.5 °C: the rule must only ever under-state the max.
* **The climate day is local STANDARD time**, all year. In summer it runs
  01:00 → 01:00 daylight time. Each market's `close_time` is 23:59 LST, which
  confirms it. Using the daylight-time midnight would pull in the previous
  climate day's late-evening readings.
* **Hourly METARs miss the true peak.** The CLI high comes from the
  continuous record and is usually a degree or so above the hourly max (KAUS
  2026-08-01: METAR max 95 °F, CLI 96 °F). That is the safe direction for this
  rule. The dangerous direction — a METAR above the CLI — is measured per
  city-day in the report, not assumed away.
* **The historical cutoff moves.** At writing (2026-10-18) markets settled
  before 2026-08-19 sit in `/historical/markets` and their candles at
  `/historical/markets/{t}/candlesticks` (`close`, not `close_dollars`). The
  historical tier also reaches back to March 2026 now, further than Session 31
  saw through `/markets?status=settled`.
* **Trades straddle tiers.** `fetch trades` queries both `/historical/trades`
  and `/markets/trades` and dedupes by `trade_id`.
* **Pace ≤ 8 req/s.** The candle route 429s above that from one IP.
* **Stations are never guessed.** 32 newer KXHIGH* series (Phoenix, Seattle,
  international cities, …) have no resolved station and are excluded by name.
