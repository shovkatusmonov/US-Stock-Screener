"""
run_backtest.py - Tarixiy backtest va statistika hisoblagichi.
P1.7: Signal indikatorlari va real ijro (Tradable OHLC) qat'iy ajratilgan.
"""
import sqlite3
import pandas as pd
import numpy as np
from engine import Params, run_backtest
import features as ft
import strategies as st

PERIODS = {
    "DEV":  ("2020-01-01", "2022-12-31"),
    "VAL":  ("2023-01-01", "2024-12-31"),
    "TEST": ("2025-01-01", "2100-01-01"),
}


def load_all(conn: sqlite3.Connection):
    """
    Barcha aksiyalar uchun indikator xususiyatlari va real tradable OHLC kotirovkalarini yuklaydi.
    """
    bench_raw = ft.load_prices(conn, "SPY")
    bench_f = ft.build_features(bench_raw, bench_raw)
    regime = st.regime_series(bench_f)

    tickers = [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM prices WHERE ticker != 'SPY' ORDER BY ticker")]
    feats = {}
    tradable_ohlc = {}

    for t in tickers:
        raw = ft.load_prices(conn, t)
        if len(raw) < 260:
            continue
        f = ft.build_features(raw, bench_raw)
        if not f.empty:
            feats[t] = f
            # Ijro dvigateli uchun splitga moslangan real birja kotirovkalari
            tradable_ohlc[t] = ft.get_tradable_ohlc(raw).reindex(f.index).ffill()

    return feats, tradable_ohlc, regime, bench_f


def equity_stats(curve_series: pd.Series) -> dict:
    if len(curve_series) < 2:
        return {"total": 0.0, "cagr": 0.0, "sharpe": 0.0, "sortino": 0.0, "maxdd": 0.0}
    total = (curve_series.iloc[-1] / curve_series.iloc[0]) - 1.0
    days = (curve_series.index[-1] - curve_series.index[0]).days
    years = max(days / 365.25, 0.01)
    cagr = ((1.0 + max(total, -0.999)) ** (1.0 / years)) - 1.0

    rets = curve_series.pct_change().dropna()
    mean_d = rets.mean()
    std_d = rets.std()
    sharpe = (mean_d / std_d * np.sqrt(252)) if std_d > 0 else 0.0

    # To'g'ri downside deviation: barcha kunlar bo'yicha sqrt(mean(min(r,0)^2))
    downside_std = float(np.sqrt(np.mean(np.minimum(rets.values, 0.0) ** 2)))
    sortino = (mean_d / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0

    peak = curve_series.cummax()
    dd = (curve_series - peak) / peak
    maxdd = dd.min()

    return {
        "total": total,
        "cagr": cagr,
        "sharpe": sharpe,
        "sortino": sortino,
        "maxdd": maxdd
    }


def trade_stats(trades_df: pd.DataFrame) -> dict:
    if trades_df.empty:
        return {"n": 0, "win_rate": 0.0, "profit_factor": 0.0}
    n = len(trades_df)
    wins = trades_df[trades_df["pnl"] > 0]
    losses = trades_df[trades_df["pnl"] < 0]
    win_rate = len(wins) / n

    total_gain = wins["pnl"].sum()
    total_loss = abs(losses["pnl"].sum())
    profit_factor = (total_gain / total_loss) if total_loss > 0 else (99.0 if total_gain > 0 else 0.0)

    return {
        "n": n,
        "win_rate": win_rate,
        "profit_factor": profit_factor
    }


def pct(val: float) -> str:
    return f"{val * 100:.1f}%"


def num(val: float) -> str:
    return f"{val:.2f}"


def main():
    conn = sqlite3.connect("data/screener.db")
    feats, ohlc, regime, bench_f = load_all(conn)
    conn.close()

    dates = bench_f.index
    bench_close = bench_f["close"]
    import sys
    legacy = "--legacy" in sys.argv
    p = Params(tp_pct=0.35, max_hold=35) if legacy else Params.live(tp_pct=0.35, max_hold=35)
    print("REJIM: " + ("ESKI (gap/RR/Heat/butun aksiya qoidalarisiz)" if legacy
                       else "JONLI QOIDALAR (gap +-3%, R/R>=1.5, Heat 5%, butun aksiya)"))

    print("=" * 85)
    print("BACKTEST: TRADABLE OHLC VA ADJUSTED SIGNALLAR AJRATILGAN HOLATDA")
    print("=" * 85)

    for strat_name in ["breakout", "pullback"]:
        fn = st.STRATEGIES[strat_name]
        signals = {t: fn(feats[t], regime) for t in feats}
        tr, curve = run_backtest(ohlc, signals, dates, p)

        print(f"\nSTRATEGIYA: {strat_name.upper()}")
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