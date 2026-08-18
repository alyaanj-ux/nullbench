"""Volatility-scaled mean reversion (a z-score / Bollinger variant).

Idea: when price is unusually far below its own recent average, lean long;
when unusually far above, lean flat (or short). Scale the position by how
extreme the move is instead of flipping between all-in and all-out.

This has a slightly better claim to a real mechanism than MA crossover —
short-horizon reversal in liquid equities is a documented effect — but it is
also very sensitive to the lookback and threshold, which makes it easy to
overfit. Use the walk-forward and parameter-sensitivity tools before trusting
any single parameter set.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Strategy, clip_weights


class MeanReversion(Strategy):
    name = "mean_reversion"

    lookback: int = 20
    entry_z: float = 1.0
    max_z: float = 3.0
    allow_short: bool = False

    def __init__(self, lookback: int = 20, entry_z: float = 1.0,
                 max_z: float = 3.0, allow_short: bool = False):
        super().__init__(lookback=lookback, entry_z=entry_z,
                         max_z=max_z, allow_short=allow_short)

    def validate(self) -> None:
        if self.params["lookback"] < 2:
            raise ValueError("lookback must be >= 2")
        if self.params["max_z"] <= self.params["entry_z"]:
            raise ValueError("max_z must exceed entry_z")

    @property
    def warmup(self) -> int:
        return int(self.lookback) + 1

    def generate_weights(self, bars: pd.DataFrame) -> pd.Series:
        close = bars["close"]
        ma = close.rolling(self.lookback, min_periods=self.lookback).mean()
        sd = close.rolling(self.lookback, min_periods=self.lookback).std(ddof=0)

        # Guard against a flat window producing division by zero.
        z = (close - ma) / sd.replace(0.0, np.nan)

        # Below -entry_z we scale in linearly, hitting full size at -max_z.
        span = self.max_z - self.entry_z
        long_leg = ((-z) - self.entry_z).clip(lower=0.0, upper=span) / span

        weights = long_leg
        if self.allow_short:
            short_leg = (z - self.entry_z).clip(lower=0.0, upper=span) / span
            weights = long_leg - short_leg

        return clip_weights(weights, bars.index)
