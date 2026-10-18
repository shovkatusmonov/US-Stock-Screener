"""test_pit_membership.py - to'liq PIT a'zolik, alias, maska va yetishmayotgan narxlarni to'ldirish."""
import sqlite3
from datetime import date

import pandas as pd
import pytest

import build_pit_membership as bpm
from sp500_history import SP500HistoryManager
import download_missing_pit as dmp


def _snaps():
    return pd.DataFrame({
        "date": ["2019-01-02", "2020-01-02", "2021-01-04", "2022-06-09"],
        "tickers": ["AAA,BBB,FB", "AAA,BBB,FB,CCC", "AAA,FB,CCC", "AAA,META,CCC,BBB"],
    })


def test_intervals_and_alias_merge():
    rows = bpm.build_rows(_snaps(), press=[])
    d = {}
    for t, s, e, src in rows:
        d.setdefault(t, []).append((s, e))
    # FB -> META birlashtirilgan, uzluksiz: 2019-01-02 dan hozirgacha
    assert d["META"] == [("2019-01-02", None)]
    assert "FB" not in d
    # BBB: 2021-01-04 da chiqib, 2022-06-09 da qaytgan -> ikki oraliq
    assert d["BBB"] == [("2019-01-02", "2021-01-03"), ("2022-06-09", None)]
    assert d["CCC"] == [("2020-01-02", None)]


def test_press_changes_and_unverified_universe_diff():
    press = [{"date": "2022-07-01", "add": ["NEW"], "remove": ["CCC"], "source": "PR"}]
    rows = bpm.build_rows(_snaps(), universe={"AAA", "META", "BBB", "NEW", "ZZZ"}, press=press)
    d = {(t): (s, e, src) for t, s, e, src in rows if t in ("NEW", "CCC", "ZZZ")}
    assert d["NEW"] == ("2022-07-01", None, "PR")
    assert d["CCC"][1] == "2022-06-30" and d["CCC"][0] == "2020-01-02"
    # universe.csv da bor, lekin tarixda yo'q -> UNVERIFIED
    assert d["ZZZ"][2].startswith("UNVERIFIED")


def _db(tmp_path):
    db = str(tmp_path / "t.db")
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE prices (ticker TEXT NOT NULL, date TEXT NOT NULL, open REAL, high REAL, low REAL,
                 close REAL, adj_close REAL, volume REAL, source TEXT NOT NULL, fetched_at TEXT NOT NULL,
                 PRIMARY KEY (ticker, date))""")
    c.commit()
    c.close()
    bpm.write_membership(db, bpm.build_rows(_snaps(), press=[]))
    return db


def test_complete_manager_unknown_ticker_is_not_member(tmp_path):
    hm = SP500HistoryManager(db_path=_db(tmp_path))
    assert hm.complete
    assert hm.is_member_on_date("NOPE", "2021-01-01", {"NOPE"}) is False     # eski xatti-harakat: True edi
    assert hm.is_member_on_date("NOPE", "2021-01-01", {"NOPE"}, allow_unknown=False) is None
    assert hm.is_member_on_date("BBB", "2021-06-01", set()) is False          # chiqarilgan davr
    assert hm.is_member_on_date("BBB", "2022-06-09", set()) is True           # qaytgan


def test_membership_mask_supports_multiple_intervals(tmp_path):
    hm = SP500HistoryManager(db_path=_db(tmp_path))
    idx = pd.DatetimeIndex(["2020-06-01", "2021-06-01", "2022-07-01"])
    assert hm.membership_mask("BBB", idx).tolist() == [True, False, True]
    assert hm.membership_mask("UNKNOWN", idx).tolist() == [False] * 3


def test_coverage_uses_real_universe_not_current_list(tmp_path):
    db = _db(tmp_path)
    c = sqlite3.connect(db)
    # faqat AAA va META (=FB) uchun narx bor; BBB/CCC yo'q
    for t in ("AAA", "META"):
        c.execute("INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?,?)", (t, "2020-06-01", 1, 1, 1, 1, 1, 1, "x", "x"))
    c.commit()
    hm = SP500HistoryManager(db_path=db)
    cov = hm.evaluate_date_matrix_coverage(["2020-06-01"], {"AAA", "META"}, c)
    assert cov["total_slots"] == 4          # AAA, BBB, META, CCC
    assert cov["filled_slots"] == 2
    assert cov["coverage_pct"] == pytest.approx(0.5)   # joriy ro'yxatdan qurilsa 100% chiqardi


def test_missing_report_and_fill_with_mock_fetch(tmp_path):
    db = _db(tmp_path)
    hm = SP500HistoryManager(db_path=db)
    c = sqlite3.connect(db)
    todo = dmp.missing_pit_tickers(c, hm, start="2019-01-02")
    assert {"AAA", "BBB", "CCC", "META"} <= set(todo)
    c.close()

    def fake_fetch(t, start):
        if t == "BBB":
            idx = pd.bdate_range("2018-01-01", periods=300)
            return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0,
                                 "Adj Close": 1.0, "Volume": 1.0}, index=idx)
        return pd.DataFrame()                 # boshqalari "topilmadi" (delisted)

    res = dmp.run(db_path=db, fetch=fake_fetch)
    assert res["filled"] == ["BBB"]
    assert "AAA" in res["not_found"]
    c = sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM prices WHERE ticker='BBB'").fetchone()[0] == 300
