#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
US Stock Screener - next-stage trading logic audit.
Run from the project root:
    .\.venv\Scripts\python.exe .\audit_trading_logic.py
Read-only: does not modify project files or database.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PASS = 0
WARN = 0
FAIL = 0

def report(status: str, name: str, detail: str = ""):
    global PASS, WARN, FAIL
    if status == "PASS":
        PASS += 1
    elif status == "WARN":
        WARN += 1
    else:
        FAIL += 1
    print(f"[{status:4}] {name}" + (f" — {detail}" if detail else ""))

def load_module(name):
    try:
        return importlib.import_module(name)
    except Exception as e:
        report("FAIL", f"Import {name}.py", f"{type(e).__name__}: {e}")
        return None

def syntax_check():
    for name in ("features.py", "strategies.py", "engine.py"):
        p = ROOT / name
        if not p.exists():
            report("FAIL", f"Syntax {name}", "file missing")
            continue
        try:
            ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(p))
            report("PASS", f"Syntax {name}")
        except Exception as e:
            report("FAIL", f"Syntax {name}", f"{type(e).__name__}: {e}")

def make_prices(n=260, start="2022-01-03", seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.012, n))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0.001, 0.015, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0.001, 0.015, n))
    vol = rng.integers(1_000_000, 4_000_000, n).astype(float)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": vol, "adj_close": close * 0.98,
    }, index=idx)

def test_adjust_ohlc(features):
    df = make_prices(80)
    # Deliberately missing early adjustment factors: verify no future factor leaks backward.
    df.loc[df.index[:10], "adj_close"] = np.nan
    try:
        out = features.adjust_ohlc(df)
        if out[["open", "high", "low", "close"]].isna().any().any():
            report("WARN", "adjust_ohlc missing adj_close", "NaN OHLC remains; inspect intended fallback policy")
        else:
            early_close = out["close"].iloc[0]
            raw_close = df["close"].iloc[0]
            if np.isclose(early_close, raw_close, rtol=1e-5):
                report("PASS", "adjust_ohlc future-factor risk", "missing early adjustment factors cleanly use 1.0 (no future leakage)")
            else:
                report("WARN", "adjust_ohlc future-factor risk", "missing early adjustment factors are filled from later dates")
    except Exception as e:
        report("FAIL", "adjust_ohlc synthetic test", f"{type(e).__name__}: {e}")

def test_features_prefix_invariance(features):
    df = make_prices(260)
    bench = make_prices(260, seed=9)
    try:
        full = features.build_features(df, bench)
        cut = 220
        prefix = features.build_features(df.iloc[:cut], bench.iloc[:cut])
        cols = [c for c in ("sma20", "sma50", "sma200", "atr14", "rsi14",
                            "rs60", "high20", "low20", "rel_vol", "dollar_vol20")
                if c in full.columns and c in prefix.columns]
        a = full.iloc[:cut][cols].to_numpy(dtype=float)
        b = prefix[cols].to_numpy(dtype=float)
        same = np.allclose(a, b, equal_nan=True, rtol=1e-10, atol=1e-10)
        if same:
            report("PASS", "Feature prefix-invariance", "future rows did not alter prior feature values in synthetic data")
        else:
            report("FAIL", "Feature prefix-invariance", "features for earlier dates change when future rows are appended")
    except Exception as e:
        report("FAIL", "Feature prefix-invariance", f"{type(e).__name__}: {e}")

def test_strategy_signal_shape(features, strategies):
    df = make_prices(280)
    bench = make_prices(280, seed=10)
    try:
        f = features.build_features(df, bench)
        regime = strategies.regime_series(f)
        for name, fn in strategies.STRATEGIES.items():
            sig = fn(f, regime)
            needed = {"signal", "atr", "rank"}
            if not needed.issubset(sig.columns):
                report("FAIL", f"Strategy schema {name}", f"missing {sorted(needed - set(sig.columns))}")
            elif not sig.index.equals(f.index):
                report("FAIL", f"Strategy index {name}", "signal index differs from features index")
            else:
                report("PASS", f"Strategy schema/index {name}")
    except Exception as e:
        report("FAIL", "Strategy synthetic test", f"{type(e).__name__}: {e}")

