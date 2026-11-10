"""
main.py - Unified Command Line Interface for US Stock Screener.

Subcommands:
  universe   - Manage instruments universe (stats, sync, migrate, list)
  data       - Market data updates and inspection (update, backfill, info)
  screen     - Run technical screening presets or custom scans
  quote      - Instant technical analysis quote for a single symbol
  breadth    - Market breadth and benchmark health report
  health     - System resources (RAM/CPU/Disk) and database integrity
  bot        - Start Telegram bot daemon
  scheduler  - Start background APScheduler daemon
"""

import argparse
import logging
import os
from pathlib import Path
import sys
from typing import List, Optional

# Ensure project root is in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.settings import (
    DB_PATH,
    BENCHMARKS,
    MAX_RAM_MB,
    TELEGRAM_BOT_TOKEN
)
from app.monitoring.logger import setup_logging
from app.monitoring.health import SystemHealthMonitor, enforce_memory_limit

logger = setup_logging()


def cmd_universe(args: argparse.Namespace) -> int:
    from app.data.universe import UniverseManager
    mgr = UniverseManager()

    if args.action == "stats":
        stats = mgr.get_stats()
        print("\n" + "=" * 50)
        print("         US STOCK SCREENER — UNIVERSE STATS")
        print("=" * 50)
        print(f"Total Instruments in Catalog : {stats['total']:,}")
        print("\nStatus Distribution:")
        for status, cnt in stats["by_status"].items():
            print(f"  - {status:<15}: {cnt:>6,}")
        print("\nActive by Asset Class:")
        for ac, cnt in sorted(stats["by_asset_class"].items()):
            print(f"  - {ac:<15}: {cnt:>6,}")
        print("\nActive by Exchange:")
        for ex, cnt in sorted(stats["by_exchange"].items()):
            print(f"  - {ex:<15}: {cnt:>6,}")
        print("=" * 50)
        return 0

    elif args.action == "migrate":
        print("[*] Starting non-destructive migration from legacy universe table...")
        res = mgr.migrate_from_legacy_universe()
        print(f"[+] Migration complete: {res}")
        return 0

    elif args.action == "sync":
        print("[*] Fetching authoritative symbols from NASDAQ Trader Directory...")
        res = mgr.sync_universe()
        print(f"[+] Dynamic sync complete: {res}")
        return 0

    elif args.action == "list":
        instruments = mgr.get_instruments(
            asset_class=args.asset_class,
            exchange=args.exchange,
            status=args.status,
            limit=args.limit
        )
        print(f"\nListing {len(instruments)} instruments:")
        print(f"{'SYMBOL':<10} {'EXCHANGE':<12} {'CLASS':<6} {'ETF':<5} {'NAME'}")
        print("-" * 65)
        for inst in instruments:
            print(f"{inst.symbol:<10} {inst.exchange:<12} {inst.asset_class:<6} {str(inst.is_etf):<5} {inst.name[:30]}")
        return 0

    elif args.action == "audit":
        from scripts.audit_coverage import run_audit
        run_audit()
        return 0

    else:
        print(f"Unknown universe action: {args.action}. Use stats, migrate, sync, list, or audit.")
        return 1


