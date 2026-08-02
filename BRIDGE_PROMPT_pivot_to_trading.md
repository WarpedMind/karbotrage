Continuing work on Karbot Rage! (repo: /Users/tom/Projects/karbotrage/karbotrage_v1,
GitHub: WarpedMind/karbotrage). Read CLAUDE.md in full first (especially
KNOWN DEBT and Next session priorities), DECISIONS.md's five Session 28
entries (S1 structural impossibility, S2/S3/S4 audit, RiskGate sizing,
Telegram security, strategy roadmap) plus its Session 26 entry (the S1
bid/ask sign fix that Session 28 builds on), and SESSIONS.md's Session
29, 28, and 27 entries in full, in that order — Session 29 has its own
"Addendum" subsection (Phase 1/Phase 2 work), and note Session 27's
entry had a missing top-level heading that was just restored in a small
fix; if you see a "### Context" subsection reading oddly close to
Session 28's, that's this same fix — verify the heading is intact and
the content makes sense, don't assume the file is still broken. Verify
anything load-bearing against the real repo/VPS state before trusting a
summary, including this one — this project's whole discipline this
session was diagnose-from-real-data, not argue-from-theory. The four
original planning docs are also worth a read for vision context:
`/Users/tom/Projects/karbotrage/Karbot_Rage_Strategic_Vision.docx`,
`Karbot_Rage_Architecture.docx`, `Karbot_Rage_Agent_Architecture.docx`,
`Karbot_Rage_World_Intelligence.docx`.

## Where things stand — the honest one-paragraph version

S1 (single-market YES+NO arbitrage) is confirmed structurally
impossible on Kalshi — not thin, impossible by construction of the
exchange's matching engine — and is now canary-mode-only (detects and
logs, never trades). A follow-up review (using temporary access to a
more capable model) proposed two successor arb strategies (S5a event
baskets, S5b threshold ladders); both were checked against real live
Kalshi data before any code was written, and neither shows a
currently-exploitable edge in the sample checked. One real security hole (Telegram accepted commands from any
sender, not just the operator) and several other real bugs were found
and fixed along the way (a kill switch that had no trigger anywhere, a
disk-space watchdog silently broken since Session 26, S2/S3/S4 all
confirmed broken and disabled by default). Full numbers and math for
all of this are in the docs above —
don't take this paragraph's word for any of it, the docs have the real
data.

**The decision already made, not up for re-litigation without new
evidence**: shift the center of gravity from pure structural arbitrage
toward statistical/correlated trading (real edge, real variance — not
risk-free like arb was supposed to be). This session's job is to turn
that decision into a concrete, phased plan — not to re-argue whether
pure arb is dead (it's confirmed) or re-run the S5a/S5b checks (already
done, documented, not stale enough to redo).

## What must NOT be touched or deleted

The existing arbitrage infrastructure is deliberately being left in
place, not ripped out — S1/S2/S3/S4/S5 groundwork (even disabled/canary
ones), the event bus, `PriceWatcher`'s order-book reconstruction,
`RiskGate`, `PaperExecutor`, `ComplianceOfficer`, `TelegramNotification-
Agent` (now with working sender-auth and a real kill switch). All of
this is reusable substrate for whatever gets built next, and the arb
strategies specifically should stay available to revisit later (a
detect-and-log window over a longer real timeframe, or a more targeted
search for genuine winner-take-all Kalshi events, could still be worth
doing eventually) — this pivot is an addition, not a replacement.
Nothing here should be deleted or gutted; new work should be new agents/
strategies alongside what exists.

## The actual job this session: a phased strategy + architecture spec — NOT implementation

Produce a written, prioritized plan for the pivot. Do not write
trading-strategy code this session (infrastructure fixes/bugfixes
unrelated to new-strategy scope are fine if found along the way, same
as any session). The deliverable is documentation the next session can
implement from without re-deriving any of this — same pattern as the
Session 28 review, which worked well: real analysis, real math where
it applies, explicit "confirmed" vs. "argued, needs verification"
labeling, and no invented numbers.

Cover, in order:

1. **Pick the near-term direction: market-making vs. model-divergence
   trading (or both, sequenced).** Both were flagged as the most
   promising non-arb ideas. Market-making (quote both sides, capture
   the spread, ~$0 maker fees on Kalshi) needs a live order-management
   layer (place/cancel/track) that doesn't exist yet, and carries real
   inventory/adverse-selection risk. Model-divergence trading (compare
   Kalshi's price against a real external calibrated source — NOAA
   forecasts for weather markets, CME FedWatch for Fed-decision
   markets, sharp sportsbook closing lines for sports markets — and
   trade the gap) needs a real data feed and a backtesting discipline,
   and carries model risk. Worth weighing explicitly: model-divergence
   trading is conceptually closer to how forex/stock trading actually
   works (compare an external view of fair value to market price),
   so it may be the more natural bridge toward the multi-asset ambition
   below — market-making infrastructure is comparatively Kalshi-specific
   and wouldn't transfer the same way. This is a real fork — use
   `AskUserQuestion` here, it's a genuine decision point, not a
   judgment call to make unilaterally.
