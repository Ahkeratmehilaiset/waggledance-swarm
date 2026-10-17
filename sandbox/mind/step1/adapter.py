"""Hex-Mind step 1b adapter: a stable reader identity and cursor, so the Mind's incremental fold engages.

Sandbox only (fable-5, 2026-10-14). Lead diagnostic 8BECE4A1: the shadow adapter named each capture's own sha256 as
the log identity ("capture-window:<sha>"), so a new observation time alone forced rebuild_required and the
incremental fold never ran. Here the identity never holds a capture hash, an observation time or an event hash.

Identity and progress, by what the input actually carries:

* reader cursor (the pinned compact reader's ``cursor``: "bc1." + base64url JSON {file_identity, generation,
  offset}): identity = (file_identity, generation), progress = offset. A delta page (scope
  ``position_delta_batch_not_complete_history``) continues the checkpoint only when it was read exactly after the
  checkpoint cursor. A tail window (``bounded_snapshot_not_complete_history``) continues it only when its end
  offset did not go back and it still holds the checkpoint's last accepted rows in order.
* no reader cursor (every capture written so far: capture.json and capture-record.json keep the rows, the
  observation time and the repo, never the reader's cursor, file identity, generation or offset): identity is
  DECLARED from the capture record (repo of the pinned compact reader, generation "not-captured"), progress is the
  accepted row count, and a window continues the checkpoint only when it holds the checkpoint's last accepted rows
  in order (content overlap). Gap: a replaced log is caught only when its window lacks those rows.

A window or page that does not cover the checkpoint contiguously gives coverage UNKNOWN and rebuild_required with a
reason; it is never folded. A page already applied (a reader cursor at or before the checkpoint, or a window whose
every ref was already seen) changes nothing and writes nothing. The fold is the core's own: ``full_rebuild`` for
the origin window, ``apply_incremental`` for each page (the core dedupes rows by event ref); the core cursor counts
accepted rows since the origin, the reader offset is kept beside it, and nothing before the origin is claimed.

Checkpoint, one commit point: the core's ``write_checkpoint`` (reducer state, fact candidates and its validated
display snapshot) goes to a new step file, then ``adapter.json`` (identity, reader cursor, anchor refs, code and
schema versions, input-page hashes and the step file's sha256) replaces the previous one with the core's atomic
writer (temp file, fsync, replace). A crash before that replace leaves the previous pointer and step file in force,
and the same pages give the same state again. A digest, version, schema or binding mismatch refuses the
checkpoint; it is never used. One writer per state directory.

Outputs carry refs, hashes, counts, sizes and times only. No bridge read or write, no gate code, no network, no
wall clock in the fold (time is each page's observation time).
"""
from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import wd_bridge_mind as core  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402
from waggledance.core.bridge_log_reader import parse_bridge_json_object  # noqa: E402

SCHEMA = "wd.hexmind-adapter-checkpoint.v1"
VERSION = "1b.1"
WINDOW_SCOPE = "bounded_snapshot_not_complete_history"
DELTA_SCOPE = "position_delta_batch_not_complete_history"
CAPTURE_SCHEMA = "wd.bridge-mind-capture.v1"
CAPTURE_RECORD_SCHEMA = "wd.bridge-mind-capture-record.v1"
PINNED_READER = "Read-AgentBridge.ps1 -Compact -EventId"
NOT_CAPTURED = "not-captured"
ANCHOR = 8  # the last accepted refs a window must still hold, in order
RECENT_PAGES = 32  # accepted page records kept verbatim; every older one lives on in the chain digest
POINTER = "adapter.json"
PROOFS = ("reader_cursor", "content_overlap_reader_identity", "content_overlap_declared_identity")  # strongest first
RECORD_KEYS = frozenset({"schema", "authority", "scope", "code", "identity", "reader_cursor", "anchor", "core_cursor",
                         "coverage", "step", "as_of_utc", "observed_utc", "pages", "core_checkpoint", "record_sha256"})
