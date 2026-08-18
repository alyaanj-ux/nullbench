"""Stationary block bootstrap null: resampled REAL returns, not GBM.

Why this exists
---------------
The GBM null asks "what would luck alone produce on a Gaussian random walk?"
Real returns are not Gaussian: they have fat tails (single days that move ten
sigmas of a normal) and volatility clustering (crashes arrive in bunches).
Both widen the spread of outcomes luck can produce, so a GBM-based band is too
narrow on real data — biased toward declaring a fake edge real. That is the
exact error the noise test exists to prevent.

The fix is to build the null out of the real returns themselves, resampled in
blocks so short-range dependence (the clustering) survives:

  * Politis–Romano (1994) stationary bootstrap: blocks of geometrically
    distributed length (expected ~20 trading days here), wrapping circularly.
  * CRITICAL: one index sequence shared by ALL symbols per trial. Resampling
    each symbol independently would destroy cross-sectional correlation and
    re-narrow the band — the same failure the GBM null fixed with its
    one-factor model, reintroduced through the back door.
  * (overnight, intraday) log-return PAIRS are resampled jointly and the bars
    rebuilt via `_ohlcv_from_components`, so resampled data still has real
    overnight gaps for the engine's next-open fill discipline to bite on.
  * Deterministic: seeds derive from blake2b of "bootstrap-<trial>". Never
    the builtin hash() — it is salted per process.

What a resampled universe destroys, on purpose: any exploitable *sequence*
longer than the block length. A strategy cannot "know" the real 2022 bear
market is coming when the blocks are shuffled; if it still beats buy-and-hold
across many resamples, that edge is structural, not memorised.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from .data import OHLCV_COLUMNS, _clean, _ohlcv_from_components
from .logging_setup import get_logger

log = get_logger("bootstrap_null")

# Expected block length in trading days. ~20 keeps a month of dependence
# (enough for volatility clustering) while still scrambling regimes.
EXPECTED_BLOCK_LEN = 20


def bootstrap_seed(tag: str) -> int:
    """Deterministic across processes and machines — blake2b, never hash()."""
    digest = hashlib.blake2b(tag.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big")


def stationary_bootstrap_indices(
    n_source: int, n_out: int, rng: np.random.Generator,
    expected_block: int = EXPECTED_BLOCK_LEN,
) -> np.ndarray:
    """Politis–Romano stationary bootstrap index sequence.

    Start at a uniformly random source position; at each step continue the
    block (next index, wrapping circularly) with probability 1 - 1/L, or start
    a fresh block at a new uniform position with probability 1/L. Block
    lengths are therefore geometric with mean L.
    """
    if n_source < 2:
        raise ValueError(f"need at least 2 source bars, got {n_source}")
    p_restart = 1.0 / float(expected_block)

    idx = np.empty(n_out, dtype=np.int64)
    # Draw all randomness up front — one uniform per step for the restart
    # decision and one for the restart position — so the sequence is a pure
    # function of the rng state.
    restarts = rng.random(n_out) < p_restart
    positions = rng.integers(0, n_source, size=n_out)

    idx[0] = positions[0]
    for t in range(1, n_out):
        idx[t] = positions[t] if restarts[t] else (idx[t - 1] + 1) % n_source
    return idx


def _return_components(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """(overnight, intraday) log returns; first bar dropped (no prev close)."""
    close = df["close"].to_numpy(dtype=float)
    open_ = df["open"].to_numpy(dtype=float)
    overnight = np.log(open_[1:] / close[:-1])
    intraday = np.log(close[1:] / open_[1:])
    return overnight, intraday


def resample_universe(
    data: dict[str, pd.DataFrame],
    trial: int,
    expected_block: int = EXPECTED_BLOCK_LEN,
    start: str = "2015-01-01",
) -> dict[str, pd.DataFrame]:
    """One bootstrap draw of the whole universe, correlation preserved.

    All symbols are cut to their common index first (the engine trades the
    intersection anyway), then every symbol is resampled with the SAME index
    sequence — day t of the resample is day idx[t] of the source for all of
    them, so cross-sectional correlation survives by construction.
    """
    common = None
    for df in data.values():
        common = df.index if common is None else common.intersection(df.index)
    if common is None or len(common) < 3:
        raise ValueError("not enough overlapping history to resample")

    aligned = {s: df.loc[common] for s, df in data.items()}
    comps = {s: _return_components(df) for s, df in aligned.items()}
    n_source = len(common) - 1                      # components drop bar 0

    rng = np.random.default_rng(bootstrap_seed(f"bootstrap-{trial}"))
    idx = stationary_bootstrap_indices(n_source, n_source, rng, expected_block)

    out: dict[str, pd.DataFrame] = {}
    for sym, (overnight, intraday) in comps.items():
        # Per-symbol wick scale from its own realised vol, like the GBM path.
        bar_vol = float(np.std(overnight + intraday, ddof=1))
        bars = _ohlcv_from_components(
            overnight[idx], intraday[idx], rng, start, bar_vol,
        )
        out[sym] = _clean(bars[OHLCV_COLUMNS])
    return out
