"""Checks that only matter once real data enters the system.

Every number this project has produced so far came from a generator that
cannot emit a bad bar: no splits, no halts, no missing days, no zero volume,
no ragged history between symbols. Real bars have all of those, and each one
has a specific way of turning into a fake backtest result:

  * An unadjusted 4:1 split is a -75% single-bar "crash". A mean-reversion
    strategy will happily buy it, and the equity curve will look brilliant.
  * A multi-day calendar gap silently changes what "next bar's open" means.
    The engine's core safety property is defined in bars, not in time.
  * Zero-volume bars are halts. You could not have traded them at any price.
  * Ragged history across symbols shrinks the intersection the engine trades
    on. One short-history name can quietly cut a 7-year backtest to 2.

None of these throw. They all produce a plausible number, which is worse.
This module surfaces them before the backtest runs rather than after you have
quoted the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .logging_setup import get_logger

log = get_logger("data_quality")

# Ratios a real corporate action lands on. An unadjusted split shows up as an
# OVERNIGHT ratio (prev close -> open) very near one of these; ordinary
# volatility does not.
COMMON_SPLIT_RATIOS = [2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 1.5]

# A single-session move this large is possible but rare enough to eyeball.
EXTREME_MOVE = 0.20

# Any overnight gap at least this large gets a finding even when it matches no
# known split ratio — a 5:4 stock dividend (ratio 1.25, a -20% gap) used to
# produce NO finding at all.
LARGE_OVERNIGHT_GAP = 0.20

# A split multiplies the share count, so volume moves by roughly the same
# factor (forward split: up; reverse split: down). Corroboration requires the
# volume multiple to sit within a factor-of-2 band around that expectation AND
# closer (in log space) to the expectation than to 1.0 — without the second
# condition, unchanged volume sits inside the band for ratios near 1 (a 3:2
# split expects x1.5; [0.75, 3.0] contains 1.0) and a takeover pop would
# "corroborate" itself. A crash or a pop moves price without re-denominating
# the shares, so it fails this test even on a heavy-volume day.
VOLUME_CORROBORATION_BAND = 2.0
VOLUME_BASELINE_WINDOW = 20

# More than this many calendar days between consecutive bars is a data hole,
# not a weekend or a public holiday. (A long weekend is 4 days.)
MAX_EXPECTED_GAP_DAYS = 5


@dataclass
class Finding:
    severity: str            # "error" | "warn" | "info"
    kind: str
    detail: str

    def __str__(self) -> str:
        mark = {"error": "!!", "warn": " !", "info": "  "}[self.severity]
        return f"{mark} [{self.kind}] {self.detail}"


@dataclass
class QualityReport:
    symbol: str
    n_bars: int
    first: pd.Timestamp | None
    last: pd.Timestamp | None
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warn"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _nearest_split_ratio(ratio: float) -> float | None:
    """Return the split ratio this price jump looks like, or None.

    Checks both directions: a 4:1 split divides price by 4, a reverse split
    multiplies it. Tolerance is 3% — wide enough to survive a split landing on
    a day that also moved, tight enough that ordinary volatility does not
    reach it (a 2:1 split is a 50% move; nothing normal is within 3% of that).
    """
    for r in COMMON_SPLIT_RATIOS:
        for candidate in (r, 1.0 / r):
            if abs(ratio - candidate) / candidate < 0.03:
                return candidate
    return None


def audit_bars(df: pd.DataFrame, symbol: str) -> QualityReport:
    """Run every check against one symbol's bars."""
    rep = QualityReport(
        symbol=symbol,
        n_bars=len(df),
        first=df.index.min() if len(df) else None,
        last=df.index.max() if len(df) else None,
    )
    add = rep.findings.append

    if len(df) < 2:
        add(Finding("error", "empty", f"only {len(df)} bar(s)"))
        return rep

    # --- structural impossibilities ------------------------------------
    if not df.index.is_monotonic_increasing:
        add(Finding("error", "unsorted", "timestamps are not increasing"))

    dupes = int(df.index.duplicated().sum())
    if dupes:
        add(Finding("error", "duplicate_bars", f"{dupes} duplicate timestamp(s)"))

    bad_hl = int((df["high"] < df["low"]).sum())
    if bad_hl:
        add(Finding("error", "high_below_low", f"{bad_hl} bar(s)"))

    outside = int(
        ((df["close"] > df["high"]) | (df["close"] < df["low"])
         | (df["open"] > df["high"]) | (df["open"] < df["low"])).sum()
    )
    if outside:
        add(Finding("error", "ohlc_inconsistent",
                    f"{outside} bar(s) with open/close outside the high/low range"))

    nonpositive = int((df[["open", "high", "low", "close"]] <= 0).any(axis=1).sum())
    if nonpositive:
        add(Finding("error", "nonpositive_price", f"{nonpositive} bar(s)"))

    # --- suspected unadjusted corporate actions ------------------------
    # This is the one that silently manufactures profit, so it is an error,
    # not a warning. Three deliberate design points, each fixing a measured
    # failure of the first version:
    #
    # 1. OVERNIGHT ratio (prev close -> open), not close/close. The split
    #    lands at the open; the day's trading happens after. Close-to-close,
    #    a 4:1 split on a day the stock also moved +5% is ratio 3.81 — outside
    #    any 3% window — and was invisible.
    # 2. VOLUME corroboration. A split re-denominates the share count, so
    #    volume moves by roughly the ratio. A genuine -34% crash (ratio ~1.5)
    #    and a +50% takeover pop (~1/1.5) used to be labelled suspected_split
    #    at error severity — the two most newsworthy real events in a series
    #    reported as data corruption. Price alone cannot tell them apart;
    #    volume can.
    # 3. A CATCH-ALL for big gaps that match nothing. A 5:4 stock dividend
    #    (ratio 1.25, -20% overnight) previously produced no finding at all.
    close = df["close"]
    open_ = df["open"]
    # Baseline volume from PRIOR bars only, so the split day's own volume
    # cannot contaminate its baseline.
    vol_base = df["volume"].shift(1).rolling(
        VOLUME_BASELINE_WINDOW, min_periods=1).median()

    on_ratio = (close.shift(1) / open_).dropna()
    for ts, r in on_ratio.items():
        if r <= 0 or not np.isfinite(r):
            continue
        gap_move = 1.0 / r - 1.0                     # signed overnight move
        # The epsilon keeps the boundary inclusive: a 5:4 dividend is exactly
        # -20%, which float arithmetic renders as -0.19999999999999996.
        if abs(gap_move) < LARGE_OVERNIGHT_GAP - 1e-9:
            continue

        hit = _nearest_split_ratio(float(r))
        base = float(vol_base[ts]) if np.isfinite(vol_base[ts]) else 0.0
        vol_mult = float(df["volume"][ts]) / base if base > 0 else np.nan
        # Expected volume multiple == the matched candidate itself: a 4:1
        # forward split multiplies shares (and volume) by ~4; a 1:4 reverse
        # split divides them, i.e. multiplies by ~0.25. Two conditions:
        # inside the band around the expectation, AND closer to the
        # expectation than to "volume unchanged" — see the constant's comment.
        corroborated = (
            hit is not None
            and np.isfinite(vol_mult) and vol_mult > 0
            and hit / VOLUME_CORROBORATION_BAND
                <= vol_mult
                <= hit * VOLUME_CORROBORATION_BAND
            and abs(np.log(vol_mult / hit)) < abs(np.log(vol_mult))
        )

        if corroborated:
            add(Finding(
                "error", "suspected_split",
                f"{ts.date()}: overnight {close.shift(1)[ts]:.2f} -> "
                f"open {open_[ts]:.2f} (ratio {r:.3f} ~ "
                f"{hit:g}:1, volume x{vol_mult:.1f} corroborates). If this "
                f"is an unadjusted split the backtest will trade a crash "
                f"that never happened."
            ))
        else:
            add(Finding(
                "warn", "large_gap",
                f"{ts.date()}: overnight gap {gap_move:+.1%} "
                f"({close.shift(1)[ts]:.2f} -> {open_[ts]:.2f}) without "
                f"volume corroboration of a split — real crash/news, halt "
                f"reopen, or an unlisted corporate-action ratio. Check it "
                f"before believing the backtest."
            ))

    # --- moves worth eyeballing ----------------------------------------
    rets = close.pct_change().dropna()
    extreme = rets[rets.abs() > EXTREME_MOVE]
    if len(extreme):
        worst = extreme.abs().idxmax()
        add(Finding(
            "warn", "extreme_moves",
            f"{len(extreme)} session(s) moved >{EXTREME_MOVE:.0%}; "
            f"largest {rets[worst]:+.1%} on {worst.date()}"
        ))

    # --- calendar holes -------------------------------------------------
    gaps = df.index.to_series().diff().dt.days.dropna()
    holes = gaps[gaps > MAX_EXPECTED_GAP_DAYS]
    if len(holes):
        biggest = holes.idxmax()
        add(Finding(
            "warn", "calendar_gaps",
            f"{len(holes)} gap(s) longer than {MAX_EXPECTED_GAP_DAYS} days; "
            f"largest {int(holes.max())} days ending {biggest.date()}"
        ))

    # --- halts and stale prints ----------------------------------------
    zero_vol = int((df["volume"] <= 0).sum())
    if zero_vol:
        add(Finding("warn", "zero_volume",
                    f"{zero_vol} bar(s) with no volume — halted or bad print; "
                    f"you could not have traded these"))

    flat = (df["open"] == df["close"]) & (df["high"] == df["low"]) \
        & (df["open"] == df["high"])
    if int(flat.sum()):
        add(Finding("warn", "flat_bars",
                    f"{int(flat.sum())} bar(s) with open==high==low==close"))

    if not rep.findings:
        add(Finding("info", "clean", "no issues found"))
    return rep


