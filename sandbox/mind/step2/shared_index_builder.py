"""HEX MEMORY STEP 2, shared index builder (v2): one frozen hex memory from one validated shared projection.

Missing part 2 of the 2026-10-31 Mind plan (RCO1 SP1; Lead bound requests lead-mind-shared-index-fable-20261031-v1
and -v2 pin the file names, the descriptor schema ``wd.bridge-mind-q4-memory-descriptor.v2``, the scope
``wd.q4-shared-projected-events.v1`` and the reader contract of ``wd_q4_private_read`` at Lead commit 3c19279f):
the Q4 memory layout that ``cell.load``, ``memory.Memory`` and ``q4.Q4`` verify was only ever written by the original
corpus builder, which needs record text and intent; projected shared records (``shared_projection``) carry neither,
only the approved-metadata head line. This module builds the same frozen layout from a projection instead:

* input = a projection object that passes ``shared_projection.validate_projection`` with the same caller-pinned
  ``approved_tasks`` and ``include_refs`` (strict bool); anything else refuses before any filesystem action;
* the encoder is INJECTED: ``encode(texts) -> float32 array (n, dim)``; its identity is a caller-supplied,
  shape-checked record with EXACTLY the reader's three keys (``ENCODER_IDENTITY_KEYS``: model, revision,
  weights_sha256) that is recorded, never derived here; ``dim`` is a separate bounded integer argument. No model is
  loaded or downloaded by this module. ``e5_passage_encoder(model)`` adapts an ALREADY-LOADED pinned E5 model to that
  callable with the original passage policy (``"passage: "`` prefix, 2000-char truncation, batch 32, normalised,
  float32; Source: ``sandbox/mind/step2/encode.py`` at 6cf81dbb). Loading and pinning the model (path, revision,
  weights digest) is the caller's Source path (the Lead reader's encoder route), not this module's;
* BOUNDS before the synchronous encoder call: at most ``MAX_RECORDS`` projected records and ``MAX_INPUT_BYTES`` of
  UTF-8 head-line bytes, encoded in batches of at most ``ENCODE_BATCH`` texts, each batch shape-checked. Every
  exception raised by the encoder (any ``Exception``, including ``RuntimeError``/``MemoryError``/``SystemExit``) is
  sanitised to the fixed refusal ``encoder_output_invalid`` with no cause and no context; ``KeyboardInterrupt``
  propagates. The call is still synchronous and this module cannot interrupt a model: the caller MUST run a build
  under an EXTERNAL PROCESS WATCHDOG (wall-clock limit + memory cap + kill), which is a runtime requirement, not a
  property of this code;
* both views (``w`` and ``d``) of a projected record encode the SAME head line with the SAME encoder: they are one
  embedding stored twice so the frozen layout stays loadable, NOT two independent semantic views; the only
  independent view at query time is the lexical ``b`` view inside ``q4``. ``independent_views`` is recorded ``False``;
* placement is the CLOSED table ``PLACEMENT`` over the projected ``type`` (``status == blocked`` goes to ``safety``);
  every value is a WaggleDance hex cell id; no keyword scan, no free text reaches the router;
* every cell is one generation ``gen-000001`` written like the original ``cell.write_generation``/``publish`` (FAISS
  index + meta, retained ``vectors.f32``, ``provenance.jsonl`` rows with ``record``/``view``/``available_utc``,
  receipt with ``content_sha256``, ``LOG.jsonl`` hash chain from ``ZERO``, ``current.json``), verified by the read-only
  ``cell.verify_generation`` before it is renamed into place;
* the EXACT validated canonical projection bytes (``json.dumps(projection, sort_keys=True, indent=1)``, UTF-8) are
  written ONCE as ``projection.json`` inside the new memory directory and their sha256 is bound as
  ``projection_sha256`` (with ``projection_schema == shared_projection.SCHEMA``) in ``memory.json`` and in every
  cell receipt, so a later agent can resolve a Q4 result ref to its projected metadata from the generation alone.
  Raw events, messages or payloads are never persisted: the projection contains none;
* ``memory.json`` (loader schema v1) binds each cell to its receipt; ``descriptor.json`` is EXACTLY the reader's
  five keys ``{schema, scope, memory_sha256, dim, encoder}`` (encoder = the three identity keys) and nothing else;
  all counts and provenance details (records, duplicates, per-cell counts, cutoff, projection sha, descriptor sha,
  disclosures) live in the separate ``build_receipt.json``;
* provenance of every row is the sha256 of projected metadata (``ref_kind == sha256_of_projected_metadata``,
  ``canonical_event False``): a projection hash, not a canonical bridge event, not authenticated;
* ``cutoff_utc`` is the latest ``available_utc`` of the projection: every row's floored ``time_utc`` lies strictly
  before it, and the query layer's ``available_utc < situation - guard`` rule keeps the v5 no-future guarantee;
* publication is confined to a caller-supplied trusted output root (``shared_projection._output_root``) plus one
  safe directory name (``MEMORY_NAME``) that must not exist in any form; the memory directory is created exclusively;
  every file is created with ``xb``; NOTHING is ever overwritten or removed by this module. On a failure after the
  directory exists the incomplete generation is RETAINED as is and an ``INCOMPLETE.json`` report (fixed reason token,
  stage, no exception text) is added beside it; the retained directory refuses any later build under the same name
  (``memory_exists``) and never loads as a memory (no ``memory.json``). Clean-up is an operator decision.

Synthetic only: no real events, notes, model, index or deployment are touched by this module or its tests. Trust
assumptions, disclosed: the encoder identity is recorded as given (the caller pins it from receipts; a wrong identity
is a wrong receipt, not detectable here); ``meta.json`` stores the head lines as FAISS texts (the same bytes as
``stored_text``, nothing more); the memory is shared-readable by construction, so only projected metadata enters;
an absent situation is NOT rejected by the memory itself, only abstained from by ``q4`` consensus when no candidate
agrees, which is a ranking property, not a proof.
"""

