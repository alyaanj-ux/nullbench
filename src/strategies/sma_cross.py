"""Moving-average crossover — the "hello world" of systematic trading.

Long when the fast MA is above the slow MA, flat otherwise.

Be clear-eyed about this one: it is the most published strategy in existence
and any edge it had was arbitraged away decades ago. It is here because it is
easy to reason about and makes a good baseline. If a fancier strategy cannot
beat this after costs, the fancier strategy is not worth running.
"""

from __future__ import annotations

import pandas as pd

from .base import Strategy, clip_weights


class SmaCross(Strategy):
    name = "sma_cross"

    fast: int = 20
    slow: int = 100
    allow_short: bool = False

    def __init__(self, fast: int = 20, slow: int = 100, allow_short: bool = False):
        super().__init__(fast=fast, slow=slow, allow_short=allow_short)

    def validate(self) -> None:
        if self.params["fast"] >= self.params["slow"]:
            raise ValueError("fast window must be shorter than slow window")
        if self.params["fast"] < 1:
            raise ValueError("fast window must be >= 1")

    @property
    def warmup(self) -> int:
        return int(self.slow) + 1

    def generate_weights(self, bars: pd.DataFrame) -> pd.Series:
        close = bars["close"]
        # min_periods=window means the MA is NaN until fully formed — no
        # partial-window signals sneaking in at the start of the sample.
        fast_ma = close.rolling(self.fast, min_periods=self.fast).mean()
        slow_ma = close.rolling(self.slow, min_periods=self.slow).mean()

        long_signal = (fast_ma > slow_ma).astype(float)
        if self.allow_short:
            weights = long_signal * 2.0 - 1.0          # -> +1 / -1
            weights[fast_ma.isna() | slow_ma.isna()] = 0.0
        else:
            weights = long_signal                       # -> +1 / 0
            weights[fast_ma.isna() | slow_ma.isna()] = 0.0

        return clip_weights(weights, bars.index)
