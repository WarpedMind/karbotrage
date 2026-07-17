# Karbot Rage! - Automated Trading System

## What this is
Karbot Rage! is a multi-agent automated trading system designed for decentralized prediction markets. It provides a modular framework with specialized agents for market monitoring, analysis, strategy execution, and compliance.

## Stack
- Python 3.8+
- Modular architecture with core, execution, data, intelligence, strategies, trading, and monitoring components
- Run with: `karbotrage_env/bin/python karbot_runner.py` (new path) or `python main.py` (legacy)

## SECURITY RULES — non-negotiable, apply to every session

### Secrets
- Credentials load from environment variables only — never from config.yaml, never hardcoded
- SecretsConfig in karbot/core/config.py is the only place secrets are read from environment
- Agents read credentials from config.secrets.* — never os.environ directly
- config.yaml is in .gitignore — only config.yaml.example is committed
- .env is in .gitignore — never committed under any circumstances

### Logs
- No credential values, API keys, tokens, or private key paths in any log output
- No SecretsConfig field values ever logged at any level
- Prompt text sent to Claude API is logged at DEBUG only, never INFO or above
- audit_trail.jsonl and kalshi_trades.csv contain trade data only — no auth material

### Git
- Before every commit: confirm no .env, no config.yaml, no *.pem in staged files
- If a secret is ever accidentally committed: rotate the credential immediately,
  then remove from git history with git filter-repo

### VPS (when provisioned)
- Private keys stored in /etc/karbot/secrets/, chmod 600, owned by service user
- Bot runs as dedicated karbot_user — never as root
- Secrets injected via systemd EnvironmentFile — not from .env inside the repo directory

### New credentials (Kalshi RSA, future exchanges)
- Generate RSA key pairs locally — never on the VPS, never online
- Upload public key only to the exchange
- Private key goes directly to /etc/karbot/secrets/ — nowhere else

## Architecture

### Target architecture (event-bus-driven agents — extend this, not the legacy path)
- karbot_runner.py: **NEW entry point** — starts all 10 Phase 1 agents as concurrent asyncio tasks; verified working. Use this, not main.py. `_run_supervised_with_restart()` added Session 20 — general-purpose capped auto-restart (fixed delay, restart budget within a rolling window, then a CRITICAL Telegram alert + permanent stop); wired only to `PriceWatcher` via `isinstance(agent, PriceWatcher)` in the task-creation loop — every other agent still uses the original `_run_supervised()` (unchanged, fire-once, no restart) — DEPLOYED BUT NOT YET CONFIRMED LIVE, see KNOWN DEBT. `config_resolved` startup log added Session 24 — logs the actual resolved value of every subsystem enable/disable flag (`telegram_enabled`, `kalshi_ws_enabled`, `polymarket_ws_enabled`, `regulatory_intelligence_enabled`, `paper_mode`, `phase`) once, right after config load and before any agent starts — closes the "silent no-op with no error" gap that let `telegram.enabled=False` go undetected across 3 live deploys.
- core/events.py: EventBus + all typed event dataclasses — the communication backbone; priority queue uses 3-tuple (priority, seq, event) to avoid heapq comparison errors between same-priority events.
- karbot/core/: Package exists — agents import from here
  - karbot/core/config.py: KarbotConfig typed dataclass; Phase 1 invariants enforced structurally at `__init__` — `polymarket_ws_enabled=True` with `phase=1` raises `ValueError`, `s2_cross_platform_enabled=True` with `phase=1` raises `ValueError`; RiskConfig hard limits also enforced at instantiation. Now also has `from_yaml(path)` classmethod, `.phase` property (→ capital.phase), and `.paper_mode` property (→ system.paper_mode). TelegramConfig + RegulatoryIntelligenceConfig sub-dataclasses added. SystemConfig gained `agent_restart_delay_seconds` (30), `agent_restart_max_count` (3), `agent_restart_window_minutes` (60) Session 20 — configures karbot_runner.py's capped auto-restart.
  - karbot/core/events.py: Re-exports all event types from core/events.py
- agents/floor/price_watcher.py: `PriceWatcherAgent` (full impl) + `PriceWatcher` (inherits it); RSA-PSS/SHA-256 auth via `cryptography` against `api.elections.kalshi.com` (migrated from `trading-api.kalshi.com` + PKCS1v15 in Session 13); `run()` connects to real Kalshi WS when credentials present, idles gracefully when absent; batched market subscription (50/message); `_fetch_active_kalshi_markets()` sends `mve_filter=exclude` (Kalshi's catalog is otherwise 12,000+ consecutive zero-volume multi-variable-event markets) and paginates via `cursor` (20-page cap) as a secondary safeguard, filtering on `volume_24h_fp` — confirmed live (Session 15, count=785/4000); `_handle_kalshi_snapshot`/`_handle_kalshi_delta`/`OrderBook.apply_delta` rewritten for the real WS schema (Session 15 — payload nested under `msg["msg"]`, `yes_dollars_fp`/`no_dollars_fp` are bid-only books with NO bids deriving YES asks at `1-p`, `delta_fp` is a RELATIVE change not absolute) — NOT YET reverified live, see KNOWN DEBT; `_request_snapshot` added (Session 17 follow-up 3) — originally a WS re-subscribe on sequence gap, throttled 10s/market; unique per-call `id` (was hardcoded 99) added Session 18 to fix a suspected response-correlation collision; `book_needs_reset` log demoted warning→debug same session; **REPLACED Session 22, auth removed Session 23 — CONFIRMED LIVE** — live wire capture (Session 21) confirmed Kalshi acks a duplicate WS subscribe with `{"type":"ok"}`, never a fresh snapshot, so the WS re-subscribe path could never have worked; `_request_snapshot` now makes an unauthenticated `aiohttp` GET to `/trade-api/v2/markets/{ticker}/orderbook` (Session 22 added RSA-PSS auth headers defensively without verification; that per-call blocking crypto/file-I/O stalled the event loop under load and crashed PriceWatcher 3x/~8min via missed WS pings — Session 23 removed auth entirely, confirmed live: 200 status, 1,764 `book_snapshot_applied`/2.5min, zero crashes), parses `orderbook_fp.yes_dollars`/`no_dollars`, and calls `apply_snapshot(bids, asks, seq=0)` directly (sentinel `seq=0` short-circuits `apply_delta`'s gap check so the next delta naturally realigns); 10s throttle and connected-guard unchanged; uses a shared `aiohttp.ClientSession` (`_get_rest_session`, closed in `stop()`) instead of one per call; REST failures (incl. an observed ~5.5% 429 rate right after restart, KNOWN DEBT) log `book_reset_rest_failed` and leave `_gap_detected=True` for a throttled retry; `_kalshi_connection_loop`'s `@retry` `before_sleep` fixed Session 19 (was `before_sleep_log(log, "WARNING")`, crashed on every retry attempt because `log` is a structlog logger, not stdlib — see KNOWN DEBT) — DEPLOYED BUT NOT YET CONFIRMED LIVE; agent-level restart after `stop_after_attempt(10)` exhaustion — RESOLVED Session 20 (operator decided: capped runner-level auto-restart, see karbot_runner.py entry below) — DEPLOYED BUT NOT YET CONFIRMED LIVE; `_handle_health_change`/`FeedHealthEvent` gained an optional `error` field Session 20 so Telegram alerts can include the underlying disconnect error
- agents/floor/arb_scanner.py: `ArbScannerAgent` (full impl, has register_subscriptions) + `ArbScanner` (inherits it); `run()` starts heartbeat + cache-cleanup tasks then idles; S1 opportunity detection fully wired
- agents/floor/risk_gate.py: `RiskGateAgent` (full impl, has register_subscriptions) + `RiskGate` (inherits it); `run()` starts heartbeat task then idles; subscribes to RegulatoryAlertEvent; _regulatory_pause=True blocks all trades when urgency=5; cleared by urgency=0 event from RegulatoryIntelligenceAgent
- agents/research/market_analyst.py: `MarketAnalystAgent` (full impl) + `MarketAnalyst` (inherits it); `run()` starts LLM analysis loop (5-min), heartbeat, cache-cleanup; no-op when ANTHROPIC_API_KEY absent; uses `AsyncAnthropic` (migrated from synchronous client in Session 14)
- agents/research/regulatory_intelligence.py: **NEW COMPLETE** — `RegulatoryIntelligenceAgentImpl` (full impl) + `RegulatoryIntelligenceAgent` (BaseAgent stub); polls CFTC RSS + Federal Register every 6h; keyword pre-filter controls API costs; Claude Sonnet assesses urgency 1-5; urgency 3→Telegram FYI, 4→Telegram alert, 5→Telegram + trading pause; operator sends clear phrase via Telegram to resume; weekly sweep skips keyword filter; daily/cycle caps + circuit breaker; overflow queue for items exceeding per-cycle cap
- agents/management/reflection.py: `ReflectionAgentImpl` (full impl) + `ReflectionAgent` (inherits it); `run()` starts nightly scheduler (02:00 ET / 07:00 UTC) + heartbeat; uses `AsyncAnthropic` (migrated from synchronous client in Session 14); reads/writes `logs/compliance.db` (trades, rejections, audit_trail tables — created Session 14)
- agents/management/compliance.py: **v4 UPDATED** — IRS dual-track logging, append-only audit trail, compliance action log, REGULATORY_HALT enforcement; **polling loop removed** (now handled by RegulatoryIntelligenceAgent); subscribes to RegulatoryAlertEvent to log AI-assessed alerts to compliance_actions.jsonl; subscriptions wired to TradeExecutedEvent, TradeResolvedEvent, LegFailureEvent, RejectedOpportunityEvent, RegulatoryAlertEvent; TradeExecutedEvent handler INSERTs per-trade row into compliance.db (INSERT OR IGNORE, real-time); TradeResolvedEvent handler updates kalshi_trades.csv (atomic read-modify-write, gain_loss split across legs, status=RESOLVED) and UPDATEs compliance.db row; _ensure_log_files bootstraps compliance.db schema (trades/rejections/audit_trail) at startup so DB is always ready
- agents/notifications/telegram_agent.py: **UPDATED** — TelegramNotificationAgent (full impl) + TelegramAgent (BaseAgent stub); subscribes to TelegramNotificationEvent, TelegramPermissionRequestEvent, LegFailureEvent (Tier 1), TradeExecutedEvent (Tier 2), RejectedOpportunityEvent (Tier 2), FeedHealthEvent (Tier 1, Session 20); getUpdates polling every 3s; 1 msg/sec rate limit; single-operator FIFO permission resolution; always publishes TelegramPermissionResponseEvent with response_text so RegulatoryIntelligenceAgent can check for clear phrase; enabled=False → no-op (no HTTP calls, no polling); `_handle_feed_health` (Session 20) tracks last-known connected state per platform and alerts only on connected→disconnected/disconnected→connected transition for platform="kalshi", ignoring other platforms — **Session 24 root cause: `telegram.enabled` has been `False` in production the entire time (no `config.yaml` existed on the VPS) — every Telegram feature since Session 19 has NEVER ACTUALLY FIRED live, not "pending verification."** **Session 25: RegulatoryAlertEvent subscription + `_handle_regulatory_alert` REMOVED** — was producing a second, broken, duplicate Telegram message for every regulatory item (blank `source_name`/`matched_keywords`, referenced a deleted `logs/regulatory_alerts.txt`, hardcoded "CRITICAL" regardless of actual urgency) alongside `RegulatoryIntelligenceAgent`'s already-correct urgency-branched message; found via tonight's first-ever live Telegram run. `RegulatoryAlertEvent` still publishes for `ComplianceOfficer`'s logging — only the Telegram consumer was removed.

