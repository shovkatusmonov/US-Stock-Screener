"""
build_pit_membership.py - Haqiqiy tarixiy (point-in-time) S&P 500 a'zoligini qurish.

Manba: data/sp500_pit_snapshots.csv (fja05680/sp500, MIT; o'zgarish sanalari bo'yicha snapshotlar)
+ S&P DJI press-relizlari (snapshotdan keyingi, tasdiqlangan o'zgarishlar)
+ joriy universe.csv bilan farq (sanasi noma'lum -> "UNVERIFIED" deb belgilanadi).

Natija: sp500_historical_membership jadvali (version '2.0') va data/sp500_pit_membership.csv.
version 2.x mavjud bo'lsa SP500HistoryManager.complete=True: qoidasi yo'q ticker a'zo EMAS.

Ishga tushirish:  python build_pit_membership.py
"""
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

import pandas as pd

SNAPSHOTS_CSV = Path("data/sp500_pit_snapshots.csv")
UNIVERSE_CSV = Path("data/universe_sp500.csv")
OUT_CSV = Path("data/sp500_pit_membership.csv")
DB_PATH = "data/screener.db"
VERSION = "2.0"
SNAPSHOT_SOURCE = "fja05680/sp500"

# Nom o'zgarishlari (eski ticker -> hozirgi ticker; kompaniya bir xil, yfinance tarixi hozirgi tickerda).
# Tasdiqlash: bir kunda juft almashinuv (eski chiqadi, yangi kiradi) + narx darajasi mosligi.
ALIASES = {
    "FB": "META", "ANTM": "ELV", "ABC": "COR", "MMC": "MRSH", "PKI": "RVTY", "RE": "EG",
    "FI": "FISV", "BLL": "BALL", "WLTW": "WTW", "BHGE": "BKR", "PEAK": "DOC", "HCP": "DOC",
    "NLOK": "GEN", "SYMC": "GEN", "JEC": "J", "UTX": "RTX", "BBT": "TFC", "FLT": "CPAY",
    "ARNC": "HWM", "EQR": "VMRK", "BK": "BNY", "HRS": "LHX",
    # Paramount zanjiri (SKYD tarixi 2019 da ~$45 = CBS darajasi)
    "CBS": "SKYD", "VIAB": "SKYD", "VIAC": "SKYD", "PARA": "SKYD", "PSKY": "SKYD",
}

# Snapshotdan keyingi, tasdiqlangan o'zgarishlar (S&P DJI, 2026-09-04 e'loni, kuchga kirishi 2026-09-21)
PRESS_CHANGES = [
    {"date": "2026-09-21", "add": ["BE", "P", "ILMN"], "remove": ["TAP", "BLDR", "TTD"],
     "source": "SPDJI press release 2026-09-04"},
]

Row = Tuple[str, str, Optional[str], str]   # (ticker, start_date, end_date_inclusive|None, source)


def norm(t: str) -> str:
    return t.strip().replace(".", "-")


def _intervals(snap: pd.DataFrame) -> list:
    """[ticker, start, end_excl|None, source] - end_excl = a'zo bo'lmagan birinchi sana."""
    snap = snap.copy()
    snap["date"] = pd.to_datetime(snap["date"])
    snap = snap.sort_values("date")
    iv, prev = {}, set()
    for r in snap.itertuples():
        cur = {norm(t) for t in r.tickers.split(",")}
        for t in cur - prev:
            iv.setdefault(t, []).append([t, r.date, None, SNAPSHOT_SOURCE])
        for t in prev - cur:
            iv[t][-1][2] = r.date
        prev = cur
    return [x for lst in iv.values() for x in lst], snap["date"].max()


