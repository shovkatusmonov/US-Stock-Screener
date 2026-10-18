import numpy as np
import pandas as pd
import pytest

import features as ft


def make_df(n=300, seed=0, start=100.0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    close = start * np.cumprod(1 + rng.normal(0.0005, 0.01, n))
    high = close * (1 + rng.uniform(0, 0.01, n))
    low = close * (1 - rng.uniform(0, 0.01, n))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    return pd.DataFrame(
        {
            "open": open_, "high": high, "low": low, "close": close,
            "adj_close": close, "volume": rng.integers(1_000_000, 2_000_000, n).astype(float),
        },
        index=idx,
    )


def test_sma_known_values():
    s = pd.Series([1.0, 2, 3, 4, 5])
    out = ft.sma(s, 3)
    assert out.isna().tolist() == [True, True, False, False, False]
    assert out.iloc[2:].tolist() == [2.0, 3.0, 4.0]


def test_atr_constant_range():
    # har kuni high-low = 2, gap yo'q -> ATR = 2
    n = 40
    close = pd.Series(np.full(n, 100.0))
    high, low = close + 1, close - 1
    out = ft.atr(high, low, close, 14)
    assert out.iloc[:13].isna().all()
    assert out.iloc[-1] == pytest.approx(2.0)


def test_atr_uses_gap():
    high = pd.Series([10.0, 20.0])
    low = pd.Series([9.0, 19.0])
    close = pd.Series([10.0, 20.0])
    tr = ft.true_range(high, low, close)
    assert tr.iloc[1] == pytest.approx(10.0)  # |high - prev_close| = 10


def test_rsi_all_up_is_100_and_all_down_is_0():
    up = pd.Series(np.arange(1.0, 60.0))
    down = pd.Series(np.arange(60.0, 1.0, -1.0))
    assert ft.rsi(up, 14).iloc[-1] == pytest.approx(100.0)
    assert ft.rsi(down, 14).iloc[-1] == pytest.approx(0.0)


def test_rsi_bounds_random():
    r = ft.rsi(make_df()["close"], 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_relative_strength_known():
    idx = pd.bdate_range("2024-01-01", periods=3)
    stock = pd.Series([100.0, 110.0, 121.0], index=idx)  # +10%, +10%
    bench = pd.Series([100.0, 105.0, 110.25], index=idx)  # +5%, +5%
    out = ft.relative_strength(stock, bench, 1)
    assert out.iloc[1] == pytest.approx(0.05)


def test_adjust_ohlc_factor():
    df = make_df(5)
    df["adj_close"] = df["close"] * 0.5
    adj = ft.adjust_ohlc(df)
    assert adj["high"].iloc[0] == pytest.approx(df["high"].iloc[0] * 0.5)
    assert adj["close"].iloc[0] == pytest.approx(df["adj_close"].iloc[0])


def test_no_lookahead():
    """Kelajak ma'lumotini qirqib tashlasak, o'tmish qiymatlari o'zgarmasligi kerak."""
    df, bench = make_df(300, 1), make_df(300, 2)
    full = ft.build_features(df, bench)
    cut = 250
    part = ft.build_features(df.iloc[:cut], bench.iloc[:cut])
    cols = [c for c in full.columns if c != "trend_up"]
    pd.testing.assert_frame_equal(
        full.iloc[:cut][cols], part[cols], check_exact=False, rtol=1e-10, atol=1e-12
    )



def test_no_lookahead_when_future_adjustment_factor_appears():
    df = make_df(300, seed=11)
    bench = make_df(300, seed=12)

    # Dastlabki 100 kun uchun adjustment koeffitsiyenti yo'q.
    df.loc[df.index[:100], "adj_close"] = np.nan
    bench.loc[bench.index[:100], "adj_close"] = np.nan

    # Faqat kelajakda koeffitsiyent mavjud bo'ladi.
    df.loc[df.index[100]:, "adj_close"] = df.loc[df.index[100]:, "close"] * 0.5
    bench.loc[bench.index[100]:, "adj_close"] = bench.loc[bench.index[100]:, "close"] * 0.5

    full = ft.build_features(df, bench)
    cut = 100
    part = ft.build_features(df.iloc[:cut], bench.iloc[:cut])

    cols = ["close", "high", "low", "sma20", "sma50", "atr14",
            "rsi14", "ret5", "rs60", "high20", "low20"]

    for col in cols:
        a = full[col].iloc[:cut].to_numpy(dtype=float)
        b = part[col].to_numpy(dtype=float)
        assert np.allclose(a, b, equal_nan=True, rtol=1e-10, atol=1e-12), col

def test_insufficient_history_gives_nan_not_zero():
    f = ft.build_features(make_df(100), make_df(100, 3))
    assert f["sma200"].isna().all()
    assert not f["trend_up"].any()  # NaN solishtirish -> False, yaxshi ball berilmaydi


def test_dollar_volume_uses_raw_close():
    df = make_df(60)
    df["adj_close"] = df["close"] * 0.5  # dividend tuzatishi
    f = ft.build_features(df, make_df(60, 4))
    expected = (df["close"] * df["volume"]).rolling(20).mean()
    pd.testing.assert_series_equal(f["dollar_vol20"], expected, check_names=False)



def test_adjust_ohlc_missing_adj_close_does_not_use_future_factor():
    df = make_df(20)
    df["adj_close"] = df["close"] * 0.97
    df.loc[df.index[:3], "adj_close"] = np.nan

    adjusted = ft.adjust_ohlc(df)

    assert adjusted[["open", "high", "low", "close"]].notna().all().all()
    # Dastlabki kunlarda kelajakdagi 0.97 koeffitsiyenti ishlatilmasligi kerak.
    assert adjusted["close"].iloc[0] == pytest.approx(df["close"].iloc[0])


def test_load_prices_filters_impossible_ohlc_rows():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE prices (
        ticker TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
        adj_close REAL, volume REAL, source TEXT, fetched_at TEXT
    )""")
    conn.executemany(
        "INSERT INTO prices VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("AAA", "2026-10-13", 100, 105, 98, 103, 103, 1000, "test", "now"),
            ("AAA", "2026-10-14", 110, 109, 100, 105, 105, 1000, "test", "now"),
        ],
    )
    loaded = ft.load_prices(conn, "AAA")
    assert len(loaded) == 1
    assert loaded.iloc[0]["close"] == 103
