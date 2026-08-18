"""The market wrapped as a Domain — instance #1 of the interface.

Mapping to the five-part spec:
  series    -> dict[symbol -> OHLCV frame] (the loaded universe)
  predict   -> run the configured strategy through the backtester
  baseline  -> run buy-and-hold through the same backtester
  score     -> Sharpe(strategy) - Sharpe(buy-and-hold)
  make_null -> night 1's stationary block bootstrap (or the GBM basket)

`evaluate` composes predict/baseline/score exactly the way `noise_test` does,
so the new path must reproduce reports/night_bands.json byte-for-byte — the
anchor tests in tests/test_validation.py enforce that, and the one-time
full-200 reproduction is committed as reports/anchor_200_check.json
(400/400 deltas exact at 6dp).
"""

from __future__ import annotations

from typing import Any

from ..backtest import Backtester
from ..config import Config
from .harness import Domain


class MarketDomain(Domain):
    def __init__(self, cfg: Config, data: dict, strategy_name: str,
                 params: dict | None = None, null: str = "bootstrap"):
        if null not in ("gbm", "bootstrap"):
            raise ValueError(f"unknown null {null!r}")
        self.name = f"market[{null}]"
        self.cfg = cfg
        self._data = data
        self.strategy_name = strategy_name
        self.params = params or {}
        self.null = null

    @property
    def series(self) -> Any:
        return self._data

    def evaluate(self, series: Any) -> float:
        from ..strategies import build_strategy
        s = Backtester(self.cfg).run(
            series, build_strategy(self.strategy_name, **self.params))
        b = Backtester(self.cfg).run(series, build_strategy("buy_and_hold"))
        return s.metrics.sharpe - b.metrics.sharpe

    def make_null(self, series: Any, trial: int) -> Any:
        if self.null == "bootstrap":
            from ..bootstrap_null import resample_universe
            return resample_universe(series, trial)
        from ..data import synthetic_universe
        # Same seeds as noise_test's GBM path: trial * 1000.
        return synthetic_universe(self.cfg.symbols, n=1500, seed=trial * 1000)
