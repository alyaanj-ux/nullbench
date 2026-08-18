"""Loads config.yaml and .env into plain Python objects.

Why a separate module: every other file imports `load_config()` instead of
reading YAML itself, so there is exactly one place that knows the file format.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional — env vars can be set by the shell
    def load_dotenv(*_args, **_kwargs):
        return False

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class DataConfig:
    timeframe: str = "1Day"
    start: str = "2018-01-01"
    end: str | None = None
    # Alpaca is the default because it is an OFFICIAL API: free keys, terms
    # that permit programmatic access, and the only option for intraday.
    #
    # "stooq" is kept as a keyless convenience, but note that stooq.com's
    # robots.txt disallows all user-agents except Googlebot and Bingbot
    # (verified). Pulling a handful of daily CSVs by hand for personal
    # research is a gray area many tools live in; making it the shipped
    # default of a public repo points strangers at an endpoint whose policy
    # asks not to be crawled — so it is opt-in, and must never be automated.
    source: str = "alpaca"
    feed: str = "iex"
    # Corporate-action adjustment for Alpaca bars: "all" | "split" |
    # "dividend" | "raw". Default "all" (splits + dividends) because
    # buy-and-hold is a total-return benchmark — comparing a strategy against
    # it on raw bars flatters the strategy. "raw" exists for demonstrating
    # what an unadjusted split does to a backtest, not for producing results.
    # An unadjusted 4:1 split reads as a -75% single-bar crash that a
    # mean-reversion strategy will happily "profit" from.
    adjustment: str = "all"
    cache_dir: str = "data/cache"


@dataclass
class BacktestConfig:
    initial_cash: float = 10_000.0
    commission_per_share: float = 0.0
    commission_min: float = 0.0
    slippage_bps: float = 5.0
    # NOTE: there is deliberately no `fill_on_next_open` option. Next-open
    # fills are an invariant of the engine, not a setting. A knob implies
    # same-bar fills are a supported mode; they are lookahead bias and must
    # never be reachable. (A dead `fill_on_next_open` flag lived here for a
    # while, defined but read nowhere — worse than useless, since it implied
    # a guarantee was configurable.)


@dataclass
class RiskConfig:
    max_position_pct: float = 0.25
    max_gross_exposure: float = 1.0
    max_daily_loss_pct: float = 0.03
    min_order_notional: float = 1.0


@dataclass
class LiveConfig:
    poll_seconds: int = 60
    dry_run: bool = True
    state_file: str = "state/positions.json"


@dataclass
class Credentials:
    api_key: str | None = None
    secret_key: str | None = None
    paper: bool = True

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.secret_key)


@dataclass
class Config:
    symbols: list[str] = field(default_factory=list)
    data: DataConfig = field(default_factory=DataConfig)
    strategy_name: str = "sma_cross"
    strategy_params: dict[str, Any] = field(default_factory=dict)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    creds: Credentials = field(default_factory=Credentials)

    def path(self, relative: str) -> Path:
        """Resolve a config-relative path against the project root."""
        p = Path(relative)
        return p if p.is_absolute() else PROJECT_ROOT / p


def load_config(path: str | Path = "config.yaml") -> Config:
    load_dotenv(PROJECT_ROOT / ".env")

    cfg_path = Path(path)
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path

    raw: dict[str, Any] = {}
    if cfg_path.exists():
        with open(cfg_path) as fh:
            raw = yaml.safe_load(fh) or {}

    strategy_block = raw.get("strategy", {}) or {}

    symbols = (raw.get("universe", {}) or {}).get("symbols", ["SPY"])
    # Reject duplicate tickers at load time. get_universe() stores bars in a
    # dict, so [SPY, SPY, QQQ] silently loads 2 of "3" symbols — the
    # partial-load guard never fires because nothing *failed*, the dict just
    # deduped. A universe smaller than configured is a different experiment
    # wearing the same name.
    seen: set[str] = set()
    dupes = sorted({s for s in symbols if s in seen or seen.add(s)})
    if dupes:
        raise ValueError(
            f"config.yaml lists duplicate symbol(s): {', '.join(dupes)}. "
            f"Each ticker may appear once — a duplicate silently shrinks the "
            f"universe below what the config claims."
        )

    return Config(
        symbols=symbols,
        data=DataConfig(**(raw.get("data", {}) or {})),
        strategy_name=strategy_block.get("name", "sma_cross"),
        strategy_params=strategy_block.get("params", {}) or {},
        backtest=BacktestConfig(**(raw.get("backtest", {}) or {})),
        risk=RiskConfig(**(raw.get("risk", {}) or {})),
        live=LiveConfig(**(raw.get("live", {}) or {})),
        creds=Credentials(
            api_key=os.getenv("ALPACA_API_KEY"),
            secret_key=os.getenv("ALPACA_SECRET_KEY"),
            # Any value other than a literal "false" keeps us on paper.
            paper=os.getenv("ALPACA_PAPER", "true").lower() != "false",
        ),
    )