def cmd_data(args: argparse.Namespace) -> int:
    from app.data.loader import MarketDataLoader
    from app.data.universe import UniverseManager

    loader = MarketDataLoader()
    mgr = UniverseManager()

    if args.action == "info":
        if not args.symbol:
            print("[!] Error: --symbol is required for data info.")
            return 1
        sym = args.symbol.upper()
        last_date = loader.get_last_date(sym)
        df = loader.load_ohlcv(sym, limit=5)
        print(f"\n--- Market Data Info: {sym} ---")
        print(f"Latest Stored Date: {last_date or 'No data'}")
        if not df.empty:
            print(f"Recent {len(df)} bars:")
            print(df[["open", "high", "low", "close", "volume"]])
        else:
            print("No bars found in local database.")
        return 0

    elif args.action == "update":
        if args.symbols:
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            symbols_with_ex = [(s, "US") for s in symbols]
        else:
            limit = args.limit or 20
            print(f"[*] Selecting first {limit} active instruments...")
            active = mgr.get_instruments(status="ACTIVE", limit=limit)
            symbols_with_ex = [(i.symbol, i.exchange) for i in active]

        print(f"[*] Updating daily OHLCV for {len(symbols_with_ex)} symbols...")
        res = loader.update_batch(symbols_with_ex)
        print(f"[+] Update complete: {res}")
        return 0

    elif args.action == "backfill":
        if not args.symbols:
            print("[!] Error: --symbols is required for backfill (e.g. --symbols SPY,QQQ,AAPL).")
            return 1
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        days = 365
        if args.period == "2y": days = 730
        elif args.period == "6mo": days = 180
        elif args.period == "1mo": days = 30
        from datetime import datetime, timedelta, timezone
        start_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        print(f"[*] Backfilling {len(symbols)} symbols from start={start_date}...")
        for sym in symbols:
            cnt = loader.update_symbol_daily(sym, "US", default_start=start_date)
            print(f"  [+] {sym}: saved {cnt} bars")
        return 0

    elif args.action == "backfill-universe":
        from app.data.backfill import HistoricalBackfiller
        bf = HistoricalBackfiller()
        limit = args.limit or 20
        batch_size = args.batch_size or 15
        delay = args.delay if args.delay is not None else 0.35
        lookback = args.lookback_days or 365
        min_bars = args.min_bars or 200
        include_no_data = bool(args.include_no_data)
        print(f"[*] Starting controlled historical backfill (limit={limit}, batch_size={batch_size}, asset_class={args.asset_class}, delay={delay}s, min_bars={min_bars})...")
        res = bf.run_backfill(
            asset_class=args.asset_class,
            exchange=args.exchange,
            max_symbols=limit,
            batch_size=batch_size,
            delay_between_tickers=delay,
            lookback_days=lookback,
            min_bars_needed=min_bars,
            include_no_data=include_no_data
        )
        print(f"[+] Backfill complete: {res}")
        return 0

    elif args.action == "progress":
        from app.data.backfill import HistoricalBackfiller
        bf = HistoricalBackfiller()
        rep = bf.get_daily_progress_report()
        print("\n" + "=" * 70)
        print("          US STOCK SCREENER — DAILY PROGRESS REPORT")
        print("=" * 70)
        print(f"Total Active Instruments    : {rep['total_instruments']:,}")
        print(f"Completed (>= 200 days)     : {rep['completed_200_days']:,} ({rep['coverage_percentage']:.2f}%)")
        print(f"Completed (>= 20 days)      : {rep['completed_20_days']:,}")
        print(f"Pending Backfill            : {rep['pending']:,}")
        print(f"No Data (Dead / Delisted)   : {rep['no_data']:,}")
        print(f"Rate Limited (Throttled)    : {rep['rate_limited']:,}")
        print(f"Errors                      : {rep['errors']:,}")
        print("-" * 70)
        print("Completed (>= 200d) by Asset Class:")
        for ac, cnt in rep['class_completed_200'].items():
            tot = rep['active_by_class'].get(ac, 0)
            pct = (cnt / tot * 100) if tot else 0.0
            print(f"  - {ac:<6}: {cnt:>5,} / {tot:>5,} ({pct:>5.1f}%)")
        print("-" * 70)
        ram = rep['system_ram']
        swap = rep['system_swap']
        print(f"Process RAM                 : {rep['process_ram_mb']:.1f} MB (Limit: {MAX_RAM_MB} MB)")
        print(f"System RAM                  : {ram['used_mb']:.1f} MB used / {ram['total_mb']:.1f} MB total ({ram['used_pct']}%) [Avail: {ram['available_mb']:.1f} MB]")
        print(f"System Swap                 : {swap['used_mb']:.1f} MB used / {swap['total_mb']:.1f} MB total ({swap['used_pct']}%) [Free: {swap['free_mb']:.1f} MB]")
        print("=" * 70)
        return 0

    else:
        print(f"Unknown data action: {args.action}. Use info, update, backfill, backfill-universe, or progress.")
        return 1


