"""Tests for the real-data layer: Stooq parsing and the quality audit.

No network. Every test here feeds a fixed CSV body or a hand-built frame, so
the suite stays runnable offline and deterministic. The live fetch is exercised
by actually running the tool, not by a test that silently passes when the
internet is down.

The audit exists because a generator cannot produce a bad bar. Everything this
module checks for is something real data does and synthetic data never did —
which means none of it was covered by the previous 51 tests.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import pytest

from src.config import Config, DataConfig
from src.data import _cache_path, _parse_stooq_csv, _stooq_symbol
from src.data_quality import (EXTREME_MOVE, audit_bars, audit_universe,
                              format_report)

GOOD_CSV = """Date,Open,High,Low,Close,Volume
2024-01-02,472.16,473.67,470.49,472.65,123456700
2024-01-03,470.43,471.19,468.17,468.79,103221400
2024-01-04,468.30,470.96,467.05,467.28,89887100
"""


def frame(closes, *, volume=1e6, index=None, opens=None):
    """Build a clean OHLC frame from a close series.

    `volume` may be a scalar or a per-bar array — split tests need the split
    day's volume jump. `opens` overrides the default open==close, because the
    split detector reads the OVERNIGHT ratio (prev close -> open).
    """
    closes = np.asarray(closes, dtype=float)
    idx = index if index is not None else pd.bdate_range(
        "2024-01-01", periods=len(closes))
    opens = closes if opens is None else np.asarray(opens, dtype=float)
    vol = (np.full(len(closes), float(volume)) if np.isscalar(volume)
           else np.asarray(volume, dtype=float))
    return pd.DataFrame(
        {"open": opens,
         "high": np.maximum(opens, closes) * 1.001,
         "low": np.minimum(opens, closes) * 0.999,
         "close": closes, "volume": vol},
        index=pd.DatetimeIndex(idx, name="timestamp"),
    )


def split_frame(ratio, *, pre=100.0, n_pre=25, n_post=25, day_move=1.0,
                vol_mult=None):
    """A frame containing one unadjusted forward split of `ratio`:1.

    The split lands at the OPEN (prev close / open == ratio); `day_move`
    optionally moves the price after the open, modelling a split on a day the
    stock also traded. Volume on the split day jumps by `vol_mult`
    (default: the ratio itself, what a real re-denomination does).
    """
    vol_mult = ratio if vol_mult is None else vol_mult
    closes = [pre] * n_pre + [pre / ratio * day_move] * n_post
    opens = list(closes)
    opens[n_pre] = pre / ratio                 # split price, before the day's move
    vol = np.full(n_pre + n_post, 1e6)
    vol[n_pre] = 1e6 * vol_mult
    return frame(closes, opens=opens, volume=vol)


# ---------------------------------------------------------------------------
# Stooq parsing
# ---------------------------------------------------------------------------

def test_stooq_symbol_mapping():
    assert _stooq_symbol("SPY") == "spy.us"
    assert _stooq_symbol(" aapl ") == "aapl.us"
    assert _stooq_symbol("^spx") == "^spx.us"
    # Anything already qualified is left alone.
    assert _stooq_symbol("eurusd.fx") == "eurusd.fx"


def test_parse_stooq_csv_happy_path():
    df = _parse_stooq_csv(GOOD_CSV, "SPY")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 3
    assert df.index.name == "timestamp"
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df["close"].iloc[0] == pytest.approx(472.65)


@pytest.mark.parametrize("body", [
    "",
    "No data",
    "<html><body>404</body></html>",
    "Exceeded the daily hits limit",
])
def test_parse_stooq_csv_rejects_non_csv_bodies(body):
    """Stooq answers 'no such symbol' with HTTP 200 and plain text.

    Parsing that as data yields an empty frame and a confusing failure far from
    the cause. It must raise here, naming the symbol.
    """
    with pytest.raises(RuntimeError, match="SPY"):
        _parse_stooq_csv(body, "SPY")


def test_parse_stooq_csv_rejects_missing_columns():
    with pytest.raises(RuntimeError, match="missing columns"):
        _parse_stooq_csv("Date,Open,Close\n2024-01-02,1,2\n", "SPY")


def test_cache_key_includes_the_source():
    """Regression test for a bug introduced while adding Stooq.

    The cache filename was symbol_timeframe_start_end. Fetch SPY from Stooq,
    re-run with --source alpaca, and you get the Stooq bars back reported as
    Alpaca's. Two providers disagree on splits, dividends and session times,
    so the run answers a different question than the one asked — silently.
    """
    base = dict(symbols=["SPY"])
    stooq = Config(**base, data=DataConfig(source="stooq"))
    alpaca = Config(**base, data=DataConfig(source="alpaca"))

    p1, p2 = _cache_path(stooq, "SPY"), _cache_path(alpaca, "SPY")
    assert p1 != p2, "cache key ignores the data source"
    assert "stooq" in p1.name and "alpaca" in p2.name


def test_cache_key_includes_the_adjustment():
    """Adjusted and raw bars must never share a cache entry.

    A raw NVDA file served back to an `adjustment: all` run reintroduces the
    fake-crash bug from disk instead of from the API — worse, because it
    survives fixing the fetch.
    """
    base = dict(symbols=["NVDA"])
    adj = Config(**base, data=DataConfig(adjustment="all"))
    raw = Config(**base, data=DataConfig(adjustment="raw"))

    p1, p2 = _cache_path(adj, "NVDA"), _cache_path(raw, "NVDA")
    assert p1 != p2, "cache key ignores the adjustment mode"
    assert "all" in p1.name and "raw" in p2.name


def test_cache_key_resolves_null_end_to_today():
    """Regression: `end: null` cached as a literal "latest", which never
    expires. A run tomorrow silently served today's file — a backtest that
    claims to cover "up to today" but stops at whenever the cache was
    written. Keying on the resolved date makes a new day a cache miss."""
    from datetime import date

    cfg = Config(symbols=["SPY"], data=DataConfig(end=None))
    name = _cache_path(cfg, "SPY").name
    assert "latest" not in name, "end=null still caches as an eternal 'latest'"
    assert str(date.today()) in name, (
        f"cache key {name!r} does not pin the resolved end date"
    )

    frozen = Config(symbols=["SPY"], data=DataConfig(end="2024-01-01"))
    assert "2024-01-01" in _cache_path(frozen, "SPY").name


def _run_fetch_alpaca_with_stub(cfg, captured):
    """Run _fetch_alpaca against a stub client, recording the request."""
    import alpaca.data.historical as hist

    class StubClient:
        def __init__(self, *a, **k):
            pass

        def get_stock_bars(self, req):
            captured.append(req)
            idx = pd.MultiIndex.from_product(
                [["NVDA"], pd.date_range("2024-01-01", periods=3, tz="UTC")],
                names=["symbol", "timestamp"])
            frame_ = pd.DataFrame(
                {"open": [1.0] * 3, "high": [1.0] * 3, "low": [1.0] * 3,
                 "close": [1.0] * 3, "volume": [1.0] * 3}, index=idx)

            class R:
                df = frame_
            return R()

    from unittest.mock import patch

    from src.data import _fetch_alpaca
    with patch.object(hist, "StockHistoricalDataClient", StubClient):
        return _fetch_alpaca(cfg, "NVDA")


def test_alpaca_request_carries_the_configured_adjustment():
    """Regression: StockBarsRequest was built with NO adjustment parameter,
    so every fetch silently used the API's default. Whatever that default is,
    it must not be an accident: config.yaml says "all", and the request must
    say what the config says. An unadjusted split is a fake crash the
    backtest will happily trade (NVDA 4:1 2021, 10:1 2024; AAPL 4:1 2020)."""
    from alpaca.data.enums import Adjustment

    captured: list = []
    cfg = Config(symbols=["NVDA"], data=DataConfig(adjustment="all"))
    cfg.creds.api_key, cfg.creds.secret_key = "k", "s"
    _run_fetch_alpaca_with_stub(cfg, captured)

    assert captured, "no request was issued"
    assert getattr(captured[0], "adjustment", None) == Adjustment.ALL, (
        "the request does not carry the configured adjustment — the API "
        "default decides whether splits are fake crashes"
    )

    captured.clear()
    cfg.data.adjustment = "raw"
    _run_fetch_alpaca_with_stub(cfg, captured)
    assert captured[0].adjustment == Adjustment.RAW


def test_invalid_adjustment_fails_loudly_before_any_request():
    cfg = Config(symbols=["NVDA"], data=DataConfig(adjustment="typo"))
    cfg.creds.api_key, cfg.creds.secret_key = "k", "s"
    with pytest.raises(ValueError, match="typo"):
        _run_fetch_alpaca_with_stub(cfg, [])


def test_duplicate_symbols_are_rejected_at_config_load(tmp_path):
    """Regression: get_universe stores bars in a dict, so [SPY, SPY, QQQ]
    silently loaded 2 of "3" symbols. The partial-load guard never fired
    because nothing failed — the dict just deduped. Reject at load time."""
    from src.config import load_config

    cfg_file = tmp_path / "dupes.yaml"
    cfg_file.write_text(
        "universe:\n  symbols: [SPY, SPY, QQQ]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SPY"):
        load_config(cfg_file)


def test_cached_and_uncached_runs_agree_to_1e9(tmp_path, monkeypatch):
    """The CSV cache round-trip perturbs values by ~6e-14 (text
    serialisation), so cached-vs-uncached equity curves are NOT byte-equal.
    That is acceptable — but it must stay at floating-point-noise scale.

    Do NOT tighten this to exact equality (`equity.equals(...)`): it will
    fail whenever the CSV fallback is active, which is any machine without a
    parquet engine — including the one this test was written on.
    """
    from src import data as data_mod
    from src.backtest import Backtester
    from src.config import BacktestConfig, RiskConfig
    from src.data import _clean, synthetic_bars
    from src.strategies import build_strategy

    monkeypatch.setattr(data_mod, "_parquet_available", lambda: False)

    bars = _clean(synthetic_bars("T", n=400, seed=3))
    path = tmp_path / "T_alpaca_all_1Day_2018-01-01_x.parquet"
    data_mod._write_cache(path, bars)
    restored = _clean(data_mod._read_cache(path))

    cfg = Config(
        symbols=["T"], data=DataConfig(timeframe="1Day"),
        backtest=BacktestConfig(initial_cash=10_000.0, slippage_bps=5.0),
        risk=RiskConfig(max_position_pct=1.0, max_gross_exposure=1.0),
    )
    strat = build_strategy("sma_cross", fast=10, slow=50)
    direct = Backtester(cfg).run({"T": bars}, strat)
    cached = Backtester(cfg).run({"T": restored}, strat)

    assert cached.metrics.sharpe == pytest.approx(direct.metrics.sharpe,
                                                  abs=1e-9)
    assert cached.metrics.n_trades == direct.metrics.n_trades
    assert cached.equity.iloc[-1] == pytest.approx(direct.equity.iloc[-1],
                                                   abs=1e-6)


# ---------------------------------------------------------------------------
# Quality audit
# ---------------------------------------------------------------------------

def test_default_source_is_the_officially_sanctioned_api():
    """The default must not silently become a source that disallows crawling.

    Stooq was briefly the default because it needs no key. stooq.com's
    robots.txt broadly disallows automated access, so shipping it as the
    default of a public repo points everyone who clones it at an endpoint that
    asked not to be crawled. Alpaca is an official API whose terms permit
    programmatic use.

    Keeping this as a test rather than a comment because "just flip the
    default back, it's easier for users" is a genuinely tempting change.
    """
    assert DataConfig().source == "alpaca"

    cfg_yaml = (ROOT / "config.yaml").read_text(encoding="utf-8")
    assert re.search(r'^\s*source:\s*"alpaca"', cfg_yaml, re.MULTILINE), \
        "config.yaml's shipped source is not alpaca"


def test_the_stooq_caveat_is_documented_where_someone_will_see_it():
    """A caveat only in a docstring is a caveat nobody reads."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "robots.txt" in readme, (
        "README does not mention that stooq.com's robots.txt disallows "
        "automated access — anyone using --source stooq deserves to know"
    )

    from src import data as data_mod
    assert "robots.txt" in (data_mod._fetch_stooq.__doc__ or "")


