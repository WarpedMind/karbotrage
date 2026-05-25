"""
Market data handling for Karbot Rage! - Automated Trading System

Phase 1: Kalshi only. Polymarket is completely disabled when
polymarket_ws_enabled=False (which is the default and Phase 1 requirement).
"""

import logging
import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from data.sources.kalshi import KalshiDataSource
from data.sources.polymarket import PolymarketDataSource


class MarketData:
    """
    Handles market data fetching and caching.

    Kalshi is always primary. Polymarket is only instantiated and fetched
    when polymarket_ws_enabled=True (Phase 2+ only).
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.logger.info("Initializing Market Data Handler")

        # Phase gate: Polymarket disabled unless explicitly enabled
        self._polymarket_enabled = config.get('polymarket_ws_enabled', False)

        # Kalshi is always active (Phase 1 primary)
        self.kalshi_source = KalshiDataSource(config)

        # Polymarket only instantiated when enabled to avoid any accidental calls
        self.polymarket_source: Optional[PolymarketDataSource] = (
            PolymarketDataSource(config) if self._polymarket_enabled else None
        )

        if not self._polymarket_enabled:
            self.logger.info(
                "Polymarket data source DISABLED (polymarket_ws_enabled=False). "
                "Phase 1: Kalshi only."
            )

        self._cache: Dict[str, Any] = {}
        self._cache_timestamps: Dict[str, datetime] = {}

    async def initialize(self):
        """Initialize active data sources. Kalshi always first."""
        self.logger.info("Initializing market data sources")

        await self.kalshi_source.initialize()

        if self._polymarket_enabled and self.polymarket_source:
            await self.polymarket_source.initialize()

        self.logger.info("Market data sources initialized")

    async def get_market_data(self) -> List[Dict[str, Any]]:
        """
        Fetch market data. Kalshi is always fetched first.
        Polymarket is only fetched when polymarket_ws_enabled=True.
        """
        self.logger.info("Fetching market data")
        markets = []

        # Kalshi first — Phase 1 primary source
        kalshi_markets = await self.kalshi_source.fetch_markets()
        markets.extend(kalshi_markets)
        self.logger.info(f"Fetched {len(kalshi_markets)} markets from Kalshi")

        # Polymarket only when explicitly enabled (Phase 2+)
        if self._polymarket_enabled and self.polymarket_source:
            polymarket_markets = await self.polymarket_source.fetch_markets()
            markets.extend(polymarket_markets)
            self.logger.info(f"Fetched {len(polymarket_markets)} markets from Polymarket")

        self.logger.info(f"Total markets fetched: {len(markets)}")
        return markets

    async def get_market_details(self, market_id: str) -> Dict[str, Any]:
        """
        Get detailed information for a specific market.
        Kalshi is always tried first.
        """
        self.logger.info(f"Fetching details for market {market_id}")

        # Try Kalshi first
        details = await self.kalshi_source.fetch_market_details(market_id)
        if details:
            return details

        # Fall back to Polymarket only if enabled
        if self._polymarket_enabled and self.polymarket_source:
            details = await self.polymarket_source.fetch_market_details(market_id)
            if details:
                return details

        return {}

    def _is_cache_valid(self, cache_key: str, max_age: int = 3600) -> bool:
        if cache_key not in self._cache_timestamps:
            return False
        age = (datetime.now() - self._cache_timestamps[cache_key]).total_seconds()
        return age < max_age

    def _get_cached_data(self, cache_key: str) -> Any:
        if self._is_cache_valid(cache_key):
            return self._cache.get(cache_key)
        return None

    def _set_cache(self, cache_key: str, data: Any):
        self._cache[cache_key] = data
        self._cache_timestamps[cache_key] = datetime.now()

    def cleanup(self):
        self.logger.info("Cleaning up Market Data Handler")
        self._cache.clear()
        self._cache_timestamps.clear()
        self.logger.info("Market Data Handler cleanup completed")
