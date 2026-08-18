#!/usr/bin/env python
"""Regenerate reports/universality.png from the committed artifacts.

The chart is a committed artifact, so its generator lives in the repo too —
same discipline as refresh_docs.py. Reads night_bands.json,
weather_validation.json and synthetic_noise_validation.json; writes the
three-panel figure the README opens with.

Layout note, learned the hard way: every annotation is placed in AXES
FRACTION coordinates (ax.transAxes), never at data coordinates derived from
the result values — a data-placed label near an axis edge clips silently
("...de the resolution"), and nothing fails.

Usage:  python scripts/make_universality_chart.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

RED, BLUE, EDGE, AMBER = "#c0392b", "#8fb4d9", "#5d8ab8", "#e8c468"


def main() -> int:
    rep = ROOT / "reports"
    nb = json.loads((rep / "night_bands.json").read_text(encoding="utf-8"))
    wp = json.loads((rep / "weather_validation.json").read_text(
        encoding="utf-8"))["results"]["persistence"]
    sp = json.loads((rep / "synthetic_noise_validation.json").read_text(
        encoding="utf-8"))["results"]["persistence"]

    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.8), dpi=150)

    # --- Panel 1: market, raw Sharpe delta ------------------------------
    ax = axes[0]
    null = np.array(nb["bootstrap"]["deltas"])
    real = nb["headline"]["delta"]
    ax.hist(null, bins=24, color=BLUE, edgecolor=EDGE, alpha=0.9)
    ax.axvline(real, color=RED, lw=2.4)
    ax.text(0.63, 0.95, f"real {real:+.2f}\ninside the band",
            transform=ax.transAxes, color=RED, fontsize=8.5, va="top")
    ax.set_title("MARKET\nSmaCross vs buy & hold\n"
                 "verdict NOISE \u2014 required NOISE \u2713", fontsize=9.5)
    ax.set_xlabel("Sharpe delta vs buy & hold", fontsize=8.5)
    ax.set_ylabel("null trials (200)")

    # --- Panels 2 & 3: zero-corrected skill with the resolution zone ----
    for ax, r, title, note, text_xy, text_ha, leg_loc in (
        (axes[1], wp,
         "WEATHER\npersistence vs climatology\n"
         "verdict REAL \u2014 required REAL \u2713",
         "80x the instrument's\nresolution", (0.84, 0.68), "right",
         "center left"),
        (axes[2], sp,
         "SYNTHETIC NOISE\nsame pipeline, generated data\n"
         "verdict NOISE \u2014 required NOISE \u2713",
         "inside the resolution:\nzero, as built", (0.97, 0.95), "right",
         "lower left"),
    ):
        z, res = r["zero_offset"], r["resolution"]
        null_c = np.array(r["null_stats"]) - z
        real_c = r["calibrated"]
        ax.hist(null_c, bins=24, color=BLUE, edgecolor=EDGE, alpha=0.9,
                label="null trials (zeroed)")
        ax.axvspan(-res, res, color=AMBER, alpha=0.5, zorder=0,
                   label=f"resolution \u00b1{res:.3f}")
        ax.axvline(real_c, color=RED, lw=2.4)
        lo = min(null_c.min(), real_c, -res)
        hi = max(null_c.max(), real_c, res)
        pad = 0.18 * (hi - lo)
        ax.set_xlim(lo - pad, hi + pad)
        ax.text(*text_xy, f"zeroed {real_c:+.3f}\n{note}",
                transform=ax.transAxes, color=RED, fontsize=8.5,
                va="top", ha=text_ha)
        ax.set_title(title, fontsize=9.5)
        ax.set_xlabel("skill, zero-corrected", fontsize=8.5)
        ax.legend(loc=leg_loc, fontsize=7, framealpha=0.9)

    # The synthetic panel shows the whole zero-test story: the shuffle-null
    # histogram sits below the honest zero by its own measured bias.
    axes[2].annotate("the shuffle-null's own\nbias, measured (-0.015)",
                     xy=(-0.015, 3.0), xycoords="data",
                     xytext=(0.06, 0.60), textcoords="axes fraction",
                     fontsize=7.5, color="#33608c",
                     arrowprops=dict(arrowstyle="->", color="#33608c",
                                     lw=0.9))

    for ax in axes:
        ax.tick_params(labelsize=8)
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("One instrument, three domains, known ground truth",
                 fontsize=11.5, y=1.03)
    fig.tight_layout()
    out = rep / "universality.png"
    fig.savefig(out, bbox_inches="tight")
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
