"""
tests/test_bot.py - Tests for Telegram Bot Formatters and Permissions.
"""

from app.telegram.formatters import (
    format_screener_results_table,
    format_quote_card,
    format_market_breadth_card,
    format_health_card
)
from config.settings import is_admin, ADMIN_USER_IDS


def test_admin_check():
    ADMIN_USER_IDS.clear()
    ADMIN_USER_IDS.add(123456)
    ADMIN_USER_IDS.add(789012)
    assert is_admin(123456) is True
    assert is_admin(789012) is True
    assert is_admin(999999) is False


def test_screener_table_formatter():
    empty_res = format_screener_results_table([], "bullish_breakout")
    assert len(empty_res) == 1
    assert "0 matches" in empty_res[0]

    candidates = [
        {
            "symbol": f"SYM{i}",
            "close": 100.0 + i,
            "rsi14": 55.0,
            "rel_vol": 1.5,
            "dist_52w_high": -0.02,
            "trend_regime": "UPTREND"
        }
        for i in range(10)
    ]
    res = format_screener_results_table(candidates, "bullish_breakout")
    assert len(res) >= 1
    assert "Ticker" in res[0]
    assert "SYM0" in res[0]


def test_quote_card_formatter():
    quote_data = {
        "symbol": "NVDA",
        "exchange": "NASDAQ",
        "name": "NVIDIA Corporation",
        "close": 125.50,
        "rsi14": 65.4,
        "sma50": 115.0,
        "sma200": 95.0,
        "rel_vol": 2.1,
        "trend_regime": "UPTREND",
        "dist_52w_high": -0.015,
        "dist_52w_low": 0.85,
        "ret20": 0.12,
        "date": "2026-10-01"
    }
    card = format_quote_card(quote_data)
    assert "NVDA" in card
    assert "$125.50" in card
    assert "UPTREND" in card
    assert "65.4" in card


def test_health_card_formatter():
    health = {
        "overall_status": "HEALTHY",
        "database": {"integrity_ok": True, "db_size_mb": 1.5, "table_counts": {"universe": 13239}},
        "data_freshness": {"latest_data_date": "2026-11-08", "lag_days": 1},
        "resources": {"process_ram_mb": 120.5, "ram_limit_mb": 700, "disk_free_gb": 40.0, "disk_total_gb": 45.0}
    }
    card = format_health_card(health)
    assert "HEALTHY" in card
    assert "120.5 MB" in card
    assert "13,239" in card
