"""
test_backtest.py - Simulyator qoidalari, risk boshqaruvi va regressiya testlari.
"""
import numpy as np
import pandas as pd
import pytest
from engine import Params, run_backtest


def _make_dummy_data(days=10, price=100.0):
    dates = pd.date_range("2024-01-01", periods=days)
    ohlc = {
        "AAA": pd.DataFrame({
            "open": [price] * days,
            "high": [price * 1.02] * days,
            "low": [price * 0.98] * days,
            "close": [price] * days,
        }, index=dates)
    }
    signals = {
        "AAA": pd.DataFrame({
            "signal": [False] * days,
            "atr": [price * 0.05] * days,
            "rank": [1.0] * days,
        }, index=dates)
    }
    return ohlc, signals, dates


def test_initial_equity():
    ohlc, signals, dates = _make_dummy_data()
    p = Params(initial_equity=15_000.0)
    tr, curve = run_backtest(ohlc, signals, dates, p)
    assert len(tr) == 0
    assert curve.iloc[0]["equity"] == 15_000.0
    assert curve.iloc[-1]["equity"] == 15_000.0


def test_single_trade_execution():
    ohlc, signals, dates = _make_dummy_data(days=5)
    signals["AAA"].loc[dates[0], "signal"] = True
    p = Params(initial_equity=10_000.0)
    tr, curve = run_backtest(ohlc, signals, dates, p)
    assert len(tr) == 1
    assert tr.iloc[0]["ticker"] == "AAA"
    assert tr.iloc[0]["entry_date"] == dates[1]


def test_max_positions_limit():
    dates = pd.date_range("2024-01-01", periods=5)
    ohlc = {}
    signals = {}
    for i in range(12):
        t = f"TICK{i}"
        ohlc[t] = pd.DataFrame({"open": [50.0]*5, "high": [52.0]*5, "low": [48.0]*5, "close": [50.0]*5}, index=dates)
        signals[t] = pd.DataFrame({"signal": [True]+[False]*4, "atr": [2.0]*5, "rank": [float(i)]*5}, index=dates)
    p = Params(initial_equity=100_000.0, max_positions=8)
    tr, curve = run_backtest(ohlc, signals, dates, p)
    # Bir vaqtda ochiq pozitsiyalar 8 tadan oshmasligi kerak
    assert len(tr) == 8


def test_position_sizing_risk():
    ohlc, signals, dates = _make_dummy_data(days=5, price=100.0)
    signals["AAA"].loc[dates[0], "signal"] = True
    # risk_pct = 0.01 (100$), ATR = 5, stop_atr = 2 -> risk_per_share = 10$ + commission
    p = Params(initial_equity=10_000.0, risk_pct=0.01, stop_atr=2.0)
    tr, _ = run_backtest(ohlc, signals, dates, p)
    shares = tr.iloc[0]["shares"]
    assert 9.0 <= shares <= 10.5


def test_max_position_notional_cap():
    ohlc, signals, dates = _make_dummy_data(days=5, price=10.0)
    signals["AAA"].loc[dates[0], "signal"] = True
    # Agar risk bo'yicha hajm juda katta chiqsa, max_pos_pct (20%) bilan cheklanishi kerak
    p = Params(initial_equity=10_000.0, risk_pct=0.10, max_pos_pct=0.20)
    tr, _ = run_backtest(ohlc, signals, dates, p)
    fill = tr.iloc[0]["entry_price"] * (1 + p.cost_per_side)
    notional = tr.iloc[0]["shares"] * fill
    assert notional <= 2_000.01


def test_min_notional_skip():
    ohlc, signals, dates = _make_dummy_data(days=5, price=100.0)
    signals["AAA"].loc[dates[0], "signal"] = True
    p = Params(initial_equity=100.0, risk_pct=0.001, min_notional=50.0)
    tr, _ = run_backtest(ohlc, signals, dates, p)
    assert len(tr) == 0


def test_stop_loss_trigger():
    dates = pd.date_range("2024-01-01", periods=4)
    ohlc = {
        "AAA": pd.DataFrame({
            "open":  [100.0, 100.0, 89.0, 90.0],
            "high":  [102.0, 101.0, 91.0, 92.0],
            "low":   [98.0,   95.0, 88.0, 89.0],  # 2-kunda low <= 90 (Stop)
            "close": [100.0,  99.0, 89.0, 90.0],
        }, index=dates)
    }
    signals = {
        "AAA": pd.DataFrame({
            "signal": [True, False, False, False],
            "atr": [5.0]*4, "rank": [1.0]*4
        }, index=dates)
    }
    p = Params(initial_equity=10_000.0, stop_atr=2.0)
    tr, _ = run_backtest(ohlc, signals, dates, p)
    assert len(tr) == 1
    assert tr.iloc[0]["reason"] in ["SL", "SL_GAP"]


def test_take_profit_trigger():
    dates = pd.date_range("2024-01-01", periods=4)
    ohlc = {
        "AAA": pd.DataFrame({
            "open":  [100.0, 100.0, 140.0, 140.0],
            "high":  [102.0, 102.0, 145.0, 142.0], # Target = 135 ga tegadi
            "low":   [98.0,   99.0, 138.0, 139.0],
            "close": [100.0, 101.0, 140.0, 140.0],
        }, index=dates)
    }
    signals = {
        "AAA": pd.DataFrame({
            "signal": [True, False, False, False],
            "atr": [5.0]*4, "rank": [1.0]*4
        }, index=dates)
    }
    p = Params(initial_equity=10_000.0, tp_pct=0.35)
    tr, _ = run_backtest(ohlc, signals, dates, p)
    assert len(tr) == 1
    assert tr.iloc[0]["reason"] in ["TP", "TP_GAP"]


def test_max_hold_time_exit():
    dates = pd.date_range("2024-01-01", periods=8)
    ohlc = {
        "AAA": pd.DataFrame({
            "open":  [100.0]*8,
            "high":  [101.0]*8,
            "low":   [99.0]*8,
            "close": [100.0]*8,
        }, index=dates)
    }
    signals = {
        "AAA": pd.DataFrame({
            "signal": [True] + [False]*7,
            "atr": [5.0]*8, "rank": [1.0]*8
        }, index=dates)
    }
    p = Params(initial_equity=10_000.0, max_hold=3)
    tr, _ = run_backtest(ohlc, signals, dates, p)
    assert len(tr) == 1
    assert tr.iloc[0]["reason"] == "TIME"
    assert tr.iloc[0]["hold_days"] == 3


def test_deterministic_signal_ranking():
    dates = pd.date_range("2024-01-01", periods=4)
    ohlc = {
        "LOW_RANK": pd.DataFrame({"open": [10.0]*4, "high": [11.0]*4, "low": [9.0]*4, "close": [10.0]*4}, index=dates),
        "HIGH_RANK": pd.DataFrame({"open": [10.0]*4, "high": [11.0]*4, "low": [9.0]*4, "close": [10.0]*4}, index=dates),
    }
    signals = {
        "LOW_RANK": pd.DataFrame({"signal": [True, False, False, False], "atr": [1.0]*4, "rank": [0.1]*4}, index=dates),
        "HIGH_RANK": pd.DataFrame({"signal": [True, False, False, False], "atr": [1.0]*4, "rank": [0.9]*4}, index=dates),
    }
    p = Params(initial_equity=10_000.0, max_positions=1)
    tr, _ = run_backtest(ohlc, signals, dates, p)
    assert len(tr) == 1
    assert tr.iloc[0]["ticker"] == "HIGH_RANK"


def test_cash_cannot_go_negative():
    dates = pd.date_range("2024-01-01", periods=5)
    ohlc = {
        "AAA": pd.DataFrame({"open": [100.0]*5, "high": [105.0]*5, "low": [95.0]*5, "close": [100.0]*5}, index=dates)
    }
    signals = {
        "AAA": pd.DataFrame({"signal": [True, False, False, False, False], "atr": [5.0]*5, "rank": [1.0]*5}, index=dates)
    }
    p = Params(initial_equity=50.0, min_notional=10.0)
    tr, curve = run_backtest(ohlc, signals, dates, p)
    assert (curve["equity"] >= 0).all()


def test_commission_and_slippage_applied():
    dates = pd.date_range("2024-01-01", periods=4)
    ohlc = {
        "AAA": pd.DataFrame({
            "open":  [100.0, 100.0, 100.0, 100.0],
            "high":  [101.0, 101.0, 101.0, 101.0],
            "low":   [99.0,   99.0,  99.0,  99.0],
            "close": [100.0, 100.0, 100.0, 100.0],
        }, index=dates)
    }
    signals = {
        "AAA": pd.DataFrame({
            "signal": [True, False, False, False],
            "atr": [5.0]*4, "rank": [1.0]*4
        }, index=dates)
    }
    p = Params(initial_equity=10_000.0, cost_per_side=0.001, max_hold=1)
    tr, _ = run_backtest(ohlc, signals, dates, p)
    assert len(tr) == 1
    # Narx o'zgarmasa ham xarajat tufayli pnl manfiy bo'lishi shart
    assert tr.iloc[0]["pnl"] < 0


def test_end_of_data_exit():
    ohlc, signals, dates = _make_dummy_data(days=3)
    signals["AAA"].loc[dates[0], "signal"] = True
    p = Params(initial_equity=10_000.0)
    tr, _ = run_backtest(ohlc, signals, dates, p)
    assert len(tr) == 1
    assert tr.iloc[0]["reason"] == "END"


def test_zero_division_guard():
    dates = pd.date_range("2024-01-01", periods=4)
    ohlc = {
        "ZERO_ATR": pd.DataFrame({"open": [10.0]*4, "high": [10.0]*4, "low": [10.0]*4, "close": [10.0]*4}, index=dates)
    }
    signals = {
        "ZERO_ATR": pd.DataFrame({"signal": [True, False, False, False], "atr": [0.0]*4, "rank": [1.0]*4}, index=dates)
    }
    p = Params(initial_equity=10_000.0)
    tr, _ = run_backtest(ohlc, signals, dates, p)
    assert len(tr) == 0


def test_gap_down_execution():
    """Ochilish narxi stopdan past bo'lsa, real ochilishda (SL_GAP) chiqishi shart."""
    ohlc = {
        "AAA": pd.DataFrame({
            "open":  [100.0, 100.0, 80.0],
            "high":  [105.0, 105.0, 85.0],
            "low":   [98.0,  95.0,  75.0],
            "close": [102.0, 101.0, 82.0]
        }, index=pd.date_range("2024-01-01", periods=3))
    }
    signals = {
        "AAA": pd.DataFrame({
            "signal": [True, False, False],
            "atr":    [5.0,  5.0,   5.0],
            "rank":   [1.0,  1.0,   1.0]
        }, index=pd.date_range("2024-01-01", periods=3))
    }
    p = Params(initial_equity=10000.0, stop_atr=2.0)
    tr, _ = run_backtest(ohlc, signals, ohlc["AAA"].index, p)
    assert len(tr) == 1
    assert tr.iloc[0]["reason"] == "SL_GAP"
    assert tr.iloc[0]["exit_price"] == 80.0


def test_stop_before_tp_priority():
    """Bir kunda ham Stop, ham Target tegsa, konservativ Stop birinchi bo'lishi shart."""
    ohlc = {
        "AAA": pd.DataFrame({
            "open":  [100.0, 100.0, 100.0],
            "high":  [105.0, 105.0, 150.0],
            "low":   [98.0,  95.0,   80.0],
            "close": [102.0, 101.0, 105.0]
        }, index=pd.date_range("2024-01-01", periods=3))
    }
    signals = {
        "AAA": pd.DataFrame({
            "signal": [True, False, False],
            "atr":    [5.0,  5.0,   5.0],
            "rank":   [1.0,  1.0,   1.0]
        }, index=pd.date_range("2024-01-01", periods=3))
    }
    p = Params(initial_equity=10000.0, stop_atr=2.0, tp_pct=0.35)
    tr, _ = run_backtest(ohlc, signals, ohlc["AAA"].index, p)
    assert len(tr) == 1
    assert tr.iloc[0]["reason"] == "SL"
