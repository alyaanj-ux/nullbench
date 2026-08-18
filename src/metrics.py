"""Performance statistics.

Total return is the least interesting number here. Pay attention to max
drawdown (can you actually sit through it?), Sharpe (is the return worth the
volatility?), and turnover (how much of your edge are you paying away in
costs?).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass
class Metrics:
    total_return: float
    cagr: float
    ann_volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    calmar: float
    win_rate: float
    n_trades: int
    rebalance_rate: float
    turnover: float
    total_costs: float
    final_equity: float
    exposure: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)

    def __str__(self) -> str:
        rows = [
            ("Total return",      f"{self.total_return:>10.2%}"),
            ("CAGR",              f"{self.cagr:>10.2%}"),
            ("Ann. volatility",   f"{self.ann_volatility:>10.2%}"),
            ("Sharpe",            f"{self.sharpe:>10.2f}"),
            ("Sortino",           f"{self.sortino:>10.2f}"),
            ("Max drawdown",      f"{self.max_drawdown:>10.2%}"),
            ("Calmar",            f"{self.calmar:>10.2f}"),
            ("Time in market",    f"{self.exposure:>10.2%}"),
            ("Win rate (daily)",  f"{self.win_rate:>10.2%}"),
            ("Rebalances",        f"{self.n_trades:>10d}"),
            ("Rebalance rate/yr", f"{self.rebalance_rate:>10.2f}"),
            ("Turnover (x/yr)",   f"{self.turnover:>10.2f}"),
            ("Total costs",       f"{self.total_costs:>10,.2f}"),
            ("Final equity",      f"{self.final_equity:>10,.2f}"),
        ]
        width = max(len(k) for k, _ in rows)
        return "\n".join(f"  {k:<{width}}  {v}" for k, v in rows)


def max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough decline, as a negative fraction."""
    if equity.empty:
        return 0.0
    running_peak = equity.cummax()
    return float((equity / running_peak - 1.0).min())


def compute_metrics(
    equity: pd.Series,
    exposure: pd.Series | None = None,
    n_trades: int = 0,
    total_costs: float = 0.0,
    periods_per_year: int = TRADING_DAYS,
    risk_free_rate: float = 0.0,
    total_notional: float = 0.0,
) -> Metrics:
    """
    Two distinct trading-activity numbers, previously conflated:

      rebalance_rate = orders per year. An activity count.
      turnover       = notional traded per year / average equity. The real
                       turnover figure, and the one that tells you how much
                       cost drag to expect.

    These differ by ~6x in practice. The old code labelled the first one
    "Turnover", which materially overstated it.
    """
    equity = equity.dropna()
    if len(equity) < 2:
        return Metrics(0, 0, 0, 0, 0, 0, 0, 0, n_trades, 0, 0, total_costs,
                       float(equity.iloc[-1]) if len(equity) else 0.0, 0)

    returns = equity.pct_change().dropna()
    years = len(returns) / periods_per_year

    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)

    # A wiped-out account ends at or below zero, and a fractional power of a
    # negative base does not have a real value. With a numpy scalar that
    # surfaced as nan; with the explicit float() cast below it is a complex
    # result and float() raises TypeError. Either way the summary table loses
    # the single most important thing a backtest can tell you — "this blew up"
    # — to a blank cell or a crash. -100% is the honest answer: capital gone.
    ratio = float(equity.iloc[-1] / equity.iloc[0])
    if years <= 0:
        cagr = 0.0
    elif ratio <= 0:
        cagr = -1.0
    else:
        cagr = float(ratio ** (1 / years) - 1.0)

    ann_vol = float(returns.std(ddof=1) * np.sqrt(periods_per_year))
    excess = returns - risk_free_rate / periods_per_year
    sharpe = float(excess.mean() / returns.std(ddof=1) * np.sqrt(periods_per_year)) \
        if returns.std(ddof=1) > 0 else 0.0

    downside = returns[returns < 0]
    dstd = downside.std(ddof=1) if len(downside) > 1 else 0.0
    sortino = float(excess.mean() / dstd * np.sqrt(periods_per_year)) if dstd > 0 else 0.0

    mdd = max_drawdown(equity)
    calmar = float(cagr / abs(mdd)) if mdd < 0 else 0.0

    win_rate = float((returns > 0).mean())
    rebalance_rate = float(n_trades / years) if years > 0 else 0.0

    avg_equity = float(equity.mean())
    turnover = (float(total_notional / avg_equity / years)
                if years > 0 and avg_equity > 0 else 0.0)

    exp = float(exposure.abs().mean()) if exposure is not None and len(exposure) else 0.0

    return Metrics(
        total_return=total_return, cagr=cagr, ann_volatility=ann_vol,
        sharpe=sharpe, sortino=sortino, max_drawdown=mdd, calmar=calmar,
        win_rate=win_rate, n_trades=n_trades, rebalance_rate=rebalance_rate,
        turnover=turnover, total_costs=total_costs,
        final_equity=float(equity.iloc[-1]), exposure=exp,
    )
