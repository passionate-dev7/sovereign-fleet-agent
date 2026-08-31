"""Idempotency: deterministic run_id + transactional claim.

The "two laptops" problem (Google's own framing): a resumable agent tick must
not perform its side effect twice. We derive a deterministic run_id from
(subject, window_start) -- never from a timestamp or uuid4 -- so that any
number of ticks for the same subject+window collapse onto the same claim.

Backends:
    MemoryBackend    -- in-process dict, for offline tests.
    FirestoreBackend -- real Firestore, using a transaction to make the
                        claim atomic (read-modify-write with no other writer
                        able to interleave).

Status lifecycle (see DESIGN.md `runs/{run_id}` schema):
    claimed -> rejected   (validator vetoed; terminal, no artifact)
    claimed -> complete   (artifact written; terminal)
A claim() call is idempotent: it returns True for a fresh claim AND for a
retry of a run that is claimed-but-not-yet-complete (so a crash mid-run can
resume). It returns False only when the run is already `complete`, which is
what stops a duplicate artifact from ever being written.
"""

from __future__ import annotations

import abc
import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any, Optional


def compute_run_id(subject: str, window_start: str) -> str:
    """Deterministic run_id from (subject, window_start).

    Same inputs always produce the same id. No timestamp, no uuid4 -- that's
    the whole point: two ticks for the same subject+window must collide.
    """
    digest_input = f"{subject}|{window_start}".encode("utf-8")
    return hashlib.sha256(digest_input).hexdigest()


@dataclass
class RunRecord:
    run_id: str
    subject: str
    window_start: str
    window_end: Optional[str] = None
    status: str = "claimed"  # claimed | rejected | complete
    attempts: int = 0
    validator_verdict: Optional[dict] = None
    artifact_uri: Optional[str] = None
    extra: dict = field(default_factory=dict)


class IdempotencyBackend(abc.ABC):
    """Pluggable claim store. Implementations MUST make claim() atomic."""

    @abc.abstractmethod
    def claim(self, run_id: str, subject: str, window_start: str,
               window_end: Optional[str] = None) -> bool:
        """Atomically claim run_id.

        Returns True if the caller may (re)run the tick body: either this is
        a brand new claim, or the run exists but is not yet `complete`
        (crash-resume case). Returns False if the run is already `complete`
        -- the caller MUST NOT write another artifact.
        """

    @abc.abstractmethod
    def mark_rejected(self, run_id: str, reason: str, evidence: dict) -> None:
        """Record a validator veto. Terminal state, no artifact."""

    @abc.abstractmethod
    def mark_complete(self, run_id: str, artifact_uri: str,
                        validator_verdict: Optional[dict] = None) -> None:
        """Record successful completion with the artifact location."""

    @abc.abstractmethod
    def get(self, run_id: str) -> Optional[RunRecord]:
        """Fetch the current record, or None if never claimed."""


class MemoryBackend(IdempotencyBackend):
    """In-process backend for offline tests. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, RunRecord] = {}

    def claim(self, run_id: str, subject: str, window_start: str,
               window_end: Optional[str] = None) -> bool:
        with self._lock:
            existing = self._runs.get(run_id)
            if existing is not None and existing.status == "complete":
                return False
            if existing is None:
                self._runs[run_id] = RunRecord(
                    run_id=run_id,
                    subject=subject,
                    window_start=window_start,
                    window_end=window_end,
                    status="claimed",
                    attempts=1,
                )
            else:
                existing.attempts += 1
                existing.status = "claimed"
            return True

    def mark_rejected(self, run_id: str, reason: str, evidence: dict) -> None:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise KeyError(f"run {run_id} was never claimed")
            record.status = "rejected"
            record.validator_verdict = {
                "passed": False,
                "reason": reason,
                "evidence": evidence,
            }

    def mark_complete(self, run_id: str, artifact_uri: str,
                        validator_verdict: Optional[dict] = None) -> None:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise KeyError(f"run {run_id} was never claimed")
            record.status = "complete"
            record.artifact_uri = artifact_uri
            if validator_verdict is not None:
                record.validator_verdict = validator_verdict

    def get(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            return self._runs.get(run_id)


class FirestoreBackend(IdempotencyBackend):
    """Firestore-backed claim store, matching DESIGN.md's `runs/{run_id}`.

    Uses a Firestore transaction so the read-check-write of the claim is
    atomic across concurrent Cloud Run Job instances. Requires
    google-cloud-firestore; import is lazy so offline tests never need it.
    """

    def __init__(self, client: Any = None, collection: str = "runs") -> None:
        if client is None:
            from google.cloud import firestore  # local import, optional dep

            client = firestore.Client()
        self._client = client
        self._collection = collection

    def _doc_ref(self, run_id: str):
        return self._client.collection(self._collection).document(run_id)

    def claim(self, run_id: str, subject: str, window_start: str,
               window_end: Optional[str] = None) -> bool:
        from google.cloud import firestore

        doc_ref = self._doc_ref(run_id)

        @firestore.transactional
        def _txn(transaction: "firestore.Transaction") -> bool:
            snapshot = doc_ref.get(transaction=transaction)
            if snapshot.exists:
                data = snapshot.to_dict() or {}
                if data.get("status") == "complete":
                    return False
                transaction.update(doc_ref, {
                    "status": "claimed",
                    "attempts": firestore.Increment(1),
                })
                return True
            transaction.set(doc_ref, {
                "run_id": run_id,
                "subject": subject,
                "window_start": window_start,
                "window_end": window_end,
                "status": "claimed",
                "attempts": 1,
                "validator_verdict": None,
                "artifact_uri": None,
            })
            return True

        transaction = self._client.transaction()
        return _txn(transaction)

    def mark_rejected(self, run_id: str, reason: str, evidence: dict) -> None:
        self._doc_ref(run_id).update({
            "status": "rejected",
            "validator_verdict": {
                "passed": False,
                "reason": reason,
                "evidence": evidence,
            },
        })

    def mark_complete(self, run_id: str, artifact_uri: str,
                        validator_verdict: Optional[dict] = None) -> None:
        update = {"status": "complete", "artifact_uri": artifact_uri}
        if validator_verdict is not None:
            update["validator_verdict"] = validator_verdict
        self._doc_ref(run_id).update(update)

    def get(self, run_id: str) -> Optional[RunRecord]:
        snapshot = self._doc_ref(run_id).get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict() or {}
        return RunRecord(
            run_id=run_id,
            subject=data.get("subject", ""),
            window_start=data.get("window_start", ""),
            window_end=data.get("window_end"),
            status=data.get("status", "claimed"),
            attempts=data.get("attempts", 0),
            validator_verdict=data.get("validator_verdict"),
            artifact_uri=data.get("artifact_uri"),
        )
