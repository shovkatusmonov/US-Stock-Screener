"""
Backtest dvigateli (portfel darajasida).
Konservativ qoida: Bir kunda Stop va TP tegilganda STOP har doim birinchi o'rinda.
TP1 ixtiyoriy qisman yopish (Dual-Target), TP2 esa asosiy Trend Runner targeti.
"""
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd


@dataclass
class Params:
    initial_equity: float = 10_000.0
    risk_pct: float = 0.01        # bir savdoda portfelning ulushi sifatida risk
    max_positions: int = 8
    max_pos_pct: float = 0.20     # bitta pozitsiya maksimal ulushi
    stop_atr: float = 2.0         # stop = kirish - stop_atr * ATR
    tp1_pct: Optional[float] = None  # None: to'liq bitta target; float bo'lsa: 50% qisman yopish
    tp_pct: float = 0.35          # Asosiy target: +35%
    max_hold: int = 35            # maksimal ushlash muddati (savdo kunlari)
    cost_per_side: float = 0.001  # 0.1% har tomonga
    min_notional: float = 20.0
    # Jonli tizim qoidalari (risk_manager.evaluate_market_open_entry bilan bir xil).
    # None / False = o'chiq (eski backtest xatti-harakati).
    max_gap_pct: Optional[float] = None       # ochilish signal yopilishidan ±shu qadar og'sa -> bekor
    min_rr: Optional[float] = None            # xarajatlar bilan Risk/Reward minimumi
    max_heat_pct: Optional[float] = None      # umumiy ochiq risk limiti (equity ulushi)
    integer_shares: bool = False              # faqat butun aksiyalar

    @classmethod
    def live(cls, **kw) -> "Params":
        """Jonli tizim (RiskConfig) qoidalari yoqilgan parametrlar."""
        base = dict(max_gap_pct=0.03, min_rr=1.5, max_heat_pct=0.05, integer_shares=True)
        base.update(kw)
        return cls(**base)


