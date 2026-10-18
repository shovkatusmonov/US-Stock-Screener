"""
test_scoring_engine.py - Comprehensive Unit and Regression Tests for the Six-Score Architecture.
"""
import pytest
import numpy as np
import pandas as pd
from datetime import datetime

from scoring_engine import (
    ScoringWeights,
    HardRiskLimits,
    ScoringEngine,
    StockEvaluationResult,
    ScoreComponent,
)

@pytest.fixture
def sample_features():
    dates = pd.bdate_range("2024-01-01", periods=300)
    # Generate realistic bullish stock features
    df = pd.DataFrame(index=dates)
    df["close"] = np.linspace(100.0, 150.0, len(dates))
    df["raw_close"] = df["close"]
    df["sma20"] = df["close"].rolling(20).mean().bfill()
    df["sma50"] = df["close"].rolling(50).mean().bfill()
    df["sma200"] = df["close"].rolling(200).mean().bfill()
    df["trend_up"] = True
    df["rsi14"] = 60.0
    df["high20"] = df["close"].shift(1).rolling(20).max().bfill()
    df["rel_vol"] = 1.8
    df["dollar_vol20"] = 50_000_000.0
    df["atr14"] = 2.5
    df["atr_tradable"] = 2.5
    df["atr_pct"] = 2.5 / df["close"]
    df["rs60"] = 0.85
    return df


@pytest.fixture
def sample_bench_features():
    dates = pd.bdate_range("2024-01-01", periods=300)
    df = pd.DataFrame(index=dates)
    df["close"] = np.linspace(400.0, 500.0, len(dates))
    df["sma50"] = df["close"].rolling(50).mean().bfill()
    df["sma200"] = df["close"].rolling(200).mean().bfill()
    df["rsi14"] = 58.0
    return df


@pytest.fixture
def sample_tradable_ohlc(sample_features):
    c = sample_features["raw_close"]
    return pd.DataFrame({
        "open": c * 0.99,
        "high": c * 1.02,
        "low": c * 0.98,
        "close": c,
        "volume": 500_000.0
    }, index=sample_features.index)


# -----------------------------------------------------------------------------
# 1. Weights and Configuration Tests
# -----------------------------------------------------------------------------
def test_weights_validation_success():
    w = ScoringWeights(technical=25, fundamental=25, news_catalyst=15, market_regime=10, risk=25)
    w.validate()  # Should not raise


def test_weights_validation_failure():
    w = ScoringWeights(technical=30, fundamental=30, news_catalyst=20, market_regime=10, risk=25)
    with pytest.raises(ValueError, match="must sum to 100"):
        w.validate()

    w_neg = ScoringWeights(technical=-10, fundamental=35, news_catalyst=25, market_regime=25, risk=25)
    with pytest.raises(ValueError, match="cannot be negative"):
        w_neg.validate()


# -----------------------------------------------------------------------------
# 2. Technical Score Tests
# -----------------------------------------------------------------------------
def test_technical_score_bullish(sample_features, sample_bench_features, sample_tradable_ohlc):
    engine = ScoringEngine()
    tech = engine.evaluate_technical(sample_features, sample_bench_features, sample_tradable_ohlc)
    assert tech.is_available is True
    assert tech.score is not None
    assert 70.0 <= tech.score <= 100.0
    assert tech.coverage == 1.0
    assert "trend_alignment" in tech.subscores
    assert tech.subscores["trend_alignment"] == 100.0


def test_technical_score_insufficient_history(sample_bench_features):
    engine = ScoringEngine()
    short_f = pd.DataFrame({"close": [10.0, 11.0]})
    tech = engine.evaluate_technical(short_f, sample_bench_features)
    assert tech.is_available is False
    assert tech.score is None
    assert tech.coverage == 0.0


# -----------------------------------------------------------------------------
# 3. Fundamental Score Tests (No Fake Data)
# -----------------------------------------------------------------------------
def test_fundamental_score_missing_is_not_fabricated():
    engine = ScoringEngine()
    fund = engine.evaluate_fundamental(None, "AAPL")
    # MUST NOT return 50.0 or pretend data is available
    assert fund.is_available is False
    assert fund.score is None
    assert fund.coverage == 0.0
    assert fund.status == "DATA_UNAVAILABLE"
    assert "not connected" in fund.warnings[0].lower()


