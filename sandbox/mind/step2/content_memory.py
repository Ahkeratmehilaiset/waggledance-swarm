"""HEX MEMORY STEP 2, useful content memory (increment 3: builder entry and pinned ask). Default OFF.

Lead scope decision 2026-11-07 18:16Z: after the two content projections (bridge messages in
``shared_content_projection``, exact-allowlisted repository documents in ``shared_doc_projection``) the route needs a
builder entry. This module builds one frozen hex memory from those two projections with the existing
``shared_index_builder`` cell and commit primitives and answers a pinned ask over it, exactly like the synthetic card
memory does for cards. It adds no resolver branch, no policy file, no launcher or installer change and no live input.

* Every function is OFF unless ``enabled is True`` and then returns ``{"status": "OFF"}`` without reading anything.
* The caller passes the SOURCES, not projections: ``messages`` = ``{rows, policy_bytes, policy_sha256, roster}`` and
  ``documents`` = ``{repo_root, policy_bytes, policy_sha256, git_executable, git_sha256}`` with exactly those keys
  (the absolute Git executable and its SHA-256 pin, MP3: never Git from PATH), at least one of them. The
  projections are derived here by the two modules' own builders, so a projection is never trusted as given; their
  refusal tokens pass through unchanged.
* The memory carries its own scope ``wd.mind-content-events.v1`` and descriptor schema, so the production Q4 reader,
  which pins the shared scope and descriptor schema, does not accept it.
* Placement is a closed rule: a bridge message by its projected type through the shared placement table (no status is
  projected, so there is no blocked override), a repository document in the learning cell.
* Recall by meaning: the stored text stays in the lane-safe ``wd.content-text.v1`` form in the projection and in every
  answer, but the cell rows and the vectors carry the DECODED text, so the encoder and the lexical index see real
  Finnish words instead of escapes (the shared Q4 lexical tokens are Unicode word runs). Decoding is strict; a
  record that does not decode refuses the build. The row provenance omits the reference-kind labels, which the
  shared Q4 reserves for the v6 projected kind; every hit is resolved by event ref AND record id against the pinned
  projection, where the labels stay.
* An answer cites each hit by its source (bridge event ref, or commit, path and blob id), returns the encoded text,
  the time, the cell and the memory's coverage cutoff; an ABSTAIN answer carries the same cutoff, so absence is never
  presented as a negative fact. The memory and projection files are pinned by the expected memory sha256.
"""
import hashlib
import hmac
import json
import os
import re
from pathlib import Path

from sandbox.mind.step2 import corpus as corpus_mod
from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import shared_doc_projection as dp
from sandbox.mind.step2 import shared_projection as sp

SCHEMA = "wd.mind-content-memory-projection.v1"
SCOPE = "wd.mind-content-events.v1"
DESCRIPTOR_SCHEMA = "wd.mind-content-descriptor.v1"
PLACEMENT_RULE = "content-v1: bridge_message by projected type (shared closed table), repo_doc -> learning"
PROJECTION_KEYS = frozenset({"schema", "authority", "scope", "messages", "documents"})
MESSAGE_KEYS = frozenset({"rows", "policy_bytes", "policy_sha256", "roster"})
DOCUMENT_KEYS = frozenset({"repo_root", "policy_bytes", "policy_sha256", "git_executable", "git_sha256"})
MAX_PROJECTION_BYTES = 64 * 1024 * 1024
# Route B (Lead decision 2026-11-07 18:56Z): the same memory under the shared scope and the v2 shared descriptor, so the
# isolated child stays the only reader; it names this projection schema, so the unchanged v6 resolver refuses it and
# only a resolver call with the same pinned content route decision resolves it.
ROUTE_SCOPE = "wd.q4-shared-projected-events.v1"  # == shared_index_builder.SCOPE == shared_result_resolver.SCOPE
PROJECTED_REF_KIND = "sha256_of_projected_metadata"  # == q4.PROJECTED_REF_KIND, the only row label the child reads
DECISION_SCHEMA = "wd.mind-content-route-decision.v1"
DECISION_NAME = "enable_content_route"
DECISION_KEYS = frozenset({"schema", "decision", "projection_schema", "scope", "content_module_sha256",
                           "message_module_sha256", "doc_module_sha256", "nonreview_policy_sha256", "roster_sha256",
                           "message_policy_sha256", "doc_policy_sha256", "source_prefix"})
