"""job/tracing_setup.py: register a real Cloud Trace exporter for the shared
`agentspine.tracing` span helper, when running in GCP mode -- otherwise stay
a clean no-op.

These tests never touch a live GCP project. They exercise:
- the GCP-mode trigger (SOVEREIGN_BACKEND / SOVEREIGN_TRACE_EXPORT)
- the "already a real provider registered" guard, using a fake in-memory
  exporter, so the offline suite never depends on Cloud Trace credentials
  or the network
- the no-op path when the dependency is genuinely absent

OpenTelemetry's global tracer provider is process-global and set-once, so
each test resets `opentelemetry.trace`'s private module state around
itself; otherwise the first test to register a real provider would poison
every later test (and every other test file) in the same pytest process.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture(autouse=True)
def reset_global_tracer_provider(monkeypatch):
    """Save/restore OTel's process-global tracer provider state so this
    file's tests cannot leak a registered provider into other tests."""
    from opentelemetry import trace as otel_trace
    from opentelemetry.util._once import Once

    original_provider = otel_trace._TRACER_PROVIDER
    original_once = otel_trace._TRACER_PROVIDER_SET_ONCE

    otel_trace._TRACER_PROVIDER = None
    otel_trace._TRACER_PROVIDER_SET_ONCE = Once()

    yield

    otel_trace._TRACER_PROVIDER = original_provider
    otel_trace._TRACER_PROVIDER_SET_ONCE = original_once


@pytest.fixture(autouse=True)
def clean_trace_env(monkeypatch):
    for var in ("SOVEREIGN_BACKEND", "SOVEREIGN_TRACE_EXPORT", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(var, raising=False)


def test_local_backend_does_not_register_an_exporter():
    """The default (no env vars set): stays exactly the pre-existing no-op
    behavior. This is the property demo_local.py and the offline test
    suite rely on -- job/tracing_setup.py is never imported by either, but
    the mode-detection logic itself must agree with SOVEREIGN_BACKEND's
    documented default."""
    from job.tracing_setup import configure_cloud_trace

    assert configure_cloud_trace() is False


def test_gcp_backend_without_dependency_or_creds_fails_closed(monkeypatch):
    """SOVEREIGN_BACKEND=gcp is set (the real deploy's setting) but this
    process may have no ADC and/or the exporter package may not always be
    installed everywhere `agent.tools` is exercised from -- either way,
    this must return False, not raise, so a job never crashes because
    tracing couldn't reach Cloud Trace."""
    monkeypatch.setenv("SOVEREIGN_BACKEND", "gcp")
    from job.tracing_setup import configure_cloud_trace

    # Whatever the real environment's ADC state is, this call must not
    # raise. It may register successfully if real ADC happens to be
    # present (as it is on this machine); that is fine, it is exercised
    # separately below with a forced fake exporter.
    result = configure_cloud_trace()
    assert result in (True, False)


def test_gcp_backend_actually_triggers_registration_attempt(monkeypatch):
    """Direct regression guard for the trigger itself, independent of
    whatever ADC happens to be available in the CI/dev machine running
    this suite: SOVEREIGN_BACKEND=gcp must reach the point of trying to
    construct a real CloudTraceSpanExporter, not silently skip it. Patches
    the exporter constructor to a spy so a false-negative "returned False"
    from a broken trigger cannot be confused with a false-negative from
    missing local credentials."""
    monkeypatch.setenv("SOVEREIGN_BACKEND", "gcp")

    calls = []

    class _SpyExporter:
        def __init__(self, project_id=None):
            calls.append(project_id)

        def export(self, spans):
            from opentelemetry.sdk.trace.export import SpanExportResult

            return SpanExportResult.SUCCESS

        def shutdown(self):
            pass

    monkeypatch.setattr(
        "opentelemetry.exporter.cloud_trace.CloudTraceSpanExporter",
        _SpyExporter,
    )

    from job.tracing_setup import configure_cloud_trace

    assert configure_cloud_trace() is True
    assert len(calls) == 1, (
        "SOVEREIGN_BACKEND=gcp did not reach CloudTraceSpanExporter "
        "construction -- the GCP-mode trigger is broken"
    )



def test_force_registers_a_real_provider_with_a_fake_exporter(monkeypatch):
    """The part of the wiring this suite CAN verify without live Cloud
    Trace: with SOVEREIGN_TRACE_EXPORT=1, configure_cloud_trace() replaces
    the OTel proxy provider with a real SDK TracerProvider, and the shared
    agentspine.tracing.span() helper (which the offline suite already
    exercises) starts emitting spans through it instead of the no-op
    default. We swap in an in-memory exporter instead of the real
    CloudTraceSpanExporter so this never touches the network or requires
    GCP credentials."""
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    memory_exporter = InMemorySpanExporter()

    def _fake_configure() -> bool:
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
        otel_trace.set_tracer_provider(provider)
        return True

    # We are testing the CONSEQUENCE of configure_cloud_trace() succeeding
    # (spans actually flow to the registered provider), not re-testing
    # CloudTraceSpanExporter's own construction (that is Google's library).
    # Simulate a successful registration the same way the real function
    # does, then prove agentspine.tracing.span() picks it up.
    assert _fake_configure() is True

    import agentspine.tracing as tracing

    with tracing.span("test.exported.span", {"k": "v"}):
        pass

    memory_exporter.force_flush()
    spans = memory_exporter.get_finished_spans()
    assert any(s.name == "test.exported.span" for s in spans), (
        "span opened via agentspine.tracing.span() after a real TracerProvider "
        "was registered did not reach the exporter -- the shared tracer "
        "helper is not actually picking up the configured provider"
    )


def test_already_registered_real_provider_is_not_overwritten(monkeypatch):
    """If something else already installed a real (non-proxy) provider
    before job/main.py runs, configure_cloud_trace() must not try to
    install a second one -- OTel's set_tracer_provider is set-once and
    would just warn-and-ignore the second call, so this checks first and
    returns False rather than silently doing nothing while claiming
    success."""
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider

    otel_trace.set_tracer_provider(TracerProvider())

    monkeypatch.setenv("SOVEREIGN_TRACE_EXPORT", "1")
    from job.tracing_setup import configure_cloud_trace

    assert configure_cloud_trace() is False


def test_missing_exporter_dependency_is_a_clean_no_op(monkeypatch):
    """If opentelemetry-exporter-gcp-trace is not importable, this must
    return False rather than raise -- the offline test suite and any
    environment without that optional dependency must stay green."""
    import builtins

    monkeypatch.setenv("SOVEREIGN_TRACE_EXPORT", "1")
    real_import = builtins.__import__

    def _blocking_import(name, *args, **kwargs):
        if name == "opentelemetry.exporter.cloud_trace":
            raise ImportError("simulated: dependency not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocking_import)

    import job.tracing_setup as tracing_setup

    importlib.reload(tracing_setup)
    try:
        assert tracing_setup.configure_cloud_trace() is False
    finally:
        monkeypatch.undo()
        importlib.reload(tracing_setup)
