# Karbot Rage!

**Karbot Rage!** is a multi-agent automated trading system for decentralized prediction markets. It is a WallStRobotics / CAIO-grade project — built to production standards from session one.

## The Name

**Karbot Rage!** is a backronym — every component has deliberate meaning:

| Letters | Word | Meaning |
|---|---|---|
| K | Kalshi | The primary CFTC-regulated exchange the bot trades on |
| Ar | Arbitrage | The core strategy — exploiting price mispricings |
| BOT | Bot | Automated trading system |
| RAGE! | Rage | Relentless, disciplined, emotion-free hunting for edge |

K + Ar + BOT + RAGE! = KARBOT RAGE!

The exclamation point belongs to RAGE, not the sentence. This is a
deliberate easter egg for traders and technologists who understand
the space. Casual observers see an energetic brand name. Those in
the know see the full etymology.

Version naming follows the theme: Rage → Fury → Wrath → Vengeance

## What it does

Ten specialized agents run concurrently over a shared async event bus, covering the full trading loop:

| Agent | Role |
|---|---|
| PositionTracker | Tracks deployed capital, open positions, daily P&L |
| PriceWatcher | Connects to Kalshi WebSocket (RSA-PSS authenticated), emits real-time price updates |
| ArbScanner | Scans for arbitrage opportunities (S1 strategy) |
| RiskGate | Enforces position/exposure limits; can pause trading on regulatory alerts |
| PaperExecutor | Simulates fills and P&L resolution in paper mode |
| MarketAnalyst | LLM-based market signal analysis (Claude) |
| RegulatoryIntelligenceAgent | Monitors CFTC/Federal Register, assesses urgency via Claude |
| ReflectionAgent | Nightly post-trade reflection and strategy tuning |
| ComplianceOfficer | Always-on compliance + audit trail (cannot be disabled) |
| TelegramAgent | Operator notifications and permission requests |

## Tech stack

- Python 3.8+, asyncio
- Pydantic typed config (`KarbotConfig`)
- Custom `EventBus` with typed event dataclasses (`core/events.py`)
- aiohttp, websockets, pyyaml, structlog, tenacity, aiosqlite, cryptography
- Anthropic SDK (LLM-based intelligence agents)
- pytest / pytest-asyncio

## How to run

```bash
# Activate the project virtualenv
source karbotrage_env/bin/activate

# Run continuously in paper mode (canonical entry point)
karbotrage_env/bin/python karbot_runner.py --mode paper

# Run a mock-data end-to-end test and exit cleanly
karbotrage_env/bin/python karbot_runner.py --mode paper \
  --mock-prices tests/fixtures/paper_test_prices.json --exit-after-test

# S5a/S5b arbitrage canary — separate process, detect-and-log only, no orders
karbotrage_env/bin/python -m canary.run_canary --once
karbotrage_env/bin/python -m canary.run_canary --interval-seconds 300
```

The legacy `python main.py` path still works but is intentionally not extended — it bypasses the event bus.

## Current phase: Phase 1

- Kalshi is the primary data source; Polymarket is gated behind `polymarket_ws_enabled` (disabled in Phase 1)
- Phase 1 invariants are enforced structurally in `KarbotConfig.__init__` — enabling Polymarket WebSocket or cross-platform strategies while `phase=1` raises `ValueError` at startup
- Paper trading mode only. The 30-day paper clock has been **reset** (Session 30) — the original 2026-06-29 → 2026-07-29 window is not usable evidence, because 9 of its first 14 days ran with a dead persistence layer and every S1 trade in it turned out to be a book-reconstruction artifact. A new clock starts when a strategy that has passed its measurement gates actually begins paper trading. Live execution stays deferred until then.

## Project layout

