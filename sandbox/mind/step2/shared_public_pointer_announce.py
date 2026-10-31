"""Pure, opt-in public pointer ANNOUNCEMENT payload + ADVISORY selection; no bridge, file, clock, model or network I/O.

Two functions, both OFF by default, both pure over caller-supplied values:

* ``announcement_payload(report, anchor_sha256, verified_view=None)`` turns ONE ``shared_public_producer`` report into
  the closed payload ``wd.q4-public-pointer-announce.v1`` (kind, generation name, pointer digest, anchor digest, the
  four artifact digests, the public source mode, and two literal UNKNOWN markers). It carries no path, no cursor, no
  raw record, no byte string and no count. A publication report (``PUBLISHED_GENESIS`` / ``PUBLISHED_HISTORY``) is
  announced as ``kind = publication``. A verified EMPTY ADVANCE (``EMPTY_ADVANCED``) changes the pointer digest but
  keeps the generation binding; its report names no generation, so it is announced as ``kind = empty_advance`` ONLY
  through the checkpoint view the caller obtained and verified itself (``read_verified_pointer`` six-field shape) for
  exactly that advanced digest and anchor (``view_required`` / ``view_mismatch`` otherwise). ``EMPTY_NO_CHANGE`` needs
  no announcement and is refused as ``no_change_not_announced``.
* ``select_announced_pointer(rows, task_id, publisher_agent, roster, verified_view=None)`` scans caller-supplied PARSED
  bridge rows and returns the newest well-formed announcement that the configured publisher lane posted under the
  configured task with the roster's uuid for that lane. Every row carrying the announcement schema is a candidate and
  must pass in full; a forged label, another lane, another task, a non-message row or a malformed payload refuses the
  WHOLE selection. Within one generation, distinct pointer digests are normal (empty advances) as long as the
  generation BINDING (artifacts, anchor, source mode) is identical; a differing binding, or one digest under two
  generations, is ``conflicting``; two distinct payloads at the newest instant are ``ambiguous``. Exact duplicate rows
  collapse. When the caller hands in the view it verified for the selected digest, the VIEW FIELDS (pointer, generation,
  artifacts, anchor) are checked against the selected payload (``view_fields_consistent``); the six-field view carries
  neither the source mode nor the public-policy pin, so ``source_mode_consistency`` is always UNKNOWN here: that is
  field consistency, never trust and never a whole-payload claim.

What a selection is NOT (Lead GO lead-mind-pointer-announce-fable-20261031-v1 + v2): it is advice. A writer-validated
lane label does not authenticate a same-user origin, a timestamp proves neither freshness nor the absence of replay,
the caller's rows may not cover the whole log, and their order is whatever the caller's reader produced. The output
therefore states ``authenticity``, ``freshness``, ``coverage``, ``ordering`` and ``same_user_integrity`` as UNKNOWN and
``advisory`` as True; only a separately trusted channel can supply an ACCEPTED pointer. Nothing here reads or writes
the bridge, touches live rows, a model, a scheduler, keys, ACLs, an installer or an activation switch.
"""
import hmac
import json
import re

from sandbox.mind.step2 import shared_projection as projection
from sandbox.mind.step2 import shared_public_producer as pub

SCHEMA = "wd.q4-public-pointer-announce.v1"
SELECTION_SCHEMA = "wd.q4-public-pointer-selection.v1"
UNKNOWN = "UNKNOWN"
KINDS = frozenset({"publication", "empty_advance"})
PUBLICATION_STATUSES = frozenset({"PUBLISHED_GENESIS", "PUBLISHED_HISTORY"})
ANNOUNCED_STATUSES = PUBLICATION_STATUSES | {"EMPTY_ADVANCED"}
ARTIFACT_KEYS = frozenset({"descriptor_sha256", "memory_sha256", "projection_sha256", "receipt_sha256"})
VIEW_KEYS = frozenset({"pointer_sha256", "generation", "artifacts", "anchor_sha256", "cursor_end_sha256", "watermark_sha256"})
PAYLOAD_KEYS = frozenset({"schema", "kind", "generation", "pointer_sha256", "anchor_sha256", "artifacts", "source_mode",
                          "authenticity", "freshness"})
SELECTION_KEYS = frozenset({"schema", "kind", "generation", "pointer_sha256", "anchor_sha256", "artifacts", "source_mode",
                            "announced_ts_utc", "publisher_agent", "candidates", "advisory", "view_fields_consistent",
                            "source_mode_consistency", "authenticity", "freshness", "coverage", "ordering",
                            "same_user_integrity"})
REFUSALS = frozenset({"request_invalid", "report_invalid", "no_change_not_announced", "view_required", "view_mismatch",
                      "rows_invalid", "no_candidate", "candidate_rejected", "ambiguous", "conflicting"})
