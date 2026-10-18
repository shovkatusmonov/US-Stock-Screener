"""
run_scoring_pipeline.py - Full Six-Score Evaluation, Ranking and Hard Risk Gating Pipeline.

Executes the 6-score quantitative engine across all tickers in screener.db:
1. Technical Score (0-100)
2. Fundamental Score (0-100, marked unavailable if no verified data)
3. News / Catalyst Score (0-100, marked unavailable if no verified data)
4. Market Regime Score (0-100)
5. Risk Score (0-100, Higher = Safer)
6. Final Score (0-100, weighted with coverage and confidence)

Applies hard risk filters:
Separates candidates into TRADE_ELIGIBLE, WATCHLIST, REJECTED, and INSUFFICIENT_DATA.
Outputs structured JSON and CSV reports.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
import pandas as pd
import numpy as np

import features as ft
import strategies as st
from scoring_engine import ScoringEngine, ScoringWeights, HardRiskLimits
from data_provider import DataProvider

def run_scoring(
    db_path: str = "data/screener.db",
    out_dir: str = "reports",
    max_tickers: Optional[int] = None
):
    conn = sqlite3.connect(db_path)
    
    # 1. Load benchmark SPY
    bench_raw = ft.load_prices(conn, "SPY")
    if bench_raw.empty:
        print("[ERROR] SPY benchmark data missing.")
        conn.close()
        return

    bench_f = ft.build_features(bench_raw, bench_raw)
    latest_date = bench_f.index[-1]
    print(f"Scoring as of date: {latest_date.date()} (SPY: ${bench_f['close'].iloc[-1]:.2f})")

    # 2. Get universe tickers
    tickers = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT ticker FROM prices WHERE ticker != 'SPY' ORDER BY ticker"
        ).fetchall()
    ]
    if max_tickers:
        tickers = tickers[:max_tickers]

    print(f"Total tickers to evaluate: {len(tickers)}")
    engine = ScoringEngine()
    provider = DataProvider(conn)

    results = []
    as_of_str = str(latest_date.date())
    for i, t in enumerate(tickers, 1):
        raw = ft.load_prices(conn, t)
        if len(raw) < 50:
            continue
        f = ft.build_features(raw, bench_raw)
        if f.empty or latest_date not in f.index:
            continue
        trd = ft.get_tradable_ohlc(raw).reindex(f.index).ffill()

        # Query Point-in-time Fundamental and News data from provider
        # Strictly enforces Point-in-time (filing_date <= as_of_date) and zero fake data
        cur_price = float(f["close"].iloc[-1])
        fund_data = provider.get_point_in_time_fundamentals(t, as_of_str, current_price=cur_price)
        news_data = provider.get_recent_news_and_events(t, as_of_str)

        # Evaluate ticker through 6-score engine
        res = engine.evaluate_stock(
            ticker=t,
            features=f,
            tradable_ohlc=trd,
            bench_features=bench_f,
            fundamental_data=fund_data,
            news_data=news_data,
            macro_data=None,
            current_heat=0.0,
            open_positions=0
        )
        results.append(res)

    conn.close()

    # 3. Rank candidates
    ranked_results = engine.rank_candidates(results)
    print(f"Successfully evaluated and ranked {len(ranked_results)} tickers.")

    # 4. Generate Machine-Readable Outputs (JSON & CSV)
    out_path = Path(out_dir)
    out_path.mkdir(exist_ok=True)
    
    dict_list = [r.to_dict() for r in ranked_results]
    json_path = out_path / "scored_candidates.json"
    with open(json_path, "w", encoding="utf-8") as fp:
        json.dump(dict_list, fp, indent=2)

    df_csv = pd.DataFrame(dict_list)
    csv_path = out_path / "scored_candidates.csv"
    df_csv.to_csv(csv_path, index=False)

    print(f"[SAVED] Machine-readable JSON: {json_path}")
    print(f"[SAVED] Machine-readable CSV:  {csv_path}")

    # 5. Display Summary Breakdown
    eligible = [r for r in ranked_results if r.eligibility == "TRADE_ELIGIBLE"]
    watchlist = [r for r in ranked_results if r.eligibility == "WATCHLIST"]
    rejected = [r for r in ranked_results if r.eligibility == "REJECTED"]
    insufficient = [r for r in ranked_results if r.eligibility == "INSUFFICIENT_DATA"]

    print("\n" + "=" * 95)
    print(f"CANDIDATE CLASSIFICATION SUMMARY (AS OF {latest_date.date()})")
    print("=" * 95)
    print(f"TRADE_ELIGIBLE:    {len(eligible):>4} tickers (Passed all hard gates, valid setup, bullish regime)")
    print(f"WATCHLIST:         {len(watchlist):>4} tickers (Passed hard gates, uptrend or setup awaiting trigger)")
    print(f"REJECTED:          {len(rejected):>4} tickers (Failed hard liquidity, gap, R/R, or history gates)")
    print(f"INSUFFICIENT_DATA: {len(insufficient):>4} tickers (< 260 bars of history)")
    print("-" * 95)

    if eligible:
        print("\nTOP TRADE-ELIGIBLE CANDIDATES:")
        hdr = f"{'Rank':<5} {'Ticker':<7} {'Price':>8} {'Tech':>6} {'Regime':>7} {'Risk':>6} {'RawFinal':>9} {'AdjFinal':>9} {'Coverage':>9} {'Setup':<18}"
        print(hdr)
        print("-" * len(hdr))
        for r in eligible[:10]:
            print(f"{r.rank:<5} {r.ticker:<7} ${r.price:>7.2f} {r.technical.score:>6.1f} {r.market_regime.score:>7.1f} {r.risk.score:>6.1f} {r.raw_final_score:>9.1f} {r.adjusted_final_score:>9.1f} {r.score_coverage*100:>8.1f}% {r.setup_classification:<18}")

    if rejected:
        print("\nSAMPLE HARD-GATE REJECTIONS (Demonstrating Hard Gates overriding scores):")
        for r in rejected[:5]:
            reasons_str = "; ".join(r.rejection_reasons)
            print(f"  - {r.ticker:<6} (RawFinal: {r.raw_final_score:.1f}, Tech: {r.technical.score:.1f}) -> REJECTED: {reasons_str}")

    return ranked_results

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default="data/screener.db")
    parser.add_argument("--max-tickers", type=int, default=None)
    args = parser.parse_args()
    run_scoring(db_path=args.db_path, max_tickers=args.max_tickers)