def test_cache_round_trips_without_a_parquet_engine(tmp_path, monkeypatch):
    """Regression test for a cache that never worked.

    `_write_cache` wrote `.parquet` unconditionally and swallowed the
    ImportError raised when neither pyarrow nor fastparquet is installed —
    and neither is in requirements.txt. So the cache silently did nothing and
    every run refetched every symbol. Nobody noticed because a missing cache
    is invisible: the numbers are identical, just slower.
    """
    from src import data as data_mod

    monkeypatch.setattr(data_mod, "_parquet_available", lambda: False)

    original = frame([100.0, 101.0, 102.0])
    path = tmp_path / "SPY_stooq_1Day_2024-01-01_latest.parquet"

    data_mod._write_cache(path, original)
    assert path.with_suffix(".csv").exists(), "nothing was written"

    restored = data_mod._read_cache(path)
    assert restored is not None, "cache wrote but could not read back"
    pd.testing.assert_frame_equal(
        restored[["open", "high", "low", "close", "volume"]],
        original[["open", "high", "low", "close", "volume"]],
        check_freq=False,
    )


def test_missing_cache_returns_none_rather_than_raising(tmp_path):
    from src import data as data_mod
    assert data_mod._read_cache(tmp_path / "does_not_exist.parquet") is None


def test_clean_data_produces_no_findings():
    rep = audit_bars(frame(np.linspace(100, 110, 200)), "CLEAN")
    assert rep.ok
    assert not rep.errors and not rep.warnings


