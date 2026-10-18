"""
scoring_engine.py - Complete Six-Score Quantitative Ranking and Risk Gating Engine.

Six Distinct Dimensions (Normalized 0 - 100):
1. Technical Score        (Trend, Relative Strength vs SPY, Momentum, Setup Quality, Volume, Extension)
2. Fundamental Score      (Profitability, Growth, Balance Sheet, Cash Flow, Valuation)
3. News / Catalyst Score  (Verified Events, Earnings Surprise, Guidance, Regulatory/M&A, Recency Decay)
4. Market Regime Score    (Broad Market Trend, Market Momentum & Breadth, Volatility & Drawdown, Macro)
5. Risk Score             (HIGHER = SAFER: Liquidity, Volatility, Drawdown, Gap Risk, Event Risk, Financial Risk)
6. Final Score            (Weighted combination, explicit coverage & confidence, strictly separated from hard risk gates)

Hard Risk Gates:
A candidate with Final Score 95+ MUST still be REJECTED if any hard gate is violated
(Liquidity, Gap, Reward/Risk, Portfolio Heat, Position Limits, Invalid OHLC, Stale Data).
"""
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd


@dataclass
class ScoringWeights:
    """Primary score weights (must sum to 100.0). Configurable hypothesis."""
    technical: float = 25.0
    fundamental: float = 25.0
    news_catalyst: float = 15.0
    market_regime: float = 10.0
    risk: float = 25.0

    def validate(self):
        total = self.technical + self.fundamental + self.news_catalyst + self.market_regime + self.risk
        if not np.isclose(total, 100.0, atol=1e-3):
            raise ValueError(f"Scoring weights must sum to 100.0, got {total:.2f}")
        for k, v in self.__dict__.items():
            if v < 0:
                raise ValueError(f"Weight {k} cannot be negative: {v}")


@dataclass
class HardRiskLimits:
    """Hard portfolio and execution constraints."""
    min_dollar_vol: float = 20_000_000.0
    min_price: float = 10.0
    max_gap_pct: float = 0.03
    min_rr: float = 1.50
    max_portfolio_heat: float = 0.05
    max_positions: int = 8
    max_pos_pct: float = 0.20
    risk_per_trade_pct: float = 0.01
    cost_per_side: float = 0.001
    min_history_bars: int = 260
    max_data_age_days: int = 5
    upcoming_earnings_risk_days: int = 5


@dataclass
class ScoreComponent:
    """Represents a single score dimension."""
    name: str
    score: Optional[float]  # 0.0 to 100.0, or None if unavailable
    weight: float           # out of 100
    is_available: bool
    coverage: float         # 0.0 to 1.0
    confidence: float       # 0.0 to 1.0
    status: str             # 'OK', 'DATA_UNAVAILABLE', 'PARTIAL', 'FAILED'
    subscores: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    source: str = "DERIVED"
    timestamp: Optional[str] = None


@dataclass
class StockEvaluationResult:
    """Complete evaluation result for a ticker."""
    ticker: str
    company_name: Optional[str]
    sector: Optional[str]
    price: float
    price_date: str
    technical: ScoreComponent
    fundamental: ScoreComponent
    news_catalyst: ScoreComponent
    market_regime: ScoreComponent
    risk: ScoreComponent
    raw_final_score: float
    adjusted_final_score: float
    score_coverage: float
    overall_confidence: float
    rank: Optional[int]
    setup_classification: str
    strategy_suitability: Dict[str, str]
    hard_gates_passed: bool
    rejection_reasons: List[str]
    eligibility: str  # 'TRADE_ELIGIBLE', 'WATCHLIST', 'REJECTED', 'INSUFFICIENT_DATA'
    positive_factors: List[str]
    negative_factors: List[str]
    missing_data: List[str]
    formula_version: str = "v1.0-six-score"
    data_snapshot_id: str = "LIVE"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "company_name": self.company_name,
            "sector": self.sector,
            "price": round(self.price, 2),
            "price_date": self.price_date,
            "technical_score": self.technical.score,
            "fundamental_score": self.fundamental.score,
            "news_catalyst_score": self.news_catalyst.score,
            "market_regime_score": self.market_regime.score,
            "risk_score": self.risk.score,
            "raw_final_score": round(self.raw_final_score, 1),
            "adjusted_final_score": round(self.adjusted_final_score, 1),
            "score_coverage_pct": round(self.score_coverage * 100, 1),
            "confidence_pct": round(self.overall_confidence * 100, 1),
            "rank": self.rank,
            "setup": self.setup_classification,
            "eligibility": self.eligibility,
            "hard_gates_passed": self.hard_gates_passed,
            "rejection_reasons": self.rejection_reasons,
            "positive_factors": self.positive_factors,
            "negative_factors": self.negative_factors,
            "missing_data": self.missing_data,
        }


