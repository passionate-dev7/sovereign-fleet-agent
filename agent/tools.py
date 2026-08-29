"""Tool functions exposed to sub-agents.

These are the functions the gateway calls AFTER a policy allow. They are
intentionally simple: Sovereign's job is to decide whether a call happens,
not to be an impressive summarizer. Bounded authority means the tool
itself only ever returns a derived summary string; it never writes the
row anywhere else, never calls out to a third service, and never expands
scope beyond the one record it was handed.
"""

from __future__ import annotations

from gateway.tool_gateway import DataRecord


def summarize_record(record: DataRecord) -> str:
    """Stand-in for what a real summarizer sub-agent tool would do. In the
    live deploy this calls Gemini 3.5 Flash with the record content; for
    the offline test suite and the policy demo, a deterministic string is
    enough because what we are proving is whether this function RUNS AT
    ALL, not the quality of its output."""
    return f"[summary of {record.record_id}] {record.content[:120]}"


def support_lookup(record: DataRecord) -> str:
    """Stand-in for a support sub-agent tool: looks up account context for
    a support reply. Same bounded-authority shape as summarize_record."""
    return f"[support context for {record.record_id}] {record.content[:120]}"
