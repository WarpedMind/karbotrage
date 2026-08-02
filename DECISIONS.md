# Decision Log
# Entries are ordered newest-to-oldest. Most recent decision is at the top.

## 2026-08-02 — Session 32: the S5a/S5b canary is built as a SEPARATE PROCESS, and its arbitrage relations are gated on settled history rather than on strike arithmetic or Kalshi's own event flag

### Status of each claim, labeled up front
- **CONFIRMED LIVE this session** (measured against `api.elections.kalshi.com`,
  unauthenticated): the depth-field mapping, the snapshot-staleness window, the
  `strike_type` census, the non-binary settlement rate, the qualification
  verdicts for 60 real series, and the basket arithmetic on real open events.
- **MEASURED, one snapshot only**: zero candidates. One sweep is not a verdict —
  that is the entire reason this thing runs for weeks.
- **ARGUED, not measured**: that a separate process is safer than an in-runner
  agent. The Session 23 outage it reasons from is confirmed; the claim that the
  canary would reproduce it is not, and is not tested.
- **OPEN, deliberately not guessed at**: whether Kalshi refunds voided positions
  at cost. Decisive for whether the 4.1% non-binary settlement rate on ATP is a
  fee-sized drag or a principal-sized one.

### Decision 1 — separate process, not an agent in `karbot_runner.py`
The project's convention is that everything is an agent on the event bus. This
deliberately is not, and the operator asked for the reasoning rather than the
recommendation, so it is recorded here in full.

The decisive argument is not safety, it is **reuse**. `backtest/kalshi_history.py`
and `backtest/costs.py` already do settled-market fetching with cursor
pagination and backoff, and the ceil'd Kalshi fee with a live cross-check
against the trading path's own `KalshiFeeModel`. All of it is blocking
`requests`. Inside `karbot_runner.py` none of it is callable: a blocking call in
that event loop **is** the Session 23 outage verbatim — it stalled the loop past
PriceWatcher's 10s WebSocket ping deadline, Kalshi tore down the transport, and
the agent crashed three times in eight minutes until it exhausted its restart
budget. Going in-runner therefore means rewriting the fetch layer in `aiohttp`
(two code paths that will drift — and the drifting one decides whether a trade
looks profitable), or wrapping every call in a thread executor. In a separate
process the reuse is an import.

The secondary argument, fault isolation, is real but was **sized honestly rather
than overstated**: a sweep is ~45 seconds wall-clock but only a few hundred
milliseconds of CPU spread across awaits, which a 10s ping timeout would very
likely survive even done badly. It is the same shape of risk as Session 23, not
the same magnitude.

**The strongest argument against, given equal weight**: a separate unit is more
likely to die silently. This project is specifically bad at that —
`karbot-disk-alert.sh`, the watchdog built to prevent a silent outage, was
itself silently non-functional from Session 26 to Session 29. An in-runner agent
would inherit `_run_supervised`, boot-start and a Telegram path for free.
Mitigated with `Restart=always` and a per-sweep heartbeat line in the JSONL, and
**recorded as a known gap rather than claimed as parity**.

### Decision 2 — a relation is licensed by settled history, never by strike arithmetic alone
This is the load-bearing design decision and it came from a live counterexample,
not from caution.

`KXMLBSPREAD-26AUG021340CWSTB` holds eight markets in one event, all
`strike_type=greater`, covering **two different metrics** — Tampa Bay's winning
margin and Chicago's — at overlapping strikes (`TB4` and `CWS4` both at
`floor_strike=3.5`). Interval arithmetic will cheerfully prove that Tampa Bay
winning by 4+ implies Chicago winning by 3+, and then price a riskless
arbitrage on it. This is Session 29's trap with a sharper edge: Session 29 found
78 apparent sum-to-one baskets and confirmed **0 of 78** were real, all ladders
misidentified by grouping on `event_ticker` without checking comparability.