```
karbot_runner.py          # Entry point — starts all 10 Phase 1 agents
core/events.py            # EventBus + all typed event dataclasses
karbot/core/
  config.py               # KarbotConfig (Phase 1 invariants, from_yaml, .phase, .paper_mode, SecretsConfig)
  events.py               # Re-exports from core/events.py
agents/
  floor/
    price_watcher.py      # PriceWatcher (Kalshi WS, RSA-PSS auth, api.elections.kalshi.com)
    arb_scanner.py        # ArbScanner
    risk_gate.py          # RiskGate
    position_tracker.py   # PositionTracker
    paper_executor.py      # PaperExecutor
  research/
    market_analyst.py     # MarketAnalyst
    regulatory_intelligence.py  # RegulatoryIntelligenceAgent
  management/
    reflection.py         # ReflectionAgent
    compliance.py          # ComplianceOfficer (always-on)
  notifications/
    telegram_agent.py      # TelegramAgent
execution/engine.py       # Legacy monolith — do not extend until paper tested
data/market_data.py       # Kalshi-first market data
backtest/                 # Offline calibration harness — NEVER imported by the live path
  nbm_text.py             # NOAA NBM station bulletins (plain ASCII, no GRIB decoder)
  kalshi_history.py       # Settled markets + candlestick price history
  stations.py             # Kalshi series -> NWS station, resolved empirically
  probability.py          # Forecast -> market probability (continuity-corrected)
  scoring.py              # Brier, reliability, date-blocked bootstrap
  costs.py                # Ceil'd taker fees, executable-price economics
  resolve_and_verify.py   # Gate: prove the ground truth before modelling it
  verify_alignment.py     # Gate: prove the NBM valid-time -> local-day mapping
  run_calibration.py      # The report
  diagnose_gap.py         # Why the market wins: point forecast vs uncertainty
  reports/                # Committed raw output
canary/                   # S5a/S5b arbitrage canary — separate process, NEVER on the live path
  kalshi_rest.py          # Sweep primitive + authoritative order-book top
  strikes.py              # Strike conventions as intervals; implication & disjointness
  qualify.py              # What a series' settled history proves (confirmed/refuted/insufficient)
  economics.py            # One basket evaluator: asks, ceil'd fees x N legs, depth-capped size
  scan.py                 # Two-stage sweep — bulk snapshot, then per-leg re-confirmation
  run_canary.py           # Loop, JSONL output, per-sweep heartbeat
scripts/
  karbot-canary.service     # systemd unit for the canary (deployed)
  karbot-canary-alert.sh    # cron watchdog: Telegram on stall or on a candidate
```

## Recent fixes (order-book gap recovery, feed monitoring)

The price feed's order-book gap-recovery path went through several
iterations to reach its current, live-confirmed state:

- **Sequence-gap detection** was already correct: `OrderBook.apply_delta`
  flags `needs_reset` when a Kalshi WebSocket delta arrives out of sequence,
  and a corrupt book must be re-synced before further deltas can apply safely.
- **First recovery attempt** re-subscribed to the market over the existing
  WebSocket, on the assumption Kalshi would respond with a fresh snapshot.
  Live wire capture later showed Kalshi only acks a duplicate subscribe
  (`{"type": "ok"}`) — it never sends a new snapshot on re-subscribe, so
  this path could never have worked.
- **Current recovery mechanism**: `_request_snapshot()` fetches a fresh
  order book via a plain REST call (`GET
  /trade-api/v2/markets/{ticker}/orderbook`, no authentication — confirmed
  both by Kalshi's docs and live) using a single shared `aiohttp.ClientSession`
  reused across calls. **Confirmed live**: HTTP 200, `book_snapshot_applied`
  firing correctly, zero crashes under sustained load.
- **Along the way**: a `tenacity`/`structlog` logging incompatibility that
  silently defeated the WebSocket reconnect retry was found and fixed; the
  runner gained a capped auto-restart for `PriceWatcher` (fixed delay,
  bounded number of restarts per rolling window, then a Telegram alert if
  exhausted); and `TelegramNotificationAgent` was built to send an alert on
  feed disconnect/reconnect and on restart-budget exhaustion.
- **Known minor issue, not urgent**: right after a restart, when many
  markets need recovery at once, a small fraction (~5.5% observed) of REST
  snapshot fetches hit Kalshi's rate limit. Handled safely (retried on the
  next throttle window) but a concurrency limiter is a flagged follow-up.

## Telegram alerting had never actually run in production (found & fixed)

All of the Telegram-alerting work above was built and unit-tested correctly,
but `TelegramConfig.enabled` defaults to `False`, and no `config.yaml`
existed on the VPS (only the committed `config.yaml.example` template) — so
every Telegram alert has been silently disabled through three live deploys,
including a real crash/restart/restart-budget-exhaustion cycle that should
have paged the operator. `TelegramNotificationAgent` no-ops completely when
disabled: no HTTP calls, no error, no warning. Fixed by adding a
`config_resolved` startup log line (`karbot_runner.py`) that prints the
actual resolved value of every subsystem enable/disable flag — including
`telegram_enabled` — once at startup, so this class of gap is visible in
logs going forward instead of requiring source-code archaeology to notice.
A real `config.yaml` with `telegram.enabled: true` is being created directly
on the VPS (never committed — gitignored, environment-specific).

**First live Telegram run immediately found a real bug**: every regulatory
item was producing two Telegram messages — the correct, urgency-branched
one from `RegulatoryIntelligenceAgent`, and a second, broken one from a
leftover direct subscription in `TelegramNotificationAgent` that referenced
blank fields and a deleted log file, hardcoded to display as "CRITICAL"
regardless of actual urgency. Removed; the urgency-branched message is now
the sole source of regulatory Telegram alerts.

See DECISIONS.md and SESSIONS.md for full session-by-session detail.

## The VPS was silently dead for 9 days (found & fixed, 2026-07-13)

No session had touched this project since 2026-07-01. Resuming work
uncovered a real production outage that had been running invisibly the
entire time:

