Continuing work on Karbot Rage! (repo: /Users/tom/Projects/karbotrage/karbotrage_v1,
GitHub: WarpedMind/karbotrage). Read CLAUDE.md in full first (its KNOWN DEBT and
"Next session priorities" were both rewritten in Session 32 — the priority list
now opens with two small, specific jobs rather than a fork). Then read
DECISIONS.md's **Session 32** entry in full — that is the authoritative record of
what was built and measured — followed by Session 31's entry (why S6 weather is
dead), then Sessions 30, 29 and 28 for the history that led here (the S5a/S5b
spec, S1's structural death, the S2/S3/S4 audit, the RiskGate sizing findings,
the Telegram security fix). Then SESSIONS.md Session 32, and `canary/README.md`
for the traps list. Verify anything load-bearing against the real repo/API state
before trusting a summary, **including this one** — the discipline that has
produced every genuine result on this project is diagnose-from-real-data, not
argue-from-theory, and it is why S1 died for $0, S6 died for one session, and
Session 32 caught three of its own bugs before shipping.

## Where things stand — the honest one-paragraph version

**Session 31 killed S6 weather; Session 32 built the thing the operator chose in
its place, and it works.** `canary/` is a standalone detect-and-log process that
sweeps Kalshi's whole open universe every few minutes, prices S5a baskets and
S5b pairwise relations at the ask with ceil'd per-order fees and depth-capped
integer size, re-confirms every candidate against the live order book, and
appends to `logs/basket_candidates.jsonl`. It publishes nothing, sizes nothing,
orders nothing, and is never imported by the trading path (a test enforces it).
**12 consecutive sweeps, 13,094 event-evaluations, zero candidates, zero errors,
every sweep reconciling.** Every near miss is exactly one spread wide — ATP
$1.01, CS2 $1.02, MLB $1.07, weather ladder $1.09, each for a guaranteed $1.00.
That is a functioning market, and it reproduces Session 29's hand check (closest
1.01) exactly. **This is the instrument, not a verdict**: twenty-five minutes on
a Sunday afternoon is not weeks, and real arbitrage is sporadic by nature.
301/301 tests pass. Nothing was deployed to the VPS.

**It is deployed, running, and watched.** `karbot-canary.service` is installed,
enabled at boot and active on the VPS, sweeping every 5 minutes at `Nice=10`.
`karbot-canary-alert.sh` runs from cron every 15 minutes and sends Telegram on
two edges — **CANARY STALLED** (no sweep for 20 minutes, with a recovery
message) and **CANARY FOUND N CANDIDATE(S)** with the candidate's economics and
its `confirmed`/`vanished_on_recheck` status. Both paths were exercised with
real Telegram sends before being trusted. So **the operator will be told if a
candidate appears** — you do not need to poll for one, and a session that starts
with "did it find anything" can answer that from the log in one command.

The canary's measured effect on the trading process is small but real:
`book_reset_rest_failed` went from 0 across 38,752 REST snapshots in the 84
minutes before, to 4 across 2,390 in the 13 minutes after — about 0.17%, an
order of magnitude below Session 23's 5.5%, absorbed by the existing retry path.
Worth re-checking over a longer window.

