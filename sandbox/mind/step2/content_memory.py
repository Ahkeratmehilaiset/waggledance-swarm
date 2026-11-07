"""HEX MEMORY STEP 2, useful content memory (increment 3: builder entry and pinned ask). Default OFF.

Lead scope decision 2026-11-07 18:16Z: after the two content projections (bridge messages in
``shared_content_projection``, exact-allowlisted repository documents in ``shared_doc_projection``) the route needs a
builder entry. This module builds one frozen hex memory from those two projections with the existing
``shared_index_builder`` cell and commit primitives and answers a pinned ask over it, exactly like the synthetic card
memory does for cards. It adds no resolver branch, no policy file, no launcher or installer change and no live input.

* Every function is OFF unless ``enabled is True`` and then returns ``{"status": "OFF"}`` without reading anything.
* The caller passes the SOURCES, not projections: ``messages`` = ``{rows, policy_bytes, policy_sha256, roster}`` and
  ``documents`` = ``{repo_root, policy_bytes, policy_sha256}`` with exactly those keys, at least one of them. The
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
import json
import os
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
DOCUMENT_KEYS = frozenset({"repo_root", "policy_bytes", "policy_sha256"})
MAX_PROJECTION_BYTES = 64 * 1024 * 1024


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
    """The cell-row form of a record: decoded text, and no reference-kind labels (the shared Q4 accepts those only
    for the v6 projected kind; this memory resolves every hit against its own pinned projection instead)."""
    text = cp.decode_text(record["stored_text"])
    provenance = {key: value for key, value in record["provenance"].items()
                  if key not in ("ref_kind", "canonical_event")}
    return dict(record, stored_text=text, summary=text, provenance=provenance)


def build_content_memory(*, enabled=False, messages=None, documents=None, encode=None, identity=None, dim=None,
                         root=None, name=None):
    """One frozen content memory ``root/name`` derived from the sources, or ``{"status": "OFF"}``."""
    if not _enabled(enabled):
        return {"status": "OFF"}
    projection = derive_content_projection(messages, documents)
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
    projection_bytes = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
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
        memory = {"schema": memory_mod.SCHEMA, "authority": "none", "scope": SCOPE, "synthetic": False,
                  "cutoff_utc": cutoff_utc, "projection_sha256": projection_sha256, "projection_schema": SCHEMA,
                  "projection_file": "projection.json", "encoder": encoder, "dim": dim, "placement": PLACEMENT_RULE,
                  "topology_source_sha256": cell_mod.file_sha256(sib.TOPOLOGY_FILE), "cells": pointers,
                  "records": len(records), "duplicates": duplicates, "views": list(sib.VIEWS),
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
