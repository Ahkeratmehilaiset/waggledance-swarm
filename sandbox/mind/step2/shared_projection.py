"""HEX MEMORY STEP 2, shared projection: the default-deny record set every lane may hold in full.

The Q4 memory directory is plain files whose cell metadata carries every stored text, so whatever is projected here
is readable by every lane that can read the memory. This module therefore builds records from APPROVED bridge event
METADATA only (``APPROVED_FIELDS``): time, lane agent, a non-decision event type, a status from a closed enum, a
task id only when it is in a caller-supplied pre-approved set, and optional PR number, 40-hex head and CI value from
a closed enum. Everything else on an event (message, payload, paths, identities, nonces, digests, unknown keys) is
never read, never rendered and never persisted; it is only counted.

v2 (independent review of v1): a regex-shaped field is a free-text channel. ``status`` and ``ci`` are therefore
CLOSED ENUMS (``ALLOWED_STATUS``, ``ALLOWED_CI``): any other text maps to ``UNKNOWN`` and never appears. ``task_id``
is rendered ONLY when it is a member of ``approved_tasks`` (a frozenset the caller pins from an operator-approved
list; nothing in this module names a task); any other id, including a path-shaped one, becomes ``UNKNOWN`` and is
not echoed anywhere. ``agent`` and ``type`` were already closed sets (``LANES``, ``ALLOWED_TYPES``). Memory notes
are excluded from the shared path entirely: ``project_notes`` always refuses, a caller-provided hash list is not an
authorization. ``write_projection`` never overwrites an existing file, stages in an exclusively created unique file
beside the target, removes only that staging on failure, and turns filesystem errors into fixed path-free refusals.

The one text a record carries (``stored_text`` = ``summary`` = the encoder input, see ``encoder_input``) is the head
line ``<type> <status> <agent> <task_id> [pr:N] [head:HEX] [ci:VALUE]`` rebuilt from the approved fields, so the
persisted bytes, the BM25 text and the vector input are one and the same string. A record's reference is the sha256
of its PROJECTED approved fields in canonical JSON (``event:<hex64>``): it identifies this projection of the
metadata, NOT the canonical bridge event (``provenance.canonical_event`` is false), and nothing here builds or
claims a Q4 index generation.

Remaining bounded channels, disclosed rather than closed: ``pr`` (a positive integer), ``head`` (40 lowercase hex
characters) and ``ts_utc`` (a normalized timestamp) are validated by shape only; a producer could encode a few
bytes in them. ``approved_tasks`` is trusted as given once it is safe-shaped.

Default deny everywhere else: a lane outside ``LANES``, a type outside ``ALLOWED_TYPES`` (every peer-review and
decision type is outside), a sealed-named approved task, a malformed time, PR or head, or any text field over its
UTF-8 byte cap (a 4-byte character costs four) refuses that event whole with one fixed reason. Synthetic only: no
real capture, memory, index or model is touched by this module or its tests.
"""

import hashlib
import json
import os
import re
import secrets
from pathlib import Path

from sandbox.mind.step2 import corpus as corpus_mod

SCHEMA = "wd.bridge-mind-shared-projection.v2"
LANES = frozenset({"codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"})
ALLOWED_TYPES = frozenset({"message", "wake_request", "handoff", "test", "claim", "release", "done", "milestone",
                           "status", "progress"})
# Documented for readers; the allowlist above is what decides. Peer review and decisions never enter the shared set.
EXCLUDED_TYPES = frozenset({"decision", "finding", "rco_pass", "vote", "veto", "changes_requested", "recusal",
                            "build_consensus", "review"})
ALLOWED_STATUS = frozenset({"none", "requested", "answered", "milestone", "info", "in_progress", "handoff", "done",
                            "active", "delivered", "blocked", "released"})
