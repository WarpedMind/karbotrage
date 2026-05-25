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
- karbot/core/config.py: KarbotConfig typed dataclass with enforced risk hard limits
- karbot/core/events.py: Re-exports from core/events.py for agent imports
- agents/floor/price_watcher.py: WebSocket → PriceUpdateEvent publisher
- agents/floor/arb_scanner.py: PriceUpdateEvent → OpportunityEvent detector
- agents/floor/risk_gate.py: OpportunityEvent → ApprovedOpportunityEvent (8 pre-trade checks)
- agents/research/market_analyst.py: LLM semantic analysis → LogicalArbCandidateEvent
- agents/management/reflection.py: Nightly learning cycle, strategy weight updates

### Legacy execution path (do not extend — scheduled for removal)
- main.py / karbot/main.py: Old entry point
- execution/engine.py: Monolithic orchestrator — calls components directly, bypasses event bus
- data/market_data.py: Market data (Kalshi-first, Polymarket gated behind polymarket_ws_enabled)

## Current status
- core/events.py: Full event bus with all typed events — production-ready
- karbot/core/config.py: KarbotConfig with Phase 1 invariant enforcement at instantiation
- Agent stubs: All wired to EventBus via register_subscriptions(); imports resolve
- requirements.txt: Full — aiohttp, pydantic, websockets, structlog, tenacity, aiosqlite, anthropic, etc.
- execution/engine.py: Monolithic (flagged for incremental event-bus refactor)
- Paper trading: Not yet end-to-end tested via agent layer

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