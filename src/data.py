"""Market data: fetch from Alpaca's free tier, cache to disk, or synthesise.

Three sources, in order of preference:
  1. On-disk cache (fast, free, works offline)
  2. Alpaca free IEX feed (needs the free API keys in .env)
  3. Synthetic geometric-Brownian-motion data (needs nothing at all)

Source 3 exists so the whole pipeline is runnable and testable before you
have signed up for anything. It is NOT a substitute for real data when you
are evaluating a strategy — random walks have no exploitable structure.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config
from .logging_setup import get_logger

log = get_logger("data")

# Alpaca's timeframe strings -> (amount, unit) for the SDK
_TIMEFRAME_MINUTES = {
    "1Min": 1, "5Min": 5, "15Min": 15, "30Min": 30,
    "1Hour": 60, "1Day": 60 * 24,
}

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_path(cfg: Config, symbol: str) -> Path:
    """Cache filename. Everything that changes the BARS is part of the key.

    * SOURCE: without it, fetching SPY from Stooq and then re-running with
      `--source alpaca` serves the Stooq bars back from disk and reports them
      as Alpaca's. Two providers disagree on splits, dividends and session
      times, so that is not a caching detail — it silently answers a different
      question than the one asked.
    * ADJUSTMENT: adjusted and raw bars must never share a cache entry. A raw
      NVDA file served to an `adjustment: all` run reintroduces the exact
      fake-crash bug the knob exists to prevent, from disk instead of the API.
    * END, resolved to TODAY when null: `end: null` used to cache as a
      literal "latest", which never expires — tomorrow's run silently
      backtested today's file, forever. Keying on the actual date means a new
      calendar day is a cache miss and a refetch. One request per symbol per
      day is the price of the two runs actually covering the window they
      claim; pass an explicit `end:` if you want a frozen window.
    """
    d = cfg.path(cfg.data.cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    end = cfg.data.end or str(date.today())
    return d / (
        f"{symbol}_{cfg.data.source}_{cfg.data.adjustment}_"
        f"{cfg.data.timeframe}_{cfg.data.start}_{end}.parquet"
    )


def _parquet_available() -> bool:
    """Is a parquet engine installed?

    This is checked rather than assumed because it was not true. The cache
    wrote `.parquet` unconditionally, `to_parquet` raised ImportError when
    neither pyarrow nor fastparquet was present, and `_write_cache` swallowed
    it as a warning. Net effect: the cache silently never worked for anyone who
    had not installed pyarrow separately — it is not in requirements.txt — and
    every run refetched every symbol. Degrading quietly to "no caching at all"
    is exactly the kind of failure this project is supposed to notice.

    Daily bars are tiny, so CSV is a perfectly good fallback and costs no
    dependency.
    """
    for engine in ("pyarrow", "fastparquet"):
        try:
            __import__(engine)
            return True
        except ImportError:
            continue
    return False


def _cache_file(path: Path) -> Path:
    """Resolve the cache path to the format we can actually read and write."""
    return path if _parquet_available() else path.with_suffix(".csv")


def _read_cache(path: Path) -> pd.DataFrame | None:
    target = _cache_file(path)
    if not target.exists():
        return None
    try:
        if target.suffix == ".parquet":
            df = pd.read_parquet(target)
        else:
            df = pd.read_csv(target, index_col=0, parse_dates=True)
            df.index.name = "timestamp"
        log.info("cache hit: %s (%d bars)", target.name, len(df))
        return df
    except Exception as exc:  # a corrupt cache should never be fatal
        log.warning("cache unreadable (%s), refetching: %s", target.name, exc)
        return None


def _write_cache(path: Path, df: pd.DataFrame) -> None:
    target = _cache_file(path)
    try:
        if target.suffix == ".parquet":
            df.to_parquet(target)
        else:
            df.to_csv(target)
    except Exception as exc:
        log.warning("could not write cache %s: %s", target.name, exc)


# ---------------------------------------------------------------------------
# Alpaca
# ---------------------------------------------------------------------------

def _fetch_alpaca(cfg: Config, symbol: str) -> pd.DataFrame:
    """Pull historical bars from Alpaca's free market data API.

    Notes on the free tier (as of writing):
      * Feed is IEX, not the full SIP consolidated tape. Volume is a fraction
        of true market volume and some bars will be missing. Fine for daily
        bars and learning; not fine for anything latency- or microstructure-
        sensitive.
      * There is a rate limit (200 req/min on the free plan). We fetch one
        symbol at a time and cache aggressively rather than retry-storming.
    """
    from alpaca.data.enums import Adjustment
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    # Validate against the SDK's enum rather than a hand-kept list, and fail
    # here — a typo'd adjustment must not fall through to whatever the API
    # defaults to. The default matters: this request previously sent NO
    # adjustment parameter at all, and unadjusted bars turn every split in
    # the window into a fake crash (NVDA 4:1 2021, 10:1 2024; AAPL 4:1 2020).
    try:
        adjustment = Adjustment(cfg.data.adjustment)
    except ValueError as exc:
        valid = ", ".join(a.value for a in Adjustment)
        raise ValueError(
            f"data.adjustment={cfg.data.adjustment!r} is not valid. "
            f"Choose one of: {valid}."
        ) from exc

    if not cfg.creds.is_configured:
        raise RuntimeError(
            "No Alpaca credentials found. Copy .env.example to .env and add "
            "your free paper-trading keys, or run with --synthetic."
        )

    minutes = _TIMEFRAME_MINUTES.get(cfg.data.timeframe)
    if minutes is None:
        raise ValueError(f"Unsupported timeframe: {cfg.data.timeframe}")
    if cfg.data.timeframe == "1Day":
        timeframe = TimeFrame.Day
    elif minutes % 60 == 0:
        timeframe = TimeFrame(minutes // 60, TimeFrameUnit.Hour)
    else:
        timeframe = TimeFrame(minutes, TimeFrameUnit.Minute)

    client = StockHistoricalDataClient(cfg.creds.api_key, cfg.creds.secret_key)

    now = pd.Timestamp.now("UTC").tz_localize(None)
    end = pd.Timestamp(cfg.data.end) if cfg.data.end else now
    # The free feed will not serve you the most recent 15 minutes.
    end = min(end, now - timedelta(minutes=16))

    req = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=timeframe,
        start=pd.Timestamp(cfg.data.start).to_pydatetime(),
        end=end.to_pydatetime(),
        feed=cfg.data.feed,
        adjustment=adjustment,
    )
    bars = client.get_stock_bars(req).df

    if bars.empty:
        raise RuntimeError(f"Alpaca returned no bars for {symbol}")

    # get_stock_bars returns a MultiIndex (symbol, timestamp) — flatten it.
    if isinstance(bars.index, pd.MultiIndex):
        bars = bars.xs(symbol, level="symbol")

    bars = bars[OHLCV_COLUMNS].copy()
    bars.index = pd.DatetimeIndex(bars.index).tz_localize(None)
    bars.index.name = "timestamp"
    log.info("fetched %d bars for %s from Alpaca (%s)", len(bars), symbol, cfg.data.feed)
    return bars


# ---------------------------------------------------------------------------
# Stooq — free daily bars, no API key, no signup
# ---------------------------------------------------------------------------

STOOQ_URL = "https://stooq.com/q/d/l/"


def _stooq_symbol(symbol: str) -> str:
    """Map a US ticker to Stooq's naming ('SPY' -> 'spy.us')."""
    s = symbol.strip().lower()
    return s if "." in s else f"{s}.us"


