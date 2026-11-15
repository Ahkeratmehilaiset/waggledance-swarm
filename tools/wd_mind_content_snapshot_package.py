"""Default-off, synthetic-first in-memory v3 content artifact producer.

Only a source snapshot accepted by the current reader can supply records. The
producer re-derives it before providers, after each passage/serialization call,
and after verifying all decoded index vectors. It writes no files, advances no
cursor, publishes no pointer and installs no startup integration. Each call
returns a detached package; replacing a caller's current package is separate.

Passage callbacks receive tuples of at most 16 already screened strings with
the fixed E5 ``passage: `` prefix and 2000-character truncation, and must return
exact little-endian float32 bytes. Serializers receive immutable vector bytes
and dimension 384, returning index bytes. Decoders use the existing read-only
v3 adapter protocol: reconstruct_n must accept ranges of at most 128 rows;
there is no full-cell fallback. No model/native module is imported, no casting
or additional normalization is performed, and provider exceptions never leave
this boundary.

These are caller-trusted synchronous callbacks: their metadata/code digests
do not authenticate code, computation or assets, constrain provider allocation,
or install a deadline. Source consistency is not operator authorization; output
creates no publication authority. Same-user source ABA remains unproven.

The initial 256-record / 4 MiB anchored-prefix / 2 MiB projection ceiling is
deliberately much smaller than v3 query capacity. This does NOT implement the
full-log production goal or establish a hard bound on process memory. Only the
two producer files are new; internal reader helpers retain the exact existing
source, JSON, receipt, pair and vector checks rather than duplicating them.
"""
from __future__ import annotations

import struct

from tools import wd_mind_content_query_readonly as reader


MAX_RECORDS = 256
MAX_PREFIX_BYTES = 4 * 1024 * 1024
MAX_PROJECTION_BYTES = 2 * 1024 * 1024
MAX_TEXT_BYTES = 512 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_BYTES = 8 * 1024 * 1024
PASSAGE_BATCH = 16
_ROW = struct.Struct("<384f")


class SnapshotPackageRefused(ValueError):
    """Fixed-token refusal without source paths, content or callback details."""


def _snapshot(value):
    if (type(value) is not dict or set(value) != reader._Q4_SOURCE_KEYS
            or type(value.get("messages")) is not dict
            or type(value.get("projection_bytes")) is not bytes
            or not 0 < len(value["projection_bytes"]) <= MAX_PROJECTION_BYTES
            or value.get("approved_tasks_bytes") != reader._ALL_TASK_SCOPE_BYTES
            or value.get("approved_tasks_sha256") != reader._ALL_TASK_SCOPE_SHA256):
        raise ValueError("invalid_snapshot")
    frozen = dict(value, messages=dict(value["messages"]))
    decision = reader._pinned(frozen["decision_bytes"], frozen["decision_sha256"])
    prefix = decision.get("source_prefix")
    if (type(prefix) is not dict or type(prefix.get("offset")) is not int
            or not 0 < prefix["offset"] <= MAX_PREFIX_BYTES):
        raise ValueError("source_over_budget")
    return frozen


def _unchanged(original, frozen, report):
    # Reader validation accepts only exact scalar/immutable cursor types; the
    # captured dictionaries keep provider mutations from retargeting the build.
    if original != frozen:
        raise ValueError("snapshot_changed")
    _, _, checked = reader._verified(frozen)
    if checked != report:
        raise ValueError("source_changed")