ALLOWED_CI = frozenset({"green", "red", "pending", "none"})
APPROVED_FIELDS = ("ts_utc", "agent", "type", "status", "task_id", "pr", "head", "ci")
OPTIONAL_FIELDS = ("pr", "head", "ci")
SEALED_MARKERS = ("sealed", "gold", "heldout", "judge")
BYTE_CAPS = {"agent": 40, "type": 32, "status": 32, "task_id": 128, "ci": 32}
HEAD_LINE_BYTES = 320
UNKNOWN = "UNKNOWN"

LANE = re.compile(r"[a-z][a-z0-9-]{1,39}")
TYPE = re.compile(r"[a-z][a-z0-9_]{0,31}")
TASK = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")  # no separator, no drive letter, no space: a path never fits
HEAD = re.compile(r"[0-9a-f]{40}")
PR_MAX = 10 ** 9


class ProjectionRefused(ValueError):
    """One fixed reason token; never the offending value."""


def _bytes(text):
    return len(text.encode("utf-8", "surrogatepass"))


def _text(name, value):
    """A text field within its byte cap; anything else refuses with a fixed reason."""
    if not isinstance(value, str):
        raise ProjectionRefused(f"{name}_invalid")
    if _bytes(value) > BYTE_CAPS[name]:
        raise ProjectionRefused(f"{name}_over_bytes")
    return value


def _closed(name, value, pattern, allowed):
    """A required field that must match its pattern AND be a member of a closed set."""
    value = _text(name, value)
    if pattern.fullmatch(value) is None:
        raise ProjectionRefused(f"{name}_invalid")
    return value if value in allowed else None


def _enum(name, value, allowed, empty=None):
    """An enum field: a member renders itself, any other text becomes UNKNOWN, a non-text refuses."""
    if value is None or value == "":
        return empty
    value = _text(name, value)
    return value if value in allowed else UNKNOWN


def _approved_tasks(approved_tasks):
    if approved_tasks is None:
        return frozenset()
    if not isinstance(approved_tasks, frozenset) or not all(isinstance(t, str) and TASK.fullmatch(t) is not None
                                                            for t in approved_tasks):
        raise ProjectionRefused("approved_tasks_invalid")
    return approved_tasks


def _task(value, approved_tasks):
    """The task id only when pre-approved and safe-shaped; a sealed-named approved task refuses; else UNKNOWN."""
    value = _text("task_id", value)
    if TASK.fullmatch(value) is None or value not in approved_tasks:
        return UNKNOWN
    if any(marker in value for marker in SEALED_MARKERS):
        raise ProjectionRefused("sealed_named_excluded")
    return value


def approved_fields(event, approved_tasks=frozenset()):
    """The approved fields of one event, validated; forbidden and unknown keys are counted, never read."""
    if not isinstance(event, dict):
        raise ProjectionRefused("event_not_object")
    dropped = sum(1 for key in event if key not in APPROVED_FIELDS)
    when = corpus_mod.parse_utc(event.get("ts_utc")) if isinstance(event.get("ts_utc"), str) else None
    if when is None:
        raise ProjectionRefused("time_invalid")
    agent = _closed("agent", event.get("agent"), LANE, LANES)
    if agent is None:
        raise ProjectionRefused("agent_not_lane")
    kind = _closed("type", event.get("type"), TYPE, ALLOWED_TYPES)
    if kind is None:
        raise ProjectionRefused("type_not_allowed")
    status = _enum("status", event.get("status"), ALLOWED_STATUS, empty="none")
    task = _task(event.get("task_id"), approved_tasks)
    pr = event.get("pr")
    if pr is not None and (type(pr) is not int or pr < 1 or pr > PR_MAX):
        raise ProjectionRefused("pr_invalid")
    head = event.get("head")
    if head is not None and (not isinstance(head, str) or HEAD.fullmatch(head) is None):
        raise ProjectionRefused("head_invalid")
    ci = _enum("ci", event.get("ci"), ALLOWED_CI, empty=None)
    fields = {"ts_utc": corpus_mod.utc_text(when), "agent": agent, "type": kind, "status": status, "task_id": task,
              "pr": pr, "head": head, "ci": ci}
    return fields, dropped


