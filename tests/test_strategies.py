"""Tests for strategy signal generation.

The theme: strategies must produce bounded, aligned, non-forward-looking
weights, and must refuse nonsensical parameters loudly.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import pytest

from src.data import _clean, stable_seed, synthetic_bars
from src.strategies import (REGISTRY, BuyAndHold, MeanReversion, SmaCross,
                            build_strategy)


@pytest.fixture
def bars():
    return _clean(synthetic_bars("TEST", n=500, seed=11))


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_strategy_returns_valid_weights(name, bars):
    strat = build_strategy(name)
    w = strat.generate_weights(bars)

    assert isinstance(w, pd.Series)
    assert w.index.equals(bars.index), f"{name}: index misaligned"
    assert not w.isna().any(), f"{name}: produced NaNs"
    assert (w.abs() <= 1.0 + 1e-9).all(), f"{name}: weight outside [-1, 1]"
    assert np.isfinite(w.values).all()


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_signals_do_not_depend_on_future_bars(name, bars):
    """Truncating the data must not change any earlier signal.

    If a strategy uses a centred window or peeks ahead, the weight at bar 200
    will differ between the full series and a series cut at bar 300.
    """
    strat = build_strategy(name)
    full = strat.generate_weights(bars)
    truncated = strat.generate_weights(bars.iloc[:300])

    pd.testing.assert_series_equal(
        full.iloc[:300], truncated, check_names=False,
        obj=f"{name} leaked future information",
    )


def test_sma_cross_rejects_bad_params():
    with pytest.raises(ValueError):
        SmaCross(fast=100, slow=20)
    with pytest.raises(ValueError):
        SmaCross(fast=50, slow=50)


def test_sma_cross_is_flat_during_warmup(bars):
    strat = SmaCross(fast=10, slow=50)
    w = strat.generate_weights(bars)
    assert (w.iloc[:49] == 0.0).all(), "traded before the slow MA was formed"


def test_sma_cross_goes_long_in_an_uptrend():
    n = 300
    close = np.linspace(100, 200, n)          # monotone uptrend
    idx = pd.bdate_range("2020-01-01", periods=n)
    df = pd.DataFrame({"open": close, "high": close, "low": close,
                       "close": close, "volume": np.full(n, 1e6)}, index=idx)
    w = SmaCross(fast=10, slow=50).generate_weights(df)
    assert w.iloc[-1] == 1.0
    assert w.iloc[60:].mean() > 0.95


def test_mean_reversion_leans_long_when_price_is_depressed():
    n = 120
    close = np.full(n, 100.0)
    close[-1] = 80.0                          # sharp drop on the last bar
    idx = pd.bdate_range("2020-01-01", periods=n)
    df = pd.DataFrame({"open": close, "high": close, "low": close,
                       "close": close, "volume": np.full(n, 1e6)}, index=idx)
    w = MeanReversion(lookback=20, entry_z=1.0).generate_weights(df)
    assert w.iloc[-1] > 0, "should want to buy an unusually cheap price"


def test_mean_reversion_rejects_bad_params():
    with pytest.raises(ValueError):
        MeanReversion(lookback=1)
    with pytest.raises(ValueError):
        MeanReversion(entry_z=2.0, max_z=1.0)


def test_mean_reversion_survives_a_flat_series():
    """Zero standard deviation must not produce NaN or inf weights."""
    n = 100
    close = np.full(n, 50.0)
    idx = pd.bdate_range("2020-01-01", periods=n)
    df = pd.DataFrame({"open": close, "high": close, "low": close,
                       "close": close, "volume": np.full(n, 1e6)}, index=idx)
    w = MeanReversion(lookback=20).generate_weights(df)
    assert not w.isna().any()
    assert np.isfinite(w.values).all()


def test_buy_and_hold_is_always_invested(bars):
    w = BuyAndHold().generate_weights(bars)
    assert (w == 1.0).all()


def test_unknown_strategy_raises():
    with pytest.raises(KeyError):
        build_strategy("definitely_not_a_strategy")


def test_synthetic_data_is_deterministic():
    """Same explicit seed -> same path. Weak: only proves stability within
    one process. See the two tests below for the real guarantee."""
    a = synthetic_bars("AAPL", n=100, seed=5)
    b = synthetic_bars("AAPL", n=100, seed=5)
    pd.testing.assert_frame_equal(a, b)


def test_default_seed_is_stable_across_processes():
    """Regression test for a real bug.

    The default seed used to come from `abs(hash(symbol))`. Python randomises
    string hashing per process (PYTHONHASHSEED), so every run produced
    different "reproducible" data and backtest numbers could not be compared
    between runs.

    These are hard-coded expected values. If the seed derivation changes, this
    test fails loudly instead of silently making results irreproducible.
    """
    assert stable_seed("SPY") == 865570452
    assert stable_seed("AAPL") == 497611092
    assert stable_seed("") == 309448485

    # Different symbols must not collide onto the same path.
    seeds = {stable_seed(s) for s in ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]}
    assert len(seeds) == 5


def test_default_seed_produces_fixed_prices():
    """End-to-end pin: the actual generated series, not just the seed."""
    df = synthetic_bars("SPY", n=200)
    assert df["close"].iloc[0] == pytest.approx(100.37433033344061, rel=1e-9)
    assert df["close"].iloc[-1] == pytest.approx(71.98757699731999, rel=1e-9)


def test_clean_drops_bad_bars():
    idx = pd.bdate_range("2020-01-01", periods=5)
    df = pd.DataFrame(
        {"open": [1, 2, -3, 4, 5], "high": [1, 2, 3, 4, 5],
         "low": [1, 2, 3, 4, 5], "close": [1, 2, 3, np.nan, 5],
         "volume": [1, 1, 1, 1, 1]},
        index=idx, dtype=float,
    )
    out = _clean(df)
    assert len(out) == 3           # drops the negative-open and NaN-close rows
    assert out.index.is_monotonic_increasing
