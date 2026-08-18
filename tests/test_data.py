"""Tests for the data layer.

The valuable test here is `test_synthetic_bars_are_identical_across_processes`.
The synthetic feed is the project's null hypothesis — the thing you check a
strategy against to confirm it cannot find edge where none exists. That only
works if the path is the same every run. It previously was not: the default
seed came from `hash(symbol)`, and CPython salts string hashing per process
unless PYTHONHASHSEED is fixed, so every invocation drew a different series.
Within a single process the bug is invisible, which is why this test spawns
real subprocesses with conflicting hash seeds.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

from src.data import OHLCV_COLUMNS, synthetic_bars


def _digest(df) -> str:
    return hashlib.sha256(
        df[OHLCV_COLUMNS].to_numpy(dtype="float64").tobytes()
    ).hexdigest()


# Printed by the child process so the parent can compare paths across
# different hash salts. Kept to one line of stdout for easy parsing.
_CHILD = """
import hashlib, sys
sys.path.insert(0, {root!r})
from src.data import OHLCV_COLUMNS, synthetic_bars
df = synthetic_bars("SPY", n=64)
print(hashlib.sha256(df[OHLCV_COLUMNS].to_numpy(dtype="float64").tobytes()).hexdigest())
"""


def _digest_in_subprocess(hash_seed: str) -> str:
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = hash_seed
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD.format(root=str(ROOT))],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120,
    )
    assert proc.returncode == 0, f"child failed:\n{proc.stderr}"
    return proc.stdout.strip().splitlines()[-1]


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------

def test_synthetic_bars_are_identical_across_processes():
    """The regression test.

    Two interpreters with deliberately different string-hash salts must
    generate the same path for the same symbol. Under the old
    `hash(symbol)` seed these digests differed on essentially every run.
    """
    first = _digest_in_subprocess("1")
    second = _digest_in_subprocess("2")

    assert first == second, (
        "synthetic path changed with PYTHONHASHSEED — the default seed is "
        "process-dependent, so the synthetic null hypothesis is not reproducible"
    )


def test_synthetic_bars_are_identical_within_a_process():
    assert _digest(synthetic_bars("SPY", n=64)) == _digest(synthetic_bars("SPY", n=64))


def test_different_symbols_get_different_paths():
    """Reproducible must not collapse into identical — each symbol needs its
    own path, or a multi-symbol backtest is really a single-symbol one."""
    digests = {s: _digest(synthetic_bars(s, n=64)) for s in ("SPY", "QQQ", "AAPL")}
    assert len(set(digests.values())) == 3, f"symbol paths collided: {digests}"


def test_explicit_seed_takes_precedence():
    assert _digest(synthetic_bars("SPY", n=64, seed=7)) == _digest(
        synthetic_bars("QQQ", n=64, seed=7)
    ), "explicit seed ignored — symbol still influencing the path"
    assert _digest(synthetic_bars("SPY", n=64, seed=7)) != _digest(
        synthetic_bars("SPY", n=64, seed=8)
    )


# ---------------------------------------------------------------------------
# Shape and sanity
# ---------------------------------------------------------------------------

def test_synthetic_bars_have_a_valid_ohlc_envelope():
    df = synthetic_bars("SPY", n=200)

    assert list(df.columns[: len(OHLCV_COLUMNS)]) == OHLCV_COLUMNS
    assert len(df) == 200
    assert df.index.is_monotonic_increasing
    assert (df["high"] >= df[["open", "close"]].max(axis=1) - 1e-9).all()
    assert (df["low"] <= df[["open", "close"]].min(axis=1) + 1e-9).all()
    assert (df[["open", "high", "low", "close"]] > 0).all().all()
    assert df.notna().all().all()


@pytest.mark.parametrize("n", [1, 2, 50])
def test_synthetic_bars_handle_short_series(n):
    """Guards the `close[:-1]` concatenation in the open-price construction,
    which is the part most likely to break on a 1-bar request."""
    df = synthetic_bars("SPY", n=n)
    assert len(df) == n
    assert df.notna().all().all()
