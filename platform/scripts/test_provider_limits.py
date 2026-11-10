"""
scripts/test_provider_limits.py - Test and investigate provider history limits.
"""
import sys
import yfinance as yf
import pandas as pd

def test_ticker(sym):
    try:
        t = yf.Ticker(sym)
        df = t.history(period="max", auto_adjust=False)
        if df is None or df.empty:
            print(f"[{sym}] EMPTY / NO DATA")
            return
        first_dt = df.index[0].strftime("%Y-%m-%d")
        last_dt = df.index[-1].strftime("%Y-%m-%d")
        has_adj = "Adj Close" in df.columns
        print(f"[{sym}] SUCCESS: rows={len(df):,}, range={first_dt} to {last_dt}, AdjClose={has_adj}")
    except Exception as e:
        print(f"[{sym}] ERROR: {e}")

if __name__ == "__main__":
    symbols = ["IBM", "AAPL", "SPY", "ARM", "KVUE", "AACO", "AACP", "TWTR"]
    for s in symbols:
        test_ticker(s)
