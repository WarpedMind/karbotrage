#!/usr/bin/env python3
"""
Main entry point for Karbot Rage! - Automated Trading System
"""

import sys
import os
import logging
from pathlib import Path

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.config import load_config
from execution.engine import ExecutionEngine
from monitoring.logger import setup_logger

def main():
    """Main entry point for the Karbot Rage! system"""

    # Setup logging
    logger = setup_logger(__name__)
    logger.info("Starting Karbot Rage! system")

    try:
        # Load configuration
        config = load_config()
        logger.info("Configuration loaded successfully")

        # Initialize execution engine
        engine = ExecutionEngine(config)
        logger.info("Execution engine initialized")

        # Start the system
        engine.start()
        logger.info("Karbot Rage! system started successfully")

    except Exception as e:
        logger.error(f"Failed to start Karbot Rage! system: {str(e)}")
        sys.exit(1)

def main_paper():
    """Main entry point for paper trading mode"""
    logger = setup_logger(__name__)
    logger.info("Starting Karbot Rage! paper trading mode")

    try:
        config = load_config()
        config['trading']['mode'] = 'paper'
        engine = ExecutionEngine(config)
        engine.start()
        logger.info("Karbot Rage! paper trading mode started successfully")

    except Exception as e:
        logger.error(f"Failed to start Karbot Rage! paper trading mode: {str(e)}")
        sys.exit(1)

def main_backtest():
    """Main entry point for backtesting mode"""
    logger = setup_logger(__name__)
    logger.info("Starting Karbot Rage! backtesting mode")

    try:
        config = load_config()
        config['trading']['mode'] = 'backtest'
        engine = ExecutionEngine(config)
        engine.start()
        logger.info("Karbot Rage! backtesting mode started successfully")

    except Exception as e:
        logger.error(f"Failed to start Karbot Rage! backtesting mode: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "paper":
            main_paper()
        elif sys.argv[1] == "backtest":
            main_backtest()
        else:
            main()
    else:
        main()