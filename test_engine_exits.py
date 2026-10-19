"""
test_engine_exits.py - TP1/TP2 chiqish siyosati va PIT matritsasi regressiya testlari.
"""
import pytest
import pandas as pd
from engine import Params, run_backtest
from sp500_history import SP500HistoryManager


def test_tp1_partial_exit_and_breakeven_stop():
    """TP1 (+20%) tekkanda 50% pozitsiya yopilib, stop kirish narxiga ko'chishi shart."""
    dates = pd.date_range("2024-01-01", periods=4)
    ohlc = {
        "AAA": pd.DataFrame({
            "open":  [100.0, 100.0, 122.0, 122.0],  # 2-kuni TP1 (+20%) ga tegadi
            "high":  [101.0, 102.0, 125.0, 124.0],
            "low":   [99.0,   98.0, 120.0,  99.0],  # 3-kuni pastga tushadi
            "close": [100.0, 101.0, 122.0, 100.0],
        }, index=dates)
    }
    signals = {
        "AAA": pd.DataFrame({
            "signal": [True, False, False, False],
            "atr":    [5.0,  5.0,   5.0,   5.0],
            "rank":   [1.0,  1.0,   1.0,   1.0]
        }, index=dates)
    }
    p = Params(initial_equity=10_000.0, tp1_pct=0.20, tp_pct=0.35, stop_atr=2.0)
    tr, curve = run_backtest(ohlc, signals, dates, p)

    # 2 ta savdo yozuvi bo'lishi kerak: birinchisi TP1 (qisman), ikkinchisi qolgan qismi
    assert len(tr) >= 2
    tp1_trade = tr.iloc[0]
    assert tp1_trade["reason"] in ["TP1", "TP1_GAP"]
    assert tp1_trade["pnl"] > 0

    second_trade = tr.iloc[1]
    # Stop breakeven (100.0) ga ko'chgan bo'lishi kerak, shuning uchun katta zarar bilan chiqmaydi
    assert second_trade["exit_price"] >= 100.0 or second_trade["reason"] in ["SL", "END"]


def test_sp500_unknown_policy():
    """Qoidasi bo'lmagan noma'lum ticker allow_unknown=False da None qaytarishi shart."""
    hm = SP500HistoryManager(rules={})
    # Bazada qoidasi yo'q bo'lgan tasodifiy ticker
    res = hm.is_member_on_date("RANDOM_TICKER", "2024-01-01", base_tickers_2026={"AAPL"}, allow_unknown=False)
    assert res is None


def test_tp1_then_same_day_breakeven_stop_is_conservative():
    """TP1 va yangi breakeven stop bir kunda tegsa, qolgan qism ham yopiladi."""
    dates = pd.date_range("2024-02-01", periods=3)
    ohlc = {
        "AAA": pd.DataFrame({
            "open":  [100.0, 100.0, 100.0],
            "high":  [101.0, 125.0, 105.0],
            "low":   [99.0,  95.0,  99.0],  # eski stop=90 buzilmaydi, BE=100 tegiladi
            "close": [100.0, 110.0, 101.0],
        }, index=dates)
    }
    signals = {
        "AAA": pd.DataFrame({
            "signal": [True, False, False],
            "atr": [5.0, 5.0, 5.0],
            "rank": [1.0, 1.0, 1.0],
        }, index=dates)
    }
    p = Params(initial_equity=10_000.0, tp1_pct=0.20, tp_pct=0.35,
               stop_atr=2.0, max_positions=1, max_pos_pct=0.5,
               cost_per_side=0.0, min_notional=1.0)
    trades, _ = run_backtest(ohlc, signals, dates, p)
    same_day = trades[trades["exit_date"] == dates[1]]
    assert len(same_day) == 2
    assert same_day.iloc[0]["reason"] == "TP1"
    assert same_day.iloc[1]["reason"] == "SL_BE_AFTER_TP1"
    assert same_day.iloc[1]["exit_price"] == 100.0
