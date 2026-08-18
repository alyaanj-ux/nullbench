"""Tests for the stationary block bootstrap null.

The properties that matter, each with a mutant that was proven to break it
before shipping (see NIGHT_LOG):

  * determinism ACROSS PROCESSES (blake2b seeds, never hash())
  * cross-sectional correlation preserved (joint blocks; per-symbol
    resampling would re-narrow the band)
  * resampled moments match the source (it is the same distribution,
    reshuffled)
  * block lengths actually geometric with the configured mean
  * bars structurally valid, with real overnight gaps surviving
  * the engine still fills at the next open on resampled bars
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import pytest

from src.bootstrap_null import (EXPECTED_BLOCK_LEN, bootstrap_seed,
                                resample_universe,
                                stationary_bootstrap_indices)
from src.data import synthetic_universe

SYMS = ["A", "B", "C", "D"]


def _universe(n=400, seed=5):
    return synthetic_universe(SYMS, n=n, seed=seed)


def _digest(uni):
    h = hashlib.sha256()
    for s in sorted(uni):
        h.update(uni[s]["close"].to_numpy(dtype="float64").tobytes())
    return h.hexdigest()


_CHILD = (
    "import sys; sys.path.insert(0, {root!r});"
    "from tests.test_bootstrap import _universe, _digest;"
    "from src.bootstrap_null import resample_universe;"
    "print(_digest(resample_universe(_universe(), trial=3)))"
)


def _digest_in_subprocess(hash_seed):
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = hash_seed
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD.format(root=str(ROOT))],
        capture_output=True, text=True, encoding="utf-8", env=env,
        cwd=str(ROOT), timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip().splitlines()[-1]


def test_resample_is_identical_across_processes():
    """blake2b seeding, never hash(): two interpreters with different string
    hash salts must produce the same draw for the same trial number."""
    assert _digest_in_subprocess("1") == _digest_in_subprocess("2")


def test_different_trials_give_different_draws():
    uni = _universe()
    assert _digest(resample_universe(uni, 0)) != _digest(resample_universe(uni, 1))


def test_cross_sectional_correlation_is_preserved():
    """THE property. Joint blocks keep symbols moving together; independent
    per-symbol resampling would diversify the null's variance away and
    re-narrow the band — the exact bug the correlated GBM null fixed."""
    uni = _universe(n=600)

    def mean_corr(u):
        rets = pd.DataFrame({s: u[s]["close"].pct_change() for s in u}).dropna()
        c = rets.corr().to_numpy()
        return float(c[np.triu_indices_from(c, k=1)].mean())

    src = mean_corr(uni)
    res = np.mean([mean_corr(resample_universe(uni, t)) for t in range(4)])
    assert abs(res - src) < 0.1, (
        f"source pairwise corr {src:.3f}, resampled {res:.3f} — "
        f"correlation not preserved; are the blocks still joint?"
    )


def test_resampled_moments_match_source():
    """Same returns, reshuffled: per-symbol vol must match closely and the
    mean must sit within block-bootstrap sampling error."""
    uni = _universe(n=600)
    for sym in SYMS:
        src = np.log(uni[sym]["close"]).diff().dropna()
        vols, means = [], []
        for t in range(8):
            r = np.log(resample_universe(uni, t)[sym]["close"]).diff().dropna()
            vols.append(r.std())
            means.append(r.mean())
        vol_ratio = np.mean(vols) / src.std()
        assert 0.85 < vol_ratio < 1.18, f"{sym}: vol ratio {vol_ratio:.3f}"
        # SE of a block-bootstrap mean is inflated ~sqrt(block); be generous.
        se = src.std() / np.sqrt(len(src) / EXPECTED_BLOCK_LEN)
        assert abs(np.mean(means) - src.mean()) < 4 * se, (
            f"{sym}: mean {np.mean(means):.2e} vs source {src.mean():.2e}"
        )


def test_block_lengths_are_geometric_with_expected_mean():
    rng = np.random.default_rng(bootstrap_seed("block-geometry-test"))
    n_src, n_out = 1000, 6000
    idx = stationary_bootstrap_indices(n_src, n_out, rng, EXPECTED_BLOCK_LEN)

    # A continuation is idx[t] == idx[t-1] + 1 (with circular wrap).
    cont = (idx[1:] == (idx[:-1] + 1) % n_src)
    lengths, run = [], 1
    for c in cont:
        if c:
            run += 1
        else:
            lengths.append(run)
            run = 1
    lengths.append(run)
    mean_len = float(np.mean(lengths))
    # Geometric(p=1/20) has mean 20; with ~300 blocks the SE is ~1.2.
    assert 15.0 < mean_len < 26.0, f"mean block length {mean_len:.1f}"
    assert max(lengths) > EXPECTED_BLOCK_LEN, "no long blocks at all?"


def test_resampled_bars_are_structurally_valid():
    uni = _universe()
    out = resample_universe(uni, 7)
    n_expected = len(uni["A"]) - 1          # components drop the first bar
    for sym, df in out.items():
        assert len(df) == n_expected
        assert df.index.is_monotonic_increasing
        a = df[["open", "high", "low", "close", "volume"]].to_numpy()
        assert np.isfinite(a).all(), f"{sym}: NaN/inf in resampled bars"
        assert (df[["open", "high", "low", "close"]] > 0).all().all()
        assert (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-9).all()
        assert (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-9).all()


def test_overnight_gaps_survive_and_fills_stay_at_the_open():
    """Resampling (overnight, intraday) PAIRS keeps real gaps in the data, so
    the engine's next-open discipline still has something to bite on —
    and every fill must still price at that bar's open."""
    from src.backtest import Backtester
    from src.config import BacktestConfig, Config, DataConfig, RiskConfig
    from src.strategies import build_strategy

    uni = _universe()
    out = resample_universe(uni, 2)

    def gap_std(u, sym):
        df = u[sym]
        return float(np.log(df["open"] / df["close"].shift(1)).dropna().std())

    for sym in SYMS:
        assert gap_std(out, sym) > 0.3 * gap_std(uni, sym), (
            f"{sym}: overnight gaps were destroyed by the resample"
        )

    cfg = Config(symbols=SYMS, data=DataConfig(timeframe="1Day"),
                 backtest=BacktestConfig(initial_cash=10_000.0,
                                         slippage_bps=5.0),
                 risk=RiskConfig(max_position_pct=0.25,
                                 max_gross_exposure=1.0))
    r = Backtester(cfg).run(out, build_strategy("sma_cross", fast=10, slow=50))
    assert len(r.trades) > 0, "no trades on resampled bars — vacuous"
    for _, tr in r.trades.iterrows():
        bar_open = float(out[tr["symbol"]].loc[tr.name, "open"])
        assert tr["ref_price"] == pytest.approx(bar_open), (
            f"fill referenced {tr['ref_price']}, bar open is {bar_open} — "
            f"not filling at the next open"
        )