DECISION_PINS = ("content_module_sha256", "message_module_sha256", "doc_module_sha256", "nonreview_policy_sha256",
                 "roster_sha256")
PREFIX_KEYS = frozenset({"sha256", "offset", "generation"})
ROUTE_KEYS = frozenset({"decision_sha256", "source_prefix", "source_status", "completeness", "message_policy_sha256",
                        "doc_policy_sha256", "doc_commit"})
ROUTE_MESSAGE_KEYS = frozenset({"source_path", "generation_path", "end_cursor", "policy_bytes", "policy_sha256"})
MAX_DECISION = 65536
MAX_SOURCE_ROWS = 4096
MAX_ROW_BYTES = 65536
MAX_PREFIX_BYTES = 256 * 1024 * 1024
PAGE_BYTES = 4 * 1024 * 1024
GENERATION = re.compile(r"[A-Za-z0-9._:-]{1,128}")
HEX64 = re.compile(r"[0-9a-f]{64}")
VISIBILITY_GUARD_SECONDS = 1.0  # == the shared resolver guard band


class ContentMemoryRefused(ValueError):
    """A fixed refusal token; never any input text."""


def _refuse(token):
    raise ContentMemoryRefused(token)


def _enabled(enabled):
    if enabled is False:
        return False
    if enabled is not True:
        _refuse("enabled_invalid")
    return True


def _source(value, keys):
    if value is None:
        return None
    if type(value) is not dict or set(value) != keys:
        _refuse("sources_invalid")
    return value


def derive_content_projection(messages=None, documents=None):
    """The combined projection, derived from the sources by the two projection modules."""
    messages = _source(messages, MESSAGE_KEYS)
    documents = _source(documents, DOCUMENT_KEYS)
    if messages is None and documents is None:
        _refuse("sources_missing")
    try:
        message_projection = (cp.build_content_projection(enabled=True, **messages)
                              if messages is not None else None)
        document_projection = dp.build_doc_projection(enabled=True, **documents) if documents is not None else None
    except (cp.ContentRefused, dp.DocRefused) as refusal:
        token = str(refusal)
    else:
        token = None
    if token is not None:
        _refuse(token)
    return {"schema": SCHEMA, "authority": "none", "scope": SCOPE, "messages": message_projection,
            "documents": document_projection}


def records_of(projection):
    out = []
    for part, schema in (("messages", cp.SCHEMA), ("documents", dp.SCHEMA)):
        value = projection[part]
        if value is None:
            continue
        if type(value) is not dict or value.get("schema") != schema or type(value.get("records")) is not list:
            _refuse("projection_invalid")
        out.extend(value["records"])
    return out


def place(record):
    """The cell of one content record under the closed content rule."""
    from sandbox.mind.step2 import shared_index_builder as sib
    from waggledance.core import hex_cell_topology as topology_mod
    kind = record["provenance"]["source_kind"]
    if kind == cp.SOURCE_KIND:
        try:
            return sib.PLACEMENT[record["meta"]["type"]]
        except KeyError:
            raise sib.BuildRefused("placement_unknown") from None
    if kind == dp.SOURCE_KIND:
        return topology_mod.CELL_LEARNING
    raise sib.BuildRefused("placement_unknown")


def _decoded(record):
    """The cell-row form of a record: decoded text under the one row form the shared Q4 and the isolated child read
    (kind ``event``, the projected reference kind, not canonical). The projection keeps the record's own kind and
    reference kind; every hit is resolved by event ref AND record id against that pinned projection."""
    text = cp.decode_text(record["stored_text"])
    provenance = dict(record["provenance"], ref_kind=PROJECTED_REF_KIND, canonical_event=False)
    return dict(record, kind="event", stored_text=text, summary=text, provenance=provenance)


def build_content_memory(*, enabled=False, messages=None, documents=None, encode=None, identity=None, dim=None,
                         root=None, name=None):
    """One frozen content memory ``root/name`` derived from the sources, or ``{"status": "OFF"}``."""
    if not _enabled(enabled):
        return {"status": "OFF"}
    projection = derive_content_projection(messages, documents)
    return _build(projection, encode, identity, dim, root, name, SCOPE, DESCRIPTOR_SCHEMA, None, None)


