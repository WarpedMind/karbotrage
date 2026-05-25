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
- agents/management/compliance.py: **NEW** — `ComplianceOfficer` stub; always-on, cannot be disabled; subscriptions to be wired next session

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
- karbot/core/config.py: KarbotConfig Phase 1 invariants structural + from_yaml() + .phase + .paper_mode added
- agents/management/compliance.py: Created — ComplianceOfficer stub running
- All Phase 1 agent stubs: Conforming run() and register_subscriptions() on all 6 runner-facing classes
- requirements.txt: aiohttp, pydantic, websockets, pyyaml, python-json-logger, structlog, tenacity, aiosqlite, anthropic, pytest, pytest-asyncio, black, flake8
- execution/engine.py: INTENTIONALLY DEFERRED — do not refactor until paper tested end-to-end
- Paper trading: Not yet end-to-end tested via agent layer

## Next session priorities (in order)
1. Wire ComplianceOfficer subscriptions to TradeExecutedEvent
2. IRS dual-track logging (Kalshi = ordinary income, Polymarket = capital gains)
3. Paper trading end-to-end test

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
