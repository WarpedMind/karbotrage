# `canary/` — S5a/S5b passive arbitrage canary

Detect and log. Never trade.

This process polls Kalshi's public REST API, looks for multi-leg positions whose
payout is guaranteed regardless of outcome, and appends what it finds to
`logs/basket_candidates.jsonl`. It publishes no events, sizes no positions and
places no orders. Its value is **frequency over weeks**: Session 29 checked
S5a/S5b in a single snapshot, found nothing, and correctly noted that a snapshot
cannot rule out rare windows during volatility or thin off-hours trading. This is
the instrument that answers that question.

```bash
karbotrage_env/bin/python -m canary.run_canary --once
karbotrage_env/bin/python -m canary.run_canary --interval-seconds 300
```

## The two strategies

**S5a — event basket.** For an event's N markets:

| basket | buy | pays | requires |
|---|---|---|---|
| YES | YES on all N at ask | ≥ $1 (one leg must win) | exhaustive |
| NO | NO on all N at ask | ≥ $(N−1) (at most one leg wins) | mutually exclusive |

**S5b — pairwise structure.** Within an event, from strike arithmetic:

| relation | buy | pays | worst case |
|---|---|---|---|
| A ⇒ B (A's interval inside B's) | YES(B) + NO(A) | ≥ $1 | A true → $1 + $0 |
| A, B disjoint | NO(A) + NO(B) | ≥ $1 | one true → $0 + $1 |

All four are the same evaluator (`economics.evaluate_basket`) at different
arities, priced at the **ask**, with ceil'd per-order fees × N legs and a size
capped by the shallowest leg.

## Traps, all confirmed live on 2026-08-02

**The NO leg's depth is `yes_bid_size_fp`.** There is no `no_ask_size_fp` field.
A NO ask is a resting YES bid at `1 − price`, so the quantity behind it is the
YES bid size. Verified in both directions against `/markets/{t}/orderbook`:
derived `yes_ask` = 1 − no_bid with depth = no_bid_qty, derived `no_ask` =
1 − yes_bid with depth = yes_bid_qty, matching the snapshot exactly. Reading the
same-named field for both sides is the Session 26 bug class.

**The bulk snapshot is stale within seconds.** Read back-to-back against the
order book it agreed 16/16. Held ~10 seconds while other requests ran, an
actively traded market moved underneath it (`KXMLBSPREAD-…-CWS6`: yes_bid
0.10 → 0.14, size 3 → 2071). A sweep takes ~45 seconds, so **every candidate is
re-priced leg by leg from `/orderbook` before it is logged as real.** Candidates
that evaporate are kept as `vanished_on_recheck` — that survival rate is itself
the measurement, separating "real resting arbitrage" from "our view of the book
is noisy".

**One event can hold two different metrics.** `KXMLBSPREAD-26AUG021340CWSTB`
puts eight `greater` markets in one event covering Tampa Bay's winning margin
*and* Chicago's, at overlapping strikes (`TB4` and `CWS4` both at
`floor_strike=3.5`). Pure interval arithmetic "proves" that Tampa Bay winning by
4+ implies Chicago winning by 3+. Measured on the settled record: **2,267
violations**, series disqualified.

**`less` carries its threshold in `cap_strike`, `floor_strike` is null** — the
opposite convention from `greater`. Session 31 shipped a bug here that removed
the low tail of every ladder from a validation still printing 100%.

**`structured` is not one thing.** With a `floor_strike` it is a threshold
("2+ RBIs"); without one it is categorical ("Los Angeles D wins"). Intervals
derived from the former are marked `inferred` and barred from disjointness
claims — a wrong inference cannot create a false implication between two upper
rays, but it could create a false disjointness against a bounded interval.

**Some events settle on neither YES nor NO — and it turns out not to matter.**
Kalshi finalizes a postponed game or an unplayed match as `result: "scalar"`,
`status: "finalized"`, on every leg: **0.7% of KXMLBGAME events and 4.1% of
KXATPMATCH events.** Kalshi's own `rules_secondary` says such a market *"will
resolve to a **fair price** in accordance with the rules"* — neither a refund at
cost nor a zero, which were the only two possibilities considered when this was
first flagged. The real question is whether those fair prices preserve the
sum-to-one invariant both baskets rest on.

They do. Every leg carries `settlement_value_dollars`, and across **243
scalar-settled events in 8 series (236 two-leg, 7 three-leg), all 243 sum to
exactly $1.00** — zero violations, zero unverifiable. So a YES-basket still pays
`Σ settlement = $1` and a NO-basket still pays `Σ(1 − settlement) = $(N−1)`.
Checked per series in `qualify.scalar_sum_to_one` rather than assumed globally:
a violation, or a cancellation whose values can't be read, disqualifies that
series' baskets — both payouts are functions of `Σ settlement`, so both fail
together.

## Structure proposes, history disposes

Interval arithmetic only *generates* candidate relations. A relation is usable
only if the series' real settled record has never once violated it
(`qualify.py`). Verdicts are `confirmed` / `refuted` / `insufficient_evidence`,
and **zero tests is never `confirmed`** — a vacuous pass is the exact shape of
Session 31's bug, a validation reporting success over a sample it had quietly
emptied.

