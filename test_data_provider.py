"""
test_data_provider.py - Unit tests for real data provider, SEC EDGAR parser, PIT retrieval, and zero fake data.
"""

import json
import sqlite3
from datetime import date
import pytest
import numpy as np

from data_provider import DataProvider, init_db
from scoring_engine import ScoringEngine, ScoringWeights


@pytest.fixture
def in_memory_db():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def mock_sec_facts():
    """Mock SEC EDGAR XBRL company facts payload."""
    return {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"form": "10-Q", "fy": 2023, "fp": "Q1", "end": "2022-12-31", "filed": "2023-02-03", "val": 117154000000},
                            {"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-09-30", "filed": "2023-11-03", "val": 383285000000},
                            {"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-09-28", "filed": "2024-11-01", "val": 391035000000}
                        ]
                    }
                },
                "OperatingIncomeLoss": {
                    "units": {
                        "USD": [
                            {"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-09-30", "filed": "2023-11-03", "val": 114301000000},
                            {"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-09-28", "filed": "2024-11-01", "val": 123216000000}
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-09-30", "filed": "2023-11-03", "val": 96995000000},
                            {"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-09-28", "filed": "2024-11-01", "val": 93736000000}
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            {"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-09-30", "filed": "2023-11-03", "val": 110543000000},
                            {"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-09-28", "filed": "2024-11-01", "val": 118254000000}
                        ]
                    }
                },
                "PaymentsToAcquirePropertyPlantAndEquipment": {
                    "units": {
                        "USD": [
                            {"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-09-30", "filed": "2023-11-03", "val": 10959000000},
                            {"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-09-28", "filed": "2024-11-01", "val": 9450000000}
                        ]
                    }
                },
                "Assets": {
                    "units": {
                        "USD": [
                            {"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-09-30", "filed": "2023-11-03", "val": 352583000000},
                            {"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-09-28", "filed": "2024-11-01", "val": 364980000000}
                        ]
                    }
                },
                "AssetsCurrent": {
                    "units": {
                        "USD": [
                            {"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-09-30", "filed": "2023-11-03", "val": 143566000000},
                            {"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-09-28", "filed": "2024-11-01", "val": 152980000000}
                        ]
                    }
                },
                "LiabilitiesCurrent": {
                    "units": {
                        "USD": [
                            {"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-09-30", "filed": "2023-11-03", "val": 145308000000},
                            {"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-09-28", "filed": "2024-11-01", "val": 176390000000}
                        ]
                    }
                },
                "StockholdersEquity": {
                    "units": {
                        "USD": [
                            {"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-09-30", "filed": "2023-11-03", "val": 62146000000},
                            {"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-09-28", "filed": "2024-11-01", "val": 66890000000}
                        ]
                    }
                },
                "LongTermDebtNoncurrent": {
                    "units": {
                        "USD": [
                            {"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-09-30", "filed": "2023-11-03", "val": 95281000000},
                            {"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-09-28", "filed": "2024-11-01", "val": 85750000000}
                        ]
                    }
                },
                "CommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-09-30", "filed": "2023-11-03", "val": 15550000000},
                            {"form": "10-K", "fy": 2024, "fp": "FY", "end": "2024-09-28", "filed": "2024-11-01", "val": 15204000000}
                        ]
                    }
                }
            }
        }
    }


def test_table_initialization(in_memory_db):
    """Test tables are created cleanly without errors."""
    cur = in_memory_db.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "company_fundamentals" in tables
    assert "news_catalysts" in tables
    assert "earnings_calendar" in tables


def test_sec_edgar_parser_calculations(mock_sec_facts):
    """Test parsing SEC EDGAR company facts and derived metrics."""
    records = DataProvider.parse_sec_company_facts(mock_sec_facts, "AAPL")
    assert len(records) >= 2

    # Find 2023 10-K record
    rec_2023 = next(r for r in records if r["fiscal_year"] == 2023 and r["form"] == "10-K")
    assert rec_2023["filing_date"] == "2023-11-03"
    assert rec_2023["revenue"] == 383285000000
    assert rec_2023["operating_income"] == 114301000000
    assert rec_2023["net_income"] == 96995000000
    assert rec_2023["operating_cash_flow"] == 110543000000
    assert rec_2023["capex"] == 10959000000
    assert rec_2023["free_cash_flow"] == 110543000000 - 10959000000
    assert np.isclose(rec_2023["operating_margin"], 114301000000 / 383285000000)
    assert np.isclose(rec_2023["roe"], 96995000000 / 62146000000)
    assert np.isclose(rec_2023["current_ratio"], 143566000000 / 145308000000)


def test_negative_equity_handling():
    """Verify that negative equity produces negative debt_to_equity and roe flags."""
    neg_equity_facts = {
        "cik": 12345,
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": [{"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-12-31", "filed": "2024-02-15", "val": 1000000}]}},
                "NetIncomeLoss": {"units": {"USD": [{"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-12-31", "filed": "2024-02-15", "val": -500000}]}},
                "StockholdersEquity": {"units": {"USD": [{"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-12-31", "filed": "2024-02-15", "val": -2000000}]}},
                "LongTermDebtNoncurrent": {"units": {"USD": [{"form": "10-K", "fy": 2023, "fp": "FY", "end": "2023-12-31", "filed": "2024-02-15", "val": 5000000}]}}
            }
        }
    }
    records = DataProvider.parse_sec_company_facts(neg_equity_facts, "DISTRESSED")
    assert len(records) == 1
    assert records[0]["debt_to_equity"] == -1.0
    assert records[0]["roe"] == -1.0


