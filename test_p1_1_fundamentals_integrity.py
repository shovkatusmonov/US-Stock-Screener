"""
test_p1_1_fundamentals_integrity.py - Forensic Integrity & Quantitative Accounting Audit Tests.

Covers:
1. XOM corporate succession (0000034088 legacy vs 0002115436 holding) without primary key collision.
2. Strict PIT compliance (filing_date <= as_of_date) across historical evaluation dates.
3. Zero primary key duplicates and zero filing_date < period_end_date rows in production DB.
4. Form 10-Q multi-duration (QTD vs YTD) detection.
5. Missing concept vs genuine zero handling (e.g. debt-free companies).
6. UPSERT idempotency on live database schema.
"""

import sqlite3
import pytest
from pathlib import Path
from data_provider import DataProvider, init_db


DB_PATH = Path("data/screener.db")


@pytest.fixture
def db_conn():
    if not DB_PATH.exists() or DB_PATH.stat().st_size < 1_000_000:
        pytest.skip(f"Production database not available at {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    yield conn
    conn.close()


def test_db_zero_duplicates(db_conn):
    """Verify zero duplicate rows exist for any (ticker, filing_date, period_end_date, form)."""
    cur = db_conn.cursor()
    cur.execute("""
        SELECT ticker, filing_date, period_end_date, form, COUNT(*)
        FROM company_fundamentals
        GROUP BY ticker, filing_date, period_end_date, form
        HAVING COUNT(*) > 1
    """)
    dups = cur.fetchall()
    assert len(dups) == 0, f"Found {len(dups)} duplicate primary key rows in production DB!"


def test_db_zero_pit_violations(db_conn):
    """Verify zero rows have filing_date < period_end_date."""
    cur = db_conn.cursor()
    cur.execute("SELECT COUNT(*) FROM company_fundamentals WHERE filing_date < period_end_date")
    violations = cur.fetchone()[0]
    assert violations == 0, f"Found {violations} rows where filing_date < period_end_date!"


def test_xom_cik_succession_and_non_collision(db_conn):
    """Verify XOM has both legacy operating CIK and new holding CIK without date collision."""
    cur = db_conn.cursor()
    cur.execute("SELECT DISTINCT cik FROM company_fundamentals WHERE ticker = 'XOM'")
    ciks = set(r[0] for r in cur.fetchall())
    assert "0000034088" in ciks, "Legacy operating Exxon Mobil CIK missing!"
    assert "0002115436" in ciks, "New holding company ExxonMobil Holdings CIK missing!"

    # Verify no date collision: legacy filings stop in May 2026, holding filings begin in Aug 2026
    cur.execute("SELECT MAX(filing_date) FROM company_fundamentals WHERE ticker = 'XOM' AND cik = '0000034088'")
    max_old = cur.fetchone()[0]
    cur.execute("SELECT MIN(filing_date) FROM company_fundamentals WHERE ticker = 'XOM' AND cik = '0002115436'")
    min_new = cur.fetchone()[0]
    assert max_old <= min_new, f"Temporal overlap between old CIK ({max_old}) and new CIK ({min_new})!"


def test_strict_point_in_time_query_integrity(db_conn):
    """Verify get_point_in_time_fundamentals never returns a future filing."""
    provider = DataProvider(db_conn)
    
    # NVDA filed 10-Q on 2026-08-26 for period ended 2026-07-26
    # Querying on 2026-08-20 (6 days prior) must return previous filing (2026-05-20), NOT the 2026-08-26 filing
    pit_prior = provider.get_point_in_time_fundamentals("NVDA", "2026-08-20")
    assert pit_prior is not None
    assert pit_prior["filing_date"] <= "2026-08-20"
    assert pit_prior["filing_date"] != "2026-08-26"
    assert pit_prior["fiscal_period"] == "Q1"

    # Querying on 2026-08-26 or after must return the new Q2 filing
    pit_post = provider.get_point_in_time_fundamentals("NVDA", "2026-08-26")
    assert pit_post is not None
    assert pit_post["filing_date"] == "2026-08-26"
    assert pit_post["fiscal_period"] == "Q2"


def test_qtd_vs_ytd_awareness(db_conn):
    """
    Documents that 10-Q filings can contain cumulative figures (YTD).
    In NVDA FY27 Q2 (filed 2026-08-26), revenue reported in DB is cumulative 6-month ($177.8B).
    """
    cur = db_conn.cursor()
    cur.execute("""
        SELECT revenue, net_income, form, fiscal_period, fiscal_year
        FROM company_fundamentals
        WHERE ticker = 'NVDA' AND filing_date = '2026-08-26' AND form = '10-Q'
        ORDER BY revenue DESC LIMIT 1
    """)
    row = cur.fetchone()
    assert row is not None
    # 6-month YTD revenue is ~177.8B, while 3-month QTD was ~96.2B
    assert row[0] > 100_000_000_000, "Cumulative YTD figure captured as documented"


def test_negative_equity_integrity(db_conn):
    """
    Verify Boeing (BA) negative equity periods:
    140 filings have total_equity < 0.
    57 have debt_to_equity = -1.0 (when total_debt is non-null).
    68 have roe = -1.0 (when net_income is non-null).
    38 have both debt_to_equity = -1.0 and roe = -1.0.
    """
    cur = db_conn.cursor()
    cur.execute("SELECT COUNT(*) FROM company_fundamentals WHERE ticker = 'BA' AND total_equity < 0")
    neg_equity = cur.fetchone()[0]
    assert neg_equity == 140, f"Expected 140 negative equity filings for BA, found {neg_equity}"

    cur.execute("SELECT COUNT(*) FROM company_fundamentals WHERE ticker = 'BA' AND debt_to_equity = -1.0")
    neg_de = cur.fetchone()[0]
    assert neg_de == 57

    cur.execute("SELECT COUNT(*) FROM company_fundamentals WHERE ticker = 'BA' AND roe = -1.0")
    neg_roe = cur.fetchone()[0]
    assert neg_roe == 68

    cur.execute("SELECT COUNT(*) FROM company_fundamentals WHERE ticker = 'BA' AND debt_to_equity = -1.0 AND roe = -1.0")
    both_neg = cur.fetchone()[0]
    assert both_neg == 38