def _merge(rows: list, gap_days: int = 4) -> list:
    out = []
    for t in sorted({r[0] for r in rows}):
        cur = None
        for r in sorted((x for x in rows if x[0] == t), key=lambda x: x[1]):
            if cur is None:
                cur = list(r)
                continue
            if cur[2] is None or r[1] <= cur[2] + timedelta(days=gap_days):
                if cur[2] is not None:
                    cur[2] = None if r[2] is None else max(cur[2], r[2])
                if "UNVERIFIED" in str(r[3]):
                    cur[3] = r[3]
            else:
                out.append(cur)
                cur = list(r)
        out.append(cur)
    return out


def build_rows(snapshots: pd.DataFrame, universe: Optional[Set[str]] = None,
               press: Optional[List[dict]] = None) -> List[Row]:
    """Snapshotlardan (ticker, start, end, source) qatorlarini quradi. press=None -> PRESS_CHANGES."""
    press = PRESS_CHANGES if press is None else press
    iv, last_snap = _intervals(snapshots)

    for p in press:                                   # tasdiqlangan, snapshotdan keyingi
        pd_ = pd.Timestamp(p["date"])
        if pd_ <= last_snap:
            continue
        for t in p.get("remove", []):
            for x in iv:
                if x[0] == t and x[2] is None:
                    x[2], x[3] = pd_, p["source"]
        for t in p.get("add", []):
            iv.append([t, pd_, None, p["source"]])

    if universe:                                      # joriy ro'yxat bilan farq (UNVERIFIED)
        uni = {ALIASES.get(norm(t), norm(t)) for t in universe}
        active = {ALIASES.get(x[0], x[0]) for x in iv if x[2] is None}
        first_unknown = last_snap + timedelta(days=1)
        for t in sorted(uni - active):
            iv.append([t, first_unknown, None, "UNVERIFIED universe diff"])
        for t in sorted(active - uni):
            for x in iv:
                if ALIASES.get(x[0], x[0]) == t and x[2] is None:
                    x[2], x[3] = first_unknown, "UNVERIFIED universe diff"

    for x in iv:
        x[0] = ALIASES.get(x[0], x[0])
    merged = _merge(iv)
    rows = [(t, s.strftime("%Y-%m-%d"), None if e is None else (e - timedelta(days=1)).strftime("%Y-%m-%d"), src)
            for t, s, e, src in merged]
    return sorted(rows, key=lambda r: (r[0], r[1]))


def write_membership(db_path: str, rows: Iterable[Row]) -> None:
    """sp500_historical_membership jadvalini to'liq almashtiradi (version 2.0)."""
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS sp500_historical_membership (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, company_name TEXT,
        start_date TEXT NOT NULL, end_date TEXT, source TEXT, version TEXT, UNIQUE(ticker, start_date))""")
    conn.execute("DELETE FROM sp500_historical_membership")
    conn.executemany(
        "INSERT INTO sp500_historical_membership (ticker, company_name, start_date, end_date, source, version) "
        "VALUES (?, NULL, ?, ?, ?, ?)", [(t, s, e, src, VERSION) for t, s, e, src in rows])
    conn.commit()
    conn.close()


def main(db_path: str = DB_PATH):
    snaps = pd.read_csv(SNAPSHOTS_CSV)
    uni = set(pd.read_csv(UNIVERSE_CSV)["symbol"].astype(str)) if UNIVERSE_CSV.exists() else None
    rows = build_rows(snaps, universe=uni)
    pd.DataFrame(rows, columns=["ticker", "start_date", "end_date", "source"]).to_csv(OUT_CSV, index=False)
    write_membership(db_path, rows)
    unv = [r for r in rows if "UNVERIFIED" in r[3]]
    print(f"PIT a'zolik: {len({r[0] for r in rows})} ticker, {len(rows)} oraliq -> {OUT_CSV} va {db_path}")
    print(f"UNVERIFIED qatorlar ({len(unv)}):")
    for t, s, e, _ in unv:
        print(f"  {t}: {s} .. {e}")


if __name__ == "__main__":
    main(*(sys.argv[1:2] or []))
