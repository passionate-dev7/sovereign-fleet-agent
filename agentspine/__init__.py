"""agentspine: shared spine for the three hackathon submissions.

Cloud Scheduler -> Cloud Run Job -> Firestore (idempotency) -> Validator (veto)
-> Gemini/ADK -> GCS (artifact), traced with OpenTelemetry.

See projects/shared/INTERFACE.md for the contract sibling agents code against.
"""

from agentspine.idempotency import (
    IdempotencyBackend,
    MemoryBackend,
    FirestoreBackend,
    compute_run_id,
)
from agentspine.artifacts import ArtifactBackend, LocalBackend, GcsBackend
from agentspine.validator import Validator, Verdict
from agentspine.job import run_tick, TickResult
from agentspine import transcript

__all__ = [
    "transcript",
    "IdempotencyBackend",
    "MemoryBackend",
    "FirestoreBackend",
    "compute_run_id",
    "ArtifactBackend",
    "LocalBackend",
    "GcsBackend",
    "Validator",
    "Verdict",
    "run_tick",
    "TickResult",
]
