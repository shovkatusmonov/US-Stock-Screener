"""
test_bootstrap_verification.py - Independent Verification and Synthetic Tests for Block-Bootstrap Excess Returns.
"""
import pytest
import numpy as np
import pandas as pd

from run_robustness import bootstrap_excess, bootstrap_sharpe, _boot_idx


def test_bootstrap_index_bounds_and_blocks():
    n = 100
    n_boot = 500
    block = 10
    idx = _boot_idx(n, n_boot, block, seed=42)
    assert idx.shape == (n_boot, n)
    assert idx.min() >= 0
    assert idx.max() < n
    # Verify block continuity (adjacent elements within block differ by 1)
    diffs = np.diff(idx, axis=1)
    # Most transitions within a block have diff == 1
    within_block_diffs = (diffs == 1).mean()
    assert within_block_diffs > 0.85


def test_bootstrap_reproducibility_with_seed():
    rng = np.random.default_rng(123)
    r1 = rng.normal(0.0005, 0.01, 200)
    r2 = rng.normal(0.0002, 0.01, 200)
    
    b1 = bootstrap_excess(r1, r2, n_boot=500, block=10, seed=99)
    b2 = bootstrap_excess(r1, r2, n_boot=500, block=10, seed=99)
    assert np.array_equal(b1, b2)

    # Different seed produces different samples
    b3 = bootstrap_excess(r1, r2, n_boot=500, block=10, seed=100)
    assert not np.array_equal(b1, b3)


def test_bootstrap_clearly_positive_excess_return():
    # Strategy returns +0.2% daily on average (~50% annualized), benchmark returns 0.0%
    rng = np.random.default_rng(42)
    strat_ret = rng.normal(0.0020, 0.008, 300)
    bench_ret = rng.normal(0.0000, 0.008, 300)
    
    res = bootstrap_excess(strat_ret, bench_ret, n_boot=2000, block=10, seed=42)
    p_gt_0 = (res > 0).mean()
    assert p_gt_0 >= 0.99
    assert res.mean() > 0.35  # Annualized excess > 35%


def test_bootstrap_clearly_negative_excess_return():
    # Strategy loses -0.2% daily on average, benchmark is neutral
    rng = np.random.default_rng(42)
    strat_ret = rng.normal(-0.0020, 0.008, 300)
    bench_ret = rng.normal(0.0000, 0.008, 300)
    
    res = bootstrap_excess(strat_ret, bench_ret, n_boot=2000, block=10, seed=42)
    p_gt_0 = (res > 0).mean()
    assert p_gt_0 <= 0.01
    assert res.mean() < -0.35


def test_bootstrap_identical_series():
    # Identical return series must have exactly zero excess in every single bootstrap draw
    rng = np.random.default_rng(42)
    rets = rng.normal(0.0005, 0.01, 250)
    res = bootstrap_excess(rets, rets, n_boot=500, block=10, seed=42)
    assert np.allclose(res, 0.0)


def test_bootstrap_date_alignment_handling():
    # Test date alignment using pandas Series with mismatched dates
    dates1 = pd.bdate_range("2024-01-01", periods=100)
    dates2 = pd.bdate_range("2024-01-15", periods=100)  # Starts 2 weeks later
    
    s_series = pd.Series(np.random.normal(0.001, 0.01, 100), index=dates1)
    b_series = pd.Series(np.random.normal(0.0005, 0.01, 100), index=dates2)
    
    # Proper alignment on common dates:
    common_idx = s_series.index.intersection(b_series.index)
    assert len(common_idx) > 0
    s_aligned = s_series.loc[common_idx]
    b_aligned = b_series.loc[common_idx]
    
    assert len(s_aligned) == len(b_aligned)
    res = bootstrap_excess(s_aligned.values, b_aligned.values, n_boot=200, block=5, seed=42)
    assert len(res) == 200
    assert not np.isnan(res).any()


def test_bootstrap_handles_edge_cases():
    # Series with length smaller than block size
    short_a = np.array([0.01, 0.02, -0.01])
    short_b = np.array([0.005, 0.01, 0.00])
    res = bootstrap_excess(short_a, short_b, n_boot=100, block=10, seed=42)
    assert len(res) == 100
    assert not np.isnan(res).any()
