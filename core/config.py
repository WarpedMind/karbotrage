"""
Configuration handling for Karbot Rage! - Automated Trading System
"""

import json
import os
from typing import Dict, Any

def load_config(config_path: str = None) -> Dict[str, Any]:
    """
    Load configuration from file or use default

    Args:
        config_path: Path to configuration file

    Returns:
        Configuration dictionary
    """
    # Default configuration
    default_config = {
        "system": {
            "debug": False,
            "log_level": "INFO"
        },
        "trading": {
            "mode": "paper",
            "max_position_size": 1000,
            "risk_tolerance": 0.05
        },
        "api": {
            "polymarket": {
                "enabled": True,
                "api_key": "",
                "base_url": "https://api.polymarket.com"
            },
            "kalshi": {
                "enabled": False,
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
                "enabled": True,
                "min_profit": 0.01,
                "max_slippage": 0.02
            },
            "price_trend_following": {
                "enabled": True,
                "lookback_period": 24,
                "threshold": 0.05
            }
        }
    }

    # If config path is provided, try to load it
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                user_config = json.load(f)

            # Merge default and user config
            merged_config = _merge_configs(default_config, user_config)
            return merged_config
        except Exception as e:
            print(f"Warning: Could not load config file {config_path}: {e}")
            print("Using default configuration")

    return default_config

def _merge_configs(default: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively merge two configuration dictionaries

    Args:
        default: Default configuration
        user: User configuration

    Returns:
        Merged configuration
    """
    merged = default.copy()

    for key, value in user.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _merge_configs(merged[key], value)
        else:
            merged[key] = value

    return merged

def validate_config(config: Dict[str, Any]) -> bool:
    """
    Validate configuration

    Args:
        config: Configuration dictionary

    Returns:
        True if valid, False otherwise
    """
    # Basic validation
    if not isinstance(config, dict):
        return False

    # Check required sections
    required_sections = ['trading', 'api', 'data', 'strategies']
    for section in required_sections:
        if section not in config:
            print(f"Missing required section: {section}")
            return False

    # Check trading mode
    trading_mode = config.get('trading', {}).get('mode')
    if trading_mode and trading_mode not in ['paper', 'live', 'backtest']:
        print(f"Invalid trading mode: {trading_mode}")
        return False

    return True