from datetime import datetime, timedelta
import sqlite3

import pandas as pd

import run_daily


def test_stale_spy_data_blocks_new_trades(tmp_path, monkeypatch):
    db_path = tmp_path / "stale.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE prices (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL,
            adj_close REAL, volume REAL,
            source TEXT NOT NULL, fetched_at TEXT NOT NULL,
            PRIMARY KEY (ticker, date)
        )
    """)

    old_date = (datetime.now().date() - timedelta(days=10)).isoformat()
    for i in range(260):
        d = (pd.Timestamp(old_date) - pd.offsets.BDay(259 - i)).strftime("%Y-%m-%d")
        conn.execute(
            "INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("SPY", d, 100, 101, 99, 100, 100, 1000000, "test", "test"),
        )
    conn.commit()
    conn.close()

    # Pipeline stale-data holatida yangi savdo yaratishga o'tmasligi kerak.
    result = run_daily.run_daily_pipeline(
        db_path=str(db_path),
        max_data_age_days=5,
    )

    assert result is False
