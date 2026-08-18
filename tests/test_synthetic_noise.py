"""Hygiene tests for the synthetic-noise generator (T4's committed half).

The generator's job is to be provably structureless: deterministic across
processes and free of lag-1 autocorrelation. The VERDICT it produced against
the anomaly-shuffle null is a separate story — see NIGHT_LOG_2.md: the data
is clean (fresh-draw distribution centred on the measured statistic); the
null carries a small calibration bias that the log documents.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np

from src.domains.synthetic_noise import synthetic_noise_universe
from src.domains.weather_domain import _doy_climatology


def test_generation_is_deterministic_across_processes():
    child = (
        "import sys, hashlib; sys.path.insert(0, {root!r});"
        "from src.domains.synthetic_noise import synthetic_noise_universe;"
        "u = synthetic_noise_universe(weather=None);"
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


def test_generated_anomalies_are_serially_uncorrelated():
    uni = synthetic_noise_universe(weather=None)
    for city, s in uni.items():
        doy = s.index.dayofyear.to_numpy()
        v = s.to_numpy(dtype=float)
        clim = _doy_climatology(doy, v)
        anom = v - clim[doy]
        rho = float(np.corrcoef(anom[:-1], anom[1:])[0, 1])
        assert abs(rho) < 0.05, (
            f"{city}: generated anomalies have lag-1 rho={rho:.3f} — "
            f"the zero test's ground truth is not actually structureless"
        )
