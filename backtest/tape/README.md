# `backtest/tape/` — Kalshi trade-tape and quote-snapshot return tests (Session 36)

**Offline research only. Nothing here may be imported by the live trading path.**
Stdlib + `requests`, public unauthenticated Kalshi REST, no keys. Data lands in
`backtest/cache/tape/` (gitignored). Every script `chdir`s there on start.

It answers one question per candidate, cheaply, before any strategy code:

> After Kalshi's real fee, would buying at the price actually on offer have made money,
> on markets whose outcome was not yet known?

Authoritative result and reasoning: **DECISIONS.md, Session 36**. Short form:
the favorite–longshot bias is real in the *trade tape* and replicates out of
sample (H4 +6.83%), but **a mechanical rule buying the quoted ask loses**
(H5 −15.15% out of sample, −26.44% in-sample), and still loses with a 3¢ spread
cap (H6 −4.09%). The tape edge belongs to takers who chose their moment; on a
tight book the ask is a fair price minus costs. See the table in DECISIONS.

## Run order

```bash
P=karbotrage_env/bin/python
$P backtest/tape/universe.py        # live snapshot: series catalog + all open markets (volume by category)
$P backtest/tape/settled.py         # every settled non-MVE market, vol>=1000, 2026-07-30..2026-09-27, one file per day
$P backtest/tape/fetch_trades.py    # stratified sample (<=300 events/category, <=12/series, <=6 markets/event) -> pre-cutoff trades
$P backtest/tape/analyze.py trades recs.json
$P backtest/tape/stats.py           # pre-registered H1-H3 + replication + exploratory splits
$P backtest/tape/fetch_hist.py      # same design on the historical tier (settled 2026-05-01..2026-07-29)
$P backtest/tape/analyze.py hist_trades hist_recs.json
$P backtest/tape/oos_h4.py          # frozen H4, out of sample
$P backtest/tape/fetch_candles.py   # hourly bid/ask candles for every sampled market in the 8 H4 categories
$P backtest/tape/h5.py              # mechanical rule on quotes (pre-registered)
$P backtest/tape/h6.py              # H5 + spread cap (pre-registered after H5 failed)
$P backtest/tape/calibration.py hist  # sanity check: candle-mid calibration by decile
$P backtest/tape/timing.py trades "Climate and Weather"   # when in a market's life the tape return accrues
$P backtest/tape/xvenue_snapshot.py # live Kalshi vs Polymarket NFL moneylines
```
Fetching is ~2–3 hours in total at ~6 concurrent requests; everything caches.

## Definitions that the numbers depend on

* **Cutoff (the ≥2h lead gate).** `min(settlement_ts, expected_expiration_time, close_time) − 2h`.
  Trades and candles after it are discarded. There is **no public field for when an
  outcome became known**, so this is the closest defensible proxy. Consequence: many
  sports markets (set winners, quarter totals) trade only in play and drop out entirely.
* **Taker return** for a fill at price `p` on the side the taker bought:
  `(payout − p − fee) / (p + fee)`, `fee = 0.07·p·(1−p)` (M=0 on the ten zero-fee series).
  **Maker return** is the other side at `1−p`, with `0.0175·q·(1−q)` only on the maker-fee
  series listed in `documentation/kalshi-fee-schedule.pdf`. Continuous fee, no per-order
  cent round-up: slightly optimistic, which only strengthens a negative result.
* **Independent unit = settlement date.** All intervals bootstrap whole dates (event-level
  clustering is also reported for H1–H3 and agrees). Treating markets as independent would
  turn noise into an edge — the `backtest/README.md` lesson.
* **Equal-weight vs dollar-weight.** Dollar-weighted = ratio of sums (a few huge markets
  dominate). Equal-weight = mean of per-market returns = "one fixed-size position per market",
  which is the only implementable form of a per-market rule.

## Traps found the hard way

* **The tape is not an opportunity set.** A trade at 78¢ happened because *someone chose to
  take it then*. Tape returns measure that population's timing, informed traders included.
  H4 (+6.8% out of sample on the tape) and H5 (−15% for a rule buying the quoted ask in the
  same band on the same markets) are both correct. Any tape-derived "edge" must be re-tested
  on quotes before it means anything.
* **Conditioning on the ASK selects wide books.** "Ask in 70–85¢" is disproportionately true
  when the spread is wide, especially the seeded quotes at a market's open, which are the
  worst-calibrated prices in the data (first candle 80–90¢ mid wins 77.5%).
* **Candle schema differs by tier.** Historical `/historical/markets/{t}/candlesticks` uses
  `{"close": "0.14"}`; live `/series/{s}/markets/{t}/candlesticks` uses
  `{"close_dollars": "0.14"}`. The live route 404s for historical-tier markets.
* **Historical tier.** Settled markets before `GET /historical/cutoff` (2026-07-30 at the time
  of writing) move to `/historical/markets` and `/historical/trades`. `min_close_ts`/`max_close_ts`
  are **ignored** there; filter by `series_ticker` and page newest-first. `mve_filter` works.
* **One Fed meeting is not a category.** Economics' in-sample favorite "edge" was three
  `KXFEDDECISION` markets from a single meeting. Check which series carry a slice before
  reading it.
* **Weather's tape return sits 6–12h before cutoff** (≈ 3–9pm ET), i.e. after the day's high
  is usually already observed. That is a stale-quote / nowcast effect, not a pricing bias —
  a different mechanism from the favorite–longshot bias and recorded as its own candidate.
