"""
tests/test_backfill.py - Tests for All-Time Historical Data Backfiller and Snapshot Refresh.
"""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import pandas as pd
import pytest

from app.data.backfill import (
    HistoricalBackfiller,
    STATUS_ALL_TIME_COMPLETE,
    STATUS_PARTIAL_HISTORY,
    STATUS_NO_DATA,
    STATUS_RATE_LIMIT,
    STATUS_ERROR,
    STATUS_DELISTED,
    estimate_missing_sessions
)
from app.data.loader import MarketDataLoader
from app.data.provider import MockDataProvider
from app.data.universe import UniverseManager, Instrument
from app.scanners.engine import ScreenerEngine


def test_backfiller_schema_init(tmp_path):
    test_db = tmp_path / "test_bf.db"
    bf = HistoricalBackfiller(db_path=str(test_db))
    conn = sqlite3.connect(str(test_db))
    bf.init_schema(conn)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='backfill_log';")
    assert cur.fetchone() is not None

    cur.execute("PRAGMA table_info(backfill_log);")
    cols = {row[1] for row in cur.fetchall()}
    assert "coverage_status" in cols
    assert "first_available_date" in cols
    assert "last_available_date" in cols
    assert "bars_count" in cols
    assert "missing_sessions" in cols
    assert "provider" in cols
    assert "last_checked_at" in cols
    conn.close()


def test_missing_sessions_estimation():
    # 5 business days with 5 actual bars -> 0 missing
    assert estimate_missing_sessions("2026-01-05", "2026-01-09", 5) == 0
    # Empty inputs
    assert estimate_missing_sessions("", "", 0) == 0