def _cell(records, cell_id, vector_rows, identity, projection_sha, cutoff, serialize, checkpoint):
    ids, texts, provenance, chunks = [], [], [], []
    for record in records:
        text = reader.decode_text(record["stored_text"])
        for view in ("w", "d"):
            doc_id = record["record_id"] + "#" + view
            ids.append(doc_id)
            texts.append(text)
            chunks.append(vector_rows[record["record_id"]])
            provenance.append(reader._canonical({"record_id": doc_id, "kind": "event",
                "time_utc": record["time_utc"], "cell_id": cell_id,
                "provenance": dict(record["provenance"], record=record["record_id"], view=view,
                                   ref_kind="sha256_of_projected_metadata", canonical_event=False)}) + b"\n")
    vectors = b"".join(chunks)
    index = serialize(vectors, reader._Q4_DIM)
    checkpoint()
    if type(index) is not bytes or not 0 < len(index) <= MAX_ARTIFACT_BYTES:
        raise ValueError("invalid_serialized_index")
    artifacts = {"index": index, "vectors": vectors,
        "meta": reader._canonical({"dim": reader._Q4_DIM, "doc_ids": ids, "texts": texts,
                                   "metadata": [{} for _ in ids]}),
        "provenance": b"".join(provenance)}
    if any(len(raw) > MAX_ARTIFACT_BYTES for raw in artifacts.values()):
        raise ValueError("artifact_over_budget")
    pairs = b"".join(doc_id.encode("utf-8") + b"\0" + row for doc_id, row in zip(ids, chunks))
    content = {"cell_id": cell_id, "count": len(ids), "dim": reader._Q4_DIM,
        "cutoff_utc": cutoff, "encoder": identity,
        "projection_sha256": projection_sha, "projection_schema": reader._ALL_TASK_COMBINED_SCHEMA,
        "files": {name: reader._sha(raw) for name, raw in artifacts.items()},
        "ids_sha256": reader._sha("\n".join(ids).encode("utf-8")), "pairs_sha256": reader._sha(pairs)}
    artifacts["receipt"] = reader._canonical({"schema": "wd.bridge-mind-hex-cell.v1", "authority": "none",
        "scope": reader._ALL_TASK_ROUTE_SCOPE, "content": content,
        "content_sha256": reader._sha(reader._canonical(content))})
    pointer = {"generation": 1, "seq": 1, "receipt_sha256": reader._sha(artifacts["receipt"])}
    log = dict(pointer, schema="wd.bridge-mind-hex-cell-log.v1", prev_line_sha256="0" * 64)
    return {"pointer": reader._canonical(pointer), "log": reader._canonical(log) + b"\n",
            "artifacts": artifacts}, dict(pointer, count=len(ids))