PAYLOAD_LIMIT = 4096
ROWS_LIMIT = 100000
HEX64 = re.compile(r"[0-9a-f]{64}")
TS_UTC = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,7}))?Z")


class PointerAnnounceRefused(ValueError):
    """Fixed, data-free refusal token (one of ``REFUSALS``)."""


def _refuse(token):
    raise PointerAnnounceRefused(token) from None


def _hex(value):
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _artifacts_ok(value):
    return isinstance(value, dict) and set(value) == ARTIFACT_KEYS and all(_hex(value[key]) for key in ARTIFACT_KEYS)


def _source_mode_ok(value):
    return (isinstance(value, dict) and set(value) == {"status_mode", "public_policy_sha256"}
            and value["status_mode"] == pub.STATUS_MODE and _hex(value["public_policy_sha256"]))


def _generation_ok(value):
    return isinstance(value, str) and pub.builder.MEMORY_NAME.fullmatch(value) is not None


def _view_ok(view):
    """The caller-verified checkpoint view in the Lead reader's exact six-field shape."""
    return (isinstance(view, dict) and set(view) == VIEW_KEYS and _hex(view["pointer_sha256"])
            and _generation_ok(view["generation"]) and _artifacts_ok(view["artifacts"]) and _hex(view["anchor_sha256"])
            and _hex(view["cursor_end_sha256"]) and _hex(view["watermark_sha256"]))


