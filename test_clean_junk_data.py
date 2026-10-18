import sqlite3
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
