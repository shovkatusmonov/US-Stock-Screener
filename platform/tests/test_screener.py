"""
tests/test_screener.py - Tests for Screener Filters, Presets, and Screener Engine.
"""

from pathlib import Path
import sqlite3
import pytest

from app.scanners.filters import ScreenFilter
from app.scanners.presets import PRESETS, get_preset
from app.scanners.engine import ScreenerEngine


def test_screen_filter_logic():
    flt = ScreenFilter(
        min_price=10.0,
        max_price=100.0,
        min_volume=500000.0,
        min_rsi=50.0,
        above_sma50=True,
        trend_regime="UPTREND"
    )

    good_row = {
        "close": 50.0,
        "volume": 1000000.0,
        "rsi14": 60.0,
        "sma50": 45.0,
        "trend_regime": "UPTREND",
        "asset_class": "CS"
    }
    assert flt.matches(good_row) is True

    bad_price = dict(good_row, close=8.0)
    assert flt.matches(bad_price) is False

    bad_rsi = dict(good_row, rsi14=42.0)
    assert flt.matches(bad_rsi) is False

    bad_trend = dict(good_row, trend_regime="DOWNTREND")
    assert flt.matches(bad_trend) is False

    missing_sma50 = dict(good_row, sma50=None)
    assert flt.matches(missing_sma50) is False

    missing_sma50 = dict(good_row)
    missing_sma50.pop("sma50")
    assert flt.matches(missing_sma50) is False


def test_presets_exist():
    assert "bullish_breakout" in PRESETS
    assert "pullback_support" in PRESETS
    assert "oversold_bounce" in PRESETS
    assert "volume_surge" in PRESETS

    p = get_preset("bullish_breakout")
    assert p.min_price == 5.0
    assert p.trend_regime == "UPTREND"


def test_screener_engine_with_snapshots(tmp_path):
    test_db = tmp_path / "test_screener.db"
    conn = sqlite3.connect(str(test_db))
    conn.execute("""
        CREATE TABLE technical_snapshots (
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL DEFAULT 'US',
            date TEXT NOT NULL,
            close REAL NOT NULL,
            sma20 REAL,
            sma50 REAL,
            sma200 REAL,
            ema9 REAL,
            ema21 REAL,
            ema50 REAL,
            rsi14 REAL,
            macd REAL,
            macd_signal REAL,
            macd_hist REAL,
            atr14 REAL,
            atr_pct REAL,
            bb_upper REAL,
            bb_middle REAL,
            bb_lower REAL,
            bb_pct_b REAL,
            adx14 REAL,
            rel_vol REAL,
            ret5 REAL,
            ret20 REAL,
            ret60 REAL,
            high_52w REAL,
            low_52w REAL,
            dist_52w_high REAL,
            dist_52w_low REAL,
            trend_regime TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (symbol, exchange, date)
        );
    """)
    conn.execute("""
        CREATE TABLE instruments (
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            name TEXT,
            asset_class TEXT NOT NULL,
            is_etf INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            delisted_date TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (symbol, exchange)
        );
    """)

    conn.execute("""
        INSERT INTO technical_snapshots (
            symbol, exchange, date, close, sma20, sma50, sma200, ema9,
            ema21, ema50, rsi14, macd, macd_signal, macd_hist, atr14, atr_pct,
            bb_upper, bb_middle, bb_lower, bb_pct_b, adx14, rel_vol, ret5,
            ret20, ret60, high_52w, low_52w, dist_52w_high, dist_52w_low,
            trend_regime, updated_at
        ) VALUES
        ('BULL1', 'US', '2026-10-01', 120.0, 115.0, 110.0, 95.0, 118.0, 114.0, 110.0, 65.0, 2.5, 2.0, 0.5, 3.0, 0.025, 125.0, 115.0, 105.0, 0.75, 28.0, 2.1, 0.05, 0.12, 0.25, 122.0, 80.0, -0.016, 0.50, 'UPTREND', '2026-10-01T20:00:00'),
        ('BEAR1', 'US', '2026-10-01', 30.0, 32.0, 35.0, 45.0, 31.0, 33.0, 35.0, 28.0, -1.5, -1.0, -0.5, 1.2, 0.04, 38.0, 32.0, 26.0, 0.25, 32.0, 1.1, -0.08, -0.15, -0.30, 60.0, 29.0, -0.50, 0.034, 'DOWNTREND', '2026-10-01T20:00:00')
    """)
    conn.execute("""
        INSERT INTO instruments VALUES
        ('BULL1', 'US', 'Bullish Leader Inc.', 'CS', 0, 1, 'ACTIVE', '2026-01-01', '2026-10-01', NULL, '2026-10-01', '2026-10-01'),
        ('BEAR1', 'US', 'Bearish Lag Co.', 'CS', 0, 1, 'ACTIVE', '2026-01-01', '2026-10-01', NULL, '2026-10-01', '2026-10-01')
    """)
    conn.commit()
    conn.close()

    engine = ScreenerEngine(db_path=str(test_db))

    bull_res = engine.screen("bullish_breakout")
    assert len(bull_res) == 1
    assert bull_res[0]["symbol"] == "BULL1"

    oversold_res = engine.screen("oversold_bounce")
    assert len(oversold_res) == 1
    assert oversold_res[0]["symbol"] == "BEAR1"
