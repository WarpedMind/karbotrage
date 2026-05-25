# Karbot Rage! - Automated Trading System

## What this is
Karbot Rage! is a multi-agent automated trading system designed for decentralized prediction markets. It provides a modular framework with specialized agents for market monitoring, analysis, strategy execution, and compliance.

## Stack
- Python 3.8+
- Modular architecture with core, execution, data, intelligence, strategies, trading, and monitoring components
- Run with: `karbotrage_env/bin/python karbot_runner.py` (new path) or `python main.py` (legacy)

## Architecture

### Target architecture (event-bus-driven agents — extend this, not the legacy path)
- karbot_runner.py: **NEW entry point** — starts all 6 Phase 1 agents as concurrent asyncio tasks; verified working. Use this, not main.py.
- core/events.py: EventBus + all typed event dataclasses — the communication backbone
- karbot/core/: Package exists — agents import from here
  - karbot/core/config.py: KarbotConfig typed dataclass; Phase 1 invariants enforced structurally at `__init__` — `polymarket_ws_enabled=True` with `phase=1` raises `ValueError`, `s2_cross_platform_enabled=True` with `phase=1` raises `ValueError`; RiskConfig hard limits also enforced at instantiation. Now also has `from_yaml(path)` classmethod, `.phase` property (→ capital.phase), and `.paper_mode` property (→ system.paper_mode).
  - karbot/core/events.py: Re-exports all event types from core/events.py
- agents/floor/price_watcher.py: `PriceWatcherAgent` (full impl) + `PriceWatcher` (BaseAgent-conforming stub used by runner)
- agents/floor/arb_scanner.py: `ArbScannerAgent` (full impl, has register_subscriptions) + `ArbScanner` (inherits it, adds run() stub)
- agents/floor/risk_gate.py: `RiskGateAgent` (full impl, has register_subscriptions) + `RiskGate` (inherits it, adds run() stub)
- agents/research/market_analyst.py: `MarketAnalystAgent` (full impl) + `MarketAnalyst` (BaseAgent-conforming stub used by runner)
- agents/management/reflection.py: `ReflectionAgentImpl` (full impl, renamed from ReflectionAgent) + `ReflectionAgent` (BaseAgent-conforming stub used by runner)
- agents/management/compliance.py: **FULL v2 IMPL** — IRS dual-track logging, append-only audit trail, regulatory monitoring (CFTC RSS + Federal Register every 6h), compliance action log, REGULATORY_HALT enforcement; subscriptions wired to TradeExecutedEvent, LegFailureEvent, RejectedOpportunityEvent

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
- karbot_runner.py: **Written and verified** — all 3 spec verification steps pass; 6 agents start, run, and shut down cleanly
- core/events.py: Full event bus with all typed events — production-ready
- karbot/core/config.py: KarbotConfig Phase 1 invariants structural + from_yaml() + .phase + .paper_mode + regulatory_halt fields added
- agents/management/compliance.py: **v2 COMPLETE** — IRS trade logging, audit trail, regulatory monitoring, REGULATORY_HALT — all 7 spec verification steps pass
- All Phase 1 agent stubs: Conforming run() and register_subscriptions() on all 6 runner-facing classes
- requirements.txt: aiohttp, pydantic, websockets, pyyaml, python-json-logger, structlog, tenacity, aiosqlite, anthropic, pytest, pytest-asyncio, black, flake8
- execution/engine.py: INTENTIONALLY DEFERRED — do not refactor until paper tested end-to-end
- Paper trading: Not yet end-to-end tested via agent layer

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
1. Paper trading end-to-end test
2. Wire execution layer to emit TradeExecutedEvent / LegFailureEvent so compliance logs real trades

## FUTURE ROADMAP (do not build yet — design required first)

- **Regulatory Intelligence Agent** (Research Floor): Uses Claude API to read and
  interpret regulatory documents, assess urgency 1-5, send Telegram notifications
  with recommendations, ask operator permission before acting. Replaces keyword
  scanning with genuine AI interpretation. Weekly automated check cycle plus
  immediate alerts for high-urgency keywords.
- **Telegram notification integration**: Real-time operator alerts for regulatory
  events, system health issues, and permission requests.

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
- Run system: python main.py
- Run with debug: python main.py --debug
- Run with specific mode: python main.py --mode paper