def test_detects_an_unadjusted_split():
    """The bug this module exists for.

    Modelled on NVDA's real 10:1 split (June 2024). Unadjusted, the close goes
    from ~1208 to ~120.9 in one session: a -90% "crash" that never happened.
    A mean-reversion strategy buys it and prints a spectacular equity curve.
    Nothing else in the codebase would flag this. Volume jumps ~10x on the
    split day, because ten times as many shares exist — that corroboration is
    what separates a split from a crash.
    """
    rep = audit_bars(split_frame(10, pre=1200.0), "NVDA")

    kinds = [f.kind for f in rep.errors]
    assert "suspected_split" in kinds, "a 10:1 split was not detected"
    assert not rep.ok, "a suspected split must be an error, not a warning"
    assert "10:1" in str(rep.errors[0]) or "10" in str(rep.errors[0])


def test_detects_a_split_on_a_day_the_stock_also_moved():
    """Regression: co-move blindness.

    A 4:1 split on a day the stock also rose 5% is a close-to-close ratio of
    3.81 — outside any tolerance around 4 — and the first detector, which read
    close/close, produced NO split finding for it. The split lands at the
    open; the overnight ratio is exactly 4 regardless of what the day does.
    """
    rep = audit_bars(split_frame(4, day_move=1.05), "MOVED")
    assert any(f.kind == "suspected_split" for f in rep.errors), \
        "a 4:1 split on a +5% day was not detected"