### BaseAgent interface (all runner-facing classes implement this)
```python
def __init__(self, bus: EventBus, config: KarbotConfig): ...
def register_subscriptions(self): ...
async def run(self): ...
```

### Legacy execution path (do not extend — removal blocked on paper test)
- main.py / karbot/main.py: Old entry point — leave untouched
- execution/engine.py: Monolithic orchestrator — calls components directly, bypasses event bus — **INTENTIONALLY DEFERRED**: do not touch until paper tested end-to-end
- data/market_data.py: Market data (Kalshi-first, Polymarket gated behind polymarket_ws_enabled)

## Current status
- karbot_runner.py: **Written and verified** — supports --mock-prices and --exit-after-test flags; 10 agents start and exit cleanly; `_run_supervised()` wrapper prevents single-agent crash from killing the runner; `return_exceptions=True` on main gather; continuous paper mode confirmed working (no credentials required); PaperExecutor now in continuous paper mode agent list; exit cleanup cancels all background sub-tasks (zero "Task was destroyed" warnings)
- core/events.py: Full event bus with all typed events — production-ready; RegulatoryAlertEvent has full AI-assessment fields (urgency, summary, affected, recommended_action, raw_title, cycle_type); TelegramPermissionResponseEvent has response_text field; priority queue fixed with sequence tiebreaker
- karbot/core/config.py: KarbotConfig Phase 1 invariants structural + from_yaml() + .phase + .paper_mode + regulatory_halt + TelegramConfig + RegulatoryIntelligenceConfig + SecretsConfig sub-dataclasses; SystemConfig.paper_resolution_delay_seconds added
- agents/research/regulatory_intelligence.py: **COMPLETE** — full Regulatory Intelligence Agent; 11 tests passing; Claude Sonnet urgency assessment; cost controls (daily cap, circuit breaker, overflow queue, spend estimator); operator clear flow via Telegram
- agents/management/compliance.py: **v4 UPDATED** — see Architecture section above for full feature list (TradeResolvedEvent wired, real-time DB INSERT, compliance.db bootstrap)
- agents/floor/risk_gate.py: **UPDATED** — subscribes to RegulatoryAlertEvent; _regulatory_pause blocks trades on urgency=5; cleared on urgency=0
- agents/notifications/telegram_agent.py: **UPDATED** — response_text field populated on every operator message for clear phrase detection
- agents/floor/paper_executor.py: **UPDATED** — paper trading fill simulator; subscribes to ApprovedOpportunityEvent, emits TradeExecutedEvent(paper_mode=True); schedules TradeResolvedEvent via asyncio.create_task after paper_resolution_delay_seconds (default 300s)
- agents/floor/mock_price_watcher.py: **COMPLETE** — fixture-driven price replay for end-to-end tests; signals done via asyncio.Event; 0.1s initial delay ensures PositionSnapshot is dispatched before first price
- agents/floor/position_tracker.py: **Phase 2 COMPLETE** — subscribes to TradeExecutedEvent, TradeResolvedEvent, LegFailureEvent; deployed_capital_usd, open_positions, daily_trades, daily_pnl all update in real time; daily UTC reset; publishes snapshot on every state change; correlation_score=0.0 (Phase 3 item)
- tests/test_paper_trading.py: **UPDATED** — 5 scenarios passing (happy path, rejection, no-opportunity, resolve-after-delay, full P&L cycle)
- tests/test_position_tracker.py: **COMPLETE** — 9 tests passing; includes integration test confirming Risk Gate enforces capital limits against real deployed capital
- tests/test_regulatory_intelligence.py: **COMPLETE** — 11 tests passing; all mocked (no real API calls)
- tests/fixtures/paper_test_prices.json: **COMPLETE** — 3 price snapshots for test scenarios
- All Phase 1 agent stubs: Conforming run() and register_subscriptions() on all 10 runner-facing classes
- requirements.txt: aiohttp, pydantic, websockets, pyyaml, python-json-logger, structlog, tenacity, aiosqlite, anthropic, pytest, pytest-asyncio, black, flake8, python-dotenv
- execution/engine.py: INTENTIONALLY DEFERRED — do not refactor until paper tested end-to-end
- SecretsConfig: implemented — all credentials load from environment variables only ✓
- config.yaml: moved to .gitignore; config.yaml.example + .env.example committed ✓
- Paper trading: End-to-end tested ✓ (kalshi_trades.csv confirmed populated)
- TradeResolvedEvent: wired via PaperExecutor — full paper P&L cycle closes ✓
- compliance.py `_build_trade_row`: FIXED (Session 16) — was reading
  nonexistent flat fields from `TradeExecutedEvent` (every CSV field was
  empty/zero since Session 8). Now reads from `event.platform_legs`; writes
  one row per leg with real market_id, side, quantity, price, fees.
  Confirmed live on VPS: real Kalshi trades (PGA, World Cup, tennis, MLB)
  writing correctly with full data to kalshi_trades.csv ✓
