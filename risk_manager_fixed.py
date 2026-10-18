"""Risk planning and market-open validation for US Stock Screener.

This module intentionally does not place broker orders. It creates/validates
paper-trade plans and enforces portfolio limits supplied by the caller.
"""
from dataclasses import dataclass
from datetime import datetime
import math
from typing import Optional

import numpy as np


@dataclass
class RiskConfig:
    initial_equity: float = 10_000.0
    risk_per_trade_pct: float = 0.01
    max_portfolio_risk_pct: float = 0.05
    max_positions: int = 8
    max_pos_pct: float = 0.20
    max_allowed_gap_pct: float = 0.03
    min_risk_reward: float = 1.5
    stop_atr: float = 2.0
    tp_pct: float = 0.35
    cost_per_side: float = 0.001
    min_notional: float = 20.0


def _finite_positive(value) -> bool:
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def reward_risk(entry: float, stop: float, target: float,
                cost_per_side: float = 0.001) -> Optional[float]:
    """Return net reward/risk after estimated entry and exit costs, else None."""
    try:
        entry, stop, target, cost = map(float, (entry, stop, target, cost_per_side))
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(x) for x in (entry, stop, target, cost)):
        return None
    if entry <= 0 or stop <= 0 or target <= 0 or cost < 0 or cost >= 1:
        return None
    entry_fill = entry * (1.0 + cost)
    exit_fill = target * (1.0 - cost)
    risk = entry_fill - stop
    reward = exit_fill - entry_fill
    if risk <= 0 or reward < 0:
        return None
    return reward / risk


def create_trade_plan(ticker: str, signal_close: float, atr: float, rank: float,
                      signal_date, cfg: RiskConfig, current_equity: float,
                      current_portfolio_risk: float):
    """Build a planned trade using the signal close; no order is submitted."""
    if not _finite_positive(signal_close):
        return None, "Yaroqsiz signal narxi"
    if not _finite_positive(atr):
        return None, "Yaroqsiz ATR"
    if not _finite_positive(current_equity):
        return None, "Joriy equity yaroqsiz"
    try:
        current_portfolio_risk = float(current_portfolio_risk)
    except (TypeError, ValueError, OverflowError):
        return None, "Joriy portfel riski yaroqsiz"
    if not math.isfinite(current_portfolio_risk) or current_portfolio_risk < 0:
        return None, "Joriy portfel riski yaroqsiz"
    if current_portfolio_risk + cfg.risk_per_trade_pct > cfg.max_portfolio_risk_pct + 1e-9:
        return None, (
            "Umumiy portfel riski limiti to'lgan "
            f"(Hozirgi: {current_portfolio_risk * 100:.1f}%, "
            f"Limit: {cfg.max_portfolio_risk_pct * 100:.1f}%)"
        )

    signal_close = float(signal_close)
    atr = float(atr)
    current_equity = float(current_equity)
    stop = signal_close - cfg.stop_atr * atr
    if stop <= 0:
        return None, "Stop darajasi noldan past"
    target = signal_close * (1.0 + cfg.tp_pct)
    rr = reward_risk(signal_close, stop, target, cfg.cost_per_side)
    if rr is None:
        return None, "Risk/Reward hisoblab bo'lmadi"
    if rr < cfg.min_risk_reward:
        return None, f"Risk/Reward yetarli emas ({rr:.2f} < {cfg.min_risk_reward:.2f})"

    entry_fill_est = signal_close * (1.0 + cfg.cost_per_side)
    risk_per_share = entry_fill_est - stop
    if risk_per_share <= 0:
        return None, "Risk per share noto'g'ri"
    shares_by_risk = math.floor(cfg.risk_per_trade_pct * current_equity / risk_per_share)
    shares_by_position = math.floor(cfg.max_pos_pct * current_equity / entry_fill_est)
    shares = min(shares_by_risk, shares_by_position)
    if shares <= 0 or shares * entry_fill_est < cfg.min_notional:
        return None, "Pozitsiya notional qiymati juda kichik"

    actual_risk = shares * risk_per_share
    return {
        "ticker": ticker,
        "status": "PLANNED",
        "signal_date": str(signal_date),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "signal_price": signal_close,
        "atr": atr,
        "rank": float(rank),
        "planned_stop": float(stop),
        "planned_target": float(target),
        "min_open_allowed": float(signal_close * (1.0 - cfg.max_allowed_gap_pct)),
        "max_open_allowed": float(signal_close * (1.0 + cfg.max_allowed_gap_pct)),
        "min_open": float(signal_close * (1.0 - cfg.max_allowed_gap_pct)),
        "max_open": float(signal_close * (1.0 + cfg.max_allowed_gap_pct)),
        "shares": int(shares),
        "dollar_risk": float(actual_risk),
        "risk_pct": float(actual_risk / current_equity),
        # Both names are kept for compatibility with callers/tests.
        "rr": float(rr),
        "rr_ratio": float(rr),
    }, "OK"


