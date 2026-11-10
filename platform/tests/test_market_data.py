"""
tests/test_market_data.py - Tests for Market Data Provider, Cleaning, and Incremental Loader.
"""

from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from app.data.provider import clean_ohlcv, MockDataProvider
from app.data.loader import MarketDataLoader, resample_ohlcv


def test_clean_ohlcv_filters_invalid_bars():
    dates = pd.date_range("2026-01-01", periods=4, freq="D")
    df = pd.DataFrame({
        "open": [10.0, 10.0, -5.0, 10.0],
        "high": [12.0, 9.0, 10.0, 15.0],    # row 1 has high < open (impossible)
        "low": [8.0, 8.0, -10.0, 16.0],     # row 2 has negative price; row 3 has low > high
        "close": [11.0, 8.5, 5.0, 12.0],
        "volume": [1000, 1000, 1000, 1000]
    }, index=dates)

    cleaned = clean_ohlcv(df)
    assert len(cleaned) == 1
    assert cleaned.index[0] == dates[0]


def test_incremental_loader_and_resampling(tmp_path):
    test_db = tmp_path / "test_loader.db"

    # Create synthetic daily data for AAPL (15 business days)
    dates = pd.date_range("2026-01-01", periods=15, freq="B")
    prices = np.linspace(100.0, 120.0, len(dates))
    mock_df = pd.DataFrame({
        "open": prices,
        "high": prices + 2.0,
        "low": prices - 1.0,
        "close": prices + 1.0,
        "adj_close": prices + 1.0,
        "volume": np.full(len(dates), 1000000.0)
    }, index=dates)

    # First batch: first 10 days
    provider = MockDataProvider({"AAPL": mock_df.iloc[:10]})
    loader = MarketDataLoader(db_path=str(test_db), provider=provider)

    added1 = loader.update_symbol_daily("AAPL", exchange="NASDAQ")
    assert added1 == 10

    # Second batch: remaining 5 days
    loader.provider = MockDataProvider({"AAPL": mock_df.iloc[10:]})
    added2 = loader.update_symbol_daily("AAPL", exchange="NASDAQ")
    assert added2 == 5

    # Check total rows loaded in DB
    daily_loaded = loader.load_ohlcv("AAPL", exchange="NASDAQ", timeframe="1d")
    assert len(daily_loaded) == 15

    # Check weekly resampling (spans 4 Friday boundaries)
    weekly_loaded = loader.load_ohlcv("AAPL", exchange="NASDAQ", timeframe="1wk")
    assert len(weekly_loaded) == 4
    assert "close" in weekly_loaded.columns
    assert "volume" in weekly_loaded.columns
