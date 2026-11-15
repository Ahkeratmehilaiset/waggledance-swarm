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
the default. Only capacity_profile="bounded_v3_capacity_v1" selects the fixed
256 MiB prefix / 32768-record profile, subject to every reader limit as well.
It preflights all cells before passage calls; batches remain 16 and every source
checkpoint remains. The profile neither establishes a hard process-memory bound
nor solves the cost of repeated full source verification. A source may exceed
its finite caps. Internal reader helpers retain the existing source, JSON,
receipt, pair and vector checks rather than duplicating them.

The explicit full_prefix_stream_v1 profile uses the reader's complete pinned
source verification, without this producer's fixed source or record ceiling.
It still materializes the projection and artifacts in memory and derives its
full-only decoder limits from the verified snapshot. It refuses when validation
or resources fail. This is not a disk-streaming implementation. Its batch
helper does not authenticate callbacks, models, sources or publish anything.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import threading
from pathlib import Path

from tools import wd_mind_content_query_readonly as reader


MAX_RECORDS = 256
MAX_PREFIX_BYTES = 4 * 1024 * 1024
MAX_PROJECTION_BYTES = 2 * 1024 * 1024
MAX_TEXT_BYTES = 512 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_BYTES = 8 * 1024 * 1024
PASSAGE_BATCH = 16
_WATCH_INTERVAL_SECONDS = 1.0
_ROW = struct.Struct("<384f")


class SnapshotPackageRefused(ValueError):
    """Fixed-token refusal without source paths, content or callback details."""


def _capacity(profile):
    if profile is None:
        return None
    if type(profile) is str and profile == "full_prefix_stream_v1":
        return {"streamed": True}
    if type(profile) is not str or profile != "bounded_v3_capacity_v1":
        raise ValueError("invalid_capacity_profile")
    # A fixed, per-call profile; callers cannot supply arbitrary numeric caps.
    return {"records": 32768, "prefix": 256 * 1024 * 1024,
            "projection": 64 * 1024 * 1024, "text": 32768 * 300,
            "artifact": 48 * 1024 * 1024, "package": 512 * 1024 * 1024,
            "index_overhead": 65536, "control": 65536}


def _cap(capacity, name, default):
    return default if capacity is None else capacity[name]


def _snapshot(value, capacity=None):
    full = capacity is not None and capacity.get("streamed") is True
    if (type(value) is not dict or set(value) != reader._Q4_SOURCE_KEYS
            or type(value.get("messages")) is not dict
            or type(value.get("projection_bytes")) is not bytes
            or not value["projection_bytes"]
            or (not full and len(value["projection_bytes"]) > _cap(capacity, "projection", MAX_PROJECTION_BYTES))
            or value.get("approved_tasks_bytes") != reader._ALL_TASK_SCOPE_BYTES
            or value.get("approved_tasks_sha256") != reader._ALL_TASK_SCOPE_SHA256):
        raise ValueError("invalid_snapshot")
    if capacity is not None:
        raw, digest = value["projection_bytes"], value.get("projection_sha256")
        if not reader._hex(digest) or not reader.hmac.compare_digest(reader._sha(raw), digest):
            raise ValueError("invalid_projection_pin")
        # A valid source-derived record has fewer than 64 JSON nodes; the
        # outer projection/refusal summary fits the fixed 256-node allowance.
        # Bound expansion before _verified's authoritative JSON parse. The
        # same immutable bytes are still fully verified at every checkpoint.
        reader._q4_json_budget(raw, max(256, len(raw) * 2) if full else 256 + 64 * capacity["records"])
    frozen = dict(value, messages=dict(value["messages"]))
    decision = reader._pinned(frozen["decision_bytes"], frozen["decision_sha256"])
    prefix = decision.get("source_prefix")
    if (type(prefix) is not dict or type(prefix.get("offset")) is not int
            or prefix["offset"] <= 0
            or (not full and prefix["offset"] > _cap(capacity, "prefix", MAX_PREFIX_BYTES))):
        raise ValueError("source_over_budget")
    return frozen