def test_fundamental_score_with_valid_pit_data():
    engine = ScoringEngine()
    data = {
        "gross_margin": 0.45,
        "operating_margin": 0.28,
        "roe": 0.35,
        "revenue_growth_yoy": 0.18,
        "eps_growth_yoy": 0.22,
        "debt_to_equity": 0.50,
        "current_ratio": 1.6,
        "free_cash_flow": 100_000_000.0,
        "pe_ratio": 22.0,
        "source": "SEC_10Q",
        "filing_date": "2024-05-15"
    }
    fund = engine.evaluate_fundamental(data, "MSFT")
    assert fund.is_available is True
    assert fund.score is not None
    assert 70.0 <= fund.score <= 100.0
    assert fund.coverage == 1.0
    assert fund.source == "SEC_10Q"


def test_fundamental_score_handles_negative_equity():
    engine = ScoringEngine()
    distressed_data = {
        "operating_margin": -0.15,
        "revenue_growth_yoy": -0.10,
        "debt_to_equity": -2.0,  # Negative shareholder equity!
        "current_ratio": 0.6,
        "free_cash_flow": -50_000_000.0,
        "pe_ratio": -10.0
    }
    fund = engine.evaluate_fundamental(distressed_data, "DIST")
    assert fund.is_available is True
    assert fund.score is not None
    assert fund.score < 35.0  # Must severely penalize insolvency risk


# -----------------------------------------------------------------------------
# 4. News / Catalyst Score Tests (No Fake Data & Event Risk)
# -----------------------------------------------------------------------------
def test_news_score_missing_is_not_fabricated():
    engine = ScoringEngine()
    news = engine.evaluate_news_catalyst(None, "NVDA")
    assert news.is_available is False
    assert news.score is None
    assert news.coverage == 0.0
    assert news.status == "DATA_UNAVAILABLE"


def test_news_imminent_earnings_triggers_event_risk():
    engine = ScoringEngine()
    today_str = "2024-06-01"
    news_data = {
        "upcoming_earnings_date": "2024-06-03",  # 2 days away!
        "events": []
    }
    news = engine.evaluate_news_catalyst(news_data, "XYZ", as_of_date=today_str)
    assert news.is_available is True
    assert news.subscores["event_risk_safety"] <= 30.0
    assert any("imminent earnings" in w.lower() for w in news.warnings)


# -----------------------------------------------------------------------------
# 5. Risk Score Tests (Higher = Safer)
# -----------------------------------------------------------------------------
def test_risk_score_direction_higher_is_safer(sample_features, sample_tradable_ohlc):
    engine = ScoringEngine()
    # Safe stock: high dollar volume, low volatility (ATR 1.5%), low drawdown
    safe_features = sample_features.copy()
    safe_features["dollar_vol20"] = 150_000_000.0
    safe_features["atr_pct"] = 0.015
    safe_risk = engine.evaluate_risk(safe_features, sample_tradable_ohlc)

    # Risky stock: low volume, high volatility (ATR 9.0%), deep drawdown (-45%)
    risky_features = sample_features.copy()
    risky_features["dollar_vol20"] = 8_000_000.0
    risky_features["atr_pct"] = 0.09
    risky_ohlc = sample_tradable_ohlc.copy()
    risky_ohlc.iloc[-10:, risky_ohlc.columns.get_loc("close")] = risky_ohlc["close"].iloc[-11] * 0.55
    risky_risk = engine.evaluate_risk(risky_features, risky_ohlc)

    assert safe_risk.score is not None
    assert risky_risk.score is not None
    # HIGHER MUST MEAN SAFER:
    assert safe_risk.score > risky_risk.score
    assert safe_risk.score >= 75.0
    assert risky_risk.score <= 50.0


# -----------------------------------------------------------------------------
# 6. Hard Risk Gating Tests (CRITICAL: Strict Separation from Final Score)
# -----------------------------------------------------------------------------
def test_hard_gates_rejects_candidate_despite_excellent_scores(
    sample_features, sample_bench_features, sample_tradable_ohlc
):
    """
    CRITICAL MANDATE: Even if Final Score is 90+, candidate MUST be REJECTED
    if liquidity or gap or portfolio heat limits are violated!
    """
    engine = ScoringEngine()
    # Stock has poor dollar volume ($5M < $20M)
    illiquid_features = sample_features.copy()
    illiquid_features["dollar_vol20"] = 5_000_000.0

    eval_res = engine.evaluate_stock(
        ticker="LOWLIQ",
        features=illiquid_features,
        tradable_ohlc=sample_tradable_ohlc,
        bench_features=sample_bench_features,
        current_heat=0.01,
        open_positions=2
    )

    assert eval_res.hard_gates_passed is False
    assert eval_res.eligibility == "REJECTED"
    assert any("REJECT_INSUFFICIENT_LIQUIDITY" in r for r in eval_res.rejection_reasons)


