"""Validator: the deterministic veto layer, the core thesis of all three projects.

"If deleting the validator leaves the demo working, the project is a wrapper."
(DESIGN.md) So the Validator protocol is intentionally tiny and deterministic:
implementations must not call an LLM to decide passed/failed. They may use an
LLM's *claim* as one input to compare against ground truth (e.g. Refill's
calculator vs. Gemini's asserted next-eligible date), but the verdict itself
comes from arithmetic/policy/a second independent probe, never from asking
the model if it's sure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class Verdict:
    passed: bool
    evidence: dict = field(default_factory=dict)
    reason: str = ""


@runtime_checkable
class Validator(Protocol):
    """A deterministic veto. `context` is whatever the project's job needs
    to decide (e.g. a probe result, a calculated eligibility date, a policy
    lookup). Implementations must be deterministic and side-effect-free.
    """

    def verdict(self, context: Any) -> Verdict:
        ...
