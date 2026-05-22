# Karbot Rage! - Automated Trading System

## What this is
Karbot Rage! is a multi-agent automated trading system designed for decentralized prediction markets. It provides a modular framework with specialized agents for market monitoring, analysis, strategy execution, and compliance.

## Stack
- Python 3.8+
- Modular architecture with core, execution, data, intelligence, strategies, trading, and monitoring components
- Run with: python main.py

## Architecture
- main.py: Main entry point and system initialization
- core/config.py: Configuration management
- execution/engine.py: Main execution engine coordinating all components
- data/market_data.py: Market data handling
- intelligence/analyzer.py: Market analysis and signal generation
- strategies/strategy_manager.py: Strategy execution
- trading/trader.py: Trade execution
- monitoring/logger.py: Logging system

## Current status
- Complete modular framework with all components implemented
- Configuration system with defaults
- Core architecture working
- Documentation in place
- Tests exist but not fully implemented

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