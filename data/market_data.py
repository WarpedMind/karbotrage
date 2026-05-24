"""
Market data handling for Karbot Rage! - Automated Trading System
"""

import logging
import asyncio
from typing import List, Dict, Any
from datetime import datetime, timedelta

# Import the data sources
from data.sources.polymarket import PolymarketDataSource
from data.sources.kalshi import KalshiDataSource

class MarketData:
    """
    Handles market data fetching and caching
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize market data handler

        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.logger.info("Initializing Market Data Handler")

        # Initialize data sources
        self.polymarket_source = PolymarketDataSource(config)
        self.kalshi_source = KalshiDataSource(config)

        # Cache for market data
        self._cache = {}
        self._cache_timestamps = {}

    async def initialize(self):
        """
        Initialize all data sources
        """
        self.logger.info("Initializing market data sources")
        await self.polymarket_source.initialize()
        await self.kalshi_source.initialize()
        self.logger.info("Market data sources initialized")

    async def get_market_data(self) -> List[Dict[str, Any]]:
        """
        Get market data from APIs

        Returns:
            List of market data dictionaries
        """
        self.logger.info("Fetching market data from all sources")

        markets = []

        # Fetch from Polymarket
        polymarket_markets = await self.polymarket_source.fetch_markets()
        markets.extend(polymarket_markets)

        # Fetch from Kalshi
        kalshi_markets = await self.kalshi_source.fetch_markets()
        markets.extend(kalshi_markets)

        self.logger.info(f"Fetched {len(markets)} markets from all sources")
        return markets

    async def get_market_details(self, market_id: str) -> Dict[str, Any]:
        """
        Get detailed information for a specific market

        Args:
            market_id: ID of the market to fetch

        Returns:
            Market details dictionary
        """
        self.logger.info(f"Fetching details for market {market_id}")

        # Try to fetch from Polymarket first
        details = await self.polymarket_source.fetch_market_details(market_id)
        if details:
            return details

        # If not found in Polymarket, try Kalshi
        details = await self.kalshi_source.fetch_market_details(market_id)
        return details

    def _is_cache_valid(self, cache_key: str, max_age: int = 3600) -> bool:
        """
        Check if cached data is still valid

        Args:
            cache_key: Key to check in cache
            max_age: Maximum cache age in seconds

        Returns:
            True if cache is valid, False otherwise
        """
        if cache_key not in self._cache_timestamps:
            return False

        timestamp = self._cache_timestamps[cache_key]
        age = (datetime.now() - timestamp).total_seconds()

        return age < max_age

    def _get_cached_data(self, cache_key: str) -> Any:
        """
        Get cached data

        Args:
            cache_key: Key to retrieve from cache

        Returns:
            Cached data or None if not found
        """
        if self._is_cache_valid(cache_key):
            return self._cache.get(cache_key)
        return None

    def _set_cache(self, cache_key: str, data: Any):
        """
        Set cached data

        Args:
            cache_key: Key to store in cache
            data: Data to cache
        """
        self._cache[cache_key] = data
        self._cache_timestamps[cache_key] = datetime.now()

    def cleanup(self):
        """
        Cleanup resources
        """
        self.logger.info("Cleaning up Market Data Handler")
        # Clear cache
        self._cache.clear()
        self._cache_timestamps.clear()
        self.logger.info("Market Data Handler cleanup completed")