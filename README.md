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
  exhausted); and `TelegramNotificationAgent` now sends an immediate alert
  on feed disconnect/reconnect.
- **Known minor issue, not urgent**: right after a restart, when many
  markets need recovery at once, a small fraction (~5.5% observed) of REST
  snapshot fetches hit Kalshi's rate limit. Handled safely (retried on the
  next throttle window) but a concurrency limiter is a flagged follow-up.

See DECISIONS.md and SESSIONS.md for full session-by-session detail.

## Next up

1. Add a concurrency limiter (`asyncio.Semaphore`) on `_request_snapshot`
   REST calls to smooth the post-restart burst noted above — not urgent.
2. Telegram `/mute` `/unmute` operator commands.
3. Begin live executor spec once the 30-day paper run completes (2026-07-29).

## License

MIT
