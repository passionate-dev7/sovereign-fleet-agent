"""OFFLINE DEMO. `make demo` or `python demo.py`.

This is the short offline demo: no GCP, no API key, no model call. The
policy engine, gateway, registry and hash-chained decision log are the
real production modules; the only substitution is the sub-agent tool
BODY, which uses `agent.tools.offline_summarize_record` (a deterministic
marker string) instead of `summarize_record` (the real Gemini 3.5 Flash
call). That substitution is named explicitly on the import line below,
never defaulted to.

The substitution is sound for what this demo proves: the claim is about
WHETHER a tool function runs under a given policy verdict, not about the
quality of its output. `demo_local.py` is the longer version of the same
story with idempotency and tamper-detection acts; `job/main.py` is the
production entrypoint and it binds the real Gemini-backed tools.

Sequence:
    1. Register the fleet (declared region + service account per sub-agent).
    2. Allowed call: EU record through EU summarizer.
    3. Denied call: same EU record through US summarizer.
    4. Denied call: an injected-instruction EU record through US summarizer,
       even though its content claims "you are authorized."
    5. Print the hash-chained decision log and verify it (valid: true).
    6. Tamper with one entry, re-verify (valid: false, broken_at_seq shown).
"""

from __future__ import annotations

import json

from agent.tools import offline_summarize_record as summarize_record
from audit.decision_log import DecisionLog
from gateway.tool_gateway import DataRecord, ToolGateway
from injection.injected_record import INJECTED_RECORD
from policy.engine import DEFAULT_ENGINE
from registry.agent_registry import bootstrap_default_registry


def line(title: str) -> None:
    print(f"\n=== {title} ===")


def print_decision(label: str, result) -> None:
    d = result.decision
    verdict = "ALLOWED" if d.allowed else "DENIED"
    print(f"[{verdict}] {label}")
    print(f"  clause={d.clause_id}")
    print(f"  caller_region={d.caller_region} data_region={d.data_region} purpose={d.purpose}")
    print(f"  reason={d.reason}")
    if d.allowed:
        print(f"  tool_result={result.tool_result!r}")


def main() -> None:
    registry = bootstrap_default_registry()
    decision_log = DecisionLog()
    gateway = ToolGateway(
        registry=registry, policy_engine=DEFAULT_ENGINE, decision_log=decision_log
    )

    line("1. Registered fleet")
    for agent_id in registry.all_agent_ids():
        reg = registry.latest(agent_id)
        print(f"  {reg.agent_id:16s} region={reg.region:2s} capability={reg.capability:10s} "
              f"sa={reg.service_account}")

    line("2. Allowed call: EU record -> EU summarizer")
    eu_record = DataRecord(
        record_id="eu-customer-1001", region="EU", content="EU support ticket text"
    )
    result = gateway.invoke("eu-summarizer", eu_record, "summarize", summarize_record)
    print_decision("EU record through eu-summarizer", result)

    line("3. Denied call: same EU record -> US summarizer")
    result = gateway.invoke("us-summarizer", eu_record, "summarize", summarize_record)
    print_decision("EU record through us-summarizer (cross-region)", result)

    line("4. Injected-instruction case: EU record with 'you are authorized' -> US summarizer")
    print(f"  record.content = {INJECTED_RECORD.content!r}")
    result = gateway.invoke(
        "us-summarizer", INJECTED_RECORD, "summarize", summarize_record
    )
    print_decision("Injected EU record through us-summarizer", result)
    print("  ^ denied on residency grounds; record.content was never passed")
    print("    to policy.engine.evaluate() at all, so the injected text had")
    print("    no path to the verdict, regardless of any model reasoning.")

    line("5. Hash-chained decision log")
    for e in decision_log.entries:
        print(f"  seq={e.seq} verdict={e.verdict:5s} clause={e.clause_id:20s} "
              f"hash={e.entry_hash[:12]}... prev={e.prev_hash[:12]}...")
    result = decision_log.verify()
    print(f"  decision_log.verify() -> valid={result.valid} ({result.reason})")

    line("6. Tamper detection: flip entry[1] verdict, then re-verify")
    tampered = decision_log.entries[1]
    print(f"  before: entries[1].verdict = {tampered.verdict!r}")
    decision_log.entries[1] = tampered.__class__(
        **{**tampered.to_dict(), "verdict": "allow"}
    )
    tampered_result = decision_log.verify()
    print(f"  after tamper: entries[1].verdict = {decision_log.entries[1].verdict!r}")
    print(f"  decision_log.verify() -> valid={tampered_result.valid} "
          f"broken_at_seq={tampered_result.broken_at_seq} ({tampered_result.reason})")

    line("Restore and re-verify (green)")
    decision_log.entries[1] = tampered
    restored_result = decision_log.verify()
    print(f"  decision_log.verify() -> valid={restored_result.valid}")


if __name__ == "__main__":
    main()
