"""
test_review_fixes.py - 2026-10-11 auditida topilgan xatolar uchun regressiya testlari.
"""
import numpy as np
import pandas as pd
import pytest

from reporting import calculate_candidate_scores, generate_full_report
from risk_manager import RiskConfig, create_trade_plan, reward_risk
from run_backtest import equity_stats
from trade_manager import TradeManager


def _plan(ticker, entry=100.0, stop=90.0, target=135.0, shares=10, date="2026-10-09"):
    return {
        "ticker": ticker, "signal_price": entry, "planned_stop": stop, "planned_target": target,
        "shares": shares, "dollar_risk": 100.0, "signal_date": date,
    }


def test_reward_risk_matches_create_trade_plan():
    cfg = RiskConfig()
    plan, _ = create_trade_plan("X", 100.0, 5.0, 0.1, "2026-10-09", cfg, 10_000.0, 0.0)
    assert plan is not None
    assert reward_risk(100.0, plan["planned_stop"], plan["planned_target"], cfg.cost_per_side) == pytest.approx(plan["rr"])


def test_plan_dollar_risk_is_actual_not_nominal():
    cfg = RiskConfig()
    plan, _ = create_trade_plan("X", 225.0, 15.0, 0.1, "2026-10-09", cfg, 10_000.0, 0.0)
    risk_ps = 225.0 * (1 + cfg.cost_per_side) - plan["planned_stop"]
    assert plan["dollar_risk"] == pytest.approx(plan["shares"] * risk_ps)
    assert plan["dollar_risk"] <= cfg.risk_per_trade_pct * 10_000.0 + 1e-9


def test_scores_not_fabricated_when_signal_data_missing():
    s = calculate_candidate_scores({"signal_price": 225.0})
    assert s["swing_score"] is None and s["volatility_score"] is None


def test_scores_use_signal_price_when_close_missing():
    s = calculate_candidate_scores({"rank": 0.1, "atr": 10.0, "signal_price": 100.0, "rel_vol": 2.0})
    assert s["volatility_score"] == 100.0  # atr% = 10% -> 100


def test_pending_plan_report_shows_real_rr_not_2(tmp_path):
    # Bazadan yuklangan reja: 'rr' kaliti yo'q, rank/atr yo'q
    c = _plan("MRNA", entry=225.0, stop=194.61, target=303.75, shares=3)
    rep = generate_full_report(
        "2026-10-11", "2026-10-09", {"is_bull": True, "spy_close": 1.0, "spy_sma200": 1.0},
        [dict(c, risk_pct=0.01, min_open=218.25, max_open=231.75)],
        {"portfolio_heat_pct": 0.0, "open_count": 0}, 500, output_dir=str(tmp_path))
    assert "1 : 2.00" not in rep
    assert "1 : 2.5" in rep           # ~2.55
    assert "N/A" in rep               # ballar baholanmagan
    assert "Baholanmadi" in rep


def test_sortino_uses_full_downside_deviation():
    idx = pd.bdate_range("2025-01-01", periods=60)
    rng = np.random.default_rng(0)
    r = rng.normal(0.001, 0.01, len(idx))
    eq = pd.Series(10_000 * np.cumprod(1 + r), index=idx)
    got = equity_stats(eq)["sortino"]
    rets = eq.pct_change().dropna()
    dd = np.sqrt(np.mean(np.minimum(rets.values, 0.0) ** 2))
    assert got == pytest.approx(rets.mean() / dd * np.sqrt(252))


def test_market_open_respects_position_capacity(tmp_path):
    db = str(tmp_path / "t.db")
    tm = TradeManager(db_path=db, initial_cash=10_000.0)
    cfg = RiskConfig(max_positions=1)
    ok1, id1 = tm.add_planned_trade(_plan("AAA"))
    ok2, id2 = tm.add_planned_trade(_plan("BBB"))
    assert ok1 and ok2
    assert tm.execute_market_open(id1, 100.0, "2026-10-12", cfg)[0] is True
    ok, msg = tm.execute_market_open(id2, 100.0, "2026-10-12", cfg)
    assert ok is False and "CAPACITY" in msg


def test_market_open_respects_portfolio_heat(tmp_path):
    db = str(tmp_path / "t.db")
    tm = TradeManager(db_path=db, initial_cash=100_000.0)
    cfg = RiskConfig(max_positions=20)
    ids = []
    for i in range(6):
        ok, sid = tm.add_planned_trade(_plan(f"T{i}", shares=50))
        assert ok
        ids.append(sid)
    results = [tm.execute_market_open(s, 100.0, "2026-10-12", cfg)[0] for s in ids]
    # Har biri ~1% risk -> 5% heat limitida 5 tadan keyin to'xtashi kerak
    assert results[:5] == [True] * 5
    assert results[5] is False