Qualification is at series granularity, which is coarse: one mixed-metric event
disqualifies a series even for its well-behaved pairs. That is deliberate. A
false positive manufactures a confident stream of fake arbitrage; a false
negative costs coverage in a process whose only output is a log file.

`MIN_SETTLED_EVENTS` (30) is a **logging filter, not a risk control.** A relation
that held in *n* events with zero violations still carries a rule-of-three upper
95% bound of ~`3/n` on its failure probability — ~10% at the default. That bound
is written into every profile and every candidate so "qualified" can never be
read as "proven".

The YES-basket additionally requires a *partition* (exactly one YES every time),
not just exhaustiveness. On a nested ladder ("over 1 run", "over 2 runs", …) the
bottom rung is almost always YES, so 40 clean events look identical to a
structural guarantee right up until a 0-0 game settles every leg NO.

## First live results (2026-08-02)

8,598 open events, 76,483 markets, 3,086 distinct series. Sweep takes ~45s
(the universe is streamed twice: once to rank series by volume, once to
evaluate). 60 series are qualified per sweep, highest-volume first, so the
profile cache converges over a few hours; events not yet covered are counted as
`event_profile_not_yet_built` rather than dropped.

Of the first 60 qualified: 26 qualified for something. Winner-take-all events
(MLB, ATP/WTA/ITF tennis, CS2, LoL, Dota, Valorant, soccer) come back
`exclusive + exhaustive confirmed` — these are the genuine mutually-exclusive
events Session 29 noted were absent from its sample. Nested totals ladders
(KXMLBTOTAL, KXWTI oil) come back `implication confirmed` on tens of thousands
of pair tests. Weather (KXHIGHLAX) comes back exclusive + exhaustive + disjoint,
matching Session 31's 1,261/1,261 finding. Player-prop series (KXMLBHIT,
KXMLBHRR) are `implication refuted` — the multi-metric trap again.

**Zero candidates** across 12 consecutive sweeps — 13,094 event-evaluations,
zero errors, every sweep reconciling, coverage climbing 725 → 1,284 evaluated
events per sweep as the cache filled. The near misses are all exactly one spread
wide:

| event | legs | basket cost | guaranteed payout |
|---|---|---|---|
| KXATPMATCH | 2 | $1.01 | $1.00 |
| KXCS2GAME | 2 | $1.02 | $1.00 |
| KXMLBGAME | 2 | $1.07 | $1.00 |
| KXHIGHLAX | 6 | $1.09 | $1.00 |

That is what a functioning market looks like, and it matches Session 29's ladder
check (closest 1.01) exactly. One snapshot is not a verdict — accumulating the
frequency data is the point.

## Watchdog

Every sweep writes a `record: "sweep"` line whether or not it found anything, so
that line doubles as a heartbeat. On the VPS the check is automated:
`scripts/karbot-canary-alert.sh` runs from cron every 15 minutes and sends
Telegram on two edges — **CANARY STALLED** if no sweep has been written for 20
minutes (with a recovery message), and **CANARY FOUND N CANDIDATE(S)** the
moment a real one appears, carrying its economics and its
`confirmed`/`vanished_on_recheck` status. A candidate sitting unread in a JSONL
file is worth nothing.

Both alert paths, plus the silent no-op case, were exercised with real Telegram
sends before being trusted, against a scratch file via the `CANARY_LOG` and
`CANARY_STATE_DIR` overrides so no real data was touched. An untested watchdog
is worse than none: `karbot-disk-alert.sh`, built to prevent a silent outage,
was itself silently non-functional from Session 26 to Session 29.

Manual check:

```bash
tail -1 logs/basket_candidates.jsonl | python3 -m json.tool
grep -c '"record": "candidate"' logs/basket_candidates.jsonl
```

## Isolated in process, NOT isolated in rate limit

Worth stating plainly, because "separate process" overstates the independence:
`canary` and `karbot.service` reach Kalshi from the same IP and share its rate
limit. `PriceWatcher._request_snapshot` already measured a **~5.5% 429 rate**
during post-restart bursts (Session 23).

Canary load is ~43 requests per sweep at steady state, but **~160 per sweep
while profiles are being built** (43 event pages plus up to 60 settled-history
fetches) — a few hours after a cold start, recurring weekly as profiles age out.
At the default 300s interval that is a low sustained rate, and `kalshi_rest.get`
backs off on 429 rather than retrying hot.

**Measured after deploying:** counting `book_reset_rest_failed` (the real 429
counter — a naive `grep 429` matches sequence numbers containing those digits
and is worthless), the trading process logged **0 failures across 38,752 REST
snapshots in the 84 minutes before** the canary started and **4 across 2,390 in
the 13 minutes after** — about **0.17%**. Real, but an order of magnitude below
Session 23's 5.5%, and absorbed by the existing retry path.

**If that rate climbs, look here first.** The levers are `--interval-seconds`
(up) and `max_new_profiles` (down), not a theory about Kalshi changing
something.

## Not on the live trading path

`canary/` and `backtest/` are research code and must never be imported by
`karbot_runner.py` or any agent — `tests/test_canary_isolation.py` enforces it.
This package uses blocking `requests` on purpose, which is correct in its own
process and is precisely the Session 23 outage if it ran inside the runner's
event loop. `canary` importing `backtest` (fee model, settled-market fetch) is
the allowed direction.