def head_line(fields):
    """The only text a shared record carries, rebuilt from approved fields; refused over the byte cap."""
    parts = [fields["type"], fields["status"], fields["agent"], fields["task_id"]]
    if fields["pr"] is not None:
        parts.append(f"pr:{fields['pr']}")
    if fields["head"] is not None:
        parts.append(f"head:{fields['head']}")
    if fields["ci"] is not None:
        parts.append(f"ci:{fields['ci']}")
    line = " ".join(parts)
    if _bytes(line) > HEAD_LINE_BYTES:
        raise ProjectionRefused("head_line_over_bytes")
    return line


def projected_ref(fields):
    """sha256 of the projected approved fields; identifies this projection, not the canonical bridge event."""
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def project_event(event, approved_tasks=frozenset()):
    """One shared record from one event, or ProjectionRefused; nothing partial is returned."""
    fields, dropped = approved_fields(event, approved_tasks)
    text = head_line(fields)
    ref = projected_ref(fields)
    record = {"record_id": "event:" + ref, "kind": "event", "time_utc": fields["ts_utc"],
              "available_utc": fields["ts_utc"], "stored_text": text, "summary": text,
              "meta": {"type": fields["type"], "status": fields["status"], "agent": fields["agent"],
                       "task_id": fields["task_id"], "pr": fields["pr"], "head": fields["head"], "ci": fields["ci"]},
              "provenance": {"source_kind": "projected_bridge_metadata", "event_ref": ref,
                             "ref_kind": "sha256_of_projected_metadata", "canonical_event": False,
                             "tier": {"source_type": "bridge_event"}, "available_utc": fields["ts_utc"]}}
    return record, dropped


def encoder_input(record):
    """What the encoder would embed for this record: exactly the stored text, nothing appended."""
    return record["stored_text"]


def _projection(records, refused, dropped):
    out = {"schema": SCHEMA, "authority": "none", "scope": "shared_metadata_projection_not_gate",
           "accepted": len(records), "refused": dict(sorted(refused.items())), "dropped_fields": dropped,
           "records": records}
    corpus_mod.ws.guard_object(out)
    return out


def project_events(events, approved_tasks=None):
    """Every acceptable event as a record; refusals are counted by reason only; a non-object input refuses whole.

    ``approved_tasks`` is a frozenset of safe-shaped task ids the caller pins from an operator-approved list; with
    none, every task id renders as UNKNOWN."""
    approved = _approved_tasks(approved_tasks)
    records, refused, dropped = [], {}, 0
    for event in events:
        if not isinstance(event, dict):
            raise ProjectionRefused("event_not_object")
        try:
            record, count = project_event(event, approved)
        except ProjectionRefused as exc:
            refused[str(exc)] = refused.get(str(exc), 0) + 1
            continue
        records.append(record)
        dropped += count
    return _projection(records, refused, dropped)


def project_notes(notes, allowlist=None):
    """Memory notes never enter the shared path; a caller-provided hash list is not an authorization."""
    raise ProjectionRefused("notes_excluded")


def write_projection(path, projection):
    """Guard the whole object, stage it in a unique exclusively created file, then rename onto a path that must not
    exist yet; on any refusal nothing new exists at or beside ``path``, and only this call's staging is removed."""
    try:
        corpus_mod.ws.guard_object(projection)
    except corpus_mod.ws.WorkspaceError:
        raise ProjectionRefused("output_guarded") from None
    data = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
    path = Path(path)
    staging = None
    try:
        if path.exists():
            raise ProjectionRefused("output_exists")
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{path.name}.{secrets.token_hex(8)}.staging")
        with open(staging, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise ProjectionRefused("output_exists")
        os.rename(staging, path)  # fails on Windows and POSIX-renames onto nothing: the target was absent just above
    except ProjectionRefused:
        if staging is not None and staging.exists():
            staging.unlink()
        raise
    except OSError:
        if staging is not None and staging.exists():
            staging.unlink()
        raise ProjectionRefused("write_failed") from None
    return hashlib.sha256(data).hexdigest()
