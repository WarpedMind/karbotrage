import pytest
from pathlib import Path
from karbot.core.config import KarbotConfig, Secrets

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
    assert config.compliance is not None
    assert config.alerts is not None

def test_secrets_creation():
    """Test that we can create a secrets object"""
    # This test is mostly to ensure the class structure works
    # In a real system, we'd test with actual environment variables
    secrets = Secrets()
    assert secrets is not None