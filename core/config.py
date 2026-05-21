"""
Karbot Rage! - Configuration System
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from pydantic import BaseModel, Field, validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Set up logging
logger = logging.getLogger(__name__)

class SystemConfig(BaseModel):
    """System-wide configuration"""
    paper_mode: bool = Field(default=True, description="Set to false for live trading")
    log_level: str = Field(default="INFO", description="Logging level")
    vps_location: str = Field(default="new_york", description="Location for VPS deployment")
    dashboard_port: int = Field(default=5000, description="Web dashboard port")
    health_port: int = Field(default=8080, description="Health check port")
    telegram_enabled: bool = Field(default=False, description="Enable Telegram alerts")
    version: str = Field(default="1.0.0", description="Current version")

class CapitalConfig(BaseModel):
    """Capital allocation configuration"""
    total_deployed_usd: float = Field(default=0.0, description="Total capital deployed")
    phase: int = Field(default=1, description="Trading phase (1=Kalshi only, 2=+Polymarket, 3=+others)")
    kalshi_allocation_pct: float = Field(default=100.0, description="Allocation to Kalshi")
    polymarket_allocation_pct: float = Field(default=0.0, description="Allocation to Polymarket")
    reserve_pct: float = Field(default=30.0, description="Keep 30% liquid at all times")

class RiskConfig(BaseModel):
    """Risk management configuration"""
    max_capital_per_trade_pct: float = Field(default=5.0, description="Max 5% of capital per trade")
    max_capital_locked_pct: float = Field(default=40.0, description="Max 40% of capital locked")
    polymarket_max_capital_pct: float = Field(default=30.0, description="Max 30% on gray-area platform")
    max_daily_loss_pct: float = Field(default=3.0, description="Max 3% daily loss")
    max_weekly_loss_pct: float = Field(default=7.0, description="Max 7% weekly loss")
    max_daily_trades: int = Field(default=50, description="Max 50 trades per day")
    leg_timeout_seconds: int = Field(default=10, description="Leg timeout in seconds")
    max_slippage_pct: float = Field(default=0.3, description="Max 0.3% slippage")
    pause_window_minutes: int = Field(default=5, description="Pause window after announcements")
    gas_fee_ceiling_pct: float = Field(default=15.0, description="Max gas as % of expected profit")
    kelly_fraction: float = Field(default=0.15, description="Kelly criterion fraction")

class StrategyConfig(BaseModel):
    """Strategy configuration"""
    s1_rebalancing_enabled: bool = Field(default=True, description="Enable rebalancing strategy")
    s1_min_net_profit_pct: float = Field(default=0.3, description="Min profit for rebalancing")
    s2_cross_platform_enabled: bool = Field(default=False, description="Enable cross-platform strategy")
    s2_min_net_profit_pct: float = Field(default=1.5, description="Higher bar for cross-platform")
    s3_logical_arb_enabled: bool = Field(default=False, description="Enable logical arbitrage")
    s3_min_edge_pct: float = Field(default=2.0, description="Min edge for logical arb")
    s3_min_confidence: str = Field(default="HIGH", description="Minimum confidence level")
    s4_settlement_arb_enabled: bool = Field(default=False, description="Enable settlement arbitrage")
    s4_max_seconds_from_event: int = Field(default=120, description="Max seconds from event")
    s5_combinatorial_enabled: bool = Field(default=False, description="Enable combinatorial strategy")
    s5_paper_mode_only: bool = Field(default=True, description="Only run in paper mode")
    s6_options_signal_enabled: bool = Field(default=False, description="Enable options signal strategy")
    s6_min_divergence_pct: float = Field(default=5.0, description="Min divergence for options signal")
    s7_behavioral_bias_enabled: bool = Field(default=False, description="Enable behavioral bias strategy")
    s7_paper_mode_only: bool = Field(default=True, description="Only run in paper mode")

class IntelligenceConfig(BaseModel):
    """Intelligence module configuration"""
    llm_model_analysis: str = Field(default="claude-sonnet-4-6", description="Model for market analysis")
    llm_model_debate: str = Field(default="claude-opus-4-6", description="Model for debates")
    llm_batch_size: int = Field(default=20, description="Markets per LLM API call")
    llm_cache_ttl_minutes: int = Field(default=10, description="Cache TTL in minutes")
    llm_daily_spend_limit: float = Field(default=10.0, description="USD daily spend limit")
    high_value_trade_threshold_usd: float = Field(default=200.0, description="Threshold for debate review")
    persona_enabled: bool = Field(default=True, description="Enable persona tracking")
    whale_min_win_rate: float = Field(default=0.65, description="Min win rate for whale tracking")
    whale_min_trade_count: int = Field(default=10, description="Min trades to trust win rate")
    alt_source_shadow_days: int = Field(default=30, description="Shadow monitoring days")
    correlation_enabled: bool = Field(default=True, description="Enable correlation engine")
    correlation_min_sample: int = Field(default=20, description="Min resolutions to trust")
    correlation_p_threshold: float = Field(default=0.05, description="Statistical significance")

class DataFeedsConfig(BaseModel):
    """Data feeds configuration"""
    kalshi_ws_enabled: bool = Field(default=True, description="Enable Kalshi WebSocket")
    polymarket_ws_enabled: bool = Field(default=False, description="Enable Polymarket WebSocket")
    ws_reconnect_max_tries: int = Field(default=10, description="Max reconnection attempts")
    ws_reconnect_delay_sec: float = Field(default=1.0, description="Reconnection delay in seconds")
    calendar_enabled: bool = Field(default=True, description="Enable economic calendar")
    calendar_refresh_hours: int = Field(default=6, description="Calendar refresh interval")
    news_enabled: bool = Field(default=True, description="Enable news feed")
    news_poll_interval_sec: int = Field(default=60, description="News poll interval")
    news_relevance_threshold: float = Field(default=0.6, description="News relevance threshold")
    ads_b_enabled: bool = Field(default=False, description="Enable ads B")
    ship_tracking_enabled: bool = Field(default=False, description="Enable ship tracking")
    satellite_enabled: bool = Field(default=False, description="Enable satellite tracking")
    foia_enabled: bool = Field(default=True, description="Enable FOIA records")
    congress_trading_enabled: bool = Field(default=True, description="Enable Congress trading")
    sec_filings_enabled: bool = Field(default=True, description="Enable SEC filings")
    google_trends_enabled: bool = Field(default=True, description="Enable Google Trends")
    wikipedia_enabled: bool = Field(default=True, description="Enable Wikipedia")
    reddit_enabled: bool = Field(default=True, description="Enable Reddit")

class ComplianceConfig(BaseModel):
    """Compliance configuration"""
    tax_year: int = Field(default=2026, description="Tax year for reporting")
    state: str = Field(default="FL", description="State for tax purposes")
    monthly_export_day: int = Field(default=1, description="Day for monthly exports")
    log_retention_months: int = Field(default=84, description="7 years retention (IRS requirement)")
    vpn_check_enabled: bool = Field(default=True, description="Enable VPN check")
    mnpi_check_enabled: bool = Field(default=True, description="Enable MNPI check")

class AlertConfig(BaseModel):
    """Alert configuration"""
    trade_executed: bool = Field(default=True, description="Alert on trade execution")
    daily_pnl_summary: bool = Field(default=True, description="Daily PnL summary")
    risk_limit_hit: bool = Field(default=True, description="Alert on risk limit hit")
    agent_health_failure: bool = Field(default=True, description="Alert on health failure")
    strategy_self_disabled: bool = Field(default=True, description="Alert on strategy disable")
    new_correlation_found: bool = Field(default=True, description="Alert on new correlation")
    regulatory_change: bool = Field(default=True, description="Alert on regulatory changes")
    kill_switch_activated: bool = Field(default=True, description="Alert on kill switch")

class KarbotConfig(BaseModel):
    """Main Karbot configuration"""
    system: SystemConfig = field(default_factory=SystemConfig)
    capital: CapitalConfig = field(default_factory=CapitalConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategies: StrategyConfig = field(default_factory=StrategyConfig)
    intelligence: IntelligenceConfig = field(default_factory=IntelligenceConfig)
    data_feeds: DataFeedsConfig = field(default_factory=DataFeedsConfig)
    compliance: ComplianceConfig = field(default_factory=ComplianceConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)

    @classmethod
    def from_file(cls, config_path: Path) -> 'KarbotConfig':
        """Load configuration from YAML file"""
        if not config_path.exists():
            logger.warning(f"Configuration file not found: {config_path}")
            # Return default configuration
            return cls()

        try:
            with open(config_path, 'r') as f:
                config_data = yaml.safe_load(f)

            # Create config instance from loaded data
            config = cls(**config_data)
            logger.info("Configuration loaded successfully")
            return config

        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            raise

class Secrets(BaseSettings):
    """Secrets loaded from environment variables"""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)

    # Kalshi API credentials
    kalshi_api_key: Optional[str] = Field(default=None, env="KALSHI_API_KEY")
    kalshi_api_secret: Optional[str] = Field(default=None, env="KALSHI_API_SECRET")

    # Optional API keys
    polymarket_private_key: Optional[str] = Field(default=None, env="POLYMARKET_PRIVATE_KEY")
    anthropic_api_key: Optional[str] = Field(default=None, env="ANTHROPIC_API_KEY")
    telegram_bot_token: Optional[str] = Field(default=None, env="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: Optional[str] = Field(default=None, env="TELEGRAM_CHAT_ID")
    news_api_key: Optional[str] = Field(default=None, env="NEWS_API_KEY")
    alpaca_news_api_key: Optional[str] = Field(default=None, env="ALPACA_NEWS_API_KEY")
    polygon_rpc_url_1: Optional[str] = Field(default=None, env="POLYGON_RPC_URL_1")
    polygon_rpc_url_2: Optional[str] = Field(default=None, env="POLYGON_RPC_URL_2")
    polygon_rpc_url_3: Optional[str] = Field(default=None, env="POLYGON_RPC_URL_3")
    tradier_api_key: Optional[str] = Field(default=None, env="TRADIER_API_KEY")
    quiver_api_key: Optional[str] = Field(default=None, env="QUIVER_API_KEY")
    google_trends_api_key: Optional[str] = Field(default=None, env="GOOGLE_TRENDS_API_KEY")

    @classmethod
    def load(cls) -> 'Secrets':
        """Load secrets from environment"""
        try:
            secrets = cls()
            logger.info("Secrets loaded successfully")
            return secrets
        except Exception as e:
            logger.error(f"Error loading secrets: {e}")
            raise