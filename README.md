# Karbot Rage!

**Karbot Rage!** is a multi-agent automated trading system for decentralized prediction markets. It is a WallStRobotics / CAIO-grade project — built to production standards from session one.

> **Name etymology:** *Arbitrage* → swap "bit" for "bot" (it's a bot) → *Arbotrage* → prefix K for Kalshi → *Karbotrage* → split and add ! for brand energy → **Karbot Rage!**

## What it does

Six specialized agents run concurrently over a shared async event bus, covering the full trading loop:

| Agent | Role |
|---|---|
| PriceWatcher | Watches Kalshi market prices in real time |
| ArbScanner | Scans for arbitrage opportunities |
| RiskGate | Enforces position and exposure limits |
| MarketAnalyst | Analyzes market signals |
| ReflectionAgent | Post-trade reflection and strategy tuning |
| ComplianceOfficer | Always-on compliance monitoring (cannot be disabled) |

## Tech stack

- Python 3.8+, asyncio
- Pydantic typed config (`KarbotConfig`)
- Custom `EventBus` with typed event dataclasses (`core/events.py`)
- aiohttp, websockets, pyyaml, structlog, tenacity, aiosqlite
- Anthropic SDK (for future intelligence layer)
- pytest / pytest-asyncio

## How to run

```bash
# Activate the project virtualenv
source karbotrage_env/bin/activate

# Start all 6 agents (canonical entry point)
python karbot_runner.py
```

The legacy `python main.py` path still works but is intentionally not extended — it bypasses the event bus.

## Current phase: Phase 1

- Kalshi is the primary data source; Polymarket is gated behind `polymarket_ws_enabled` (disabled in Phase 1)
- Phase 1 invariants are enforced structurally in `KarbotConfig.__init__` — enabling Polymarket WebSocket or cross-platform strategies while `phase=1` raises `ValueError` at startup
- Paper trading mode only; live execution deferred until end-to-end paper test passes

## Project layout

```
karbot_runner.py          # Entry point — starts all 6 agents
core/events.py            # EventBus + all typed event dataclasses
karbot/core/
  config.py               # KarbotConfig (Phase 1 invariants, from_yaml, .phase, .paper_mode)
  events.py               # Re-exports from core/events.py
agents/
  floor/
    price_watcher.py      # PriceWatcher
    arb_scanner.py        # ArbScanner
    risk_gate.py          # RiskGate
  research/
    market_analyst.py     # MarketAnalyst
  management/
    reflection.py         # ReflectionAgent
    compliance.py         # ComplianceOfficer (always-on)
execution/engine.py       # Legacy monolith — do not extend until paper tested
data/market_data.py       # Kalshi-first market data
```

## Next up

1. Wire `ComplianceOfficer` subscriptions to `TradeExecutedEvent`
2. IRS dual-track logging (Kalshi = ordinary income, Polymarket = capital gains)
3. Paper trading end-to-end test

## License

MIT
