"""HEX MEMORY STEP 2, synthetic text memory: a default-OFF candidate for a useful shared text memory.

Candidate only (Lead request lead-mind-useful-text-synthetic-20261107-v1). It brings back the original step-2 event
text rule of 6cf81dbb (``corpus.py``: an event that says something becomes one record whose text is its message, cut
to a cap and kept as the longest whole-line prefix that passes the privacy guard) on top of the existing reviewed
pieces, and it is restricted to SYNTHETIC input:

* every function is OFF unless ``enabled is True`` and returns ``{"status": "OFF"}`` without reading anything;
* there is no source path, bridge reader or file input: events are in-memory dicts from the caller, and ANY event
  whose task id is not ``synthetic-<slug>`` refuses the whole call (``live_input_refused``), so no live bridge message
  can be indexed through this module; this is not an authorization to index live messages;
* metadata comes from the existing ``shared_projection.approved_fields`` with ``exclude_review_signal=True``: review
  lanes and PR-branch task ids refuse, and only the existing closed type list passes (decision, finding, rco_pass,
  vote, veto and the other review types never do); claim, release and wake bookkeeping and empty messages are
  skipped as in 6cf81dbb; payload, paths, write scope, cwd, identities and every other key are never read;
* the stored text is the existing head line, a newline and the message: whole lines only, at most ``TEXT_CAP``
  characters of message, ending before the first line that matches a refusal rule (workspace guarded words, a drive,
  UNC, home or profile path, an environment variable path, a credential word or token prefix, sealed, gold, heldout
  or judge, or a private tool directory); a record with no line left is refused (``text_guarded``) and counted;
* the memory is built with the existing ``shared_index_builder`` cell and commit primitives and read with the
  existing ``memory.Memory`` and ``q4.Q4``; it carries its own scope and descriptor schema, so the production Q4
  reader, which pins the shared scope and descriptor schema, does not accept it. The cell receipts are written by
  the existing builder primitive and carry that builder's own receipt scope token;
* an answer cites each result by its record reference and returns the record's stored text from the projection
  that ``memory.json`` binds (cited retrieval); the situation text is never stored or returned.

A reference is the sha256 of the record's projected fields and stored text, not a canonical bridge event id; it
proves which synthetic record answered, not who wrote it. The refusal rules are a review surface, not a proof that
free text is safe. No policy, live data, launcher, installer or production path is touched.
"""
import hashlib
import json
import os
import re
from pathlib import Path

from sandbox.mind.step2 import corpus as corpus_mod
from sandbox.mind.step2 import shared_projection as sp

SCHEMA = "wd.mind-synthetic-text-projection.v1"
SCOPE = "wd.mind-synthetic-text-events.v1"
DESCRIPTOR_SCHEMA = "wd.mind-synthetic-text-descriptor.v1"
TEXT_CAP = 4000  # == corpus.py TEXT_CAP at 6cf81dbb
SYNTHETIC_TASK = re.compile(r"synthetic-[a-z0-9][a-z0-9/._-]{0,62}\Z")
BOOKKEEPING = frozenset({"claim", "release", "wake_request"})  # the 6cf81dbb skip rule within the allowed types
PROJECTION_KEYS = frozenset({"schema", "authority", "scope", "synthetic", "accepted", "refused", "records"})
RECORD_KEYS = frozenset({"record_id", "kind", "time_utc", "available_utc", "stored_text", "summary", "meta",
                         "provenance"})
META_KEYS = frozenset({"type", "status", "agent", "task_id", "text_cut"})
MAX_EVENTS = 4096
REFUSE_LINE = (
    re.compile(r"(?i)(?<![a-z0-9])[a-z]:[\\/]"),  # a drive path
    re.compile(r"\\\\"),  # a UNC path or an escaped backslash pair
    re.compile(r"(?i)(?:^|[\\/])(?:home|users)[\\/]"),  # a home or profile directory
    re.compile(r"%[A-Za-z_][A-Za-z0-9_]*%"),  # an environment variable path
    re.compile(r"(?i)\b(?:password|passwd|secret|token|credential|bearer)s?\b"),
    re.compile(r"(?i)\bapi[_ -]?key"),
    re.compile(r"(?i)\b(?:ghp_|gho_|github_pat_|sk-|xox[abp]-)"),
    re.compile(r"(?i)\b(?:sealed|gold|heldout|held-out|judge)\b"),
    re.compile(r"(?i)\.(?:codex-audit|agent-bridge|ssh|aws)\b"),
)


