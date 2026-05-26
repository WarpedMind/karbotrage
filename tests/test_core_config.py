import pytest
from pathlib import Path
from karbot.core.config import KarbotConfig
# Secrets was removed from karbot/core/config.py in a prior session.
# API credentials are no longer managed as a config dataclass.

def test_config_creation():
    """Test that we can create a config object"""
    # Test default config creation
    config = KarbotConfig()
    assert config is not None
    assert config.system is not None
    assert config.capital is not None
    assert config.risk is not None
    assert config.strategies is not None
    assert config.intelligence is not None
    assert config.data_feeds is not None
    # config.compliance and config.alerts were removed alongside Secrets in a
    # prior session; they are not present in the current KarbotConfig dataclass.

# test_secrets_creation was removed: Secrets class was deliberately removed from
# karbot/core/config.py in a prior session. No replacement exists — API credentials
# are not managed as a config dataclass in the current architecture.