def test_engine_entry_and_stop_priority(engine):
    # Day 0 signal -> entry at day 1 open. Day 1 touches both stop and target;
    # conservative policy should close at stop first.
    dates = pd.bdate_range("2024-01-02", periods=4)
    px = pd.DataFrame({
        "open":  [100.0, 100.0, 100.0, 100.0],
        "high":  [101.0, 140.0, 101.0, 101.0],
        "low":   [99.0,  70.0,  99.0,  99.0],
        "close": [100.0, 110.0, 100.0, 100.0],
        "volume":[1e6]*4,
    }, index=dates)
    sig = pd.DataFrame({
        "signal": [True, False, False, False],
        "atr":    [10.0, 10.0, 10.0, 10.0],
        "rank":   [1.0,  0.0,  0.0,  0.0],
    }, index=dates)
    try:
        p = engine.Params(initial_equity=10_000, risk_pct=0.01, max_positions=2,
                          max_pos_pct=0.5, stop_atr=2.0, tp_pct=0.20,
                          max_hold=10, cost_per_side=0.0, min_notional=1.0)
        trades, curve = engine.run_backtest({"AAA": px}, {"AAA": sig}, dates, p)
        if trades.empty:
            report("FAIL", "Engine stop-priority scenario", "no trade generated")
            return
        tr = trades.iloc[0]
        if tr["entry_date"] != dates[1]:
            report("FAIL", "Signal-to-next-open timing", f"entry date was {tr['entry_date']}, expected {dates[1]}")
        else:
            report("PASS", "Signal-to-next-open timing")
        if tr["reason"] == "SL":
            report("PASS", "Same-day SL/TP priority", "stop wins as specified")
        else:
            report("FAIL", "Same-day SL/TP priority", f"expected SL, got {tr['reason']}")
    except Exception as e:
        report("FAIL", "Engine synthetic test", f"{type(e).__name__}: {e}")

def static_logic_checks():
    p = ROOT / "engine.py"
    if not p.exists():
        return
    src = p.read_text(encoding="utf-8-sig")
    if 'if l <= pos["stop"]' in src and 'elif h >= pos["target"]' in src:
        report("PASS", "Intraday SL before main TP", "ordering in source matches conservative rule")
    else:
        report("FAIL", "Intraday SL before main TP", "expected stop-first ordering not found")
    if 'close_shares(t, d, i, half, pos["tp1_target"], "TP1", is_partial=True)' in src:
        if '"SL_BE_AFTER_TP1"' in src:
            report("PASS", "TP1 then breakeven-stop same day",
                   "after TP1, a low touching the newly raised stop closes the remainder conservatively")
        else:
            report("WARN", "TP1 then breakeven-stop same day",
                   "after TP1, the day's low is not rechecked against the newly raised stop")
    if 'close_shares(t, last_d, last_i' in src and '"END"' in src:
        if 'exit_fill = exit_raw * (1 - p.cost_per_side)' in src and 'curve[-1] = (last_d, cash, 0.0)' in src:
            report("PASS", "Final open-position handling",
                   "terminal liquidation uses last observed close, exit cost is applied, final equity equals cash")
        else:
            report("WARN", "Final open-position handling",
                   "verify terminal liquidation fees and final equity convention")
    if "entry_raw" in src and "entry_fill" in src and "risk_per_share = fill - stop" in src:
        report("PASS", "ATR/stop price basis",
               "risk/share includes entry fill cost; stop trigger remains at raw market price by design")

def main():
    print("=" * 76)
    print("US STOCK SCREENER — TRADING LOGIC AUDIT (READ-ONLY)")
    print(f"Project: {ROOT}")
    print("=" * 76)
    syntax_check()
    features = load_module("features")
    strategies = load_module("strategies")
    engine = load_module("engine")
    if features:
        test_adjust_ohlc(features)
        test_features_prefix_invariance(features)
    if features and strategies:
        test_strategy_signal_shape(features, strategies)
    if engine:
        test_engine_entry_and_stop_priority(engine)
    static_logic_checks()
    print("-" * 76)
    print(f"Jami: PASS={PASS}, WARN={WARN}, FAIL={FAIL}")
    print("Eslatma: bu skript butun loyihani yoki strategiyaning foydaliligini tasdiqlamaydi.")
    print("WARN — qo'lda tekshirish/tuzatish kerak; FAIL — aniq muvaffaqiyatsiz tekshiruv.")
    return 1 if FAIL else 0

if __name__ == "__main__":
    raise SystemExit(main())
