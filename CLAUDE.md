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
- agents/notifications/telegram_agent.py: **UPDATED** — TelegramNotificationAgent (full impl) + TelegramAgent (BaseAgent stub); subscribes to TelegramNotificationEvent, TelegramPermissionRequestEvent, LegFailureEvent (Tier 1), TradeExecutedEvent (Tier 2), RejectedOpportunityEvent (Tier 2), FeedHealthEvent (Tier 1, Session 20); getUpdates polling every 3s; 1 msg/sec rate limit; single-operator FIFO permission resolution; always publishes TelegramPermissionResponseEvent with response_text so RegulatoryIntelligenceAgent can check for clear phrase; enabled=False → no-op (no HTTP calls, no polling); `_handle_feed_health` (Session 20) tracks last-known connected state per platform and alerts only on connected→disconnected/disconnected→connected transition for platform="kalshi", ignoring other platforms — **Session 24 root cause: `telegram.enabled` has been `False` in production the entire time (no `config.yaml` existed on the VPS) — every Telegram feature since Session 19 has NEVER ACTUALLY FIRED live, not "pending verification."** **Session 25: RegulatoryAlertEvent subscription + `_handle_regulatory_alert` REMOVED** — was producing a second, broken, duplicate Telegram message for every regulatory item (blank `source_name`/`matched_keywords`, referenced a deleted `logs/regulatory_alerts.txt`, hardcoded "CRITICAL" regardless of actual urgency) alongside `RegulatoryIntelligenceAgent`'s already-correct urgency-branched message; found via tonight's first-ever live Telegram run. `RegulatoryAlertEvent` still publishes for `ComplianceOfficer`'s logging — only the Telegram consumer was removed. **Session 33 (2026-08-29): `/mute`/`/unmute` operator commands added** — in-memory `_muted` flag toggled in `_handle_operator_reply` (short-circuits before the kill-switch/yes-no paths, same pattern the kill switch uses); suppresses Tier 2 handlers only (`_handle_trade_executed`, `_handle_trade_resolved`, `_handle_rejected_opportunity`, and the tier==2 branch of `_handle_notification`); Tier 1 (`_handle_leg_failure`, `_handle_feed_health`) and `_handle_permission_request` are untouched and always send, so the Session 20 feed-down alert keeps bypassing mute as designed; flag resets to unmuted on every restart (no persistence file) so a forgotten mute can't outlive a deploy. Deployed, service confirmed to restart cleanly. **Live operator round-trip CONFIRMED, same session** — operator tested `/mute`/`/unmute` from Telegram directly and reported it working ("telegram appears to work").

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
- **Strategy direction — UPDATED Session 31 (2026-08-02): S6 weather
  divergence is DEAD. It was built, measured, and FAILED gate G2.**
  NOAA/NBM is measurably *worse* calibrated than the Kalshi price at every
  lead NOAA publishes (Brier 0.2013 vs 0.1757 at 12h on contested markets;
  skill −0.146; P(model no better than market) = 1.000 over 36 independent
  dates; 17 of 18 cities lose). Root cause measured, not guessed: **the
  market's implied point forecast is ~20% more accurate than NBM's** (MAE
  1.27 °F vs 1.59 °F at 12h) while NBM's published spread is close to
  correct — so the deficit is the forecast, not the probability conversion,
  and no better error model can recover it. Authoritative record:
  **DECISIONS.md Session 31 entry**; raw output in `backtest/reports/`.
  **No `DivergenceScannerAgent` and no `FairValueEngineAgent` were built**,
  and no live-path code was touched. Market-making (S8) and the S5a/S5b
  canary are untouched by this result — see "Next session priorities".
- **Strategy screen — Session 36 (2026-09-28): 14 new candidates (C1–C14)
  ranked by cost-to-falsify; the cheapest tested to a result the same
  session. No edge claimed, no live-path code.** The favorite–longshot bias is
  real on Kalshi's **trade tape** and replicates out of sample (H4: takers
  buying 70–85¢ in 8 non-sports categories earned +6.83% net, p < 0.0003, 3/3
  periods), but **a rule buying the quoted ask in the same band on the same
  markets LOSES −15.15%** (H5; −26.4% in-sample), and with a 3¢ spread cap
  still loses −4.09% (H6). The tape measures informed takers' timing, not a
  price on offer; on a tight book the ask is fair. Cross-venue (Polymarket) on major sports is dead by live
  snapshot (0.5¢ median gap). One lead points somewhere: **makers who bought
  favourites were paid in both periods** (85–99¢: +1.94% / +2.45%) while makers
  who bought longshots lost — the first offline evidence on S8's economics,
  side-specific, not a claim. Authoritative record: **DECISIONS.md Session 36**.
- `backtest/tape/`: **NEW (Session 36)** — offline trade-tape and quote-snapshot
  return tests (stdlib + `requests`, public REST, data in gitignored
  `backtest/cache/tape/`). Never imported by the live path. See
  `backtest/tape/README.md` for run order, definitions and the traps list
  (tape ≠ opportunity set; historical vs live candle schema; historical tier
  ignores `min_close_ts`).
