# Decision Log
# Entries are ordered newest-to-oldest. Most recent decision is at the top.

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
