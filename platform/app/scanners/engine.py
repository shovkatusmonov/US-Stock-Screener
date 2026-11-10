"""
app/scanners/engine.py - High-Performance Screener Execution Engine.

Features:
- Fast query path: uses precomputed `technical_snapshots` table for instant scans.
- Fallback streaming path: dynamically computes indicators in chunks of 50 tickers,
  ensuring minimal RAM usage on 952 MiB RAM VPS.
- Integrates with Universe Manager for asset class and exchange metadata.
- Ranks candidates by momentum, volume surge, or proximity to breakouts.
"""

from datetime import datetime, timezone
import gc
import logging
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Union
import pandas as pd

try:
    from config.settings import DB_PATH as CONFIG_DB_PATH, BATCH_SIZE_SCREENING
    DEFAULT_DB_PATH = str(CONFIG_DB_PATH)
    DEFAULT_BATCH = BATCH_SIZE_SCREENING
except ImportError:
    DEFAULT_DB_PATH = "data/screener.db"
    DEFAULT_BATCH = 50

from app.indicators.technical import compute_all_indicators, create_snapshot_row
from app.scanners.filters import ScreenFilter
from app.scanners.presets import get_preset

logger = logging.getLogger(__name__)


class ScreenerEngine:
    """Executes screening algorithms over the market data universe."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = str(db_path or DEFAULT_DB_PATH)

    def init_schema(self, conn: sqlite3.Connection) -> None:
        """Initializes technical_snapshots table if not exists."""
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS technical_snapshots (
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL DEFAULT 'US',
                date TEXT NOT NULL,
                close REAL NOT NULL,
                sma20 REAL,
                sma50 REAL,
                sma200 REAL,
                ema9 REAL,
                ema21 REAL,
                ema50 REAL,
                rsi14 REAL,
                macd REAL,
                macd_signal REAL,
                macd_hist REAL,
                atr14 REAL,
                atr_pct REAL,
                bb_upper REAL,
                bb_middle REAL,
                bb_lower REAL,
                bb_pct_b REAL,
                adx14 REAL,
                rel_vol REAL,
                ret5 REAL,
                ret20 REAL,
                ret60 REAL,
                high_52w REAL,
                low_52w REAL,
                dist_52w_high REAL,
                dist_52w_low REAL,
                trend_regime TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, exchange, date)
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tech_date ON technical_snapshots (date);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tech_sym ON technical_snapshots (symbol);")
        conn.commit()

    def compute_and_save_snapshots(
        self,
        symbols_with_exchange: Optional[List[tuple]] = None,
        as_of_date: Optional[str] = None,
        lookback_days: int = 260
    ) -> int:
        """
        Precomputes technical indicators and saves into technical_snapshots table.
        Processes in batches with explicit garbage collection.
        """
        path = Path(self.db_path)
        if not path.exists():
            return 0

        conn_ro = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        conn_ro.row_factory = sqlite3.Row
        cur_ro = conn_ro.cursor()

        if symbols_with_exchange is None:
            try:
                cur_ro.execute("""
                    SELECT DISTINCT symbol, exchange FROM market_data_daily
                    ORDER BY symbol
                """)
                symbols_with_exchange = [(r["symbol"], r["exchange"]) for r in cur_ro.fetchall()]
            except sqlite3.OperationalError:
                symbols_with_exchange = []

        saved_count = 0
        chunk_sz = DEFAULT_BATCH

        for i in range(0, len(symbols_with_exchange), chunk_sz):
            chunk = symbols_with_exchange[i:i + chunk_sz]
            batch_rows = []

            for sym, ex in chunk:
                q = """
                    SELECT date, open, high, low, close, adj_close, volume, vwap
                    FROM market_data_daily
                    WHERE symbol = ? AND exchange = ?
                """
                p = [sym, ex]
                if as_of_date:
                    q += " AND date <= ?"
                    p.append(as_of_date)
                q += " ORDER BY date DESC LIMIT ?"
                p.append(lookback_days)

                try:
                    cur_ro.execute(q, p)
                    raw_rows = cur_ro.fetchall()
                except sqlite3.OperationalError:
                    raw_rows = []

                if not raw_rows or len(raw_rows) < 5:
                    continue

                df = pd.DataFrame([dict(r) for r in reversed(raw_rows)])
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date").sort_index()

                snap = create_snapshot_row(sym, ex, df)
                if snap:
                    batch_rows.append((
                        snap["symbol"], snap["exchange"], snap["date"], snap["close"],
                        snap["sma20"], snap["sma50"], snap["sma200"], snap["ema9"],
                        snap["ema21"], snap["ema50"], snap["rsi14"], snap["macd"],
                        snap["macd_signal"], snap["macd_hist"], snap["atr14"], snap["atr_pct"],
                        snap["bb_upper"], snap["bb_middle"], snap["bb_lower"], snap["bb_pct_b"],
                        snap["adx14"], snap["rel_vol"], snap["ret5"], snap["ret20"], snap["ret60"],
                        snap["high_52w"], snap["low_52w"], snap["dist_52w_high"], snap["dist_52w_low"],
                        snap["trend_regime"], snap["updated_at"]
                    ))

            if batch_rows:
                conn_rw = sqlite3.connect(self.db_path)
                self.init_schema(conn_rw)
                cur_rw = conn_rw.cursor()
                cur_rw.executemany("""
                    INSERT OR REPLACE INTO technical_snapshots (
                        symbol, exchange, date, close, sma20, sma50, sma200, ema9,
                        ema21, ema50, rsi14, macd, macd_signal, macd_hist, atr14, atr_pct,
                        bb_upper, bb_middle, bb_lower, bb_pct_b, adx14, rel_vol, ret5,
                        ret20, ret60, high_52w, low_52w, dist_52w_high, dist_52w_low,
                        trend_regime, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, batch_rows)
                conn_rw.commit()
                conn_rw.close()
                saved_count += len(batch_rows)

            gc.collect()

        conn_ro.close()
        logger.info(f"Generated and saved {saved_count} technical snapshots.")
        return saved_count

    def screen(
        self,
        filter_criteria: Union[str, ScreenFilter],
        limit: int = 50,
        as_of_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Executes a screen against the universe.
        Returns matching candidates sorted by relative volume or momentum.
        """
        if isinstance(filter_criteria, str):
            crit = get_preset(filter_criteria)
        else:
            crit = filter_criteria

        matches: List[Dict[str, Any]] = []
        path = Path(self.db_path)
        if not path.exists():
            return matches

        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Check existing tables
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {r[0] for r in cur.fetchall()}

        has_snapshots = False
        if "technical_snapshots" in tables:
            if not as_of_date:
                cur.execute("SELECT MAX(date) FROM technical_snapshots;")
                row = cur.fetchone()
                target_date = row[0] if row and row[0] else None
            else:
                target_date = as_of_date

            if target_date:
                join_inst = "LEFT JOIN instruments i ON s.symbol = i.symbol AND s.exchange = i.exchange" if "instruments" in tables else ""
                inst_cols = ", i.name, i.asset_class, i.is_etf" if "instruments" in tables else ""
                cur.execute(f"""
                    SELECT s.* {inst_cols}
                    FROM technical_snapshots s
                    {join_inst}
                    WHERE s.date = ?
                """, (target_date,))
                snapshot_rows = cur.fetchall()

                if snapshot_rows:
                    has_snapshots = True
                    for r in snapshot_rows:
                        row_dict = dict(r)
                        if crit.matches(row_dict):
                            matches.append(row_dict)

        if has_snapshots:
            conn.close()
            matches.sort(key=lambda x: (x.get("rel_vol") or 0.0, x.get("ret20") or 0.0), reverse=True)
            return matches[:limit]

        # Dynamic streaming fallback
        if "market_data_daily" in tables:
            cur.execute("SELECT DISTINCT symbol, exchange FROM market_data_daily ORDER BY symbol;")
            symbols = [(r["symbol"], r["exchange"]) for r in cur.fetchall()]

            chunk_sz = DEFAULT_BATCH
            for i in range(0, len(symbols), chunk_sz):
                chunk = symbols[i:i + chunk_sz]
                for sym, ex in chunk:
                    q = """
                        SELECT date, open, high, low, close, adj_close, volume, vwap
                        FROM market_data_daily
                        WHERE symbol = ? AND exchange = ?
                    """
                    p = [sym, ex]
                    if as_of_date:
                        q += " AND date <= ?"
                        p.append(as_of_date)
                    q += " ORDER BY date DESC LIMIT 260"
                    cur.execute(q, p)
                    raw_rows = cur.fetchall()

                    if not raw_rows or len(raw_rows) < 20:
                        continue

                    df = pd.DataFrame([dict(r) for r in reversed(raw_rows)])
                    df["date"] = pd.to_datetime(df["date"])
                    df = df.set_index("date").sort_index()

                    snap = create_snapshot_row(sym, ex, df)
                    if snap and crit.matches(snap):
                        matches.append(snap)
                        if len(matches) >= limit * 2:
                            break

                gc.collect()
                if len(matches) >= limit * 2:
                    break

        conn.close()
        matches.sort(key=lambda x: (x.get("rel_vol") or 0.0, x.get("ret20") or 0.0), reverse=True)
        return matches[:limit]

    def refresh_snapshots_incremental(self, min_bars: int = 5) -> Dict[str, Any]:
        """
        Incrementally generates or updates technical snapshots.
        Only recomputes snapshots when market_data_daily has a newer date than existing snapshot.
        Tracks eligible, updated, insufficient_bars, and error statistics.
        """
        path = Path(self.db_path)
        if not path.exists():
            return {"error": "Database not found"}

        conn_init = sqlite3.connect(self.db_path)
        self.init_schema(conn_init)
        conn_init.close()

        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        cur = conn.cursor()

        try:
            cur.execute("""
                SELECT d.symbol, d.exchange, max(d.date) as max_data_date, count(*) as bar_count, max(s.date) as max_snap_date
                FROM market_data_daily d
                LEFT JOIN technical_snapshots s ON d.symbol = s.symbol AND d.exchange = s.exchange
                GROUP BY d.symbol, d.exchange;
            """)
            rows = cur.fetchall()
        except sqlite3.OperationalError as e:
            conn.close()
            return {"error": str(e)}
        finally:
            conn.close()

        stats = {
            "total_candidates": len(rows),
            "up_to_date": 0,
            "insufficient_bars": 0,
            "needs_update": 0,
            "generated": 0,
            "errors": 0
        }

        to_update = []
        for sym, ex, data_date, bar_cnt, snap_date in rows:
            if bar_cnt < min_bars:
                stats["insufficient_bars"] += 1
                continue
            if snap_date and snap_date >= data_date:
                stats["up_to_date"] += 1
                continue
            stats["needs_update"] += 1
            to_update.append((sym, ex))

        if to_update:
            saved = self.compute_and_save_snapshots(symbols_with_exchange=to_update)
            stats["generated"] = saved

        return stats

    def validate_all_presets(self, as_of_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Executes all 8 presets against real database snapshots and reports performance.
        """
        import time
        from app.scanners.presets import PRESETS

        path = Path(self.db_path)
        if not path.exists():
            return {"error": "Database not found"}

        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        cur = conn.cursor()
        try:
            cur.execute("SELECT count(DISTINCT symbol), count(*) FROM technical_snapshots;")
            row = cur.fetchone()
            eligible_symbols = row[0] if row else 0
            total_snapshots = row[1] if row else 0
        except sqlite3.OperationalError:
            eligible_symbols = 0
            total_snapshots = 0
        finally:
            conn.close()

        preset_results = {}
        total_start = time.time()

        for name in PRESETS.keys():
            t0 = time.time()
            try:
                matches = self.screen(name, limit=50, as_of_date=as_of_date)
                duration_ms = round((time.time() - t0) * 1000, 2)
                top_tickers = [m["symbol"] for m in matches[:5]]
                preset_results[name] = {
                    "matched_count": len(matches),
                    "duration_ms": duration_ms,
                    "top_tickers": top_tickers,
                    "status": "OK"
                }
            except Exception as e:
                duration_ms = round((time.time() - t0) * 1000, 2)
                preset_results[name] = {
                    "matched_count": 0,
                    "duration_ms": duration_ms,
                    "error": str(e),
                    "status": "ERROR"
                }

        total_duration = round(time.time() - total_start, 3)

        return {
            "eligible_instruments": eligible_symbols,
            "total_snapshots": total_snapshots,
            "total_duration_sec": total_duration,
            "presets": preset_results
        }