def audit_universe(data: dict[str, pd.DataFrame]) -> tuple[list[QualityReport], list[Finding]]:
    """Per-symbol reports plus findings that only exist across symbols."""
    reports = [audit_bars(df, sym) for sym, df in sorted(data.items())]
    cross: list[Finding] = []

    if len(data) > 1:
        common = None
        for df in data.values():
            common = df.index if common is None else common.intersection(df.index)
        longest = max(len(df) for df in data.values())
        n_common = len(common) if common is not None else 0

        # The engine trades the intersection. If one short-history symbol
        # halves the usable window, that changes the result and nothing else
        # would tell you.
        #
        # `<=`, not `<`: exactly 90% retention used to be silent, and 91-99%
        # never produced any output at all — up to ~7 months of a 1500-bar
        # window discarded with no trace. The warn threshold is still 90%,
        # but ANY discarded history now gets at least an info line.
        if longest and n_common <= 0.9 * longest:
            shortest_sym = min(data, key=lambda s: len(data[s]))
            cross.append(Finding(
                "warn", "ragged_history",
                f"the engine trades the {n_common}-bar intersection, but the "
                f"longest symbol has {longest} bars ({n_common / longest:.0%} "
                f"retained). Shortest is {shortest_sym} "
                f"({len(data[shortest_sym])} bars)."
            ))
        elif longest and n_common < longest:
            cross.append(Finding(
                "info", "trimmed_history",
                f"intersection is {n_common} bars vs {longest} in the longest "
                f"symbol ({longest - n_common} bars discarded, "
                f"{n_common / longest:.0%} retained)."
            ))
    return reports, cross


def format_report(reports: list[QualityReport], cross: list[Finding]) -> str:
    """Human-readable summary for the CLI."""
    lines = [f"\n{'=' * 60}\n  DATA QUALITY\n{'=' * 60}"]
    for rep in reports:
        span = (f"{rep.first.date()} -> {rep.last.date()}"
                if rep.first is not None else "no data")
        lines.append(f"\n  {rep.symbol}: {rep.n_bars} bars, {span}")
        for f in rep.findings:
            lines.append(f"    {f}")
    if cross:
        lines.append("\n  Across the universe:")
        for f in cross:
            lines.append(f"    {f}")

    n_err = sum(len(r.errors) for r in reports)
    n_warn = sum(len(r.warnings) for r in reports) + len(cross)
    lines.append(f"\n  {n_err} error(s), {n_warn} warning(s)")
    if n_err:
        lines.append("  >> Errors above can manufacture fake profit. "
                     "Fix the data before believing any result.")
    return "\n".join(lines)