- **The VPS disk filled to 100% on 2026-07-04** and stayed full until
  2026-07-13. `compliance.db`, `kalshi_trades.csv`, and `audit_trail.jsonl`
  all silently stopped being written the moment it filled — `systemctl
  status karbot` reported "active (running)" the whole time, so nothing
  about this was visible without checking disk space directly. The existing
  Telegram alerting only covers feed disconnects and restart-budget
  exhaustion, not disk space, so it never fired either.
- **Root cause**: `structlog.configure()` was never called anywhere in the
  codebase. `logging.basicConfig(level=logging.INFO)` only filters the
  stdlib root logger — every agent's `structlog.get_logger()` calls
  rendered DEBUG output unconditionally regardless. A specific order-book
  market got stuck permanently re-triggering `book_needs_reset` on every
  single WebSocket delta (the 10s recovery throttle blocks the REST
  re-fetch, but not this per-delta debug log) — **169 million log lines**
  accumulated in `/var/log/syslog` over 9 days, filling the disk.
- **Fixed**: `structlog.configure(wrapper_class=structlog.make_filtering_
  bound_logger(logging.INFO))` added to `karbot_runner.py::setup_logging()`
  — confirmed live, no more DEBUG output. VPS disk freed; `logrotate`
  hardened with a `maxsize` cap plus an hourly size-check cron (the default
  daily schedule was too slow to catch a fast-growing file); a new,
  independent disk-space watchdog (`/usr/local/bin/karbot-disk-alert.sh`,
  every 15 minutes via cron, reads Telegram credentials directly rather
  than going through the app) now pages on 80% disk usage — deliberately
  outside the karbot process so it can't fail the same silent way.
- **Also found**: the VPS was 4 git commits behind `main` — three
  previously-documented "CONFIRMED LIVE" fixes (Sessions 23–25) had never
  actually been deployed. No prior session had checked the VPS's actual
  `git log` before making that claim. Deployed and current as of
  commit `9b210fe`.
- **Underlying stuck order-book loop is not yet fixed** — only the
  disk-filling symptom is. Why some specific books never complete recovery
  via the existing REST mechanism still needs investigation.

Full writeup: SESSIONS.md, Session 26 (2026-07-13).

## P&L inflation — three compounding bugs found and fixed (2026-07-13)

Same session, in order — each fix's investigation led to the next:

1. **Stale price publish on sequence gap**: `price_watcher.py`'s
   `_handle_kalshi_delta` discarded `OrderBook.apply_delta`'s return value
   and published a `PriceUpdateEvent` from stale pre-gap prices on the
   delta that first detected a gap. Fixed by checking the return value.
   Added `s1_max_net_profit_pct` (15%) to `ArbScanner` as a backstop.
2. **No order-book depth anywhere in the pipeline**: `RiskGate` sized
   positions purely off capital and Kelly criterion; `PaperExecutor`
   filled the full size at the top-of-book quote regardless of real
   liquidity. A live Kalshi order book pulled directly from the REST API
   showed a "47% edge" backed by exactly 1 contract. Fixed: real book
   depth now flows through `PriceUpdateEvent`, and `RiskGate` caps size
   to what's actually resting at the quoted price.
3. **The actual root cause**: investigating #2 required knowing which
   side of the book a BUY order executes against, which surfaced that
   `ArbScanner` was pricing S1 off **bid** prices — what other
   participants will pay, not what this system can buy at. Verified
   against a live market pulled from Kalshi's API: a "+47% profit" by the
   old formula was actually a **47% loss** by the real, executable ask
   price. Cross-checked against this project's own history — this
   project's Session 2 (2026-05-25) original spec prices, rejected as
   unprofitable back then, come out to a realistic small loss under the
   corrected formula, suggesting this sign error dates back to the
   strategy's first working version. **Every S1 "opportunity" this system
   has ever flagged as profitable was very likely a computed loss with
   the sign flipped.** Fixed: `_check_s1_rebalancing` now reads
   `yes_ask`/`no_ask` instead of `yes_bid`/`no_bid`.

17 new/updated tests, 99/99 total passing. **Confirmed live**: after
deploying and restarting, zero opportunities of any kind fired over ~4
minutes and 1,331 lines of book activity — versus nearly every price tick
producing a false "opportunity" before. Expected and correct: real
markets rarely offer a genuine executable edge after fees. Full
investigation and math: DECISIONS.md, "S1 arb formula uses BID prices for
both legs of a BUY trade." Revert point if needed: commit `5348533`.

Also fixed in the same session: `TelegramNotificationAgent` never
subscribed to `TradeResolvedEvent` — every message the operator saw was
the pre-resolution *estimate*, never the actual realized outcome. Added a
resolution message, and made both messages include market/strategy/legs
instead of a bare trade_id and dollar figure.

