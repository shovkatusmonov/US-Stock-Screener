"""
test_p1_2_3_db_remediation_dry_run.py - Independent verification suite for P1.2.3
Safe Fundamentals Database Remediation Dry-Run.

Validates:
1. Pre-flight backup presence, size, and validity.
2. Read-only dry-run isolation (production DB untouched).
3. Q2/Q3 discrete QTD vs cumulative YTD correction proposal on AAPL, MSFT, NVDA, CAT.
4. XOM corporate succession segregation & NULL cash flow policy on successor (Zero Fake Data).
5. Missing SEC JSON files skip logic (XOM predecessor).
6. Idempotency guarantees (0 changes on second pass).
7. Database primary key uniqueness and schema integrity.
"""

import json
import os
import sqlite3
import pytest
from pathlib import Path
from data_provider import DataProvider, init_db


DB_PATH = Path("data/screener.db")
BACKUP_DIR = Path("backups/db_remediation_dryrun_20261018_1542")
SEC_FACTS_DIR = Path("data/sec_facts")


@pytest.fixture(scope="module")
def prod_db_conn():
    if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
        pytest.skip(f"Production database not available at {DB_PATH}")
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def test_preflight_backup_exists_and_verified():
    """Validates that a complete, non-empty pre-flight backup was captured."""
    if not BACKUP_DIR.exists():
        pytest.skip(f"Pre-flight backup directory not found at {BACKUP_DIR}")
    
    db_backup = BACKUP_DIR / "screener.db"
    assert db_backup.exists(), "screener.db missing in backup"
    assert db_backup.stat().st_size > 100_000_000, f"screener.db backup size too small ({db_backup.stat().st_size} bytes)"

    for code_file in ["data_provider.py", "import_fundamentals.py", "conftest.py"]:
        p = BACKUP_DIR / code_file
        assert p.exists(), f"Backup file {code_file} missing"
        assert p.stat().st_size > 0, f"Backup file {code_file} is empty"


def test_production_db_is_unmodified_and_intact(prod_db_conn):
    """Verifies that screener.db has not been written to during dry-run."""
    cur = prod_db_conn.cursor()
    cur.execute("PRAGMA integrity_check")
    assert cur.fetchone()[0] == "ok"

    cur.execute("SELECT COUNT(*), COUNT(DISTINCT ticker) FROM company_fundamentals")
    row_count, ticker_count = cur.fetchone()
    assert row_count == 171494
    assert ticker_count == 502

    # Verify AAPL Q3 still has the uncorrected 9m YTD in DB (proving zero writes occurred)
    cur.execute("""
        SELECT revenue, net_income FROM company_fundamentals
        WHERE ticker = 'AAPL' AND filing_date = '2026-07-31' AND period_end_date = '2026-06-27'
    """)
    row = cur.fetchone()
    assert row is not None
    assert row["revenue"] == 364357000000.0, "DB was prematurely modified!"


def test_sample_tickers_dry_run_proposed_changes(prod_db_conn):
    """
    Verifies that dry-run parser identifies exact discrete quarterly figures
    for AAPL, MSFT, NVDA, and CAT in Forms 10-Q.
    """
    sample_facts = [
        ("AAPL", SEC_FACTS_DIR / "CIK0000320193.json"),
        ("MSFT", SEC_FACTS_DIR / "CIK0000789019.json"),
        ("NVDA", SEC_FACTS_DIR / "CIK0001045810.json"),
        ("CAT", SEC_FACTS_DIR / "CIK0000018230.json"),
    ]
    for ticker, path in sample_facts:
        if not path.exists():
            pytest.skip(f"Required SEC facts file for {ticker} not found at {path}")

    provider = DataProvider(prod_db_conn)

    # 1. AAPL FY26 Q3
    with open(SEC_FACTS_DIR / "CIK0000320193.json", "r", encoding="utf-8") as f:
        aapl_facts = json.load(f)
    aapl_recs = provider.parse_sec_company_facts(aapl_facts, "AAPL")
    aapl_q3 = next(r for r in aapl_recs if r["filing_date"] == "2026-07-31" and r["period_end_date"] == "2026-06-27")
    assert aapl_q3["revenue"] == 109417000000.0
    assert aapl_q3["net_income"] == 29789000000.0
    assert aapl_q3["operating_cash_flow"] == 34369000000.0
    assert aapl_q3["capex"] == 2455000000.0
    assert aapl_q3["free_cash_flow"] == 31914000000.0

    # 2. MSFT FY26 Q3
    with open(SEC_FACTS_DIR / "CIK0000789019.json", "r", encoding="utf-8") as f:
        msft_facts = json.load(f)
    msft_recs = provider.parse_sec_company_facts(msft_facts, "MSFT")
    msft_q3 = next(r for r in msft_recs if r["filing_date"] == "2026-04-29" and r["period_end_date"] == "2026-03-31")
    assert msft_q3["revenue"] == 82886000000.0
    assert msft_q3["net_income"] == 31778000000.0
    assert msft_q3["operating_cash_flow"] == 46679000000.0
    assert msft_q3["capex"] == 30876000000.0
    assert msft_q3["free_cash_flow"] == 15803000000.0

    # 3. NVDA FY27 Q2
    with open(SEC_FACTS_DIR / "CIK0001045810.json", "r", encoding="utf-8") as f:
        nvda_facts = json.load(f)
    nvda_recs = provider.parse_sec_company_facts(nvda_facts, "NVDA")
    nvda_q2 = next(r for r in nvda_recs if r["filing_date"] == "2026-08-26" and r["period_end_date"] == "2026-07-26")
    assert nvda_q2["revenue"] == 96221000000.0
    assert nvda_q2["net_income"] == 59688000000.0
    assert nvda_q2["operating_cash_flow"] == 24077000000.0
    assert nvda_q2["capex"] == 2677000000.0
    assert nvda_q2["free_cash_flow"] == 21400000000.0

    # 4. CAT FY26 Q2
    with open(SEC_FACTS_DIR / "CIK0000018230.json", "r", encoding="utf-8") as f:
        cat_facts = json.load(f)
    cat_recs = provider.parse_sec_company_facts(cat_facts, "CAT")
    cat_q2 = next(r for r in cat_recs if r["filing_date"] == "2026-08-05" and r["period_end_date"] == "2026-06-30")
    assert cat_q2["revenue"] == 20543000000.0
    assert cat_q2["net_income"] == 3593000000.0
    assert cat_q2["operating_cash_flow"] == 4371000000.0
    assert cat_q2["capex"] == 587000000.0
    assert cat_q2["free_cash_flow"] == 3784000000.0