def _fetch_stooq(cfg: Config, symbol: str) -> pd.DataFrame:
    """Pull daily OHLCV from Stooq as plain CSV. Opt-in, not the default.

    Read this before using it
    -------------------------
    stooq.com's robots.txt disallows all user-agents except Googlebot and
    Bingbot (verified 2026-08). That is the reason this source must never be
    automated: no cron jobs, no CI, no symbol loops without the cache.
    Fetching a few daily CSVs by hand for personal research is a gray area
    that plenty of tooling lives in (pandas-datareader ships a Stooq reader),
    but it is not something to make the shipped default of a public
    repository — that points every stranger who clones it at an endpoint
    whose policy asks not to be crawled.

    So Alpaca is the default: an official API, with terms that permit
    programmatic access, and free keys. Use Stooq only if you have decided
    that is appropriate for your use, and keep the cache on so a repeat run
    costs zero requests.

    What you are getting, stated honestly:
      * Daily bars only. Stooq's free endpoint does not serve intraday.
      * Prices are adjusted for SPLITS but NOT for dividends. That understates
        buy-and-hold's total return, and it understates the strategy's by a
        similar amount, so the *edge* is roughly unaffected — but it is not
        exact, and it is not a total-return comparison. Say so if you quote it.
      * No official rate limit is published. The on-disk cache means a repeat
        run costs zero requests; do not remove it and then loop over symbols.
      * Volume is exchange-reported and occasionally zero on half-days.

    `audit_bars` in data_quality.py runs over whatever comes back, because a
    source claiming to be split-adjusted is not the same as it being so.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    if cfg.data.timeframe != "1Day":
        raise ValueError(
            f"Stooq's free endpoint serves daily bars only, got "
            f"{cfg.data.timeframe!r}. Use --source alpaca for intraday."
        )

    params = {"s": _stooq_symbol(symbol), "i": "d"}
    if cfg.data.start:
        params["d1"] = pd.Timestamp(cfg.data.start).strftime("%Y%m%d")
    if cfg.data.end:
        params["d2"] = pd.Timestamp(cfg.data.end).strftime("%Y%m%d")

    url = f"{STOOQ_URL}?{urllib.parse.urlencode(params)}"
    # Descriptive UA, not a browser impersonation: if the operator ever wants
    # to block this tool, the string makes that easy. Low volume by design —
    # one request per symbol per day, everything else served from the cache.
    req = urllib.request.Request(url, headers={
        "User-Agent": "algotrader/1.0 (personal-research backtester; "
                      "opt-in source; cached, one request per symbol per day)",
    })

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Stooq request failed for {symbol}: {exc}") from exc

    return _parse_stooq_csv(payload, symbol)


def _parse_stooq_csv(payload: str, symbol: str) -> pd.DataFrame:
    """Turn a Stooq CSV body into an OHLCV frame, or fail loudly.

    Split out from the fetch so it is testable without a network call. Stooq
    signals "no such symbol" with HTTP 200 and a plain-text body, not an error
    status — parsing that as data yields an empty frame and a confusing failure
    a long way from the cause.
    """
    from io import StringIO

    head = payload.lstrip()[:200].lower()
    if not head.startswith("date,"):
        snippet = payload.strip()[:120] or "<empty response>"
        raise RuntimeError(
            f"Stooq returned no usable data for {symbol!r}: {snippet!r}. "
            f"Check the ticker exists (US symbols map to '<ticker>.us')."
        )

    df = pd.read_csv(StringIO(payload))
    df.columns = [c.strip().lower() for c in df.columns]

    missing = {"date", *OHLCV_COLUMNS} - set(df.columns)
    if missing:
        raise RuntimeError(
            f"Stooq CSV for {symbol} is missing columns: {sorted(missing)}"
        )

    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")[OHLCV_COLUMNS]
    df.index.name = "timestamp"

    if df.empty:
        raise RuntimeError(f"Stooq returned zero rows for {symbol}")

    log.info("fetched %d bars for %s from Stooq", len(df), symbol)
    return df


# ---------------------------------------------------------------------------
# Synthetic
# ---------------------------------------------------------------------------

def stable_seed(symbol: str) -> int:
    """A per-symbol seed that is identical across processes and machines.

    Do NOT use the builtin hash() here. Python randomises string hashing per
    process (PYTHONHASHSEED), so hash("SPY") differs on every run — which
    silently makes "reproducible" synthetic data irreproducible. blake2b is
    stable forever.
    """
    digest = hashlib.blake2b(symbol.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big")


# Share of daily variance that happens overnight (close -> next open) rather
# than intraday (open -> close). US large caps run roughly 40-60%.
#
# This matters more than it looks. The engine's core safety property is that
# orders fill at the NEXT bar's open. If synthetic opens sit ~0% away from the
# prior close, that discipline costs nothing and is never exercised in P&L
# terms — the guarantee is untested where it counts. An earlier version of this
# generator produced gaps worth 7% of daily vol, which is why this constant
# now exists.
# NOTE this is a VARIANCE share, so the observable gap-vol / daily-vol ratio is
# its square root. Setting it to 0.5 gives a 71% vol ratio, overshooting the
# 40-60% target — a units mistake that is easy to make twice. 0.30 -> ~55%.
OVERNIGHT_VARIANCE_SHARE = 0.30


def _ohlcv_from_components(
    overnight: np.ndarray,
    intraday: np.ndarray,
    rng: np.random.Generator,
    start: str,
    bar_vol: float,
    initial_price: float = 100.0,
) -> pd.DataFrame:
    """Assemble bars from per-day overnight and intraday log returns.

    close[t-1] --overnight--> open[t] --intraday--> close[t]

    Building bars this way (rather than simulating closes and back-filling
    opens) is what produces realistic gaps.
    """
    n = len(overnight)
    open_ = np.empty(n)
    close = np.empty(n)

    prev_close = initial_price
    for t in range(n):
        open_[t] = prev_close * np.exp(overnight[t])
        close[t] = open_[t] * np.exp(intraday[t])
        prev_close = close[t]

    # Wick beyond the open/close body.
    wick = np.abs(rng.normal(0, bar_vol * 0.4, n))
    high = np.maximum(open_, close) * (1 + wick)
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, bar_vol * 0.4, n)))

    volume = rng.integers(500_000, 5_000_000, n).astype(float)
    idx = pd.bdate_range(start=start, periods=n, name="timestamp")

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def synthetic_bars(
    symbol: str,
    n: int = 1500,
    start: str = "2018-01-01",
    seed: int | None = None,
    annual_drift: float = 0.07,
    annual_vol: float = 0.22,
) -> pd.DataFrame:
    """Geometric Brownian motion with a deterministic per-symbol seed.

    Same symbol always yields the same path, on any machine, on any run — so
    a backtest result you quote is a result someone else can reproduce.

    A GBM path has no predictable structure by construction, which makes it a
    useful null hypothesis: if your strategy "makes money" on this, you have
    found overfitting, not edge.
    """
    if seed is None:
        seed = stable_seed(symbol)
    rng = np.random.default_rng(seed)

    dt = 1 / 252
    mu, sigma = annual_drift, annual_vol
    bar_vol = sigma * np.sqrt(dt)
    drift = (mu - 0.5 * sigma**2) * dt

    share = OVERNIGHT_VARIANCE_SHARE
    overnight = rng.normal(drift * share, bar_vol * np.sqrt(share), n)
    intraday = rng.normal(drift * (1 - share), bar_vol * np.sqrt(1 - share), n)

    return _ohlcv_from_components(overnight, intraday, rng, start, bar_vol)


def synthetic_universe(
    symbols: list[str],
    n: int = 1500,
    start: str = "2018-01-01",
    seed: int = 0,
    rho: float = 0.8,
    annual_drift: float = 0.07,
    annual_vol: float = 0.22,
) -> dict[str, pd.DataFrame]:
    """A CORRELATED basket of random-walk paths.

    Why this exists: generating each symbol independently produces a portfolio
    whose variance is far lower than any real basket, because the idiosyncratic
    moves diversify away almost completely. Real large caps run 0.7-0.9
    correlated — SPY, QQQ, AAPL, MSFT and NVDA essentially move together.

    Using independent paths for a null distribution makes the null band **too
    narrow**, which biases you toward falsely concluding a real result sits
    "outside the noise." That is the exact error this tooling exists to
    prevent, so the null has to be built from a realistic basket.

    Uses a one-factor model: each symbol's shock is a blend of a shared market
    factor and its own noise, giving pairwise correlation ~= rho.
    """
    rng = np.random.default_rng(seed)
    k = len(symbols)

    dt = 1 / 252
    bar_vol = annual_vol * np.sqrt(dt)
    drift = (annual_drift - 0.5 * annual_vol**2) * dt
    share = OVERNIGHT_VARIANCE_SHARE

    def correlated(count: int) -> np.ndarray:
        """(k, n) standard normals with pairwise correlation rho."""
        market = rng.standard_normal(count)
        idio = rng.standard_normal((k, count))
        return np.sqrt(rho) * market + np.sqrt(1.0 - rho) * idio

    on_shocks = correlated(n)
    id_shocks = correlated(n)

    out: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(symbols):
        overnight = drift * share + bar_vol * np.sqrt(share) * on_shocks[i]
        intraday = drift * (1 - share) + bar_vol * np.sqrt(1 - share) * id_shocks[i]
        out[sym] = _clean(
            _ohlcv_from_components(overnight, intraday, rng, start, bar_vol)
        )
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

FETCHERS = {"stooq": _fetch_stooq, "alpaca": _fetch_alpaca}


def get_bars(cfg: Config, symbol: str, synthetic: bool = False,
             use_cache: bool = True) -> pd.DataFrame:
    """Return a clean OHLCV frame indexed by timestamp for one symbol."""
    if synthetic:
        return _clean(synthetic_bars(symbol, start=cfg.data.start))

    fetch = FETCHERS.get(cfg.data.source)
    if fetch is None:
        raise ValueError(
            f"Unknown data source {cfg.data.source!r}. "
            f"Choose one of: {', '.join(sorted(FETCHERS))}"
        )

    path = _cache_path(cfg, symbol)
    if use_cache:
        cached = _read_cache(path)
        if cached is not None:
            return _clean(cached)

    df = _clean(fetch(cfg, symbol))
    _write_cache(path, df)
    return df


def get_universe(cfg: Config, synthetic: bool = False,
                 use_cache: bool = True,
                 allow_partial: bool = False) -> dict[str, pd.DataFrame]:
    """Fetch every symbol in the configured universe. Failures are skipped.

    In synthetic mode this builds a CORRELATED basket via `synthetic_universe`,
    the same generator the noise test uses.

    Why that matters: this function used to call `synthetic_bars` per symbol,
    producing an uncorrelated universe (measured pairwise correlation 0.001)
    while the null distribution used correlated paths (0.80). The headline
    synthetic result was therefore not a draw from its own noise band — you
    could not legitimately compare the two numbers, which is the entire point
    of having a band. Same process on both sides or the comparison is void.
    """
    if synthetic:
        # Seed 7 is deliberately NOT a multiple of 1000, so this run can never
        # collide with a noise-test trial (those use seed = trial * 1000).
        # The headline must be an INDEPENDENT draw from the same process —
        # comparing it to a band that literally contains it would be circular.
        return synthetic_universe(cfg.symbols, start=cfg.data.start, seed=7)

    out: dict[str, pd.DataFrame] = {}
    failures: dict[str, str] = {}
    for sym in cfg.symbols:
        try:
            out[sym] = get_bars(cfg, sym, synthetic=False, use_cache=use_cache)
        except Exception as exc:
            failures[sym] = str(exc)
            log.error("failed to load %s: %s", sym, exc)

    if not out:
        # When every symbol fails the same way — no API keys, no network — the
        # cause is one thing, not N things. Repeating it per symbol buries the
        # actionable sentence in noise, and this is the first error a new user
        # ever sees.
        distinct = set(failures.values())
        if len(distinct) == 1:
            raise RuntimeError(
                f"No data loaded. All {len(failures)} symbols failed with the "
                f"same error:\n\n  {distinct.pop()}\n"
            )
        raise RuntimeError(
            "No data loaded for any symbol.\n"
            + "\n".join(f"  {s}: {e}" for s, e in sorted(failures.items()))
        )

    # This used to skip failures silently. With synthetic data nothing ever
    # failed, so the branch was invisible; with real data a fat-fingered or
    # delisted ticker quietly changes the universe, and the result you compare
    # against yesterday's is a different experiment wearing the same name.
    # Refuse by default, and make opting in an explicit act.
    if failures and not allow_partial:
        raise RuntimeError(
            f"Loaded {len(out)}/{len(cfg.symbols)} symbols. Missing: "
            f"{', '.join(sorted(failures))}. A partial universe is a different "
            f"backtest — fix the tickers, or pass allow_partial=True "
            f"(--allow-partial) if you meant it.\n"
            + "\n".join(f"  {s}: {e}" for s, e in sorted(failures.items()))
        )
    if failures:
        log.warning("continuing with %d/%d symbols; missing: %s",
                    len(out), len(cfg.symbols), ", ".join(sorted(failures)))
    return out


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Drop duplicate/incomplete bars and guarantee sorted, numeric OHLCV."""
    df = df.copy()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    for col in OHLCV_COLUMNS:
        if col not in df.columns:
            raise ValueError(f"Missing column {col!r} in bar data")
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    # A zero or negative price is bad data, not a trading opportunity.
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    return df