def cmd_screen(args: argparse.Namespace) -> int:
    from app.scanners.presets import PRESETS
    from app.scanners.engine import ScreenerEngine
    from app.telegram.formatters import format_screener_results_table

    engine = ScreenerEngine()

    if args.refresh_snapshots:
        print("[*] Incrementally refreshing technical snapshots...")
        stats = engine.refresh_snapshots_incremental()
        print(f"[+] Snapshot refresh complete: {stats}")
        return 0

    if args.validate_presets:
        print("[*] Running all 8 presets against real database snapshots...")
        val = engine.validate_all_presets()
        print("\n" + "=" * 75)
        print("               SCREENER PRESETS VALIDATION REPORT")
        print("=" * 75)
        print(f"Eligible Instruments: {val['eligible_instruments']} | Total Snapshots: {val['total_snapshots']} | Duration: {val['total_duration_sec']}s\n")
        print(f"{'Preset Name':<20} {'Status':<8} {'Matches':<9} {'Duration':<12} {'Top Matches'}")
        print("-" * 75)
        for name, info in val["presets"].items():
            tickers_str = ", ".join(info.get("top_tickers", [])) or "None"
            print(f"{name:<20} {info['status']:<8} {info['matched_count']:<9} {info['duration_ms']:>6.1f} ms    {tickers_str}")
        print("=" * 75)
        return 0

    if args.list_presets:
        print("\n" + "=" * 60)
        print("          AVAILABLE SCREENER PRESETS")
        print("=" * 60)
        for name, preset in PRESETS.items():
            print(f"\n* {name}")
            print(f"  Description: {preset.description}")
            criteria_str = []
            if preset.min_price: criteria_str.append(f"Price >= ${preset.min_price}")
            if preset.min_rel_vol: criteria_str.append(f"RelVol >= {preset.min_rel_vol}x")
            if preset.trend_regime: criteria_str.append(f"Trend == {preset.trend_regime}")
            if preset.min_rsi: criteria_str.append(f"RSI >= {preset.min_rsi}")
            if preset.max_rsi: criteria_str.append(f"RSI <= {preset.max_rsi}")
            if preset.min_dist_52w_high is not None: criteria_str.append(f"52w High >= {preset.min_dist_52w_high*100:.1f}%")
            if criteria_str:
                print(f"  Criteria   : {', '.join(criteria_str)}")
        print("=" * 60)
        return 0

    preset_name = args.preset or "bullish_breakout"
    if preset_name not in PRESETS:
        print(f"[!] Unknown preset '{preset_name}'. Use --list-presets to view options.")
        return 1

    engine = ScreenerEngine()

    if args.compute_snapshots:
        print("[*] Precomputing technical snapshots from available daily bars...")
        cnt = engine.compute_and_save_snapshots()
        print(f"[+] Saved {cnt} technical snapshots.")

    print(f"[*] Running screen with preset: '{preset_name}' (limit={args.limit})...")
    results = engine.screen(preset_name, limit=args.limit)
    print(f"[+] Matches found: {len(results)}")

    if results:
        pages = format_screener_results_table(results, preset_name=preset_name)
        for page in pages:
            print("\n" + page)
    else:
        print("No instruments matched this criteria or snapshots have not been generated yet.")
        print("Tip: Run `python main.py data update` and `python main.py screen --compute-snapshots`.")
    return 0