def _serialized(projection):
    """The one byte form of a projection: what the build writes and what the snapshot check re-derives."""
    return json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")


def _build(projection, encode, identity, dim, root, name, scope, descriptor_schema, policy, evidence):
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
        records, duplicates = sib.distinct_records(records_of(projection))
        if not records:
            raise sib.BuildRefused("projection_empty")
        sib._check_bounds(records)
        cells = {}
        for record in records:
            cell_id = place(record)
            cells.setdefault(cell_id, []).extend(sib.cell_rows(_decoded(record), cell_id))
        cutoff_utc = max(record["available_utc"] for record in records)
        texts = sorted({row["stored_text"] for rows in cells.values() for row in rows})
        by_text = dict(zip(texts, sib._vectors(encode, texts, dim)))
    except (sib.BuildRefused, sp.ProjectionRefused, cp.ContentRefused) as refusal:
        raise ContentMemoryRefused(str(refusal)) from None
    projection_bytes = _serialized(projection)
    projection_sha256 = hashlib.sha256(projection_bytes).hexdigest()
    memory_dir = root / name
    try:
        os.mkdir(memory_dir)
    except FileExistsError:
        raise ContentMemoryRefused("memory_exists") from None
    except OSError:
        raise ContentMemoryRefused("write_failed") from None
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
        memory = {"schema": memory_mod.SCHEMA, "authority": "none", "scope": scope, "synthetic": False,
                  "cutoff_utc": cutoff_utc, "projection_sha256": projection_sha256, "projection_schema": SCHEMA,
                  "projection_file": "projection.json", "encoder": encoder, "dim": dim, "placement": PLACEMENT_RULE,
                  "topology_source_sha256": cell_mod.file_sha256(sib.TOPOLOGY_FILE), "cells": pointers,
                  "records": len(records), "duplicates": duplicates, "views": list(sib.VIEWS),
                  "independent_views": False}
        if policy is not None:
            memory["policy"] = policy
        if evidence is not None:
            memory["content_route"] = evidence
        corpus_mod.ws.guard_object(memory)
        memory_bytes = cell_mod.canonical(memory).encode("utf-8")
        memory_sha256 = hashlib.sha256(memory_bytes).hexdigest()
        descriptor = {"schema": descriptor_schema, "scope": scope, "memory_sha256": memory_sha256, "dim": dim,
                      "encoder": encoder}
        descriptor_bytes = cell_mod.canonical(descriptor).encode("utf-8")
        stage = "descriptor"
        sib._write_new(memory_dir / "descriptor.json", descriptor_bytes)
        stage = "memory"
        sib._commit_memory(memory_dir, memory_bytes)
    except sib.BuildRefused as refusal:
        sib._report_incomplete(memory_dir, name, str(refusal), stage)
        raise ContentMemoryRefused(str(refusal)) from None
    except Exception:  # noqa: BLE001 - one fixed token after the directory exists; nothing is removed
        sib._report_incomplete(memory_dir, name, "write_failed", stage)
        raise ContentMemoryRefused("write_failed") from None
    return {"memory_sha256": memory_sha256, "descriptor_sha256": hashlib.sha256(descriptor_bytes).hexdigest(),
            "descriptor": descriptor, "records": len(records), "cutoff_utc": cutoff_utc}


