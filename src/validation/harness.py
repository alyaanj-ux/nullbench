"""Domain-agnostic validation harness.

The market noise test asked one question: "is this result distinguishable
from what luck produces?" Nothing in that question is finance-specific, and
neither is the machinery that answers it — walk-forward evaluation, a
200-trial null band, percentile placement, a verdict. This module extracts
that machinery so any domain can use it.

A domain supplies five things (DESIGN_UNIVERSAL.md):

    series      the real data (opaque to the harness — dict of frames for
                markets, dict of Series for weather)
    predict     the rule being tested
    baseline    the naive reference the rule must beat
    score       one number, > 0 means the rule beat the baseline
    make_null   a structure-destroyed copy of the series

The harness owns everything else and never looks inside `series`.

The null-design principle, which is the intellectual core of the project:
**a null declares exactly which structure you accuse the result of
exploiting, then destroys only that.** The market null uses ~20-day blocks —
preserving fat tails and volatility clustering while destroying multi-week
trends. A weather null must do the OPPOSITE: the weather signal IS the
short-range structure, so blocks would smuggle the signal into the null and
the band would swallow the real result. Blocks for trends, shuffles for
persistence. Swapping them is a silent catastrophe, which is why each
domain's `make_null` is part of its spec, not a harness default.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..logging_setup import get_logger

log = get_logger("validation")


def stable_trial_seed(tag: str) -> int:
    """blake2b, never hash() — the same discipline as every other seed here."""
    digest = hashlib.blake2b(tag.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big")


class Domain(ABC):
    """One dataset + one rule + one honest null."""

    name: str = "domain"

    @property
    @abstractmethod
    def series(self) -> Any:
        """The real data. Opaque to the harness."""

    @abstractmethod
    def evaluate(self, series: Any) -> float:
        """Score the rule against the baseline on `series`.

        Composes the spec's predict/baseline/score parts: run the predictor
        and the baseline over the walk-forward, return one number where > 0
        means the rule beat the baseline. The harness treats it as a black
        box so market Sharpe deltas and weather skill scores flow through
        the identical code path.
        """

    @abstractmethod
    def make_null(self, series: Any, trial: int) -> Any:
        """A structure-destroyed copy of `series` for null trial `trial`.

        Must be deterministic in `trial` (blake2b-style seeds) and must
        destroy exactly the structure the predictor is accused of exploiting
        — see the module docstring.
        """


@dataclass
class ValidationResult:
    domain: str
    n_trials: int
    statistic: float                    # the real result
    null_stats: list[float] = field(default_factory=list)

    @property
    def _d(self) -> np.ndarray:
        return np.asarray(self.null_stats, dtype=float)

    @property
    def p5(self) -> float:
        return float(np.percentile(self._d, 5))

    @property
    def p95(self) -> float:
        return float(np.percentile(self._d, 95))

    @property
    def mean(self) -> float:
        return float(self._d.mean())

    @property
    def std(self) -> float:
        return float(self._d.std(ddof=1)) if len(self.null_stats) > 1 else 0.0

    @property
    def percentile(self) -> float:
        """Share of null trials the real result beats."""
        return float((self._d < self.statistic).mean())

    @property
    def verdict(self) -> str:
        """REAL only above the 95th percentile of luck. Everything else —
        inside the band, or below it — is NOISE: not distinguishable from
        (or worse than) what the null produces."""
        return "REAL" if self.statistic > self.p95 else "NOISE"

    def sentence(self) -> str:
        return (
            f"{self.domain}: statistic {self.statistic:+.3f} vs null band "
            f"[{self.p5:+.3f}, {self.p95:+.3f}] ({self.n_trials} trials) -> "
            f"{self.verdict} (sits at the {self.percentile:.0%} percentile "
            f"of luck)"
        )


def run_validation(domain: Domain, n_trials: int = 200) -> ValidationResult:
    """The whole instrument: real statistic, null band, verdict."""
    real = float(domain.evaluate(domain.series))
    stats: list[float] = []
    for trial in range(n_trials):
        null_series = domain.make_null(domain.series, trial)
        stats.append(float(domain.evaluate(null_series)))
        if (trial + 1) % 25 == 0:
            log.info("%s: null trial %d/%d", domain.name, trial + 1, n_trials)
    return ValidationResult(domain=domain.name, n_trials=n_trials,
                            statistic=real, null_stats=stats)
