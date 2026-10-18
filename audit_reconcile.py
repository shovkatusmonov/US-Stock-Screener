"""
audit_reconcile.py - Breakout V2 natijalari farqini (Static vs PIT Adjusted vs PIT Tradable)
bir xil sharoitda takroriy hisoblab chiqaruvchi rasmiy audit skripti.
"""
import sqlite3
import pandas as pd
from engine import Params, run_backtest
import features as ft
import strategies as st
from sp500_history import SP500HistoryManager
from run_backtest import equity_stats, trade_stats, pct, num

PERIODS = {
    "DEV":  ("2020-01-01", "2022-12-31"),
    "VAL":  ("2023-01-01", "2024-12-31"),
    "TEST": ("2025-01-01", "2026-10-09"),
}


def run_reconciliation():
    conn = sqlite3.connect("data/screener.db")
    bench_raw = ft.load_prices(conn, "SPY")
    bench_f = ft.build_features(bench_raw, bench_raw)
    regime = st.regime_series(bench_f)
    dates = bench_f.index
    bench_close = bench_f["close"]

    tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM prices WHERE ticker != 'SPY' ORDER BY ticker")]
    feats, tradable_ohlc, adjusted_ohlc = {}, {}, {}

    for t in tickers:
        raw = ft.load_prices(conn, t)
        if len(raw) < 260:
            continue
        f = ft.build_features(raw, bench_raw)
        if not f.empty:
            feats[t] = f
            tradable_ohlc[t] = ft.get_tradable_ohlc(raw).reindex(f.index).ffill()
            adjusted_ohlc[t] = ft.adjust_ohlc(raw).reindex(f.index).ffill()
    conn.close()

    hm = SP500HistoryManager()
    fn = st.STRATEGIES["breakout"]
    raw_signals = {t: fn(feats[t], regime) for t in feats}

    pit_signals_adj = {}
    pit_signals_trd = {}
    for t, sig_df in raw_signals.items():
        df_adj = sig_df.copy()
        if t in hm.membership_rules:
            rule = hm.membership_rules[t]
            intervals = rule if isinstance(rule, list) else [rule]
            valid_mask = pd.Series(False, index=df_adj.index)
            for st_d, en_d in intervals:
                m = pd.Series(True, index=df_adj.index)
                if st_d:
                    m &= (df_adj.index >= pd.to_datetime(st_d))
                if en_d:
                    m &= (df_adj.index <= pd.to_datetime(en_d))
                valid_mask |= m
            df_adj.loc[~valid_mask, "signal"] = False
        pit_signals_adj[t] = df_adj

        df_trd = df_adj.copy()
        trd_df = tradable_ohlc[t]
        df_trd["atr"] = ft.atr(trd_df["high"], trd_df["low"], trd_df["close"], 14)
        pit_signals_trd[t] = df_trd

    p = Params(tp_pct=0.35, max_hold=35)

    tr_stat, curve_stat = run_backtest(adjusted_ohlc, raw_signals, dates, p)
    tr_adj, curve_adj = run_backtest(adjusted_ohlc, pit_signals_adj, dates, p)
    tr_trd, curve_trd = run_backtest(tradable_ohlc, pit_signals_trd, dates, p)

    print("=" * 95)
    print("BREAKOUT V2 YARASHTIRUV AUDITI: STATIK vs PIT ADJUSTED OHLC vs PIT TRADABLE OHLC")
    print("=" * 95)

    runs = [
        ("1. Statik 2026 (Adjusted)", tr_stat, curve_stat),
        ("2. PIT Tarkib (Adjusted)",  tr_adj,  curve_adj),
        ("3. PIT Tarkib (Tradable)",  tr_trd,  curve_trd),
    ]

    for p_name, (st_d, en_d) in PERIODS.items():
        sp = bench_close.loc[st_d:en_d]
        bs = equity_stats(sp)
        print(f"\n--- DAVR: {p_name} ({st_d} -> {en_d}) ---")
        header = f"{'Variant':<28} | {'Daromad':>8} | {'CAGR':>8} | {'Sharpe':>7} | {'MaxDD':>8} | {'Trades':>6} | {'WinRate':>7} | {'PF':>5}"
        print("-" * len(header))
        print(header)
        print("-" * len(header))
        print(f"{'SPY Buy & Hold':<28} | {pct(bs['total']):>8} | {pct(bs['cagr']):>8} | {num(bs['sharpe']):>7} | {pct(bs['maxdd']):>8} | {'-':>6} | {'-':>7} | {'-':>5}")

        for label, tr, curve in runs:
            eq_slice = curve["equity"].loc[st_d:en_d]
            t_sub = tr[(tr["entry_date"] >= st_d) & (tr["entry_date"] <= en_d)] if len(tr) else tr
            es = equity_stats(eq_slice)
            ts = trade_stats(t_sub)
            tr_n = ts["n"] if ts else 0
            wr_s = pct(ts["win_rate"]).strip() if ts else "0.0%"
            pf_s = num(ts["profit_factor"]).strip() if ts else "0.00"
            print(f"{label:<28} | {pct(es['total']):>8} | {pct(es['cagr']):>8} | {num(es['sharpe']):>7} | {pct(es['maxdd']):>8} | {tr_n:>6} | {wr_s:>7} | {pf_s:>5}")


if __name__ == "__main__":
    run_reconciliation()
