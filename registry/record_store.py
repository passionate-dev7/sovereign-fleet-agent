"""Trusted record store: where a record's residency label actually comes from.

THE POINT OF THIS MODULE. Sovereign's whole claim is "the policy engine
decides from data the model cannot influence." That claim was false in the
ADK fleet path until this module existed: `make_region_tool`'s tool
signature took `record_region` and `record_content` as arguments supplied
BY THE MODEL, so a model that mislabelled an EU row as "US" produced
caller_region == data_region and the gateway allowed EU content into the US
summarizer. Found by the final hostile-judge audit; see
tests/test_fleet.py::test_model_cannot_relabel_a_records_region.

The fix is structural rather than defensive: the model is given ONE
argument, an opaque `record_id`. Region and content are resolved here, from
the storage layer, and there is no parameter through which a model could
assert otherwise. An unknown id is a hard error, never a synthesised
record, because inventing a record would recreate the same hole.
"""

from __future__ import annotations

from typing import Dict, Iterable

from gateway.tool_gateway import DataRecord


class UnknownRecordError(KeyError):
    """Raised when a caller asks for a record id the store does not hold.

    Deliberately fatal. Returning a placeholder record would let a model
    conjure a row with a region of its choosing, which is precisely the
    vulnerability this store exists to close.
    """


class RecordStore:
    """Read-only, id-addressed view of records and their residency labels.

    Stands in for the real datastore (Firestore/BigQuery row + its
    residency column). The security property is not "in-memory vs cloud",
    it is that `region` is read from storage and is not an input any
    caller, model or otherwise, can supply.
    """

    def __init__(self, records: Iterable[DataRecord] = ()):
        self._records: Dict[str, DataRecord] = {r.record_id: r for r in records}

    def add(self, record: DataRecord) -> None:
        self._records[record.record_id] = record

    def get(self, record_id: str) -> DataRecord:
        try:
            return self._records[record_id]
        except KeyError:
            raise UnknownRecordError(
                f"record_id {record_id!r} is not in the record store; "
                "refusing to synthesise a record (its region would be "
                "unverifiable)"
            ) from None

    def ids(self) -> list[str]:
        return sorted(self._records)

    def __len__(self) -> int:
        return len(self._records)