Four things worth internalising before starting. First, **the design decision
that carries the correctness is "structure proposes, history disposes."** Strike
arithmetic only *generates* candidate relations; a relation is usable only if
the series' real settled record has never violated it. That came from a live
counterexample, not caution: `KXMLBSPREAD` puts two different metrics (each
team's winning margin) in one event at overlapping strikes, so interval logic
"proves" that Tampa Bay winning by 4+ implies Chicago winning by 3+ — 2,267
measured violations. Second, **all of Session 32's real finds came from
counting, not from tests**, with a fully green suite sitting next to each: a
sweep that evaluated zero events and errored on nothing, a reconciliation that
was off by 23, and a settlement outcome that is neither YES nor NO. Third, and
most uncomfortable: **Session 32 reproduced Session 31's exact failure mode in
brand-new code** — it filed Kalshi's cancelled-event settlements under
"unsettled" and dropped them, so the profile reported `exhaustive: confirmed`
while nothing had checked whether the basket's guaranteed dollar survived.
Knowing the lesson did not prevent committing it; only counting did.

Fourth, and the most useful one: **the void-settlement question was escalated to
the operator as a binary and both options were wrong.** Does Kalshi refund a
voided position at cost, or not? Neither — its own `rules_secondary` says a
cancelled market "will resolve to a **fair price** in accordance with the
rules", so the deciding question was a third one nobody had asked: *do those
fair prices sum to $1?* They do, 243/243 in the first sample and then 361/361
across every partition series on the deployed box. It took one field
(`settlement_value_dollars`) that was already being fetched. **Before escalating
anything as "open and decisive", check whether the data already in hand answers
it.**

All the numbers and reasoning are in the docs above; don't take this
paragraph's word for any of it.

## What must NOT be touched or deleted

Unchanged, and it still matters: the arbitrage infrastructure stays. S1's canary
detector, the S2/S3/S4 groundwork, the event bus, `PriceWatcher`'s order-book
reconstruction, `RiskGate`, `PaperExecutor`, `ComplianceOfficer`,
`TelegramNotificationAgent` (with its sender-auth fix) — all reusable substrate.
New work is new modules **alongside** what exists.

- **`backtest/` must never be imported by the live trading path.** Offline
  analysis, stdlib + `requests` only, deliberately, so that adding numpy/scipy
  for a report never lands on the VPS.
- **`canary/` must never be imported by the live trading path either.** It uses
  blocking `requests` on purpose — correct in its own process, and precisely the
  Session 23 outage if it ran inside `karbot_runner.py`'s event loop.
  `canary` importing `backtest` is the allowed direction.
  `tests/test_canary_isolation.py` enforces both; do not weaken it.
- **Do not rebuild S6-weather** without genuinely new information. The negative
  is measured, out of sample, at every lead that exists, with the mechanism
  identified. Re-running it is not diligence.
- **Do not delete `backtest/`** because its subject failed. `nbm_text.py`,
  `kalshi_history.py`, `stations.py`, `scoring.py` and `costs.py` are
  general-purpose; `canary/` already reuses the last of them.
- **Do not relax the qualification gate in `canary/qualify.py` to get more
  candidates.** Its coarseness is deliberate. A false positive manufactures a
  confident stream of fake arbitrage; a false negative costs coverage in a
  process whose only output is a log file.

## What Session 32 finished (do NOT redo)

Confirm against `git log` rather than trusting this list, but do not re-derive:
- **`canary/`**: `kalshi_rest`, `strikes`, `qualify`, `economics`, `scan`,
  `run_canary`, README. Plus `scripts/karbot-canary.service` (written, **not
  deployed**) and 75 tests. 301/301 passing.
- **Confirmed live**: NO-leg depth is `yes_bid_size_fp` (the field with the
  opposite name — there is no `no_ask_size_fp`), verified both directions
  against `/orderbook`; the bulk snapshot goes stale within seconds (16/16
  agreement back-to-back, but a traded market moved yes_bid 0.10→0.14, size
  3→2071 over ~10s), hence mandatory per-leg re-confirmation; a `strike_type`
  census over 12,000 open markets with `less`/`cap_strike` reconfirmed 105/105
  and `structured` found to be two different things.
- **Session 29's coverage gap is closed.** Genuine winner-take-all events (MLB,
  ATP/WTA/ITF tennis, CS2, LoL, Dota, soccer) do qualify as
  `exclusive + exhaustive confirmed`, and still show nothing.
- **Cancelled events do not break the basket guarantee.** 361 cancellations
  across all 19 partition series on the deployed box sum to exactly $1.00, zero
  violations; the 96 violations are all in non-partition series (player props,
  PGA top-N, spreads) that were never basket candidates. Checked per series in
  `qualify.scalar_sum_to_one`, and the gate currently blocks 0 qualifying
  series.
