"""Strategy registry.

Add a new strategy by dropping a module in this package and registering the
class below. `build_strategy("my_strat", **params)` is how the rest of the
system instantiates one, so nothing else needs to import your class directly.
"""

from __future__ import annotations

from typing import Any

from .base import Strategy, clip_weights
from .buy_and_hold import BuyAndHold
from .mean_reversion import MeanReversion
from .sma_cross import SmaCross

REGISTRY: dict[str, type[Strategy]] = {
    "sma_cross": SmaCross,
    "mean_reversion": MeanReversion,
    "buy_and_hold": BuyAndHold,
}


def build_strategy(name: str, **params: Any) -> Strategy:
    try:
        cls = REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"Unknown strategy {name!r}. Available: {sorted(REGISTRY)}"
        ) from None
    return cls(**params)


__all__ = ["Strategy", "clip_weights", "build_strategy", "REGISTRY",
           "SmaCross", "MeanReversion", "BuyAndHold"]
