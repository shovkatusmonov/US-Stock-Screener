#!/usr/bin/env python3
"""Read-only export of daily prices from a SQLite database (no data is modified)."""
import argparse, csv, glob, os, sqlite3, sys
from collections import defaultdict
from datetime import datetime, timezone

EMBEDDED = ("AAPL,ABNB,ADBE,ADI,ALAB,ALB,AMAT,AMD,AME,AMWL,ANET,APPS,ASML,ASTS,AVTR,AXON,AZN,BE,BHP,BIDU,BKSY,BMY,BSKY,"
 "CAT,CEG,CHH,CI,CIFR,CLS,CLSK,CNS,CPRT,CRM,CRWV,CVNA,DAR,DAVE,DELL,DHR,DJT,DRI,DUOL,EAT,EEM,ENX,EOSE,FORM,FRGT,GETY,"
 "GLD,GLL,GM,GME,GOOG,GRAB,GREE,HOOD,HUBS,HUM,IBKR,IHG,INTC,INTL,IONQ,IREN,LAD,LLY,LRCX,LUV,LYFT,MAGS,MCD,MCHP,MDLZ,"
 "META,MRN,MRNA,MRVL,MSFT,MU,NCEW,NCLH,NCNO,NFLX,NVDA,NXE,ORMP,PLUG,PPLT,PRI,QBTS,QQQ,RACE,RCAT,REGN,RSI,RUN,RVTY,"
 "SGML,SMCI,SNDK,SOFI,SOXL,SPCX,SPIR,SPOT,SPY,SQQQ,STNG,TCOM,TEM,TGT,TOST,TRIP,TSLA,TSM,TTWO,TWLO,TWST,TXN,UNH,UPRO,"
 "URA,V,VALE,VLO,VSCO,WDC,XRAY,ZBH")

DATE_NAMES = ["date", "datetime", "timestamp", "trade_date", "dt", "time"]
TICKER_NAMES = ["ticker", "symbol", "sym", "code"]
FIELDS = {
    "open": ["open", "o"], "high": ["high", "h"], "low": ["low", "l"],
    "close": ["close", "c", "px_close"],
    "adj_close": ["adj_close", "adjclose", "adjusted_close", "close_adj", "adj close"],
    "volume": ["volume", "vol", "v"],
}
START = "2025-01-01"


def pick(cols, names):
    low = {c.lower(): c for c in cols}
    for n in names:
        if n in low:
            return low[n]
    return None


def inspect(con):
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'")]
    info = {}
    with open("schema.txt", "w", encoding="utf-8") as f:
        for t in tables:
            cols = [r[1] for r in con.execute('PRAGMA table_info("%s")' % t)]
            try:
                n = con.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            except Exception:
                n = "?"
            info[t] = cols
            f.write("%s  rows=%s\n  columns: %s\n\n" % (t, n, ", ".join(cols)))
    print("schema.txt written (%d tables)" % len(tables))
    return info


def candidates(info):
    out = []
    for t, cols in info.items():
        m = {k: pick(cols, v) for k, v in FIELDS.items()}
        d, tk = pick(cols, DATE_NAMES), pick(cols, TICKER_NAMES)
        if m["close"] and d and tk and m["open"] and m["high"] and m["low"]:
            out.append((t, d, tk, m))
    return out


def norm_date(v):
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        s = v / 1000 if v > 1e11 else v
        return datetime.fromtimestamp(s, tz=timezone.utc).strftime("%Y-%m-%d")
    return str(v)[:10]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join("data", "screener.db"))
    ap.add_argument("--table")
    ap.add_argument("--tickers", default="tickers_needed.csv")
    a = ap.parse_args()

    if not os.path.exists(a.db):
        sys.exit("Database not found: " + a.db)
    con = sqlite3.connect("file:%s?mode=ro" % a.db.replace("\\", "/"), uri=True)  # read-only
    info = inspect(con)
    cands = candidates(info)
    if a.table:
        cands = [c for c in cands if c[0] == a.table]
    if not cands:
        sys.exit("Price table not found automatically. Send schema.txt.")
    if len(cands) > 1:
        print("Several candidate tables:", [c[0] for c in cands], "-> using the first (override with --table)")
    table, dcol, tcol, m = cands[0]
    print("Price table: %s (date=%s, ticker=%s)" % (table, dcol, tcol))

    if os.path.exists(a.tickers):
        with open(a.tickers, encoding="utf-8-sig") as f:
            wanted = {r["ticker"].strip().upper() for r in csv.DictReader(f)}
    else:
        wanted = set(EMBEDDED.split(","))
    print("Tickers requested:", len(wanted))

    sel = [dcol, tcol] + [m[k] for k in FIELDS if m[k]]
    names = ["date", "ticker"] + [k for k in FIELDS if m[k]]
    q = "SELECT " + ", ".join('"%s"' % c for c in sel) + ' FROM "%s"' % table
    seen = defaultdict(int)
    stats = {}
    n_out = 0
    with open("prices_export.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(names)
        for row in con.execute(q):
            d = norm_date(row[0])
            t = str(row[1]).strip().upper()
            if t not in wanted:
                continue
            if d and d < START:
                continue
            seen[(t, d)] += 1
            s = stats.setdefault(t, {"rows": 0, "min": d, "max": d, "nulls": 0, "bad_ohlc": 0, "zero_vol": 0})
            s["rows"] += 1
            s["min"] = min(s["min"], d)
            s["max"] = max(s["max"], d)
            vals = list(row[2:])
            o, h, l, c = (vals[names.index(k) - 2] if k in names else None for k in ("open", "high", "low", "close"))
            if any(x is None for x in (o, h, l, c)):
                s["nulls"] += 1
            else:
                try:
                    if not (l <= min(o, c) and h >= max(o, c)):
                        s["bad_ohlc"] += 1
                except TypeError:
                    s["nulls"] += 1
            if "volume" in names and (vals[names.index("volume") - 2] in (0, None)):
                s["zero_vol"] += 1
            w.writerow([d, t] + vals)
            n_out += 1
    dups = defaultdict(int)
    for (t, d), c in seen.items():
        if c > 1:
            dups[t] += c - 1

    with open("coverage_report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "rows", "first_date", "last_date", "duplicate_rows", "null_rows",
                    "ohlc_inconsistent", "zero_volume_rows", "in_database"])
        for t in sorted(wanted | set(stats)):
            s = stats.get(t)
            if s:
                w.writerow([t, s["rows"], s["min"], s["max"], dups.get(t, 0), s["nulls"], s["bad_ohlc"], s["zero_vol"], "yes"])
            else:
                w.writerow([t, 0, "", "", "", "", "", "", "NO"])
    missing = sorted(t for t in wanted if t not in stats)
    print("prices_export.csv: %d rows, %d tickers" % (n_out, len(stats)))
    print("Not in database: %d -> %s" % (len(missing), missing[:40]))
    print("Price columns:", {k: v for k, v in m.items() if v})
    print("Done: schema.txt, prices_export.csv, coverage_report.csv")


if __name__ == "__main__":
    main()
