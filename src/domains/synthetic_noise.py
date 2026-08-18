"""Synthetic-noise domain: the instrument's zero test.

Ten series shaped exactly like the weather panel — climatology plus
anomalies — except the anomalies are i.i.d. Gaussian by construction, with
sigma matched to each city's real anomaly standard deviation. Persistence has
zero skill on i.i.d. anomalies BY CONSTRUCTION: today's anomaly says nothing
about tomorrow's. Required verdict: NOISE.

This runs through the weather domain code UNCHANGED — same predictors, same
anomaly-shuffle null, same trial count. Only the data differs. If the
pipeline reports REAL here it is manufacturing signal from structureless
data, and finding where is more important than anything else that day.

Fallback (spec): if real weather data is unavailable, a sine climatology
(365.25-day period, 10 degC amplitude) keeps this fully offline-runnable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..validation.harness import stable_trial_seed
from .weather_domain import _doy_climatology

FALLBACK_YEARS = 46          # match the real panel's 1980->present span


def synthetic_noise_universe(
    weather: dict[str, pd.Series] | None = None,
    cities: tuple[str, ...] | None = None,
) -> dict[str, pd.Series]:
    """climatology + iid Gaussian anomalies, sigma-matched, blake2b-seeded."""
    out: dict[str, pd.Series] = {}
    if weather:
        for city in sorted(weather):
            s = weather[city].dropna()
            v = s.to_numpy(dtype=float)
            doy = s.index.dayofyear.to_numpy()
            clim = _doy_climatology(doy, v)
            anom = v - clim[doy]
            sigma = float(anom.std(ddof=1))
            rng = np.random.default_rng(
                stable_trial_seed(f"synthetic-noise-{city}"))
            noise = rng.normal(0.0, sigma, len(v))
            out[city] = pd.Series(clim[doy] + noise, index=s.index,
                                  name="temperature")
        return out

    # Offline fallback: sine climatology, spec-fixed shape.
    cities = cities or tuple(f"S{i}" for i in range(10))
    idx = pd.date_range("1980-01-01", periods=int(365.25 * FALLBACK_YEARS),
                        freq="D")
    doy = idx.dayofyear.to_numpy()
    for ci, city in enumerate(sorted(cities)):
        clim = 10.0 * np.sin(2 * np.pi * (doy / 365.25) + ci)
        rng = np.random.default_rng(
            stable_trial_seed(f"synthetic-noise-{city}"))
        noise = rng.normal(0.0, 4.0, len(idx))
        out[city] = pd.Series(clim + noise, index=idx, name="temperature")
    return out
