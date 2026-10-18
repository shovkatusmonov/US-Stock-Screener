"""
data_provider.py - Real Data Provider Architecture for Fundamentals, News & Events.

Provides point-in-time financial data and corporate events for the 6-score quantitative engine.
Strictly enforces:
1. Point-In-Time (PIT) integrity: filing_date <= as_of_date (zero lookahead bias).
2. Zero fake data policy: returns None / DATA_UNAVAILABLE when data is not in database.
3. Graceful offline error handling: catches network failures ([Errno 11001]) and documents blockers.
4. Robust parsing of SEC EDGAR XBRL company facts (10-K / 10-Q).
5. Negative equity handling: captures balance sheet insolvency risk.
"""

import json
import os
import sqlite3
import urllib.request
import urllib.error
import socket
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import numpy as np
import pandas as pd


# SEC EDGAR US-GAAP Concept Mappings
CONCEPT_MAPPINGS = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueServicesNet",
        "RevenuesNetOfInterestExpense",
        "InterestAndDividendIncomeOperating"
    ],
    "operating_income": [
        "OperatingIncomeLoss"
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss"
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities"
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets"
    ],
    "total_assets": [
        "Assets"
    ],
    "total_liabilities": [
        "Liabilities"
    ],
    "total_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
    ],
    "long_term_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebt"
    ],
    "short_term_debt": [
        "DebtCurrent",
        "ShortTermBorrowings"
    ],
    "current_assets": [
        "AssetsCurrent"
    ],
    "current_liabilities": [
        "LiabilitiesCurrent"
    ],
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic"
    ],
    "cost_of_revenue": [
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue"
    ]
}

# Categorization for Point-in-Time duration and aggregation rules
FLOW_METRICS = {
    "revenue", "cost_of_revenue", "operating_income", "net_income",
    "operating_cash_flow", "capex"
}
INSTANT_METRICS = {
    "total_assets", "total_liabilities", "total_equity",
    "long_term_debt", "short_term_debt", "current_assets",
    "current_liabilities", "shares_outstanding"
}

# Corporate Succession & Multi-CIK Registry
# Maps ticker to list of historical / current corporate entity CIKs
CIK_SUCCESSION_REGISTRY: Dict[str, List[Dict[str, str]]] = {
    "XOM": [
        {"cik": "0000034088", "role": "predecessor", "name": "Exxon Mobil Corp (Operating Predecessor)"},
        {"cik": "0002115436", "role": "successor", "name": "ExxonMobil Holdings Corp (Holding Successor)"}
    ]
}

DISALLOWED_UA_DOMAINS = {
    "example.com", "example.org", "example.net",
    "sample.com", "sample.org", "sample.net",
    "test.com", "test.org", "test.net",
    "placeholder.com", "placeholder.org", "placeholder.net",
    "fake.com", "fake.org", "fake.net",
    "dummy.com", "none.com",
    "domain.com", "mycompany.com", "company.com"
}


def validate_sec_user_agent(user_agent: Optional[str]) -> Tuple[bool, str]:
    """
    Validates that a User-Agent string complies with SEC EDGAR Fair Access requirements:
    1. Must not be empty, None, or whitespace only.
    2. Must contain contact information (email address or organizational URL).
    3. Rejects placeholder domains (example.com, sample.com, test.com, etc.).
    4. Rejects generic dummy/fake contacts.
    Returns (True, "Valid SEC User-Agent") or (False, "Descriptive error message").
    """
    if not user_agent or not isinstance(user_agent, str) or not user_agent.strip():
        return False, "SEC User-Agent is empty or not configured. SEC Fair Access requires a declared organizational contact."

    ua = user_agent.strip()
    ua_lower = ua.lower()

    for placeholder in ["placeholder", "dummy", "fake"]:
        if placeholder in ua_lower:
            return False, f"SEC User-Agent contains placeholder term '{placeholder}'. A real organizational contact is required per SEC Fair Access policy."

    import re
    email_pattern = r"[a-zA-Z0-9_.+-]+@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)"
    emails = re.findall(email_pattern, ua)

    url_pattern = r"https?://([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)"
    urls = re.findall(url_pattern, ua)

    if not emails and not urls:
        return False, "SEC User-Agent must include an administrative contact email (e.g. 'AppName/1.0 (contact@firm.com)') or organizational domain URL per SEC Fair Access policy."

    for domain in emails:
        d_clean = domain.lower().strip(".")
        if d_clean in DISALLOWED_UA_DOMAINS or any(d_clean.endswith("." + dis) for dis in DISALLOWED_UA_DOMAINS):
            return False, f"SEC User-Agent contains disallowed placeholder email domain '{domain}'. Please provide a genuine organizational contact."

    for domain in urls:
        d_clean = domain.lower().strip(".")
        if d_clean in DISALLOWED_UA_DOMAINS or any(d_clean.endswith("." + dis) for dis in DISALLOWED_UA_DOMAINS):
            return False, f"SEC User-Agent contains disallowed placeholder URL domain '{domain}'. Please provide a genuine organizational contact."

    return True, "Valid SEC User-Agent"


