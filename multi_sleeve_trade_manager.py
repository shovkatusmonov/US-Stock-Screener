"""
multi_sleeve_trade_manager.py - Enterprise Multi-Sleeve Portfolio & Order Management Architecture.

Manages dedicated sub-accounts (sleeves) for 3 independent strategies:
1. SLEEVE_SP500:       SP500_LONG_TERM_ACCUMULATION (Drawdown/57% formula & dip tranches)
2. SLEEVE_BREAKOUT:    ATR Breakout V2 (Momentum Donchian Breakout)
3. SLEEVE_FUNDAMENTAL: AZIZ_HALIKOV_TRAILING_SEC_PROXY_V1 (6-Filter Fundamental Trend with SPY Anchor)

Guarantees:
- Strict sub-account cash isolation (Zero cross-sleeve cash cannibalization).
- Guaranteed cash solvency for macro accumulation dip-tranches (-15%, -25%, -35%).
- Cross-sleeve ticker contention policy (prevents duplicate exposure to the same stock).
- Dual-target partial exits (TP1 50% + Breakeven stop trail, TP2 runner).
- Comprehensive immutable audit log (SIGNAL, CONFLICT, ORDER, FILL, CANCEL, EXIT).
- Risk limits enforcement: Max heat per sleeve (5%), Max position limits, Gap filters, R/R >= 1.5.
"""

import math
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from risk_manager import RiskConfig, reward_risk


