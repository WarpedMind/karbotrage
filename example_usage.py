#!/usr/bin/env python3
"""
Example usage of Karbot Rage! - Automated Trading System
"""

import sys
import os
import json

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import load_config
from execution.engine import ExecutionEngine

def main():
    """
    Example of how to use the Karbot Rage! system
    """
    print("Karbot Rage! - Automated Trading System")
    print("=" * 50)

    # Load configuration
    print("Loading configuration...")
    config = load_config()
    print("Configuration loaded successfully")

    # Display some configuration details
    print(f"Trading mode: {config['trading']['mode']}")
    print(f"Risk tolerance: {config['trading']['risk_tolerance']}")

    # Initialize the engine
    print("\nInitializing execution engine...")
    engine = ExecutionEngine(config)
    print("Engine initialized successfully")

    # Run a single execution cycle
    print("\nRunning execution cycle...")
    try:
        engine.start()
        print("Execution cycle completed successfully")
    except Exception as e:
        print(f"Error during execution: {e}")
    finally:
        engine.stop()
        print("Engine stopped")

if __name__ == "__main__":
    main()