def _payload_ok(value):
    """The closed announcement payload, exactly; any extra key, non-digest, unknown kind or trust claim fails."""
    return (isinstance(value, dict) and set(value) == PAYLOAD_KEYS and value["schema"] == SCHEMA
            and isinstance(value["kind"], str) and value["kind"] in KINDS  # a non-string kind refuses, never a TypeError
            and _generation_ok(value["generation"]) and _hex(value["pointer_sha256"]) and _hex(value["anchor_sha256"])
            and _artifacts_ok(value["artifacts"]) and _source_mode_ok(value["source_mode"])
            and value["authenticity"] == UNKNOWN and value["freshness"] == UNKNOWN
            and len(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")) <= PAYLOAD_LIMIT)


def _view_matches(view, *, pointer_sha256, anchor_sha256, generation=None, artifacts=None):
    if (not _view_ok(view) or not hmac.compare_digest(view["pointer_sha256"], pointer_sha256)
            or not hmac.compare_digest(view["anchor_sha256"], anchor_sha256)
            or (generation is not None and view["generation"] != generation)
            or (artifacts is not None and view["artifacts"] != artifacts)):
        _refuse("view_mismatch")


def announcement_payload(report, anchor_sha256, *, verified_view=None, enabled=False):
    """Build the closed announcement payload from ONE producer report, the caller's anchor digest and, for an empty
    advance, the checkpoint view the caller verified for that advanced digest. OFF by default; fixed tokens only."""
    if type(enabled) is not bool:
        _refuse("request_invalid")
    if not enabled:
        return {"status": "OFF"}
    if (not isinstance(report, dict) or set(report) != pub.REPORT_KEYS or not isinstance(report["status"], str)
            or report["status"] not in ANNOUNCED_STATUSES | {"EMPTY_NO_CHANGE"}):
        _refuse("report_invalid")  # non-string status (list/dict) and anything outside the producer statuses: fixed token
    if report["status"] == "EMPTY_NO_CHANGE":
        _refuse("no_change_not_announced")  # the pointer did not move: nothing new to announce
    if (not _hex(report["pointer_sha256"]) or not _source_mode_ok(report["source_mode"]) or type(report["records"]) is not int
            or report["records"] < 0 or report["publisher_identity"] != UNKNOWN or report["source_authenticity"] != UNKNOWN
            or report["freshness"] != UNKNOWN or report["authenticated"] is not False or report["pins_bound"] is not True
            or not _hex(anchor_sha256)):
        _refuse("report_invalid")
    pointer_sha256 = report["pointer_sha256"]
    if report["status"] in PUBLICATION_STATUSES:
        kind, generation, artifacts = "publication", report["generation"], report["artifacts"]
        if not _generation_ok(generation) or not _artifacts_ok(artifacts):
            _refuse("report_invalid")
        if verified_view is not None:
            _view_matches(verified_view, pointer_sha256=pointer_sha256, anchor_sha256=anchor_sha256, generation=generation,
                          artifacts=artifacts)
    else:  # EMPTY_ADVANCED: the report names no generation; the binding comes from the caller-verified view ONLY
        if report["generation"] is not None or report["artifacts"] is not None or report["records"] != 0:
            _refuse("report_invalid")
        if verified_view is None:
            _refuse("view_required")
        _view_matches(verified_view, pointer_sha256=pointer_sha256, anchor_sha256=anchor_sha256)
        kind, generation, artifacts = "empty_advance", verified_view["generation"], verified_view["artifacts"]
    payload = {"schema": SCHEMA, "kind": kind, "generation": generation, "pointer_sha256": pointer_sha256,
               "anchor_sha256": anchor_sha256, "artifacts": {key: artifacts[key] for key in sorted(ARTIFACT_KEYS)},
               "source_mode": dict(report["source_mode"]), "authenticity": UNKNOWN, "freshness": UNKNOWN}
    if not _payload_ok(payload):
        _refuse("report_invalid")
    return payload


def _ts_key(value):
    match = TS_UTC.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return None
    return match.group(1) + "." + (match.group(2) or "").ljust(7, "0")


def _identity(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _binding(payload):
    """The generation binding an announcement asserts: what must be identical across every pointer of one generation."""
    return _identity({"artifacts": payload["artifacts"], "anchor_sha256": payload["anchor_sha256"],
                      "source_mode": payload["source_mode"]})


def select_announced_pointer(rows, *, task_id, publisher_agent, roster, verified_view=None, enabled=False):
    """ADVISORY: the newest well-formed announcement by ``publisher_agent`` under ``task_id`` with the roster uuid, from
    caller-supplied parsed rows. OFF by default; refuses with one fixed token. Writer labels are not authentication,
    timestamps are not freshness, the rows are not coverage: every such field in the result is UNKNOWN."""
    if type(enabled) is not bool:
        _refuse("request_invalid")
    if not enabled:
        return {"status": "OFF"}
    if (not isinstance(task_id, str) or not 1 <= len(task_id.encode("utf-8")) <= projection.BYTE_CAPS["task_id"]
            or not isinstance(publisher_agent, str) or publisher_agent not in projection.LANES
            or not isinstance(roster, dict) or publisher_agent not in roster
            or not all(isinstance(agent, str) and isinstance(uuid, str) and projection.UUID.fullmatch(uuid) is not None
                       for agent, uuid in roster.items())):
        _refuse("request_invalid")
    if verified_view is not None and not _view_ok(verified_view):
        _refuse("view_mismatch")
    if not isinstance(rows, list) or len(rows) > ROWS_LIMIT or not all(isinstance(row, dict) for row in rows):
        _refuse("rows_invalid")
    expected_uuid = roster[publisher_agent]
    candidates = []
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            continue  # not an announcement: never a candidate, never a rejection
        uuid = row.get("agent_uuid")
        key = _ts_key(row.get("ts_utc"))
        if (row.get("type") != "message" or row.get("agent") != publisher_agent or not isinstance(uuid, str)
                or not hmac.compare_digest(uuid, expected_uuid) or row.get("task_id") != task_id or key is None
                or not _payload_ok(payload)):
            _refuse("candidate_rejected")
        candidates.append((key, row["ts_utc"], payload))
    if not candidates:
        _refuse("no_candidate")
    newest_key = max(key for key, _, _ in candidates)
    newest = {_identity(payload): (ts, payload) for key, ts, payload in candidates if key == newest_key}
    if len(newest) != 1:
        _refuse("ambiguous")
    by_generation, by_pointer = {}, {}
    for _, _, payload in candidates:
        if by_generation.setdefault(payload["generation"], _binding(payload)) != _binding(payload):
            _refuse("conflicting")  # one generation, two bindings
        if by_pointer.setdefault(payload["pointer_sha256"], payload["generation"]) != payload["generation"]:
            _refuse("conflicting")  # one pointer digest, two generations
    ts, payload = next(iter(newest.values()))
    view_fields_consistent = UNKNOWN  # the view's four shared fields only; it carries no source mode or policy pin
    if verified_view is not None:
        _view_matches(verified_view, pointer_sha256=payload["pointer_sha256"], anchor_sha256=payload["anchor_sha256"],
                      generation=payload["generation"], artifacts=payload["artifacts"])
        view_fields_consistent = True
    out = {"schema": SELECTION_SCHEMA, "kind": payload["kind"], "generation": payload["generation"],
           "pointer_sha256": payload["pointer_sha256"], "anchor_sha256": payload["anchor_sha256"],
           "artifacts": dict(payload["artifacts"]), "source_mode": dict(payload["source_mode"]), "announced_ts_utc": ts,
           "publisher_agent": publisher_agent, "candidates": len(candidates), "advisory": True,
           "view_fields_consistent": view_fields_consistent, "source_mode_consistency": UNKNOWN, "authenticity": UNKNOWN,
           "freshness": UNKNOWN, "coverage": UNKNOWN, "ordering": UNKNOWN, "same_user_integrity": UNKNOWN}
    if set(out) != SELECTION_KEYS:
        _refuse("rows_invalid")
    return out
