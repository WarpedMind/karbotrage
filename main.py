#!/usr/bin/env python3
"""
Karbot Rage! - Automated Trading System

Main entry point for the automated trading system.
"""

import sys
import os
import logging
import argparse
from typing import Dict, Any

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import load_config
from execution.engine import ExecutionEngine
from monitoring.logger import setup_logger

def main():
    """
    Main function to run the Karbot Rage! system
    """
    # Setup logging
    logger = setup_logger('karbotrage')
    logger.info("Starting Karbot Rage! - Automated Trading System")

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Karbot Rage! - Automated Trading System')
    parser.add_argument('--config', '-c', help='Path to configuration file')
    parser.add_argument('--mode', '-m', choices=['paper', 'live', 'backtest'],
                        help='Trading mode')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')

    args = parser.parse_args()

    try:
        # Load configuration
        config = load_config(args.config)

        # Override mode if specified
        if args.mode:
            config['trading']['mode'] = args.mode

        # Set debug level
        if args.debug:
            config['system']['debug'] = True
            logger.setLevel(logging.DEBUG)
            logger.debug("Debug mode enabled")

        logger.info(f"Configuration loaded successfully")
        logger.info(f"Trading mode: {config['trading']['mode']}")

        # Initialize and start execution engine
        engine = ExecutionEngine(config)
        engine.start()

        logger.info("Karbot Rage! completed successfully")

    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"Error in main execution: {str(e)}")
        raise
    finally:
        # Cleanup
        try:
            engine.stop()
        except:
            pass

if __name__ == "__main__":
    main()