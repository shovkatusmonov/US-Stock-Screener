import sqlite3
import pandas as pd
from engine import Params, run_backtest
from run_backtest import equity_stats, trade_stats
import features as ft
import strategies as st
from sp500_history import SP500HistoryManager

PERIODS_DEV_VAL = {
    "DEV": ("2020-01-01", "2022-12-31"),
    "VAL": ("2023-01-01", "2024-12-31"),
}

def load_earnings_calendar(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='earnings_events'")
    if cur.fetchone():
        df = pd.read_sql("SELECT ticker, date FROM earnings_events", conn)
        cal = {}
        for t, d in zip(df["ticker"], df["date"]):
            cal.setdefault(t, set()).add(str(d)[:10])
        return cal
    return {}

def filter_signals_near_earnings(signals: dict, earnings_cal: dict, blackout_days: int = 5) -> dict:
    out = {}
    for t, df in signals.items():
        res = df.copy()
        if t in earnings_cal:
            e_dates = pd.to_datetime(sorted(list(earnings_cal[t])))
            sig_dates = res.index
            for sd in sig_dates[res["signal"]]:
                future_earnings = e_dates[e_dates >= sd]
                if len(future_earnings) > 0:
                    diff_days = (future_earnings[0] - sd).days
                    if 0 <= diff_days <= (blackout_days * 1.5):
                        res.loc[sd, "signal"] = False
        out[t] = res
    return out

def run_experiment(db_path: str = "data/screener.db"):
    conn = sqlite3.connect(db_path)
    bench_raw = ft.load_prices(conn, "SPY")
    bench_f = ft.build_features(bench_raw, bench_raw)
    bench_trd = ft.get_tradable_ohlc(bench_raw).reindex(bench_f.index).ffill()
    regime = st.regime_series(bench_f)
    dates = bench_f.index

    hm = SP500HistoryManager()
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
            tradable_ohlc[t] = ft.get_tradable_ohlc(raw).reindex(f.index).ffill()

    earnings_cal = load_earnings_calendar(conn)
    conn.close()

    params = Params.live() if hasattr(Params, "live") else Params(tp_pct=0.35, max_hold=35)
    strat_fn = st.STRATEGIES["breakout"]

    base_signals = {}
    for t, f in feats.items():
        sig_df = strat_fn(f, regime).copy()
        mask = hm.membership_mask(t, sig_df.index)
        sig_df.loc[~mask, "signal"] = False
        trd_df = tradable_ohlc[t]
        sig_df["atr"] = ft.atr(trd_df["high"], trd_df["low"], trd_df["close"], 14)
        base_signals[t] = sig_df

    filtered_signals = filter_signals_near_earnings(base_signals, earnings_cal, blackout_days=5)

    base_trades, base_curve = run_backtest(tradable_ohlc, base_signals, dates, params)
    filt_trades, filt_curve = run_backtest(tradable_ohlc, filtered_signals, dates, params)

    lines = [
        "=" * 85,
        "EARNINGS KATALIZATOR GIPOTEZASI AUDITI (DEV va VAL DAVRLARIDA)",
        "=" * 85,
        "Gipoteza: Hisobotgacha 5 savdo kunidan kam qolgan kirishlarni bloklash",
        "Eslatma: TEST davri tegilmagan (overfitting oldini olish uchun muzlatilgan)",
        "-" * 85,
        f"{'Model':<30} | {'Davr':<6} | {'Daromad':<8} | {'CAGR':<6} | {'Sharpe':<6} | {'MaxDD':<7} | {'Trades':<6} | {'WinRate':<7} | {'PF':<5}",
        "-" * 85,
    ]

    for p_name, (st_d, en_d) in PERIODS_DEV_VAL.items():
        for name, tr, cr in [("Breakout V2 (Baza)", base_trades, base_curve), ("Breakout V2 (+Earnings Filtr)", filt_trades, filt_curve)]:
            eq_s = cr["equity"].loc[st_d:en_d]
            t_sub = tr[(tr["entry_date"] >= st_d) & (tr["entry_date"] <= en_d)] if len(tr) else tr
            es = equity_stats(eq_s)
            ts = trade_stats(t_sub)
            lines.append(
                f"{name:<30} | {p_name:<6} | {es['total']*100:+.1f}%  | {es['cagr']*100:+.1f}% | {es['sharpe']:.2f}   | {es['maxdd']*100:.1f}%  | {ts['n']:<6} | {ts['win_rate']*100:.1f}%  | {ts['profit_factor']:.2f}"
            )
        lines.append("-" * 85)

    report_text = "\n".join(lines)
    print(report_text)
    return report_text

if __name__ == "__main__":
    run_experiment()
