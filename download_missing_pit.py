"""
download_missing_pit.py - PIT universe uchun bazada yetishmayotgan narxlarni to'ldiradi.

Ishga tushirish (loyiha papkasida, o'z kompyuteringizda):
    python download_missing_pit.py                 # yfinance orqali urinadi
    python download_missing_pit.py --csv-dir DIR   # DIR/TICKER.csv fayllaridan import
        (ustunlar: Date,Open,High,Low,Close,Adj Close,Volume)
    python download_missing_pit.py --report        # faqat nima yetishmasligini ko'rsatadi

DIQQAT: Yahoo Finance ko'pincha delisted (sotib olingan/bankrot) kompaniyalar tarixini BERMAYDI.
Bunday tickerlar "TOPILMADI" ro'yxatida chiqadi - ular uchun pullik/maxsus manba kerak
(masalan Norgate, Sharadar, EODHD, Tiingo) va CSV importidan foydalaning.
"""
import sqlite3
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

from sp500_history import SP500HistoryManager

DB_PATH = "data/screener.db"
PIT_START = "2019-01-02"
WARMUP_DAYS = 400   # SMA200/rs60 uchun a'zolikdan oldingi tarix


def missing_pit_tickers(conn: sqlite3.Connection, hm: SP500HistoryManager,
                        start: str = PIT_START, min_ratio: float = 0.85) -> Dict[str, dict]:
    """
    A'zolik oynasi ICHIDA narxi yo'q/to'liq emas tickerlar: {ticker: {n, first_price, member_from, in_window, expected}}.
    Oynadan tashqaridagi qatorlar (masalan ticker keyinchalik boshqa kompaniyaga berilgan) sanalmaydi.
    expected = oyna ichidagi taxminiy savdo kunlari (ish kunlari * 0.96).
    """
    import numpy as np
    last = conn.execute("SELECT MAX(date) FROM prices").fetchone()[0] or pd.Timestamp.today().strftime("%Y-%m-%d")
    have = {t: (n, first) for t, n, first in conn.execute(
        "SELECT ticker, COUNT(*), MIN(date) FROM prices GROUP BY ticker")}
    out = {}
    for t, rules in hm.membership_rules.items():
        spans = [(max(s, start), min(e, last) if e else last) for s, e in rules if (e is None or e >= start)]
        spans = [(s, e) for s, e in spans if s <= e]
        if not spans:
            continue
        expected = sum(np.busday_count(s, (pd.Timestamp(e) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")) for s, e in spans) * 0.96
        in_win = sum(conn.execute("SELECT COUNT(*) FROM prices WHERE ticker=? AND date BETWEEN ? AND ?",
                                  (t, s, e)).fetchone()[0] for s, e in spans)
        if expected >= 1 and in_win < min_ratio * expected:
            n, first = have.get(t, (0, None))
            out[t] = {"n": n, "first_price": first, "member_from": min(s for s, _ in spans),
                      "in_window": in_win, "expected": int(expected)}
    return out


def save_prices(conn: sqlite3.Connection, ticker: str, df: pd.DataFrame, source: str) -> int:
    from datetime import datetime, timezone
    fetched = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = [(ticker, pd.Timestamp(d).strftime("%Y-%m-%d"), r["Open"], r["High"], r["Low"], r["Close"],
             r["Adj Close"], r["Volume"], source, fetched) for d, r in df.iterrows()]
    before = conn.total_changes
    conn.executemany("INSERT OR IGNORE INTO prices VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return conn.total_changes - before


def _yf_fetch(ticker: str, start: str) -> pd.DataFrame:
    from download_data import fetch   # yfinance faqat shu yerda import qilinadi
    return fetch(ticker, start)


def run(db_path: str = DB_PATH, csv_dir: Optional[str] = None, report_only: bool = False,
        fetch: Callable[[str, str], pd.DataFrame] = _yf_fetch) -> dict:
    conn = sqlite3.connect(db_path)
    hm = SP500HistoryManager(db_path)
    todo = missing_pit_tickers(conn, hm)
    print(f"PIT universe'da narxi yetishmaydigan tickerlar: {len(todo)}")
    if report_only:
        for t, i in sorted(todo.items()):
            print(f"  {t:<7} oyna ichida {i['in_window']}/{i['expected']} kun, jami qator={i['n']:<5} a'zolik boshi={i['member_from']} narx boshi={i['first_price']}")
        conn.close()
        return {"todo": list(todo), "filled": [], "not_found": list(todo)}
    filled, not_found = [], []
    for t in sorted(todo):
        start = (pd.Timestamp(todo[t]["member_from"]) - pd.Timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
        df = pd.DataFrame()
        source = "yfinance"
        try:
            if csv_dir and (Path(csv_dir) / f"{t}.csv").exists():
                df = pd.read_csv(Path(csv_dir) / f"{t}.csv", parse_dates=["Date"]).set_index("Date")
                df = df[["Open", "High", "Low", "Close", "Adj Close", "Volume"]]
                source = "csv_import"
            elif not csv_dir:
                df = fetch(t, start)
        except Exception as e:  # noqa: BLE001
            print(f"  {t:<7} XATO {e!r}"[:120])
        if df is None or df.empty:
            not_found.append(t)
            print(f"  {t:<7} TOPILMADI")
            continue
        added = save_prices(conn, t, df, source)
        filled.append(t)
        print(f"  {t:<7} +{added} qator ({source})")
    hm_after = missing_pit_tickers(conn, hm)
    print(f"\nTo'ldirildi: {len(filled)}, topilmadi: {len(not_found)}, hali yetishmaydi: {len(hm_after)}")
    conn.close()
    return {"todo": list(todo), "filled": filled, "not_found": not_found}


if __name__ == "__main__":
    args = sys.argv[1:]
    csv_dir = args[args.index("--csv-dir") + 1] if "--csv-dir" in args else None
    run(csv_dir=csv_dir, report_only="--report" in args)
