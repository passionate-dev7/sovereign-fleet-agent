"""Tests for the ToolGateway: the enforcement point wiring registry ->
policy -> OTel span -> decision log -> conditional tool execution."""

from __future__ import annotations

from audit.decision_log import DecisionLog
from gateway.tool_gateway import DataRecord, ToolGateway
from policy.engine import DEFAULT_ENGINE
from registry.agent_registry import bootstrap_default_registry


def _gateway():
    return ToolGateway(
        registry=bootstrap_default_registry(),
        policy_engine=DEFAULT_ENGINE,
        decision_log=DecisionLog(),
    )


def test_same_region_call_executes_the_tool():
    gw = _gateway()
    record = DataRecord(record_id="eu-1", region="EU", content="hello EU")
    calls = []

    def tool_fn(rec: DataRecord) -> str:
        calls.append(rec.record_id)
        return f"summary:{rec.record_id}"

    result = gw.invoke("eu-summarizer", record, "summarize", tool_fn)
    assert result.decision.allowed is True
    assert result.tool_result == "summary:eu-1"
    assert calls == ["eu-1"]


def test_cross_region_call_never_executes_the_tool():
    """The core enforcement guarantee: on deny, tool_fn is never invoked,
    so the record's content never leaves the gateway."""
    gw = _gateway()
    record = DataRecord(record_id="eu-2", region="EU", content="secret EU row")
    calls = []

    def tool_fn(rec: DataRecord) -> str:
        calls.append(rec.record_id)
        return "SHOULD NOT HAPPEN"

    result = gw.invoke("us-summarizer", record, "summarize", tool_fn)
    assert result.decision.allowed is False
    assert result.decision.clause_id == "SOV-001-RESIDENCY"
    assert result.tool_result is None
    assert calls == []  # tool_fn never ran


def test_every_call_appends_to_decision_log_regardless_of_verdict():
    gw = _gateway()
    log = gw.decision_log
    eu_record = DataRecord(record_id="eu-3", region="EU", content="x")
    us_record = DataRecord(record_id="us-3", region="US", content="y")

    gw.invoke("eu-summarizer", eu_record, "summarize", lambda r: "ok")
    gw.invoke("us-summarizer", eu_record, "summarize", lambda r: "ok")
    gw.invoke("us-support", us_record, "support", lambda r: "ok")

    assert len(log.entries) == 3
    assert [e.verdict for e in log.entries] == ["allow", "deny", "allow"]
    assert log.verify().valid is True


def test_unregistered_agent_is_denied_and_logged():
    gw = _gateway()
    record = DataRecord(record_id="x", region="US", content="y")
    result = gw.invoke("ghost-agent", record, "summarize", lambda r: "unreachable")
    assert result.decision.allowed is False
    assert result.decision.clause_id == "SOV-998-UNREGISTERED-AGENT"
    assert len(gw.decision_log.entries) == 1