def test_all_time_backfiller_run_with_mock(tmp_path):
    test_db = tmp_path / "test_bf_run.db"

    # 1. Populate test universe with 5 instruments
    mgr = UniverseManager(db_path=str(test_db))
    instruments = [
        Instrument("TEST1", "Q", "Test 1 Inc", "CS", False),
        Instrument("TEST2", "N", "Test 2 Corp", "CS", False),
        Instrument("TEST3", "P", "Test 3 ETF", "ETF", True),
        Instrument("TEST4", "Q", "Test 4 Ltd", "ADR", False),
        Instrument("TEST5", "Q", "Test 5 Inc", "CS", False)
    ]
    mgr.sync_universe(instruments)

    # 2. Setup mock data provider with 1,000 bars (spanning multiple years) for TEST1, TEST2, TEST3
    dates = pd.date_range("2022-01-03", periods=1000, freq="B")
    mock_data = {}
    for sym in ["TEST1", "TEST2", "TEST3"]:
        df = pd.DataFrame({
            "open": [100.0] * 1000,
            "high": [105.0] * 1000,
            "low": [95.0] * 1000,
            "close": [102.0] * 1000,
            "adj_close": [102.0] * 1000,
            "volume": [1000000.0] * 1000
        }, index=dates)
        mock_data[sym] = df

    mock_provider = MockDataProvider(data_dict=mock_data)
    loader = MarketDataLoader(db_path=str(test_db), provider=mock_provider)
    bf = HistoricalBackfiller(db_path=str(test_db), loader=loader)

    # 3. Run backfill with batch size 2 in all-time mode
    res = bf.run_backfill(
        batch_size=2,
        delay_between_tickers=0.0,
        all_time=True
    )

    assert res["total_targeted"] == 5
    assert res["processed"] == 5
    assert res["all_time_complete"] == 3  # TEST1, TEST2, TEST3
    assert res["no_data"] == 2            # TEST4, TEST5

    summary = bf.get_backfill_summary()
    assert summary["by_status"]["ALL_TIME_COMPLETE"] == 3
    assert summary["by_status"]["NO_DATA"] == 2

    # 4. Check historical bounds recorded in backfill_log
    conn = sqlite3.connect(str(test_db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM backfill_log WHERE symbol = 'TEST1';")
    row = cur.fetchone()
    assert row["coverage_status"] == STATUS_ALL_TIME_COMPLETE
    assert row["bars_count"] == 1000
    assert row["first_available_date"] == "2022-01-03"
    assert row["provider"] == "yfinance"
    conn.close()

    # 5. Test resumption: completed ALL_TIME_COMPLETE symbols should NOT be eligible
    eligible = bf.get_eligible_symbols(include_no_data=True)
    assert len(eligible) == 2
    symbols_left = [e[0] for e in eligible]
    assert "TEST4" in symbols_left
    assert "TEST5" in symbols_left

    eligible_skip_nodata = bf.get_eligible_symbols(include_no_data=False)
    assert len(eligible_skip_nodata) == 0

    # 6. Check all-time coverage report
    cov_rep = bf.get_all_time_coverage_report()
    assert len(cov_rep["by_asset_class"]) > 0
    assert len(cov_rep["top_histories"]) == 3
    assert cov_rep["top_histories"][0]["bars_count"] == 1000


def test_incremental_snapshot_refresh_and_preset_validation(tmp_path):
    test_db = tmp_path / "test_engine_val.db"
    dates = pd.date_range("2026-01-01", periods=250, freq="B")
    df = pd.DataFrame({
        "open": [100.0 + i * 0.1 for i in range(250)],
        "high": [105.0 + i * 0.1 for i in range(250)],
        "low": [95.0 + i * 0.1 for i in range(250)],
        "close": [104.0 + i * 0.1 for i in range(250)],
        "adj_close": [104.0 + i * 0.1 for i in range(250)],
        "volume": [1500000.0 for _ in range(250)]
    }, index=dates)

    mock_provider = MockDataProvider(data_dict={"MOCK1": df})
    loader = MarketDataLoader(db_path=str(test_db), provider=mock_provider)
    loader.update_symbol_daily("MOCK1", "US")

    engine = ScreenerEngine(db_path=str(test_db))

    # Refresh snapshots
    ref = engine.refresh_snapshots_incremental()
    assert ref["generated"] == 1
    assert ref["up_to_date"] == 0

    # Second refresh without new bars should say up_to_date == 1
    ref2 = engine.refresh_snapshots_incremental()
    assert ref2["generated"] == 0
    assert ref2["up_to_date"] == 1

    # Validate presets
    val = engine.validate_all_presets()
    assert val["eligible_instruments"] == 1
    assert val["total_snapshots"] == 1
    assert len(val["presets"]) == 8
    for name, info in val["presets"].items():
        assert info["status"] == "OK"
        assert "duration_ms" in info


def test_priority_ordering_and_all_time_progress_report(tmp_path):
    test_db = tmp_path / "test_prio.db"
    mgr = UniverseManager(db_path=str(test_db))
    instruments = [
        Instrument("ETF1", "P", "ETF 1", "ETF", True),
        Instrument("CS1", "N", "Common Stock 1", "CS", False),
        Instrument("ADR1", "Q", "ADR 1", "ADR", False),
        Instrument("PRF1", "N", "Preferred 1", "PRF", False),
        Instrument("CS2", "Q", "Common Stock 2", "CS", False),
    ]
    mgr.sync_universe(instruments)

    bf = HistoricalBackfiller(db_path=str(test_db))
    eligible = bf.get_eligible_symbols()

    # Priority order must place CS before ETF before ADR before PRF
    symbols_order = [e[0] for e in eligible]
    assert symbols_order[0] in ("CS1", "CS2")
    assert symbols_order[1] in ("CS1", "CS2")
    assert symbols_order[2] == "ETF1"
    assert symbols_order[3] == "ADR1"
    assert symbols_order[4] == "PRF1"

    # Progress report
    rep = bf.get_daily_progress_report()
    assert rep["total_instruments"] == 5
    assert rep["all_time_complete"] == 0
    assert rep["pending"] == 5
    assert "system_ram" in rep
    assert "system_swap" in rep
    assert "class_all_time_complete" in rep
