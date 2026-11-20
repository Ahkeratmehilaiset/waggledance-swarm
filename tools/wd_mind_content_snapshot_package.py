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
or additional normalization is performed. Provider failures are converted to a
fixed refusal. Keyboard and asyncio cancellation signals propagate without
provider-supplied details; cancellation groups are flattened and sanitized.
If the caller is already handling an exception, Python can retain that
caller-owned exception in ``__context__``; it is suppressed in the traceback.

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
The legacy verified_immutable_snapshot_v1 option retains the verified screened
projection as immutable bytes; it checks the raw prefix in bounded blocks and
retains its digest, not an immutable copy of the raw prefix. Endpoint and watcher
source verification still apply; same-user source ABA remains unproven.
"""
from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import struct
import hmac
import math
import sys
import threading
from datetime import datetime, timezone
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
# The full-prefix verifier has measured runs above two minutes on the target
# host. This is a bounded close wait, not a deadline for synchronous providers.
_WATCH_JOIN_TIMEOUT_SECONDS = 600.0
_WATCH_JOIN_MAX_SECONDS = 600.0
_ROW = struct.Struct("<384f")
_PACKAGE_INTERNAL_HOLD = object()

# Separate imports of this module share the owner within the interpreter.
# A live non-daemon thread can still keep its process alive after a refusal.
_WATCHER_REGISTRY = vars(builtins).setdefault(
    "_wd_mind_snapshot_build_watcher_registry_v1", {"lock": threading.Lock(), "owner": None})


class SnapshotPackageRefused(ValueError):
    """Fixed-token refusal without source paths, content or callback details."""


class SnapshotPackageHold(SnapshotPackageRefused):
    """An opt-in verifier is still owned; no package has been published."""


def snapshot_build_watcher_status():
    """Return process-local watcher state without source or policy data."""
    with _WATCHER_REGISTRY["lock"]:
        owner = _WATCHER_REGISTRY["owner"]
        if owner is None or (owner.finished.is_set() and not owner.alive()):
            return {"state": "idle"}
        return {"state": "watcher_stopping" if owner.finished.is_set() else "building"}


def _reserve_watcher():
    with _WATCHER_REGISTRY["lock"]:
        owner = _WATCHER_REGISTRY["owner"]
        if owner is not None and not (owner.finished.is_set() and not owner.alive()):
            raise SnapshotPackageHold("content_snapshot_watcher_hold")
        watcher = _SourceWatcher()
        _WATCHER_REGISTRY["owner"] = watcher
        return watcher


def _release_watcher(watcher):
    with _WATCHER_REGISTRY["lock"]:
        watcher.finished.set()
        if _WATCHER_REGISTRY["owner"] is watcher and not watcher.alive():
            _WATCHER_REGISTRY["owner"] = None


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


def _immutable_prefix(context, *, prefix_offset=None):
    """Return the verified raw-prefix digest without retaining raw source bytes.

    The historical name is retained for internal callers. The immutable data
    retained during provider calls is the screened projection, not the raw log.
    When requested, return the full and shorter prefix digests from the same
    fenced scan. The shorter digest is not accepted unless the full digest is
    independently bound to the approved snapshot.
    """
    messages, anchor = context["messages"], context["decision"]["source_prefix"]
    cursor = messages["end_cursor"]
    if prefix_offset is not None and (type(prefix_offset) is not int or not 0 <= prefix_offset <= cursor.offset):
        raise ValueError("source_offset")
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
        # Hash the anchored prefix in bounded blocks. The screened immutable
        # projection, rather than a second copy of the raw log, is retained.
        remaining = cursor.offset
        digest, last = hashlib.sha256(), b""
        shorter = hashlib.sha256() if prefix_offset is not None else None
        shorter_remaining = prefix_offset
        while remaining:
            part = stream.read(min(1024 * 1024, remaining))
            if not part:
                raise ValueError("source_truncated")
            digest.update(part)
            if shorter is not None and shorter_remaining:
                included = min(shorter_remaining, len(part))
                shorter.update(memoryview(part)[:included])
                shorter_remaining -= included
            last = part[-1:]
            remaining -= len(part)
        prefix_sha256 = digest.hexdigest()
        if (last != b"\n" or not reader.hmac.compare_digest(prefix_sha256, anchor["sha256"])
                or bridge._file_identity(stream) != cursor.file_identity
                or os.fstat(stream.fileno()).st_size < size):
            raise ValueError("source_changed")
        fence()
        with bridge._open_log(source) as current:
            if bridge._file_identity(current) != cursor.file_identity or os.fstat(current.fileno()).st_size < size:
                raise ValueError("source_replaced")
        fence()
    if shorter is not None:
        if shorter_remaining:
            raise ValueError("source_truncated")
        return prefix_sha256, shorter.hexdigest()
    return prefix_sha256


class _SourceWatcher:
    """Own and join one background verifier; failure always invalidates output."""
    def __init__(self):
        self.stop_event = threading.Event()
        self.failed = threading.Event()
        self.finished = threading.Event()
        self.thread = None
        self.join_attempted = False

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

    def alive(self):
        return self.thread is not None and self.thread.is_alive()

    def stop(self, timeout=None):
        self.stop_event.set()
        if self.thread is not None and self.thread.ident is not None and not self.join_attempted:
            self.join_attempted = True
            self.thread.join(_WATCH_JOIN_TIMEOUT_SECONDS if timeout is None else timeout)
        if self.alive():
            raise SnapshotPackageHold("content_snapshot_watcher_hold")
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
             source_verification=None, watcher_join_timeout_seconds=None):
    if source_verification is not None and (source_verification != "verified_immutable_snapshot_v1"
            or type(source_verification) is not str or capacity != {"streamed": True}):
        raise ValueError("invalid_source_verification")
    if watcher_join_timeout_seconds is not None and (
            source_verification is None or type(watcher_join_timeout_seconds) not in (int, float)
            or not math.isfinite(watcher_join_timeout_seconds)
            or not 0 < watcher_join_timeout_seconds <= _WATCH_JOIN_MAX_SECONDS):
        raise ValueError("invalid_watcher_join_timeout")
    watcher, output, problem, cleanup_problem = None, None, None, None
    held = False
    try:
        if source_verification is not None:
            try:
                watcher = _reserve_watcher()
            except SnapshotPackageHold:
                # The registry, not a provider exception, establishes HOLD.
                held = True
        if not held:
            output = _package_inner(snapshot, passage_encoder, index_serializer, index_decoder, encoder_binding_bytes,
                encoder_binding_sha256, index_binding_bytes, index_binding_sha256, capacity, watcher,
                watcher_join_timeout_seconds)
    except BaseException as exc:
        problem = exc
    finally:
        if watcher is not None:
            try:
                watcher.stop(watcher_join_timeout_seconds)
            except SnapshotPackageHold:
                held = True
            except BaseException as exc:
                cleanup_problem = exc
            finally:
                _release_watcher(watcher)
    # Preserve cancellation even if the watcher must remain owned in HOLD.
    # The retained owner remains observable through the registry status.
    signals = [reader._provider_cancellation(exc) for exc in (problem, cleanup_problem) if exc is not None]
    signals = [signal for signal in signals if signal is not None]
    if signals:
        problem = cleanup_problem = None
        if len(signals) == 1:
            raise signals[0] from None
        raise BaseExceptionGroup('provider_cancelled', signals) from None
    if held:
        return _PACKAGE_INTERNAL_HOLD
    if cleanup_problem is not None:
        raise cleanup_problem from None
    if problem is not None:
        raise problem from None
    return output


def _package_inner(snapshot, passage_encoder, index_serializer, index_decoder, encoder_binding_bytes,
                   encoder_binding_sha256, index_binding_bytes, index_binding_sha256, capacity, watcher,
                   watcher_join_timeout_seconds):
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
        # The verified projection is re-parsed below. Release it first so both
        # complete record trees do not coexist during that parse.
        del projection
        # The screened immutable projection is bound to the streamed raw-prefix
        # digest and cursor. Raw source bytes are not retained after this check.
        verified_prefix_sha256 = _immutable_prefix(context)
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
        # Keep the final full check after the owned verifier has finished:
        # verifying concurrently would leave a gap before publication.
        watcher.stop(watcher_join_timeout_seconds)
        _unchanged(snapshot, frozen, report, complete=True)
        if (verified_prefix_sha256 != report["source_prefix"]["sha256"]
                or reader._serialized(projection) != frozen["projection_bytes"]):
            raise ValueError("immutable_snapshot_changed")
    return output


def build_narrow_source_manifest(*, enabled=False, snapshot=None, descriptor_bytes=None,
                                 descriptor_sha256=None, memory_bytes=None, memory_sha256=None):
    """Build source row locations after a complete verification, without writing.

    A publisher must independently place these exact bytes and their digest in
    an authorized deployment manifest before any narrow query can use them.
    This does not authenticate the publisher, sender or operator, and does not
    change the producer's existing per-callback complete-source checkpoints.
    """
    output = None
    try:
        if enabled is not True or type(snapshot) is not dict:
            raise ValueError('disabled')
        projection, context, before = reader._verified_complete(snapshot)
        descriptor = reader._pinned(descriptor_bytes, descriptor_sha256)
        memory = reader._pinned(memory_bytes, memory_sha256, limit=1024 * 1024)
        records = projection['messages']['records']
        if (type(descriptor) is not dict or descriptor.get('schema') != 'wd.bridge-mind-q4-memory-descriptor.v3'
                or descriptor.get('scope') != reader._ALL_TASK_ROUTE_SCOPE
                or descriptor.get('memory_sha256') != memory_sha256 or type(memory) is not dict
                or memory.get('schema') != 'wd.bridge-mind-hex-memory-step2.v1'
                or memory.get('projection_sha256') != snapshot['projection_sha256']
                or type(memory.get('records')) is not int or memory['records'] != len(records)):
            raise ValueError('memory_binding')
        references = {record['provenance']['source_event_ref'] for record in records}
        if not references or any(not reader._hex(ref) for ref in references):
            raise ValueError('references')
        entries = reader._narrow_row_index(context, references)
        _, _, after = reader._verified_complete(snapshot)
        if before != after:
            raise ValueError('source_changed')
        cursor = context['messages']['end_cursor']
        manifest = {'schema': reader._NARROW_SCHEMA, 'authority': 'none',
                    'source_prefix': dict(context['decision']['source_prefix'], file_identity=cursor.file_identity),
                    'source_locations': {'log': context['messages']['source_path'],
                                         'generation': context['messages']['generation_path']},
                    'projection_sha256': snapshot['projection_sha256'],
                    'decision_sha256': context['decision_sha256'], 'descriptor_sha256': descriptor_sha256,
                    'memory_sha256': memory_sha256, 'entries': entries}
        raw = reader._canonical(manifest)
        output = {'manifest_bytes': raw, 'manifest_sha256': reader._sha(raw)}
    except Exception:
        pass
    if output is None:
        raise SnapshotPackageRefused('content_snapshot_package_refused') from None
    return output


def _consumer_bundle_controls(bundle_root, manifest_pin):
    """Read code, fleet and policy from one independently pinned bundle."""
    bridge, absent = reader._narrow_dependencies()
    root = Path(bundle_root)
    if not root.is_absolute() or '..' in root.parts or not reader._hex(manifest_pin):
        raise ValueError('bundle')
    manifest_raw = absent._read_pinned(root / 'deployment-manifest.json', manifest_pin, 4 * 1024 * 1024)
    manifest = json.loads(manifest_raw.decode('utf-8-sig'), object_pairs_hook=reader._unique_pairs,
                          parse_constant=reader._bad_constant)
    if (type(manifest) is not dict or type(manifest.get('schema_version')) is not int
            or manifest['schema_version'] != 1 or type(manifest.get('files')) is not dict):
        raise ValueError('manifest')
    files = manifest['files']
    modules = ((sys.modules[__name__], 'tools-bootstrap/tools/wd_mind_content_snapshot_package.py'),
               (reader, 'tools-bootstrap/tools/wd_mind_content_query_readonly.py'),
               (absent, 'tools-bootstrap/tools/wd_mind_absent_generation_prefix_readonly.py'),
               (bridge, 'tools-bootstrap/waggledance/core/bridge_log_reader.py'))
    for module, relative in modules:
        path = root.joinpath(*relative.split('/'))
        origin = getattr(module, '__file__', None)
        if (type(origin) is not str or os.path.normcase(os.path.abspath(origin)) !=
                os.path.normcase(os.path.abspath(path))):
            raise ValueError('origin')
        absent._read_pinned(path, files.get(relative), 8 * 1024 * 1024)
    source, sidecar, fleet_pin = absent._deployment_source(bundle_root, manifest_pin)
    fleet_raw = absent._read_pinned(root / 'wd-fleet.json', fleet_pin, 1024 * 1024)
    policy_raw = absent._read_pinned(root / 'wd-mind-message-policy.json',
                                     files.get('wd-mind-message-policy.json'), 65536)
    fleet = json.loads(fleet_raw.decode('utf-8-sig'), object_pairs_hook=reader._unique_pairs,
                       parse_constant=reader._bad_constant)
    policy = json.loads(policy_raw.decode('utf-8'), object_pairs_hook=reader._unique_pairs,
                        parse_constant=reader._bad_constant)
    if type(fleet) is not dict or type(fleet.get('lanes')) is not list or type(fleet.get('tools_supervisor')) is not dict:
        raise ValueError('fleet')
    lanes, supervisor = fleet['lanes'], fleet['tools_supervisor']
    if any(type(item) is not dict or type(item.get('agent')) is not str or type(item.get('agent_uuid')) is not str
           for item in lanes + [supervisor]):
        raise ValueError('roster')
    roster = {'schema': 'wd.q4-roster-snapshot.v1',
              'lanes': sorted(({'agent': item['agent'], 'agent_uuid': item['agent_uuid']} for item in lanes),
                              key=lambda item: item['agent']),
              'tools_supervisor': {'agent': supervisor['agent'], 'agent_uuid': supervisor['agent_uuid']}}
    reader._roster(roster)
    reader._message_policy(policy, None)
    return str(source), str(sidecar), fleet_pin, reader._canonical(roster), policy_raw


class _ConsumerOwnedGate(reader._NarrowReadGate):
    """Gate whose manifest was built locally after complete source verification."""
    def __init__(self, bundle_root, manifest_pin, values, owned_manifest):
        super().__init__(bundle_root, manifest_pin, values)
        self._owned_manifest = owned_manifest
        self._owned_digest = reader._sha(owned_manifest)

    def _manifest(self):
        if not hmac.compare_digest(reader._sha(self._owned_manifest), self._owned_digest):
            raise ValueError('owned_manifest_changed')
        value = json.loads(self._owned_manifest.decode('utf-8'), object_pairs_hook=reader._unique_pairs,
                           parse_constant=reader._bad_constant)
        if (type(value) is not dict or reader._canonical(value) != self._owned_manifest
                or set(value) != {'schema', 'authority', 'source_prefix', 'source_locations', 'projection_sha256',
                                  'decision_sha256', 'descriptor_sha256', 'memory_sha256', 'entries'}
                or value['schema'] != reader._NARROW_SCHEMA or value['authority'] != 'none'
                or value['descriptor_sha256'] != self._descriptor or value['memory_sha256'] != self._memory
                or type(value['entries']) is not dict):
            raise ValueError('owned_manifest')
        return value


class _ConsumerSourceOwner:
    """Local source owner; proposals are untrusted, and this is not an IPC API."""
    __slots__ = ('_root', '_pin', '_controls', '_snapshot', '_gate')

    def __init__(self, root, pin, controls, snapshot, gate):
        self._root, self._pin, self._controls = root, pin, controls
        self._snapshot, self._gate = snapshot, gate

    def resolve(self, *, proposals, lane, scope, source_budget=None):
        output = None
        try:
            if (_consumer_bundle_controls(self._root, self._pin) != self._controls
                    or lane not in reader._LANES or scope not in ('own', 'shared')
                    or type(proposals) is not list or not 1 <= len(proposals) <= 3):
                raise ValueError('request')
            candidates = []
            for item in proposals:
                if (type(item) is not dict or set(item) != {'ref_sha256','doc_id_sha256','source_event_sha256'}
                        or any(not reader._hex(value) for value in item.values())):
                    raise ValueError('proposal')
                candidates.append({'ref_sha256': item['ref_sha256'], 'doc_id_sha256': item['doc_id_sha256'],
                                   'ref_kind': 'sha256_of_projected_metadata', 'canonical_event': False})
            now = datetime.now(timezone.utc)
            resolved = (self._gate._resolve(candidates, reader._utc_text(now), self._snapshot)
                        if source_budget is None else
                        self._gate._resolve(candidates, reader._utc_text(now), self._snapshot,
                                            source_budget=source_budget))
            if len(resolved) != len(proposals):
                raise ValueError('candidate_count')
            projection = json.loads(self._snapshot['projection_bytes'].decode('utf-8'),
                                    object_pairs_hook=reader._unique_pairs, parse_constant=reader._bad_constant)
            records = {(reader._sha(row['provenance']['event_ref'].encode()),
                        reader._sha(row['record_id'].encode())): row
                       for row in projection['messages']['records']}
            hits = []
            for proposal, item in zip(proposals, resolved):
                record = records[(item['ref_sha256'], item['doc_id_sha256'])]
                source_ref = item['source_reference']['sha256']
                if (not hmac.compare_digest(source_ref, proposal['source_event_sha256'])
                        or (scope == 'own' and record['meta']['agent'] != lane)):
                    raise ValueError('source')
                floor = reader._utc(record['time_utc'])
                age = (datetime.now(timezone.utc) - floor).total_seconds()
                if age < 0 or reader._utc(record['available_utc']) > datetime.now(timezone.utc):
                    raise ValueError('stale')
                hits.append({'text': item['stored_text'], 'source_event_sha256': source_ref,
                             'event_time_utc_floor': record['time_utc'], 'source_agent': record['meta']['agent']})
            if _consumer_bundle_controls(self._root, self._pin) != self._controls:
                raise ValueError('controls_changed')
            if type(self._gate) is _ConsumerFlatGate:
                self._gate._check_delivery_binding()
            # Recheck time at delivery; historical records remain eligible.
            finished = datetime.now(timezone.utc)
            for hit in hits:
                age = (finished - reader._utc(hit['event_time_utc_floor'])).total_seconds()
                if age < 0:
                    raise ValueError('delivery_stale')
                hit['event_age_seconds_upper_bound'] = math.ceil(age)
            output = {'authority': 'none', 'sender_authentication': 'UNKNOWN',
                      'same_user_aba': 'UNKNOWN', 'index_match': 'UNKNOWN', 'results': hits}
        except Exception:
            pass
        if output is None:
            raise SnapshotPackageRefused('content_snapshot_package_refused') from None
        return output


def create_consumer_source_owner(*, enabled=False, bundle_root=None, expected_manifest_sha256=None,
                                 snapshot=None, descriptor_bytes=None, descriptor_sha256=None,
                                 memory_bytes=None, memory_sha256=None):
    """Build a process-local anchor after complete independent source checks.

    The manifest pin must come from an independently trusted caller. No pipe
    peer, supplied content or supplied source hash is an authorization source.
    Startup/IPC delivery and ongoing background verification are not provided.
    """
    output = None
    try:
        if enabled is not True or type(bundle_root) is not str or type(snapshot) is not dict:
            raise ValueError('input')
        controls = _consumer_bundle_controls(bundle_root, expected_manifest_sha256)
        frozen = dict(snapshot, messages=dict(snapshot['messages']))
        source, sidecar, _, roster, policy = controls
        if (os.path.normcase(os.path.abspath(frozen['messages']['source_path'])) != os.path.normcase(os.path.abspath(source))
                or os.path.normcase(os.path.abspath(frozen['messages']['generation_path'])) != os.path.normcase(os.path.abspath(sidecar))
                or frozen['roster_bytes'] != roster or frozen['messages']['policy_bytes'] != policy):
            raise ValueError('source_controls')
        built = build_narrow_source_manifest(enabled=True, snapshot=frozen,
            descriptor_bytes=descriptor_bytes, descriptor_sha256=descriptor_sha256,
            memory_bytes=memory_bytes, memory_sha256=memory_sha256)
        values = {'descriptor_sha256': descriptor_sha256, 'memory_sha256': memory_sha256}
        gate = _ConsumerOwnedGate(bundle_root, expected_manifest_sha256, values, built['manifest_bytes'])
        gate.verify_snapshot(frozen)
        if _consumer_bundle_controls(bundle_root, expected_manifest_sha256) != controls:
            raise ValueError('controls_changed')
        output = _ConsumerSourceOwner(bundle_root, expected_manifest_sha256, controls, frozen, gate)
    except Exception:
        pass
    if output is None:
        raise SnapshotPackageRefused('content_snapshot_package_refused') from None
    return output


def _consumer_flat_encoder(bundle_root, manifest_pin):
    """Derive flat query binding from the caller-pinned worker and policy."""
    _, absent = reader._narrow_dependencies()
    root = Path(bundle_root)
    manifest_raw = absent._read_pinned(root / 'deployment-manifest.json', manifest_pin, 4 * 1024 * 1024)
    manifest = json.loads(manifest_raw.decode('utf-8-sig'), object_pairs_hook=reader._unique_pairs,
                          parse_constant=reader._bad_constant)
    files = manifest['files']
    binding_raw = absent._read_pinned(root / 'wd-mind-e5-encoder-binding.json',
                                      files.get('wd-mind-e5-encoder-binding.json'), 65536)
    binding = json.loads(binding_raw.decode('utf-8'), object_pairs_hook=reader._unique_pairs,
                         parse_constant=reader._bad_constant)
    worker = 'tools-bootstrap/tools/wd_mind_e5_worker.py'
    absent._read_pinned(root.joinpath(*worker.split('/')), files.get(worker), 8 * 1024 * 1024)
    query = {'prefix': 'query: ', 'truncate_chars': 2000, 'normalize': True, 'dtype': 'float32', 'dim': 384}
    if (type(binding) is not dict
            or set(binding) != {'schema', 'encoder', 'code_sha256', 'query_policy', 'passage_policy'}
            or binding['schema'] != 'wd.mind-memory-encoder-binding.v1'
            or binding['code_sha256'] != files[worker].lower()
            or binding['query_policy'] != query or binding['passage_policy'] != dict(query, prefix='passage: ')
            or any(type(policy.get('normalize')) is not bool or type(policy.get('dim')) is not int
                   or type(policy.get('truncate_chars')) is not int
                   for policy in (binding['query_policy'], binding['passage_policy']))):
        raise ValueError('encoder')
    reader._q4_identity(binding['encoder'])
    return reader._canonical({'schema': 'wd.mind-readonly-encoder-binding.v1', 'encoder': binding['encoder'],
                              'code_sha256': binding['code_sha256'], 'query_policy': query})


def _verified_flat_manifest(raw, digest, snapshot, projection, ids, vectors, encoder, *, check_vectors):
    """Validate the distinct flat memory format against locally derived rows."""
    if (type(raw) is not bytes or not reader._hex(digest) or not hmac.compare_digest(reader._sha(raw), digest)
            or type(ids) is not tuple or type(vectors) is not bytes or type(encoder) is not bytes
            or type(check_vectors) is not bool):
        raise ValueError('flat_inputs')
    manifest = json.loads(raw.decode('utf-8'), object_pairs_hook=reader._unique_pairs,
                          parse_constant=reader._bad_constant)
    if (type(manifest) is not dict or reader._canonical(manifest) != raw
            or set(manifest) != {'schema','authority','projection_sha256','records','encoder_binding_sha256',
                                 'ids_sha256','vectors_sha256','pairs_sha256'}
            or manifest['schema'] != 'wd.mind-flat-f32-memory.v1' or manifest['authority'] != 'none'
            or manifest['projection_sha256'] != snapshot['projection_sha256']
            or type(manifest['records']) is not int or manifest['records'] <= 0
            or manifest['records'] != len(ids) or len(vectors) != len(ids) * _ROW.size
            or manifest['encoder_binding_sha256'] != reader._sha(encoder)):
        raise ValueError('flat_manifest')
    records = projection['messages']['records']
    record_ids = [record['record_id'] for record in records]
    if (any(type(item) is not str for item in ids) or len(set(record_ids)) != len(record_ids)
            or ids != tuple(sorted(record_ids))
            or manifest['ids_sha256'] != reader._sha(reader._canonical(list(ids)))
            or manifest['vectors_sha256'] != reader._sha(vectors)):
        raise ValueError('flat_records')
    pairs = hashlib.sha256()
    for index, record_id in enumerate(ids):
        vector = vectors[index * _ROW.size:(index + 1) * _ROW.size]
        if check_vectors:
            reader._q4_vector(_ROW.unpack(vector))
        pairs.update(record_id.encode('utf-8') + b'\0' + vector)
    if manifest['pairs_sha256'] != pairs.hexdigest():
        raise ValueError('flat_pairs')
    return manifest


class _ConsumerFlatGate(reader._NarrowReadGate):
    """Locally built flat-schema row map; the pipe peer supplies no trust pin."""
    def __init__(self, bundle_root, manifest_pin, raw_map, flat_raw, flat_digest, ids, vectors, encoder):
        self._bundle_root, self._manifest_pin = bundle_root, manifest_pin
        self._raw_map, self._map_digest = raw_map, reader._sha(raw_map)
        self._flat_raw, self._flat_digest = flat_raw, flat_digest
        self._ids, self._vectors, self._encoder = ids, vectors, encoder

    def _manifest(self):
        if not hmac.compare_digest(reader._sha(self._raw_map), self._map_digest):
            raise ValueError('flat_source_map')
        value = json.loads(self._raw_map.decode('utf-8'), object_pairs_hook=reader._unique_pairs,
                           parse_constant=reader._bad_constant)
        if (type(value) is not dict or reader._canonical(value) != self._raw_map
                or set(value) != {'schema','authority','source_prefix','source_locations','projection_sha256',
                                  'decision_sha256','flat_memory_sha256','entries'}
                or value['schema'] != 'wd.mind-flat-source-map.v1' or value['authority'] != 'none'
                or value['flat_memory_sha256'] != self._flat_digest or type(value['entries']) is not dict):
            raise ValueError('flat_source_map')
        return value

    def _check_delivery_binding(self):
        if _consumer_flat_encoder(self._bundle_root, self._manifest_pin) != self._encoder:
            raise ValueError('encoder_changed')

    def verify_snapshot(self, snapshot):
        if type(snapshot) is not dict:
            raise ValueError('snapshot')
        self._check_delivery_binding()
        inputs = dict(snapshot)
        raw, digest = inputs.pop('projection_bytes', None), inputs.pop('projection_sha256', None)
        if type(raw) is not bytes or not raw or not reader._hex(digest) or not hmac.compare_digest(reader._sha(raw), digest):
            raise ValueError('projection')
        context = reader._inputs(inputs, complete_source=True)
        projection = json.loads(raw.decode('utf-8'), object_pairs_hook=reader._unique_pairs,
                                parse_constant=reader._bad_constant)
        if (reader._serialized(projection) != raw or type(projection) is not dict
                or projection.get('schema') != reader._ALL_TASK_COMBINED_SCHEMA
                or type(projection.get('messages')) is not dict
                or projection['messages'].get('schema') != reader._ALL_TASK_MESSAGE_SCHEMA
                or type(projection['messages'].get('records')) is not list):
            raise ValueError('projection_schema')
        value = self._manifest()
        cursor = context['messages']['end_cursor']
        anchored = dict(context['decision']['source_prefix'], file_identity=cursor.file_identity)
        locations = {'log': context['messages']['source_path'], 'generation': context['messages']['generation_path']}
        if (value['source_prefix'] != anchored or value['source_locations'] != locations
                or value['projection_sha256'] != digest or value['decision_sha256'] != context['decision_sha256']):
            raise ValueError('source_binding')
        _verified_flat_manifest(self._flat_raw, self._flat_digest, snapshot, projection,
                                self._ids, self._vectors, self._encoder, check_vectors=False)
        refs = {record['provenance']['source_event_ref'] for record in projection['messages']['records']}
        if set(value['entries']) != refs or any(not reader._hex(ref) for ref in refs):
            raise ValueError('coverage')
        for entry in value['entries'].values():
            if (type(entry) is not dict or set(entry) != {'offset','length','raw_sha256'}
                    or type(entry['offset']) is not int or type(entry['length']) is not int
                    or entry['offset'] < 0 or not 0 < entry['length'] <= 4 * 1024 * 1024
                    or entry['offset'] + entry['length'] > cursor.offset or not reader._hex(entry['raw_sha256'])):
                raise ValueError('row_bounds')
        bridge, absent = reader._narrow_dependencies()
        source, sidecar = Path(context['messages']['source_path']), Path(context['messages']['generation_path'])
        with bridge._open_log(source) as stream:
            reader._narrow_fence(stream, source, sidecar, cursor, bridge, absent)
        report = {'status':'MATCH', 'projection_sha256':digest, 'decision_sha256':context['decision_sha256'],
                  'source_prefix':dict(context['decision']['source_prefix']), 'doc_commit':None,
                  'operator_scope_authorization':'UNKNOWN', 'required_release_task_list_sha256':None}
        return projection, context, report


def create_consumer_flat_source_owner(*, enabled=False, bundle_root=None, expected_manifest_sha256=None,
                                      snapshot=None, flat_manifest_bytes=None, flat_manifest_sha256=None,
                                      ids=None, vectors=None, encoder_binding_bytes=None, source_budget=None):
    """Independently build a process-local flat source gate; never trust IPC pins.

    Full source verification occurs while constructing this owner. Peer identity,
    index selection, continuous monitoring, same-user ABA and startup delivery
    remain unproven; query proposals are treated as untrusted references only.
    """
    output = None
    try:
        if enabled is not True or type(bundle_root) is not str or type(snapshot) is not dict:
            raise ValueError('input')
        controls = _consumer_bundle_controls(bundle_root, expected_manifest_sha256)
        encoder = _consumer_flat_encoder(bundle_root, expected_manifest_sha256)
        if encoder_binding_bytes != encoder:
            raise ValueError('encoder_binding')
        frozen = dict(snapshot, messages=dict(snapshot['messages']))
        source, sidecar, _, roster, policy = controls
        if (os.path.normcase(os.path.abspath(frozen['messages']['source_path'])) != os.path.normcase(os.path.abspath(source))
                or os.path.normcase(os.path.abspath(frozen['messages']['generation_path'])) != os.path.normcase(os.path.abspath(sidecar))
                or frozen['roster_bytes'] != roster or frozen['messages']['policy_bytes'] != policy):
            raise ValueError('source_controls')
        projection, context, before = (reader._verified_complete(frozen) if source_budget is None else
                                       reader._verified_complete(frozen, source_budget=source_budget))
        _verified_flat_manifest(flat_manifest_bytes, flat_manifest_sha256, frozen, projection,
                                ids, vectors, encoder, check_vectors=True)
        references = {row['provenance']['source_event_ref'] for row in projection['messages']['records']}
        if not references or any(not reader._hex(ref) for ref in references):
            raise ValueError('references')
        entries = (reader._narrow_row_index(context, references) if source_budget is None else
                   reader._narrow_row_index(context, references, source_budget=source_budget))
        _, _, after = (reader._verified_complete(frozen) if source_budget is None else
                       reader._verified_complete(frozen, source_budget=source_budget))
        if before != after:
            raise ValueError('source_changed')
        cursor = context['messages']['end_cursor']
        source_map = {'schema':'wd.mind-flat-source-map.v1', 'authority':'none',
            'source_prefix':dict(context['decision']['source_prefix'], file_identity=cursor.file_identity),
            'source_locations':{'log':context['messages']['source_path'], 'generation':context['messages']['generation_path']},
            'projection_sha256':frozen['projection_sha256'], 'decision_sha256':context['decision_sha256'],
            'flat_memory_sha256':flat_manifest_sha256, 'entries':entries}
        gate = _ConsumerFlatGate(bundle_root, expected_manifest_sha256, reader._canonical(source_map), flat_manifest_bytes, flat_manifest_sha256,
                                 ids, vectors, encoder)
        gate.verify_snapshot(frozen)
        if (_consumer_bundle_controls(bundle_root, expected_manifest_sha256) != controls
                or _consumer_flat_encoder(bundle_root, expected_manifest_sha256) != encoder):
            raise ValueError('controls_changed')
        output = _ConsumerSourceOwner(bundle_root, expected_manifest_sha256, controls, frozen, gate)
    except Exception:
        pass
    if output is None:
        raise SnapshotPackageRefused('content_snapshot_package_refused') from None
    return output


def package_content_snapshot(*, enabled=False, snapshot=None, passage_encoder=None, index_serializer=None,
                             index_decoder=None, encoder_binding_bytes=None, encoder_binding_sha256=None,
                             index_binding_bytes=None, index_binding_sha256=None, capacity_profile=None,
                             source_verification=None, watcher_join_timeout_seconds=None):
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
    A stuck verifier raises SnapshotPackageHold and retains interpreter-local
    ownership until it finishes. The non-daemon verifier can prevent process
    exit; this mechanism does not survive a process restart.
    The optional watcher_join_timeout_seconds sets a bounded verifier close
    wait (maximum 600 s); it is not a deadline for the synchronous build.
    """
    output, problem, held = None, None, False
    succeeded = False
    try:
        if enabled is not True:
            raise ValueError("disabled")
        capacity = _capacity(capacity_profile)
        output = _package(snapshot, passage_encoder, index_serializer, index_decoder, encoder_binding_bytes,
                          encoder_binding_sha256, index_binding_bytes, index_binding_sha256, capacity,
                          source_verification, watcher_join_timeout_seconds)
        if output is _PACKAGE_INTERNAL_HOLD:
            output, held = None, True
        else:
            succeeded = True
    except BaseException as exc:
        problem = exc
    if held:
        raise SnapshotPackageHold('content_snapshot_watcher_hold') from None
    if not succeeded:
        cancellation = reader._provider_cancellation(problem)
        problem = None
        if cancellation is not None:
            raise cancellation from None
        raise SnapshotPackageRefused("content_snapshot_package_refused") from None
    return output