2. **Architecture for whichever direction is chosen**: how it plugs
   into the existing event bus (new agent(s), new event types, how
   `RiskGate` needs to change for a strategy with real variance instead
   of a risk-free guarantee — note DECISIONS.md's Session 28 RiskGate
   entry already found Kelly sizing is wrong for arb and right for
   exactly this kind of statistical strategy, so this may be where
   Kelly sizing finally gets used correctly instead of removed). Be
   concrete: what data source, what agent, what event flow, what needs
   backtesting before it ever sees paper money.
3. **A brief, honest scoping pass on the multi-asset ambition**
   (forex, stocks, "beyond Kalshi eventually") — this is a much bigger
   undertaking than the Kalshi pivot above (different regulatory regime
   entirely — SEC/FINRA/NFA depending on venue, pattern-day-trader
   rules, broker API access, KYC/AML, different tax treatment) and
   should be scoped as a real future phase with its own research pass,
   not conflated with or blocking the near-term work. A short "what
   this would actually require, roughly how big a lift" note is enough
   this session — do not attempt a full spec for it now.
4. **Revisit the "giant holistic trading engine" vision docs** (the
   four `.docx` files) for anything genuinely still worth building that
   this session's direction doesn't already cover — DECISIONS.md's
   Session 28 entry already found the vision specced 16 agents and only
   10 exist (missing: News Analyst, Sentiment, Geopolitical, an Options
   Signal agent — which maps closely to the model-divergence idea above
   if that direction is chosen — Whale Tracker, Resolution Verifier,
   Portfolio Manager, Health Monitor). Don't just build all of it
   reflexively; note what's genuinely relevant to the chosen direction
   vs. what was speculative scope from the original vision that may not
   still make sense.
5. **A concrete, ordered build plan** for the next implementation
   session(s) — phased, with an explicit "detect-and-log before wiring
   to real (paper) trading" gate for anything with real variance, same
   discipline as the arb strategies got.

## Standing practices for this session

- **Quality, security, and privacy are non-negotiable defaults, not
  per-task judgments** — apply CLAUDE.md's SECURITY RULES section
  without being asked. This applies with extra force to anything
  involving new external data sources (API keys, rate limits, data
  licensing) or, later, real broker/exchange credentials.
- **Use `AskUserQuestion` at genuine decision points** (direction
  forks, scope calls, "should this continue or stop") — batch several
  into one round-trip where they're genuinely independent. **Don't use
  it as a substitute for judgment calls this session is actually
  equipped to make alone** — over-using it just relocates decisions
  without reducing round-trips, and trains the reader to stop reading
  the options carefully. (Source: `/Users/tom/Projects/foundry/docs/
  context-efficiency-playbook.md`, entry 9 — worth a full read if
  unfamiliar, it has real nuance beyond this summary.)
- **Never fabricate a quantitative claim** — no invented percentages,
  no "this should yield X% returns" without real backtested numbers
  behind it. This project's whole hard-won lesson this session was
  "verified beats argued" — carry that forward into the new strategy
  work, don't relax it because the topic changed from bug-hunting to
  strategy design.
- Duplication across CLAUDE.md/DECISIONS.md/SESSIONS.md is fine;
  drift is the enemy — if a fact changes, grep for every place it's
  stated and fix all of them, not just the one being actively edited.

## Before ending this session

Update SESSIONS.md (new entry), DECISIONS.md (a full entry for the
chosen direction and architecture, with real reasoning, not just a
bullet list), CLAUDE.md (Current status, KNOWN DEBT, Next session
priorities), and README.md (per this project's standing rule: refreshed
alongside doc changes, not left stale). Commit and push. Confirm `git
status` is clean and `git log origin/main -1` matches local before
signing off.

**Last step of this session, always**: write the next bridge prompt
(`BRIDGE_PROMPT_<topic>.md`, matching this file's naming convention —
topical, not just a session number) reflecting whatever actually got
decided and built this session, and make sure that new bridge prompt
repeats this same closing instruction so the chain doesn't drop. Follow
the same structure this file used: where things stand (pointing at the
durable docs, not restating them), what must not be touched/deleted,
the actual next job, standing practices, and this closing instruction.
