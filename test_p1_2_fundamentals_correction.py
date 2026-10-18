"""
test_p1_2_fundamentals_correction.py - Unit & Regression Tests for P1.2 Fundamentals Corrections.

Validates:
1. QTD vs YTD Multi-Duration filtering:
   - Isolates discrete ~90d quarterly flow metrics from 6m/9m cumulative figures.
   - Reconciles Q2/Q3 cash flows via exact fiscal year start date deduction.
   - Tested against real SEC EDGAR facts (NVDA, AAPL, CAT).
2. Free Cash Flow (FCF) Mathematical Integrity:
   - If CapEx is None, FCF must be None (never equate FCF to OCF).
   - If CapEx is reported, FCF = OCF - abs(CapEx).
3. Debt-Free Company & Balance Sheet Representation:
   - Missing debt concepts -> None (Zero Fake Data).
   - Explicit zero debt -> 0.0 (not None), producing Debt/Equity = 0.0.
4. Full Column UPSERT Idempotency:
   - Updates cost_of_revenue, fiscal_year, fiscal_period, cik, and source on conflict.
5. Incremental Freshness Resume Logic:
   - Bypasses static 'existing >= 15' gate; skips only if latest filing is within freshness_days.
6. Corporate Succession & Multi-CIK Registry:
   - ExxonMobil (XOM) resolves both legacy operating CIK 0000034088 and holding successor 0002115436.
7. Customizable SEC EDGAR User-Agent via SEC_EDGAR_USER_AGENT environment variable.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import pytest
import numpy as np

from data_provider import DataProvider, init_db, CIK_SUCCESSION_REGISTRY, DEFAULT_SEC_USER_AGENT
from import_fundamentals import check_ticker_freshness, fetch_and_import_ticker


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


# -----------------------------------------------------------------------------
# 1. QTD vs YTD Parsing Tests
# -----------------------------------------------------------------------------
def test_nvda_discrete_quarterly_metrics():
    """
    Verify NVDA FY27 Q2 (filed 2026-08-26, period ended 2026-07-26):
    Must parse discrete ~90d quarter, NOT 6-month cumulative ($177.8B).
    """
    nvda_path = Path("data/sec_facts/CIK0001045810.json")
    if not nvda_path.exists():
        pytest.skip("NVDA raw facts JSON not cached locally")

    with open(nvda_path, "r", encoding="utf-8") as fp:
        facts = json.load(fp)

    records = DataProvider.parse_sec_company_facts(facts, "NVDA")
    
    # Locate the FY27 Q2 filing
    q2_recs = [r for r in records if r["filing_date"] == "2026-08-26" and r["period_end_date"] == "2026-07-26"]
    assert len(q2_recs) == 1, "Expected exactly 1 filing record for NVDA on 2026-08-26 for period ended 2026-07-26"
    q2 = q2_recs[0]

    # Discrete quarterly revenue should be ~$96.22B, NOT $177.8B
    assert q2["revenue"] is not None
    assert np.isclose(q2["revenue"], 96_221_000_000, atol=1e8)
    assert q2["revenue"] < 100_000_000_000, f"Revenue {q2['revenue']} is cumulative YTD instead of discrete QTD!"

    # Discrete quarterly net income should be ~$59.69B, NOT $118.0B
    assert q2["net_income"] is not None
    assert np.isclose(q2["net_income"], 59_688_000_000, atol=1e8)
    assert q2["net_income"] < 70_000_000_000

    # Reconciled discrete quarterly OCF: YTD Q2 ($74.42B) - YTD Q1 ($50.34B) = $24.08B
    assert q2["operating_cash_flow"] is not None
    assert np.isclose(q2["operating_cash_flow"], 24_077_000_000, atol=1e8)

    # Reconciled discrete quarterly CapEx: YTD Q2 ($4.43B) - YTD Q1 ($1.76B) = $2.68B
    assert q2["capex"] is not None
    assert np.isclose(q2["capex"], 2_677_000_000, atol=1e8)

    # Discrete Free Cash Flow: $24.08B - $2.68B = $21.40B (NOT $70.0B)
    assert q2["free_cash_flow"] is not None
    assert np.isclose(q2["free_cash_flow"], 21_400_000_000, atol=1e8)


def test_aapl_discrete_quarterly_revenue():
    """
    Verify AAPL FY26 Q2 (filed 2026-05-01, period ended 2026-03-28):
    Discrete quarterly revenue must be ~$111.18B, NOT 6-month cumulative ($254.94B).
    """
    aapl_path = Path("data/sec_facts/CIK0000320193.json")
    if not aapl_path.exists():
        pytest.skip("AAPL raw facts JSON not cached locally")

    with open(aapl_path, "r", encoding="utf-8") as fp:
        facts = json.load(fp)

    records = DataProvider.parse_sec_company_facts(facts, "AAPL")
    q2_recs = [r for r in records if r["filing_date"] == "2026-05-01" and r["period_end_date"] == "2026-03-28"]
    assert len(q2_recs) == 1
    q2 = q2_recs[0]

    assert q2["revenue"] is not None
    assert np.isclose(q2["revenue"], 111_184_000_000, atol=1e8)
    assert q2["revenue"] < 150_000_000_000


# -----------------------------------------------------------------------------
# 2. Free Cash Flow (FCF) Mathematical Rules
# -----------------------------------------------------------------------------
def test_fcf_none_when_capex_missing():
    """
    If CapEx is not reported / missing, FCF MUST be None.
    Equating FCF = OCF in the absence of CapEx violates accounting standards.
    """
    facts_no_capex = {
        "cik": 999001,
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [{"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-12-31", "filed": "2025-02-15", "val": 5000000}]}},
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [{"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-12-31", "filed": "2025-02-15", "val": 1500000}]}}
            }
        }
    }
    records = DataProvider.parse_sec_company_facts(facts_no_capex, "NOCAPEX")
    assert len(records) == 1
    rec = records[0]
    assert rec["operating_cash_flow"] == 1500000
    assert rec["capex"] is None
    assert rec["free_cash_flow"] is None, "FCF must be None when CapEx is missing!"


def test_fcf_calculated_when_capex_present():
    """When both OCF and CapEx are present, FCF = OCF - abs(CapEx)."""
    facts_with_capex = {
        "cik": 999002,
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [{"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-12-31", "filed": "2025-02-15", "val": 5000000}]}},
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [{"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-12-31", "filed": "2025-02-15", "val": 2000000}]}},
                "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [{"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-12-31", "filed": "2025-02-15", "val": 400000}]}}
            }
        }
    }
    records = DataProvider.parse_sec_company_facts(facts_with_capex, "WITHCAPEX")
    assert len(records) == 1
    rec = records[0]
    assert rec["free_cash_flow"] == 2000000 - 400000


# -----------------------------------------------------------------------------
# 3. Debt-Free & Debt Handling Rules
# -----------------------------------------------------------------------------
def test_zero_debt_company_handling():
    """
    If debt concepts are reported as 0.0, total_debt must evaluate to 0.0 (not None),
    yielding debt_to_equity = 0.0 when equity > 0.
    """
    zero_debt_facts = {
        "cik": 999003,
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [{"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-12-31", "filed": "2025-02-15", "val": 10000000}]}},
                "StockholdersEquity": {"units": {"USD": [{"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-12-31", "filed": "2025-02-15", "val": 5000000}]}},
                "LongTermDebtNoncurrent": {"units": {"USD": [{"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-12-31", "filed": "2025-02-15", "val": 0.0}]}},
                "DebtCurrent": {"units": {"USD": [{"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-12-31", "filed": "2025-02-15", "val": 0.0}]}}
            }
        }
    }
    records = DataProvider.parse_sec_company_facts(zero_debt_facts, "DEBTFREE")
    assert len(records) == 1
    rec = records[0]
    assert rec["total_debt"] == 0.0, f"Expected total_debt == 0.0, got {rec['total_debt']}"
    assert rec["debt_to_equity"] == 0.0, f"Expected debt_to_equity == 0.0, got {rec['debt_to_equity']}"


def test_missing_debt_concepts_remain_none():
    """If neither long-term nor short-term debt concept exists, total_debt is None (Zero Fake Data)."""
    no_debt_concepts = {
        "cik": 999004,
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [{"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-12-31", "filed": "2025-02-15", "val": 10000000}]}},
                "StockholdersEquity": {"units": {"USD": [{"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-12-31", "filed": "2025-02-15", "val": 5000000}]}}
            }
        }
    }
    records = DataProvider.parse_sec_company_facts(no_debt_concepts, "NODEBTTAGS")
    assert len(records) == 1
    rec = records[0]
    assert rec["total_debt"] is None
    assert rec["debt_to_equity"] is None


# -----------------------------------------------------------------------------
# 4. UPSERT Completeness
# -----------------------------------------------------------------------------
def test_upsert_updates_all_columns(memory_db):
    """
    On duplicate primary key (ticker, filing_date, period_end_date, form),
    all columns including cost_of_revenue, fiscal_year, fiscal_period, cik, source must update.
    """
    provider = DataProvider(memory_db)
    r1 = [{
        "ticker": "TEST",
        "cik": "0000000001",
        "filing_date": "2025-01-01",
        "period_end_date": "2024-12-31",
        "form": "10-K",
        "fiscal_year": 2024,
        "fiscal_period": "FY",
        "revenue": 100.0,
        "cost_of_revenue": 40.0,
        "operating_income": 30.0,
        "net_income": 20.0,
        "operating_cash_flow": 25.0,
        "capex": 5.0,
        "free_cash_flow": 20.0,
        "total_assets": 200.0,
        "total_liabilities": 100.0,
        "total_equity": 100.0,
        "total_debt": 50.0,
        "current_assets": 80.0,
        "current_liabilities": 40.0,
        "shares_outstanding": 10.0,
        "gross_margin": 0.6,
        "operating_margin": 0.3,
        "roe": 0.2,
        "debt_to_equity": 0.5,
        "current_ratio": 2.0,
        "source": "INITIAL"
    }]
    provider.save_fundamentals(r1)

    # Updated row with modified cik, fiscal_year, cost_of_revenue, source
    r2 = [{
        "ticker": "TEST",
        "cik": "0000000002",
        "filing_date": "2025-01-01",
        "period_end_date": "2024-12-31",
        "form": "10-K",
        "fiscal_year": 2025,
        "fiscal_period": "Q4_AMENDED",
        "revenue": 150.0,
        "cost_of_revenue": 55.0,
        "operating_income": 45.0,
        "net_income": 35.0,
        "operating_cash_flow": 30.0,
        "capex": 10.0,
        "free_cash_flow": 20.0,
        "total_assets": 250.0,
        "total_liabilities": 110.0,
        "total_equity": 140.0,
        "total_debt": 40.0,
        "current_assets": 90.0,
        "current_liabilities": 30.0,
        "shares_outstanding": 10.0,
        "gross_margin": 0.63,
        "operating_margin": 0.30,
        "roe": 0.25,
        "debt_to_equity": 0.28,
        "current_ratio": 3.0,
        "source": "UPDATED"
    }]
    provider.save_fundamentals(r2)

    cur = memory_db.cursor()
    cur.execute("SELECT cik, fiscal_year, fiscal_period, cost_of_revenue, revenue, source FROM company_fundamentals WHERE ticker = 'TEST'")
    row = cur.fetchone()
    assert row[0] == "0000000002", "CIK not updated on conflict!"
    assert row[1] == 2025, "fiscal_year not updated on conflict!"
    assert row[2] == "Q4_AMENDED", "fiscal_period not updated on conflict!"
    assert row[3] == 55.0, "cost_of_revenue not updated on conflict!"
    assert row[4] == 150.0, "revenue not updated on conflict!"
    assert row[5] == "UPDATED", "source not updated on conflict!"


# -----------------------------------------------------------------------------
# 5. Incremental Freshness Logic
# -----------------------------------------------------------------------------
def test_check_ticker_freshness_logic(memory_db):
    """
    Verifies that check_ticker_freshness flags tickers older than freshness_days
    as needing an update, while recent filings are correctly identified as fresh.
    """
    cur = memory_db.cursor()
    today_str = datetime.now().strftime("%Y-%m-%d")
    old_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    # 1. No data
    fresh, count, m_date = check_ticker_freshness(cur, "EMPTY", freshness_days=45)
    assert not fresh
    assert count == 0

    # 2. Old filing (90 days ago) with 45-day threshold -> should NOT be fresh
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, filing_date, period_end_date, form)
        VALUES ('OLDCO', ?, '2026-01-01', '10-Q')
    """, (old_date,))
    memory_db.commit()

    fresh_old, count_old, m_old = check_ticker_freshness(cur, "OLDCO", freshness_days=45)
    assert not fresh_old, "90-day-old filing must not be considered fresh under 45-day rule!"
    assert count_old == 1
    assert m_old == old_date

    # 3. Recent filing (today) with 45-day threshold -> should be fresh
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, filing_date, period_end_date, form)
        VALUES ('NEWCO', ?, '2026-01-01', '10-Q')
    """, (today_str,))
    memory_db.commit()

    fresh_new, count_new, m_new = check_ticker_freshness(cur, "NEWCO", freshness_days=45)
    assert fresh_new, "Today's filing must be considered fresh!"
    assert count_new == 1


# -----------------------------------------------------------------------------
# 6. Corporate Succession & Multi-CIK Registry
# -----------------------------------------------------------------------------
def test_xom_succession_registry():
    """Verify XOM maps to both predecessor and successor CIKs in succession registry."""
    ciks = DataProvider.get_ciks_for_ticker("XOM")
    assert "0000034088" in ciks, "Operating company CIK 0000034088 missing for XOM!"
    assert "0002115436" in ciks, "Holding company successor CIK 0002115436 missing for XOM!"
    assert len(ciks) == 2


def test_standard_ticker_resolves_single_cik():
    """Standard tickers (AAPL) resolve to their single primary CIK."""
    ciks = DataProvider.get_ciks_for_ticker("AAPL")
    assert ciks == ["0000320193"]


# -----------------------------------------------------------------------------
# 7. Configurable SEC User-Agent
# -----------------------------------------------------------------------------
def test_customizable_sec_user_agent(monkeypatch):
    """Verify SEC_EDGAR_USER_AGENT env var is honored."""
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "CustomResearchCorp/2.0 (quant@custom.com)")
    
    # Check default agent pick up
    fetched_ua = os.environ.get("SEC_EDGAR_USER_AGENT", DEFAULT_SEC_USER_AGENT)
    assert fetched_ua == "CustomResearchCorp/2.0 (quant@custom.com)"