def cmd_quote(args: argparse.Namespace) -> int:
    if not args.symbol:
        print("[!] Error: --symbol is required.")
        return 1
    sym = args.symbol.upper()
    from app.data.loader import MarketDataLoader
    from app.indicators.technical import create_snapshot_row
    from app.telegram.formatters import format_quote_card

    loader = MarketDataLoader()
    df = loader.load_ohlcv(sym, limit=260)
    if df.empty or len(df) < 20:
        print(f"[*] Local data for {sym} insufficient ({len(df)} bars). Fetching from provider...")
        loader.update_symbol_daily(sym, "US")
        df = loader.load_ohlcv(sym, limit=260)

    if df.empty:
        print(f"[!] Failed to fetch market data for {sym}.")
        return 1

    snap = create_snapshot_row(sym, "US", df)
    if not snap:
        print(f"[!] Not enough history to compute indicators for {sym} (minimum 20 bars needed).")
        return 1

    card = format_quote_card(snap)
    print("\n" + card)
    return 0


def cmd_breadth(args: argparse.Namespace) -> int:
    from app.analytics.breadth import MarketBreadthAnalyzer
    from app.telegram.formatters import format_market_breadth_card

    analyzer = MarketBreadthAnalyzer()
    print("[*] Calculating market breadth across active universe & benchmarks...")
    report = analyzer.compute_daily_breadth()
    if not report:
        print("[!] No technical snapshots available to compute breadth.")
        print("    Run `python main.py data update` and `python main.py screen --compute-snapshots` first.")
        return 0

    benchmarks = analyzer.get_benchmark_summary(report.get("date"))
    card = format_market_breadth_card(report, benchmarks)
    print("\n" + card)
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    monitor = SystemHealthMonitor()
    report = monitor.get_full_health_report()

    print("\n" + "=" * 50)
    print("      US STOCK SCREENER — SYSTEM HEALTH CHECK")
    print("=" * 50)
    print(f"Timestamp (UTC)     : {report['timestamp']}")
    print(f"Overall Status      : {report['overall_status']}")
    print("-" * 50)
    res = report["resources"]
    print(f"Process RSS RAM     : {res['process_ram_mb']:.1f} MB (Limit: {res['ram_limit_mb']} MB)")
    print(f"RAM Status          : {res['ram_status']}")
    print(f"Disk Free Space     : {res['disk_free_gb']:.2f} GB / {res['disk_total_gb']:.2f} GB")
    print("-" * 50)
    db = report["database"]
    print(f"Database Integrity  : {'OK' if db.get('integrity_ok') else 'FAILED'}")
    print(f"Database Size       : {db.get('db_size_mb', 0.0):.2f} MB")
    print("Table Record Counts :")
    for tbl, cnt in db.get("table_counts", {}).items():
        print(f"  - {tbl:<20}: {cnt:>8,}")
    print("-" * 50)
    freshness = report["data_freshness"]
    print(f"Data Freshness      : {freshness['status']}")
    print(f"Latest Stored Date  : {freshness.get('latest_data_date') or 'N/A'}")
    print(f"Lag Days (vs NY)    : {freshness.get('lag_days', 0)} days (Stale: {freshness.get('is_stale')})")
    print("=" * 50)
    return 0 if report["overall_status"] in ("HEALTHY", "WARNING") else 1


def cmd_bot(args: argparse.Namespace) -> int:
    if not TELEGRAM_BOT_TOKEN:
        print("[!] TELEGRAM_BOT_TOKEN is not configured in .env!")
        print("    Please set TELEGRAM_BOT_TOKEN in your environment or .env file.")
        return 1
    from app.telegram.bot import run_bot
    print("[*] Starting Telegram Screener Bot daemon...")
    run_bot()
    return 0