class TextRefused(ValueError):
    """A fixed refusal token; never any input text."""


def _enabled(enabled):
    if enabled is False:
        return False
    if enabled is not True:
        raise TextRefused("enabled_invalid")
    return True


def _line_ok(line):
    if any(rule.search(line) for rule in REFUSE_LINE):
        return False
    try:
        corpus_mod.ws.guard_object(line)
    except corpus_mod.ws.WorkspaceError:
        return False
    return True


def kept_text(message):
    """(kept, cut): the longest whole-line prefix of the message within ``TEXT_CAP`` whose every line passes."""
    lines, kept, size = message.split("\n"), [], 0
    for line in lines:
        added = len(line) + (1 if kept else 0)
        if size + added > TEXT_CAP or not _line_ok(line):
            break
        kept.append(line)
        size += added
    while kept and not kept[-1].strip():
        kept.pop()
    text = "\n".join(kept)
    return text, text != message.rstrip()


def _head_fields(meta, time_utc):
    return {"ts_utc": time_utc, "agent": meta["agent"], "type": meta["type"], "status": meta["status"],
            "task_id": meta["task_id"], "pr": None, "head": None, "ci": None}


def record_ref(record):
    """sha256 of the record's projected fields and its stored text (canonical JSON)."""
    fields = _head_fields(record["meta"], record["time_utc"])
    raw = json.dumps({"fields": fields, "text": record["stored_text"]}, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _record(fields, body, cut):
    text = sp.head_line(fields) + "\n" + body
    available = sp._available(fields["ts_utc"])
    record = {"record_id": None, "kind": "event", "time_utc": fields["ts_utc"], "available_utc": available,
              "stored_text": text, "summary": text,
              "meta": {"type": fields["type"], "status": fields["status"], "agent": fields["agent"],
                       "task_id": fields["task_id"], "text_cut": cut},
              "provenance": None}
    ref = record_ref(record)
    record["record_id"] = "text:" + ref
    record["provenance"] = {"source_kind": "synthetic_projected_text", "event_ref": ref,
                            "ref_kind": "sha256_of_projected_metadata", "canonical_event": False,
                            "tier": {"source_type": "bridge_event"}, "available_utc": available}
    return record


def project_synthetic_text(events, *, enabled=False):
    """The synthetic text projection of in-memory events, or ``{"status": "OFF"}``."""
    if not _enabled(enabled):
        return {"status": "OFF"}
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise TextRefused("events_invalid")
    for event in events:
        if (not isinstance(event, dict) or not isinstance(event.get("task_id"), str)
                or SYNTHETIC_TASK.fullmatch(event["task_id"]) is None):
            raise TextRefused("live_input_refused")
    approved = frozenset(event["task_id"] for event in events)
    records, refused = [], {}

    def refuse(token):
        refused[token] = refused.get(token, 0) + 1

    for event in events:
        try:
            fields, _dropped = sp.approved_fields(event, approved, False, None, True)
        except sp.ProjectionRefused as refusal:
            refuse(str(refusal))
            continue
        if fields["type"] in BOOKKEEPING:
            refuse("bookkeeping_skipped")
            continue
        message = event.get("message")
        if not isinstance(message, str):
            refuse("message_invalid")
            continue
        if not message.strip():
            refuse("message_empty")
            continue
        body, cut = kept_text(message)
        if not body.strip():
            refuse("text_guarded")
            continue
        records.append(_record(fields, body, cut))
    out = {"schema": SCHEMA, "authority": "none", "scope": SCOPE, "synthetic": True, "accepted": len(records),
           "refused": dict(sorted(refused.items())), "records": records}
    corpus_mod.ws.guard_object(out)
    return out


def validate_projection(projection):
    """Re-derive every record of a synthetic text projection; ``projection_invalid`` on any difference."""
    try:
        if (not isinstance(projection, dict) or set(projection) != PROJECTION_KEYS or projection["schema"] != SCHEMA
                or projection["scope"] != SCOPE or projection["authority"] != "none"
                or projection["synthetic"] is not True or not isinstance(projection["records"], list)
                or projection["accepted"] != len(projection["records"]) or len(projection["records"]) > MAX_EVENTS):
            raise ValueError()
        for record in projection["records"]:
            meta = record["meta"]
            if (set(record) != RECORD_KEYS or set(meta) != META_KEYS or type(meta["text_cut"]) is not bool
                    or not isinstance(meta["task_id"], str) or SYNTHETIC_TASK.fullmatch(meta["task_id"]) is None
                    or "/" in meta["task_id"] or meta["type"] not in sp.ALLOWED_TYPES or meta["type"] in BOOKKEEPING
                    or meta["status"] not in sp.ALLOWED_STATUS | {"UNKNOWN"} or meta["agent"] not in sp.LANES
                    or meta["agent"] in sp.REVIEW_LANES):
                raise ValueError()
            fields = _head_fields(meta, record["time_utc"])
            head = sp.head_line(fields) + "\n"
            body = record["stored_text"][len(head):]
            expected = _record(fields, body, meta["text_cut"])
            if (record != expected or not record["stored_text"].startswith(head) or not body.strip()
                    or kept_text(body)[0] != body):
                raise ValueError()
        corpus_mod.ws.guard_object(projection)
    except (ValueError, KeyError, TypeError, AttributeError, sp.ProjectionRefused, corpus_mod.ws.WorkspaceError):
        raise TextRefused("projection_invalid") from None
    return projection


def build_synthetic_text_memory(projection, encode, identity, dim, root, name, *, enabled=False):
    """One frozen synthetic text memory ``root/name`` from a validated projection, or ``{"status": "OFF"}``."""
    if not _enabled(enabled):
        return {"status": "OFF"}
    validate_projection(projection)
    from sandbox.mind.step2 import cell as cell_mod
    from sandbox.mind.step2 import memory as memory_mod
    from sandbox.mind.step2 import shared_index_builder as sib
    from waggledance.core import hex_cell_topology as topology_mod
    import numpy as np
    try:
        encoder = sib.encoder_identity(identity)
        dim = sib.check_dim(dim)
        if not callable(encode):
            raise sib.BuildRefused("encoder_invalid")
        if not isinstance(name, str) or sib.MEMORY_NAME.fullmatch(name) is None:
            raise sib.BuildRefused("memory_name_invalid")
        sib._publication_supported()
        root = sp._output_root(root)
        records, duplicates = sib.distinct_records(projection["records"])
        if not records:
            raise sib.BuildRefused("projection_empty")
        sib._check_bounds(records)
        cells = {}
        for record in records:
            cell_id = sib.place(record)
            cells.setdefault(cell_id, []).extend(sib.cell_rows(record, cell_id))
        cutoff_utc = max(record["available_utc"] for record in records)
        texts = sorted({row["stored_text"] for rows in cells.values() for row in rows})
        by_text = dict(zip(texts, sib._vectors(encode, texts, dim)))
    except (sib.BuildRefused, sp.ProjectionRefused) as refusal:
        raise TextRefused(str(refusal)) from None
    projection_bytes = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
    projection_sha256 = hashlib.sha256(projection_bytes).hexdigest()
    memory_dir = root / name
    try:
        os.mkdir(memory_dir)
    except FileExistsError:
        raise TextRefused("memory_exists") from None
    except OSError:
        raise TextRefused("write_failed") from None
    stage = "projection"
    try:
        sib._write_new(memory_dir / "projection.json", projection_bytes)
        stage = "cells"
        pointers = {}
        for cell_id in topology_mod.ALL_CELLS:
            if cell_id not in cells:
                continue
            rows = cells[cell_id]
            vectors = np.vstack([by_text[row["stored_text"]] for row in rows])
            (memory_dir / "cells").mkdir(exist_ok=True)
            pointer = sib._publish_cell(memory_dir / "cells" / cell_id, cell_id, rows, vectors, dim=dim,
                                        cutoff_utc=cutoff_utc, encoder=encoder, projection_sha256=projection_sha256)
            pointers[cell_id] = dict(pointer, count=len(rows))
        stage = "finalize"
        memory = {"schema": memory_mod.SCHEMA, "authority": "none", "scope": SCOPE, "synthetic": True,
                  "cutoff_utc": cutoff_utc, "projection_sha256": projection_sha256, "projection_schema": SCHEMA,
                  "projection_file": "projection.json", "encoder": encoder, "dim": dim,
                  "placement": sib.PLACEMENT_RULE, "topology_source_sha256": cell_mod.file_sha256(sib.TOPOLOGY_FILE),
                  "cells": pointers, "records": len(records), "duplicates": duplicates, "views": list(sib.VIEWS),
                  "independent_views": False}
        corpus_mod.ws.guard_object(memory)
        memory_bytes = cell_mod.canonical(memory).encode("utf-8")
        memory_sha256 = hashlib.sha256(memory_bytes).hexdigest()
        descriptor = {"schema": DESCRIPTOR_SCHEMA, "scope": SCOPE, "memory_sha256": memory_sha256, "dim": dim,
                      "encoder": encoder}
        descriptor_bytes = cell_mod.canonical(descriptor).encode("utf-8")
        stage = "descriptor"
        sib._write_new(memory_dir / "descriptor.json", descriptor_bytes)
        stage = "memory"
        sib._commit_memory(memory_dir, memory_bytes)
    except sib.BuildRefused as refusal:
        sib._report_incomplete(memory_dir, name, str(refusal), stage)
        raise TextRefused(str(refusal)) from None
    except Exception:  # noqa: BLE001 - one fixed token after the directory exists; nothing is removed
        sib._report_incomplete(memory_dir, name, "write_failed", stage)
        raise TextRefused("write_failed") from None
    return {"memory_sha256": memory_sha256, "descriptor_sha256": hashlib.sha256(descriptor_bytes).hexdigest(),
            "descriptor": descriptor, "records": len(records)}


def _read_bounded(path, limit):
    with open(path, "rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise TextRefused("file_over_bound")
    return raw


def ask_synthetic_text(memory_dir, expected_memory_sha256, encoder, situation, situation_time_utc, *, enabled=False):
    """Q4 over a pinned synthetic text memory; every result cites its record and carries the stored text."""
    if not _enabled(enabled):
        return {"status": "OFF"}
    from sandbox.mind.step2 import memory as memory_mod
    from sandbox.mind.step2 import q4 as q4_mod
    memory_dir = Path(memory_dir)
    try:
        raw = _read_bounded(memory_dir / "memory.json", memory_mod.MAX_MEMORY_MANIFEST_BYTES)
    except OSError:
        raise TextRefused("memory_unreadable") from None
    if (not isinstance(expected_memory_sha256, str) or sp.HEX64.fullmatch(expected_memory_sha256) is None
            or hashlib.sha256(raw).hexdigest() != expected_memory_sha256):
        raise TextRefused("memory_mismatch")
    try:
        manifest = json.loads(raw)
        if manifest.get("scope") != SCOPE or manifest.get("synthetic") is not True:
            raise ValueError()
        projection_raw = _read_bounded(memory_dir / manifest["projection_file"], 64 * 1024 * 1024)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise TextRefused("memory_invalid") from None
    if hashlib.sha256(projection_raw).hexdigest() != manifest["projection_sha256"]:
        raise TextRefused("projection_mismatch")
    try:
        projection = validate_projection(json.loads(projection_raw))
    except ValueError:
        raise TextRefused("projection_invalid") from None
    by_ref = {record["provenance"]["event_ref"]: record for record in projection["records"]}
    try:
        memory = memory_mod.Memory(memory_dir, manifest_bytes=raw)
        out = q4_mod.Q4(memory, encoder).ask(situation, situation_time_utc)
    except memory_mod.MemoryError_:
        raise TextRefused("memory_unreadable") from None
    results = []
    for result in out["results"]:
        record = by_ref.get(result["ref"])
        if record is None or record["record_id"] != result["doc_id"]:
            raise TextRefused("result_unresolved")
        results.append({"ref": result["ref"], "text": record["stored_text"], "time_utc": record["time_utc"],
                        "cell": result["cell"], "kind": record["kind"]})
    answer = {"status": out["status"], "reason": out["reason"], "results": results}
    corpus_mod.ws.guard_object(answer)
    return answer