import datetime as dt
import hashlib
import json
import os
import re
import secrets
from pathlib import Path

import numpy as np

from core.faiss_store import FaissCollection
from sandbox.mind.step2 import cell as cell_mod
from sandbox.mind.step2 import memory as memory_mod
from sandbox.mind.step2 import shared_projection as sp
from waggledance.core import hex_cell_topology as topology_mod

DESCRIPTOR_SCHEMA = "wd.bridge-mind-q4-memory-descriptor.v2"  # pinned by the Lead request 16:00:59Z
BUILD_RECEIPT_SCHEMA = "wd.bridge-mind-shared-index-build-receipt.v1"
INCOMPLETE_SCHEMA = "wd.bridge-mind-shared-index-incomplete.v1"
SCOPE = "wd.q4-shared-projected-events.v1"  # one scope token for receipts, memory.json and the descriptor
VIEWS = ("w", "d")
ENCODER_IDENTITY_KEYS = frozenset({"model", "revision", "weights_sha256"})  # == wd_q4_private_read.ENCODER_KEYS
DESCRIPTOR_KEYS = frozenset({"schema", "scope", "memory_sha256", "dim", "encoder"})  # == the reader's exact set
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}")
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
MEMORY_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
MAX_DIM = 4096
# Conservative input bounds, checked before the first encoder call (a projected head line is a few hundred bytes).
MAX_RECORDS = 4096
MAX_INPUT_BYTES = 4 * 1024 * 1024
ENCODE_BATCH = 256
# The original E5 passage policy (sandbox/mind/step2/encode.py at 6cf81dbb): prefix, truncation, batch, dim.
E5_PASSAGE_PREFIX = "passage: "
E5_TRUNCATE = 2000
E5_PASSAGE_BATCH = 32
E5_DIM = 384
# Closed placement table over the projected event type; status "blocked" overrides to the safety cell.
PLACEMENT = {"message": topology_mod.CELL_GENERAL, "milestone": topology_mod.CELL_GENERAL,
             "progress": topology_mod.CELL_GENERAL, "status": topology_mod.CELL_SYSTEM,
             "wake_request": topology_mod.CELL_SYSTEM, "handoff": topology_mod.CELL_SYSTEM,
             "claim": topology_mod.CELL_SYSTEM, "release": topology_mod.CELL_SYSTEM, "done": topology_mod.CELL_SYSTEM,
             "test": topology_mod.CELL_LEARNING}
