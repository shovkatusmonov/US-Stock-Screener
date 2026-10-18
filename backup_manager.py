"""
backup_manager.py - SQLite ma'lumotlar bazasini xavfsiz zaxiralash (backup)
va tiklashni (restore) avtomatik tekshirish moduli.
"""
import sqlite3
from datetime import datetime
from pathlib import Path


def create_and_verify_backup(db_path: str = "data/screener.db", backup_dir: str = "backups") -> dict:
    src = Path(db_path)
    if not src.exists():
        return {"ok": False, "error": f"Baza topilmadi: {db_path}"}

    b_dir = Path(backup_dir)
    b_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = b_dir / f"screener_backup_{stamp}.db"

    src_conn = sqlite3.connect(str(src))
    dst_conn = sqlite3.connect(str(dst))
    with dst_conn:
        src_conn.backup(dst_conn)
    src_conn.close()
    dst_conn.close()

    verify_conn = sqlite3.connect(str(dst))
    integrity = verify_conn.execute("PRAGMA integrity_check").fetchone()[0]
    row_count = verify_conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    verify_conn.close()

    return {
        "ok": integrity == "ok" and row_count > 0,
        "backup_file": str(dst),
        "integrity": integrity,
        "prices_rows": row_count,
    }


if __name__ == "__main__":
    res = create_and_verify_backup()
    print(f"[BACKUP AUDIT] Holat: {res}")