@pytest.mark.parametrize("ratio", [2, 3, 4, 10, 20])
def test_detects_common_split_ratios_both_directions(ratio):
    fwd_vol = np.concatenate([np.full(10, 1e6), np.full(10, 1e6 * ratio)])
    rev_vol = np.concatenate([np.full(10, 1e6), np.full(10, 1e6 / ratio)])
    forward = audit_bars(
        frame([100.0] * 10 + [100.0 / ratio] * 10, volume=fwd_vol), "FWD")
    reverse = audit_bars(
        frame([100.0] * 10 + [100.0 * ratio] * 10, volume=rev_vol), "REV")
    assert any(f.kind == "suspected_split" for f in forward.errors)
    assert any(f.kind == "suspected_split" for f in reverse.errors)


def test_a_real_crash_is_not_labelled_a_split():
    """Regression: the detector cried wolf on the two most newsworthy real
    events an equity series can contain.

    A genuine -34% single-day crash has an overnight ratio ~1.5 — inside the
    3% window around the 1.5:1 ratio — and the price-only detector reported
    it as suspected_split at ERROR severity, with a message telling the user
    their data was corrupt. Volume separates them: a crash does not
    re-denominate the share count. Elevated panic volume (5x here) is nowhere
    near the ~1.5x a real 3:2 split would print — outside the factor-2 band
    around 1.5 — so this must downgrade to a warning, not an error.
    """
    n = 60
    closes = np.full(n, 100.0)
    closes[30:] = 66.0                              # -34% overnight, sticks
    vol = np.full(n, 1e6)
    vol[30] = 5e6                                   # heavy panic volume
    rep = audit_bars(frame(closes, volume=vol), "CRASH")

    assert not any(f.kind == "suspected_split" for f in rep.errors), \
        "a real crash was reported as data corruption"
    assert any(f.kind == "large_gap" for f in rep.warnings), \
        "a -34% overnight gap deserves at least a warning"