def _read_bounded(path, limit):
    with open(path, "rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        _refuse("file_over_bound")
    return raw


def _cite(record):
    provenance = record["provenance"]
    if provenance["source_kind"] == cp.SOURCE_KIND:
        return {"source_kind": cp.SOURCE_KIND, "source_ref_sha256": provenance["source_event_ref"],
                "agent": record["meta"]["agent"], "type": record["meta"]["type"], "task_id": record["meta"]["task_id"],
                "chunk_index": provenance["chunk_index"], "chunk_count": provenance["chunk_count"]}
    return {"source_kind": dp.SOURCE_KIND, "source_ref_sha256": provenance["source_doc_ref"],
            "commit": provenance["commit"], "path": provenance["path"], "blob_id": provenance["blob_id"],
            "section_index": provenance["section_index"], "chunk_index": provenance["chunk_index"],
            "chunk_count": provenance["chunk_count"]}


def ask_content_memory(memory_dir, expected_memory_sha256, encoder, situation, situation_time_utc, *,
                       enabled=False):
    """Q4 over a pinned content memory; every result cites its source and carries the lane-safe encoded text."""
    if not _enabled(enabled):
        return {"status": "OFF"}
    from sandbox.mind.step2 import memory as memory_mod
    from sandbox.mind.step2 import q4 as q4_mod
    memory_dir = Path(memory_dir)
    try:
        raw = _read_bounded(memory_dir / "memory.json", memory_mod.MAX_MEMORY_MANIFEST_BYTES)
    except OSError:
        _refuse("memory_unreadable")
    if (not isinstance(expected_memory_sha256, str) or sp.HEX64.fullmatch(expected_memory_sha256) is None
            or hashlib.sha256(raw).hexdigest() != expected_memory_sha256):
        _refuse("memory_mismatch")
    try:
        manifest = json.loads(raw)
        if (manifest.get("scope") != SCOPE or manifest.get("projection_schema") != SCHEMA
                or manifest.get("placement") != PLACEMENT_RULE):
            raise ValueError()
        projection_raw = _read_bounded(memory_dir / manifest["projection_file"], MAX_PROJECTION_BYTES)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ContentMemoryRefused):
        _refuse("memory_invalid")
    if hashlib.sha256(projection_raw).hexdigest() != manifest["projection_sha256"]:
        _refuse("projection_mismatch")
    try:
        projection = json.loads(projection_raw)
        if type(projection) is not dict or set(projection) != PROJECTION_KEYS or projection["schema"] != SCHEMA:
            raise ValueError()
        records = records_of(projection)
    except (ValueError, KeyError, TypeError, ContentMemoryRefused):
        _refuse("projection_invalid")
    when = corpus_mod.parse_utc(situation_time_utc) if isinstance(situation_time_utc, str) else None
    if when is None:
        _refuse("situation_time_invalid")
    by_ref = {record["provenance"]["event_ref"]: record for record in records}
    try:
        memory = memory_mod.Memory(memory_dir, manifest_bytes=raw)
        out = q4_mod.Q4(memory, encoder).ask(situation, situation_time_utc)
    except memory_mod.MemoryError_:
        _refuse("memory_unreadable")
    results = []
    for result in out["results"]:
        record = by_ref.get(result["ref"])
        if record is None or record["record_id"] != result["doc_id"]:
            _refuse("result_unresolved")
        results.append(dict(_cite(record), ref=result["ref"], text=record["stored_text"],
                            text_encoding=cp.TEXT_ENCODING, time_utc=record["time_utc"], cell=result["cell"]))
    answer = {"status": out["status"], "reason": out["reason"], "results": results,
              "coverage": {"cutoff_utc": manifest["cutoff_utc"]}}
    corpus_mod.ws.guard_object(answer)
    return answer


# --- route B: the content memory for the existing isolated reader ----------------------------------------------------

def _file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def module_sha256s():
    """The source hashes of the three content modules as loaded; a content route decision names all three."""
    return {"content_module_sha256": _file_sha256(__file__), "message_module_sha256": _file_sha256(cp.__file__),
            "doc_module_sha256": _file_sha256(dp.__file__)}


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _no_constant(_):
    raise ValueError()


