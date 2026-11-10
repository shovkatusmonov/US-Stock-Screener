"""
tests/test_breadth.py - Tests for Market Breadth and Benchmark Aggregates.
"""

from pathlib import Path
import sqlite3
import pytest
from app.analytics.breadth import MarketBreadthAnalyzer


def test_market_breadth_calculation(tmp_path):
    test_db = tmp_path / "test_breadth.db"
    conn = sqlite3.connect(str(test_db))
    conn.execute("""
        CREATE TABLE technical_snapshots (
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL DEFAULT 'US',
            date TEXT NOT NULL,
            close REAL NOT NULL,
            sma50 REAL,
            sma200 REAL,
            ret5 REAL,
            dist_52w_high REAL,
            dist_52w_low REAL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (symbol, exchange, date)
        );
    """)
    conn.execute("""
        INSERT INTO technical_snapshots (
            symbol, exchange, date, close, sma50, sma200, ret5, dist_52w_high, dist_52w_low, updated_at
        ) VALUES
        ('SPY', 'US', '2026-10-01', 500.0, 480.0, 450.0, 0.02, -0.005, 0.25, '2026-10-01T20:00:00'),
        ('QQQ', 'US', '2026-10-01', 450.0, 430.0, 400.0, 0.03, -0.002, 0.30, '2026-10-01T20:00:00'),
        ('STOCK1', 'US', '2026-10-01', 100.0, 90.0, 80.0, 0.05, -0.008, 0.40, '2026-10-01T20:00:00'),
        ('STOCK2', 'US', '2026-10-01', 50.0, 55.0, 60.0, -0.04, -0.30, 0.005, '2026-10-01T20:00:00')
    """)
    conn.commit()
    conn.close()

    analyzer = MarketBreadthAnalyzer(db_path=str(test_db))
    record = analyzer.compute_daily_breadth("2026-10-01")

    assert record is not None
    assert record["total_stocks"] == 4
    assert record["advancing"] == 3
    assert record["declining"] == 1
    assert record["above_sma50_count"] == 3
    assert record["above_sma50_pct"] == 75.0
    assert record["new_52w_highs"] == 3
    assert record["new_52w_lows"] == 1

    latest = analyzer.get_latest_breadth()
    assert latest is not None
    assert latest["date"] == "2026-10-01"
    assert latest["total_stocks"] == 4
