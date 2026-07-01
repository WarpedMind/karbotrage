"""
tests/test_config_resolved_log.py — karbot_runner.py's config_resolved startup log

Covers the Session 24 fix: Telegram alerting (feed-down, restart-exhaustion)
went undetected across three live deploys because telegram.enabled defaults
to False and no config.yaml existed on the VPS to override it -- a silent
no-op with no error. karbot_runner.py now logs the resolved state of every
subsystem enable/disable flag once at startup so this is visible in VPS
logs without grepping source code.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import karbot_runner


@pytest.mark.asyncio
async def test_config_resolved_log_fires_once_with_accurate_values(caplog):
    """Running the mock-prices/exit-after-test path logs exactly one
    config_resolved line, with values matching the resolved KarbotConfig."""
    fixture_path = str(PROJECT_ROOT / "tests" / "fixtures" / "paper_test_prices.json")
    args = argparse.Namespace(mock_prices=fixture_path, exit_after_test=True, mode="paper")

    with caplog.at_level(logging.INFO, logger="karbot_runner"):
        await karbot_runner.run(args)

    config_resolved_lines = [
        r.message for r in caplog.records if "config_resolved" in r.message
    ]
    assert len(config_resolved_lines) == 1

    line = config_resolved_lines[0]
    # No config.yaml present in the test environment -> KarbotConfig defaults.
    assert "telegram_enabled=False" in line
    assert "kalshi_ws_enabled=True" in line
    assert "polymarket_ws_enabled=False" in line
    assert "regulatory_intelligence_enabled=True" in line
    assert "paper_mode=True" in line
    assert "phase=1" in line
