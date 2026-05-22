"""
Execution engine for Karbot Rage! - Automated Trading System
"""

import logging
import asyncio
from typing import Dict, Any
from datetime import datetime

from data.market_data import MarketData
from intelligence.analyzer import MarketAnalyzer
from strategies.strategy_manager import StrategyManager
from trading.trader import Trader

class ExecutionEngine:
    """
    Main execution engine that coordinates all components
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize execution engine

        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.logger.info("Initializing Execution Engine")

        # Initialize components
        self.market_data = MarketData(config)
        self.analyzer = MarketAnalyzer(config)
        self.strategy_manager = StrategyManager(config)
        self.trader = Trader(config)

        self.running = False

    def start(self):
        """
        Start the execution engine
        """
        self.logger.info("Starting Execution Engine")
        self.running = True

        try:
            # Run a single cycle of execution
            self._execute_cycle()
        except Exception as e:
            self.logger.error(f"Error in execution cycle: {str(e)}")
            raise
        finally:
            self.stop()

    def _execute_cycle(self):
        """
        Execute a single cycle of the trading system
        """
        self.logger.info("Starting execution cycle")

        # 1. Get market data
        markets = self.market_data.get_market_data()
        self.logger.info(f"Retrieved {len(markets)} markets")

        # 2. Analyze markets
        analysis_results = self.analyzer.analyze_markets(markets)
        self.logger.info(f"Analysis completed for {len(analysis_results)} markets")

        # 3. Execute strategies
        trades = self.strategy_manager.execute_strategies(analysis_results)
        self.logger.info(f"Strategy execution generated {len(trades)} trades")

        # 4. Execute trades
        if trades:
            execution_results = self.trader.execute_trades(trades)
            self.logger.info(f"Trade execution completed with {len(execution_results)} results")
        else:
            self.logger.info("No trades to execute")

        self.logger.info("Execution cycle completed")

    def stop(self):
        """
        Stop the execution engine
        """
        if not self.running:
            return

        self.logger.info("Stopping Execution Engine")
        self.running = False

        # Cleanup components
        try:
            self.market_data.cleanup()
        except Exception as e:
            self.logger.error(f"Error during market data cleanup: {str(e)}")

        try:
            self.analyzer.cleanup()
        except Exception as e:
            self.logger.error(f"Error during analyzer cleanup: {str(e)}")

        try:
            self.strategy_manager.cleanup()
        except Exception as e:
            self.logger.error(f"Error during strategy manager cleanup: {str(e)}")

        try:
            self.trader.cleanup()
        except Exception as e:
            self.logger.error(f"Error during trader cleanup: {str(e)}")

        self.logger.info("Execution Engine stopped")