"""Default-off lane-side public Q4 caller over ``tools.wd_q4_lane_link``; neither a CLI, a transport nor a wiring.

One lane turn asks the pinned public Q4 wrapper one bounded question and receives a closed answer. Everything
this module needs comes from the caller as explicit arguments: the bundle root, commit, deployment-manifest and
wrapper digests, the hash-pinned public plan, the caller's OWN expected binding digests (pointer, source anchor,
encoder, policy, roster, public policy, descriptor, projection), the bytes of the projection those digests pin,
and an injected transport. Nothing is read from the environment, argv, a log or a default location; the situation
text travels only as in-memory stdin bytes to the transport, never in arguments, exceptions or output.

v2 (query-echo fix): no text returned by the child is ever output. The caller supplies ``projection_bytes`` whose
sha256 must equal the caller's own ``expected_binding["projection_sha256"]`` (checked BEFORE any transport call);
the bytes must be the builder's canonical serialization of a shared projection; every record is indexed by the
same two digests the Q4 runtime reports (``sha256(provenance.event_ref)``, ``sha256(record_id)``). A result is
accepted only if its two digests name one record of that verified projection, its ``stored_text`` equals that
record's text byte for byte, and the record is visible at the query time; the text that is output is the
record's, read from the caller-pinned bytes. A transport or child that echoes any part of the situation therefore
fails ``result_refused``: the situation is not a projection record. Syntactic filtering of the child text was never
a privacy proof and is no longer relied on; the closed character class remains only as defense in depth over the
projected text. Raw query bytes and raw transport output are lane-local: they are never logged, posted or returned.

``lane`` is a declaration shared by the five known lanes; it is never authentication. A projection pin is integrity
of what the caller supplied, not attestation that the projection is public, fresh or policy-conformant: this module
does not re-pin policy membership or roster authorship of the projection (it has no policy bytes), and it does not
prove freshness, publisher authentication, source filtering, same-user protection, process containment or a
whole-call time bound. Until a separately trusted transport exists (``TRANSPORT_INTERFACE``), only an injected
synthetic callable can be used; nothing here enables, installs or activates a producer, runtime or model.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
from os import PathLike
import re

from sandbox.mind.step2 import shared_projection as sp
from tools import wd_q4_lane_link as link

SCHEMA = "wd.q4-lane-answer.v1"
LANES = link._LANES
MAX_SITUATION_CHARS = 4000
MAX_QUERY_BYTES = 65536
MAX_RESULTS = 100
MAX_PROJECTION_BYTES = 16 * 1024 * 1024  # == shared_result_resolver.MAX_BYTES
VISIBILITY_GUARD_SECONDS = 1.0  # == the resolver guard band over shared_projection.visible_at
REF_KIND = "sha256_of_projected_metadata"
TRANSPORT_INTERFACE = "transport(*, wrapper: str, arguments: tuple, stdin_bytes: bytes) -> (bytes, int)"
BINDING_KEYS = frozenset({"pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
                          "roster_sha256", "public_policy_sha256", "descriptor_sha256", "projection_sha256"})
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PIN64 = re.compile(r"[0-9A-Fa-f]{64}\Z")
_TEXT = re.compile(r"[A-Za-z0-9._: -]{1,320}\Z")
_HEX_RUN = re.compile(r"[0-9a-fA-F]{32,}")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_SEAM_REASONS = frozenset({"disabled", "invalid_lane", "transport_unavailable", "binding_refused", "invalid_query",
                           "transport_refused"})
_TRUST = {"lane_authentication": "UNKNOWN", "freshness": "UNKNOWN", "publisher_authentication": "UNKNOWN",
          "projection_public_attestation": "UNKNOWN", "transport": "caller_injected", "projection": "caller_pinned"}


class LaneQueryRefused(ValueError):
    """Fixed public reason only: never the situation, a path, a pin, a reply, a record or a transport detail."""


def _hex(value, pattern):
    return type(value) is str and pattern.fullmatch(value) is not None


def _inputs(situation, time, expected_commit, expected_manifest_sha256, expected_wrapper_sha256,
            expected_public_plan_sha256, expected_binding, bundle_root, public_plan_path, projection_bytes):
    """Syntax of every caller input, checked before any file is touched; returns the canonical query bytes."""
    if (not _hex(expected_commit, _HEX40) or not _hex(expected_manifest_sha256, _PIN64)
            or not _hex(expected_wrapper_sha256, _PIN64) or not _hex(expected_public_plan_sha256, _HEX64)
            or type(expected_binding) is not dict or set(expected_binding) != BINDING_KEYS
            or any(not _hex(expected_binding[key], _HEX64) for key in BINDING_KEYS)
            or not isinstance(bundle_root, (str, PathLike)) or not isinstance(public_plan_path, (str, PathLike))
            or type(projection_bytes) is not bytes or not 1 <= len(projection_bytes) <= MAX_PROJECTION_BYTES):
        raise ValueError()
    if (type(situation) is not str or not 1 <= len(situation) <= MAX_SITUATION_CHARS
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in situation)):
        raise ValueError()
    if not isinstance(time, datetime) or time.tzinfo is None or time.utcoffset() is None:
        raise ValueError()
    when = time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = json.dumps({"situation": situation, "time": when}, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False).encode("ascii")
    if len(query) > MAX_QUERY_BYTES:
        raise ValueError()
    return query, when


def _constant(_):
    raise ValueError()  # NaN / Infinity never enter a projection


def _pairs(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError()
        document[key] = value
    return document


def _records(projection_bytes, expected_sha256):
    """The record index of the caller-pinned projection: ``(sha256(event_ref), sha256(record_id)) -> record``.

    The bytes must hash to the caller's pin, be the builder's canonical serialization (``sort_keys``, ``indent=1``),
    carry the shared-projection schema/scope/authority, and every record must be an event record with the
    projected-metadata ref kind, ``canonical_event`` False and ``record_id == "event:" + event_ref``. Two different
    records under one key refuse. Policy membership and roster authorship are NOT re-pinned here (see module doc).
    """
    if not hmac.compare_digest(hashlib.sha256(projection_bytes).hexdigest(), expected_sha256):
        raise ValueError()
    projection = json.loads(projection_bytes.decode("utf-8", "strict"), object_pairs_hook=_pairs,
                            parse_constant=_constant)
    if (type(projection) is not dict
            or json.dumps(projection, sort_keys=True, indent=1).encode("utf-8") != projection_bytes
            or projection.get("schema") != sp.SCHEMA or projection.get("scope") != sp.SCOPE
            or projection.get("authority") != "none" or type(projection.get("records")) is not list):
        raise ValueError()
    records = {}
    for record in projection["records"]:
        if type(record) is not dict or type(record.get("provenance")) is not dict:
            raise ValueError()
        provenance = record["provenance"]
        ref = provenance["event_ref"]
        if (record["kind"] != "event"
                or provenance["ref_kind"] != REF_KIND or provenance["canonical_event"] is not False
                or not _hex(ref, _HEX64) or record["record_id"] != "event:" + ref
                or type(record["stored_text"]) is not str or type(provenance["available_utc"]) is not str):
            raise ValueError()
        key = (hashlib.sha256(ref.encode("utf-8")).hexdigest(),
               hashlib.sha256(record["record_id"].encode("utf-8")).hexdigest())
        if key in records and records[key] != record:
            raise ValueError()
        records.setdefault(key, record)
    return records


def _text(value):
    if (type(value) is not str or _TEXT.fullmatch(value) is None or "@" in value
            or _HEX_RUN.search(value) is not None or _UUID.search(value) is not None):
        raise ValueError()
    return value


def _resolved(item, records, when, seen):
    """The projection record a child result names, or ValueError; the output text is the RECORD's, never the item's."""
    key = (item["ref_sha256"], item["doc_id_sha256"])
    record = records.get(key)
    if (record is None or key in seen or type(item["stored_text"]) is not str
            or not hmac.compare_digest(item["stored_text"].encode("utf-8"), record["stored_text"].encode("utf-8"))
            or not sp.visible_at(record, when, guard_band_s=VISIBILITY_GUARD_SECONDS)):
        raise ValueError()
    seen.add(key)
    return {"ref_sha256": key[0], "doc_id_sha256": key[1], "stored_text": _text(record["stored_text"])}


def _answer(lane, value, expected_binding, records, when):
    """Closed projection of a seam-validated reply; the caller's own pins and projection decide, never the child."""
    if value["status"] == "REFUSED":
        return {"schema": SCHEMA, "lane": lane, "status": "REFUSED", "reason": "runtime_refused",
                "result_count": 0, "results": [], "binding": None, "trust": dict(_TRUST)}
    binding = value["binding"]
    if any(not hmac.compare_digest(binding[key], expected_binding[key]) for key in BINDING_KEYS):
        raise LaneQueryRefused("binding_refused")
    results = value["results"]
    if type(results) is not list or len(results) > MAX_RESULTS:
        raise LaneQueryRefused("result_refused")
    seen = set()
    try:
        clean = [_resolved(item, records, when, seen) for item in results]
    except Exception:
        raise LaneQueryRefused("result_refused") from None
    return {"schema": SCHEMA, "lane": lane, "status": value["status"], "reason": "", "result_count": len(clean),
            "results": clean, "binding": {key: expected_binding[key] for key in sorted(BINDING_KEYS)},
            "trust": dict(_TRUST)}