BLOCKED_CELL = topology_mod.CELL_SAFETY
PLACEMENT_RULE = "shared-projection-v1: closed table over projected type; status blocked -> safety"
TOPOLOGY_FILE = Path(topology_mod.__file__)
WATCHDOG_RULE = "external process watchdog required: synchronous encoder call, wall-clock limit + memory cap"


class BuildRefused(ValueError):
    """One fixed reason token; never the offending value."""


def place(record):
    """The cell of one projected record from the closed table; a type outside it cannot occur after validation."""
    meta = record["meta"]
    if meta["status"] == "blocked":
        return BLOCKED_CELL
    try:
        return PLACEMENT[meta["type"]]
    except KeyError:
        raise BuildRefused("placement_unknown") from None


def encoder_identity(identity):
    """A caller-supplied encoder identity with exactly the reader's three keys and safe shapes; recorded, never
    derived. ``dim`` is deliberately not part of it (the reader rejects a fourth key)."""
    if not isinstance(identity, dict) or set(identity) != ENCODER_IDENTITY_KEYS:
        raise BuildRefused("encoder_identity_invalid")
    model, revision, weights = identity["model"], identity["revision"], identity["weights_sha256"]
    if (not isinstance(model, str) or MODEL_NAME.fullmatch(model) is None
            or not isinstance(revision, str) or HEX40.fullmatch(revision) is None
            or not isinstance(weights, str) or HEX64.fullmatch(weights) is None):
        raise BuildRefused("encoder_identity_invalid")
    return {"model": model, "revision": revision, "weights_sha256": weights}


def check_dim(dim):
    """The vector width: a plain bounded int (bool is not an int here)."""
    if type(dim) is not int or dim < 1 or dim > MAX_DIM:
        raise BuildRefused("dim_invalid")
    return dim


def e5_passage_encoder(model):
    """Adapt an ALREADY-LOADED pinned E5 sentence-transformer model to ``encode(texts) -> float32 (n, dim)`` with the
    original passage policy of ``encode.py`` at 6cf81dbb: ``"passage: "`` prefix, 2000-character truncation,
    ``batch_size=32``, ``normalize_embeddings=True``, ``convert_to_numpy=True``, no progress bar, C-contiguous
    ``<f4``. The model object is not inspected beyond having an ``encode`` method; its identity (model, revision,
    weights_sha256) and loading are the caller's pinned Source path. Exceptions from the model propagate to
    ``_vectors``, which sanitises them."""
    if model is None or not callable(getattr(model, "encode", None)):
        raise BuildRefused("encoder_invalid")

    def encode(texts):
        inputs = [E5_PASSAGE_PREFIX + text[:E5_TRUNCATE] for text in texts]
        out = model.encode(inputs, batch_size=E5_PASSAGE_BATCH, normalize_embeddings=True, convert_to_numpy=True,
                           show_progress_bar=False)
        return np.ascontiguousarray(np.asarray(out), dtype="<f4")
    return encode


def _encode_batch(encode, batch, dim):
    """One bounded encoder call; any exception is sanitised to the fixed refusal without cause or context."""
    try:
        vectors = np.asarray(encode(list(batch)), dtype="<f4")
    except KeyboardInterrupt:
        raise
    except BaseException:  # noqa: B036 - sanitise everything the model could raise, including SystemExit
        vectors = None
    if vectors is None or vectors.ndim != 2 or vectors.shape != (len(batch), dim):
        raise BuildRefused("encoder_output_invalid") from None
    return vectors


def _vectors(encode, texts, dim):
    """Encode the head lines in bounded batches; refuse anything but a finite, non-zero float32 matrix of the
    declared width."""
    batch_size = ENCODE_BATCH
    if type(batch_size) is not int or batch_size < 1:
        raise BuildRefused("batch_bound_invalid")
    parts = [_encode_batch(encode, texts[start:start + batch_size], dim) for start in range(0, len(texts), batch_size)]
    vectors = np.vstack(parts) if parts else np.zeros((0, dim), dtype="<f4")
    if vectors.shape != (len(texts), dim):
        raise BuildRefused("encoder_output_invalid")
    try:
        cell_mod.check_vectors(vectors, dim)
    except cell_mod.CellError:
        raise BuildRefused("encoder_output_invalid") from None
    return np.ascontiguousarray(vectors, dtype="<f4")