def run_backtest(ohlc: dict, signals: dict, dates, p: Params):
    dates = pd.DatetimeIndex(dates)
    cand_by_date = defaultdict(list)
    for t, sg in signals.items():
        hits = sg[sg["signal"]]
        for d, row in hits.iterrows():
            if np.isfinite(row["atr"]) and row["atr"] > 0 and np.isfinite(row["rank"]):
                cand_by_date[d].append((float(row["rank"]), t, float(row["atr"])))

    cash = p.initial_equity
    last_equity = p.initial_equity
    skipped = defaultdict(int)  # jonli qoidalar bo'yicha bekor qilingan kirishlar sanagichi
    positions: dict = {}
    pending: list = []
    trades: list = []
    curve = []

    def close_shares(t, d, i, shares_to_close, exit_raw, reason, is_partial=False):
        nonlocal cash
        pos = positions[t]
        exit_fill = exit_raw * (1 - p.cost_per_side)
        cash += shares_to_close * exit_fill
        pnl = shares_to_close * (exit_fill - pos["entry_fill"])
        ret = exit_fill / pos["entry_fill"] - 1

        trades.append({
            "ticker": t,
            "entry_date": pos["entry_date"],
            "exit_date": d,
            "entry_price": pos["entry_raw"],
            "exit_price": exit_raw,
            "shares": shares_to_close,
            "pnl": pnl,
            "ret": ret,
            "reason": reason,
            "hold_days": i - pos["entry_idx"],
        })

        if is_partial:
            pos["remaining_shares"] -= shares_to_close
            pos["tp1_hit"] = True
            pos["stop"] = max(pos["stop"], pos["entry_raw"])
        else:
            del positions[t]

    n = len(dates)
    for i, d in enumerate(dates):
        # 1) Kirishlar (Market Open)
        for t, atr_v in pending:
            if len(positions) >= p.max_positions:
                break
            if t in positions or t not in ohlc or d not in ohlc[t].index:
                continue
            o = ohlc[t].at[d, "open"]
            if not np.isfinite(o) or o <= 0:
                continue
            if p.max_gap_pct is not None and i > 0:
                sig_px = ohlc[t]["close"].get(dates[i - 1], np.nan)
                if np.isfinite(sig_px) and sig_px > 0 and abs(o / sig_px - 1.0) > p.max_gap_pct:
                    skipped["GAP"] += 1
                    continue
            stop = o - p.stop_atr * atr_v
            if stop <= 0:
                continue
            fill = o * (1 + p.cost_per_side)
            risk_per_share = fill - stop
            if p.min_rr is not None:
                rr = (o * (1 + p.tp_pct) * (1 - p.cost_per_side) - fill) / risk_per_share
                if rr < p.min_rr:
                    skipped["RR"] += 1
                    continue
            if p.max_heat_pct is not None:
                open_risk = sum(max(0.0, q["entry_fill"] - q["stop"]) * q["remaining_shares"]
                                for q in positions.values())
                heat = open_risk / last_equity if last_equity > 0 else 0.0
                if heat + p.risk_pct > p.max_heat_pct + 1e-6:
                    skipped["HEAT"] += 1
                    continue
            shares = p.risk_pct * last_equity / risk_per_share
            max_notional = min(p.max_pos_pct * last_equity, cash)
            if shares * fill > max_notional:
                shares = max_notional / fill
            if p.integer_shares:
                shares = math.floor(shares + 1e-9)
            if shares * fill < p.min_notional:
                skipped["SIZE"] += 1
                continue
            cash -= shares * fill
            positions[t] = {
                "shares": shares,
                "remaining_shares": shares,
                "entry_fill": fill,
                "entry_raw": o,
                "stop": stop,
                "tp1_target": o * (1 + p.tp1_pct) if p.tp1_pct else None,
                "tp1_hit": False,
                "target": o * (1 + p.tp_pct),
                "entry_date": d,
                "entry_idx": i,
                "last_close": o,
            }
        pending = []

        # 2) Chiqishlar
        for t in list(positions):
            if d not in ohlc[t].index:
                continue
            r = ohlc[t].loc[d]
            o, h, l, c = r["open"], r["high"], r["low"], r["close"]
            pos = positions[t]

            exit_raw = reason = None

            # 2.1) OCHILISH GAPLARI (Kirish kunidan keyingi kunlarda)
            if pos["entry_idx"] != i:
                if o <= pos["stop"]:
                    exit_raw, reason = o, "SL_GAP"
                elif o >= pos["target"]:
                    exit_raw, reason = o, "TP_GAP"
                elif pos["tp1_target"] is not None and not pos["tp1_hit"] and o >= pos["tp1_target"]:
                    half = pos["remaining_shares"] / 2.0
                    close_shares(t, d, i, half, o, "TP1_GAP", is_partial=True)

            if exit_raw is not None:
                close_shares(t, d, i, pos["remaining_shares"], exit_raw, reason, is_partial=False)
                continue

            # 2.2) INTRADAY CHIQISHLAR (Qat'iy Konservativ: Stop birinchi o'rinda)
            pos = positions[t]
            if l <= pos["stop"]:
                exit_raw, reason = pos["stop"], "SL"
            elif h >= pos["target"]:
                exit_raw, reason = pos["target"], "TP"
            elif pos["tp1_target"] is not None and not pos["tp1_hit"] and h >= pos["tp1_target"]:
                half = pos["remaining_shares"] / 2.0
                close_shares(t, d, i, half, pos["tp1_target"], "TP1", is_partial=True)
                pos = positions.get(t)
                # Konservativ qoida: TP1 kunida narx yangi breakeven stop darajasiga
                # ham tushgan bo'lsa, qolgan qismni shu stopda yopamiz. OHLC yo'li
                # noma'lum bo'lgani uchun bu optimistik natijani oldini oladi.
                if pos is not None and l <= pos["stop"]:
                    exit_raw, reason = pos["stop"], "SL_BE_AFTER_TP1"

            # 2.3) VAQT BO'YICHA CHIQISH (Time exit)
            if pos is not None and exit_raw is None and (i - pos["entry_idx"] >= p.max_hold):
                exit_raw, reason = c, "TIME"

            if exit_raw is not None and t in positions:
                close_shares(t, d, i, positions[t]["remaining_shares"], exit_raw, reason, is_partial=False)
            elif t in positions:
                positions[t]["last_close"] = c

        # 3) Equity va Invested hisoblash
        mkt = sum(pos["remaining_shares"] * pos["last_close"] for pos in positions.values())
        equity = cash + mkt
        last_equity = equity
        curve.append((d, equity, mkt / equity if equity > 0 else 0.0))

        # 4) Ertangi kun uchun signallar (Deterministik saralash)
        if i < n - 1:
            cands = [(rk, t, a) for rk, t, a in cand_by_date.get(d, []) if t not in positions]
            cands.sort(key=lambda x: (-x[0], x[1]))
            pending = [(t, a) for _, t, a in cands]

    if n:
        last_i, last_d = n - 1, dates[-1]
        for t in list(positions):
            close_shares(t, last_d, last_i, positions[t]["remaining_shares"], positions[t]["last_close"], "END", is_partial=False)
        curve[-1] = (last_d, cash, 0.0)

    tdf = pd.DataFrame(trades)
    cdf = pd.DataFrame(curve, columns=["date", "equity", "invested"]).set_index("date")
    cdf.attrs["skipped"] = dict(skipped)
    return tdf, cdf