def _unchanged(original, frozen, report, *, complete=False):
    # Reader validation accepts only exact scalar/immutable cursor types; the
    # captured dictionaries keep provider mutations from retargeting the build.
    if original != frozen:
        raise ValueError("snapshot_changed")
    _, _, checked = (reader._verified_complete(frozen) if complete else reader._verified(frozen))
    if checked != report:
        raise ValueError("source_changed")


def _immutable_prefix(context):
    """Capture bytes from the same identity and SHA anchor as complete verification."""
    messages, anchor = context["messages"], context["decision"]["source_prefix"]
    cursor = messages["end_cursor"]
    source, sidecar = Path(messages["source_path"]), Path(messages["generation_path"])
    bridge = reader._pinned_dependency("waggledance.core.bridge_log_reader",
        "waggledance/core/bridge_log_reader.py", ("_open_log", "_file_identity", "_read_generation"))
    if cursor.generation is None:
        absent = reader._pinned_dependency("tools.wd_mind_absent_generation_prefix_readonly",
            "tools/wd_mind_absent_generation_prefix_readonly.py", ("_plain", "_sidecar_absent"))
        if sidecar != source.with_name("events.generation.json"):
            raise ValueError("generation_path_mismatch")

        def fence():
            absent._plain(source, directory=False)
            absent._sidecar_absent(sidecar)
    else:
        def fence():
            if bridge._read_generation(sidecar) != cursor.generation:
                raise ValueError("generation_changed")

    fence()
    with bridge._open_log(source) as stream:
        size = os.fstat(stream.fileno()).st_size
        if bridge._file_identity(stream) != cursor.file_identity or size < cursor.offset:
            raise ValueError("source_changed")
        parts, remaining = [], cursor.offset
        while remaining:
            part = stream.read(min(1024 * 1024, remaining))
            if not part:
                raise ValueError("source_truncated")
            parts.append(part)
            remaining -= len(part)
        raw = b"".join(parts)
        if (raw[-1:] != b"\n" or not reader.hmac.compare_digest(reader._sha(raw), anchor["sha256"])
                or bridge._file_identity(stream) != cursor.file_identity
                or os.fstat(stream.fileno()).st_size < size):
            raise ValueError("source_changed")
        fence()
        with bridge._open_log(source) as current:
            if bridge._file_identity(current) != cursor.file_identity or os.fstat(current.fileno()).st_size < size:
                raise ValueError("source_replaced")
        fence()
    return raw


class _SourceWatcher:
    """Own and join one background verifier; failure always invalidates output."""
    def __init__(self):
        self.stop_event = threading.Event()
        self.failed = threading.Event()
        self.thread = None

    def start(self, frozen, report):
        def run():
            try:
                while not self.stop_event.wait(_WATCH_INTERVAL_SECONDS):
                    _, _, observed = reader._verified_complete(frozen)
                    if observed != report:
                        raise ValueError("source_changed")
            except BaseException:
                self.failed.set()
        self.thread = threading.Thread(target=run, name="mind-source-verifier", daemon=False)
        self.thread.start()

    def check(self):
        if self.failed.is_set() or self.thread is None or not self.thread.is_alive():
            raise ValueError("source_watcher_failed")

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join()
        if self.failed.is_set():
            raise ValueError("source_watcher_failed")


def _full_prefix_encode_batches(records, passage_encoder, checkpoint, stage):
    """Feed B16 verified records to an uncommitted stage, one bounded batch at a time.

    Internal building block only. The caller must establish the source/policy
    pins, provide a real checkpoint, guard duplicates, abort the entire stage on
    any error, and independently validate all staged artifacts before use. No
    current public profile invokes this helper or publishes its staged output.
    """
    import itertools
    iterator = iter(records)
    while True:
        batch = tuple(itertools.islice(iterator, PASSAGE_BATCH))
        if not batch:
            return
        if any(type(record) is not dict or type(record.get("record_id")) is not str
               or not record["record_id"] for record in batch):
            raise ValueError("invalid_records")
        texts = tuple("passage: " + reader.decode_text(record["stored_text"])[:2000] for record in batch)
        raw = passage_encoder(texts)
        checkpoint()
        if type(raw) is not bytes or len(raw) != len(batch) * _ROW.size:
            raise ValueError("invalid_passage_bytes")
        rows = tuple(raw[offset * _ROW.size:(offset + 1) * _ROW.size] for offset in range(len(batch)))
        for row in rows:
            reader._q4_vector(_ROW.unpack(row))
        stage(tuple((record["record_id"], row) for record, row in zip(batch, rows)))