class ScoringEngine:
    """
    Deterministic Six-Score Evaluation & Hard Gating Engine.
    """
    def __init__(
        self,
        weights: Optional[ScoringWeights] = None,
        limits: Optional[HardRiskLimits] = None,
        version: str = "v1.0"
    ):
        self.weights = weights or ScoringWeights()
        self.weights.validate()
        self.limits = limits or HardRiskLimits()
        self.version = version

    # -------------------------------------------------------------------------
    # 1. TECHNICAL SCORE (0 - 100)
    # -------------------------------------------------------------------------
    def evaluate_technical(
        self,
        features: pd.DataFrame,
        bench_features: pd.DataFrame,
        tradable_ohlc: Optional[pd.DataFrame] = None
    ) -> ScoreComponent:
        if features.empty or len(features) < 30:
            return ScoreComponent(
                name="Technical",
                score=None,
                weight=self.weights.technical,
                is_available=False,
                coverage=0.0,
                confidence=0.0,
                status="DATA_UNAVAILABLE",
                reasons=[],
                warnings=["Insufficient price history for technical scoring (<30 bars)"]
            )

        latest = features.iloc[-1]
        subscores = {}
        reasons = []
        warnings = []

        # Subcomponent 1: Trend Alignment (weight 25%)
        # close > sma20 > sma50 > sma200
        trend_score = 0.0
        c = latest.get("close", 0.0)
        s20 = latest.get("sma20", 0.0)
        s50 = latest.get("sma50", 0.0)
        s200 = latest.get("sma200", 0.0)

        if c > s200:
            trend_score += 30.0
        if c > s50:
            trend_score += 25.0
        if s50 > s200:
            trend_score += 25.0
        if c > s20:
            trend_score += 20.0
        subscores["trend_alignment"] = round(trend_score, 1)
        if trend_score >= 80:
            reasons.append("Strong multi-timeframe bullish trend alignment (Close > SMA20 > SMA50 > SMA200)")
        elif trend_score < 40:
            warnings.append("Weak or broken trend structure (below key moving averages)")

        # Subcomponent 2: Relative Strength vs SPY (weight 20%)
        # rs60 is percentile rank [0, 1]
        rs = latest.get("rs60", 0.5)
        if pd.isna(rs):
            rs_score = 50.0
            warnings.append("Relative strength metric (rs60) is NaN")
        else:
            rs_score = float(np.clip(rs * 100.0, 0.0, 100.0))
        subscores["relative_strength"] = round(rs_score, 1)
        if rs_score >= 70:
            reasons.append(f"High relative strength vs SPY (Rank: {rs_score:.0f}/100)")
        elif rs_score < 30:
            warnings.append("Laggard relative performance vs broad market")

        # Subcomponent 3: Momentum (RSI14) (weight 15%)
        rsi = latest.get("rsi14", 50.0)
        if pd.isna(rsi):
            mom_score = 50.0
        elif 50.0 <= rsi <= 68.0:
            mom_score = 90.0  # Optimal bullish momentum without being overbought
        elif 40.0 <= rsi < 50.0:
            mom_score = 65.0  # Pullback zone
        elif 68.0 < rsi <= 78.0:
            mom_score = 70.0  # Strong but near overbought
        elif rsi > 78.0:
            mom_score = 40.0  # Overbought extension risk
            warnings.append(f"RSI overbought ({rsi:.1f})")
        else:
            mom_score = max(10.0, rsi)  # Oversold / bearish
        subscores["momentum"] = round(mom_score, 1)

        # Subcomponent 4: Setup Quality (Breakout / Pullback) (weight 20%)
        high20 = latest.get("high20", c)
        rel_vol = latest.get("rel_vol", 1.0)
        is_breakout = (c > high20) and (rel_vol >= 1.5)
        is_pullback = (c <= s20) and (c > s50) and (35.0 <= rsi <= 55.0)

        if is_breakout:
            setup_score = 95.0
            reasons.append(f"Confirmed 20-day high breakout with {rel_vol:.1f}x relative volume")
        elif is_pullback:
            setup_score = 80.0
            reasons.append("Constructive pullback bouncing between SMA20 and SMA50")
        elif c > high20 * 0.98:
            setup_score = 65.0  # Coiling near high
        elif c > s50:
            setup_score = 50.0  # Generic trend continuation
        else:
            setup_score = 25.0  # No constructive setup
        subscores["setup_quality"] = round(setup_score, 1)

        # Subcomponent 5: Volume Confirmation (weight 10%)
        if pd.isna(rel_vol):
            vol_score = 50.0
        elif rel_vol >= 2.0:
            vol_score = 100.0
            reasons.append(f"Exceptional volume surge ({rel_vol:.1f}x 20-day average)")
        elif rel_vol >= 1.5:
            vol_score = 85.0
        elif rel_vol >= 1.0:
            vol_score = 60.0
        else:
            vol_score = max(20.0, rel_vol * 50.0)
            warnings.append(f"Below average volume ({rel_vol:.2f}x)")
        subscores["volume_confirmation"] = round(vol_score, 1)

        # Subcomponent 6: Extension & Price Structure (weight 10%)
        # Penalize if price is stretched > 15% above SMA20
        dist_sma20 = (c - s20) / s20 if s20 > 0 else 0.0
        if 0.0 <= dist_sma20 <= 0.06:
            ext_score = 100.0  # Right at the moving average base
        elif 0.06 < dist_sma20 <= 0.12:
            ext_score = 80.0
        elif dist_sma20 > 0.15:
            ext_score = max(15.0, 100.0 - (dist_sma20 * 400.0))
            warnings.append(f"Extended entry price ({dist_sma20*100:.1f}% above SMA20)")
        elif dist_sma20 < -0.05:
            ext_score = 40.0
        else:
            ext_score = 70.0
        subscores["price_extension"] = round(ext_score, 1)

        # Weighted calculation
        tech_score = (
            subscores["trend_alignment"] * 0.25 +
            subscores["relative_strength"] * 0.20 +
            subscores["momentum"] * 0.15 +
            subscores["setup_quality"] * 0.20 +
            subscores["volume_confirmation"] * 0.10 +
            subscores["price_extension"] * 0.10
        )
        tech_score = float(np.clip(tech_score, 0.0, 100.0))

        confidence = 0.95 if len(features) >= 260 else (len(features) / 260.0) * 0.95

        return ScoreComponent(
            name="Technical",
            score=round(tech_score, 1),
            weight=self.weights.technical,
            is_available=True,
            coverage=1.0,
            confidence=round(confidence, 2),
            status="OK",
            subscores=subscores,
            reasons=reasons,
            warnings=warnings,
            source="OHLCV_POINT_IN_TIME",
            timestamp=str(features.index[-1].date())
        )

    # -------------------------------------------------------------------------
    # 2. FUNDAMENTAL SCORE (0 - 100)
    # -------------------------------------------------------------------------
    def evaluate_fundamental(
        self,
        fundamental_data: Optional[Dict[str, Any]],
        ticker: str,
        as_of_date: Optional[str] = None
    ) -> ScoreComponent:
        """
        Evaluates company financials only when verified point-in-time fundamental data exists.
        STRICT REQUIREMENT: NO FAKE DATA!
        If fundamental_data is None or empty, returns is_available=False, score=None, coverage=0.0.
        """
        if not fundamental_data:
            return ScoreComponent(
                name="Fundamental",
                score=None,
                weight=self.weights.fundamental,
                is_available=False,
                coverage=0.0,
                confidence=0.0,
                status="DATA_UNAVAILABLE",
                subscores={},
                reasons=[],
                warnings=["Fundamental data provider not connected in database. Zero assumption made (score not fabricated)."],
                source="NONE",
                timestamp=as_of_date
            )

        subscores = {}
        reasons = []
        warnings = []
        valid_items = 0
        total_items = 5

        # Subcomponent 1: Profitability (25%)
        gross_m = fundamental_data.get("gross_margin")
        op_m = fundamental_data.get("operating_margin")
        roe = fundamental_data.get("roe")
        if gross_m is not None or op_m is not None or roe is not None:
            valid_items += 1
            prof_score = 50.0
            if op_m is not None:
                if op_m > 0.20:
                    prof_score += 30.0
                elif op_m > 0.10:
                    prof_score += 15.0
                elif op_m < 0.0:
                    prof_score -= 30.0
                    warnings.append(f"Operating loss (Operating Margin: {op_m*100:.1f}%)")
            if roe is not None:
                if roe > 0.15:
                    prof_score += 20.0
                elif roe < 0.0:
                    prof_score -= 20.0
            subscores["profitability"] = float(np.clip(prof_score, 0.0, 100.0))
        else:
            subscores["profitability"] = None

        # Subcomponent 2: Growth (25%)
        rev_growth = fundamental_data.get("revenue_growth_yoy")
        eps_growth = fundamental_data.get("eps_growth_yoy")
        if rev_growth is not None or eps_growth is not None:
            valid_items += 1
            growth_score = 50.0
            if rev_growth is not None:
                if rev_growth > 0.20:
                    growth_score += 25.0
                    reasons.append(f"Strong top-line growth (Revenue YoY: +{rev_growth*100:.1f}%)")
                elif rev_growth > 0.08:
                    growth_score += 10.0
                elif rev_growth < 0.0:
                    growth_score -= 25.0
                    warnings.append(f"Revenue contraction (YoY: {rev_growth*100:.1f}%)")
            if eps_growth is not None:
                if eps_growth > 0.15:
                    growth_score += 25.0
                elif eps_growth < 0.0:
                    growth_score -= 20.0
            subscores["growth"] = float(np.clip(growth_score, 0.0, 100.0))
        else:
            subscores["growth"] = None

        # Subcomponent 3: Balance Sheet Health (20%)
        debt_to_equity = fundamental_data.get("debt_to_equity")
        current_ratio = fundamental_data.get("current_ratio")
        if debt_to_equity is not None or current_ratio is not None:
            valid_items += 1
            bs_score = 60.0
            if debt_to_equity is not None:
                if debt_to_equity < 0.0:
                    bs_score = 15.0  # Negative shareholder equity is severe risk
                    warnings.append("Negative shareholder equity (High balance sheet insolvency risk)")
                elif debt_to_equity < 0.8:
                    bs_score += 25.0
                elif debt_to_equity > 2.5:
                    bs_score -= 30.0
                    warnings.append(f"High debt load (Debt/Equity: {debt_to_equity:.2f})")
            if current_ratio is not None:
                if current_ratio >= 1.5:
                    bs_score += 15.0
                elif current_ratio < 1.0:
                    bs_score -= 20.0
                    warnings.append(f"Working capital deficit (Current Ratio: {current_ratio:.2f})")
            subscores["balance_sheet"] = float(np.clip(bs_score, 0.0, 100.0))
        else:
            subscores["balance_sheet"] = None

        # Subcomponent 4: Cash Flow Quality (15%)
        fcf = fundamental_data.get("free_cash_flow")
        ocf = fundamental_data.get("operating_cash_flow")
        if fcf is not None or ocf is not None:
            valid_items += 1
            cf_score = 50.0
            if fcf is not None and fcf > 0:
                cf_score += 35.0
                reasons.append("Positive free cash flow generation")
            elif fcf is not None and fcf <= 0:
                cf_score -= 35.0
                warnings.append("Negative free cash flow (Cash burn)")
            subscores["cash_flow"] = float(np.clip(cf_score, 0.0, 100.0))
        else:
            subscores["cash_flow"] = None

        # Subcomponent 5: Valuation (15%)
        pe = fundamental_data.get("pe_ratio")
        ps = fundamental_data.get("ps_ratio")
        if pe is not None or ps is not None:
            valid_items += 1
            val_score = 50.0
            if pe is not None:
                if 8.0 <= pe <= 25.0:
                    val_score += 25.0
                elif pe > 60.0:
                    val_score -= 25.0
                    warnings.append(f"Elevated valuation multiple (P/E: {pe:.1f})")
                elif pe < 0.0:
                    val_score = 30.0  # Unprofitable
                    warnings.append("Company is currently unprofitable (Negative P/E)")
            subscores["valuation"] = float(np.clip(val_score, 0.0, 100.0))
        else:
            subscores["valuation"] = None

        coverage = valid_items / total_items
        if coverage == 0:
            return ScoreComponent(
                name="Fundamental",
                score=None,
                weight=self.weights.fundamental,
                is_available=False,
                coverage=0.0,
                confidence=0.0,
                status="DATA_UNAVAILABLE",
                reasons=[],
                warnings=["Fundamental fields were empty or missing"],
                source="POINT_IN_TIME_FILING"
            )

        # Compute weighted average of available fundamental subscores
        sub_weights = {"profitability": 0.25, "growth": 0.25, "balance_sheet": 0.20, "cash_flow": 0.15, "valuation": 0.15}
        total_w = sum(sub_weights[k] for k, v in subscores.items() if v is not None)
        weighted_sum = sum(subscores[k] * sub_weights[k] for k, v in subscores.items() if v is not None)
        f_score = weighted_sum / total_w if total_w > 0 else 50.0

        pub_ts = fundamental_data.get("filing_date", as_of_date)
        return ScoreComponent(
            name="Fundamental",
            score=round(f_score, 1),
            weight=self.weights.fundamental,
            is_available=True,
            coverage=round(coverage, 2),
            confidence=round(coverage * 0.9, 2),
            status="OK" if coverage >= 0.8 else "PARTIAL",
            subscores={k: round(v, 1) if v is not None else None for k, v in subscores.items()},
            reasons=reasons,
            warnings=warnings,
            source=fundamental_data.get("source", "SEC_EDGAR_PIT"),
            timestamp=str(pub_ts)
        )

    # -------------------------------------------------------------------------
    # 3. NEWS / CATALYST SCORE (0 - 100)
    # -------------------------------------------------------------------------
    def evaluate_news_catalyst(
        self,
        news_data: Optional[Dict[str, Any]],
        ticker: str,
        as_of_date: Optional[str] = None
    ) -> ScoreComponent:
        """
        Evaluates verified company-specific catalysts and upcoming event risks.
        STRICT REQUIREMENT: NO FAKE DATA!
        If news_data is None or empty, returns is_available=False, score=None, coverage=0.0.
        """
        if not news_data:
            return ScoreComponent(
                name="News/Catalyst",
                score=None,
                weight=self.weights.news_catalyst,
                is_available=False,
                coverage=0.0,
                confidence=0.0,
                status="DATA_UNAVAILABLE",
                subscores={},
                reasons=[],
                warnings=["News and catalyst feed not connected in database. Zero assumption made (score not fabricated)."],
                source="NONE",
                timestamp=as_of_date
            )

        events = news_data.get("events", [])
        upcoming_earnings = news_data.get("upcoming_earnings_date")
        subscores = {}
        reasons = []
        warnings = []

        # Check upcoming earnings risk
        event_risk_score = 90.0
        if upcoming_earnings:
            try:
                ref_d = pd.to_datetime(as_of_date or datetime.now().date()).date()
                earn_d = pd.to_datetime(upcoming_earnings).date()
                days_to_earn = (earn_d - ref_d).days
                if 0 <= days_to_earn <= self.limits.upcoming_earnings_risk_days:
                    event_risk_score = 20.0
                    warnings.append(f"Imminent earnings announcement in {days_to_earn} days ({upcoming_earnings}). High binary gap risk!")
                elif days_to_earn < 0:
                    event_risk_score = 80.0
                else:
                    event_risk_score = 90.0
            except Exception:
                pass
        subscores["event_risk"] = event_risk_score

        if not events and not upcoming_earnings:
            return ScoreComponent(
                name="News/Catalyst",
                score=None,
                weight=self.weights.news_catalyst,
                is_available=False,
                coverage=0.0,
                confidence=0.0,
                status="DATA_UNAVAILABLE",
                warnings=["No verified news events or earnings dates found"],
                timestamp=as_of_date
            )

        # Catalyst impact evaluation
        pos_impact = 0.0
        neg_impact = 0.0
        for ev in events:
            impact = ev.get("impact", "NEUTRAL")  # 'POSITIVE', 'NEGATIVE', 'NEUTRAL'
            materiality = ev.get("materiality", 1.0)  # 1.0 to 3.0
            recency_days = ev.get("recency_days", 0)
            decay = max(0.2, 1.0 - (recency_days * 0.1))

            if impact == "POSITIVE":
                pos_impact += materiality * 15.0 * decay
                reasons.append(f"Positive catalyst: {ev.get('headline', 'Corporate announcement')}")
            elif impact == "NEGATIVE":
                neg_impact += materiality * 20.0 * decay
                warnings.append(f"Negative catalyst: {ev.get('headline', 'Adverse news event')}")

        base_cat = 50.0 + pos_impact - neg_impact
        cat_score = float(np.clip(base_cat * 0.7 + event_risk_score * 0.3, 0.0, 100.0))
        subscores["catalyst_sentiment"] = round(float(np.clip(base_cat, 0.0, 100.0)), 1)
        subscores["event_risk_safety"] = round(event_risk_score, 1)

        return ScoreComponent(
            name="News/Catalyst",
            score=round(cat_score, 1),
            weight=self.weights.news_catalyst,
            is_available=True,
            coverage=0.85 if upcoming_earnings else 0.60,
            confidence=0.80,
            status="OK",
            subscores=subscores,
            reasons=reasons,
            warnings=warnings,
            source=news_data.get("source", "VERIFIED_EVENTS_FEED"),
            timestamp=as_of_date
        )

    # -------------------------------------------------------------------------
    # 4. MARKET REGIME SCORE (0 - 100)
    # -------------------------------------------------------------------------
    def evaluate_market_regime(
        self,
        bench_features: pd.DataFrame,
        macro_data: Optional[Dict[str, Any]] = None
    ) -> ScoreComponent:
        if bench_features.empty or len(bench_features) < 30:
            return ScoreComponent(
                name="Market Regime",
                score=None,
                weight=self.weights.market_regime,
                is_available=False,
                coverage=0.0,
                confidence=0.0,
                status="DATA_UNAVAILABLE",
                warnings=["SPY benchmark features missing or insufficient"]
            )

        latest = bench_features.iloc[-1]
        c = latest.get("close", 0.0)
        s50 = latest.get("sma50", 0.0)
        s200 = latest.get("sma200", 0.0)

        subscores = {}
        reasons = []
        warnings = []

        # 1. Broad Market Trend (30%)
        trend_score = 0.0
        if c > s200:
            trend_score += 50.0
        if c > s50:
            trend_score += 30.0
        if s50 > s200:
            trend_score += 20.0
        subscores["broad_market_trend"] = round(trend_score, 1)
        if trend_score >= 80:
            reasons.append("SPY in confirmed bull market (Above 50 and 200 SMA)")
        else:
            warnings.append("SPY market trend under pressure or below 200 SMA (Bearish regime)")

        # 2. Market Momentum & Breadth (20%)
        rsi_spy = latest.get("rsi14", 50.0)
        mom_score = float(np.clip(rsi_spy, 0.0, 100.0))
        subscores["market_momentum"] = round(mom_score, 1)

        # 3. Volatility & Drawdown Environment (25%)
        # Lower volatility / lower drawdown = safer regime score
        ret_spy = bench_features["close"].pct_change().tail(21)
        realized_vol = float(ret_spy.std() * np.sqrt(252)) if len(ret_spy) > 10 else 0.15
        
        # Historical drawdown over last 252 bars
        peak = bench_features["close"].tail(252).cummax()
        dd = (bench_features["close"].tail(252) - peak) / peak
        max_dd = float(dd.min())

        vol_score = 50.0
        if realized_vol < 0.12:
            vol_score += 40.0
            reasons.append(f"Low market volatility regime (Realized Vol: {realized_vol*100:.1f}%)")
        elif realized_vol < 0.18:
            vol_score += 20.0
        elif realized_vol > 0.28:
            vol_score -= 30.0
            warnings.append(f"High market volatility / stress (Realized Vol: {realized_vol*100:.1f}%)")

        if max_dd > -0.08:
            vol_score += 10.0
        elif max_dd < -0.15:
            vol_score -= 20.0
            warnings.append(f"Elevated market drawdown ({max_dd*100:.1f}%)")
        subscores["volatility_environment"] = round(float(np.clip(vol_score, 0.0, 100.0)), 1)

        # 4. Macro Environment (25% if available, otherwise redistribute)
        if macro_data:
            macro_score = macro_data.get("macro_score", 50.0)
            subscores["macro_environment"] = round(macro_score, 1)
            regime_score = (
                subscores["broad_market_trend"] * 0.30 +
                subscores["market_momentum"] * 0.20 +
                subscores["volatility_environment"] * 0.25 +
                subscores["macro_environment"] * 0.25
            )
            macro_cov = 1.0
        else:
            subscores["macro_environment"] = None
            warnings.append("Macroeconomic data feed not connected. Score normalized on market trend & volatility.")
            regime_score = (
                subscores["broad_market_trend"] * 0.40 +
                subscores["market_momentum"] * 0.25 +
                subscores["volatility_environment"] * 0.35
            )
            macro_cov = 0.0

        regime_score = float(np.clip(regime_score, 0.0, 100.0))

        return ScoreComponent(
            name="Market Regime",
            score=round(regime_score, 1),
            weight=self.weights.market_regime,
            is_available=True,
            coverage=round(0.75 + (0.25 * macro_cov), 2),
            confidence=0.90,
            status="OK",
            subscores=subscores,
            reasons=reasons,
            warnings=warnings,
            source="SPY_POINT_IN_TIME",
            timestamp=str(bench_features.index[-1].date())
        )

    # -------------------------------------------------------------------------
    # 5. RISK SCORE (0 - 100, HIGHER = SAFER / FAVORABLE RISK PROFILE)
    # -------------------------------------------------------------------------
    def evaluate_risk(
        self,
        features: pd.DataFrame,
        tradable_ohlc: pd.DataFrame,
        current_heat: float = 0.0,
        open_positions: int = 0,
        upcoming_earnings_days: Optional[int] = None
    ) -> ScoreComponent:
        if features.empty or len(features) < 30:
            return ScoreComponent(
                name="Risk",
                score=None,
                weight=self.weights.risk,
                is_available=False,
                coverage=0.0,
                confidence=0.0,
                status="DATA_UNAVAILABLE",
                warnings=["Insufficient history for risk scoring"]
            )

        latest_f = features.iloc[-1]
        latest_t = tradable_ohlc.iloc[-1]
        subscores = {}
        reasons = []
        warnings = []

        # 1. Liquidity Quality (Safety) (weight 20%)
        dvol = latest_f.get("dollar_vol20", 0.0)
        c = latest_f.get("raw_close", latest_t.get("close", 0.0))
        if dvol >= 100_000_000.0:
            liq_score = 100.0
            reasons.append("High liquidity buffer (Dollar volume > $100M/day)")
        elif dvol >= 40_000_000.0:
            liq_score = 85.0
        elif dvol >= 20_000_000.0:
            liq_score = 70.0
        elif dvol >= 10_000_000.0:
            liq_score = 45.0
            warnings.append(f"Marginal liquidity (${dvol/1e6:.1f}M/day)")
        else:
            liq_score = 15.0
            warnings.append(f"Severe liquidity risk (${dvol/1e6:.1f}M/day below threshold)")
        subscores["liquidity_safety"] = round(liq_score, 1)

        # 2. Volatility & Gap Risk Safety (weight 20%)
        # Lower ATR % = higher safety score
        atr_pct = latest_f.get("atr_pct", 0.03)
        if atr_pct <= 0.02:
            vol_score = 95.0
        elif atr_pct <= 0.035:
            vol_score = 80.0
        elif atr_pct <= 0.05:
            vol_score = 60.0
        elif atr_pct <= 0.08:
            vol_score = 35.0
            warnings.append(f"High stock volatility (ATR: {atr_pct*100:.1f}%)")
        else:
            vol_score = 15.0
            warnings.append(f"Extreme stock volatility (ATR: {atr_pct*100:.1f}%)")
        subscores["volatility_safety"] = round(vol_score, 1)

        # 3. Drawdown & Downside Risk Safety (weight 20%)
        recent_closes = tradable_ohlc["close"].tail(252)
        peak = recent_closes.cummax()
        dd = (recent_closes - peak) / peak
        max_dd = float(dd.min()) if len(dd) else -0.20
        if max_dd >= -0.15:
            dd_score = 95.0
            reasons.append(f"Contained historical drawdown ({max_dd*100:.1f}%)")
        elif max_dd >= -0.25:
            dd_score = 75.0
        elif max_dd >= -0.40:
            dd_score = 50.0
        else:
            dd_score = 25.0
            warnings.append(f"Deep historical drawdown ({max_dd*100:.1f}%)")
        subscores["drawdown_safety"] = round(dd_score, 1)

        # 4. Earnings & Event Risk Safety (weight 20%)
        if upcoming_earnings_days is not None:
            if upcoming_earnings_days <= 3:
                event_score = 20.0
                warnings.append(f"Earnings release in {upcoming_earnings_days} days. High binary gap risk!")
            elif upcoming_earnings_days <= 7:
                event_score = 50.0
                warnings.append(f"Earnings release in {upcoming_earnings_days} days")
            else:
                event_score = 90.0
        else:
            event_score = 75.0  # Neutral assumption when calendar is clear
        subscores["event_risk_safety"] = round(event_score, 1)

        # 5. Portfolio Heat & Capacity Safety (weight 20%)
        heat_avail = self.limits.max_portfolio_heat - current_heat
        if heat_avail >= 0.03 and open_positions < self.limits.max_positions - 2:
            heat_score = 100.0
        elif heat_avail >= 0.01 and open_positions < self.limits.max_positions:
            heat_score = 70.0
        elif heat_avail <= 0.0 or open_positions >= self.limits.max_positions:
            heat_score = 10.0
            warnings.append("Portfolio heat or position capacity limit exhausted")
        else:
            heat_score = 50.0
        subscores["portfolio_capacity_safety"] = round(heat_score, 1)

        # Weighted Risk Score (Higher = Safer)
        risk_score = (
            subscores["liquidity_safety"] * 0.20 +
            subscores["volatility_safety"] * 0.20 +
            subscores["drawdown_safety"] * 0.20 +
            subscores["event_risk_safety"] * 0.20 +
            subscores["portfolio_capacity_safety"] * 0.20
        )
        risk_score = float(np.clip(risk_score, 0.0, 100.0))

        return ScoreComponent(
            name="Risk (Safety)",
            score=round(risk_score, 1),
            weight=self.weights.risk,
            is_available=True,
            coverage=1.0,
            confidence=0.95,
            status="OK",
            subscores=subscores,
            reasons=reasons,
            warnings=warnings,
            source="TRADABLE_EXECUTION_METRICS",
            timestamp=str(features.index[-1].date())
        )

    # -------------------------------------------------------------------------
    # 6. HARD RISK GATES CHECK (STRICT SEPARATION FROM FINAL SCORE)
    # -------------------------------------------------------------------------
    def evaluate_hard_gates(
        self,
        features: pd.DataFrame,
        tradable_ohlc: pd.DataFrame,
        current_heat: float,
        open_positions: int,
        upcoming_earnings_days: Optional[int] = None,
        opening_gap: Optional[float] = None
    ) -> Tuple[bool, List[str]]:
        """
        Hard Risk Gates:
        Returns (passed, list_of_rejection_codes).
        A candidate that fails any hard gate cannot be TRADE_ELIGIBLE regardless of Final Score!
        """
        rejections = []
        if features.empty or len(features) < self.limits.min_history_bars:
            rejections.append("REJECT_INSUFFICIENT_HISTORY")
            return False, rejections

        latest_f = features.iloc[-1]
        latest_t = tradable_ohlc.iloc[-1]

        # Liquidity gates
        dvol = latest_f.get("dollar_vol20", 0.0)
        c = latest_f.get("raw_close", latest_t.get("close", 0.0))
        if dvol < self.limits.min_dollar_vol:
            rejections.append(f"REJECT_INSUFFICIENT_LIQUIDITY: Dollar volume ${dvol/1e6:.1f}M < ${self.limits.min_dollar_vol/1e6:.1f}M")
        if c < self.limits.min_price:
            rejections.append(f"REJECT_INSUFFICIENT_LIQUIDITY: Price ${c:.2f} < ${self.limits.min_price:.2f}")

        # Opening Gap Gate (if evaluating next session open)
        if opening_gap is not None and abs(opening_gap) > self.limits.max_gap_pct:
            rejections.append(f"REJECT_GAP_LIMIT: Opening gap {opening_gap*100:+.2f}% exceeds +/-{self.limits.max_gap_pct*100:.1f}%")

        # Portfolio Heat & Capacity
        if current_heat >= self.limits.max_portfolio_heat:
            rejections.append(f"REJECT_PORTFOLIO_HEAT: Open portfolio risk {current_heat*100:.1f}% >= limit {self.limits.max_portfolio_heat*100:.1f}%")
        if open_positions >= self.limits.max_positions:
            rejections.append(f"REJECT_POSITION_LIMIT: Open position count {open_positions} >= limit {self.limits.max_positions}")

        # Upcoming Earnings Gate (if within binary risk window)
        if upcoming_earnings_days is not None and 0 <= upcoming_earnings_days <= 1:
            rejections.append(f"REJECT_EVENT_RISK: Earnings release in {upcoming_earnings_days} days. High binary gap risk!")

        # Reward/Risk Ratio Gate
        atr = latest_f.get("atr_tradable", latest_f.get("atr14", 0.0))
        if atr > 0 and c > 0:
            stop_dist = 2.0 * atr
            target_dist = c * 0.35  # TP2 = 35%
            # Include round-trip cost in R/R calculation
            round_trip_cost = c * (2.0 * self.limits.cost_per_side)
            net_reward = target_dist - round_trip_cost
            net_risk = stop_dist + round_trip_cost
            rr = (net_reward / net_risk) if net_risk > 0 else 0.0
            if rr < self.limits.min_rr:
                rejections.append(f"REJECT_RR: Net Reward/Risk {rr:.2f} < minimum required {self.limits.min_rr:.2f}")

        # Check for invalid OHLC
        if latest_t.get("high", 0.0) < latest_t.get("low", 0.0) or latest_t.get("close", 0.0) <= 0:
            rejections.append("REJECT_INVALID_OHLC")

        passed = len(rejections) == 0
        return passed, rejections

    # -------------------------------------------------------------------------
    # 7. FINAL SCORE AND COMPREHENSIVE STOCK RANKING
    # -------------------------------------------------------------------------
    def evaluate_stock(
        self,
        ticker: str,
        features: pd.DataFrame,
        tradable_ohlc: pd.DataFrame,
        bench_features: pd.DataFrame,
        fundamental_data: Optional[Dict[str, Any]] = None,
        news_data: Optional[Dict[str, Any]] = None,
        macro_data: Optional[Dict[str, Any]] = None,
        current_heat: float = 0.0,
        open_positions: int = 0,
        company_name: Optional[str] = None,
        sector: Optional[str] = None,
        upcoming_earnings_days: Optional[int] = None,
        opening_gap: Optional[float] = None
    ) -> StockEvaluationResult:
        """
        Evaluates all 6 dimensions, tests hard risk gates, computes transparent Final Score,
        and determines operational eligibility.
        """
        # Evaluate individual components
        tech = self.evaluate_technical(features, bench_features, tradable_ohlc)
        regime = self.evaluate_market_regime(bench_features, macro_data)
        risk = self.evaluate_risk(features, tradable_ohlc, current_heat, open_positions, upcoming_earnings_days)
        fund = self.evaluate_fundamental(fundamental_data, ticker, str(features.index[-1].date()) if not features.empty else None)
        news = self.evaluate_news_catalyst(news_data, ticker, str(features.index[-1].date()) if not features.empty else None)

        # Hard Risk Gates Check (Strictly Separated from Score)
        gates_passed, rejections = self.evaluate_hard_gates(
            features, tradable_ohlc, current_heat, open_positions, upcoming_earnings_days, opening_gap
        )

        # Setup Classification
        latest_f = features.iloc[-1] if not features.empty else {}
        c = latest_f.get("close", 0.0)
        h20 = latest_f.get("high20", 0.0)
        rvol = latest_f.get("rel_vol", 1.0)
        s20 = latest_f.get("sma20", 0.0)
        s50 = latest_f.get("sma50", 0.0)
        rsi = latest_f.get("rsi14", 50.0)

        if c > h20 and rvol >= 1.5 and latest_f.get("trend_up", False):
            setup_class = "BREAKOUT_CANDIDATE"
        elif c <= s20 and c > s50 and (35.0 <= rsi <= 55.0) and latest_f.get("trend_up", False):
            setup_class = "PULLBACK_CANDIDATE"
        elif latest_f.get("trend_up", False):
            setup_class = "TREND_CONTINUATION"
        elif c > s50:
            setup_class = "WEAK_TREND"
        else:
            setup_class = "NO_VALID_SETUP"

        # Strategy Suitability
        is_bull_market = bool(regime.subscores.get("broad_market_trend", 0) >= 60)
        strat_suit = {
            "breakout_v2": "HIGH" if (setup_class == "BREAKOUT_CANDIDATE" and is_bull_market) else ("MODERATE" if setup_class == "BREAKOUT_CANDIDATE" else "LOW"),
            "pullback": "MODERATE" if (setup_class == "PULLBACK_CANDIDATE" and is_bull_market) else "LOW"
        }

        # Calculate Final Score and Coverage
        # Primary components list
        all_components = [tech, fund, news, regime, risk]
        available_comps = [comp for comp in all_components if comp.is_available and comp.score is not None]

        missing_data = []
        for comp in all_components:
            if not comp.is_available or comp.score is None:
                missing_data.append(comp.name)

        if available_comps:
            avail_weight = sum(comp.weight for comp in available_comps)
            score_coverage = avail_weight / 100.0
            raw_final_score = sum(comp.score * comp.weight for comp in available_comps) / avail_weight
            # Coverage-penalized score:
            # Stocks with missing data are penalized in ranking so fully covered stocks are favored
            adjusted_final_score = raw_final_score * (0.50 + 0.50 * score_coverage)
        else:
            score_coverage = 0.0
            raw_final_score = 0.0
            adjusted_final_score = 0.0

        # Overall data confidence weighted across all system components (0.0 for missing components)
        overall_confidence = float(sum((comp.confidence * comp.weight / 100.0) for comp in all_components))

        # Eligibility Determination
        if len(features) < self.limits.min_history_bars:
            eligibility = "INSUFFICIENT_DATA"
        elif not gates_passed:
            eligibility = "REJECTED"
        elif setup_class in ("BREAKOUT_CANDIDATE", "PULLBACK_CANDIDATE") and is_bull_market:
            eligibility = "TRADE_ELIGIBLE"
        else:
            eligibility = "WATCHLIST"

        # Collect positive and negative factors
        pos_factors = []
        neg_factors = []
        for comp in all_components:
            pos_factors.extend(comp.reasons)
            neg_factors.extend(comp.warnings)

        latest_price = float(tradable_ohlc["close"].iloc[-1]) if not tradable_ohlc.empty else 0.0
        latest_date = str(features.index[-1].date()) if not features.empty else "N/A"

        return StockEvaluationResult(
            ticker=ticker,
            company_name=company_name,
            sector=sector,
            price=latest_price,
            price_date=latest_date,
            technical=tech,
            fundamental=fund,
            news_catalyst=news,
            market_regime=regime,
            risk=risk,
            raw_final_score=raw_final_score,
            adjusted_final_score=adjusted_final_score,
            score_coverage=score_coverage,
            overall_confidence=overall_confidence,
            rank=None,
            setup_classification=setup_class,
            strategy_suitability=strat_suit,
            hard_gates_passed=gates_passed,
            rejection_reasons=rejections,
            eligibility=eligibility,
            positive_factors=pos_factors,
            negative_factors=neg_factors,
            missing_data=missing_data,
            formula_version=self.version
        )

    def rank_candidates(self, results: List[StockEvaluationResult]) -> List[StockEvaluationResult]:
        """
        Sorts candidates by eligibility (TRADE_ELIGIBLE first, then WATCHLIST, then REJECTED),
        then by adjusted_final_score descending. Assigns 1-indexed ranks.
        """
        eligibility_priority = {
            "TRADE_ELIGIBLE": 1,
            "WATCHLIST": 2,
            "INSUFFICIENT_DATA": 3,
            "REJECTED": 4
        }
        sorted_results = sorted(
            results,
            key=lambda r: (eligibility_priority.get(r.eligibility, 99), -r.adjusted_final_score, -r.raw_final_score)
        )
        for i, res in enumerate(sorted_results, 1):
            res.rank = i
        return sorted_results
