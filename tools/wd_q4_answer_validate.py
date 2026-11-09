"""Default-off validation of a lane answer against a caller-authorized record map.

The map has the existing lane query format: (sha256(event_ref),
sha256(record_id)) -> projected record dictionary containing stored_text.
The caller must independently authenticate the map, its provenance, binding
pins, source/public policy and freshness, and must prevent concurrent mutation.
Membership in the map proves none of those properties; ABSTAIN is not proof of
absence. This module performs no I/O, query, publication or lane authentication.
"""

import hmac
import json
import re

from tools import wd_q4_session_outer_synthetic as outer

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MAX_BYTES = 65536
_MAX_RECORDS = 4096  # same bound as shared_index_builder.MAX_RECORDS


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def _reject_constant(_):
    raise ValueError()


def validate_answer(*, answer_bytes, expected_lane, expected_binding, allowed_records, enabled=False):
    """Return a freshly parsed answer only after exact per-result record membership checks."""
    try:
        if enabled is not True or type(answer_bytes) is not bytes or not 3 <= len(answer_bytes) <= _MAX_BYTES:
            raise ValueError()
        if not answer_bytes.endswith(b"\n") or b"\n" in answer_bytes[:-1] or b"\r" in answer_bytes:
            raise ValueError()
        if type(allowed_records) is not dict or len(allowed_records) > _MAX_RECORDS:
            raise ValueError()
        for key, record in allowed_records.items():
            if (type(key) is not tuple or len(key) != 2
                    or any(type(part) is not str or not _HEX.fullmatch(part) for part in key)
                    or type(record) is not dict or type(record.get("stored_text")) is not str):
                raise ValueError()
        value = json.loads(answer_bytes[:-1].decode("utf-8", "strict"), object_pairs_hook=_unique,
                           parse_constant=_reject_constant)
        outer._validate_lane_answer(value, expected_lane, expected_binding)
        for item in value["results"]:
            key = (item["ref_sha256"], item["doc_id_sha256"])
            record = allowed_records.get(key)
            if (record is None or not hmac.compare_digest(item["stored_text"].encode("utf-8"),
                                                           record["stored_text"].encode("utf-8"))):
                raise ValueError()
            # Output the authorized map's text, not any separate child value.
            item["stored_text"] = record["stored_text"]
        return value
    except Exception:
        pass
    # The new exception keeps no input references in this function's frame.
    # Caller frames, nested frames and memory erasure are outside this guarantee.
    answer_bytes = expected_lane = expected_binding = allowed_records = enabled = None
    value = item = key = record = None
    raise ValueError("answer_validation_refused") from None
