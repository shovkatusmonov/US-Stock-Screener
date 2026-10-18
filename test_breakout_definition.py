"""test_breakout_definition.py - breakout high20 ni ikki marta siljitmasligi (off-by-one) regressiyasi."""
import pandas as pd
import strategies as st


def _feat(close, high20):
    idx = pd.bdate_range("2026-01-01", periods=len(close))
    n = len(close)
    return pd.DataFrame({
        "close": close, "high20": high20, "raw_close": close,
        "dollar_vol20": [1e9] * n, "trend_up": [True] * n, "rs60": [0.1] * n,
        "atr14": [2.0] * n, "rel_vol": [2.0] * n,
    }, index=idx)


def _regime(f):
    return pd.Series(True, index=f.index)


def test_no_signal_when_close_below_current_high20():
    # high20 kecha 100 dan 120 ga ko'tarilgan; close=110 <= 120 -> breakout emas
    f = _feat([100.0, 100.0, 110.0], [100.0, 100.0, 120.0])
    assert not st.breakout_signal(f, _regime(f))["signal"].iloc[-1]


def test_signal_when_close_above_current_high20():
    f = _feat([100.0, 100.0, 121.0], [100.0, 100.0, 120.0])
    assert st.breakout_signal(f, _regime(f))["signal"].iloc[-1]