def test_hard_gates_rejects_on_excessive_opening_gap(
    sample_features, sample_bench_features, sample_tradable_ohlc
):
    engine = ScoringEngine()
    # +5.0% opening gap exceeds 3.0% limit
    eval_res = engine.evaluate_stock(
        ticker="GAPPER",
        features=sample_features,
        tradable_ohlc=sample_tradable_ohlc,
        bench_features=sample_bench_features,
        current_heat=0.01,
        open_positions=2,
        opening_gap=0.05
    )

    assert eval_res.hard_gates_passed is False
    assert eval_res.eligibility == "REJECTED"
    assert any("REJECT_GAP_LIMIT" in r for r in eval_res.rejection_reasons)


def test_hard_gates_rejects_on_portfolio_heat_exhaustion(
    sample_features, sample_bench_features, sample_tradable_ohlc
):
    engine = ScoringEngine()
    # Heat 5.5% exceeds max 5.0%
    eval_res = engine.evaluate_stock(
        ticker="HEATEX",
        features=sample_features,
        tradable_ohlc=sample_tradable_ohlc,
        bench_features=sample_bench_features,
        current_heat=0.055,
        open_positions=6
    )

    assert eval_res.hard_gates_passed is False
    assert eval_res.eligibility == "REJECTED"
    assert any("REJECT_PORTFOLIO_HEAT" in r for r in eval_res.rejection_reasons)


# -----------------------------------------------------------------------------
# 7. Final Score and Ranking Tests
# -----------------------------------------------------------------------------
def test_final_score_coverage_and_missing_data_penalty(
    sample_features, sample_bench_features, sample_tradable_ohlc
):
    engine = ScoringEngine()
    # Candidate with only Technical, Market Regime, and Risk (no Fundamentals or News)
    eval_res = engine.evaluate_stock(
        ticker="PARTIAL",
        features=sample_features,
        tradable_ohlc=sample_tradable_ohlc,
        bench_features=sample_bench_features,
        fundamental_data=None,
        news_data=None
    )

    # Coverage should be (25 Tech + 10 Regime + 25 Risk) / 100 = 60%
    assert eval_res.score_coverage == pytest.approx(0.60, abs=1e-2)
    assert "Fundamental" in eval_res.missing_data
    assert "News/Catalyst" in eval_res.missing_data
    # Adjusted final score must be lower than raw final score due to missing data penalty
    assert eval_res.adjusted_final_score < eval_res.raw_final_score


def test_ranking_determinism_and_eligibility_sorting(
    sample_features, sample_bench_features, sample_tradable_ohlc
):
    engine = ScoringEngine()
    # Create 3 candidates:
    # 1. Eligible strong candidate
    res_eligible = engine.evaluate_stock(
        ticker="BEST",
        features=sample_features,
        tradable_ohlc=sample_tradable_ohlc,
        bench_features=sample_bench_features
    )
    # 2. Watchlist candidate (trend up but not breakout/pullback setup)
    watch_feat = sample_features.copy()
    watch_feat["high20"] = watch_feat["close"] * 1.10
    watch_feat["rel_vol"] = 1.0
    res_watchlist = engine.evaluate_stock(
        ticker="WATCH",
        features=watch_feat,
        tradable_ohlc=sample_tradable_ohlc,
        bench_features=sample_bench_features
    )
    # 3. Rejected candidate (dollar volume too low)
    rej_feat = sample_features.copy()
    rej_feat["dollar_vol20"] = 1_000_000.0
    res_rejected = engine.evaluate_stock(
        ticker="REJ",
        features=rej_feat,
        tradable_ohlc=sample_tradable_ohlc,
        bench_features=sample_bench_features
    )

    ranked = engine.rank_candidates([res_rejected, res_watchlist, res_eligible])
    # Eligible MUST rank before Watchlist, and Watchlist MUST rank before Rejected!
    assert ranked[0].ticker == "BEST"
    assert ranked[0].rank == 1
    assert ranked[1].ticker == "WATCH"
    assert ranked[1].rank == 2
    assert ranked[2].ticker == "REJ"
    assert ranked[2].rank == 3
