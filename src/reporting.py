"""Report blocks, formatted in exactly one place.

Why this module exists
----------------------
`scripts/refresh_docs.py` used to reformat the walk-forward and noise-test
output with print statements copied from `scripts/run_backtest.py`. Two copies
of the same formatting drifted almost immediately: different label text
("Mean OOS edge" vs "Mean OOS edge vs benchmark"), different column padding,
and six interpretive lines that the CLI printed and the README did not.

Worse, the whole doc-verification chain was self-referential. README was
compared to a snapshot written by refresh_docs.py, and the snapshot was
compared to refresh_docs.py re-run. Nothing at any speed compared README to the
output of the command README tells the reader to run — which is why the drift
went unnoticed.

Both callers now use the functions here, so the README block is literally the
command's output, and `test_readme_block_matches_the_documented_command`
asserts it.
"""

from __future__ import annotations

import pandas as pd


def format_walk_forward(wf: pd.DataFrame, n_param_sets: int) -> str:
    """The walk-forward table plus its summary and caveats."""
    lines = [wf.to_string(index=False)]
    if wf.empty:
        return "\n".join(lines)

    edge = wf["oos_edge"]
    lines += [
        "",
        f"  Mean in-sample Sharpe:      {wf['in_sample_sharpe'].mean():.2f}",
        f"  Mean out-of-sample Sharpe:  {wf['oos_sharpe'].mean():.2f}",
        f"  Mean OOS benchmark Sharpe:  {wf['oos_benchmark_sharpe'].mean():.2f}",
        f"  Mean OOS edge:             {edge.mean():+.2f}"
        f"   ({(edge > 0).sum()}/{len(edge)} folds positive)",
        "",
        "  Read the EDGE column, not the raw OOS Sharpe — an unbenchmarked",
        "  Sharpe just tells you what the market did in that window.",
        f"  Note: in-sample is the BEST of {n_param_sets} parameter sets while",
        "  out-of-sample is a single run, so the gap overstates degradation.",
    ]
    if len(edge) < 8:
        lines += [
            f"  Note: only {len(edge)} folds — the standard error here is large.",
            "  Treat the mean edge as indicative, not conclusive.",
        ]
    return "\n".join(lines)


def format_noise_test(nt: dict) -> str:
    """The null-distribution summary."""
    return "\n".join([
        "  Sharpe delta vs buy & hold (strategy minus benchmark):",
        f"    mean              {nt['mean_delta']:+.3f}",
        f"    std deviation      {nt['std_delta']:.3f}",
        f"    5th–95th pct      {nt['p5']:+.3f} to {nt['p95']:+.3f}",
        f"    full range        {nt['min_delta']:+.3f} to {nt['max_delta']:+.3f}",
        f"    strategy 'won'    {nt['win_rate']:.0%} of trials",
    ])


def noise_test_verdict(nt: dict) -> str:
    """The interpretation line the CLI prints under the noise block."""
    return (
        f"\n  >> Any single real-data result inside "
        f"[{nt['p5']:+.2f}, {nt['p95']:+.2f}] is indistinguishable from noise."
        f"\n  >> To claim an edge, you need to land OUTSIDE this range "
        f"and stay there."
    )


def walk_forward_values(wf: pd.DataFrame) -> dict[str, float]:
    """Numbers worth pinning, separate from how they are printed.

    Cross-platform float differences are real — libm and BLAS are not bit
    identical between Linux and Windows — so the reproducibility check compares
    these with a tolerance instead of diffing formatted strings. A guarantee
    that only holds on the machine that generated it is not a guarantee.
    """
    if wf.empty:
        return {}
    edge = wf["oos_edge"]
    return {
        "mean_in_sample_sharpe": float(wf["in_sample_sharpe"].mean()),
        "mean_oos_sharpe": float(wf["oos_sharpe"].mean()),
        "mean_oos_benchmark_sharpe": float(wf["oos_benchmark_sharpe"].mean()),
        "mean_oos_edge": float(edge.mean()),
        "folds": float(len(edge)),
        "folds_positive": float((edge > 0).sum()),
    }


def noise_test_values(nt: dict) -> dict[str, float]:
    return {
        "mean_delta": float(nt["mean_delta"]),
        "std_delta": float(nt["std_delta"]),
        "p5": float(nt["p5"]),
        "p95": float(nt["p95"]),
        "win_rate": float(nt["win_rate"]),
    }
