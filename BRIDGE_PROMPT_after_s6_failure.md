Continuing work on Karbot Rage! (repo: /Users/tom/Projects/karbotrage/karbotrage_v1,
GitHub: WarpedMind/karbotrage). Read CLAUDE.md in full first (its KNOWN DEBT
and "Next session priorities" were both rewritten in Session 31 — the priority
list now opens with an unresolved **direction fork**, not a plan). Then read
DECISIONS.md's **Session 31** entry in full — that entry is the authoritative
record of what was measured and why the previous direction stopped — followed
by Session 30's entry for the plan it executed, and Sessions 29 and 28 for the
history that led here (S1's structural death, the S2/S3/S4 audit, the RiskGate
sizing findings, the Telegram security fix). Then SESSIONS.md Session 31.
Verify anything load-bearing against the real repo/API state before trusting a
summary, **including this one** — the discipline that has produced every
genuine result on this project is diagnose-from-real-data, not
argue-from-theory, and it is why S1 died for $0 and why S6 died for one session.

## Where things stand — the honest one-paragraph version

**The Session 30 direction was executed and the answer was no.** Session 31
built `backtest/`, produced the calibration report, and found that NOAA/NBM
converted to a probability is *worse* calibrated than the Kalshi price at every
lead NOAA publishes — Brier 0.2013 vs the market's 0.1757 at 12h on contested
markets, skill −0.146, P(model no better) = 1.000 over 36 independent dates, 17
of 18 cities losing. Simulating the trades: the model claims +$0.11–0.17 net EV
per contract and realises −$0.01 to −$0.04, and *tightening* the divergence
filter makes it worse. The root cause was measured rather than inferred: the
market's implied point forecast is ~20% more accurate than NBM's (MAE 1.27 °F
vs 1.59 °F), while NBM's published spread is close to correct — so the deficit
is the forecast, not the probability conversion, and no better error model can
recover it. **Gate G2 failed; S6-weather is closed, not paused.** That is a
successful session by Session 30's own stated terms: it cost one session
instead of an order layer. No live-path code was touched.

Two things worth internalising before starting. First, Session 30 picked
weather *because* the forecast source and the settlement source are the same
agency — and read again, that is exactly the property guaranteeing every other
participant reads it too. Session 30's own "honest counter" named this risk and
it turned out to be decisive, not a footnote. The generalised form is now a
screening question in `SIGNAL_REGISTER.md`: **"is there a reason the market
does not already know this?"** Ask it before any future candidate spends
budget. Second, Session 31's most valuable find was not the headline result: a
validation reported **7,565/7,565 matched** while silently checking only 6,305
of 7,566 markets. Nothing failed, no test caught it, and it was found only
because a total did not reconcile. *A validation that reports a rate rather
than a reconciliation can hide an arbitrarily large omission behind a
perfect-looking number.*

All the numbers and reasoning are in the docs above; don't take this
paragraph's word for any of it.

## What must NOT be touched or deleted

Unchanged, and it still matters: the arbitrage infrastructure stays. S1's
canary detector, the S2/S3/S4 groundwork, the event bus, `PriceWatcher`'s
order-book reconstruction, `RiskGate`, `PaperExecutor`, `ComplianceOfficer`,
`TelegramNotificationAgent` (with its sender-auth fix) — all reusable
substrate. New work is new modules **alongside** what exists.

Added to that list by Session 31:
- **`backtest/` must never be imported by the live trading path.** It is
  offline analysis. It has zero dependencies beyond stdlib + `requests`
  deliberately, so that adding numpy/scipy/pandas for a report never lands on
  the VPS. Keep it that way.
- **Do not rebuild S6-weather** without genuinely new information about why it
  would differ. The negative is measured, out-of-sample, at every lead that
  exists, with the mechanism identified. Re-running it is not diligence.
- **Do not delete `backtest/`** because its subject failed. `nbm_text.py`,
  `kalshi_history.py`, `stations.py`, `scoring.py` and `costs.py` are
  general-purpose and already do market discovery, ladder normalisation,
  date-blocked bootstrapping and ceil'd-fee economics — most of what an S5a/S5b
  canary or any future calibration study needs.

## What Session 31 finished (do NOT redo)

Confirm against `git log` rather than trusting this list, but do not re-derive:
- **The S6 weather calibration report.** Committed raw output in
  `backtest/reports/`. G1 pass, **G2 fail**, G3 confirms.
