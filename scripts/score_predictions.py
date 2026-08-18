#!/usr/bin/env python
"""Score any due prediction lines against what actually happened.

Reads reports/predictions.jsonl, finds lines whose target_date is at least
two days old (the archive needs a beat to consolidate a day), fetches the
actual daily means (cache-first), and appends one scored line per prediction
to reports/predictions_scored.jsonl — also append-only. Already-scored lines
(same city/target/predictor/made_at) are skipped, so the script is safe to
run any morning.

Skill per line: 1 - |predicted - actual| / |climatology - actual|, where the
climatology is built from archive data strictly BEFORE the target date (the
no-lookahead rule follows the prediction all the way to its grave). A pooled
summary prints at the end; the JSONL is the record.

Usage:  python scripts/score_predictions.py
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from src.logging_setup import get_logger, setup_logging  # noqa: E402

log = get_logger("score")

PREDICTIONS = ROOT / "reports" / "predictions.jsonl"
SCORED = ROOT / "reports" / "predictions_scored.jsonl"
ARCHIVE_LAG_DAYS = 2


def _key(line: dict) -> tuple:
    return (line["city"], line["target_date"], line["predictor"],
            line["made_at"])


def score_lines(pred_lines: list[dict], series_for_city,
                today: date) -> list[dict]:
    """Pure scoring: testable without any network.

    `series_for_city(city)` returns a pd.Series of daily means indexed by
    timestamp, covering the target date and the history before it.
    """
    from src.domains.weather_domain import _doy_climatology

    scored = []
    for line in pred_lines:
        target = date.fromisoformat(line["target_date"])
        if (today - target).days < ARCHIVE_LAG_DAYS:
            continue                     # not due yet
        s = series_for_city(line["city"]).dropna()
        mask = s.index.date == target
        if not mask.any():
            log.warning("%s %s: no actual in archive yet",
                        line["city"], line["target_date"])
            continue
        actual = float(s[mask].iloc[0])

        train = s[s.index.date < target]
        doy = train.index.dayofyear.to_numpy()
        clim = _doy_climatology(doy, train.to_numpy(dtype=float))
        target_doy = datetime.combine(target, datetime.min.time()).timetuple().tm_yday
        clim_forecast = float(clim[min(target_doy, 366)])

        err_pred = abs(line["predicted"] - actual)
        err_clim = abs(clim_forecast - actual)
        skill = 1.0 - err_pred / err_clim if err_clim > 0 else 0.0
        scored.append({
            **line,
            "actual": round(actual, 2),
            "climatology": round(clim_forecast, 2),
            "abs_error": round(err_pred, 3),
            "climatology_abs_error": round(err_clim, 3),
            "skill": round(skill, 4),
            "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    return scored


def main() -> int:
    setup_logging()
    if not PREDICTIONS.exists():
        print("no predictions to score yet")
        return 0
    lines = [json.loads(l) for l in
             PREDICTIONS.read_text(encoding="utf-8").splitlines() if l.strip()]
    done: set = set()
    if SCORED.exists():
        done = {_key(json.loads(l)) for l in
                SCORED.read_text(encoding="utf-8").splitlines() if l.strip()}
    due = [l for l in lines if _key(l) not in done]
    if not due:
        print("nothing new to score")
        return 0

    from src.domains.weather import get_city_series
    scored = score_lines(due, get_city_series,
                         datetime.now(timezone.utc).date())
    if not scored:
        print(f"{len(due)} line(s) pending, none due yet "
              f"(archive lag {ARCHIVE_LAG_DAYS}d)")
        return 0
    with open(SCORED, "a", encoding="utf-8") as fh:
        for line in scored:
            fh.write(json.dumps(line) + "\n")
    by_pred: dict[str, list[float]] = {}
    for s in scored:
        by_pred.setdefault(s["predictor"], []).append(s["skill"])
    print(f"scored {len(scored)} line(s):")
    for name, skills in sorted(by_pred.items()):
        print(f"  {name:12s} mean skill {np.mean(skills):+.3f} "
              f"over {len(skills)} line(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
