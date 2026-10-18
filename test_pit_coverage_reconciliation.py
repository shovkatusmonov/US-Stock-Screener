"""
test_pit_coverage_reconciliation.py - Strict unit tests reconciling PIT coverage definitions (94.87% vs 93.96%)
and verifying confidence weighting in ScoringEngine.
"""
import sqlite3
import pytest
import pandas as pd
import numpy as np

from audit_suite import pit_window_coverage
from sp500_history import SP500HistoryManager
from scoring_engine import ScoringEngine, ScoringWeights


@pytest.fixture
def screener_db_conn():
    conn = sqlite3.connect("data/screener.db")
    yield conn
    conn.close()


def test_pit_coverage_2020_canonical_backtest_window(screener_db_conn):
    """
    Verifies that for the Canonical Backtest period (2020-01-01 to 2026-10-15):
    Both pit_window_coverage and evaluate_date_matrix_coverage produce 100% identical numbers:
    815,224 filled / 859,319 expected = 94.87%.
    """
    hm = SP500HistoryManager("data/screener.db")
    
    # 1. audit_suite.pit_window_coverage
    cov_df = pit_window_coverage(screener_db_conn, hm, start_date="2020-01-01")
    exp_audit = int(cov_df["expected"].sum())
    have_audit = int(cov_df["have"].sum())
    pct_audit = have_audit / exp_audit

    # 2. sp500_history.evaluate_date_matrix_coverage
    cur_sp = set(pd.read_csv("data/universe_sp500.csv")["symbol"].dropna())
    spy_dates_2020 = [
        r[0][:10] for r in screener_db_conn.execute(
            "SELECT date FROM prices WHERE ticker = 'SPY' AND date >= '2020-01-01' ORDER BY date"
        ).fetchall()
    ]
    matrix_cov = hm.evaluate_date_matrix_coverage(spy_dates_2020, cur_sp, screener_db_conn)
    exp_matrix = matrix_cov["total_slots"]
    have_matrix = matrix_cov["filled_slots"]
    pct_matrix = matrix_cov["coverage_pct"]

    # Exact equality checks
    assert exp_audit == 859319
    assert have_audit == 815224
    assert exp_matrix == 859319
    assert have_matrix == 815224
    assert exp_audit == exp_matrix
    assert have_audit == have_matrix
    assert round(pct_audit * 100, 2) == 94.87
    assert round(pct_matrix * 100, 2) == 94.87


def test_pit_coverage_2019_full_system_warmup_window(screener_db_conn):
    """
    Verifies that for the Full History period including 2019 SMA-200 warmup (2019-01-02 to 2026-10-15):
    Both methods yield 926,805 filled / 986,345 expected = 93.96%.
    """
    hm = SP500HistoryManager("data/screener.db")
    cur_sp = set(pd.read_csv("data/universe_sp500.csv")["symbol"].dropna())

    spy_dates_all = [
        r[0][:10] for r in screener_db_conn.execute(
            "SELECT date FROM prices WHERE ticker = 'SPY' AND date >= '2019-01-02' ORDER BY date"
        ).fetchall()
    ]
    matrix_all = hm.evaluate_date_matrix_coverage(spy_dates_all, cur_sp, screener_db_conn)

    assert matrix_all["total_slots"] == 986345
    assert matrix_all["filled_slots"] == 926805
    assert round(matrix_all["coverage_pct"] * 100, 2) == 93.96

    # Verify the difference is exactly the 2019 warmup year
    slots_2019_exp = 986345 - 859319
    slots_2019_have = 926805 - 815224
    assert slots_2019_exp == 127026
    assert slots_2019_have == 111581
    assert round(slots_2019_have / slots_2019_exp * 100, 2) == 87.84


def test_confidence_weighting_eliminates_inflated_confidence():
    """
    Verifies that when Fundamental (25%) and News (15%) data are missing:
    Overall confidence is properly weighted (~56.5%) and NOT artificially inflated to 93.3%.
    """
    engine = ScoringEngine()
    dates = pd.bdate_range("2024-01-01", periods=300)
    df = pd.DataFrame(index=dates)
    df["close"] = 100.0
    df["raw_close"] = 100.0
    df["sma20"] = 100.0
    df["sma50"] = 100.0
    df["sma200"] = 100.0
    df["trend_up"] = True
    df["rsi14"] = 55.0
    df["high20"] = 100.0
    df["rel_vol"] = 1.0
    df["dollar_vol20"] = 30_000_000.0
    df["atr14"] = 2.0
    df["atr_tradable"] = 2.0
    df["atr_pct"] = 0.02
    df["rs60"] = 0.50

    b_df = df.copy()
    trd = pd.DataFrame({"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1e6}, index=dates)

    # Missing fundamental and news
    res = engine.evaluate_stock("TEST", df, trd, b_df, fundamental_data=None, news_data=None)
    assert res.score_coverage == 0.60  # Technical (25) + Regime (10) + Risk (25) = 60%
    # Overall confidence should be weighted: 0.95*0.25 + 0.90*0.10 + 0.95*0.25 = 0.565
    assert np.isclose(res.overall_confidence, 0.565, atol=1e-3)
    assert res.overall_confidence < 0.65  # Strictly not 93.3%!
