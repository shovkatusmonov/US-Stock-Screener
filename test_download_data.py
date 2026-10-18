"""Regression tests for overlap refresh and malformed OHLC rejection."""
import sqlite3
import sys
import types

import pandas as pd

# Unit tests target persistence/validation only; do not require network-provider package.
if "yfinance" not in sys.modules:
    fake_yf = types.ModuleType("yfinance")
    fake_yf.Ticker = lambda ticker: None
    sys.modules["yfinance"] = fake_yf

from download_data import SCHEMA, save, valid_ohlc_rows


def test_valid_ohlc_rows_rejects_impossible_bars():
    idx = pd.to_datetime(["2026-10-13", "2026-10-14", "2026-10-15"])
    df = pd.DataFrame({
        "Open": [100.0, 110.0, 0.0],
        "High": [105.0, 109.0, 5.0],
        "Low": [98.0, 100.0, 1.0],
        "Close": [103.0, 105.0, 2.0],
        "Adj Close": [103.0, 105.0, 2.0],
        "Volume": [1000.0, 1000.0, 1000.0],
    }, index=idx)
    valid, invalid_count = valid_ohlc_rows(df)
    assert list(valid.index) == [idx[0]]
    assert invalid_count == 2


def test_save_updates_existing_overlap_without_duplicate_rows():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    idx = pd.to_datetime(["2026-10-14"])
    first = pd.DataFrame({
        "Open": [100.0], "High": [105.0], "Low": [98.0],
        "Close": [103.0], "Adj Close": [103.0], "Volume": [1000.0],
    }, index=idx)
    assert save(conn, "AAA", first) == 1
    revised = first.copy()
    revised.loc[idx[0], "Close"] = 104.0
    revised.loc[idx[0], "Adj Close"] = 104.0
    assert save(conn, "AAA", revised) == 0
    row = conn.execute("SELECT close, COUNT(*) OVER () FROM prices WHERE ticker='AAA'").fetchone()
    assert row[0] == 104.0
    assert row[1] == 1