- **The "dies quietly" gap is closed.** `scripts/karbot-canary-alert.sh` +
  `/etc/cron.d/karbot-canary-alert`, mirroring `karbot-disk-alert.sh`'s
  conventions. Both alert paths and the silent no-op were exercised with real
  Telegram sends, against a scratch file via `CANARY_LOG`/`CANARY_STATE_DIR`
  overrides. **An untested watchdog is worse than none** — the precedent is
  `karbot-disk-alert.sh` being silently non-functional Session 26→29.
- **Deploying found two things nothing else would have**: `requests` was an
  undeclared dependency since Session 31 (present locally by coincidence), and
  **the dev machine and the VPS disagree on floating-point arithmetic** — local
  Python 3.14 vs VPS 3.10, and CPython 3.12 gave `sum()` compensated summation,
  so ten one-cent fees add to 0.1 on one and 0.09999999999999999 on the other.
  A test passed locally and failed there. **"All tests pass locally" is not
  evidence about production.** (3.10 also lacks PEP 701, so nested same-type
  quotes in f-strings are a syntax error there — worth remembering when writing
  a throwaway diagnostic to run on the box.)
- **Still open in Phase 0**: paper resolution against real outcomes (blocks
  nothing today; blocks everything the moment a variance-bearing strategy
  reaches paper). `--mode` is still parsed and never applied. Also 11
  pre-existing VPS test failures (10 in `test_regulatory_intelligence.py`, 1 in
  `test_config_resolved_log.py`) — environment differences, uninvestigated.

## The actual job this session

**Both of Session 32's follow-ups were completed in Session 32** — the canary is
deployed and the void question is answered. So this session opens on a genuinely
open direction question, plus one cheap piece of housekeeping.

**First, cheap and worth doing before anything else — read the log.** The canary
has been running since 2026-08-02 22:34 UTC. The measurement that matters is not
just the candidate count but the **`confirmed` vs `vanished_on_recheck` ratio**:
that is what separates "real resting arbitrage" from "our view of the book is
noisy", the exact question Session 29 could not answer from one snapshot. A high
vanish rate is also independently useful as a data-quality signal about the
snapshot endpoint.

```
ssh -i ~/kalshi-keys/oracle-vps.key ubuntu@147.224.209.18
tail -1 ~/karbotrage_v1/logs/basket_candidates.jsonl | python3 -m json.tool   # heartbeat
grep -c '"record": "candidate"' ~/karbotrage_v1/logs/basket_candidates.jsonl
```

Also re-check the canary's effect on the trading process over a longer window
than the 13 minutes measured so far — count `book_reset_rest_failed`, **never
`grep 429`**, which matches sequence numbers containing those digits and
produced a false alarm that had to be retracted.

**The direction question was put to the operator at the end of Session 32 and
ANSWERED — do not re-ask it.** The confirmed sequence is: send the Kalshi
enquiry → let the canary accumulate → spend intervening sessions on the two
infrastructure prerequisites → decide market-making with the exchange's answer
and ~2 weeks of canary data in hand. **This is a sequencing decision with an
information trigger, not a commitment to build market-making** — that call is
still open and gets made when the enquiry is answered. Surface the fork again
only if something you find changes its premise.

So this session's work, in order:

**One thing already settled, so do not re-open it unprompted**: the operator
asked in Session 32 whether paying for data or services would make this worth
it. Answer, with full reasoning in DECISIONS.md's Session 32 addendum: **not
yet, for a structural rather than budgetary reason.** The binding question is
not "is it paid?" but *"do the participants setting the price already have
it?"*, and 75.4% of Kalshi volume is sports — the worst possible place to buy
an edge, since the counterparties already buy better feeds and the sharpest
public forecast (a devigged closing line) is nearly free. Neither remaining
strategy is data-constrained: S5a/S5b is arithmetic on Kalshi's own books, and
market-making needs an order layer, which is engineering. Three specific,
testable conditions that *would* change the answer are recorded there — quote
those rather than re-deriving the argument.