_CURSOR = re.compile(r"bc1\.[A-Za-z0-9_-]{1,2044}\Z")
_GENERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")  # the reader's alphabet, as a core token
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}\Z")  # the core's identity token
_STEP_FILE = re.compile(r"core-[0-9]{8}-[0-9a-f]{16}\.json\Z")
_PAGES_ORIGIN = hashlib.sha256(b"wd.hexmind-adapter-pages.v1").hexdigest()
_SAFE_CORE = core.BLOCKED_REASONS | {"canonical_json_invalid", "explicit_utc_time_required", "as_of_precedes_event",
                                     "projection_policy_invalid", "resumable_state_required"}  # as the core's pager


class AdapterError(ValueError):
    """Fixed reason codes only; never input content."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


CODE = {"adapter_version": VERSION, "adapter_sha256": sha256(Path(__file__).read_bytes()),
        "core_sha256": sha256(Path(core.__file__).read_bytes()), "checkpoint_schema": SCHEMA,
        "core_state_schema": "wd.bridge-mind-state.v1", "core_checkpoint_schema": "wd.bridge-mind-checkpoint.v1"}


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _utc(value) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        raise AdapterError("observation_time_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise AdapterError("observation_time_invalid")
    return parsed


# -- the reader cursor ---------------------------------------------------------------------------------------------

def encode_cursor(file_identity: str, generation: str | None, offset: int) -> str:
    """The compact reader's own token: "bc1." + unpadded base64url of the sorted compact JSON cursor."""
    body = _json({"file_identity": file_identity, "generation": generation, "offset": offset})
    return "bc1." + base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(token) -> dict:
    """{file_identity, generation, offset} of a reader cursor; refused unless it is the reader's canonical token."""
    if not isinstance(token, str) or not _CURSOR.fullmatch(token):
        raise AdapterError("reader_cursor_invalid")
    body = token[4:]
    try:
        value = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8"))
    except (ValueError, RecursionError):  # binascii, Unicode and JSON errors are all ValueErrors
        raise AdapterError("reader_cursor_invalid") from None
    if not isinstance(value, dict) or set(value) != {"file_identity", "generation", "offset"}:
        raise AdapterError("reader_cursor_invalid")
    identity, generation, offset = value["file_identity"], value["generation"], value["offset"]
    if (not isinstance(identity, str) or not _TOKEN.fullmatch("bridge-log:" + identity)
            or (generation is not None and (not isinstance(generation, str) or not _GENERATION.fullmatch(generation)))
            or type(offset) is not int or offset < 0):
        raise AdapterError("reader_cursor_invalid")
    if encode_cursor(identity, generation, offset) != token:
        raise AdapterError("reader_cursor_not_canonical")
    return value


# -- pages ---------------------------------------------------------------------------------------------------------

def _no_constant(name):
    raise ValueError("json_constant_refused")


def capture_page(capture_bytes: bytes, record_bytes: bytes | None) -> dict:
    """A wd.bridge-mind-capture.v1 window and its capture record: declared identity, no reader cursor.

    The capture hash and observation time are kept as provenance only; the identity is the record's repo."""
    try:
        capture = json.loads(capture_bytes.decode("utf-8"), parse_constant=_no_constant)
    except (ValueError, RecursionError):
        raise AdapterError("capture_unreadable") from None
    if (not isinstance(capture, dict) or capture.get("schema") != CAPTURE_SCHEMA
            or capture.get("reader") != PINNED_READER or not isinstance(capture.get("events"), list)
            or not all(isinstance(row, dict) for row in capture["events"])):
        raise AdapterError("capture_shape_invalid")
    _utc(capture.get("observed_utc"))
    if record_bytes is None:
        raise AdapterError("capture_record_required")  # the declared identity is the record's repo
    try:
        record = parse_bridge_json_object(record_bytes.decode("utf-8"))
    except (ValueError, RecursionError):
        raise AdapterError("capture_record_unreadable") from None
    files, bridge, repo = record.get("files"), record.get("bridge"), record.get("repo")
    if (record.get("schema") != CAPTURE_RECORD_SCHEMA or not isinstance(files, dict) or not isinstance(bridge, dict)
            or files.get("capture.json") != sha256(capture_bytes) or bridge.get("events") != len(capture["events"])
            or bridge.get("observed_utc") != capture["observed_utc"]
            or not isinstance(repo, str) or not _TOKEN.fullmatch("declared-bridge-log:" + repo)):
        raise AdapterError("capture_record_mismatch")
    return {"identity": {"kind": "declared_no_reader_cursor", "log_identity": "declared-bridge-log:" + repo,
                         "generation": NOT_CAPTURED},
            "after": None, "end": None, "token": None, "rows": capture["events"],
            "observed_utc": capture["observed_utc"], "input_sha256": sha256(capture_bytes)}