- `canary/`: **NEW, COMPLETE (Session 32)** — the S5a/S5b passive arbitrage
  canary. A **standalone process**, not an agent: it polls Kalshi's public REST
  API, prices multi-leg positions whose payout is guaranteed regardless of
  outcome, and appends to `logs/basket_candidates.jsonl`. It publishes no
  events, sizes no positions and places no orders. Modules: `kalshi_rest`
  (sweep primitive + authoritative order-book top), `strikes` (Kalshi strike
  conventions as intervals; implication and disjointness), `qualify` (what a
  series' **settled history** proves), `economics` (one basket evaluator — ask
  prices, ceil'd per-order fees × N legs, depth-capped integer size), `scan`
  (two-stage sweep), `run_canary` (loop + JSONL + heartbeat). Run:
  `karbotrage_env/bin/python -m canary.run_canary --once`. See
  `canary/README.md` for the full traps list and **DECISIONS.md Session 32**
  for why it is a separate process and why relations are gated on settled
  history. `scripts/karbot-canary.service` exists but is **NOT deployed**.
- `backtest/`: **NEW, COMPLETE** — offline calibration harness, never
  imported by the live path, zero new dependencies (stdlib + `requests`).
  Modules: `nbm_text` (NOAA NBM station bulletins), `kalshi_history`
  (settled markets + candlesticks), `stations` (empirical Kalshi-series →
  NWS-station resolution), `probability`, `scoring` (Brier + date-blocked
  bootstrap), `costs`, plus three runnable gates —
  `resolve_and_verify`, `verify_alignment`, `run_calibration` — and
  `diagnose_gap`. See `backtest/README.md` for the traps list.
- **30-day paper trading clock: RESET (Session 30).** The old
  2026-06-29 → 2026-07-29 window is not usable evidence (dead persistence
  layer for 9 of its first 14 days; all S1 trades in it are confirmed
  book-reconstruction artifacts). A new clock starts only when a strategy
  that has passed gates G1–G5 begins paper trading. See KNOWN DEBT.
- **Candidate signal sources**: see `SIGNAL_REGISTER.md` (added Session 30)
  — a standing, open-minded register of unconventional data sources to test
  for weather and later strategies, with a hard statistical methodology gate
  (Bonferroni, n≥20, 3-period replication, market-price baseline) that any
  candidate must clear before it can influence a position.
- Full test suite: **342/342 passing as of Session 36** (7 new `backtest/tape` rule tests, `tests/test_backtest_tape_rules.py`) — was **335/335 as of Session 35** (9 new canary leg-set tests; earlier count follows) — was **321/321** (was documented as 301 through Session
  32; re-counted directly Session 33 and actually collected 305 pre-existing —
  a small, unexplained pre-existing discrepancy in this doc, not investigated
  further — plus 16 new Session 33 Telegram `/mute`/`/unmute` tests). Runner
  smoke test (`--mock-prices --exit-after-test`) exits cleanly. Note that in
  both Session 31 and Session 32 the expensive findings came from
  **counting**, not from tests — Session 32's void-settlement gap and its
  failed sweep reconciliation both had a fully green suite next to them.
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

### NEW-STRATEGY SCREEN — Session 36 (2026-09-28): read this before proposing any new strategy.
**Authoritative record: DECISIONS.md Session 36** (candidate table, ranking,
all six pre-registered tests). What a future session most needs to know:
- **The trade tape is not an opportunity set.** `/markets/trades` labels the
  taker side, which makes it tempting to read "what takers earned" as "what a
  rule would earn". It is not: H4 (+6.83% on the tape, out of sample) and H5
  (−15.15% buying the quoted ask in the same band on the same markets) are both
  correct. Re-test any tape-derived edge on quotes (hourly candles carry bid
  and ask) before it consumes more budget.
- **Conditioning on the ask selects wide books**, especially a market's seeded
  opening quotes — the worst-calibrated prices in the data.
- **Dead or parked**: C1 favourite-buying as a taker (H5 −15%/−26%; H6 with
  a 3¢ spread cap −4.1% — on a tight book the ask is a fair price minus
  costs), C4 Polymarket-as-fair-value on major sports, C8 options-implied
  (fails the screening question), C11/C12/C14 speed contests.
- **Cheapest next, in order**: (1) C10 weather post-peak nowcast, one session,
  $0 (candles cached); (2) C9 cross-series consistency, one session, $0;
  (3) the forward re-run of the C2 maker split on markets settled
  2026-09-28 → 2026-10-28, rule frozen in DECISIONS.md Session 36 ($0, no code).
- **Takers as a class lose ~3.2% after fees** on this sample. Anything that
  crosses the spread starts in that hole.
- **Data traps** (all in `backtest/tape/README.md`): the historical tier
  (settled before `GET /historical/cutoff`, 2026-07-30 at writing) ignores
  `min_close_ts`/`max_close_ts`; historical candles use `close`, live candles
  `close_dollars`; the live candle route 429s above ~8 req/s from one IP.

### S5a/S5b CANARY — Session 35 (2026-09-28): leg-set guard shipped; the "493 candidates" framing below is SUPERSEDED on size — read this first.
**Authoritative record: DECISIONS.md Session 35 item 5.** Findings:
- **YES-basket leg-set guard (canary/ only, deployed, commit `afb5a75`).**
  Session 34 and 35 each found YES-baskets whose live event had fewer legs
  than the settled events that qualified the series (six two-leg soccer
  correct-score baskets at 1¢/leg, thousands of contracts, made up ~99% of the
  log's face-value sum). `SeriesProfile` now carries `legs_per_event_dist`,
  `modal_legs`, `schema=2`; `ProfileStore._is_fresh` rejects `schema < 2` so
  profiles rebuild; `evaluate_event` prices a YES-basket only when live active
  legs ≥ `modal_legs`, else counts
  `evaluation_notes["yes_basket_fewer_legs_than_settled_norm"]`. NO-basket and
  S5b unchanged (hidden extra leg can't break them). **Coverage dips for
  several hours after deploy** (rebuild ≤60 profiles/sweep, shared rate limit
  with `karbot.service`). Does not catch a roster that reaches the modal size
  then changes composition.
- **`KXNFLWINS-27DEN` hand-check: real quotes, not a canary bug.** 17-strike
  ladder complete; YES(≥5)@0.59 + NO(≥7)@0.08 was re-confirmed from
  `/orderbook` on three sweeps (00:19–00:30 UTC 2026-09-28) and Kalshi candlesticks
  show 17,188 contracts traded in that hour, ask low 0.59. A seeded ladder on a
  newly listed market, swept by a faster participant within ~11 minutes; gone now.
- **Economics (robust, distinct confirmed baskets, net <30% of cost and <$200):**
  445 baskets, $2,549 net over 56 days, **86% of it that one `KXNFLWINS`
  window**; ≤14-day baskets: 346, $355 net (~$6/day), peak capital ≈ $16.6k;
  hold-everything-to-settlement peak ≈ $209k for ~1% (long-dated NFL). Arbitrage
  ceiling does not justify a live executor. The face-value sum of the log is
  meaningless and must not be quoted.
- **Trap now recorded in `canary/README.md`**: the leg set of a categorical
  event is not stable, and a settled event's leg count is not a fact about the
  live one (this is the Session 34 item below, finally written down).

### S5a/S5b CANARY — Session 34 (2026-09-27) reading, kept for history: "493 real candidates". SUPERSEDED on size by the Session 35 entry above; the mechanism notes still hold.
Nobody had read the canary's own log since Session 32 deployed it. 56 days
later (Session 34), the accumulated `logs/basket_candidates.jsonl` tells a
completely different story than the entry below, which is kept only for its
still-accurate mechanism description:

- **15,167 sweeps since deployment, 1 transient 429, zero reconcile
  failures.** The instrument itself has been rock-solid.
- **493 distinct candidate baskets found, not one** (Session 35: most face value was false positives or one seed-ladder window). On recheck (the
  re-price-from-`/orderbook` step this file's own README calls the real
  measurement): **3,618 confirmed / 182 vanished — 95.2% hold up.** That
  ratio is exactly what Session 32 said would separate "real resting
  arbitrage" from "our view of the book is noisy," and it came back
  strongly in favor of real.
- Most are small (cents to low dollars, capped by thin depth) and short-
  lived (minutes to hours), consistent with a real, thin, quickly-arbed
  edge rather than a structural giveaway.
- **One candidate (`KXNFLMOSTRSHYDS-26SEP27CINPIT`, 2026-09-27) was hand-
  checked against the live Kalshi API and found to be a genuine false
  positive** — not one of this file's previously-documented trap classes.
  The event's true leg count changed *while the basket was resting*: a
  third player's market (`PITJWARREN30`) was added by Kalshi mid-window,
  after the canary had already qualified a 2-leg exhaustive partition
  (`CINCBROWN30` + `CINSPERINE34`) against 31 settled events with zero
  violations. The 2-leg basket was genuinely exhaustive for the ~2.5 hours
  it existed; a real trade during that window would have lost the full
  stake, since the actual winner (Warren) wasn't one of the two legs
  bought. The canary itself stopped flagging it within one sweep of the
  third market appearing — the failure mode is a real-time roster change
  invalidating a previously-true partition, not a logic bug in `qualify.py`.
  **New trap, not previously documented**: `canary/README.md`'s traps list
  should gain this as "the leg set of a categorical event is not guaranteed
  stable — a late-added market can retroactively break an already-qualified
  partition." **Added to that file in Session 35**, together with the
  leg-count guard that addresses it for YES-baskets.
- **Practical read**: the 95.2% hold-up rate is real signal, but the one
  candidate actually checked by hand was the exception, not the rule, and
  it was also the single highest-edge one seen (38%, `KXNFLMOSTRSHYDS`).
  That is not enough data to say high-edge candidates are systematically
  more likely to be false positives than small ones — it is enough to say
  **do not act on any candidate, especially a large one, without checking
  it against a live API pull first**, exactly the discipline this project
  has needed since the S1 sign-flip bug.

Heartbeat command from `canary/README.md` still works and is the fastest way
to check current state: `tail -1 logs/basket_candidates.jsonl | python3 -m
json.tool` on the VPS.

### S5a/S5b CANARY — BUILT AND LIVE-VERIFIED, Session 32 (2026-08-02). Zero candidates so far; the instrument is the deliverable, not a result. SUPERSEDED by the Session 34 entry above — kept for its mechanism description only.
**Read this before the Session 29 "S5a/S5b checked against real live data" entry
below, which it supersedes on method — Session 29 checked one snapshot by hand,
this runs continuously.** Authoritative record: **DECISIONS.md Session 32**.

What is true now:
- The scanner exists (`canary/`), is verified against real books rather than
  fixtures, and refuses the specific false positives that killed S1 and that
  Session 29 caught by hand.
- **Zero candidates** across 12 consecutive sweeps / **13,094 event-evaluations**,
  with zero errors and every sweep reconciling. Near misses are all exactly one
  spread wide: ATP $1.01, CS2 $1.02, MLB $1.07, weather ladder $1.09, each for a
  guaranteed $1.00. ATP is one cent away, but two near-the-money legs pay ~3.5¢
  in taker fees, so it needs **$0.965** to be real. Twenty-five minutes is not
  weeks — this is not a verdict on S5a/S5b.
- **Session 29's coverage gap is closed**: genuine winner-take-all events (MLB,
  ATP/WTA/ITF tennis, CS2, LoL, Dota, soccer) do qualify as
  `exclusive + exhaustive confirmed`. Session 29 correctly noted they were
  absent from its sample; they are now in scope and still show nothing.

**RESOLVED same session, in the guarantee's favour — cancelled events do NOT
break the basket.** Kalshi finalizes a postponed game or unplayed match as
`result: "scalar"`, `status: "finalized"` on every leg — 0.7% of KXMLBGAME and
**4.1% of KXATPMATCH** events. This was flagged as open and decisive, then
answered from the primary source plus a measurement:
- Kalshi's own `rules_secondary` says a cancelled match *"will resolve to a
  **fair price** in accordance with the rules"* — so it is neither a refund at
  cost nor a zero, which were the only two options originally considered. The
  right question was whether those fair prices preserve the sum-to-one
  invariant both baskets rest on.
- They do. Every leg carries `settlement_value_dollars`, and across **243
  scalar-settled events in 8 series (236 two-leg, 7 three-leg), 243 sum to
  exactly $1.00** — zero violations, zero unverifiable, reconciled. So a
  YES-basket still pays `Σ settlement = $1` and a NO-basket still pays
  `Σ(1 − settlement) = $(N−1)`: exactly the binary guarantee.
- Now **checked per series rather than assumed globally**
  (`qualify.scalar_sum_to_one`). A violation, or an unverifiable cancellation,
  disqualifies that series' baskets — both fail together, since both payouts
  are functions of `Σ settlement`.
- **Confirmed on the deployed VPS across all 60 live profiles**, which is
  stronger than the original sample: the **19 partition series** (tennis, MLB
  game/F5, LoL, Valorant, set winners, weather, soccer) show **361
  cancellations, 0 violations**; the **6 non-partition series** (KXMLBHIT,
  KXMLBHR, KXMLBKS, KXMLBSPREAD, KXPGATOP5/10/20) show **96 violations, 0
  clean**. The invariant holds exactly where it structurally must — a partition's
  fair prices have to sum to $1 or Kalshi would mint value — and is correctly
  absent for independent player props, which have no such constraint. The split
  falls on the partition boundary with zero exceptions either way.
- **The gate blocks nothing today**: 0 of the 19 partition series has a
  violation, so it costs no coverage while guarding a real failure mode.

**The processes are isolated but SHARE KALSHI'S RATE LIMIT** — noted Session 32
before deploying, because "separate process" was overstating the independence.
`canary` and `karbot.service` run from the same VPS IP, and
`PriceWatcher._request_snapshot` already measured a **~5.5% 429 rate** during
post-restart bursts (Session 23). Canary load: ~43 requests per sweep at steady
state, but **~160 per sweep during the profile-building phase** (43 event pages
plus up to 60 settled-history fetches), which lasts a few hours until the cache
converges and then recurs weekly as profiles age out. At a 300s interval that is
a low sustained rate, and `kalshi_rest.get()` backs off on 429 rather than
retrying hot — but the coupling is real and was not part of the original
separate-process argument. **If PriceWatcher's 429 rate rises after the canary
is deployed, this is the first place to look**, and the fix is to raise
`--interval-seconds` or lower `max_new_profiles`, not to assume a Kalshi-side
change.

**MEASURED after deploying, Session 32 — the effect is real but small.**
Counting `book_reset_rest_failed` (the actual 429 counter; a naive
`grep 429` is worthless here because it matches sequence numbers containing
those digits, e.g. `expected=27854299` — a false alarm this session raised and
then retracted):

| window | `book_reset_rest_failed` | `book_snapshot_applied_rest` |
|---|---|---|
| 21:00–21:59 (pre-canary) | 0 | 30,354 |
| 22:00–22:23 (pre-canary) | 0 | 8,398 |
| 22:24 → (canary running) | 4 | 2,390 |

Zero failures across 38,752 REST snapshots in the 84 minutes before, four in the
13 minutes after — so the canary does cause some, at **~0.17%**. That is an
order of magnitude below Session 23's confirmed 5.5% and below Session 30's
0.7%, and the existing failure path absorbs it (log, keep `_gap_detected=True`,
retry on the next throttled window). **Worth re-checking over a longer window**;
the pre-canary baseline of literally zero also updates KNOWN DEBT item 15/16
favourably — the REST snapshot path was healthier than previously recorded.

**The "dies quietly" gap is now CLOSED — `karbot-canary-alert.sh`, Session 32.**
A separate systemd unit does not inherit `karbot_runner.py`'s supervision or
Telegram alerting, and `Restart=always` covers a crash but not a hang. So an
external watchdog now runs from cron every 15 minutes
(`/etc/cron.d/karbot-canary-alert`), mirroring `karbot-disk-alert.sh`'s
conventions (secrets from `/etc/karbot/secrets/karbot.env`, edge-triggered state
files in `/var/lib`, nothing echoing the token). Two alerts:
- **CANARY STALLED** if no sweep record has been written for 20 minutes
  (tolerates three missed cycles plus a restart), with a recovery message.
- **CANARY FOUND N CANDIDATE(S)** the moment a real candidate appears, with its
  economics and its `confirmed` / `vanished_on_recheck` status — because a
  candidate sitting unread in a JSONL file is worth nothing.

**Both paths were exercised with real Telegram sends before being trusted**,
plus the silent no-op case, using `CANARY_LOG`/`CANARY_STATE_DIR` overrides
against a scratch file so no real data was touched. That is deliberate: an
untested watchdog is worse than none, and the precedent is
`karbot-disk-alert.sh` being silently non-functional from Session 26 to Session
29 — the watchdog built to prevent a silent outage was itself the silent outage.

**DEPLOYED AND CONFIRMED LIVE, Session 32.** `karbot-canary.service` installed
from `scripts/karbot-canary.service`, enabled at boot, active, `Restart=always`,
`Nice=10`, 300s interval. Confirmed against the box itself: `systemctl is-active`
= active, `is-enabled` = enabled, `karbot` still active alongside it, and real
sweep records landing in `logs/basket_candidates.jsonl` (22:24/22:25/22:30 UTC,
684→765 evaluated, zero errors, all reconciling). The VPS was 10 commits behind
when this started; **none of those commits touched the live path** — verified
via `git diff --name-only` over `agents/ karbot/ core/ execution/ data/
karbot_runner.py`, which returned nothing, so the pull could not change trading
behaviour.

**Two things only deploying could have found**: `requests` was an undeclared
dependency (documented by `backtest/` since Session 31, present locally by
coincidence, `ModuleNotFoundError` on the VPS — now in `requirements.txt`); and
**the dev machine and the VPS disagree on floating-point arithmetic** (local
Python 3.14 vs VPS 3.10; CPython 3.12 gave `sum()` compensated summation, so ten
one-cent fees add to 0.1 on one and 0.09999999999999999 on the other). A test
passed locally and failed there — **"all tests pass locally" is not evidence
about production.** Fixed with `approx` in the test and an `EPSILON` in
`is_candidate` so a break-even basket can't be logged on float dust.

Note: 11 pre-existing test failures on the VPS (10 in
`test_regulatory_intelligence.py`, 1 in `test_config_resolved_log.py`) are
unrelated environment differences and were not investigated. All 76 canary tests
pass there.

### S6 WEATHER DIVERGENCE — TESTED AND DEAD, Session 31 (2026-08-02). Gate G2 FAILED.
**This is the current state of the strategy direction. Read this before the
Session 30 entry below, which it supersedes on the S6 question only.**

Built `backtest/`, ran the calibration report Session 30 specced, and got a
clean negative. NOAA/NBM converted to a probability is **worse calibrated than
the Kalshi price itself** at every lead NOAA publishes:

| contested markets, test split | 12h | 24h | 30h |
|---|---|---|---|
| Brier — NBM model | 0.2013 | 0.1795 | 0.1713 |
| Brier — **market (baseline)** | **0.1757** | **0.1612** | **0.1567** |
| Brier skill vs market | −0.146 | −0.114 | −0.093 |
| P(model no better than market) | 1.000 | 1.000 | 1.000 |

36 independent dates, 95% CIs entirely negative, 17 of 18 cities lose, and the
model barely beats climatology while the market beats it comfortably. Trading
it: claimed +$0.11–0.17 net EV per contract, **realised −$0.01 to −$0.04** —
and tightening the divergence threshold made it *worse*.

**Root cause MEASURED (this is what makes it final rather than "needs more
work")**: the market's implied point forecast is ~20% more accurate than NBM's
(MAE 1.27 °F vs 1.59 °F at 12h), while NBM's published spread is close to
correct (published SD / realised RMSE = 0.93). The deficit is the **forecast**,
not the probability conversion, so a better error model cannot recover it.

**The staleness objection is closed, not merely argued**: NBM publishes **no**
daytime-max forecast at less than 12 hours' lead (the 18Z cycle's 00Z-valid
`TXN` is null). The model loses at NOAA's freshest. Also, these markets trade
for only **~42 hours**, so there is no market price beyond ~36h of lead —
"NOAA sees further ahead" has no venue here.

**Do NOT rebuild this without new information.** Full reasoning, all numbers,
the retraction of an intra-session wrong claim, and what this does and does not
kill: **DECISIONS.md Session 31 entry**. Raw output: `backtest/reports/`.
Reusable traps and gotchas: `backtest/README.md`.

### DIRECTION SET, Session 30 (2026-08-01) — SUPERSEDED ON S6 by Session 31 above; the rest still stands
Spec-only session. Full architecture, math, gates and build plan in
DECISIONS.md's Session 30 entry (the authoritative version — this is a
pointer, not a summary to build from). The short form:
- **Chosen: S6 — External Model Divergence**, weather/NOAA first, in
  detect-and-log mode, gated behind an offline backtest. New
  `FairValueEngineAgent` (pluggable `FairValueProvider` registry) +
  `DivergenceScannerAgent` + additive `FairValueEstimateEvent`.
- **Deferred: market-making (S8)** — behind a live order-management layer
  that does not exist. Measured surface for whenever it's picked up: 486
  markets with spread ≥2¢ and ≥100 contracts both sides.
- **Continuing: S5a/S5b as a cheap passive canary**, not a priority — a
  REST poller plus arithmetic, no LLM, no orders. Turns "one snapshot found
  nothing" into real frequency data over weeks.
- **Kelly finally has a correct home**: for a binary contract bought at
  price `c` with model probability `p`, `f* = (p − c)/(1 − c)`. Fed the
  model's probability, never a hardcoded per-strategy constant.
- **Nothing was deleted or gutted.** All arb groundwork, the event bus,
  order-book reconstruction, RiskGate, PaperExecutor, ComplianceOfficer,
  and the Telegram agent stay as reusable substrate.

### Kalshi fee structure — PRIMARY-SOURCE CONFIRMED, Session 30 (2026-08-02)
From Kalshi's published fee schedule (effective 2026-07-07), supplied by
the operator. **The taker and maker formulas have different default
multipliers — this is the detail every secondary source omits:**
```
taker:  round up(M × 0.07   × C × P × (1−P))    M defaults to 1
maker:  round up(M × 0.0175 × C × P × (1−P))    M defaults to 0
```
So **the maker fee is $0 by default**, charged only on the ~76 series
explicitly listed in the schedule's "Non-Standard Fees" table with Maker
Multiplier 1. Session 28's "maker fee = $0 on most Kalshi markets" is
**correct**. Session 30 briefly published a contrary correction based on
three agreeing secondary sources (and this repo's own `KalshiFeeModel`
docstring) that all quote the 0.0175 coefficient while omitting the
zero default multiplier; that correction was wrong and has been retracted
in DECISIONS.md. **Standing lesson: agreement among secondary sources is
not confirmation.**
Measured live consequence: **42.5% of 24h volume and 3,651 of 3,858
tradeable two-sided markets carry no maker fee**, at a 2¢ median spread;
489 of them show ≥2¢ spread with ≥100 contracts both sides. The
fee-charging series (KXPGATOUR, KXMLBGAME) are the highest-volume *and*
the tightest at 1¢ — already professionally market-made. Zero-fee volume
leaders: KXMLBTOTAL, KXBOXING, KXLIGAMXGAME, KXMLBSPREAD, KXMLSGAME.
Also from the primary source: rounding prose says "centicent" ($0.0001)
but the published fee table shows ceil-to-the-cent behavior on small
orders — implement against the table and verify against a real fill.
Ten series carry zero taker *and* maker multiplier, but have zero measured
volume. Kalshi now also lists **perpetual futures** on a tiered bps
schedule (taker 12.0 / maker 5.0 bps at tier 0) — new instrument class,
noted, not acted on.

### Live Kalshi universe measurements — CONFIRMED LIVE, Session 30 (2026-08-02 ~03:00 UTC)
Public REST, no auth, 40,000 open markets (`mve_filter=exclude`),
74,654,881 contracts of 24h volume. Reusable baseline — re-measure rather
than assume these are still current:
- Volume by domain: **sports 75.4%** (PGA 31.3%, MLB ~36%), **weather
  3.2%** (2,404,232 contracts / 672 markets), **Fed-CPI-econ 0.1%**
  (46,953 contracts / 453 markets — effectively dead, which is why
  "Kalshi vs CME FedWatch" was ruled out as a starting point).
- Of 3,918–3,938 markets with vol≥100 and a two-sided book: spread p25/med/
  p75/p90 = 1¢ / **2¢** / 4¢ / 8¢; top-of-book `min(bid,ask)` p25/med/p75/
  p90 = 5 / **42** / 395 / 1,395 contracts.
- **Kalshi weather markets settle on NWS's own data** — verbatim from
  `rules_primary`: *"as reported by the National Weather Service's
  Climatological Report (Daily)"* for a named station. Forecast source and
  settlement source are the same agency's number for the same station.
  Structure: 12-market city-day temperature ladders, machine-parseable via
  `strike_type` (`greater`/`between`) and `floor_strike` — no LLM needed.

### RESOLVED, Session 30, then BUILT AND RUN in Session 31 — the data legs below are all CONFIRMED WORKING; see the S6-dead entry above for what the backtest concluded
**Session 31 correction to the plan below, in NOAA's favour**: the GRIB2 +
`.idx` byte-range route described here is **not needed**. The same bucket's
`text/` suite publishes plain-ASCII **station** bulletins, and the NBP product
(`blend_nbptx.tCCz`) carries per station and valid time `TXNMN` (mean),
`TXNSD` (spread) **and** `TXNP1/P2/P5/P7/P9` (quantiles) — for exactly the
airport stations Kalshi settles on, with no GRIB decoder and no grid
interpolation. `backtest/` uses it and needs **zero new dependencies**. The
only cost is that values are integer degrees including the spread; measured,
that did not bind (published SD / realised RMSE = 0.93). Session 31 also
confirmed **NBS** (`blend_nbstx.tCCz`) publishes **hourly** cycles while NBP is
6-hourly, and that `expiration_value` on a settled Kalshi market **is** the
observed settlement value, so no separate observations feed is required.

### RESOLVED, Session 30 (2026-08-02): the S6 backtest is buildable now — all three data legs confirmed reachable and unauthenticated
This was flagged as the biggest open unknown ("does a usable archive of
past forecasts exist?") and then answered the same session. **A real
backtest over real history is a one-session job — no weeks of forward
collection needed.** All three legs verified live, not assumed:

**1. Forecast archive — NOAA National Blend of Models on AWS Open Data.**
Bucket `noaa-nbm-grib2-pds`, anonymous S3 list/get, no key, no account.
Coverage **2020-05-18 → current** (confirmed both ends). Product suites per
cycle: `core/`, `qmd/` (quantiles), `text/`. Critically, the `core` GRIB2
index already carries:
```
TMAX:2 m above ground:12-24 hour max fcst              <- what Kalshi settles on
TMAX:2 m above ground:12-24 hour max fcst:ens std dev  <- forecast uncertainty
APCP:surface:12-24 hour acc fcst:prob >0.254           <- direct PoP, for KXRAIN
```
**This substantially retires the "must build a bespoke error model"
concern**: NBM publishes the ensemble standard deviation alongside the
mean, so a first-cut `P(high > strike)` is available directly, with the
`qmd/` quantile suite as the better-calibrated upgrade if a Gaussian
assumption proves inadequate. `.idx` sidecar files exist for every GRIB2,
so a backtest can **byte-range fetch just the TMAX record** rather than
pulling multi-GB files — cheap and fast.

**2. Kalshi settled outcomes.** `GET /markets?status=settled&series_ticker=
KXHIGHLAX` returns clean labels: `result` ∈ {yes, no}, plus `floor_strike`
and `strike_type`. 414 settled markets retrieved for that one series.

**3. Kalshi historical prices — the market baseline.** `GET /series/
{series}/markets/{ticker}/candlesticks?start_ts=&end_ts=&period_interval=`
returns HTTP 200 with hourly bars carrying `yes_bid`, `yes_ask`, and OHLC
`price` plus volume and open interest. **It carries bid AND ask**, so the
backtest can be scored against the executable side of the book from the
start — structurally avoiding the bug class that invalidated S1.

**The real binding constraint is Kalshi's history, not NOAA's.** KXHIGHLAX
settled markets begin **2026-05-25** (~69 days as of 2026-08-02), ~12
markets per city-day across ~12 cities. Workable sample, but **summer-only
and seasonally narrow** — calibration measured on LA in July should not be
assumed to transfer to Chicago in January. State the season in any result,
and treat cross-season generalization as unproven.

### 30-day paper clock RESET, Session 30 — the old dates are dead
Started 2026-06-29 targeting 2026-07-29; that date has passed and the
window is not usable evidence — 9 of its first 14 days had a dead
persistence layer (Session 26) and every S1 trade in it is a confirmed
book-reconstruction artifact (Session 29). **The clock restarts when a
strategy that has passed its gates actually begins paper trading.** No live
trading on anything until that clock has genuinely run.

### book-reset recovery is HEALTHY — a Session 30 "regression" alarm that was a log-naming artifact, plus the rename that fixes it
**Do not re-raise this as a regression.** Session 30 briefly reported the
recovery mechanism as being at ~0% completion, based on a VPS measurement of
2,174 `book_snapshot_requested` against 0 `book_snapshot_applied`. **That
conclusion was wrong**, and both halves of it were log-naming problems:
1. `book_snapshot_requested` was logged **after** a successful
   `apply_snapshot()` in `_request_snapshot` — so despite its name it counted
   *completed recoveries*, not attempts.
2. `book_snapshot_applied` exists only inside `OrderBook.apply_snapshot` at
   **DEBUG** level, which has been filtered out of production since the
   Session 26 disk-fill fix. It could never have appeared in the logs
   regardless of system health.

**Correct reading of the same measurement: 2,174 successful REST book
recoveries in 10 minutes against 16 failures — a 0.7% failure rate, better
than Session 23's confirmed 5.5%.** The Session 22/23 fix is working.

**Fixed Session 30**: the INFO-level log in `_request_snapshot` is renamed to
`book_snapshot_applied_rest` and now carries `bid_levels`/`ask_levels`, so it
reads as the completion signal it actually is. **Any log analysis written
before 2026-08-02 that greps for `book_snapshot_requested` is counting
successes, not attempts** — re-read prior sessions' numbers with that in mind.
Note this also means there is currently **no** counter for reset *attempts*;
add one deliberately if attempt-vs-success ratio is ever needed.

The separate, still-open "stuck order-book reset loop" item (Session 26 —
specific markets logging `book_needs_reset`/`book_reset_throttled` on every
delta indefinitely) is unaffected by this and remains real.

### Minor, undocumented: RSS parse errors in RegulatoryIntelligenceAgent
Observed live Session 30: `RSS parse error: mismatched tag: line 26, column 4`
fires twice at every startup, after which the 6h cycle proceeds normally. One
of the configured feeds is serving malformed XML. Low severity — the cycle
survives it — but it means at least one regulatory source is silently
contributing nothing. Identify which feed and either fix the parse or drop it.

### VPS SSH — the key is NOT in ~/.ssh; use `-i ~/kalshi-keys/oracle-vps.key`
Recorded so no future session repeats Session 30's mistake. The VPS key
lives at **`~/kalshi-keys/oracle-vps.key`**, not `~/.ssh/id_rsa` and not
in any `~/.ssh/config` entry. The correct command is:
```
ssh -i ~/kalshi-keys/oracle-vps.key ubuntu@147.224.209.18
```
Session 30 initially checked only `~/.ssh/`, found `id_rsa` rejected, and
wrongly concluded "VPS access lost, state unknown" — a conclusion that was
committed to three docs before the operator corrected it. The lesson is
the project's own standing one: an absence found in one location is not
evidence of absence, and asking costs less than a wrong claim in a doc.

**VPS state CONFIRMED LIVE, Session 30 (2026-08-02 ~04:00 UTC)**: service
`karbot` **active**; uptime 35 days; disk **17% of 49G** (healthy — for
contrast, the Session 26 outage was 100%); repo at `d1ac08c`, i.e. one
commit behind `main` after this session's docs-only push (expected; no
code change to deploy). A `*** System restart required ***` notice is
showing, and 12 pending updates including 3 security updates — worth
scheduling, not urgent.

Separately and still unexplained: a local `logs/audit_trail.jsonl`
`DailySummary` write at 2026-08-02T03:11 UTC with **no** `karbot_runner`
process running locally. Not investigated; flagged rather than waved off.

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

### Order-book reset loop — ROOT CAUSE FOUND, Session 34 (2026-09-27). Confirmed against Kalshi's own AsyncAPI spec, not a fix yet — deliberately not rushed into live code.
**`seq` is scoped per subscription (`sid`), not per market.** Kalshi's
`orderbook_delta` channel accepts `market_tickers` (an array) on one
subscribe call, which returns one `sid`; every ticker in that batch shares
one `seq` counter (confirmed from `https://docs.kalshi.com/asyncapi.yaml` —
`sid`: "Server-generated subscription identifier... used to identify the
channel"; `seq`: "Sequential number... Used for snapshot/delta consistency",
scoped to that same `sid`). `price_watcher.py` subscribes in batches of 50
tickers per message (per this file's own architecture notes) — so up to 50
markets can share one `seq` stream. `OrderBook.apply_delta`'s gap check
(`agents/floor/price_watcher.py`, `if seq != self.sequence + 1`) compares
each incoming `seq` against a counter stored **per market** (`self.sequence`
on that market's own `OrderBook`). Any time a *different* market in the same
batch updates between two deltas for *this* market — which for a market that
isn't the single busiest ticker in its batch is close to certain — the
per-market comparison sees a "gap" that never happened on the wire.

**Live-confirmed, same session, VPS journal, 2h window**: 29 distinct
markets triggered a REST recovery at all; the busiest (`KXRAIN-26SEP27-AUS`)
recovered 615 times — roughly every 10–20s, i.e. on very close to *every*
delta it received, each one logged as a fresh `sequence_gap_detected`
immediately after the prior recovery. Thin markets sharing a batch with
busier neighbors (weather rain contracts, one live soccer/cricket match)
dominate the list; a market that gets ≤1 delta in the whole window never
exercises the check at all (its first-ever delta always passes trivially),
which is why most subscribed markets never appear in this list — not
because they're healthy by a different mechanism, just quiet enough not to
trip it.

**This is not currently a correctness bug** — every detected "gap" still
triggers a real REST snapshot fetch that succeeds and fully replaces the
book, so affected markets stay accurate, just refreshed via REST-on-every-
delta instead of WS deltas as designed. The cost is efficiency and log
volume (the exact shape of the Session 26 disk-fill outage) and unnecessary
load against Kalshi's rate limit, not stale or wrong prices.

**Not fixed this session, deliberately** — the correct fix (track sequence
continuity per `sid`/subscription batch, not per individual `OrderBook`) is
a real restructuring of `_books`/`apply_delta`'s gap logic, not a one-line
patch, and this system is live. Rushing it risked exactly the kind of
regression this project has been burned by before (Session 22's
under-verified REST-auth "fix" that crashed PriceWatcher 3x in 8 minutes).
Scoped for a dedicated session: capture the `sid` value from each
`subscribed`/`orderbook_snapshot` message per batch, key gap-tracking by
`sid` instead of `market_id`, and decide whether a genuine gap on a shared
`sid` should refresh every market in that batch (safe but heavier) or only
the specific market whose price actually changed unexpectedly (lighter,
needs more care). Verify with a live capture showing zero false gaps for a
market that is the sole member of its batch, before calling it fixed.

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

### P&L figures likely inflated during paper trading — CLOSED, Session 33 (2026-08-29), corroborates Session 29's independent finding
- Originally flagged Session 25 as HIGH PRIORITY / NOT YET RE-VERIFIED. It was
  effectively already answered by **Session 29's KNOWN DEBT item 1** (S1 is
  structurally impossible; all observed paper trades correlate 100% with a
  `sequence_gap_detected` event on the same market at the same second — i.e.
  every one is a book-reconstruction artifact, not a real edge) — but that
  closure was never cross-referenced back to this entry, so it sat looking
  open. Session 33 pulled all 757 rows from `compliance.db` directly (see the
  fee-variance entry immediately below) and confirms the same population: the
  inflated $58–$288-per-trade figures cited here are exactly the pre-Session-26
  era, the same trades Session 29 independently proved were sequence-gap
  artifacts via a completely different method (event-log correlation vs.
  direct fee/PnL-by-timestamp query). Two independent investigations landing
  on the same 757 rows and the same conclusion is real corroboration, not
  just a restatement. `s1_canary_mode=True` (Session 28) already stops any of
  this from reaching a live trade. Closed — no further action.

### Paper trade fee variance — CLOSED, Session 33 (2026-08-29)
- Operator observed live via Telegram trade-executed messages on
  2026-07-01 evening that fee amounts vary in an unexplained way across
  trades: some show a flat $70.00 fee regardless of PnL size, others show
  $0.00, $42.78, $113.27, $56.64. **Investigated directly against
  `compliance.db` (all 757 rows, `SELECT timestamp, fee_paid, expected_pnl_usd
  ... ORDER BY timestamp`), not inferred.** Finding, more precise than the
  original hypothesis: it is **not** simply "flat $70 before the Session 26
  fee-model fix, price-dependent variance after." 312 rows show exactly
  `fee_paid=70.0`, and a long tail of other large values ($15.29, $42.78,
  $63.14, $83.65, $160.07, $221.71, $260.56, $329.88, …) sit alongside them —
  **all of these, including the non-$70 ones, are the pre-Session-26 flat-14%
  formula evaluated at different Kelly-derived position sizes** (0.14 × size,
  and $500 was simply the single most common size, landing exactly on $70).
  The last such row is timestamped 2026-07-13T19:09:14 UTC. Every row after
  that has `fee_paid` under $2.30 (0.07105, 0.007546, 2.2723848, 0.09065,
  0.00119945) — exactly the 5 trades Session 27 already described as
  "$0.05–$81.36" liquidity-capped positions, now confirmed as a hard fee-value
  boundary in the data itself, not just a session's recollection. So the
  "variance" was always two different code-era populations sitting in one
  table, not a live bug in the corrected formula. Closed — this also directly
  answers the P&L-inflation item immediately above, since it is the same
  underlying dataset.
- **New, found while pulling this data, not fixed this session**:
  `compliance.db`'s `trades` table has `filled_price`, `quantity`, and
  `ordered_price` **NULL on all 757 rows**, every trade ever recorded. The
  Session 16 fix documented elsewhere in this file only touched the
  CSV-writing path (`kalshi_trades.csv`'s `_build_trade_row`); the
  `compliance.db` INSERT path (`ComplianceOfficer`'s `TradeExecutedEvent`
  handler) apparently never received the equivalent fix and has never
  populated these three columns. Out of scope for a fee-variance check —
  flagged as new debt, not investigated further.

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
**READ FIRST (Session 36, 2026-09-28): the new-strategy screen is done — see
KNOWN DEBT's top entry and DECISIONS.md Session 36.** Status of the whole
direction question after it: S1, S6, S5a/S5b-as-a-business and now C1
(favourite-buying as a taker) are measured dead; market-making is still
undecided, with its first offline evidence (side-specific, mildly positive). The
three cheapest next moves, all $0 and none touching the live path:
1. **C10 weather post-peak nowcast** — one session: cached candles + IEM ASOS
   real-time observations, P(outcome | observed max so far) vs the ask.
2. **C9 cross-series consistency** — one session from candles.
3. **Forward re-run, ~2026-10-28**: re-run `backtest/tape/` on markets settled
   2026-09-28 → 2026-10-28 for the C2 maker split, **frozen exactly as written
   in DECISIONS.md Session 36**. No code needed. An input to the market-making
   decision, not a strategy.
**Stopping rule and roadmap (operator-approved, end of Session 36; DECISIONS.md
Session 36 decision 6 as amended):** if C10 and C9 both die, the search for new
Kalshi signals stops. The project doesn't:
(a) one **offline venue screen** (US-legal venues ranked by access, free data
for a kill test, fees and competition; no accounts, no money, no live code);
(b) market-making prerequisites: Health Monitor deploy-confirm, then the
Kalshi-specific `sid`-scoped sequence fix;
(c) the ~2026-10-28 C2 re-run, then market-making yes or no;
(d) **checkpoint ~2026-11-30**: if nothing has passed the gate on real quotes
and market-making is no, park the project deliberately.
This supersedes Session 30's "other venues wait for a Kalshi edge". The
market-making sequencing below otherwise stands.

**Earlier READ FIRST (Session 32): two directions have now been executed.** S6 weather divergence was
built, measured and FAILED gate G2 (Session 31). The S5a/S5b canary that the
operator chose in its place was **built and live-verified in Session 32** — see
DECISIONS.md Session 32 (authoritative) and the KNOWN DEBT entry above. It has
found **zero candidates so far**, which is information about the market, not
about the code: every near miss is exactly one spread wide.

**Both of Session 32's follow-ups were completed the same session:**
1. ~~Deploy the canary~~ — **DONE. `karbot-canary.service` is installed,
   enabled and active on the VPS**, sweeping every 5 minutes at `Nice=10`.
   Verified live: sweeps at 22:24/22:25/22:30 UTC, coverage climbing 684→765
   evaluated events, zero errors, every sweep reconciling, and the fee model
   confirmed agreeing with the live trading path at startup.
2. ~~Resolve the void-settlement question~~ — **DONE, and the question was
   framed wrong**; see KNOWN DEBT. The basket guarantee survives cancellation.

**Direction — DECIDED by the operator, 2026-08-02 (end of Session 32).**
A sequencing decision with an information trigger, **not** a commitment to build
market-making; that call is made when the Kalshi enquiry is answered.
Full reasoning in DECISIONS.md's Session 32 addendum ("the direction question,
and why the infrastructure items are on market-making's critical path"). The
sequence: **send the Kalshi market-maker enquiry
(`documentation/kalshi-mm-enquiry-draft.md`, drafted, NOT sent) → let the canary
accumulate → spend intervening sessions on the two infrastructure prerequisites
→ decide market-making with the exchange's answer and ~2 weeks of data in hand.**
The load-bearing point: the **Health Monitor** and the **stuck order-book reset
loop** are prerequisites for market-making, not alternatives to it — a quoting
system whose agent silently dies holds inventory nobody is managing, and a maker
with a stale book quotes a price someone will take. The infrastructure list for
this purpose is **bounded to those two items**; the rest of the standing list is
cosmetic and must not be used to fill sessions. The decision has an
**information trigger, not a date**.

**The one live thing to do next:**
- **Let it run, then read the log.** The measurement to watch is not just
  candidate count but the **`confirmed` vs `vanished_on_recheck` ratio** —
  that is what separates "real resting arbitrage" from "our view of the book is
  noisy", the question Session 29 could not answer from one snapshot. Heartbeat:
  `tail -1 logs/basket_candidates.jsonl | python3 -m json.tool` on the VPS.
  Also re-check the canary's effect on `book_reset_rest_failed` over a longer
  window than the 13 minutes measured so far.

**The larger direction question is still open**, and the other candidates remain
explicitly on the table (operator, Session 31: *"let's continue to have the
other options be considered where appropriate and justified later"*).

The candidates, with their state:

- **Market-making (S8)** — Session 30's deferred option, untouched by the S6
  result and on better footing than that entry first concluded once the maker
  fee was read correctly: **489 markets with ≥2¢ spread and ≥100 contracts both
  sides, paying no maker fee at all.** Cost: it needs the live
  order-management layer that does not exist, built entirely up front. This is
  the largest new subsystem in the project's history. **Session 36 correction:
  "no offline test possible" was too strong** — the public tape labels the taker
  side, so aggregate maker P&L (adverse selection included) is measurable. Result:
  all makers +1.11% (p = 0.032, fails α); makers buying favourites positive in
  both periods, makers buying longshots negative. Individual fill quality is
  still only observable by quoting.
- ~~**S5a/S5b passive arb canary**~~ — **BUILT, Session 32.** See KNOWN DEBT
  above. Still never disproven: zero candidates in the first sweeps is one more
  snapshot's worth of evidence, not a verdict. Now it accumulates automatically
  instead of needing a session to check by hand.
- **A different `FairValueProvider`** — the abstraction and the divergence
  *shape* survive; one provider on one market family failed. But apply the
  screening question Session 31 added to SIGNAL_REGISTER.md before spending
  another session: **"is there a reason the market does not already know
  this?"** A free public forecast every participant reads is the weakest
  possible candidate, and that is exactly why weather lost.
- **Stop and consolidate** — the infrastructure list below has real, unfixed
  items, several of which stop being cosmetic the moment anything carries
  variance.

### Phase 0 — prerequisites, no strategy code (STATUS: 3 of 4 done; item 4 re-scoped)
1. ~~Resolve the NOAA historical-forecast-archive question~~ — **DONE,
   Session 30.** Answer: NBM archive on AWS (`noaa-nbm-grib2-pds`,
   anonymous, 2020→now) carries TMAX + ens std dev + `.idx` byte-range
   access; Kalshi gives settled outcomes and `candlesticks` price history
   with bid/ask. The backtest is buildable now over ~69 days of Kalshi
   weather history. Full detail in KNOWN DEBT above.
2. ~~Fix the RiskGate/PaperExecutor unit system~~ — **DONE, Session 30
   (2026-08-02).** `RiskGateAgent._calculate_position_size` now returns an
   **integer contract count**, computed as a dollar budget divided by the
   real per-contract basket cost (`_basket_cost_per_contract`, summed from
   leg ask prices); floors to int and returns 0 below 1 contract. Sizing
   splits by strategy class: `RISKLESS_STRATEGIES` (S1/S2/S5a/S5b) size
   against the caps — removing the hidden ~5.26% Kelly floor that made
   `s1_min_net_profit_pct` a dead letter — while statistical strategies use
   `f* = (p − c)/(1 − c)` fed by the new `OpportunityEvent.model_probability`
   field, and size to **0** rather than guessing when it's absent. Check 2
   now falls back to the derived basket cost, so it finally binds. The
   approval log emits `size_contracts` + `cost_usd` instead of the
   misleading `size_usd`. `KalshiFeeModel.taker_fee_dollars()` added with
   Kalshi's real per-order round-up (validated against the published fee
   table); `taker_fee_fraction()` kept but documented as optimistic.
   19 new tests.
3. ~~Fix the `from_yaml()` config-parsing gaps~~ — **DONE, Session 30.**
   `data_feeds:`, `capital:`, `risk:`, `strategies:` and `intelligence:`
   are now parsed via a generic `_section()` helper that also **warns on
   unknown keys** (`config_unknown_keys`) — closing the Session 24 class of
   bug where a key looks configured and silently does nothing. RiskConfig
   hard limits and the Phase 1 invariants still bind against YAML; both are
   tested. 9 new tests. **Still open**: `--mode` is parsed and never
   applied — make it override or remove it.
4. **Make paper resolution settle against real outcomes** — RE-SCOPED,
   Session 31. `PaperExecutor` resolves every trade at its own `expected_pnl`
   after a fixed delay, which is tautological for any directional strategy.
   This was listed as a hard prerequisite for S6 *paper trading*; S6 never got
   there, so it is **no longer blocking anything today**. It becomes blocking
   again the moment any variance-bearing strategy reaches paper. Left open
   deliberately rather than built speculatively — the shape of the fix depends
   on which strategy needs it.

### Phase 1 — measure before building — **DONE, Session 31. RESULT: NEGATIVE.**
5. ~~`FairValueEngineAgent` + `NoaaTemperatureProvider` + `backtest/`~~ —
   **`backtest/` was built; the two agents deliberately were NOT.** The
   calibration report came back negative before any agent was justified.
   NOAA/NBM scores Brier 0.2013 against the market's 0.1757 at 12h lead on
   contested markets (skill −0.146, P(model no better) = 1.000, 36 independent
   dates, 17 of 18 cities losing). **G1 PASS, G2 FAIL, G3 confirms the
   failure.** Root cause measured: the market's implied point forecast is ~20%
   more accurate than NBM's, while NBM's spread is close to correct — the
   forecast is the deficit, not the conversion. See DECISIONS.md Session 31.

### Phase 2 — detect-and-log live — **CLOSED, never started**
6. ~~`DivergenceScannerAgent`~~ — **not built and should not be.** Its whole
   purpose was to confirm live that a backtested edge reproduces. There is no
   edge to reproduce. Building it would be running plumbing for a signal
   measured to be worse than the price it trades against.

### Phase 3 — arb canary — **DONE, Session 32. Built, live-verified, zero candidates so far.**
7. ~~**S5a/S5b passive detect-and-log scanner**~~ — shipped as `canary/`.
   Everything the Session 28 spec asked for, plus three things it did not
   anticipate and that were found live: relations must be gated on **settled
   history** rather than strike arithmetic (KXMLBSPREAD puts two metrics in one
   event and interval logic "proves" a false implication — 2,267 measured
   violations); the bulk snapshot is **stale within seconds**, so every
   candidate is re-priced from `/orderbook` before being logged; and some events
   settle **neither YES nor NO** (`result: "scalar"`, 4.1% of ATP), which breaks
   the payout guarantee outright. Session 31's weather finding held up
   independently — KXHIGHLAX qualifies as exclusive + exhaustive + disjoint.
   Remaining work is deployment and the void-settlement question, both listed at
   the top of this section.

### Standing / infrastructure
8. **Re-audit every "CONFIRMED LIVE" claim in this file against actual VPS
   state.** SSH works — use `ssh -i ~/kalshi-keys/oracle-vps.key
   ubuntu@147.224.209.18` (the key is NOT in `~/.ssh/`). "Confirmed live"
   means checked against `git log -1` on the VPS itself plus fresh log
   output, never inferred from a local commit. Also schedule the pending
   VPS reboot (restart-required notice, 3 security updates outstanding).
   **Spot-checked, Session 33 (2026-08-29)**: `git log -1` matches local
   `main` exactly (`7ec5b3d`), `karbot`/`karbot-canary` both active and
   enabled, disk 17% of 49G, `telegram.enabled: true` confirmed by reading
   `config.yaml` directly. Not the full line-by-line audit this item asks
   for — still standing for that.
9. ~~Confirm Kalshi's maker fee from the primary source~~ — **DONE,
   Session 30**: primary fee schedule obtained; maker M defaults to 0, so
   maker fees are $0 outside ~76 enumerated series. See KNOWN DEBT above.
10. ~~Build the Health Monitor agent~~ — **DONE, Session 34 (2026-09-27).**
   `agents/monitoring/health_monitor.py` — `HealthMonitorAgentImpl` +
   `HealthMonitor` runner stub, conforms to the standard BaseAgent interface.
   Subscribes to `AgentHeartbeat`, learns each agent's cadence dynamically
   from real traffic (never alerts on the 5 agents — position_tracker,
   paper_executor, compliance, regulatory_intelligence, telegram_agent —
   that don't currently publish one), and alerts via the existing generic
   `TelegramNotificationEvent(tier=1, ...)` path (the same mechanism
   karbot_runner.py already uses for AUTO-RECOVERY EXHAUSTED — no new event
   type) on a heartbeat→silence transition, with a distinct recovery alert
   when it resumes, mirroring the Session 20 FeedHealthEvent pattern so one
   continuous outage produces one alert, not a repeat per check tick.
   Two new `SystemConfig` fields (`health_monitor_check_interval_seconds`=30,
   `health_monitor_silence_threshold_seconds`=180, both YAML-tunable via
   `system:`). Wired into both `karbot_runner.py` agent lists (mock and
   live). 5 new tests (`tests/test_health_monitor.py`), 326/326 total
   passing, runner smoke test exits cleanly. **Not yet confirmed live** —
   deploy and observe at least one real heartbeat cycle before treating the
   silence-alert path as proven, the same standing discipline every other
   "built" feature in this file has had to clear.
11. **Fix or explicitly disable the S3 pipeline** (Session 28, DECISIONS.md
   entry 2): wire `update_markets()` from PriceWatcher's market fetch (or
   delete the loop), switch pricing to asks, guard zero/empty-book
   prices, and decide single-leg (statistical) vs paired-leg (riskless-
   if-relation-holds) semantics. `s4_settlement_arb_enabled` already
   defaults False as of Session 29.
11b. ~~Diagnose the ~0% book-reset completion rate~~ — **WITHDRAWN same
   session: there was no regression.** The alarm came from two misleading
   log names (see KNOWN DEBT above); the real rate is 2,174 successful
   recoveries per 10 minutes against 16 failures (0.7%). Log renamed to
   `book_snapshot_applied_rest`. No action needed.
12. ~~Investigate the stuck order-book reset loop~~ — **ROOT CAUSE FOUND,
   Session 34 (2026-09-27), NOT YET FIXED.** `seq` is scoped per Kalshi
   subscription (`sid`), which can cover up to 50 tickers at once (this
   project's own batch size) — not per individual market, as
   `OrderBook.apply_delta`'s gap check assumes. Confirmed against Kalshi's
   own AsyncAPI spec and live VPS journal (29 markets recovering up to 615x
   in 2 hours, almost one REST fetch per delta). Not a correctness bug today
   — every "gap" still triggers a real, successful REST snapshot, so books
   stay accurate — but it is real inefficiency and the exact log-volume
   shape that caused the Session 26 disk-full outage. Fix requires tracking
   sequence continuity per-`sid` instead of per-market — scoped, not
   implemented, deliberately not rushed into a live system. Full writeup
   and fix approach: KNOWN DEBT above.
13. ~~**Investigate paper-trade fee variance**~~ — **DONE, Session 33
   (2026-08-29).** Confirmed against all 757 `compliance.db` rows: the
   flat-$70-and-varied-large-fee population is entirely pre-Session-26
   (flat-14% formula at different Kelly-derived sizes, not a broken new
   formula), ending exactly at 2026-07-13T19:09 UTC; every row after that
   has `fee_paid` under $2.30, matching Session 27's 5 known post-fix
   trades. See KNOWN DEBT for the full writeup, which also closes the
   separate Session 25 P&L-inflation item. New debt found in the process:
   `compliance.db`'s `filled_price`/`quantity`/`ordered_price` are NULL on
   all 757 rows — the Session 16 CSV fix never reached this table.
14. **Continue live-verifying Telegram alerting** (Session 19/20/24/25) —
   confirm: no `TypeError` on any real Kalshi WS disconnect with
   `kalshi_reconnect_retry` logs increasing (Session 19); a real "FEED
   DOWN"/"FEED RECOVERED" Telegram pair on any real disconnect/reconnect
   with no duplicates mid-outage (Session 20); the runner-restart AND
   CRITICAL "AUTO-RECOVERY EXHAUSTED" Telegram alert both fire if the
   restart budget is ever exceeded (Session 20). Note: Session 26 added a
   new, independent disk-space Telegram watchdog
   (`/usr/local/bin/karbot-disk-alert.sh`, hourly-cron-adjacent via
   `/etc/cron.d/karbot-disk-alert` every 15 min) — confirmed working live.
15. **Monitor the book-reset recovery (Session 22/23)** — watch that
   `book_snapshot_applied` keeps firing at a healthy rate and the 429 rate
   (currently ~5.5% right after restart, KNOWN DEBT) stays a one-time
   post-restart surge rather than a sustained pattern.
16. **Add a concurrency limiter on `_request_snapshot` REST calls** (KNOWN
   DEBT from Session 23, not urgent) — an `asyncio.Semaphore` or similar
   bounding in-flight REST snapshot fetches, to smooth the post-restart
   burst that produced the 429s. Only worth prioritizing if 429s become a
   recurring pattern rather than a one-time restart surge.
17. ~~**Telegram mute/unmute**~~ — **DONE, Session 33 (2026-08-29).** Added
   `/mute`/`/unmute` operator commands, scoped to
   `TelegramNotificationAgent._handle_operator_reply` exactly as planned
   (no event-bus or other-agent changes). In-memory `_muted` flag,
   resets to unmuted on every restart. Suppresses Tier 2 only (trade
   opened/resolved, rejected opportunity, generic tier-2 notifications);
   Tier 1 (leg failure, feed health) and pending permission requests are
   unaffected — the Session 20 feed-down alert still bypasses mute, as
   required. 16 new tests (`tests/test_telegram_mute.py`), 321/321 total
   passing. Deployed; service confirmed to restart cleanly with no errors.
   **CONFIRMED LIVE same session** — operator sent `/mute`/`/unmute` from
   Telegram directly and confirmed it working ("telegram appears to work").
18. **Paper trading — clock RESET as of Session 30.** The old 2026-06-29 →
   2026-07-29 window is dead (see KNOWN DEBT): 9 of its first 14 days had
   a dead persistence layer, and every S1 trade in it is a confirmed
   book-reconstruction artifact, not edge. A new 30-day clock starts only
   when a strategy that has passed gates G1–G5 begins paper trading.
   Review `logs/kalshi_trades.csv` and `logs/compliance_actions.jsonl`
   periodically; confirm resolved rows show nonzero `gain_loss` and
   `status=RESOLVED`.
19. **Live executor spec, then market-making (S8) — gated, last.** Design
   `live_executor.py` / `live_order_manager.py` (place/cancel/amend/
   reconcile, order state machine, cancel-on-disconnect, rate limits).
   This is a prerequisite for going live on *anything*, and specifically
   for market-making. Do NOT go live on S1 under any circumstances. The
   first live candidate is whichever strategy has measured gate data
   behind it. Re-derive the market-making case against the **corrected**
   maker fee before committing to it.

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
