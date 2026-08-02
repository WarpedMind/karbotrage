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
Climatological Report for a named station. Session 30 also found that
Session 28's market-making premise was wrong: Kalshi's maker fee is not
$0, it is 25% of taker. **Session 30 wrote no code** — it was spec-only,
by mandate. All the numbers and the reasoning are in the docs above; don't
take this paragraph's word for any of it.

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

## The actual job this session: Phase 0, and Phase 1 only if Phase 0 clears

Implementation session, unlike the last one. Work the phased priority list
in CLAUDE.md, in order. Do not skip ahead to writing strategy code.

1. **Answer the NOAA forecast-archive question BEFORE writing any model
   code.** `api.weather.gov` serves only the *current* forecast. Is there a
   reachable, complete archive of past NWS/NBM forecasts, matched to the
   stations Kalshi settles on (NOAA NOMADS, Iowa State IEM, or NBM
   probabilistic guidance)? This single answer decides whether the backtest
   is one session over months of history or several weeks of forward
   collection — and therefore what the rest of this session can even
   attempt. Report the answer plainly either way; "no usable archive" is a
   perfectly good result that changes the plan rather than blocking it.
2. **Land the Phase 0 prerequisites** (CLAUDE.md priorities 2–4): the
   integer-contract unit fix across RiskGate/PaperExecutor/PositionTracker,
   the `from_yaml()` parsing gaps, and paper resolution settling against
   real outcomes instead of the trade's own expected P&L. These are
   genuinely blocking — a statistical strategy sized in the wrong units, or
   scored against its own forecast, produces meaningless results.
3. **Then, and only then, Phase 1**: `FairValueEngine` +
   `NoaaTemperatureProvider` + the `backtest/` harness. The deliverable of
   Phase 1 is **a calibration report, not a trading agent** — model Brier
   score against the Kalshi price as baseline, out-of-sample, with a
   reliability curve and the sample size stated. The bar is not "is NOAA
   accurate," it is "is NOAA better calibrated than the market."
4. If the calibration fails the bar, **say so and stop** — that is a
   successful session, not a failed one, and it costs one session instead
   of a live order layer.

Tests are expected for anything that ships, matching this project's
existing convention (133/133 passing as of Session 29).

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
decided and built this session, and make sure that new bridge prompt
repeats this same closing instruction so the chain doesn't drop. Follow the
same structure this file used: where things stand (pointing at the durable
docs, not restating them), what must not be touched/deleted, the actual
next job, standing practices, and this closing instruction.
