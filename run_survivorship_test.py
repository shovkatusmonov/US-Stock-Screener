"""
run_survivorship_test.py - Tarixiy Point-in-Time tarkib bilan yakuniy qayta sinov (Stage 6).
Sana x Ticker to'liq matritsasi va delisted aksiyalar shaffof auditi bilan.
"""
import sqlite3
import pandas as pd
from engine import Params, run_backtest
import strategies as st
from sp500_history import SP500HistoryManager
from run_backtest import equity_stats, trade_stats, load_all, pct, num

PERIODS = {
    "DEV":  ("2020-01-01", "2022-12-31"),
    "VAL":  ("2023-01-01", "2024-12-31"),
    "TEST": ("2025-01-01", "2100-01-01"),
}


def main():
    conn = sqlite3.connect("data/screener.db")
    feats, ohlc, regime, bench_f = load_all(conn)
    base_tickers = set(feats.keys())
    dates = bench_f.index
    bench_close = bench_f["close"]

    hm = SP500HistoryManager()

    print("=" * 85)
    print("S&P 500 TARIXIY POINT-IN-TIME (PIT) VA QAMROV MATRITSASI AUDITI")
    print("=" * 85)

    # Sana x Ticker matritsasi auditi
    all_dates_str = [str(d.date()) for d in dates]
    mat_cov = hm.evaluate_date_matrix_coverage(all_dates_str, base_tickers, conn)
    conn.close()

    print(f"Umumiy Point-in-Time 'sana x ticker' slotlari: {mat_cov['total_slots']:,} ta")
    print(f"Bazadagi real mavjud slotlar:                   {mat_cov['filled_slots']:,} ta")
    print(f"Yetishmayotgan tarixiy slotlar:                {mat_cov['missing_slots']:,} ta")
    print(f"Haqiqiy matritsa qamrovi:                       {mat_cov['coverage_pct']*100:.2f}%")
    print(f"To'liq Survivorship-Bias-Free?                 {'HA (100%)' if mat_cov['is_fully_survivorship_free'] else 'YO`Q (Cheklov mavjud)'}")
    
    print(f"PIT tarkib manbasi: {'TO`LIQ tarixiy (version 2.0)' if hm.complete else 'FAQAT 15 ta qo`lda kiritilgan o`zgarish (to`liq emas!)'}")
    if mat_cov["missing_by_ticker"]:
        mb = sorted(mat_cov["missing_by_ticker"].items(), key=lambda x: -x[1])
        print(f"\nNarxi yetishmayotgan a'zolar: {len(mb)} ta ticker (eng ko'p 15 tasi; to'liq ro'yxat: download_missing_pit.py --report)")
        for t, cnt in mb[:15]:
            print(f"  - {t:<6}: {cnt} ta savdo kuni narxi yo'q")
    print("-" * 85)

    import sys
    legacy = "--legacy" in sys.argv
    p = Params(tp_pct=0.35, max_hold=35) if legacy else Params.live(tp_pct=0.35, max_hold=35)
    print("REJIM: " + ("ESKI (gap/RR/Heat/butun aksiya qoidalarisiz)" if legacy
                       else "JONLI QOIDALAR (gap +-3%, R/R>=1.5, Heat 5%, butun aksiya)"))

    for strat_name in ["breakout", "pullback"]:
        fn = st.STRATEGIES[strat_name]
        raw_signals = {t: fn(feats[t], regime) for t in feats}

        pit_signals = {}
        for t, sig_df in raw_signals.items():
            if t in hm.membership_rules or hm.complete:
                # A'zolik oralig'idan tashqaridagi signallarni o'chirish
                # (to'liq PIT jadvalda qoidasi yo'q ticker hech qachon a'zo bo'lmagan)
                df_copy = sig_df.copy()
                df_copy["signal"] = df_copy["signal"] & hm.membership_mask(t, df_copy.index)
                pit_signals[t] = df_copy
            else:
                pit_signals[t] = sig_df

        tr, curve = run_backtest(ohlc, pit_signals, dates, p)

        print(f"\nSTRATEGIYA: {strat_name.upper()} (Point-in-Time Tarkib bilan)")
        header = f"{'Davr':<6} | {'Daromad':>8} | {'CAGR':>8} | {'Sharpe':>7} | {'Sortino':>8} | {'MaxDD':>8} | {'Trades':>6} | {'WinRate':>7} | {'PF':>5} | {'SPY Sharpe':>10}"
        print("-" * len(header))
        print(header)
        print("-" * len(header))

        for p_name, (a, b) in PERIODS.items():
            sp = bench_close.loc[a:b]
            bs = equity_stats(sp)
            eq = curve["equity"].loc[a:b]
            t_sub = tr[(tr["entry_date"] >= a) & (tr["entry_date"] <= b)] if len(tr) else tr
            es = equity_stats(eq)
            ts = trade_stats(t_sub)

            pf_str = num(ts['profit_factor']).strip() if ts else "0.0"
            tr_count = ts['n'] if ts else 0
            wr_str = pct(ts['win_rate']).strip() if ts else "0.0%"
            print(f"{p_name:<6} | {pct(es['total']):>8} | {pct(es['cagr']):>8} | {num(es['sharpe']):>7} | {num(es['sortino']):>8} | {pct(es['maxdd']):>8} | {tr_count:>6} | {wr_str:>7} | {pf_str:>5} | {num(bs['sharpe']):>10}")


if __name__ == "__main__":
    main()