def query_public(*, lane, situation, time, bundle_root, expected_commit, expected_manifest_sha256,
                 expected_wrapper_sha256, public_plan_path, expected_public_plan_sha256, expected_binding,
                 projection_bytes=None, transport=None, enabled=False):
    """One bounded public Q4 question for one declared lane over an injected transport; OFF by default.

    Order: switch, lane, transport presence, input syntax (nothing read yet), canonical query bytes in memory,
    the caller-pinned projection verified and indexed (``projection_refused``; no transport call on failure),
    then ``wd_q4_lane_link.invoke_public`` (binds bundle, manifest, wrapper and plan, calls the transport once,
    validates the reply against the plan pins), then the caller's own ``expected_binding`` (eight digests) must
    equal the reply binding and every result must resolve to one visible record of the verified projection with
    identical text (``result_refused``); the returned text is the record's. Any other outcome is a fixed refusal.
    """
    if enabled is not True:
        raise LaneQueryRefused("disabled")
    if type(lane) is not str or lane not in LANES:
        raise LaneQueryRefused("invalid_lane")
    if not callable(transport):
        raise LaneQueryRefused("transport_unavailable")
    try:
        query, when = _inputs(situation, time, expected_commit, expected_manifest_sha256, expected_wrapper_sha256,
                              expected_public_plan_sha256, expected_binding, bundle_root, public_plan_path,
                              projection_bytes)
    except Exception:
        raise LaneQueryRefused("invalid_input") from None
    try:
        records = _records(projection_bytes, expected_binding["projection_sha256"])
    except Exception:
        raise LaneQueryRefused("projection_refused") from None
    try:
        value = link.invoke_public(lane=lane, bundle_root=bundle_root, expected_commit=expected_commit,
                                   expected_manifest_sha256=expected_manifest_sha256,
                                   expected_wrapper_sha256=expected_wrapper_sha256,
                                   public_plan_path=public_plan_path,
                                   expected_public_plan_sha256=expected_public_plan_sha256,
                                   query_bytes=query, transport=transport, enabled=True)
    except link.LaneLinkRefused as refusal:
        reason = refusal.args[0] if refusal.args and refusal.args[0] in _SEAM_REASONS else "transport_refused"
        raise LaneQueryRefused(reason) from None
    except Exception:
        raise LaneQueryRefused("transport_refused") from None
    try:
        return _answer(lane, value, expected_binding, records, when)
    except LaneQueryRefused:
        raise
    except Exception:
        raise LaneQueryRefused("result_refused") from None