def reader_page(view: dict, rows: list, *, observed_utc: str, after: str = "") -> dict:
    """The pinned compact reader's own list output (cursor, reader, scope, refs) plus the detail row of each ref.

    ``after`` is the cursor the read was made after ("" for a tail window)."""
    if not isinstance(view, dict) or view.get("schema") != "wd.bridge.compact-view.v1" or view.get("authority") != "none":
        raise AdapterError("reader_view_invalid")
    end = decode_cursor(view.get("cursor"))
    start = decode_cursor(after) if after else None
    if view.get("scope") != (DELTA_SCOPE if after else WINDOW_SCOPE):
        raise AdapterError("reader_scope_invalid")
    if start is not None and ((start["file_identity"], start["generation"]) != (end["file_identity"], end["generation"])
                              or end["offset"] < start["offset"]):
        raise AdapterError("reader_cursor_pair_invalid")
    stats = view.get("reader")
    if (not isinstance(stats, dict) or type(stats.get("position")) is not int or stats["position"] != end["offset"]
            or type(stats.get("snapshot_length")) is not int or stats["snapshot_length"] < end["offset"]
            or stats.get("unconsumed_bytes") != stats["snapshot_length"] - end["offset"]):
        raise AdapterError("reader_stats_invalid")
    items = view.get("events")
    if not isinstance(items, list) or not isinstance(rows, list) or len(items) != len(rows):
        raise AdapterError("reader_rows_invalid")
    for item, row in zip(items, rows):
        if not isinstance(item, dict) or not isinstance(row, dict) or item.get("ref") != event_id(row):
            raise AdapterError("reader_row_ref_mismatch")
    _utc(observed_utc)
    refs = [item["ref"] for item in items]
    return {"identity": {"kind": "reader_cursor", "log_identity": "bridge-log:" + end["file_identity"],
                         "generation": end["generation"]},
            "after": start, "end": end, "token": view["cursor"], "rows": list(rows), "observed_utc": observed_utc,
            "input_sha256": sha256(_json({"after": after, "cursor": view["cursor"], "refs": refs}).encode("utf-8"))}


# -- plan: what a page means for a checkpoint ----------------------------------------------------------------------

def _decision(action, reason=None, coverage="UNKNOWN", start=0, proof=None) -> dict:
    return {"action": action, "reason": reason, "coverage": coverage, "start": start, "proof": proof}


def _identity_change(old: dict, new: dict) -> str | None:
    if old["kind"] != new["kind"]:
        return "identity_kind_changed"
    if old["log_identity"] != new["log_identity"]:
        return "file_identity_changed" if new["kind"] == "reader_cursor" else "log_identity_changed"
    if old["generation"] != new["generation"]:
        return "generation_changed"
    return None


def _after_anchor(rows: list, anchor: list) -> int | None:
    """Index just after the checkpoint's last accepted row in this window, scanning from the end (cheap when few rows
    are new). None: the window does not hold that row. -1: it does, but not after the other anchor refs in order."""
    for index in range(len(rows) - 1, -1, -1):
        if event_id(rows[index]) == anchor[-1]:
            run = anchor[-min(len(anchor), index + 1):]
            return index + 1 if [event_id(row) for row in rows[index + 1 - len(run):index + 1]] == run else -1
    return None


