"""Pure, opt-in public pointer ANNOUNCEMENT payload + ADVISORY selection; no bridge, file, clock, model or network I/O.

Two functions, both OFF by default, both pure over caller-supplied values:

* ``announcement_payload(report, anchor_sha256)`` turns ONE ``shared_public_producer`` publication report into the
  closed payload ``wd.q4-public-pointer-announce.v1`` (generation name, pointer digest, anchor digest, the four
  artifact digests, the public source mode, and two literal UNKNOWN markers). It carries no path, no cursor, no raw
  record, no byte string and no count. Empty advances are not publications and are refused.
* ``select_announced_pointer(rows, task_id, publisher_agent, roster)`` scans caller-supplied PARSED bridge rows and
  returns the newest well-formed announcement that the configured publisher lane posted under the configured task
  with the roster's uuid for that lane. Every row that carries the announcement schema is a candidate and must pass
  in full; a forged label, another lane, another task, a non-message row, a malformed payload, a conflicting pair
  (one generation with two digests, one digest under two generations) or an ambiguous newest pair refuses the WHOLE
  selection. Exact duplicate rows collapse.

What a selection is NOT (Lead GO lead-mind-pointer-announce-fable-20261031-v1): it is advice. A writer-validated lane
label does not authenticate a same-user origin, a timestamp proves neither freshness nor the absence of replay, the
caller's rows may not cover the whole log, and their order is whatever the caller's reader produced. The output
therefore states ``authenticity``, ``freshness``, ``coverage``, ``ordering`` and ``same_user_integrity`` as UNKNOWN
and ``advisory`` as True; only a separately trusted channel can supply an ACCEPTED pointer. Nothing here reads or
writes the bridge, touches live rows, a model, a scheduler, keys, ACLs, an installer or an activation switch.
"""
import hmac
import json
import re

from sandbox.mind.step2 import shared_projection as projection
from sandbox.mind.step2 import shared_public_producer as pub

SCHEMA = "wd.q4-public-pointer-announce.v1"
SELECTION_SCHEMA = "wd.q4-public-pointer-selection.v1"
UNKNOWN = "UNKNOWN"
ANNOUNCED_STATUSES = frozenset({"PUBLISHED_GENESIS", "PUBLISHED_HISTORY"})
ARTIFACT_KEYS = frozenset({"descriptor_sha256", "memory_sha256", "projection_sha256", "receipt_sha256"})
PAYLOAD_KEYS = frozenset({"schema", "generation", "pointer_sha256", "anchor_sha256", "artifacts", "source_mode",
                          "authenticity", "freshness"})
SELECTION_KEYS = frozenset({"schema", "generation", "pointer_sha256", "anchor_sha256", "artifacts", "source_mode",
                            "announced_ts_utc", "publisher_agent", "candidates", "advisory", "authenticity", "freshness",
                            "coverage", "ordering", "same_user_integrity"})
REFUSALS = frozenset({"request_invalid", "report_invalid", "not_a_publication", "rows_invalid", "no_candidate",
                      "candidate_rejected", "ambiguous", "conflicting"})
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


def _payload_ok(value):
    """The closed announcement payload, exactly; any extra key, non-digest or trust claim fails."""
    return (isinstance(value, dict) and set(value) == PAYLOAD_KEYS and value["schema"] == SCHEMA
            and _generation_ok(value["generation"]) and _hex(value["pointer_sha256"]) and _hex(value["anchor_sha256"])
            and _artifacts_ok(value["artifacts"]) and _source_mode_ok(value["source_mode"])
            and value["authenticity"] == UNKNOWN and value["freshness"] == UNKNOWN
            and len(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")) <= PAYLOAD_LIMIT)


def announcement_payload(report, anchor_sha256, *, enabled=False):
    """Build the closed announcement payload from ONE producer publication report and the caller's anchor digest.
    OFF by default. Refuses with ``report_invalid`` on any shape drift and ``not_a_publication`` for empty advances."""
    if type(enabled) is not bool:
        _refuse("request_invalid")
    if not enabled:
        return {"status": "OFF"}
    if not isinstance(report, dict) or set(report) != pub.REPORT_KEYS or report["status"] not in pub.STATUSES:
        _refuse("report_invalid")
    if report["status"] not in ANNOUNCED_STATUSES:
        _refuse("not_a_publication")
    if (not _generation_ok(report["generation"]) or not _hex(report["pointer_sha256"]) or not _artifacts_ok(report["artifacts"])
            or not _source_mode_ok(report["source_mode"]) or type(report["records"]) is not int or report["records"] < 0
            or report["publisher_identity"] != UNKNOWN or report["source_authenticity"] != UNKNOWN
            or report["freshness"] != UNKNOWN or report["authenticated"] is not False or report["pins_bound"] is not True
            or not _hex(anchor_sha256)):
        _refuse("report_invalid")
    payload = {"schema": SCHEMA, "generation": report["generation"], "pointer_sha256": report["pointer_sha256"],
               "anchor_sha256": anchor_sha256, "artifacts": {key: report["artifacts"][key] for key in sorted(ARTIFACT_KEYS)},
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


def select_announced_pointer(rows, *, task_id, publisher_agent, roster, enabled=False):
    """ADVISORY: the newest well-formed announcement by ``publisher_agent`` under ``task_id`` with the roster uuid, from
    caller-supplied parsed rows. OFF by default; refuses with one fixed token. Writer labels are not authentication,
    timestamps are not freshness, the rows are not coverage: every such field in the result is UNKNOWN."""
    if type(enabled) is not bool:
        _refuse("request_invalid")
    if not enabled:
        return {"status": "OFF"}
    if (not isinstance(task_id, str) or not 1 <= len(task_id.encode("utf-8")) <= projection.BYTE_CAPS["task_id"]
            or publisher_agent not in projection.LANES or not isinstance(roster, dict) or publisher_agent not in roster
            or not all(isinstance(agent, str) and isinstance(uuid, str) and projection.UUID.fullmatch(uuid) is not None
                       for agent, uuid in roster.items())):
        _refuse("request_invalid")
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
        identity = _identity(payload)
        if by_generation.setdefault(payload["generation"], identity) != identity:
            _refuse("conflicting")
        if by_pointer.setdefault(payload["pointer_sha256"], payload["generation"]) != payload["generation"]:
            _refuse("conflicting")
    ts, payload = next(iter(newest.values()))
    out = {"schema": SELECTION_SCHEMA, "generation": payload["generation"], "pointer_sha256": payload["pointer_sha256"],
           "anchor_sha256": payload["anchor_sha256"], "artifacts": dict(payload["artifacts"]),
           "source_mode": dict(payload["source_mode"]), "announced_ts_utc": ts, "publisher_agent": publisher_agent,
           "candidates": len(candidates), "advisory": True, "authenticity": UNKNOWN, "freshness": UNKNOWN,
           "coverage": UNKNOWN, "ordering": UNKNOWN, "same_user_integrity": UNKNOWN}
    if set(out) != SELECTION_KEYS:
        _refuse("rows_invalid")
    return out
