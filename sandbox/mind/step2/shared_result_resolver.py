"""Resolve Q4 hash-only results against a caller-pinned projected metadata set.

This in-memory helper is not a source authenticator, freshness oracle, filesystem
reader, deployment boundary, or TOCTOU defense. The caller must independently
bind the exact memory, manifest, projection and query at a trusted boundary.
The JSON contract matches shared_index_builder v1 at 3bf2ff74; compatibility
with a later builder is unknown until independently verified.
"""

import hashlib
import hmac
import json
import re

from sandbox.mind.step2 import shared_projection as sp
from sandbox.mind.step2 import shared_capture_adapter as adapter

SCOPE = "wd.q4-shared-projected-events.v1"
PROJECTION_SCHEMA = "wd.bridge-mind-shared-projection.v6"
REF_KIND = "sha256_of_projected_metadata"
MAX_BYTES = 16 * 1024 * 1024
MAX_RESULTS = 100
HEX = re.compile(r"[0-9a-f]{64}\Z")


class ResolveRefused(ValueError):
    """A fixed refusal without caller input, private content or paths."""


def _invalid():
    raise ResolveRefused("resolution_refused") from None


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _parse(data):
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        _invalid()
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_object_pairs,
                          parse_constant=lambda _: _invalid())
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _invalid()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _strict_memory_policy(value, policy_sha256, roster_sha256):
    return (type(value) is dict
            and set(value) == {"authorship", "review_signal_excluded", "policy_sha256", "roster_sha256"}
            and value["authorship"] == sp.AUTHORSHIP_ROSTER
            and value["review_signal_excluded"] is True
            and type(value["policy_sha256"]) is str
            and type(value["roster_sha256"]) is str
            and HEX.fullmatch(value["policy_sha256"]) is not None
            and HEX.fullmatch(value["roster_sha256"]) is not None
            and hmac.compare_digest(value["policy_sha256"], policy_sha256)
            and hmac.compare_digest(value["roster_sha256"], roster_sha256))


def resolve_results(*, memory_manifest, expected_memory_sha256, projection_bytes, results, query_utc,
                    approved_tasks=None, policy_bytes=None, policy_sha256=None, roster_bytes=None,
                    roster_sha256=None):
    """Return approved stored text only for exact, time-visible projected Q4 hits.

    Both Q4 hash fields and explicit projected/noncanonical labels must match
    the same record. Duplicate projections and repeated results collapse to
    their first occurrence. The query time is caller-provided, not a trusted
    clock; the caller must bind it to the actual Q4 query. Source identity and
    projection recency are not established by a hash pin.
    """
    strict = any(value is not None for value in (policy_bytes, policy_sha256, roster_bytes, roster_sha256))
    roster = None
    if strict:
        try:
            tasks, roster = adapter.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
            if approved_tasks is not None and approved_tasks != tasks:
                _invalid()
            approved_tasks = tasks
        except (ValueError, TypeError, RecursionError, UnicodeError):
            _invalid()
    if (not isinstance(expected_memory_sha256, str) or HEX.fullmatch(expected_memory_sha256) is None
            or not isinstance(memory_manifest, bytes) or len(memory_manifest) > MAX_BYTES
            or not hmac.compare_digest(_sha(memory_manifest), expected_memory_sha256)):
        _invalid()
    manifest = _parse(memory_manifest)
    if (not isinstance(manifest, dict) or manifest.get("scope") != SCOPE
            or manifest.get("projection_schema") != PROJECTION_SCHEMA
            or not isinstance(manifest.get("projection_sha256"), str)
            or HEX.fullmatch(manifest["projection_sha256"]) is None
            or not isinstance(projection_bytes, bytes) or len(projection_bytes) > MAX_BYTES
            or not hmac.compare_digest(_sha(projection_bytes), manifest["projection_sha256"])
            or (strict and not _strict_memory_policy(manifest.get("policy"), policy_sha256, roster_sha256))):
        _invalid()
    projection = _parse(projection_bytes)
    try:
        if (sp.SCHEMA != PROJECTION_SCHEMA or
                json.dumps(projection, sort_keys=True, indent=1).encode("utf-8") != projection_bytes):
            _invalid()
        sp.validate_projection(projection, approved_tasks=approved_tasks, include_refs=False,
                               roster=roster, exclude_review_signal=strict)
        if not isinstance(query_utc, str) or sp.corpus_mod.parse_utc(query_utc) is None:
            _invalid()
    except (sp.ProjectionRefused, ValueError, TypeError, OverflowError, RecursionError, UnicodeError):
        _invalid()
    if not isinstance(results, list) or len(results) > MAX_RESULTS:
        _invalid()
    records = {}
    for record in projection["records"]:
        provenance = record["provenance"]
        ref = provenance["event_ref"]
        if (record["kind"] != "event" or provenance["ref_kind"] != REF_KIND
                or provenance["canonical_event"] is not False or HEX.fullmatch(ref) is None
                or record["record_id"] != "event:" + ref):
            _invalid()
        key = (_sha(ref.encode("utf-8")), _sha(record["record_id"].encode("utf-8")))
        if key in records and records[key] != record:
            _invalid()
        records.setdefault(key, record)
    resolved, seen = [], set()
    for item in results:
        if (not isinstance(item, dict)
                or set(item) != {"ref_sha256", "doc_id_sha256", "ref_kind", "canonical_event"}
                or item["ref_kind"] != REF_KIND or item["canonical_event"] is not False
                or any(not isinstance(item[k], str) or HEX.fullmatch(item[k]) is None
                       for k in ("ref_sha256", "doc_id_sha256"))):
            _invalid()
        key = (item["ref_sha256"], item["doc_id_sha256"])
        record = records.get(key)
        if record is None or not sp.visible_at(record, query_utc, guard_band_s=1.0):
            _invalid()
        if key not in seen:
            resolved.append({"ref_sha256": key[0], "doc_id_sha256": key[1], "stored_text": record["stored_text"],
                             "ref_kind": REF_KIND, "canonical_event": False})
            seen.add(key)
    return resolved