**The direction analysis is already written** — DECISIONS.md's Session 32
addendum, "the direction question, and why the infrastructure items are on
market-making's critical path rather than an alternative to it". Read it rather
than re-deriving. Its conclusion, in one line: **send the Kalshi enquiry → let
the canary accumulate → spend intervening sessions on the two infrastructure
prerequisites (Health Monitor, stuck order-book reset loop) → decide
market-making with the exchange's answer and ~2 weeks of canary data in hand.**

The load-bearing observation is that those two infrastructure items are
**prerequisites for market-making, not alternatives to it**: a quoting system
whose agent silently dies is holding inventory it is not managing, and a maker
with a stale book quotes a price someone will take. So that work is phase one of
the build, and retains value even if market-making is never chosen.

The counter-argument is recorded there too and should be honoured: this is the
classic shape of a project that builds forever and ships nothing. Two guards —
the infrastructure list is **bounded to those two items** (the rest of the
standing list is genuinely cosmetic and must not be used to fill sessions), and
the decision has an **information trigger, not a date** (when the Kalshi answer
arrives and the canary has ~2 weeks of data, market-making is decided yes or
no; "institution-only" decides it *no* immediately and reopens the fork).

**The concrete work for this session, then, is the two infrastructure
prerequisites** — the Health Monitor (dead-lettered `AgentHeartbeat` events,
firing every ~30s with no subscriber) and the stuck order-book reset loop
(Session 26; specific markets log `book_needs_reset`/`book_reset_throttled` on
every delta indefinitely and never complete recovery). Both are on
market-making's critical path and both retain value regardless. **Keep the list
bounded to those two** — fee variance, `--mode`, Telegram mute are cosmetic and
must not be used to fill a session.

Also check whether the operator has sent the Kalshi enquiry and had a reply; if
so, that answer may decide market-making immediately (an "institution-only"
programme kills it and reopens the fork).

