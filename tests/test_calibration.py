"""Tests for the instrument zeroing (owner's T4 ruling).

Two properties the ruling demands, both mutation-checked: the calibration is
deterministic across processes, and a structureless series scores within
resolution of zero after correction. Plus the verdict gate itself: clearing
the null band no longer suffices when the zeroed effect is inside the
instrument's resolution.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pytest

from src.validation.calibration import (ANALYTIC_PERSISTENCE_ZERO,
                                        calibration_universe, measure_zero)
from src.validation.harness import ValidationResult


def test_calibration_is_deterministic_across_processes():
    child = (
        "import sys, hashlib; sys.path.insert(0, {root!r});"
        "from src.validation.calibration import calibration_universe;"
        "u = calibration_universe(3);"
        "h = hashlib.sha256();"
        "[h.update(u[c].to_numpy().tobytes()) for c in sorted(u)];"
        "print(h.hexdigest())"
    ).format(root=str(ROOT))

    def digest(hash_seed):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed,
               "PYTHONIOENCODING": "utf-8"}
        p = subprocess.run([sys.executable, "-c", child], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8",
                           env=env, timeout=180)
        assert p.returncode == 0, p.stderr
        return p.stdout.strip().splitlines()[-1]

    assert digest("1") == digest("2")


def test_calibration_universes_are_independent():
    a = calibration_universe(0)
    b = calibration_universe(1)
    assert not np.allclose(a["S0"].to_numpy(), b["S0"].to_numpy()), \
        "universe 0 and 1 are identical — K draws are not independent"


def test_structureless_series_scores_zero_after_correction():
    """The new zero-test pass condition. A fresh structureless universe that
    was NOT part of the calibration set (k=99) must score within resolution
    of zero once the zero_offset is subtracted."""
    from src.domains.weather_domain import evaluate_panel

    cal = measure_zero(weather=None, k_universes=12)   # small-K for speed
    fresh = calibration_universe(99)
    raw = evaluate_panel(fresh)["pooled"]["persistence"]
    z = cal["predictors"]["persistence"]
    calibrated = raw - z["zero_offset"]
    assert abs(calibrated) <= max(z["resolution"], 0.01), (
        f"structureless data reads {calibrated:+.4f} after zeroing "
        f"(resolution {z['resolution']:.4f}) — the correction is not working"
    )
    # And the analytic cross-check stays recorded, never tuned: measured zero
    # must sit ABOVE the analytic level (finite-sample climatology inflates
    # the baseline MAE), within a modest, explainable margin.
    gap = z["zero_offset"] - ANALYTIC_PERSISTENCE_ZERO
    assert 0.0 < gap < 0.06, (
        f"measured zero {z['zero_offset']:+.4f} vs analytic "
        f"{ANALYTIC_PERSISTENCE_ZERO:+.4f}: gap {gap:+.4f} is outside the "
        f"explainable finite-sample range — do NOT tune; investigate"
    )


def test_verdict_requires_clearing_resolution_not_just_the_band():
    """The exact T4 failure, replayed against the new rule: a statistic just
    above a razor-thin band but inside the instrument's resolution must read
    NOISE. The same statistic with a genuinely large calibrated effect reads
    REAL."""
    band = list(np.linspace(-0.408, -0.400, 200))
    t4_like = ValidationResult("zero-test", 200, statistic=-0.390,
                               null_stats=band,
                               zero_offset=-0.389, resolution=0.009)
    assert t4_like.statistic > t4_like.p95, "premise: it clears the band"
    assert t4_like.verdict == "NOISE", \
        "clearing the band with |calibrated| <= resolution must be NOISE"

    weather_like = ValidationResult("weather", 200, statistic=0.344,
                                    null_stats=band,
                                    zero_offset=-0.389, resolution=0.009)
    assert weather_like.verdict == "REAL"

    # Uncalibrated behaviour unchanged (the market path).
    legacy = ValidationResult("market", 200, statistic=-0.390,
                              null_stats=band)
    assert legacy.verdict == "REAL", \
        "without calibration the pre-ruling rule must hold unchanged"
