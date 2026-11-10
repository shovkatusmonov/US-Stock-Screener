"""
app/monitoring/logger.py - Central Logging Setup with File Rotation.
"""

import logging
from logging.handlers import RotatingFileHandler
import sys
from pathlib import Path
from typing import Optional

try:
    from config.settings import LOG_FILE, LOG_LEVEL
    DEFAULT_LOG_FILE = LOG_FILE
    DEFAULT_LOG_LEVEL = LOG_LEVEL
except ImportError:
    DEFAULT_LOG_FILE = Path("logs/screener.log")
    DEFAULT_LOG_LEVEL = "INFO"


def setup_logging(
    log_file: Optional[Path] = None,
    level: Optional[str] = None
) -> logging.Logger:
    """Configures application-wide structured logging with rotating file handler."""
    target_file = Path(log_file or DEFAULT_LOG_FILE)
    target_file.parent.mkdir(parents=True, exist_ok=True)

    lvl_str = level or DEFAULT_LOG_LEVEL
    numeric_level = getattr(logging, lvl_str.upper(), logging.INFO)
    logger = logging.getLogger("screener")
    logger.setLevel(numeric_level)

    # If custom log_file is explicitly provided, reconfigure handlers
    if log_file is not None:
        logger.handlers.clear()
    elif logger.handlers:
        return logger

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s:%(module)s:%(lineno)d] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console Handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(numeric_level)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Rotating File Handler (5 MB max per file, up to 5 backups)
    fh = RotatingFileHandler(
        str(target_file),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    fh.setLevel(numeric_level)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger
