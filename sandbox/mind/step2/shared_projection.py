"""HEX MEMORY STEP 2, shared projection: the default-deny record set every lane may hold in full.

The Q4 memory directory is plain files whose cell metadata carries every stored text, so whatever is projected here
is readable by every lane that can read the memory. This module therefore builds records from APPROVED bridge event
METADATA only (``APPROVED_FIELDS``): time, lane agent, a non-decision event type, a status token, a safe-shaped
task id, and optional PR number, 40-hex head and CI token. Everything else on an event (message, payload, paths,
identities, nonces, digests, unknown keys) is never read, never rendered and never persisted; it is only counted.

The one text a record carries (``stored_text`` = ``summary`` = the encoder input, see ``encoder_input``) is the head
line ``<type> <status> <agent> <task_id> [pr:N] [head:HEX] [ci:TOKEN]`` rebuilt from the approved fields, so the
persisted bytes, the BM25 text and the vector input are one and the same string. A record's reference is the sha256
of its approved fields in canonical JSON (``event:<hex64>``), never a path or a free name.

Default deny: a lane outside ``LANES``, a type outside ``ALLOWED_TYPES`` (every peer-review and decision type is
outside), a sealed-named task, a malformed time, PR, head, CI or status, or any approved field over its byte cap
(counted in UTF-8 bytes, so a 4-byte character costs four) refuses that event whole with one fixed reason; a
path-shaped or oversized task id degrades to ``UNKNOWN`` (RCO1 S6). Memory notes are excluded by default
(``project_notes`` refuses without an operator allowlist of name -> sha256 and refuses on any mismatch). A refused
write leaves no file: the whole output passes the workspace guard before a temp file is renamed into place.

Synthetic only: no real capture, memory, index or model is touched by this module or its tests.
"""

import hashlib
import json
import os
import re
from pathlib import Path

from sandbox.mind.step2 import corpus as corpus_mod

SCHEMA = "wd.bridge-mind-shared-projection.v1"
LANES = frozenset({"codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"})
ALLOWED_TYPES = frozenset({"message", "wake_request", "handoff", "test", "claim", "release", "done", "milestone",
                           "status", "progress"})
# Documented for readers; the allowlist above is what decides. Peer review and decisions never enter the shared set.
EXCLUDED_TYPES = frozenset({"decision", "finding", "rco_pass", "vote", "veto", "changes_requested", "recusal",
                            "build_consensus", "review"})
APPROVED_FIELDS = ("ts_utc", "agent", "type", "status", "task_id", "pr", "head", "ci")
OPTIONAL_FIELDS = ("pr", "head", "ci")
SEALED_MARKERS = ("sealed", "gold", "heldout", "judge")
BYTE_CAPS = {"agent": 40, "type": 32, "status": 32, "task_id": 128, "ci": 32}
HEAD_LINE_BYTES = 320
NOTE_BYTES = 4000
UNKNOWN = "UNKNOWN"

LANE = re.compile(r"[a-z][a-z0-9-]{1,39}")
TYPE = re.compile(r"[a-z][a-z0-9_]{0,31}")
STATUS = re.compile(r"[a-z][a-z0-9_-]{0,31}")
TASK = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")  # no separator, no drive letter, no space: a path never fits
CI = re.compile(r"[a-z][a-z0-9_-]{0,31}")
HEAD = re.compile(r"[0-9a-f]{40}")
NOTE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}")
PR_MAX = 10 ** 9


class ProjectionRefused(ValueError):
    """One fixed reason token; never the offending value."""


def _bytes(text):
    return len(text.encode("utf-8", "surrogatepass"))


def _token(name, value, pattern):
    """A required token field: text, within its byte cap, matching its pattern; else a fixed refusal."""
    if not isinstance(value, str):
        raise ProjectionRefused(f"{name}_invalid")
    if _bytes(value) > BYTE_CAPS[name]:
        raise ProjectionRefused(f"{name}_over_bytes")
    if pattern.fullmatch(value) is None:
        raise ProjectionRefused(f"{name}_invalid")
    return value


def _task(value):
    """The task id in safe shape, else UNKNOWN (a path, a space, a drive letter or an oversized id never passes)."""
    if not isinstance(value, str):
        raise ProjectionRefused("task_id_invalid")
    if _bytes(value) > BYTE_CAPS["task_id"]:
        raise ProjectionRefused("task_id_over_bytes")
    if TASK.fullmatch(value) is None:
        return UNKNOWN
    if any(marker in value for marker in SEALED_MARKERS):
        raise ProjectionRefused("sealed_named_excluded")
    return value


def approved_fields(event):
    """The approved fields of one event, validated; forbidden and unknown keys are counted, never read."""
    if not isinstance(event, dict):
        raise ProjectionRefused("event_not_object")
    dropped = sum(1 for key in event if key not in APPROVED_FIELDS)
    when = corpus_mod.parse_utc(event.get("ts_utc")) if isinstance(event.get("ts_utc"), str) else None
    if when is None:
        raise ProjectionRefused("time_invalid")
    agent = _token("agent", event.get("agent"), LANE)
    if agent not in LANES:
        raise ProjectionRefused("agent_not_lane")
    kind = _token("type", event.get("type"), TYPE)
    if kind not in ALLOWED_TYPES:
        raise ProjectionRefused("type_not_allowed")
    status = event.get("status")
    status = "none" if status == "" else _token("status", status, STATUS)
    task = _task(event.get("task_id"))
    pr = event.get("pr")
    if pr is not None and (type(pr) is not int or pr < 1 or pr > PR_MAX):
        raise ProjectionRefused("pr_invalid")
    head = event.get("head")
    if head is not None and (not isinstance(head, str) or HEAD.fullmatch(head) is None):
        raise ProjectionRefused("head_invalid")
    ci = event.get("ci")
    if ci is not None:
        ci = _token("ci", ci, CI)
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


def event_ref(fields):
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def project_event(event):
    """One shared record from one event, or ProjectionRefused; nothing partial is returned."""
    fields, dropped = approved_fields(event)
    text = head_line(fields)
    ref = event_ref(fields)
    record = {"record_id": "event:" + ref, "kind": "event", "time_utc": fields["ts_utc"],
              "available_utc": fields["ts_utc"], "stored_text": text, "summary": text,
              "meta": {"type": fields["type"], "status": fields["status"], "agent": fields["agent"],
                       "task_id": fields["task_id"], "pr": fields["pr"], "head": fields["head"], "ci": fields["ci"]},
              "provenance": {"source_kind": "bridge_event_metadata", "event_ref": ref,
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


def project_events(events):
    """Every acceptable event as a record; refusals are counted by reason only; a non-object input refuses whole."""
    records, refused, dropped = [], {}, 0
    for event in events:
        if not isinstance(event, dict):
            raise ProjectionRefused("event_not_object")
        try:
            record, count = project_event(event)
        except ProjectionRefused as exc:
            refused[str(exc)] = refused.get(str(exc), 0) + 1
            continue
        records.append(record)
        dropped += count
    return _projection(records, refused, dropped)


def project_notes(notes, allowlist=None):
    """Memory notes are excluded by default; with an operator allowlist {name: sha256(text)} each note must match."""
    if allowlist is None:
        raise ProjectionRefused("notes_excluded_by_default")
    if not isinstance(allowlist, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in
                                                   allowlist.items()):
        raise ProjectionRefused("note_allowlist_invalid")
    records = []
    for note in notes:
        if not isinstance(note, dict) or not isinstance(note.get("name"), str) or not isinstance(note.get("text"), str):
            raise ProjectionRefused("note_not_object")
        name, text = note["name"], note["text"]
        if NOTE_NAME.fullmatch(name) is None or name not in allowlist:
            raise ProjectionRefused("note_not_allowlisted")
        digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
        if digest != allowlist[name]:
            raise ProjectionRefused("note_allowlist_mismatch")
        if _bytes(text) > NOTE_BYTES or not text.strip():
            raise ProjectionRefused("note_over_bytes")
        records.append({"record_id": "lesson:" + digest, "kind": "lesson", "time_utc": None, "available_utc": None,
                        "stored_text": text, "summary": text,
                        "meta": {"type": "lesson", "status": "allowlisted", "agent": None, "task_id": None,
                                 "pr": None, "head": None, "ci": None},
                        "provenance": {"source_kind": "memory_note_allowlisted", "event_ref": digest,
                                       "tier": {"source_type": "memory_note"}, "available_utc": None}})
    return _projection(records, {}, 0)


def write_projection(path, projection):
    """Guard the whole object, then write it atomically; on refusal nothing exists at or beside ``path``."""
    try:
        corpus_mod.ws.guard_object(projection)
    except corpus_mod.ws.WorkspaceError:
        raise ProjectionRefused("output_guarded") from None
    data = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        with open(temp, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError:
        if temp.exists():
            temp.unlink()
        raise ProjectionRefused("write_failed") from None
    return hashlib.sha256(data).hexdigest()