**A fourth bug, found by asking "is this even viable"**: the operator
pushed further — even if the fix is correct, is single-market arbitrage
actually capable of making money? Checking that honestly required
auditing `KalshiFeeModel`, which was flagged in its own code comments as
"approximate." Kalshi's real, published taker fee (confirmed against
their official fee schedule) is `0.07 × price × (1-price)` per contract
— the code was using a flat 14% regardless of price, 4-8x too high for a
typical contract, likely rejecting real small edges as "not enough to
cover fees." Fixed. Deployed and confirmed live: even with the much
lower, more accurate fee, zero opportunities fired over the observation
window — a meaningful signal that the earlier zero-opportunity result
wasn't an artifact of overly strict fees, real markets during this
window genuinely aren't offering a crossable edge.

**Update, 2026-07-16 — first real trades observed**: over the first ~63
hours after the fixes above went live, **5 real trades fired, totaling
$11.79 realized paper profit** (sizes $0.05-$81.36, net edges 6-13%).
Two were hand-verified dollar-exact independently. Every trade so far
has been capped by real order-book liquidity, not account capital —
confirms the earlier read that depth, not capital, is the actual
constraint. Still a small sample (5 trades), but real, positive,
internally consistent evidence the corrected formula works as intended.
Full trade table: SESSIONS.md, Session 27.

Also fixed the same day: a real, legitimate `size_usd=0.05` trade
displayed in Telegram as "x0"/"$0.00" (rounding, not a bug — confirmed
via VPS logs that the zero-size rejection never fired), which looked
exactly like a regression of the fix above. `TelegramNotificationAgent`
now shows enough precision to keep small-but-real trades visibly
nonzero.

## CORRECTION, 2026-07-16 (Session 28/29): S1 is structurally impossible on Kalshi — the "5 real trades" above were not real

The section above's "first real trades observed" claim did not hold up.
A fresh review found — and this session independently verified against
real live data before accepting it — that S1's win condition
(`yes_ask+no_ask<1`) is algebraically identical to a **crossed order
book**, a state Kalshi's own matching engine physically cannot let rest
(it mints crossed bids into contract pairs instantly). Verified two
ways: pulled 778 real live markets via REST — zero show a crossed book;
checked all 5 of the "hand-verified" trades against gap-detection logs
— all 5 fired in the exact same second as a `sequence_gap_detected`
event on that exact market. Every S1 signal to date was a
book-reconstruction artifact, not a real opportunity.

**S1 is now canary-mode-only by default** (`s1_canary_mode=True`) — it
still detects and logs candidates as a data-quality signal, but can
never execute a trade, paper or real, until the underlying
reconstruction bug is fixed and re-verified.

**Also fixed the same pass**: a real security hole (Telegram accepted
commands from any sender, not just the operator — fixed with sender-auth
checking `chat_id`), the disk-space watchdog silently reading a deleted
secrets path since Session 26 (fixed), a kill switch that existed in
code but had no trigger anywhere (wired to a Telegram operator command),
and S2/S3/S4 disabled by default (all confirmed broken per the same
review).

**Then, before building anything new**: checked Fable's proposed
successor strategies (S5a event-basket, S5b threshold-ladder arbitrage)
against real live data the same way S1 was checked. Neither shows a
currently-exploitable edge — 0 of 78 naive S5a candidates are genuine
mutually-exclusive events (all were threshold ladders misidentified as
baskets), and the closest real S5b ladder crossing found across 8
diverse live markets was 1.01 (need <1.00). Full numbers and math:
SESSIONS.md Session 29, DECISIONS.md.

**Honest current state**: no confirmed-viable strategy is trading right
now. The infrastructure (order book, risk gate, paper executor,
compliance, alerting) is solid and reusable regardless of which strategy
runs on it — but the specific strategy logic built so far (S1) is dead,
and the proposed replacements (S5a/S5b) are unconfirmed, not yet
disproven. Next step is a deliberate choice, not a default continuation:
detect-and-log S5a/S5b over a longer real window, search more
specifically for genuine winner-take-all markets, or reconsider
direction entirely (e.g. market-making).

## Direction change, 2026-08-01 (Session 30): from pure arbitrage to statistical trading

Pure structural arbitrage on Kalshi turned out to be a dead end for the
strategy this project started with, and thin-to-absent for its successors.
S1 is impossible by construction (see the correction above). S5a/S5b are
not disproven, but a real 1,600-market check found nothing sitting there.
So the centre of gravity moves to **statistical trading — real edge, real
variance, no risk-free guarantee** — while the arbitrage work stays in
place rather than being ripped out.

**Chosen direction: S6 — External Model Divergence.** Compare Kalshi's
price against a calibrated external source and trade the gap. Starting
with weather markets and the National Weather Service, for one reason
that nothing else in the market universe offers: Kalshi weather markets
settle on *"the National Weather Service's Climatological Report (Daily)"*
for a specific named station — **the forecast source and the settlement
source are the same agency's number for the same station.**

This was chosen over market-making on measured grounds, not preference:

| Measured live 2026-08-02, 40,000 open markets, 74.7M contracts/24h | |
|---|---|
| Sports share of volume | 75.4% |
| Weather share of volume | 3.2% (2.4M contracts, 672 markets) |
| Fed / CPI / econ share of volume | 0.1% — effectively dead |
| Median bid-ask spread (markets that trade) | 2¢ |
| Markets with spread ≥2¢ and ≥100 contracts both sides | 486 |

The deciding argument: **divergence can be falsified offline, and
market-making cannot.** NWS publishes forecasts and Kalshi publishes
settlements, so "when the model said 70% and the market said 55%, what
actually happened?" is answerable from history with no capital at risk.
Whether resting quotes get filled — and whether the fills you get are the
ones you didn't want — can only be learned by placing real orders. A
direction that can be killed cheaply beats one that can only be evaluated
expensively. That is the same discipline that killed S1 for $0.

Market-making stays on the roadmap, deferred behind a live
order-management layer that doesn't exist yet — and it looks *better*
than expected once Kalshi's published fee schedule is read carefully. The
maker and taker fee formulas share a shape but not a default multiplier:
`round up(M × 0.0175 × C × P × (1−P))` for makers where **M defaults to
0**, versus `M × 0.07 × …` for takers where M defaults to 1. So maker
fees are **$0** except on ~76 explicitly enumerated series — which works
out to **42.5% of exchange volume and 3,651 of 3,858 tradeable markets
paying nothing to rest an order**, 489 of them with a ≥2¢ spread and ≥100
contracts on both sides. Tellingly, the series that *do* charge maker fees
are also the tightest (1¢ spreads on KXPGATOUR and KXMLBGAME) — already
professionally market-made.

Nothing is being deleted: S1's canary detector, the S2/S3/S4 groundwork,
the event bus, order-book reconstruction, RiskGate, PaperExecutor, the
compliance and Telegram agents all remain as substrate.

Full spec — architecture, Kelly derivation, backtest design, and the
G1–G5 gates that must pass before any of it sees paper money — is in
`DECISIONS.md`, Session 30.

## The answer, 2026-08-02 (Session 31): NOAA loses to the market. S6 weather is dead.

The direction above was chosen because it could be **falsified cheaply and
offline**. It was, in one session. The backtest is in
[`backtest/`](backtest/README.md); raw output in `backtest/reports/`.

The question was never "is NOAA accurate" — it is — but "is NOAA, converted
to a probability, better calibrated than the Kalshi price itself?" It is not:

| Contested markets, out-of-sample, 36 independent dates | 12h lead | 24h | 30h |
|---|---|---|---|
| Brier — NOAA/NBM model | 0.2013 | 0.1795 | 0.1713 |
| Brier — **Kalshi price (the baseline)** | **0.1757** | **0.1612** | **0.1567** |
| Brier — climatology | 0.2104 | 0.1896 | 0.1812 |
| Brier skill vs market | −0.146 | −0.114 | −0.093 |
| P(model no better than market) | 1.000 | 1.000 | 1.000 |

Every confidence interval sits entirely on the wrong side of zero. **17 of 18
cities lose.** The model barely beats climatology while the market beats it
comfortably. Simulating the trades makes it concrete: the model claims **+$0.11
to +$0.17** of net edge per contract and **realises −$0.01 to −$0.04** — and
demanding a *bigger* disagreement with the market makes it worse, because a
bigger disagreement selects harder for the model being wrong.

**Why, measured rather than assumed** — this is what makes it final instead of
"needs more work". Recovering the market's implied expected temperature from
each city-day ladder and scoring both against the settled high:

| | NOAA/NBM | market-implied |
|---|---|---|
| point-forecast MAE @12h | 1.59 °F | **1.27 °F** |
| point-forecast MAE @24h | 1.77 °F | **1.47 °F** |

NBM's published *uncertainty* is close to correct (published SD ÷ realised
RMSE = 0.93). So the deficit is in the **forecast**, not the probability
conversion — and no better error model can recover an inferior mean. The Kalshi
weather market already prices a better temperature forecast than a raw NBM run.

The obvious objection — *the model was handed stale data* — is closed by
measurement, not argument: **NBM publishes no daytime-max forecast at less than
12 hours' lead.** The model loses at NOAA's freshest.

The honest reading: Session 30 picked weather because the forecast source and
the settlement source are the same agency. Read again, that is precisely the
property that guarantees every other participant is reading it too. Session
30's own stated counter-argument turned out to be the decisive one. The
generalisable lesson is now a screening question in `SIGNAL_REGISTER.md`:
**"is there a reason the market does not already know this?"**

**What this does not kill:** the `FairValueProvider` abstraction, the
divergence strategy shape, market-making (S8), or the S5a/S5b canary. One
provider on one market family failed, for a legible reason. Full record:
`DECISIONS.md`, Session 31.

**Cost of learning it:** one session. No order layer, no capital, no paper
trades, no live exposure. That was the whole argument for sequencing divergence
first, and it held.