def plan(checkpoint, page: dict) -> dict:
    """origin, advance, already_applied, refused or rebuild_required (with a reason); reads and writes nothing."""
    if checkpoint is None:
        return _decision("origin", coverage="origin_window")
    state, record = checkpoint
    changed = _identity_change(record["identity"], page["identity"])
    if changed:
        return _decision("rebuild_required", changed)
    accepted = record["reader_cursor"]
    if page["after"] is not None:  # a delta page, read after a reader cursor
        start, end, mark = page["after"]["offset"], page["end"]["offset"], accepted["offset"]
        if start == mark:
            return _decision("advance", coverage="contiguous", proof="reader_cursor")
        if end <= mark:
            return _decision("already_applied", "page_at_or_before_checkpoint", "already_applied")
        if start > mark:
            return _decision("rebuild_required", "cursor_gap")
        return _decision("refused", "page_overlaps_checkpoint")  # re-read from the checkpoint cursor
    if accepted is not None and page["end"]["offset"] < accepted["offset"]:
        return _decision("already_applied", "window_before_checkpoint", "already_applied")
    proof = "content_overlap_declared_identity" if accepted is None else "content_overlap_reader_identity"
    if not record["anchor"]:
        return _decision("advance", None, "contiguous", 0, proof) if not page["rows"] else \
            _decision("rebuild_required", "no_anchor_rows")
    position = _after_anchor(page["rows"], record["anchor"])
    if position is None:
        if page["rows"] and all(event_id(row) in state.seen for row in page["rows"]):
            return _decision("already_applied", "window_refs_all_seen", "already_applied")
        return _decision("rebuild_required", "window_does_not_cover_checkpoint")
    if position < 0:
        return _decision("rebuild_required", "window_overlap_mismatch")
    return _decision("advance", None, "contiguous", position, proof)


# -- fold: the core's own reducer ----------------------------------------------------------------------------------

def _source(identity: dict, start: int, count: int) -> dict:
    return {"log_identity": identity["log_identity"], "generation": identity["generation"], "cursor_start": start,
            "cursor_end": start + count, "event_count": count, "coverage": "complete"}


def fold(base, page: dict, decision: dict) -> core.MindState:
    """full_rebuild for an origin, apply_incremental onto ``base`` (state, record) otherwise."""
    envelopes = [{"ref": event_id(row), "event": row} for row in page["rows"][decision["start"]:]]
    if base is None:
        return core.full_rebuild(envelopes, source=_source(page["identity"], 0, len(envelopes)),
                                 as_of_utc=page["observed_utc"])
    state = base[0]
    if not envelopes:
        return state
    return core.apply_incremental(state, envelopes, source=_source(page["identity"], state.source["cursor_end"],
                                                                   len(envelopes)), as_of_utc=page["observed_utc"])


# -- checkpoint: core step file + atomic pointer -------------------------------------------------------------------

def _record_digest(record: dict) -> str:
    return sha256(core.canonical_bytes({key: value for key, value in record.items() if key != "record_sha256"}))


def _bound(state: core.MindState, record: dict) -> bool:
    """The pointer and the core state describe the same accepted stream."""
    anchor, identity, reader = record["anchor"], record["identity"], record["reader_cursor"]
    if (state.source["log_identity"] != identity["log_identity"] or state.source["generation"] != identity["generation"]
            or state.source["cursor_end"] != record["core_cursor"]["end"]
            or not isinstance(anchor, list) or len(anchor) > ANCHOR
            or any(not isinstance(ref, str) or ref not in state.seen for ref in anchor)
            or (bool(anchor) and anchor[-1] != state.last_ref)):
        return False
    if identity["kind"] != "reader_cursor":
        return reader is None
    try:
        cursor = decode_cursor(reader["token"])
    except (AdapterError, TypeError, KeyError):
        return False
    return (cursor["offset"] == reader["offset"] and "bridge-log:" + cursor["file_identity"] == identity["log_identity"]
            and cursor["generation"] == identity["generation"])


def load(directory: Path, audit_root: Path):
    """(state, record) of the checkpoint in force, None when there is none; AdapterError when it cannot be trusted."""
    pointer = Path(directory) / POINTER
    if not pointer.exists():
        return None
    try:
        record = parse_bridge_json_object(pointer.read_bytes().decode("utf-8"))
    except (OSError, ValueError, RecursionError):
        raise AdapterError("checkpoint_pointer_unreadable") from None
    if set(record) != RECORD_KEYS or record["schema"] != SCHEMA:
        raise AdapterError("checkpoint_schema_invalid")
    try:
        if _record_digest(record) != record["record_sha256"]:
            raise AdapterError("checkpoint_digest_mismatch")
    except core.ProjectionError:
        raise AdapterError("checkpoint_digest_mismatch") from None
    if record["code"] != CODE:
        raise AdapterError("checkpoint_code_changed")  # another fold may give another state: rebuild
    step_file = record["core_checkpoint"].get("file") if isinstance(record["core_checkpoint"], dict) else None
    if not isinstance(step_file, str) or not _STEP_FILE.fullmatch(step_file):
        raise AdapterError("checkpoint_schema_invalid")
    try:
        data = (Path(directory) / step_file).read_bytes()
    except OSError:
        raise AdapterError("checkpoint_core_missing") from None
    if sha256(data) != record["core_checkpoint"].get("sha256"):
        raise AdapterError("checkpoint_core_mismatch")
    try:
        state = core.read_checkpoint(Path(directory) / step_file, audit_root=audit_root)
    except core.ProjectionError:
        raise AdapterError("checkpoint_core_refused") from None
    try:
        bound = _bound(state, record)
    except (TypeError, KeyError, AttributeError):
        bound = False
    if not bound:
        raise AdapterError("checkpoint_binding_mismatch")
    return state, record


