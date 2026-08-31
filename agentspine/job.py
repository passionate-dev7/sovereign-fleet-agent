"""run_tick: the crash-safe, resumable job body every Cloud Run Job invokes.

Sequence (see DESIGN.md state model):
    1. claim the deterministic run_id.
       - already `complete` -> return immediately, no side effects. This is
         what makes a duplicate Scheduler fire (or a retried crash) safe.
       - otherwise (fresh claim, or claimed-but-not-complete from a prior
         crash) -> proceed.
    2. run the validator (deterministic veto).
       - REJECTED -> record the rejection, return WITHOUT calling artifact_fn.
         Zero artifacts for a failing validator, always.
       - PASSED -> call artifact_fn, then mark_complete with its result.

`artifact_fn` owns its own ArtifactBackend (Local or Gcs) via closure and is
responsible for actually writing content; it returns the artifact URI (or a
list of URIs, in which case the first is stored as the primary
`artifact_uri`). This keeps run_tick's signature exactly
(subject, window, validator, artifact_fn, backend) while staying agnostic to
what each project's artifact actually looks like.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Union

from agentspine.idempotency import IdempotencyBackend, compute_run_id
from agentspine.validator import Validator, Verdict
from agentspine import tracing

Window = Union[str, tuple[str, Optional[str]], list]

ArtifactResult = Union[str, list[str]]
ArtifactFn = Callable[[dict, Verdict], ArtifactResult]


@dataclass
class TickResult:
    run_id: str
    status: str  # skipped_complete | rejected | complete
    verdict: Optional[Verdict] = None
    artifact_uri: Optional[str] = None


def _split_window(window: Window) -> tuple[str, Optional[str]]:
    if isinstance(window, (tuple, list)):
        if len(window) == 1:
            return str(window[0]), None
        return str(window[0]), (str(window[1]) if window[1] is not None else None)
    return str(window), None


def run_tick(
    subject: str,
    window: Window,
    validator: Validator,
    artifact_fn: ArtifactFn,
    backend: IdempotencyBackend,
) -> TickResult:
    """Run one idempotent tick. Safe to call any number of times for the
    same (subject, window); only the first successful pass writes an
    artifact and reaches `complete`.
    """
    window_start, window_end = _split_window(window)
    run_id = compute_run_id(subject, window_start)

    with tracing.span("agentspine.claim", {"run_id": run_id, "subject": subject}):
        may_proceed = backend.claim(run_id, subject, window_start, window_end)

    if not may_proceed:
        return TickResult(run_id=run_id, status="skipped_complete")

    context = {
        "subject": subject,
        "window_start": window_start,
        "window_end": window_end,
        "run_id": run_id,
    }

    with tracing.span("agentspine.validate", {"run_id": run_id}) as _:
        verdict = validator.verdict(context)
    tracing.record_decision("validator", verdict.passed, {"run_id": run_id})

    if not verdict.passed:
        with tracing.span("agentspine.reject", {"run_id": run_id}):
            backend.mark_rejected(run_id, verdict.reason, verdict.evidence)
        return TickResult(run_id=run_id, status="rejected", verdict=verdict)

    with tracing.span("agentspine.artifact", {"run_id": run_id}):
        result = artifact_fn(context, verdict)
    artifact_uri = result[0] if isinstance(result, list) else result

    verdict_dict = {
        "passed": verdict.passed,
        "reason": verdict.reason,
        "evidence": verdict.evidence,
    }
    with tracing.span("agentspine.complete", {"run_id": run_id}):
        backend.mark_complete(run_id, artifact_uri, verdict_dict)

    return TickResult(run_id=run_id, status="complete", verdict=verdict,
                        artifact_uri=artifact_uri)