DEFAULT_SEC_USER_AGENT = os.environ.get("SEC_EDGAR_USER_AGENT", "")


def init_db(conn: sqlite3.Connection):
    """Initializes tables for company fundamentals, news/catalysts, and earnings calendar."""
    cur = conn.cursor()
    
    # 1. Company Fundamentals (Point-in-Time SEC Filings)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS company_fundamentals (
            ticker TEXT NOT NULL,
            cik TEXT,
            filing_date TEXT NOT NULL,
            period_end_date TEXT NOT NULL,
            form TEXT NOT NULL,
            fiscal_year INTEGER,
            fiscal_period TEXT,
            revenue REAL,
            cost_of_revenue REAL,
            operating_income REAL,
            net_income REAL,
            operating_cash_flow REAL,
            capex REAL,
            free_cash_flow REAL,
            total_assets REAL,
            total_liabilities REAL,
            total_equity REAL,
            total_debt REAL,
            current_assets REAL,
            current_liabilities REAL,
            shares_outstanding REAL,
            gross_margin REAL,
            operating_margin REAL,
            roe REAL,
            debt_to_equity REAL,
            current_ratio REAL,
            source TEXT DEFAULT 'SEC_EDGAR_PIT',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker, filing_date, period_end_date, form)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_fund_ticker_filing ON company_fundamentals(ticker, filing_date)")

    # 2. News / Catalysts
    cur.execute("""
        CREATE TABLE IF NOT EXISTS news_catalysts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            published_at TEXT NOT NULL,
            headline TEXT NOT NULL,
            summary TEXT,
            event_type TEXT NOT NULL,
            sentiment_score REAL DEFAULT 0.0,
            impact_score REAL DEFAULT 0.5,
            source TEXT DEFAULT 'NEWS_API',
            url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ticker, published_at, headline)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_news_ticker_pub ON news_catalysts(ticker, published_at)")

    # 3. Earnings Calendar
    cur.execute("""
        CREATE TABLE IF NOT EXISTS earnings_calendar (
            ticker TEXT NOT NULL,
            earnings_date TEXT NOT NULL,
            time_of_day TEXT DEFAULT 'AMC',
            confirmed INTEGER DEFAULT 1,
            eps_estimate REAL,
            eps_actual REAL,
            rev_estimate REAL,
            rev_actual REAL,
            source TEXT DEFAULT 'CALENDAR_FEED',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker, earnings_date)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_earnings_ticker_date ON earnings_calendar(ticker, earnings_date)")

    conn.commit()


class DataProvider:
    """
    Financial Data Provider with strict Point-In-Time and zero-fabrication guarantees.
    """
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        init_db(self.conn)

    # -------------------------------------------------------------------------
    # POINT-IN-TIME FUNDAMENTALS QUERY
    # -------------------------------------------------------------------------
    def get_point_in_time_fundamentals(
        self,
        ticker: str,
        as_of_date: str,
        current_price: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves the most recent SEC filing available strictly on or before as_of_date.
        Zero Lookahead Bias: filing_date <= as_of_date.
        If no filing exists, returns None (no fake data).
        """
        query = """
            SELECT 
                ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period,
                revenue, operating_income, net_income, operating_cash_flow, capex, free_cash_flow,
                total_assets, total_liabilities, total_equity, total_debt, current_assets,
                current_liabilities, shares_outstanding, gross_margin, operating_margin,
                roe, debt_to_equity, current_ratio, source
            FROM company_fundamentals
            WHERE ticker = ? AND filing_date <= ? AND (revenue IS NOT NULL OR total_equity IS NOT NULL OR net_income IS NOT NULL)
            ORDER BY filing_date DESC, period_end_date DESC
            LIMIT 1
        """
        cur = self.conn.cursor()
        cur.execute(query, (ticker.upper(), as_of_date))
        row = cur.fetchone()
        if not row:
            return None

        # Build dict
        cols = [
            "ticker", "cik", "filing_date", "period_end_date", "form", "fiscal_year", "fiscal_period",
            "revenue", "operating_income", "net_income", "operating_cash_flow", "capex", "free_cash_flow",
            "total_assets", "total_liabilities", "total_equity", "total_debt", "current_assets",
            "current_liabilities", "shares_outstanding", "gross_margin", "operating_margin",
            "roe", "debt_to_equity", "current_ratio", "source"
        ]
        data = dict(zip(cols, row))

        # Calculate YoY Revenue Growth and EPS Growth from previous year filing if available
        fy = data["fiscal_year"]
        fp = data["fiscal_period"]
        form = data["form"]
        cur_cik = data.get("cik")
        rev_growth_yoy = None
        eps_growth_yoy = None

        if fy is not None and fp is not None and form is not None:
            # Determine compatible forms for the fiscal period
            # Form 10-K and 10-K/A for annual reports; Form 10-Q and 10-Q/A for quarterly reports
            if fp == "FY":
                form_clause = "form IN ('10-K', '10-K/A')"
            else:
                form_clause = "form IN ('10-Q', '10-Q/A')"

            # Base query matching fiscal period, form family, and prior fiscal year (fy - 1)
            # Enforce Point-In-Time guarantee: filing_date <= as_of_date
            # Segregate corporate succession / CIKs: prevent cross-entity comparison
            prev_base_query = f"""
                SELECT revenue, net_income, shares_outstanding, cik
                FROM company_fundamentals
                WHERE ticker = ? AND filing_date <= ? AND {form_clause} AND fiscal_period = ? AND fiscal_year = ?
            """
            params = [ticker.upper(), as_of_date, fp, fy - 1]
            if cur_cik:
                prev_base_query += " AND (cik = ? OR cik IS NULL)"
                params.append(cur_cik)

            # 1. Fetch latest prior filing with non-null revenue or net_income
            combined_query = prev_base_query + " AND (revenue IS NOT NULL OR net_income IS NOT NULL) ORDER BY filing_date DESC, period_end_date DESC LIMIT 1"
            cur.execute(combined_query, params)
            prev_row = cur.fetchone()

            prev_rev = prev_row[0] if prev_row else None
            prev_ni = prev_row[1] if prev_row else None
            prev_shares = prev_row[2] if prev_row else None
            prev_cik = prev_row[3] if prev_row else None

            # 2. If combined row had NULL revenue (e.g. specialized balance sheet amendment), find latest prior filing with non-null revenue
            if prev_rev is None and data.get("revenue") is not None:
                rev_query = prev_base_query + " AND revenue IS NOT NULL ORDER BY filing_date DESC, period_end_date DESC LIMIT 1"
                cur.execute(rev_query, params)
                rev_row = cur.fetchone()
                if rev_row:
                    prev_rev = rev_row[0]
                    if not prev_cik:
                        prev_cik = rev_row[3]

            # 3. If combined row had NULL net_income, find latest prior filing with non-null net_income
            if prev_ni is None and data.get("net_income") is not None:
                ni_query = prev_base_query + " AND net_income IS NOT NULL ORDER BY filing_date DESC, period_end_date DESC LIMIT 1"
                cur.execute(ni_query, params)
                ni_row = cur.fetchone()
                if ni_row:
                    prev_ni = ni_row[1]
                    if prev_shares is None or prev_shares <= 0:
                        prev_shares = ni_row[2]
                    if not prev_cik:
                        prev_cik = ni_row[3]

            # Entity succession check: if CIKs differ, suppress automated cross-CIK comparison
            if cur_cik and prev_cik and cur_cik != prev_cik:
                rev_growth_yoy = None
                eps_growth_yoy = None
            else:
                if prev_rev is not None and prev_rev > 0 and data.get("revenue") is not None:
                    rev_growth_yoy = (data["revenue"] - prev_rev) / prev_rev

                if (prev_ni is not None and prev_shares is not None and prev_shares > 0 and
                    data.get("net_income") is not None and data.get("shares_outstanding") is not None and data["shares_outstanding"] > 0):
                    cur_eps = data["net_income"] / data["shares_outstanding"]
                    prev_eps = prev_ni / prev_shares
                    if prev_eps > 0:
                        eps_growth_yoy = (cur_eps - prev_eps) / prev_eps

        data["revenue_growth_yoy"] = rev_growth_yoy
        data["eps_growth_yoy"] = eps_growth_yoy

        # Valuation multiples if current_price and shares are present
        pe_ratio = None
        ps_ratio = None
        if current_price is not None and current_price > 0:
            if data["shares_outstanding"] and data["shares_outstanding"] > 0:
                market_cap = current_price * data["shares_outstanding"]
                if data["net_income"] is not None:
                    if data["net_income"] > 0:
                        pe_ratio = market_cap / data["net_income"]
                    else:
                        pe_ratio = -1.0  # Unprofitable
                if data["revenue"] is not None and data["revenue"] > 0:
                    ps_ratio = market_cap / data["revenue"]

        data["pe_ratio"] = pe_ratio
        data["ps_ratio"] = ps_ratio

        return data

    # -------------------------------------------------------------------------
    # RECENT NEWS AND CATALYSTS QUERY
    # -------------------------------------------------------------------------
    def get_recent_news_and_events(
        self,
        ticker: str,
        as_of_date: str,
        lookback_days: int = 30
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves verified news events and upcoming earnings for ticker strictly up to as_of_date.
        Zero Lookahead Bias: published_at <= as_of_date.
        """
        cur = self.conn.cursor()
        as_of_dt = pd.to_datetime(as_of_date).date()
        lookback_dt = as_of_dt - pd.Timedelta(days=lookback_days)

        # Query news
        news_query = """
            SELECT published_at, headline, summary, event_type, sentiment_score, impact_score, source
            FROM news_catalysts
            WHERE ticker = ? AND published_at <= ? AND published_at >= ?
            ORDER BY published_at DESC
        """
        cur.execute(news_query, (ticker.upper(), str(as_of_dt) + " 23:59:59", str(lookback_dt)))
        news_rows = cur.fetchall()

        events = []
        for r in news_rows:
            events.append({
                "published_at": r[0],
                "headline": r[1],
                "summary": r[2],
                "event_type": r[3],
                "sentiment_score": r[4],
                "impact_score": r[5],
                "source": r[6]
            })

        # Query upcoming earnings
        earn_query = """
            SELECT earnings_date
            FROM earnings_calendar
            WHERE ticker = ? AND earnings_date >= ?
            ORDER BY earnings_date ASC
            LIMIT 1
        """
        cur.execute(earn_query, (ticker.upper(), str(as_of_dt)))
        earn_row = cur.fetchone()
        upcoming_earnings = earn_row[0] if earn_row else None

        if not events and not upcoming_earnings:
            return None

        return {
            "events": events,
            "upcoming_earnings_date": upcoming_earnings
        }

    # -------------------------------------------------------------------------
    # SEC EDGAR COMPANY FACTS FETCHER (ONLINE WITH GRACEFUL OFFLINE ERROR HANDLING)
    # -------------------------------------------------------------------------
    @staticmethod
    def get_sec_ticker_to_cik_map(
        cache_path: Optional[Path] = Path("data/sec_company_tickers.json"),
        user_agent: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Loads or fetches the official SEC ticker -> CIK mapping (10,000+ companies).
        Guarantees exact 10-digit zero-padded CIK string for each ticker symbol.
        """
        if cache_path and cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                mapping = {
                    v["ticker"].upper().strip(): str(v["cik_str"]).zfill(10)
                    for v in data.values()
                    if "ticker" in v and "cik_str" in v
                }
                # Known holding company restructurings or missing exchange entries
                mapping.update({
                    "XOM": "0000034088",  # Exxon Mobil Corp operating company (321 filings)
                    "AVB": "0000915912",  # AvalonBay Communities, Inc.
                    "EA": "0000712515",   # Electronic Arts Inc.
                })
                return mapping
            except Exception:
                pass

        if not user_agent:
            user_agent = os.environ.get("SEC_EDGAR_USER_AGENT", DEFAULT_SEC_USER_AGENT)

        is_valid, ua_err = validate_sec_user_agent(user_agent)
        if not is_valid:
            print(f"[ERROR] Cannot fetch SEC company tickers: {ua_err}")
            return {
                "XOM": "0000034088",
                "AVB": "0000915912",
                "EA": "0000712515",
            }

        # Fetch live if cache missing or corrupted
        url = "https://www.sec.gov/files/company_tickers.json"
        headers = {"User-Agent": user_agent, "Host": "www.sec.gov"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as res:
            data = json.loads(res.read().decode("utf-8"))
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as fp:
                json.dump(data, fp, indent=2)
        mapping = {
            v["ticker"].upper().strip(): str(v["cik_str"]).zfill(10)
            for v in data.values()
            if "ticker" in v and "cik_str" in v
        }
        mapping.update({
            "XOM": "0000034088",
            "AVB": "0000915912",
            "EA": "0000712515",
        })
        return mapping

    @staticmethod
    def get_ciks_for_ticker(ticker: str) -> List[str]:
        """
        Returns all official CIK strings for a given ticker symbol.
        Handles corporate restructurings, successor/predecessor entities,
        and multi-CIK histories (e.g. XOM: 0000034088 operating predecessor and 0002115436 holding successor).
        """
        t_clean = ticker.upper().strip()
        if t_clean in CIK_SUCCESSION_REGISTRY:
            return [entry["cik"] for entry in CIK_SUCCESSION_REGISTRY[t_clean]]
        mapping = DataProvider.get_sec_ticker_to_cik_map()
        cik = mapping.get(t_clean)
        return [cik] if cik else []

    @staticmethod
    def fetch_sec_edgar_facts(
        cik: str,
        user_agent: Optional[str] = None,
        max_retries: int = 3,
        backoff_sec: float = 1.0
    ) -> Dict[str, Any]:
        """
        Fetches official SEC EDGAR company facts XBRL JSON.
        Validates User-Agent per SEC Fair Access policy before sending network requests.
        Handles gzip encoding, rate limiting, and offline/airgapped environments gracefully without crashing.
        """
        import gzip
        import time
        if not user_agent:
            user_agent = os.environ.get("SEC_EDGAR_USER_AGENT", DEFAULT_SEC_USER_AGENT)

        # Enforce SEC Fair Access User-Agent validation
        is_valid, ua_err = validate_sec_user_agent(user_agent)
        if not is_valid:
            return {
                "status": "ERROR",
                "error_type": "SEC_INVALID_USER_AGENT",
                "message": ua_err,
                "details": "SEC EDGAR requests halted safely. Valid organizational contact required per SEC Fair Access policy. Zero fake data generated."
            }

        cik_clean = str(cik).lstrip("0").zfill(10)
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_clean}.json"
        headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov"
        }

        req = urllib.request.Request(url, headers=headers)
        last_error = None

        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(req, timeout=12) as response:
                    payload = response.read()
                    # Decompress if gzipped (magic bytes 0x1f 0x8b)
                    if len(payload) >= 2 and payload[0] == 0x1f and payload[1] == 0x8b:
                        payload = gzip.decompress(payload)
                    data = json.loads(payload.decode("utf-8"))
                    return {"status": "OK", "data": data}
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return {
                        "status": "NO_DATA",
                        "error_type": "SEC_404_NOT_FOUND",
                        "message": f"HTTP Error 404: No company facts found for CIK {cik_clean}",
                        "details": "SEC EDGAR has no XBRL facts JSON for this CIK. Zero fake data generated."
                    }
                elif e.code == 429:
                    last_error = e
                    if attempt < max_retries - 1:
                        time.sleep(backoff_sec * (2 ** attempt))
                        continue
                else:
                    last_error = e
                    if attempt < max_retries - 1 and e.code in (500, 502, 503, 504):
                        time.sleep(backoff_sec * (2 ** attempt))
                        continue
                    break
            except (urllib.error.URLError, socket.gaierror, ConnectionError, OSError) as e:
                err_str = str(e)
                if "11001" in err_str or "getaddrinfo failed" in err_str:
                    return {
                        "status": "ERROR",
                        "error_type": "OFFLINE_NETWORK_BLOCK",
                        "message": err_str,
                        "details": f"Network unreachable ([Errno 11001] or equivalent). Ingestion halted safely. Zero fake data generated."
                    }
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(backoff_sec * (2 ** attempt))
                    continue
            except Exception as e:
                return {
                    "status": "ERROR",
                    "error_type": "UNEXPECTED_ERROR",
                    "message": str(e),
                    "details": f"Network or decoding error: {e}. Zero fake data generated."
                }

        return {
            "status": "ERROR",
            "error_type": "NETWORK_OR_HTTP_ERROR",
            "message": str(last_error),
            "details": f"Failed after {max_retries} attempts: {last_error}. Zero fake data generated."
        }

    # -------------------------------------------------------------------------
    # SEC EDGAR COMPANY FACTS PARSER
    # -------------------------------------------------------------------------
    @staticmethod
    def parse_sec_company_facts(facts_json: Dict[str, Any], ticker: str) -> List[Dict[str, Any]]:
        """
        Parses official SEC EDGAR company facts JSON into standardized point-in-time financial records.
        """
        if not facts_json or "facts" not in facts_json or "us-gaap" not in facts_json["facts"]:
            return []

        us_gaap = facts_json["facts"]["us-gaap"]
        cik = str(facts_json.get("cik", "")).zfill(10)

        # 1. Collect candidate values from us-gaap concepts
        direct_points = {m: {} for m in CONCEPT_MAPPINGS}
        # Track YTD cumulative values by (metric, start_date) for discrete quarterly derivation
        ytd_by_start = {m: {} for m in FLOW_METRICS}
        all_keys = set()
        metric_key_to_starts = {m: {} for m in FLOW_METRICS}
        metric_key_to_ytd_start = {m: {} for m in FLOW_METRICS}

        for metric, concepts in CONCEPT_MAPPINGS.items():
            is_flow = metric in FLOW_METRICS
            for name in concepts:
                if name not in us_gaap or "units" not in us_gaap[name]:
                    continue
                units = us_gaap[name]["units"]
                for unit_key, items in units.items():
                    for item in items:
                        form = item.get("form")
                        if form not in ("10-K", "10-Q", "10-K/A", "10-Q/A"):
                            continue
                        fy = item.get("fy")
                        fp = item.get("fp")
                        end = item.get("end")
                        filed = item.get("filed")
                        val = item.get("val")
                        if val is None or not filed or not end or filed < end:
                            continue

                        start = item.get("start")
                        dur = None
                        if start and end:
                            try:
                                dur = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
                            except Exception:
                                dur = None

                        key = (form, fy, fp, end, filed)
                        all_keys.add(key)
                        if is_flow and start:
                            if key not in metric_key_to_starts[metric]:
                                metric_key_to_starts[metric][key] = []
                            if start not in metric_key_to_starts[metric][key]:
                                metric_key_to_starts[metric][key].append(start)

                        if not is_flow:
                            # Instant metric (balance sheet snapshot): first non-null concept synonym wins
                            if key not in direct_points[metric]:
                                direct_points[metric][key] = val
                        else:
                            # Flow metric (income statement & cash flow)
                            if form in ("10-Q", "10-Q/A"):
                                # Discrete quarterly period target: 65 to 115 days (~90d)
                                if dur is not None and 65 <= dur <= 115:
                                    if key not in direct_points[metric]:
                                        direct_points[metric][key] = val
                                elif dur is None:
                                    # Fallback when start is omitted (e.g. synthetic test mocks)
                                    if key not in direct_points[metric]:
                                        direct_points[metric][key] = val

                                # Record YTD cumulative figures by exact fiscal start date and fiscal year
                                if start and dur is not None:
                                    ytd_key = (fy, start)
                                    if ytd_key not in ytd_by_start[metric]:
                                        ytd_by_start[metric][ytd_key] = {}
                                    if 65 <= dur <= 115 and "q1" not in ytd_by_start[metric][ytd_key]:
                                        ytd_by_start[metric][ytd_key]["q1"] = val
                                        ytd_by_start[metric][ytd_key]["q1_end"] = end
                                    elif 150 <= dur <= 210 and "q2" not in ytd_by_start[metric][ytd_key]:
                                        ytd_by_start[metric][ytd_key]["q2"] = val
                                        ytd_by_start[metric][ytd_key]["q2_end"] = end
                                        metric_key_to_ytd_start[metric][key] = start
                                    elif 240 <= dur <= 300 and "q3" not in ytd_by_start[metric][ytd_key]:
                                        ytd_by_start[metric][ytd_key]["q3"] = val
                                        ytd_by_start[metric][ytd_key]["q3_end"] = end
                                        metric_key_to_ytd_start[metric][key] = start

                            elif form in ("10-K", "10-K/A"):
                                # Annual period target: 330 to 395 days (~365d)
                                if dur is not None and 330 <= dur <= 395:
                                    if key not in direct_points[metric]:
                                        direct_points[metric][key] = val
                                elif dur is None:
                                    # Fallback for synthetic test mocks
                                    if key not in direct_points[metric]:
                                        direct_points[metric][key] = val

        # Check dei for EntityCommonStockSharesOutstanding as fallback
        dei = facts_json.get("facts", {}).get("dei", {})
        if "EntityCommonStockSharesOutstanding" in dei and "units" in dei["EntityCommonStockSharesOutstanding"]:
            for unit_key, items in dei["EntityCommonStockSharesOutstanding"]["units"].items():
                for item in items:
                    form = item.get("form")
                    if form in ("10-K", "10-Q", "10-K/A", "10-Q/A"):
                        fy = item.get("fy")
                        fp = item.get("fp")
                        end = item.get("end")
                        filed = item.get("filed")
                        val = item.get("val")
                        if val is not None and filed and end and filed >= end:
                            # Attach shares to existing financial statement filing keys to prevent creating orphan cover-page dates
                            matching = [k for k in all_keys if k[0] == form and k[1] == fy and k[2] == fp and k[4] == filed]
                            if matching:
                                for k in matching:
                                    if direct_points["shares_outstanding"].get(k) is None:
                                        direct_points["shares_outstanding"][k] = val
                            else:
                                key = (form, fy, fp, end, filed)
                                if key not in direct_points["shares_outstanding"]:
                                    direct_points["shares_outstanding"][key] = val
                                    all_keys.add(key)

        # 2. Reconcile missing discrete Q2 / Q3 flow items from YTD differences sharing the same fiscal year and start date
        for (form, fy, fp, end, filed) in all_keys:
            if form in ("10-Q", "10-Q/A"):
                key = (form, fy, fp, end, filed)
                for m in FLOW_METRICS:
                    if key not in direct_points[m]:
                        # Determine candidate start dates isolated to this specific flow metric
                        candidate_starts = []
                        if key in metric_key_to_ytd_start[m]:
                            candidate_starts.append(metric_key_to_ytd_start[m][key])
                        for st in metric_key_to_starts[m].get(key, []):
                            if st not in candidate_starts:
                                candidate_starts.append(st)
                        # Metric-specific fallback: search start dates belonging to the SAME fiscal year (fy)
                        if not candidate_starts:
                            for (y_fy, st) in ytd_by_start[m]:
                                if y_fy == fy and st < end:
                                    candidate_starts.append(st)

                        for st in candidate_starts:
                            ytd_key = (fy, st)
                            if ytd_key in ytd_by_start[m]:
                                st_dict = ytd_by_start[m][ytd_key]
                                if fp == "Q2" and "q2" in st_dict and "q1" in st_dict:
                                    if st_dict.get("q2_end") and st_dict["q2_end"] != end:
                                        continue
                                    direct_points[m][key] = st_dict["q2"] - st_dict["q1"]
                                    break
                                elif fp == "Q3" and "q3" in st_dict and "q2" in st_dict:
                                    if st_dict.get("q3_end") and st_dict["q3_end"] != end:
                                        continue
                                    direct_points[m][key] = st_dict["q3"] - st_dict["q2"]
                                    break

        records = []
        for (form, fy, fp, end, filed) in all_keys:
            if not filed or not end or filed < end:
                continue

            metrics = {metric: direct_points[metric].get((form, fy, fp, end, filed)) for metric in CONCEPT_MAPPINGS}

            rev = metrics.get("revenue")
            cost_rev = metrics.get("cost_of_revenue")
            op_inc = metrics.get("operating_income")
            net_inc = metrics.get("net_income")
            ocf = metrics.get("operating_cash_flow")
            capex = metrics.get("capex")
            assets = metrics.get("total_assets")
            liab = metrics.get("total_liabilities")
            equity = metrics.get("total_equity")

            # Debt handling: if neither long-term nor short-term debt concept exists, total_debt is None.
            # If at least one is present, compute sum (supporting debt-free companies where debt is 0.0).
            lt_debt = metrics.get("long_term_debt")
            st_debt = metrics.get("short_term_debt")
            if lt_debt is not None or st_debt is not None:
                total_debt = (lt_debt or 0.0) + (st_debt or 0.0)
            else:
                total_debt = None

            ca = metrics.get("current_assets")
            cl = metrics.get("current_liabilities")
            shares = metrics.get("shares_outstanding")

            # Free Cash Flow = OCF - CapEx (strictly requires both; never equate FCF = OCF if CapEx missing)
            fcf = None
            if ocf is not None and capex is not None:
                fcf = ocf - abs(capex)

            # Gross Margin
            gross_m = None
            if rev and rev > 0 and cost_rev is not None:
                gross_m = (rev - cost_rev) / rev

            # Operating Margin
            op_m = None
            if rev and rev > 0 and op_inc is not None:
                op_m = op_inc / rev

            # ROE
            roe = None
            if net_inc is not None and equity is not None:
                if equity > 0:
                    roe = net_inc / equity
                else:
                    roe = -1.0  # Negative equity marker

            # Debt-to-Equity (Handling Negative Equity)
            d_to_e = None
            if total_debt is not None and equity is not None:
                if equity > 0:
                    d_to_e = total_debt / equity
                else:
                    d_to_e = -1.0  # Negative equity indicates extreme financial risk

            # Current Ratio
            curr_ratio = None
            if ca is not None and cl is not None and cl > 0:
                curr_ratio = ca / cl

            rec = {
                "ticker": ticker.upper(),
                "cik": cik,
                "filing_date": filed,
                "period_end_date": end,
                "form": form,
                "fiscal_year": fy,
                "fiscal_period": fp,
                "revenue": rev,
                "cost_of_revenue": cost_rev,
                "operating_income": op_inc,
                "net_income": net_inc,
                "operating_cash_flow": ocf,
                "capex": capex,
                "free_cash_flow": fcf,
                "total_assets": assets,
                "total_liabilities": liab,
                "total_equity": equity,
                "total_debt": total_debt,
                "current_assets": ca,
                "current_liabilities": cl,
                "shares_outstanding": shares,
                "gross_margin": gross_m,
                "operating_margin": op_m,
                "roe": roe,
                "debt_to_equity": d_to_e,
                "current_ratio": curr_ratio,
                "source": "SEC_EDGAR_PIT"
            }
            records.append(rec)

        # Sort records chronologically by filing_date
        records.sort(key=lambda x: (x["filing_date"], x["period_end_date"]))
        return records

    # -------------------------------------------------------------------------
    # DATABASE INGESTION
    # -------------------------------------------------------------------------
    def save_fundamentals(self, records: List[Dict[str, Any]]) -> int:
        """Inserts or updates fundamental records in SQLite."""
        if not records:
            return 0
        cur = self.conn.cursor()
        inserted = 0
        for r in records:
            cur.execute("""
                INSERT INTO company_fundamentals (
                    ticker, cik, filing_date, period_end_date, form, fiscal_year, fiscal_period,
                    revenue, cost_of_revenue, operating_income, net_income, operating_cash_flow, capex, free_cash_flow,
                    total_assets, total_liabilities, total_equity, total_debt, current_assets,
                    current_liabilities, shares_outstanding, gross_margin, operating_margin,
                    roe, debt_to_equity, current_ratio, source
                ) VALUES (
                    :ticker, :cik, :filing_date, :period_end_date, :form, :fiscal_year, :fiscal_period,
                    :revenue, :cost_of_revenue, :operating_income, :net_income, :operating_cash_flow, :capex, :free_cash_flow,
                    :total_assets, :total_liabilities, :total_equity, :total_debt, :current_assets,
                    :current_liabilities, :shares_outstanding, :gross_margin, :operating_margin,
                    :roe, :debt_to_equity, :current_ratio, :source
                )
                ON CONFLICT(ticker, filing_date, period_end_date, form) DO UPDATE SET
                    cik=excluded.cik,
                    fiscal_year=excluded.fiscal_year,
                    fiscal_period=excluded.fiscal_period,
                    revenue=excluded.revenue,
                    cost_of_revenue=excluded.cost_of_revenue,
                    operating_income=excluded.operating_income,
                    net_income=excluded.net_income,
                    operating_cash_flow=excluded.operating_cash_flow,
                    capex=excluded.capex,
                    free_cash_flow=excluded.free_cash_flow,
                    total_assets=excluded.total_assets,
                    total_liabilities=excluded.total_liabilities,
                    total_equity=excluded.total_equity,
                    total_debt=excluded.total_debt,
                    current_assets=excluded.current_assets,
                    current_liabilities=excluded.current_liabilities,
                    shares_outstanding=excluded.shares_outstanding,
                    gross_margin=excluded.gross_margin,
                    operating_margin=excluded.operating_margin,
                    roe=excluded.roe,
                    debt_to_equity=excluded.debt_to_equity,
                    current_ratio=excluded.current_ratio,
                    source=excluded.source
            """, r)
            inserted += 1
        self.conn.commit()
        return inserted

    def save_news(self, events: List[Dict[str, Any]]) -> int:
        """Inserts news events with duplicate suppression."""
        if not events:
            return 0
        cur = self.conn.cursor()
        inserted = 0
        for e in events:
            cur.execute("""
                INSERT OR IGNORE INTO news_catalysts (
                    ticker, published_at, headline, summary, event_type, sentiment_score, impact_score, source, url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                e["ticker"].upper(), e["published_at"], e["headline"], e.get("summary", ""),
                e.get("event_type", "GENERAL"), e.get("sentiment_score", 0.0),
                e.get("impact_score", 0.5), e.get("source", "NEWS_FEED"), e.get("url")
            ))
            if cur.rowcount > 0:
                inserted += 1
        self.conn.commit()
        return inserted

    def save_earnings_dates(self, calendar_items: List[Dict[str, Any]]) -> int:
        """Inserts upcoming or historical earnings calendar dates."""
        if not calendar_items:
            return 0
        cur = self.conn.cursor()
        inserted = 0
        for c in calendar_items:
            cur.execute("""
                INSERT OR REPLACE INTO earnings_calendar (
                    ticker, earnings_date, time_of_day, confirmed, eps_estimate, eps_actual, rev_estimate, rev_actual, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                c["ticker"].upper(), c["earnings_date"], c.get("time_of_day", "AMC"),
                c.get("confirmed", 1), c.get("eps_estimate"), c.get("eps_actual"),
                c.get("rev_estimate"), c.get("rev_actual"), c.get("source", "CALENDAR")
            ))
            inserted += 1
        self.conn.commit()
        return inserted