def verify_content_route_decision(decision_bytes, decision_sha256, *, policy_sha256=None, roster_sha256=None):
    """The explicit content route decision, or ``decision_invalid``.

    The bytes must hash to the caller pin and be canonical JSON with the exact key set; the decision must name the
    loaded source hashes of the three content modules, the same non-review policy and roster pins the route runs
    with, the message and document policy pins (at least one; ``null`` for a source the route does not use) and, iff
    messages are used, one anchored bridge prefix (sha256, end offset, generation). It is caller-pinned and who
    issued it is not authenticated (no signature).
    """
    decision, failed = None, False
    try:
        if (type(decision_bytes) is not bytes or not 0 < len(decision_bytes) <= MAX_DECISION
                or type(decision_sha256) is not str or HEX64.fullmatch(decision_sha256) is None
                or not hmac.compare_digest(hashlib.sha256(decision_bytes).hexdigest(), decision_sha256)):
            raise ValueError()
        decision = json.loads(decision_bytes.decode("ascii"), object_pairs_hook=cp._unique_pairs,
                              parse_constant=_no_constant)
        if type(decision) is not dict or set(decision) != DECISION_KEYS:
            raise ValueError()
        if _canon(decision).encode("ascii") != decision_bytes:
            raise ValueError()
        message_pin, doc_pin, prefix = (decision["message_policy_sha256"], decision["doc_policy_sha256"],
                                        decision["source_prefix"])
        if (decision["schema"] != DECISION_SCHEMA or decision["decision"] != DECISION_NAME
                or decision["projection_schema"] != SCHEMA or decision["scope"] != ROUTE_SCOPE
                or any(type(decision[key]) is not str or HEX64.fullmatch(decision[key]) is None
                       for key in DECISION_PINS)
                or any(not hmac.compare_digest(decision[key], value) for key, value in module_sha256s().items())
                or type(policy_sha256) is not str or type(roster_sha256) is not str
                or not hmac.compare_digest(decision["nonreview_policy_sha256"], policy_sha256)
                or not hmac.compare_digest(decision["roster_sha256"], roster_sha256)
                or (message_pin is None and doc_pin is None)
                or any(pin is not None and (type(pin) is not str or HEX64.fullmatch(pin) is None)
                       for pin in (message_pin, doc_pin))
                or (message_pin is None) != (prefix is None)):
            raise ValueError()
        if prefix is not None and (type(prefix) is not dict or set(prefix) != PREFIX_KEYS
                                   or type(prefix["sha256"]) is not str or HEX64.fullmatch(prefix["sha256"]) is None
                                   or type(prefix["offset"]) is not int or prefix["offset"] < 1
                                   or type(prefix["generation"]) is not str
                                   or GENERATION.fullmatch(prefix["generation"]) is None):
            raise ValueError()
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OSError):
        failed = True
    if failed:
        _refuse("decision_invalid")
    return decision


def _read_prefix_rows(source_path, generation_path, end_cursor, expected_prefix_sha256, tasks):
    """The rows of the approved tasks inside exactly the anchored bridge prefix, keyed by canonical event id.

    The same chained prefix read as the card prefix verifier (prefix sha256 carried page to page, the end cursor,
    the file identity and the generation re-checked at the end), but kept to the content policy's own approved
    tasks; anything else raises ``ValueError``."""
    from tools.bridge_compact_view import event_id
    from waggledance.core import bridge_log_reader as bridge
    if (type(source_path) is not str or not os.path.isabs(source_path)
            or type(generation_path) is not str or not os.path.isabs(generation_path)
            or type(end_cursor) is not bridge.BridgeCursor or bridge._validate_cursor(end_cursor)
            or type(end_cursor.generation) is not str or not end_cursor.generation
            or type(end_cursor.offset) is not int or not 0 < end_cursor.offset <= MAX_PREFIX_BYTES
            or type(expected_prefix_sha256) is not str or HEX64.fullmatch(expected_prefix_sha256) is None):
        raise ValueError()
    cursor = bridge.BridgeCursor(0, end_cursor.file_identity, end_cursor.generation)
    prefix_sha = hashlib.sha256(b"").hexdigest()
    rows, pages = {}, 0
    while True:
        pages += 1
        if pages > MAX_PREFIX_BYTES // PAGE_BYTES + 2:
            raise ValueError()
        read = bridge.read_bridge_log(source_path, cursor=cursor,
                                      max_bytes=min(PAGE_BYTES, end_cursor.offset - cursor.offset), max_rows=100000,
                                      generation_path=generation_path, expected_prefix_sha256=prefix_sha,
                                      max_prefix_bytes=MAX_PREFIX_BYTES)
        if (not isinstance(read, bridge.BridgePrefixReadResult)
                or read.status not in (bridge.BridgeReadStatus.OK, bridge.BridgeReadStatus.IDLE)
                or read.snapshot_length is None or read.snapshot_length < end_cursor.offset
                or read.candidate_cursor is None or read.candidate_prefix is None):
            raise ValueError()
        for event in read.rows:
            if type(event) is not dict or type(event.get("task_id")) is not str or event["task_id"] not in tasks:
                continue
            encoded = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                                 allow_nan=False).encode("utf-8")
            if len(encoded) > MAX_ROW_BYTES or len(rows) >= MAX_SOURCE_ROWS:
                raise ValueError()
            rows[event_id(event)] = event
        nxt = read.candidate_cursor
        if nxt.offset == end_cursor.offset:
            if nxt != end_cursor or not hmac.compare_digest(read.candidate_prefix.sha256, expected_prefix_sha256):
                raise ValueError()
            with bridge._open_log(Path(source_path)) as stream:
                if (bridge._file_identity(stream) != end_cursor.file_identity
                        or os.fstat(stream.fileno()).st_size < end_cursor.offset):
                    raise ValueError()
            if bridge._read_generation(Path(generation_path)) != end_cursor.generation:
                raise ValueError()
            return rows
        if nxt.offset <= cursor.offset or nxt.offset > end_cursor.offset:
            raise ValueError()
        cursor, prefix_sha = nxt, read.candidate_prefix.sha256