- **Ground truth, all proved rather than assumed** — settlement rule replayed
  against 7,565 real settlements (exact); ladders confirmed exhaustive
  partitions (1,261/1,261 city-days, one YES each); station identity resolved
  empirically 18/18 at 100% (**Houston is KHOU/Hobby, not KIAH**; also KMDW not
  KORD, KNYC, KDCA, KDFW); NBM valid-time mapping scored both ways.
- **A cheaper NOAA data route than Session 30 specced.** The NBM `text/` suite
  publishes per-station ASCII bulletins with mean, spread **and** quantiles —
  no GRIB2 decoder, no grid interpolation. NBS is hourly, NBP is 6-hourly.
- **Two real bugs fixed**: the `less`/`cap_strike` field convention, and a
  parser sign flip on packed 3-digit rows.
- **Still open in Phase 0**: paper resolution against real outcomes
  (re-scoped — it blocked S6 paper trading, which no longer exists, so it
  blocks nothing today and becomes blocking again the moment any
  variance-bearing strategy reaches paper). Also `--mode` is parsed and never
  applied.

226/226 tests passing; runner smoke test clean.

## The actual job this session: build the S5a/S5b passive arb canary

**The fork was already put to the operator and answered at the end of Session
31: build the S5a/S5b canary next.** The operator's exact framing —
*"Option 1, but let's continue to have the other options be considered (where
appropriate and justified) later"* — so this is **sequencing, not elimination**.
Market-making especially is not rejected; it is waiting on a live order layer
and on the willingness to build a large subsystem that cannot be falsified
offline. Do not re-ask the fork; do surface it again if something you find
changes its premise.

The other candidates, kept live for later:

1. **S5a/S5b passive arb canary — THIS SESSION.** Cheapest, safest, parallel to
   anything else. REST poller plus arithmetic: no LLM, no orders, no hot path. Never
   disproven (Session 29 found nothing in *one snapshot*, which is not the same
   as nothing existing). Converts a snapshot into real frequency data over
   weeks. `backtest/kalshi_history.py` already does the discovery.
2. **Market-making (S8)** — the strongest remaining statistical candidate,
   untouched by the S6 result, with a measured surface: 489 markets at ≥2¢
   spread and ≥100 contracts both sides paying **no maker fee**. Cost: needs
   the live order-management layer that does not exist, built entirely up
   front, and — unlike divergence — it **cannot be falsified offline** at all.
   That asymmetry is why Session 30 sequenced it second; it is still true.
3. **A different `FairValueProvider`** — the abstraction survives. Apply the
   screening question first.
4. **Consolidate infrastructure** — the standing list has real items, several
   of which stop being cosmetic the moment anything carries variance (Health
   Monitor / dead-lettered `AgentHeartbeat`, the stuck order-book reset loop,
   the fee-variance question, re-auditing "CONFIRMED LIVE" claims against the
   VPS).

Tests are expected for anything that ships, matching this project's convention
(226/226 as of Session 31).

## The single most important practice — carry this forward deliberately

The operator asked that each session apply **the same diligence used in Session
30 to proactively find issues and contradictions**, rather than only doing the
task in front of it. Session 31 kept that going, and everything it found came
from the same two habits:

1. **Verify one level deeper than feels necessary, especially on a negative or
   a confident conclusion.** Session 31 found the `less`/`cap_strike` bug by
   noticing a total that did not reconcile, found a parser sign flip by writing
   a test for a field it did not even use, and closed the "stale data"
   objection to its own headline result by *measuring* that NBM publishes no
   sub-12-hour daytime max rather than arguing it was unlikely to matter. It
   also **retracted its own mid-session claim** that NBM's published spread was
   too narrow — that generalised one station's value to the population, and the
   measurement (SD/RMSE = 0.93) said otherwise. Applies in both directions: if
   a result looks too good, suspect the pipeline; if it looks too bad, suspect
   the pipeline just as hard.
2. **Actively look for contradictions between the docs and reality.** Where two
   docs disagree, or a doc disagrees with the code, or a comment disagrees with
   what a function does — that gap is usually a real bug. When you find one,
   fix *all* the places it's stated, and record the retraction rather than
   quietly editing.

