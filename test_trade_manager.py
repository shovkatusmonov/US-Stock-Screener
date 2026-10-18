"""
test_trade_manager.py - TradeManager birlik va integratsiya testlari.
"""
import pytest
from risk_manager import RiskConfig
from trade_manager import TradeManager


@pytest.fixture
def tm(tmp_path):
    db_file = str(tmp_path / "test_screener.db")
    return TradeManager(db_path=db_file, initial_cash=10000.0)


def test_add_planned_and_prevent_duplicate(tm):
    plan = {
        "ticker": "AAPL", "signal_date": "2024-01-01", "signal_price": 100.0,
        "shares": 10, "planned_stop": 90.0, "planned_target": 135.0,
        "dollar_risk": 100.0
    }
    ok, sig_id = tm.add_planned_trade(plan)
    assert ok is True
    # Takroriy signal qo'shish bloklanishi kerak
    ok2, err = tm.add_planned_trade(plan)
    assert ok2 is False
    assert "Takroriy pozitsiya taqiqlandi" in err


def test_execute_market_open_gap_cancel(tm):
    cfg = RiskConfig(max_allowed_gap_pct=0.03)
    plan = {
        "ticker": "AAPL", "signal_date": "2024-01-01", "signal_price": 100.0,
        "shares": 10, "planned_stop": 90.0, "planned_target": 135.0,
        "dollar_risk": 100.0
    }
    _, sig_id = tm.add_planned_trade(plan)
    # 5% ga sakrab ochilsa (105.0), bekor qilinishi shart
    ok, msg = tm.execute_market_open(sig_id, 105.0, "2024-01-02", cfg)
    assert ok is False
    assert "CANCELLED_GAP_LIMIT" in msg


def test_daily_stop_loss_trigger(tm):
    cfg = RiskConfig(max_allowed_gap_pct=0.03)
    plan = {
        "ticker": "AAPL", "signal_date": "2024-01-01", "signal_price": 100.0,
        "shares": 10, "planned_stop": 90.0, "planned_target": 135.0,
        "dollar_risk": 100.0
    }
    _, sig_id = tm.add_planned_trade(plan)
    tm.execute_market_open(sig_id, 100.0, "2024-01-02", cfg)

    # 3-kun narx stopdan pastga tushdi
    ohlc = {"AAPL": {"open": 95.0, "high": 96.0, "low": 89.0, "close": 91.0}}
    acts = tm.update_daily_positions("2024-01-03", ohlc)
    assert len(acts) == 1
    assert acts[0]["action"] == "CLOSED"
    assert acts[0]["reason"] == "SL"


def test_daily_gap_down_execution(tm):
    cfg = RiskConfig(max_allowed_gap_pct=0.03)
    plan = {
        "ticker": "AAPL", "signal_date": "2024-01-01", "signal_price": 100.0,
        "shares": 10, "planned_stop": 90.0, "planned_target": 135.0,
        "dollar_risk": 100.0
    }
    _, sig_id = tm.add_planned_trade(plan)
    tm.execute_market_open(sig_id, 100.0, "2024-01-02", cfg)

    # 3-kun ochilishning o'zi stopdan pastda (Gap-down 85.0)
    ohlc = {"AAPL": {"open": 85.0, "high": 87.0, "low": 84.0, "close": 86.0}}
    acts = tm.update_daily_positions("2024-01-03", ohlc)
    assert len(acts) == 1
    assert acts[0]["reason"] == "SL_GAP"


def test_partial_close_workflow(tm):
    cfg = RiskConfig(max_allowed_gap_pct=0.03)
    plan = {
        "ticker": "AAPL", "signal_date": "2024-01-01", "signal_price": 100.0,
        "shares": 10, "planned_stop": 90.0, "planned_target": 135.0,
        "dollar_risk": 100.0
    }
    _, sig_id = tm.add_planned_trade(plan)
    tm.execute_market_open(sig_id, 100.0, "2024-01-02", cfg)

    # 4 dona aksiyani foydada qisman yopish
    ok, msg = tm.partial_close(sig_id, 4, 120.0, "2024-01-03")
    assert ok is True
    with tm._get_conn() as conn:
        r = conn.execute("SELECT status, remaining_shares, realized_pnl FROM portfolio_trades WHERE signal_id = ?", (sig_id,)).fetchone()
        assert r["status"] == "PARTIALLY_CLOSED"
        assert r["remaining_shares"] == 5
        assert r["realized_pnl"] > 0
