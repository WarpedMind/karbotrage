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
from karbot.core.config import KarbotConfig


@pytest.mark.asyncio
async def test_config_resolved_log_fires_once_with_accurate_values(caplog):
    """Exactly one config_resolved line, reporting the ACTUAL resolved config.

    Expected values are derived from a freshly loaded ``KarbotConfig`` rather
    than hardcoded, and that is the point of the test. The original version
    asserted ``telegram_enabled=False`` with the comment "no config.yaml present
    in the test environment" — which made it a test of whether a production
    config file happened to be absent. It passed on the development machine and
    **failed on the VPS**, where a real ``config.yaml`` exists with Telegram
    enabled (created in Session 24, precisely so that Telegram alerting would
    stop being a silent no-op).

    That is backwards twice over: the assertion could never catch a genuine
    regression on the box it was meant to protect, and it went red for a reason
    that was correct behaviour. What this line is actually for is confirming
    that what the log *says* matches what the runner *resolved* — so that is
    what gets asserted.
    """
    fixture_path = str(PROJECT_ROOT / "tests" / "fixtures" / "paper_test_prices.json")
    args = argparse.Namespace(mock_prices=fixture_path, exit_after_test=True, mode="paper")

    with caplog.at_level(logging.INFO, logger="karbot_runner"):
        await karbot_runner.run(args)

    config_resolved_lines = [
        r.message for r in caplog.records if "config_resolved" in r.message
    ]
    assert len(config_resolved_lines) == 1
    line = config_resolved_lines[0]

    # The same config the runner loads, from the same path.
    config = KarbotConfig.from_yaml("config.yaml")
    for field, value in (
        ("telegram_enabled", config.telegram.enabled),
        ("kalshi_ws_enabled", config.data_feeds.kalshi_ws_enabled),
        ("polymarket_ws_enabled", config.data_feeds.polymarket_ws_enabled),
        ("regulatory_intelligence_enabled", config.regulatory_intelligence.enabled),
        ("paper_mode", config.paper_mode),
        ("phase", config.phase),
    ):
        assert f"{field}={value}" in line, (
            f"config_resolved reports a different {field} than the loaded config"
        )

    # Phase 1 invariants, which are structural rather than environmental and so
    # ARE safe to assert absolutely — KarbotConfig.__init__ raises if violated.
    assert "phase=1" in line
    assert "polymarket_ws_enabled=False" in line