- **30-day paper trading clock: STARTED 2026-06-29. Target live date: 2026-07-29.**
- Full test suite: 83/83 passing ✓ (49 baseline + 4 S17 + 2 S17-fu2 + 4 S17-fu3 + 4 S18 + 2 S19 + 4 S20-telegram + 3 S20-restart + 2 S22-net + 4 S22-new + 4 S23 + 1 S24 + 3 S25)
- Kalshi market volume filter: FIXED AND CONFIRMED LIVE (Session 15) —
  `_fetch_active_kalshi_markets()` sends `mve_filter=exclude`, paginates
  via `cursor`, filters on `volume_24h_fp` (cast to float). Live VPS
  confirmation: `kalshi_markets_fetched count=1217 total=4000` (volume
  fluctuates; an earlier check the same session showed count=785), and
  `kalshi_markets_subscribed total=1217` with a successful Kalshi ack.
- Kalshi WS message schema (snapshot/delta handlers + `OrderBook.apply_delta`):
  FIXED AND CONFIRMED LIVE (Session 15) — even with subscription
  working, zero order book activity was initially observed for 15+
  minutes despite a healthy TCP socket. Root cause: handlers assumed a
  schema (`market_ticker` at top level, `yes.bids`/`yes.asks`) that
  doesn't exist on the wire — every message was silently dropped before
  any log fired. Rewrote against the real schema, confirmed via Kalshi's
  WS docs plus live captured traffic (payload nested under `msg["msg"]`;
  `yes_dollars_fp`/`no_dollars_fp` are bid-only books, NO bids derive
  YES asks at `1-p`; `delta_fp` is a RELATIVE size change, confirmed via
  a live matched +523.00/-523.00 pair). Live VPS confirmation:
  `kalshi_first_price_update` fired ~2 seconds after subscribing
  (`market=KXITFWMATCH-26JUN28MAQVAN-MAQ side=no`) — real order book
  data is now flowing end-to-end for the first time.
- VPS (`karbot-rage-prod`, 147.224.209.18): SSH access confirmed working;
  Session 13 Kalshi fix deployed and verified live — `kalshi_ws_connected`
  and `kalshi_markets_fetched` both confirmed in logs, zero auth errors ✓
