"""test_data_audit.py - sakrashlarni hajm bo'yicha tasniflash va a'zolik oynasi ichidagi qamrov."""
import sqlite3

import numpy as np
import pandas as pd

import audit_suite as au
import build_pit_membership as bpm
import download_missing_pit as dmp
from sp500_history import SP500HistoryManager


def _series(ticker, n=40, price=100.0, vol=1000.0, start="2024-01-01"):
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame({"ticker": ticker, "date": idx, "close": price, "volume": vol})


def _jump(ticker, price_after, vol_after, **kw):
    d = _series(ticker, **kw)
    d.loc[d.index[-1], ["close", "volume"]] = [price_after, vol_after]
    return d


def test_classification_by_volume_not_by_percent():
    df = pd.concat([
        _jump("SPLIT", 50.0, 2000.0),       # narx yarmi, hajm 2x -> tuzatilmagan split
        _jump("CRASH", 50.0, 8000.0),       # narx yarmi, hajm 8x -> haqiqiy voqea
        _jump("GLITCH", 160.0, 1000.0),     # hajm o'zgarmagan, +60% -> shubhali
        _jump("PENNY", 1.5, 1000.0, price=1.0),
    ])
    j = au.classify_jumps(df).set_index("ticker")["class"].to_dict()
    assert j == {"SPLIT": "SPLIT_SUSPECT", "CRASH": "EVENT_VOLUME", "GLITCH": "GLITCH_SUSPECT", "PENNY": "PENNY_OR_REUSED"}


def test_market_wide_day_is_grouped_separately():
    df = pd.concat([_jump(f"T{i}", 55.0, 4000.0) for i in range(25)])
    j = au.classify_jumps(df)
    assert (j["class"] == "MARKET_WIDE").all() and len(j) == 25


def test_in_window_flag_uses_membership():
    df = _jump("AAA", 160.0, 1000.0)
    last = df["date"].iloc[-1].strftime("%Y-%m-%d")
    j_in = au.classify_jumps(df, {"AAA": [("2019-01-01", None)]})
    j_out = au.classify_jumps(df, {"AAA": [("2019-01-01", "2020-01-01")]})
    assert bool(j_in["in_window"].iloc[0]) is True and bool(j_out["in_window"].iloc[0]) is False


def _db(tmp_path):
    db = str(tmp_path / "t.db")
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE prices (ticker TEXT NOT NULL, date TEXT NOT NULL, open REAL, high REAL, low REAL,
                 close REAL, adj_close REAL, volume REAL, source TEXT NOT NULL, fetched_at TEXT NOT NULL,
                 PRIMARY KEY (ticker, date))""")
    snaps = pd.DataFrame({"date": ["2020-01-02", "2021-01-04"], "tickers": ["AAA,JUNK", "AAA"]})
    c.commit()
    c.close()
    bpm.write_membership(db, bpm.build_rows(snaps, press=[]))
    return db


def _fill(c, ticker, start, end):
    for d in pd.bdate_range(start, end):
        c.execute("INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (ticker, d.strftime("%Y-%m-%d"), 1, 1, 1, 1, 1, 1, "x", "x"))


def test_rows_outside_membership_window_do_not_count_as_filled(tmp_path):
    db = _db(tmp_path)
    c = sqlite3.connect(db)
    _fill(c, "AAA", "2020-01-02", "2025-12-31")     # to'liq (bazadagi oxirgi sanagacha)
    _fill(c, "SPY", "2020-01-02", "2025-12-31")
    _fill(c, "JUNK", "2023-01-02", "2025-12-31")    # 700+ qator, lekin a'zolik oynasidan (2020-01..2021-01) tashqarida
    c.commit()
    hm = SP500HistoryManager(db_path=db)
    todo = dmp.missing_pit_tickers(c, hm, start="2019-01-02")
    assert "JUNK" in todo and todo["JUNK"]["in_window"] == 0 and todo["JUNK"]["n"] > 260
    assert "AAA" not in todo
    cov = au.pit_window_coverage(c, hm.membership_rules).set_index("ticker")
    assert cov.loc["JUNK", "have"] == 0 and cov.loc["JUNK", "outside_rows"] > 260
    assert cov.loc["AAA", "ratio"] >= 0.95


def test_pit_window_coverage_matches_matrix_and_ignores_non_spy_dates(tmp_path):
    """
    1-VAZIFA regressiya testi:
    Agar aksiyada SPY kalendarida yo'q sanada qator bo'lsa, pit_window_coverage uni
    'have' ga qo'shmasligi va evaluate_date_matrix_coverage bilan aynan bir xil raqam qaytarishi shart.
    """
    import sqlite3
    from sp500_history import SP500HistoryManager
    from audit_suite import pit_window_coverage

    db_file = tmp_path / "cov_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE prices (ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, adj_close REAL)")

    spy_dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    for d in spy_dates:
        conn.execute("INSERT INTO prices VALUES ('SPY', ?, 100, 101, 99, 100, 1000, 100)", (d,))

    for d in ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-06"]:
        conn.execute("INSERT INTO prices VALUES ('AAA', ?, 50, 51, 49, 50, 500, 50)", (d,))

    conn.execute("INSERT INTO prices VALUES ('BBB', '2019-05-01', 10, 11, 9, 10, 100, 10)")
    conn.commit()

    hm = SP500HistoryManager(rules={}, complete=True)
    hm.membership_rules = {
        "AAA": [("2024-01-01", "2024-12-31")],
        "BBB": [("2024-01-01", "2024-12-31")],
    }
    hm.complete = True

    cov_df = pit_window_coverage(conn, rules=hm, start_date="2024-01-01", end_date="2024-12-31")
    cov_matrix = hm.evaluate_date_matrix_coverage(spy_dates, {"AAA", "BBB"}, conn)

    assert int(cov_df["expected"].sum()) == 8
    assert int(cov_df["have"].sum()) == 3
    assert int(cov_df["expected"].sum()) == cov_matrix["total_slots"]
    assert int(cov_df["have"].sum()) == cov_matrix["filled_slots"]

    cov_by_t = cov_df.set_index("ticker")
    assert cov_by_t.loc["AAA", "have"] == 3
    assert cov_by_t.loc["BBB", "have"] == 0
    conn.close()
