"""
test_tradable_execution.py - P1.7: Tradable OHLC va Adjusted narxlar ajratilishi regressiya testi.
"""
import pytest
import pandas as pd
import numpy as np
import features as ft


def test_tradable_ohlc_preserves_raw_quotes():
    """Tradable OHLC dividendlardan tuzatilgan sun'iy narxlarni emas, sof narxlarni saqlashi shart."""
    dates = pd.date_range("2024-01-01", periods=3)
    raw_df = pd.DataFrame({
        "open":      [100.0, 102.0, 104.0],
        "high":      [105.0, 106.0, 108.0],
        "low":       [99.0,  101.0, 103.0],
        "close":     [102.0, 104.0, 106.0],
        "volume":    [1000,  1200,  1100],
        "adj_close": [90.0,   92.0,  94.0],  # Dividendlardan keyingi orqaga surilgan narx
    }, index=dates)

    tradable = ft.get_tradable_ohlc(raw_df)
    # Tradable narx 100.0 (real narx) bo'lishi shart, 90.0 emas
    assert tradable["open"].iloc[0] == 100.0
    assert tradable["close"].iloc[0] == 102.0

    # Adjusted narx esa 90.0 atrofida bo'lishi shart
    adjusted = ft.adjust_ohlc(raw_df)
    assert adjusted["close"].iloc[0] == 90.0
    assert adjusted["open"].iloc[0] < 100.0


def test_features_build_properly():
    """Indikatorlar xususiyatlar jadvali to'g'ri yaratilishi tekshiruvi."""
    dates = pd.date_range("2023-01-01", periods=250)
    raw = pd.DataFrame({
        "open": np.linspace(100, 150, 250),
        "high": np.linspace(102, 152, 250),
        "low": np.linspace(98, 148, 250),
        "close": np.linspace(101, 151, 250),
        "volume": [10000] * 250,
        "adj_close": np.linspace(101, 151, 250),
    }, index=dates)

    f = ft.build_features(raw, raw)
    assert not f.empty
    assert "atr" in f.columns
    assert "rel_strength" in f.columns