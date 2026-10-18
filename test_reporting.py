"""
test_reporting.py - Hisobot generatori testlari.
"""
from reporting import calculate_candidate_scores, generate_full_report


def test_calculate_candidate_scores():
    candidate = {"rank": 0.25, "rel_vol": 2.0, "atr": 5.0, "close": 100.0}
    scores = calculate_candidate_scores(candidate)
    assert 0.0 <= scores["swing_score"] <= 100.0
    assert 0.0 <= scores["risk_score"] <= 100.0


def test_generate_report_with_candidates(tmp_path):
    bench = {"is_bull": True, "spy_close": 500.0, "spy_sma200": 450.0}
    candidates = [{
        "ticker": "NVDA", "signal_price": 120.0, "min_open": 116.4, "max_open": 123.6,
        "planned_stop": 110.0, "planned_target": 162.0, "shares": 10,
        "dollar_risk": 100.0, "risk_pct": 0.01, "rr": 4.0, "rel_vol": 1.8, "rank": 0.15
    }]
    rep = generate_full_report(
        "2026-10-11", "2026-10-10", bench, candidates,
        {"portfolio_heat_pct": 0.02, "open_count": 2}, 500, output_dir=str(tmp_path)
    )
    assert "NVDA" in rep
    assert "Breakout V2 Trend Runner" in rep
    assert "BULLISH" in rep


def test_generate_report_no_candidates(tmp_path):
    bench = {"is_bull": True, "spy_close": 500.0, "spy_sma200": 450.0}
    rep = generate_full_report(
        "2026-10-11", "2026-10-10", bench, [],
        {"portfolio_heat_pct": 0.0, "open_count": 0}, 500, output_dir=str(tmp_path)
    )
    assert "SAVDO UCHUN MOS NOMZOD TOPILMADI" in rep