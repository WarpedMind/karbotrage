# Decision Log

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