- Git remote URL: CONFIRMED CORRECT on local (`origin` =
  `github.com/WarpedMind/karbotrage.git`) and FIXED on VPS this session
  via `git remote set-url origin https://github.com/WarpedMind/karbotrage.git`
  (VPS was still on the old `karbotrage_v1.git` URL, working only via
  GitHub's redirect). Verified working on VPS with a live `git fetch`.
  Local directory name `~/Projects/karbotrage/karbotrage_v1/` does NOT
  need to match the GitHub repo name (`karbotrage`) — this is normal;
  only `git remote -v` matters, and it's correct on both sides now.
- compliance.db: created at `logs/compliance.db` (local + VPS) with
  `trades`, `rejections`, `audit_trail` tables — schema matches what
  `ReflectionAgentImpl` actually queries (status, timestamp, resolved_at
  columns); ReflectionAgent nightly cycle can now run without failing ✓;
  ComplianceOfficer now bootstraps the schema at startup (`CREATE TABLE IF
  NOT EXISTS`) and INSERTs a FILLED row on every TradeExecutedEvent in
  real time (Session 17 follow-up 2) — DB no longer depends on nightly
  cycle for data ingestion

## KALSHI API NOTES (2026-06-27)
- Kalshi migrated their API from `trading-api.kalshi.com` to
  `api.elections.kalshi.com` and now requires RSA-PSS signing
  (was RSA-PKCS1v15). Both changes are live in
  agents/floor/price_watcher.py as of Session 13. If Kalshi auth ever
  fails again, verify against the live API directly (e.g.
  `/trade-api/v2/portfolio/balance`) before assuming which part broke —
  do not assume domain and signing scheme change together without
  confirming each independently.

## KNOWN DEBT

### S5a/S5b checked against real live data BEFORE building — neither shows a currently-exploitable edge, Session 29 (2026-07-16)
Before writing any S5a/S5b code, ran the same empirical-first discipline
that killed S1: pulled 1,600 real open markets, checked all 78 naive
sum-to-one candidates against the actual `mutually_exclusive` event
flag — **0 of 78 are real** (all are threshold/spread/total ladders
misidentified as basket markets by summing same-`event_ticker` markets
without checking the flag, the exact trap Fable's own spec warned
against). Then computed S5b's real arb condition properly across 8
diverse live threshold ladders (temperature ×5, gold, silver, oil) —
closest any got to a real crossing was 1.01, none went below 1.00.
Neither strategy has an obviously-sitting opportunity right now. Doesn't
rule out either existing rarely (a snapshot can't see time-varying
windows) or S5a existing on genuine winner-take-all events not sampled
here — but there's no free lunch quietly waiting either. Full numbers:
SESSIONS.md Session 29 addendum. **Decision needed before more building
effort**: invest in detect-and-log mode over 1-2 weeks to catch rare
windows, search more specifically for real mutually-exclusive events, or
reconsider direction (market-making per Session 28's S8 note).

### Session 28 (2026-07-16) strategy/architecture review — full findings in DECISIONS.md (5 entries) and SESSIONS.md Session 28; summary here
Review-only session (no code changed). Headline findings, each with a
full DECISIONS.md entry:
1. **S1 single-market arb is structurally impossible on Kalshi — CONFIRMED LIVE, Session 29 (2026-07-16)**.
   the opportunity condition `yes_ask+no_ask<1` is algebraically
   identical to `yes_bid+no_bid>1`, a crossed book, which Kalshi's
   unified price-time-priority matching engine never lets rest (a NO
   bid IS a YES ask in the same book). Session 29 ran the verification
   plan: (1) 0/778 real markets pulled live via REST show a crossed
   book; (2) all 5 of Session 27's paper trades correlate at 100% (same
   second) with a `sequence_gap_detected` event on that exact market.
   Not "argued" anymore — confirmed. **Fixed same session**: S1 is now
   canary-mode-only by default (`s1_canary_mode=True` in
   `StrategiesConfig`) — still detects and logs candidates as a
   data-quality signal, never publishes a tradeable `OpportunityEvent`.
   Do not set `s1_canary_mode=False` until the underlying
   reconstruction bug (stuck reset loops / mid-match multi-delta races)
   is actually fixed and independently re-verified.
2. **S2/S3/S4 audit**: all three price the wrong side of the book (same
   class as Session 26's S1 bug — S2 sums bids for a buy, S3/S4 buy at
   `yes_bid`); S3's input pipeline has never run
   (`MarketAnalyst.update_markets()` has zero callers → `_active_markets`
   always empty → zero LLM calls ever — "0 candidates" is a wiring fact,
   not a market fact) and inflates edges on empty books (`yes_bid=0.0`
   reads as a giant edge); S2's exact-`market_id` cross-platform match
   can never hit and its Polymarket fee model is outdated; S4 is dead
   code behind an enabled-by-default flag (no NewsSignalEvent publisher)
   and is directional, not arb. Also: `ReflectionAgent`'s strategy
   weights are stored by ArbScanner and never read — the learning loop's
   output is a dead knob.
3. **RiskGate unit mismatch confirmed and traced**: Kelly outputs
   dollars; PaperExecutor/PositionTracker consume the same number as
   contract quantity; it only balanced because an S1 pair costs ≈ $1.
   Worse: Kelly at p=0.95 imposes a hidden **~5.26% minimum net edge**
   (making `s1_min_net_profit_pct=0.5` a dead letter) — exactly
   backwards for arb, where small edges are the real ones. Plus:
   `capital_required_usd` is never set (RiskGate check 2 has never
   run), quantities must be integer ≥1 live (0.05-contract paper trades
   are impossible on Kalshi), and Kalshi fees round UP to the next cent
   (the continuous fee model is optimistic exactly on the tiny
   liquidity-capped orders this system does).
4. **SECURITY — Telegram accepts commands from any sender — sender-auth FIXED, Session 29 (2026-07-16)**:
   chat_id was never checked on inbound messages; anyone who found the
   bot could approve pending permission requests and clear an
   urgency-5 regulatory halt (default clear phrase is committed in the
   public repo). Fixed: `_is_authorized_sender()` checks `msg.chat.id`
   against `TELEGRAM_CHAT_ID` before dispatching to
   `_handle_operator_reply`; unauthorized messages are dropped and
   logged. 4 new tests. **Not yet fixed**: the VPS's `config.yaml`
   still uses the default, publicly-known clear phrase — confirmed live
   Session 29, needs rotating to a non-default value. **Also confirmed
   and fixed Session 29**: `karbot-disk-alert.sh` was indeed still
   pointing at the `.env` path Session 26 deleted — the disk-space
   watchdog had been silently non-functional since then (grep-on-missing-
   file swallowed by `|| true`). Fixed on the VPS directly, verified
   live with a real test send. **Still open**: token can leak into logs
   via raw aiohttp exception text; kill switch has no trigger path
   anywhere (no publisher, no caller); VPS runs as `ubuntu`, not the
   dedicated user these rules require.
5. **Strategy roadmap**: the real successors to S1 are **S5a event
   sum-to-one baskets** (Kalshi does NOT atomically match across an
   event's N markets — YES-basket needs exhaustiveness, NO-basket only
   mutual exclusivity) and **S5b threshold/date-ladder arb** (A⇒B from
   ticker/strike structure, no LLM; buy YES(B)+NO(A) at ask, payout
   ≥$1). Both true riskless arb, both Kalshi-only/Phase-1-compatible.
   Build detect-and-log first. Market-making is the best statistical
   candidate (maker fee = 0 on most markets) but needs a live order
   layer. S2 cross-platform stays deferred (real unhedged-leg risk +
   two missing prerequisites + Polymarket US-access/fee verification).
6. Smaller: `karbot_runner.py --mode` flag is parsed but never applied
   (config.yaml alone decides paper/live); `from_yaml()` also never
   parses `capital:`/`risk:`/`strategies:` sections (generalizes the
   Session 24 `data_feeds:` finding — capital is ALWAYS the $10k paper
   default on the VPS; strategy thresholds are not YAML-tunable);
   Telegram `_et_timestamp` hardcodes UTC-4 (wrong half the year);
   PaperExecutor resolves every trade at `expected_pnl` by construction
   (paper P&L is tautological for directional strategies).

### S1 is real and working — first live trades observed and hand-verified, Session 27 (2026-07-16)
### → SUPERSEDED, Session 29 (2026-07-16): CONFIRMED WRONG. Session 28's challenge was independently live-verified (0/778 real markets crossed; all 5 trades below correlate 100% with a sequence gap on their exact market at their exact second). These were not real trades — see KNOWN DEBT item 1 above and DECISIONS.md. Keeping this entry for the historical record only; do not use it as evidence of anything.
5 real trades fired over the first ~63 hours after Session 26's fixes
went live: $10.79 total realized paper profit, roughly 2 trades/day,
sizes ranging $0.05-$81.36. Two were hand-verified dollar-exact
(including by the operator independently). Every trade so far has been
liquidity-capped, not capital-capped, confirming real order-book depth
— not account size — is the actual bottleneck. See SESSIONS.md Session
27 for the full trade table and math. Still a small sample; 5 trades
isn't a verdict, just real, positive, consistent evidence the corrected
formula and depth cap are working as designed.

### Telegram display rounding hid a real trade as a fake "zero-size bug" — FIXED, Session 27 (2026-07-16)
A genuine, liquidity-capped `size_usd=0.05` trade displayed in Telegram
as "x0" / "$0.00" (`:.0f` / `:.2f` formatting), looking exactly like the
`ZERO_APPROVED_SIZE` bug from Session 26 had regressed. Investigated via
VPS logs before assuming either way — confirmed 0 `ZERO_APPROVED_SIZE`
rejections in the window and the real `size_usd=0.05` in the log line;
the fix from Session 26 was working correctly, this was purely a display
issue. Fixed: `TelegramNotificationAgent._fmt_qty()`/`_fmt_usd()` show
enough precision to keep real small values visibly nonzero (`.4g` for
quantities, extra decimals under 1 cent for dollars). 8 new tests,
120/120 total passing.

### Scheduled tasks in this environment are not guaranteed to complete — found Session 27 (2026-07-16)
A one-time scheduled task fired on time and auto-disabled correctly, but
its actual execution stopped after 2 tool calls with no final report
ever written (confirmed by tracing the task's transcript file directly).
Not a Karbot Rage bug — a harness/environment limitation. Don't rely on
a scheduled task's completion notification alone; if a report doesn't
show up, check directly (SSH/logs) rather than assume the task didn't
run.

### S1 P&L inflation — THREE COMPOUNDING BUGS FOUND AND FIXED, ROOT CAUSE CONFIRMED LIVE, Session 26 (2026-07-13)
Three independent, compounding bugs were live simultaneously, found in
order across one session:

1. **Stale price publish on sequence gap** (fixed first): `price_watcher.py`'s
   `_handle_kalshi_delta` discarded `OrderBook.apply_delta`'s return value
   and published a `PriceUpdateEvent` from stale pre-gap prices on the
   delta that first detected a gap. Fixed by checking the return value.
   Added `s1_max_net_profit_pct` (default 15%) to `ArbScanner` as
   defense-in-depth.
2. **No order-book depth anywhere in the pipeline** (fixed second, in
   response to the operator asking why sanity-ceiling rejections were
   *still* occurring after fix #1 — investigating that question is what
   found #3 below): `RiskGate._calculate_position_size` sized positions
   purely from Kelly criterion and capital, and `PaperExecutor` filled
   the full size at the top-of-book quote regardless of real available
   liquidity. A live-pulled Kalshi order book showed a "47% edge" backed
   by exactly 1 contract. Fixed: `OrderBook.depth()` +
   `PriceUpdateEvent.yes_ask_depth`/`no_ask_depth` expose real depth;
   `OpportunityEvent.max_fillable_qty` caps S1 size to what's actually
   resting at the quoted price (top-of-book only, not a multi-level walk
   — deliberately conservative scope, see SESSIONS.md for why a full walk
   was considered and deferred); `RiskGate` clips Kelly size to that cap.
3. **THE ROOT CAUSE — S1 priced the wrong side of the book entirely**:
   investigating the depth question required knowing which side of the
   book a BUY order executes against, which surfaced that
   `_check_s1_rebalancing` computed profitability from
   `yes_bid + no_bid` — bid prices, what *other participants* will pay,
   not prices this system can buy at. A real buy executes against the
   **ask**. Verified against a live Kalshi market pulled directly from
   the REST API: `yes_bid=0.23`/`no_bid=0.30` was reported as **+47%
   profit** by the old formula; the real executable cost via asks
   (`yes_ask=1-no_bid`, `no_ask=1-yes_bid`) is $1.47 for a guaranteed $1
   payout — a **47% loss**. Cross-checked against this project's own
   history: `SESSIONS.md` Session 2's original spec prices (0.47/0.51),
   rejected as unprofitable by whatever formula existed then, come out to
   a small ~2% loss under the corrected formula — exactly what a healthy,
   efficient market should look like, and strong evidence the sign has
   been backwards since the very first working version of this strategy
   (2026-05-25). **This means every S1 "opportunity" this system has ever
   flagged as profitable was very likely a computed loss with the sign
   flipped, not a data-quality issue on top of a sound strategy.** Fixed:
   `_check_s1_rebalancing` now reads `event.yes_ask`/`event.no_ask`
   (already correctly computed by `price_watcher.py`, just previously
   unused by this function). Full math and historical cross-check:
   **DECISIONS.md, "S1 arb formula uses BID prices for both legs of a BUY
   trade."**

17 new/updated tests across all three fixes, 99/99 total passing.
**CONFIRMED LIVE**: after deploying and restarting, zero
`opportunity_approved` or ceiling-rejected events fired over ~4 minutes
and 1,331 lines of book activity — a dramatic contrast with pre-fix
behavior where nearly every price tick produced a "profitable" signal.
Expected and correct: real markets rarely offer a genuine executable
edge after fees. **Revert point if any of this needs to be backed out**:
commit `5348533` (depth plumbing only, predates bugs #2's cap wiring and
#3's formula fix).

- **New minor bug noticed while confirming fix #1, not yet fixed**: some
  `opportunity_approved` events show `size_usd=0.0` — zero-size trades
  being approved and executed pointlessly. Separate issue, flagged for a
  future session.
- **Not audited**: S2/S3/S4 strategies were not reviewed for similar
  bid/ask or depth-blindness issues this session — only S1 was in scope.

### FOURTH bug, same investigation: KalshiFeeModel used a flat 14% approximation instead of Kalshi's real per-price fee — FIXED, Session 26 (2026-07-13)
- Operator asked, reasonably, whether S1 is even a viable strategy
  regardless of whether tonight's fixes were correct. Checking that
  required auditing one more input: `KalshiFeeModel`, self-documented as
  "approximate." Kalshi's real, published taker fee (confirmed via
  Kalshi's official fee schedule) is `0.07 * price * (1-price)` per
  contract — peaks at 1.75% on a 50c contract, near zero at the extremes.
  The old model used a flat 14% of trade value regardless of price,
  roughly 4-8x too high for a typical near-the-money contract — directly
  gating `s1_min_net_profit_pct` and very likely rejecting real, small,
  profitable edges as "not enough to cover fees."
- Fixed: `KalshiFeeModel.taker_fee_fraction(price)` implements the real
  formula; each `OpportunityEvent` leg carries its own real per-leg fee
  instead of an even split of a flat total. Test fixtures retuned (old
  0.40/0.40 fixture now scores above the sanity ceiling under the
  corrected, lower fee; retuned to 0.45/0.45). 8 new tests, 107/107 total
  passing. **CONFIRMED LIVE**: deployed and restarted; even with the much
  lower, more accurate fee estimate, zero opportunities fired over the
  observation window — a meaningful data point that the earlier
  zero-opportunity result wasn't an artifact of an overly strict fee
  assumption.
- **Honest viability read, not a verdict**: pure single-market S1 arb on
  an actively market-made exchange is a well-known, thin-margin, heavily
  competed strategy. The live order books checked tonight both sat just
  slightly on the unprofitable side of break-even — the normal signature
  of a functioning market, not a broken one. Expect S1 alone to fire
  rarely; whether that's "worth it" depends on real observed frequency
  and average edge size over time, which needs the corrected code to run
  for real, not further code review. This project's own roadmap already
  treats S1 as Phase 1's "safest starter" strategy, with S3/S4 expected
  to carry more real edge — tonight's findings are consistent with that
  framing, not a contradiction of it.

### Order-book reset loop never resolves for some markets — found Session 26, log-volume symptom fixed, root cause not fixed
- Specific markets (e.g. `KXWORLDNEWSMENTION-26JUL10-WILD`) get stuck
  logging `book_needs_reset` → `book_reset_throttled` on every WS delta
  received, indefinitely — the Session 22/23 10s-throttle only blocks the
  actual REST re-fetch, not this per-delta debug logging. 169 million such
  lines accumulated over ~9 days and filled the VPS disk to 100%
  (2026-07-04 to 2026-07-13), silently breaking `compliance.db` and
  `audit_trail.jsonl` writes the entire time with zero alerting (see
  SESSIONS.md Session 26 for the full outage writeup). Fixed this session:
  `structlog.configure()` was never called anywhere in the codebase, so
  every `log.debug()` rendered unconditionally regardless of
  `logging.basicConfig(level=logging.INFO)` — added the missing
  `structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))`
  to `karbot_runner.py::setup_logging()`, confirmed live (no more DEBUG
  output, disk growth back to normal). This stops the *symptom* (disk
  fill); it does not explain *why* these specific books never complete
  recovery. Needs its own investigation.

### VPS deployment gap — "CONFIRMED LIVE" claims were not verified against actual VPS state
- Found Session 26: the VPS was 4 git commits behind `main`
  (`origin/main` was at `7057d8d`; missing `8a7e6ce`, `185dc6c`, `7d022b9`
  — the Session 23 docs finalize, Session 24 `config_resolved` fix, and
  Session 25 duplicate-Telegram-alert removal). This file and README.md
  documented all three as "CONFIRMED LIVE." No prior session had actually
  checked `git log` on the VPS before making that claim — it was inferred
  from a local commit plus a plausible-looking log line seen once. Fixed
  Session 26 (`git pull` on VPS, now at `9b210fe`) — but every other
  "CONFIRMED LIVE" claim elsewhere in this file should be treated as
  unverified until independently re-checked against the VPS directly.

### Secrets policy violation on live VPS — FIXED, Session 26 (2026-07-13)
- `karbot.service` had `EnvironmentFile=/home/ubuntu/karbotrage_v1/.env` —
  violated this file's own VPS security rule that secrets must come from
  a systemd EnvironmentFile *outside* the repo directory. The private key
  (`KALSHI_PRIVATE_KEY_PATH`) was already correctly stored at
  `/etc/karbot/secrets/kalshi_private_key.pem` (`chmod 600`, owned by the
  `ubuntu` service user) — only the `.env` holding
  `KALSHI_API_KEY_ID`/`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`/
  `ANTHROPIC_API_KEY` was misplaced, and was world-readable
  (`rw-rw-r--`) inside the repo directory.
- Fixed: copied to `/etc/karbot/secrets/karbot.env` (matching the
  existing private-key file's ownership/permission convention:
  `ubuntu:ubuntu`, `chmod 600`), updated `karbot.service`'s
  `EnvironmentFile=` to the new path, `daemon-reload` + restart. Verified
  live: `config_resolved` log shows `telegram_enabled=True` and all
  subsystems correctly enabled from the new path, no
  `secrets_missing_at_startup` warning. Old repo-directory `.env` deleted
  after confirming the service ran cleanly without it (was gitignored,
  never committed — confirmed via `git check-ignore` before deletion).

- correlation_score in PositionSnapshot is permanently 0.0 — Phase 3 item
- execution/engine.py — legacy monolithic path, intentionally deferred,
  must be removed or replaced before live trading; do not extend
- `AgentHeartbeat` events are being dead-lettered every ~30s in VPS logs
  (noticed incidentally during Session 15 investigation) — no agent
  currently subscribes to handle them; CLAUDE.md references a "Health
  Monitor Agent" conceptually but it isn't implemented. Likely
  pre-existing, not a regression, but unconfirmed.

### PriceWatcher died permanently on WS disconnect for ~6 hours — fix applied, DEPLOYED BUT NOT YET CONFIRMED LIVE
- `_kalshi_connection_loop`'s `@retry` decorator used tenacity's
  `before_sleep_log(log, "WARNING")`, written for stdlib `logging.Logger`. It
  calls `logger.log("WARNING", ...)` (a string level); structlog's
  `BoundLogger.log()` expects an int and raises
  `TypeError: '<' not supported between instances of 'str' and 'int'` on the
  very first retry attempt — meaning `@retry` had never actually retried
  successfully since this decorator was written. The TypeError propagated
  out of tenacity's own machinery, crashing through to `_run_supervised` in
  `karbot_runner.py`, which killed the agent permanently.
  **Confirmed live**: a Kalshi WS disconnect at 07:42:02 UTC on 2026-06-30
  killed the price feed for ~6 hours (zero retry attempts logged) until a
  manual `systemctl restart karbot`.
  Fix (Session 19): replaced `before_sleep_log(log, "WARNING")` with a custom
  `_log_before_sleep(retry_state)` function using structlog's own API.
  `stop_after_attempt(10)`, `wait_exponential(...)`,
  `retry_if_exception_type(...)` unchanged. Unit-tested (2 new tests, 65
  total) — one test reproduces the original bug directly (mocked
  `ConnectionClosedError` on first `connect()`, success on second; confirms
  retry now actually proceeds instead of crashing on attempt 1).
  **NOT yet deployed to VPS or verified against a real Kalshi disconnect** —
  next session must deploy and confirm `kalshi_reconnect_retry` logs appear
  (with no `TypeError`) on any real disconnect, and that the feed actually
  recovers.
- **Precondition-breaking for the Session 18 book-reset investigation**: if
  `PriceWatcher` was dying permanently on WS disconnects throughout the
  2026-06-30 observation window, the `book_snapshot_requested`/
  `book_snapshot_applied` 10.2% completion-rate data may be confounded by an
  agent that was dead for stretches of that window, not actively processing
  gap events. Re-verify the Session 18 completion-rate comparison only after
  this fix is confirmed live and the feed is confirmed to survive disconnects.
- **RESOLVED Session 20**: once `stop_after_attempt(10)` is genuinely
  exhausted (10 real failed reconnects), `karbot_runner.py` now restarts
  `PriceWatcher` automatically after a 30s delay, capped at 3 restarts per
  rolling 60-minute window (all configurable via
  `KarbotConfig.system.agent_restart_*`); exceeding the cap stops
  auto-restart permanently and fires a CRITICAL Telegram alert
  ("AUTO-RECOVERY EXHAUSTED"). Operator decided on this capped-auto-restart
  approach over "accept permanent death, manual restart only." See the
  "Telegram feed-down alert + capped runner-level auto-restart" entry below
  and SESSIONS.md Session 20 / DECISIONS.md for full framing.

### Telegram feed-down alert + capped runner-level auto-restart — feed-down/recovered CONFIRMED LIVE Session 27 (2026-07-16), restart-budget-exhaustion still unconfirmed
- **Feed-down/recovery Telegram alert (Session 20)**: `FeedHealthEvent`
  gained an additive `error: str = ""` field; `TelegramNotificationAgent`
  subscribes to `FeedHealthEvent` and alerts (Tier 1, bypasses
  `telegram.enabled` gating the same way other Tier 1 handlers do) only on
  a connected→disconnected or disconnected→connected transition for
  `platform="kalshi"` — not on every repeated `connected=False` event during
  one continuous outage. Down alert includes the error message when present;
  recovery alert is textually distinct ("FEED RECOVERED").
- **Capped runner-level auto-restart (Session 20)**: resolves the Session 19
  open question — `karbot_runner.py._run_supervised_with_restart()` restarts
  a crashed `PriceWatcher` task after `agent_restart_delay_seconds` (default
  30s), capped at `agent_restart_max_count` (default 3) restarts within any
  rolling `agent_restart_window_minutes` (default 60) window. Exceeding the
  budget stops auto-restart permanently for that agent and publishes a
  CRITICAL Telegram alert ("AUTO-RECOVERY EXHAUSTED for {agent_name}") via
  `TelegramNotificationEvent` — a bus-published event, not a direct call.
  General-purpose function, reusable for other agents, but wired only to
  `PriceWatcher` this session (`isinstance(agent, PriceWatcher)` in the task
  loop); every other agent still uses the original, unmodified
  `_run_supervised()`.
  Unit-tested (7 new tests total: 4 Telegram feed-health, 3 runner-restart,
  72 total).
  **Session 24 root cause: this has NEVER actually fired live, not "pending
  verification."** `telegram.enabled` defaults to `False`, and no
  `config.yaml` existed on the VPS (only the committed `.example` template)
  — so `TelegramNotificationAgent` has been running fully disabled (no HTTP
  calls, no polling, no error) through all three live deploys since this
  was built, including today's real crash/restart/restart-budget-exhaustion
  cycle from Session 23. The code path itself has not been proven wrong —
  it simply never ran. Fixed (Session 24): a `config_resolved` startup log
  now surfaces the actual resolved value of `telegram.enabled` (and every
  other subsystem flag) so this class of gap can't go undetected again;
  the operator is creating a real `config.yaml` with `telegram.enabled: true`
  on the VPS (never committed) as the next deploy step. Next session must
  confirm, for the first time ever: (1) a real disconnect produces a "FEED
  DOWN" Telegram message and a "FEED RECOVERED" message on reconnect with
  no duplicate alerts mid-outage; (2) if `PriceWatcher`'s internal retry is
  ever exhausted, both the runner-restart behavior AND the CRITICAL
  "AUTO-RECOVERY EXHAUSTED" Telegram alert actually fire.
  **(1) CONFIRMED LIVE Session 27 (2026-07-16)**: operator received a real
  `FEED DOWN` → `FEED RECOVERED` → `FEED DOWN` sequence in Telegram with no
  duplicate alerts mid-outage — the first real confirmation since this was
  built. **(2) still unconfirmed** — no restart-budget exhaustion has been
  observed live yet.

### Duplicate/broken regulatory Telegram alert — REMOVED (Session 25)
- `TelegramNotificationAgent` had its own subscription to
  `RegulatoryAlertEvent` (`_handle_regulatory_alert`), separate from
  `RegulatoryIntelligenceAgent._route_by_urgency`'s already-correct
  urgency-branched `TelegramNotificationEvent` messages. Since
  `RegulatoryAlertEvent` publishes unconditionally for every item (by
  design, for `ComplianceOfficer`'s logging), every regulatory item
  produced two Telegram messages — found live tonight (2026-07-01), the
  first time Telegram alerting has actually been enabled/exercised (see
  Session 24 above). The second message was broken:
  `event.source_name`/`event.matched_keywords` are never populated by the
  publisher (always empty/blank), and it told the operator to check
  `logs/regulatory_alerts.txt`, a file deleted in an earlier session. Worse
  than just noise: it was hardcoded `"🚨 KARBOT RAGE! CRITICAL"` regardless
  of actual urgency, so a routine urgency-3 FYI showed up labeled CRITICAL
  — degrading trust in the one alert that matters most (urgency 5,
  trading-halt).
  Fixed: removed the subscription and handler entirely.
  `RegulatoryAlertEvent` still publishes unconditionally for
  `ComplianceOfficer`'s audit logging (untouched); `_route_by_urgency`'s
  urgency-branched Telegram path (untouched, already correct) is now the
  sole source of regulatory Telegram messages. Unit-tested (3 new tests,
  83 total).

### KarbotConfig.from_yaml() does not parse a `data_feeds:` YAML section — discovered Session 24
- `kalshi_ws_enabled`/`polymarket_ws_enabled` always come from
  `DataFeedsConfig()` dataclass defaults; `from_yaml()` never calls
  `raw.get("data_feeds")` or otherwise reads such a section. Consequently
  `config.yaml.example`'s `api.kalshi.enabled`/`api.polymarket.enabled` keys
  are dead — editing them has zero runtime effect. Discovered while tracing
  exactly which fields the new `config_resolved` log line should report;
  not fixed (out of scope for that task — config + one log line only).
  Flagged with a comment in `config.yaml.example`. A future session should
  either wire `data_feeds:` parsing into `from_yaml()` or remove the
  misleading `api:` section if Phase 1 never needs it YAML-configurable.

### book_needs_reset recovery — WS re-subscribe replaced with REST fetch, no-auth fix — CONFIRMED LIVE (Session 23)
- **Root cause found (Session 21 live wire capture + Kalshi docs)**: the
  original Session 17/18 WS re-subscribe recovery mechanism assumed Kalshi
  would respond to a duplicate `subscribe` message with a fresh
  `orderbook_snapshot`. Live traffic capture confirmed Kalshi actually
  responds with `{"type": "ok", "id": N}` — a plain ack, never a snapshot —
  and Kalshi's own WS docs confirm snapshot delivery is initial-subscribe-only.
  The Session 18 id-collision fix (unique per-call `id`) improved
  request/response correlation but could never have recovered a book, since
  the correlated response never carried book data. This explains both the
  original 10.2% completion rate (Session 18) and the later regression to
  0% (`book_snapshot_requested` climbing to 3,365 in an 18-minute window
  while `book_snapshot_applied` fell to zero, down from 37%) observed going
  into Session 22.
- **Fix (Session 22)**: `_request_snapshot(market_id)` makes a direct
  `aiohttp` GET to `https://api.elections.kalshi.com/trade-api/v2/markets/
  {ticker}/orderbook`, parses `orderbook_fp.yes_dollars`/`no_dollars`
  (string values, cast to float; NO bids still derive YES asks at `1-p`),
  and calls `book.apply_snapshot(bids, asks, seq=0)` directly. The REST
  response carries no sequence number — `seq=0` is a sentinel that
  short-circuits `OrderBook.apply_delta`'s gap check (`if seq !=
  self.sequence + 1 and self.sequence != 0`), so the next delta is accepted
  regardless of its own seq value and `self.sequence` naturally realigns;
  verified against the actual gap-check code, not assumed. The 10s
  per-market throttle and "client connected" guard are unchanged.
- **Live outage + fix (Session 23)**: Session 22 defensively added
  `_build_kalshi_auth_headers`/`_load_kalshi_private_key` calls to this
  REST fetch, without empirical verification that Kalshi's endpoint
  (documented as requiring no auth) needed them. Deploying it crashed
  `PriceWatcher` 3 times in ~8 minutes — the per-call blocking RSA-PSS
  signing + private-key file read stalled the event loop long enough under
  real gap-event load (~13,761 `book_needs_reset`/15min) that the WS listen
  loop missed Kalshi's ping frames within `ping_timeout=10s`; Kalshi tore
  down the transport, and the next `recv()` crashed with `AttributeError:
  'NoneType' object has no attribute 'resume_reading'` — exhausting the
  Session 20 restart budget and leaving the agent permanently stopped. Auth
  removed entirely; also added a shared `aiohttp.ClientSession`
  (`_get_rest_session()`, closed in `stop()`) instead of one per call.
- **CONFIRMED LIVE (Session 23)**: unauthenticated `GET
  /trade-api/v2/markets/{ticker}/orderbook` returns HTTP 200; 1,764
  `book_snapshot_applied` events fired correctly in a ~2.5 minute window;
  zero crashes over sustained load. The book-reset recovery mechanism now
  works end-to-end for the first time since it was originally designed in
  Session 17.
- Unit-tested (79 total, 4 new this session: no-auth-helpers-called,
  shared-session-reuse, `_get_rest_session` same-instance, `stop()` closes
  session).
- Session 21's temporary diagnostic instrumentation (unconditional
  per-message WS logging, added solely to capture the traffic that led to
  this fix) was fully reverted in Session 22 — confirmed via `grep -in
  "diagnostic\|diag" agents/floor/price_watcher.py` returning zero matches.

### REST snapshot fetch has no concurrency limit — follow-up, not urgent
- Live verification (Session 23) surfaced 56/1,016 (~5.5%) REST snapshot
  requests hitting HTTP 429 (`too_many_requests`) during the initial
  post-restart surge, when many markets simultaneously needed recovery at
  once. Already handled safely by the existing failure path — the 429 logs
  as `book_reset_rest_failed`, `_gap_detected` stays `True`, and the next
  throttled window (10s later) retries — not a crash risk, just an
  efficiency gap under restart-time bursts.
- A future session should add an `asyncio.Semaphore` (or similar) bounding
  in-flight `_request_snapshot` REST calls to smooth bursts and avoid
  hitting Kalshi's rate limit, especially right after a restart when many
  books are simultaneously stale. Not implemented — explicitly deferred,
  not urgent.

### P&L figures likely inflated during paper trading — HIGH PRIORITY, NOT YET RE-VERIFIED (Session 25)
- VPS paper trades show $58–$288 realized P&L per trade at ~$500 position
  size, implying 11–57% net margins. S1 arb on liquid Kalshi binary markets
  should realistically yield 1–5% net after fees. The most probable cause is
  corrupt order books (from unrecovered sequence gaps) feeding stale/wrong
  bid-ask spreads to ArbScanner, which then detects spuriously large spreads
  as arb opportunities. The book-reset recovery mechanism is confirmed
  working live (Session 23) — but the resulting P&L distribution has NOT
  been checked against the 1–5% benchmark since. **Live Telegram PnL
  figures observed by the operator on 2026-07-01 evening ($338.50, $343.50,
  $383.50, $323.50, etc.) appear comparable to or larger than the
  originally-flagged inflated range — NOT confirmed improved.**
  **First priority next session**: pull RESOLVED trades from
  `compliance.db` timestamped after 2026-07-01 16:31 UTC (when the Session
  23 fix went live), compute PnL as a percentage of position size, and
  determine whether the distribution is now realistic or still inflated.
  Do not treat paper trading data as validated until this is checked — if
  still inflated, the original hypothesis (corrupt books → bad spreads →
  spurious S1 opportunities) was incomplete or wrong and needs a fresh
  investigation, not an assumption that the book-reset fix also fixed this.

### Paper trade fee variance — flagged, NOT investigated (Session 25)
- Operator observed live via Telegram trade-executed messages on
  2026-07-01 evening that fee amounts vary in an unexplained way across
  trades: some show a flat $70.00 fee regardless of PnL size, others show
  $0.00, $42.78, $113.27, $56.64. Not investigated this session. Next
  session should pull the fee calculation logic (`PaperExecutor` or
  wherever fees are computed) and cross-reference against `compliance.db`
  to determine whether this is expected (e.g. fee scales with position
  size or trade type in a way not obvious from the Telegram summary) or a
  real bug. Do not assume either way without checking the actual numbers.

### Reconciliation (NOT built — future session)
- No periodic reconciliation job exists to cross-check resolved S1 trades
  against Kalshi's actual market resolution data. This is intentionally
  decoupled from the live trading path: S1 P&L is deterministic at fill
  time (guaranteed $1 payout on $1 binary contracts), so polling Kalshi's
  resolution API is not needed for correctness. However, edge cases exist
  where Kalshi could void, dispute, or delay a market in a way that breaks
  the S1 "guaranteed $1 payout" assumption. A future audit job should
  periodically sample resolved S1 trades and verify against
  `/markets/{ticker}` resolution status to catch such anomalies. NOT built
  in Session 17. Design this as a standalone offline job, not in the live
  trading path.

## REGULATORY CONTEXT (May 2026 — current)
- CFTC Letter 26-15 (May 19 2026, EFFECTIVE NOW): New cooperation
  policy — voluntary self-reporting + full cooperation + remediation
  = path to declination. compliance_actions.jsonl IS this evidence.
- CFTC enforcement priorities: insider trading (#1), manipulation,
  wash trading. CFTC using AI surveillance on prediction markets.
- CFTC v. Van Dyke (Apr 23 2026): First insider trading prosecution
  involving event contracts. DOJ also filed charges.
- DEATH BETS Act (introduced Mar 2026): would prohibit contracts
  on terrorism/assassination/war/death. Monitor for passage.
- Karbot Rage! is clean: public data only, arbitrage only, no MNPI,
  Kalshi only Phase 1, full audit trail from day one.
- regulatory_halt flag in config.yaml: operator sets after reading
  guidance, bot refuses to start until cleared and documented.

## Next session priorities (in order)
1. ~~Fix the Telegram sender-authentication hole~~ — **DONE, Session 29
   (2026-07-16)**: `_is_authorized_sender()` checks `message.chat.id`
   against `TELEGRAM_CHAT_ID`. ~~Set a non-default `regulatory_clear_phrase`
   on the VPS~~ — **still open**, confirmed the VPS still uses the
   default value, needs rotating. ~~Verify `karbot-disk-alert.sh`
   reads the right secrets path~~ — **DONE**: it didn't, fixed and
   verified live with a real test send.
2. ~~Run the S1 structural-impossibility verification plan~~ — **DONE,
   Session 29**: confirmed (0/778 real markets crossed; 5/5 trades
   correlate 100% with a gap event). S1 demoted to canary mode
   (`s1_canary_mode=True`, default). Session 27's claims marked
   superseded.
3. **S5a/S5b — decision point, not a straightforward build** (Session 28
   spec in DECISIONS.md entry 5; Session 29 empirical check in KNOWN
   DEBT above and SESSIONS.md). Neither showed a currently-exploitable
   edge against a real 1,600-market live sample — 0/78 genuine
   mutually-exclusive S5a candidates, closest S5b ladder crossing was
   1.01 (not <1.00). Before building the full scanners, operator should
   decide: (a) invest in detect-and-log mode over 1-2 weeks anyway to
   catch rare/time-varying windows a snapshot can't see, (b) search more
   specifically for genuine winner-take-all events (elections, award
   winners) not well-represented in the sample checked, or (c)
   reconsider direction (market-making, Session 28's S8 note). If (a) is
   chosen: group by `event_ticker`, honor `mutually_exclusive` +
   exhaustiveness rules (YES-basket needs exhaustive; NO-basket only
   needs mutual exclusivity), price at ask via existing depth fields,
   apply ceil'd per-order fees × N legs, no RiskGate wiring yet.
4. **Fix the RiskGate/PaperExecutor unit system** (Session 28,
   DECISIONS.md entry 3): standardize on integer contract count
   end-to-end; set `capital_required_usd = qty × basket_cost` so check
   2 finally binds; floor quantities to int, reject < 1; replace Kelly
   with cap/depth-based sizing for riskless strategies (keep fractional
   Kelly only for statistical ones); make `KalshiFeeModel` ceil to the
   next cent. Prerequisite for trading S5a/S5b.
5. **Fix or explicitly disable the S3 pipeline** (Session 28, DECISIONS.md
   entry 2): wire `update_markets()` from PriceWatcher's market fetch (or
   delete the loop), switch pricing to asks, guard zero/empty-book
   prices, and decide single-leg (statistical) vs paired-leg (riskless-
   if-relation-holds) semantics. Flip `s4_settlement_arb_enabled`
   default to False until a News Analyst exists.
6. **Investigate the stuck order-book reset loop** (Session 26) — specific
   markets (e.g. `KXWORLDNEWSMENTION-26JUL10-WILD`) get stuck logging
   `book_needs_reset`/`book_reset_throttled` on every delta indefinitely,
   never actually completing recovery via the Session 22/23 REST mechanism.
   169 million such log lines accumulated over ~9 days and were the proximate
   cause of the Session 26 disk-full outage. The Session 26 fix
   (`structlog.configure` filtering) stops this from filling the disk again,
   but does not fix why the loop happens.
7. **Re-audit every "CONFIRMED LIVE" claim in this file against actual VPS
   state** (Session 26) — the VPS was found 4 commits behind `main`,
   silently missing the Session 23/24/25 fixes despite this file marking
   them "CONFIRMED LIVE." Going forward, "confirmed live" must mean checked
   against `git log -1` on the VPS itself and fresh log output, not just a
   local commit plus a plausible-sounding log line from one prior check.
8. **Investigate paper-trade fee variance** (KNOWN DEBT, Session 25) — fee
   amounts observed live via Telegram vary unexplainably ($70.00 flat,
   $0.00, $42.78, $113.27, $56.64). Session 28 note: the flat $70.00
   entries are almost certainly the pre-Session-26 flat-14% fee model (7%
   per leg × ~$500 size × 2 legs = $70) and the variance since is the
   price-dependent real formula — cross-reference against `compliance.db`
   to confirm, then close this item.
9. **Continue live-verifying Telegram alerting** (Session 19/20/24/25) —
   confirm: no `TypeError` on any real Kalshi WS disconnect with
   `kalshi_reconnect_retry` logs increasing (Session 19); a real "FEED
   DOWN"/"FEED RECOVERED" Telegram pair on any real disconnect/reconnect
   with no duplicates mid-outage (Session 20); the runner-restart AND
   CRITICAL "AUTO-RECOVERY EXHAUSTED" Telegram alert both fire if the
   restart budget is ever exceeded (Session 20). Note: Session 26 added a
   new, independent disk-space Telegram watchdog
   (`/usr/local/bin/karbot-disk-alert.sh`, hourly-cron-adjacent via
   `/etc/cron.d/karbot-disk-alert` every 15 min) — confirmed working live.
10. **Monitor the book-reset recovery (Session 22/23)** — watch that
   `book_snapshot_applied` keeps firing at a healthy rate and the 429 rate
   (currently ~5.5% right after restart, KNOWN DEBT) stays a one-time
   post-restart surge rather than a sustained pattern.
11. **Add a concurrency limiter on `_request_snapshot` REST calls** (KNOWN
   DEBT from Session 23, not urgent) — an `asyncio.Semaphore` or similar
   bounding in-flight REST snapshot fetches, to smooth the post-restart
   burst that produced the 429s. Only worth prioritizing if 429s become a
   recurring pattern rather than a one-time restart surge.
12. **Telegram mute/unmute** — add operator commands (`/mute`, `/unmute`)
   so the bot can be silenced during high-volume paper trading without
   disabling the agent entirely. Scope: `TelegramNotificationAgent`
   command handler only; no changes to event bus or other agents. Note: the
   Session 20 feed-down alert is explicitly designed to keep bypassing mute
   once this is built — do not let it get silenced. Prerequisite: the
   sender-authentication fix (priority 1) — operator commands must not be
   world-writable.
13. **Monitor paper trading** — clock running since 2026-06-29, target
   live date 2026-07-29 (9 of the 14 elapsed days as of Session 26 had a
   dead persistence layer — see Session 26 in SESSIONS.md, don't count
   that window as clean data; Session 28: S1 trades in this data are
   suspected artifacts pending the verification plan — don't count them as
   edge evidence either). Review `logs/kalshi_trades.csv` and
   `logs/compliance_actions.jsonl` periodically. Confirm resolved rows
   show nonzero `gain_loss` and `status=RESOLVED` after
   `paper_resolution_delay_seconds`.
14. **Begin live executor spec** after 30-day paper run completes
   (2026-07-29). Design `live_executor.py` to replace `paper_executor.py`
   on the real Kalshi trading path. Session 28 gate: do NOT go live on S1
   under any circumstances until the structural-impossibility verification
   is resolved; the first live candidate strategy should be S5a/S5b with
   measured detect-and-log data behind it.
15. **Investigate dead_letter `AgentHeartbeat` events** firing every ~30s
   in VPS logs — no Health Monitor agent subscribed yet; confirm this
   isn't masking a real event-bus wiring issue.
16. **Fix the `from_yaml()` config-parsing gaps** (KNOWN DEBT, Session
   24 + Session 28) — `data_feeds:`, `capital:`, `risk:`, and
   `strategies:` YAML sections are all silently unparsed; capital is
   permanently the $10k paper default and strategy thresholds are not
   operator-tunable without a code change. Also make `--mode` actually
   override, or remove it from the documented commands.

## FUTURE ROADMAP (do not build yet — design required first)

- Phase 2 Polymarket integration (after original principal recovered)
- Real-time market data via Kalshi WebSocket
- Advanced strategy agents (S3 logical arb, S4 settlement arb)
  - Note: S1 is a deterministic-P&L strategy — P&L is locked at fill time,
    no Kalshi resolution polling needed. Any future strategy (e.g. S4
    settlement arb) whose P&L genuinely depends on real Kalshi market
    resolution would need real settlement polling designed specifically for
    that strategy. Do NOT preemptively add resolution polling to the S1
    path — design it only when a strategy that requires it is actually specced.
- Portfolio Manager agent for cross-strategy capital allocation
- **CSV → DB migration (NOT built in Session 17)**: `kalshi_trades.csv` is
  currently the live write target with atomic read-modify-write on resolution.
  This works at current paper trading volume but is not the long-term
  architecture. The correct direction is `compliance.db` as the primary source
  of truth with CSV as a periodic export/snapshot. Migration should happen
  before live trading volume grows. Not built in Session 17 — flagged for a
  future session.

## GitHub
- Repo: https://github.com/WarpedMind/karbotrage
- Branch strategy: main = stable, feature branches for new work

## Rules / Never do
- Never use regex to replace HTML or CSS blocks
- Always read the file before editing it
- Commit before any major refactor
- If the exact string doesn't match during a replacement, read the file first to find the actual content - do not reach for regex as a fallback

## How to run tests
Run: python -m pytest tests/

## Bash commands

### Canonical entry point (use this)
Run with mock prices and auto-exit (test mode):
  karbotrage_env/bin/python karbot_runner.py --mode paper --mock-prices tests/fixtures/paper_test_prices.json --exit-after-test

Run continuously (paper mode):
  karbotrage_env/bin/python karbot_runner.py --mode paper

### Legacy entry point (do not use — left untouched pending removal)
Run legacy: python main.py
Run legacy with debug: python main.py --debug
Run legacy with specific mode: python main.py --mode paper
