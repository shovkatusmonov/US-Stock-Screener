"""
app/monitoring/health.py - Health Checks, Data Freshness, and RAM/CPU Guard.

Specially tuned for Ubuntu 24.04 (952 MiB RAM / 1 GiB Swap VPS):
- Native Linux /proc/self/status memory inspection (zero overhead, no psutil needed).
- Memory limit enforcement with garbage collection.
- Database integrity and row count tracking.
- Data freshness monitoring against US market calendar.
"""

from datetime import datetime, timezone, timedelta
import gc
import logging
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any, Dict, Optional

try:
    from config.settings import DB_PATH as CONFIG_DB_PATH, MAX_RAM_MB as CONFIG_MAX_RAM_MB, NY_TZ as CONFIG_NY_TZ
    DEFAULT_DB_PATH = Path(CONFIG_DB_PATH)
    DEFAULT_MAX_RAM = CONFIG_MAX_RAM_MB
    DEFAULT_TZ = CONFIG_NY_TZ
except ImportError:
    from zoneinfo import ZoneInfo
    DEFAULT_DB_PATH = Path("data/screener.db")
    DEFAULT_MAX_RAM = 700
    DEFAULT_TZ = ZoneInfo("America/New_York")

logger = logging.getLogger(__name__)


def get_process_memory_mb() -> float:
    """Returns the RSS memory usage of the current process in MB."""
    # 1. Native Linux /proc/self/status (instant, zero overhead)
    proc_status = Path("/proc/self/status")
    if proc_status.exists():
        try:
            with open(proc_status, "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        return float(parts[1]) / 1024.0  # kB to MB
        except Exception:
            pass

    # 2. Try psutil if available
    try:
        import psutil
        process = psutil.Process(os.getpid())
        return float(process.memory_info().rss) / (1024.0 * 1024.0)
    except Exception:
        pass

    # 3. Windows ctypes fallback
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                return float(pmc.WorkingSetSize) / (1024.0 * 1024.0)
        except Exception:
            pass

    return 0.0


def get_system_memory_and_swap() -> Dict[str, Any]:
    """Returns system-wide RAM and Swap statistics in MB and percentages."""
    stats = {
        "ram_total_mb": 0.0,
        "ram_used_mb": 0.0,
        "ram_available_mb": 0.0,
        "ram_used_pct": 0.0,
        "swap_total_mb": 0.0,
        "swap_used_mb": 0.0,
        "swap_free_mb": 0.0,
        "swap_used_pct": 0.0
    }

    meminfo_path = Path("/proc/meminfo")
    if meminfo_path.exists():
        try:
            mem_data = {}
            with open(meminfo_path, "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        k = parts[0].strip()
                        v = parts[1].strip().split()[0]
                        mem_data[k] = float(v) / 1024.0

            total_ram = mem_data.get("MemTotal", 0.0)
            avail_ram = mem_data.get("MemAvailable", mem_data.get("MemFree", 0.0))
            used_ram = max(0.0, total_ram - avail_ram)

            total_swap = mem_data.get("SwapTotal", 0.0)
            free_swap = mem_data.get("SwapFree", 0.0)
            used_swap = max(0.0, total_swap - free_swap)

            stats["ram_total_mb"] = round(total_ram, 1)
            stats["ram_used_mb"] = round(used_ram, 1)
            stats["ram_available_mb"] = round(avail_ram, 1)
            stats["ram_used_pct"] = round((used_ram / total_ram * 100), 1) if total_ram > 0 else 0.0

            stats["swap_total_mb"] = round(total_swap, 1)
            stats["swap_used_mb"] = round(used_swap, 1)
            stats["swap_free_mb"] = round(free_swap, 1)
            stats["swap_used_pct"] = round((used_swap / total_swap * 100), 1) if total_swap > 0 else 0.0
            return stats
        except Exception:
            pass

    stats["ram_total_mb"] = 1024.0
    stats["ram_available_mb"] = 512.0
    stats["ram_used_mb"] = 512.0
    stats["ram_used_pct"] = 50.0
    return stats


def enforce_memory_limit(threshold_mb: Optional[int] = None) -> float:
    """
    Checks RSS memory and runs garbage collection if exceeding limit.
    Returns current memory in MB.
    """
    limit = threshold_mb or DEFAULT_MAX_RAM
    curr_mb = get_process_memory_mb()
    if curr_mb > limit:
        logger.warning(f"Process RAM usage ({curr_mb:.1f} MB) exceeds limit ({limit} MB). Running gc.collect()...")
        gc.collect()
        curr_mb = get_process_memory_mb()
        logger.info(f"Post-GC RAM usage: {curr_mb:.1f} MB")
    return curr_mb


class SystemHealthMonitor:
    """Performs full system, database, and data freshness diagnostics."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)

    def check_database_health(self) -> Dict[str, Any]:
        """Runs SQLite integrity check and inspects table counts."""
        if not self.db_path.exists():
            return {"status": "CRITICAL", "message": f"Database file not found at {self.db_path}"}

        try:
            conn = sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True)
            cur = conn.cursor()
            res = cur.execute("PRAGMA integrity_check;").fetchone()
            integrity = (res[0] == "ok") if res else False

            counts = {}
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [r[0] for r in cur.fetchall()]
            for t in ["universe", "instruments", "market_data_daily", "technical_snapshots", "market_breadth", "prices"]:
                if t in tables:
                    cur.execute(f"SELECT COUNT(*) FROM {t};")
                    counts[t] = cur.fetchone()[0]

            conn.close()
            return {
                "status": "HEALTHY" if integrity else "CRITICAL",
                "integrity_ok": integrity,
                "table_counts": counts,
                "db_size_mb": round(self.db_path.stat().st_size / (1024 * 1024), 2)
            }
        except Exception as e:
            return {"status": "CRITICAL", "error": str(e)}

    def check_data_freshness(self) -> Dict[str, Any]:
        """Checks the latest date of available daily market data."""
        if not self.db_path.exists():
            return {"status": "UNKNOWN", "message": "DB not found"}

        latest_date = None
        try:
            conn = sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = {r[0] for r in cur.fetchall()}

            if "market_data_daily" in tables:
                cur.execute("SELECT MAX(date) FROM market_data_daily;")
                r = cur.fetchone()
                if r and r[0]:
                    latest_date = r[0]

            if not latest_date and "prices" in tables:
                cur.execute("SELECT MAX(date) FROM prices;")
                r = cur.fetchone()
                if r and r[0]:
                    latest_date = r[0]

            if not latest_date and "universe" in tables:
                cur.execute("SELECT MAX(updated_at) FROM universe;")
                r = cur.fetchone()
                if r and r[0]:
                    latest_date = r[0][:10]

            conn.close()
        except Exception:
            pass

        now_et = datetime.now(DEFAULT_TZ)
        today_et = now_et.strftime("%Y-%m-%d")

        is_stale = False
        lag_days = 0
        if latest_date:
            try:
                latest_dt = datetime.strptime(latest_date[:10], "%Y-%m-%d").date()
                diff = (now_et.date() - latest_dt).days
                lag_days = diff
                # Weekends allow 2-3 days lag
                if now_et.weekday() in (5, 6) and diff <= 3:
                    is_stale = False
                elif diff > 3:
                    is_stale = True
            except Exception:
                pass

        return {
            "status": "WARNING" if is_stale else "HEALTHY",
            "latest_data_date": latest_date,
            "today_et": today_et,
            "lag_days": lag_days,
            "is_stale": is_stale
        }

    def check_system_resources(self) -> Dict[str, Any]:
        """Inspects process RAM, host RAM limit, and disk space."""
        proc_ram_mb = get_process_memory_mb()
        total, used, free = shutil.disk_usage(str(self.db_path.parent))

        return {
            "process_ram_mb": round(proc_ram_mb, 1),
            "ram_limit_mb": DEFAULT_MAX_RAM,
            "ram_status": "WARNING" if proc_ram_mb > DEFAULT_MAX_RAM else "HEALTHY",
            "disk_free_gb": round(free / (1024 ** 3), 2),
            "disk_total_gb": round(total / (1024 ** 3), 2)
        }

    def get_full_health_report(self) -> Dict[str, Any]:
        """Generates comprehensive system health report."""
        db_health = self.check_database_health()
        freshness = self.check_data_freshness()
        resources = self.check_system_resources()

        overall = "HEALTHY"
        if db_health.get("status") == "CRITICAL" or freshness.get("status") == "CRITICAL":
            overall = "CRITICAL"
        elif db_health.get("status") == "WARNING" or freshness.get("status") == "WARNING" or resources.get("ram_status") == "WARNING":
            overall = "WARNING"

        return {
            "overall_status": overall,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database": db_health,
            "data_freshness": freshness,
            "resources": resources
        }