class _FullCheckedIndex:
    """Recheck the frozen source after each full-profile reconstruction call."""
    __slots__ = ("_index", "_checkpoint")

    def __init__(self, index, checkpoint):
        self._index, self._checkpoint = index, checkpoint

    @property
    def d(self):
        return self._index.d

    @property
    def ntotal(self):
        return self._index.ntotal

    @property
    def metric_type(self):
        return self._index.metric_type

    def reconstruct_n(self, start, count):
        value = self._index.reconstruct_n(start, count)
        self._checkpoint()
        return value


def _append_bounded(stream, raw, limit):
    if limit is not None and stream.tell() + len(raw) > limit:
        raise ValueError("artifact_over_budget")
    stream.write(raw)


def _cell_metadata(records, cell_id, limit):
    """Exact canonical bytes without a second full JSON string or row list."""
    ids = [record["record_id"] + "#" + view for record in records for view in ("w", "d")]
    meta, provenance = io.BytesIO(), io.BytesIO()
    put = lambda raw: _append_bounded(meta, raw, limit)
    put(b'{"dim":384,"doc_ids":[')
    for position, doc_id in enumerate(ids):
        put((b"," if position else b"") + reader._canonical(doc_id))
    put(b'],"metadata":[')
    for position in range(len(ids)):
        put(b",{}" if position else b"{}")
    put(b'],"texts":[')
    for position, record in enumerate(records):
        text = reader._canonical(reader.decode_text(record["stored_text"]))
        put((b"," if position else b"") + text + b"," + text)
        for view in ("w", "d"):
            row = {"record_id": record["record_id"] + "#" + view, "kind": "event",
                "time_utc": record["time_utc"], "cell_id": cell_id,
                "provenance": dict(record["provenance"], record=record["record_id"], view=view,
                                   ref_kind="sha256_of_projected_metadata", canonical_event=False)}
            _append_bounded(provenance, reader._canonical(row) + b"\n", limit)
    put(b"]}")
    return ids, meta.getvalue(), provenance.getvalue()


def _capacity_preflight(cells, record_count, base_bytes, capacity):
    """Reserve every cell before providers; serialized indexes are still checked."""
    if (type(record_count) is not int or not 0 < record_count <= capacity["records"]
            or not 0 < len(cells) <= 3 or not set(cells) <= {"general", "system", "learning"}
            or any(not items for items in cells.values())
            or sum(map(len, cells.values())) != record_count):
        raise ValueError("invalid_cell_records")
    # One fixed reservation covers memory+descriptor; each cell reserves its
    # receipt+pointer+single log line separately. Actual controls must fit too.
    total, prepared = base_bytes + capacity["control"], {}
    for cell_id, records in sorted(cells.items()):
        vector_bytes = len(records) * 2 * _ROW.size
        index_limit = vector_bytes + capacity["index_overhead"]
        total += vector_bytes + index_limit + capacity["control"]
        if capacity["package"] is not None and total > capacity["package"]:
            raise ValueError("package_over_budget")
        item = _cell_metadata(records, cell_id, capacity["artifact"])
        total += len(item[1]) + len(item[2])
        if capacity["package"] is not None and total > capacity["package"]:
            raise ValueError("package_over_budget")
        prepared[cell_id] = item
    return prepared


