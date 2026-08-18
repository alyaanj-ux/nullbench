#!/usr/bin/env python
"""Emit tomorrow's temperature predictions — falsifiable, timestamped, append-only.

This is the "live data" proof for the weather domain: every run appends one
JSON line per city to reports/predictions.jsonl BEFORE the fact, and
scripts/score_predictions.py grades any line whose target date has since
landed in the archive. History is never rewritten; a wrong prediction stays
in the file, timestamped, exactly like a dyno sheet stays in the binder.

Predictors are the validated ones (persistence + trend k in {1,3,5}), fed by
the Open-Meteo FORECAST endpoint's recent daily means (past_days covers the
trend lookback; today's partial day is never used as history). Etiquette: one
request per city, >=1s apart, project User-Agent.

Usage:  python scripts/predict_tomorrow.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.domains.weather import CITIES, USER_AGENT  # noqa: E402
from src.logging_setup import get_logger, setup_logging  # noqa: E402

log = get_logger("predict")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PREDICTIONS = ROOT / "reports" / "predictions.jsonl"
TREND_KS = (1, 3, 5)


def recent_daily_means(city: str) -> dict[str, float]:
    """date-string -> daily mean for the last ~8 days, forecast endpoint."""
    lat, lon = CITIES[city]
    params = {
        "latitude": lat, "longitude": lon,
        "daily": "temperature_2m_mean", "timezone": "UTC",
        "past_days": 8, "forecast_days": 1,
    }
    url = f"{FORECAST_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
    daily = body.get("daily") or {}
    out = {}
    for d, v in zip(daily.get("time") or [], daily.get("temperature_2m_mean") or []):
        if v is not None:
            out[d] = float(v)
    if not out:
        raise RuntimeError(f"no daily means for {city}")
    return out


def predictions_for(city: str, means: dict[str, float],
                    today: date) -> list[dict]:
    """Persistence + trend lines for tomorrow, using only COMPLETE days.

    Today's mean is partial until midnight UTC, so history ends yesterday:
    persistence for tomorrow = yesterday's mean, and the line records that
    base date explicitly. Honest and scoreable beats nominally fresher.
    """
    hist_dates = sorted(d for d in means if date.fromisoformat(d) < today)
    if len(hist_dates) < max(TREND_KS) + 1:
        raise RuntimeError(f"{city}: only {len(hist_dates)} complete days")
    target = str(today + timedelta(days=1))
    made_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    base = hist_dates[-1]
    t0 = means[base]

    lines = [{
        "made_at": made_at, "city": city, "target_date": target,
        "predictor": "persistence", "predicted": round(t0, 2),
        "base_date": base,
    }]
    for k in TREND_KS:
        tk = means[hist_dates[-1 - k]]
        lines.append({
            "made_at": made_at, "city": city, "target_date": target,
            "predictor": f"trend_{k}",
            "predicted": round(t0 + (t0 - tk) / k, 2),
            "base_date": base,
        })
    return lines


def main() -> int:
    setup_logging()
    PREDICTIONS.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date()
    n = 0
    with open(PREDICTIONS, "a", encoding="utf-8") as fh:
        for city in CITIES:
            t0 = time.time()
            try:
                means = recent_daily_means(city)
                for line in predictions_for(city, means, today):
                    fh.write(json.dumps(line) + "\n")
                    n += 1
            except Exception as exc:
                log.error("%s: %s", city, exc)
            dt = time.time() - t0
            if dt < 1.0:
                time.sleep(1.0 - dt)
    log.info("appended %d prediction line(s) to %s", n, PREDICTIONS.name)
    print(f"appended {n} predictions for {today + timedelta(days=1)}")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
