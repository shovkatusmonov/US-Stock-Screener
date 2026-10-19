import argparse
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
from sp500_history import SP500HistoryManager, HISTORICAL_CHANGES_SEED

JUNK_CANDIDATES = {"STI", "LB", "SBNY", "INFO", "APC", "NFX", "QRVO", "LEG", "WBD"}


def _load_history_read_only(db_path: str) -> SP500HistoryManager:
    """Load membership rules without creating or modifying any database objects."""
    rules = {}
    complete = False

    try:
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='sp500_historical_membership'"
            ).fetchone()

            if exists:
                rows = conn.execute(
                    "SELECT ticker, start_date, end_date "
                    "FROM sp500_historical_membership"
                ).fetchall()
                complete = bool(conn.execute(
                    "SELECT 1 FROM sp500_historical_membership "
                    "WHERE version LIKE '2%' LIMIT 1"
                ).fetchone())

                for ticker, start_date, end_date in rows:
                    rules.setdefault(ticker, []).append((start_date, end_date))
            else:
                for item in HISTORICAL_CHANGES_SEED:
                    rules.setdefault(item["ticker"], []).append(
                        (item["start_date"], item["end_date"])
                    )
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        # Do not silently treat an unreadable existing database as empty.
        raise RuntimeError(
            f"Tarixiy a'zolik ma'lumotlarini faqat o'qish rejimida yuklab bo'lmadi: {exc}"
        ) from exc

    return SP500HistoryManager(rules=rules, complete=complete)


def run_cleanup(
    db_path: str = "data/screener.db",
    dry_run: bool = True,
    backup_dir: str = "backups",
) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        hm = _load_history_read_only(db_path)

        spy_dates = [
            r[0][:10] for r in conn.execute(
                "SELECT date FROM prices "
                "WHERE ticker = 'SPY' AND date >= '2020-01-01' ORDER BY date"
            ).fetchall()
        ]
        spy_idx = pd.to_datetime(spy_dates)
        spy_set = set(spy_dates)

        cur = conn.cursor()
        cur.execute("SELECT ticker, substr(date, 1, 10) FROM prices")
        dates_by_t = {}
        rows_by_t = {}

        for ticker, date_str in cur.fetchall():
            rows_by_t[ticker] = rows_by_t.get(ticker, 0) + 1
            if date_str in spy_set:
                dates_by_t.setdefault(ticker, set()).add(date_str)

        stats = {}
        total_deletable = 0

        for ticker in sorted(JUNK_CANDIDATES):
            total_rows = rows_by_t.get(ticker, 0)
            mask = hm.membership_mask(ticker, spy_idx)
            in_window = 0

            if mask is not None and int(mask.sum()) > 0:
                active_dates = {
                    spy_dates[i] for i, flag in enumerate(mask) if flag
                }
                in_window = len(active_dates & dates_by_t.get(ticker, set()))

            if in_window == 0 and total_rows > 0:
                action = "DELETE"
                total_deletable += total_rows
            else:
                action = "KEEP"

            stats[ticker] = {
                "total_rows": total_rows,
                "in_window": in_window,
                "action": action,
            }

        if dry_run:
            return {
                "dry_run": True,
                "backup_created": False,
                "backup_path": None,
                "candidates": stats,
                "total_rows_to_delete": total_deletable,
            }
    finally:
        conn.close()

    bp = Path(backup_dir)
    bp.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = bp / f"screener_pre_cleanup_{ts}.db"
    shutil.copy2(db_path, str(backup_file))

    conn = sqlite3.connect(db_path)
    try:
        deleted_count = 0
        with conn:
            for ticker, stats_item in stats.items():
                if stats_item["action"] == "DELETE":
                    result = conn.execute(
                        "DELETE FROM prices WHERE ticker = ?", (ticker,)
                    )
                    deleted_count += result.rowcount

        conn.execute("VACUUM")
    finally:
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

    for ticker, stats_item in res["candidates"].items():
        print(
            f"- {ticker:5s}: Jami={stats_item['total_rows']:4d} "
            f"| Oynada={stats_item['in_window']:2d} -> {stats_item['action']}"
        )

    print("-" * 60)
    if res["dry_run"]:
        print(f"Jami o'chirilishi mumkin bo'lgan qatorlar: {res['total_rows_to_delete']:,} ta")
        print("[ESLATMA] Real tozalash uchun: python clean_junk_data.py --execute")
    else:
        print(f"Zaxira nusxa yaratildi: {res['backup_path']}")
        print(f"Muvaffaqiyatli o'chirildi: {res['total_rows_deleted']:,} ta qator")