def test_point_in_time_query_prevents_lookahead(in_memory_db, mock_sec_facts):
    """
    STRICT POINT-IN-TIME VERIFICATION:
    2023 10-K filed on 2023-11-03.
    Query on 2023-11-02 -> MUST return None (or prior filing only).
    Query on 2023-11-03 -> MUST return 2023 10-K.
    """
    provider = DataProvider(in_memory_db)
    records = provider.parse_sec_company_facts(mock_sec_facts, "AAPL")
    provider.save_fundamentals(records)

    # Date before 2023-02-03 (first Q1 filing)
    before_any = provider.get_point_in_time_fundamentals("AAPL", "2023-01-15")
    assert before_any is None

    # Date between 10-Q (2023-02-03) and 10-K (2023-11-03)
    mid_year = provider.get_point_in_time_fundamentals("AAPL", "2023-06-01")
    assert mid_year is not None
    assert mid_year["form"] == "10-Q"
    assert mid_year["filing_date"] == "2023-02-03"

    # Exactly one day before 10-K was filed
    day_before_10k = provider.get_point_in_time_fundamentals("AAPL", "2023-11-02")
    assert day_before_10k["form"] == "10-Q"

    # On filing date of 10-K
    on_10k = provider.get_point_in_time_fundamentals("AAPL", "2023-11-03")
    assert on_10k["form"] == "10-K"
    assert on_10k["fiscal_year"] == 2023

    # On filing date of 2024 10-K (2024-11-01)
    on_2024_10k = provider.get_point_in_time_fundamentals("AAPL", "2024-11-05")
    assert on_2024_10k["fiscal_year"] == 2024
    assert on_2024_10k["revenue_growth_yoy"] is not None
    assert np.isclose(on_2024_10k["revenue_growth_yoy"], (391035000000 - 383285000000) / 383285000000)


def test_zero_fake_data_policy_in_scoring_engine(in_memory_db):
    """
    STRICT REQUIREMENT: ZERO FAKE DATA!
    When ticker has no fundamentals in DB, provider returns None,
    and ScoringEngine returns score=None, coverage=0.0, status='DATA_UNAVAILABLE'.
    """
    provider = DataProvider(in_memory_db)
    fund_data = provider.get_point_in_time_fundamentals("UNKNOWN_TICKER", "2024-01-01")
    assert fund_data is None

    engine = ScoringEngine()
    comp = engine.evaluate_fundamental(fund_data, "UNKNOWN_TICKER", "2024-01-01")
    assert comp.is_available is False
    assert comp.score is None
    assert comp.coverage == 0.0
    assert comp.status == "DATA_UNAVAILABLE"


def test_news_duplicate_suppression_and_earnings(in_memory_db):
    """Test duplicate suppression and upcoming earnings calendar retrieval."""
    provider = DataProvider(in_memory_db)

    # Save news events
    events = [
        {"ticker": "MSFT", "published_at": "2024-01-10 10:00:00", "headline": "MSFT Cloud Revenue Up", "sentiment_score": 0.8},
        {"ticker": "MSFT", "published_at": "2024-01-10 10:00:00", "headline": "MSFT Cloud Revenue Up", "sentiment_score": 0.8}, # Duplicate!
        {"ticker": "MSFT", "published_at": "2024-01-15 14:00:00", "headline": "New Partnership Announced", "sentiment_score": 0.5}
    ]
    inserted = provider.save_news(events)
    assert inserted == 2  # Duplicate skipped

    # Save earnings date
    provider.save_earnings_dates([
        {"ticker": "MSFT", "earnings_date": "2024-01-25", "time_of_day": "AMC"}
    ])

    # Query as of 2024-01-20
    res = provider.get_recent_news_and_events("MSFT", "2024-01-20")
    assert res is not None
    assert len(res["events"]) == 2
    assert res["upcoming_earnings_date"] == "2024-01-25"


def test_sec_edgar_fetch_and_offline_handling(monkeypatch):
    """Verify that fetch_sec_edgar_facts fetches when online and handles unreachable networks gracefully."""
    # Test real fetch or online status
    result = DataProvider.fetch_sec_edgar_facts(cik="0000320193")
    assert "status" in result
    if result["status"] == "OK":
        assert "facts" in result["data"]
        assert result["data"]["entityName"] == "Apple Inc."

    # Test simulated network outage (airgapped environment / [Errno 11001])
    import urllib.request
    def mock_urlopen_failure(*args, **kwargs):
        import socket
        raise urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen_failure)
    offline_res = DataProvider.fetch_sec_edgar_facts(cik="0000320193")
    assert offline_res["status"] == "ERROR"
    assert offline_res["error_type"] == "OFFLINE_NETWORK_BLOCK"
    assert "11001" in offline_res["details"]
    assert "zero fake data" in offline_res["details"].lower()
