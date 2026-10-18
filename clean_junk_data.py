import argparse
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