## Canary log read for the first time, Health Monitor built, reset-loop root cause found, 2026-09-27 (Session 34)

Nobody had read the canary's own log since Session 32 deployed it 56 days
earlier. **The "zero candidates" claim below was wrong the whole time it sat
in this file** — 15,167 sweeps, 1 transient 429, and **493 distinct candidate
baskets found**, 95.2% confirmed on recheck. One hand-checked against the
live Kalshi API (the single largest, 38% edge) turned out to be a genuine
false positive — a new trap, not previously documented: Kalshi added a third
player's market to the event mid-window, breaking a partition that had
legitimately qualified against 31 clean settled events. See
`canary/README.md` and CLAUDE.md's KNOWN DEBT for the full writeup. Lesson:
95% of candidates holding up is real signal, but the one candidate actually
verified was false and was also the biggest — check before trusting size.

Also this session: **built the Health Monitor agent**
(`agents/monitoring/health_monitor.py`), closing one of the two named
market-making prerequisites — not yet deployed to the VPS. And **found the
root cause of the standing order-book reset loop**: Kalshi's `seq` field is
scoped per subscription batch (up to 50 tickers), not per individual market,
confirmed directly against Kalshi's AsyncAPI spec and a live VPS journal
capture (one market recovering 615x in 2 hours). Not a correctness bug today
— every "gap" still successfully re-fetches via REST — but it's the exact
log-volume pattern that caused the Session 26 disk-full outage. Fix is
scoped, deliberately not implemented this session (a live-system change, not
rushed). 326/326 tests passing.

## VPS spot-check, fee variance closed, Telegram /mute /unmute, 2026-08-29 (Session 33)

Housekeeping session: no strategy code touched. Three standing items from the
"Next up" list closed.

**VPS spot-check** — `git log -1` on the box matches local `main`
(`7ec5b3d`) exactly, `karbot` and `karbot-canary` are both active/enabled,
disk is 17% of 49G, and the canary's latest sweep (18:13 UTC) evaluated 3,049
events with zero candidates and zero errors — consistent with every sweep
since deployment. `telegram.enabled: true` was confirmed by reading
`config.yaml` on disk, not inferred from a log line, closing the specific
uncertainty Session 24 left open.

**Paper-trade fee variance, closed** — see the "Open questions" entry below
for the finding: it was two eras of trades (pre- and post-Session-26-fix)
in one table, not a live bug. Answering it also answered the older,
separately-tracked P&L-inflation item from Session 25.

**Telegram `/mute` `/unmute` shipped** — `TelegramNotificationAgent` now
tracks an in-memory `_muted` flag, toggled by the operator sending `/mute`
or `/unmute` (checked in `_handle_operator_reply`, short-circuiting before
the kill-switch and yes/no-permission paths, same pattern the kill switch
already used). Muting suppresses Tier 2 chatter only — trade opened, trade
resolved, rejected opportunity, and generic tier-2 notifications. Tier 1
(leg failure, feed health) and pending permission requests are unaffected
by design: the Session 20 feed-down alert exists specifically so an
inventory-bearing agent going dark is never optional to see. The flag is
in-memory only and resets to unmuted on every restart, so a forgotten mute
can't silently outlive a deploy. 16 new tests, 321/321 total passing.
Deployed to the VPS and the service was confirmed to restart cleanly with
no errors. **Live-confirmed same session** — the operator sent `/mute` and
`/unmute` from Telegram directly and confirmed it working.

## Open questions (flagged live, not yet resolved)

