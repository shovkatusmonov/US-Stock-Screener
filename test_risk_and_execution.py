import pytest
from risk_manager import RiskConfig, create_trade_plan, evaluate_market_open_entry


def test_gap_exceeds_threshold_cancels_trade():
    cfg = RiskConfig(max_allowed_gap_pct=0.03)
    plan, msg = create_trade_plan("AAPL", 100.0, 5.0, 1.0, "2024-01-01", cfg, 10000.0, 0.0)
    assert msg == "OK"
    result, message = evaluate_market_open_entry(plan, 104.0, cfg, 10000.0, 10000.0)
    assert result is None
    assert "CANCELLED_GAP_LIMIT" in message
    result, message = evaluate_market_open_entry(plan, 95.0, cfg, 10000.0, 10000.0)
    assert result is None
    assert "CANCELLED_GAP_LIMIT" in message


def test_order_recalculation_on_moderate_open():
    cfg = RiskConfig(max_allowed_gap_pct=0.03, stop_atr=2.0)
    plan, _ = create_trade_plan("AAPL", 100.0, 5.0, 1.0, "2024-01-01", cfg, 10000.0, 0.0)
    result, message = evaluate_market_open_entry(plan, 101.0, cfg, 10000.0, 10000.0)
    assert message == "OK"
    assert result["actual_entry_price"] == pytest.approx(101.0)
    assert result["stop"] == pytest.approx(91.0)


def test_total_portfolio_heat_limit():
    cfg = RiskConfig(risk_per_trade_pct=0.01, max_portfolio_risk_pct=0.05)
    plan, message = create_trade_plan("AAPL", 100.0, 5.0, 1.0, "2024-01-01", cfg, 10000.0, 0.045)
    assert plan is None
    assert "Umumiy portfel riski limiti" in message


def test_invalid_stop_rejected():
    cfg = RiskConfig(stop_atr=2.0)
    plan, message = create_trade_plan("PENNY", 10.0, 6.0, 1.0, "2024-01-01", cfg, 10000.0, 0.0)
    assert plan is None
    assert "Stop darajasi" in message


def test_cash_limits_shares():
    cfg = RiskConfig(max_allowed_gap_pct=0.03, cost_per_side=0.0)
    plan, _ = create_trade_plan("AAA", 100.0, 5.0, 1.0, "2024-01-01", cfg, 10000.0, 0.0)
    result, message = evaluate_market_open_entry(plan, 100.0, cfg, current_cash=250.0, current_equity=10000.0)
    assert message == "OK"
    assert result["notional"] <= 250.0 + 1e-8
