"""
Logger setup for Karbot Rage! - Automated Trading System
"""

import logging
import sys
from datetime import datetime

def setup_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Setup logger with custom formatting

    Args:
        name: Logger name
        level: Logging level

    Returns:
        Configured logger
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid adding multiple handlers if already configured
    if not logger.handlers:
        # Create console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)

        # Create file handler
        file_handler = logging.FileHandler(f'karbotrage_{datetime.now().strftime("%Y%m%d")}.log')
        file_handler.setLevel(level)

        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

        # Add formatter to handlers
        console_handler.setFormatter(formatter)
        file_handler.setFormatter(formatter)

        # Add handlers to logger
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

    return logger