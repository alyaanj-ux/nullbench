"""WeatherDomain: the load-bearing REAL of the universality proof.

Predictors (docs/design.md, fixed):
    persistence  T-hat(t+1) = T(t)
    trend_k      T-hat(t+1) = T(t) + (T(t) - T(t-k)) / k,   k in {1, 3, 5}
Baseline: climatology — the day-of-year mean computed from TRAINING data
only. The no-lookahead rule applies to climatology exactly as it applied to
market parameters: each test year's climatology sees only years before it.
Score: skill = 1 - MAE(predictor) / MAE(climatology), pooled equal-weight
across cities. The baseline's own skill is 0 by construction.

The null (fixed by the spec — do NOT redesign):
    1. climatology per city
    2. anomaly = actual - climatology
    3. permute the anomalies i.i.d. (blake2b seed f"weather-null-{trial}")
    4. null = climatology + permuted anomalies
Seasonality survives; day-to-day persistence dies. This is the OPPOSITE
choice from the market's 20-day block bootstrap, on purpose: the weather
signal IS short-range structure, and blocks would smuggle it into the null.

Interpretation note: the spec says the null's
climatology comes from "training data only". At null-construction time there
is no single train/test split (37 expanding folds), so the decomposition here
uses the full-series climatology; the SCORING path recomputes climatology
from training data per fold regardless, so no forecast ever sees the future.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..logging_setup import get_logger
from ..validation.harness import Domain, stable_trial_seed

log = get_logger("weather_domain")

TEST_START_YEAR = 1990
PREDICTORS = ("persistence", "trend_1", "trend_3", "trend_5")


def _doy_climatology(doy: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Mean value per day-of-year (1..366). Empty bins fall back to the
    overall mean (only doy 366 in the earliest folds)."""
    sums = np.bincount(doy, weights=values, minlength=367)
    counts = np.bincount(doy, minlength=367)
    out = np.full(367, values.mean())
    mask = counts > 0
    out[mask] = sums[mask] / counts[mask]
    return out


def evaluate_panel(series: dict[str, pd.Series],
                   predictors: tuple[str, ...] = PREDICTORS,
                   test_start_year: int = TEST_START_YEAR) -> dict:
    """Walk-forward skill for every predictor, one climatology pass per fold.

    Expanding calendar-year folds: to score year Y, climatology is built from
    all data strictly before Y. Predictions inside Y may use actual values
    from earlier days (that is the past, not lookahead).

    Returns {"pooled": {name: skill}, "per_city": {city: {name: skill}}}.
    """
    per_city: dict[str, dict[str, float]] = {}
    for city in sorted(series):
        s = series[city].dropna()
        v = s.to_numpy(dtype=float)
        years = s.index.year.to_numpy()
        doy = s.index.dayofyear.to_numpy()

        abs_err = {name: 0.0 for name in predictors}
        clim_err, n_scored = 0.0, 0

        for year in range(max(test_start_year, int(years.min()) + 1),
                          int(years.max()) + 1):
            train = years < year
            test = years == year
            if train.sum() < 365 or not test.any():
                continue
            clim = _doy_climatology(doy[train], v[train])

            idx = np.nonzero(test)[0]
            # need at least max-k history behind each scored day
            idx = idx[idx >= 6]
            if idx.size == 0:
                continue
            actual = v[idx]
            clim_err += np.abs(clim[doy[idx]] - actual).sum()
            for name in predictors:
                if name == "persistence":
                    fc = v[idx - 1]
                else:
                    k = int(name.split("_")[1])
                    fc = v[idx - 1] + (v[idx - 1] - v[idx - 1 - k]) / k
                abs_err[name] += np.abs(fc - actual).sum()
            n_scored += idx.size

        if n_scored == 0 or clim_err == 0:
            raise ValueError(f"{city}: nothing scoreable")
        per_city[city] = {name: 1.0 - abs_err[name] / clim_err
                          for name in predictors}
        # MAEs exposed for reporting and for the no-lookahead test: the
        # baseline's MAE on a constructed series is exactly computable only
        # if climatology really is train-only.
        per_city[city]["climatology_mae"] = clim_err / n_scored
        for name in predictors:
            per_city[city][f"{name}_mae"] = abs_err[name] / n_scored

    pooled = {name: float(np.mean([per_city[c][name] for c in per_city]))
              for name in predictors}          # skills only, not the MAE keys
    return {"pooled": pooled, "per_city": per_city}


def make_weather_null(series: dict[str, pd.Series],
                      trial: int) -> dict[str, pd.Series]:
    """The anomaly-shuffle null, exactly per spec. One rng stream per trial
    (blake2b of f"weather-null-{trial}"), cities consumed in sorted order so
    the draw is deterministic."""
    rng = np.random.default_rng(stable_trial_seed(f"weather-null-{trial}"))
    out: dict[str, pd.Series] = {}
    for city in sorted(series):
        s = series[city].dropna()
        v = s.to_numpy(dtype=float)
        doy = s.index.dayofyear.to_numpy()
        clim = _doy_climatology(doy, v)
        anom = v - clim[doy]
        shuffled = anom[rng.permutation(len(anom))]
        out[city] = pd.Series(clim[doy] + shuffled, index=s.index,
                              name=s.name)
    return out


class WeatherDomain(Domain):
    """One predictor vs climatology on the weather panel."""

    def __init__(self, series: dict[str, pd.Series],
                 predictor: str = "persistence"):
        if predictor not in PREDICTORS:
            raise ValueError(f"unknown predictor {predictor!r}")
        self.name = f"weather[{predictor}]"
        self.predictor = predictor
        self._series = series

    @property
    def series(self) -> dict[str, pd.Series]:
        return self._series

    def evaluate(self, series: dict[str, pd.Series]) -> float:
        return evaluate_panel(series, predictors=(self.predictor,))[
            "pooled"][self.predictor]

    def make_null(self, series: dict[str, pd.Series],
                  trial: int) -> dict[str, pd.Series]:
        return make_weather_null(series, trial)
