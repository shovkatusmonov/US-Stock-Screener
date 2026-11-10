"""
tests/test_universe.py - Tests for Universe Manager, Normalization, Classification, and Delisting.
"""

from pathlib import Path
import sqlite3
import pytest
from app.data.universe import (
    UniverseManager,
    Instrument,
    normalize_symbol,
    classify_instrument
)


SAMPLE_INSTRUMENTS = [
    Instrument(symbol="AAPL", exchange="NASDAQ", name="Apple Inc. - Common Stock", asset_class="CS", is_etf=False),
    Instrument(symbol="SPY", exchange="NYSE_ARCA", name="SPDR S&P 500 ETF Trust", asset_class="ETF", is_etf=True),
    Instrument(symbol="BABA", exchange="NYSE", name="Alibaba Group Holding Limited American Depositary Shares", asset_class="ADR", is_etf=False),
    Instrument(symbol="BAC-PA", exchange="NYSE", name="Bank of America Corporation 7.25% Non-Cumulative Preferred Stock, Series L", asset_class="PRF", is_etf=False),
    Instrument(symbol="XYZ-WT", exchange="NYSE_AMERICAN", name="XYZ Acquisition Corp Warrant", asset_class="WNT", is_etf=False),
    Instrument(symbol="ABC-UN", exchange="NYSE", name="ABC Units consisting of 1 CS and 1 Warrant", asset_class="UNT", is_etf=False),
    Instrument(symbol="RGT-RT", exchange="NASDAQ", name="Right to Acquire Ordinary Shares", asset_class="RGT", is_etf=False),
]


def test_normalize_symbol():
    assert normalize_symbol("AAPL") == "AAPL"
    assert normalize_symbol("BRK B") == "BRK-B"
    assert normalize_symbol("BF.B") == "BF-B"
    assert normalize_symbol("BAC$A") == "BAC-PA"
    assert normalize_symbol("WFC/PR/A") == "WFC-PA"
    assert normalize_symbol("XYZ/WS") == "XYZ-WT"
    assert normalize_symbol("ABC/U") == "ABC-UN"
    assert normalize_symbol("RGT/RT") == "RGT-RT"


def test_classify_instrument():
    assert classify_instrument("AAPL", "Apple Inc. Common Stock", False) == "CS"
    assert classify_instrument("SPY", "SPDR S&P 500 ETF Trust", True) == "ETF"
    assert classify_instrument("BABA", "Alibaba Group American Depositary Shares", False) == "ADR"
    assert classify_instrument("BAC-PA", "Bank of America Preferred Stock", False) == "PRF"
    assert classify_instrument("XYZ-WT", "XYZ Warrant", False) == "WNT"
    assert classify_instrument("ABC-UN", "ABC Acquisition Units", False) == "UNT"
    assert classify_instrument("RGT-RT", "Right to Acquire Ordinary Shares", False) == "RGT"


def test_sync_universe_and_delisting(tmp_path):
    test_db = tmp_path / "test_universe.db"
    mgr = UniverseManager(db_path=str(test_db))

    # Initial sync
    stats1 = mgr.sync_universe(SAMPLE_INSTRUMENTS)
    assert stats1["inserted"] == len(SAMPLE_INSTRUMENTS)
    assert stats1["active_count"] == len(SAMPLE_INSTRUMENTS)

    # Verify both instruments and legacy universe table were populated
    conn = sqlite3.connect(str(test_db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM instruments")
    assert cur.fetchone()[0] == len(SAMPLE_INSTRUMENTS)

    cur.execute("SELECT COUNT(*) FROM universe")
    assert cur.fetchone()[0] == len(SAMPLE_INSTRUMENTS)

    # Second sync: AAPL is missing (delisted) and NEWCO is added
    updated_instruments = [inst for inst in SAMPLE_INSTRUMENTS if inst.symbol != "AAPL"]
    updated_instruments.append(Instrument(
        symbol="NEWCO", exchange="NASDAQ", name="New Company Inc. Common Stock", asset_class="CS", is_etf=False
    ))

    stats2 = mgr.sync_universe(updated_instruments)
    assert stats2["inserted"] == 1
    assert stats2["delisted"] == 1

    # Check that AAPL is marked as DELISTED
    row_aapl = cur.execute("SELECT status, is_active, delisted_date FROM instruments WHERE symbol='AAPL'").fetchone()
    assert row_aapl["status"] == "DELISTED"
    assert row_aapl["is_active"] == 0
    assert row_aapl["delisted_date"] is not None

    # Check that NEWCO is ACTIVE
    row_newco = cur.execute("SELECT status, is_active, first_seen FROM instruments WHERE symbol='NEWCO'").fetchone()
    assert row_newco["status"] == "ACTIVE"
    assert row_newco["is_active"] == 1
    assert row_newco["first_seen"] is not None

    conn.close()


def test_composite_key_allows_same_symbol_different_exchanges(tmp_path):
    test_db = tmp_path / "test_collision.db"
    mgr = UniverseManager(db_path=str(test_db))

    insts = [
        Instrument(symbol="XYZ", exchange="NASDAQ", name="XYZ Tech Nasdaq", asset_class="CS", is_etf=False),
        Instrument(symbol="XYZ", exchange="BATS", name="XYZ Fund Bats", asset_class="ETF", is_etf=True),
    ]

    stats = mgr.sync_universe(insts)
    assert stats["inserted"] == 2

    conn = sqlite3.connect(str(test_db))
    count = conn.execute("SELECT COUNT(*) FROM instruments WHERE symbol='XYZ'").fetchone()[0]
    conn.close()
    assert count == 2
