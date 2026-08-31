"""Shared transcript printer for the three offline demo runners.

Every project's `demo_local.py` prints the same shaped transcript so a
viewer (and a judge) sees one consistent story across all three videos:
ACT headings, labelled facts, and an explicit ALLOW/DENY verdict line.

Pure stdout. No colours by default (terminal recordings and CI logs both
stay readable); set AGENTSPINE_DEMO_COLOR=1 for ANSI colour locally.
"""

from __future__ import annotations

import os
import shutil

_COLOR = os.environ.get("AGENTSPINE_DEMO_COLOR") == "1"


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def _width() -> int:
    return min(shutil.get_terminal_size((78, 24)).columns, 78)


def header(title: str, subtitle: str = "") -> None:
    w = _width()
    print("=" * w)
    print(_c("1", title))
    if subtitle:
        print(subtitle)
    print("=" * w)


def act(number: int, title: str) -> None:
    w = _width()
    print()
    print("-" * w)
    print(_c("1", f"ACT {number}: {title}"))
    print("-" * w)


def step(text: str) -> None:
    print(f"  -> {text}")


def fact(label: str, value: object) -> None:
    print(f"     {label:.<28} {value}")


def verdict_line(passed: bool, reason: str) -> None:
    tag = _c("32", "VALIDATOR: PASSED") if passed else _c("31", "VALIDATOR: REJECTED")
    print(f"  {tag}")
    if reason:
        print(f"     reason: {reason}")


def artifacts(uris: list[str], empty_note: str = "no artifact written") -> None:
    if not uris:
        print(f"  {_c('31', 'ARTIFACTS: 0')} ({empty_note})")
        return
    print(f"  {_c('32', f'ARTIFACTS: {len(uris)}')}")
    for uri in uris:
        print(f"     {uri}")


def note(text: str) -> None:
    print(f"  # {text}")


def summary(rows: list[tuple[str, str]]) -> None:
    w = _width()
    print()
    print("=" * w)
    print(_c("1", "SUMMARY"))
    for label, value in rows:
        print(f"  {label:.<44} {value}")
    print("=" * w)


def assert_demo(condition: bool, message: str) -> None:
    """A demo that silently prints the wrong thing is worse than no demo.
    Every claim the transcript makes is asserted here, so the runner exits
    non-zero if reality stops matching the narration.
    """
    if not condition:
        raise AssertionError(f"DEMO INVARIANT FAILED: {message}")