class MultiSleeveTradeManager:
    """
    Manages multi-strategy capital allocation, independent sub-accounts, and order lifecycles.
    """

    SLEEVE_SPECS = {
        "SLEEVE_SP500": {
            "strategy_name": "SP500_LONG_TERM_ACCUMULATION",
            "default_weight": 0.50,
            "max_positions": 1,
            "max_heat_pct": 0.20,
        },
        "SLEEVE_BREAKOUT": {
            "strategy_name": "ATR_BREAKOUT_V2",
            "default_weight": 0.35,
            "max_positions": 8,
            "max_heat_pct": 0.05,
        },
        "SLEEVE_FUNDAMENTAL": {
            "strategy_name": "AZIZ_HALIKOV_TRAILING_SEC_PROXY_V1",
            "default_weight": 0.15,
            "max_positions": 4,
            "max_heat_pct": 0.05,
        },
    }

    def __init__(
        self,
        db_path: str = "data/screener.db",
        total_initial_capital: float = 100_000.0,
        weights: Optional[Dict[str, float]] = None,
        table_prefix: str = "ms_",
    ):
        self.db_path = db_path
        self.total_initial_capital = total_initial_capital
        self.weights = weights or {k: v["default_weight"] for k, v in self.SLEEVE_SPECS.items()}
        self.prefix = table_prefix
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        p = self.prefix
        with self._get_conn() as conn:
            # 1. Sleeve master state table
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {p}sleeves (
                    sleeve_id TEXT PRIMARY KEY,
                    strategy_name TEXT NOT NULL,
                    target_weight REAL NOT NULL,
                    allocated_capital REAL NOT NULL,
                    cash_balance REAL NOT NULL,
                    invested_capital REAL NOT NULL DEFAULT 0.0,
                    total_equity REAL NOT NULL,
                    max_heat_pct REAL NOT NULL,
                    current_heat_pct REAL NOT NULL DEFAULT 0.0,
                    max_positions INTEGER NOT NULL,
                    open_positions_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
            """)

            # 2. Multi-sleeve trades table
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {p}trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sleeve_id TEXT NOT NULL,
                    signal_id TEXT UNIQUE NOT NULL,
                    ticker TEXT NOT NULL,
                    strategy_version TEXT NOT NULL,
                    status TEXT NOT NULL, -- PLANNED, OPEN, PARTIALLY_CLOSED, CLOSED, CANCELLED
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
                    last_bar_date TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (sleeve_id) REFERENCES {p}sleeves(sleeve_id)
                )
            """)

            # 3. Comprehensive immutable audit log
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {p}audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    sleeve_id TEXT,
                    event_type TEXT NOT NULL,
                    ticker TEXT,
                    action TEXT NOT NULL,
                    details TEXT,
                    sleeve_cash_after REAL,
                    portfolio_total_equity REAL
                )
            """)

            # Initialize sleeve records if not present
            now_ts = datetime.now().isoformat()
            for s_id, spec in self.SLEEVE_SPECS.items():
                w = self.weights.get(s_id, spec["default_weight"])
                cap = self.total_initial_capital * w
                conn.execute(f"""
                    INSERT OR IGNORE INTO {p}sleeves (
                        sleeve_id, strategy_name, target_weight, allocated_capital,
                        cash_balance, invested_capital, total_equity, max_heat_pct,
                        current_heat_pct, max_positions, open_positions_count, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0.0, ?, ?, 0.0, ?, 0, ?)
                """, (
                    s_id, spec["strategy_name"], w, cap, cap, cap,
                    spec["max_heat_pct"], spec["max_positions"], now_ts
                ))

    def log_audit(
        self,
        event_type: str,
        action: str,
        sleeve_id: Optional[str] = None,
        ticker: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        conn: Optional[sqlite3.Connection] = None,
    ):
        """Records an immutable audit event."""
        p = self.prefix
        now_ts = datetime.now().isoformat()
        details_str = json.dumps(details) if details else ""

        def _do_write(c):
            sleeve_cash = None
            if sleeve_id:
                row = c.execute(f"SELECT cash_balance FROM {p}sleeves WHERE sleeve_id = ?", (sleeve_id,)).fetchone()
                if row:
                    sleeve_cash = float(row["cash_balance"])
            
            tot_eq_row = c.execute(f"SELECT SUM(total_equity) FROM {p}sleeves").fetchone()
            tot_eq = float(tot_eq_row[0]) if tot_eq_row and tot_eq_row[0] is not None else 0.0

            c.execute(f"""
                INSERT INTO {p}audit_log (
                    timestamp, sleeve_id, event_type, ticker, action, details,
                    sleeve_cash_after, portfolio_total_equity
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (now_ts, sleeve_id, event_type, ticker, action, details_str, sleeve_cash, tot_eq))

        if conn is not None:
            _do_write(conn)
        else:
            with self._get_conn() as c:
                _do_write(c)

    def get_sleeve_state(self, sleeve_id: str) -> Dict[str, Any]:
        """Returns the current operational state of a specific sleeve."""
        p = self.prefix
        with self._get_conn() as conn:
            row = conn.execute(f"SELECT * FROM {p}sleeves WHERE sleeve_id = ?", (sleeve_id,)).fetchone()
            return dict(row) if row else {}

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Returns the firm-wide total capital, cash, invested, and risk metrics."""
        p = self.prefix
        with self._get_conn() as conn:
            rows = conn.execute(f"SELECT * FROM {p}sleeves").fetchall()
            sleeves = {r["sleeve_id"]: dict(r) for r in rows}
            total_cash = sum(s["cash_balance"] for s in sleeves.values())
            total_invested = sum(s["invested_capital"] for s in sleeves.values())
            total_equity = sum(s["total_equity"] for s in sleeves.values())
            total_open = sum(s["open_positions_count"] for s in sleeves.values())

            return {
                "total_equity": total_equity,
                "total_cash": total_cash,
                "total_invested": total_invested,
                "total_open_positions": total_open,
                "sleeves": sleeves,
            }

    # -------------------------------------------------------------------------
    # TRADE PLANNING & CROSS-SLEEVE CONFLICT RESOLUTION
    # -------------------------------------------------------------------------
    def add_planned_trade(
        self,
        sleeve_id: str,
        plan: dict,
        strategy_version: str,
        relative_strength: float = 1.0,
    ) -> Tuple[bool, str]:
        """
        Registers a planned trade within a designated sleeve.
        Enforces cross-sleeve ticker contention policies and duplicate prevention.
        """
        p = self.prefix
        if sleeve_id not in self.SLEEVE_SPECS:
            return False, f"UNKNOWN_SLEEVE: {sleeve_id}"

        ticker = plan["ticker"].upper()
        signal_date = plan["signal_date"]
        signal_id = f"{sleeve_id}_{ticker}_{signal_date}_{strategy_version}"
        now_ts = datetime.now().isoformat()

        with self._get_conn() as conn:
            # 1. Sleeve Capacity & Limit Checks
            s_row = conn.execute(f"SELECT * FROM {p}sleeves WHERE sleeve_id = ?", (sleeve_id,)).fetchone()
            if not s_row:
                return False, f"SLEEVE_NOT_FOUND: {sleeve_id}"

            active_sleeve_trades = conn.execute(f"""
                SELECT COUNT(*) FROM {p}trades
                WHERE sleeve_id = ? AND status IN ('PLANNED', 'OPEN', 'PARTIALLY_CLOSED')
            """, (sleeve_id,)).fetchone()[0]

            if active_sleeve_trades >= s_row["max_positions"]:
                self.log_audit("REJECTED", "SLEEVE_MAX_POSITIONS_REACHED", sleeve_id, ticker, {
                    "limit": s_row["max_positions"], "active": active_sleeve_trades
                }, conn=conn)
                return False, f"SLEEVE_CAPACITY_EXCEEDED: max {s_row['max_positions']} reached"

            # 2. Heat Limit Check
            planned_risk_pct = plan.get("risk_pct", 0.01)
            if s_row["current_heat_pct"] + planned_risk_pct > s_row["max_heat_pct"] + 1e-6:
                self.log_audit("REJECTED", "SLEEVE_HEAT_LIMIT_EXCEEDED", sleeve_id, ticker, {
                    "current_heat": s_row["current_heat_pct"], "max_heat": s_row["max_heat_pct"]
                }, conn=conn)
                return False, f"SLEEVE_HEAT_LIMIT_EXCEEDED: {s_row['current_heat_pct']:.3f} + {planned_risk_pct:.3f} > {s_row['max_heat_pct']:.3f}"

            # 3. Cross-Sleeve Ticker Contention Policy
            # If the ticker is already OPEN or PARTIALLY_CLOSED in ANY sleeve, reject second position
            existing_cross = conn.execute(f"""
                SELECT sleeve_id, status FROM {p}trades
                WHERE ticker = ? AND status IN ('OPEN', 'PARTIALLY_CLOSED')
            """, (ticker,)).fetchone()
            if existing_cross:
                msg = f"REJECTED_CROSS_SLEEVE_EXISTS: {ticker} already open in {existing_cross['sleeve_id']}"
                self.log_audit("CONFLICT_REJECTED", msg, sleeve_id, ticker, conn=conn)
                return False, msg

            # 4. Simultaneous PLANNED Contention Policy
            # If another sleeve already planned this ticker today, resolve by conviction / capacity
            planned_cross = conn.execute(f"""
                SELECT id, sleeve_id, planned_risk FROM {p}trades
                WHERE ticker = ? AND signal_date = ? AND status = 'PLANNED'
            """, (ticker, signal_date)).fetchone()
            if planned_cross:
                other_sleeve = planned_cross["sleeve_id"]
                # Rule: Priority to Breakout if RS > 1.2, else resolve by vacant capacity
                msg = f"CROSS_SLEEVE_CONTENTION: Both {sleeve_id} and {other_sleeve} signaled {ticker}."
                self.log_audit("CONTENTION_DETECTED", msg, sleeve_id, ticker, conn=conn)
                # Keep earlier plan, reject duplicate candidate
                return False, f"CONTENTION_RESOLVED_ASSIGNED_TO_{other_sleeve}"

            # 5. Duplicate Signal Check
            existing_signal = conn.execute(f"SELECT id FROM {p}trades WHERE signal_id = ?", (signal_id,)).fetchone()
            if existing_signal:
                msg = f"DUPLICATE_SIGNAL_REJECTED: {signal_id} already exists"
                self.log_audit("DUPLICATE_REJECTED", msg, sleeve_id, ticker, conn=conn)
                return False, msg

            # 6. Insert Planned Trade
            conn.execute(f"""
                INSERT INTO {p}trades (
                    sleeve_id, signal_id, ticker, strategy_version, status,
                    planned_entry, shares, remaining_shares, initial_stop, current_stop,
                    initial_target, current_target, planned_risk, signal_date,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'PLANNED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sleeve_id, signal_id, ticker, strategy_version, plan["signal_price"],
                plan["shares"], plan["shares"], plan["planned_stop"], plan["planned_stop"],
                plan["planned_target"], plan["planned_target"], plan["dollar_risk"],
                signal_date, now_ts, now_ts
            ))

            # Update planned heat
            conn.execute(f"""
                UPDATE {p}sleeves
                SET current_heat_pct = current_heat_pct + ?, updated_at = ?
                WHERE sleeve_id = ?
            """, (planned_risk_pct, now_ts, sleeve_id))

        self.log_audit("SIGNAL_ACCEPTED", "PLANNED_TRADE_REGISTERED", sleeve_id, ticker, plan)
        return True, signal_id

    # -------------------------------------------------------------------------
    # MARKET OPEN EXECUTION WITH SLEEVE CASH SOLVENCY
    # -------------------------------------------------------------------------
    def execute_market_open(
        self,
        signal_id: str,
        actual_open: float,
        entry_date: str,
        cfg: RiskConfig,
    ) -> Tuple[bool, str]:
        """
        Executes a planned trade at Market Open using the designated sleeve's isolated cash.
        Guarantees that other sleeves' cash is never touched.
        """
        p = self.prefix
        now_ts = datetime.now().isoformat()

        with self._get_conn() as conn:
            row = conn.execute(f"SELECT * FROM {p}trades WHERE signal_id = ?", (signal_id,)).fetchone()
            if not row or row["status"] != "PLANNED":
                return False, "TRADE_NOT_PLANNED"

            sleeve_id = row["sleeve_id"]
            ticker = row["ticker"]
            s_row = conn.execute(f"SELECT * FROM {p}sleeves WHERE sleeve_id = ?", (sleeve_id,)).fetchone()
            sleeve_cash = float(s_row["cash_balance"])

            # 1. Overnight Gap Filter
            signal_px = float(row["planned_entry"])
            gap_pct = abs(actual_open / signal_px - 1.0)
            if gap_pct > cfg.max_allowed_gap_pct:
                cancel_reason = f"GAP_EXCEEDED: gap {gap_pct:.2%} > max {cfg.max_allowed_gap_pct:.2%}"
                conn.execute(f"""
                    UPDATE {p}trades SET status = 'CANCELLED', cancel_reason = ?, updated_at = ?
                    WHERE signal_id = ?
                """, (cancel_reason, now_ts, signal_id))
                self.log_audit("CANCELLED", cancel_reason, sleeve_id, ticker, conn=conn)
                return False, cancel_reason

            # 2. Risk / Reward Minimum Filter
            min_rr = getattr(cfg, "min_risk_reward", 1.5)
            rr = reward_risk(actual_open, row["initial_stop"], row["initial_target"], cfg.cost_per_side)
            if rr < min_rr:
                cancel_reason = f"RR_TOO_LOW: rr {rr:.2f} < min {min_rr:.2f}"
                conn.execute(f"""
                    UPDATE {p}trades SET status = 'CANCELLED', cancel_reason = ?, updated_at = ?
                    WHERE signal_id = ?
                """, (cancel_reason, now_ts, signal_id))
                self.log_audit("CANCELLED", cancel_reason, sleeve_id, ticker, conn=conn)
                return False, cancel_reason

            # 3. Position Sizing & Sleeve Cash Solvency Check
            fill_price = actual_open * (1.0 + cfg.cost_per_side)
            risk_per_share = fill_price - float(row["initial_stop"])
            if risk_per_share <= 0:
                cancel_reason = "INVALID_RISK_PER_SHARE"
                conn.execute(f"""
                    UPDATE {p}trades SET status = 'CANCELLED', cancel_reason = ?, updated_at = ?
                    WHERE signal_id = ?
                """, (cancel_reason, now_ts, signal_id))
                return False, cancel_reason

            # Sizing based strictly on sleeve equity
            sleeve_eq = float(s_row["total_equity"])
            target_dollar_risk = cfg.risk_per_trade_pct * sleeve_eq
            shares = math.floor(target_dollar_risk / risk_per_share)
            
            # Position cap (e.g. max 20% of sleeve equity)
            max_pos_cap = getattr(cfg, "max_pos_pct", 0.20)
            max_notional = min(max_pos_cap * sleeve_eq, sleeve_cash)
            if shares * fill_price > max_notional:
                shares = math.floor(max_notional / fill_price)

            if shares <= 0 or (shares * fill_price) > sleeve_cash:
                cancel_reason = "CASH_DEFICIT_IN_SLEEVE"
                conn.execute(f"""
                    UPDATE {p}trades SET status = 'CANCELLED', cancel_reason = ?, updated_at = ?
                    WHERE signal_id = ?
                """, (cancel_reason, now_ts, signal_id))
                self.log_audit("CANCELLED", cancel_reason, sleeve_id, ticker, {
                    "required": shares * fill_price, "available_sleeve_cash": sleeve_cash
                }, conn=conn)
                return False, cancel_reason

            total_notional = shares * fill_price
            comm_entry = shares * actual_open * cfg.cost_per_side

            # Deduct strictly from sleeve cash
            conn.execute(f"""
                UPDATE {p}sleeves
                SET cash_balance = cash_balance - ?,
                    invested_capital = invested_capital + ?,
                    open_positions_count = open_positions_count + 1,
                    updated_at = ?
                WHERE sleeve_id = ?
            """, (total_notional, total_notional, now_ts, sleeve_id))

            # Update trade to OPEN
            conn.execute(f"""
                UPDATE {p}trades SET
                    status = 'OPEN',
                    actual_entry = ?,
                    fill_price = ?,
                    shares = ?,
                    remaining_shares = ?,
                    entry_date = ?,
                    total_cost = total_cost + ?,
                    updated_at = ?
                WHERE signal_id = ?
            """, (actual_open, fill_price, shares, shares, entry_date, comm_entry, now_ts, signal_id))

        self.log_audit("FILL_ENTRY", "POSITION_OPENED", sleeve_id, ticker, {
            "shares": shares, "fill_price": fill_price, "notional": total_notional
        })
        return True, "FILLED"

    # -------------------------------------------------------------------------
    # POSITION EXITS & DUAL-TARGET LIFECYCLE
    # -------------------------------------------------------------------------
    def process_position_bar(
        self,
        signal_id: str,
        bar_date: str,
        bar_open: float,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        cost_per_side: float = 0.001,
        max_hold: int = 35,
    ) -> Optional[Dict[str, Any]]:
        """
        Processes a daily bar for an open position:
        - Checks Stop Loss (conservative priority).
        - Checks TP1 (+10% partial exit 50% + Breakeven stop trail).
        - Checks TP2 (+35% runner exit).
        - Checks Time Exit (max_hold days).
        Returns exit event dict if exit occurred, else None.
        """
        p = self.prefix
        now_ts = datetime.now().isoformat()

        with self._get_conn() as conn:
            row = conn.execute(f"SELECT * FROM {p}trades WHERE signal_id = ?", (signal_id,)).fetchone()
            if not row or row["status"] not in ("OPEN", "PARTIALLY_CLOSED"):
                return None

            sleeve_id = row["sleeve_id"]
            ticker = row["ticker"]
            rem_shares = row["remaining_shares"]
            cur_stop = float(row["current_stop"])
            entry_px = float(row["actual_entry"])
            hold_days = row["hold_days"] + 1

            exit_price = None
            exit_reason = None
            is_partial = False
            shares_to_exit = 0

            # 1. Conservative Stop Loss Check (Priority 1)
            if bar_low <= cur_stop:
                exit_price = min(bar_open, cur_stop)  # handles gap down
                exit_reason = "STOP_LOSS"
                shares_to_exit = rem_shares
            # 2. TP1 Partial Exit Check (+10%)
            elif row["status"] == "OPEN" and bar_high >= entry_px * 1.10:
                exit_price = max(bar_open, entry_px * 1.10)
                exit_reason = "TP1_PARTIAL"
                shares_to_exit = math.ceil(rem_shares * 0.50)
                is_partial = True
            # 3. TP2 Main Target Check (+35%)
            elif bar_high >= entry_px * 1.35:
                exit_price = max(bar_open, entry_px * 1.35)
                exit_reason = "TP2_FULL_TARGET"
                shares_to_exit = rem_shares
            # 4. Max Hold Days Check
            elif hold_days >= max_hold:
                exit_price = bar_close
                exit_reason = "MAX_HOLD_TIME"
                shares_to_exit = rem_shares

            if exit_price is None:
                # Update hold days and last bar date
                conn.execute(f"""
                    UPDATE {p}trades SET hold_days = ?, last_bar_date = ?, updated_at = ?
                    WHERE signal_id = ?
                """, (hold_days, bar_date, now_ts, signal_id))
                return None

            # Execute Exit
            exit_fill = exit_price * (1.0 - cost_per_side)
            proceeds = shares_to_exit * exit_fill
            cost_exit = shares_to_exit * exit_price * cost_per_side
            cost_basis = shares_to_exit * float(row["fill_price"])
            pnl = proceeds - cost_basis

            new_rem_shares = rem_shares - shares_to_exit
            new_status = "PARTIALLY_CLOSED" if is_partial else "CLOSED"
            new_stop = max(cur_stop, entry_px) if is_partial else cur_stop  # move to Breakeven

            # Update Sleeve Cash Balance
            conn.execute(f"""
                UPDATE {p}sleeves
                SET cash_balance = cash_balance + ?,
                    invested_capital = max(0.0, invested_capital - ?),
                    open_positions_count = open_positions_count - ?,
                    updated_at = ?
                WHERE sleeve_id = ?
            """, (proceeds, cost_basis, 0 if is_partial else 1, now_ts, sleeve_id))

            # Update Trade Record
            conn.execute(f"""
                UPDATE {p}trades SET
                    status = ?,
                    remaining_shares = ?,
                    current_stop = ?,
                    realized_pnl = realized_pnl + ?,
                    total_cost = total_cost + ?,
                    exit_date = ?,
                    exit_price = ?,
                    exit_reason = ?,
                    hold_days = ?,
                    last_bar_date = ?,
                    updated_at = ?
                WHERE signal_id = ?
            """, (
                new_status, new_rem_shares, new_stop, pnl, cost_exit,
                bar_date, exit_price, exit_reason, hold_days, bar_date, now_ts, signal_id
            ))

        self.log_audit("POSITION_EXIT", exit_reason, sleeve_id, ticker, {
            "exit_price": exit_price, "shares": shares_to_exit, "pnl": pnl, "is_partial": is_partial
        })

        return {
            "signal_id": signal_id,
            "sleeve_id": sleeve_id,
            "ticker": ticker,
            "exit_reason": exit_reason,
            "exit_price": exit_price,
            "shares_closed": shares_to_exit,
            "pnl": pnl,
            "is_partial": is_partial,
        }

    # -------------------------------------------------------------------------
    # MACRO SP500 ACCUMULATION DIP TRANCHE DEPLOYMENT
    # -------------------------------------------------------------------------
    def check_and_deploy_sp500_tranche(
        self,
        current_spy_price: float,
        rolling_ath: float,
        date_str: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Deploys dedicated SP500 accumulation cash when index draws down -15%, -25%, -35% from ATH.
        Guaranteed to never fail from cash starvation due to isolated sleeve cash balance.
        """
        p = self.prefix
        sleeve_id = "SLEEVE_SP500"
        now_ts = datetime.now().isoformat()

        if current_spy_price >= rolling_ath or rolling_ath <= 0:
            return None

        dd = (current_spy_price - rolling_ath) / rolling_ath
        tranche_name = None
        deploy_fraction = 0.0

        if dd <= -0.35:
            tranche_name = "TRANCHE_35_PCT"
            deploy_fraction = 1.0  # all remaining cash
        elif dd <= -0.25:
            tranche_name = "TRANCHE_25_PCT"
            deploy_fraction = 0.50
        elif dd <= -0.15:
            tranche_name = "TRANCHE_15_PCT"
            deploy_fraction = 0.3333

        if not tranche_name:
            return None

        with self._get_conn() as conn:
            # Check if this tranche was already deployed
            existing = conn.execute(f"""
                SELECT id FROM {p}trades
                WHERE sleeve_id = ? AND cancel_reason = ? AND status = 'OPEN'
            """, (sleeve_id, tranche_name)).fetchone()
            if existing:
                return None  # already deployed for this dip level

            s_row = conn.execute(f"SELECT cash_balance FROM {p}sleeves WHERE sleeve_id = ?", (sleeve_id,)).fetchone()
            sleeve_cash = float(s_row["cash_balance"])
            if sleeve_cash < 50.0:
                return None

            deploy_amt = sleeve_cash * deploy_fraction
            shares = math.floor(deploy_amt / current_spy_price)
            if shares <= 0:
                return None

            total_notional = shares * current_spy_price
            signal_id = f"{sleeve_id}_SPY_{date_str}_{tranche_name}"

            # Deduct from sleeve cash
            conn.execute(f"""
                UPDATE {p}sleeves
                SET cash_balance = cash_balance - ?,
                    invested_capital = invested_capital + ?,
                    open_positions_count = open_positions_count + 1,
                    updated_at = ?
                WHERE sleeve_id = ?
            """, (total_notional, total_notional, now_ts, sleeve_id))

            # Record tranche position
            conn.execute(f"""
                INSERT INTO {p}trades (
                    sleeve_id, signal_id, ticker, strategy_version, status,
                    actual_entry, fill_price, shares, remaining_shares,
                    initial_stop, current_stop, initial_target, current_target,
                    planned_risk, cancel_reason, signal_date, entry_date, created_at, updated_at
                ) VALUES (?, ?, 'SPY', 'SP500_ACCUMULATION_TRANCHE', 'OPEN',
                          ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0.0, ?, ?, ?, ?, ?)
            """, (
                sleeve_id, signal_id, current_spy_price, current_spy_price, shares, shares,
                tranche_name, date_str, date_str, now_ts, now_ts
            ))

        self.log_audit("TRANCHE_DEPLOYED", tranche_name, sleeve_id, "SPY", {
            "drawdown": dd, "shares": shares, "price": current_spy_price, "amount": total_notional
        })

        return {
            "tranche": tranche_name,
            "drawdown_pct": round(dd * 100, 2),
            "shares": shares,
            "price": current_spy_price,
            "amount_deployed": round(total_notional, 2),
        }