def _cell(records, cell_id, vector_rows, identity, projection_sha, cutoff, serialize, checkpoint,
          *, capacity=None, prepared=None):
    ids, texts, provenance, chunks = [], [], [], []
    for record in records if prepared is None else ():
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
    if prepared is not None:
        ids, meta_bytes, provenance_bytes = prepared
        chunks = [vector_rows[record["record_id"]] for record in records for _ in ("w", "d")]
    vectors = b"".join(chunks)
    index = serialize(vectors, reader._Q4_DIM)
    checkpoint()
    index_limit = MAX_ARTIFACT_BYTES if capacity is None else len(records) * 2 * _ROW.size + capacity["index_overhead"]
    if type(index) is not bytes or not 0 < len(index) <= index_limit:
        raise ValueError("invalid_serialized_index")
    artifacts = {"index": index, "vectors": vectors,
        "meta": meta_bytes if prepared is not None else reader._canonical(
            {"dim": reader._Q4_DIM, "doc_ids": ids, "texts": texts, "metadata": [{} for _ in ids]}),
        "provenance": provenance_bytes if prepared is not None else b"".join(provenance)}
    if capacity is None and any(len(raw) > MAX_ARTIFACT_BYTES for raw in artifacts.values()):
        raise ValueError("artifact_over_budget")
    if capacity is not None and (len(vectors) != len(records) * 2 * _ROW.size
            or (capacity["artifact"] is not None
                and any(len(artifacts[name]) > capacity["artifact"] for name in ("meta", "provenance")))):
        raise ValueError("artifact_over_budget")
    pairs = hashlib.sha256()
    for doc_id, row in zip(ids, chunks):
        pairs.update(doc_id.encode("utf-8") + b"\0" + row)
    content = {"cell_id": cell_id, "count": len(ids), "dim": reader._Q4_DIM,
        "cutoff_utc": cutoff, "encoder": identity,
        "projection_sha256": projection_sha, "projection_schema": reader._ALL_TASK_COMBINED_SCHEMA,
        "files": {name: reader._sha(raw) for name, raw in artifacts.items()},
        "ids_sha256": reader._sha("\n".join(ids).encode("utf-8")), "pairs_sha256": pairs.hexdigest()}
    artifacts["receipt"] = reader._canonical({"schema": "wd.bridge-mind-hex-cell.v1", "authority": "none",
        "scope": reader._ALL_TASK_ROUTE_SCOPE, "content": content,
        "content_sha256": reader._sha(reader._canonical(content))})
    pointer = {"generation": 1, "seq": 1, "receipt_sha256": reader._sha(artifacts["receipt"])}
    log = dict(pointer, schema="wd.bridge-mind-hex-cell-log.v1", prev_line_sha256="0" * 64)
    item = {"pointer": reader._canonical(pointer), "log": reader._canonical(log) + b"\n", "artifacts": artifacts}
    if capacity is not None and (len(item["pointer"]) + len(item["log"])
                                + len(artifacts["receipt"]) > capacity["control"]):
        raise ValueError("control_over_budget")
    return item, dict(pointer, count=len(ids))


def _package(snapshot, passage_encoder, index_serializer, index_decoder, encoder_binding_bytes,
             encoder_binding_sha256, index_binding_bytes, index_binding_sha256, capacity=None,
             source_verification=None):
    if source_verification is not None and (source_verification != "verified_immutable_snapshot_v1"
            or type(source_verification) is not str or capacity != {"streamed": True}):
        raise ValueError("invalid_source_verification")
    watcher = _SourceWatcher() if source_verification is not None else None
    try:
        return _package_inner(snapshot, passage_encoder, index_serializer, index_decoder, encoder_binding_bytes,
            encoder_binding_sha256, index_binding_bytes, index_binding_sha256, capacity, watcher)
    finally:
        if watcher is not None:
            watcher.stop()


