# Karbot Rage! - Automated Trading System

## What this is
Karbot Rage! is a multi-agent automated trading system designed for decentralized prediction markets. It provides a modular framework with specialized agents for market monitoring, analysis, strategy execution, and compliance.

## Stack
- Python 3.8+
- Modular architecture with core, execution, data, intelligence, strategies, trading, and monitoring components
- Run with: python main.py

## Architecture

### Target architecture (event-bus-driven agents — extend this, not the legacy path)
- core/events.py: EventBus + all typed event dataclasses — the communication backbone
- karbot/core/: Package exists — agents import from here
  - karbot/core/config.py: KarbotConfig typed dataclass; Phase 1 invariants enforced structurally at `__init__` — `polymarket_ws_enabled=True` with `phase=1` raises `ValueError`, `s2_cross_platform_enabled=True` with `phase=1` raises `ValueError`; RiskConfig hard limits also enforced at instantiation
  - karbot/core/events.py: Re-exports all event types from core/events.py
- agents/floor/price_watcher.py: WebSocket → PriceUpdateEvent publisher
- agents/floor/arb_scanner.py: PriceUpdateEvent → OpportunityEvent detector
- agents/floor/risk_gate.py: OpportunityEvent → ApprovedOpportunityEvent (8 pre-trade checks)
- agents/research/market_analyst.py: LLM semantic analysis → LogicalArbCandidateEvent
- agents/management/reflection.py: Nightly learning cycle, strategy weight updates

### Legacy execution path (do not extend — removal blocked on karbot_runner.py + paper test)
- main.py / karbot/main.py: Old entry point
- execution/engine.py: Monolithic orchestrator — calls components directly, bypasses event bus — **INTENTIONALLY DEFERRED**: do not touch until karbot_runner.py is written and paper tested end-to-end
- data/market_data.py: Market data (Kalshi-first, Polymarket gated behind polymarket_ws_enabled)

## Current status
- core/events.py: Full event bus with all typed events — production-ready
- karbot/core/: Package exists and imports resolve; KarbotConfig Phase 1 invariants are structural (raise ValueError at instantiation), not just config defaults
- requirements.txt: Restored — aiohttp, pydantic, websockets, pyyaml, python-json-logger, structlog, tenacity, aiosqlite, anthropic, pytest, pytest-asyncio, black, flake8
- Agent stubs: All wired to EventBus via register_subscriptions(); imports resolve
- execution/engine.py: INTENTIONALLY DEFERRED — do not refactor until karbot_runner.py is written and paper tested
- Paper trading: Not yet end-to-end tested via agent layer

## Next session priorities (in order)
1. Write karbot_runner.py — starts agents via EventBus, not old engine
2. Wire compliance/officer.py to event bus
3. IRS dual-track logging (Kalshi = ordinary income, Polymarket = capital gains)
4. Paper trading end-to-end test

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
