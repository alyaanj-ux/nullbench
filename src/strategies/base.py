"""The Strategy contract.

A strategy's only job is: given a price history, emit a *target weight* for
each bar — a number in [-1, 1] saying what fraction of the portfolio slot
should be in this symbol. 1.0 = fully long, 0.0 = flat, -1.0 = fully short.

Target weights (rather than buy/sell signals) make position sizing, risk
limits, and rebalancing the engine's problem instead of the strategy's.

CRITICAL RULE: `generate_weights` must never use information from bar `i`
to decide the weight applied *at* bar `i`'s close if that weight will be
filled at bar `i`'s price. The backtester handles this by filling on the
next bar's open, but strategies must still avoid centred/forward-looking
transforms (e.g. `.rolling(...).mean().shift(-1)`, `df.iloc[i+1]`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class Strategy(ABC):
    name: str = "base"

    def __init__(self, **params: Any):
        self.params = params
        for key, value in params.items():
            setattr(self, key, value)
        self.validate()

    def validate(self) -> None:
        """Override to raise on nonsensical parameters."""

    @abstractmethod
    def generate_weights(self, bars: pd.DataFrame) -> pd.Series:
        """Return a float Series aligned to `bars.index`, values in [-1, 1]."""

    @property
    def warmup(self) -> int:
        """Bars needed before the first meaningful signal. Used by the live
        loop to decide how much history to pull."""
        return 0

    def __repr__(self) -> str:
        args = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.__class__.__name__}({args})"


def clip_weights(w: pd.Series, index: pd.Index) -> pd.Series:
    """Normalise any strategy output into a safe, aligned weight series."""
    return (
        pd.Series(w, index=index, dtype="float64")
        .fillna(0.0)
        .clip(-1.0, 1.0)
    )
