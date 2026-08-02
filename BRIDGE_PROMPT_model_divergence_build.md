Continuing work on Karbot Rage! (repo: /Users/tom/Projects/karbotrage/karbotrage_v1,
GitHub: WarpedMind/karbotrage). Read CLAUDE.md in full first (especially
KNOWN DEBT and Next session priorities — both were rewritten in Session
30 and the priority list is now phased, not a flat list). Then read
DECISIONS.md's Session 30 entry in full — **that entry is the
authoritative spec for this session's work**, not this file; this file
is a pointer and an orientation, and it deliberately does not restate the
architecture, the math, or the gates. Then SESSIONS.md Session 30, and
Sessions 29 and 28 for the history that led here (S1's structural death,
the S2/S3/S4 audit, the RiskGate sizing findings, the Telegram security
fix). Verify anything load-bearing against the real repo/API state before
trusting a summary, **including this one** — the discipline that has
produced every genuine result on this project is diagnose-from-real-data,
not argue-from-theory, and it is the reason S1 died for $0 instead of for
real money.

## Where things stand — the honest one-paragraph version

Pure structural arbitrage is finished as this project's centre of gravity:
S1 is impossible by construction on Kalshi (confirmed live, Session 29),
and S5a/S5b — while never disproven — showed nothing sitting there in a
real 1,600-market check. Session 30 turned the already-made pivot decision
into a phased spec: **S6 external-model divergence, weather/NOAA first, in
detect-and-log mode behind an offline backtest**, with market-making
deferred behind a live order-management layer that doesn't exist and
S5a/S5b continuing as a cheap passive canary. The direction was chosen on
measured grounds — a live scan of 40,000 open Kalshi markets showed
Fed/econ markets are effectively dead (0.1% of volume), sports is 75% but
benchmarked against sharp closing lines, and weather is small (3.2%) but
uniquely tractable because Kalshi weather markets settle on NWS's own
Climatological Report for a named station. **Session 30 wrote no code** —
it was spec-only, by mandate.

Two corrections were made *within* Session 30 that the docs record in full
and that are worth internalizing before starting: it published a
"correction" claiming Kalshi's maker fee is 25% of taker (wrong — the
maker formula's multiplier defaults to **0**, so maker fees are $0 outside
~76 enumerated series, and Session 28 was right), and it published
"VPS access lost, state unknown" (wrong — the SSH key simply wasn't in
`~/.ssh/`). Both wrong claims reached a commit before being caught. Both
came from the same failure: **a confident negative conclusion drawn from
an incomplete search.** Three agreeing secondary sources were not
confirmation; one directory listing was not a search. Carry that forward —
it applies directly to this session's NOAA-archive question, where "I
couldn't find an archive" must not become "no archive exists."

All the numbers and the reasoning are in the docs above; don't take this
paragraph's word for any of it.

## What must NOT be touched or deleted

Unchanged from the previous bridge, and it still matters: the arbitrage
infrastructure stays in place. S1's canary detector, the S2/S3/S4
groundwork, the event bus, `PriceWatcher`'s order-book reconstruction,
`RiskGate`, `PaperExecutor`, `ComplianceOfficer`, `TelegramNotification-
Agent` (with its sender-auth fix and working kill switch) — all of it is
reusable substrate, and the arb strategies specifically should stay
available to revisit. New work is new agents and new modules **alongside**
what exists. In particular: do **not** add divergence logic into
`ArbScannerAgent`. Session 30 deliberately specced a separate
`DivergenceScannerAgent` precisely to stop riskless and statistical
strategies blurring together — that blurring is exactly what Session 28
found had happened everywhere else (single-leg S3 sold as "arb", Kelly
applied to riskless baskets).

## What Session 30 already finished (do NOT redo)

Phase 0 is 3-of-4 done and pushed. Confirm against `git log` rather than
trusting this list, but do not re-derive:
- **NOAA archive question: ANSWERED.** NBM on AWS (`noaa-nbm-grib2-pds`,
  anonymous S3, 2020-05-18→now) carries `TMAX:2 m above ground:12-24 hour
  max fcst` **and** `:ens std dev`, plus a `qmd/` quantile suite and `.idx`
  sidecars for byte-range fetching. Kalshi supplies settled outcomes
  (`status=settled`, `result`) and a `candlesticks` endpoint with
  `yes_bid`/`yes_ask` history. **The backtest is buildable now.**
- **Unit system: FIXED.** RiskGate sizes in integer contracts;
  riskless-vs-statistical sizing split; `f* = (p−c)/(1−c)` wired to a new
  `OpportunityEvent.model_probability`; `KalshiFeeModel.taker_fee_dollars()`
  implements the real per-order round-up.
