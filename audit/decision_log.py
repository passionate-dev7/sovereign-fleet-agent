"""Hash-chained decision log.

Every policy decision (allow or deny) is appended as an entry that commits
to the previous entry's hash. A verifier walks the chain and recomputes
each hash; any mutation to any entry (including entries in the middle of
the chain) breaks every hash after it, so tampering is detectable without
needing a separate signature key or trusted third party.

Entry fields: who asked (caller_id), what data (record_id), which policy
clause fired (clause_id), the verdict (allow/deny), a timestamp, and
prev_hash. The entry's own hash commits to all of the above.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from policy.engine import PolicyDecision

GENESIS_HASH = "0" * 64


@dataclass(frozen=True)
class DecisionLogEntry:
    seq: int
    ts: float
    caller_id: str
    record_id: str
    caller_region: str
    data_region: str
    purpose: str
    clause_id: str
    verdict: str  # "allow" | "deny"
    reason: str
    prev_hash: str
    entry_hash: str = field(default="")

    def canonical_payload(self) -> dict:
        """Fields that participate in the hash, in a fixed, stable shape.
        entry_hash is deliberately excluded (it is the output, not input)."""
        return {
            "seq": self.seq,
            "ts": self.ts,
            "caller_id": self.caller_id,
            "record_id": self.record_id,
            "caller_region": self.caller_region,
            "data_region": self.data_region,
            "purpose": self.purpose,
            "clause_id": self.clause_id,
            "verdict": self.verdict,
            "reason": self.reason,
            "prev_hash": self.prev_hash,
        }

    def compute_hash(self) -> str:
        payload = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "DecisionLogEntry":
        return cls(**d)


class TamperError(Exception):
    """Raised by verify() (in raise mode) when the chain fails integrity check."""


class DecisionLog:
    """Append-only, hash-chained log of policy decisions.

    In-memory + optional JSON-file persistence (stands in for the GCS
    object `decisions/<run_id>.json` in the real deploy: same format,
    same verifier, different backing store).
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.entries: list[DecisionLogEntry] = []
        if path is not None and path.exists():
            self._load()

    @property
    def last_hash(self) -> str:
        return self.entries[-1].entry_hash if self.entries else GENESIS_HASH

    def append_decision(
        self,
        decision: PolicyDecision,
        ts: Optional[float] = None,
    ) -> DecisionLogEntry:
        entry_unhashed = DecisionLogEntry(
            seq=len(self.entries),
            ts=ts if ts is not None else time.time(),
            caller_id=decision.caller_id,
            record_id=decision.record_id,
            caller_region=decision.caller_region,
            data_region=decision.data_region,
            purpose=decision.purpose,
            clause_id=decision.clause_id,
            verdict="allow" if decision.allowed else "deny",
            reason=decision.reason,
            prev_hash=self.last_hash,
        )
        entry = DecisionLogEntry(
            **{**entry_unhashed.to_dict(), "entry_hash": entry_unhashed.compute_hash()}
        )
        self.entries.append(entry)
        if self.path is not None:
            self._save()
        return entry

    def verify(self, raise_on_failure: bool = False) -> "VerificationResult":
        """Walk the chain, recompute every hash, and check linkage.

        Returns a VerificationResult describing the first broken entry (if
        any) and whether the chain is intact end to end.
        """
        expected_prev = GENESIS_HASH
        for entry in self.entries:
            recomputed = entry.compute_hash()
            if recomputed != entry.entry_hash:
                result = VerificationResult(
                    valid=False,
                    broken_at_seq=entry.seq,
                    reason=(
                        f"entry {entry.seq} content hash mismatch: "
                        f"stored={entry.entry_hash[:12]} "
                        f"recomputed={recomputed[:12]} "
                        "(entry content was altered after being written)"
                    ),
                )
                if raise_on_failure:
                    raise TamperError(result.reason)
                return result
            if entry.prev_hash != expected_prev:
                result = VerificationResult(
                    valid=False,
                    broken_at_seq=entry.seq,
                    reason=(
                        f"entry {entry.seq} prev_hash does not match prior "
                        "entry's hash (chain link broken)"
                    ),
                )
                if raise_on_failure:
                    raise TamperError(result.reason)
                return result
            expected_prev = entry.entry_hash
        return VerificationResult(valid=True, broken_at_seq=None, reason="chain intact")

    def _save(self) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([e.to_dict() for e in self.entries], indent=2)
        )

    def _load(self) -> None:
        assert self.path is not None
        data = json.loads(self.path.read_text())
        self.entries = [DecisionLogEntry.from_dict(d) for d in data]


@dataclass(frozen=True)
class VerificationResult:
    valid: bool
    broken_at_seq: Optional[int]
    reason: str
