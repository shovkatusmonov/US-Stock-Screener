"""
tests/test_indicators.py - Tests for Technical Indicators and Multi-Timeframe Analysis.
"""

import numpy as np
import pandas as pd
import pytest
from app.indicators.technical import (
    sma, ema, rsi, macd, atr, bollinger_bands, adx, vwap,
    compute_all_indicators, create_snapshot_row
)
from app.indicators.multi_timeframe import compute_multi_timeframe_analysis


@pytest.fixture
def sample_ohlcv():
    dates = pd.date_range("2025-01-01", periods=100, freq="B")
    trend = np.linspace(100.0, 150.0, len(dates))
    noise = np.sin(np.linspace(0, 10, len(dates))) * 3.0
    close = trend + noise
    high = close + np.random.uniform(1.0, 2.5, len(dates))
    low = close - np.random.uniform(1.0, 2.5, len(dates))
    open_p = close + np.random.uniform(-1.0, 1.0, len(dates))
    volume = np.random.uniform(500000, 2000000, len(dates))

    return pd.DataFrame({
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "adj_close": close,
        "volume": volume
    }, index=dates)


def test_sma_and_ema(sample_ohlcv):
    c = sample_ohlcv["close"]
    s20 = sma(c, 20)
    e9 = ema(c, 9)

    assert len(s20) == len(c)
    assert s20.iloc[:19].isna().all()
    assert not np.isnan(s20.iloc[-1])
    assert not np.isnan(e9.iloc[-1])


def test_rsi_bounds(sample_ohlcv):
    c = sample_ohlcv["close"]
    rsi14 = rsi(c, 14)

    assert rsi14.iloc[:14].isna().all()
    valid = rsi14.dropna()
    assert (valid >= 0.0).all()
    assert (valid <= 100.0).all()


def test_macd(sample_ohlcv):
    c = sample_ohlcv["close"]
    m = macd(c, 12, 26, 9)

    assert "macd" in m.columns
    assert "macd_signal" in m.columns
    assert "macd_hist" in m.columns
    assert len(m) == len(c)


def test_bollinger_bands(sample_ohlcv):
    c = sample_ohlcv["close"]
    bb = bollinger_bands(c, 20, 2.0)

    assert "bb_upper" in bb.columns
    assert "bb_middle" in bb.columns
    assert "bb_lower" in bb.columns
    valid = bb.dropna()
    assert (valid["bb_upper"] >= valid["bb_middle"]).all()
    assert (valid["bb_middle"] >= valid["bb_lower"]).all()


def test_atr(sample_ohlcv):
    h = sample_ohlcv["high"]
    l = sample_ohlcv["low"]
    c = sample_ohlcv["close"]
    atr14 = atr(h, l, c, 14)

    assert len(atr14) == len(c)
    valid = atr14.dropna()
    assert (valid > 0).all()


def test_adx(sample_ohlcv):
    h = sample_ohlcv["high"]
    l = sample_ohlcv["low"]
    c = sample_ohlcv["close"]
    adx_df = adx(h, l, c, 14)

    assert "adx" in adx_df.columns
    valid = adx_df["adx"].dropna()
    assert (valid >= 0.0).all()
    assert (valid <= 100.0).all()


def test_compute_all_and_snapshot(sample_ohlcv):
    full = compute_all_indicators(sample_ohlcv)
    assert "sma50" in full.columns
    assert "rsi14" in full.columns
    assert "trend_regime" in full.columns
    assert "rel_vol" in full.columns

    snap = create_snapshot_row("AAPL", "NASDAQ", sample_ohlcv)
    assert snap is not None
    assert snap["symbol"] == "AAPL"
    assert snap["exchange"] == "NASDAQ"
    assert snap["trend_regime"] == "INSUFFICIENT_DATA"


def test_multi_timeframe(sample_ohlcv):
    res = compute_multi_timeframe_analysis(sample_ohlcv)
    assert res["status"] == "INSUFFICIENT_DATA"
    assert res["alignment"] == "INSUFFICIENT_DATA"
    assert res["daily"]["ready"] is False
    assert "weekly" in res



def test_short_history_trend_is_insufficient():
    dates = pd.date_range("2026-01-01", periods=100, freq="B")
    close = pd.Series(np.linspace(50.0, 80.0, 100), index=dates)
    df = pd.DataFrame({
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "adj_close": close,
        "volume": 500000.0
    }, index=dates)

    snap = create_snapshot_row("SHORT", "US", df)

    assert snap is not None
    assert snap["sma50"] is not None
    assert snap["sma200"] is None
    assert snap["trend_regime"] == "INSUFFICIENT_DATA"



def test_multi_timeframe_ready_with_sufficient_history():
    dates = pd.date_range("2020-01-01", periods=400, freq="B")
    close = pd.Series(np.linspace(50.0, 100.0, 400), index=dates)
    df = pd.DataFrame({
        "open": close - 0.2,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "adj_close": close,
        "volume": 500000.0
    }, index=dates)

    result = compute_multi_timeframe_analysis(df)

    assert result["status"] == "OK"
    assert result["daily"]["ready"] is True
    assert result["weekly"]["ready"] is True
    assert result["alignment"] in (
        "BULLISH_CONFLUENCE",
        "BEARISH_CONFLUENCE",
        "DAILY_PULLBACK_OR_COUNTERTREND",
        "NEUTRAL"
    )
