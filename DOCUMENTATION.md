# Karbot Rage! - Automated Trading System

## System Overview

Karbot Rage! is a modular, extensible automated trading system designed for decentralized prediction markets. It provides a framework for analyzing market data, executing trading strategies, and managing trades across multiple prediction market platforms.

## Architecture

The system follows a clean architecture with separation of concerns:

### Core Components

1. **core/** - Core system components (configuration, initialization)
2. **execution/** - Main execution engine that orchestrates all components
3. **data/** - Data handling and market data fetching
4. **intelligence/** - Analysis and intelligence components
5. **strategies/** - Strategy management and execution
6. **trading/** - Trade execution and management
7. **monitoring/** - Logging and monitoring

### Main Entry Point

- **main.py** - Main application entry point with argument parsing and system initialization

## Module Descriptions

### core/
- **config.py** - Configuration handling with default values and merging logic

### execution/
- **engine.py** - Main execution engine that coordinates all components

### data/
- **market_data.py** - Market data fetching and handling

### intelligence/
- **analyzer.py** - Market analysis and signal generation

### strategies/
- **strategy_manager.py** - Strategy execution and trade generation

### trading/
- **trader.py** - Trade execution and management

### monitoring/
- **logger.py** - Logging configuration and setup

## Key Features

1. **Modular Design** - Each component can be extended or replaced independently
2. **Multi-Mode Support** - Paper trading, live trading, and backtesting modes
3. **Configurable Strategies** - Easily modify strategy parameters
4. **Comprehensive Logging** - Detailed logging for monitoring and debugging
5. **Error Handling** - Graceful degradation and error recovery
6. **Asynchronous Operations** - Efficient data fetching and processing

## Configuration

The system uses a JSON configuration file with the following structure:

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

## Usage

### Running the System

```bash
python main.py
```

### Command Line Options

- `--config, -c`: Path to configuration file
- `--mode, -m`: Trading mode (paper, live, backtest)
- `--debug`: Enable debug mode

### Example

```bash
python main.py --mode paper --debug
```

## Extending the System

### Adding New Strategies

1. Create a new strategy class in the strategies module
2. Implement the strategy logic in the class
3. Register the strategy in the configuration

### Adding New Data Sources

1. Create a new data source class in the data module
2. Implement data fetching logic
3. Register the data source in the MarketData class

### Adding New Trading Platforms

1. Create a new API client class
2. Implement trading logic
3. Register the platform in the trader module

## Testing

The system includes basic unit tests in the tests/ directory. Run tests with:

```bash
python -m pytest tests/
```

## License

This project is licensed under the MIT License.

## Contributing

Contributions are welcome! Please submit a pull request.