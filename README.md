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
```

The legacy `python main.py` path still works but is intentionally not extended — it bypasses the event bus.

## Current phase: Phase 1

- Kalshi is the primary data source; Polymarket is gated behind `polymarket_ws_enabled` (disabled in Phase 1)
- Phase 1 invariants are enforced structurally in `KarbotConfig.__init__` — enabling Polymarket WebSocket or cross-platform strategies while `phase=1` raises `ValueError` at startup
- Paper trading mode only; 30-day paper trading clock started 2026-06-29, target live date 2026-07-29; live execution deferred until it completes and end-to-end results are reviewed

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

## Open questions (flagged live, not yet resolved)

- **P&L magnitude — now root-caused, HIGH PRIORITY, live-confirmed still
  broken (2026-07-13)**: immediately after a clean restart with fresh code
  and a fresh disk, live `opportunity_approved` events showed `net_pct` of
  20.7%, 31.7%, 54.7%, 61.7%, 47.7% — against a realistic 1–5% S1 benchmark
  — firing in the same seconds as `sequence_gap_detected` warnings.
  `agents/floor/arb_scanner.py` has a lower-bound rejection on `net_pct`
  but **no upper-bound sanity check at all**, so a stale/corrupt order book
  can trivially produce a fake huge spread that gets traded on. This is now
  understood to be the actual mechanism, not a measurement question. **Top
  priority before live trading** — needs an upper `net_pct` ceiling and/or
  order-book-freshness gating in `ArbScanner`. Not yet fixed.
- **Paper trade fee variance**: fee amounts shown in Telegram trade
  messages vary in a way that hasn't been explained yet (flat $70, or
  $0–$113 depending on the trade) — needs a cross-check against the fee
  calculation logic and `compliance.db` before assuming it's correct. Not
  investigated yet.
- **Secrets policy deviation on the live VPS**: `karbot.service`'s
  `EnvironmentFile=/home/ubuntu/karbotrage_v1/.env` violates this
  project's own stated rule that secrets should be injected from outside
  the repo directory (e.g. `/etc/karbot/secrets/`). Found 2026-07-13, not
  yet fixed.

## Next up

1. Fix `ArbScanner`'s missing upper-bound sanity check / add order-book
   freshness gating — this is the real blocker on the P&L question above.
2. Investigate the stuck order-book reset loop (why some markets never
   complete recovery).
3. Move `.env` off the repo path on the VPS to close the secrets-policy gap.
4. Re-audit every other "CONFIRMED LIVE" claim in CLAUDE.md against actual
   VPS state, not just prior session notes.
5. Investigate the paper-trade fee variance noted above.
6. Continue live-verifying Telegram alerting (feed-down/recovered,
   restart-budget-exhaustion) now that the duplicate regulatory message is gone.
7. Add a concurrency limiter (`asyncio.Semaphore`) on `_request_snapshot`
   REST calls to smooth the post-restart burst noted above — not urgent.
8. Telegram `/mute` `/unmute` operator commands.
9. Begin live executor spec once the 30-day paper run completes (2026-07-29)
   — note the run has a confirmed dead zone from 2026-07-04 to 2026-07-13
   where persistence was broken; don't count that window as clean data.

## License

MIT
