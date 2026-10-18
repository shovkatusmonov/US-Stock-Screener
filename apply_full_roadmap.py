"""
apply_full_roadmap.py
4-vazifa, 5-vazifa va 6-vazifalarni loyihaga to'liq integratsiya qiluvchi skript.
Windows CRLF formatini qat'iy saqlaydi.
"""
from pathlib import Path

ROOT = Path("C:/us_stock_screener")

def write_crlf(path: Path, text: str) -> None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    path.write_bytes(normalized.encode("utf-8"))

# 1. clean_junk_data.py
write_crlf(ROOT / "clean_junk_data.py", Path("clean_junk_data.py").read_text(encoding="utf-8") if Path("clean_junk_data.py").exists() else '''import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
import pandas as pd
from sp500_history import SP500HistoryManager

JUNK_CANDIDATES = {"STI", "LB", "SBNY", "INFO", "APC", "NFX", "QRVO", "LEG", "WBD"}

def run_cleanup(db_path: str = "data/screener.db", dry_run: bool = True, backup_dir: str = "backups") -> dict:
    conn = sqlite3.connect(db_path)
    hm = SP500HistoryManager()

    spy_dates = [r[0][:10] for r in conn.execute(
        "SELECT date FROM prices WHERE ticker = 'SPY' AND date >= '2020-01-01' ORDER BY date"
    ).fetchall()]
    spy_idx = pd.to_datetime(spy_dates)
    spy_set = set(spy_dates)

    cur = conn.cursor()
    cur.execute("SELECT ticker, substr(date, 1, 10) FROM prices")
    dates_by_t = {}
    rows_by_t = {}
    for t, d_s in cur.fetchall():
        rows_by_t[t] = rows_by_t.get(t, 0) + 1
        if d_s in spy_set:
            dates_by_t.setdefault(t, set()).add(d_s)

    stats = {}
    total_deletable = 0

    for t in sorted(JUNK_CANDIDATES):
        tot_rows = rows_by_t.get(t, 0)
        mask = hm.membership_mask(t, spy_idx) if hasattr(hm, "membership_mask") else None
        in_win = 0
        if mask is not None and int(mask.sum()) > 0:
            active_spy_dates = {spy_dates[i] for i, flag in enumerate(mask) if flag}
            in_win = len(active_spy_dates & dates_by_t.get(t, set()))

        if in_win == 0 and tot_rows > 0:
            stats[t] = {"total_rows": tot_rows, "in_window": in_win, "action": "DELETE"}
            total_deletable += tot_rows
        else:
            stats[t] = {"total_rows": tot_rows, "in_window": in_win, "action": "KEEP"}

    if dry_run:
        conn.close()
        return {
            "dry_run": True,
            "backup_created": False,
            "backup_path": None,
            "candidates": stats,
            "total_rows_to_delete": total_deletable,
        }

    bp = Path(backup_dir)
    bp.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = bp / f"screener_pre_cleanup_{ts}.db"
    conn.close()
    shutil.copy2(db_path, str(backup_file))

    conn = sqlite3.connect(db_path)
    deleted_count = 0
    with conn:
        for t, s in stats.items():
            if s["action"] == "DELETE":
                res = conn.execute("DELETE FROM prices WHERE ticker = ?", (t,))
                deleted_count += res.rowcount

    conn.execute("VACUUM")
    conn.close()

    return {
        "dry_run": False,
        "backup_created": True,
        "backup_path": str(backup_file),
        "candidates": stats,
        "total_rows_deleted": deleted_count,
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Chiqindi qatorlarni xavfsiz tozalash")
    parser.add_argument("--db-path", default="data/screener.db", help="SQLite baza yo'li")
    parser.add_argument("--execute", action="store_true", help="Real tozalashni bajarish")
    args = parser.parse_args()
    res = run_cleanup(db_path=args.db_path, dry_run=not args.execute)
    print("=" * 60)
    print(f"CHIQINDI QATORLARNI TOZALASH ({'DRY-RUN' if res['dry_run'] else 'REAL EXECUTION'})")
    print("=" * 60)
    for t, s in res["candidates"].items():
        print(f"- {t:5s}: Jami={s['total_rows']:4d} | Oynada={s['in_window']:2d} -> {s['action']}")
    print("-" * 60)
    if res["dry_run"]:
        print(f"Jami o'chirilishi mumkin bo'lgan qatorlar: {res['total_rows_to_delete']:,} ta")
        print("[ESLATMA] Real tozalash uchun: python clean_junk_data.py --execute")
    else:
        print(f"Zaxira nusxa yaratildi: {res['backup_path']}")
        print(f"Muvaffaqiyatli o'chirildi: {res['total_rows_deleted']:,} ta qator")
''')

# 2. test_clean_junk_data.py
write_crlf(ROOT / "test_clean_junk_data.py", '''import sqlite3
import pandas as pd
from clean_junk_data import run_cleanup

def test_clean_junk_dry_run_does_not_modify_db(tmp_path):
    db_file = tmp_path / "test_junk.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE prices (ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, adj_close REAL)")
    for d in ["2024-01-02", "2024-01-03", "2024-01-04"]:
        conn.execute("INSERT INTO prices VALUES ('SPY', ?, 100, 101, 99, 100, 1000, 100)", (d,))
    conn.execute("INSERT INTO prices VALUES ('SBNY', '2019-01-02', 50, 51, 49, 50, 500, 50)")
    conn.commit()
    conn.close()

    res = run_cleanup(db_path=str(db_file), dry_run=True)
    assert res["dry_run"] is True
    assert res["candidates"]["SBNY"]["total_rows"] == 1
    assert res["candidates"]["SBNY"]["action"] == "DELETE"

    conn = sqlite3.connect(str(db_file))
    cnt = conn.execute("SELECT COUNT(*) FROM prices WHERE ticker = 'SBNY'").fetchone()[0]
    conn.close()
    assert cnt == 1

def test_clean_junk_execution_creates_backup_and_deletes(tmp_path):
    db_file = tmp_path / "test_junk.db"
    backup_dir = tmp_path / "backups"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE prices (ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, adj_close REAL)")
    for d in ["2024-01-02", "2024-01-03"]:
        conn.execute("INSERT INTO prices VALUES ('SPY', ?, 100, 101, 99, 100, 1000, 100)", (d,))
    conn.execute("INSERT INTO prices VALUES ('SBNY', '2019-01-02', 50, 51, 49, 50, 500, 50)")
    conn.commit()
    conn.close()

    res = run_cleanup(db_path=str(db_file), dry_run=False, backup_dir=str(backup_dir))
    assert res["dry_run"] is False
    assert res["backup_created"] is True
    assert res["total_rows_deleted"] == 1

    conn = sqlite3.connect(str(db_file))
    cnt = conn.execute("SELECT COUNT(*) FROM prices WHERE ticker = 'SBNY'").fetchone()[0]
    spy_cnt = conn.execute("SELECT COUNT(*) FROM prices WHERE ticker = 'SPY'").fetchone()[0]
    conn.close()
    assert cnt == 0
    assert spy_cnt == 2
''')

# 3. run_earnings_experiment.py
write_crlf(ROOT / "run_earnings_experiment.py", '''import sqlite3
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

    report_text = "\\n".join(lines)
    print(report_text)
    return report_text

if __name__ == "__main__":
    run_experiment()
''')

print("[OK] apply_full_roadmap.py: 4, 5 va 6-vazifalar modullari tayyorlandi.")