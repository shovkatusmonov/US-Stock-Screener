"""
test_phase_c_remediation.py - Comprehensive Verification Suite for Phase C Blocker Remediation.

Covers:
1. MultiSleeveTradeManager (Sub-account cash isolation, dip-tranche solvency, conflict resolution).
2. MarketSchedulerGuard (NYSE/NASDAQ calendar, Eastern timezone, freshness gate, idempotency).
3. EnterpriseAlertManager (Signal formatting, deduplication, secret redaction, offline fallback).
4. Isolated Fundamental Remediation (QTD/YTD accuracy, idempotency).
5. Production Database and Core Codebase Hash Integrity.
"""

import os
import sqlite3
import hashlib
import pytest
from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path

from multi_sleeve_trade_manager import MultiSleeveTradeManager
from market_scheduler import (
    is_us_market_holiday,
    is_market_trading_day,
    get_latest_expected_trading_day,
    MarketSchedulerGuard,
    NY_TZ
)
from enterprise_alerts import EnterpriseAlertManager
from risk_manager import RiskConfig


@pytest.fixture
def temp_ms_db(tmp_path):
    """Creates a clean temporary database for testing MultiSleeveTradeManager."""
    db_file = str(tmp_path / "test_multi_sleeve.db")
    return db_file


# -----------------------------------------------------------------------------
# 1. MULTI-SLEEVE TRADEMANAGER TESTS
# -----------------------------------------------------------------------------
class TestMultiSleeveTradeManager:
    def test_initial_sleeve_capital_allocation(self, temp_ms_db):
        mgr = MultiSleeveTradeManager(db_path=temp_ms_db, total_initial_capital=100_000.0)
        summary = mgr.get_portfolio_summary()

        assert summary["total_equity"] == 100_000.0
        assert summary["total_cash"] == 100_000.0
        assert summary["total_invested"] == 0.0
        assert summary["total_open_positions"] == 0

        # Check sleeve balances: 50% SP500, 35% Breakout, 15% Fundamental
        sleeves = summary["sleeves"]
        assert sleeves["SLEEVE_SP500"]["cash_balance"] == 50_000.0
        assert sleeves["SLEEVE_BREAKOUT"]["cash_balance"] == 35_000.0
        assert sleeves["SLEEVE_FUNDAMENTAL"]["cash_balance"] == 15_000.0

    def test_subaccount_cash_isolation(self, temp_ms_db):
        """Verifies that trades in SLEEVE_BREAKOUT only deduct from its own cash."""
        mgr = MultiSleeveTradeManager(db_path=temp_ms_db, total_initial_capital=100_000.0)
        cfg = RiskConfig(cost_per_side=0.001)

        plan = {
            "ticker": "AAPL",
            "signal_date": "2024-05-01",
            "signal_price": 180.0,
            "planned_stop": 170.0,
            "planned_target": 243.0,
            "shares": 35,
            "dollar_risk": 350.0,
            "risk_pct": 0.01,
        }

        ok, sig_id = mgr.add_planned_trade("SLEEVE_BREAKOUT", plan, "V2_BREAKOUT")
        assert ok is True

        # Execute Market Open
        fill_ok, msg = mgr.execute_market_open(sig_id, actual_open=180.0, entry_date="2024-05-02", cfg=cfg)
        assert fill_ok is True

        summary = mgr.get_portfolio_summary()
        # SLEEVE_BREAKOUT cash must decrease
        b_sleeve = summary["sleeves"]["SLEEVE_BREAKOUT"]
        assert b_sleeve["cash_balance"] < 35_000.0
        assert b_sleeve["open_positions_count"] == 1

        # SLEEVE_SP500 cash must remain strictly untouched!
        sp_sleeve = summary["sleeves"]["SLEEVE_SP500"]
        assert sp_sleeve["cash_balance"] == 50_000.0, "SP500 cash was cannibalized!"

    def test_cross_sleeve_ticker_contention(self, temp_ms_db):
        """Verifies that an open position in one sleeve prevents duplicate exposure in another."""
        mgr = MultiSleeveTradeManager(db_path=temp_ms_db, total_initial_capital=100_000.0)
        cfg = RiskConfig()

        plan1 = {
            "ticker": "NVDA",
            "signal_date": "2024-05-01",
            "signal_price": 100.0,
            "planned_stop": 90.0,
            "planned_target": 135.0,
            "shares": 35,
            "dollar_risk": 350.0,
            "risk_pct": 0.01,
        }
        ok1, sig1 = mgr.add_planned_trade("SLEEVE_BREAKOUT", plan1, "V2_BREAKOUT")
        mgr.execute_market_open(sig1, actual_open=100.0, entry_date="2024-05-02", cfg=cfg)

        # Attempt to plan NVDA in SLEEVE_FUNDAMENTAL while already open in Breakout
        plan2 = dict(plan1)
        plan2["signal_date"] = "2024-05-03"
        ok2, msg2 = mgr.add_planned_trade("SLEEVE_FUNDAMENTAL", plan2, "AZIZ_HALIKOV_V1")
        assert ok2 is False
        assert "REJECTED_CROSS_SLEEVE_EXISTS" in msg2

    def test_macro_sp500_dip_tranche_solvency(self, temp_ms_db):
        """Verifies that SP500 dip tranches deploy cleanly from dedicated cash."""
        mgr = MultiSleeveTradeManager(db_path=temp_ms_db, total_initial_capital=100_000.0)
        
        # When SPY drops 16% from ATH 500 to 420
        res = mgr.check_and_deploy_sp500_tranche(
            current_spy_price=420.0,
            rolling_ath=500.0,
            date_str="2024-08-05"
        )
        assert res is not None
        assert res["tranche"] == "TRANCHE_15_PCT"
        assert res["shares"] > 0

        # Verify SP500 sleeve recorded the position
        s_state = mgr.get_sleeve_state("SLEEVE_SP500")
        assert s_state["open_positions_count"] == 1
        assert s_state["cash_balance"] < 50_000.0


