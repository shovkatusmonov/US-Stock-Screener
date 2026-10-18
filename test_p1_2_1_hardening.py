"""
test_p1_2_1_hardening.py - Exhaustive Unit & Integration Tests for P1.2.1 Hardening.

Validates:
1. YoY Prior Filing Selection:
   - Order by filing_date DESC, period_end_date DESC to select latest amended filing.
   - Graceful recovery when amendments have NULL revenue or NULL net income.
   - Handling selective metric restatements across multiple filings.
   - Strict Point-in-Time compliance: filing_date <= as_of_date prevents lookahead.
2. XOM Corporate Succession Segregation:
   - Prohibits automated cross-entity YoY comparison between predecessor (CIK 0000034088)
     and successor (CIK 0002115436).
   - Allows clean YoY comparisons within the same legal entity.
3. QTD/YTD Metric Isolation in Parser:
   - Decoupled start date contexts prevent income statement dates from polluting cash flow statements.
   - Reconciles discrete quarterly cash flow from YTD difference even when income statement has discrete 90d items first.
4. SEC EDGAR User-Agent Validation:
   - Rejects missing, empty, or whitespace-only User-Agents.
   - Rejects placeholder domains (example.com, sample.com, test.com, placeholder, fake.com).
   - Requires genuine organizational contact (email or URL).
   - Zero fake data / zero invented contacts guarantee.
   - fetch_sec_edgar_facts and run_universe_import safely halt before making network requests.
"""

import json
import sqlite3
import urllib.request
import pytest
import numpy as np

from data_provider import DataProvider, init_db, validate_sec_user_agent
from import_fundamentals import run_universe_import, fetch_and_import_ticker


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


# -----------------------------------------------------------------------------
# 1. YoY Prior Filing Selection Tests
# -----------------------------------------------------------------------------
def test_yoy_prior_filing_selection_amendments(memory_db):
    """
    When multiple filings exist for the prior fiscal year (e.g., original 10-K and amended 10-K/A),
    the system must select the latest amended filing as of as_of_date.
    """
    cur = memory_db.cursor()
    # Prior FY2024 original 10-K filed on 2025-02-15
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('TEST', '0000123456', '2025-02-15', '2024-12-31', '10-K', 2024, 'FY', 1000.0, 100.0, 10.0)
    """)
    # Prior FY2024 amended 10-K/A filed on 2025-04-10 (restated revenue to 1100.0, net_income to 110.0)
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('TEST', '0000123456', '2025-04-10', '2024-12-31', '10-K', 2024, 'FY', 1100.0, 110.0, 10.0)
    """)
    # Current FY2025 10-K filed on 2026-02-20
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('TEST', '0000123456', '2026-02-20', '2025-12-31', '10-K', 2025, 'FY', 1320.0, 132.0, 10.0)
    """)
    memory_db.commit()

    provider = DataProvider(memory_db)
    pit = provider.get_point_in_time_fundamentals("TEST", as_of_date="2026-02-25")
    assert pit is not None
    assert pit["revenue"] == 1320.0
    # Must compare against amended 1100.0: (1320 - 1100) / 1100 = 0.20 (+20%), NOT (1320 - 1000) / 1000 = 0.32 (+32%)
    assert np.isclose(pit["revenue_growth_yoy"], 0.20)
    # EPS: cur_eps = 13.2, prev_eps = 11.0 -> (13.2 - 11.0) / 11.0 = 0.20 (+20%)
    assert np.isclose(pit["eps_growth_yoy"], 0.20)


def test_yoy_prior_filing_null_metric_recovery(memory_db):
    """
    If a subsequent prior-year filing only modified balance sheet items (revenue and net_income are NULL),
    the system must not return None; it must fall back to the latest filing where the metric was populated.
    """
    cur = memory_db.cursor()
    # Prior FY2024 original 10-K filed on 2025-02-15
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding, total_equity)
        VALUES ('NULLCO', '0000123456', '2025-02-15', '2024-12-31', '10-K', 2024, 'FY', 1000.0, 100.0, 10.0, 500.0)
    """)
    # Prior FY2024 balance-sheet amendment filed on 2025-05-01 (revenue and net_income left NULL)
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding, total_equity)
        VALUES ('NULLCO', '0000123456', '2025-05-01', '2024-12-31', '10-K', 2024, 'FY', NULL, NULL, 10.0, 550.0)
    """)
    # Current FY2025 10-K filed on 2026-02-20
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding, total_equity)
        VALUES ('NULLCO', '0000123456', '2026-02-20', '2025-12-31', '10-K', 2025, 'FY', 1200.0, 120.0, 10.0, 600.0)
    """)
    memory_db.commit()

    provider = DataProvider(memory_db)
    pit = provider.get_point_in_time_fundamentals("NULLCO", as_of_date="2026-02-25")
    assert pit is not None
    # Prior revenue recovered from the populated filing (1000.0): (1200 - 1000) / 1000 = 0.20
    assert pit["revenue_growth_yoy"] is not None
    assert np.isclose(pit["revenue_growth_yoy"], 0.20)
    # Prior net income recovered: (12.0 - 10.0) / 10.0 = 0.20
    assert pit["eps_growth_yoy"] is not None
    assert np.isclose(pit["eps_growth_yoy"], 0.20)


