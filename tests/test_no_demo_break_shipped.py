"""tests/test_no_demo_break_shipped.py — the filming break must never be
committed.

`demo/break_validator.sh` flips the residency policy engine
(`policy/engine.py`) into a bypassed state so the RED half of the
red/green proof can be filmed in seconds. If that break is committed, the
code a judge clones has no veto at all, and the central claim of this
project is false in the shipped source.

This is not hypothetical: the sibling tabclose repo shipped exactly that
state in its committed HEAD. Its test suite did catch it, which means the
break was committed without running the tests. Grepping for the marker is
cheaper than remembering.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# demo/break_validator.sh stamps exactly this string into whatever it breaks.
MARKER = "DEMO-BREAK"


def test_no_demo_break_marker_in_shipped_source():
    offenders = []
    for py in REPO_ROOT.rglob("*.py"):
        if "__pycache__" in py.parts or ".git" in py.parts:
            continue
        if py.name == Path(__file__).name:  # this file names the marker on purpose
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        if MARKER in text:
            offenders.append(str(py.relative_to(REPO_ROOT)))
    assert offenders == [], (
        f"these files still carry the filming demo-break: {offenders}. "
        "Run demo/restore_validator.sh before committing: shipping this "
        "disables the veto in the code judges clone."
    )


def test_no_leftover_demo_break_backup_files():
    """break_validator.sh leaves a .demo-break-backup next to the file it
    edits. One in the tree means a break was applied and never restored."""
    leftovers = [
        str(p.relative_to(REPO_ROOT))
        for p in REPO_ROOT.rglob("*.demo-break-backup")
        if ".git" not in p.parts
    ]
    assert leftovers == [], (
        f"leftover demo-break backups: {leftovers}. Run "
        "demo/restore_validator.sh and delete the backup before committing."
    )