- **`from_yaml()`: FIXED.** All previously-ignored sections parse, with an
  unknown-key warning.
- **Still open in Phase 0**: paper resolution settling against real market
  outcomes (`PaperExecutor` resolves every trade at its own `expected_pnl`,
  which is tautological for a directional strategy and would make S6 paper
  results meaningless). Also `--mode` is parsed and never applied.

157/157 tests passing; runner smoke test clean.

## The actual job this session: Phase 1 — the calibration report

1. **Finish the last Phase 0 item** (real-outcome paper resolution) if you
   want it out of the way — but it does **not** block the backtest, which
   is pure offline analysis and touches none of RiskGate/PaperExecutor.
2. **Build `backtest/` and produce a calibration report.** This is the
   session's deliverable, and it is **a report, not a trading agent**:
   - Pull NBM forecast TMAX + ens std dev for the stations Kalshi settles
     on, at several lead times, using `.idx` byte-range fetches.
   - Convert to `P(high > strike)` per Kalshi market. First cut: Gaussian
     from mean + ens std dev. Upgrade to the `qmd/` quantiles if the
     Gaussian assumption is visibly poor — check, don't assume.
   - Pull settled outcomes and `candlesticks` price history for the same
     markets. **Score the model's Brier against the market price's Brier**,
     out-of-sample. Report a reliability curve and the sample size.
   - Then apply ceil'd taker fees (`taker_fee_dollars`), the half-spread
     cost of crossing to the ask, and the observed depth cap, to convert a
     calibration edge into a net-of-cost expected edge.
3. **Constraints to respect in the report**: Kalshi weather history starts
   ~2026-05-25, so the sample is **summer-only**. State the season. Do not
   claim cross-season generalisation — it is unproven until winter data
   exists.
4. **If the model does not beat the market price, say so and stop.** That
   is a successful session, not a failed one — it costs one session instead
   of an order layer, and it is the entire reason this direction was chosen
   over market-making.

Tests are expected for anything that ships, matching this project's
convention (157/157 as of Session 30).

## CARRY-FORWARD BLOCK — copy this whole section into the next bridge prompt, and every one after it

Everything in this section is **operating knowledge, not project state**.
Project state lives in CLAUDE.md/DECISIONS.md/SESSIONS.md and should only be
*pointed at* from a bridge. The items below are the things nothing else
carries — if a future bridge drops them, they are gone. **Add to this block
as new items are learned; never thin it out.**