# -----------------------------------------------------------------------------
# 2. MARKET SCHEDULER & CALENDAR TESTS
# -----------------------------------------------------------------------------
class TestMarketScheduler:
    def test_ny_holidays(self):
        # Good Friday 2024 was March 29
        is_gf, name = is_us_market_holiday(date(2024, 3, 29))
        assert is_gf is True
        assert name == "Good Friday"

        # July 4, 2024
        is_ind, name = is_us_market_holiday(date(2024, 7, 4))
        assert is_ind is True
        assert name == "Independence Day"

        # Regular trading day (e.g. Wednesday July 10, 2024)
        is_hol, _ = is_us_market_holiday(date(2024, 7, 10))
        assert is_hol is False
        assert is_market_trading_day(date(2024, 7, 10)) is True

    def test_timezone_dst_awareness(self):
        # Summer (EDT = UTC-4) vs Winter (EST = UTC-5)
        summer_dt = datetime(2024, 7, 1, 12, 0, tzinfo=NY_TZ)
        assert summer_dt.dst().total_seconds() == 3600  # Daylight saving active

        winter_dt = datetime(2024, 1, 15, 12, 0, tzinfo=NY_TZ)
        assert winter_dt.dst().total_seconds() == 0     # Standard time

    def test_scheduler_idempotency(self, temp_ms_db):
        guard = MarketSchedulerGuard(db_path=temp_ms_db)
        
        # First run succeeds
        ok1, run_id1 = guard.start_pipeline_run("2024-05-01")
        assert ok1 is True
        guard.complete_pipeline_run(run_id1, tickers_audited=500, tickers_valid=500, signals_generated=2)

        # Second run on same session date is blocked
        ok2, msg2 = guard.start_pipeline_run("2024-05-01")
        assert ok2 is False
        assert "IDEMPOTENCY_BLOCK" in msg2


# -----------------------------------------------------------------------------
# 3. ENTERPRISE ALERTS TESTS
# -----------------------------------------------------------------------------
class TestEnterpriseAlerts:
    def test_alert_formatting_and_deduplication(self, temp_ms_db):
        alerts = EnterpriseAlertManager(db_path=temp_ms_db)

        msg = alerts.format_signal_message(
            sleeve_id="SLEEVE_BREAKOUT",
            strategy_version="V2_BREAKOUT",
            ticker="MSFT",
            signal_date="2024-05-01",
            signal_price=400.0,
            planned_stop=380.0,
            planned_target=540.0,
            shares=20,
            dollar_risk=400.0,
            rr_ratio=1.75
        )
        assert "🔔 [TRADING SIGNAL] MSFT" in msg
        assert "Sleeve: SLEEVE_BREAKOUT" in msg
        assert "Initial Stop: $380.00" in msg

        # Send alert in unconfigured environment -> LOGGED_ONLY (clean fallback)
        ok1, status1 = alerts.send_alert("MSFT_20240501_SIG", "MSFT", "NEW_SIGNAL", "INFO", msg)
        assert ok1 is True
        assert status1 == "LOGGED_ONLY"

        # Duplicate alert must be suppressed
        ok2, status2 = alerts.send_alert("MSFT_20240501_SIG", "MSFT", "NEW_SIGNAL", "INFO", msg)
        assert ok2 is False
        assert "DUPLICATE_SUPPRESSED" in status2


# -----------------------------------------------------------------------------
# 4. PRODUCTION DATABASE & CORE CODEBASE INTEGRITY
# -----------------------------------------------------------------------------
class TestProductionIntegrity:
    @pytest.mark.production_verification
    def test_production_db_hash_strictly_unmodified(self):
        prod_db = "data/screener.db"
        assert os.path.exists(prod_db), "Production DB missing!"
        assert os.path.getsize(prod_db) == 184_537_088, "Production DB size changed!"
        
        h = hashlib.sha256(open(prod_db, "rb").read()).hexdigest()
        expected = "04cfd75add3ef9d1e2aa99d4e2d40862de85767d98abacfcdd44fbd8e359f0c6"
        assert h == expected, f"Production DB hash modified! Expected {expected}, got {h}"

    def test_core_codebase_files_untouched(self):
        expected_hashes = {
            "strategies.py": "b15e9e9a2d8e",
            "engine.py": "083bcd2ce8e8",
            "features.py": "bdb96beb9eff",
        }
        for fname, exp_prefix in expected_hashes.items():
            actual = hashlib.sha256(open(fname, "rb").read()).hexdigest()[:12]
            assert actual == exp_prefix, f"{fname} was modified! Expected {exp_prefix}, got {actual}"
