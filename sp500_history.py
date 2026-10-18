"""
sp500_history.py - S&P 500 tarixiy a'zoligini (Point-in-Time) tiklash,
sana x ticker matritsasi bo'yicha qamrov auditi va delisted kompaniyalar nazorati.
"""
import sqlite3
from typing import Dict, List, Set, Tuple, Optional
import pandas as pd

HISTORICAL_CHANGES_SEED = [
    {"ticker": "TSLA", "company_name": "Tesla Inc", "start_date": "2020-12-21", "end_date": None, "source": "SP_OFFICIAL", "version": "1.0"},
    {"ticker": "MRNA", "company_name": "Moderna Inc", "start_date": "2021-07-21", "end_date": None, "source": "SP_OFFICIAL", "version": "1.0"},
    {"ticker": "PANW", "company_name": "Palo Alto Networks", "start_date": "2023-06-20", "end_date": None, "source": "SP_OFFICIAL", "version": "1.0"},
    {"ticker": "PLTR", "company_name": "Palantir Technologies", "start_date": "2024-09-23", "end_date": None, "source": "SP_OFFICIAL", "version": "1.0"},
    {"ticker": "DELL", "company_name": "Dell Technologies", "start_date": "2024-09-23", "end_date": None, "source": "SP_OFFICIAL", "version": "1.0"},
    {"ticker": "CRWD", "company_name": "CrowdStrike Holdings", "start_date": "2024-06-24", "end_date": None, "source": "SP_OFFICIAL", "version": "1.0"},
    {"ticker": "KKR", "company_name": "KKR & Co", "start_date": "2024-06-24", "end_date": None, "source": "SP_OFFICIAL", "version": "1.0"},
    {"ticker": "SMCI", "company_name": "Super Micro Computer", "start_date": "2024-03-18", "end_date": None, "source": "SP_OFFICIAL", "version": "1.0"},
    {"ticker": "ABNB", "company_name": "Airbnb Inc", "start_date": "2023-09-18", "end_date": None, "source": "SP_OFFICIAL", "version": "1.0"},
    {"ticker": "UBER", "company_name": "Uber Technologies", "start_date": "2023-12-18", "end_date": None, "source": "SP_OFFICIAL", "version": "1.0"},
    {"ticker": "SIVB", "company_name": "SVB Financial Group", "start_date": "2019-01-01", "end_date": "2023-03-15", "source": "SP_OFFICIAL", "version": "1.0"},
    {"ticker": "SBNY", "company_name": "Signature Bank", "start_date": "2021-12-20", "end_date": "2023-03-15", "source": "SP_OFFICIAL", "version": "1.0"},
    {"ticker": "FRC", "company_name": "First Republic Bank", "start_date": "2019-01-01", "end_date": "2023-05-04", "source": "SP_OFFICIAL", "version": "1.0"},
    {"ticker": "DISH", "company_name": "DISH Network", "start_date": "2019-01-01", "end_date": "2023-06-20", "source": "SP_OFFICIAL", "version": "1.0"},
    {"ticker": "AAL", "company_name": "American Airlines Group", "start_date": "2019-01-01", "end_date": "2024-09-23", "source": "SP_OFFICIAL", "version": "1.0"},
]


