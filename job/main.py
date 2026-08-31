"""Cloud Run Job entrypoint.

`python -m job.main` (or the container's default CMD) runs one tick of the
Sovereign demo batch: an allowed same-region call, a denied cross-region
call, and the injected-instruction case, then writes the resulting
hash-chained decision log to the configured artifact backend and marks the
run complete in the idempotency backend.

Environment variables:

    Model credentials, ONE of these two modes (see agent/tools.py):
      GOOGLE_GENAI_USE_VERTEXAI=TRUE + GOOGLE_CLOUD_PROJECT (+ normally
                                     GOOGLE_CLOUD_LOCATION)   Vertex AI mode,
                                     via Application Default Credentials --
                                     no API key. This is what the real
                                     deploy sets (../../infra/deploy_sovereign.sh's
                                     COMMON_ENV), matching Tabclose/Refill.
      GOOGLE_API_KEY (or GEMINI_API_KEY)   Gemini Developer API mode, for a
                                     local run with no GCP project handy.
    The allowed calls in the batch invoke the real Gemini 2.5 Flash
    summarizer tools. With neither mode configured, `agent.tools` raises
    MissingModelCredentials naming what to set; it never substitutes a
    fabricated summary. For a model-free run use `python demo.py` or
    `python demo_local.py`, which name the offline tool variants explicitly.

    SOVEREIGN_BACKEND=local|gcp     default "local"
    SOVEREIGN_GCS_BUCKET            required if SOVEREIGN_BACKEND=gcp
    SOVEREIGN_ARTIFACT_ROOT         local dir for LocalBackend (default ./artifacts)
    SOVEREIGN_SUBJECT               idempotency subject (default "sovereign-demo-batch")
    SOVEREIGN_WINDOW                idempotency window_start (default a fixed string,
                                     NOT wall-clock time, so repeated invocations in the
                                     same demo window collapse onto the same run_id)
    SOVEREIGN_TRACE_EXPORT=1        force-enable the Cloud Trace exporter even with
                                     SOVEREIGN_BACKEND=local (see job/tracing_setup.py).
                                     With SOVEREIGN_BACKEND=gcp (the real deploy's
                                     setting) the exporter is enabled automatically;
                                     if it cannot reach Cloud Trace (no ADC, API not
                                     enabled) it fails closed to the existing no-op
                                     behavior rather than crashing the job.
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
from job.tracing_setup import configure_cloud_trace
from policy.engine import DEFAULT_ENGINE
from registry.agent_registry import bootstrap_default_registry
from registry.record_store import RecordStore
from agent.fleet import RoutedCall, build_fleet
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


def build_record_store() -> RecordStore:
    """The trusted storage layer for this tick's records.

    Every record's `region` here is the residency label the policy engine
    evaluates. It is set at the storage layer and there is no code path by
    which a sub-agent, or the model driving one, can supply or override it.
    """
    return RecordStore(
        [
            DataRecord(
                record_id="eu-customer-1001",
                region="EU",
                content="EU support ticket text",
            ),
            INJECTED_RECORD,  # EU-resident, content carries a SYSTEM OVERRIDE
            DataRecord(
                record_id="us-customer-2002",
                region="US",
                content="US support ticket",
            ),
        ]
    )


def demo_batch(record_store: RecordStore) -> list[BatchCall]:
    """The batch this tick runs, addressed BY RECORD ID.

    Records are resolved from the trusted store rather than constructed
    inline, so the residency label the policy engine sees comes from
    storage on this path exactly as it does on the ADK fleet path. The
    routing choices below are deliberately wrong in two of four cases;
    the point of the run is that the gateway refuses them.
    """
    eu_record = record_store.get("eu-customer-1001")
    injected = record_store.get(INJECTED_RECORD.record_id)
    us_record = record_store.get("us-customer-2002")
    return [
        # Allowed: EU record through the registered EU summarizer.
        BatchCall("eu-summarizer", eu_record, "summarize", summarize_record),
        # Denied: the exact same EU record routed to the US summarizer.
        BatchCall("us-summarizer", eu_record, "summarize", summarize_record),
        # Denied, and the strongest case: an EU record whose content carries
        # an injected "you are authorized" instruction, still routed to the
        # US summarizer, still denied on residency grounds alone.
        BatchCall("us-summarizer", injected, "summarize", summarize_record),
        # Allowed: a same-region US support lookup, for contrast.
        BatchCall("us-support", us_record, "support", support_lookup),
    ]


def main() -> int:
    # Register the Cloud Trace exporter FIRST, before any span is opened
    # (gateway.tool_gateway's spans start as soon as the fleet/batch run).
    # No-ops when not in GCP mode; see job/tracing_setup.py.
    trace_exported = configure_cloud_trace()
    print(f"cloud_trace_exporter_registered={trace_exported}")

    registry = bootstrap_default_registry()
    decision_log = DecisionLog()
    gateway = ToolGateway(
        registry=registry, policy_engine=DEFAULT_ENGINE, decision_log=decision_log
    )
    record_store = build_record_store()

    # Assemble the real ADK fleet. Its sub-agents' tool closures are bound
    # to this same gateway and record store, so a model-driven call and the
    # batch below are enforced by the identical code path -- the fleet is
    # part of the product, not a test fixture.
    call_log: list[RoutedCall] = []
    orchestrator = build_fleet(registry, gateway, call_log, record_store)
    print(
        f"fleet: {orchestrator.name} -> "
        + ", ".join(sorted(a.name for a in orchestrator.sub_agents))
    )

    calls = demo_batch(record_store)

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