- ~~**What replaces S6?**~~ **Answered and built.** The S5a/S5b passive arb
  canary shipped in Session 32 as `canary/` — a standalone detect-and-log
  process, live-verified, and (per Session 34's first real read of its log)
  **493 candidates found, 95.2% confirmed on recheck** — not zero, as this
  file said until now. Market-making, a different `FairValueProvider`, and
  infrastructure consolidation all remain on the table to revisit; this was
  sequencing, not elimination. The larger direction question is still open.
- ~~**Does Kalshi refund a voided position at cost?**~~ **Answered — and the
  question was framed wrong.** Kalshi's own `rules_secondary` says a cancelled
  match *"will resolve to a fair price in accordance with the rules"* — neither
  a refund nor a zero. So the deciding question was a third one: do those fair
  prices sum to $1? **243 of 243 cancelled events across 8 series sum to exactly
  $1.00**, so the basket guarantee survives cancellation intact. Now checked per
  series rather than assumed.
- ~~**Is there a usable archive of past NWS forecasts?**~~ **Answered and
  used.** NBM on AWS (`noaa-nbm-grib2-pds`, anonymous, 2020→now). Better than
  expected: the bucket's `text/` suite publishes plain-ASCII *station*
  bulletins carrying forecast mean, spread **and** quantiles — so no GRIB2
  decoder and no grid interpolation are needed, and `backtest/` ships with zero
  new dependencies.
- **Every "CONFIRMED LIVE" claim in CLAUDE.md still needs re-auditing**
  against the VPS directly — the VPS was once found 4 commits behind
  `main` while docs claimed otherwise. **Spot-checked 2026-08-29 (Session
  33)**: `git log -1` on the VPS matches local `main` exactly, both
  `karbot` and `karbot-canary` services are active and enabled, disk is
  17% of 49G, and `telegram.enabled: true` was confirmed by reading
  `config.yaml` on disk directly (not inferred). This is a spot check, not
  the full line-by-line audit the item calls for — still standing.
- ~~**S5a/S5b viability**~~ — **Answered, Session 34: real, but verify before
  sizing.** 15,167 sweeps since deployment, 493 distinct candidates, 3,618
  confirmed / 182 vanished on recheck (95.2%) — exactly the ratio Session 32
  said would separate real resting arbitrage from a noisy view of the book,
  and it came back positive. But the one candidate actually hand-verified
  against the live API (also the largest, 38% edge) was a genuine false
  positive from a new failure mode — an event's leg count changing mid-window.
  Net: the canary works and is finding real, mostly-small, short-lived edges;
  do not act on any single candidate, especially a large one, without an
  independent live check first.
- **Which unconventional data sources actually predict anything** — see
  [`SIGNAL_REGISTER.md`](SIGNAL_REGISTER.md), a standing register of
  candidate signals (official weather-modification filings, ADS-B,
  solar/lunar/geophysical events, crowd-sourced claim data, Farmer's
  Almanac) with a hard statistical gate. Nothing in it is endorsed and
  nothing is dismissed — track record decides. The gate exists because the
  failure mode of an open mind on a small sample isn't wasted time, it's
  confidently trading noise.
- **S1's liquidity cap is top-of-book only**, not a full multi-level
  depth walk — moot while S1 is canary-mode-only, but relevant again if
  the reconstruction bug is ever fixed.
- ~~**RiskGate dollar/quantity unit mismatch**~~ — **fixed in Session 30**;
  sizing now returns integer contracts. Listed here in error after the fix.
- ~~**Paper trade fee variance**~~ — **investigated and closed, Session 33
  (2026-08-29)**. Pulled all 757 rows from `compliance.db` directly. The
  split is real but isn't "old formula vs new formula" the way it first
  looked: 312 rows show exactly `fee_paid=70.0` and a long tail of other
  large values ($15–$330) — **all of these are the pre-Session-26 flat-14%
  formula at different Kelly-derived position sizes**, not a broken new
  formula; $70 is just the single most common size ($500) landing on a
  round number. The last such row is 2026-07-13T19:09 UTC. Every row after
  that has a tiny fee (under $2.30) — exactly the 5 trades Session 27
  already described as "$0.05–$81.36" positions. So there was never a
  live bug in the corrected fee formula; the "variance" was two different
  eras of trades sitting in the same table. This also closes the older,
  separately-flagged **P&L-inflation KNOWN DEBT item (Session 25, "HIGH
  PRIORITY, NOT YET RE-VERIFIED")**: the cited $58–$288 range is exactly
  the pre-fix era, not a still-open regression.
- **New, found while investigating the above**: `compliance.db`'s `trades`
  table has `filled_price`, `quantity`, and `ordered_price` **NULL on all
  757 rows** — every trade ever recorded. The Session 16 fix documented in
  CLAUDE.md only touched the CSV-writing path (`kalshi_trades.csv`); the
  `compliance.db` INSERT path never received the same fix. Not fixed this
  session (out of scope for a fee-variance check) — flagged as new debt.

Two other items flagged earlier the same session were fixed before this
list needed to carry them: the `size_usd=0.0` approved-trade bug
(RiskGate now rejects a non-positive approved size instead of executing
it — `ZERO_APPROVED_SIZE`) and the secrets policy deviation (`.env`
moved to `/etc/karbot/secrets/karbot.env`, `chmod 600`, matching the
private key's existing convention; old repo-directory copy deleted after
confirming the service ran cleanly from the new path).

Also added the same session: `s1_candidate_seen` visibility logging —
every S1 candidate that clears zero gross spread now logs its
gross/fee/net breakdown regardless of whether it clears the trading
threshold, so the operator can judge real-world viability from a few
hours of logs instead of waiting days for an actual trade.

## Next up

**Phase 0 — prerequisites** *(3 of 4 done, 2026-08-02)*

1. ✅ **NOAA forecast-archive question answered — and the backtest built and
   run.** NBM archive on AWS (`noaa-nbm-grib2-pds`, anonymous, 2020→now).
   The `text/` suite turned out to publish per-station bulletins with forecast
   mean, spread *and* quantiles in plain ASCII, so no GRIB2 decoder was needed.
   Kalshi supplies settled outcomes (`expiration_value` **is** the observed
   high) and `candlesticks` price history with bid *and* ask.
2. ✅ **RiskGate dollar-vs-contract unit mismatch fixed.** Sizing now
   returns integer contracts derived from real per-contract basket cost.
   Riskless strategies size against caps (removing a hidden ~5.26%
   minimum-edge floor); statistical strategies use `f* = (p − c)/(1 − c)`
   fed by a real model probability, and size to zero rather than guess.
3. ✅ **`from_yaml()` now parses `capital:`, `risk:`, `strategies:`,
   `data_feeds:`, `intelligence:`** — and warns on unknown keys, so a
   mistyped setting can't look configured while doing nothing.
4. ⬜ Make paper resolution settle against real market outcomes.
   `PaperExecutor` currently resolves every trade at its own expected P&L,
   which is tautological for a directional strategy. **Re-scoped Session 31**:
   this was a prerequisite for S6 paper trading, which no longer exists, so it
   blocks nothing today — and becomes blocking again the moment any
   variance-bearing strategy reaches paper.

**Phase 1 — measure before building** ✅ *done, result negative*

5. ✅ `backtest/` harness built and run; **`FairValueEngine` and the NOAA
   provider deliberately were NOT built**, because the calibration report came
   back negative before either was justified. G1 pass, **G2 fail**, G3 confirms.

**Phase 2 — detect and log** — closed, never started

6. ~~`DivergenceScanner` live for 1–2 weeks~~ — its purpose was to confirm a
   backtested edge reproduces live. There is no edge to reproduce.

**Phase 3 — arb canary** ✅ *built and live-verified, Session 32*

7. ✅ **S5a/S5b basket/ladder canary shipped as `canary/`** — a standalone
   detect-and-log process. Everything the Session 28 spec asked for, plus three
   things it did not anticipate, all found live: relations are gated on
   **settled history** rather than strike arithmetic (one Kalshi event can hold
   two different metrics, and interval logic will "prove" a false implication —
   2,267 measured violations on KXMLBSPREAD); the bulk price snapshot goes
   **stale within seconds**, so every candidate is re-priced from `/orderbook`
   before being logged; and some events settle **neither YES nor NO**
   (`result: "scalar"` — 4.1% of ATP matches), which breaks the payout guarantee
   outright. Session 31's weather finding held up independently.

7a. ✅ **Deployed** as `karbot-canary.service` on the VPS (enabled, active,
    `Restart=always`, `Nice=10`), sweeping every 5 minutes. Deploying found two
    things nothing else would have: an **undeclared `requests` dependency**, and
    that the dev machine (Python 3.14) and the VPS (3.10) **disagree on
    floating-point summation** — CPython 3.12 gave `sum()` compensated
    summation — so "all tests pass locally" was never evidence about production.
7b. ✅ **Void-settlement question answered**, see Open questions above.

7c. ✅ **Watchdog installed** — `karbot-canary-alert.sh` runs from cron every 15
    minutes and sends Telegram if the canary stalls for 20 minutes or if a real
    candidate appears. Both alert paths were exercised with real sends before
    being trusted; an untested watchdog is worse than none, and this project's
    previous one was silently broken for three sessions.

**Now next**

7d. **Read the log once it has run for a while.** The measurement that matters
    is the **`confirmed` vs `vanished_on_recheck` ratio**, not just the
    candidate count — that separates real resting arbitrage from a noisy view of
    the book. Heartbeat check:
    `tail -1 logs/basket_candidates.jsonl | python3 -m json.tool`.
7e. **Send the Kalshi market-maker enquiry** —
    `documentation/kalshi-mm-enquiry-draft.md`, drafted but not sent. Four
    questions whose answers materially change the market-making decision,
    chiefly whether the programme is open to individual participants at all.
    Asking costs minutes; building the order layer costs sessions, and
    market-making **cannot be falsified offline at all**.
7f. **Then the two infrastructure prerequisites** — the Health Monitor
    (dead-lettered `AgentHeartbeat`) and the stuck order-book reset loop. These
    are on market-making's critical path rather than alternatives to it: a
    quoting system whose agent silently dies holds inventory nobody is managing,
    and a maker with a stale book quotes a price someone will take. Full
    reasoning, with the honest counter-argument, in DECISIONS.md Session 32.

**Standing**

8. Re-audit every "CONFIRMED LIVE" claim against the VPS directly, and
   schedule its pending reboot / security updates. *Spot-checked
   2026-08-29 (Session 33) — see above; full line-by-line audit still
   outstanding.*
9. Build the Health Monitor agent — no longer cosmetic once positions
   carry real variance.
10. Investigate the stuck order-book reset loop; a concurrency limiter on
    `_request_snapshot`; the newly-found `compliance.db` NULL
    `filled_price`/`quantity`/`ordered_price` gap. ~~The paper-trade fee
    variance~~ and ~~Telegram `/mute` `/unmute`~~ — both done Session 33.
11. Live executor, then market-making — gated, last. Target the
    zero-maker-fee series, not the headline ones.

## License

MIT
