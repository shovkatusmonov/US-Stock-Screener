"""test_robustness.py - run_robustness yordamchi funksiyalari."""
import numpy as np
import pandas as pd
import pytest

import run_robustness as rr


def test_regime_timed_spy_all_bull_matches_spy_with_one_day_lag():
    idx = pd.bdate_range("2024-01-01", periods=6)
    px = pd.Series([100, 101, 102, 101, 103, 104.0], index=idx)
    eq, pos = rr.regime_timed_spy(px, pd.Series(True, index=idx))
    assert pos.iloc[0] == 0.0 and (pos.iloc[1:] == 1.0).all()
    assert eq.iloc[-1] / 10_000 == pytest.approx(px.iloc[-1] / px.iloc[0])


def test_regime_timed_spy_bear_is_flat_and_uses_previous_day_signal():
    idx = pd.bdate_range("2024-01-01", periods=5)
    px = pd.Series([100, 90, 80, 70, 60.0], index=idx)
    eq, _ = rr.regime_timed_spy(px, pd.Series(False, index=idx))
    assert (eq == 10_000.0).all()
    # bugungi rejim kechagi pozitsiyani belgilaydi (look-ahead yo'q)
    reg = pd.Series([True, False, False, False, False], index=idx)
    eq2, pos2 = rr.regime_timed_spy(px, reg)
    assert pos2.tolist() == [0.0, 1.0, 0.0, 0.0, 0.0]
    assert eq2.iloc[1] == pytest.approx(9_000.0)


def test_bootstrap_is_deterministic_and_directional():
    rng = np.random.default_rng(1)
    good = rng.normal(0.002, 0.005, 400)
    bad = rng.normal(-0.002, 0.005, 400)
    assert np.array_equal(rr.bootstrap_sharpe(good, 200, seed=3), rr.bootstrap_sharpe(good, 200, seed=3))
    assert (rr.bootstrap_sharpe(good, 500) > 0).mean() > 0.99
    assert (rr.bootstrap_sharpe(bad, 500) > 0).mean() < 0.01
    # bir xil qatorlar -> ortiqcha daromad 0
    assert np.allclose(rr.bootstrap_excess(good, good, 100), 0.0)
    # yaxshi vs yomon -> ustunlik deyarli 100%
    excess_pos = rr.bootstrap_excess(good, bad, 500, block=10, seed=42)
    assert (excess_pos > 0).mean() > 0.99
    # yomon vs yaxshi -> ustunlik deyarli 0%
    excess_neg = rr.bootstrap_excess(bad, good, 500, block=10, seed=42)
    assert (excess_neg > 0).mean() < 0.01
