"""Registers a real Cloud Trace exporter for the shared `agentspine.tracing`
span helper, when running in GCP -- otherwise leaves it exactly as it was
(no-op, per `agentspine/tracing.py`'s own docstring).

Why this lives here and not in `agentspine/tracing.py`: that module is
vendored byte-identical into all three submissions
(`tests/test_no_vendored_spine.py` enforces this) and intentionally stays a
thin, dependency-light, no-op-by-default helper for all three. Sovereign is
the one project whose demo narrates a Trace panel
, so the GCP-specific exporter wiring -- and the extra
`opentelemetry-exporter-gcp-trace` dependency it requires -- is additive and
local to this project's `job/`, not a change to the shared spine.

`agentspine.tracing.span()` calls `opentelemetry.trace.get_tracer(...)`,
which resolves against whatever global `TracerProvider` is registered at
call time (see `opentelemetry.trace.ProxyTracer`). So calling
`configure_cloud_trace()` once, early in `job/main.py`, before any span is
opened, is enough to make every existing `tracer.start_as_current_span(...)`
call in `gateway/tool_gateway.py` and every `tracing.span(...)` call in
`job/tick.py` export to Cloud Trace, with no change to either of those
files.

**Not exercised against real Cloud Trace in this build environment**: this
module was written and unit-tested (`tests/test_tracing_setup.py`) with a
fake in-memory span exporter and by asserting the real `CloudTraceSpanExporter`
is constructed when explicitly forced on, but no span was actually sent to
a live Cloud Trace project from this environment -- there is no GCP project
with billing/Trace API enabled available here to verify against.
"""

from __future__ import annotations

import os


def _gcp_mode_requested() -> bool:
    """Same trigger `infra/deploy_sovereign.sh` uses for the real backend:
    `SOVEREIGN_BACKEND=gcp` is what the deployed Cloud Run Job always sets.
    `SOVEREIGN_TRACE_EXPORT=1` is an explicit override for anyone who wants
    Cloud Trace wired up without the full GCP artifact/idempotency backends
    (e.g. testing the exporter in isolation against a real project)."""
    if os.environ.get("SOVEREIGN_TRACE_EXPORT", "").strip().lower() in ("1", "true"):
        return True
    return os.environ.get("SOVEREIGN_BACKEND", "local") == "gcp"


def configure_cloud_trace(*, force: bool = False) -> bool:
    """Register a `TracerProvider` backed by `CloudTraceSpanExporter` as the
    global OTel tracer provider, IF running in GCP mode (or `force=True`).

    Returns True if a Cloud Trace exporter was registered, False if this
    was a deliberate no-op (not in GCP mode, dependency missing, or a
    tracer provider was already registered by someone else -- OTel's
    `set_tracer_provider` is set-once and logs a warning rather than
    raising on a second call, so this function checks first rather than
    relying on that to stay silent).

    Never raises. A missing/broken exporter must never take down the job
    that is trying to prove a policy denial; at worst, tracing stays a
    no-op, exactly as it was before this module existed.
    """
    if not force and not _gcp_mode_requested():
        return False

    from opentelemetry import trace as otel_trace

    if not isinstance(otel_trace.get_tracer_provider(), otel_trace.ProxyTracerProvider):
        # Something already installed a real provider (e.g. a test, or a
        # future caller). Do not fight it or double-register an exporter.
        return False

    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
    except ImportError:
        # opentelemetry-exporter-gcp-trace not installed. Stay a no-op
        # rather than crash the job -- this is the exact "clean local/
        # offline run" property the offline test suite
        # depend on.
        return False

    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT") or None
    try:
        exporter = CloudTraceSpanExporter(project_id=project_id)
    except Exception:
        # No ADC / no reachable Cloud Trace API / wrong project: fail
        # closed on tracing specifically, not on the job. The job's
        # actual product (the policy decision + hash-chained log) does
        # not depend on this succeeding.
        return False

    resource = Resource.create({"service.name": "sovereign", "service.namespace": "agentspine"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    otel_trace.set_tracer_provider(provider)
    return True
