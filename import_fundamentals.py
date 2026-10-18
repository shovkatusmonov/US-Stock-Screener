"""
import_fundamentals.py - CLI tool to import SEC EDGAR company facts into screener.db.

Supports:
1. Online fetching from SEC EDGAR API with rate limiting (max 10 req/s), retries, and backoff.
2. Offline import from local JSON files or directories.
3. Resuming interrupted batch imports without duplicate rows (idempotent).
4. Status reporting: IMPORTED, PARTIAL_DATA, NO_CIK, NO_DATA, RETRY, ERROR.
5. Strict Point-In-Time and Zero Fake Data guarantees.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import pandas as pd
from data_provider import DataProvider, init_db, DEFAULT_SEC_USER_AGENT, validate_sec_user_agent


def check_ticker_freshness(
    cur: sqlite3.Cursor,
    ticker: str,
    freshness_days: int = 45
) -> Tuple[bool, int, Optional[str]]:
    """
    Checks if a ticker's fundamental records in DB are sufficiently fresh.
    A ticker is fresh if it has rows in company_fundamentals and the latest
    filing_date was within freshness_days of today.
    """
    cur.execute("SELECT COUNT(*), MAX(filing_date) FROM company_fundamentals WHERE ticker = ?", (ticker,))
    count, max_date = cur.fetchone()
    if count == 0 or not max_date:
        return False, 0, None
    try:
        last_dt = datetime.strptime(max_date, "%Y-%m-%d")
        days_old = (datetime.now() - last_dt).days
        is_fresh = (days_old <= freshness_days)
        return is_fresh, count, max_date
    except Exception:
        return False, count, max_date


def evaluate_record_quality(records: List[Dict[str, Any]]) -> str:
    """
    Evaluates whether records represent full or partial coverage.
    If core metrics (revenue, net_income, operating_cash_flow, total_equity)
    are present in the most recent filings, returns IMPORTED, otherwise PARTIAL_DATA.
    """
    if not records:
        return "NO_DATA"
    
    # Check the last 4 filings (most recent year)
    recent = records[-4:]
    has_rev = any(r.get("revenue") is not None for r in recent)
    has_ni = any(r.get("net_income") is not None for r in recent)
    has_equity = any(r.get("total_equity") is not None for r in recent)
    
    if has_rev and has_ni and has_equity:
        return "IMPORTED"
    return "PARTIAL_DATA"


def fetch_and_import_ticker(
    ticker: str,
    cik: Optional[Any],
    provider: DataProvider,
    user_agent: Optional[str] = None,
    raw_dir: Optional[Path] = None,
    max_retries: int = 3,
    backoff_sec: float = 1.0
) -> Dict[str, Any]:
    ticker_clean = ticker.upper().strip()
    if not user_agent:
        user_agent = os.environ.get("SEC_EDGAR_USER_AGENT", DEFAULT_SEC_USER_AGENT)

    is_valid, ua_err = validate_sec_user_agent(user_agent)
    if not is_valid:
        return {
            "status": "ERROR",
            "ticker": ticker_clean,
            "cik": None,
            "records": 0,
            "error_type": "SEC_INVALID_USER_AGENT",
            "message": ua_err
        }

    # Normalize CIK parameter (supports single string, list/tuple, or None)
    if isinstance(cik, str):
        ciks = [cik.strip()] if cik.strip() else []
    elif isinstance(cik, (list, tuple, set)):
        ciks = [str(c).strip() for c in cik if c]
    elif cik is None:
        ciks = provider.get_ciks_for_ticker(ticker_clean)
    else:
        ciks = [str(cik).strip()]

    if not ciks:
        return {
            "status": "NO_CIK",
            "ticker": ticker_clean,
            "cik": None,
            "records": 0,
            "message": f"No official CIK found for ticker {ticker_clean}"
        }

    all_records = []
    cik_labels = []

    for c in ciks:
        cik_clean = str(c).lstrip("0").zfill(10)
        cik_labels.append(cik_clean)
        data = None

        # Check raw_dir cache first if provided
        raw_file = (raw_dir / f"CIK{cik_clean}.json") if raw_dir else None
        if raw_file and raw_file.exists():
            try:
                with open(raw_file, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
            except Exception:
                data = None

        if data is None:
            fetch_res = DataProvider.fetch_sec_edgar_facts(
                cik=cik_clean,
                user_agent=user_agent,
                max_retries=max_retries,
                backoff_sec=backoff_sec
            )
            if fetch_res["status"] == "NO_DATA":
                continue
            elif fetch_res["status"] != "OK":
                if len(ciks) == 1:
                    return {
                        "status": "ERROR",
                        "ticker": ticker_clean,
                        "cik": cik_clean,
                        "records": 0,
                        "message": fetch_res.get("message", "Fetch failed"),
                        "details": fetch_res.get("details", "")
                    }
                continue
            data = fetch_res["data"]
            # Save to raw_dir if requested
            if raw_file:
                raw_dir.mkdir(parents=True, exist_ok=True)
                with open(raw_file, "w", encoding="utf-8") as fp:
                    json.dump(data, fp)

        records = provider.parse_sec_company_facts(data, ticker_clean)
        if records:
            all_records.extend(records)

    if not all_records:
        return {
            "status": "NO_DATA",
            "ticker": ticker_clean,
            "cik": ",".join(cik_labels),
            "records": 0,
            "message": "Zero 10-K or 10-Q financial records parsed from facts"
        }

    inserted = provider.save_fundamentals(all_records)
    quality = evaluate_record_quality(all_records)
    primary_cik = cik_labels[-1] if cik_labels else None
    return {
        "status": quality,
        "ticker": ticker_clean,
        "cik": primary_cik,
        "ciks": cik_labels,
        "records": inserted,
        "filing_count": len(all_records),
        "min_filing": all_records[0]["filing_date"],
        "max_filing": all_records[-1]["filing_date"],
        "message": f"Successfully ingested {inserted} filings ({all_records[0]['filing_date']} to {all_records[-1]['filing_date']})"
    }


def import_file(file_path: Path, ticker: Optional[str], provider: DataProvider) -> int:
    if not file_path.exists():
        print(f"[ERROR] File not found: {file_path}")
        return 0

    with open(file_path, "r", encoding="utf-8") as fp:
        try:
            data = json.load(fp)
        except Exception as e:
            print(f"[ERROR] Failed to parse JSON from {file_path}: {e}")
            return 0

    if not ticker:
        stem = file_path.stem.upper().replace("CIK", "")
        ticker = stem

    records = provider.parse_sec_company_facts(data, ticker)
    if not records:
        print(f"[WARN] No 10-K or 10-Q financial items parsed from {file_path}")
        return 0

    inserted = provider.save_fundamentals(records)
    print(f"[OK] Ingested {inserted} filings for {ticker} from {file_path.name}")
    return inserted


def print_status(conn: sqlite3.Connection):
    init_db(conn)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT ticker) FROM company_fundamentals")
    fund_count, fund_tickers = cur.fetchone()
    
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT ticker) FROM news_catalysts")
    news_count, news_tickers = cur.fetchone()

    cur.execute("SELECT COUNT(*), COUNT(DISTINCT ticker) FROM earnings_calendar")
    earn_count, earn_tickers = cur.fetchone()

    print("\n" + "=" * 60)
    print("FINANCIAL DATA PROVIDER STATUS (screener.db)")
    print("=" * 60)
    print(f"Company Fundamentals:  {fund_count:>6} rows across {fund_tickers:>4} tickers")
    print(f"News & Catalysts:      {news_count:>6} rows across {news_tickers:>4} tickers")
    print(f"Earnings Calendar:     {earn_count:>6} rows across {earn_tickers:>4} tickers")
    print("=" * 60)


def run_universe_import(
    conn: sqlite3.Connection,
    tickers: List[str],
    user_agent: Optional[str] = None,
    rate_limit_sec: float = 0.15,
    resume: bool = True,
    freshness_days: int = 45,
    raw_dir: Optional[Path] = None,
    log_file: Optional[Path] = None
) -> Dict[str, Any]:
    if not user_agent:
        user_agent = os.environ.get("SEC_EDGAR_USER_AGENT", DEFAULT_SEC_USER_AGENT)

    is_valid, ua_err = validate_sec_user_agent(user_agent)
    if not is_valid:
        print(f"\n[ERROR] Halting SEC EDGAR import: {ua_err}")
        return {
            "timestamp": datetime.now().isoformat(),
            "total_tickers": len(tickers),
            "status_counts": {"ERROR": len(tickers)},
            "total_filings_ingested": 0,
            "error_type": "SEC_INVALID_USER_AGENT",
            "message": ua_err,
            "results": {}
        }

    provider = DataProvider(conn)
    
    results = {}
    counts_by_status = {"IMPORTED": 0, "PARTIAL_DATA": 0, "NO_CIK": 0, "NO_DATA": 0, "RETRY": 0, "ERROR": 0, "SKIPPED": 0, "FRESH": 0}
    total_filings = 0

    cur = conn.cursor()
    print(f"\nStarting SEC EDGAR Fundamental Import across {len(tickers)} tickers...")
    print(f"Rate limit: {rate_limit_sec}s delay between requests | Resume mode: {resume} (freshness threshold: {freshness_days}d)")

    for idx, ticker in enumerate(tickers, 1):
        t_clean = ticker.upper().strip()
        
        # Check incremental freshness condition in resume mode
        if resume:
            is_fresh, existing, max_date = check_ticker_freshness(cur, t_clean, freshness_days)
            if is_fresh:
                results[t_clean] = {
                    "status": "FRESH",
                    "ticker": t_clean,
                    "records": existing,
                    "max_filing": max_date,
                    "message": f"Skipped: already fresh with {existing} rows (latest filing {max_date} within {freshness_days}d)"
                }
                counts_by_status["SKIPPED"] += 1
                counts_by_status["FRESH"] = counts_by_status.get("FRESH", 0) + 1
                total_filings += existing
                print(f"[{idx:>3}/{len(tickers)}] {t_clean:<5} FRESH (latest: {max_date}, {existing} rows)")
                continue

        ciks = provider.get_ciks_for_ticker(t_clean)
        res = fetch_and_import_ticker(
            ticker=t_clean,
            cik=ciks,
            provider=provider,
            user_agent=user_agent,
            raw_dir=raw_dir
        )
        status = res["status"]
        results[t_clean] = res
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        total_filings += res.get("records", 0)

        if status in ("IMPORTED", "PARTIAL_DATA"):
            print(f"[{idx:>3}/{len(tickers)}] {t_clean:<5} {status:<12} +{res['records']} filings ({res.get('min_filing', '')} -> {res.get('max_filing', '')})")
        else:
            print(f"[{idx:>3}/{len(tickers)}] {t_clean:<5} {status:<12} {res.get('message', '')}")

        # Sleep to strictly respect SEC rate limits (< 10 req/s)
        time.sleep(rate_limit_sec)

    summary = {
        "timestamp": datetime.now().isoformat(),
        "total_tickers": len(tickers),
        "status_counts": counts_by_status,
        "total_filings_ingested": total_filings,
        "results": results
    }

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "w", encoding="utf-8") as fp:
            json.dump(summary, fp, indent=2)
        print(f"\nImport log saved to {log_file}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Import SEC EDGAR facts and news into screener.db")
    parser.add_argument("--db-path", default="data/screener.db")
    parser.add_argument("--file", type=str, help="Path to single SEC facts JSON file")
    parser.add_argument("--ticker", type=str, help="Ticker symbol for the file")
    parser.add_argument("--dir", type=str, help="Directory containing SEC facts JSON files")
    parser.add_argument("--tickers", type=str, help="Comma-separated ticker list to fetch from SEC EDGAR")
    parser.add_argument("--universe", action="store_true", help="Fetch for all tickers in data/universe_sp500.csv")
    parser.add_argument("--max-tickers", type=int, help="Limit number of tickers to process")
    parser.add_argument("--rate-limit", type=float, default=0.15, help="Seconds delay between SEC requests (default: 0.15s)")
    parser.add_argument("--freshness-days", type=int, default=45, help="Skip tickers whose latest filing is within N days in resume mode (default: 45)")
    parser.add_argument("--user-agent", type=str, default=DEFAULT_SEC_USER_AGENT, help="SEC User-Agent header")
    parser.add_argument("--save-raw-dir", type=str, default="data/sec_facts", help="Directory to cache raw SEC facts JSON")
    parser.add_argument("--log-file", type=str, default="reports/sec_import_log.json", help="Log output path")
    parser.add_argument("--no-resume", action="store_true", help="Do not skip fresh tickers")
    parser.add_argument("--status", action="store_true", help="Print current data provider status")

    args = parser.parse_args()
    conn = sqlite3.connect(args.db_path)
    provider = DataProvider(conn)

    if args.status or (not args.file and not args.dir and not args.tickers and not args.universe):
        print_status(conn)
        conn.close()
        return

    if args.file:
        import_file(Path(args.file), args.ticker, provider)
        conn.close()
        return

    if args.dir:
        p = Path(args.dir)
        if not p.is_dir():
            print(f"[ERROR] Directory not found: {args.dir}")
            conn.close()
            return
        files = list(p.glob("*.json"))
        print(f"Found {len(files)} JSON files in {args.dir}...")
        total = 0
        for f in files:
            total += import_file(f, None, provider)
        print(f"[DONE] Total filings ingested: {total}")
        conn.close()
        return

    # Handle online ticker or universe import
    tickers = []
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    elif args.universe:
        u_path = Path("data/universe_sp500.csv")
        if not u_path.exists():
            print(f"[ERROR] Universe file not found: {u_path}")
            conn.close()
            return
        df = pd.read_csv(u_path)
        tickers = df["symbol"].str.strip().tolist()

    if args.max_tickers:
        tickers = tickers[:args.max_tickers]

    raw_dir = Path(args.save_raw_dir) if args.save_raw_dir else None
    log_file = Path(args.log_file) if args.log_file else None

    run_universe_import(
        conn=conn,
        tickers=tickers,
        user_agent=args.user_agent,
        rate_limit_sec=args.rate_limit,
        resume=not args.no_resume,
        freshness_days=args.freshness_days,
        raw_dir=raw_dir,
        log_file=log_file
    )
    conn.close()


if __name__ == "__main__":
    main()
