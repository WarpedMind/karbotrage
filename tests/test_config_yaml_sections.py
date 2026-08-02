"""
tests/test_config_yaml_sections.py

Covers the 2026-08-02 (Session 30) fix wiring `data_feeds:`, `capital:`,
`risk:`, `strategies:` and `intelligence:` into KarbotConfig.from_yaml().

Before this, from_yaml() parsed only `system:`, `telegram:` and
`regulatory_intelligence:` — every other section in config.yaml was silently
ignored. Consequences found live: capital was permanently the $10k paper
default on the VPS regardless of config, no strategy threshold was tunable
without a code change, and config.yaml.example's `api.*` keys were decorative
(Session 24 discovered the same class of bug the expensive way, when
telegram.enabled defaulted False through three live deploys).

Also covers the unknown-key warning, which exists so a mistyped or obsolete
key can never again look configured while doing nothing.
"""

import logging
import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from karbot.core.config import KarbotConfig


def _write(tmp_path: Path, body: str) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(body))
    return str(p)


class TestPreviouslyIgnoredSections:
    def test_capital_section_is_parsed(self, tmp_path):
        cfg = KarbotConfig.from_yaml(_write(tmp_path, """
            capital:
              total_deployed_usd: 2500.0
              phase: 1
        """))
        assert cfg.capital.total_deployed_usd == 2500.0

    def test_strategies_section_is_parsed(self, tmp_path):
        cfg = KarbotConfig.from_yaml(_write(tmp_path, """
            strategies:
              s1_min_net_profit_pct: 2.5
              s1_canary_mode: false
        """))
        assert cfg.strategies.s1_min_net_profit_pct == 2.5
        assert cfg.strategies.s1_canary_mode is False

    def test_risk_section_is_parsed(self, tmp_path):
        cfg = KarbotConfig.from_yaml(_write(tmp_path, """
            risk:
              max_capital_per_trade_pct: 2.0
              kelly_fraction: 0.05
        """))
        assert cfg.risk.max_capital_per_trade_pct == 2.0
        assert cfg.risk.kelly_fraction == 0.05

    def test_data_feeds_section_is_parsed(self, tmp_path):
        cfg = KarbotConfig.from_yaml(_write(tmp_path, """
            data_feeds:
              kalshi_ws_enabled: false
        """))
        assert cfg.data_feeds.kalshi_ws_enabled is False

    def test_absent_sections_still_fall_back_to_safe_defaults(self, tmp_path):
        cfg = KarbotConfig.from_yaml(_write(tmp_path, "system:\n  debug: true\n"))
        assert cfg.strategies.s1_canary_mode is True          # safe default
        assert cfg.data_feeds.polymarket_ws_enabled is False  # Phase 1 invariant
        assert cfg.capital.phase == 1


class TestGuardrailsStillBind:
    def test_yaml_cannot_raise_a_risk_limit_above_the_hard_ceiling(self, tmp_path):
        """RiskConfig.__post_init__ enforces ABSOLUTE_* limits. Making these
        sections configurable must not become a way to configure past them."""
        with pytest.raises(ValueError, match="exceeds hard limit"):
            KarbotConfig.from_yaml(_write(tmp_path, """
                risk:
                  max_capital_per_trade_pct: 50.0
            """))

    def test_yaml_cannot_violate_the_phase_1_invariant(self, tmp_path):
        with pytest.raises(ValueError, match="Phase 1 invariant"):
            KarbotConfig.from_yaml(_write(tmp_path, """
                capital:
                  phase: 1
                strategies:
                  s2_cross_platform_enabled: true
            """))


class TestUnknownKeyWarning:
    def test_unknown_key_warns_instead_of_silently_doing_nothing(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING):
            KarbotConfig.from_yaml(_write(tmp_path, """
                strategies:
                  s1_min_net_profit_pct: 1.0
                  s1_min_net_profit_percent: 9.9
            """))
        assert "config_unknown_keys" in caplog.text
        assert "s1_min_net_profit_percent" in caplog.text

    def test_known_keys_do_not_warn(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING):
            KarbotConfig.from_yaml(_write(tmp_path, """
                strategies:
                  s1_min_net_profit_pct: 1.0
            """))
        assert "config_unknown_keys" not in caplog.text