def evaluate_market_open_entry(
    plan: dict,
    actual_open: float,
    cfg: RiskConfig,
    current_cash: float,
    current_equity: float,
    portfolio_heat: float = 0.0,
    open_positions_count: int = 0,
):
    """Validate a planned entry against gap, cash, position and portfolio risk limits.

    Supports both plan key styles used by the project:
    min_open_allowed/max_open_allowed and min_open/max_open.
    """
    if not _finite_positive(actual_open):
        return None, "CANCELLED: Ochilish narxi yaroqsiz"
    try:
        signal_price = float(plan.get("signal_price", plan.get("planned_entry", 0)))
        low = float(plan.get("min_open_allowed", plan.get("min_open", signal_price * (1 - cfg.max_allowed_gap_pct))))
        high = float(plan.get("max_open_allowed", plan.get("max_open", signal_price * (1 + cfg.max_allowed_gap_pct))))
        current_cash = float(current_cash)
        current_equity = float(current_equity)
        portfolio_heat = float(portfolio_heat or 0.0)
        open_positions_count = int(open_positions_count or 0)
    except (TypeError, ValueError, OverflowError):
        return None, "CANCELLED: Reja yoki portfel qiymatlari yaroqsiz"

    actual_open = float(actual_open)
    if signal_price <= 0:
        return None, "CANCELLED: Signal narxi yaroqsiz"
    if actual_open < low or actual_open > high:
        gap_pct = (actual_open - signal_price) / signal_price
        return None, f"CANCELLED_GAP_LIMIT: Ochilish og'ishi {gap_pct * 100:+.2f}%"
    if not math.isfinite(current_equity) or current_equity <= 0:
        return None, "CANCELLED: Equity yaroqsiz"
    if not math.isfinite(current_cash) or current_cash <= 0:
        return None, "CANCELLED: Erkin pul yo'q"
    if open_positions_count >= cfg.max_positions:
        return None, "CANCELLED_POSITION_LIMIT: Ochiq pozitsiyalar limiti to'lgan"
    if not math.isfinite(portfolio_heat) or portfolio_heat < 0:
        return None, "CANCELLED: Portfolio heat yaroqsiz"
    if portfolio_heat + cfg.risk_per_trade_pct > cfg.max_portfolio_risk_pct + 1e-9:
        return None, "CANCELLED_PORTFOLIO_HEAT: Umumiy portfel riski limiti to'lgan"

    atr = plan.get("atr")
    if not _finite_positive(atr):
        # TradeManager reconstructs ATR from signal price and initial stop.
        planned_entry = plan.get("planned_entry", signal_price)
        initial_stop = plan.get("initial_stop")
        try:
            atr = (float(planned_entry) - float(initial_stop)) / cfg.stop_atr
        except (TypeError, ValueError, OverflowError, ZeroDivisionError):
            return None, "CANCELLED: ATR yaroqsiz"
    atr = float(atr)

    stop = actual_open - cfg.stop_atr * atr
    if stop <= 0:
        return None, "CANCELLED: Stop darajasi noldan past"
    target = actual_open * (1.0 + cfg.tp_pct)
    fill = actual_open * (1.0 + cfg.cost_per_side)
    risk_per_share = fill - stop
    if risk_per_share <= 0:
        return None, "CANCELLED: Risk per share yaroqsiz"

    risk_budget = cfg.risk_per_trade_pct * current_equity
    shares = min(
        math.floor(risk_budget / risk_per_share),
        math.floor(cfg.max_pos_pct * current_equity / fill),
        math.floor(current_cash / fill),
    )
    if shares <= 0 or shares * fill < cfg.min_notional:
        return None, "CANCELLED: Erkin pul yoki notional yetarli emas"

    actual_risk = shares * risk_per_share
    return {
        "ticker": plan.get("ticker", ""),
        "status": "READY_TO_BUY",
        "signal_price": signal_price,
        "actual_entry_price": actual_open,
        "fill_price": float(fill),
        "shares": int(shares),
        "stop": float(stop),
        "target": float(target),
        "planned_stop": float(stop),
        "planned_target": float(target),
        "dollar_risk": float(actual_risk),
        "notional": float(shares * fill),
        "rr": reward_risk(actual_open, stop, target, cfg.cost_per_side),
    }, "OK"
