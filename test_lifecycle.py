"""test_lifecycle.py - run_daily portfel hayot sikli: fill, bekor, chiqish, idempotentlik, muddat."""
import sqlite3

import pandas as pd
import pytest

from lifecycle import advance_portfolio
from risk_manager import RiskConfig
from trade_manager import TradeManager

DATES = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-01", periods=8)]  # Du..Pa, Du, Se


def _setup(tmp_path, bars):
    """bars: {ticker: {date: (o,h,l,c)}}; SPY har kuni bor."""
    db = str(tmp_path / "t.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE prices (ticker TEXT NOT NULL, date TEXT NOT NULL, open REAL, high REAL, low REAL,
                    close REAL, adj_close REAL, volume REAL, source TEXT NOT NULL, fetched_at TEXT NOT NULL,
                    PRIMARY KEY (ticker, date))""")
    allb = dict(bars)
    allb["SPY"] = {d: (400, 401, 399, 400) for d in DATES}
    for t, per in allb.items():
        for d, (o, h, l, c) in per.items():
            conn.execute("INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?,?)", (t, d, o, h, l, c, c, 1e6, "x", "x"))
    conn.commit()
    return db, conn, TradeManager(db_path=db, initial_cash=10_000.0)


def _plan(tm, ticker="AAA", entry=100.0, stop=90.0, target=135.0, shares=10, signal="2024-01-01"):
    ok, sid = tm.add_planned_trade({"ticker": ticker, "signal_date": signal, "signal_price": entry,
                                    "shares": shares, "planned_stop": stop, "planned_target": target,
                                    "dollar_risk": 100.0})
    assert ok
    return sid


def _flat(price=100.0, days=DATES):
    return {d: (price, price + 1, price - 1, price) for d in days}


def _status(tm, sid):
    with tm._get_conn() as c:
        return dict(c.execute("SELECT * FROM portfolio_trades WHERE signal_id=?", (sid,)).fetchone())


def test_plan_fills_at_next_session_open_and_stays_open(tmp_path):
    db, conn, tm = _setup(tmp_path, {"AAA": _flat()})
    sid = _plan(tm)
    ev = advance_portfolio(tm, conn, DATES[3], RiskConfig())
    row = _status(tm, sid)
    assert [e["ticker"] for e in ev["filled"]] == ["AAA"]
    assert row["status"] == "OPEN" and row["entry_date"] == DATES[1]
    assert row["hold_days"] == 2            # 3-kun (idx3) gacha: idx2, idx3 (kirish kuni hisobga olinmaydi)
    assert row["last_bar_date"] == DATES[3]


def test_idempotent_second_run_changes_nothing(tmp_path):
    db, conn, tm = _setup(tmp_path, {"AAA": _flat()})
    sid = _plan(tm)
    advance_portfolio(tm, conn, DATES[3], RiskConfig())
    before, cash = _status(tm, sid), tm.get_cash()
    ev = advance_portfolio(tm, conn, DATES[3], RiskConfig())
    assert _status(tm, sid) == before and tm.get_cash() == cash
    assert not ev["filled"] and not ev["closed"]


def test_catch_up_over_several_days_equals_day_by_day(tmp_path):
    """Kunma-kun (har kuni ishga tushirish) va bir martalik catch-up bir xil natija berishi shart."""
    bars = {"AAA": _flat()}
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _, c1, tm1 = _setup(tmp_path / "a", bars)
    sid1 = _plan(tm1)
    advance_portfolio(tm1, c1, DATES[5], RiskConfig())
    _, c2, tm2 = _setup(tmp_path / "b", bars)
    sid2 = _plan(tm2)
    for d in DATES[:6]:
        advance_portfolio(tm2, c2, d, RiskConfig())
    a, b = _status(tm1, sid1), _status(tm2, sid2)
    for k in ("status", "entry_date", "hold_days", "last_bar_date", "fill_price", "remaining_shares"):
        assert a[k] == b[k]
    assert tm1.get_cash() == pytest.approx(tm2.get_cash())


def test_gap_over_3pct_cancels_plan(tmp_path):
    bars = {"AAA": {d: (105, 106, 104, 105) for d in DATES}}   # +5% ochilish
    db, conn, tm = _setup(tmp_path, bars)
    sid = _plan(tm)
    ev = advance_portfolio(tm, conn, DATES[2], RiskConfig())
    assert _status(tm, sid)["status"] == "CANCELLED"
    assert "CANCELLED_GAP_LIMIT" in ev["cancelled"][0]["reason"]
    assert tm.get_cash() == 10_000.0


def test_stop_hit_closes_position_and_returns_cash(tmp_path):
    per = _flat()
    per[DATES[3]] = (99.0, 100.0, 80.0, 85.0)                  # low stopdan past
    db, conn, tm = _setup(tmp_path, {"AAA": per})
    sid = _plan(tm)
    ev = advance_portfolio(tm, conn, DATES[4], RiskConfig())
    row = _status(tm, sid)
    assert row["status"] == "CLOSED" and row["exit_reason"] == "SL" and row["exit_date"] == DATES[3]
    assert [e["reason"] for e in ev["closed"]] == ["SL"]
    assert row["realized_pnl"] < 0 and tm.get_cash() < 10_000.0 and tm.get_cash() > 9_000.0


def test_entry_day_intraday_stop_is_applied_like_engine(tmp_path):
    per = _flat()
    per[DATES[1]] = (100.0, 101.0, 85.0, 90.0)                 # kirish kunining o'zida stop
    db, conn, tm = _setup(tmp_path, {"AAA": per})
    sid = _plan(tm)
    advance_portfolio(tm, conn, DATES[1], RiskConfig())
    row = _status(tm, sid)
    assert row["status"] == "CLOSED" and row["exit_reason"] == "SL" and row["hold_days"] == 0


def test_plan_without_entry_session_bar_expires(tmp_path):
    per = {d: v for d, v in _flat().items() if d != DATES[1]}  # kirish sessiyasida AAA narxi yo'q
    db, conn, tm = _setup(tmp_path, {"AAA": per})
    sid = _plan(tm)
    ev = advance_portfolio(tm, conn, DATES[3], RiskConfig())
    assert _status(tm, sid)["status"] == "CANCELLED"
    assert ev["cancelled"][0]["reason"] == "EXPIRED_NO_DATA"


def test_plan_stays_planned_until_entry_session_exists(tmp_path):
    db, conn, tm = _setup(tmp_path, {"AAA": _flat()})
    sid = _plan(tm, signal=DATES[3])
    advance_portfolio(tm, conn, DATES[3], RiskConfig())        # signal kuni: hali kirish sessiyasi yo'q
    assert _status(tm, sid)["status"] == "PLANNED"
    advance_portfolio(tm, conn, DATES[4], RiskConfig())
    assert _status(tm, sid)["status"] == "OPEN"


def test_time_exit_matches_engine_hold_days(tmp_path):
    db, conn, tm = _setup(tmp_path, {"AAA": _flat()})
    sid = _plan(tm)
    advance_portfolio(tm, conn, DATES[7], RiskConfig(), max_hold=3)
    row = _status(tm, sid)
    assert row["status"] == "CLOSED" and row["exit_reason"] == "TIME"
    assert row["hold_days"] == 3 and row["exit_date"] == DATES[4]   # kirish idx1 + 3 bar = idx4


def test_two_positions_one_updates_other_exits_no_db_lock(tmp_path):
    """Regressiya: bir bar'da biri oddiy yangilanib, ikkinchisi yopilganda 'database is locked' bo'lmasligi kerak."""
    per_ok = _flat()
    per_sl = _flat()
    per_sl[DATES[2]] = (99.0, 100.0, 80.0, 85.0)
    db, conn, tm = _setup(tmp_path, {"AAA": per_ok, "BBB": per_sl})
    s1 = _plan(tm, "AAA")
    s2 = _plan(tm, "BBB")
    ev = advance_portfolio(tm, conn, DATES[3], RiskConfig())
    assert _status(tm, s1)["status"] == "OPEN" and _status(tm, s2)["status"] == "CLOSED"
    assert [e["ticker"] for e in ev["closed"]] == ["BBB"]
