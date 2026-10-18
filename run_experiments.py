"""
run_experiments.py - DEV va VAL davrlarida gipotezalarni tezkor solishtirish.
Asosiy fayllarni o'zgartirmaydi, barcha testlarni xotirada bajaradi.
"""
import sqlite3
import numpy as np
import pandas as pd
from engine import Params, run_backtest
import features as ft
import strategies as st
from run_backtest import equity_stats, trade_stats, load_all, pct, num

DEV_RANGE = ("2020-01-01", "2022-12-31")
VAL_RANGE = ("2023-01-01", "2024-12-31")

def test_variant(name, params_dict, ohlc, feats, regime, bench_f, vol_shift_fix=False):
    # features nusxasini olish
    active_feats = {}
    for t, f in feats.items():
        df_f = f.copy()
        if vol_shift_fix:
            # Bugungi kun hajmini o'rtachadan chiqarib tashlash
            shifted_avg = df_f["avg_vol20"].shift(1)
            raw_vol = ohlc[t]["open"] * 0 + df_f["avg_vol20"] * df_f["rel_vol"] # asl hajm
            df_f["rel_vol"] = raw_vol / shifted_avg
        active_feats[t] = df_f

    dates = bench_f.index
    p = Params(**params_dict)
    
    results = {}
    for strat_name in ["breakout", "pullback"]:
        fn = st.STRATEGIES[strat_name]
        signals = {t: fn(active_feats[t], regime) for t in active_feats}
        tr, curve = run_backtest(ohlc, signals, dates, p)
        
        for p_name, (d_start, d_end) in [("DEV", DEV_RANGE), ("VAL", VAL_RANGE)]:
            eq = curve["equity"].loc[d_start:d_end]
            sp = bench_f["close"].loc[d_start:d_end]
            t_sub = tr[(tr["entry_date"] >= d_start) & (tr["entry_date"] <= d_end)] if len(tr) else tr
            
            es = equity_stats(eq)
            bs = equity_stats(sp)
            ts = trade_stats(t_sub)
            exp = curve["invested"].loc[d_start:d_end].mean()
            
            results[(strat_name, p_name)] = {
                "variant": name,
                "strat": strat_name,
                "period": p_name,
                "return": es["total"],
                "cagr": es["cagr"],
                "sharpe": es["sharpe"],
                "sortino": es["sortino"],
                "maxdd": es["maxdd"],
                "exposure": exp,
                "spy_ret": bs["total"],
                "spy_sharpe": bs["sharpe"],
                "trades": ts["n"] if ts else 0,
                "win_rate": ts["win_rate"] if ts else 0,
                "pf": ts["profit_factor"] if ts else 0,
                "beat_spy": es["sharpe"] > bs["sharpe"] and es["sortino"] > bs["sortino"]
            }
    return results

def main():
    conn = sqlite3.connect("data/screener.db")
    feats, ohlc, regime, bench_f = load_all(conn)
    conn.close()
    
    print("\n" + "="*80)
    print("DEV va VAL GIPOTEZALAR TAQQOSLASH AUDITI (TEST MUZLATILGAN)")
    print("="*80)

    # 1. Baza: V0
    v0_res = test_variant("V0: Joriy Baza (TP 10%, Hold 15)", 
                          {"tp_pct": 0.10, "max_hold": 15}, 
                          ohlc, feats, regime, bench_f, vol_shift_fix=False)

    # 2. V1: Kengaytirilgan target + Hajm to'g'rilash
    v1_res = test_variant("V1: Hajm Fix + TP 20%, Hold 25", 
                          {"tp_pct": 0.20, "max_hold": 25}, 
                          ohlc, feats, regime, bench_f, vol_shift_fix=True)

    # 3. V2: Trend Runner (TP 35%, Hold 35)
    v2_res = test_variant("V2: Trend Runner (TP 35%, Hold 35)", 
                          {"tp_pct": 0.35, "max_hold": 35}, 
                          ohlc, feats, regime, bench_f, vol_shift_fix=True)

    all_res = [v0_res, v1_res, v2_res]

    for strat in ["breakout", "pullback"]:
        for period in ["DEV", "VAL"]:
            print(f"\n>>> STRATEGIYA: {strat.upper()} | DAVR: {period} <<<")
            header = f"{'Versiya':<33} | {'Daromad':>8} | {'CAGR':>8} | {'Sharpe':>7} | {'Sortino':>8} | {'MaxDD':>8} | {'Savdolar':>8} | {'WinRate':>7} | {'SPY Ustun?':>10}"
            print("-" * len(header))
            print(header)
            print("-" * len(header))
            
            for r_set in all_res:
                r = r_set[(strat, period)]
                beat_str = "HA" if r["beat_spy"] else "YO'Q"
                print(f"{r['variant']:<33} | {pct(r['return']):>8} | {pct(r['cagr']):>8} | {num(r['sharpe']):>7} | {num(r['sortino']):>8} | {pct(r['maxdd']):>8} | {r['trades']:>8} | {pct(r['win_rate']):>7} | {beat_str:>10}")

if __name__ == "__main__":
    main()