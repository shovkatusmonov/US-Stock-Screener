"""
app/analytics/breadth.py - Market Breadth and Major Benchmark Analytics.

Tracks:
- Benchmarks: SPY (S&P 500), QQQ (Nasdaq 100), IWM (Russell 2000), DIA (Dow Jones).
- Breadth indicators:
  - Advance / Decline line & ratio
  - % of universe stocks above 50 SMA and 200 SMA
  - New 52-week Highs vs New 52-week Lows
  - Sector and market participation
"""

from datetime import datetime, timezone
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

try:
    from config.settings import DB_PATH as CONFIG_DB_PATH, BENCHMARKS
    DEFAULT_DB_PATH = str(CONFIG_DB_PATH)
    DEFAULT_BENCHMARKS = BENCHMARKS
except ImportError:
    DEFAULT_DB_PATH = "data/screener.db"
    DEFAULT_BENCHMARKS = ["SPY", "QQQ", "IWM", "DIA"]

logger = logging.getLogger(__name__)


class MarketBreadthAnalyzer:
    """Computes and tracks broad market health and participation metrics."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = str(db_path or DEFAULT_DB_PATH)

    def init_schema(self, conn: sqlite3.Connection) -> None:
        """Initializes market_breadth table if not exists."""
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_breadth (
                date TEXT PRIMARY KEY,
                total_stocks INTEGER,
                advancing INTEGER,
                declining INTEGER,
                unchanged INTEGER,
                adv_dec_ratio REAL,
                above_sma50_count INTEGER,
                above_sma50_pct REAL,
                above_sma200_count INTEGER,
                above_sma200_pct REAL,
                new_52w_highs INTEGER,
                new_52w_lows INTEGER,
                high_low_ratio REAL,
                spy_close REAL,
                spy_return REAL,
                qqq_return REAL,
                iwm_return REAL,
                dia_return REAL,
                updated_at TEXT NOT NULL
            );
        """)
        conn.commit()

    def compute_daily_breadth(self, as_of_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Calculates advance/decline, % above SMA50/200, and 52w high/low counts
        from technical_snapshots on a given date.
        """
        path = Path(self.db_path)
        if not path.exists():
            return None

        conn_ro = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        conn_ro.row_factory = sqlite3.Row
        cur = conn_ro.cursor()

        try:
            if not as_of_date:
                cur.execute("SELECT MAX(date) FROM technical_snapshots;")
                row = cur.fetchone()
                target_date = row[0] if row and row[0] else None
            else:
                target_date = as_of_date

            if not target_date:
                conn_ro.close()
                return None

            cur.execute("""
                SELECT symbol, close, sma50, sma200, ret5, dist_52w_high, dist_52w_low
                FROM technical_snapshots
                WHERE date = ?
            """, (target_date,))
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            conn_ro.close()
            return None

        conn_ro.close()

        if not rows:
            return None

        total = len(rows)
        advancing = 0
        declining = 0
        unchanged = 0
        above_sma50 = 0
        above_sma200 = 0
        new_highs = 0
        new_lows = 0

        for r in rows:
            close = r["close"]
            ret = r["ret5"]
            sma50 = r["sma50"]
            sma200 = r["sma200"]
            dist_h = r["dist_52w_high"]
            dist_l = r["dist_52w_low"]

            if ret is not None:
                if ret > 0.001:
                    advancing += 1
                elif ret < -0.001:
                    declining += 1
                else:
                    unchanged += 1

            if sma50 is not None and close is not None and close > sma50:
                above_sma50 += 1
            if sma200 is not None and close is not None and close > sma200:
                above_sma200 += 1

            if dist_h is not None and dist_h >= -0.01:
                new_highs += 1
            if dist_l is not None and dist_l <= 0.01:
                new_lows += 1

        adv_dec_ratio = advancing / max(1, declining)
        above_50_pct = (above_sma50 / total) * 100.0 if total > 0 else 0.0
        above_200_pct = (above_sma200 / total) * 100.0 if total > 0 else 0.0
        hl_ratio = new_highs / max(1, new_lows)

        bench_data = self.get_benchmark_summary(target_date)
        spy_close = bench_data.get("SPY", {}).get("close")
        spy_ret = bench_data.get("SPY", {}).get("ret5")
        qqq_ret = bench_data.get("QQQ", {}).get("ret5")
        iwm_ret = bench_data.get("IWM", {}).get("ret5")
        dia_ret = bench_data.get("DIA", {}).get("ret5")

        now_iso = datetime.now(timezone.utc).isoformat()

        record = {
            "date": target_date,
            "total_stocks": total,
            "advancing": advancing,
            "declining": declining,
            "unchanged": unchanged,
            "adv_dec_ratio": round(adv_dec_ratio, 2),
            "above_sma50_count": above_sma50,
            "above_sma50_pct": round(above_50_pct, 2),
            "above_sma200_count": above_sma200,
            "above_sma200_pct": round(above_200_pct, 2),
            "new_52w_highs": new_highs,
            "new_52w_lows": new_lows,
            "high_low_ratio": round(hl_ratio, 2),
            "spy_close": spy_close,
            "spy_return": spy_ret,
            "qqq_return": qqq_ret,
            "iwm_return": iwm_ret,
            "dia_return": dia_ret,
            "updated_at": now_iso
        }

        conn_rw = sqlite3.connect(self.db_path)
        self.init_schema(conn_rw)
        conn_rw.execute("""
            INSERT OR REPLACE INTO market_breadth (
                date, total_stocks, advancing, declining, unchanged,
                adv_dec_ratio, above_sma50_count, above_sma50_pct,
                above_sma200_count, above_sma200_pct, new_52w_highs,
                new_52w_lows, high_low_ratio, spy_close, spy_return,
                qqq_return, iwm_return, dia_return, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record["date"], record["total_stocks"], record["advancing"], record["declining"],
            record["unchanged"], record["adv_dec_ratio"], record["above_sma50_count"],
            record["above_sma50_pct"], record["above_sma200_count"], record["above_sma200_pct"],
            record["new_52w_highs"], record["new_52w_lows"], record["high_low_ratio"],
            record["spy_close"], record["spy_return"], record["qqq_return"], record["iwm_return"],
            record["dia_return"], record["updated_at"]
        ))
        conn_rw.commit()
        conn_rw.close()

        return record

    def get_benchmark_summary(self, as_of_date: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Returns snapshot data for major benchmark ETFs."""
        benchmarks = DEFAULT_BENCHMARKS
        summary = {}

        path = Path(self.db_path)
        if not path.exists():
            return summary

        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        try:
            for b in benchmarks:
                if as_of_date:
                    cur.execute("""
                        SELECT * FROM technical_snapshots
                        WHERE symbol = ? AND date <= ?
                        ORDER BY date DESC LIMIT 1
                    """, (b, as_of_date))
                else:
                    cur.execute("""
                        SELECT * FROM technical_snapshots
                        WHERE symbol = ?
                        ORDER BY date DESC LIMIT 1
                    """, (b,))
                row = cur.fetchone()
                if row:
                    summary[b] = dict(row)
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

        return summary

    def get_latest_breadth(self) -> Optional[Dict[str, Any]]:
        """Fetch the most recent market breadth record."""
        path = Path(self.db_path)
        if not path.exists():
            return None

        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        try:
            cur.execute("SELECT * FROM market_breadth ORDER BY date DESC LIMIT 1;")
            row = cur.fetchone()
            return dict(row) if row else None
        except sqlite3.OperationalError:
            return None
        finally:
            conn.close()