class SP500HistoryManager:
    def __init__(
        self,
        db_path: str = "data/screener.db",
        rules: Optional[Dict[str, List[Tuple[str, Optional[str]]]]] = None,
        complete: Optional[bool] = None,
    ):
        self.db_path = db_path
        # Tests may inject a small in-memory PIT universe without creating a database.
        if rules is not None:
            self.membership_rules = {
                ticker: list(intervals if intervals and isinstance(intervals[0], (tuple, list)) else [intervals])
                for ticker, intervals in rules.items()
            }
            self.complete = bool(complete)
            return
        self._init_db()
        self._load_cache()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sp500_historical_membership (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    company_name TEXT,
                    start_date TEXT NOT NULL,
                    end_date TEXT,
                    source TEXT,
                    version TEXT,
                    UNIQUE(ticker, start_date)
                )
            """)
            has_rows = conn.execute("SELECT COUNT(*) FROM sp500_historical_membership").fetchone()[0]
            for item in ([] if has_rows else HISTORICAL_CHANGES_SEED):
                conn.execute("""
                    INSERT OR IGNORE INTO sp500_historical_membership
                    (ticker, company_name, start_date, end_date, source, version)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (item["ticker"], item["company_name"], item["start_date"], item["end_date"], item["source"], item["version"]))

    def _load_cache(self):
        with self._get_conn() as conn:
            rows = conn.execute("SELECT ticker, start_date, end_date FROM sp500_historical_membership").fetchall()
            self.membership_rules = {}
            # version 2.0 = to'liq tarixiy tarkib (build_pit_membership.py); noma'lum ticker a'zo EMAS
            self.complete = bool(conn.execute(
                "SELECT 1 FROM sp500_historical_membership WHERE version LIKE '2%' LIMIT 1").fetchone())
            for r in rows:
                t = r["ticker"]
                if t not in self.membership_rules:
                    self.membership_rules[t] = []
                self.membership_rules[t].append((r["start_date"], r["end_date"]))

    def is_member_on_date(self, ticker: str, query_date: str, base_tickers_2026: Set[str], allow_unknown: bool = True) -> Optional[bool]:
        if ticker in self.membership_rules:
            for st, en in self.membership_rules[ticker]:
                if st <= query_date and (en is None or query_date <= en):
                    return True
            return False

        if not allow_unknown:
            return None
        if self.complete:
            return False

        return ticker in base_tickers_2026

    def get_point_in_time_universe(self, query_date: str, base_tickers_2026: Set[str]) -> Set[str]:
        universe = set()
        if not self.complete:
            for t in base_tickers_2026:
                if self.is_member_on_date(t, query_date, base_tickers_2026, allow_unknown=True):
                    universe.add(t)
        for t in self.membership_rules:
            for st, en in self.membership_rules[t]:
                if st <= query_date and (en is None or query_date <= en):
                    universe.add(t)
        return universe

    def membership_mask(self, ticker: str, index) -> "pd.Series":
        """index (DatetimeIndex) bo'yicha a'zolik maskasi (bir nechta oraliqni qo'llab-quvvatlaydi)."""
        mask = pd.Series(False, index=index)
        for st, en in self.membership_rules.get(ticker, []):
            lo = pd.to_datetime(st)
            hi = pd.to_datetime(en) if en else pd.Timestamp.max
            mask |= (index >= lo) & (index <= hi)
        return mask

    def evaluate_price_coverage(self, universe: Set[str], available_db_tickers: Set[str]) -> dict:
        covered = universe.intersection(available_db_tickers)
        missing = universe - available_db_tickers
        total = len(universe)
        coverage_pct = (len(covered) / total) if total > 0 else 0.0
        return {
            "total_constituents": total,
            "covered_count": len(covered),
            "missing_count": len(missing),
            "coverage_pct": coverage_pct,
            "missing_tickers": sorted(list(missing)),
        }

    def evaluate_date_matrix_coverage(self, dates: List[str], base_tickers_2026: Set[str], db_conn: sqlite3.Connection) -> dict:
        cursor = db_conn.cursor()
        total_slots = 0
        filled_slots = 0
        missing_by_ticker = {}

        cursor.execute("SELECT ticker, substr(date, 1, 10) FROM prices")
        available_pairs = set(cursor.fetchall())

        for d_str in dates:
            d_short = d_str[:10]
            current_universe = self.get_point_in_time_universe(d_short, base_tickers_2026)
            for t in current_universe:
                total_slots += 1
                if (t, d_short) in available_pairs:
                    filled_slots += 1
                else:
                    missing_by_ticker[t] = missing_by_ticker.get(t, 0) + 1

        coverage_pct = (filled_slots / total_slots) if total_slots > 0 else 0.0
        return {
            "total_slots": total_slots,
            "filled_slots": filled_slots,
            "missing_slots": total_slots - filled_slots,
            "coverage_pct": coverage_pct,
            "missing_by_ticker": missing_by_ticker,
            "is_fully_survivorship_free": (total_slots - filled_slots) == 0
        }