def _package(snapshot, passage_encoder, index_serializer, index_decoder, encoder_binding_bytes,
             encoder_binding_sha256, index_binding_bytes, index_binding_sha256):
    frozen = _snapshot(snapshot)
    encoder_binding = reader._pinned(encoder_binding_bytes, encoder_binding_sha256)
    index_binding = reader._pinned(index_binding_bytes, index_binding_sha256)
    reader._q4_bindings(encoder_binding, index_binding)
    if any(isinstance(callback, type) or not callable(callback)
           for callback in (passage_encoder, index_serializer, index_decoder)):
        raise ValueError("invalid_callback")
    projection, _, report = reader._verified(frozen)
    records = projection["messages"]["records"]
    if (projection["schema"] != reader._ALL_TASK_COMBINED_SCHEMA
            or not 0 < len(records) <= MAX_RECORDS
            or len({record["record_id"] for record in records}) != len(records)):
        raise ValueError("invalid_records")
    texts = [reader.decode_text(record["stored_text"]) for record in records]
    if sum(len(text.encode("utf-8")) for text in texts) > MAX_TEXT_BYTES:
        raise ValueError("text_over_budget")
    cells = {}
    for record in records:
        cell_id = reader._Q4_PLACEMENT[record["meta"]["type"]]
        cells.setdefault(cell_id, []).append(record)
    checkpoint = lambda: _unchanged(snapshot, frozen, report)
    vectors = {}
    for start in range(0, len(records), PASSAGE_BATCH):
        batch = tuple("passage: " + text[:2000] for text in texts[start:start + PASSAGE_BATCH])
        raw = passage_encoder(batch)
        checkpoint()
        if type(raw) is not bytes or len(raw) != len(batch) * _ROW.size:
            raise ValueError("invalid_passage_bytes")
        for offset, record in enumerate(records[start:start + len(batch)]):
            row = raw[offset * _ROW.size:(offset + 1) * _ROW.size]
            reader._q4_vector(_ROW.unpack(row))
            vectors[record["record_id"]] = row
    identity = encoder_binding["encoder"]
    cutoff = max(record["available_utc"] for record in records)
    snapshots, pointers = {}, {}
    artifact_total = len(frozen["projection_bytes"]) + len(encoder_binding_bytes) + len(index_binding_bytes)
    for cell_id, items in sorted(cells.items()):
        snapshots[cell_id], pointers[cell_id] = _cell(
            items, cell_id, vectors, identity, frozen["projection_sha256"], cutoff,
            index_serializer, checkpoint)
        item = snapshots[cell_id]
        artifact_total += len(item["pointer"]) + len(item["log"]) + sum(map(len, item["artifacts"].values()))
        if artifact_total > MAX_PACKAGE_BYTES:
            raise ValueError("package_over_budget")
    memory = {"schema": "wd.bridge-mind-hex-memory-step2.v1", "authority": "none",
        "scope": reader._ALL_TASK_ROUTE_SCOPE, "dim": reader._Q4_DIM, "encoder": identity,
        "projection_schema": reader._ALL_TASK_COMBINED_SCHEMA,
        "projection_sha256": frozen["projection_sha256"], "cutoff_utc": cutoff, "cells": pointers,
        "records": len(records), "views": ["w", "d"], "independent_views": False}
    memory_bytes = reader._canonical(memory)
    descriptor = {"schema": "wd.bridge-mind-q4-memory-descriptor.v3", "scope": reader._ALL_TASK_ROUTE_SCOPE,
                  "memory_sha256": reader._sha(memory_bytes), "dim": reader._Q4_DIM, "encoder": identity}
    descriptor_bytes = reader._canonical(descriptor)
    output = {"descriptor_bytes": descriptor_bytes, "descriptor_sha256": reader._sha(descriptor_bytes),
        "memory_bytes": memory_bytes, "memory_sha256": reader._sha(memory_bytes), "cell_snapshots": snapshots,
        "encoder_sha256": reader._sha(reader._canonical(identity)), "runtime_revision": identity["revision"],
        "encoder_binding_bytes": encoder_binding_bytes, "encoder_binding_sha256": encoder_binding_sha256,
        "index_binding_bytes": index_binding_bytes, "index_binding_sha256": index_binding_sha256}
    total = len(frozen["projection_bytes"]) + sum(len(raw) for raw in
            (memory_bytes, descriptor_bytes, encoder_binding_bytes, index_binding_bytes))
    captured = {}
    record_map = {record["record_id"]: record for record in records}
    for cell_id, item in snapshots.items():
        total += len(item["pointer"]) + len(item["log"]) + sum(map(len, item["artifacts"].values()))
        if total > MAX_PACKAGE_BYTES:
            raise ValueError("package_over_budget")
        captured[cell_id] = reader._q4_cell_controls(cell_id, item, pointers[cell_id], memory, record_map)
    # All cells' controls precede any decoder. Reconstruction is verified with
    # the reader's retained-vector and pair checks, not the serializer's claim.
    def checked_decoder(raw):
        checkpoint()
        index = index_decoder(raw)
        checkpoint()
        return index

    provider = reader._ReadonlyIndexAdapter(checked_decoder, index_binding,
        [reader._sha(item["artifacts"]["index"]) for item in snapshots.values()],
        allow_bounded_v3=True)
    reader._q4_decode_cells_bounded_v3(captured, provider, record_map)
    checkpoint()
    return output


def package_content_snapshot(*, enabled=False, snapshot=None, passage_encoder=None, index_serializer=None,
                             index_decoder=None, encoder_binding_bytes=None, encoder_binding_sha256=None,
                             index_binding_bytes=None, index_binding_sha256=None):
    """Return detached v3 query artifact arguments or a fixed refusal.

    Only ``enabled is True`` opts in. Snapshot paths remain caller-supplied and
    reader-verified. Each package's local cell generation is 1; it is a new
    immutable artifact set, never a claim to advance source/runtime generation.
    Caller-owned providers, source inputs and query stream remain separate from
    the returned artifact arguments. A consumer must use query_content_snapshot
    to re-verify this package at use time; build success grants no authority.
    """
    output = None
    succeeded = False
    try:
        if enabled is not True:
            raise ValueError("disabled")
        output = _package(snapshot, passage_encoder, index_serializer, index_decoder, encoder_binding_bytes,
                          encoder_binding_sha256, index_binding_bytes, index_binding_sha256)
        succeeded = True
    except Exception:
        pass
    if not succeeded:
        raise SnapshotPackageRefused("content_snapshot_package_refused") from None
    return output
