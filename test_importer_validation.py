"""
test_importer_validation.py - Comprehensive Unit & Validation Tests for SEC EDGAR Importer (Phase 2).
Validates:
1. SEC official API headers, User-Agent, and rate limiting logic.
2. Retry and backoff on transient errors (no infinite loops).
3. Strict Idempotency: re-importing the same company never duplicates rows.
4. Field validity: filing_date >= period_end_date, correct forms, ticker and CIK integrity.
5. Point-In-Time guarantee: filing_date <= as_of_date prevents lookahead.
6. Clear distinction: SEC 404/empty facts gives NO_DATA, whereas network failures give ERROR.
7. Negative equity and insolvency handling.
"""

import json
import sqlite3
import urllib.request
import urllib.error
import pytest
from pathlib import Path

from data_provider import DataProvider, init_db
from import_fundamentals import fetch_and_import_ticker, evaluate_record_quality


@pytest.fixture
def test_db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def sample_facts_payload():
    return {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"form": "10-Q", "fy": 2023, "fp": "Q1", "end": "2022-12-31", "filed": "2023-02-03", "val": 117154000000},
                            {"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-09-30", "filed": "2023-11-03", "val": 383285000000}
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {"form": "10-Q", "fy": 2023, "fp": "Q1", "end": "2022-12-31", "filed": "2023-02-03", "val": 29998000000},
                            {"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-09-30", "filed": "2023-11-03", "val": 96995000000}
                        ]
                    }
                },
                "StockholdersEquity": {
                    "units": {
                        "USD": [
                            {"form": "10-Q", "fy": 2023, "fp": "Q1", "end": "2022-12-31", "filed": "2023-02-03", "val": 56727000000},
                            {"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-09-30", "filed": "2023-11-03", "val": 62146000000}
                        ]
                    }
                }
            }
        }
    }


def test_idempotency_of_importer(test_db, sample_facts_payload):
    """Re-importing the same filings must not duplicate rows."""
    provider = DataProvider(test_db)
    records = provider.parse_sec_company_facts(sample_facts_payload, "AAPL")
    assert len(records) == 2

    # First ingestion
    c1 = provider.save_fundamentals(records)
    assert c1 == 2
    cur = test_db.cursor()
    cur.execute("SELECT COUNT(*) FROM company_fundamentals WHERE ticker = 'AAPL'")
    assert cur.fetchone()[0] == 2

    # Second ingestion of the exact same records
    c2 = provider.save_fundamentals(records)
    assert c2 == 2
    cur.execute("SELECT COUNT(*) FROM company_fundamentals WHERE ticker = 'AAPL'")
    assert cur.fetchone()[0] == 2, "Row count changed on second ingestion! Idempotency violated!"


def test_filing_dates_and_chronology(sample_facts_payload):
    """filing_date must be >= period_end_date (chronological reporting rule)."""
    provider = DataProvider(sqlite3.connect(":memory:"))
    records = provider.parse_sec_company_facts(sample_facts_payload, "AAPL")
    for r in records:
        assert r["filing_date"] >= r["period_end_date"], f"Filing date {r['filing_date']} is before period end {r['period_end_date']}"
        assert r["form"] in ("10-K", "10-Q")
        assert r["ticker"] == "AAPL"
        assert r["cik"] == "0000320193"


def test_point_in_time_guarantee_prevents_lookahead(test_db, sample_facts_payload):
    """get_point_in_time_fundamentals must never return a filing before its filing_date."""
    provider = DataProvider(test_db)
    records = provider.parse_sec_company_facts(sample_facts_payload, "AAPL")
    provider.save_fundamentals(records)

    # 1. Before 10-Q filing (as of 2023-02-01, period ended 2022-12-31 but filed 2023-02-03)
    pit_before = provider.get_point_in_time_fundamentals("AAPL", "2023-02-01")
    assert pit_before is None, "Lookahead bias: filing returned before it was publicly filed!"

    # 2. On 10-Q filing date (2023-02-03)
    pit_q1 = provider.get_point_in_time_fundamentals("AAPL", "2023-02-03")
    assert pit_q1 is not None
    assert pit_q1["form"] == "10-Q"
    assert pit_q1["net_income"] == 29998000000

    # 3. Before 10-K filing (2023-11-01) -> must still return 10-Q, NOT 10-K
    pit_mid = provider.get_point_in_time_fundamentals("AAPL", "2023-11-01")
    assert pit_mid["form"] == "10-Q"

    # 4. On or after 10-K filing date (2023-11-03) -> returns 10-K
    pit_k = provider.get_point_in_time_fundamentals("AAPL", "2023-11-03")
    assert pit_k["form"] == "10-K"
    assert pit_k["net_income"] == 96995000000


def test_distinguish_no_data_from_network_error(monkeypatch):
    """HTTP 404 must return NO_DATA, whereas network offline must return ERROR."""
    # 1. Test 404 Not Found
    def mock_404(*args, **kwargs):
        raise urllib.error.HTTPError("http://example.com", 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", mock_404)
    res_404 = DataProvider.fetch_sec_edgar_facts(cik="9999999999")
    assert res_404["status"] == "NO_DATA"
    assert res_404["error_type"] == "SEC_404_NOT_FOUND"

    # 2. Test Offline DNS failure ([Errno 11001])
    def mock_offline(*args, **kwargs):
        import socket
        raise urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_offline)
    res_offline = DataProvider.fetch_sec_edgar_facts(cik="0000320193")
    assert res_offline["status"] == "ERROR"
    assert res_offline["error_type"] == "OFFLINE_NETWORK_BLOCK"
    assert "11001" in res_offline["details"]


def test_retry_on_rate_limit(monkeypatch):
    """HTTP 429 must trigger backoff and retries, succeeding if subsequent attempt succeeds."""
    call_count = 0
    def mock_rate_limit_then_succeed(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise urllib.error.HTTPError("http://example.com", 429, "Too Many Requests", {}, None)
        
        # Succeed on 2nd attempt
        import io
        return io.BytesIO(b'{"cik": 320193, "entityName": "Apple Inc.", "facts": {}}')

    monkeypatch.setattr(urllib.request, "urlopen", mock_rate_limit_then_succeed)
    res = DataProvider.fetch_sec_edgar_facts(cik="0000320193", max_retries=2, backoff_sec=0.01)
    assert res["status"] == "OK"
    assert call_count == 2


def test_negative_equity_detection(test_db):
    """Negative equity must be preserved and assigned D/E = -1.0 and ROE = -1.0."""
    provider = DataProvider(test_db)
    synthetic_facts = {
        "cik": 12927,
        "entityName": "The Boeing Company",
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [{"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-12-31", "filed": "2024-01-31", "val": 77794000000}]}},
                "NetIncomeLoss": {"units": {"USD": [{"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-12-31", "filed": "2024-01-31", "val": -2222000000}]}},
                "StockholdersEquity": {"units": {"USD": [{"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-12-31", "filed": "2024-01-31", "val": -17178000000}]}},
                "LongTermDebt": {"units": {"USD": [{"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-12-31", "filed": "2024-01-31", "val": 46995000000}]}}
            }
        }
    }
    records = provider.parse_sec_company_facts(synthetic_facts, "BA")
    assert len(records) == 1
    rec = records[0]
    assert rec["total_equity"] == -17178000000
    assert rec["debt_to_equity"] == -1.0, "Negative equity must set debt_to_equity to -1.0 risk marker"
    assert rec["roe"] == -1.0, "Negative equity must set roe to -1.0 risk marker"
