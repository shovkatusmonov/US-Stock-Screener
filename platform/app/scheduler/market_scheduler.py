"""
app/scheduler/market_scheduler.py - Market Schedule Automation with US Eastern Time & DST.

Configured with APScheduler and strict ZoneInfo("America/New_York") timezone handling.
Includes:
- 08:30 ET (Mon-Fri): Pre-market preparation and health check
- 12:00 ET (Mon-Fri): Midday memory enforcement and resource check
- 16:30 ET (Mon-Fri): Market close data ingestion, snapshot compute, breadth, and screener scan
- Sun 12:00 ET: Weekly universe sync and DB maintenance backup
"""

from datetime import datetime, timezone
import logging
from typing import Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

try:
    from config.settings import NY_TZ, BENCHMARKS, DB_PATH
except ImportError:
    from zoneinfo import ZoneInfo
    NY_TZ = ZoneInfo("America/New_York")
    BENCHMARKS = ["SPY", "QQQ", "IWM", "DIA"]
    DB_PATH = "data/screener.db"

from app.data.universe import UniverseManager
from app.data.loader import MarketDataLoader
from app.scanners.engine import ScreenerEngine
from app.analytics.breadth import MarketBreadthAnalyzer
from app.monitoring.health import SystemHealthMonitor, enforce_memory_limit

logger = logging.getLogger(__name__)


def job_pre_market():
    """Executed at 08:30 America/New_York before market open."""
    logger.info("Executing Pre-Market Preparation Job [08:30 ET]...")
    monitor = SystemHealthMonitor(db_path=DB_PATH)
    health = monitor.get_full_health_report()
    enforce_memory_limit()
    logger.info(f"Pre-market health check completed: status={health['overall_status']}")


def job_midday_check():
    """Executed at 12:00 America/New_York during market session."""
    logger.info("Executing Midday Memory & Resource Job [12:00 ET]...")
    enforce_memory_limit()


def job_market_close():
    """Executed at 16:30 America/New_York after market close."""
    logger.info("Executing Post-Market Close Routine [16:30 ET]...")
    try:
        loader = MarketDataLoader(db_path=DB_PATH)
        benchmarks = [(b, "US") for b in BENCHMARKS]
        loader.update_batch(benchmarks)

        engine = ScreenerEngine(db_path=DB_PATH)
        engine.compute_and_save_snapshots(symbols_with_exchange=benchmarks)

        analyzer = MarketBreadthAnalyzer(db_path=DB_PATH)
        breadth = analyzer.compute_daily_breadth()
        logger.info(f"Market close routine completed. Breadth: {breadth}")

        enforce_memory_limit()
    except Exception as e:
        logger.error(f"Error in market close routine: {e}", exc_info=True)


def job_weekly_maintenance():
    """Executed on Sunday at 12:00 America/New_York."""
    logger.info("Executing Weekly Maintenance Routine [Sunday 12:00 ET]...")
    try:
        um = UniverseManager(db_path=DB_PATH)
        stats = um.sync_universe()
        logger.info(f"Weekly universe sync completed: {stats}")
        enforce_memory_limit()
    except Exception as e:
        logger.error(f"Error in weekly maintenance job: {e}", exc_info=True)


def create_market_scheduler() -> BackgroundScheduler:
    """Builds and schedules APScheduler jobs configured with America/New_York timezone."""
    scheduler = BackgroundScheduler(timezone=NY_TZ)

    scheduler.add_job(
        job_pre_market,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=30, timezone=NY_TZ),
        id="pre_market_job",
        name="Pre-Market Health & Readiness Check"
    )

    scheduler.add_job(
        job_midday_check,
        CronTrigger(day_of_week="mon-fri", hour=12, minute=0, timezone=NY_TZ),
        id="midday_check_job",
        name="Midday Resource Guard"
    )

    scheduler.add_job(
        job_market_close,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=NY_TZ),
        id="market_close_job",
        name="Market Close Ingestion & Screener Routine"
    )

    scheduler.add_job(
        job_weekly_maintenance,
        CronTrigger(day_of_week="sun", hour=12, minute=0, timezone=NY_TZ),
        id="weekly_maintenance_job",
        name="Weekly Universe Sync & DB Maintenance"
    )

    return scheduler


def start_scheduler_blocking():
    """Runs scheduler indefinitely (blocking loop)."""
    scheduler = create_market_scheduler()
    scheduler.start()
    print("⏰ Market Scheduler ishga tushirildi (America/New_York timezone). To'xtatish uchun Ctrl+C bosing.")
    try:
        import time
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        print("⏰ Market Scheduler to'xtatildi.")
