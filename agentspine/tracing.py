"""Thin OpenTelemetry span helper that no-ops cleanly with no exporter.

Sovereign's demo needs an OTel trace showing an allowed hop and a denied
hop. The other two projects just want spans around claim/validate/artifact
without caring whether an exporter is configured. This module gives one
`span()` context manager that works identically whether or not OTel is
installed/configured, so `job.py` can unconditionally wrap its steps.
"""

from __future__ import annotations

import contextlib
from typing import Any, Iterator, Optional

try:
    from opentelemetry import trace as _otel_trace

    _TRACER = _otel_trace.get_tracer("agentspine")
    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the dep
    _TRACER = None
    _OTEL_AVAILABLE = False


@contextlib.contextmanager
def span(name: str, attributes: Optional[dict[str, Any]] = None) -> Iterator[None]:
    """Open a span named `name`. No-ops if OpenTelemetry isn't installed or
    has no exporter configured -- the SDK's default is already a no-op
    tracer provider, so this is mostly a convenience wrapper that also
    tolerates OTel being absent entirely (e.g. minimal test environments).
    """
    if not _OTEL_AVAILABLE:
        yield
        return
    with _TRACER.start_as_current_span(name) as current_span:
        if attributes:
            for key, value in attributes.items():
                try:
                    current_span.set_attribute(key, value)
                except Exception:
                    # Never let a bad attribute value break the tick.
                    pass
        yield


def record_decision(name: str, allowed: bool, attributes: Optional[dict[str, Any]] = None) -> None:
    """Record a single allow/deny decision as its own span (Sovereign's
    "denied hop" in the trace). Safe to call with no exporter configured.
    """
    attrs = dict(attributes or {})
    attrs["decision.allowed"] = allowed
    with span(f"decision.{name}", attrs):
        pass
