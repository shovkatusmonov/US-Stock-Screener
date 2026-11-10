"""
config/settings.py - Central Configuration Management for US Stock Screener.
Tuned for Ubuntu 24.04 (952 MiB RAM / 1 GiB Swap Free Tier VPS).
"""

import os
from pathlib import Path
from typing import List, Set
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Directory Paths
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
SCRIPTS_DIR = BASE_DIR / "scripts"
DB_PATH = Path(os.getenv("SCREENER_DB_PATH", str(DATA_DIR / "screener.db"))).expanduser()
SYMBOLS_CACHE_PATH = DATA_DIR / "nasdaq_symbols.txt"

# Timezone: strict US Eastern Time with DST handling
TIMEZONE_NAME = "America/New_York"
NY_TZ = ZoneInfo(TIMEZONE_NAME)

# Memory & Process Safety (for 952 MiB RAM VPS)
MAX_RAM_MB = int(os.getenv("MAX_RAM_MB", "700"))
BATCH_SIZE_DOWNLOAD = int(os.getenv("BATCH_SIZE_DOWNLOAD", "25"))
BATCH_SIZE_SCREENING = int(os.getenv("BATCH_SIZE_SCREENING", "50"))
MAX_CONCURRENT_WORKERS = int(os.getenv("MAX_CONCURRENT_WORKERS", "2"))

# Data Provider
PRIMARY_PROVIDER = os.getenv("MARKET_DATA_PROVIDER", "yfinance")
YFINANCE_RATE_LIMIT_DELAY = float(os.getenv("YFINANCE_RATE_LIMIT_DELAY", "0.2"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

# Benchmarks
BENCHMARKS: List[str] = ["SPY", "QQQ", "IWM", "DIA"]
PRIMARY_BENCHMARK = "SPY"

# Telegram Bot
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_USER_IDS: Set[int] = {
    int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",") if x.strip().isdigit()
}

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = LOGS_DIR / "screener.log"

# Ensure essential directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def is_admin(user_id: int) -> bool:
    """Check if given Telegram user is authorized."""
    if not ADMIN_USER_IDS:
        return False
    return user_id in ADMIN_USER_IDS
