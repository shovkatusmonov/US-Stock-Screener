"""
test_atr_execution_consistency.py - P0 Regressiya Testlari:
ATR, Stop-Loss va Ijro Narxlari Masshtabi Muvofiqligi (Priority P0).
"""
import pytest
import pandas as pd
import numpy as np

import features as ft
import strategies as st
from engine import Params, run_backtest
from risk_manager import RiskConfig, create_trade_plan


def test_atr_scale_consistency_with_large_dividend_factor():
    """
    Katta dividend orqaga surilishi (masalan, factor = 0.5) bo'lganda,
    ATR ijro narxi (raw open) masshtabiga to'liq mos kelishi va
    stop masofasi sun'iy torayib ketmasligi shart.
    """
    dates = pd.bdate_range("2023-01-02", periods=40)
    # Raw close = 100.0, kunlik tebranish (high - low) = 10.0 -> Raw ATR ~ 10.0
    # adj_close = 50.0 (factor = 0.5) -> Adjusted ATR ~ 5.0
    raw_df = pd.DataFrame({
        "open":      [100.0] * 40,
        "high":      [105.0] * 40,
        "low":       [95.0]  * 40,
        "close":     [100.0] * 40,
        "volume":    [2_000_000] * 40,
        "adj_close": [50.0]  * 40,
    }, index=dates)

    bench_df = raw_df.copy()

    f = ft.build_features(raw_df, bench_df)

    # 1. atr14 (adjusted ATR) ~ 5.0 bo'lishi kerak
    assert f["atr14"].iloc[-1] == pytest.approx(5.0, rel=0.05)

    # 2. atr_tradable (ijro uchun ATR) ~ 10.0 (raw narx masshtabida) bo'lishi kerak
    assert f["atr_tradable"].iloc[-1] == pytest.approx(10.0, rel=0.05)

    # 3. strategies._pack signali atr_tradable'ni uzatishi shart
    sig = st._pack(f, pd.Series([False]*39 + [True], index=dates))
    assert sig["atr"].iloc[-1] == pytest.approx(10.0, rel=0.05)

    # 4. engine.run_backtest ichida stop va pozitsiya hajmi tekshiruvi:
    # Keyingi sessiya (d41) da ochilish = 100.0, stop_atr = 2.0
    # Kutilgan stop = 100.0 - 2.0 * 10.0 = 80.0 (agar xato bo'lsa 90.0 bo'lib qolardi)
    dates_ext = pd.bdate_range("2023-01-02", periods=42)
    ohlc_ext = pd.DataFrame({
        "open":  [100.0] * 42,
        "high":  [105.0] * 40 + [102.0, 102.0],
        # d41 kuni low = 85.0. To'g'ri stop (80.0) ga tegmaydi, lekin xato stop (90.0) ga tegib ketardi!
        "low":   [95.0]  * 40 + [85.0, 95.0],
        "close": [100.0] * 40 + [98.0, 99.0],
        "volume":[2_000_000] * 42,
    }, index=dates_ext)

    sig_ext = pd.DataFrame({
        "signal": [False]*39 + [True] + [False]*2,
        "atr":    [float(sig["atr"].iloc[-1])] * 42,
        "rank":   [1.0] * 42,
    }, index=dates_ext)

    p = Params(initial_equity=10_000.0, risk_pct=0.01, stop_atr=2.0, cost_per_side=0.0, min_notional=1.0)
    trades, curve = run_backtest({"AAA": ohlc_ext}, {"AAA": sig_ext}, dates_ext, p)

    # d41 kuni low=85.0 bo'lganda stop urilmasligi kerak (chunki stop = 80.0)
    assert not trades.empty
    tr = trades.iloc[0]
    # Trade faqat oxirgi kunda (END) yopilishi kerak, SL bilan d41 da emas!
    assert tr["reason"] == "END"
    # Pozitsiya hajmi: 100$ risk / 20$ stop masofasi = 5 dona aksiya (agar xato bo'lsa 10 dona bo'lardi)
    assert tr["shares"] == pytest.approx(5.0, abs=0.5)


def test_parity_between_live_and_backtest_when_factor_one():
    """Joriy sessiyada (factor = 1.0) atr_tradable va atr14 aynan teng bo'lishi kerak."""
    dates = pd.bdate_range("2024-01-02", periods=30)
    raw_df = pd.DataFrame({
        "open":   np.linspace(100, 120, 30),
        "high":   np.linspace(102, 122, 30),
        "low":    np.linspace(98, 118, 30),
        "close":  np.linspace(101, 121, 30),
        "volume": [1_000_000] * 30,
        "adj_close": np.linspace(101, 121, 30), # factor = 1.0
    }, index=dates)

    f = ft.build_features(raw_df, raw_df)
    assert np.allclose(f["atr_tradable"].dropna(), f["atr14"].dropna())
