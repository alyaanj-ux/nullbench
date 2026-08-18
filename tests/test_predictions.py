"""Tests for the forward-prediction scorer — synthetic lines, no network."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(ROOT / "scripts"))
from score_predictions import ARCHIVE_LAG_DAYS, score_lines  # noqa: E402


def _series(constant=10.0, spike_on=None, spike=20.0):
    idx = pd.date_range("2020-01-01", "2026-08-15", freq="D")
    v = np.full(len(idx), constant)
    if spike_on:
        v[idx.date == date.fromisoformat(spike_on)] = spike
    return pd.Series(v, index=idx, name="temperature")


def _line(predicted, target="2026-08-10", predictor="persistence"):
    return {"made_at": "2026-08-09T12:00:00+00:00", "city": "X",
            "target_date": target, "predictor": predictor,
            "predicted": predicted, "base_date": "2026-08-09"}


def test_perfect_prediction_scores_skill_one():
    """Actual = 20 on the target day against a constant-10 climatology.
    A perfect prediction has zero error -> skill exactly 1."""
    s = _series(spike_on="2026-08-10")
    out = score_lines([_line(20.0)], lambda c: s, today=date(2026, 8, 15))
    assert len(out) == 1
    assert out[0]["actual"] == pytest.approx(20.0)
    assert out[0]["climatology"] == pytest.approx(10.0)
    assert out[0]["skill"] == pytest.approx(1.0)


def test_climatology_equal_prediction_scores_zero():
    s = _series(spike_on="2026-08-10")
    out = score_lines([_line(10.0)], lambda c: s, today=date(2026, 8, 15))
    assert out[0]["skill"] == pytest.approx(0.0)


def test_worse_than_climatology_scores_negative():
    s = _series(spike_on="2026-08-10")
    out = score_lines([_line(0.0)], lambda c: s, today=date(2026, 8, 15))
    assert out[0]["skill"] < 0


def test_not_due_lines_are_left_alone():
    """A prediction for yesterday must NOT be scored — the archive needs
    ARCHIVE_LAG_DAYS to consolidate. Scoring early grades against
    provisional data and rewrites history when the archive settles."""
    s = _series()
    out = score_lines([_line(10.0, target="2026-08-14")],
                      lambda c: s, today=date(2026, 8, 15))
    assert out == [], "scored a line that was not due"
    # And it becomes due once the lag passes.
    out2 = score_lines([_line(10.0, target="2026-08-14")],
                       lambda c: s, today=date(2026, 8, 16))
    assert len(out2) == 1


def test_climatology_is_built_from_before_the_target_only():
    """Spike ON the target day: a lookahead climatology would absorb the
    spike into the day-of-year mean and shrink the baseline error."""
    s = _series(spike_on="2026-08-10", spike=120.0)
    out = score_lines([_line(10.0)], lambda c: s, today=date(2026, 8, 15))
    # Honest climatology = 10 (the spike is excluded), so baseline error is
    # exactly |10 - 120| = 110 and the prediction of 10 has the same error.
    assert out[0]["climatology"] == pytest.approx(10.0), \
        "climatology saw the target day"
    assert out[0]["climatology_abs_error"] == pytest.approx(110.0)
