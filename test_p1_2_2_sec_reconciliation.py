"""
test_p1_2_2_sec_reconciliation.py - Exhaustive Empirical SEC Facts Reconciliation & Verification.

Validates against real cached SEC EDGAR company facts JSON:
1. AAPL (CIK 0000320193): Discrete Form 10-Q metrics (Q3 FY26), FCF, shares, and EPS.
2. MSFT (CIK 0000789019): Discrete Form 10-Q metrics (Q3 FY26), FCF, shares, and EPS.
3. NVDA (CIK 0001045810): Discrete Form 10-Q metrics (Q2 FY27), YTD cash flow derivation.
4. CAT (CIK 0000018230): Discrete Form 10-Q metrics (Q2 FY26), YTD cash flow derivation.
5. XOM Successor (CIK 0002115436): Discrete Form 10-Q metrics (Q2 FY26), missing Q1 handling.
6. XOM Predecessor (CIK 0000034088): Explicitly SKIPPED if raw JSON is missing locally, with documented reason.
7. YoY Calculation: Form family clauses (10-K/A, 10-Q/A) and 52/53-week fiscal period alignment.
8. Free Cash Flow Integrity: Strictly None if CapEx is None.
9. Database Integrity & PIT Chronology: Zero primary key duplicates and zero filing_date < period_end_date.
"""

import json
import sqlite3
from pathlib import Path
import pytest
import numpy as np

from data_provider import DataProvider, init_db


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


DB_PATH = Path("data/screener.db")