Corollaries: label every claim **confirmed vs. argued**; treat "the tests pass"
as weak evidence (this codebase's expensive bugs all had passing tests, and
Session 31's `less`-market bug had a *100% pass rate* printed next to it); and
when you are wrong, retract it explicitly in the docs rather than silently.

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
  "Other" option automatically; don't add one.) *Session 31 note: it recurs
  intermittently even after switching, and individual retries often succeed —
  retry once or twice before escalating, and prefer writing a real script file
  with Write (which never needs the classifier) over long inline heredocs.*
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
- **Long data pulls belong in a background Bash task, but do not pipe them
  through `tail`** — Python block-buffers to a pipe, so the output file stays
  empty until the process exits and an interrupted run loses everything.
  Redirect to a file, and have the script also write its raw rows to disk so
  the analysis can be re-run without re-fetching. (Session 31 lost the header
  of a 20-minute run this way.)
- **`hash()` is salted per interpreter run** — never use it to name a cache
  file. Session 31 shipped that bug and caught it in review; use `hashlib`.

### Behavioural disciplines — restate these every time, they are what actually works
- **Verify one level deeper than feels necessary, especially on a negative.**
  See the diligence section above; this is the single highest-value habit on
  this project and it evaporates if not restated.
- **Agreement among secondary sources is not verification** — get the
  primary document.
- **Never treat a log name, metric name, or comment as evidence of what it
  measures** — read the emitting code, including its level.
- **A validation that reports a RATE can hide an arbitrarily large omission.**
  Reconcile totals: seen vs checked vs matched. Count skips by reason and make
  an unhandled case a hard failure, never a silent `continue`. (Session 31:
  "7,565/7,565 matched" while silently skipping 1,255 markets.)
- **Label every claim confirmed vs. argued.** "Deployed" is not "confirmed
  live"; "the service is active" is not "the service is working."
- **Retract errors explicitly in the docs**, with the wrong claim, the
  correction, and why it was wrong — never silently edit them away.
- **Ask the decision question after the investigation that informs it**, not
  before. Session 30 put a strategy fork to the operator and had to reopen
  it an hour later when a primary source overturned the premise.
- **The independent unit is rarely the row.** Before any statistical claim,
  ask what is actually independent. Session 31's 7,565 markets were 71
  independent dates; getting that wrong shrinks a confidence interval ~9× and
  turns noise into an edge.

### How the operator wants to work
- **NEVER end a turn without calling `AskUserQuestion` first.** This is a
  hard rule, not a preference, and it applies **especially** when the work
  looks finished — that is exactly when it keeps getting forgotten. Ending
  the turn with a summary and no question forces the operator to spend a
  whole extra round-trip just to say "actually, one more thing," which is
  the specific cost this rule exists to prevent. The final call of every
  turn is a question announcing you are done and asking whether anything
  else is needed. (`AskUserQuestion` supplies its own "Other" option
  automatically — do not add one, and do not ask the operator to use it.)
- **Stay in one turn** more generally. Use `AskUserQuestion` rather than
  ending the turn, including to hand off tasks outside your reach —
  retrieving a rate-limited page, flipping a permission mode, running
  something locally. Blocked work should be unblocked mid-turn, not
  deferred to the next session.
- **Explain reasoning, not just recommendations.** The operator usually
  defers to the recommendation, which makes an unexplained one an unreviewed
  decision. Give the reasons and the honest counter-argument. When something
  seems to contradict an earlier decision, explain the distinction rather
  than assuming it is understood.
- **Proactively surface issues and contradictions**, not just the assigned
  task. Most of Session 30's and Session 31's real finds were not the task.
- **Ask for the time budget at the start if it isn't stated.** "Quick one"
  versus "I have a few hours" changes scoping from the first move — whether
  to open a large build at all, or to take a bounded piece and hand off. One
  question, asked once, in the first `AskUserQuestion` of the session.

### Decide these yourself — do NOT spend a question on them
The operator has explicitly delegated these. Asking about them wastes a
round-trip and trains them to skim the options:
- **Test changes** — adding tests, and updating existing ones when behaviour
  legitimately changed. (Always say *why* a test changed, in the commit and
  in SESSIONS.md, so it can't look like fudging a failure — see the Session
  30 liquidity-cap tests for the right pattern.)
- **Documentation structure** — section placement, headings, wording, which
  of the four docs a fact belongs in.
- **Commit granularity and messages.**
- **Repo hygiene** — `.gitignore` entries, untracking build/OS artifacts,
  removing stray temp or backup files you created.
- **Implementation choices** among equivalent options, and refactors
  confined to code you are already changing.
- **Adding comments** that record a subtlety or a past bug.

### Still ask about these
- Direction/strategy forks with a real trade-off, and scope calls that
  change what gets built.
- Anything that spends money, sends something outward, or goes live.
- Deleting or gutting existing work (the arb substrate especially — see
  "what must NOT be touched" above).
- Production/VPS changes beyond a routine verified deploy.
- Any case where two readings of the request produce materially different
  work — and ask *before* doing the work, not after.
- Keep all four docs current (CLAUDE.md, DECISIONS.md, SESSIONS.md,
  README.md), commit and push, and confirm `git status` clean +
  `git log origin/main -1` matching local before signing off.

## Standing practices for this session

- **Quality, security, and privacy are non-negotiable defaults, not
  per-task judgments** — apply CLAUDE.md's SECURITY RULES without being
  asked. Any new external data source needs a descriptive `User-Agent` with
  contact info (`backtest/` sets one on every outbound request). If any
  provider ever needs a key, it goes through `SecretsConfig` and environment
  variables only — never config.yaml, never hardcoded.
- **Never fabricate a quantitative claim.** No invented percentages, no
  "this should yield X%" without real measured numbers. Any config threshold
  specced as a placeholder must be *set from measurement*, never quietly
  promoted into a default that later reads as validated.
- **Label every claim as confirmed vs. argued**, the way the Session 28/29/
  30/31 entries do. "Deployed" is not "confirmed live." VPS access works via
  the key path in the carry-forward block; nothing is "confirmed live" there
  until checked against `git log -1` on the box itself plus fresh log output.
- **Use `AskUserQuestion` at genuine decision points** — direction forks,
  scope calls, "should this continue or stop" — and batch independent ones
  into a single round-trip. Don't use it as a substitute for judgment calls
  this session is equipped to make alone. The operator explicitly wants
  reasoning, not just a recommendation — when presenting a fork, explain
  plainly *why* one option beats the other, and don't assume a decision has
  been understood just because it was stated.
  (Source: `/Users/tom/Projects/foundry/docs/context-efficiency-playbook.md`,
  entry 9 — worth a full read if unfamiliar.)
- Duplication across CLAUDE.md/DECISIONS.md/SESSIONS.md/README.md is fine;
  drift is the enemy — if a fact changes, grep for every place it's stated
  and fix all of them, not just the one being actively edited.
- **`SIGNAL_REGISTER.md` is a standing document** — a tiered register of
  candidate unconventional data sources with a mandatory statistical gate.
  The operator explicitly wants an open mind here: nothing pre-judged, track
  record decides. **Add to it whenever a new candidate comes up**, and
  populate its "tested — no edge" section as things fail. It now has its
  first entry (NOAA/NBM, Session 31) and two Session 31 corrections worth
  knowing before using it: a **screening question** — *"is there a reason the
  market does not already know this?"* — and a correction to its own
  multiple-comparisons budget, which is **~71 independent dates, not thousands
  of markets**. That budget supports very few hypotheses, so spend it on
  sources with a plausible informational advantage, not on breadth.
  One item there is still worth acting on if weather work ever resumes:
  NOAA's weather-modification registry requires filing **≥10 days before**
  activity commences, which is real advance public notice with a direct
  physical path to precipitation markets. Whether filings are visible at
  submission or only at quarterly publication is **unverified and decisive** —
  check it. Note it clears the screening question in a way NBM did not.

## Recommended model / effort for this session

**Opus, medium-to-high effort.** Not max, and not Sonnet, for specific reasons.

The S5a/S5b canary is small — a REST poller and some arithmetic, publishing
nothing tradeable. That argues for a cheaper model. What argues against it is
that **every trap in this work is of the exact class this project has already
paid for three times**: pricing the wrong side of the book (Session 26),
mistaking a structural impossibility for an opportunity (Session 28), and
mis-reading a strike-field convention (Session 31). Specifically here —
exhaustiveness versus mutual exclusivity are *different conditions* and the
YES-basket and NO-basket cases need different ones; every leg must be priced at
the **ask**, not the bid or the mid; and the fee is **ceil'd per order and
multiplied by N legs**, which is exactly where a thin basket edge dies. Those
are cheap to write and easy to get subtly wrong, and a wrong version produces a
confident stream of fake opportunities rather than an error.

Not max effort: there is no open modelling or statistical question this time.
The measurement work that justified max-adjacent effort in Session 31 is done,
and what remains is bounded implementation against a spec that already exists
(DECISIONS.md Session 28's roadmap entry, plus Session 29's empirical check).

Sonnet is a reasonable choice in this repo for mechanical work — doc sweeps,
test scaffolding, refactors confined to code already being changed — and it is
the wrong economy for anything that decides whether a trade is profitable.

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

**Then, as the actual final action of the session**: call
`AskUserQuestion` to announce you are done and ask whether anything else is
needed. Do not end the turn with a summary alone — see the hard rule in the
carry-forward block above. This applies even when everything is committed,
pushed and verified; *especially* then.
