"""
scripts/audit_coverage.py - Detailed market data coverage audit for US Stock Screener.
Runs read-only queries against SQLite database on the VPS.
"""

from collections import defaultdict
from pathlib import Path
import sqlite3
import sys

# Ensure project root in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.settings import DB_PATH


def run_audit(db_path: str = str(DB_PATH)):
    p = Path(db_path)
    if not p.exists():
        print(f"[!] Database file not found: {db_path}")
        return

    print("=" * 70)
    print("       US STOCK SCREENER — MARKET DATA COVERAGE AUDIT")
    print("=" * 70)
    print(f"Database Path: {p.resolve()} ({p.stat().st_size / (1024*1024):.2f} MB)")

    conn = sqlite3.connect(f"file:{p.resolve()}?mode=ro", uri=True)
    cur = conn.cursor()

    # 1. Total Universe & Active Classification
    cur.execute("SELECT status, count(*) FROM instruments GROUP BY status;")
    status_counts = dict(cur.fetchall())
    total_instruments = sum(status_counts.values())
    active_total = status_counts.get("ACTIVE", 0)

    cur.execute("""
        SELECT asset_class, count(*)
        FROM instruments
        WHERE status = 'ACTIVE'
        GROUP BY asset_class
        ORDER BY count(*) DESC;
    """)
    asset_class_counts = dict(cur.fetchall())

    cur.execute("""
        SELECT exchange, count(*)
        FROM instruments
        WHERE status = 'ACTIVE'
        GROUP BY exchange
        ORDER BY count(*) DESC;
    """)
    exchange_counts = dict(cur.fetchall())

    # 2. Market Data Daily Counts per Symbol
    # Get bars count per symbol in market_data_daily
    cur.execute("""
        SELECT symbol, exchange, count(*) as bar_count, min(date) as min_date, max(date) as max_date, max(fetched_at) as last_fetch
        FROM market_data_daily
        GROUP BY symbol, exchange;
    """)
    mdd_rows = cur.fetchall()

    symbols_with_data = len(mdd_rows)
    symbols_with_200_bars = sum(1 for r in mdd_rows if r[2] >= 200)
    symbols_with_50_bars = sum(1 for r in mdd_rows if r[2] >= 50)
    symbols_with_20_bars = sum(1 for r in mdd_rows if r[2] >= 20)

    # 3. Technical Snapshots
    cur.execute("SELECT count(DISTINCT symbol), count(*), min(date), max(date) FROM technical_snapshots;")
    snap_stats = cur.fetchone()
    snap_distinct_symbols = snap_stats[0] if snap_stats else 0
    snap_total_records = snap_stats[1] if snap_stats else 0
    snap_min_date = snap_stats[2] if snap_stats else "N/A"
    snap_max_date = snap_stats[3] if snap_stats else "N/A"

    # 4. Join Market Data with Instruments to calculate Coverage % by Asset Class and Exchange
    cur.execute("""
        SELECT i.symbol, i.exchange, i.asset_class, COALESCE(d.bar_count, 0) as bars, d.max_date, d.last_fetch
        FROM instruments i
        LEFT JOIN (
            SELECT symbol, count(*) as bar_count, max(date) as max_date, max(fetched_at) as last_fetch
            FROM market_data_daily
            GROUP BY symbol
        ) d ON i.symbol = d.symbol
        WHERE i.status = 'ACTIVE';
    """)
    active_joined = cur.fetchall()

    class_stats = defaultdict(lambda: {"total": 0, "with_data": 0, "with_200": 0})
    ex_stats = defaultdict(lambda: {"total": 0, "with_data": 0, "with_200": 0})
    timestamps = []

    for r in active_joined:
        ac = r[2]
        ex = r[1]
        bars = r[3]
        last_fetch = r[5]

        class_stats[ac]["total"] += 1
        ex_stats[ex]["total"] += 1

        if bars > 0:
            class_stats[ac]["with_data"] += 1
            ex_stats[ex]["with_data"] += 1
        if bars >= 200:
            class_stats[ac]["with_200"] += 1
            ex_stats[ex]["with_200"] += 1

        if last_fetch:
            timestamps.append(last_fetch)

    conn.close()

    # Print Report
    print("\n[1] UNIVERSE SUMMARY (ACTIVE):")
    print(f"  Total Catalog Instruments    : {total_instruments:,}")
    print(f"  Active Instruments           : {active_total:,}")
    print(f"  Active Common Stocks (CS)    : {asset_class_counts.get('CS', 0):,}")
    print(f"  Active ETFs                  : {asset_class_counts.get('ETF', 0):,}")
    print(f"  Active ADRs                  : {asset_class_counts.get('ADR', 0):,}")
    print(f"  Active Preferred Shares (PRF): {asset_class_counts.get('PRF', 0):,}")
    print(f"  Active Units / Warrants / RGT: {asset_class_counts.get('UNT', 0) + asset_class_counts.get('WNT', 0) + asset_class_counts.get('RGT', 0):,}")

    print("\n[2] MARKET DATA DAILY (OHLCV) COVERAGE:")
    print(f"  Instruments with ANY OHLCV data  : {symbols_with_data:,} / {active_total:,} ({(symbols_with_data / active_total * 100) if active_total else 0:.2f}%)")
    print(f"  Instruments with >= 200 trading days: {symbols_with_200_bars:,} / {active_total:,} ({(symbols_with_200_bars / active_total * 100) if active_total else 0:.2f}%)")
    print(f"  Instruments with >= 50 trading days : {symbols_with_50_bars:,}")
    print(f"  Instruments with >= 20 trading days : {symbols_with_20_bars:,}")

    print("\n[3] TECHNICAL SNAPSHOTS COVERAGE:")
    print(f"  Distinct Symbols in Snapshots    : {snap_distinct_symbols:,}")
    print(f"  Total Snapshot Records           : {snap_total_records:,}")
    print(f"  Snapshot Date Range              : {snap_min_date} -> {snap_max_date}")

    print("\n[4] DATA UPDATE TIMESTAMPS:")
    if timestamps:
        timestamps.sort()
        print(f"  Earliest update timestamp: {timestamps[0]}")
        print(f"  Latest update timestamp  : {timestamps[-1]}")
        print(f"  Total updated records    : {len(timestamps)}")
    else:
        print("  No update timestamps found.")

    print("\n[5] COVERAGE BY ASSET CLASS:")
    print(f"{'Asset Class':<14} {'Total Active':<14} {'With Data':<12} {'% With Data':<14} {'With >=200d':<12} {'% >=200d'}")
    print("-" * 75)
    for ac, d in sorted(class_stats.items(), key=lambda x: x[1]["total"], reverse=True):
        pct_data = (d["with_data"] / d["total"] * 100) if d["total"] else 0
        pct_200 = (d["with_200"] / d["total"] * 100) if d["total"] else 0
        print(f"{ac:<14} {d['total']:<14,d} {d['with_data']:<12,d} {pct_data:>6.2f}%        {d['with_200']:<12,d} {pct_200:>6.2f}%")

    print("\n[6] COVERAGE BY LISTING EXCHANGE:")
    print(f"{'Exchange':<14} {'Total Active':<14} {'With Data':<12} {'% With Data':<14} {'With >=200d':<12} {'% >=200d'}")
    print("-" * 75)
    for ex, d in sorted(ex_stats.items(), key=lambda x: x[1]["total"], reverse=True):
        pct_data = (d["with_data"] / d["total"] * 100) if d["total"] else 0
        pct_200 = (d["with_200"] / d["total"] * 100) if d["total"] else 0
        print(f"{ex:<14} {d['total']:<14,d} {d['with_data']:<12,d} {pct_data:>6.2f}%        {d['with_200']:<12,d} {pct_200:>6.2f}%")

    print("=" * 70)


if __name__ == "__main__":
    run_audit()
