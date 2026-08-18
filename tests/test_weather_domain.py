"""Tests for the weather domain — including the executable proof of the
null trap from DESIGN_UNIVERSAL.md.

The trap: applying the market's ~20-day block bootstrap to weather would
preserve day-to-day persistence inside every block, so the null would still
contain the signal and the instrument would wrongly answer NOISE. The first
test MEASURES that: an i.i.d. shuffle kills lag-1 autocorrelation, a block
shuffle demonstrably does not. Swapping make_weather_null for a block
permutation makes that test fail — verified by mutation during the run.
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

from src.domains.weather_domain import (_doy_climatology, evaluate_panel,
                                        make_weather_null)
from src.validation.harness import stable_trial_seed


def _panel(n_years=12, cities=("A", "B"), seed=7, persistent=True):
    """Small deterministic panel: sine climatology + AR(1) or iid anomalies."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("1980-01-01", periods=int(365.25 * n_years), freq="D")
    doy = idx.dayofyear.to_numpy()
    out = {}
    for ci, city in enumerate(cities):
        clim = 10.0 * np.sin(2 * np.pi * (doy / 365.25) + ci)
        eps = rng.normal(0, 3.0, len(idx))
        if persistent:                       # AR(1), rho ~ 0.7 like weather
            anom = np.empty(len(idx))
            anom[0] = eps[0]
            for t in range(1, len(idx)):
                anom[t] = 0.7 * anom[t - 1] + eps[t]
        else:
            anom = eps
        out[city] = pd.Series(clim + anom, index=idx, name="temperature")
    return out


def _anomalies(s: pd.Series) -> np.ndarray:
    doy = s.index.dayofyear.to_numpy()
    v = s.to_numpy(dtype=float)
    clim = _doy_climatology(doy, v)
    return v - clim[doy]


def _lag1(x: np.ndarray) -> float:
    return float(np.corrcoef(x[:-1], x[1:])[0, 1])


def test_null_destroys_persistence_and_a_block_shuffle_would_not():
    """THE trap test. Three measurements:
      source anomalies: strongly persistent (lag-1 rho > 0.3)
      i.i.d.-shuffle null: persistence destroyed (|rho| < 0.05)
      20-day BLOCK shuffle (the market null's move): persistence SURVIVES —
        which is exactly why that null must never be used here.
    """
    uni = _panel(persistent=True)
    null = make_weather_null(uni, trial=0)

    for city in uni:
        rho_src = _lag1(_anomalies(uni[city]))
        rho_null = _lag1(_anomalies(null[city]))
        assert rho_src > 0.3, f"{city}: source anomalies not persistent enough to test"
        assert abs(rho_null) < 0.05, (
            f"{city}: null anomalies still persistent (rho={rho_null:.3f}) — "
            f"the shuffle is not destroying the structure it accuses"
        )

    # The executable proof of the spec's trap section: shuffle in 20-day
    # blocks instead, and the "null" keeps most of the signal.
    city = "A"
    anom = _anomalies(uni[city])
    n = len(anom) // 20 * 20
    blocks = anom[:n].reshape(-1, 20)
    rng = np.random.default_rng(stable_trial_seed("trap-proof"))
    block_shuffled = blocks[rng.permutation(len(blocks))].ravel()
    rho_block = _lag1(block_shuffled)
    assert rho_block > 0.3, (
        f"expected the block shuffle to PRESERVE persistence (got "
        f"rho={rho_block:.3f}) — if this fails the trap argument is wrong"
    )


def test_null_preserves_seasonality():
    """Monthly means of the null must match the CLIMATOLOGY (the spec's
    literal phrasing) — not the source's raw monthly means, which carry the
    AR(1) anomalies' clustering noise. After an i.i.d. shuffle the anomaly
    contribution to a month is a mean of ~370 independent draws (SE ~0.2 for
    this panel), so a 1.0-degree tolerance is generous for a correct null
    and hopeless for one that forgot to add climatology back (which is off
    by the full ~10-degree seasonal amplitude)."""
    uni = _panel()
    null = make_weather_null(uni, trial=3)
    for city in uni:
        s = uni[city]
        doy = s.index.dayofyear.to_numpy()
        clim = _doy_climatology(doy, s.to_numpy(dtype=float))
        clim_series = pd.Series(clim[doy], index=s.index)
        clim_m = clim_series.groupby(clim_series.index.month).mean()
        nul_m = null[city].groupby(null[city].index.month).mean()
        assert (clim_m - nul_m).abs().max() < 1.0, (
            f"{city}: null monthly means diverge from climatology"
        )


def test_null_is_deterministic_across_processes():
    child = (
        "import sys, hashlib; sys.path.insert(0, {root!r});"
        "from tests.test_weather_domain import _panel;"
        "from src.domains.weather_domain import make_weather_null;"
        "n = make_weather_null(_panel(), 5);"
        "h = hashlib.sha256();"
        "[h.update(n[c].to_numpy().tobytes()) for c in sorted(n)];"
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


def test_ragged_histories_evaluate_without_nan():
    uni = _panel(n_years=14)
    uni["B"] = uni["B"].loc["1985-06-15":]          # ragged on purpose
    res = evaluate_panel(uni, test_start_year=1990)
    for city, d in res["per_city"].items():
        for k, val in d.items():
            assert np.isfinite(val), f"{city}.{k} is not finite"
    null = make_weather_null(uni, 1)
    res_n = evaluate_panel(null, test_start_year=1990)
    assert all(np.isfinite(v) for v in res_n["pooled"].values())


def test_climatology_is_train_only_no_lookahead():
    """Constructed so the honest baseline MAE is EXACTLY computable.

    Years 1988-1989: constant 0. Year 1990 (the only test year): constant
    +12 with every 7th day at -12. A train-only climatology is 0 everywhere,
    so its MAE over 1990 is exactly 12. A climatology that leaks the test
    year absorbs part of the +12 level and lands measurably below 12.
    """
    idx = pd.date_range("1988-01-01", "1990-12-31", freq="D")
    v = np.zeros(len(idx))
    y1990 = idx.year == 1990
    v[y1990] = 12.0
    sevens = np.zeros(len(idx), dtype=bool)
    sevens[np.nonzero(y1990)[0][::7]] = True
    v[sevens] = -12.0
    uni = {"X": pd.Series(v, index=idx, name="temperature")}

    res = evaluate_panel(uni, test_start_year=1990)
    assert res["per_city"]["X"]["climatology_mae"] == pytest.approx(12.0), (
        "baseline MAE is not exactly 12 — climatology is seeing the test year"
    )
