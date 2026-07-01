# Decision Log
# Entries are ordered newest-to-oldest. Most recent decision is at the top.

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
