"""
karbot/core/config.py
─────────────────────
Typed configuration for the Karbot Rage! agent layer.

All risk limits below are HARD CONSTRAINTS — not defaults, not preferences.
They are coded as module-level constants and enforced in KarbotConfig.
The Risk Gate reads from this config and rejects anything that violates them.

Phase rules:
  Phase 1 = Kalshi only. polymarket_ws_enabled MUST be False.
  Phase 2 = Cross-platform, ONLY after original principal is recovered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


# ── Hard risk limits (non-negotiable) ─────────────────────────────────────────

ABSOLUTE_MAX_PER_TRADE_PCT   = 5.0    # Max 5% capital per trade
ABSOLUTE_MAX_LOCKED_PCT      = 40.0   # Max 40% capital locked at any time
ABSOLUTE_MAX_DAILY_LOSS_PCT  = 3.0    # Max 3% daily loss — bot halts, requires manual restart
ABSOLUTE_MAX_WEEKLY_LOSS_PCT = 7.0    # Max 7% weekly loss
ABSOLUTE_MAX_DAILY_TRADES    = 50     # Max 50 trades/day
ABSOLUTE_LEG_TIMEOUT_SEC     = 10.0   # Max 10s leg fill timeout
ABSOLUTE_MAX_SLIPPAGE_PCT    = 0.3    # Max 0.3% slippage
POLYMARKET_MAX_CAPITAL_PCT   = 30.0   # Phase 2 only: Polymarket cap at 30%


# ── Sub-configs ───────────────────────────────────────────────────────────────

@dataclass
class SystemConfig:
    paper_mode: bool = True   # Must be True until live trading is explicitly enabled
    debug: bool = False
    log_level: str = "INFO"


@dataclass
class DataFeedsConfig:
    kalshi_ws_enabled: bool = True
    polymarket_ws_enabled: bool = False   # Phase 1: always False


@dataclass
class CapitalConfig:
    total_deployed_usd: float = 0.0
    phase: int = 1   # 1 = Kalshi only; 2 = cross-platform (after principal recovered)


@dataclass
class RiskConfig:
    max_capital_per_trade_pct: float  = ABSOLUTE_MAX_PER_TRADE_PCT
    max_capital_locked_pct: float     = ABSOLUTE_MAX_LOCKED_PCT
    max_daily_loss_pct: float         = ABSOLUTE_MAX_DAILY_LOSS_PCT
    max_weekly_loss_pct: float        = ABSOLUTE_MAX_WEEKLY_LOSS_PCT
    max_daily_trades: int             = ABSOLUTE_MAX_DAILY_TRADES
    max_slippage_pct: float           = ABSOLUTE_MAX_SLIPPAGE_PCT
    polymarket_max_capital_pct: float = POLYMARKET_MAX_CAPITAL_PCT
    leg_timeout_sec: float            = ABSOLUTE_LEG_TIMEOUT_SEC
    kelly_fraction: float             = 0.15   # Fractional Kelly for position sizing
    gas_fee_ceiling_pct: float        = 30.0   # Gas must be <30% of expected profit
    pause_window_minutes: int         = 15     # Trading pause window around announcements

    def __post_init__(self):
        # Enforce hard limits — operators cannot configure above these
        if self.max_capital_per_trade_pct > ABSOLUTE_MAX_PER_TRADE_PCT:
            raise ValueError(
                f"max_capital_per_trade_pct {self.max_capital_per_trade_pct} "
                f"exceeds hard limit {ABSOLUTE_MAX_PER_TRADE_PCT}"
            )
        if self.max_capital_locked_pct > ABSOLUTE_MAX_LOCKED_PCT:
            raise ValueError(
                f"max_capital_locked_pct {self.max_capital_locked_pct} "
                f"exceeds hard limit {ABSOLUTE_MAX_LOCKED_PCT}"
            )
        if self.max_daily_loss_pct > ABSOLUTE_MAX_DAILY_LOSS_PCT:
            raise ValueError(
                f"max_daily_loss_pct {self.max_daily_loss_pct} "
                f"exceeds hard limit {ABSOLUTE_MAX_DAILY_LOSS_PCT}"
            )
        if self.max_weekly_loss_pct > ABSOLUTE_MAX_WEEKLY_LOSS_PCT:
            raise ValueError(
                f"max_weekly_loss_pct {self.max_weekly_loss_pct} "
                f"exceeds hard limit {ABSOLUTE_MAX_WEEKLY_LOSS_PCT}"
            )
        if self.max_daily_trades > ABSOLUTE_MAX_DAILY_TRADES:
            raise ValueError(
                f"max_daily_trades {self.max_daily_trades} "
                f"exceeds hard limit {ABSOLUTE_MAX_DAILY_TRADES}"
            )
        if self.max_slippage_pct > ABSOLUTE_MAX_SLIPPAGE_PCT:
            raise ValueError(
                f"max_slippage_pct {self.max_slippage_pct} "
                f"exceeds hard limit {ABSOLUTE_MAX_SLIPPAGE_PCT}"
            )


@dataclass
class StrategiesConfig:
    # S1: Single-market YES+NO rebalancing (Kalshi only, Phase 1 safe)
    s1_rebalancing_enabled: bool  = True
    s1_min_net_profit_pct: float  = 0.5

    # S2: Cross-platform arbitrage (Phase 2 only)
    s2_cross_platform_enabled: bool = False   # Phase 1: always False
    s2_min_net_profit_pct: float    = 1.0

    # S3: Logical/semantic arbitrage (LLM-detected)
    s3_logical_arb_enabled: bool = True
    s3_min_edge_pct: float       = 1.0
    s3_min_confidence: str       = "HIGH"   # HIGH | MEDIUM | LOW

    # S4: Settlement arbitrage (news-triggered)
    s4_settlement_arb_enabled: bool = True


@dataclass
class IntelligenceConfig:
    llm_model_analysis: str     = "claude-sonnet-4-6"
    llm_cache_ttl_minutes: int  = 10
    llm_daily_spend_limit: float = 5.0   # USD — halt LLM calls above this
    llm_batch_size: int         = 20     # Markets per LLM batch call


@dataclass
class TelegramConfig:
    enabled: bool = False
    notify_on_trade: bool = True        # Tier 2: live always True; paper follows this flag
    notify_on_rejection: bool = False   # Tier 2: off by default to reduce noise
    permission_timeout_seconds: int = 300
    # bot_token and chat_id come from environment variables only:
    # TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
    # Never stored in config.yaml


@dataclass
class RegulatoryIntelligenceConfig:
    enabled: bool = True
    regulatory_ai_calls_per_cycle: int = 10
    regulatory_ai_daily_cap: int = 50
    regulatory_circuit_breaker_calls: int = 20
    regulatory_circuit_breaker_window_minutes: int = 10
    regulatory_cost_per_call_usd: float = 0.003
    regulatory_clear_phrase: str = "CLEAR REGULATORY HOLD"
    regulatory_keywords: List[str] = field(default_factory=lambda: [
        "prediction market", "event contract", "CFTC", "enforcement",
        "insider trading", "manipulation", "wash trading", "DEATH BETS",
        "self-reporting", "declination", "Van Dyke",
    ])
    weekly_sweep_day: str = "sunday"
    weekly_sweep_hour_utc: int = 6
    poll_interval_hours: int = 6


# ── Top-level config ──────────────────────────────────────────────────────────

@dataclass
class KarbotConfig:
    system:                   SystemConfig                  = field(default_factory=SystemConfig)
    data_feeds:               DataFeedsConfig               = field(default_factory=DataFeedsConfig)
    capital:                  CapitalConfig                 = field(default_factory=CapitalConfig)
    risk:                     RiskConfig                    = field(default_factory=RiskConfig)
    strategies:               StrategiesConfig              = field(default_factory=StrategiesConfig)
    intelligence:             IntelligenceConfig            = field(default_factory=IntelligenceConfig)
    telegram:                 TelegramConfig                = field(default_factory=TelegramConfig)
    regulatory_intelligence:  RegulatoryIntelligenceConfig = field(default_factory=RegulatoryIntelligenceConfig)

    # Regulatory compliance fields (set manually in config.yaml after reading guidance)
    regulatory_halt: bool = False
    regulatory_halt_reason: str = ""
    regulatory_check_interval_hours: int = 6

    def __post_init__(self):
        # Phase 1 invariant: if phase=1, Polymarket must be disabled
        if self.capital.phase == 1 and self.data_feeds.polymarket_ws_enabled:
            raise ValueError(
                "Phase 1 invariant violated: polymarket_ws_enabled must be False "
                "until original principal is recovered and phase is set to 2."
            )
        # S2 must be disabled in Phase 1
        if self.capital.phase == 1 and self.strategies.s2_cross_platform_enabled:
            raise ValueError(
                "Phase 1 invariant violated: s2_cross_platform_enabled must be False "
                "in Phase 1."
            )

    # ── Convenience properties (used by karbot_runner.py) ─────────────────────

    @property
    def phase(self) -> int:
        return self.capital.phase

    @property
    def paper_mode(self) -> bool:
        return self.system.paper_mode

    # ── YAML loader ───────────────────────────────────────────────────────────

    @classmethod
    def from_yaml(cls, path: str) -> "KarbotConfig":
        """Load config from a YAML file, falling back to safe defaults."""
        import yaml

        yaml_path = Path(path)
        raw: Dict[str, Any] = {}
        if yaml_path.exists():
            with open(yaml_path) as f:
                raw = yaml.safe_load(f) or {}

        # Map legacy config.yaml fields to KarbotConfig structure.
        # Unknown keys are silently ignored — defaults are Phase 1 safe.
        trading = raw.get("trading", {})
        paper = trading.get("mode", "paper") == "paper"

        system = SystemConfig(
            paper_mode=paper,
            debug=raw.get("system", {}).get("debug", False),
            log_level=raw.get("system", {}).get("log_level", "INFO"),
        )

        tg_raw = raw.get("telegram", {})
        telegram = TelegramConfig(
            enabled=tg_raw.get("enabled", False),
            notify_on_trade=tg_raw.get("notify_on_trade", True),
            notify_on_rejection=tg_raw.get("notify_on_rejection", False),
            permission_timeout_seconds=tg_raw.get("permission_timeout_seconds", 300),
        )

        ri_raw = raw.get("regulatory_intelligence", {})
        default_ri = RegulatoryIntelligenceConfig()
        regulatory_intelligence = RegulatoryIntelligenceConfig(
            enabled=ri_raw.get("enabled", default_ri.enabled),
            regulatory_ai_calls_per_cycle=ri_raw.get(
                "regulatory_ai_calls_per_cycle", default_ri.regulatory_ai_calls_per_cycle
            ),
            regulatory_ai_daily_cap=ri_raw.get(
                "regulatory_ai_daily_cap", default_ri.regulatory_ai_daily_cap
            ),
            regulatory_circuit_breaker_calls=ri_raw.get(
                "regulatory_circuit_breaker_calls", default_ri.regulatory_circuit_breaker_calls
            ),
            regulatory_circuit_breaker_window_minutes=ri_raw.get(
                "regulatory_circuit_breaker_window_minutes",
                default_ri.regulatory_circuit_breaker_window_minutes,
            ),
            regulatory_cost_per_call_usd=ri_raw.get(
                "regulatory_cost_per_call_usd", default_ri.regulatory_cost_per_call_usd
            ),
            regulatory_clear_phrase=ri_raw.get(
                "regulatory_clear_phrase", default_ri.regulatory_clear_phrase
            ),
            regulatory_keywords=ri_raw.get(
                "regulatory_keywords", default_ri.regulatory_keywords
            ),
            weekly_sweep_day=ri_raw.get("weekly_sweep_day", default_ri.weekly_sweep_day),
            weekly_sweep_hour_utc=ri_raw.get(
                "weekly_sweep_hour_utc", default_ri.weekly_sweep_hour_utc
            ),
            poll_interval_hours=ri_raw.get(
                "poll_interval_hours", default_ri.poll_interval_hours
            ),
        )

        return cls(
            system=system,
            telegram=telegram,
            regulatory_intelligence=regulatory_intelligence,
            regulatory_halt=raw.get("regulatory_halt", False),
            regulatory_halt_reason=raw.get("regulatory_halt_reason", ""),
            regulatory_check_interval_hours=raw.get(
                "regulatory_check_interval_hours", 6
            ),
        )