def _check_bounds(records):
    """Record count and total UTF-8 head-line bytes against the conservative module bounds, before any encoding."""
    if len(records) > MAX_RECORDS:
        raise BuildRefused("records_over_bound")
    total = 0
    for record in records:
        total += len(record["stored_text"].encode("utf-8"))
        if total > MAX_INPUT_BYTES:
            raise BuildRefused("input_over_bound")


def cell_rows(record, cell_id):
    """The two cell rows of one projected record (``<record_id>#w`` and ``#d``), both carrying its one text."""
    if record["stored_text"] != record["summary"]:
        raise BuildRefused("record_text_mismatch")
    for view in VIEWS:
        provenance = dict(record["provenance"], record=record["record_id"], view=view)
        yield {"record_id": f"{record['record_id']}#{view}", "kind": record["kind"], "time_utc": record["time_utc"],
               "cell_id": cell_id, "provenance": provenance,
               "meta": dict(record["meta"], view=view, cell_id=cell_id), "stored_text": record["stored_text"]}


def distinct_records(records):
    """Deterministic duplicate treatment: a projected record id is the sha256 of its approved fields, so two events
    with identical metadata in one minute are one record; the first occurrence (projection order) is kept, the rest
    are counted and reported in the build receipt as ``duplicates``. Nothing else about ordering matters: cells are
    written in topology order and rows in projection order."""
    seen, kept, duplicates = set(), [], 0
    for record in records:
        if record["record_id"] in seen:
            duplicates += 1
            continue
        seen.add(record["record_id"])
        kept.append(record)
    return kept, duplicates


