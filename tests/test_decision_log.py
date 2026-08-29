"""Tests for the hash-chained decision log, including the required
tamper-a-middle-entry -> verification FAILS scenario."""

from __future__ import annotations

import json

import pytest

from audit.decision_log import DecisionLog, GENESIS_HASH, TamperError
from policy.engine import DEFAULT_ENGINE, ToolCallRequest


def _decisions():
    reqs = [
        ToolCallRequest(caller_region="EU", data_region="EU", purpose="summarize",
                         caller_id="eu-summarizer", record_id="eu-1"),
        ToolCallRequest(caller_region="US", data_region="EU", purpose="summarize",
                         caller_id="us-summarizer", record_id="eu-2"),
        ToolCallRequest(caller_region="US", data_region="US", purpose="support",
                         caller_id="us-support", record_id="us-1"),
    ]
    return [DEFAULT_ENGINE.evaluate(r) for r in reqs]


def test_first_entry_chains_to_genesis():
    log = DecisionLog()
    entry = log.append_decision(_decisions()[0])
    assert entry.prev_hash == GENESIS_HASH
    assert entry.entry_hash == entry.compute_hash()


def test_chain_links_sequential_entries():
    log = DecisionLog()
    decisions = _decisions()
    entries = [log.append_decision(d) for d in decisions]
    assert entries[1].prev_hash == entries[0].entry_hash
    assert entries[2].prev_hash == entries[1].entry_hash


def test_clean_chain_verifies():
    log = DecisionLog()
    for d in _decisions():
        log.append_decision(d)
    result = log.verify()
    assert result.valid is True
    assert result.broken_at_seq is None


def test_tampering_a_middle_entry_fails_verification():
    log = DecisionLog()
    for d in _decisions():
        log.append_decision(d)

    # Mutate entry 1's verdict from deny to allow (the exact attack this
    # log exists to catch: quietly flipping a denial after the fact).
    tampered = log.entries[1]
    assert tampered.verdict == "deny"
    mutated = tampered.__class__(
        **{**tampered.to_dict(), "verdict": "allow"}
    )
    log.entries[1] = mutated

    result = log.verify()
    assert result.valid is False
    assert result.broken_at_seq == 1

    with pytest.raises(TamperError):
        log.verify(raise_on_failure=True)


def test_tampering_is_detected_even_if_entry_hash_field_also_edited():
    """A naive tamperer might also patch entry_hash to match the mutated
    content. That should still be caught, because entry 1's new hash no
    longer matches what entry 2's prev_hash committed to."""
    log = DecisionLog()
    for d in _decisions():
        log.append_decision(d)

    tampered = log.entries[1]
    mutated = tampered.__class__(**{**tampered.to_dict(), "verdict": "allow"})
    mutated = mutated.__class__(
        **{**mutated.to_dict(), "entry_hash": mutated.compute_hash()}
    )
    log.entries[1] = mutated

    result = log.verify()
    assert result.valid is False
    # entry 1's own hash now matches its (tampered) content, so the
    # break surfaces at entry 2, whose prev_hash still points at the
    # ORIGINAL (pre-tamper) hash of entry 1.
    assert result.broken_at_seq == 2


def test_persist_and_reload_roundtrip(tmp_path):
    path = tmp_path / "decisions" / "run-1.json"
    log = DecisionLog(path=path)
    for d in _decisions():
        log.append_decision(d)
    assert path.exists()

    reloaded = DecisionLog(path=path)
    assert len(reloaded.entries) == 3
    assert reloaded.verify().valid is True


def test_tampering_the_persisted_file_on_disk_is_detected(tmp_path):
    path = tmp_path / "decisions" / "run-2.json"
    log = DecisionLog(path=path)
    for d in _decisions():
        log.append_decision(d)

    raw = json.loads(path.read_text())
    raw[1]["verdict"] = "allow"  # flip a deny to allow directly on disk
    path.write_text(json.dumps(raw, indent=2))

    reloaded = DecisionLog(path=path)
    result = reloaded.verify()
    assert result.valid is False
    assert result.broken_at_seq == 1