def test_yoy_prior_filing_selective_metric_amendment(memory_db):
    """
    If an amendment restated revenue but left net_income NULL, revenue growth must use the
    amended revenue, while EPS growth must use the non-null net income from the earlier filing.
    """
    cur = memory_db.cursor()
    # Prior FY2024 original 10-K (both populated)
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('SELCO', '0000123456', '2025-02-15', '2024-12-31', '10-K', 2024, 'FY', 1000.0, 100.0, 10.0)
    """)
    # Prior FY2024 amendment (restated revenue to 1050.0, but net_income is NULL)
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('SELCO', '0000123456', '2025-06-01', '2024-12-31', '10-K', 2024, 'FY', 1050.0, NULL, 10.0)
    """)
    # Current FY2025 10-K
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('SELCO', '0000123456', '2026-02-20', '2025-12-31', '10-K', 2025, 'FY', 1260.0, 120.0, 10.0)
    """)
    memory_db.commit()

    provider = DataProvider(memory_db)
    pit = provider.get_point_in_time_fundamentals("SELCO", as_of_date="2026-02-25")
    assert pit is not None
    # Revenue: uses amended 1050.0 -> (1260 - 1050) / 1050 = 0.20 (+20%)
    assert np.isclose(pit["revenue_growth_yoy"], 0.20)
    # EPS: uses un-amended 100.0 -> cur_eps=12, prev_eps=10 -> (12 - 10) / 10 = 0.20 (+20%)
    assert np.isclose(pit["eps_growth_yoy"], 0.20)


def test_yoy_pit_lookahead_prevention(memory_db):
    """
    An amendment filed after as_of_date must NOT be considered for YoY comparison.
    """
    cur = memory_db.cursor()
    # Prior FY2024 original 10-K filed 2025-02-15
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('TIME', '0000123456', '2025-02-15', '2024-12-31', '10-K', 2024, 'FY', 1000.0, 100.0, 10.0)
    """)
    # Current FY2025 10-K filed 2026-02-20
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('TIME', '0000123456', '2026-02-20', '2025-12-31', '10-K', 2025, 'FY', 1200.0, 120.0, 10.0)
    """)
    # Prior FY2024 post-dated amendment filed on 2026-04-01 (future relative to query date 2026-03-01)
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('TIME', '0000123456', '2026-04-01', '2024-12-31', '10-K', 2024, 'FY', 900.0, 90.0, 10.0)
    """)
    memory_db.commit()

    provider = DataProvider(memory_db)
    # Query as of 2026-03-01: post-dated amendment on 2026-04-01 must be invisible
    pit = provider.get_point_in_time_fundamentals("TIME", as_of_date="2026-03-01")
    assert pit is not None
    # Must use 1000.0 (original), NOT 900.0 (future amendment): (1200 - 1000) / 1000 = 0.20
    assert np.isclose(pit["revenue_growth_yoy"], 0.20)