def _utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _write_new(path, data):
    """Create ``path`` exclusively with ``data``; never overwrites."""
    with open(path, "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _write_generation(gen_dir, cell_id, rows, vectors, *, dim, cutoff_utc, encoder, projection_sha256):
    ids = [row["record_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise BuildRefused("duplicate_id")
    if len(ids) != vectors.shape[0]:
        raise BuildRefused("count_mismatch")
    for row in rows:
        if row["time_utc"] >= cutoff_utc:
            raise BuildRefused("row_after_cutoff")
    files = cell_mod.paths(gen_dir, cell_id)
    files["index"].parent.mkdir(parents=True)
    collection = FaissCollection(f"cell_{cell_id}", dim=dim, persist_dir=str(files["index"].parent))
    if collection.count:
        raise BuildRefused("staging_not_empty")
    collection.add_batch(ids, [row["stored_text"] for row in rows], vectors, [row["meta"] for row in rows])
    if collection.count != len(ids):
        raise BuildRefused("count_mismatch")
    collection.save()
    _write_new(files["vectors"], vectors.tobytes())
    provenance_rows = [{"record_id": row["record_id"], "kind": row["kind"], "time_utc": row["time_utc"],
                        "cell_id": cell_id, "provenance": row["provenance"]} for row in rows]
    _write_new(files["provenance"], "".join(cell_mod.canonical(row) + "\n" for row in provenance_rows).encode("utf-8"))
    stored = np.vstack([collection._index.reconstruct(i) for i in range(collection.count)])
    content = {"cell_id": cell_id, "count": len(ids), "dim": dim, "cutoff_utc": cutoff_utc, "encoder": encoder,
               "projection_sha256": projection_sha256, "projection_schema": sp.SCHEMA,
               "ids_sha256": cell_mod.sha256("\n".join(ids).encode("utf-8")),
               "pairs_sha256": cell_mod.pairs_sha256(ids, stored),
               "files": {name: cell_mod.file_sha256(files[name]) for name in cell_mod.FILES}}
    receipt = {"schema": cell_mod.SCHEMA, "authority": "none", "scope": SCOPE, "content": content,
               "content_sha256": cell_mod.sha256(cell_mod.canonical(content).encode("utf-8")),
               "source": "shared_index_builder", "built_utc": _utc_now()}
    _write_new(gen_dir / "receipt.json", cell_mod.canonical(receipt).encode("utf-8"))
    return receipt


def _publish_cell(cell_dir, cell_id, rows, vectors, **kw):
    """Generation 1 of a brand-new cell: stage, verify with the read-only verifier, rename, log, point. On a refusal
    the staging directory is left in place (retained incomplete generation; nothing is removed)."""
    cell_dir.mkdir()  # fresh cell inside our own memory directory; never an existing one
    staging = cell_dir / f".staging-000001-{secrets.token_hex(8)}"
    staging.mkdir()
    receipt = _write_generation(staging, cell_id, rows, vectors, **kw)
    receipt_sha = cell_mod.file_sha256(staging / "receipt.json")
    try:
        cell_mod.verify_generation(staging, cell_id, receipt_sha)
    except cell_mod.CellError:
        raise BuildRefused("generation_unverifiable") from None
    final = cell_dir / "gen-000001"
    os.rename(staging, final)  # final cannot exist: the cell directory was created empty just above
    line = {"schema": cell_mod.LOG_SCHEMA, "seq": 1, "generation": 1, "receipt_sha256": receipt_sha,
            "content_sha256": receipt["content_sha256"], "prev_line_sha256": cell_mod.ZERO,
            "published_utc": _utc_now()}
    _write_new(cell_dir / "LOG.jsonl", cell_mod.canonical(line).encode("utf-8") + b"\n")
    pointer = {"generation": 1, "seq": 1, "receipt_sha256": receipt_sha}
    _write_new(cell_dir / "current.json", cell_mod.canonical(pointer).encode("utf-8"))
    return pointer


def _report_incomplete(memory_dir, name, reason, stage):
    """Best-effort retained-failure report beside the incomplete generation: a fixed reason token and the stage,
    never exception text. Its own failure is ignored so the original refusal is what the caller sees."""
    report = {"schema": INCOMPLETE_SCHEMA, "authority": "none", "scope": SCOPE, "memory_name": name,
              "reason": reason, "stage": stage, "complete": False, "retained": True, "removed": [],
              "written_utc": _utc_now()}
    try:
        _write_new(memory_dir / "INCOMPLETE.json", cell_mod.canonical(report).encode("utf-8"))
    except OSError:
        pass


def build_memory(projection, encode, identity, dim, root, name, *, approved_tasks=None, include_refs=False):
    """Build ``root/name`` from a validated projection; returns the exact reader descriptor, the build receipt and
    their sha256 digests.

    Refuses (nothing written) on an invalid projection, identity, dim, root or name, a bound violation, a failing
    encoder, or an existing target; after the memory directory exists, any failure retains the incomplete output,
    adds ``INCOMPLETE.json`` and removes nothing."""
    sp.validate_projection(projection, approved_tasks, include_refs)
    encoder = encoder_identity(identity)
    dim = check_dim(dim)
    if not callable(encode):
        raise BuildRefused("encoder_invalid")
    if not isinstance(name, str) or MEMORY_NAME.fullmatch(name) is None:
        raise BuildRefused("memory_name_invalid")
    root = sp._output_root(root)
    _check_bounds(projection["records"])
    projection_bytes = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
    projection_sha256 = hashlib.sha256(projection_bytes).hexdigest()
    records, duplicates = distinct_records(projection["records"])
    if not records:
        raise BuildRefused("projection_empty")
    cells = {}
    for record in records:
        cell_id = place(record)
        cells.setdefault(cell_id, []).extend(cell_rows(record, cell_id))
    cutoff_utc = max(record["available_utc"] for record in records)
    texts = sorted({row["stored_text"] for rows in cells.values() for row in rows})
    by_text = dict(zip(texts, _vectors(encode, texts, dim)))
    memory_dir = root / name
    try:
        os.lstat(memory_dir)
    except FileNotFoundError:
        pass
    else:
        raise BuildRefused("memory_exists")
    try:
        os.mkdir(memory_dir)
    except FileExistsError:
        raise BuildRefused("memory_exists") from None
    except OSError:
        raise BuildRefused("write_failed") from None
    stage = "projection"
    try:
        _write_new(memory_dir / "projection.json", projection_bytes)
        stage = "cells"
        pointers = {}
        for cell_id in topology_mod.ALL_CELLS:
            if cell_id not in cells:
                continue
            rows = cells[cell_id]
            vectors = np.vstack([by_text[row["stored_text"]] for row in rows])
            (memory_dir / "cells").mkdir(exist_ok=True)
            pointer = _publish_cell(memory_dir / "cells" / cell_id, cell_id, rows, vectors, dim=dim,
                                    cutoff_utc=cutoff_utc, encoder=encoder, projection_sha256=projection_sha256)
            pointers[cell_id] = dict(pointer, count=len(rows))
        stage = "memory"
        memory = {"schema": memory_mod.SCHEMA, "authority": "none", "scope": SCOPE, "cutoff_utc": cutoff_utc,
                  "projection_sha256": projection_sha256, "projection_schema": sp.SCHEMA,
                  "projection_file": "projection.json", "encoder": encoder, "dim": dim, "placement": PLACEMENT_RULE,
                  "topology_source_sha256": cell_mod.file_sha256(TOPOLOGY_FILE), "cells": pointers,
                  "records": len(records), "duplicates": duplicates, "views": list(VIEWS),
                  "independent_views": False}
        sp.corpus_mod.ws.guard_object(memory)
        memory_bytes = cell_mod.canonical(memory).encode("utf-8")
        _write_new(memory_dir / "memory.json", memory_bytes)
        stage = "descriptor"
        memory_sha256 = hashlib.sha256(memory_bytes).hexdigest()
        descriptor = {"schema": DESCRIPTOR_SCHEMA, "scope": SCOPE, "memory_sha256": memory_sha256, "dim": dim,
                      "encoder": encoder}
        if set(descriptor) != DESCRIPTOR_KEYS:
            raise BuildRefused("descriptor_shape")
        sp.corpus_mod.ws.guard_object(descriptor)
        descriptor_bytes = cell_mod.canonical(descriptor).encode("utf-8")
        _write_new(memory_dir / "descriptor.json", descriptor_bytes)
        stage = "build_receipt"
        descriptor_sha256 = hashlib.sha256(descriptor_bytes).hexdigest()
        build_receipt = {"schema": BUILD_RECEIPT_SCHEMA, "authority": "none", "scope": SCOPE, "memory_name": name,
                         "memory_sha256": memory_sha256, "descriptor_sha256": descriptor_sha256,
                         "descriptor_schema": DESCRIPTOR_SCHEMA, "projection_sha256": projection_sha256,
                         "projection_schema": sp.SCHEMA, "projection_file": "projection.json", "encoder": encoder,
                         "dim": dim, "records": len(records), "duplicates": duplicates,
                         "cells": {cell_id: row["count"] for cell_id, row in pointers.items()},
                         "cutoff_utc": cutoff_utc, "views": list(VIEWS), "independent_views": False,
                         "canonical_event": False, "authenticated": False,
                         "bounds": {"max_records": MAX_RECORDS, "max_input_bytes": MAX_INPUT_BYTES,
                                    "encode_batch": ENCODE_BATCH},
                         "watchdog": WATCHDOG_RULE, "built_utc": _utc_now()}
        sp.corpus_mod.ws.guard_object(build_receipt)
        receipt_bytes = cell_mod.canonical(build_receipt).encode("utf-8")
        _write_new(memory_dir / "build_receipt.json", receipt_bytes)
    except BuildRefused as refused:
        _report_incomplete(memory_dir, name, str(refused), stage)
        raise
    except sp.corpus_mod.ws.WorkspaceError:
        _report_incomplete(memory_dir, name, "output_guarded", stage)
        raise BuildRefused("output_guarded") from None
    except (OSError, ValueError, TypeError, KeyError, cell_mod.CellError):
        _report_incomplete(memory_dir, name, "write_failed", stage)
        raise BuildRefused("write_failed") from None
    return {"memory_sha256": memory_sha256, "descriptor_sha256": descriptor_sha256, "descriptor": descriptor,
            "build_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(), "build_receipt": build_receipt,
            "projection_sha256": projection_sha256}
