"""
portfolio_tracker.py - Virtual portfel tahlili, metrikalari va jamlangan hisoboti.
Eskirgan narxlar (STALE_DATA) va stop buzilishlari (STOP_BREACHED) alohida ajratiladi.
"""
from typing import Dict
from trade_manager import TradeManager


class PortfolioTracker:
    def __init__(self, manager: TradeManager):
        self.mgr = manager

    def get_portfolio_snapshot(self, current_prices: Dict[str, float]) -> dict:
        cash = self.mgr.get_cash()
        with self.mgr._get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM portfolio_trades WHERE status IN ('OPEN', 'PARTIALLY_CLOSED')
            """).fetchall()

            realized_row = conn.execute("""
                SELECT SUM(realized_pnl) AS total_realized FROM portfolio_trades WHERE status IN ('CLOSED', 'PARTIALLY_CLOSED')
            """).fetchone()

        total_realized = float(realized_row["total_realized"]) if realized_row["total_realized"] else 0.0

        open_market_val = 0.0
        total_open_risk = 0.0
        stale_positions = []
        breached_positions = []
        positions = []

        for r in rows:
            t = r["ticker"]
            rem_shares = r["remaining_shares"]
            is_stale = t not in current_prices
            curr_p = current_prices.get(t, None)

            if is_stale:
                stale_positions.append(t)
                curr_p = r["actual_entry"]  # faqat baholash uchun vaqtinchalik

            mkt_val = rem_shares * curr_p
            open_market_val += mkt_val

            # Stop tekshiruvi
            if curr_p <= r["current_stop"]:
                breached_positions.append(t)
                # Stop buzilganda xavf 0 emas, balki real zararni ko'rsatishi shart
                pos_risk = rem_shares * abs(curr_p - r["current_stop"])
            else:
                pos_risk = rem_shares * (curr_p - r["current_stop"])

            total_open_risk += pos_risk
            unrealized = rem_shares * (curr_p - r["fill_price"])

            positions.append({
                "ticker": t,
                "shares": rem_shares,
                "entry": r["actual_entry"],
                "current_price": curr_p,
                "stop": r["current_stop"],
                "target": r["current_target"],
                "market_value": mkt_val,
                "unrealized_pnl": unrealized,
                "dollar_risk": pos_risk,
                "hold_days": r["hold_days"],
                "is_stale": is_stale,
                "is_breached": curr_p <= r["current_stop"],
            })

        total_equity = cash + open_market_val
        heat_pct = (total_open_risk / total_equity) if total_equity > 0 else 0.0
        positions.sort(key=lambda x: -x["dollar_risk"])

        return {
            "initial_cash": self.mgr.initial_cash,
            "cash": cash,
            "open_market_val": open_market_val,
            "total_equity": total_equity,
            "total_realized_pnl": total_realized,
            "total_unrealized_pnl": sum(p["unrealized_pnl"] for p in positions),
            "total_open_risk": total_open_risk,
            "portfolio_heat_pct": heat_pct,
            "open_count": len(positions),
            "stale_count": len(stale_positions),
            "stale_tickers": stale_positions,
            "breached_count": len(breached_positions),
            "breached_tickers": breached_positions,
            "positions": positions,
        }

    def generate_report_text(self, current_prices: Dict[str, float]) -> str:
        s = self.get_portfolio_snapshot(current_prices)
        lines = [
            "=" * 85,
            "VIRTUAL PORTFEL STATUS HISOBOTI",
            "=" * 85,
            f"Boshlang'ich kapital: ${s['initial_cash']:>10.2f} | Joriy pul (Cash):    ${s['cash']:>10.2f}",
            f"Ochiq pozitsiyalar:   ${s['open_market_val']:>10.2f} | Umumiy kapital:     ${s['total_equity']:>10.2f}",
            f"Amalga oshgan PnL:    ${s['total_realized_pnl']:>10.2f} | Ochiq PnL:           ${s['total_unrealized_pnl']:>10.2f}",
            f"Ochiq xavf (Heat):    {s['portfolio_heat_pct']*100:>9.1f}% | Pozitsiyalar soni:  {s['open_count']:>10d}",
        ]

        if s["stale_count"] > 0:
            lines.append(f"[OGOHLANTIRISH] Eskirgan kotirovkalar ({s['stale_count']} ta): {', '.join(s['stale_tickers'])}")
        if s["breached_count"] > 0:
            lines.append(f"[KRITIK] Stop darajasi buzilgan pozitsiyalar ({s['breached_count']} ta): {', '.join(s['breached_tickers'])} (EXIT_REVIEW_REQUIRED)")

        lines.append("-" * 85)

        if not s["positions"]:
            lines.append("Hozirda ochiq pozitsiyalar mavjud emas.")
        else:
            header = f"{'Ticker':<7} | {'Lot':>5} | {'Kirish':>8} | {'Joriy':>8} | {'Stop':>8} | {'Qiymat ($)':>10} | {'Ochiq PnL':>10} | {'Risk ($)':>8} | {'Holat':<12}"
            lines.append(header)
            lines.append("-" * len(header))
            for p in s["positions"]:
                status_str = "BREACHED" if p["is_breached"] else ("STALE" if p["is_stale"] else "OK")
                lines.append(
                    f"{p['ticker']:<7} | {p['shares']:>5d} | ${p['entry']:>7.2f} | ${p['current_price']:>7.2f} | "
                    f"${p['stop']:>7.2f} | ${p['market_value']:>9.2f} | ${p['unrealized_pnl']:>9.2f} | ${p['dollar_risk']:>7.2f} | {status_str:<12}"
                )
        lines.append("=" * 85)
        return "\n".join(lines)