def _route_evidence(decision, decision_sha256, projection):
    documents = projection["documents"]
    return {"decision_sha256": decision_sha256, "source_prefix": decision["source_prefix"],
            "source_status": "MATCH_WITHIN_PREFIX" if decision["message_policy_sha256"] is not None else "NO_MESSAGES",
            "completeness": "UNKNOWN", "message_policy_sha256": decision["message_policy_sha256"],
            "doc_policy_sha256": decision["doc_policy_sha256"],
            "doc_commit": documents["commit"] if documents is not None else None}


def build_route_content_memory(*, enabled=False, messages=None, documents=None, decision_bytes=None,
                               decision_sha256=None, policy_bytes=None, policy_sha256=None, roster_bytes=None,
                               roster_sha256=None, encode=None, identity=None, dim=None, root=None, name=None):
    """Route B: the content memory under the shared scope and the v2 shared descriptor, or ``{"status": "OFF"}``.

    ``messages`` = ``{source_path, generation_path, end_cursor, policy_bytes, policy_sha256}``: the message rows are
    read here, only inside the decision's anchored prefix and only for the content policy's approved tasks, which
    must be a subset of the provisioned non-review policy's tasks; the roster is the strict pinned roster. The
    decision must verify and pin exactly the policies used. The manifest binds the strict policy pins and the route
    evidence; the resolver re-checks both against the same decision.
    """
    if not _enabled(enabled):
        return {"status": "OFF"}
    from sandbox.mind.step2 import shared_index_builder as sib
    projection, decision = _route_projection(messages, documents, decision_bytes, decision_sha256, policy_bytes,
                                             policy_sha256, roster_bytes, roster_sha256)
    policy = {"authorship": sp.AUTHORSHIP_ROSTER, "review_signal_excluded": True, "policy_sha256": policy_sha256,
              "roster_sha256": roster_sha256}
    return _build(projection, encode, identity, dim, root, name, ROUTE_SCOPE, sib.DESCRIPTOR_SCHEMA, policy,
                  _route_evidence(decision, decision_sha256, projection))


def _route_projection(messages, documents, decision_bytes, decision_sha256, policy_bytes, policy_sha256,
                      roster_bytes, roster_sha256):
    """``(projection, decision)`` re-derived from the sources: the strict pins, the verified decision, the content
    policy as a subset of the non-review policy, exactly the anchored message prefix and the pinned document blobs.
    The route build and the snapshot check both take this one path."""
    messages = _source(messages, ROUTE_MESSAGE_KEYS)
    documents = _source(documents, DOCUMENT_KEYS)
    if messages is None and documents is None:
        _refuse("sources_missing")
    from sandbox.mind.step2 import shared_capture_adapter as adapter
    pins = None
    try:
        pins = adapter.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
    except Exception:  # noqa: BLE001 - one fixed token, never a pin or its bytes
        pins = None
    if pins is None:
        _refuse("policy_invalid")
    tasks, roster = pins
    decision = verify_content_route_decision(decision_bytes, decision_sha256, policy_sha256=policy_sha256,
                                             roster_sha256=roster_sha256)
    if ((messages is None) != (decision["message_policy_sha256"] is None)
            or (documents is None) != (decision["doc_policy_sha256"] is None)
            or (messages is not None and messages["policy_sha256"] != decision["message_policy_sha256"])
            or (documents is not None and documents["policy_sha256"] != decision["doc_policy_sha256"])):
        _refuse("decision_mismatch")
    derived = None
    if messages is not None:
        try:
            content_policy = cp.load_content_policy(messages["policy_bytes"], messages["policy_sha256"])
        except cp.ContentRefused:
            content_policy = None
        if content_policy is None:
            _refuse("policy_refused")
        if not content_policy["approved_tasks"] <= tasks:
            _refuse("policy_not_subset")
        prefix, cursor = decision["source_prefix"], messages["end_cursor"]
        if (type(getattr(cursor, "offset", None)) is not int or cursor.offset != prefix["offset"]
                or getattr(cursor, "generation", None) != prefix["generation"]):
            _refuse("source_unanchored")
        rows = None
        try:
            rows = _read_prefix_rows(messages["source_path"], messages["generation_path"], cursor, prefix["sha256"],
                                     content_policy["approved_tasks"])
        except Exception:  # noqa: BLE001 - one fixed token, never a path or row
            rows = None
        if rows is None:
            _refuse("source_unverified")
        derived = {"rows": rows, "policy_bytes": messages["policy_bytes"], "policy_sha256": messages["policy_sha256"],
                   "roster": roster}
    return derive_content_projection(derived, documents), decision


