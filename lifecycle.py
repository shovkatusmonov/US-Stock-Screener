"""
lifecycle.py - virtual portfelni kunma-kun yurgizish (run_daily.py shu modulni chaqiradi).

Har bir savdo kuni d uchun, xronologik tartibda:
  1) Signal kuni < d bo'lgan PLANNED rejalardan, kirish sessiyasi (signaldan keyingi birinchi savdo kuni) d bo'lganlari
     d kunining haqiqiy OCHILISH narxida TradeManager.execute_market_open orqali bajariladi
     (gap +-3%, R/R, Heat, sig'im, naqd pul tekshiruvlari bilan) -> OPEN yoki CANCELLED.
     Kirish sessiyasida ticker narxi bo'lmasa -> CANCELLED (EXPIRED_NO_DATA). Reja faqat BIR sessiya amal qiladi.
  2) Barcha ochiq pozitsiyalarga d bar'i qo'llanadi (Stop birinchi, gap, time exit).
Qayta ishga tushirish xavfsiz (idempotent): qayta ishlangan kunlar o'tkazib yuboriladi.
"""
import sqlite3
from typing import Dict, List, Optional

from risk_manager import RiskConfig
from trade_manager import TradeManager


def _spy_dates(conn: sqlite3.Connection) -> List[str]:
    return [r[0] for r in conn.execute("SELECT date FROM prices WHERE ticker='SPY' ORDER BY date")]


def _bar(conn, ticker: str, date: str) -> Optional[dict]:
    r = conn.execute("SELECT open, high, low, close FROM prices WHERE ticker=? AND date=?", (ticker, date)).fetchone()
    if not r or any(v is None for v in r):
        return None
    return {"open": float(r[0]), "high": float(r[1]), "low": float(r[2]), "close": float(r[3])}


def _equity_asof(tm: TradeManager, conn, date: str) -> float:
    """Naqd + ochiq pozitsiyalarning `date` dan OLDINGI oxirgi yopilish bo'yicha qiymati."""
    eq = tm.get_cash()
    with tm._get_conn() as c:
        rows = c.execute("SELECT ticker, remaining_shares, fill_price FROM portfolio_trades "
                         "WHERE status IN ('OPEN','PARTIALLY_CLOSED')").fetchall()
    for r in rows:
        p = conn.execute("SELECT close FROM prices WHERE ticker=? AND date<? ORDER BY date DESC LIMIT 1",
                         (r["ticker"], date)).fetchone()
        eq += r["remaining_shares"] * (float(p[0]) if p else (r["fill_price"] or 0.0))
    return eq


def advance_portfolio(tm: TradeManager, conn: sqlite3.Connection, latest_date: str,
                      cfg: RiskConfig, max_hold: int = 35) -> Dict[str, List[dict]]:
    """latest_date gacha portfelni yuritadi. Hodisalar ro'yxatini qaytaradi."""
    ev: Dict[str, List[dict]] = {"filled": [], "cancelled": [], "closed": []}
    dates = _spy_dates(conn)
    if not dates:
        return ev
    with tm._get_conn() as c:
        planned = c.execute("SELECT signal_date FROM portfolio_trades WHERE status='PLANNED'").fetchall()
        opened = c.execute("SELECT entry_date, last_bar_date FROM portfolio_trades "
                           "WHERE status IN ('OPEN','PARTIALLY_CLOSED')").fetchall()
    starts = []
    for r in planned:   # signaldan keyingi birinchi sessiya
        nxt = [d for d in dates if d > r["signal_date"]]
        if nxt:
            starts.append(nxt[0])
    for r in opened:    # hali qayta ishlanmagan birinchi bar
        base = r["last_bar_date"]
        nxt = [d for d in dates if (d > base if base else d >= r["entry_date"])]
        if nxt:
            starts.append(nxt[0])
    if not starts:
        return ev

    for d in [x for x in dates if min(starts) <= x <= latest_date]:
        with tm._get_conn() as c:
            plans = c.execute("SELECT signal_id, ticker, signal_date FROM portfolio_trades "
                              "WHERE status='PLANNED' ORDER BY signal_date, id").fetchall()
        for p in plans:
            nxt = [x for x in dates if x > p["signal_date"]]
            if not nxt or nxt[0] > d:
                continue                                  # kirish sessiyasi hali kelmagan
            bar = _bar(conn, p["ticker"], d) if nxt[0] == d else None
            if bar is None:                               # sessiya o'tib ketgan yoki narx yo'q -> muddati tugadi
                with tm._get_conn() as c:
                    c.execute("UPDATE portfolio_trades SET status='CANCELLED', cancel_reason='EXPIRED_NO_DATA' "
                              "WHERE signal_id=?", (p["signal_id"],))
                ev["cancelled"].append({"ticker": p["ticker"], "signal_id": p["signal_id"], "reason": "EXPIRED_NO_DATA"})
                continue
            ok, msg = tm.execute_market_open(p["signal_id"], bar["open"], d, cfg,
                                             current_equity=_equity_asof(tm, conn, d))
            rec = {"ticker": p["ticker"], "signal_id": p["signal_id"], "date": d, "open": bar["open"], "reason": msg}
            (ev["filled"] if ok else ev["cancelled"]).append(rec)

        with tm._get_conn() as c:
            tick = [r[0] for r in c.execute("SELECT DISTINCT ticker FROM portfolio_trades "
                                            "WHERE status IN ('OPEN','PARTIALLY_CLOSED')")]
        ohlc = {t: b for t in tick if (b := _bar(conn, t, d))}
        for a in tm.update_daily_positions(d, ohlc, max_hold=max_hold, cost_per_side=cfg.cost_per_side):
            if a["action"] == "CLOSED":
                a["date"] = d
                ev["closed"].append(a)
    return ev