def _tidy(directory: Path, keep: set) -> None:
    """Orphans of an interrupted commit (core temp files, unreferenced step files) go; the pointer never names them."""
    for path in Path(directory).iterdir():
        if (path.name.startswith(".mind-") and path.name.endswith(".tmp")) or \
                (_STEP_FILE.fullmatch(path.name) and path.name not in keep):
            path.unlink()


def commit(directory: Path, audit_root: Path, state: core.MindState, record: dict, *, write_state: bool) -> dict:
    """Write the core step file (when the state changed), then replace adapter.json: the one commit point."""
    sizes = {"core_bytes": 0, "pointer_bytes": 0}
    previous = record["core_checkpoint"]["file"] if record["core_checkpoint"] else None
    try:
        if write_state:
            name = "core-%08d-%s.json" % (record["step"], record["pages"]["recent"][-1]["page_sha256"][:16])
            core.write_checkpoint(state, Path(directory) / name, as_of_utc=record["as_of_utc"], audit_root=audit_root)
            data = (Path(directory) / name).read_bytes()
            record["core_checkpoint"] = {"file": name, "sha256": sha256(data), "bytes": len(data)}
            sizes["core_bytes"] = len(data)
        record["record_sha256"] = _record_digest(record)
        core._write_publication(record, Path(directory) / POINTER, audit_root)  # the core's temp + fsync + replace
    except (core.ProjectionError, OSError):
        raise AdapterError("checkpoint_write_failed") from None
    sizes["pointer_bytes"] = len(core.canonical_bytes(record))
    _tidy(directory, {record["core_checkpoint"]["file"], previous})
    return sizes


def _next_record(base: dict | None, previous: dict | None, page: dict, decision: dict, state: core.MindState,
                 refs: list) -> dict:
    """The pointer after this page. ``base`` is the record the fold continued (None for an origin), ``previous`` the
    last readable one (it only numbers the step and names the step file kept beside the new one)."""
    number = 0 if previous is None else previous["step"] + 1
    reader = None if page["end"] is None else [None if page["after"] is None else page["after"]["offset"],
                                               page["end"]["offset"]]
    start = state.source["cursor_end"] - len(refs)
    page_sha = sha256(_json({"identity": page["identity"], "reader": reader, "core_cursor": [start, start + len(refs)],
                             "refs": refs}).encode("utf-8"))
    pages = {"accepted": 0, "chain_sha256": _PAGES_ORIGIN, "recent": []} if base is None else base["pages"]
    entry = {"step": number, "page_sha256": page_sha, "input_sha256": page["input_sha256"], "rows": len(refs),
             "first_ref": refs[0] if refs else None, "last_ref": refs[-1] if refs else None,
             "core_cursor": [start, start + len(refs)], "reader": reader, "observed_utc": page["observed_utc"]}
    if base is None:
        coverage = {"before_origin": "UNKNOWN", "origin_scope": DELTA_SCOPE if page["after"] else WINDOW_SCOPE,
                    "since_origin": "origin_only", "rebuilt_after": decision["reason"]}
    else:
        weakest = base["coverage"]["since_origin"]
        if weakest == "origin_only" or PROOFS.index(decision["proof"]) > PROOFS.index(weakest):
            weakest = decision["proof"]
        coverage = dict(base["coverage"], since_origin=weakest)
    return {"schema": SCHEMA, "authority": "none", "scope": "sandbox_projection_not_gate", "code": CODE,
            "identity": page["identity"],
            "reader_cursor": None if page["end"] is None else {"token": page["token"], "offset": page["end"]["offset"]},
            "anchor": (([] if base is None else base["anchor"]) + refs)[-ANCHOR:],
            "core_cursor": {"unit": "accepted_rows_since_origin", "end": state.source["cursor_end"]},
            "coverage": coverage, "step": number,
            "as_of_utc": page["observed_utc"] if base is None or refs else base["as_of_utc"],
            "observed_utc": page["observed_utc"],
            "pages": {"accepted": pages["accepted"] + 1,
                      "chain_sha256": sha256((pages["chain_sha256"] + page_sha).encode("ascii")),
                      "recent": (pages["recent"] + [entry])[-RECENT_PAGES:]},
            "core_checkpoint": None if previous is None else previous["core_checkpoint"], "record_sha256": None}


