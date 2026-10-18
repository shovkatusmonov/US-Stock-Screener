"""
test_sp500_history.py - S&P 500 tarixiy tarkibi va qamrov testlari.
"""
import pytest
from sp500_history import SP500HistoryManager


@pytest.fixture
def hm(tmp_path):
    db_file = str(tmp_path / "test_screener.db")
    return SP500HistoryManager(db_path=db_file)


def test_tsla_addition_boundary(hm):
    base_set = {"AAPL", "MSFT", "TSLA"}
    # TSLA 2020-12-21 da qo'shilgan: 2020-01-01 da bo'lmasligi kerak
    assert hm.is_member_on_date("TSLA", "2020-01-01", base_set) is False
    # 2021-01-01 da esa tarkibda bo'lishi kerak
    assert hm.is_member_on_date("TSLA", "2021-01-01", base_set) is True


def test_sivb_removal_boundary(hm):
    base_set = {"AAPL", "MSFT"}
    # SIVB 2023-03-15 da chiqarilgan: 2022-01-01 da indeksda bo'lgan
    assert hm.is_member_on_date("SIVB", "2022-01-01", base_set) is True
    # 2024-01-01 da esa indeksdan chiqqan bo'lishi kerak
    assert hm.is_member_on_date("SIVB", "2024-01-01", base_set) is False


def test_point_in_time_coverage_audit(hm):
    base_set = {"AAPL", "MSFT", "TSLA"}
    universe = hm.get_point_in_time_universe("2022-01-01", base_set)
    # 2022-yilda SIVB bo'lishi kerak, lekin bazamizda (db_tickers) SIVB yo'q bo'lsa:
    available_db = {"AAPL", "MSFT", "TSLA"}
    cov = hm.evaluate_price_coverage(universe, available_db)
    assert cov["missing_count"] >= 1
    assert "SIVB" in cov["missing_tickers"]
    assert 0.0 < cov["coverage_pct"] <= 1.0


def test_invalid_date_boundaries(hm):
    base_set = {"AAPL"}
    # Juda qadimgi sanada kiritilmagan bo'lishi kerak
    assert hm.is_member_on_date("PLTR", "2019-01-01", base_set) is False