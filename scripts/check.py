#!/usr/bin/env python3
"""Fast post-edit check, wired to Claude Code's PostToolUse hook.

Runs the test suite whenever a Python file is edited and reports concisely.
Exits 2 on failure, which tells Claude Code to surface the error and stop
rather than continuing on top of a broken suite.

Skips itself when only docs or config changed, so editing the README doesn't
trigger a test run.

Run manually any time:  python scripts/check.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Only these extensions trigger a run.
CODE_SUFFIXES = {".py"}


def edited_path_from_hook() -> str | None:
    """Claude Code passes hook context as JSON on stdin. Absent when run by hand."""
    if sys.stdin.isatty():
        return None
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return None
        payload = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return None

    tool_input = payload.get("tool_input") or {}
    return tool_input.get("file_path") or tool_input.get("notebook_path")


def python_executable() -> str:
    """Prefer the project venv so the hook works regardless of how it's invoked."""
    for candidate in (
        PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",   # Windows
        PROJECT_ROOT / ".venv" / "bin" / "python",           # macOS / Linux
    ):
        if candidate.exists():
            return str(candidate)
    return sys.executable


# Editing anything here can move the numbers published in README.
NUMBER_MOVING_DIRS = ("src", "scripts")


def _remind_about_slow_tests(edited: str | None) -> None:
    """Say out loud what the default suite does NOT cover.

    `pytest.ini` deselects `-m slow`, so an engine change that moves the
    numbers quoted in README passes a green suite. That trade is deliberate —
    a hook costing 90 seconds per edit gets switched off, and a switched-off
    hook catches nothing — but round four proved the discipline alone is not
    enough: the docs drifted anyway, and nothing announced the debt.

    So announce it. A reminder nobody reads is still better than silence, and
    this one only fires when the edit could actually have moved a number.
    """
    if edited is None:
        return
    try:
        rel = Path(edited).resolve().relative_to(PROJECT_ROOT)
    except (ValueError, OSError):
        return
    if rel.parts and rel.parts[0] in NUMBER_MOVING_DIRS:
        print(f"  note: {rel.as_posix()} can move the numbers published in "
              f"README.\n"
              f"  The default suite does not check them. Before quoting any "
              f"result:\n"
              f"      python scripts/refresh_docs.py && python -m pytest -m slow")


def main() -> int:
    edited = edited_path_from_hook()

    # Hook mode with a non-code edit -> stay quiet.
    if edited is not None and Path(edited).suffix not in CODE_SUFFIXES:
        return 0

    # encoding="utf-8": Python 3.14 writes UTF-8 to pipes while text=True
    # decodes with the locale — on Windows that mojibakes every em-dash in
    # pytest's failure output, which is exactly the text this hook exists to
    # surface legibly.
    proc = subprocess.run(
        [python_executable(), "-m", "pytest", "tests/", "-q", "--no-header",
         "-x", "--tb=short"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )

    if proc.returncode == 0:
        last = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
        print(f"tests OK — {last[-1] if last else 'passed'}")
        _remind_about_slow_tests(edited)
        return 0

    print("TEST FAILURE — fix before continuing\n", file=sys.stderr)
    print(proc.stdout[-4000:], file=sys.stderr)
    if proc.stderr.strip():
        print(proc.stderr[-2000:], file=sys.stderr)
    # Exit 2 = blocking error in Claude Code's hook protocol.
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.TimeoutExpired:
        print("check.py timed out after 300s", file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:  # never let the hook itself break the session
        print(f"check.py error (non-blocking): {exc}", file=sys.stderr)
        raise SystemExit(1)