def _ms(marks: list) -> dict:
    out = {name: round((b - a) * 1000, 3) for name, a, b in zip(("load", "plan", "fold", "commit"), marks, marks[1:])}
    out["total"] = round((marks[-1] - marks[0]) * 1000, 3)
    return out


def step(directory: Path, audit_root: Path, page: dict, *, rebuild: bool = False) -> dict:
    """One cycle: load the checkpoint, plan, fold, commit. The receipt carries refs, hashes, counts, sizes and ms.

    ``rebuild`` folds the page as a new origin when the checkpoint cannot continue (gap, identity change, refused
    checkpoint); without it such a page is only reported and nothing is written."""
    clock = time.perf_counter
    marks = [clock()]
    Path(directory).mkdir(parents=True, exist_ok=True)
    try:
        previous, refused = load(directory, audit_root), None
    except AdapterError as exc:
        previous, refused = None, "checkpoint_refused:" + str(exc)
    marks.append(clock())
    decision = _decision("rebuild_required", refused) if refused else plan(previous, page)
    base = previous
    if decision["action"] == "rebuild_required" and rebuild:
        decision, base = _decision("origin", decision["reason"], "origin_window"), None
    rows = page["rows"][decision["start"]:]
    if decision["action"] == "advance":
        accepted = previous[1]["reader_cursor"]
        moved = accepted is not None and page["end"]["offset"] > accepted["offset"]
        if not rows and not moved:
            decision = dict(decision, action="no_change", reason="no_new_rows")
        elif rows and _utc(page["observed_utc"]) < _utc(previous[1]["observed_utc"]):
            decision = dict(decision, action="refused", reason="observation_time_regressed")
    receipt = {"action": decision["action"], "reason": decision["reason"], "coverage": decision["coverage"],
               "proof": decision["proof"], "identity_kind": page["identity"]["kind"],
               "input_sha256": page["input_sha256"], "observed_utc": page["observed_utc"],
               "checkpoint_step": None if previous is None else previous[1]["step"], "step": None, "rows_folded": 0,
               "duplicates_folded": 0, "core_cursor": None,
               "reader_offset": None if page["end"] is None else page["end"]["offset"], "page_sha256": None,
               "core_checkpoint_sha256": None, "written": {"core_bytes": 0, "pointer_bytes": 0}}
    marks.append(clock())
    if decision["action"] not in ("origin", "advance"):
        return dict(receipt, ms=_ms(marks))
    try:
        state = fold(base, page, decision)
    except core.ProjectionError as exc:
        reason = str(exc) if str(exc) in _SAFE_CORE else "projection_refused"
        return dict(receipt, action="refused", reason="core:" + reason, ms=_ms(marks))
    marks.append(clock())
    refs = [event_id(row) for row in rows]
    record = _next_record(None if base is None else base[1], None if previous is None else previous[1], page,
                          decision, state, refs)
    written = commit(directory, audit_root, state, record, write_state=bool(refs) or base is None)
    marks.append(clock())
    last = record["pages"]["recent"][-1]
    return dict(receipt, step=record["step"], rows_folded=len(refs),
                duplicates_folded=state.duplicates - (0 if base is None else base[0].duplicates),
                core_cursor=last["core_cursor"], page_sha256=last["page_sha256"],
                core_checkpoint_sha256=record["core_checkpoint"]["sha256"], written=written, ms=_ms(marks))
