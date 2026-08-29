"""Cloud Run Job entrypoint.

`python -m job.main` (or the container's default CMD) runs one tick of the
Sovereign demo batch: an allowed same-region call, a denied cross-region
call, and the injected-instruction case, then writes the resulting
hash-chained decision log to the configured artifact backend and marks the
run complete in the idempotency backend.

Environment variables:

    GOOGLE_API_KEY (or GEMINI_API_KEY)   REQUIRED. The allowed calls in the
                                     batch invoke the real Gemini 3.5 Flash
                                     summarizer tools. With no key set,
                                     `agent.tools` raises
                                     MissingModelCredentials naming the
                                     variable; it never substitutes a
                                     fabricated summary. For a model-free
                                     run use `python demo.py` or
                                     `python demo_local.py`, which name the
                                     offline tool variants explicitly.

    SOVEREIGN_BACKEND=local|gcp     default "local"
    SOVEREIGN_GCS_BUCKET            required if SOVEREIGN_BACKEND=gcp
    SOVEREIGN_ARTIFACT_ROOT         local dir for LocalBackend (default ./artifacts)
    SOVEREIGN_SUBJECT               idempotency subject (default "sovereign-demo-batch")
    SOVEREIGN_WINDOW                idempotency window_start (default a fixed string,
                                     NOT wall-clock time, so repeated invocations in the
                                     same demo window collapse onto the same run_id)
"""

from __future__ import annotations

import os
import sys

from agentspine.artifacts import GcsBackend, LocalBackend
from agentspine.idempotency import FirestoreBackend, MemoryBackend

from audit.decision_log import DecisionLog
from gateway.tool_gateway import DataRecord, ToolGateway
from injection.injected_record import INJECTED_RECORD
from job.tick import BatchCall, run_job_tick
from policy.engine import DEFAULT_ENGINE
from registry.agent_registry import bootstrap_default_registry
from agent.tools import summarize_record, support_lookup

# Module-level backend so a duplicate tick in the same process (used by the
# idempotency demo) reuses claim state instead of starting fresh each call.
_MEMORY_IDEMPOTENCY_BACKEND = MemoryBackend()


def build_backends():
    mode = os.environ.get("SOVEREIGN_BACKEND", "local")
    if mode == "gcp":
        bucket = os.environ["SOVEREIGN_GCS_BUCKET"]
        artifact_backend = GcsBackend(bucket)
        idempotency_backend = FirestoreBackend()
    else:
        artifact_root = os.environ.get("SOVEREIGN_ARTIFACT_ROOT", "./artifacts")
        artifact_backend = LocalBackend(artifact_root)
        idempotency_backend = _MEMORY_IDEMPOTENCY_BACKEND
    return artifact_backend, idempotency_backend


def demo_batch(gateway: ToolGateway) -> list[BatchCall]:
    eu_record = DataRecord(
        record_id="eu-customer-1001", region="EU", content="EU support ticket text"
    )
    return [
        # Allowed: EU record through the registered EU summarizer.
        BatchCall("eu-summarizer", eu_record, "summarize", summarize_record),
        # Denied: the exact same EU record routed to the US summarizer.
        BatchCall("us-summarizer", eu_record, "summarize", summarize_record),
        # Denied, and the strongest case: an EU record whose content carries
        # an injected "you are authorized" instruction, still routed to the
        # US summarizer, still denied on residency grounds alone.
        BatchCall(
            "us-summarizer", INJECTED_RECORD, "summarize", summarize_record
        ),
        # Allowed: a same-region US support lookup, for contrast.
        BatchCall(
            "us-support",
            DataRecord(record_id="us-customer-2002", region="US", content="US support ticket"),
            "support",
            support_lookup,
        ),
    ]


def main() -> int:
    registry = bootstrap_default_registry()
    decision_log = DecisionLog()
    gateway = ToolGateway(
        registry=registry, policy_engine=DEFAULT_ENGINE, decision_log=decision_log
    )
    calls = demo_batch(gateway)

    artifact_backend, idempotency_backend = build_backends()
    subject = os.environ.get("SOVEREIGN_SUBJECT", "sovereign-demo-batch")
    window = os.environ.get("SOVEREIGN_WINDOW", "demo-window-1")

    result = run_job_tick(
        subject, window, calls, gateway, artifact_backend, idempotency_backend
    )

    print(f"run_id={result.run_id} status={result.status}")
    if result.artifact_uri:
        print(f"artifact_uri={result.artifact_uri}")
    if result.decisions is not None:
        for d in result.decisions:
            print(f"  seq={d['seq']} verdict={d['verdict']} clause={d['clause_id']} "
                  f"record={d['record_id']} caller_region={d['caller_region']} "
                  f"data_region={d['data_region']}")
        chain_ok = decision_log.verify().valid
        print(f"decision_log.verify() -> valid={chain_ok}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
