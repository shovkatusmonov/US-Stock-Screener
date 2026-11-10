"""
scripts/run_backfill_pipeline.py - Controlled All-Time Historical Backfill Runner.

Designed for low-memory VPS (952 MiB RAM) to systematically expand
ALL AVAILABLE historical OHLCV data across US instruments.

Features:
- Retrieve maximum available provider history (period='max').
- Conservative batches (15 tickers per batch).
- Priority: Common Stocks (CS) -> ETFs -> ADRs -> Preferred Shares (PRF) -> Others.
- Polite rate-limiting (0.35s delay) + automatic cooldown on 429.
- Restartable after interruption (zero duplicate fetching).
- Periodic snapshot refresh and all-time progress reporting.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# Ensure project root in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.data.backfill import HistoricalBackfiller
from app.monitoring.logger import setup_logging
from app.monitoring.health import get_process_memory_mb, get_system_memory_and_swap
from app.scanners.engine import ScreenerEngine

logger = setup_logging()


def main():
    parser = argparse.ArgumentParser(description="US Stock Screener All-Time Historical Backfill Pipeline")
    parser.add_argument("--batch-size", type=int, default=15, help="Batch size (default 15)")
    parser.add_argument("--max-symbols", type=int, default=150, help="Max total symbols to process in this run (default 150)")
    parser.add_argument("--asset-class", type=str, default=None, help="Filter by CS, ETF, ADR, PRF (default: all prioritized)")
    parser.add_argument("--exchange", type=str, default=None, help="Filter by exchange (e.g. Q, N, P)")
    parser.add_argument("--delay", type=float, default=0.35, help="Delay between tickers in seconds (default 0.35)")
    parser.add_argument("--all-time", action="store_true", default=True, help="Fetch all available provider history (period='max')")
    parser.add_argument("--refresh-snapshots", action="store_true", help="Refresh snapshots after backfill")

    args = parser.parse_args()

    print("=" * 75)
    print("      US STOCK SCREENER — ALL-TIME HISTORICAL DATA BACKFILL")
    print("=" * 75)
    print(f"Target Max Symbols : {args.max_symbols}")
    print(f"Batch Size         : {args.batch_size}")
    print(f"Asset Class Filter : {args.asset_class or 'All (Priority: CS -> ETF -> ADR -> PRF)'}")
    print(f"Exchange Filter    : {args.exchange or 'All'}")
    print(f"Delay Per Ticker   : {args.delay}s")
    print(f"All-Time Mode      : {args.all_time} (period='max')")
    print("=" * 75)

    bf = HistoricalBackfiller()
    engine = ScreenerEngine()

    start_t = time.time()
    res = bf.run_backfill(
        asset_class=args.asset_class,
        exchange=args.exchange,
        max_symbols=args.max_symbols,
        batch_size=args.batch_size,
        delay_between_tickers=args.delay,
        all_time=args.all_time
    )

    print("\n[+] Backfill Execution Results:")
    print(f"  Targeted          : {res['total_targeted']}")
    print(f"  Processed         : {res['processed']}")
    print(f"  All-Time Complete : {res['all_time_complete']}")
    print(f"  Partial History   : {res['partial_history']}")
    print(f"  No Data           : {res['no_data']}")
    print(f"  Delisted          : {res['delisted']}")
    print(f"  Rate Limited      : {res['rate_limited']}")
    print(f"  Errors            : {res['errors']}")
    print(f"  Total Bars Saved  : {res['total_bars_saved']:,}")
    print(f"  Duration          : {res['duration_sec']}s")
    print(f"  Process RAM       : {res['ram_mb']:.1f} MB")

    if args.refresh_snapshots or res["total_bars_saved"] > 0:
        print("\n[*] Updating technical snapshots for updated instruments...")
        snap_res = engine.refresh_snapshots_incremental()
        print(f"[+] Snapshots updated: {snap_res.get('generated', 0)} generated, {snap_res.get('up_to_date', 0)} up to date.")

    print("\n[*] Generating Daily Progress Report:")
    rep = bf.get_daily_progress_report()
    print("-" * 75)
    print(f"Total Active Instruments    : {rep['total_instruments']:,}")
    print(f"All-Time Complete           : {rep['all_time_complete']:,} ({rep['coverage_percentage']:.2f}%)")
    print(f"Partial History             : {rep['partial_history']:,}")
    print(f"Pending Backfill            : {rep['pending']:,}")
    print(f"No Data (Provider Empty)    : {rep['no_data']:,}")
    print(f"Delisted (No Quotes)        : {rep['delisted']:,}")
    print(f"Rate Limited (Throttled)    : {rep['rate_limited']:,}")
    print(f"Errors                      : {rep['errors']:,}")
    print(f"Historical Date Range in DB : {rep['earliest_historical_date']} -> {rep['latest_historical_date']}")
    print(f"Total Daily Bars in DB      : {rep['total_bars_in_db']:,}")
    print("-" * 75)
    for ac, cnt in rep.get('class_all_time_complete', {}).items():
        tot = rep['active_by_class'].get(ac, 0)
        pct = (cnt / tot * 100) if tot else 0.0
        print(f"  - {ac:<6}: {cnt:>5,} / {tot:>5,} ({pct:>5.1f}%)")
    print("-" * 75)
    ram = rep['system_ram']
    swap = rep['system_swap']
    print(f"System RAM  : {ram['used_mb']:.0f} MB / {ram['total_mb']:.0f} MB ({ram['used_pct']}%) [Avail: {ram['available_mb']:.0f} MB]")
    print(f"System Swap : {swap['used_mb']:.0f} MB / {swap['total_mb']:.0f} MB ({swap['used_pct']}%)")
    print("=" * 75)


if __name__ == "__main__":
    main()
