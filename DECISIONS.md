# Decision Log
# Entries are ordered newest-to-oldest. Most recent decision is at the top.

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
