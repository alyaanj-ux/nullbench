"""Tests that the documentation still describes the code that exists.

Rounds one and two of the adversarial audit found bugs in the engine and then
bugs in the fixes. Round three found something different: the README quoted a
walk-forward table and a noise band that no longer reproduced, both labelled
"actual output", and a sentence claiming the null universes were independent
directly contradicting another sentence thirteen lines below it.

Nothing was wrong with the code. The docs had simply rotted, and prose does
not run, so no test noticed. These do.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

README = ROOT / "README.md"
SNAPSHOT = ROOT / "docs_snapshot.json"


def _snapshot() -> dict:
    assert SNAPSHOT.exists(), (
        f"{SNAPSHOT.name} is missing — run `python scripts/refresh_docs.py`"
    )
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _readme_block(name: str) -> str:
    text = README.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"<!-- generated:{re.escape(name)} -->\n```\n(.*?)\n```\n"
        rf"<!-- /generated:{re.escape(name)} -->",
        re.DOTALL,
    )
    match = pattern.search(text)
    assert match, f"README.md has no generated:{name} block"
    return match.group(1)


def test_readme_generated_blocks_are_in_sync():
    """README's quoted output must match what refresh_docs.py last produced.

    Cheap: compares text, runs nothing. Catches hand-editing a number into the
    README, which is exactly how the old stale table got there.
    """
    snap = _snapshot()
    assert snap["blocks"], "snapshot contains no blocks — did the script fail?"

    for name, expected in snap["blocks"].items():
        actual = _readme_block(name)
        assert actual == expected, (
            f"README block '{name}' does not match docs_snapshot.json.\n"
            f"Run `python scripts/refresh_docs.py` instead of editing by hand."
        )


def test_readme_does_not_call_the_null_universes_independent():
    """Regression test for a self-contradicting doc.

    README used to say the noise test ran against "many independent
    random-walk universes" and then, thirteen lines later, correctly say the
    universes are a correlated basket. The code agrees with the second one
    (measured pairwise correlation ~0.80). Independence is the specific error
    that makes the null band too narrow, so claiming it in the README
    undersells the fix and misleads anyone reading only the first sentence.
    """
    text = README.read_text(encoding="utf-8")

    # Scan the whole document, not line by line: an audit showed the claim
    # split across a line break slipped past a per-line scan. Collapse
    # whitespace so wrapping cannot hide it.
    flat = re.sub(r"\s+", " ", text)

    # Check only sentences that ASSERT what the tool builds. README also
    # explains *why* independence would be wrong ("independent paths diversify
    # away nearly all portfolio variance") — that sentence is correct and must
    # not be flagged. A regex that cannot tell a claim from an explanation
    # either cries wolf or gets deleted.
    sentences = re.split(r"(?<=[.!?])\s+", flat)
    # Bare "universes"/"baskets" are included on purpose: an audit mutation
    # ("The universes are independent, random-walk baskets.") slipped past a
    # subject list that only knew the fully-qualified phrasings.
    subjects = ("noise-test", "noise test", "null universes",
                "null distribution", "those universes", "universes",
                "baskets")

    # Match the ERROR, not one phrasing of it. "uncorrelated" is the same claim
    # and was invisible to a regex that only knew "independent";
    # "independent, random-walk" was invisible to one requiring adjacency.
    claim = re.compile(
        r"(?<!not )\b(independent|uncorrelated|unrelated)\b[ ,]+"
        r"(random[- ]walk|paths|universes|baskets)",
        re.IGNORECASE,
    )
    offenders = [
        s.strip() for s in sentences
        if any(sub in s.lower() for sub in subjects) and claim.search(s)
    ]
    assert not offenders, (
        "README describes the null universes as independent random walks; "
        f"they are a correlated basket. Offending line(s): {offenders}"
    )


def test_documented_noise_band_matches_the_snapshot():
    """Any [lo, hi] band quoted in prose must be the band the code produces.

    INTERVIEW_PREP.md quoted -0.66 to +0.34 in two places, from a run that
    predates the correlated generator. A number that does not reproduce is
    worst in a file written to be said out loud to an interviewer.
    """
    snap = _snapshot()
    p5, p95 = snap["noise_band"]["p5"], snap["noise_band"]["p95"]

    # An audit found five ways a stale band slipped past the first version of
    # this regex: one decimal place ("-0.9 to +0.9"), percentages
    # ("-66% to +34%"), an en-dash separator, a line lacking the words "band"
    # or "noise", and any value sitting on a line containing the skip words.
    # Match number pairs generally, and scope the historical exemption to the
    # ONE value that is legitimately old rather than to a keyword.
    band_re = re.compile(
        r"([+-]?\d+(?:\.\d+)?)\s*%?\s*(?:to|and|,|–|—|-{1,2}>?)\s*"
        r"([+-]?\d+(?:\.\d+)?)\s*%?"
    )
    # The genuinely historical band, quoted on purpose as the wrong answer.
    HISTORICAL = {(-0.55, 0.17)}

    checked = 0
    # GLOSSARY.md is scanned too: an audit planted a stale band there and
    # nothing noticed, because the list stopped at the three "main" docs.
    for doc in ("README.md", "INTERVIEW_PREP.md", "CLAUDE.md", "GLOSSARY.md"):
        path = ROOT / doc
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            low = line.lower()
            # "distribution" and "spread" joined the list after an audit
            # mutation ("The null distribution runs -0.66 to +0.34 in
            # Sharpe.") sailed past a filter that only knew "band"/"noise".
            if not any(w in low for w in ("band", "noise", "sharpe vs",
                                          "percentile", "pct", "5th",
                                          "distribution", "spread")):
                continue
            for lo, hi in band_re.findall(line):
                lo_f, hi_f = float(lo), float(hi)
                if "%" in line:                      # "-66% to +34%" form
                    lo_f, hi_f = lo_f / 100, hi_f / 100
                if not (-1.5 < lo_f < 0 < hi_f < 1.5):
                    continue                         # not a Sharpe band
                if (round(lo_f, 2), round(hi_f, 2)) in HISTORICAL:
                    continue
                checked += 1
                assert lo_f == pytest.approx(p5, abs=0.02), (
                    f"{doc}:{i} quotes a lower bound of {lo_f}, "
                    f"current is {p5:+.3f} — line: {line.strip()!r}"
                )
                assert hi_f == pytest.approx(p95, abs=0.02), (
                    f"{doc}:{i} quotes an upper bound of {hi_f}, "
                    f"current is {p95:+.3f} — line: {line.strip()!r}"
                )
    assert checked >= 2, (
        f"only {checked} band(s) were checked — the regex or the docs changed "
        f"shape, so this test is no longer guarding anything"
    )


def test_interview_numbers_to_know_cold_are_pinned():
    """Every quantitative row of INTERVIEW_PREP's table must trace to the
    snapshot or the config.

    An audit set the win rate to 99%, the IS->OOS pair to 5.00 -> -9.00, and
    the slippage to 1 bp — three separate lies in the one file scripted to be
    read out loud to an interviewer — and the suite stayed green, because only
    the band row was pinned.
    """
    snap = _snapshot()
    text = (ROOT / "INTERVIEW_PREP.md").read_text(encoding="utf-8")

    def row(label: str) -> str:
        m = re.search(rf"^\|\s*{re.escape(label)}[^|]*\|\s*([^|]+)\|",
                      text, re.MULTILINE)
        assert m, f"INTERVIEW_PREP.md table has no row {label!r}"
        return m.group(1).strip()

    # Win rate: the snapshot stores it; the table quotes it.
    win = row('Strategy "wins" on random data')
    m = re.search(r"(\d+)\s*%", win)
    assert m, f"win-rate row has no percentage: {win!r}"
    assert int(m.group(1)) == round(snap["noise_band"]["win_rate"] * 100), (
        f"table says {m.group(1)}% but the snapshot's win rate is "
        f"{snap['noise_band']['win_rate']:.1%}"
    )

    # IS -> OOS Sharpe pair, against the snapshot's pinned values.
    pair = row("In-sample → out-of-sample Sharpe")
    nums = re.findall(r"[+-]?\d+\.\d+", pair)
    assert len(nums) == 2, f"IS->OOS row does not hold two numbers: {pair!r}"
    vals = snap["values"]
    assert float(nums[0]) == pytest.approx(
        vals["mean_in_sample_sharpe"], abs=0.015), (
        f"table IS Sharpe {nums[0]} vs snapshot "
        f"{vals['mean_in_sample_sharpe']:+.4f}"
    )
    assert float(nums[1]) == pytest.approx(
        vals["mean_oos_sharpe"], abs=0.015), (
        f"table OOS Sharpe {nums[1]} vs snapshot "
        f"{vals['mean_oos_sharpe']:+.4f}"
    )

    # Slippage: must equal what config.yaml actually ships.
    from src.config import load_config
    slip = row("Default slippage assumption")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:bps|bp)", slip)
    assert m, f"slippage row quotes no bps figure: {slip!r}"
    assert float(m.group(1)) == load_config().backtest.slippage_bps, (
        f"table quotes {m.group(1)} bps but config.yaml ships "
        f"{load_config().backtest.slippage_bps}"
    )


def test_readme_and_cli_share_one_formatter():
    """Neither script may format a report block itself.

    refresh_docs.py used to reformat the walk-forward and noise blocks with
    print statements copied from run_backtest.py. The copies drifted: different
    label text, different padding, and six interpretive lines the CLI printed
    and the README omitted. Both must call src/reporting.py so there is exactly
    one implementation.

    Cheap structural check; the behavioural proof is the slow test below.
    """
    reporting = (ROOT / "src" / "reporting.py").read_text(encoding="utf-8")
    assert "def format_walk_forward" in reporting
    assert "def format_noise_test" in reporting

    for script in ("run_backtest.py", "refresh_docs.py"):
        text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert "from src.reporting import" in text, (
            f"{script} does not use the shared formatter"
        )
        assert "Mean in-sample Sharpe" not in text, (
            f"{script} formats a report block itself — that is the duplication "
            f"that already drifted once. Put it in src/reporting.py."
        )


@pytest.mark.slow
def test_generated_blocks_actually_reproduce():
    """Re-run the real thing and report what moved.

    The cheap test proves README matches the snapshot. This proves the snapshot
    still matches the code. Compares pinned NUMBERS with a tolerance rather
    than diffing formatted strings — floats are not bit-identical across
    platforms, and the first version of this check failed on Windows against
    docs generated on Linux while printing no diff.
    """
    # encoding="utf-8" is load-bearing: Python 3.14 writes UTF-8 to pipes
    # while text=True decodes with the locale (cp1252 on Windows). Without it
    # every em-dash in the output mojibakes and string comparisons lie.
    proc = subprocess.run(
        [sys.executable, "scripts/refresh_docs.py", "--check"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, (
        "docs no longer reproduce from a live run — "
        f"run `python scripts/refresh_docs.py`.\n{proc.stdout}\n{proc.stderr}"
    )


@pytest.mark.slow
def test_readme_block_matches_the_documented_command():
    """The guarantee that was missing entirely.

    Every other doc test compares README to a snapshot written by
    refresh_docs.py, or that snapshot to refresh_docs.py re-run. Nothing
    compared README to the output of the command README tells the reader to
    run. That is why a label difference and six missing lines survived: the
    verification chain never touched run_backtest.py.

    This runs the documented command and asserts every non-numeric line of the
    README block appears in its stdout. Digits are excluded because they
    legitimately vary in the last place across platforms; structure must not.
    """
    # encoding="utf-8", or the interpretive lines' em-dashes mojibake on
    # Windows (child writes UTF-8 to the pipe, text=True decodes cp1252) and
    # this test reports the README contains lines "the command does not
    # print" when the command prints them byte-for-byte.
    proc = subprocess.run(
        [sys.executable, "scripts/run_backtest.py",
         "--synthetic", "--walk-forward", "--no-plot"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, f"the documented command failed:\n{proc.stderr}"

    stdout_labels = {
        re.sub(r"[-+\d.]+", "", line).strip()
        for line in proc.stdout.splitlines()
    }
    published = _readme_block("walk_forward")

    checked = 0
    for line in published.splitlines():
        label = re.sub(r"[-+\d.]+", "", line).strip()
        if len(label) < 8:          # skip pure-number rows
            continue
        checked += 1
        assert label in stdout_labels, (
            f"README's walk_forward block contains a line the documented "
            f"command does not print: {line!r}"
        )
    assert checked >= 5, (
        f"only {checked} lines were compared — the block shape changed and "
        f"this test is no longer guarding anything"
    )
