"""
trade_manager.py - Savdo buyurtmalari, pozitsiyalar hayot sikli va holatlar boshqaruvi.
"""
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from risk_manager import RiskConfig, evaluate_market_open_entry


class TradeManager:
    def __init__(self, db_path: str = "data/screener.db", initial_cash: float = 10000.0):
        self.db_path = db_path
        self.initial_cash = initial_cash
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT UNIQUE NOT NULL,
                    ticker TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    planned_entry REAL,
                    actual_entry REAL,
                    fill_price REAL,
                    shares INTEGER,
                    remaining_shares INTEGER,
                    initial_stop REAL,
                    current_stop REAL,
                    initial_target REAL,
                    current_target REAL,
                    planned_risk REAL,
                    total_cost REAL DEFAULT 0.0,
                    realized_pnl REAL DEFAULT 0.0,
                    unrealized_pnl REAL DEFAULT 0.0,
                    signal_date TEXT,
                    entry_date TEXT,
                    exit_date TEXT,
                    exit_price REAL,
                    exit_reason TEXT,
                    cancel_reason TEXT,
                    hold_days INTEGER DEFAULT 0,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            # Additiv migratsiya: eski bazalarda last_bar_date ustuni yo'q (ma'lumot o'chirilmaydi)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(portfolio_trades)")}
            if "last_bar_date" not in cols:
                conn.execute("ALTER TABLE portfolio_trades ADD COLUMN last_bar_date TEXT")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_cash (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    cash REAL NOT NULL,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO portfolio_cash (id, cash, updated_at)
                VALUES (1, ?, ?)
            """, (self.initial_cash, datetime.now().isoformat()))

    def get_cash(self) -> float:
        with self._get_conn() as conn:
            row = conn.execute("SELECT cash FROM portfolio_cash WHERE id = 1").fetchone()
            return float(row["cash"]) if row else self.initial_cash

    def set_cash(self, amount: float):
        with self._get_conn() as conn:
            conn.execute("UPDATE portfolio_cash SET cash = ?, updated_at = ? WHERE id = 1",
                         (amount, datetime.now().isoformat()))

    def add_planned_trade(self, plan: dict, strategy_version: str = "V2_BREAKOUT") -> Tuple[bool, str]:
        signal_id = f"{plan['ticker']}_{plan['signal_date']}_{strategy_version}"
        now_ts = datetime.now().isoformat()
        with self._get_conn() as conn:
            existing = conn.execute(
                "SELECT id, status FROM portfolio_trades WHERE signal_id = ? OR (ticker = ? AND status IN ('PLANNED', 'OPEN', 'PARTIALLY_CLOSED'))",
                (signal_id, plan["ticker"])
            ).fetchone()
            if existing:
                return False, f"Takroriy pozitsiya taqiqlandi: {plan['ticker']} allaqachon mavjud (Holat: {existing['status']})"

            conn.execute("""
                INSERT INTO portfolio_trades (
                    signal_id, ticker, strategy_version, status, planned_entry,
                    shares, remaining_shares, initial_stop, current_stop,
                    initial_target, current_target, planned_risk, signal_date,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'PLANNED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal_id, plan["ticker"], strategy_version, plan["signal_price"],
                plan["shares"], plan["shares"], plan["planned_stop"], plan["planned_stop"],
                plan["planned_target"], plan["planned_target"], plan["dollar_risk"],
                plan["signal_date"], now_ts, now_ts
            ))
        return True, signal_id

    def execute_market_open(self, signal_id: str, actual_open: float, entry_date: str, cfg: RiskConfig,
                            current_equity: Optional[float] = None,
                            portfolio_heat: Optional[float] = None,
                            open_positions_count: Optional[int] = None) -> Tuple[bool, str]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM portfolio_trades WHERE signal_id = ?", (signal_id,)).fetchone()
            if not row or row["status"] != "PLANNED":
                return False, "Reja topilmadi yoki PLANNED holatida emas"

            plan_dict = {
                "ticker": row["ticker"],
                "signal_price": row["planned_entry"],
                "min_open": row["planned_entry"] * (1 - cfg.max_allowed_gap_pct),
                "max_open": row["planned_entry"] * (1 + cfg.max_allowed_gap_pct),
                "atr": (row["planned_entry"] - row["initial_stop"]) / cfg.stop_atr,
            }
            cash = self.get_cash()
            open_rows = conn.execute(
                "SELECT remaining_shares, fill_price, current_stop FROM portfolio_trades "
                "WHERE status IN ('OPEN', 'PARTIALLY_CLOSED')"
            ).fetchall()
            if open_positions_count is None:
                open_positions_count = len(open_rows)
            if current_equity is None:
                # Narx berilmasa: naqd + ochiq pozitsiyalarning tannarxi
                current_equity = cash + sum(
                    r["remaining_shares"] * r["fill_price"] for r in open_rows if r["fill_price"])
            if portfolio_heat is None:
                open_risk = sum(
                    max(0.0, r["fill_price"] - r["current_stop"]) * r["remaining_shares"]
                    for r in open_rows if r["fill_price"] and r["current_stop"] is not None)
                portfolio_heat = open_risk / current_equity if current_equity > 0 else 0.0
            res, msg = evaluate_market_open_entry(
                plan_dict, actual_open, cfg, cash, current_equity, portfolio_heat, open_positions_count)
            now_ts = datetime.now().isoformat()

            if res is None:
                # Testlar va downstream hisobotlar uchun pozitsiya limiti xabarini
                # barqaror CAPACITY kodi bilan qaytaramiz.
                if "POSITION_LIMIT" in str(msg) and "CAPACITY" not in str(msg):
                    msg = f"CAPACITY_{msg}"
                conn.execute("""
                    UPDATE portfolio_trades SET status = 'CANCELLED', cancel_reason = ?, updated_at = ?
                    WHERE signal_id = ?
                """, (msg, now_ts, signal_id))
                return False, msg

            cost = res["shares"] * res["fill_price"] * cfg.cost_per_side
            total_required = res["notional"]
            if total_required > cash:
                conn.execute("""
                    UPDATE portfolio_trades SET status = 'CANCELLED', cancel_reason = 'CASH_DEFICIT', updated_at = ?
                    WHERE signal_id = ?
                """, (now_ts, signal_id))
                return False, "Naqd pul yetarli emas"

            conn.execute("UPDATE portfolio_cash SET cash = cash - ?, updated_at = ? WHERE id = 1",
                         (total_required, now_ts))
            conn.execute("""
                UPDATE portfolio_trades SET
                    status = 'OPEN', actual_entry = ?, fill_price = ?,
                    shares = ?, remaining_shares = ?, current_stop = ?,
                    current_target = ?, total_cost = ?, entry_date = ?,
                    updated_at = ?
                WHERE signal_id = ?
            """, (
                actual_open, res["fill_price"], res["shares"], res["shares"],
                res["stop"], res["target"], cost, entry_date, now_ts, signal_id
            ))
            return True, "EXECUTED"

    def update_daily_positions(self, today_date: str, ohlc_dict: Dict[str, dict], max_hold: int = 35, cost_per_side: float = 0.001) -> List[dict]:
        """Bir savdo kunining (today_date) bar'ini ochiq pozitsiyalarga qo'llaydi.

        - Idempotent: bir xil kun qayta chaqirilsa (last_bar_date) hech narsa o'zgarmaydi.
        - Kirish kuni (entry_date == today_date): ochilish gap tekshiruvi va hold_days oshishi YO'Q
          (engine.py bilan bir xil), faqat kun ichidagi Stop/Target tekshiriladi.
        - Konservativ qoida: Stop har doim Target'dan oldin.
        """
        actions = []
        now_ts = datetime.now().isoformat()
        with self._get_conn() as conn:
            open_rows = conn.execute("""
                SELECT * FROM portfolio_trades WHERE status IN ('OPEN', 'PARTIALLY_CLOSED')
                ORDER BY id
            """).fetchall()

            for row in open_rows:
                t = row["ticker"]
                t_id = row["id"]
                if row["entry_date"] and row["entry_date"] > today_date:
                    continue
                if row["last_bar_date"] and row["last_bar_date"] >= today_date:
                    continue
                if t not in ohlc_dict:
                    actions.append({"ticker": t, "signal_id": row["signal_id"], "action": "STALE_DATA",
                                    "reason": "Kotirovka topilmadi"})
                    continue

                bar = ohlc_dict[t]
                o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
                is_entry_bar = row["entry_date"] == today_date
                hold_days = row["hold_days"] if is_entry_bar else row["hold_days"] + 1

                exit_price = None
                exit_reason = None

                # 1) Ochilish gap'lari (kirish kunida ochilish = kirish narxi, gap yo'q)
                if not is_entry_bar and o <= row["current_stop"]:
                    exit_price, exit_reason = o, "SL_GAP"
                elif not is_entry_bar and o >= row["current_target"]:
                    exit_price, exit_reason = o, "TP_GAP"
                # 2) Kun ichida: Stop har doim birinchi
                elif l <= row["current_stop"]:
                    exit_price, exit_reason = row["current_stop"], "SL"
                elif h >= row["current_target"]:
                    exit_price, exit_reason = row["current_target"], "TP"
                # 3) Maksimal muddat (Time exit)
                elif hold_days >= max_hold:
                    exit_price, exit_reason = c, "TIME"

                if exit_price is not None:
                    rem_shares = row["remaining_shares"]
                    exit_fill = exit_price * (1 - cost_per_side)
                    proceeds = rem_shares * exit_fill
                    cost_exit = rem_shares * exit_price * cost_per_side
                    pnl = proceeds - (rem_shares * row["fill_price"])

                    # Naqd pul shu ulanishda atomik yangilanadi (ikkinchi ulanish "database is locked" beradi)
                    conn.execute("UPDATE portfolio_cash SET cash = cash + ?, updated_at = ? WHERE id = 1",
                                 (proceeds, now_ts))
                    conn.execute("""
                        UPDATE portfolio_trades SET
                            status = 'CLOSED', remaining_shares = 0,
                            exit_date = ?, exit_price = ?, exit_reason = ?,
                            realized_pnl = realized_pnl + ?, unrealized_pnl = 0.0,
                            total_cost = total_cost + ?, hold_days = ?, last_bar_date = ?,
                            updated_at = ?
                        WHERE id = ?
                    """, (today_date, exit_price, exit_reason, pnl, cost_exit, hold_days, today_date, now_ts, t_id))
                    actions.append({"ticker": t, "signal_id": row["signal_id"], "action": "CLOSED",
                                    "reason": exit_reason, "pnl": pnl, "exit_price": exit_price})
                else:
                    unrealized = row["remaining_shares"] * (c - row["fill_price"])
                    conn.execute("""
                        UPDATE portfolio_trades SET
                            unrealized_pnl = ?, hold_days = ?, last_bar_date = ?, updated_at = ?
                        WHERE id = ?
                    """, (unrealized, hold_days, today_date, now_ts, t_id))
        return actions

    def partial_close(self, signal_id: str, shares_to_close: int, exit_price: float, exit_date: str, cost_per_side: float = 0.001) -> Tuple[bool, str]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM portfolio_trades WHERE signal_id = ?", (signal_id,)).fetchone()
            if not row or row["status"] not in ("OPEN", "PARTIALLY_CLOSED"):
                return False, "Ochiq pozitsiya topilmadi"

            rem = row["remaining_shares"]
            if shares_to_close <= 0 or shares_to_close >= rem:
                return False, f"Yaroqsiz lot soni (Qoldiq: {rem})"

            exit_fill = exit_price * (1 - cost_per_side)
            proceeds = shares_to_close * exit_fill
            pnl = proceeds - (shares_to_close * row["fill_price"])
            cost = shares_to_close * exit_price * cost_per_side

            conn.execute("UPDATE portfolio_cash SET cash = cash + ?, updated_at = ? WHERE id = 1",
                         (proceeds, datetime.now().isoformat()))
            conn.execute("""
                UPDATE portfolio_trades SET
                    status = 'PARTIALLY_CLOSED', remaining_shares = remaining_shares - ?,
                    realized_pnl = realized_pnl + ?, total_cost = total_cost + ?,
                    updated_at = ?
                WHERE signal_id = ?
            """, (shares_to_close, pnl, cost, datetime.now().isoformat(), signal_id))
            return True, f"{shares_to_close} dona yopildi, PnL: ${pnl:.2f}"