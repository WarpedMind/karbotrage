# Karbot Rage! - Automated Trading System

An automated trading system for prediction markets, built with Python and async/await for high performance.

## Features

- **Async Execution Engine**: Fully asynchronous execution using Python's asyncio
- **Multi-Source Market Data**: Supports data from multiple prediction market platforms
- **Strategy Management**: Modular strategy framework for different trading approaches
- **Risk Management**: Built-in risk tolerance and position sizing controls
- **Paper Trading Mode**: Test strategies without real money
- **Extensible Architecture**: Easy to add new data sources and trading strategies

## Architecture

```
karbotrage/
├── main.py                 # Main entry point
├── config.yaml             # Configuration file
├── core/
│   ├── config.py           # Configuration handling
│   └── __init__.py
├── execution/
│   ├── engine.py           # Async execution engine
│   └── __init__.py
├── data/
│   ├── market_data.py      # Market data handling
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── polymarket.py   # Polymarket data source
│   │   └── kalshi.py       # Kalshi data source
│   └── __init__.py
├── intelligence/
│   ├── analyzer.py         # Market analysis
│   └── __init__.py
├── strategies/
│   ├── strategy_manager.py # Strategy management
│   └── __init__.py
├── trading/
│   ├── trader.py           # Trade execution
│   └── __init__.py
├── monitoring/
│   ├── logger.py           # Logging setup
│   └── __init__.py
└── requirements.txt        # Dependencies
```

## Installation

1. Clone the repository
2. Install dependencies:
   ```bash
   pip3 install --break-system-packages -r requirements.txt
   ```

## Usage

```bash
# Run with default configuration
python3 main.py

# Run with custom configuration
python3 main.py --config /path/to/config.yaml

# Run in live trading mode
python3 main.py --mode live

# Enable debug mode
python3 main.py --debug
```

## Configuration

The system uses a YAML configuration file (`config.yaml`) with the following structure:

```yaml
system:
  debug: true
  log_level: INFO
  log_file: karbotrage.log

trading:
  mode: paper
  max_positions: 10
  position_size: 1000
  risk_tolerance: 0.02

api:
  polymarket:
    enabled: true
    api_key: "your-polymarket-api-key"
    base_url: "https://api.polymarket.com"
  kalshi:
    enabled: false
    api_key: "your-kalshi-api-key"
    base_url: "https://api.kalshi.com"

strategy:
  enabled: true
  name: "basic_strategy"
  parameters:
    threshold: 0.1
    max_loss: 0.05
    max_gain: 0.2

monitoring:
  enabled: true
  metrics:
    - "market_data"
    - "trades"
    - "portfolio"
```

## Components

### Execution Engine
The core execution engine (`execution/engine.py`) orchestrates the entire system with async/await, managing the flow from data fetching to strategy execution to trade execution.

### Data Sources
- **Polymarket**: Fetches data from Polymarket API
- **Kalshi**: Fetches data from Kalshi API

### Intelligence
- **Market Analyzer**: Analyzes market data and generates trading signals

### Strategies
- **Strategy Manager**: Manages and executes trading strategies

### Trading
- **Trader**: Executes trades based on strategy signals

## License

MIT License