# `backtest/consistency/` — C9, spread ladder vs game winner (Session 37)

**Offline research only. Nothing here may be imported by the live trading path**
(`tests/test_canary_isolation.py` guards it). Public unauthenticated Kalshi REST,
live tier only. Data in `backtest/cache/consistency/` (gitignored).

Result: **dead.** Buying the game-winner ask when a normal fitted to the spread
ladder says it is >3¢ cheap after fees lost **−15.1%** on 2,550 uncovered soccer
events, and lost in every period. The game market's mid is the *better* price
(Brier 0.2041 vs the ladder's 0.2438). Record: DECISIONS.md Session 37.

## Run order

```bash
P=karbotrage_env/bin/python
$P -m backtest.consistency.fetch events    # pairs <P>GAME/<P>SPREAD, settled 2026-08-19..10-17, seeded sample
$P -m backtest.consistency.fetch candles   # ~28k candle series, ~2 h at <= 5 req/s
$P -m backtest.consistency.analyze         # -> backtest/reports/consistency_h9.txt
```

## Traps

* **There is no game-start field.** `occurrence_datetime` looks like the
  expected *end*, and `close_time` is when the market actually closed (often
  early). A "pre-game only" window cannot be guaranteed from public fields.
* **Spread rungs name the team in the ticker suffix**: `…-DET39` is "Detroit
  wins by over 38.5". Strip the trailing digits and match the game market's team
  code. 82 rungs did not match and were dropped.
* **Soccer game markets are 3-way** (a `-TIE` leg). A win is margin ≥ 1, so read
  the fitted survival function at x = 0.5, not 0.
* **The ladder's rungs live in the longshot zone.** Fitting a distribution to
  2–20¢ mids imports the favourite–longshot bias into the reference. It was the
  reference that was miscalibrated, not the market.
* **Cups and knockouts are excluded** because extra time can split the game and
  spread settlement definitions.
* Pacing is shared with `backtest.nowcast.fetch` (live ≤ 5 req/s, historical
  ≤ 2 req/s).
