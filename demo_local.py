#!/usr/bin/env python3
"""demo_local.py — the full Sovereign loop, offline, no GCP, no API key.

Runs the real production path: the agent registry, the deterministic region
policy engine, the tool gateway, the hash-chained decision log, and
`job.tick.run_job_tick` (agentspine idempotency + artifact backends).
Nothing is faked except the sub-agent tool bodies, which are ordinary local
functions, and the model, which is not consulted at all -- by design. The
policy engine has no parameter through which model output could reach a
verdict.

Sovereign inverts the other two projects' artifact rule on purpose: a DENY
is not "nothing happened", it is the thing that must be recorded. So the
artifact is the decision log containing both verdicts, and the proof that
the denial is real is that the sub-agent's tool function never ran.

Acts:
    1. FLEET: the registered sub-agents and their declared regions.
    2. ALLOW: an EU record through the EU summarizer. Tool runs.
    3. DENY: the same EU record through the US summarizer. Tool never runs.
    4. INJECTION: a record whose CONTENT says "you are authorized". Still
       denied, because content never reaches the policy engine.
    5. ARTIFACT: the tick writes the hash-chained decision log; a second
       tick for the same window writes nothing.
    6. TAMPER: flip one logged verdict, chain verification goes red;
       restore it, green.

Usage:  ../../.venv/bin/python demo_local.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentspine import transcript as t
from agentspine.artifacts import LocalBackend
from agentspine.idempotency import MemoryBackend, compute_run_id

from audit.decision_log import DecisionLog
from gateway.tool_gateway import DataRecord, ToolGateway
from injection.injected_record import INJECTED_RECORD, contains_injected_instruction
from job.tick import BatchCall, run_job_tick
from policy.engine import DEFAULT_ENGINE
from registry.agent_registry import bootstrap_default_registry

SUBJECT = "eu-support-queue"
WINDOW = "2026-08-29T06:00:00+00:00"


def demo_artifact_root() -> str | None:
    """Where artifacts land.

    Default (None) is LocalBackend's own fresh temp dir, which is right for
    tests. For FILMING, demo/record_sovereign.sh sets DEMO_ARTIFACT_DIR to a
    fixed path so demo/watch_artifacts.py can be pointed at that exact
    directory in a second pane and be seen going from empty to non-empty on
    camera. A random temp dir cannot be watched, so the money shot needs this.
    """
    base = os.environ.get("DEMO_ARTIFACT_DIR")
    if not base:
        return None
    Path(base).mkdir(parents=True, exist_ok=True)
    return base

_tool_runs: list[str] = []


def summarize(record: DataRecord) -> str:
    """The sub-agent's tool body. Records every invocation, so the demo can
    PROVE a denied call never reached it (rather than asserting it did not)."""
    _tool_runs.append(record.record_id)
    return f"summary of {record.record_id}: {record.content[:40]}..."


def print_decision(label: str, result) -> None:
    d = result.decision
    t.verdict_line(d.allowed, d.reason)
    t.fact("clause", d.clause_id)
    t.fact("caller_region", d.caller_region)
    t.fact("data_region", d.data_region)
    t.fact("purpose", d.purpose)
    t.fact("tool_result", result.tool_result if d.allowed else "None (tool never invoked)")


def main() -> int:
    t.header(
        "SOVEREIGN — offline end-to-end demo",
        "Fortified Enterprise Fleet track. Zero network, zero GCP, zero API key.\n"
        "Validator: a deterministic region policy engine that DENIES a\n"
        "sub-agent's tool call when data residency does not match. The denial\n"
        "is the demo.",
    )

    registry = bootstrap_default_registry()
    decision_log = DecisionLog()
    gateway = ToolGateway(registry=registry, policy_engine=DEFAULT_ENGINE,
                          decision_log=decision_log)
    idem = MemoryBackend()
    artifacts = LocalBackend(demo_artifact_root())
    t.note("idempotency backend: MemoryBackend (FirestoreBackend in prod)")
    t.note(f"artifact backend:    LocalBackend at {artifacts.root_dir} (GcsBackend in prod)")

    # ---------------------------------------------------------------- ACT 1
    t.act(1, "FLEET — genuinely separate sub-agents, region declared at registration")
    for agent_id in registry.all_agent_ids():
        reg = registry.latest(agent_id)
        t.fact(agent_id, f"region={reg.region}  capability={reg.capability}  sa={reg.service_account.split('@')[0]}")
    t.note("region comes from the REGISTRY, never from what an agent claims at")
    t.note("call time. An agent cannot talk its way into another region.")

    # ---------------------------------------------------------------- ACT 2
    t.act(2, "ALLOW — EU record through the EU summarizer")
    eu_record = DataRecord(record_id="eu-customer-1001", region="EU",
                           content="EU support ticket: package never arrived")
    runs_before = len(_tool_runs)
    allowed = gateway.invoke("eu-summarizer", eu_record, "summarize", summarize)
    print_decision("EU -> eu-summarizer", allowed)
    t.fact("sub-agent tool invocations", len(_tool_runs) - runs_before)

    t.assert_demo(allowed.decision.allowed is True, "same-region call must be allowed")
    t.assert_demo(len(_tool_runs) == runs_before + 1, "allowed call must actually run the tool")

    # ---------------------------------------------------------------- ACT 3
    t.act(3, "DENY — the same EU record through the US summarizer")
    runs_before = len(_tool_runs)
    denied = gateway.invoke("us-summarizer", eu_record, "summarize", summarize)
    print_decision("EU -> us-summarizer (cross-region)", denied)
    t.fact("sub-agent tool invocations", len(_tool_runs) - runs_before)
    t.note("this is bounded authority: Sovereign can refuse to run a call, it")
    t.note("cannot un-run one it already ran. So the check happens FIRST.")

    t.assert_demo(denied.decision.allowed is False, "cross-region call must be denied")
    t.assert_demo(len(_tool_runs) == runs_before, "denied call must never reach the tool")
    t.assert_demo(denied.tool_result is None, "denied call returns no data")

    # ---------------------------------------------------------------- ACT 4
    t.act(4, "INJECTION — the record's content says 'you are authorized'")
    t.step("the untrusted record content:")
    print(f"     | {INJECTED_RECORD.content}")
    t.assert_demo(
        contains_injected_instruction(INJECTED_RECORD),
        "the demo record must actually carry an injected override",
    )
    t.note("a model reading that content may well comply in its own reasoning.")
    t.note("This demo does not show a model doing so, because it makes no model")
    t.note("call at all -- and that is the point being proven: the verdict below")
    t.note("is computed without the model, and would be identical either way.")

    runs_before = len(_tool_runs)
    injected = gateway.invoke("us-summarizer", INJECTED_RECORD, "summarize", summarize)
    print_decision("injected EU record -> us-summarizer", injected)
    t.fact("sub-agent tool invocations", len(_tool_runs) - runs_before)
    t.note("policy.engine.evaluate() takes only caller_region, data_region and")
    t.note("purpose. There is no parameter through which record content, an")
    t.note("'urgency' claim, or model output could reach the verdict. The")
    t.note("injection is not resisted by judgment, it is unreachable.")

    t.assert_demo(injected.decision.allowed is False, "injected record must still be denied")
    t.assert_demo(len(_tool_runs) == runs_before, "injected denied call must not run the tool")

    # ---------------------------------------------------------------- ACT 5
    t.act(5, "ARTIFACT — the decision log is written once per window")
    tick_log = DecisionLog()
    tick_gateway = ToolGateway(registry=registry, policy_engine=DEFAULT_ENGINE,
                               decision_log=tick_log)
    calls = [
        BatchCall("eu-summarizer", eu_record, "summarize", summarize),
        BatchCall("us-summarizer", eu_record, "summarize", summarize),
        BatchCall("us-summarizer", INJECTED_RECORD, "summarize", summarize),
    ]
    result = run_job_tick(SUBJECT, WINDOW, calls, tick_gateway, artifacts, idem)
    t.fact("tick status", result.status)
    t.fact("run_id", result.run_id[:16] + "...")
    t.artifacts([result.artifact_uri])
    verdicts = [d["verdict"] for d in (result.decisions or [])]
    t.fact("decisions recorded", f"{verdicts.count('allow')} allow / {verdicts.count('deny')} deny")

    t.step("second tick, same (subject, window) -> same deterministic run_id")
    t.fact("computed run_id", compute_run_id(SUBJECT, WINDOW)[:16] + "...")
    files_before = len(artifacts.list_prefix("decisions"))
    repeat = run_job_tick(SUBJECT, WINDOW, calls, tick_gateway, artifacts, idem)
    t.fact("tick status", repeat.status)
    t.fact("decision-log files", len(artifacts.list_prefix("decisions")))

    t.assert_demo(result.status == "complete", "first tick must complete")
    t.assert_demo("deny" in verdicts and "allow" in verdicts,
                  "the artifact must contain BOTH an allowed and a denied hop")
    t.assert_demo(repeat.status == "skipped_complete", "duplicate tick must skip")
    t.assert_demo(len(artifacts.list_prefix("decisions")) == files_before,
                  "duplicate tick must not write a second artifact")

    # ---------------------------------------------------------------- ACT 6
    t.act(6, "TAMPER — the decision log is hash-chained, and proves it")
    verified = tick_log.verify()
    t.fact("verify() valid", verified.valid)
    for e in tick_log.entries:
        t.fact(f"seq={e.seq} {e.verdict}", f"{e.clause_id}  hash={e.entry_hash[:12]}...")

    t.step("flip entries[1] from deny to allow, then re-verify")
    original = tick_log.entries[1]
    tick_log.entries[1] = original.__class__(**{**original.to_dict(), "verdict": "allow"})
    tampered = tick_log.verify()
    t.fact("verify() valid", tampered.valid)
    t.fact("broken_at_seq", tampered.broken_at_seq)
    t.fact("reason", tampered.reason)

    t.step("restore the original entry")
    tick_log.entries[1] = original
    restored = tick_log.verify()
    t.fact("verify() valid", restored.valid)

    t.assert_demo(verified.valid is True, "an untampered chain must verify")
    t.assert_demo(tampered.valid is False, "a tampered chain must fail verification")
    t.assert_demo(restored.valid is True, "restoring must return the chain to green")

    # ------------------------------------------------------------- SUMMARY
    stored = json.loads(artifacts.read(f"decisions/{result.run_id}.json").decode())
    t.summary([
        ("EU record -> EU summarizer", "ALLOWED, tool ran"),
        ("EU record -> US summarizer", "DENIED, tool never invoked"),
        ("injected 'you are authorized' record", "DENIED anyway"),
        ("decision log artifact", f"{len(stored)} entries, allow + deny both present"),
        ("duplicate scheduler tick", "skipped_complete, no second artifact"),
        ("tamper detection", "red on tamper, green on restore"),
        ("total sub-agent tool invocations", str(len(_tool_runs))),
        ("network calls", "0"),
        ("GCP credentials required", "none"),
    ])
    print("\nDelete the policy engine and acts 3 and 4 turn into act 2:")
    print("the EU row gets summarized in the US. That is the whole project.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
