"""Anchor tests for the domain-agnostic harness.

The refactor's contract (DESIGN_UNIVERSAL.md): a market run through the NEW
harness reproduces reports/night_bands.json exactly. Two layers:

  * statistics layer, zero backtests: feeding the artifact's raw deltas into
    ValidationResult must reproduce the artifact's stored band/mean/std to
    the digit — pins np.percentile behaviour and every summary formula.
  * evaluation layer: MarketDomain.evaluate on bootstrap trial 0 and on the
    real universe must equal the artifact's deltas[0] / headline delta. The
    trial-0 check is strong: identical seeds mean one matching draw implies
    the whole path matches. (Full 200-trial reproduction was run once and
    logged in NIGHT_LOG_2.md; repeating 30 minutes of compute per test run
    buys nothing further.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pytest

from src.validation.harness import ValidationResult, run_validation, Domain

ARTIFACT = ROOT / "reports" / "night_bands.json"


def _artifact() -> dict:
    assert ARTIFACT.exists(), "reports/night_bands.json missing"
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def _real_universe_or_skip():
    from src.config import load_config
    from src.data import get_universe
    cfg = load_config()
    try:
        return cfg, get_universe(cfg, synthetic=False)
    except Exception as exc:            # no cache + no keys on this machine
        pytest.skip(f"real universe unavailable here: {exc}")


# ---------------------------------------------------------------------------
# Statistics layer — zero backtests, runs everywhere
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("null", ["gbm", "bootstrap"])
def test_harness_statistics_reproduce_the_artifact(null):
    nb = _artifact()
    r = ValidationResult(domain=f"market[{null}]",
                         n_trials=nb[null]["n_trials"],
                         statistic=nb["headline"]["delta"],
                         null_stats=nb[null]["deltas"])
    # Tolerance 2e-6, and not tighter, for a stated reason: the artifact
    # stores deltas rounded to 6 decimals while its summary numbers were
    # computed from the unrounded values. Recomputing from the stored deltas
    # can therefore differ by up to ~5e-7 per order statistic (interpolated
    # percentiles combine two of them). This is the precision bound of the
    # stored data, not slack in the formulas.
    assert r.p5 == pytest.approx(nb[null]["p5"], abs=2e-6)
    assert r.p95 == pytest.approx(nb[null]["p95"], abs=2e-6)
    assert r.mean == pytest.approx(nb[null]["mean_delta"], abs=2e-6)
    assert r.std == pytest.approx(nb[null]["std_delta"], abs=2e-6)
    # Counting statistics are exact: rounding a delta by 5e-7 cannot move it
    # across zero or across the headline unless it was already equal at 6dp.
    assert (np.asarray(r.null_stats) > 0).mean() == pytest.approx(
        nb[null]["win_rate"], abs=1e-9)
    assert r.percentile == pytest.approx(nb[null]["headline_percentile"],
                                         abs=1e-9)
    # Ground truth: the market result is NOISE against both nulls.
    assert r.verdict == "NOISE"


def test_verdict_is_real_only_above_the_p95():
    base = list(np.linspace(-0.5, 0.5, 200))
    inside = ValidationResult("d", 200, statistic=0.2, null_stats=base)
    above = ValidationResult("d", 200, statistic=0.6, null_stats=base)
    below = ValidationResult("d", 200, statistic=-0.9, null_stats=base)
    assert inside.verdict == "NOISE"
    assert above.verdict == "REAL"
    assert below.verdict == "NOISE", "worse-than-luck is not REAL"


def test_run_validation_is_deterministic_and_ordered():
    class Toy(Domain):
        name = "toy"
        @property
        def series(self):
            return 100.0
        def evaluate(self, series):
            return float(series) / 100.0
        def make_null(self, series, trial):
            return float(trial)      # evaluate -> trial/100
    r = run_validation(Toy(), n_trials=10)
    assert r.statistic == 1.0
    assert r.null_stats == [t / 100.0 for t in range(10)], \
        "trials must be evaluated in order with their own trial index"
    assert r.verdict == "REAL"


# ---------------------------------------------------------------------------
# Evaluation layer — a few real backtests, cache/keys permitting
# ---------------------------------------------------------------------------

def test_market_domain_reproduces_headline_and_bootstrap_trial0():
    from src.validation.market import MarketDomain
    nb = _artifact()
    cfg, data = _real_universe_or_skip()
    dom = MarketDomain(cfg, data, cfg.strategy_name, cfg.strategy_params,
                       null="bootstrap")
    assert dom.evaluate(dom.series) == pytest.approx(
        nb["headline"]["delta"], abs=5e-5), "headline delta diverged"
    assert dom.evaluate(dom.make_null(dom.series, 0)) == pytest.approx(
        nb["bootstrap"]["deltas"][0], abs=5e-7), \
        "bootstrap trial 0 diverged — seed discipline or evaluate() changed"


@pytest.mark.slow
def test_market_domain_reproduces_gbm_and_more_bootstrap_trials():
    from src.validation.market import MarketDomain
    nb = _artifact()
    cfg, data = _real_universe_or_skip()
    gbm = MarketDomain(cfg, data, cfg.strategy_name, cfg.strategy_params,
                       null="gbm")
    for t in range(2):
        assert gbm.evaluate(gbm.make_null(gbm.series, t)) == pytest.approx(
            nb["gbm"]["deltas"][t], abs=5e-7), f"gbm trial {t} diverged"
    boot = MarketDomain(cfg, data, cfg.strategy_name, cfg.strategy_params,
                        null="bootstrap")
    for t in (1, 2):
        assert boot.evaluate(boot.make_null(boot.series, t)) == pytest.approx(
            nb["bootstrap"]["deltas"][t], abs=5e-7), f"bootstrap trial {t} diverged"