- **Bash breaks under `auto` permission mode.** The failure looks like
  "claude-opus-5 is temporarily unavailable, so auto mode cannot determine
  the safety of Bash." When it happens, **use `AskUserQuestion` to ask the
  operator to switch to acceptEdits** — do not silently work around it or
  abandon the check. (Note: `AskUserQuestion` always supplies its own
  "Other" option automatically; don't add one.)
- **SSH to the VPS uses a key OUTSIDE `~/.ssh/`:**
  `ssh -i ~/kalshi-keys/oracle-vps.key ubuntu@147.224.209.18`. Session 30
  wasted effort and published a wrong conclusion by checking only
  `~/.ssh/`. If something looks absent, ask before concluding it's gone.
- **The operator wants to stay in one turn where possible.** Batch
  questions; use `AskUserQuestion` rather than ending the turn to ask
  something. If you need a task done outside your reach (fetching a doc,
  running something locally, checking a dashboard), **ask via
  `AskUserQuestion` and continue in the same turn** rather than stopping.
  Do not end the session without explicitly saying so and confirming.
- **Supporting documents the operator fetches go in a `supporting docs/`
  folder** in the repo. Check there before assuming a source is
  unreachable. Kalshi's fee schedule (effective 2026-07-07) is one of
  these.
- **Web access works** — one caveat: `kalshi.com` rate-limits (HTTP 429)
  on repeated fetches. The operator can retrieve pages via Brave when a
  fetch is blocked; ask. Retrieved documents go in `documentation/`
  (Kalshi's fee schedule is already there).
- **VPS**: `karbot.service` is enabled at boot, as is `cron` and the
  `/etc/cron.d/karbot-disk-alert` watchdog — so a reboot is safe. Never
  enable DEBUG logging globally: that is what filled the disk and killed
  the box for 9 days in Session 26. Per-module DEBUG only.

### Behavioural disciplines — restate these every time, they are what actually works
- **Verify one level deeper than feels necessary, especially on a negative.**
  See the diligence section below; this is the single highest-value habit on
  this project and it evaporates if not restated.
- **Agreement among secondary sources is not verification** — get the
  primary document.
- **Never treat a log name, metric name, or comment as evidence of what it
  measures** — read the emitting code, including its level.
- **Label every claim confirmed vs. argued.** "Deployed" is not "confirmed
  live"; "the service is active" is not "the service is working."
- **Retract errors explicitly in the docs**, with the wrong claim, the
  correction, and why it was wrong — never silently edit them away.
- **Ask the decision question after the investigation that informs it**, not
  before. Session 30 put a strategy fork to the operator and had to reopen
  it an hour later when a primary source overturned the premise.

### How the operator wants to work
- **Stay in one turn.** Use `AskUserQuestion` rather than ending the turn —
  including to hand off tasks outside your reach (fetching a blocked page,
  flipping a permission mode, running something locally). Do not end a
  session without explicitly saying so and confirming.
- **Explain reasoning, not just recommendations.** The operator usually
  defers to the recommendation, which makes an unexplained one an unreviewed
  decision. Give the reasons and the honest counter-argument. When something
  seems to contradict an earlier decision, explain the distinction rather
  than assuming it is understood.
- **Proactively surface issues and contradictions**, not just the assigned
  task. Most of Session 30's real finds were not the task.
- Keep all four docs current (CLAUDE.md, DECISIONS.md, SESSIONS.md,
  README.md), commit and push, and confirm `git status` clean +
  `git log origin/main -1` matching local before signing off.

## The single most important practice — carry this forward deliberately

The operator asked explicitly that this session apply **the same diligence
used in Session 30 to proactively find issues and contradictions**, rather
than only doing the task in front of it. Concretely, that session found:
Session 28's maker-fee premise was wrong (then that its own correction was
wrong); the SSH key wasn't where it looked; a misnamed log line that had
been quietly misrepresenting book-reset health for multiple sessions;
`from_yaml()` ignoring four config sections; and three tests that passed
only because they never exercised what they claimed to. **None of those
were the assigned task.** All were found by pulling one thread further than
strictly required.

Two rules that produced all of it:

1. **Verify one level deeper than feels necessary, especially on a negative
   or a confident conclusion.** Session 30 got four things wrong, and every
   single one was a confident conclusion drawn from an *incomplete search*:
   three agreeing secondary sources that all omitted the same field; one
   directory listing treated as a search; two log names taken at face value
   without reading the code emitting them. The instinct to check was right
   every time — the stopping point was too early. "I couldn't find X" is
   never "X doesn't exist." **This applies directly to this session**: if
   the NBM data doesn't look right, or the calibration looks too good,
   assume the pipeline before assuming the finding.
2. **Actively look for contradictions between the docs and reality.** Where
   two docs disagree, or a doc disagrees with the code, or a comment
   disagrees with what a function does — that gap is usually a real bug,
   not a documentation lapse. Every major find in Sessions 26–30 started as
   exactly that kind of mismatch. When you find one, fix *all* the places
   it's stated, and record the retraction rather than quietly editing.

Corollaries worth stating: label every claim **confirmed vs. argued**; treat
"the tests pass" as weak evidence (this codebase's expensive bugs all had
passing tests); and when you are wrong, retract it in the docs explicitly
rather than silently — Session 30 has three retractions written into
DECISIONS.md/SESSIONS.md on purpose, because a wrong claim that was quietly
deleted teaches nobody.

## Standing practices for this session

- **Quality, security, and privacy are non-negotiable defaults, not
  per-task judgments** — apply CLAUDE.md's SECURITY RULES without being
  asked. This session touches a new external data source: `api.weather.gov`
  needs no key but does require a descriptive `User-Agent` with contact
  info, and its rate limits are unpublished. If any provider ever needs a
  key, it goes through `SecretsConfig` and environment variables only —
  never config.yaml, never hardcoded.
- **Never fabricate a quantitative claim.** No invented percentages, no
  "this should yield X%" without real measured numbers. The `s6_*` config
  thresholds specced in DECISIONS.md are explicitly labeled placeholders
  to be *set from the backtest* — do not quietly promote a guessed number
  into a default that later reads as validated.
- **Label every claim as confirmed vs. argued**, the way the Session 28/29/
  30 entries do. "Deployed" is not "confirmed live." Note that VPS access
  is currently broken (`Permission denied (publickey)`), so nothing can be
  confirmed live there until it's restored.
- **Use `AskUserQuestion` at genuine decision points** — direction forks,
  scope calls, "should this continue or stop" — and batch independent ones
  into a single round-trip. Don't use it as a substitute for judgment calls
  this session is equipped to make alone. Note from Session 30: the
  operator explicitly wants reasoning, not just a recommendation — when
  presenting a fork, explain plainly *why* one option beats the other, and
  don't assume a decision has been understood just because it was stated.
  (Source: `/Users/tom/Projects/foundry/docs/context-efficiency-playbook.md`,
  entry 9 — worth a full read if unfamiliar.)
- Duplication across CLAUDE.md/DECISIONS.md/SESSIONS.md/README.md is fine;
  drift is the enemy — if a fact changes, grep for every place it's stated
  and fix all of them, not just the one being actively edited.
- **`SIGNAL_REGISTER.md` is a standing document** — a tiered register of
  candidate unconventional data sources (official NOAA weather-modification
  filings, ADS-B, solar/lunar/geophysical events, crowd-sourced claim data,
  Farmer's Almanac) with a mandatory statistical gate. The operator
  explicitly wants an open mind here: nothing pre-judged, track record
  decides. **Add to it whenever a new candidate comes up**, and populate
  its "tested — no edge" section as things fail, because a recorded failure
  stops the same idea being re-proposed. The gate (Bonferroni/FDR, n≥20,
  3-period replication, ≥2h lead, out-of-sample, market-price baseline) is
  not optional — the failure mode of an open mind on a small sample is
  confidently trading noise, which is precisely what S1 was.
  One item there is worth acting on early if weather work continues: NOAA's
  weather-modification registry requires filing **≥10 days before** activity
  commences, which is real advance public notice with a direct physical path
  to precipitation markets. Whether filings are visible at submission or only
  at quarterly publication is **unverified and decisive** — check it.

## Book-reset health: RESOLVED, do not re-raise

Session 30 briefly reported the book-reset recovery as regressed to ~0%
completion, then found the alarm was a log-naming artifact and fixed it.
**The mechanism is healthy: ~2,174 successful REST recoveries per 10 minutes
against 16 failures (0.7%), better than Session 23's confirmed 5.5%.** The
INFO log is now `book_snapshot_applied_rest`. One thing to carry: **any
historical analysis grepping `book_snapshot_requested` was counting
successes, not attempts** — including Session 22's own regression evidence.
There is currently no attempt counter; add one deliberately if needed. The
separate Session 26 "stuck reset loop" item is unaffected and still open.

## Recommended model / effort for this session

**Opus, high effort** (not max — the work is mostly implementation once the
approach is right; not medium — the statistical reasoning is where the risk
lives). This is not a general preference — it is specific to what this
session does. The work is a probability-calibration harness whose
whole job is to avoid fooling itself: converting a deterministic forecast
plus an ensemble spread into a probability, scoring it against the right
baseline, and resisting the many ways a backtest can look profitable and be
wrong. Every expensive bug in this project's history has been of exactly
that shape — a sign error, a unit mismatch, the wrong side of the book, a
default multiplier — and every one passed its tests. Sonnet is a reasonable
choice for mechanical work in this repo; it is the wrong economy here.

## Before ending this session

Update SESSIONS.md (new entry), DECISIONS.md (a full entry for anything
genuinely decided, with real reasoning — not a bullet list; skip it if
nothing was decided rather than padding it), CLAUDE.md (Current status,
KNOWN DEBT, Next session priorities), and README.md (per this project's
standing rule: refreshed alongside doc changes, not left stale). Commit and
push. Confirm `git status` is clean and `git log origin/main -1` matches
local before signing off.

**Last step of this session, always**: write the next bridge prompt
(`BRIDGE_PROMPT_<topic>.md`, matching this file's naming convention —
topical, not just a session number) reflecting whatever actually got
decided and built this session. Follow the same structure this file used:
where things stand (pointing at the durable docs, not restating them), what
must not be touched/deleted, the actual next job, standing practices, and
this closing instruction.

**Two things the new bridge prompt MUST do, or the chain degrades:**
1. **Copy the entire CARRY-FORWARD BLOCK above into it, verbatim**, adding
   any new operating knowledge learned this session. Never thin it out. That
   block holds environment quirks, behavioural disciplines and working
   preferences — none of which live anywhere else, so if a bridge drops
   them they are permanently lost. Project *state* does not belong there;
   point at CLAUDE.md/DECISIONS.md/SESSIONS.md for that.
2. **Repeat these two instructions themselves**, so the next bridge carries
   them forward too. This instruction is what makes the chain
   self-sustaining rather than dependent on each session remembering.

Also recommend a model and effort level for the next session, with the
reason — not a generic default.