def validate_content_snapshot(*, enabled=False, projection_bytes=None, projection_sha256=None, messages=None,
                              documents=None, decision_bytes=None, decision_sha256=None, policy_bytes=None,
                              policy_sha256=None, roster_bytes=None, roster_sha256=None):
    """Source re-derivation of a pinned route projection (RCO2 MP1), or ``{"status": "OFF"}``.

    The structural checks only prove that a record agrees with itself. This re-reads exactly the decision's anchored
    message prefix and the pinned document blobs, re-derives the projection on the build's own path and requires
    byte equality with the pinned projection bytes; the projection pin is checked before any source is read. Any
    difference is ``snapshot_mismatch``; a source that is not the pinned one refuses with the build's own token.
    Not run per query: it costs one read of the whole anchored prefix."""
    if not _enabled(enabled):
        return {"status": "OFF"}
    if (type(projection_bytes) is not bytes or len(projection_bytes) > MAX_PROJECTION_BYTES
            or type(projection_sha256) is not str or HEX64.fullmatch(projection_sha256) is None
            or not hmac.compare_digest(hashlib.sha256(projection_bytes).hexdigest(), projection_sha256)):
        _refuse("snapshot_mismatch")
    projection, decision = _route_projection(messages, documents, decision_bytes, decision_sha256, policy_bytes,
                                             policy_sha256, roster_bytes, roster_sha256)
    if not hmac.compare_digest(_serialized(projection), projection_bytes):
        _refuse("snapshot_mismatch")
    documents = projection["documents"]
    return {"status": "MATCH", "projection_sha256": projection_sha256, "decision_sha256": decision_sha256,
            "source_prefix": decision["source_prefix"],
            "doc_commit": documents["commit"] if documents is not None else None}


def validate_route_projection(projection):
    """Structural integrity of a stored content projection, or ``projection_invalid``: the exact keys and schemas,
    every record id and event ref re-derived from its own text and source fields, the lane output class, a strict
    decode, the record kind and reference kind of its source and no duplicate record id. It cannot re-read the
    sources; their binding is the route decision and the producer."""
    failed = False
    try:
        if (type(projection) is not dict or set(projection) != PROJECTION_KEYS or projection["schema"] != SCHEMA
                or projection["authority"] != "none" or projection["scope"] != SCOPE):
            raise ValueError()
        seen = set()
        for record in records_of(projection):
            provenance, text = record["provenance"], record["stored_text"]
            if type(text) is not str or cp._OUTPUT.fullmatch(text) is None or record["summary"] != text:
                raise ValueError()
            cp.decode_text(text)
            if provenance["source_kind"] == cp.SOURCE_KIND:
                source = provenance["source_event_ref"]
                ref = hashlib.sha256(cp._canonical({"text": text, "source": source,
                                                    "chunk": provenance["chunk_index"]})).hexdigest()
                prefix, kind, ref_kind = "content:", "event", cp.REF_KIND
            elif provenance["source_kind"] == dp.SOURCE_KIND:
                source = hashlib.sha256(cp._canonical({"blob": provenance["blob_id"], "commit": provenance["commit"],
                                                       "path": provenance["path"]})).hexdigest()
                if provenance["source_doc_ref"] != source:
                    raise ValueError()
                ref = hashlib.sha256(cp._canonical({"text": text, "source": source,
                                                    "section": provenance["section_index"],
                                                    "chunk": provenance["chunk_index"]})).hexdigest()
                prefix, kind, ref_kind = "doc:", "document", dp.REF_KIND
            else:
                raise ValueError()
            if (provenance["event_ref"] != ref or record["record_id"] != prefix + ref or record["kind"] != kind
                    or provenance["ref_kind"] != ref_kind or provenance["canonical_event"] is not False
                    or record["record_id"] in seen):
                raise ValueError()
            seen.add(record["record_id"])
        corpus_mod.ws.guard_object(projection)
    except (ValueError, KeyError, TypeError, AttributeError, cp.ContentRefused, ContentMemoryRefused,
            corpus_mod.ws.WorkspaceError):
        failed = True
    if failed:
        _refuse("projection_invalid")
    return projection


