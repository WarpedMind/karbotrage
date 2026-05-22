# Karbot Rage! - Automated Trading System

An automated trading system for decentralized prediction markets.

## Overview

Karbot Rage! is a modular, extensible automated trading system designed for decentralized prediction markets. It provides a framework for analyzing market data, executing trading strategies, and managing trades across multiple prediction market platforms.

## Features

- Modular architecture for easy extension
- Support for multiple trading modes (paper, live, backtest)
- Configurable strategies with parameters
- Comprehensive logging and monitoring
- Asynchronous data fetching for performance
- Error handling and graceful degradation

## Architecture

The system follows a clean architecture with separation of concerns:

1. **Core Components**
   - `core/` - Core system components (configuration, initialization)
   - `execution/` - Main execution engine that orchestrates all components
   - `data/` - Data handling and market data fetching
   - `intelligence/` - Analysis and intelligence components
   - `strategies/` - Strategy management and execution
   - `trading/` - Trade execution and management
   - `monitoring/` - Logging and monitoring

2. **Main Entry Point**
   - `main.py` - Main application entry point with argument parsing and system initialization

3. **Configuration**
   - `config.json` - Default configuration file
   - `requirements.txt` - Python dependencies

## Installation

1. Clone the repository
2. Create a virtual environment:
   ```bash
   python3 -m venv karbotrage_env
   source karbotrage_env/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

Run the system with:
```bash
python main.py
```

### Command Line Options

- `--config, -c`: Path to configuration file
- `--mode, -m`: Trading mode (paper, live, backtest)
- `--debug`: Enable debug mode

## Configuration

The system uses a JSON configuration file (`config.json`) with the following structure:

```json
{
  "system": {
    "debug": false,
    "log_level": "INFO"
  },
  "trading": {
    "mode": "paper",
    "max_position_size": 1000,
    "risk_tolerance": 0.05
  },
  "api": {
    "polymarket": {
      "enabled": true,
      "api_key": "",
      "base_url": "https://api.polymarket.com"
    },
    "kalshi": {
      "enabled": false,
      "api_key": "",
      "base_url": "https://api.kalshi.com"
    }
  },
  "data": {
    "cache_duration": 3600,
    "max_retries": 3,
    "timeout": 30
  },
  "strategies": {
    "simple_arbitrage": {
      "enabled": true,
      "min_profit": 0.01,
      "max_slippage": 0.02
    },
    "price_trend_following": {
      "enabled": true,
      "lookback_period": 24,
      "threshold": 0.05
    }
  }
}
```

## Testing

Run tests with:
```bash
python -m pytest tests/
```

## License

This project is licensed under the MIT License.

## Contributing

Contributions are welcome! Please submit a pull request.