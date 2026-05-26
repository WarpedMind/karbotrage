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
- karbot_runner.py: **NEW entry point** — starts all 10 Phase 1 agents as concurrent asyncio tasks; verified working. Use this, not main.py.
- core/events.py: EventBus + all typed event dataclasses — the communication backbone; priority queue uses 3-tuple (priority, seq, event) to avoid heapq comparison errors between same-priority events.
- karbot/core/: Package exists — agents import from here
  - karbot/core/config.py: KarbotConfig typed dataclass; Phase 1 invariants enforced structurally at `__init__` — `polymarket_ws_enabled=True` with `phase=1` raises `ValueError`, `s2_cross_platform_enabled=True` with `phase=1` raises `ValueError`; RiskConfig hard limits also enforced at instantiation. Now also has `from_yaml(path)` classmethod, `.phase` property (→ capital.phase), and `.paper_mode` property (→ system.paper_mode). TelegramConfig + RegulatoryIntelligenceConfig sub-dataclasses added.
  - karbot/core/events.py: Re-exports all event types from core/events.py
- agents/floor/price_watcher.py: `PriceWatcherAgent` (full impl) + `PriceWatcher` (BaseAgent-conforming stub used by runner)
- agents/floor/arb_scanner.py: `ArbScannerAgent` (full impl, has register_subscriptions) + `ArbScanner` (inherits it, adds run() stub)
- agents/floor/risk_gate.py: `RiskGateAgent` (full impl, has register_subscriptions) + `RiskGate` (inherits it, adds run() stub); subscribes to RegulatoryAlertEvent; _regulatory_pause=True blocks all trades when urgency=5; cleared by urgency=0 event from RegulatoryIntelligenceAgent
- agents/research/market_analyst.py: `MarketAnalystAgent` (full impl) + `MarketAnalyst` (BaseAgent-conforming stub used by runner)
- agents/research/regulatory_intelligence.py: **NEW COMPLETE** — `RegulatoryIntelligenceAgentImpl` (full impl) + `RegulatoryIntelligenceAgent` (BaseAgent stub); polls CFTC RSS + Federal Register every 6h; keyword pre-filter controls API costs; Claude Sonnet assesses urgency 1-5; urgency 3→Telegram FYI, 4→Telegram alert, 5→Telegram + trading pause; operator sends clear phrase via Telegram to resume; weekly sweep skips keyword filter; daily/cycle caps + circuit breaker; overflow queue for items exceeding per-cycle cap
- agents/management/reflection.py: `ReflectionAgentImpl` (full impl, renamed from ReflectionAgent) + `ReflectionAgent` (BaseAgent-conforming stub used by runner)
- agents/management/compliance.py: **v2 UPDATED** — IRS dual-track logging, append-only audit trail, compliance action log, REGULATORY_HALT enforcement; **polling loop removed** (now handled by RegulatoryIntelligenceAgent); subscribes to RegulatoryAlertEvent to log AI-assessed alerts to compliance_actions.jsonl; subscriptions wired to TradeExecutedEvent, LegFailureEvent, RejectedOpportunityEvent, RegulatoryAlertEvent
- agents/notifications/telegram_agent.py: **UPDATED** — TelegramNotificationAgent (full impl) + TelegramAgent (BaseAgent stub); subscribes to TelegramNotificationEvent, TelegramPermissionRequestEvent, RegulatoryAlertEvent (Tier 1), LegFailureEvent (Tier 1), TradeExecutedEvent (Tier 2), RejectedOpportunityEvent (Tier 2); getUpdates polling every 3s; 1 msg/sec rate limit; single-operator FIFO permission resolution; always publishes TelegramPermissionResponseEvent with response_text so RegulatoryIntelligenceAgent can check for clear phrase; enabled=False → no-op (no HTTP calls, no polling)

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
- karbot_runner.py: **Written and verified** — supports --mock-prices and --exit-after-test flags; 10 agents start and exit cleanly
- core/events.py: Full event bus with all typed events — production-ready; RegulatoryAlertEvent has full AI-assessment fields (urgency, summary, affected, recommended_action, raw_title, cycle_type); TelegramPermissionResponseEvent has response_text field; priority queue fixed with sequence tiebreaker
- karbot/core/config.py: KarbotConfig Phase 1 invariants structural + from_yaml() + .phase + .paper_mode + regulatory_halt + TelegramConfig + RegulatoryIntelligenceConfig + SecretsConfig sub-dataclasses; SystemConfig.paper_resolution_delay_seconds added
- agents/research/regulatory_intelligence.py: **COMPLETE** — full Regulatory Intelligence Agent; 11 tests passing; Claude Sonnet urgency assessment; cost controls (daily cap, circuit breaker, overflow queue, spend estimator); operator clear flow via Telegram
- agents/management/compliance.py: **v2 UPDATED** — polling loop removed; subscribes to RegulatoryAlertEvent; logs AI-assessed regulatory alerts to compliance_actions.jsonl
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
- 30-day paper trading clock started: 2026-05-26
- Full test suite: 35/35 passing ✓

## KNOWN DEBT

- correlation_score in PositionSnapshot is permanently 0.0 — Phase 3 item
- execution/engine.py — legacy monolithic path, intentionally deferred,
  must be removed or replaced before live trading; do not extend

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
1. Monitor paper trading for 30 days — review daily summary logs weekly
2. Provision Kalshi API credentials (RSA key generation and registration per .env.example)
3. Begin live executor spec after 30-day paper run completes (2026-06-25)

## FUTURE ROADMAP (do not build yet — design required first)

- Phase 2 Polymarket integration (after original principal recovered)
- Real-time market data via Kalshi WebSocket
- Advanced strategy agents (S3 logical arb, S4 settlement arb)
- Portfolio Manager agent for cross-strategy capital allocation

## GitHub
- Repo: https://github.com/WarpedMind/karbotrage_v1
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
