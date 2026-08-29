"""Deterministic region data-residency policy engine.

This is the ONE component in Sovereign with veto power over the model.
It is a pure function of structured, trusted inputs: the caller sub-agent's
REGISTRY-DECLARED region, and the DATA's residency label attached at the
storage layer. It never reads model output, model reasoning, or any field
sourced from a record's free-text content. That is a structural guarantee,
not a policy choice: `evaluate()` has no parameter through which prompt
text, "urgency", or a claimed authorization could reach the decision.

Fail-closed: if no clause matches, the default is DENY.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

# --- Declared regions -------------------------------------------------------

REGIONS = ("EU", "US")

# Purposes the policy is willing to reason about at all. Anything else is
# denied outright (SOV-000) rather than silently allowed, because an
# undeclared purpose is exactly the kind of gap a prompt-injected "purpose"
# string would try to exploit.
ALLOWED_PURPOSES = ("support", "analytics", "summarize", "billing")


@dataclass(frozen=True)
class ToolCallRequest:
    """The ONLY inputs the policy engine is allowed to see.

    caller_region: the region declared by the sub-agent AT REGISTRATION
        (registry/, not something the agent asserts at call time).
    data_region: the residency label attached to the row/record by the
        data layer, independent of anything in the record's own content.
    purpose: a structured enum-like string chosen by the gateway from a
        fixed vocabulary, never free text copied from the record or from
        model output.
    record_id / caller_id: for audit logging only, never evaluated.
    """

    caller_region: str
    data_region: str
    purpose: str
    caller_id: str = "unknown-caller"
    record_id: str = "unknown-record"

    def __post_init__(self) -> None:
        if self.caller_region not in REGIONS:
            raise ValueError(f"unknown caller_region: {self.caller_region!r}")
        if self.data_region not in REGIONS:
            raise ValueError(f"unknown data_region: {self.data_region!r}")


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    clause_id: str
    reason: str
    caller_region: str
    data_region: str
    purpose: str
    caller_id: str
    record_id: str

    def to_span_attributes(self) -> dict:
        """OTel span attribute dict. Every attribute the demo trace needs."""
        return {
            "sovereign.policy.clause_id": self.clause_id,
            "sovereign.policy.verdict": "allow" if self.allowed else "deny",
            "sovereign.policy.caller_region": self.caller_region,
            "sovereign.policy.data_region": self.data_region,
            "sovereign.policy.purpose": self.purpose,
            "sovereign.policy.caller_id": self.caller_id,
            "sovereign.policy.record_id": self.record_id,
            "sovereign.policy.reason": self.reason,
        }


@dataclass(frozen=True)
class Clause:
    id: str
    reason_allow: str
    reason_deny: str
    # Returns True -> allow (clause fires, matched), False -> deny (clause
    # fires, matched), None -> clause does not apply, fall through.
    predicate: Callable[[ToolCallRequest], Optional[bool]]


def _purpose_clause(req: ToolCallRequest) -> Optional[bool]:
    if req.purpose not in ALLOWED_PURPOSES:
        return False
    return None  # valid purpose, defer to residency clauses


def _cross_region_deny_clause(req: ToolCallRequest) -> Optional[bool]:
    if req.data_region != req.caller_region:
        return False
    return None


def _same_region_allow_clause(req: ToolCallRequest) -> Optional[bool]:
    if req.data_region == req.caller_region:
        return True
    return None


DEFAULT_CLAUSES: tuple[Clause, ...] = (
    Clause(
        id="SOV-000-PURPOSE",
        reason_allow="",
        reason_deny="purpose is not in the declared policy vocabulary",
        predicate=_purpose_clause,
    ),
    Clause(
        id="SOV-001-RESIDENCY",
        reason_allow="",
        reason_deny=(
            "cross-region processing violates data residency policy: "
            "data may only be processed by a sub-agent registered in its "
            "own region"
        ),
        predicate=_cross_region_deny_clause,
    ),
    Clause(
        id="SOV-002-SAME-REGION",
        reason_allow="caller region matches data region; processing permitted",
        reason_deny="",
        predicate=_same_region_allow_clause,
    ),
)


class PolicyEngine:
    """Deterministic, side-effect-free policy evaluator.

    No network call, no model call, no randomness, no clock. Same input
    always produces the same PolicyDecision. This determinism is what
    makes the engine unit-testable and what makes the denial reproducible
    on camera every single take.
    """

    def __init__(self, clauses: tuple[Clause, ...] = DEFAULT_CLAUSES):
        self.clauses = clauses

    def evaluate(self, request: ToolCallRequest) -> PolicyDecision:
        for clause in self.clauses:
            verdict = clause.predicate(request)
            if verdict is None:
                continue
            reason = clause.reason_allow if verdict else clause.reason_deny
            return PolicyDecision(
                allowed=verdict,
                clause_id=clause.id,
                reason=reason,
                caller_region=request.caller_region,
                data_region=request.data_region,
                purpose=request.purpose,
                caller_id=request.caller_id,
                record_id=request.record_id,
            )
        # Fail closed: no clause matched.
        return PolicyDecision(
            allowed=False,
            clause_id="SOV-999-DEFAULT-DENY",
            reason="fail-closed: no policy clause matched this request",
            caller_region=request.caller_region,
            data_region=request.data_region,
            purpose=request.purpose,
            caller_id=request.caller_id,
            record_id=request.record_id,
        )


DEFAULT_ENGINE = PolicyEngine()