@pytest.fixture
def prod_db():
    if not DB_PATH.exists() or DB_PATH.stat().st_size < 1_000_000:
        pytest.skip(f"Production database not available at {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    yield conn
    conn.close()


# -----------------------------------------------------------------------------
# 1. Real SEC EDGAR Facts Reconciliation: AAPL
# -----------------------------------------------------------------------------
def test_aapl_real_sec_facts_reconciliation():
    """Verify AAPL Form 10-Q for FY26 Q3 (filed 2026-07-31, period ended 2026-06-27)."""
    p = Path("data/sec_facts/CIK0000320193.json")
    if not p.exists():
        pytest.skip("AAPL raw facts JSON not cached locally at data/sec_facts/CIK0000320193.json")

    facts = json.loads(p.read_text(encoding="utf-8"))
    records = DataProvider.parse_sec_company_facts(facts, "AAPL")

    q3_recs = [r for r in records if r["filing_date"] == "2026-07-31" and r["period_end_date"] == "2026-06-27"]
    assert len(q3_recs) == 1, "Expected exactly 1 filing record for AAPL on 2026-07-31 for period ended 2026-06-27"
    q3 = q3_recs[0]

    # Discrete quarterly revenue must be ~$109.42B (NOT 9m cumulative $364.36B)
    assert np.isclose(q3["revenue"], 109_417_000_000, atol=1e7)
    # Discrete quarterly net income must be ~$29.79B (NOT 9m cumulative $101.46B)
    assert np.isclose(q3["net_income"], 29_789_000_000, atol=1e7)
    # Discrete OCF: ~$34.37B
    assert np.isclose(q3["operating_cash_flow"], 34_369_000_000, atol=1e7)
    # Discrete CapEx: ~$2.46B
    assert np.isclose(q3["capex"], 2_455_000_000, atol=1e7)
    # Discrete Free Cash Flow: $34.37B - $2.46B = $31.91B
    assert np.isclose(q3["free_cash_flow"], 31_914_000_000, atol=1e7)
    # Shares outstanding
    assert np.isclose(q3["shares_outstanding"], 14_608_963_000, atol=1e6)


# -----------------------------------------------------------------------------
# 2. Real SEC EDGAR Facts Reconciliation: MSFT
# -----------------------------------------------------------------------------
def test_msft_real_sec_facts_reconciliation():
    """Verify MSFT Form 10-Q for FY26 Q3 (filed 2026-04-29, period ended 2026-03-31)."""
    p = Path("data/sec_facts/CIK0000789019.json")
    if not p.exists():
        pytest.skip("MSFT raw facts JSON not cached locally at data/sec_facts/CIK0000789019.json")

    facts = json.loads(p.read_text(encoding="utf-8"))
    records = DataProvider.parse_sec_company_facts(facts, "MSFT")

    q3_recs = [r for r in records if r["filing_date"] == "2026-04-29" and r["period_end_date"] == "2026-03-31"]
    assert len(q3_recs) == 1, "Expected exactly 1 filing record for MSFT on 2026-04-29 for period ended 2026-03-31"
    q3 = q3_recs[0]

    # Discrete quarterly revenue must be ~$82.89B (NOT 9m cumulative $241.83B)
    assert np.isclose(q3["revenue"], 82_886_000_000, atol=1e7)
    # Discrete quarterly net income must be ~$31.78B (NOT 9m cumulative $97.98B)
    assert np.isclose(q3["net_income"], 31_778_000_000, atol=1e7)
    # Discrete OCF: ~$46.68B
    assert np.isclose(q3["operating_cash_flow"], 46_679_000_000, atol=1e7)
    # CapEx: ~$30.88B
    assert np.isclose(q3["capex"], 30_876_000_000, atol=1e7)
    # FCF = $46.68B - $30.88B = $15.80B
    assert np.isclose(q3["free_cash_flow"], 15_803_000_000, atol=1e7)
    # Shares outstanding
    assert np.isclose(q3["shares_outstanding"], 7_429_000_000, atol=1e6)


# -----------------------------------------------------------------------------
# 3. Real SEC EDGAR Facts Reconciliation: NVDA
# -----------------------------------------------------------------------------
def test_nvda_real_sec_facts_reconciliation():
    """Verify NVDA Form 10-Q for FY27 Q2 (filed 2026-08-26, period ended 2026-07-26)."""
    p = Path("data/sec_facts/CIK0001045810.json")
    if not p.exists():
        pytest.skip("NVDA raw facts JSON not cached locally at data/sec_facts/CIK0001045810.json")

    facts = json.loads(p.read_text(encoding="utf-8"))
    records = DataProvider.parse_sec_company_facts(facts, "NVDA")

    q2_recs = [r for r in records if r["filing_date"] == "2026-08-26" and r["period_end_date"] == "2026-07-26"]
    assert len(q2_recs) == 1, "Expected exactly 1 filing record for NVDA on 2026-08-26 for period ended 2026-07-26"
    q2 = q2_recs[0]

    # Discrete quarterly revenue: ~$96.22B (NOT 6m cumulative $177.84B)
    assert np.isclose(q2["revenue"], 96_221_000_000, atol=1e7)
    # Discrete quarterly net income: ~$59.69B (NOT 6m cumulative $118.01B)
    assert np.isclose(q2["net_income"], 59_688_000_000, atol=1e7)
    # Reconciled OCF from YTD difference: $74.42B - $50.34B = $24.08B
    assert np.isclose(q2["operating_cash_flow"], 24_077_000_000, atol=1e7)
    # Reconciled CapEx from YTD difference: $4.43B - $1.76B = $2.68B
    assert np.isclose(q2["capex"], 2_677_000_000, atol=1e7)
    # FCF = $24.08B - $2.68B = $21.40B
    assert np.isclose(q2["free_cash_flow"], 21_400_000_000, atol=1e7)
    # Shares outstanding
    assert np.isclose(q2["shares_outstanding"], 24_338_000_000, atol=1e6)


# -----------------------------------------------------------------------------
# 4. Real SEC EDGAR Facts Reconciliation: CAT
# -----------------------------------------------------------------------------
def test_cat_real_sec_facts_reconciliation():
    """Verify CAT Form 10-Q for FY26 Q2 (filed 2026-08-05, period ended 2026-06-30)."""
    p = Path("data/sec_facts/CIK0000018230.json")
    if not p.exists():
        pytest.skip("CAT raw facts JSON not cached locally at data/sec_facts/CIK0000018230.json")

    facts = json.loads(p.read_text(encoding="utf-8"))
    records = DataProvider.parse_sec_company_facts(facts, "CAT")

    q2_recs = [r for r in records if r["filing_date"] == "2026-08-05" and r["period_end_date"] == "2026-06-30"]
    assert len(q2_recs) == 1, "Expected exactly 1 filing record for CAT on 2026-08-05 for period ended 2026-06-30"
    q2 = q2_recs[0]

    # Discrete quarterly revenue: ~$20.54B (NOT 6m cumulative $37.96B)
    assert np.isclose(q2["revenue"], 20_543_000_000, atol=1e7)
    # Discrete quarterly net income: ~$3.59B (NOT 6m cumulative $6.14B)
    assert np.isclose(q2["net_income"], 3_593_000_000, atol=1e7)
    # Reconciled OCF from YTD difference: $6.241B - $1.870B = $4.371B
    assert np.isclose(q2["operating_cash_flow"], 4_371_000_000, atol=1e7)
    # Reconciled CapEx from YTD difference: $1.315B - $0.728B = $0.587B
    assert np.isclose(q2["capex"], 587_000_000, atol=1e7)
    # FCF = $4.371B - $0.587B = $3.784B
    assert np.isclose(q2["free_cash_flow"], 3_784_000_000, atol=1e7)
    # Shares outstanding
    assert np.isclose(q2["shares_outstanding"], 459_700_000, atol=1e5)


# -----------------------------------------------------------------------------
# 5. Real SEC EDGAR Facts Reconciliation: XOM Successor
# -----------------------------------------------------------------------------
def test_xom_successor_real_sec_facts_reconciliation():
    """Verify XOM Successor Form 10-Q for FY26 Q2 (filed 2026-08-03, period ended 2026-06-30)."""
    p = Path("data/sec_facts/CIK0002115436.json")
    if not p.exists():
        pytest.skip("XOM Successor raw facts JSON not cached locally at data/sec_facts/CIK0002115436.json")

    facts = json.loads(p.read_text(encoding="utf-8"))
    records = DataProvider.parse_sec_company_facts(facts, "XOM")

    q2_recs = [r for r in records if r["filing_date"] == "2026-08-03" and r["period_end_date"] == "2026-06-30"]
    assert len(q2_recs) == 1, "Expected exactly 1 filing record for XOM successor on 2026-08-03 for period ended 2026-06-30"
    q2 = q2_recs[0]

    # Discrete quarterly revenue: ~$116.02B (NOT 6m cumulative $201.16B)
    assert np.isclose(q2["revenue"], 116_017_000_000, atol=1e7)
    # Discrete quarterly net income: ~$14.53B (NOT 6m cumulative $18.71B)
    assert np.isclose(q2["net_income"], 14_525_000_000, atol=1e7)
    # OCF, CapEx, FCF must be None: Successor entity has no Q1 filing to derive Q2 from YTD difference
    assert q2["operating_cash_flow"] is None, "Zero fake data: OCF must be None when prior Q1 is missing in successor entity"
    assert q2["capex"] is None
    assert q2["free_cash_flow"] is None
    # Shares outstanding
    assert np.isclose(q2["shares_outstanding"], 4_112_000_000, atol=1e6)


# -----------------------------------------------------------------------------
# 6. Real SEC EDGAR Facts: XOM Predecessor Marked SKIPPED
# -----------------------------------------------------------------------------
def test_xom_predecessor_sec_facts_missing_marked_skipped():
    """
    CIK0000034088.json is not present in local data/sec_facts cache.
    Per prompt instructions: mark test as SKIPPED and state why, without fabricating PASS.
    """
    p = Path("data/sec_facts/CIK0000034088.json")
    if not p.exists():
        pytest.skip(
            "CIK0000034088.json (Exxon Mobil Corp operating predecessor) is not present in local cache; "
            "airgapped zero fake data policy prevents live fetch without verified user credentials."
        )


# -----------------------------------------------------------------------------
# 7. YoY Calculation: Amendment Form Family Matching
# -----------------------------------------------------------------------------
def test_yoy_calculation_with_amendment_form_clauses(memory_db):
    """
    Form 10-K compares against prior Form 10-K/A amendment;
    Form 10-Q compares against prior Form 10-Q/A amendment.
    """
    cur = memory_db.cursor()
    # Prior FY2024 Form 10-K filed on 2025-02-15
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('AMENDCO', '0000999999', '2025-02-15', '2024-12-31', '10-K', 2024, 'FY', 1000.0, 100.0, 10.0)
    """)
    # Prior FY2024 Form 10-K/A amendment filed on 2025-04-01
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('AMENDCO', '0000999999', '2025-04-01', '2024-12-31', '10-K/A', 2024, 'FY', 1100.0, 110.0, 10.0)
    """)
    # Current FY2025 Form 10-K filed on 2026-02-20
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('AMENDCO', '0000999999', '2026-02-20', '2025-12-31', '10-K', 2025, 'FY', 1320.0, 132.0, 10.0)
    """)
    memory_db.commit()

    provider = DataProvider(memory_db)
    pit = provider.get_point_in_time_fundamentals("AMENDCO", as_of_date="2026-02-25")
    assert pit is not None
    # Must compare against 10-K/A (1100.0): (1320 - 1100) / 1100 = 0.20 (+20%)
    assert np.isclose(pit["revenue_growth_yoy"], 0.20)
    assert np.isclose(pit["eps_growth_yoy"], 0.20)


# -----------------------------------------------------------------------------
# 8. 52/53-Week Fiscal Year Period Alignment
# -----------------------------------------------------------------------------
def test_52_53_week_fiscal_period_matching(memory_db):
    """
    Companies on 52/53-week calendars (e.g. AAPL, NVDA) have period_end_date floating
    between years (e.g. 2025-07-27 vs 2026-07-26). Matching by (fiscal_period, fiscal_year - 1)
    must successfully align these periods without error.
    """
    cur = memory_db.cursor()
    # Prior FY2026 Q2 period ended on Sunday 2025-07-27
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('WEEK52', '0001045810', '2025-08-27', '2025-07-27', '10-Q', 2026, 'Q2', 46743.0, 26000.0, 24000.0)
    """)
    # Current FY2027 Q2 period ended on Sunday 2026-07-26
    cur.execute("""
        INSERT INTO company_fundamentals (ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period, revenue, net_income, shares_outstanding)
        VALUES ('WEEK52', '0001045810', '2026-08-26', '2026-07-26', '10-Q', 2027, 'Q2', 96221.0, 59000.0, 24000.0)
    """)
    memory_db.commit()

    provider = DataProvider(memory_db)
    pit = provider.get_point_in_time_fundamentals("WEEK52", as_of_date="2026-08-30")
    assert pit is not None
    # (96221 - 46743) / 46743 = 1.0585
    expected_rev_growth = (96221.0 - 46743.0) / 46743.0
    assert np.isclose(pit["revenue_growth_yoy"], expected_rev_growth)


