"""
Basic tests for Karbot Rage! - Automated Trading System
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + '/..')

from core.config import load_config
from execution.engine import ExecutionEngine
from data.market_data import MarketData
from intelligence.analyzer import MarketAnalyzer
from strategies.strategy_manager import StrategyManager
from trading.trader import Trader

class TestKarbotRage(unittest.TestCase):
    """Test basic components of Karbot Rage!"""

    def setUp(self):
        """Set up test fixtures"""
        self.config = load_config()
        self.engine = ExecutionEngine(self.config)

    def test_config_loading(self):
        """Test configuration loading"""
        config = load_config()
        self.assertIsInstance(config, dict)
        self.assertIn('trading', config)
        self.assertIn('api', config)
        self.assertIn('data', config)
        self.assertIn('strategies', config)

    def test_market_data_initialization(self):
        """Test market data initialization"""
        market_data = MarketData(self.config)
        self.assertIsInstance(market_data, MarketData)

    def test_analyzer_initialization(self):
        """Test analyzer initialization"""
        analyzer = MarketAnalyzer(self.config)
        self.assertIsInstance(analyzer, MarketAnalyzer)

    def test_strategy_manager_initialization(self):
        """Test strategy manager initialization"""
        strategy_manager = StrategyManager(self.config)
        self.assertIsInstance(strategy_manager, StrategyManager)

    def test_trader_initialization(self):
        """Test trader initialization"""
        trader = Trader(self.config)
        self.assertIsInstance(trader, Trader)

    def test_engine_initialization(self):
        """Test engine initialization"""
        self.assertIsInstance(self.engine, ExecutionEngine)

    @patch('data.market_data.MarketData.get_market_data')
    def test_engine_cycle(self, mock_get_data):
        """Test a complete engine cycle"""
        # Mock market data
        mock_get_data.return_value = [
            {
                'market_id': 'test_market',
                'name': 'Test Market',
                'price': 0.5,
                'volume': 1000,
                'timestamp': '2023-01-01T00:00:00',
                'source': 'test'
            }
        ]

        # This should not raise an exception
        try:
            self.engine._execute_cycle()
            self.assertTrue(True)  # If we get here, no exception was raised
        except Exception as e:
            self.fail(f"Engine cycle failed with exception: {e}")

if __name__ == '__main__':
    unittest.main()