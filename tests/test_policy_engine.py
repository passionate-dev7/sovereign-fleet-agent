"""Unit tests for the deterministic region policy engine.

These are the tests the swarm task explicitly requires:
  - EU row -> US sub-agent: DENIED
  - same-region: ALLOWED
  - unknown/undeclared purpose: DENIED (fail-closed)
  - the model gets no vote: no parameter exists for record content,
    "urgency", or a claimed authorization to influence the verdict.
"""

from __future__ import annotations

import pytest

from policy.engine import (
    DEFAULT_ENGINE,
    PolicyEngine,
    ToolCallRequest,
)


def test_eu_row_to_us_agent_is_denied():
    req = ToolCallRequest(
        caller_region="US",
        data_region="EU",
        purpose="summarize",
        caller_id="us-summarizer",
        record_id="eu-1",
    )
    decision = DEFAULT_ENGINE.evaluate(req)
    assert decision.allowed is False
    assert decision.clause_id == "SOV-001-RESIDENCY"


def test_us_row_to_eu_agent_is_denied_symmetrically():
    req = ToolCallRequest(
        caller_region="EU",
        data_region="US",
        purpose="summarize",
        caller_id="eu-summarizer",
        record_id="us-1",
    )
    decision = DEFAULT_ENGINE.evaluate(req)
    assert decision.allowed is False
    assert decision.clause_id == "SOV-001-RESIDENCY"


def test_same_region_eu_is_allowed():
    req = ToolCallRequest(
        caller_region="EU",
        data_region="EU",
        purpose="summarize",
        caller_id="eu-summarizer",
        record_id="eu-2",
    )
    decision = DEFAULT_ENGINE.evaluate(req)
    assert decision.allowed is True
    assert decision.clause_id == "SOV-002-SAME-REGION"


def test_same_region_us_is_allowed():
    req = ToolCallRequest(
        caller_region="US",
        data_region="US",
        purpose="support",
        caller_id="us-support",
        record_id="us-2",
    )
    decision = DEFAULT_ENGINE.evaluate(req)
    assert decision.allowed is True
    assert decision.clause_id == "SOV-002-SAME-REGION"


def test_unknown_purpose_is_denied_even_same_region():
    req = ToolCallRequest(
        caller_region="US",
        data_region="US",
        purpose="delete_everything",  # not in ALLOWED_PURPOSES
        caller_id="us-summarizer",
        record_id="us-3",
    )
    decision = DEFAULT_ENGINE.evaluate(req)
    assert decision.allowed is False
    assert decision.clause_id == "SOV-000-PURPOSE"


def test_invalid_region_raises_before_any_verdict():
    with pytest.raises(ValueError):
        ToolCallRequest(
            caller_region="APAC",
            data_region="EU",
            purpose="summarize",
        )


def test_engine_is_deterministic_same_input_same_output():
    req = ToolCallRequest(
        caller_region="US", data_region="EU", purpose="summarize"
    )
    d1 = DEFAULT_ENGINE.evaluate(req)
    d2 = DEFAULT_ENGINE.evaluate(req)
    assert d1 == d2


def test_decision_span_attributes_contain_clause_and_regions():
    req = ToolCallRequest(
        caller_region="US",
        data_region="EU",
        purpose="summarize",
        caller_id="us-summarizer",
        record_id="eu-1",
    )
    decision = DEFAULT_ENGINE.evaluate(req)
    attrs = decision.to_span_attributes()
    assert attrs["sovereign.policy.clause_id"] == "SOV-001-RESIDENCY"
    assert attrs["sovereign.policy.verdict"] == "deny"
    assert attrs["sovereign.policy.caller_region"] == "US"
    assert attrs["sovereign.policy.data_region"] == "EU"


def test_a_broken_engine_that_ignores_residency_would_wrongly_allow():
    """This test documents the RED case from the break/restore exercise:
    an engine with the residency clause removed allows the EU row to
    cross into a US caller. We construct that broken engine explicitly
    here (rather than mutating the real one) so this test is a permanent,
    always-green regression guard that the real DEFAULT_ENGINE does NOT
    have this shape, while still proving what "broken" looks like."""
    from policy.engine import Clause, DEFAULT_CLAUSES

    broken_clauses = tuple(
        c for c in DEFAULT_CLAUSES if c.id != "SOV-001-RESIDENCY"
    ) + (
        Clause(
            id="SOV-BROKEN-ALWAYS-ALLOW",
            reason_allow="policy engine broken: residency clause removed",
            reason_deny="",
            predicate=lambda req: True,
        ),
    )
    broken_engine = PolicyEngine(clauses=broken_clauses)

    req = ToolCallRequest(
        caller_region="US", data_region="EU", purpose="summarize"
    )
    decision = broken_engine.evaluate(req)
    # RED: with the residency clause removed, the EU row crosses silently.
    assert decision.allowed is True
    assert decision.clause_id == "SOV-BROKEN-ALWAYS-ALLOW"

    # Restoring the real engine immediately re-denies the same request (GREEN).
    restored_decision = DEFAULT_ENGINE.evaluate(req)
    assert restored_decision.allowed is False
    assert restored_decision.clause_id == "SOV-001-RESIDENCY"
