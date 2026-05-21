#!/usr/bin/env python3
"""
Karbot Rage! - Main entry point
"""

import sys
import os
from pathlib import Path

# Add the project root to Python path
sys.path.insert(0, str(Path(__file__).parent))

from karbot.core.config import KarbotConfig, Secrets

def main():
    print("Karbot Rage! Initializing...")

    try:
        # Load secrets from environment
        secrets = Secrets.load()
        print("✓ Secrets loaded successfully")

        # Load configuration from file
        config_path = Path("config/config.yaml")
        config = KarbotConfig.from_file(config_path)
        print("✓ Configuration loaded successfully")

        print("Karbot Rage! System initialized successfully!")
        print(f"Paper mode: {config.system.paper_mode}")
        print(f"Log level: {config.system.log_level}")

    except Exception as e:
        print(f"Error initializing Karbot Rage!: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()