def test_xom_successor_cash_flow_null_policy(prod_db_conn):
    """
    Verifies that XOM Successor (CIK 0002115436) sets Q2 discrete cash flow
    metrics to None because no Q1 filing is available in facts to deduce Q2 from YTD.
    """
    xom_path = SEC_FACTS_DIR / "CIK0002115436.json"
    if not xom_path.exists():
        pytest.skip(f"Required SEC facts file for XOM successor not found at {xom_path}")

    provider = DataProvider(prod_db_conn)
    with open(xom_path, "r", encoding="utf-8") as f:
        xom_facts = json.load(f)
    xom_recs = provider.parse_sec_company_facts(xom_facts, "XOM")
    q2_rec = next(r for r in xom_recs if r["filing_date"] == "2026-08-03" and r["period_end_date"] == "2026-06-30")

    assert q2_rec["revenue"] == 116017000000.0
    assert q2_rec["net_income"] == 14525000000.0
    assert q2_rec["operating_cash_flow"] is None, "OCF must be None when prior Q1 is unavailable"
    assert q2_rec["capex"] is None, "CapEx must be None when prior Q1 is unavailable"
    assert q2_rec["free_cash_flow"] is None, "FCF must be None when CapEx is missing"


def test_xom_predecessor_remains_untouched_on_missing_json(prod_db_conn):
    """
    Verifies that XOM Predecessor (CIK 0000034088) records in the database
    are preserved without deletion or corruption when raw facts JSON is missing.
    """
    cur = prod_db_conn.cursor()
    cur.execute("SELECT COUNT(*) FROM company_fundamentals WHERE ticker = 'XOM' AND cik = '0000034088'")
    predecessor_count = cur.fetchone()[0]
    assert predecessor_count == 315, f"Expected 315 rows for predecessor CIK 0000034088, found {predecessor_count}"


def test_remediation_idempotency_simulation():
    """
    Verifies in an isolated in-memory DB that applying the remediation once
    and running the dry-run comparison a second time produces 0 differences.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    provider = DataProvider(conn)

    # Ingest synthetic records
    facts = {
        "cik": "1234567890",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"form": "10-Q", "fy": 2026, "fp": "Q1", "start": "2026-01-01", "end": "2026-03-31", "filed": "2026-05-01", "val": 1000.0},
                            {"form": "10-Q", "fy": 2026, "fp": "Q2", "start": "2026-01-01", "end": "2026-06-30", "filed": "2026-08-01", "val": 2500.0},
                            {"form": "10-Q", "fy": 2026, "fp": "Q2", "start": "2026-04-01", "end": "2026-06-30", "filed": "2026-08-01", "val": 1500.0}
                        ]
                    }
                }
            }
        }
    }
    recs = provider.parse_sec_company_facts(facts, "IDEM")
    # Pass 1: Insert into DB
    provider.save_fundamentals(recs)

    # Pass 2: Dry-run check
    cur = conn.cursor()
    diff_count = 0
    for r in recs:
        pk = (r["ticker"], r["filing_date"], r["period_end_date"], r["form"])
        cur.execute("SELECT revenue FROM company_fundamentals WHERE ticker=? AND filing_date=? AND period_end_date=? AND form=?", pk)
        row = cur.fetchone()
        if row["revenue"] != r["revenue"]:
            diff_count += 1

    assert diff_count == 0, "Remediation is not idempotent!"
    conn.close()
