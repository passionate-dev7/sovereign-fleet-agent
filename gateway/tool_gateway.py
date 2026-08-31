"""The gateway: every sub-agent tool call passes through here first.

Call path:
    orchestrator -> gateway.invoke(agent_id, record, purpose) -> [POLICY] ->
        allow -> sub-agent tool function runs, gets the record
        deny  -> sub-agent tool function NEVER runs, record is not passed anywhere

Both paths open an OTel span with policy decision attributes and append an
entry to the hash-chained decision log. The record's free-text `content`
field is never passed to `policy.engine.evaluate()` -- only its structured
`region` label is. That is what makes the prompt-injection demo (injection/)
work: the model can read and even act on injected instructions in its own
reasoning, but the gateway decision is computed before the sub-agent ever
sees the content, from data the injected text cannot reach.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode

    tracer = trace.get_tracer("sovereign.gateway")
except ImportError:  # pragma: no cover - exercised only without the dep
    # OTel is optional the same way agentspine.tracing treats it: the gateway
    # must still veto cross-region calls with no exporter and no OTel installed
    # at all (e.g. the browser 'Try it out' function). Fall back to a no-op
    # tracer so the policy path is byte-for-byte identical either way.
    import contextlib

    class _NoopSpan:
        def set_attribute(self, *_a, **_k):
            pass

        def set_status(self, *_a, **_k):
            pass

    class _NoopTracer:
        @contextlib.contextmanager
        def start_as_current_span(self, *_a, **_k):
            yield _NoopSpan()

    class StatusCode:  # noqa: N801 - mirrors the OTel enum surface used below
        OK = "OK"
        ERROR = "ERROR"

    def Status(_code):  # noqa: N802 - mirrors the OTel callable used below
        return _code

    tracer = _NoopTracer()

from audit.decision_log import DecisionLog
from policy.engine import PolicyDecision, PolicyEngine, ToolCallRequest
from registry.agent_registry import AgentRegistry, UnknownAgentError


@dataclass(frozen=True)
class DataRecord:
    """A row the fleet might process. `region` is the trusted residency
    label attached at the storage layer. `content` is untrusted free text
    that may contain an injected instruction -- it is available to the
    sub-agent tool function ONLY after an allow verdict, and is never
    passed to the policy engine."""

    record_id: str
    region: str
    content: str


class PolicyDeniedError(Exception):
    """Raised when a caller tries to force execution of a denied call."""

    def __init__(self, decision: PolicyDecision):
        self.decision = decision
        super().__init__(decision.reason)


@dataclass(frozen=True)
class GatewayResult:
    decision: PolicyDecision
    tool_result: Optional[object]  # None when denied


class ToolGateway:
    """Wires registry lookup -> policy evaluation -> OTel span -> decision
    log -> (conditionally) the sub-agent tool call, in that order."""

    def __init__(
        self,
        registry: AgentRegistry,
        policy_engine: PolicyEngine,
        decision_log: DecisionLog,
        caller_id: str = "sovereign-orchestrator",
    ):
        self.registry = registry
        self.policy_engine = policy_engine
        self.decision_log = decision_log
        self.caller_id = caller_id

    def invoke(
        self,
        agent_id: str,
        record: DataRecord,
        purpose: str,
        tool_fn: Callable[[DataRecord], object],
    ) -> GatewayResult:
        """Route a tool call through policy. `tool_fn` receives the record
        ONLY if the policy decision allows it. Denied calls never invoke
        `tool_fn` -- this is the bounded-authority guarantee: Sovereign
        can refuse to run the call, it cannot un-run one it already ran.
        """
        try:
            sub_agent = self.registry.latest(agent_id)
        except UnknownAgentError:
            # Unregistered agent: fail closed with a synthetic decision,
            # do not attempt to evaluate policy against a region we cannot
            # trust because it was never declared at registration.
            decision = PolicyDecision(
                allowed=False,
                clause_id="SOV-998-UNREGISTERED-AGENT",
                reason=f"agent_id {agent_id!r} is not in the registry",
                caller_region="UNKNOWN",
                data_region=record.region,
                purpose=purpose,
                caller_id=self.caller_id,
                record_id=record.record_id,
            )
            return self._finish(decision, tool_result=None)

        request = ToolCallRequest(
            caller_region=sub_agent.region,
            data_region=record.region,
            purpose=purpose,
            caller_id=self.caller_id,
            record_id=record.record_id,
        )
        decision = self.policy_engine.evaluate(request)

        if not decision.allowed:
            return self._finish(decision, tool_result=None)

        with tracer.start_as_current_span(f"sovereign.tool_call.{agent_id}") as span:
            for k, v in decision.to_span_attributes().items():
                span.set_attribute(k, v)
            result = tool_fn(record)
            span.set_status(Status(StatusCode.OK))

        self.decision_log.append_decision(decision)
        return GatewayResult(decision=decision, tool_result=result)

    def _finish(
        self, decision: PolicyDecision, tool_result: Optional[object]
    ) -> GatewayResult:
        with tracer.start_as_current_span(
            f"sovereign.tool_call.{decision.reason[:40]}"
            if not decision.allowed
            else "sovereign.tool_call"
        ) as span:
            for k, v in decision.to_span_attributes().items():
                span.set_attribute(k, v)
            span.set_status(
                Status(StatusCode.OK if decision.allowed else StatusCode.ERROR)
            )
        self.decision_log.append_decision(decision)
        return GatewayResult(decision=decision, tool_result=tool_result)