# -----------------------------------------------------------------------------
# 2. XOM Corporate Succession Segregation Tests
# -----------------------------------------------------------------------------
def test_xom_cross_cik_segregation(memory_db):
    """
    ExxonMobil (XOM) restructured from operating predecessor (CIK 0000034088)
    to holding successor (CIK 0002115436). Automated YoY growth across different CIKs
    must be suppressed (returning None) without audited pro-forma continuity.
    """
    cur = memory_db.cursor()
    # Predecessor filing for FY2025 Q2 (filed 2025-08-01 under CIK 0000034088)
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('XOM', '0000034088', '2025-08-01', '2025-06-30', '10-Q', 2025, 'Q2', 80000.0, 8000.0, 4000.0)
    """)
    # Successor filing for FY2026 Q2 (filed 2026-08-03 under CIK 0002115436)
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('XOM', '0002115436', '2026-08-03', '2026-06-30', '10-Q', 2026, 'Q2', 90000.0, 9000.0, 4000.0)
    """)
    memory_db.commit()

    provider = DataProvider(memory_db)
    # Query as of 2026-08-05 (successor era)
    pit_successor = provider.get_point_in_time_fundamentals("XOM", as_of_date="2026-08-05")
    assert pit_successor is not None
    assert pit_successor["cik"] == "0002115436"
    assert pit_successor["revenue"] == 90000.0
    # Automated cross-CIK YoY comparison must be strictly suppressed
    assert pit_successor["revenue_growth_yoy"] is None, "Cross-CIK comparison was not suppressed for XOM corporate succession!"
    assert pit_successor["eps_growth_yoy"] is None

    # Now verify intra-entity comparison works when predecessor has prior predecessor filing
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('XOM', '0000034088', '2024-08-01', '2024-06-30', '10-Q', 2024, 'Q2', 70000.0, 7000.0, 4000.0)
    """)
    memory_db.commit()

    # Query as of 2025-08-05 (predecessor era): both filings belong to CIK 0000034088
    pit_predecessor = provider.get_point_in_time_fundamentals("XOM", as_of_date="2025-08-05")
    assert pit_predecessor is not None
    assert pit_predecessor["cik"] == "0000034088"
    assert pit_predecessor["revenue_growth_yoy"] is not None
    # (80000 - 70000) / 70000 = 0.142857
    assert np.isclose(pit_predecessor["revenue_growth_yoy"], (80000 - 70000) / 70000)


# -----------------------------------------------------------------------------
# 3. QTD/YTD Metric Isolation in Parser Tests
# -----------------------------------------------------------------------------
def test_qtd_ytd_metric_isolation_in_parser():
    """
    Verifies that discrete income statement start dates (e.g. 2026-04-01 for Q2)
    do not pollute cash flow start dates (e.g. 2026-01-01 for Q2 YTD), enabling
    clean reconciliation of discrete quarterly cash flows.
    """
    synthetic_facts = {
        "cik": 123456,
        "entityName": "Isolated Metrics Corp",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            # Discrete 3-month Q2 item listed FIRST in JSON
                            {"form": "10-Q", "fy": 2026, "fp": "Q2", "start": "2026-04-01", "end": "2026-06-30", "filed": "2026-08-01", "val": 50000.0},
                            # Discrete 3-month Q1 item
                            {"form": "10-Q", "fy": 2026, "fp": "Q1", "start": "2026-01-01", "end": "2026-03-31", "filed": "2026-05-01", "val": 40000.0}
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            # Q1 YTD (dur ~90d)
                            {"form": "10-Q", "fy": 2026, "fp": "Q1", "start": "2026-01-01", "end": "2026-03-31", "filed": "2026-05-01", "val": 20000.0},
                            # Q2 YTD (dur ~180d)
                            {"form": "10-Q", "fy": 2026, "fp": "Q2", "start": "2026-01-01", "end": "2026-06-30", "filed": "2026-08-01", "val": 45000.0}
                        ]
                    }
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            # CapEx Q1 YTD
                            {"form": "10-Q", "fy": 2026, "fp": "Q1", "start": "2026-01-01", "end": "2026-03-31", "filed": "2026-05-01", "val": 3000.0},
                            # CapEx Q2 YTD
                            {"form": "10-Q", "fy": 2026, "fp": "Q2", "start": "2026-01-01", "end": "2026-06-30", "filed": "2026-08-01", "val": 8000.0}
                        ]
                    }
                }
            }
        }
    }

    records = DataProvider.parse_sec_company_facts(synthetic_facts, "ISOL")
    assert len(records) == 2

    q2_recs = [r for r in records if r["fiscal_period"] == "Q2"]
    assert len(q2_recs) == 1
    q2 = q2_recs[0]

    # Revenue should be discrete Q2
    assert q2["revenue"] == 50000.0
    # Operating cash flow should be successfully reconciled from YTD (45000 - 20000 = 25000)
    # despite Revenue having start date 2026-04-01!
    assert q2["operating_cash_flow"] is not None
    assert q2["operating_cash_flow"] == 25000.0
    # CapEx reconciled: 8000 - 3000 = 5000
    assert q2["capex"] == 5000.0
    # Free cash flow: 25000 - 5000 = 20000
    assert q2["free_cash_flow"] == 20000.0


# -----------------------------------------------------------------------------
# 4. SEC EDGAR User-Agent Validation Tests
# -----------------------------------------------------------------------------
def test_sec_user_agent_validation_matrix():
    """Validates acceptance and rejection matrix for SEC EDGAR User-Agent compliance."""
    # Disallowed: empty or whitespace
    assert not validate_sec_user_agent(None)[0]
    assert not validate_sec_user_agent("")[0]
    assert not validate_sec_user_agent("   ")[0]

    # Disallowed: missing contact email or URL
    assert not validate_sec_user_agent("Mozilla/5.0")[0]
    assert not validate_sec_user_agent("Python-urllib/3.11")[0]
    assert not validate_sec_user_agent("USStockScreener/1.0")[0]

    # Disallowed: placeholder domains
    assert not validate_sec_user_agent("USStockScreener/1.0 (research@example.com)")[0]
    assert not validate_sec_user_agent("USStockScreener/1.0 (contact@example.org)")[0]
    assert not validate_sec_user_agent("MyBot/1.0 (admin@sample.com)")[0]
    assert not validate_sec_user_agent("TestAgent/1.0 (dev@test.com)")[0]
    assert not validate_sec_user_agent("QuantTool/1.0 (bot@placeholder.com)")[0]
    assert not validate_sec_user_agent("App/1.0 (fake@fake.com)")[0]
    assert not validate_sec_user_agent("App/1.0 (dummy@dummy.com)")[0]

    # Allowed: valid organizational headers
    assert validate_sec_user_agent("USStockScreenerAudit/1.0 (auditor@screener-audit.org)")[0]
    assert validate_sec_user_agent("QuantitativeHoldingsCorp/2.0 (quant-team@quantholdings.com)")[0]
    assert validate_sec_user_agent("ResearchPlatform/1.5 (https://myresearchinstitute.org/bot)")[0]
    assert validate_sec_user_agent("AlphaResearchFund compliance@alphafund.co.uk")[0]


def test_fetch_sec_edgar_facts_halts_on_placeholder_user_agent():
    """fetch_sec_edgar_facts must halt before sending network requests when UA is invalid."""
    res = DataProvider.fetch_sec_edgar_facts(
        cik="0000320193",
        user_agent="USStockScreener/1.0 (research@example.com)"
    )
    assert res["status"] == "ERROR"
    assert res["error_type"] == "SEC_INVALID_USER_AGENT"
    assert "placeholder" in res["message"].lower() or "example.com" in res["message"].lower()


def test_fetch_sec_edgar_facts_halts_on_empty_user_agent(monkeypatch):
    """fetch_sec_edgar_facts halts when user agent is empty or unconfigured."""
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "")
    res = DataProvider.fetch_sec_edgar_facts(cik="0000320193", user_agent="")
    assert res["status"] == "ERROR"
    assert res["error_type"] == "SEC_INVALID_USER_AGENT"


def test_run_universe_import_halts_on_invalid_user_agent(memory_db):
    """run_universe_import safely halts without making network calls when UA is invalid."""
    summary = run_universe_import(
        conn=memory_db,
        tickers=["AAPL", "MSFT"],
        user_agent="AutomatedBot/1.0 (test@sample.com)"
    )
    assert summary["error_type"] == "SEC_INVALID_USER_AGENT"
    assert summary["total_filings_ingested"] == 0
    assert summary["status_counts"]["ERROR"] == 2
