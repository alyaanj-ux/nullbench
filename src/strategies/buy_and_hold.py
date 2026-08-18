"""Buy and hold — your benchmark, and a humbling one.

Always fully invested. This is the bar every other strategy has to clear on a
risk-adjusted, after-cost basis. Most don't.
"""

from __future__ import annotations

import pandas as pd

from .base import Strategy, clip_weights


class BuyAndHold(Strategy):
    name = "buy_and_hold"

    def __init__(self):
        super().__init__()

    def generate_weights(self, bars: pd.DataFrame) -> pd.Series:
        return clip_weights(pd.Series(1.0, index=bars.index), bars.index)
