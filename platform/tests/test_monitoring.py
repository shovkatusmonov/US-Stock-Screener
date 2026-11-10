"""
tests/test_monitoring.py - Tests for RAM/CPU Guard, Health Diagnostics, and Logging.
"""

from pathlib import Path
import sqlite3
import pytest
from app.monitoring.health import (
    get_process_memory_mb,
    enforce_memory_limit,
    SystemHealthMonitor
)
from app.monitoring.logger import setup_logging


def test_memory_retrieval_on_linux_or_win():
    mem = get_process_memory_mb()
    assert isinstance(mem, float)
    assert mem >= 0.0

    post_mem = enforce_memory_limit(threshold_mb=10000)
    assert isinstance(post_mem, float)


def test_health_monitor_on_database(tmp_path):
    test_db = tmp_path / "test_health.db"
    conn = sqlite3.connect(str(test_db))
    conn.execute("""
        CREATE TABLE universe (
            ticker TEXT PRIMARY KEY,
            name TEXT,
            exchange TEXT,
            is_etf INTEGER DEFAULT 0,
            source TEXT,
            updated_at TEXT
        );
    """)
    conn.execute("INSERT INTO universe VALUES ('AAPL', 'Apple Inc', 'Q', 0, 'nasdaq', '2026-11-08T20:00:00')")
    conn.commit()
    conn.close()

    monitor = SystemHealthMonitor(db_path=test_db)
    rep = monitor.get_full_health_report()

    assert rep["overall_status"] in ("HEALTHY", "WARNING")
    assert rep["database"]["status"] == "HEALTHY"
    assert rep["database"]["integrity_ok"] is True
    assert rep["database"]["table_counts"]["universe"] == 1
    assert "resources" in rep
    assert rep["resources"]["disk_free_gb"] > 0


def test_logger_setup(tmp_path):
    test_log = tmp_path / "test.log"
    log = setup_logging(log_file=test_log, level="DEBUG")
    log.info("Testing structured logging")
    assert test_log.exists()