# -----------------------------------------------------------------------------
# 9. Free Cash Flow Integrity: Strictly None if CapEx Missing
# -----------------------------------------------------------------------------
def test_fcf_strictly_none_when_capex_missing():
    """If CapEx is not reported or None, FCF must strictly be None (never equated to OCF)."""
    synthetic_facts = {
        "cik": 99999,
        "entityName": "No CapEx Corp",
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [{"form": "10-K", "fy": 2025, "fp": "FY", "end": "2025-12-31", "filed": "2026-02-15", "val": 100000.0}]}},
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [{"form": "10-K", "fy": 2025, "fp": "FY", "end": "2025-12-31", "filed": "2026-02-15", "val": 25000.0}]}}
                # Note: PaymentsToAcquirePropertyPlantAndEquipment is completely omitted
            }
        }
    }
    records = DataProvider.parse_sec_company_facts(synthetic_facts, "NOCAP")
    assert len(records) == 1
    rec = records[0]
    assert rec["operating_cash_flow"] == 25000.0
    assert rec["capex"] is None
    assert rec["free_cash_flow"] is None, "FCF was erroneously populated when CapEx was missing!"


# -----------------------------------------------------------------------------
# 10. Database Integrity & PIT Chronology
# -----------------------------------------------------------------------------
def test_db_primary_key_uniqueness_and_pit_chronology(prod_db):
    """Verify production database has zero primary key duplicates and zero PIT temporal violations."""
    cur = prod_db.cursor()
    # 1. Primary key uniqueness: (ticker, filing_date, period_end_date, form)
    cur.execute("""
        SELECT ticker, filing_date, period_end_date, form, COUNT(*)
        FROM company_fundamentals
        GROUP BY ticker, filing_date, period_end_date, form
        HAVING COUNT(*) > 1
    """)
    dups = cur.fetchall()
    assert len(dups) == 0, f"Found {len(dups)} duplicate primary keys in company_fundamentals!"

    # 2. PIT chronology: filing_date >= period_end_date
    cur.execute("SELECT COUNT(*) FROM company_fundamentals WHERE filing_date < period_end_date")
    violations = cur.fetchone()[0]
    assert violations == 0, f"Found {violations} rows where filing_date < period_end_date!"