def test_a_takeover_pop_is_not_labelled_a_split():
    """+50% overnight on constant volume: ratio ~1/1.5 matched the reverse-
    split window and used to be an error. No volume drop, no reverse split."""
    closes = np.full(60, 100.0)
    closes[30:] = 150.0
    rep = audit_bars(frame(closes), "TAKEOVER")
    assert not any(f.kind == "suspected_split" for f in rep.errors)
    assert any(f.kind == "large_gap" for f in rep.warnings)


def test_an_uncommon_ratio_still_produces_a_finding():
    """Regression: coverage holes.

    A 5:4 stock dividend (ratio 1.25, a -20% overnight gap) matched no entry
    in COMMON_SPLIT_RATIOS and produced NO finding of any kind — clean data,
    said the audit, about a corporate action it exists to catch. Anything
    beyond a 20% overnight gap must at least warn.
    """
    closes = np.full(50, 100.0)
    closes[25:] = 80.0                              # ratio 1.25, -20% gap
    rep = audit_bars(frame(closes), "DIV54")
    assert any(f.kind == "large_gap" for f in rep.warnings), \
        "a 1.25-ratio corporate action produced no finding at all"


def test_covid_shaped_volatility_stays_clean_of_gap_findings():
    """Sessions of +/-9-13% (March 2020 had several) are big days, not data
    defects. They must not produce split errors or large_gap warnings."""
    rng = np.random.default_rng(0)
    moves = np.concatenate([rng.normal(0, 0.01, 90),
                            [-0.12, 0.09, -0.10, 0.11, -0.13],
                            rng.normal(0, 0.01, 105)])
    closes = 100 * np.cumprod(1 + moves)
    rep = audit_bars(frame(closes), "COVID")
    assert not any(f.kind == "suspected_split" for f in rep.findings)
    assert not any(f.kind == "large_gap" for f in rep.findings)


def test_ordinary_volatility_is_not_flagged_as_a_split():
    """The check must not cry wolf, or people will switch it off.

    A 20% crash is a real market day (and gets a warning), but it is nowhere
    near a 2:1 split's 50%, so it must not be reported as a corporate action.
    """
    rng = np.random.default_rng(3)
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.02, 500))
    closes[250] *= 0.80                       # a genuine -20% session
    rep = audit_bars(frame(closes), "VOL")

    assert not any(f.kind == "suspected_split" for f in rep.findings)
    assert any(f.kind == "extreme_moves" for f in rep.warnings)


