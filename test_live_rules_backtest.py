"""test_live_rules_backtest.py - engine.Params.live() qoidalari jonli tizim bilan mosligi."""
import pandas as pd
from engine import Params, run_backtest


def _mk(open_px, close_sig=100.0, n=4, atr=2.0, tickers=("AAA",)):
    dates = pd.date_range("2024-01-01", periods=n)
    ohlc, sig = {}, {}
    for t in tickers:
        o = [close_sig, open_px] + [open_px] * (n - 2)
        ohlc[t] = pd.DataFrame({"open": o, "high": [x * 1.01 for x in o],
                                "low": [x * 0.99 for x in o],
                                "close": [close_sig] + [open_px] * (n - 1)}, index=dates)
        sig[t] = pd.DataFrame({"signal": [True] + [False] * (n - 1), "atr": [atr] * n,
                               "rank": [1.0] * n}, index=dates)
    return dates, ohlc, sig


def test_defaults_keep_old_behaviour():
    dates, ohlc, sig = _mk(open_px=108.0)  # +8% gap
    tr, _ = run_backtest(ohlc, sig, dates, Params())
    assert len(tr) == 1


def test_gap_over_3pct_cancelled():
    dates, ohlc, sig = _mk(open_px=104.0)
    tr, cv = run_backtest(ohlc, sig, dates, Params.live())
    assert tr.empty and cv.attrs["skipped"].get("GAP") == 1


def test_gap_within_3pct_allowed():
    dates, ohlc, sig = _mk(open_px=102.0)
    tr, _ = run_backtest(ohlc, sig, dates, Params.live())
    assert len(tr) == 1


def test_rr_below_minimum_cancelled():
    # ATR katta -> stop uzoq -> R/R < 1.5
    dates, ohlc, sig = _mk(open_px=100.0, atr=15.0)
    tr, cv = run_backtest(ohlc, sig, dates, Params.live())
    assert tr.empty and cv.attrs["skipped"].get("RR") == 1


def test_heat_cap_limits_open_positions():
    tick = tuple(f"T{i}" for i in range(8))
    dates, ohlc, sig = _mk(open_px=100.0, tickers=tick, n=5)
    tr, cv = run_backtest(ohlc, sig, dates, Params.live(initial_equity=100_000.0, max_pos_pct=0.2))
    assert tr["ticker"].nunique() == 5
    assert cv.attrs["skipped"].get("HEAT") == 3


def test_integer_shares():
    dates, ohlc, sig = _mk(open_px=101.0)
    tr, _ = run_backtest(ohlc, sig, dates, Params.live())
    assert float(tr.iloc[0]["shares"]).is_integer()
