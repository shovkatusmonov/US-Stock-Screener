"""
1-qadam: kunlik narx ma'lumotini yuklab, SQLite'ga saqlash.

Ishga tushirish (loyiha papkasida):
    .venv\\Scripts\\python download_data.py           (kichik sinov ro'yxati)
    .venv\\Scripts\\python download_data.py --sp500    (S&P 500, ~10 daqiqa)

Xususiyatlari:
- Takroriy ishga tushirishda faqat yangi kunlar yuklanadi (dublikat yozilmaydi).
- Har bir yozuvda manba va olingan vaqt saqlanadi.
- Oxirida oddiy sifat tekshiruvi chiqariladi.
- Birinchi sinov uchun kichik ro'yxat; muvaffaqiyatli bo'lsa kengaytiramiz.
"""
import io
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

DB_PATH = Path("data/screener.db")
START = "2019-01-01"
BENCHMARK = "SPY"
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "JPM", "V", "MA",
    "LLY", "UNH", "XOM", "COST", "HD", "PG", "JNJ", "ABBV", "NFLX", "CRM",
    "AMD", "ORCL", "CAT", "GE", "MCD", "TMO", "ADBE", "QCOM", "TXN", "AMAT",
]
SOURCE = "yfinance"
UNIVERSE_CSV = Path("data/universe_sp500.csv")

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, adj_close REAL, volume REAL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS fetch_log (
    run_at TEXT NOT NULL,
    ticker TEXT NOT NULL,
    status TEXT NOT NULL,
    rows_added INTEGER,
    message TEXT
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def last_date(conn, ticker):
    row = conn.execute("SELECT MAX(date) FROM prices WHERE ticker=?", (ticker,)).fetchone()
    return row[0]


def fetch(ticker: str, start: str) -> pd.DataFrame:
    """Yuklab oladi, lekin AQSH sharqiy vaqtidagi joriy kunning tugallanmagan barini olmaydi."""
    df = yf.Ticker(ticker).history(start=start, auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    idx = pd.DatetimeIndex(pd.to_datetime(df.index))
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    if "Adj Close" not in df.columns:
        df["Adj Close"] = df["Close"]
    else:
        df["Adj Close"] = df["Adj Close"].where(df["Adj Close"].notna(), df["Close"])
    df = df[["Open", "High", "Low", "Close", "Adj Close", "Volume"]]
    now_et = datetime.now(ZoneInfo("America/New_York"))
    # AQSH bozorining kunlik barini yopilishdan keyin kamida 30 daqiqa o'tgach qabul qilamiz.
    # Bozor yopilmagan bo'lsa, bugungi qisman barni saqlamaymiz.
    cutoff_date = now_et.date() + timedelta(days=1) if (
        now_et.hour > 16 or (now_et.hour == 16 and now_et.minute >= 30)
    ) else now_et.date()
    return df.loc[df.index.date < cutoff_date]


def valid_ohlc_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Noto'g'ri OHLCV qatorlarini savdo ma'lumotlaridan ajratadi."""
    if df.empty:
        return df, 0
    cols = ["Open", "High", "Low", "Close", "Volume"]
    numeric = df[cols].apply(pd.to_numeric, errors="coerce")
    finite = numeric.notna().all(axis=1)
    positive = (numeric[["Open", "High", "Low", "Close"]] > 0).all(axis=1)
    ordered = (
        (numeric["High"] >= numeric["Low"])
        & (numeric["High"] >= numeric["Open"])
        & (numeric["High"] >= numeric["Close"])
        & (numeric["Low"] <= numeric["Open"])
        & (numeric["Low"] <= numeric["Close"])
        & (numeric["Volume"] >= 0)
    )
    mask = finite & positive & ordered
    return df.loc[mask].copy(), int((~mask).sum())


def save(conn, ticker: str, df: pd.DataFrame) -> int:
    """Valid qatorlarni yozadi; overlap davridagi eski qatorlarni yangi ma'lumot bilan yangilaydi."""
    if df.empty:
        return 0
    df, invalid = valid_ohlc_rows(df)
    if df.empty:
        return 0
    fetched = now_iso()
    rows = [
        (ticker, d.strftime("%Y-%m-%d"), float(r["Open"]), float(r["High"]),
         float(r["Low"]), float(r["Close"]), float(r["Adj Close"]),
         float(r["Volume"]), SOURCE, fetched)
        for d, r in df.iterrows()
    ]
    keys = [row[1] for row in rows]
    existing = {
        r[0] for r in conn.execute(
            "SELECT date FROM prices WHERE ticker=? AND date BETWEEN ? AND ?",
            (ticker, min(keys), max(keys)),
        )
    }
    conn.executemany(
        """INSERT INTO prices
           (ticker,date,open,high,low,close,adj_close,volume,source,fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(ticker,date) DO UPDATE SET
             open=excluded.open, high=excluded.high, low=excluded.low,
             close=excluded.close, adj_close=excluded.adj_close,
             volume=excluded.volume, source=excluded.source, fetched_at=excluded.fetched_at""",
        rows,
    )
    # Eski caller'lar "added" kutganligi uchun faqat yangi kalitlar soni qaytariladi.
    return sum(1 for key in keys if key not in existing)


def update_ticker(conn, ticker: str) -> None:
    run_at = now_iso()
    try:
        ld = last_date(conn, ticker)
        if ld is None:
            start = START
        else:
            # So'nggi 7 kalendar kunni qayta olib, oldin yozilgan qisman/yangilangan barlarni tuzatish.
            overlap_start = datetime.strptime(ld, "%Y-%m-%d") - timedelta(days=7)
            start = max(datetime.strptime(START, "%Y-%m-%d"), overlap_start).strftime("%Y-%m-%d")
        df = fetch(ticker, start)
        raw_count = len(df)
        df, invalid = valid_ohlc_rows(df)
        if df.empty:
            status, added = ("WARN", 0) if invalid else (("OK", 0) if ld else ("FAIL", 0))
            msg = f"valid yangi/yangilanadigan bar yo'q; invalid={invalid}" if invalid else "yangi ma'lumot yo'q"
        else:
            added = save(conn, ticker, df)
            status = "WARN" if invalid else "OK"
            msg = f"{start} dan; yangi={added}, overlap yangilandi={len(df)-added}, invalid={invalid}"
    except Exception as e:  # noqa: BLE001 - manba xatosini qayd etamiz
        status, added, msg = "FAIL", 0, repr(e)[:300]
    conn.execute("INSERT INTO fetch_log VALUES (?,?,?,?,?)", (run_at, ticker, status, added, msg))
    conn.commit()
    print(f"{ticker:6s} {status:5s} +{added} {msg}")


def fetch_sp500() -> pd.DataFrame:
    """Wikipedia'dan hozirgi S&P 500 tarkibi (sektor bilan). DIQQAT: bu BUGUNGI tarkib (survivorship bias)."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (us_stock_screener research)"}, timeout=30)
    r.raise_for_status()
    t = pd.read_html(io.StringIO(r.text))[0]
    out = pd.DataFrame(
        {
            "symbol": t["Symbol"].astype(str).str.replace(".", "-", regex=False),
            "name": t["Security"],
            "sector": t["GICS Sector"],
        }
    )
    return out.drop_duplicates("symbol").reset_index(drop=True)


def quality_report(conn) -> None:
    print("\n=== SIFAT TEKSHIRUVI ===")
    df = pd.read_sql("SELECT * FROM prices", conn, parse_dates=["date"])
    if df.empty:
        print("Baza bo'sh.")
        return
    lines = []
    for t, g in df.groupby("ticker"):
        g = g.sort_values("date")
        issues = []
        if (g[["open", "high", "low", "close"]] <= 0).any().any():
            issues.append("nol/manfiy narx")
        if (g["high"] < g["low"]).any():
            issues.append("high<low")
        if g["close"].pct_change().abs().gt(0.4).any():
            issues.append("40%+ sakrash (split?)")
        if g["date"].diff().dt.days.gt(6).any():
            issues.append("6+ kunlik bo'shliq")
        stale = (pd.Timestamp.today().normalize() - g["date"].max()).days
        if stale > 5:
            issues.append(f"eskirgan ({stale} kun)")
        status = "WARNING: " + ", ".join(issues) if issues else "PASS"
        line = f"{t:6s} {len(g):5d} qator  {g['date'].min().date()} -> {g['date'].max().date()}  {status}"
        lines.append((issues, line))
    n_total = len(lines)
    n_warn = sum(1 for i, _ in lines if i)
    for issues, line in lines:
        if n_total <= 60 or issues:
            print(line)
    print(f"\nJami: {n_total} ticker, PASS: {n_total - n_warn}, WARNING: {n_warn}")


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tickers = UNIVERSE
    if "--sp500" in sys.argv:
        try:
            uni = fetch_sp500()
            uni.to_csv(UNIVERSE_CSV, index=False)
            tickers = uni["symbol"].tolist()
            print(f"S&P 500 ro'yxati olindi: {len(tickers)} ta ticker -> {UNIVERSE_CSV}")
        except Exception as e:  # noqa: BLE001
            if UNIVERSE_CSV.exists():
                tickers = pd.read_csv(UNIVERSE_CSV)["symbol"].tolist()
                print(f"Wikipedia xatosi ({e!r}); saqlangan ro'yxat ishlatiladi: {len(tickers)} ta")
            else:
                sys.exit(f"S&P 500 ro'yxatini olib bo'lmadi: {e!r}")
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    todo = [BENCHMARK] + [t for t in tickers if t != BENCHMARK]
    for k, t in enumerate(todo, 1):
        print(f"[{k}/{len(todo)}] ", end="")
        update_ticker(conn, t)
        time.sleep(0.2)
    quality_report(conn)
    conn.close()


if __name__ == "__main__":
    main()