The other candidates stay on the table (operator, Session 31: *"let's continue
to have the other options be considered where appropriate and justified
later"*):

- **Market-making (S8)** — the strongest remaining statistical candidate,
  untouched by the S6 result, with a measured surface: 489 markets at ≥2¢ spread
  and ≥100 contracts both sides paying **no maker fee**. Cost: it needs the live
  order-management layer that does not exist, built entirely up front, and —
  unlike divergence — it **cannot be falsified offline at all**. That asymmetry
  is why Session 30 sequenced it second; still true. This is the largest new
  subsystem in the project's history and should be entered deliberately, not
  drifted into.
- **A different `FairValueProvider`** — the abstraction and the divergence
  *shape* survive. Apply `SIGNAL_REGISTER.md`'s screening question first: **"is
  there a reason the market does not already know this?"** A free public
  forecast every participant reads is the weakest possible candidate, which is
  exactly why weather lost.
- **Consolidate infrastructure** — the standing list has real items, several of
  which stop being cosmetic the moment anything carries variance (Health Monitor
  / dead-lettered `AgentHeartbeat`, the stuck order-book reset loop, the
  fee-variance question, re-auditing "CONFIRMED LIVE" claims against the VPS).

Tests are expected for anything that ships, matching this project's convention
(301/301 as of Session 32).

## The single most important practice — carry this forward deliberately

The operator asked that each session apply **the same diligence used in Session
30 to proactively find issues and contradictions**, rather than only doing the
task in front of it. Sessions 31 and 32 both kept that going, and everything
they found came from the same two habits:

1. **Verify one level deeper than feels necessary, especially on a negative or a
   confident conclusion.** Session 32 found its void-settlement gap by asking
   what six dropped events actually *were* instead of accepting "unsettled";
   found its prioritisation bug because a sweep reported `evaluated=0` without
   erroring; and found its reconciliation bug because a total was off by 23. It
   also corrected four of its own test expectations mid-build — including one
   fixture that accidentally contained a **real** NO-basket arbitrage the
   scanner correctly found and the test wrongly called a failure. Applies in
   both directions: if a result looks too good, suspect the pipeline; if it
   looks too bad, suspect the pipeline just as hard.
2. **Actively look for contradictions between the docs and reality.** Where two
   docs disagree, or a doc disagrees with the code, or a comment disagrees with
   what a function does — that gap is usually a real bug. When you find one, fix
   *all* the places it's stated, and record the retraction rather than quietly
   editing.

Corollaries: label every claim **confirmed vs. argued**; treat "the tests pass"
as weak evidence (this codebase's expensive bugs all had passing tests, and all
three of Session 32's finds had a green suite next to them); and when you are
wrong, retract it explicitly in the docs rather than silently.

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
  *Session 32 note: the failure can be **command-shape-specific**, not global —
  `echo probe` succeeded while every `python` invocation failed, in the same
  minute, ~8 retries. So probe with a trivial command before concluding "Bash
  is down"; and note it can recur after the operator has already switched
  modes, in which case ask again rather than assuming the mode reverted.*
- **When you run a background Bash task with your own `>` redirect, read the
  redirect target, not the harness's task-output file** — the latter stayed
  empty for the whole run in Session 32 while the redirect file had everything.
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
- **The dev machine and the VPS run different Python versions and therefore
  different arithmetic** — local 3.14, VPS 3.10. CPython 3.12 gave `sum()`
  compensated (Neumaier) summation for floats, so a sum of ten `0.01`s is
  exactly `0.1` locally and `0.09999999999999999` there; 3.10 also predates
  PEP 701, so nested same-type quotes inside an f-string are a syntax error.
  **A green local test suite is not evidence about production.** Run the suite
  on the box after any deploy, and never assert exact float equality on an
  accumulated sum.

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
- **A skip label that sounds benign can hide the exact risk you are measuring.**
  Session 32 filed Kalshi's voided events under "unsettled" and dropped them —
  the count was right there and correct, but the *name* made it look routine,
  so the profile reported `exhaustive: confirmed` while the guarantee failed on
  4.1% of real events. Ask what each skipped item actually *is*, not just how
  many there were. Knowing the Session 31 lesson did not prevent committing its
  twin; only counting and then *looking* did.
- **A run that reports success while doing nothing is the most dangerous
  output there is.** Session 32's first live sweep processed 8,608 events,
  evaluated 0, errored on nothing, and looked exactly like a working scanner
  finding no arbitrage. Always check that the work you think happened actually
  happened, in units of work — not in absence of errors.
- **Before escalating a question as "open and decisive", check whether the data
  already in hand answers it.** Session 32 put the void-settlement question to
  the operator as a binary and *both options were wrong*; the real answer was in
  `rules_secondary` (which ships on every market) and `settlement_value_dollars`
  (a field already being fetched). A question framed as a binary between two
  plausible outcomes is a warning sign that the third possibility has not been
  looked for.
- **An untested watchdog is worse than none.** Anything whose job is to notice a
  failure must have its alerting path fired for real before it is trusted, and
  the no-op case checked too. `karbot-disk-alert.sh` was built to prevent a
  silent outage and was itself silently non-functional for three sessions.
  Design monitoring scripts with an override (an env var for the log path and
  state dir) so both edges can be exercised against a scratch file without
  touching real data.
- **`grep` is not a measurement.** Session 32 reported a 429 rate by counting
  `grep -i "429"`, which matched sequence numbers containing those digits
  (`expected=27854299`), and had to retract it. Grep for the *emitting
  identifier* (`book_reset_rest_failed`), not for a value that could appear
  anywhere.
- **A diagnostic that deliberately bypasses a gate will print fake
  opportunities.** Label such output unmistakably at the point of printing, and
  never quote it in a doc or a message without the caveat attached — a "+$4.36
  riskless arbitrage" line is exactly the sort of thing that gets copied
  forward without its footnote.
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
  than assuming it is understood. *Session 32: the operator answered a
  recommendation with "help me understand why you're recommending option 1
  instead of 2" — the first version had given the conclusion and the
  supporting facts but had not made the decisive argument decisive, nor given
  the counter-argument equal weight. Lead with what actually decides it, and
  say plainly which of your own arguments is weak.*
- **Proactively surface issues and contradictions**, not just the assigned
  task. Most of Session 30's, 31's and 32's real finds were not the task.
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
  30 liquidity-cap tests and the Session 32 fee/fixture corrections for the
  right pattern.)
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
- Production/VPS changes beyond a routine verified deploy — **including
  installing a new systemd unit**, which is exactly where Session 32 stopped.
- Any case where two readings of the request produce materially different
  work — and ask *before* doing the work, not after.
- Keep all four docs current (CLAUDE.md, DECISIONS.md, SESSIONS.md,
  README.md), commit and push, and confirm `git status` clean +
  `git log origin/main -1` matching local before signing off.

## Standing practices for this session

- **Quality, security, and privacy are non-negotiable defaults, not
  per-task judgments** — apply CLAUDE.md's SECURITY RULES without being
  asked. Any new external data source needs a descriptive `User-Agent` with
  contact info (`backtest/` and `canary/` both set one on every outbound
  request). If any provider ever needs a key, it goes through `SecretsConfig`
  and environment variables only — never config.yaml, never hardcoded.
- **Never fabricate a quantitative claim.** No invented percentages, no
  "this should yield X%" without real measured numbers. Any config threshold
  specced as a placeholder must be *set from measurement*, never quietly
  promoted into a default that later reads as validated. (`canary`'s
  `MIN_SETTLED_EVENTS` is documented as a **logging filter, not a risk
  control**, with its rule-of-three bound written into every record, for
  exactly this reason. Do not let it drift into a risk control.)
- **Label every claim as confirmed vs. argued**, the way the Session
  28/29/30/31/32 entries do. "Deployed" is not "confirmed live." VPS access
  works via the key path in the carry-forward block; nothing is "confirmed
  live" there until checked against `git log -1` on the box itself plus fresh
  log output.
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
  populate its "tested — no edge" section as things fail. It has one entry
  (NOAA/NBM, Session 31) and two Session 31 corrections worth knowing before
  using it: a **screening question** — *"is there a reason the market does not
  already know this?"* — and a correction to its own multiple-comparisons
  budget, which is **~71 independent dates, not thousands of markets**. That
  budget supports very few hypotheses, so spend it on sources with a plausible
  informational advantage, not on breadth. (Session 32 added nothing to it —
  no new signal candidate arose — rather than padding it.)
  One item there is still worth acting on if weather work ever resumes:
  NOAA's weather-modification registry requires filing **≥10 days before**
  activity commences, which is real advance public notice with a direct
  physical path to precipitation markets. Whether filings are visible at
  submission or only at quarterly publication is **unverified and decisive** —
  check it. Note it clears the screening question in a way NBM did not.

## Recommended model / effort for this session

**Opus, medium-to-high effort.** The direction is settled, so this is a bounded
build session rather than an open question — but the two items in it are the
opposite of mechanical.

The **Health Monitor** decides whether a silently-stopped agent is noticed, and
the whole reason it stops being cosmetic is that market-making would have it
managing real inventory. The **stuck order-book reset loop** has resisted two
prior sessions' attempts and its symptom (169 million log lines filling the disk
over 9 days, Session 26) was mistaken for the disease once already; the fix then
addressed the log volume and explicitly not the cause. Neither is a
tighten-the-bolts task, and both touch the live trading process — which is where
this project's expensive outages have all been.

**Sonnet is the wrong economy here** despite the work looking like
infrastructure. It is reasonable only for genuinely mechanical accompaniments —
doc sweeps, test scaffolding, refactors confined to code already being changed.

**Escalate to Opus high** if the Kalshi answer arrives and market-making is
committed to: that is the largest new subsystem in the project's history, it
**cannot be falsified offline at all**, and every trap in it is a class already
paid for four times — pricing the wrong side of the book (Session 26), mistaking
a structural impossibility for an opportunity (Session 28), mis-reading a
strike-field convention (Session 31), and mistaking a stale snapshot for a
resting order (Session 32).

**Do not use max effort** unless a genuinely open statistical question reappears.
None is open — Session 31's calibration work is done and Session 32's questions
all resolved to measurements.

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