def check_content_route_evidence(evidence, projection, decision, decision_sha256):
    """The manifest's bound route evidence must be exactly what the decision and the projection give."""
    failed = False
    try:
        messages, documents = projection["messages"], projection["documents"]
        if (type(evidence) is not dict or set(evidence) != ROUTE_KEYS
                or _canon(evidence) != _canon(_route_evidence(decision, decision_sha256, projection))
                or (messages is None) != (decision["message_policy_sha256"] is None)
                or (documents is None) != (decision["doc_policy_sha256"] is None)
                or (messages is not None and messages["policy_sha256"] != decision["message_policy_sha256"])
                or (documents is not None and documents["policy_sha256"] != decision["doc_policy_sha256"])):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        failed = True
    if failed:
        _refuse("evidence_invalid")
    return evidence


def _rescreened(record):
    """The read-time screen: the decoded text of a returned record must still classify KEEP to the same stored text
    (the length bound is the build's; here only the screen counts)."""
    try:
        text = cp.decode_text(record["stored_text"])
        verdict, value = cp.classify(text, max(cp.MAX_MESSAGE_CHARS, len(text)))
    except Exception:  # noqa: BLE001 - a fixed refusal, never the text
        return False
    return verdict == "KEEP" and value == record["stored_text"]


def resolve_route_content(projection, results, query_utc):
    """Stored text for exact, time-visible hash-only hits of the isolated child, or ``result_unresolved``.

    Each hit names ``sha256(event_ref)`` and ``sha256(record_id)`` of one projected record under the projected
    reference kind (the child's only row label); repeats collapse to their first occurrence."""
    if not isinstance(query_utc, str) or corpus_mod.parse_utc(query_utc) is None:
        _refuse("query_time_invalid")
    if not isinstance(results, list) or len(results) > 100:
        _refuse("result_unresolved")
    records = {}
    for record in records_of(projection):
        key = (hashlib.sha256(record["provenance"]["event_ref"].encode("ascii")).hexdigest(),
               hashlib.sha256(record["record_id"].encode("ascii")).hexdigest())
        records.setdefault(key, record)
    resolved, seen = [], set()
    for item in results:
        if (not isinstance(item, dict) or set(item) != {"ref_sha256", "doc_id_sha256", "ref_kind", "canonical_event"}
                or item["ref_kind"] != PROJECTED_REF_KIND or item["canonical_event"] is not False
                or any(not isinstance(item[key], str) or HEX64.fullmatch(item[key]) is None
                       for key in ("ref_sha256", "doc_id_sha256"))):
            _refuse("result_unresolved")
        key = (item["ref_sha256"], item["doc_id_sha256"])
        record = records.get(key)
        if (record is None or not sp.visible_at(record, query_utc, guard_band_s=VISIBILITY_GUARD_SECONDS)
                or not _rescreened(record)):
            _refuse("result_unresolved")
        if key not in seen:
            resolved.append({"ref_sha256": key[0], "doc_id_sha256": key[1], "stored_text": record["stored_text"],
                             "ref_kind": PROJECTED_REF_KIND, "canonical_event": False})
            seen.add(key)
    return resolved