Two text-based tests for metric identity were tried and **both rejected on
evidence**:
- Stripping numbers from `rules_primary` separates the two teams correctly but
  also splits a legitimate weather ladder, because `less`/`between`/`greater`
  markets phrase their rules differently ("is less than 78" vs "is between
  78-79").
- An `expiration_value` identity test is simply **wrong**: measured across 123
  settled `KXMLBSPREAD` events, all markets in an event share one
  `expiration_value` despite being on different metrics.

So: **structure proposes, history disposes.** Interval arithmetic generates
candidate relations; a relation is usable only if the series' settled record has
never once violated it. On KXMLBSPREAD that is **2,267 measured violations** and
the series is disqualified. Verdicts are `confirmed` / `refuted` /
`insufficient_evidence`, and **zero tests is never `confirmed`** — a vacuous
pass is exactly the shape of Session 31's bug, a validation reporting success
over a sample it had quietly emptied.

Disqualification is at series granularity, which is coarse: one mixed-metric
event disqualifies a series even for its well-behaved pairs. Deliberate. A false
positive here manufactures a confident stream of fake arbitrage; a false
negative costs coverage in a process whose only output is a log file.

Kalshi's own `mutually_exclusive` flag is **recorded alongside** the empirical
verdict rather than used as the gate, so a disagreement between the two is
visible rather than silently trusted.

### Decision 3 — `MIN_SETTLED_EVENTS` is a logging filter, not a risk control
Stated explicitly so it cannot be quietly promoted into one later. A relation
that held in *n* independent settled events with zero violations still carries a
rule-of-three upper 95% bound of ~`3/n` on its failure probability. At the
default of 30 that is **~10% — nowhere near "riskless"**. The bound is computed
and written into every profile and every logged candidate precisely so nobody
can read "qualified" as "proven". Nothing in this package trades, so the
threshold's only job is to keep the log from filling with noise.

### Decision 4 — the YES-basket requires a partition, not merely exhaustiveness
Strictly, "at least one leg must resolve YES" is all the YES-basket's dollar
needs. The implementation requires exactly-one, and the extra condition is
deliberate.

"At least one YES in n settled events" means different things for different
shapes. On a temperature partition it is structural — the buckets tile the
outcome space. On a **nested** ladder ("over 1 run", "over 2 runs", …) it is a
coincidence of the sample: the bottom rung is almost always YES, so 40 clean
events look identical to a structural guarantee right up until a 0-0 game
settles every leg NO. Measured live: `KXMLBTOTAL` comes back
`exhaustive: confirmed` for exactly this reason. Requiring a partition costs the
nested-ladder YES-basket — never economically plausible anyway, since eleven
nested YES legs cost $5.61 for the dollar they pay — and removes a concrete,
foreseeable failure.

### What was confirmed live, and the three things that were nearly missed
**The depth-field mapping.** There is no `no_ask_size_fp`. A NO ask is a resting
YES bid at `1 − price`, so the quantity available for buying NO at `no_ask` is
**`yes_bid_size_fp`** — the field with the opposite name. Verified in both
directions against `/markets/{t}/orderbook` on a real market: derived
`yes_ask` = 1 − no_bid = 0.13 with depth = no_bid_qty = 5, derived
`no_ask` = 1 − yes_bid = 0.92 with depth = yes_bid_qty = 32, both matching the
`/markets` snapshot exactly. Reading the same-named field for both sides is the
Session 26 bug class and would have sized every NO leg off the wrong side of the
book.

**The snapshot goes stale in seconds.** Read back-to-back against the order
book, the bulk list agreed **16/16** on price and size. Held ~10 seconds while
other requests ran, an actively traded market moved underneath it
(`KXMLBSPREAD-…-CWS6`: yes_bid 0.10 → 0.14, size 3 → 2071). A full sweep takes
~45 seconds, so the earliest pages describe a book that no longer exists. Hence
a mandatory second stage: **every candidate is re-priced leg by leg from
`/orderbook` before it is logged as real**, and candidates that evaporate are
kept as `vanished_on_recheck` because that survival rate is itself the
measurement — it separates "real resting arbitrage" from "our view of the book is
noisy", which is the exact question Session 29 could not answer.

**The `strike_type` census**, across 12,000 live open markets: `greater` 6438,
`structured` 3282, `between` 1408, `custom` 569, `less` 105, none 100,
`greater_or_equal` 98. Two findings in that. Session 31's `less`/`cap_strike`
convention is reconfirmed at 105/105. And **`structured` is not one thing** —
with a `floor_strike` it is a threshold ("2+ RBIs"), without one it is
categorical ("Los Angeles D wins"). Intervals derived from the former are marked
`inferred` and **barred from disjointness claims**: a wrong inference cannot
create a false implication between two upper rays, but it could create a false
disjointness against a bounded interval.

Three things were nearly missed and are worth recording as process, not trivia:

1. **The first live sweep evaluated nothing at all.** 8,608 events seen, zero
   evaluated. The profile budget of 60 series was spent entirely on the 60 the
   events endpoint happens to return first — `KXNEXTNATOSECGEN`, `KXNEWPOPE`,
   `KXXISUCCESSOR` and the like — every one a long-horizon "who will be next"
   market with **zero settled events**, so none could qualify. Fixed by ranking
   unqualified series by their open markets' 24h volume, which is principled
   rather than arbitrary: a series with no volume cannot be traded even if a
   mispricing appeared in it. Nothing errored; it would have looked like a
   working scanner finding nothing.

2. **The reconciliation check caught a real bug on its first live run.** 8,608
   events accounted for as 8,631. Cause: per-event evaluation *notes* (a basket
   leg with a one-sided book) were being counted in the same bucket as event
   *dispositions*, and an evaluated event can raise several notes or none. Split
   into `event_skips` (must reconcile) and `evaluation_notes` (informational).
   This is Session 31's "reconcile totals, do not report a rate" lesson paying
   for itself immediately.

3. **A settlement outcome that is neither YES nor NO.** Kalshi finalizes a
   postponed game or an unplayed match as `result: "scalar"`,
   `status: "finalized"`, `market_type: "binary"`, empty `expiration_value`, on
   every leg of the event. Measured: **6 of 884 KXMLBGAME events (0.7%) and 37
   of 910 KXATPMATCH events (4.1%)**. The first implementation filed these under
   "unsettled" and dropped them — so the profile reported `exhaustive:
   confirmed` while the basket's guaranteed dollar quietly failed on 4% of real
   ATP events. **That is the Session 31 failure mode reproduced in new code**: an
   inconvenient case dropped under a benign label, leaving a clean-looking rate.
   Now split, measured, stored on the profile and attached to every candidate.
   **Then resolved the same session — see below.**

### The void-settlement question, asked properly and answered — the framing was wrong before the answer was
Flagged above as open and decisive, and put to the operator as a binary: does
Kalshi refund a voided position **at cost** (loss = the fees) or **not**
(loss = the principal)? Both were wrong, and the framing is the lesson.

Kalshi's own `rules_secondary`, which ships on every market and is therefore the
cheapest primary source available, says a cancelled match *"will resolve to a
**fair price** in accordance with the rules"*. Not a refund. Not a zero. A
settled scalar value between $0 and $1 — which is exactly why `result` reads
`"scalar"`. So the question that actually decides the basket is neither of the
two asked: **do a cancelled event's fair prices sum to $1?** If they do, a
YES-basket pays `Σ settlement = $1` and a NO-basket pays
`Σ(1 − settlement) = $(N−1)` — precisely the binary guarantee, and the
cancellation is a non-event.

They do. Every leg carries `settlement_value_dollars`, a field not previously
noticed. Reconciled across 8 series:

| | scalar events seen | checked | sum to $1.00 | unverifiable |
|---|---|---|---|---|
| ATP / WTA / ITF / ATP-Challenger | 149 | 149 | **149** | 0 |
| CS2 / LoL | 82 | 82 | **82** | 0 |
| MLB game / MLB F5 | 12 | 12 | **12** | 0 |
| **total** | **243** | **243** | **243** | **0** |

236 two-leg events and 7 three-leg, zero violations, totals reconciling.

**Implemented rather than merely recorded**: `qualify.scalar_sum_to_one` checks
the invariant per series, and `allows_yes_basket`/`allows_no_basket` both
require `scalar_sum_to_one_violations == 0`. A cancellation whose values cannot
be read counts **against**, never for — "could not check" must never be stored
as "checked and fine". Both basket payouts are functions of `Σ settlement`, so
they are gated together rather than separately.

**The generalisable lesson is about the question, not the answer.** A decision
was framed as a binary between two plausible outcomes, and reality was a third
thing that made the whole concern evaporate. It took one API call to a field
already being fetched. *Before escalating a question to the operator as
"open and decisive", check whether the data already on hand answers it* — this
one had been sitting in `rules_secondary` and `settlement_value_dollars` the
entire time.

### The rate-limit coupling: predicted, then measured, after one false alarm
"Separate process" overstated the isolation — `canary` and `karbot.service`
reach Kalshi from one IP and share its rate limit. Raised before deploying and
documented, then measured after.

A first measurement said the canary made **no** difference. That was wrong, and
the reason is worth recording: it counted lines matching `grep -i "429"`, which
matches sequence numbers containing those digits (`expected=27854299`). The
real counter is `book_reset_rest_failed`:

| window | `book_reset_rest_failed` | `book_snapshot_applied_rest` |
|---|---|---|
| 21:00–21:59 (pre-canary) | 0 | 30,354 |
| 22:00–22:23 (pre-canary) | 0 | 8,398 |
| 22:24 → (canary running) | 4 | 2,390 |

So the effect is **real** — zero failures across 38,752 REST snapshots in the 84
minutes before, four in the 13 minutes after, about **0.17%**. That is an order
of magnitude below Session 23's confirmed 5.5%, well below Session 30's 0.7%,
and absorbed by the existing failure path. Not a problem; not nothing either.
Two by-products: the standing debt item "only worth prioritizing if 429s become
a recurring pattern" now has a **zero-failure pre-canary baseline** it never
had, and this is one more instance of the standing rule — *never treat a grep,
a log name or a metric name as evidence of what it measures*.

### First live result: zero candidates, and every near miss is one spread wide
**12 consecutive sweeps, 13,094 event-evaluations, zero candidates, zero errors,
and every sweep reconciling.** Coverage climbed 725 → 1,284 evaluated events per
sweep as the profile cache filled (720 series qualified across the run), so the
run is also a working demonstration that the deferred-qualification design
converges rather than stalling.

8,598 open events, 76,483 markets, 3,086 distinct series. Of the first 60
qualified, 26 qualified for something: winner-take-all events (MLB, ATP/WTA/ITF
tennis, CS2, LoL, Dota, Valorant, soccer) come back `exclusive + exhaustive
confirmed` — **these are exactly the genuine mutually-exclusive events Session 29
noted were absent from its sample**, so that gap is now closed. Nested totals
ladders (KXMLBTOTAL, KXWTI oil) come back `implication confirmed` on tens of
thousands of pair tests. Weather (KXHIGHLAX) comes back exclusive + exhaustive +
disjoint, independently matching Session 31's 1,261/1,261. Player-prop series
(KXMLBHIT, KXMLBHRR) are `implication refuted` — the multi-metric trap in a
family nobody had flagged.

| event | legs | basket cost | guaranteed payout |
|---|---|---|---|
| KXATPMATCH | 2 | $1.01 | $1.00 |
| KXCS2GAME | 2 | $1.02 | $1.00 |
| KXMLBGAME | 2 | $1.07 | $1.00 |
| KXHIGHLAX (weather ladder) | 6 | $1.09 | $1.00 |

That is a functioning market, and it reproduces Session 29's ladder check
(closest 1.01) exactly. Worth stating what would be needed for the tightest of
them: ATP at $1.01 is one cent away, but the taker fee on two near-the-money
legs is ~3.5¢ per contract-set, so it would have to reach **$0.965** to be real.

A useful negative control fell out of this. Running the arithmetic with the
qualification gate **deliberately bypassed**, `KXMLBTOTAL` prices as a "+$4.36
riskless NO-basket". It is not: buying NO on eleven nested "over N runs" legs
pays `11 − (number of YES)`, and on a nested ladder several are YES at once. The
gate refutes the series and the scanner never logs it. Session 29's trap,
reproduced on live data, caught.

### Deployed, and what deploying surfaced
Installed on the VPS as `karbot-canary.service` (enabled, active, `Restart=always`,
`Nice=10`), after confirming the box was **10 commits behind main** and that
**none of those commits touched the live path** — verified with
`git diff --name-only` over `agents/ karbot/ core/ execution/ data/
karbot_runner.py`, which returned nothing. So the pull could not alter trading
behaviour; that is checked, not assumed.

Two things only deploying could have found:

1. **`requests` was an undeclared dependency.** `backtest/` has documented
   "stdlib + `requests`" since Session 31 and `canary/` uses it too, but
   `requirements.txt` never listed it. It was present in the local dev venv by
   coincidence and surfaced as `ModuleNotFoundError` the first time anything
   imported those packages on the VPS. Now declared.
2. **The dev machine and production do not agree on floating-point arithmetic.**
   A test asserting `basket_fee(...) == 0.10` passed locally and failed on the
   VPS. Local Python is 3.14, the VPS is 3.10, and CPython 3.12 gave `sum()`
   compensated (Neumaier) summation for floats — so ten one-cent fees add to
   exactly `0.1` on one and `0.09999999999999999` on the other. **"301/301
   passing locally" was therefore never evidence about production**, which is
   the confirmed-vs-argued distinction in miniature. Fixed in two places: the
   test asserts `approx`, and `is_candidate` compares against an `EPSILON` of
   1e-9 rather than `0.0`, so a basket priced at exactly break-even cannot be
   logged as an opportunity because an accumulated float landed 1e-16 above
   zero. The epsilon guards representation error, **not** thin edges — a cent is
   the smallest real quantity here, nine orders of magnitude above the guard.

### Cost, and what this does not claim
One session. No order layer, no capital, no paper trades, no live exposure, and
no change to the trading path — enforced by `tests/test_canary_isolation.py`,
which fails if anything under `agents/`, `karbot/`, `core/` or `karbot_runner.py`
imports `canary` or `backtest`.

**This does not show that S5a/S5b arbitrage exists.** 13,094 event-evaluations
over ~25 minutes found nothing, which is a stronger version of what Session 29
already found and is not a verdict — real arbitrage, if it exists, is sporadic,
and twenty-five minutes on a Sunday afternoon is not weeks. The claim is
narrower and worth stating precisely: the instrument that could detect it now
exists, has been verified against real books rather than fixtures, converges its
own coverage, and refuses the specific false positives that killed S1 and that
Session 29 caught by hand.

---

## 2026-08-02 — Session 31: S6 weather divergence FAILS gate G2 — NOAA/NBM is measurably WORSE calibrated than the Kalshi price, and the reason is the forecast itself, not the probability conversion

### Status of each claim, labeled up front
- **MEASURED, out of sample, this session**: every Brier score, skill score,
  confidence interval and forecast-error number below. Reproducible end to end
  from public unauthenticated APIs via `backtest/`; raw output committed under
  `backtest/reports/`.
- **CONFIRMED LIVE**: all three data legs, the settlement rule, the station
  identities, and the NBM valid-time mapping — each proved against real
  settled Kalshi markets rather than assumed. See "What was proved before any
  modelling ran" below.
- **ARGUED, not measured**: the read-across to other `FairValueProvider`
  candidates in the "what this does and does not kill" section. It is
  reasoning about why weather failed, not evidence about anything else.

### The decision
**S6 external-model divergence, using NOAA/NBM on Kalshi's daily-high
temperature markets, is dead. It does not proceed to Phase 2 (detect-and-log).
No `DivergenceScannerAgent` is built, and `FairValueEngineAgent` is not built
for this provider.** Session 30 specced exactly this outcome as an acceptable
one — "it costs one session to learn, which is the entire argument for going
this way first" — and that is what happened. **No code was written into the
live trading path this session.**

### Gate results
- **G1 — Data: PASS.** 18 Kalshi daily-high series, 1,261 city-days across 71
  dates (2026-05-22 → 2026-08-01), 7,565 settled markets, 22,587
  (market × forecast-cycle) evaluation rows at leads of 12, 24 and 30 hours.
- **G2 — Calibration: FAIL, decisively and at every lead.**
- **G3 — Net of costs: moot, and confirms the failure** — trading the model's
  divergences realises a *loss*.

### G2, the actual numbers
Test split only (the later 36 of 71 dates); "contested" means the market itself
priced the outcome as genuinely uncertain (mid in [0.05, 0.95]), which is where
any trade would happen. 95% intervals come from a bootstrap resampling whole
**dates**.

| contested, test split | lead 12h | lead 24h | lead 30h |
|---|---|---|---|
| n markets | 2,079 | 2,510 | 2,642 |
| base rate | 0.301 | 0.254 | 0.238 |
| Brier — NBM model | 0.2013 | 0.1795 | 0.1713 |
| Brier — **market price (baseline)** | **0.1757** | **0.1612** | **0.1567** |
| Brier — climatology | 0.2104 | 0.1896 | 0.1812 |
| Brier skill vs market | **−0.146** | **−0.114** | **−0.093** |
| 95% CI on (market − model) Brier | [−0.032, −0.019] | [−0.024, −0.012] | [−0.019, −0.010] |
| P(model no better than market) | **1.000** | **1.000** | **1.000** |

Three things in that table matter more than the headline:
1. **Every confidence interval lies entirely on the wrong side of zero**, over
   36 independent dates. This is not a marginal or noisy result.
2. **The model barely beats climatology** (0.2013 vs 0.2104 at 12h) while the
   market beats it comfortably. A model that beats a coin flip but loses to the
   market has no edge — that framing was written into Session 30's spec before
   any data existed, and it is exactly what came back.
3. **The model does WORSE the shorter the lead** (−0.146 at 12h vs −0.093 at
   30h). The market's informational advantage grows as the event approaches,
   which is the signature of the market incorporating information a single
   model run does not have.

Per-city: **17 of 18 cities lose to the market.** The one nominal winner
(KXHIGHTATL, +0.0013 Brier) is far inside what 18 uncorrected comparisons
produce by chance — Bonferroni at 18 demands p < 0.0028, and this is not close.

### G3 — what trading it would actually have done
Buying whenever the model's probability exceeds the executable ask by a
threshold, one contract, real ceil'd Kalshi taker fees:

| lead | threshold | trades | model's claimed net EV/contract | **realised P&L/contract** |
|---|---|---|---|---|
| 12h | 2¢ | 2,406 | +$0.1055 | **−$0.0195** |
| 12h | 10¢ | 1,191 | +$0.1715 | **−$0.0297** |
| 24h | 10¢ | 1,000 | +$0.1651 | **−$0.0378** |
| 30h | 10¢ | 948 | +$0.1578 | **−$0.0101** |

**The model claims a 10-17 cent edge per contract and loses 1-4 cents.** And it
gets *worse* as the divergence filter gets stricter: demanding a bigger
disagreement with the market selects harder for cases where the model is wrong,
not for cases where the market is. That is the cleanest possible statement of
what a divergence signal looks like when the model is the less-informed party,
and it is worth remembering as the shape to watch for in any future
`FairValueProvider`.

### WHY — the part that decides whether this is fixable
A negative calibration result is only useful if it says which component failed,
because the two candidates imply opposite follow-ups:

- **(a) the market's point forecast is better** — no cheap fix exists; a better
  error model around an inferior mean cannot recover it; or
- **(b) the point forecasts are comparable but NBM's published uncertainty is
  wrong** — the failure is in the probability conversion, and the fix is a
  properly estimated error distribution or the float-valued GRIB2 fields.

Measured, by recovering the market's implied expected temperature from each
city-day ladder (an exhaustive partition, so normalising its YES prices gives a
proper distribution over temperature) and scoring both against the settled high:

| | NBM | market-implied |
|---|---|---|
| point-forecast MAE @ 12h | 1.59 °F | **1.27 °F** |
| point-forecast MAE @ 24h | 1.77 °F | **1.47 °F** |
| bias @ 12h | +0.21 °F | +0.11 °F |

| | published SD | realised RMSE | ratio |
|---|---|---|---|
| lead 12h | 2.32 °F | 2.16 °F | **0.93** |
| lead 24h | 2.55 °F | 2.38 °F | **0.93** |

**It is case (a).** The market's implied temperature forecast is ~20% more
accurate than NBM's, at both leads. NBM's published spread, meanwhile, is close
to correct — if anything very slightly *wide* (ratio 0.93), not narrow. So the
probability conversion is not the bottleneck and improving it cannot close the
gap. **The Kalshi weather market already prices a better temperature forecast
than a raw NBM run.**

**RETRACTION, recorded rather than quietly dropped**: earlier in this same
session, on seeing a 12h RMSE of 2.27 °F next to a KLAX `TXNSD` of 1, I said
this was "a first sign the model will be overconfident" from integer truncation
of the published spread. **That was wrong.** It generalised from one station's
value to the population; the population mean published SD is 2.32 °F and the
ratio to realised error is 0.93. Same failure mode as Session 30's fee error —
a confident conclusion from an incomplete look — caught here by measuring it.

### The objection that would kill this result, tested and closed
The obvious challenge: *the model was handed stale data.* At a 12-hour lead the
comparison uses the 12Z NBM cycle, while the market at that moment can see
anything more recent.

**Closed by measurement, not by argument: NBM does not publish a daytime-max
forecast at a shorter lead than 12 hours.** The 18Z cycle's 00Z-valid `TXN`
column is null — the max-temperature product for a 12Z-00Z window is simply not
issued from a cycle three-quarters of the way through it. Twelve hours is the
freshest daytime-max forecast NOAA publishes. The model is losing at NOAA's own
best, not to a handicap this harness imposed.

A second structural limit worth recording: **these markets only trade for ~42
hours** (KXHIGHLAX-26AUG01 opened 2026-07-31T14:00Z, closed 2026-08-02T07:59Z).
There is no market price beyond ~36 hours of lead at all, so any strategy whose
thesis is "NOAA sees further ahead than the market" has no venue to express it
here.

### What was proved before any modelling ran
Every one of these was treated as a gate, because a well-calibrated model of the
wrong event is the failure mode that would have survived every test:

1. **The settlement rule.** `settles_yes()` replayed against all 7,565 settled
   markets using each market's real `expiration_value` and real `result`:
   **7,565/7,565 exact, across all three strike types.**
2. **The ladders are an exhaustive partition.** 1,261 of 1,261 city-days have
   exactly one YES. (This also makes them simultaneous S5a/S5b candidates, as
   Session 30 noted.)
3. **Station identity, resolved empirically rather than guessed.** Each series'
   `expiration_value` series matched day-for-day against candidate stations'
   NWS CLI highs: **18/18 resolved at 100% exact match**, with clear separation
   from runners-up. **Houston is KHOU (Hobby), not KIAH** — the obvious guess is
   wrong. Also KMDW not KORD, KNYC not KLGA/KJFK, KDCA not KIAD, KDFW not KDAL.
4. **The NBM valid-time → local-day mapping.** Scored both readings against real
   settlements: "local day = valid date − 1" gives MAE 1.85 °F, the naive
   same-date reading gives 3.46 °F. Not subtle, and now not assumed.
5. **The fee model** cross-checks against the live `KalshiFeeModel` at every
   price/size probe, so this report cannot assume cheaper fees than the trading
   path uses.

### A bug found by counting, not by testing — worth generalising
Gate 1 initially reported a clean **7,565/7,565 match on 6,305 markets checked**
out of 7,566 seen. The counts do not reconcile, and the missing 1,255 were
almost exactly one per city-day.

Cause: **Kalshi's `less` markets carry their threshold in `cap_strike` and leave
`floor_strike` null — the opposite convention from `greater`.** Reading
`floor_strike` returned `None`, and `None` was being skipped silently. That
removed the entire low tail of every ladder — a fifth of the sample, and
systematically the same region of every distribution — while every printed
diagnostic still read as a perfect 100%.

Nothing failed. No test caught it. It was found because a total did not add up.
The fix is not just the field: `verify_strike_logic` now **counts every skip by
reason and refuses to pass with any unhandled case**, so silence can no longer
look like success. Recording this as a standing pattern: *a validation that
reports a rate rather than a reconciliation can hide an arbitrarily large
omission behind a perfect-looking number.*

### An architectural correction to Session 30's spec, in NOAA's favour
Session 30 specced the forecast leg as byte-range GRIB2 fetches from the
`core/` suite, with the `qmd/` quantiles as a later upgrade if a Gaussian proved
inadequate — which implies a GRIB2 decoder (eccodes/cfgrib, a binary
dependency) and point interpolation from a CONUS grid.

**None of that is necessary.** The same bucket's `text/` suite publishes plain
ASCII **station** bulletins, and the NBP product carries, per station, per valid
time: `TXNMN` (mean), `TXNSD` (spread) **and** `TXNP1/P2/P5/P7/P9` (the 10th
through 90th percentiles). Mean, spread and quantiles, for exactly the airport
stations Kalshi settles on, with no decoder and no interpolation. `backtest/`
therefore has **no new dependencies at all** — stdlib plus `requests`, which the
project already has.

This is worth carrying forward independently of the weather result: it is the
cheapest known route to NOAA point-forecast data, and it stays true for any
future provider built on NBM.

The one real cost: text-bulletin values are **integer degrees**, including the
spread. That was the reason to suspect overconfidence — and it was measured and
found not to bind (ratio 0.93 above). Should NBM ever be revisited, the GRIB2
float route remains the upgrade path, but it would be fixing something that this
session has shown is not broken.

### A data-source trap that would have silently corrupted the ground truth
Iowa State's IEM exposes two daily-temperature feeds and **they disagree**:

    /cgi-bin/request/daily.py   (ASOS-derived)     KLAX 2026-08-01 -> 79 °F
    /json/cli.py                (parsed NWS CLI)   KLAX 2026-08-01 -> 80 °F

Kalshi's `expiration_value` for that day is **80.00**. Kalshi settles on the CLI
product, exactly as its rules say, and Kalshi's own `rules_secondary` warns
about precisely this ("rounding and conversion nuances"). The more obvious
endpoint is the wrong one. Use `json/cli.py`.

In the end this harness did not need an observations feed at all —
`expiration_value` **is** the settled observation, published on every market —
but the discrepancy is recorded because anyone building a live weather provider
will reach for `daily.py` first.

### What this kills, and what it does not
**Killed:** NOAA/NBM temperature forecasts as a fair-value source for Kalshi
daily-high markets. Not "unproven" — measured, out of sample, at every lead
NOAA publishes, with the reason identified.

**Not killed, and deliberately not re-litigated here:**
- The `FairValueProvider` abstraction and the divergence *shape* of strategy.
  What failed is one provider on one market family, for a specific and
  legible reason: the market was better informed than the model. That is a
  fact about NWS-settled weather markets — where the settlement source is
  public, free, and read by every participant — not a general law.
- **The generalisable lesson, which is the useful output**: a divergence
  strategy needs a source whose informational advantage over the market is
  plausible *before* it is measured. A free public forecast that every
  participant can read is the weakest possible candidate, and weather was
  chosen in Session 30 precisely because forecast and settlement share a source
  — which, read again, is the same property that guarantees the market sees it
  too. **Session 30's own "honest counter" said exactly this** ("Kalshi weather
  markets are known to attract participants already using NOAA") and it turned
  out to be the decisive consideration, not a footnote.
- Market-making (S8), which Session 30 deferred behind a live order layer and
  whose measured 489-market zero-maker-fee surface is untouched by this result.
- S5a/S5b as a passive canary, which never depended on S6.

### Cost of learning this
One session. No order-management layer, no capital, no paper trades, no live
exposure. That was the entire argument for sequencing divergence ahead of
market-making, and it held.

---

## 2026-08-01 — Session 30 (spec-only): the pivot direction is external-model divergence (S6) first; market-making (S8) is deferred behind a live order layer; S5a/S5b arb continues as a cheap passive canary

### Status of each claim in this entry, labeled up front
- **MEASURED LIVE this session** (2026-08-02 ~03:00 UTC, public Kalshi REST,
  no auth, 40,000 open markets pulled with `mve_filter=exclude`): the volume
  distribution, spread distribution, and top-of-book depth numbers below.
  Re-runnable — the scan script is trivial and is specced for the repo.
- **SECONDARY-SOURCED, needs primary confirmation**: the Kalshi maker-fee
  formula (three independent third-party sources agree; Kalshi's own fee
  schedule PDF returned HTTP 429 on three attempts this session).
- **ARGUED, not measured**: everything about whether NOAA-vs-Kalshi
  divergence has real edge. No number in this entry claims it does. The
  whole point of the plan below is to measure it before building on it.

### What was measured, and why it drove the decision
| Measurement | Value |
|---|---|
| Total open markets (mve excluded) | 40,000 |
| Total 24h volume across universe | 74,654,881 contracts |
| Sports share of 24h volume | **75.4%** (KXPGATOUR 31.3%, MLB series ~36% combined) |
| Weather share of 24h volume | **3.2%** (2,404,232 contracts, 672 markets) |
| Fed / CPI / econ share of 24h volume | **0.1%** (46,953 contracts, 453 markets) |
| Markets with vol≥100 AND a two-sided book | 3,918–3,938 (two runs, minutes apart) |
| Bid-ask spread on those: p25 / median / p75 / p90 | 1¢ / **2¢** / 4¢ / 8¢ |
| Share with spread ≥2¢ / ≥3¢ / ≥5¢ | 48.9% / 36.7% / 20.4% |
| Top-of-book `min(bid_size, ask_size)`: p25/med/p75/p90 | 5 / **42** / 395 / 1,395 contracts |
| Markets with spread ≥2¢ **and** ≥100 contracts both sides | **486** |
| Markets with spread ≥3¢ **and** ≥50 contracts both sides | **384** |

Three things fall directly out of this and shaped the decision:

1. **Fed/econ markets are effectively dead on Kalshi** — 46,953 contracts of
   74.6M. The obvious-sounding "compare Kalshi to CME FedWatch" idea has
   almost no addressable volume behind it. It is not the place to start,
   regardless of how clean the data source is.
2. **Sports is where the volume is (75.4%)** but the external reference for
   sports is a sharp sportsbook's closing line — the hardest benchmark to
   beat in the entire space, and the data is not free. Correct target
   eventually, wrong target for a first, unproven build.
3. **Weather is small but real** — 2.4M contracts/day across 672 markets,
   and uniquely tractable: the forecast source (NWS/NOAA) and the
   *settlement* source for these markets are the same organization, and the
   API is free with no key. That combination does not exist anywhere else
   in the universe scanned.

### Kalshi weather market structure — CONFIRMED LIVE this session, not assumed
Pulled directly from the markets endpoint, so the implementation session
does not have to re-derive it:
- **672 open weather markets**, structured as **city-day temperature
  ladders of ~12 markets each**, across ~12 cities. Volume leaders:
  `KXHIGHLAX` 616,690 contracts/24h, `KXHIGHNY` 160,642, `KXLOWTMIA`
  147,915, `KXRAIN` 122,921 (40 markets), then AUS/MIA/CHI/PHX/ATL/SFO/
  BOS/DAL at 65k–121k each.
- **Settlement source is NWS itself.** Verbatim from
  `KXHIGHLAX-26AUG01-T85`'s `rules_primary`: *"If the highest temperature
  recorded in Los Angeles Airport, CA for August 01, 2026 as reported by
  the National Weather Service's Climatological Report (Daily), is greater
  than 85°, then the market resolves to Yes."* This is the fact that makes
  the whole direction tractable: the thing being forecast and the thing
  being settled against are the same agency's number for a specific named
  station — not a proxy, not a correlated index.
- **Ladder structure is machine-parseable, no LLM needed**: markets carry
  `strike_type` (`greater`, `between`) and `floor_strike` (e.g. 85, 78) —
  the same fields S5b needs. Two shapes coexist per city-day: threshold
  markets (">85°") and disjoint bucket markets ("78–79°").
- **Cross-link worth noting**: a city-day's `between` buckets are mutually
  exclusive and (with the tail thresholds) close to exhaustive — i.e. these
  ladders are simultaneously S5a basket candidates and S5b ladder
  candidates. The S5a/S5b canary and the S6 provider therefore share market
  discovery and can share a poller.
- Prices at scan time on the two highest-volume LAX markets were
  `yes_bid=0.00 / yes_ask=0.01` — end-of-day markets already resolved in
  substance. Real quoting behavior must be sampled intraday, not from a
  single late-evening snapshot; the implementation session should collect
  across the day rather than reason from this one.

### Kalshi's real fee structure — PRIMARY-SOURCE CONFIRMED, and a correction-of-a-correction
This section was written twice in one session. Recording both passes,
because the mistake is instructive.

**First pass (wrong).** Three independent third-party fee references say
Kalshi's maker fee is "25% of the taker fee," and this repo's own
`KalshiFeeModel` docstring says the same. On that basis this entry
originally declared Session 28's "maker fee = $0 on most Kalshi markets"
to be wrong. **That declaration was itself wrong.**

**Second pass (correct).** The operator supplied Kalshi's actual published
fee schedule (effective 2026-07-07). Verbatim:
```
Trading (taker) fees:  round up(M × 0.07   × C × P × (1−P))
    M = the multiplier for each contract (default is 1 unless otherwise indicated)
Maker fees:            round up(M × 0.0175 × C × P × (1−P))
    M = the multiplier for each contract (default is 0 unless otherwise indicated)
```
**The two formulas have different default multipliers.** Taker M defaults
to **1**; maker M defaults to **0**. So the maker fee is **$0 by default**,
and is charged only on the ~76 series explicitly enumerated in the
schedule's "Non-Standard Fees" table with a Maker Multiplier of 1.
Session 28 was substantially right; the secondary sources quote the
formula's coefficient (0.0175 = 25% of 0.07) while omitting that the
multiplier it is applied to defaults to zero.

**The lesson, recorded deliberately**: three agreeing secondary sources
and a matching internal docstring still produced a wrong conclusion,
because all four described the *coefficient* and none described the
*default multiplier*. Agreement among secondary sources is not
confirmation. This is the same failure mode as Session 26's "tests
internally consistent with a wrong formula" and Session 28's "0 candidates
is a wiring fact, not a market fact" — and it happened here in the middle
of a session whose entire premise was verified-beats-argued.

**Measured consequence (live, same session, ~04:15 UTC — note total volume
read 69.4M on this pass vs 74.7M an hour earlier, so treat all these as
intraday snapshots, not constants):**
| | share of 24h volume | tradeable two-sided markets | median spread | spread ≥2¢ AND ≥100 contracts both sides |
|---|---|---|---|---|
| Maker fee **$0** (M defaults 0) | **42.5%** (29.5M contracts) | **3,651** | **2¢** | **489** |
| Maker fee charged (M=1) | 57.5% (39.9M contracts) | 207 | 1¢ | 27 |

The shape of that split is the interesting part: the fee-charging series
are the highest-volume ones (KXPGATOUR, KXMLBGAME) **and** the tightest —
1¢ median spread, i.e. already professionally market-made. The wide
spreads sit in the zero-maker-fee series, which still carry real volume:
KXMLBTOTAL (2.45M), KXBOXING (2.28M), KXLIGAMXGAME (2.17M), KXMLBSPREAD
(1.64M), KXMLSGAME (1.33M). **489 markets currently offer a ≥2¢ spread
with ≥100 contracts resting on both sides and no maker fee at all.**

Two further details from the primary source worth carrying forward:
- **Rounding**: the schedule's prose says fees round up "such that the fee
  + positionCost is rounded to a centicent" ($0.0001), but its own
  published fee table shows 1 contract at $0.10 paying $0.01 — i.e.
  ceil-to-the-cent behavior for small orders. The table is authoritative
  for behavior; the prose may relate to the sub-cent (`deci_cent`)
  `price_level_structure` now present on some markets. **Implement against
  the table, and verify against a real fill before trusting either.**
- **Ten series carry BOTH multipliers at 0** — no taker fee and no maker
  fee (KXBTCY, KXETHY, KXCITRINI, KXDOED, KXGREENLAND, KXIRANDEMOCRACY,
  KXELECTIRAN, KXGAMBLINGREPEAL, KXLAYOFFSYINFO, KXPAHLAVIHEAD). Measured
  volume in these across tradeable markets: **zero**. Free to trade,
  nothing to trade.
- **Kalshi now lists perpetual futures** with a tiered bps maker/taker
  schedule (taker 12.0 bps at tier 0, maker 5.0 bps). A new instrument
  class on a CFTC-regulated venue — noted for the multi-asset scoping
  section below, not acted on.

**Does this change the direction decision?** It weakens one of the four
arguments for divergence-first, and only one. Reasons 1, 2 and 4 below —
divergence can be falsified offline while market-making cannot;
market-making requires a live order layer built entirely up front; the
fair-value abstraction transfers to other venues and an order layer does
not — are untouched by the fee correction. Reason 3 (the fee argument) is
withdrawn. Market-making is now a **stronger** candidate than this entry
first concluded, and the 489-market zero-fee surface should be treated as
a real finding when it is picked up. The sequencing stands on the
remaining three reasons, not on the fee.

### The decision
**Near-term direction: S6 — External Model Divergence, weather/NOAA first,
in detect-and-log mode, gated behind an offline backtest.**
Market-making (S8) is specced but deferred behind a live order-management
layer that does not exist. S5a/S5b arbitrage detection continues in
parallel as a cheap, passive canary — not as a competing priority.

### Why divergence before market-making — the reasoning, not just the verdict
1. **Divergence can be falsified offline; market-making cannot.** NWS
   publishes forecasts; Kalshi publishes how every market settled. The
   question "when the model said 70% and the market said 55%, what
   actually happened?" is answerable from history, with no capital, no
   paper trades, and no order placement. There is **no offline test for
   market-making at all** — whether resting quotes get filled, and whether
   the fills you get are the ones you didn't want, is only observable by
   placing real resting orders in a real book. A direction that can be
   killed cheaply beats one that can only be evaluated expensively. That
   is the single discipline that has actually worked on this project:
   S1 died for $0 because someone pulled 778 real books instead of
   arguing.
2. **Market-making requires the one capability this codebase has never
   had.** Every component to date is read-only: watch books, detect,
   simulate fills. Market-making needs authenticated *write* access with
   an order state machine — place, cancel, amend, reconcile, and
   cancel-on-disconnect — and it needs all of it built *before* the first
   bit of evidence arrives. That is the largest and riskiest new
   subsystem in the project's history, spent entirely up front.
3. ~~**Its quantitative case was built on the maker-fee error above.**~~
   **WITHDRAWN** — this reason was itself based on the mistaken fee
   reading, and the primary source shows market-making's fee economics are
   *better* than this entry first concluded, not worse. See the fee
   section above. The other three reasons stand on their own.
4. **Divergence transfers to the stated multi-asset ambition; market-making
   does not.** "Compute an external fair value, compare to market price,
   trade the gap" is the shape of essentially all discretionary and
   systematic trading outside pure market-making. A `FairValueProvider`
   abstraction written for NOAA/Kalshi is the same abstraction for an
   options-implied provider, or a rates provider, on a different venue.
   An order-management layer written against Kalshi's order API is
   Kalshi-specific and would be rewritten per venue.

**The honest counter, recorded so it isn't lost:** weather is 3.2% of
volume, Kalshi weather markets are known to attract participants already
using NOAA, and the backtest may well show no edge net of fees. That is an
acceptable outcome — it costs one session to learn, which is the entire
argument for going this way first.

### Where arbitrage actually stands (three different answers, previously blurred)
- **S1 — dead by construction, permanently.** Confirmed live Session 29.
  Not revisitable. Stays in canary mode as a book-corruption detector.
- **S5a (event sum-to-one baskets) / S5b (threshold ladders) — not
  disproven, just not sitting there in one snapshot.** Session 29 checked
  1,600 markets: 0 of 78 apparent basket candidates survived the
  `mutually_exclusive` check, and the closest real ladder came to 1.01
  against the 1.00 needed. That is the signature of an efficient market,
  not of a broken strategy — and crucially, unlike S1 there is **no
  structural reason these cannot exist**: Kalshi does not atomically match
  across an event's separate markets, so a mispricing there genuinely can
  rest.
- **Therefore S5a/S5b continue — as a passive canary, not a priority.** A
  detect-and-log scanner for these is a REST poller plus arithmetic: no
  LLM, no order placement, no risk, no hot path. Built once, it runs in the
  background and converts "one snapshot found nothing" into real frequency
  data over weeks. Explicitly *additive* to the divergence work, consuming
  a small share of one build session, not competing with it.

### Architecture — S6 External Model Divergence
Deliberately additive. Nothing existing is removed, gutted, or repurposed
(see CLAUDE.md and the Session 30 bridge prompt: the arb substrate stays).

**Naming/lineage**: the April 2026 vision docs already spec this as S6
("Options Divergence Signal") with an Options Signal Agent publishing
`ImpliedProbabilityDivergenceEvent` — and call it the system's "secret
weapon." This is that idea generalized from options to any calibrated
external source, so it keeps the S6 name and the existing event type is
reserved for the eventual options provider rather than being contorted now.

**New agent 1 — `agents/research/fair_value_engine.py`**
(`FairValueEngineAgent`, Research Floor, slow cycle, allowed external HTTP).
Owns a registry of pluggable providers. Provider interface:
```python
class FairValueProvider(Protocol):
    name: str            # "noaa_gridpoint" — provenance, logged on every estimate
    version: str         # bump on any model change; recorded per estimate
    def matches(self, market: Mapping) -> bool: ...
    async def fair_value(self, market: Mapping) -> Optional[FairValueEstimate]: ...
```
First provider: `NoaaTemperatureProvider` (api.weather.gov — **confirmed
free, no API key, requires a descriptive `User-Agent` with contact info,
rate-limited but unpublished limits; `/points/{lat},{lon}` → gridpoint →
`/gridpoints/{office}/{x},{y}/forecast`**). Polls on a slow cycle (forecasts
update roughly hourly — no reason to poll faster), caches the `/points`
lookup per city permanently.

**New agent 2 — `agents/floor/divergence_scanner.py`**
(`DivergenceScannerAgent`, Trading Floor). Subscribes to `PriceUpdateEvent`
and `FairValueEstimateEvent`, holds both caches, computes divergence, and
in detect-and-log mode writes a structured record and publishes nothing
tradeable. Deliberately **not** added to `ArbScannerAgent`: that agent's
contract is riskless-arb detection on the hot path, and mixing a
variance-bearing strategy into it would blur exactly the riskless-vs-
statistical distinction that Session 28 found blurred everywhere else
(single-leg S3 sold as "arb", Kelly applied to riskless baskets).

**New event — `FairValueEstimateEvent`** (additive to `core/events.py`, all
fields defaulted per existing convention):
`platform, market_id, provider_name, provider_version, fair_prob,
confidence, observed_at, valid_until, provenance, sample_support`.
`valid_until` is load-bearing: a fair value derived from a 6-hour-old
forecast is not a fair value, and RiskGate must be able to reject on it.

**Config — `StrategiesConfig` additions** (all default to the safe state):
```python
s6_divergence_enabled: bool     = False   # off until the backtest gate passes
s6_detect_and_log_only: bool    = True    # same canary pattern as s1_canary_mode
s6_min_divergence_pct: float    = 0.0     # PLACEHOLDER — must be SET FROM THE
                                          # BACKTEST, never guessed. 0.0 with
                                          # detect-and-log on = log everything.
s6_min_model_confidence: float  = 0.6
s6_max_model_age_minutes: int   = 90
s6_max_concurrent_positions: int = 3
s6_max_capital_per_provider_pct: float = 10.0
```
Note also that `from_yaml()` still does not parse the `strategies:` section
at all (Session 24/28 debt) — so until that is fixed, every one of these is
code-editable only. Fixing `from_yaml()` is a listed prerequisite.

### RiskGate — this is where Kelly finally belongs, with the right inputs
Session 28 established that Kelly is wrong for riskless arb (it imposes a
hidden ~5.26% net-edge floor via a hardcoded `p=0.95`). S6 is the opposite
case: a real probability, real variance, repeated bets — textbook Kelly.
But it must be fed the model's probability, not a per-strategy constant.

For a binary contract bought at price `c` with model probability `p`
(stake `c`, profit `1−c` on win, loss `c` otherwise), net odds
`b = (1−c)/c`, so:
```
f* = (b·p − q)/b = p − (1−p)·c/(1−c) = (p − c)/(1 − c)
```
**`f* = (p − c)/(1 − c)`** — fraction of bankroll to stake, in dollars.
Contracts = `floor(f* × kelly_fraction × bankroll / c)`, which is
unit-correct by construction and rejects below 1 contract. Note this makes
the strategy's *own* threshold binding again instead of a hidden constant:
`f* > 0 ⟺ p > c`, exactly the condition the strategy is claiming.

Changes required in `agents/floor/risk_gate.py`:
1. **Prerequisite, not optional — land the integer-contract unit fix first**
   (Session 28, DECISIONS.md entry 3). S1's dollars≈contracts coincidence
   (a YES+NO pair costs ≈$1) does not hold for a single-leg S6 position at
   any price; the error is a factor of `1/c` — 3.3x at a 30¢ contract.
   Populate `capital_required_usd = qty × c` so check 2 finally binds.
2. **Replace the hardcoded per-strategy `p`** in
   `_calculate_position_size` with the model's `fair_prob` for S6, using
   the closed form above. Leave the arb path alone (it is canary/disabled).
3. **Per-strategy staleness horizon** — check 7 currently hardcodes 2s for
   `S1_REBALANCING` and 30s for everything else. S6 opportunities live for
   hours; 30s would reject nearly all of them. Add a per-strategy horizon
   and, separately, a **model-staleness** rejection on
   `FairValueEstimate.valid_until` / `s6_max_model_age_minutes`.
4. **New: correlated-exposure cap.** Five strike levels on one city's
   temperature ladder are one bet, not five, and a systematically biased
   provider makes *every* open S6 position wrong simultaneously. Needs
   (a) a per-`event_ticker` cap and (b) a per-provider capital cap
   (`s6_max_capital_per_provider_pct`). This finally makes
   `PositionSnapshot.correlation_score` load-bearing — it has been
   permanently 0.0 and filed as a "Phase 3 item"; for a variance-bearing
   strategy it is a Phase 1 requirement.
5. **Hold-time/exit semantics.** Arb positions are held to resolution by
   design. S6 positions have a thesis that can expire (the forecast
   updates and the divergence closes, or reverses). `PaperExecutor`
   currently resolves *every* trade at `expected_pnl` after a fixed delay
   — which is tautological for a directional strategy (Session 28 item 6)
   and would make S6 paper results meaningless. **S6 paper resolution must
   be settled against the market's actual outcome, not against its own
   forecast.** This is a hard requirement, not a refinement.

### The backtest — mandatory gate, and the biggest unknown in this plan
The vision docs listed a backtesting framework in the build sequence
(Architecture doc §8.1, item 9); it was never built. For riskless arb its
absence was arguably survivable. For a statistical strategy it is not:
without it, "S6 is profitable" is an opinion.

Minimum viable scope (`backtest/`, offline, never imported by the live path):
1. **Outcome data**: settled Kalshi weather markets — ticker, strike,
   close time, `result`. Available from the same public REST endpoint with
   `status=settled`.
2. **Forecast data — the open question that must be resolved FIRST.**
   `api.weather.gov` serves the *current* forecast, not an archive of past
   forecasts. Historical NWS/NBM forecast archives are understood to exist
   (NOAA NOMADS, Iowa State's IEM archive of NWS products) but **this
   session did not verify that any of them is reachable, complete, or
   matched to the specific stations Kalshi settles on.** Resolving this is
   the first task of the next session, because it decides the timeline:
   - **If an archive is usable** → a real backtest is possible immediately,
     over months of history, in one session.
   - **If not** → the fallback is *forward collection*: log
     (forecast, market price, later outcome) triples starting now, and the
     "backtest" becomes a forward test needing weeks before it can say
     anything. Cheap to run, slow to answer.
   Do not start writing model code before this is settled.
3. **The scoring bar, stated precisely, because it is easy to get wrong**:
   the question is **not** "is NOAA accurate?" (it is) — it is "**is NOAA,
   converted to a probability, better calibrated than the Kalshi price
   itself?**" The baseline is the market price. Metric: Brier score of
   model vs Brier score of market price, out-of-sample, plus a reliability
   curve. A model that beats a coin flip but loses to the market has no
   edge.
4. **The modeling step that is easy to underestimate**: NWS gridpoint
   forecasts are *deterministic* values (a predicted high temperature),
   not probabilities. Turning "predicted high 78°F" into "P(high > 75°F)"
   requires a **forecast-error distribution**, estimated per station and
   per lead time from historical forecast-vs-actual pairs. That error model
   *is* the strategy. NBM probabilistic guidance may supply this directly —
   verify before building a bespoke one.
5. Only after all of the above: apply ceil'd taker fees, the observed
   depth cap, and realistic fill assumptions to convert a calibration edge
   into a net-of-cost expected edge.

### Gates before S6 ever touches paper money
Same discipline the arb strategies got, in order. Each is a stop:
- **G1 — Data**: forecast/outcome pairs obtained (archive or forward), with
  the sample size stated explicitly.
- **G2 — Calibration**: model Brier score beats the market-price baseline
  out-of-sample. Sample size and the split must be stated. Failing here
  ends the strategy — cheaply, which is the point.
- **G3 — Net of costs**: edge survives ceil'd fees + depth caps + slippage.
- **G4 — Live plumbing**: detect-and-log running live 1–2 weeks, and the
  observed divergence frequency/magnitude matches what the backtest
  predicted. A mismatch here means a live-data bug, not an edge — this is
  the gate that would have caught S1 four sessions earlier.
- **G5 — Prerequisites landed**: integer-contract unit fix, S6 paper
  resolution against real outcomes, correlated-exposure caps.
Only then `s6_detect_and_log_only = False`, in paper mode, small.

### Market-making (S8) — deferred, and what would unblock it
Kept on the roadmap, explicitly not next — but on better footing than this
entry's first draft concluded, once the fee schedule was read correctly.
Measured surface for whenever it is picked up, **restricted to series that
pay no maker fee at all**: **489 markets** with a spread ≥2¢ and ≥100
contracts resting on both sides; **391** with ≥3¢ and ≥50; median
top-of-book `min(bid,ask)` across all traded markets is **42 contracts**.
The zero-maker-fee universe is 3,651 tradeable markets carrying 42.5% of
exchange volume at a 2¢ median spread. Concentrate any future work there
rather than on the headline series (KXPGATOUR, KXMLBGAME), which both
charge maker fees *and* quote at a 1¢ median spread — the signature of
existing professional market-makers.
Prerequisites, all currently missing: a live order-management layer
(`live_order_manager.py` — place/cancel/amend/reconcile, order state
machine, cancel-on-disconnect, rate-limit handling); an inventory tracker
with per-market position and skew; adverse-selection monitoring (are fills
concentrated on the side that then moves against you?); and a quote
repricing loop. Note the live executor is *also* a prerequisite for going
live on anything at all — so this work is not wasted, just correctly
sequenced after a strategy has evidence behind it.

### Multi-asset (forex/equities) — scoped as a separate future phase, deliberately not specced here
Brief and honest, per this session's mandate. This is not an extension of
the current work; it is a different regulatory regime and roughly a
Kalshi-integration-sized lift *per venue*:
- **Equities/options**: SEC/FINRA rather than CFTC. Pattern-day-trader rule
  binds directly at this account size (under $25k equity → 3 day trades per
  5 rolling days). Different tax treatment entirely — capital gains and
  Form 8949, plus wash-sale rules, versus Kalshi's ordinary-income
  1099-MISC path the `ComplianceOfficer` already implements. New broker
  auth (Alpaca/IBKR), new market-data schema, new order semantics.
- **Retail forex**: NFA/CFTC registration regime, 50:1 leverage cap on
  majors, FIFO rule and the no-hedging restriction, Section 988 vs 1256
  election.
- **Futures**: CFTC/NFA again, but 60/40 1256 treatment — different from
  both of the above.
Each venue means new auth, new data schema, new order semantics, new fee
model, new tax treatment, and an expanded regulatory-monitoring scope for
`RegulatoryIntelligenceAgent`. **Verdict: its own research phase, its own
session, gated on the Kalshi work actually producing measured edge. It does
not block anything near-term.** The one thing worth doing *now* costs
nothing: keep `FairValueProvider` and the divergence logic venue-agnostic
(no Kalshi types in the interface) so the abstraction survives the move.

### Vision-doc reconciliation — what is still worth building, and what isn't
Read all four April 2026 planning docs again this session. Against the
chosen direction:
**Genuinely relevant now:**
- **Options Signal Agent (S6)** — becomes the *second* `FairValueProvider`
  behind NOAA, not a separate agent. The docs' "secret weapon" framing is
  the direction now chosen, generalized.
- **Health Monitor** — still missing, still causing dead-lettered
  `AgentHeartbeat` events every ~30s, and materially *more* important once
  positions carry variance: a statistical strategy holding inventory must
  not silently stop. Cheap; should finally get built.
- **Portfolio Manager** — take the *problem* it exists to solve
  (correlated exposure across nominally separate positions), not the
  specced solution. The Bull/Bear LLM debate is not the right instrument
  for "five strikes on one city's temperature are one bet"; a correlation
  group cap in RiskGate is.
**Not now, with reasons:**
- News Analyst / Sentiment / Geopolitical — real ideas, but each is a
  diffuse signal feeding a directional bet with no calibration
  infrastructure behind it. `GeopoliticalRiskEvent` already has a live
  RiskGate consumer and no publisher; building a weak publisher for it
  would be worse than leaving it dead.
- Whale Tracker, Resolution Verifier — Polymarket/cross-platform only,
  i.e. Phase 2, still gated.
- **Correlation Engine / butterfly-effect module / 12-persona panel** — the
  intellectual heart of the World Intelligence doc, and the part most in
  tension with what has actually worked here. Every win on this project
  came from checking *one specific claim* against real data; a module whose
  premise is "test thousands of speculative correlations" produces false
  positives by construction (to the doc's credit, it does specify
  Bonferroni correction, 20-resolution minimums, and replication across
  three periods — those guardrails are the load-bearing part, not the
  ambition). Honest read: its first legitimate instance already exists
  inside this plan — measuring, per category and over time, whether the
  fair-value model beats the market price. That *is* the correlation
  engine in miniature, and it is the version worth building first.
- One more thing worth recording from the re-read: the vision's own S1
  trigger, "YES_price + NO_price < 1.00," never specified *which side of
  the book*. The ambiguity that became the Session 26 sign bug and then the
  Session 28 structural finding was present in the spec from day one.
  **Applied to S6**: the spec above names the executable price side and the
  settlement source explicitly, because that is precisely the class of
  omission that cost this project three months.

### Ordered build plan (next implementation session onward)
- **Phase 0 — prerequisites, no strategy code.**
  0a. Integer-contract unit fix across RiskGate/PaperExecutor/PositionTracker;
      populate `capital_required_usd`; floor to int, reject < 1; make
      `KalshiFeeModel` ceil to the next cent.
  0b. Resolve the NOAA historical-forecast-archive question (§backtest item
      2). Decides whether Phase 1 takes a session or several weeks.
  0c. Wire `from_yaml()` to parse `strategies:`/`capital:`/`risk:`/
      `data_feeds:` so any of this is tunable without a code change.
- **Phase 1 — measure before building.** `FairValueEngine` +
  `NoaaTemperatureProvider` + the `backtest/` harness. Deliverable is a
  calibration report (Brier vs market baseline, reliability curve, stated
  sample size) — **not** a trading agent. G1/G2 decided here.
- **Phase 2 — detect-and-log.** `DivergenceScanner` live, publishing
  nothing tradeable, logging to `logs/divergence_candidates.jsonl`. Run
  1–2 weeks. G3/G4 decided here.
- **Phase 3 — S5a/S5b passive canary** (parallel, small). REST-poll basket
  and ladder scanner honoring `mutually_exclusive` + exhaustiveness,
  pricing at ask, applying ceil'd per-order fees × N legs, logging to
  `logs/basket_candidates.jsonl`. Never publishes a tradeable event.
- **Phase 4 — gate review, then paper.** Only if G1–G5 all pass.
- **Phase 5 — live order manager**, then market-making, then live trading.

### Also decided: the 30-day paper clock is restarted, not paused
The clock started 2026-06-29 with a target live date of 2026-07-29 — a date
that has now passed. That window is not usable evidence: 9 of its first 14
days had a dead persistence layer (Session 26), and every S1 trade in it is
a confirmed book-reconstruction artifact (Session 29), not edge. The clock
is **reset to start when a strategy that passes its gates begins paper
trading**, and CLAUDE.md is updated to stop presenting the old dates as
live. No live trading on any strategy until that clock has actually run.

---

## 2026-07-16 — Session 28 (review-only): S1 single-market arbitrage is structurally impossible on Kalshi — every S1 signal, including Session 27's 5 "hand-verified" trades, is almost certainly a book-reconstruction artifact

### Status: FOUND, ARGUED, NOT YET LIVE-VERIFIED. No code changed this session (review-only mandate). Verification plan below is cheap and specific — run it before trusting any S1 paper P&L, and before building anything else on top of S1.

### The structural argument
Kalshi runs ONE central limit order book per market, with price-time
priority matching. The book stores only bids per side (confirmed live,
Session 15), because on a binary contract the two sides are the same
instrument viewed from opposite ends:

```
a resting NO bid at price p  ≡  an offer to sell YES at (1 - p)
a resting YES bid at price q ≡  an offer to sell NO  at (1 - q)
```

The corrected S1 condition (Session 26) is `yes_ask + no_ask < 1`.
Substituting the identities above:

```
yes_ask + no_ask = (1 - no_bid) + (1 - yes_bid) = 2 - (yes_bid + no_bid)
yes_ask + no_ask < 1   ⟺   yes_bid + no_bid > 1
```

But `yes_bid + no_bid > 1` is a **crossed book**: the YES bid at q and
the NO bid at p are, in Kalshi's own unified representation, a bid at q
resting above an ask at (1-p) < q. A price-time-priority matching engine
executes crossed orders against each other **at match time — they never
rest**. Kalshi's help center states this directly: complementary
positions are automatically paired and redeemed ("Kalshi automatically
exchanges every pair you own and credits $1"); two bids summing over $1
are matched and minted into a contract pair the moment the second order
arrives. A resting state where buying YES+NO costs less than $1 cannot
exist on the exchange, even transiently, from the exchange's point of
view.

So under a CORRECT, CURRENT view of a Kalshi order book, S1 can never
fire. Not "rarely" — never. Every S1 candidate this system has ever seen,
before or after the Session 26 sign fix, was a view of the book that the
exchange itself never had.

### Independent corroboration
1. **Third-party**: botforkalshi.com's arbitrage guide states it
   outright — "buying NO is mechanically the same trade as selling YES,"
   the two sides "straddle 100¢ with a spread," and there is "never a
   harvestable gap" in a single market; the apparent gap "is the bid-ask
   spread itself: the price of crossing the book, not an edge."
2. **This project's own data**: every real, live-pulled book documented
   in this file sums the bids BELOW $1, exactly as the structural
   argument requires: 0.23+0.30=0.53, 0.42+0.40=0.82, 0.47+0.51=0.98
   (Session 26's three examples). Nobody has ever pulled a live Kalshi
   book via REST — outside our own WS reconstruction — that showed
   yes_bid + no_bid > 1.
3. **The shape of the observed "edges"** (Session 27: 5.99%, 8.95%,
   12.91%, 11.89%, 8.30% net) sits exactly in the window the pipeline
   filters for: above the hidden ~5.26% Kelly sizing floor (see the
   RiskGate entry below) and below the 15% sanity ceiling. Genuine
   competed arb would cluster near zero; artifacts have no reason to.

### Where the artifacts come from (two known-live mechanisms)
1. **Mid-match multi-delta transitions.** A single Kalshi match event
   (e.g. a taker crossing the spread, or two bids being minted into a
   pair) changes both derived sides of our book, and arrives as MORE THAN
   ONE WS delta message. Between message 1 and message 2 of an atomic
   server-side event, our reconstructed book holds a state the exchange
   never had — frequently a crossed one. `ArbScanner._on_price_update`
   evaluates S1 on EVERY delta, so it reads these half-applied states at
   full confidence. Session 15's own confirmed evidence (a level move
   arriving as a matched +523/−523 pair) is this exact mechanism.
   RiskGate's 2s staleness check and the depth cap don't help: the event
   is fresh and the phantom level carries real-looking size.
2. **Unrecovered sequence gaps leaving stale phantom bids** — the
   known-live, still-unfixed stuck book-reset loops (Session 26 KNOWN
   DEBT). A bid that was matched or cancelled during a gap stays in our
   book; any later opposite-side bid can then "cross" it.

### What Session 27's hand-verification actually verified
The dollar-exact checks verified the ARITHMETIC from the recorded
prices, and PaperExecutor fills unconditionally at recorded prices —
so agreement is guaranteed regardless of whether those prices were ever
simultaneously executable. In paper mode there is no step at which the
exchange gets to say "no." The verification was internally consistent
and still proves nothing about fillability — the same trap as Session
26's tests that were "internally consistent with a wrong formula."

### Verification plan (run before anything else is built on S1)
1. **Rest-state scan**: pull ~200 live order books via the existing REST
   snapshot endpoint and count how many show `yes_bid + no_bid >= 1.00`.
   Structural prediction: zero.
2. **Trade/gap correlation**: for each of the 5 Session 27 trades, grep
   VPS logs for `sequence_gap_detected` / `book_needs_reset` on that
   market within ±60s of the fill. Prediction: most or all correlate.
3. **Persistence test**: log how many `s1_candidate_seen` events survive
   to the NEXT delta on the same market (or 500ms, whichever is later).
   Prediction: approximately none survive.

### What this means for the strategy (decision)
- S1 as a profit strategy on Kalshi is **dead by construction**, not
  thin. Keep the detector running as a **data-quality canary** (an S1
  signal = our book disagrees with a state the exchange permits = a
  reconstruction bug or gap), but stop treating its paper P&L as
  strategy evidence. The 30-day paper clock, already compromised by the
  9-day dead zone, measures artifact frequency for S1 — not edge.
- The REAL generalization survives: Kalshi does **not** atomically match
  across DIFFERENT markets within an event. Sum-to-one arbitrage across
  an event's N outcome markets, and logical-ladder arbitrage across
  threshold markets, can genuinely rest and are the correct successors —
  see the strategy-roadmap entry below.

---

## 2026-07-16 — Session 28 (review-only): strategy audit — S2/S3/S4 all price the wrong side of the book; S3 and S4 have no working input path; S2 cannot match a market

Session 26 fixed the bid/ask sign error in S1 only and explicitly
flagged S2/S3/S4 as unaudited. Audited all three this session
(`agents/floor/arb_scanner.py`). All share S1's original bug class, and
each is additionally dead or broken upstream. None of this is deployed
risk today (S2 is phase-gated off; S3/S4 never receive events) — but
none of it should be built on without these fixes.

### S2 `_check_s2_cross_platform` — four independent defects
1. **Bid-side pricing** (same class as the Session 26 S1 bug):
   `cost_a = event.yes_bid + other_event.no_bid` and
   `cost_b = event.no_bid + other_event.yes_bid` — bid prices for a BUY
   on both legs, and the legs quote those bids as execution prices. Real
   cost is ask-side on both platforms; the computed "profit" has the
   same optimistic bias S1 had.
2. **Market matching can never succeed**: it looks up
   `self._prices["polymarket"][event.market_id]` — an EXACT id match.
   Kalshi tickers (`KXWNBAMENTION-26JUL13...`) and Polymarket condition
   ids share no namespace; this lookup will never hit. S2 as written is
   unreachable even in Phase 2 with feeds on. A real market-matching
   layer (semantic, LLM-assisted, human-confirmed) is a prerequisite.
3. **Fee model is a placeholder**: Kalshi fee hardcoded at worst-case
   `estimate_fee_pct(0.5, 0.5)` regardless of actual prices;
   `PolymarketFeeModel` (2% winner fee + $0.15 gas) does not match
   Polymarket's current fee schedule and must be re-verified against
   their current docs before any S2 work (their fee structure has
   changed repeatedly; the US-access entity question matters too — see
   SESSIONS.md Session 28, cross-platform assessment).
4. **No depth/liquidity cap** — predates the Session 26 depth work;
   `max_fillable_qty` is never set for S2.
   Note the existing failsafe that makes all this latent rather than
   live: `resolution_criteria_match` is always `None`, and RiskGate
   check 6 rejects cross-platform trades with `None` — so even if S2
   fired, nothing would execute. Three of the four defects must still be
   fixed before Phase 2.

### S3 `_check_s3_logical` — wrong side of book, empty-book edge inflation, and a silently dead input pipeline
1. **Dead input pipeline — why "S3 live, zero candidates" is not a
   market observation**: `MarketAnalystAgent._analysis_loop` skips every
   cycle when `self._active_markets` is empty, and `update_markets()` —
   the only thing that populates it — **has zero callers anywhere in the
   codebase** (the docstring says "Called by Price Watcher when market
   list is refreshed"; PriceWatcher never does). S3 has never analyzed a
   single market, never made an LLM call in production, and never could
   have produced a candidate. Zero candidates is a wiring fact, not a
   market fact.
2. **Bid-side pricing**: `_get_current_price` returns `yes_bid`; the
   check both computes the edge from bids and quotes the leg at the bid.
   A buy executes at `yes_ask`; same fix as S1.
3. **Empty-book edge inflation**: `to_price_event()` publishes
   `yes_bid=0.0` when a book has no bids. `_get_current_price` returns
   that 0.0 (not None — the None path only triggers when the market has
   never ticked), so a market-B book that is merely EMPTY yields
   `edge_pct = market_a_price * 100` — the thinner the book, the bigger
   the phantom edge. Must guard `price <= 0`.
4. **Single-leg S3 is statistical, not arbitrage** — it buys only
   underpriced B and waits for convergence; `net = edge * 0.7` is an
   arbitrary haircut, not a fee/slippage computation. The riskless
   version of an A⇒B violation is TWO legs: buy YES(B) at ask + buy
   NO(A) at ask; payoff is ≥ $1 in every logically-possible outcome
   (A∧B → 1+0; ¬A∧B → 1+1; ¬A∧¬B → 0+1; A∧¬B impossible if A⇒B), so
   cost < $1 − fees is a true arb **conditional on the implication
   actually holding at settlement** — which is an LLM/semantic judgment,
   i.e. residual tail risk lives in the relationship, not the prices.
   Classify paired-S3 as riskless-if-relation-holds, single-leg-S3 as
   statistical; never blur them.

### S4 `_check_s4_settlement` — unreachable, and directional by nature
No `NewsSignalEvent` publisher exists (News Analyst agent was never
built), so S4 is dead code behind an enabled-by-default flag
(`s4_settlement_arb_enabled=True` — should default False until real).
When it does run it prices off `yes_bid`, hardcodes a 0.95 repricing
target, 1.0% fees, 0.5% slippage, and takes one directional leg on a
news judgment — that is speculation with a stopwatch, not arbitrage,
and should be specced as such (position limits, confidence calibration)
when News Analyst is actually designed.

### Also found: the learning loop's output is a dead knob
`ReflectionAgent` publishes `StrategyWeightUpdateEvent`; `ArbScanner`
stores the weights in `_strategy_weights` and **never reads them in any
strategy check** — they appear only in `stats`. The vision docs say
weights "adjust strategy activation thresholds"; nothing does. Decide:
either wire weights into thresholds or delete the plumbing — a knob that
looks connected but isn't is how the telegram.enabled class of bug
happens (Session 24 precedent).

---

## 2026-07-16 — Session 28 (review-only): RiskGate sizing — Kelly-dollars are consumed as contract-quantity downstream, and Kelly is the wrong model for riskless arb (it imposes a silent ~5.26% net floor)

### The unit mismatch, traced end-to-end
`RiskGate._calculate_position_size` computes `size = total_capital *
kelly_fraction` — **dollars** — and caps it against
`event.max_fillable_qty` — **contracts** (`size = min(size,
event.max_fillable_qty)`, comparing incompatible units). The result
flows into `ApprovedOpportunityEvent.approved_size`, which
`PaperExecutor` writes directly into every leg's `"quantity"` and uses
as the fee multiplier (`fee_estimate * approved_size`), and
`PositionTracker` then computes `capital_used = Σ(filled_price ×
quantity)` — i.e. the same number is dollars at birth and contracts
everywhere after.

### Why 63 hours of live trades didn't expose it
For S1 specifically, one YES+NO pair costs ≈ $1 (`yes_ask + no_ask ≈
1`), so N dollars ≈ N contracts and `capital_used ≈ approved_size` —
the two unit systems coincide numerically **by coincidence of the
strategy's shape**. Per-contract profit is `net_pct/100` dollars, so
`expected_pnl = net_pct/100 × approved_size` is also self-consistent
under the contracts reading. The books balance for S1 while the units
are still wrong. They stop balancing the moment any single-leg strategy
(S3/S4) trades: at a 0.30 leg price, "size 100" deploys $30 as 100
contracts — the Kelly intent is off by 1/price (3.3x here).

### The deeper problem: Kelly with p=0.95 silently overrides the configured minimum edge
```
kelly_full = (b·p − q)/b  >  0   ⟺   b > q/p = 0.05/0.95 ≈ 5.26%
```
Any S1/S2 opportunity with net edge below ~5.26% sizes to ≤ 0 and is
rejected as `ZERO_APPROVED_SIZE`. So `s1_min_net_profit_pct = 0.5` is a
dead letter — the REAL floor is 5.26%, ten times higher, set implicitly
by a probability parameter nobody tuned for this purpose. This inverts
the risk logic of an arbitrage system: the safest signals (small edges,
which on a competed exchange are the only real ones) are all rejected,
and only implausibly-large edges (which per the S1 structural finding
are data artifacts) get sized. Kelly models repeated bets with
meaningful loss probability; a filled riskless basket has ~zero
variance, where Kelly's answer is "bet the maximum the caps allow" —
the caps (per-trade %, free capital, depth) should be the binding
constraint, not a pseudo-probability.

### Also: three adjacent correctness gaps for live trading
1. `OpportunityEvent.capital_required_usd` is never set by any strategy
   (defaults 0.0), and RiskGate check 2 only binds when `required > 0` —
   the per-trade position-size check has never actually run.
2. Kalshi quantities are **integer contracts, minimum 1** — the pipeline
   happily trades 0.05 contracts (Session 27's fifth trade), which
   cannot exist live. Sizes must floor to int and reject at < 1.
3. Kalshi's real fee is **rounded UP to the next cent** on the order
   (`ceil(0.07 × C × P × (1−P))` — formula confirmed against Kalshi's
   published schedule; per-order-vs-per-contract rounding granularity
   should be re-confirmed when implementing). `KalshiFeeModel`'s
   continuous fraction underestimates exactly where this system trades
   most — tiny, liquidity-capped orders, where the 1¢ minimum is a large
   fraction of face value (a 1-contract fill at 10¢ pays 1¢ = ~10% of
   price vs the model's 0.63%). Small-basket profitability must be
   computed with ceil'd per-order fees or it will be systematically
   optimistic. (Related, for the market-making analysis: maker orders
   pay NO fee on most Kalshi markets.)

### Recommended fix direction (implementation session — not done here)
Standardize the pipeline unit as **integer contract count**: strategies
emit per-contract economics; RiskGate sizes in contracts as
`min(depth_cap, floor(per_trade_cap_usd / basket_cost_per_contract),
floor(0.9·free_capital / basket_cost_per_contract))`; set
`capital_required_usd = qty × basket_cost` so check 2 finally binds;
keep fractional Kelly ONLY for statistical strategies (paired-S3, S6,
S7) where a genuine loss probability exists.

---

## 2026-07-16 — Session 28 (review-only): SECURITY — the Telegram operator channel trusts any sender on Earth; chat_id is never checked

### The finding (HIGH severity for a system that gates trading halts through this channel)
`TelegramNotificationAgent._poll_updates` processes every `getUpdates`
message and dispatches ANY text from ANY chat to
`_handle_operator_reply` — `msg["chat"]["id"]` is never compared to the
configured `TELEGRAM_CHAT_ID` (which the code holds and uses for
OUTBOUND messages only). Telegram bots are publicly addressable: anyone
who finds or guesses the bot's username can message it. As wired today,
a stranger's message can:
1. Resolve the oldest pending permission request — any text containing
   "yes"/"approve" approves it (FIFO, no request id needed);
2. **Clear an urgency-5 regulatory trading halt** — the clear phrase is
   checked against `response_text` of every operator message, and the
   default phrase ("CLEAR REGULATORY HOLD") is committed in
   `karbot/core/config.py` in a public repo;
3. Drive any future operator command (`/mute`, kill switch) added to
   this channel.
This has been latent since the agent was built and became live-relevant
the day Telegram was actually enabled (Session 24). Paper mode caps the
damage today; it must be fixed before live trading, and preferably now —
it is a ~5-line fix.

### Fix (implementation session)
In `_poll_updates`, drop (and log at warning, with the sender id) any
update whose `message.chat.id` does not equal
`config.secrets.telegram_chat_id` (string-compare after str() — Telegram
ids are ints). Additionally: set a non-default
`regulatory_clear_phrase` in the VPS config.yaml, since the default is
public.

### Secondary security findings (same pass, lower severity — full list in SESSIONS.md Session 28)
- **Bot token can leak into logs via exception text**: `_send_message` /
  `_poll_updates` build URLs embedding the token and then log raw
  exceptions (`f"...error: {e}"`); several aiohttp exception classes
  include the request URL in `str(e)`. Redact token from any logged
  error, or log only `type(e).__name__`.
- **VPS service user**: runs as `ubuntu` (default sudo-capable user),
  not the dedicated non-privileged `karbot_user` CLAUDE.md's own rules
  require. Deviation accepted so far; tighten before live.
- **`/usr/local/bin/karbot-disk-alert.sh` may be broken since Session
  26's own secrets move**: it was written to read Telegram credentials
  from the repo `.env` — which Session 26 deleted later that same
  session. VERIFY on the VPS that the script points at
  `/etc/karbot/secrets/karbot.env`; if not, the disk watchdog is
  currently silent — the exact failure mode it exists to prevent.
- **Kill switch has no trigger path**: `KillSwitchEvent` has zero
  publishers and `activate_kill_switch()` zero callers; the vision docs
  require CLI + dashboard + Telegram paths, none exist. The
  "non-bypassable" risk gate's strongest control is currently
  unreachable. Related dead inputs: `AnnouncementWarningEvent` and
  `GeopoliticalRiskEvent` also have no publishers (checks 4/5 can never
  trigger from real data), and `ResolutionVerificationResult` has no
  publisher (correct failsafe for S2, but means Resolution Verifier is a
  hard prerequisite for Phase 2).
- Positive findings, for the record: secrets handling itself is clean
  (env-only via `SecretsConfig`, nothing sensitive in `config.yaml`,
  both `config.yaml` and `.env` confirmed gitignored; no secret values
  logged anywhere found); the REST/WS auth code signs correctly and
  never logs key material; `compliance.db`/CSV carry trade data only.

---

## 2026-07-16 — Session 28 (review-only): strategy roadmap — event-basket sum-to-one and threshold-ladder arbitrage are the correct Phase-1-compatible successors to S1; S2 cross-platform stays deferred

Full menu with risk categorization in SESSIONS.md Session 28. The
decision-relevant core:

### Why these two (both TRUE riskless arbitrage, same guarantee class as S1 was believed to have, both Kalshi-only)
Kalshi's matching engine unifies YES/NO **within one market** (which is
what kills S1) but does **not** atomically match across the N separate
markets of a multi-outcome event, nor across logically-linked markets in
different events. Mispricings there CAN rest. Third-party corroboration
(botforkalshi.com) agrees real candidates exist but are thin and often
longshot-heavy — expectations should be "S1-like frequency, real this
time," not a gold mine.

1. **S5a — event sum-to-one basket**: for an event with N
   mutually-exclusive outcome markets:
   - YES-basket: buy YES on all N at ask; cost `Σ yes_ask_i`; pays
     exactly $1 **iff the event is also exhaustive** (one outcome must
     resolve YES). Arb iff `Σ yes_ask_i < 1 − Σ fees`.
   - NO-basket: buy NO on all N at ask; cost `Σ no_ask_i`; pays
     `$(N−1)` if exactly one outcome occurs; robust to
     "none-of-the-above" (pays $N, better) but requires mutual
     exclusivity (two YES outcomes would pay $(N−2)). Arb iff
     `Σ no_ask_i < (N−1) − Σ fees`.
   - Kalshi's event API exposes `event_ticker` grouping and a
     `mutually_exclusive` flag — use them; exhaustiveness must be
     verified per event series (some events have no catch-all bucket:
     YES-basket forbidden there, NO-basket still valid).
   - Real risks that remain (this is not S2-style leg risk, but it is
     not zero): N legs fill independently (no atomic basket order on
     Kalshi — confirmed) → partial-fill exposure for seconds; ceil'd
     per-order fees × N legs crush thin baskets (a 10-leg basket pays
     ≥10¢/contract-set in fee minimums); capital locked until
     resolution.
2. **S5b — threshold/date-ladder arb** (deterministic S3, no LLM): for
   same-underlying markets A = "metric > x_hi", B = "metric > x_lo",
   x_hi > x_lo, logic guarantees A⇒B. If priced backwards, buy YES(B)
   at ask + buy NO(A) at ask; payout ≥ $1 in every possible outcome
   (=$2 when the metric lands between the strikes); arb iff
   `yes_ask_B + no_ask_A < 1 − fees`. The implication comes from ticker
   structure / `floor_strike`-`cap_strike` fields — machine-checkable,
   zero semantic risk, unlike LLM-derived S3 relations. Same for date
   ladders ("by June" ⇒ "by July").

### Sequencing decision
Build S5a/S5b scanners in DETECT-AND-LOG mode first (no trading), run
them live for 1-2 weeks to measure real frequency/size/fee-adjusted
edge, then wire to RiskGate — after the unit-mismatch fix lands, since
basket sizing is exactly where dollars-vs-contracts confusion would do
damage. S2 cross-platform remains deferred: it adds genuine
unhedged-leg risk (non-atomic across venues), requires a Resolution
Verifier and a market matcher that don't exist, and Polymarket's US
legal-access status and current fee schedule must be verified first —
full assessment in SESSIONS.md. Market-making (S8) is the most
promising NON-riskless idea (maker fees are zero on most Kalshi
markets; observed books sit just outside break-even for takers, i.e.
just INSIDE it for makers) but requires a live order-management layer
that doesn't exist yet — statistical inventory risk, flag it clearly as
a departure from pure arb if pursued.
> **→ CONFIRMED CORRECT, Session 30 (2026-08-02), against Kalshi's own
> published fee schedule.** "Maker fees are zero on most Kalshi markets"
> holds: the maker formula's multiplier `M` **defaults to 0** (the taker
> formula's defaults to 1), so maker fees apply only to the ~76 series
> explicitly listed in the schedule's Non-Standard Fees table. Measured
> live: **42.5% of 24h volume, and 3,651 of 3,858 tradeable two-sided
> markets, carry no maker fee** — including 489 with a ≥2¢ spread and
> ≥100 contracts resting on both sides.
> Two refinements to the inference above, though: the fee-charging series
> are the *highest-volume* ones (KXPGATOUR, KXMLBGAME) and also the
> *tightest* (1¢ median spread — already professionally made), so the
> zero-fee opportunity lives in the mid-volume series, not the headline
> ones. Session 30 briefly published a contrary "correction" to this entry
> based on three agreeing secondary sources that quoted the coefficient
> but omitted the default multiplier; that correction was wrong and has
> been retracted. See the Session 30 entry at the top of this file.

---

## 2026-07-13 — Session 26: S1 arb formula uses BID prices for both legs of a BUY trade — likely inverts P&L sign on every trade since inception

### Revert point: commit `5348533` — before this fix. Everything below it (all Session 26 work up to and including the disk outage fix, stale-publish fix, sanity ceiling, depth plumbing) is unaffected by this finding and does not need to be reverted if this fix is backed out.

### The math
`agents/floor/arb_scanner.py::_check_s1_rebalancing` computes:
```
combined_cost = event.yes_bid + event.no_bid
gross_pct = (1.0 - combined_cost) * 100
```
`yes_bid` and `no_bid` are **bid** prices — the price *other market
participants* are resting orders to buy at. They are not prices you can
buy at. To actually execute "buy YES + buy NO," you must cross to the
**ask** side of each book.

Kalshi's order book is bid-only by design (documented in this file,
Session 15): a resting bid to buy NO at price P is mathematically
equivalent to an offer to sell YES at price `(1-P)`, because holding NO
and being short YES have identical payoffs on a binary contract. So:
```
real cost to buy YES now = yes_ask = 1 - best_no_bid
real cost to buy NO now  = no_ask  = 1 - best_yes_bid
real combined cost       = yes_ask + no_ask = 2 - (yes_bid + no_bid)
real gross profit        = 1 - real_combined_cost = (yes_bid + no_bid) - 1
```
That is the **negative** of what `_check_s1_rebalancing` currently
computes (`gross_pct = (1 - (yes_bid+no_bid))*100`). The formula has the
sign backwards: it is scoring the BID-side sum as if it were the ASK-side
cost.

`PriceUpdateEvent.yes_ask` / `.no_ask` already contain the correct,
real, executable ask prices (`to_price_event()` in `price_watcher.py`
computes them correctly) — the bug is narrowly that
`_check_s1_rebalancing` reads `.yes_bid`/`.no_bid` instead.

### Verification against real data, not just algebra
1. **Live market pulled directly from Kalshi's REST API this session**
   (`KXWNBAMENTION-26JUL13PHXMIN-MVP`): `yes_bid=0.23`, `no_bid=0.30`.
   Current code: `combined=0.53` → reports **+47% profit**. Real
   executable cost: `yes_ask=1-0.30=0.70`, `no_ask=1-0.23=0.77`,
   `total=1.47` → actually a **47% guaranteed loss**.
2. **A "normal," non-outlier example** (`yes_bid=0.42`, `no_bid=0.40`,
   which the current code scores as a clean +3.7% net edge after fees):
   real cost comes out to `yes_ask=0.60 + no_ask=0.58 = 1.18` — an 18%
   loss, not a profit. This wasn't cherry-picked — it's the "realistic
   small edge" example from tonight's own sanity-ceiling test
   (`tests/test_arb_scanner_s1_sanity_ceiling.py`), which passed and was
   treated as evidence the ceiling fix was working correctly.
3. **Corroborating evidence already in this project's own history**:
   `SESSIONS.md` Session 2 records that the strategy's original spec
   prices (YES=0.47, NO=0.51) were "unprofitable after fees" under
   whatever formula existed at the time, so the team substituted
   artificial 0.40/0.40 fixture prices to make the test pipeline fire.
   Under the *correct* ask-based formula, 0.47/0.51 works out to
   `yes_ask=1-0.51=0.49`, `no_ask=1-0.47=0.53`, `total=1.02` — a small
   ~2% loss, exactly what you'd expect from a normal, roughly-efficient
   market with an ordinary bid-ask spread. That the original, presumably
   real/researched spec prices come out approximately break-even under
   the corrected formula — while the buggy formula rejected them as
   unprofitable and needed invented numbers instead — is strong
   independent support that the sign has been wrong since Session 2
   (2026-05-25), the very first working version of this strategy.

### What this means
If this holds, **every S1 "opportunity" the system has ever flagged as
profitable was, by the corrected math, a computed loss with the sign
flipped** — not a subset, not just the outliers caught by tonight's
sanity ceiling. This is a distinct issue from the stale-order-book bug
fixed earlier tonight (that bug made a wrong-but-plausible number look
worse than it should; this one makes the entire strategy's profitability
signal backwards regardless of data quality) and from the missing-depth
issue (that one is about whether a real, correctly-priced edge is
actually fillable at size). All three bugs were live simultaneously,
independently discovered in the same session, each compounding the
others' effect on the reported P&L.

### Fix
`_check_s1_rebalancing` should read `event.yes_ask` / `event.no_ask`
(already correctly computed, just unused by this function) instead of
`event.yes_bid` / `event.no_bid`, and the resulting opportunity's legs
should quote the ask prices (the real prices a buy order would pay), not
the bid prices. See the commit immediately following this one for the
implementation and tests.

### Why this wasn't caught by any of tonight's other fixes or the 83-92
passing tests before this session
None of the existing tests constructed a `PriceUpdateEvent` with
independently-set `yes_ask`/`no_ask` values that diverged meaningfully
from a naive `1 - other_side_bid` assumption in a way that would surface
the sign error — the fixture prices used throughout (0.40/0.40, etc.)
were chosen specifically to make the *existing* (buggy) formula produce
a positive, testable result, which is exactly the trap: the tests were
written to confirm the code did what it currently does, not to check
that what it currently does is financially correct. This is a case
where 100% passing tests provided false confidence — the tests were
internally consistent with a wrong formula.

---

## 2026-07-01 — Session 25: one event, one Telegram consumer — removed a duplicate/broken regulatory alert path

### RegulatoryAlertEvent stays a pure ComplianceOfficer logging signal; Telegram gets it only via the urgency-branched path
- `TelegramNotificationAgent` had its own direct subscription to
  `RegulatoryAlertEvent` (`_handle_regulatory_alert`), independent of
  `RegulatoryIntelligenceAgent._route_by_urgency`'s already-correct,
  urgency-branched `TelegramNotificationEvent` publications. Both fired for
  every regulatory item, since `RegulatoryAlertEvent` is published
  unconditionally (by design, for `ComplianceOfficer`'s audit trail) —
  producing two Telegram messages per item. The direct-subscription path
  was leftover from before `RegulatoryIntelligenceAgent` existed
  (`RegulatoryAlertEvent`'s `source_name`/`matched_keywords` fields and the
  `logs/regulatory_alerts.txt` reference are artifacts of the old
  keyword-scanning `ComplianceOfficer` polling loop, removed in an earlier
  session — see "ComplianceOfficer polling loop removed" decision) and was
  never updated or removed when the new agent took over regulatory Telegram
  messaging.
- Decision: removed `TelegramNotificationAgent`'s `RegulatoryAlertEvent`
  subscription and handler entirely, rather than fixing the broken
  field references or updating the dead file path. The event already has a
  correct, complete Telegram-messaging consumer
  (`RegulatoryIntelligenceAgent._route_by_urgency`) — the fix is
  subtraction, not repair. `RegulatoryAlertEvent` keeps publishing
  unconditionally for `ComplianceOfficer`'s benefit; only the redundant
  Telegram subscriber was removed.
- **Rationale, beyond just noise**: the broken duplicate message was
  hardcoded to `"🚨 KARBOT RAGE! CRITICAL"` regardless of actual urgency.
  A routine urgency-3 FYI produced a message labeled CRITICAL — this
  actively degrades operator trust in that label, which matters most for
  urgency 5 (trading-halt). A wrong or redundant alert is not neutral; it
  has a real cost against the one alert the system most needs to be taken
  seriously. This is the first live evidence (from tonight's first-ever
  enabled Telegram run, following Session 24's config fix) that two
  independent consumers of the same event, each built at different times
  with different assumptions about what the event means, is itself a
  design smell worth watching for elsewhere in the event bus — one event
  should have one clearly-owned interpretation per concern (here:
  ComplianceOfficer owns "log it," RegulatoryIntelligenceAgent owns "tell
  the operator, tiered by urgency" — not two agents both deciding
  independently how to tell the operator).
- **This is also a direct consequence of Session 24's finding that Telegram
  alerting had never actually been exercised in production** — this bug
  existed in the codebase through every prior session that touched
  Telegram or Regulatory Intelligence, but was invisible until tonight's
  first live run with `telegram.enabled=True` actually produced Telegram
  output an operator could read.

### Known open items flagged, not resolved this session (see SESSIONS.md for full detail)
- Paper trade fee variance ($70.00 flat / $0.00 / $42.78 / $113.27 / $56.64
  observed across trades) — not investigated, needs a fee-calculation-logic
  vs. `compliance.db` cross-reference next session.
- **P&L magnitude not yet re-verified since the Session 23 REST-based
  book-reset recovery fix went live** (~16:31 UTC 2026-07-01). The original
  inflation hypothesis (corrupt books → bad spreads → spurious S1
  opportunities) had its proposed root cause fixed, but the resulting P&L
  distribution has not been checked against the realistic 1-5% benchmark.
  Live Telegram PnL figures observed tonight ($338.50, $343.50, $383.50,
  $323.50) look comparable to or larger than the original inflated range —
  not confirmed improved. Flagged as the first priority for next session;
  do not treat paper trading data as validated until checked.

---

## 2026-07-01 — Session 24: "verify live" extends to config state, not just API/code behavior — Telegram alerting never actually ran

### A feature can pass every test, deploy cleanly three times, and still never run — if a gating flag defaults off and nothing confirms its resolved value in production
- `TelegramConfig.enabled` defaults to `False`. No `config.yaml` existed on
  the VPS (confirmed via `ls` — only the committed `config.yaml.example`
  template is present), so `KarbotConfig.from_yaml()` fell back to that
  default in production the entire time Sessions 19-20's Telegram features
  were being built and deployed. `TelegramNotificationAgent` no-ops
  completely when disabled — by design, correctly — but with zero error or
  warning distinguishing "intentionally disabled" from "accidentally never
  configured." Three live deploys, including a real crash/restart/
  restart-budget-exhaustion cycle today that should have fired a CRITICAL
  Telegram alert, produced no Telegram messages at all, and nothing in the
  logs made that obvious.
- **This extends the project's established "verify live before trusting
  assumptions" principle (Session 13/15/18/21/22/23 precedent — previously
  applied to API behavior, WS schema, and the project's own defensive code
  additions) to a new category: config/environment state.** A feature can
  be perfectly coded, pass every unit test, and deploy cleanly multiple
  times, and still never actually execute in production if a gating
  configuration flag silently resolves to "off" and nothing in the system
  surfaces that resolved value where an operator would see it. Passing
  tests and clean deploys are necessary but not sufficient evidence that a
  feature is running — the actual runtime configuration state has to be
  independently confirmed, the same way live API behavior has to be
  independently confirmed rather than assumed from docs.
- Decision: added a `config_resolved` startup log line
  (`karbot_runner.py`) that prints the actual resolved value of every
  subsystem enable/disable flag once, immediately after config load and
  before any agent starts. This is the config-state equivalent of the
  `kalshi_first_price_update` one-shot live-confirmation log added in
  Session 15 for the WS price pipeline — a cheap, always-on, low-noise
  signal that answers "is this actually on?" without needing to reconstruct
  the answer from source code or tribal knowledge every time.
- Also documented (comment in `config.yaml.example`, not a code fix) a
  related but separate gap found while tracing this: `KarbotConfig.
  from_yaml()` never parses a `data_feeds:` YAML section at all, so
  `config.yaml.example`'s `api.kalshi.enabled`/`api.polymarket.enabled`
  keys are dead — editing them has zero runtime effect, which is its own
  smaller instance of the same category of bug (config that looks
  authoritative but silently isn't). Not fixed this session — flagged as
  KNOWN DEBT, out of scope for a "config + one log line" task.

---

## 2026-07-01 — Session 23: REST snapshot endpoint requires no auth — confirmed live; defensive auth caused a real outage

### Kalshi's orderbook REST endpoint requires no authentication — CONFIRMED LIVE
- Session 22 added RSA-PSS auth headers to the new REST snapshot fetch
  defensively, without empirical verification, despite Kalshi's docs
  already stating the endpoint requires no auth. That session's own
  SESSIONS.md entry explicitly flagged this as unverified.
- Deploying it caused a real live outage: `PriceWatcher` crashed 3 times in
  ~8 minutes with `AttributeError: 'NoneType' object has no attribute
  'resume_reading'` inside `websockets`' `recv()` flow control, because
  `_request_snapshot()` called `_load_kalshi_private_key()` (blocking file
  read) and `_build_kalshi_auth_headers()` (blocking RSA-PSS signing)
  synchronously inside an `async def`, on every REST call. Under real gap
  -event load this blocked the event loop long enough to miss Kalshi's WS
  ping frames within `ping_timeout=10s`; Kalshi tore down the transport,
  and the next `recv()` hit a `None` transport. This exhausted the Session
  20 restart budget (3/60min) and left the agent permanently stopped.
- Decision: removed the auth headers entirely from `_request_snapshot`.
  **Live-confirmed** after deploy: the unauthenticated `GET
  /trade-api/v2/markets/{ticker}/orderbook` call returns HTTP 200, with
  1,764 `book_snapshot_applied` events firing correctly in a ~2.5 minute
  window and zero crashes over sustained load.
- **This is a concrete instance of the project's standing "verify live
  before trusting assumptions" principle (Session 13/15/18/21/22
  precedent) applying to the project's own defensive code additions, not
  just third-party claims or ambiguous docs.** A "safe-looking" defensive
  addition (auth headers "just in case") was itself the direct cause of a
  production outage, because it was never empirically checked against the
  documented behavior it was defending against. Going forward: when docs
  already state a specific behavior (e.g. "no auth required"), treat
  deviating from that documented behavior as the thing that needs
  justification and verification — not the reverse.

### Shared aiohttp.ClientSession for REST snapshot fetches
- Replaced the per-call `async with aiohttp.ClientSession() as session:`
  pattern in `_request_snapshot` with a lazily-created, agent-level shared
  session (`PriceWatcherAgent._get_rest_session()`), closed in `stop()`.
- Decision: independent of the auth-blocking bug, unbounded per-call
  session creation is wasteful under the bursty gap-event load this path
  is designed to handle (dozens of markets can go stale in the same
  second). A single reused session avoids that overhead entirely.

### Concurrency limiter on REST snapshot calls — flagged, NOT built this session
- Live verification also surfaced 56/1,016 (~5.5%) REST snapshot requests
  hitting HTTP 429 (`too_many_requests`) during the initial post-restart
  surge, when many markets simultaneously needed recovery.
- Decision: not fixed this session — already handled safely by the
  existing failure path (429 logged as `book_reset_rest_failed`,
  `_gap_detected` stays `True`, retried on the next throttled window). Not
  a crash risk, purely an efficiency gap under restart-time bursts.
  Flagged as KNOWN DEBT for a future session to add an `asyncio.Semaphore`
  (or similar) bounding in-flight `_request_snapshot` calls — explicitly
  deferred per instruction, not urgent.

---

## 2026-07-01 — Session 22: book-reset recovery replaced with REST fetch (WS re-subscribe confirmed non-functional)

### Kalshi does not send a fresh snapshot on duplicate WS subscribe — confirmed via live capture + docs
- Session 18's `_request_snapshot` sent a WS `subscribe` message for an
  already-subscribed market, on the assumption (stated explicitly in that
  session's code comment) that Kalshi would respond with a fresh
  `orderbook_snapshot`. Session 21's temporary diagnostic instrumentation
  (unconditional per-message logging of every WS message's `type`/`id`)
  captured live traffic and found Kalshi actually responds to a duplicate
  subscribe with `{"type": "ok", "id": N}` — a plain acknowledgment, never
  a snapshot. Cross-checked against Kalshi's own WS documentation, which
  states snapshot delivery happens only on the *initial* subscribe to a
  channel, not on re-subscribing to a market already subscribed.
- This explains the regression observed going into this session:
  `book_snapshot_requested` climbed to 3,365 in an 18-minute window while
  `book_snapshot_applied` fell to **zero** in that same window (down from a
  37% apply rate measured right before the last restart) — the WS
  re-subscribe recovery mechanism could never have worked as designed; the
  Session 18 id-collision fix improved request/response correlation, but
  correlating with an ack that never carries book data doesn't recover
  anything. The book-reset recovery KNOWN DEBT item, open since Session 18,
  is retroactively explained: it was never going to work, regardless of the
  id-collision fix.
- Decision: this is the second time in this project (after Session 15's WS
  schema ambiguity) that a WS behavior assumption was wrong in a way local
  tests couldn't catch, because the tests were written against the assumed
  schema, not the real one. Applied the same discipline as Session 15:
  temporary, clearly-labeled diagnostic logging, capture real traffic,
  confirm against official docs, then act on evidence — not a fourth guess.

### Fix: REST fetch replaces WS re-subscribe for book-reset recovery
- `_request_snapshot(market_id)` now makes a direct `aiohttp` GET to
  `https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}/orderbook`
  (RSA-PSS auth headers reused via the existing `_build_kalshi_auth_headers`
  helper, matching the pattern already used by
  `_fetch_active_kalshi_markets` — simpler and consistent with the rest of
  the file's REST calls, rather than adding a second unauthenticated-call
  code path for a single endpoint) and calls `book.apply_snapshot(...)`
  directly with the parsed `orderbook_fp.yes_dollars`/`no_dollars` levels
  (string values cast to float; NO bids still derive YES asks at `1-p`,
  matching the WS snapshot schema's existing convention).
- The 10s per-market throttle and the "client must exist and be connected"
  guard are unchanged — both are still meaningful for a REST-based
  recovery path (avoid hammering the endpoint on repeated gap events; no
  point fetching if the WS connection that would resume normal delta flow
  is itself down).
- **No sequence number in the REST response.** `apply_snapshot` is called
  with `seq=0` (sentinel). `OrderBook.apply_delta`'s gap check —
  `if seq != self.sequence + 1 and self.sequence != 0` — short-circuits on
  `self.sequence == 0`, so the very next delta is accepted regardless of
  its own seq value, and `self.sequence` naturally realigns to whatever
  Kalshi sends next. No special-casing needed downstream; verified this
  reasoning against the actual gap-check code before relying on it, rather
  than assuming a sentinel would "just work."
- On any REST failure (non-200, network error, timeout — a single
  `try/except Exception` wraps the whole call), logs `book_reset_rest_failed`
  at warning and returns without calling `apply_snapshot` — `_gap_detected`
  stays `True`, so the next delta on that market retriggers a throttled
  retry rather than crashing `_kalshi_connection_loop`.
- The `_snapshot_request_id_counter` from Session 18 is kept (not removed)
  but is no longer load-bearing for this recovery path, since no WS message
  is sent from `_request_snapshot` anymore — left in place per explicit
  instruction, with a comment explaining why, rather than ripping out
  otherwise-harmless code mid-fix.
- **Status: NOT yet confirmed live.** Unit-tested (4 new tests: REST
  success applies snapshot + clears gap, book auto-created if missing,
  non-200 leaves gap state, network error leaves gap state) plus the
  existing throttle/no-client tests rewritten against the new REST call
  shape. Not yet exercised against the real Kalshi REST endpoint on the
  VPS. Next session must deploy and confirm `book_snapshot_applied`
  (renamed conceptually — same log key, now fired from the REST path)
  actually climbs again, and that `book_reset_rest_failed` rate stays low.

### Session 21 diagnostic instrumentation reverted
- All four `TEMPORARY DIAGNOSTIC` blocks added in Session 21
  (`kalshi_raw_msg_diag` in `_route_message`, `_diag_msg_type_counts`,
  `_diag_summary_loop`, `kalshi_raw_msg_diag_sent` in `_request_snapshot`)
  removed now that the data they existed to capture has been captured and
  used to diagnose the root cause above — consistent with the Session 15
  precedent of not leaving temporary diagnostic logging in the codebase
  permanently. Confirmed via `grep` that zero `DIAGNOSTIC`/`diag` references
  remain in `agents/floor/price_watcher.py`.

---

## 2026-07-01 — Session 20: Telegram feed-down alert + capped runner-level auto-restart

### RESOLVED: agent-level restart after stop_after_attempt(10) exhaustion (was open, flagged in Session 19)
- Session 19 flagged a real failure-recovery philosophy question: once
  `PriceWatcher`'s internal `@retry` (`stop_after_attempt(10)`) is exhausted,
  should the agent stay dead until a manual `systemctl restart karbot`, or
  should something restart it automatically?
- **Operator decision: task-level auto-restart with a capped budget.**
  `karbot_runner.py`'s supervision layer now restarts a crashed
  `PriceWatcher` task after a fixed 30-second delay, up to 3 restarts within
  any rolling 60-minute window. If that budget is exceeded, auto-restart
  stops permanently for the affected agent and a CRITICAL Telegram alert
  fires ("AUTO-RECOVERY EXHAUSTED") instead of continuing to retry silently
  forever — bounding the failure mode instead of choosing between "never
  restart" and "restart forever, possibly masking a real outage."
- Rationale: an unbounded auto-restart risks silently hiding a genuine,
  longer-lived Kalshi-side or credential-side outage (the operator would
  never know the feed had been down for hours if the runner just kept
  quietly relaunching); a hard cap converts "silent infinite retry" into
  "bounded retry, then a loud, distinct alert demanding human attention" —
  consistent with the project's existing pattern of capped budgets +
  circuit-breaker-style Telegram alerts elsewhere (e.g. RegulatoryIntelligence's
  daily-cap/circuit-breaker Telegram alerts).
- Decision: implemented as a general-purpose `_run_supervised_with_restart()`
  function (agent name + coro factory + bus + three configurable params),
  not a `PriceWatcher`-specific hack — reusable for other agents in the
  future — but wired only to `PriceWatcher` this session; every other
  agent's supervision is unchanged (`_run_supervised()`, untouched).
- Configurable via `KarbotConfig.system.agent_restart_delay_seconds` (30),
  `agent_restart_max_count` (3), `agent_restart_window_minutes` (60) — not
  hardcoded, so the operator can retune without a code change.
- **Status: NOT confirmed live.** Unit-tested (3 new tests) against a
  simulated crashing agent; not yet exercised against a real Kalshi outage
  or a real crash on the VPS. See SESSIONS.md Session 20 for the
  verification plan.

### FeedHealthEvent-driven Tier 1 Telegram alert on feed down/recovery
- Added a `FeedHealthEvent.error: str = ""` additive field and a
  `TelegramNotificationAgent._handle_feed_health` subscriber that alerts on
  a connected→disconnected or disconnected→connected transition for
  `platform="kalshi"` only, tracking last-known state per platform to avoid
  re-alerting on every repeated `connected=False` event during one
  continuous outage.
- Decision: routed entirely through the existing event-bus
  publish/subscribe pattern (`FeedHealthEvent` → `TelegramNotificationAgent`
  subscription), not a new direct call from `price_watcher.py` into
  Telegram — consistent with "event-bus architecture is canonical" from
  CLAUDE.md.
- Decision: this alert bypasses `config.telegram.enabled`-gated tier
  routing the same way existing Tier 1 handlers (`_handle_leg_failure`,
  `_handle_regulatory_alert`) already do, and is explicitly designed to keep
  bypassing any future mute/unmute feature (not yet built) — a dead price
  feed should never be silenced.
- **Status: NOT confirmed live.** Unit-tested (4 new tests); not yet
  exercised against a real Kalshi WS disconnect/reconnect on the VPS.

---

## 2026-07-01 — Session 19: structlog-incompatible before_sleep_log crashed WS reconnect retry

### Custom before_sleep callback over any structlog/tenacity compatibility shim
- tenacity's `before_sleep_log(logger, "WARNING")` assumes a stdlib
  `logging.Logger` and calls `logger.log("WARNING", ...)` — a string level.
  structlog's `BoundLogger.log()` expects an int and does
  `if level < min_level`, raising `TypeError` on the very first retry
  attempt. This meant `@retry` on `_kalshi_connection_loop` had never
  actually retried successfully — confirmed live via a 2026-06-30 07:42 UTC
  Kalshi WS disconnect that killed the price feed for ~6 hours with zero
  retry attempts logged.
- Decision: wrote a small module-level `_log_before_sleep(retry_state)`
  function calling `log.warning("kalshi_reconnect_retry", attempt=...,
  wait_seconds=...)` directly, passed as `before_sleep=_log_before_sleep`.
  Did not reach for a generic "make structlog look like stdlib logging"
  adapter — the callback tenacity needs is a single-argument function taking
  `RetryState`, and structlog's own API surface (keyword-based `.warning()`)
  is a better fit than shimming compatibility with the stdlib-oriented helper.
- Rationale: this is the same category of bug as any interface mismatch
  between two libraries with different logging conventions — the safest fix
  is a small adapter function scoped to exactly this call site, not a
  project-wide compatibility layer that could mask other, different
  mismatches. Confirmed via direct code inspection of both tenacity's
  `before_sleep_log` source and structlog's `BoundLogger.log()` source (not
  just inferred from the live symptom) — a stronger verification posture
  than the still-unconfirmed Session 18 id-collision hypothesis below.

### Agent-level restart after stop_after_attempt(10) exhaustion — NOT decided, flagged for operator
### → RESOLVED Session 20: see "RESOLVED: agent-level restart..." entry above.
- Once `stop_after_attempt(10)` is genuinely exhausted, `PriceWatcher` dies
  permanently (`tenacity.RetryError` propagates through `_run_supervised` in
  `karbot_runner.py`) and requires a manual `systemctl restart karbot`.
  Documented via a `NOTE` comment above `_kalshi_connection_loop`, not
  resolved. Two live options: (1) accept as designed — operator is
  paged/alerted and restarts manually; (2) `_run_supervised` itself restarts
  a dead `PriceWatcher` after a cooldown.
- Decision: explicitly deferred. This is a failure-recovery philosophy
  question (acceptable downtime, whether Kalshi-side transient outages
  should self-heal without human intervention) — not a code-correctness bug,
  and not something to decide unilaterally per session instructions. See
  SESSIONS.md Session 19 for full framing; needs operator input before any
  runner-level restart logic is built.

---

## 2026-06-30 — Session 18: book-reset id collision fix (leading hypothesis, unconfirmed live)
### → SUPERSEDED Session 22: WS re-subscribe recovery confirmed non-functional (Kalshi acks with "ok", never a fresh snapshot); replaced with REST fetch. See "book-reset recovery replaced with REST fetch" entry above.

### Unique per-call WS correlation id, not a hardcoded 99
- `_request_snapshot(market_id)`'s WS re-subscribe message used `"id": 99` for
  every call (Session 17 follow-up 3). VPS logs from 2026-06-30 showed a
  10.2% `book_snapshot_requested` → `book_snapshot_applied` completion rate
  (23,412 vs 2,380). Leading hypothesis: Kalshi's WS server correlates
  responses to requests via `id`; concurrent resets across dozens of markets
  within the same second sharing id=99 caused most responses to be dropped
  or misattributed to the wrong market.
- Decision: added `self._snapshot_request_id_counter`, incremented per call,
  used as the `id` value. No lock: single event loop, single call site
  (inside `_handle_kalshi_delta`, invoked serially by the WS message loop).
- **Status: NOT confirmed live.** This is a reasoned hypothesis from the
  completion-rate data and the known gap-event clustering pattern, not a
  captured/confirmed root cause (unlike the Session 15 precedent of
  verifying against real WS traffic). Do not treat the book-reset recovery
  KNOWN DEBT item as resolved until next session's VPS log comparison
  confirms the completion rate actually improves. See SESSIONS.md Session 18
  for the full verification plan.

### book_needs_reset log demoted to debug (noise only, not correctness)
- This log fired at warning level on every delta received while a market
  awaited snapshot recovery, not once per gap episode — 2.17M warning lines
  in a single day on the VPS, burying real signal.
- Decision: changed this specific call site to debug. Left
  `sequence_gap_detected` in `OrderBook.apply_delta()` at warning — it
  already fires only once per gap (False→True transition) and is the
  correct signal-bearing log for this condition.

---

## 2026-06-30 — Session 17: TradeResolvedEvent wiring, real-time DB INSERT, book reset recovery

### S1 P&L is deterministic at fill time — no Kalshi resolution polling needed
- `TradeResolvedEvent.realized_pnl` is computed by `PaperExecutor` as
  `(opp.net_profit_pct / 100) * approved_size` — the same formula as
  `expected_pnl_usd`. For S1 (binary yes/no arb), P&L is locked at fill
  time because both legs are purchased at prices that sum to less than $1,
  and both pay out exactly $1 at settlement regardless of outcome.
- Decision: `ComplianceOfficer.handle_trade_resolved` uses the P&L value from
  the event directly rather than polling Kalshi's `/markets/{ticker}` resolution
  API. No settlement polling added to the S1 path.
- Rationale: polling adds API risk surface and complexity for zero correctness
  benefit on S1. Any future strategy (e.g. S4 settlement arb) where P&L
  genuinely depends on the Kalshi resolution outcome should design its own
  polling path from scratch when that strategy is actually specced.

### CSV atomic read-modify-write on trade resolution
- `_update_csv_trade_resolved()` reads all rows, modifies matched rows in
  memory, writes to `.csv.tmp`, then `os.replace()` atomically.
- Decision: atomic temp-file + replace over in-place overwrite or append.
- Rationale: `kalshi_trades.csv` is the IRS tax record — a crash mid-write
  that corrupts it is a compliance problem. `os.replace()` is atomic at the
  filesystem level; the worst case is the old file is unchanged (no partial
  write). Append-only approaches don't work here because resolution updates
  existing rows rather than adding new ones.

### Real-time DB INSERT on TradeExecutedEvent (not nightly batch)
- `_insert_db_trade_executed()` runs `INSERT OR IGNORE INTO trades` immediately
  in `handle_trade_executed`, before the CSV write returns.
- Decision: real-time INSERT over relying on `ReflectionAgent`'s nightly
  cycle or a separate bootstrap script.
- Rationale: the nightly cycle only reads; it never inserts. The DB was always
  empty during the day because no INSERT path existed. Real-time INSERT ensures
  `compliance.db` stays in sync with `kalshi_trades.csv` throughout the day and
  makes intraday DB queries (e.g. operator status checks) return live data.
- `INSERT OR IGNORE` provides idempotency: if `TradeExecutedEvent` is delivered
  more than once (e.g. event replay), the duplicate is silently dropped rather
  than erroring. Requires `UNIQUE` constraint on `trade_id` — added in
  `_ensure_log_files()` bootstrap schema; live VPS DB (Session 14) needs a
  one-time migration before live trading:
  `CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_trade_id ON trades(trade_id);`

### Book reset recovery via WS re-subscribe — deployed but NOT confirmed live
- `_request_snapshot(market_id)` sends `{"cmd": "subscribe", "channels":
  ["orderbook_delta"], "market_tickers": [market_id]}` over the existing WS
  on sequence gap detection, throttled 10s/market.
- Decision: WS re-subscribe over REST API snapshot call or forced reconnect.
- Rationale: Kalshi's own WS docs imply a duplicate subscribe triggers a fresh
  `orderbook_snapshot` response. No REST endpoint was needed, and a forced
  reconnect would disrupt all ~1200 subscribed markets to recover one. The
  re-subscribe approach is surgical and connection-preserving.
- **Caveat**: `book_snapshot_applied` has NOT been observed in VPS logs after a
  `book_snapshot_requested`. It is unknown whether Kalshi actually responds to
  duplicate subscribes in practice. This must be verified live next session
  before treating the corrupt-book / P&L-inflation problem as solved.

---

## 2026-06-28 — Session 15: Kalshi price-flow chain (volume filter, mve_filter, WS schema)

### Verify each layer against the live API/wire before declaring it fixed
- Three independent, compounding bugs were found in the Kalshi price-flow
  path this session, each invisible until the previous layer was fixed
  AND re-verified live (not just locally tested): (1) a nonexistent
  `volume_24h` field plus broken pagination in `_fetch_active_kalshi_markets()`;
  (2) even after fixing (1), a live deploy showed `count=0` — Kalshi's
  unfiltered catalog is dominated by 12,000+ consecutive zero-volume
  multi-variable-event markets, requiring the documented `mve_filter=exclude`
  param; (3) even after fixing (1) and (2), real markets were subscribing
  but zero order book messages were ever processed — the WS snapshot/delta
  handlers assumed a message schema that doesn't exist on the wire.
- Decision: at each layer, verified against the actual live system
  (direct REST queries with real credentials, a live 12,000-market scan,
  captured raw WS traffic) rather than trusting the previous fix's local
  test pass or the immediately-visible log line. Deployed and checked
  live logs after each fix before moving to docs updates.
- Rationale: same category of risk as the Session 13 (Kalshi domain/signing)
  and Session 14 (task-brief schema) decisions below — local tests and
  docs can both be wrong about the live system's actual current behavior,
  and a wrong assumption here is high blast radius (CLAUDE.md flags order
  book reconstruction as code where "a bug here silently corrupts ALL
  downstream pricing").

### Kalshi WS orderbook schema is ambiguous in official docs on two
### correctness-critical points — resolved empirically, not by guessing
- Kalshi's WS docs (docs.kalshi.com/websockets/orderbook-updates) name the
  real fields (`yes_dollars_fp`, `no_dollars_fp`, `price_dollars`,
  `delta_fp`, `side`) but do not state whether `yes/no_dollars_fp` are
  both bid-only books or whether `delta_fp` is an absolute size vs. a
  relative change to apply.
- Decision: added temporary, clearly-labeled diagnostic logging
  (`kalshi_raw_msg_diag`), deployed it, captured real live traffic, then
  reverted the diagnostic once both questions were answered from the
  actual data — rather than guessing from the ambiguous docs or from
  general Kalshi market-microstructure assumptions.
- Resolution: confirmed both `_dollars_fp` arrays are resting-bid-only
  books (NO bid at price `p` ⇒ derived YES ask at `1-p`, consistent with
  pre-existing `to_price_event()` math already in the codebase); confirmed
  `delta_fp` is a RELATIVE change via a live matched `+523.00`/`-523.00`
  pair on ticker `KXCS2GAME-...-AIM` when a resting order moved from
  price 0.02 to 0.08 — only explicable as incremental deltas.
- `OrderBook.apply_delta()`'s signature/semantics were changed accordingly
  (from "set absolute size at price" to "add relative delta, clamp at 0,
  remove level at/below 0") rather than working around the mismatch at
  the call site — the discrepancy was in what the method itself assumed
  about the data, not in how callers used it.

### Added a permanent low-noise live-verification log instead of repeating
### ad-hoc diagnostics a third time
- After two rounds of temporary diagnostic logging (raw API dumps via
  Bash probes, then raw WS message logging) to resolve this session's
  bugs, added one permanent one-shot `kalshi_first_price_update` INFO log
  (fires once per platform on the first successfully-applied delta).
- Rationale: this and future sessions need a real, cheap, always-available
  signal that the price pipeline is alive, rather than re-deriving
  ad-hoc diagnostic logging from scratch each time something needs live
  verification. Deliberately one-shot (not per-message) to stay low-noise
  in production.

---

## 2026-06-27 — Session: VPS deployment verification, compliance.db, AsyncAnthropic migration

### Verify the task brief against the code, not just against the world
- The handoff brief for this session specified `data/compliance.db` with a
  `created_at`/`opened_at`-based schema, and named
  agents/research/regulatory_intelligence.py as needing the AsyncAnthropic
  fix. Both were wrong: `ReflectionAgent` hardcodes `data_dir = Path("logs")`
  and queries `status`/`timestamp`/`resolved_at` columns plus an
  `audit_trail` table not in the proposed schema; `regulatory_intelligence.py`
  already used `AsyncAnthropic` correctly, while the real synchronous-client
  debt was in market_analyst.py and reflection.py.
- Decision: read the actual consuming code (reflection.py's queries, the
  grep for `anthropic.Anthropic`) before building to the brief's spec, and
  built/fixed what the code actually needed instead.
- Rationale: this is the same category of risk as Session 13's "verify
  external claims against the live API" decision, just applied to an
  internally-authored task brief instead of a web search — a wrong schema
  or a fix applied to the wrong file is silently useless at best.

### compliance.db schema (current, as of this session)
- Location: `logs/compliance.db` (not `data/compliance.db` — matches
  `ReflectionAgent.__init__`'s hardcoded `data_dir`)
- Tables: `trades` (status, timestamp, resolved_at, realized_pnl, strategy,
  market_id, platform, plus additive columns: trade_id, opportunity_id,
  fee_paid, etc.), `rejections` (reason, timestamp), `audit_trail`
  (event_type, entry_json, timestamp) — schema built to match exactly what
  `ReflectionAgentImpl`'s nightly cycle queries

---

## 2026-06-27 — Session: Kalshi API migration (domain + signing algorithm)

### Verify external claims against the live API before changing code
- A third-party (web-search-sourced, unconfirmed) suggestion proposed two
  simultaneous changes to Kalshi auth: a domain move to
  `api.elections.kalshi.com` and switching from RSA-PKCS1v15 to RSA-PSS
  signing. Only the domain change had first-party evidence at the time
  (a live 401 from Kalshi's own server stating the API had moved).
- Decision: apply and verify one change at a time rather than trusting the
  bundled claim wholesale.
  1. Applied the URL fix alone, left signing untouched.
  2. Live-tested PKCS1v15 against the new domain — got a real
     `401 INCORRECT_API_KEY_SIGNATURE`, which is a signature-format
     rejection (not a routing error), independently confirming something
     about the signing scheme really had changed.
  3. Only then tried RSA-PSS, and only trusted it after a live `200`
     against `/trade-api/v2/portfolio/balance` using the actual function
     in agents/floor/price_watcher.py (not a disposable test script).
- Rationale: an AI-generated fix bundling two unverified claims together is
  exactly the situation where, if you blindly apply both, you can't tell
  afterward which change (if either) was actually necessary or correct —
  and a wrong signing scheme on a real trading account's credentials is not
  a place to guess. Treat URL/domain "moved" errors and signature rejection
  errors as distinct failure modes requiring distinct evidence.
- This pattern (isolate one variable, get first-party evidence, then act)
  should apply to any future externally-sourced "fix" affecting auth,
  credentials, or money movement.

### Kalshi API endpoint + signing scheme (current, as of this session)
- Base/WS domain: `api.elections.kalshi.com` (was `trading-api.kalshi.com`)
- Signing: RSA-PSS + SHA-256, `salt_length=PSS.MAX_LENGTH` (was PKCS1v15)
- See SESSIONS.md Session 13 for full verification trail

---

## 2026-05-26 — Session: Security hardening + TradeResolvedEvent

### Secrets management pattern (project-wide, permanent)
- SecretsConfig dataclass loads all credentials from environment variables only
- config.yaml moved to .gitignore; config.yaml.example committed in its place
- .env.example documents all required environment variables with setup instructions
- python-dotenv added: local dev uses .env file; VPS uses systemd EnvironmentFile
- load_dotenv() in karbot_runner.py is a no-op when real env vars already set
- Agents access credentials via config.secrets.* exclusively — never os.environ directly

### TradeResolvedEvent wiring
- PaperExecutor now emits TradeResolvedEvent after paper_resolution_delay_seconds (default 300)
- Full paper P&L cycle now closes: trade opens → capital deploys → trade resolves →
  capital frees → P&L accumulates in _total_capital
- 30-day paper trading clock starts after this session (2026-05-26, target complete 2026-06-25)

### Known remaining debt
- correlation_score in PositionSnapshot permanently 0.0 — Phase 3 item
- execution/engine.py legacy path — deferred until after live executor is built and tested

---

## 2026-05-26 — Session: PositionTracker Phase 2

### What was wired
- PositionTracker now subscribes to TradeExecutedEvent, TradeResolvedEvent, LegFailureEvent
- deployed_capital_usd, open_positions, daily_trades, daily_pnl all update in real time
- Risk Gate capital checks now enforce against real deployed capital, not permanent zero

### EventBus tiebreaker fix (from prior session — adding to decisions log)
- Pre-existing bug: same-priority events in PriorityQueue caused heapq to compare
  event dataclasses, raising TypeError
- Fixed with (priority, seq, event) 3-tuple in core/events.py
- Production-critical: would have caused unpredictable crashes under live trading load
- Caught by test suite before any live deployment

### Known remaining gap
- correlation_score in PositionSnapshot is permanently 0.0 — Phase 3 item
- TradeResolvedEvent wiring completed in Session 9 (Security + TradeResolvedEvent)
  via PaperExecutor asyncio.create_task() — full paper P&L cycle now closes

---

## 2026-05-26 — Session: Regulatory Intelligence Agent

### Model selection
- Claude Sonnet (claude-sonnet-4-6) selected over Haiku for regulatory interpretation
- Rationale: quality matters for compliance decisions; cost still negligible at 10 calls/cycle

### Cost controls
- Per-cycle cap: 10 calls (configurable via regulatory_ai_calls_per_cycle)
- Daily hard cap: 50 calls/day (configurable); hit → stop + Telegram alert
- Circuit breaker: 20 calls in 10 minutes (configurable) → immediate stop + Telegram alert + runner restart required
- Monthly spend estimator: logged daily at 00:00 UTC reset
- Overflow queue: items exceeding per-cycle cap held for processing in the next cycle — not dropped

### Operator control philosophy
- Urgency 5 pauses new trade approvals — AI recommends, operator decides
- Clear phrase in config.yaml (regulatory_clear_phrase) — operator sends via Telegram to resume
- Circuit breaker requires runner restart — not clearable via Telegram by design

### TelegramPermissionResponseEvent: response_text field added
- Added response_text: str = "" to TelegramPermissionResponseEvent
- TelegramAgent now always publishes TelegramPermissionResponseEvent with response_text for every operator message (not just when a pending request exists)
- This allows RegulatoryIntelligenceAgent to detect the clear phrase without requiring a formal permission request cycle
- Existing behavior for FIFO permission resolution unchanged — additive only

### EventBus priority queue tiebreaker
- Fixed pre-existing bug: asyncio.PriorityQueue with (priority, event) tuples fails when two events have the same priority (heapq tries to compare event objects)
- Fix: use 3-tuple (priority, seq, event) where seq is a monotonic counter
- Exposed by Python 3.14 but would have failed in earlier versions too whenever same-priority events were enqueued simultaneously
- No behavior change; FIFO ordering preserved within same priority level

### ComplianceOfficer polling loop removed
- Polling was: fetch CFTC RSS + Federal Register every 6h, keyword scan, log to file
- Replaced by: RegulatoryIntelligenceAgent does the same fetching with AI interpretation
- ComplianceOfficer now subscribes to RegulatoryAlertEvent and logs AI-assessed alerts to compliance_actions.jsonl
- regulatory_alerts.txt removed (was written by ComplianceOfficer; no longer needed)

---

## 2026-05-26 — Session: Telegram notification agent

### Polling vs webhook
- Polling selected over webhook
- Rationale: VPS does not expose public inbound ports (dashboard is local-only per architecture doc). Polling is consistent with that posture, requires zero additional infrastructure, and handles the operator permission use case adequately given 3-second polling intervals are fast enough for human response times.
- Implementation: getUpdates polling every 3 seconds, last_update_id tracked across calls

### Operator reply resolution for permission requests
- Single-operator simplification: any "yes"/"no" reply resolves the oldest pending permission request (FIFO)
- Rationale: Only one operator. Multi-request concurrency is not a real scenario in Phase 1.
- Revisit when: Regulatory Intelligence Agent generates concurrent permission requests (unlikely but possible)

### New event types added to core/events.py
- `RegulatoryAlertEvent`: published by ComplianceOfficer when regulatory keyword match found (not yet wired in compliance.py — TelegramAgent subscribed and ready)
- `TelegramNotificationEvent`: any agent can publish to request a Telegram message (tier 1=critical, 2=trade-level, 3=digest)
- `TelegramPermissionRequestEvent`: any agent can publish to request operator permission with timeout + default
- `TelegramPermissionResponseEvent`: TelegramAgent publishes on operator reply or timeout; `source` field = "operator" or "timeout"

### TelegramConfig: credentials from env vars only
- TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID never stored in config.yaml
- enabled=False default — must be explicitly opted in
- graceful degradation: if enabled=True but env vars missing, logs warning and drops messages silently

---

## 2026-05-26 — Session: Paper trading verification, debt cleanup, next phase sequencing

### Telegram architecture decision
- Option A selected: build Telegram as a standalone notification layer (its own BaseAgent-conforming agent) before building the Regulatory Intelligence Agent
- Rationale: Telegram will be needed system-wide (health alerts, trade notifications, operator permission requests); building it inline inside one agent creates rework
- Do not build Telegram notification inline inside any agent

### Project principles (standing, apply to all future sessions)
- Always favor best practice and quality over speed
- Spec here before anything goes to Claude Code
- Lead on sequencing — don't ask what to do next, tell the operator what we're doing and why
- Paper mode must behave identically to live mode — no paper mode bypasses in business logic

### Next two items in sequence
1. Standalone Telegram notification layer (BaseAgent-conforming, event-bus-driven)
2. Regulatory Intelligence Agent (uses Telegram layer; Claude API for document interpretation; replaces keyword scanning in ComplianceOfficer)

---

## 2026-05-25 — Session: Requirements, Config, Market Data, Agent Wiring

### What was fixed this session

**1. requirements.txt restored**
- Was stripped to 2 lines (`aiohttp` and bare `asyncio`).
- Restored to full dependency list: aiohttp, pydantic, websockets, pyyaml,
  python-json-logger, structlog, tenacity, aiosqlite, pytest, pytest-asyncio,
  black, flake8, anthropic.
- Added `structlog` (required by core/events.py), `tenacity` (price_watcher.py
  reconnection logic), and `aiosqlite` (reflection agent's trade database) — these
  were in the original codebase but missing from the stripped requirements.

**2. core/config.py — Phase 1 defaults fixed**
- Kalshi was incorrectly set to `enabled: False`, Polymarket to `enabled: True`.
  This is the inverse of Phase 1 requirements. Fixed.
- Added `polymarket_ws_enabled: False` as a top-level config key.
  This is the flag read by `data/market_data.py` to gate Polymarket data fetches.

**3. data/market_data.py — fetch order and phase gate**
- Previously fetched Polymarket first, then Kalshi. Reversed to Kalshi first.
- Polymarket DataSource is now only instantiated when `polymarket_ws_enabled=True`.
  When False (Phase 1 default), the Polymarket source object is never created and
  never called, preventing any accidental Polymarket data path activation.
- `get_market_details()` similarly tries Kalshi first, only falls back to Polymarket
  if explicitly enabled.

**4. karbot/core/ package created**
- The agent layer (`agents/floor/`, `agents/research/`, `agents/management/`) imports
  from `karbot.core.config` and `karbot.core.events`, but this package did not exist.
- Created `karbot/core/__init__.py`, `karbot/core/events.py` (re-exports all event
  types from `core.events`), and `karbot/core/config.py` (full typed `KarbotConfig`
  dataclass with sub-configs for system, data_feeds, capital, risk, strategies,
  and intelligence).
- `KarbotConfig.__post_init__` enforces Phase 1 invariants at instantiation time:
  `polymarket_ws_enabled=True` with `phase=1` raises `ValueError`.
  `s2_cross_platform_enabled=True` with `phase=1` raises `ValueError`.
- `RiskConfig.__post_init__` enforces hard limits: any value exceeding the absolute
  constants (ABSOLUTE_MAX_PER_TRADE_PCT=5%, ABSOLUTE_MAX_LOCKED_PCT=40%, etc.)
  raises `ValueError` at startup.

**5. Agent __init__.py files added**
- `agents/floor/__init__.py`, `agents/research/__init__.py`, `agents/management/__init__.py`
  were missing, preventing Python from treating these as packages.

### What was explicitly NOT changed this session

**execution/engine.py — monolithic orchestrator (flagged, not touched)**

This file calls `analyzer.analyze_markets()`, `strategy_manager.execute_strategies()`,
and `trader.execute_trades()` directly — bypassing the event bus. This is wrong for
the intended architecture (event-bus-driven agents, no direct coupling).

However, this is a large refactor. Touching it without also wiring up the agents,
updating `main.py`, and testing the cycle end-to-end would break what currently runs.

Recommended incremental approach:
1. Keep `execution/engine.py` as-is until the agent layer is ready to replace it.
2. The new entry point should be a `karbot_runner.py` (or extend `karbot/main.py`)
   that instantiates `KarbotConfig`, `EventBus`, then starts each agent.
3. Once the agent-based cycle (PriceWatcher → ArbScanner → RiskGate → Executor)
   is confirmed working end-to-end in paper mode, remove the old engine.

### Architecture note

There are now two execution paths:
- **Old path**: `main.py` / `karbot/main.py` → `execution/engine.py` → direct calls
- **New path** (agents): `agents/floor/`, `agents/research/`, `agents/management/`
  → publish/subscribe via `karbot.core.events.EventBus`

The new path is the correct target architecture. The old path should not be extended.

### Known remaining work

- `execution/engine.py` needs event-bus refactor (see above — do incrementally)
- `karbot/main.py` should be updated to start agents instead of the old engine
- `agents/floor/arb_scanner.py` S2 check uses `config.capital.phase >= 2` which
  correctly gates cross-platform — already working
- Compliance officer (`compliance/officer.py`) needs to be wired to the event bus
- IRS dual-track logging (Kalshi = ordinary income, Polymarket = capital gains) is
  not yet implemented — needed before any live trading

---

## 2026-05-22 — Initial session

### What was built
- Complete multi-agent trading system framework for prediction markets
- Modular architecture with core, execution, data, intelligence, strategies, trading, and monitoring components
- Configuration system with defaults
- Documentation files (README, DOCUMENTATION, ARCHITECTURE)
- Example usage script
- Testing framework
- Git repository setup with proper remote

### Key architectural decisions
- Multi-agent architecture with specialized agents for different functions
- Event-bus-driven inter-agent communication (core/events.py)
- Configuration-driven system with defaults
- Separation of concerns between data handling, intelligence, strategy execution, and trading

### What was explicitly ruled out
- Actual API integrations with specific prediction markets — left for future
- Real trade execution capabilities — framework structure first
- Advanced risk management — built as foundation

### Current known issues at end of session
- Tests not fully implemented
- No actual market data APIs integrated
- No real trading execution implemented
- Limited to paper trading mode functionality

### What the next session should tackle
- ~~Restore requirements.txt~~ (done 2026-05-25)
- ~~Fix data/market_data.py Polymarket-first bug~~ (done 2026-05-25)
- ~~Wire karbot.core package for agents~~ (done 2026-05-25)
- Implement karbot/main.py agent runner to replace old execution engine
- Add compliance officer event bus wiring
- IRS dual-track logging implementation
- Paper trading end-to-end test
