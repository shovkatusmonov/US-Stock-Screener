"""
run_final_audit.py - To'liq yakuniy tekshiruv va taqqoslash auditi.
1. Regressiya va arxitektura testlari (Gap-Down, SL ustunligi).
2. Pytest to'liq to'plami.
3. DEV, VAL va TEST davrlarida V0 vs V2 vs SPY natijalari.
"""
import sqlite3
import pytest
import numpy as np
import pandas as pd
from engine import Params, run_backtest
import features as ft
import strategies as st
from run_backtest import equity_stats, trade_stats, load_all, pct, num

PERIODS = {
    "DEV":  ("2020-01-01", "2022-12-31"),
    "VAL":  ("2023-01-01", "2024-12-31"),
    "TEST": ("2025-01-01", "2100-01-01"),
}

def verify_system_invariants():
    print("=" * 80)
    print("1. ARXITEKTURA VA REGRESSIYA TESTLARI AUDITI")
    print("=" * 80)
    
    # Test A: Gap-Down ijrosi (0-kun: signal, 1-kun: kirish, 2-kun: gap-down)
    ohlc_test = {
        "TEST": pd.DataFrame({
            "open":  [100.0, 100.0, 80.0],
            "high":  [105.0, 105.0, 85.0],
            "low":   [98.0,  95.0,  75.0],
            "close": [102.0, 101.0, 82.0]
        }, index=pd.date_range("2024-01-01", periods=3))
    }
    signals_test = {
        "TEST": pd.DataFrame({
            "signal": [True,  False, False],
            "atr":    [5.0,   5.0,   5.0],
            "rank":   [1.0,   1.0,   1.0]
        }, index=pd.date_range("2024-01-01", periods=3))
    }
    # Stop = 100 - 2*5 = 90. 2-kuni Open = 80 (< 90). Chiqish 80 da bo'lishi shart!
    p = Params(initial_equity=10000.0, stop_atr=2.0)
    tr, _ = run_backtest(ohlc_test, signals_test, ohlc_test["TEST"].index, p)
    assert len(tr) == 1, "Savdo yopilmadi!"
    assert tr.iloc[0]["reason"] == "SL_GAP", f"Noto'g'ri sabab: {tr.iloc[0]['reason']}"
    assert tr.iloc[0]["exit_price"] == 80.0, f"Gap-Down xatosi: {tr.iloc[0]['exit_price']} != 80.0"
    print("[OK] Test A: Gap-Down ochilish narxida (SL_GAP) to'g'ri bajarilmoqda.")

    # Test B: Stop va TP bir kunda tegishi (Stop ustunligi)
    ohlc_same_day = {
        "TEST": pd.DataFrame({
            "open":  [100.0, 100.0, 100.0],
            "high":  [105.0, 105.0, 120.0], # Target = 110 ga yetdi
            "low":   [98.0,  95.0,   85.0], # Stop = 90 ga yetdi
            "close": [102.0, 101.0, 105.0]
        }, index=pd.date_range("2024-01-01", periods=3))
    }
    tr_same, _ = run_backtest(ohlc_same_day, signals_test, ohlc_same_day["TEST"].index, p)
    assert len(tr_same) == 1
    assert tr_same.iloc[0]["reason"] == "SL", "Stop ustunligi buzildi (TP hisoblangan)!"
    print("[OK] Test B: Stop va TP bir kunda tekkanda Stop ustunligi to'liq ta'minlangan.")
    
    # Test C: Pytest ishga tushirish
    print("\n2. PYTEST BIRLIK TESTLARI:")
    code = pytest.main(["-q"])
    assert code == 0, "Pytest testlarida xatolik aniqlandi!"
    print("[OK] Barcha 24 ta mavjud pytest testlari 100% muvaffaqiyatli o'tdi.")

def evaluate_models():
    conn = sqlite3.connect("data/screener.db")
    feats, ohlc, regime, bench_f = load_all(conn)
    conn.close()
    
    # V2 uchun to'g'rilangan hajm
    feats_v2 = {}
    for t, f in feats.items():
        df_f = f.copy()
        shifted_avg = df_f["avg_vol20"].shift(1)
        raw_vol = df_f["avg_vol20"] * df_f["rel_vol"]
        df_f["rel_vol"] = raw_vol / shifted_avg
        feats_v2[t] = df_f

    configs = {
        "V0: Baza (TP 10%, Hold 15)": (feats, Params(tp_pct=0.10, max_hold=15)),
        "V2: Trend Runner (TP 35%, Hold 35)": (feats_v2, Params(tp_pct=0.35, max_hold=35))
    }
    
    dates = bench_f.index
    bench_close = bench_f["close"]

    print("\n" + "=" * 80)
    print("3. DEV, VAL VA TEST YAKUNIY TAQQOSLASH JADVALLARI")
    print("=" * 80)

    for strat_name in ["breakout", "pullback"]:
        print(f"\n##############################################################################")
        print(f"STRATEGIYA: {strat_name.upper()}")
        print(f"##############################################################################")
        
        runs = {}
        for cname, (f_dict, p) in configs.items():
            fn = st.STRATEGIES[strat_name]
            signals = {t: fn(f_dict[t], regime) for t in f_dict}
            tr, curve = run_backtest(ohlc, signals, dates, p)
            runs[cname] = (tr, curve)
            
        for p_name, (a, b) in PERIODS.items():
            sp = bench_close.loc[a:b]
            bs = equity_stats(sp)
            print(f"\n--- DAVR: {p_name} ({sp.index[0].date()} -> {sp.index[-1].date()}) ---")
            header = f"{'Model':<35} | {'Daromad':>8} | {'CAGR':>8} | {'Sharpe':>7} | {'Sortino':>8} | {'MaxDD':>8} | {'Trades':>6} | {'WinRate':>7} | {'PF':>5}"
            print("-" * len(header))
            print(header)
            print("-" * len(header))
            
            # SPY qatori
            print(f"{'SPY Buy & Hold':<35} | {pct(bs['total']):>8} | {pct(bs['cagr']):>8} | {num(bs['sharpe']):>7} | {num(bs['sortino']):>8} | {pct(bs['maxdd']):>8} | {'-':>6} | {'-':>7} | {'-':>5}")
            
            for cname, (tr, curve) in runs.items():
                eq = curve["equity"].loc[a:b]
                t_sub = tr[(tr["entry_date"] >= a) & (tr["entry_date"] <= b)] if len(tr) else tr
                es = equity_stats(eq)
                ts = trade_stats(t_sub)
                
                pf_str = num(ts['profit_factor']).strip() if ts else "0.0"
                tr_count = ts['n'] if ts else 0
                wr_str = pct(ts['win_rate']).strip() if ts else "0.0%"
                
                print(f"{cname:<35} | {pct(es['total']):>8} | {pct(es['cagr']):>8} | {num(es['sharpe']):>7} | {num(es['sortino']):>8} | {pct(es['maxdd']):>8} | {tr_count:>6} | {wr_str:>7} | {pf_str:>5}")

if __name__ == "__main__":
    verify_system_invariants()
    evaluate_models()