def cmd_scheduler(args: argparse.Namespace) -> int:
    from app.scheduler.market_scheduler import MarketScheduler
    print("[*] Starting US Market Scheduler (APScheduler ET / DST aware)...")
    sched = MarketScheduler()
    sched.start(blocking=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="screener",
        description="METRIQA-grade US Stock Market Screener CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # universe
    p_univ = subparsers.add_parser("universe", help="Manage instruments universe")
    p_univ.add_argument("action", choices=["stats", "sync", "migrate", "list", "audit"], help="Action to execute")
    p_univ.add_argument("--asset-class", type=str, default=None, help="Filter by CS, ETF, ADR, PRF, etc.")
    p_univ.add_argument("--exchange", type=str, default=None, help="Filter by NASDAQ, NYSE, etc.")
    p_univ.add_argument("--status", type=str, default="ACTIVE", help="Filter by ACTIVE/DELISTED")
    p_univ.add_argument("--limit", type=int, default=50, help="Max items to list")

    # data
    p_data = subparsers.add_parser("data", help="Market data management")
    p_data.add_argument("action", choices=["info", "update", "backfill", "backfill-universe", "progress"], help="Action to execute")
    p_data.add_argument("--symbol", type=str, default=None, help="Single symbol for info")
    p_data.add_argument("--symbols", type=str, default=None, help="Comma-separated symbols")
    p_data.add_argument("--period", type=str, default="1y", help="Period for backfill (e.g. 1mo, 1y, 2y)")
    p_data.add_argument("--limit", type=int, default=20, help="Number of symbols to update or backfill")
    p_data.add_argument("--batch-size", type=int, default=15, help="Batch size for backfill (default 15)")
    p_data.add_argument("--delay", type=float, default=0.35, help="Delay between tickers in seconds (default 0.35)")
    p_data.add_argument("--lookback-days", type=int, default=365, help="Historical days to backfill (default 365)")
    p_data.add_argument("--min-bars", type=int, default=200, help="Target minimum trading days of history (default 200)")
    p_data.add_argument("--include-no-data", action="store_true", help="Re-attempt tickers previously logged as NO_DATA")
    p_data.add_argument("--asset-class", type=str, default=None, help="Filter backfill by asset class (e.g. CS, ETF)")
    p_data.add_argument("--exchange", type=str, default=None, help="Filter backfill by exchange")

    # screen
    p_screen = subparsers.add_parser("screen", help="Run screening algorithms")
    p_screen.add_argument("--preset", type=str, default="bullish_breakout", help="Preset name")
    p_screen.add_argument("--list-presets", action="store_true", help="List all available presets")
    p_screen.add_argument("--limit", type=int, default=20, help="Max results")
    p_screen.add_argument("--compute-snapshots", action="store_true", help="Precompute technical snapshots first")
    p_screen.add_argument("--refresh-snapshots", action="store_true", help="Incrementally refresh technical snapshots")
    p_screen.add_argument("--validate-presets", action="store_true", help="Run validation benchmark across all 8 presets")

    # quote
    p_quote = subparsers.add_parser("quote", help="Technical analysis quote for a symbol")
    p_quote.add_argument("--symbol", type=str, required=True, help="Stock or ETF ticker")

    # breadth
    subparsers.add_parser("breadth", help="Market breadth & benchmarks report")

    # health
    subparsers.add_parser("health", help="Check system resources and database health")

    # bot
    subparsers.add_parser("bot", help="Run private Telegram bot daemon")

    # scheduler
    subparsers.add_parser("scheduler", help="Run APScheduler background daemon")

    return parser


def main() -> int:
    parser = build_parser()
    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    args = parser.parse_args()
    if args.command == "universe":
        return cmd_universe(args)
    elif args.command == "data":
        return cmd_data(args)
    elif args.command == "screen":
        return cmd_screen(args)
    elif args.command == "quote":
        return cmd_quote(args)
    elif args.command == "breadth":
        return cmd_breadth(args)
    elif args.command == "health":
        return cmd_health(args)
    elif args.command == "bot":
        return cmd_bot(args)
    elif args.command == "scheduler":
        return cmd_scheduler(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
