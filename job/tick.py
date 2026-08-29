"""The idempotent job tick: builds a batch of gateway calls, runs them once,
persists the resulting hash-chained decision log as the run's artifact.

Reuses agentspine's `IdempotencyBackend` (claim/mark_complete, deterministic
run_id, no timestamp/uuid4) and `ArtifactBackend` (Local/Gcs) for the same
crash-resume and two-laptops guarantees the other two submissions rely on.

Sovereign's tick differs from agentspine's own `run_tick` in one structural
way: for Tabclose/Refill, a validator REJECT means "nothing happened, write
zero artifacts." For Sovereign, a policy DENY is not a failure state, it is
exactly what must be recorded. So this module does not gate artifact-writing
on any call's verdict; every call in the batch (allow or deny) is appended
to the DecisionLog, and the whole batch's decision log is the one artifact
this job tick writes. Idempotency at the job level still guarantees a
duplicate Scheduler fire, or a resumed crash, writes that artifact exactly
once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from agentspine.artifacts import ArtifactBackend
from agentspine.idempotency import IdempotencyBackend, compute_run_id
from agentspine import tracing

from audit.decision_log import DecisionLog
from gateway.tool_gateway import DataRecord, ToolGateway


@dataclass(frozen=True)
class BatchCall:
    """One gateway.invoke() the job tick will make."""

    agent_id: str
    record: DataRecord
    purpose: str
    tool_fn: Callable[[DataRecord], object]


@dataclass
class JobTickResult:
    run_id: str
    status: str  # skipped_complete | complete
    artifact_uri: Optional[str] = None
    decisions: Optional[list[dict]] = None


def run_job_tick(
    subject: str,
    window_start: str,
    calls: list[BatchCall],
    gateway: ToolGateway,
    artifact_backend: ArtifactBackend,
    idempotency_backend: IdempotencyBackend,
) -> JobTickResult:
    """Run one idempotent tick of the Sovereign job.

    `subject` + `window_start` determine the deterministic run_id exactly
    as agentspine's other two projects do; calling this twice for the same
    (subject, window_start) is safe and writes the decision log artifact
    at most once.
    """
    run_id = compute_run_id(subject, window_start)

    with tracing.span("sovereign.claim", {"run_id": run_id, "subject": subject}):
        may_proceed = idempotency_backend.claim(run_id, subject, window_start)

    if not may_proceed:
        return JobTickResult(run_id=run_id, status="skipped_complete")

    for call in calls:
        with tracing.span(
            "sovereign.batch_call",
            {"run_id": run_id, "agent_id": call.agent_id, "record_id": call.record.record_id},
        ):
            result = gateway.invoke(call.agent_id, call.record, call.purpose, call.tool_fn)
            tracing.record_decision(
                f"policy.{call.agent_id}",
                result.decision.allowed,
                result.decision.to_span_attributes(),
            )

    entries = [e.to_dict() for e in gateway.decision_log.entries]
    artifact_path = f"decisions/{run_id}.json"
    with tracing.span("sovereign.write_artifact", {"run_id": run_id}):
        artifact_uri = artifact_backend.write(
            artifact_path,
            json.dumps(entries, indent=2),
            content_type="application/json",
        )

    verify_result = gateway.decision_log.verify()
    with tracing.span(
        "sovereign.complete",
        {"run_id": run_id, "chain_valid": verify_result.valid},
    ):
        idempotency_backend.mark_complete(
            run_id,
            artifact_uri,
            {
                "passed": verify_result.valid,
                "reason": verify_result.reason,
                "evidence": {"entries": len(entries)},
            },
        )

    return JobTickResult(
        run_id=run_id, status="complete", artifact_uri=artifact_uri, decisions=entries
    )