def _package_inner(snapshot, passage_encoder, index_serializer, index_decoder, encoder_binding_bytes,
                   encoder_binding_sha256, index_binding_bytes, index_binding_sha256, capacity, watcher):
    full = capacity is not None and capacity.get("streamed") is True
    frozen = _snapshot(snapshot, capacity)
    encoder_binding = reader._pinned(encoder_binding_bytes, encoder_binding_sha256)
    index_binding = reader._pinned(index_binding_bytes, index_binding_sha256)
    reader._q4_bindings(encoder_binding, index_binding)
    if any(isinstance(callback, type) or not callable(callback)
           for callback in (passage_encoder, index_serializer, index_decoder)):
        raise ValueError("invalid_callback")
    projection, context, report = (reader._verified_complete(frozen) if full else reader._verified(frozen))
    if watcher is not None:
        # The immutable raw prefix and the reader-derived projection are linked
        # by the same pinned SHA-256 and cursor. Only screened projection bytes
        # are parsed and provided to the callbacks; raw source never leaves here.
        immutable_prefix = _immutable_prefix(context)
        projection = json.loads(frozen["projection_bytes"].decode("utf-8"),
                                object_pairs_hook=reader._unique_pairs, parse_constant=reader._bad_constant)
        if reader._serialized(projection) != frozen["projection_bytes"]:
            raise ValueError("projection_changed")
        watcher.start(frozen, report)
    records = projection["messages"]["records"]
    if (projection["schema"] != reader._ALL_TASK_COMBINED_SCHEMA
            or not records or (not full and len(records) > _cap(capacity, "records", MAX_RECORDS))
            or len({record["record_id"] for record in records}) != len(records)):
        raise ValueError("invalid_records")
    texts = [reader.decode_text(record["stored_text"]) for record in records]
    text_size = sum(len(text.encode("utf-8")) for text in texts)
    if not full and text_size > _cap(capacity, "text", MAX_TEXT_BYTES):
        raise ValueError("text_over_budget")
    cells = {}
    for record in records:
        cell_id = reader._Q4_PLACEMENT[record["meta"]["type"]]
        cells.setdefault(cell_id, []).append(record)
    if full:
        # No caller-declared RAM reservation: exact cell/byte limits come from
        # these source-verified records. Allocation remains subject to the OS.
        capacity = {"records": len(records), "text": text_size,
                    "artifact": None, "package": None, "index_overhead": 65536, "control": 65536}
    if watcher is None:
        checkpoint = lambda: _unchanged(snapshot, frozen, report, complete=full)
    else:
        def checkpoint():
            watcher.check()
            if snapshot != frozen:
                raise ValueError("snapshot_changed")
            controls = dict(snapshot)
            controls.pop("projection_bytes")
            controls.pop("projection_sha256")
            reader._inputs(controls, complete_source=True)
            reader._q4_bindings(reader._pinned(encoder_binding_bytes, encoder_binding_sha256),
                                reader._pinned(index_binding_bytes, index_binding_sha256))
    base_bytes = len(frozen["projection_bytes"]) + len(encoder_binding_bytes) + len(index_binding_bytes)
    prepared = (_capacity_preflight(cells, len(records), base_bytes, capacity)
                if capacity is not None else {})
    complete_limits = {}
    if full:
        for cell_id, items in cells.items():
            count = 2 * len(items)
            complete_limits[cell_id] = {"count": count, "meta": len(prepared[cell_id][1]),
                "provenance": len(prepared[cell_id][2]), "vectors": count * _ROW.size,
                "index": count * _ROW.size + capacity["index_overhead"]}
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
    artifact_total = base_bytes
    for cell_id, items in sorted(cells.items()):
        snapshots[cell_id], pointers[cell_id] = _cell(
            items, cell_id, vectors, identity, frozen["projection_sha256"], cutoff,
            index_serializer, checkpoint, capacity=capacity, prepared=prepared.pop(cell_id, None))
        item = snapshots[cell_id]
        artifact_total += len(item["pointer"]) + len(item["log"]) + sum(map(len, item["artifacts"].values()))
        if _cap(capacity, "package", MAX_PACKAGE_BYTES) is not None and artifact_total > _cap(capacity, "package", MAX_PACKAGE_BYTES):
            raise ValueError("package_over_budget")
        if capacity is not None:
            for record in items:
                del vectors[record["record_id"]]
    memory = {"schema": "wd.bridge-mind-hex-memory-step2.v1", "authority": "none",
        "scope": reader._ALL_TASK_ROUTE_SCOPE, "dim": reader._Q4_DIM, "encoder": identity,
        "projection_schema": reader._ALL_TASK_COMBINED_SCHEMA,
        "projection_sha256": frozen["projection_sha256"], "cutoff_utc": cutoff, "cells": pointers,
        "records": len(records), "views": ["w", "d"], "independent_views": False}
    memory_bytes = reader._canonical(memory)
    descriptor = {"schema": "wd.bridge-mind-q4-memory-descriptor.v3", "scope": reader._ALL_TASK_ROUTE_SCOPE,
                  "memory_sha256": reader._sha(memory_bytes), "dim": reader._Q4_DIM, "encoder": identity}
    descriptor_bytes = reader._canonical(descriptor)
    if capacity is not None and len(memory_bytes) + len(descriptor_bytes) > capacity["control"]:
        raise ValueError("control_over_budget")
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
        if _cap(capacity, "package", MAX_PACKAGE_BYTES) is not None and total > _cap(capacity, "package", MAX_PACKAGE_BYTES):
            raise ValueError("package_over_budget")
        captured[cell_id] = (reader._q4_complete_cell_controls(cell_id, item, pointers[cell_id], memory,
                                                             record_map, complete_limits[cell_id]) if full else
                             reader._q4_cell_controls(cell_id, item, pointers[cell_id], memory, record_map))
    # All cells' controls precede any decoder. Reconstruction is verified with
    # the reader's retained-vector and pair checks, not the serializer's claim.
    def checked_decoder(raw):
        checkpoint()
        index = index_decoder(raw)
        checkpoint()
        return _FullCheckedIndex(index, checkpoint) if full else index

    if full:
        source_limits = {}
        for cell_id, item in snapshots.items():
            digest = reader._sha(item["artifacts"]["index"])
            count = complete_limits[cell_id]["count"]
            if digest in source_limits and source_limits[digest] != count:
                raise ValueError("conflicting_index_counts")
            source_limits[digest] = count
        provider = reader._CompleteIndexAdapter(checked_decoder, source_limits)
        reader._q4_decode_cells_complete_v1(captured, provider, record_map)
    else:
        provider = reader._ReadonlyIndexAdapter(checked_decoder, index_binding,
            [reader._sha(item["artifacts"]["index"]) for item in snapshots.values()],
            allow_bounded_v3=True)
        reader._q4_decode_cells_bounded_v3(captured, provider, record_map)
    checkpoint()
    if watcher is not None:
        watcher.stop()
        _unchanged(snapshot, frozen, report, complete=True)
        if (reader._sha(immutable_prefix) != report["source_prefix"]["sha256"]
                or reader._serialized(projection) != frozen["projection_bytes"]):
            raise ValueError("immutable_snapshot_changed")
    return output