def test_flags_zero_volume_bars():
    df = frame([100.0] * 50)
    df.iloc[10, df.columns.get_loc("volume")] = 0
    rep = audit_bars(df, "HALT")
    assert any(f.kind == "zero_volume" for f in rep.warnings)


def test_flags_calendar_holes():
    idx = list(pd.bdate_range("2024-01-01", periods=20)) + \
          list(pd.bdate_range("2024-06-01", periods=20))
    rep = audit_bars(frame([100.0] * 40, index=idx), "GAPPY")
    assert any(f.kind == "calendar_gaps" for f in rep.warnings)


def test_flags_impossible_ohlc():
    df = frame([100.0] * 30)
    df.iloc[5, df.columns.get_loc("high")] = 50.0     # high below low
    rep = audit_bars(df, "BROKEN")
    kinds = {f.kind for f in rep.errors}
    assert "high_below_low" in kinds or "ohlc_inconsistent" in kinds
    assert not rep.ok


def test_extreme_move_threshold_is_actually_applied():
    """Pin the boundary so a future refactor cannot quietly widen it."""
    just_under = audit_bars(frame([100.0, 100.0 * (1 + EXTREME_MOVE * 0.9)]), "U")
    just_over = audit_bars(frame([100.0, 100.0 * (1 + EXTREME_MOVE * 1.5)]), "O")
    assert not any(f.kind == "extreme_moves" for f in just_under.findings)
    assert any(f.kind == "extreme_moves" for f in just_over.findings)


def test_ragged_history_is_reported_across_the_universe():
    """The engine trades the intersection of all symbols' dates.

    One short-history name can cut a 7-year backtest to 2 without any error,
    and the result looks completely normal. Only a cross-symbol check sees it.
    """
    data = {
        "LONG": frame([100.0] * 500),
        "SHORT": frame([100.0] * 500).iloc[-50:],
    }
    _, cross = audit_universe(data)
    assert any(f.kind == "ragged_history" for f in cross), \
        "a symbol contributing 10% of the window was not reported"


def test_aligned_universe_produces_no_ragged_warning():
    data = {"A": frame([100.0] * 300), "B": frame([50.0] * 300)}
    _, cross = audit_universe(data)
    assert not any(f.kind == "ragged_history" for f in cross)


def test_ragged_history_boundary_and_partial_trim_visibility():
    """Regression: the 0.9 threshold used strict `<`, so exactly 90%
    retention was silent — and 91-99% produced no output of any kind, hiding
    up to ~7 months of a 1500-bar window. Exactly 90% must now warn, and any
    discarded history must at least leave an info-level trace."""
    def universe(retained, total=1000):
        return {
            "LONG": frame([100.0] * total),
            "SHORT": frame([100.0] * total).iloc[-retained:],
        }

    _, at_boundary = audit_universe(universe(900))       # exactly 90%
    assert any(f.kind == "ragged_history" for f in at_boundary), \
        "exactly 90% retention slipped the strict < comparison"

    _, above = audit_universe(universe(950))             # 95% — no warn, but visible
    assert not any(f.kind == "ragged_history" for f in above)
    assert any(f.kind == "trimmed_history" for f in above), \
        "50 discarded bars left no trace at all"


def test_format_report_surfaces_errors_prominently():
    reports, cross = audit_universe({"NVDA": split_frame(10, pre=1200.0)})
    text = format_report(reports, cross)
    assert "suspected_split" in text
    assert "error" in text.lower()
    assert "NVDA" in text


def test_empty_frame_is_an_error_not_a_crash():
    rep = audit_bars(frame([]), "EMPTY")
    assert not rep.ok
    assert any(f.kind == "empty" for f in rep.errors)
