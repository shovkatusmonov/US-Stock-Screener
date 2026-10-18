"""
test_portfolio_tracker.py - PortfolioTracker hisobotlari va metrikalari testlari.
"""
import pytest
from portfolio_tracker import PortfolioTracker
from risk_manager import RiskConfig
from trade_manager import TradeManager


@pytest.fixture
def pt(tmp_path):
    db_file = str(tmp_path / "test_screener.db")
    tm = TradeManager(db_path=db_file, initial_cash=10000.0)
    return PortfolioTracker(tm)


def test_portfolio_metrics_and_heat(pt):
    cfg = RiskConfig(max_allowed_gap_pct=0.03)
    plan = {
        "ticker": "AAPL", "signal_date": "2024-01-01", "signal_price": 100.0,
        "shares": 10, "planned_stop": 90.0, "planned_target": 135.0,
        "dollar_risk": 100.0
    }
    _, sig_id = pt.mgr.add_planned_trade(plan)
    pt.mgr.execute_market_open(sig_id, 100.0, "2024-01-02", cfg)

    # Joriy narx $110 bo'lganda portfel hisoboti
    snap = pt.get_portfolio_snapshot({"AAPL": 110.0})
    assert snap["open_count"] == 1
    assert snap["total_unrealized_pnl"] > 0
    assert snap["portfolio_heat_pct"] > 0.0

    rep = pt.generate_report_text({"AAPL": 110.0})
    assert "VIRTUAL PORTFEL STATUS HISOBOTI" in rep
    assert "AAPL" in rep