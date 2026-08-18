"""Weather data adapter: Open-Meteo archive -> daily mean temperature series.

Domain #2 of the universality proof (DESIGN_UNIVERSAL.md). Open-Meteo's
archive API is keyless and free for non-commercial use, CC BY 4.0 — the
attribution line lives in the README. API etiquette: one request per city,
>= 1 second apart, cache-first through the same disk-cache machinery the
market data uses (source key "open-meteo", daily refetch discipline via the
resolved end date in the cache key).

The ten cities are climate-diverse ON PURPOSE: two are southern-hemisphere
(Sydney, São Paulo), which flips seasonality and stress-tests the climatology
code — a hemisphere bug shows up as Sydney's hottest month landing in
June-August, and the audit checks exactly that.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

import numpy as np
import pandas as pd

from ..config import PROJECT_ROOT
from ..data import _read_cache, _write_cache
from ..logging_setup import get_logger

log = get_logger("weather")

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
USER_AGENT = ("algotrader-universality/1.0 "
              "(personal research; cache-first; low volume)")
START_DATE = "1980-01-01"

# name -> (latitude, longitude). Fixed by DESIGN_UNIVERSAL.md.
CITIES: dict[str, tuple[float, float]] = {
    "NYC": (40.71, -74.01),
    "London": (51.51, -0.13),
    "Tokyo": (35.68, 139.69),
    "Karachi": (24.86, 67.01),
    "Sydney": (-33.87, 151.21),
    "SaoPaulo": (-23.55, -46.63),
    "Cairo": (30.04, 31.24),
    "Moscow": (55.76, 37.62),
    "Denver": (39.74, -104.99),
    "Singapore": (1.35, 103.82),
}

# Southern-hemisphere members, for the seasonality sanity check.
SOUTHERN = {"Sydney", "SaoPaulo"}


def _cache_path(city: str) -> "Path":
    from pathlib import Path
    d = PROJECT_ROOT / "data" / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{city}_open-meteo_1Day_{START_DATE}_{date.today()}.parquet"


def _fetch_city(city: str) -> pd.DataFrame:
    lat, lon = CITIES[city]
    params = {
        "latitude": lat, "longitude": lon,
        "start_date": START_DATE, "end_date": str(date.today()),
        "daily": "temperature_2m_mean", "timezone": "UTC",
    }
    url = f"{ARCHIVE_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    # Etiquette rule: on 429/5xx back off 60s and retry ONCE, then give up
    # and let the caller mark the domain blocked. A 46-year archive request
    # is heavy, and the burst limiter genuinely fires (observed live: the
    # first big call after the S1 probe drew a 429 that cleared on retry).
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 429 or exc.code >= 500:
            log.warning("%s: HTTP %d from Open-Meteo — backing off 60s, "
                        "retrying once", city, exc.code)
            time.sleep(60)
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.load(resp)
        else:
            raise
    daily = body.get("daily") or {}
    times, temps = daily.get("time"), daily.get("temperature_2m_mean")
    if not times or temps is None:
        raise RuntimeError(f"Open-Meteo returned no daily data for {city}: "
                           f"{str(body)[:200]}")
    df = pd.DataFrame(
        {"temperature": pd.array(temps, dtype="float64")},
        index=pd.DatetimeIndex(pd.to_datetime(times), name="timestamp"),
    )
    log.info("fetched %d days for %s from Open-Meteo", len(df), city)
    return df


def get_city_series(city: str, use_cache: bool = True) -> pd.Series:
    """One city's daily mean temperature, cache-first."""
    path = _cache_path(city)
    if use_cache:
        cached = _read_cache(path)
        if cached is not None:
            return cached["temperature"]
    df = _fetch_city(city)
    _write_cache(path, df)
    return df["temperature"]


def get_weather_universe(use_cache: bool = True,
                         min_interval: float = 1.0) -> dict[str, pd.Series]:
    """All ten cities, one request at a time, >= min_interval apart."""
    from ..data import _cache_file
    out: dict[str, pd.Series] = {}
    for city in CITIES:
        # Decide BEFORE the call whether this will hit the network — only
        # actual fetches are paced; cache hits cost the provider nothing.
        will_fetch = not (use_cache and _cache_file(_cache_path(city)).exists())
        t0 = time.time()
        out[city] = get_city_series(city, use_cache=use_cache)
        if will_fetch:
            elapsed = time.time() - t0
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
    return out


def audit_city(series: pd.Series, city: str) -> list[str]:
    """Quality findings for one city's series. Strings, human-readable."""
    out: list[str] = []
    n = len(series)
    nan_mask = series.isna()
    if nan_mask.any():
        runs = (nan_mask != nan_mask.shift()).cumsum()[nan_mask]
        longest = int(runs.value_counts().max())
        out.append(f"{int(nan_mask.sum())} NaN day(s), longest run {longest}")
    gaps = series.index.to_series().diff().dt.days.dropna()
    holes = gaps[gaps > 1]
    if len(holes):
        out.append(f"{len(holes)} calendar gap(s), largest {int(holes.max())} days")
    impossible = int((series.abs() > 60).sum())
    if impossible:
        out.append(f"{impossible} impossible value(s) (|T| > 60C)")
    # Seasonality sanity: hemisphere check via hottest month.
    monthly = series.groupby(series.index.month).mean()
    hottest = int(monthly.idxmax())
    if city in SOUTHERN:
        if hottest not in (12, 1, 2):
            out.append(f"HEMISPHERE BUG? hottest month is {hottest}, "
                       f"expected Dec-Feb for {city}")
    else:
        if hottest not in (5, 6, 7, 8, 9):
            out.append(f"seasonality oddity: hottest month {hottest}")
    if not out:
        out.append("clean")
    return [f"{city}: {n} days, {series.index.min().date()} -> "
            f"{series.index.max().date()}"] + [f"  - {f}" for f in out]