def package_content_snapshot(*, enabled=False, snapshot=None, passage_encoder=None, index_serializer=None,
                             index_decoder=None, encoder_binding_bytes=None, encoder_binding_sha256=None,
                             index_binding_bytes=None, index_binding_sha256=None, capacity_profile=None,
                             source_verification=None):
    """Return detached v3 query artifact arguments or a fixed refusal.

    Only ``enabled is True`` opts in. Snapshot paths remain caller-supplied and
    reader-verified. Each package's local cell generation is 1; it is a new
    immutable artifact set, never a claim to advance source/runtime generation.
    Caller-owned providers, source inputs and query stream remain separate from
    the returned artifact arguments. A consumer must use query_content_snapshot
    to re-verify this package at use time; build success grants no authority.
    ``capacity_profile=None`` keeps the original small limits. The exact named
    v3 capacity profile expands only finite capacity, never the B16 passage or
    source-checkpoint protocol. Its index allowance is float32 rows plus 64 KiB;
    a serializer exceeding that allowance refuses without a fallback.
    ``source_verification="verified_immutable_snapshot_v1"`` is opt-in and
    valid only with the full-prefix profile. It validates the complete source
    at both endpoints and periodically in an owned background thread; batches
    use the immutable, SHA-bound screened projection. Changes restored between
    scans may go unnoticed. This is not continuous source authentication, a
    live index publisher, or a guarantee about what happens after return.
    """
    output = None
    succeeded = False
    try:
        if enabled is not True:
            raise ValueError("disabled")
        capacity = _capacity(capacity_profile)
        output = _package(snapshot, passage_encoder, index_serializer, index_decoder, encoder_binding_bytes,
                          encoder_binding_sha256, index_binding_bytes, index_binding_sha256, capacity,
                          source_verification)
        succeeded = True
    except Exception:
        pass
    if not succeeded:
        raise SnapshotPackageRefused("content_snapshot_package_refused") from None
    return output
