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
  (``memory_exists``) and never loads as a memory (no ``memory.json``). Clean-up is an operator decision;
* WRITE ORDER = the completeness boundary (Lead independent review 16:29:34Z): ``projection.json`` first, then the
  cells, then every final object is computed and guarded in memory (stage ``finalize``), then ``build_receipt.json``,
  then ``descriptor.json``, and LAST ``memory.json`` as the single commit point: its bytes are written and fsynced to a
  staging name (``.memory.json.staging-<hex>``) and promoted by one no-replace rename. Hence a loadable
  ``memory.json`` exists only when every other file of the generation is complete; a failure at any earlier stage
  leaves no ``memory.json`` (``memory.Memory`` refuses ``memory_unreadable``; the Lead reader refuses
  ``descriptor_unreadable`` or ``memory_descriptor_unreadable``), and ``INCOMPLETE.json`` never coexists with a
  promoted ``memory.json``. A torn or altered ``memory.json`` after the fact is caught by the descriptor digest
  (``memory_descriptor_mismatch``), not prevented. The promotion primitive is per platform (nt no-replace rename,
  posix no-replace link + best-effort unlink, else fail closed before any write); a competitor that appears between
  the lstat check and the primitive keeps its bytes and this build refuses ``memory_exists``; once the primitive
  succeeds the generation is published and a failing staging unlink only leaves a second hard link;
* STRICT v6 POLICY (RCO2 spec S2 + S5): the projection must have been built under a pinned roster
  (``authorship == declared_roster_consistent``) with the review signal excluded (``review_signal_excluded True``),
  is re-validated under the builder's own roster, and both are recorded as ``policy`` in ``memory.json`` and the
  build receipt; a v5-style projection refuses ``projection_policy_weak``. The labels are CALLER ASSERTIONS re-pinned
  by this builder's own roster, not proof: a relabelled projection cannot be told apart in-process, and the capture
  side must still pre-filter review lanes and bind its source (RCO2 spec S1/S3) before anything reaches here;
* ENCODER IDENTITY (RCO1 B2): with ``runtime`` the identity is derived by ``encode.model_identity`` from the verified
  model directory (weights sha256, fixed model name, pinned revision) and the build receipt records the runtime
  generation, manifest sha256, weights size and ``identity_source``; a caller-asserted identity, when also given,
  must equal the derived one. The caller-asserted route alone remains for synthetic tests and is labelled
  ``caller_asserted`` in the receipt;
* PINNED INPUTS + SOURCE BINDING (v4, Lead bound request 17:13:50Z, supplements 17:13:59Z/17:15:50Z/17:19:17Z):
  the caller supplies the policy document and a SEPARATE roster snapshot as bytes with their sha256 digests
  (``resolve_pins``); the approved set and the roster are derived from those bytes, and ``memory.policy`` binds the
  exact ``policy_sha256`` + ``roster_sha256`` (pinned through the descriptor), so replacing one roster uuid and
  recomputing a digest is a different memory a consumer can tell apart. The roster snapshot carries lane identities
  only (no memory or deployment fields), which keeps the digest graph acyclic (a wd-fleet.json that pins a memory
  digest is refused). On the strict publication path the build receipt records a closed ``source_binding``
  (``wd.q4-shared-index-source-binding.v1``: anchor, projection, cursor start/end as sha256 of the snapshot tokens,
  policy, roster) only after the Lead verifier ``shared_bridge_snapshot.verify_anchored_projection`` accepted the
  snapshot result for this projection with independently supplied expected digests; a missing source, an unavailable
  verifier or any verifier refusal fails closed before any write. Nothing here authenticates the source: a matching
  caller-provided hash is integrity checking of what the caller supplied (``source_authenticated False``);
* NOT PROVED, disclosed: ``core.faiss_store.FaissCollection.save`` writes index.faiss and meta.json with ordinary
  (replacing) writes inside the fresh staging directory, not ``xb``; the staging directory names carry 16 random hex
  characters but all lanes run as one OS user, so a same-user writer racing inside the memory directory is not
  proved absent (the receipt digests detect, they do not prevent); the resolver-side consumer must still compare
  ``memory.json`` bytes to the descriptor pin.

Synthetic only: no real events, notes, model, index or deployment are touched by this module or its tests. Trust
assumptions, disclosed: the encoder identity is recorded as given (the caller pins it from receipts; a wrong identity
is a wrong receipt, not detectable here); ``meta.json`` stores the head lines as FAISS texts (the same bytes as
``stored_text``, nothing more); the memory is shared-readable by construction, so only projected metadata enters;
an absent situation is NOT rejected by the memory itself, only abstained from by ``q4`` consensus when no candidate
agrees, which is a ranking property, not a proof.
"""

import datetime as dt
import hashlib
import hmac
import importlib
import json
import os
import re
import secrets
import stat
from pathlib import Path

import numpy as np

from core.faiss_store import FaissCollection
from sandbox.mind.step2 import cell as cell_mod
from sandbox.mind.step2 import encode as encode_mod
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
# v4 (Lead bound request lead-mind-shared-index-fable-20261031-v4): caller-PINNED policy and roster snapshot bytes.
PINS_KEYS = frozenset({"policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256"})
POLICY_SCHEMA = "wd.q4-non-review-policy.v1"  # == shared_capture_adapter.strict_pins at the Lead integration head
POLICY_LIMIT = 65536
# == tools/wd_q4_resolved_child.MAX_TASKS at the Lead integration head (RCO2 via Lead 17:35:21Z: the builder must not
# accept a policy the reader refuses); the adapter's 4096 is Lead's to align, a shared constant is proposed for sp.
MAX_APPROVED_TASKS = 512
REVIEW_TASK = re.compile(r"(?:^|[._-])(?:review|rco[12]?)(?:$|[._-])", re.IGNORECASE)  # == the adapter's pattern
# The roster snapshot is a SEPARATE immutable document (Lead 17:19:17Z): lanes + tools_supervisor identities only, no
# memory or deployment fields, so memory.policy.roster_sha256 never hashes a document that itself pins a memory.
ROSTER_SNAPSHOT_SCHEMA = "wd.q4-roster-snapshot.v1"
ROSTER_SNAPSHOT_KEYS = frozenset({"schema", "lanes", "tools_supervisor"})
ROSTER_ENTRY_KEYS = frozenset({"agent", "agent_uuid", "role"})
# The source binding recorded in the build receipt after the Lead verifier accepted the snapshot/projection pair.
SOURCE_BINDING_SCHEMA = "wd.q4-shared-index-source-binding.v1"
SOURCE_BINDING_KEYS = frozenset({"schema", "anchor_sha256", "projection_sha256", "cursor_start_sha256",
                                 "cursor_end_sha256", "policy_sha256", "roster_sha256"})
SOURCE_KEYS = frozenset({"snapshot_result", "expected_anchor_sha256"})
SNAPSHOT_MODULE = "sandbox.mind.step2.shared_bridge_snapshot"  # Lead module; imported lazily, never re-implemented
# v5 (Lead bound request lead-mind-shared-index-fable-20261031-v5, 17:55:13Z): bounded prior metadata history. The
# merge is the PINNED Lead helper ``shared_history.merge_projections`` (history helper head 9caa041b), imported lazily
# and never re-implemented here; the predecessor is the caller's pinned previous generation (directory + digests).
HISTORY_MODULE = "sandbox.mind.step2.shared_history"
HISTORY_SCHEMA = "wd.q4-shared-index-history.v1"
PREDECESSOR_KEYS = frozenset({"memory_dir", "memory_sha256", "projection_sha256", "build_receipt_sha256"})
PREDECESSOR_FILES = {"memory_sha256": "memory.json", "projection_sha256": "projection.json",
                     "build_receipt_sha256": "build_receipt.json"}
PREDECESSOR_FILE_LIMIT = 64 * 1024 * 1024
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
# Commit-point primitive per platform (the same policy as shared_projection._publish): nt -> os.rename, which is
# MoveFileEx WITHOUT REPLACE_EXISTING and fails on an existing destination; posix -> os.link, which never replaces
# (EEXIST) and is atomic on local filesystems, then the staging name is unlinked best-effort. Anything else, or a
# filesystem without hard links, fails closed BEFORE any write (publication_unsupported). Bound once so tests can
# pin them deterministically.
SUPPORTED_PLATFORMS = frozenset({"nt", "posix"})
_PLATFORM = os.name
_rename = os.rename
_link = getattr(os, "link", None)
_unlink = os.unlink
# B2: the encoder identity may be DERIVED from a verified runtime model directory (encode.model_identity hashes the
# weights; the revision is the caller's pinned declaration) together with the runtime generation and manifest pin.
RUNTIME_KEYS = frozenset({"model_dir", "revision", "generation", "manifest_sha256"})
GENERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


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


def runtime_identity(runtime):
    """Derive the encoder identity from a verified runtime: ``runtime`` is a closed record {model_dir, revision,
    generation, manifest_sha256}; the identity comes from ``encode.model_identity`` (sha256 of model.safetensors in
    that directory, the fixed model name, the pinned revision), never from the caller's assertion. Returns the
    three-key identity plus ``weights_bytes``, ``generation`` and ``manifest_sha256`` for the build receipt. Any
    problem reading the model is the fixed refusal ``encoder_identity_unavailable`` (no path in the message)."""
    if (not isinstance(runtime, dict) or set(runtime) != RUNTIME_KEYS
            or not isinstance(runtime["model_dir"], str) or not isinstance(runtime["revision"], str)
            or not isinstance(runtime["generation"], str) or GENERATION.fullmatch(runtime["generation"]) is None
            or not isinstance(runtime["manifest_sha256"], str) or HEX64.fullmatch(runtime["manifest_sha256"]) is None):
        raise BuildRefused("runtime_invalid")
    try:
        derived = encode_mod.model_identity(model_dir=runtime["model_dir"], revision=runtime["revision"])
    except (OSError, ValueError, TypeError, KeyError):
        raise BuildRefused("encoder_identity_unavailable") from None
    identity = encoder_identity({key: derived[key] for key in ENCODER_IDENTITY_KEYS if key in derived})
    weights_bytes = derived.get("weights_bytes")
    if type(weights_bytes) is not int or weights_bytes < 1:
        raise BuildRefused("encoder_identity_unavailable")
    return {"identity": identity, "weights_bytes": weights_bytes, "generation": runtime["generation"],
            "manifest_sha256": runtime["manifest_sha256"]}


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


def _publication_supported():
    """Fail closed before any write when no atomic no-replace primitive exists on this platform."""
    if _PLATFORM == "nt":
        return
    if _PLATFORM == "posix" and _link is not None:
        return
    raise BuildRefused("publication_unsupported")


def _commit_memory(memory_dir, memory_bytes):
    """The single commit point of a generation: fully write + fsync ``memory.json`` under a staging name inside the
    memory directory, then promote it with one atomic NO-REPLACE primitive: nt ``rename`` (fails on an existing
    destination), posix ``link`` (EEXIST when a competitor won; its bytes are never touched) followed by a
    best-effort unlink of the staging name. The lstat pre-check is only a cheap early refusal, not the guarantee;
    the primitive is. A competitor that appears between the check and the primitive is preserved and this build
    refuses ``memory_exists``. A posix filesystem that refuses links fails closed ``publication_unsupported``. Once
    the primitive has succeeded the generation IS published: a failing staging unlink leaves an extra hard link and
    nothing else (never INCOMPLETE). A failure before or during the primitive leaves no ``memory.json`` (the staging
    copy is retained, never promoted, never removed)."""
    target = memory_dir / "memory.json"
    try:
        os.lstat(target)
    except FileNotFoundError:
        pass
    else:
        raise BuildRefused("memory_exists")  # only a foreign writer could have put it there; never replace it
    staging = memory_dir / f".memory.json.staging-{secrets.token_hex(8)}"
    _write_new(staging, memory_bytes)
    if _PLATFORM == "nt":
        try:
            _rename(staging, target)  # MoveFileEx without REPLACE_EXISTING: fails when the destination exists
        except FileExistsError:
            raise BuildRefused("memory_exists") from None
        return
    if _PLATFORM == "posix" and _link is not None:
        try:
            _link(staging, target)  # link(2) never replaces: EEXIST when another writer won
        except FileExistsError:
            raise BuildRefused("memory_exists") from None
        except OSError:
            raise BuildRefused("publication_unsupported") from None  # no hard links here: fail closed
        try:  # published from here on: the staging name is a second link to the same bytes
            _unlink(staging)
        except OSError:
            pass
        return
    raise BuildRefused("publication_unsupported")


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


def _no_constant(_name):
    raise ValueError("constant")


def _parse_pinned_json(raw, token):
    """Closed JSON parse of pinned bytes: duplicate keys, NaN/Infinity, bad UTF-8 or any syntax error -> ``token``."""
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=sp._pairs_without_duplicates, parse_constant=_no_constant)
    except (ValueError, RecursionError):  # ProjectionRefused (duplicate key) and UnicodeDecodeError are ValueErrors
        raise BuildRefused(token) from None


def resolve_pins(pins):
    """The caller's pinned policy + roster snapshot -> {approved_tasks, roster, policy_sha256, roster_sha256}.

    Each byte string must hash to its digest (``pin_mismatch``); the policy is exactly
    ``{"schema": POLICY_SCHEMA, "approved_tasks": [...]}`` with distinct safe-shaped non-review task ids
    (``policy_invalid``); the roster snapshot is exactly ``{"schema", "lanes", "tools_supervisor"}`` whose entries carry
    only ``agent``/``agent_uuid`` (optionally ``role``) and name every projection lane once
    (``roster_snapshot_invalid``). A matching digest is integrity checking of what the caller supplied, not
    authentication of its origin."""
    if pins is None:
        raise BuildRefused("pins_required")
    if not isinstance(pins, dict) or set(pins) != PINS_KEYS:
        raise BuildRefused("pins_invalid")
    for raw_key, digest_key, limit in (("policy_bytes", "policy_sha256", POLICY_LIMIT),
                                       ("roster_bytes", "roster_sha256", sp.ROSTER_BUDGET)):
        raw, digest = pins[raw_key], pins[digest_key]
        if (type(raw) is not bytes or len(raw) > limit or not isinstance(digest, str)
                or HEX64.fullmatch(digest) is None):
            raise BuildRefused("pins_invalid")
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), digest):
            raise BuildRefused("pin_mismatch")
    policy = _parse_pinned_json(pins["policy_bytes"], "policy_invalid")
    if (not isinstance(policy, dict) or set(policy) != {"schema", "approved_tasks"} or policy["schema"] != POLICY_SCHEMA
            or not isinstance(policy["approved_tasks"], list) or len(policy["approved_tasks"]) > MAX_APPROVED_TASKS
            or any(not isinstance(task, str) or REVIEW_TASK.search(task) for task in policy["approved_tasks"])
            or len(set(policy["approved_tasks"])) != len(policy["approved_tasks"])):
        raise BuildRefused("policy_invalid")
    try:
        approved = sp._approved_tasks(frozenset(policy["approved_tasks"]))
    except sp.ProjectionRefused:
        raise BuildRefused("policy_invalid") from None
    snapshot = _parse_pinned_json(pins["roster_bytes"], "roster_snapshot_invalid")
    if (not isinstance(snapshot, dict) or set(snapshot) != ROSTER_SNAPSHOT_KEYS
            or snapshot["schema"] != ROSTER_SNAPSHOT_SCHEMA or not isinstance(snapshot["lanes"], list)
            or not isinstance(snapshot["tools_supervisor"], dict)
            or any(not isinstance(entry, dict) or not set(entry) <= ROSTER_ENTRY_KEYS
                   for entry in snapshot["lanes"] + [snapshot["tools_supervisor"]])):
        raise BuildRefused("roster_snapshot_invalid")
    try:
        roster = sp.roster_from_fleet(snapshot)
    except sp.ProjectionRefused:
        raise BuildRefused("roster_snapshot_invalid") from None
    if set(roster) != sp.LANES:
        raise BuildRefused("roster_snapshot_invalid")
    return {"approved_tasks": approved, "roster": roster, "policy_sha256": pins["policy_sha256"],
            "roster_sha256": pins["roster_sha256"]}


def _source_verifier():
    """The Lead verifier (``shared_bridge_snapshot.verify_anchored_projection`` + ``_token``), imported lazily; any
    import failure or a module without that API refuses ``source_verifier_unavailable`` (nothing is re-implemented)."""
    try:
        module = importlib.import_module(SNAPSHOT_MODULE)
        verify, token = module.verify_anchored_projection, module._token
    except Exception:  # noqa: BLE001 - ImportError, AttributeError or an import-time failure: one token
        raise BuildRefused("source_verifier_unavailable") from None
    if not callable(verify) or not callable(token):
        raise BuildRefused("source_verifier_unavailable")
    return verify, token


def bind_source(source, projection, projection_sha256, policy_sha256, roster_sha256):
    """The closed receipt ``source_binding`` for a snapshot result the Lead verifier accepted for THIS projection and
    the pinned policy/roster digests. Cursor digests are taken from the verified ``source`` (``cursor_start_sha256``,
    ``cursor_end_sha256``: sha256 of the snapshot token strings, the adapter never exposes the tokens) and the end
    digest must equal sha256 of ``_token(candidate_cursor)``. Refuses ``source_invalid`` (shape),
    ``source_verifier_unavailable`` or ``source_binding_refused`` (any verifier or binding failure, no text)."""
    if (not isinstance(source, dict) or set(source) != SOURCE_KEYS or not isinstance(source["snapshot_result"], dict)
            or not isinstance(source["expected_anchor_sha256"], str)
            or HEX64.fullmatch(source["expected_anchor_sha256"]) is None):
        raise BuildRefused("source_invalid")
    verify, token = _source_verifier()
    result = source["snapshot_result"]
    try:
        accepted = verify(result, projection, expected_anchor_sha256=source["expected_anchor_sha256"],
                          expected_policy_sha256=policy_sha256, expected_roster_sha256=roster_sha256)
        # The verified ``source`` carries cursor DIGESTS only (the Lead adapter never returns raw cursor tokens,
        # Lead 17:33:37Z): take both from it and require the end digest to be the digest of the candidate cursor.
        start_sha256, end_sha256 = result["source"]["cursor_start_sha256"], result["source"]["cursor_end_sha256"]
        candidate_sha256 = hashlib.sha256(token(result["candidate_cursor"]).encode("utf-8")).hexdigest()
        if (accepted is not True
                or not isinstance(start_sha256, str) or HEX64.fullmatch(start_sha256) is None
                or not isinstance(end_sha256, str) or HEX64.fullmatch(end_sha256) is None
                or not hmac.compare_digest(end_sha256, candidate_sha256)):
            raise ValueError("binding")
    except Exception:  # noqa: BLE001 - SnapshotRefused, KeyError, TypeError, ValueError: one token, never its text
        raise BuildRefused("source_binding_refused") from None
    binding = {"schema": SOURCE_BINDING_SCHEMA, "anchor_sha256": source["expected_anchor_sha256"],
               "projection_sha256": projection_sha256, "cursor_start_sha256": start_sha256,
               "cursor_end_sha256": end_sha256, "policy_sha256": policy_sha256, "roster_sha256": roster_sha256}
    if set(binding) != SOURCE_BINDING_KEYS:
        raise BuildRefused("source_binding_shape")
    return binding


def _history_merger():
    """The pinned Lead merge helper (``shared_history.merge_projections``), imported lazily; any import failure or a
    module without that API refuses ``history_helper_unavailable`` (the merge is never re-implemented here)."""
    try:
        module = importlib.import_module(HISTORY_MODULE)
        merge = module.merge_projections
    except Exception:  # noqa: BLE001 - ImportError, AttributeError or an import-time failure: one token
        raise BuildRefused("history_helper_unavailable") from None
    if not callable(merge):
        raise BuildRefused("history_helper_unavailable")
    return merge


def _read_plain_file(path):
    """Bounded read of one regular file that is neither a symlink nor a Windows reparse point (junction); any
    filesystem problem is the fixed token ``predecessor_unreadable``, no path ever leaves this function."""
    try:
        info = os.lstat(path)
        reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if stat.S_ISLNK(info.st_mode) or reparse or not stat.S_ISREG(info.st_mode):
            raise BuildRefused("predecessor_unreadable")
        if info.st_size > PREDECESSOR_FILE_LIMIT:
            raise BuildRefused("predecessor_over_bytes")
        with open(path, "rb") as handle:
            data = handle.read(PREDECESSOR_FILE_LIMIT + 1)
    except OSError:
        raise BuildRefused("predecessor_unreadable") from None
    if len(data) > PREDECESSOR_FILE_LIMIT:
        raise BuildRefused("predecessor_over_bytes")
    return data


def load_predecessor(predecessor, policy, delta_binding):
    """The caller's pinned previous generation -> (its validated canonical projection, a closed receipt summary,
    continuity_checked).

    ``predecessor`` is exactly ``{memory_dir, memory_sha256, projection_sha256, build_receipt_sha256}``
    (``predecessor_invalid``); ``memory.json``, ``projection.json`` and ``build_receipt.json`` are read bounded from a
    plain directory (``predecessor_unreadable``/``predecessor_over_bytes``), must hash to the pinned digests and bind
    each other (memory.json and the receipt pin the projection digest, the receipt pins memory.json, the projection is
    its exact canonical bytes: ``predecessor_mismatch``), and must carry EXACTLY the current ``policy`` digests
    (``predecessor_policy_mismatch``: a policy or roster pin change is a series rollover, never a silent carry).
    Continuity (the in-process part of RCO2 H1): when the delta is source-bound the predecessor must be bound too
    (``predecessor_unbound``) and its recorded ``cursor_end_sha256`` must equal the delta's ``cursor_start_sha256``
    (``predecessor_discontinuous``). Whether this predecessor IS the current checkpoint pointer generation is the Lead
    checkpoint's CAS authority and not provable here; the receipt records the digests so the checkpoint can require
    them. Matching digests are integrity checking of what the caller supplied, never authenticity."""
    if (not isinstance(predecessor, dict) or set(predecessor) != PREDECESSOR_KEYS
            or not isinstance(predecessor["memory_dir"], (str, Path))
            or any(not isinstance(predecessor[key], str) or HEX64.fullmatch(predecessor[key]) is None
                   for key in PREDECESSOR_FILES)):
        raise BuildRefused("predecessor_invalid")
    directory = Path(predecessor["memory_dir"])
    contents = {key: _read_plain_file(directory / name) for key, name in PREDECESSOR_FILES.items()}
    for key, data in contents.items():
        if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), predecessor[key]):
            raise BuildRefused("predecessor_mismatch")
    memory = _parse_pinned_json(contents["memory_sha256"], "predecessor_invalid")
    receipt = _parse_pinned_json(contents["build_receipt_sha256"], "predecessor_invalid")
    projection = _parse_pinned_json(contents["projection_sha256"], "predecessor_invalid")
    if (not isinstance(memory, dict) or not isinstance(receipt, dict) or not isinstance(projection, dict)
            or json.dumps(projection, sort_keys=True, indent=1).encode("utf-8") != contents["projection_sha256"]
            or not isinstance(projection.get("records"), list)
            or memory.get("scope") != SCOPE or memory.get("projection_sha256") != predecessor["projection_sha256"]
            or receipt.get("schema") != BUILD_RECEIPT_SCHEMA or receipt.get("scope") != SCOPE
            or receipt.get("projection_sha256") != predecessor["projection_sha256"]
            or receipt.get("memory_sha256") != predecessor["memory_sha256"]):
        raise BuildRefused("predecessor_mismatch")
    if memory.get("policy") != policy or receipt.get("policy") != policy:
        raise BuildRefused("predecessor_policy_mismatch")
    binding = receipt.get("source_binding")
    bound = (isinstance(binding, dict) and set(binding) == SOURCE_BINDING_KEYS
             and binding["schema"] == SOURCE_BINDING_SCHEMA
             and all(isinstance(binding[key], str) and HEX64.fullmatch(binding[key]) is not None
                     for key in SOURCE_BINDING_KEYS - {"schema"}))
    if binding is not None and not bound:
        raise BuildRefused("predecessor_mismatch")
    continuity_checked = False
    if delta_binding is not None:
        if not bound:
            raise BuildRefused("predecessor_unbound")  # a bound delta never extends an unbound (synthetic) series
        if not hmac.compare_digest(binding["cursor_end_sha256"], delta_binding["cursor_start_sha256"]):
            raise BuildRefused("predecessor_discontinuous")
        continuity_checked = True
    summary = {"memory_sha256": predecessor["memory_sha256"], "projection_sha256": predecessor["projection_sha256"],
               "build_receipt_sha256": predecessor["build_receipt_sha256"], "records": len(projection["records"]),
               "source_bound": bound, "cursor_end_sha256": binding["cursor_end_sha256"] if bound else None}
    return projection, summary, continuity_checked


def build_memory(projection, encode, identity, dim, root, name, *, approved_tasks=None, include_refs=False,
                 roster=None, exclude_review_signal=False, runtime=None, pins=None, source=None,
                 allow_unbound_source=False, predecessor=None):
    """Build ``root/name`` from a validated projection; returns the exact reader descriptor, the build receipt and
    their sha256 digests.

    ``roster`` (REQUIRED, lane -> uuid, see ``shared_projection.load_roster``) and ``exclude_review_signal``
    (REQUIRED ``True``; ``review_exclusion_required`` otherwise, the production path has no lax mode) are the strict
    v6 policy: the projection must carry ``authorship == declared_roster_consistent`` and
    ``review_signal_excluded is True`` (``projection_policy_weak`` otherwise) and is re-validated under this exact
    roster with the exclusion pinned. Both are recorded as ``policy`` in ``memory.json`` (bound by the descriptor's
    ``memory_sha256``) and in the build receipt; the descriptor itself keeps the reader's exact five keys.
    v4: ``pins`` (REQUIRED, ``resolve_pins``) supplies the pinned policy and roster snapshot bytes with their digests;
    the approved set and the roster are DERIVED from them (a caller ``approved_tasks``/``roster``, when also given,
    must be equal: ``pin_caller_mismatch``) and ``memory.policy`` binds the exact ``policy_sha256`` and
    ``roster_sha256`` (Lead 17:15:50Z), so a consumer compares them before any query. ``source`` is the snapshot
    result the Lead verifier binds to this projection (``bind_source``): on the strict publication path (the default)
    a missing source refuses ``source_binding_required``; ``allow_unbound_source=True`` is the synthetic route and
    records ``source_binding None`` / ``source_bound False``. A caller-provided hash is integrity checking of what the
    caller supplied, never source authenticity (``source_authenticated False`` always).
    v5: ``predecessor`` (optional, ``load_predecessor``) pins the PREVIOUS generation; the new generation then indexes
    the pinned Lead merge ``shared_history.merge_projections(previous, delta)`` of the predecessor projection and this
    delta (``history_refused`` on any helper refusal or a merged id set that is not exactly previous | delta), bounded
    by ``MAX_RECORDS`` BEFORE the helper runs (``records_over_bound``), while the source binding still binds the
    DELTA only (the verifier is never called on a merged projection). The receipt and ``memory.json`` record both
    digests explicitly: ``projection_sha256`` = the MERGED projection that is persisted and indexed,
    ``delta_projection_sha256`` = what ``source_binding`` binds; ``history`` carries the predecessor digests, counts
    and whether cursor continuity was checked. An empty delta refuses ``projection_empty`` even with a predecessor
    (a no-change advance is a checkpoint pointer event, Lead-owned). The builder publishes ONE directory; it is not
    an atomic checkpoint publication and never moves a cursor. ``identity`` is the caller-asserted three-key encoder identity (synthetic route); ``runtime`` derives the
    identity from a verified model directory instead (``runtime_identity``) and, when both are given, they must be
    equal (``encoder_identity_mismatch``). Every refusal is ``BuildRefused`` with a fixed token (projection refusals
    are re-raised under their own token). Refuses (nothing written) on an invalid projection, policy, identity, dim,
    root or name, a bound violation, a failing encoder, an unsupported platform or an existing target; after the
    memory directory exists, ANY failure retains the incomplete output, adds ``INCOMPLETE.json`` and removes nothing.
    ``memory.json`` is written last (``_commit_memory``), so it exists only for a complete generation."""
    if (not isinstance(projection, dict) or projection.get("authorship") != sp.AUTHORSHIP_ROSTER
            or projection.get("review_signal_excluded") is not True):
        raise BuildRefused("projection_policy_weak")
    if exclude_review_signal is not True:
        raise BuildRefused("review_exclusion_required")
    resolved = resolve_pins(pins)
    try:
        if roster is not None and sp._roster(roster) != resolved["roster"]:
            raise BuildRefused("pin_caller_mismatch")
        if approved_tasks is not None and sp._approved_tasks(approved_tasks) != resolved["approved_tasks"]:
            raise BuildRefused("pin_caller_mismatch")
        roster, approved_tasks = resolved["roster"], resolved["approved_tasks"]
        sp.validate_projection(projection, approved_tasks, include_refs, roster, exclude_review_signal)
    except sp.ProjectionRefused as refused:
        raise BuildRefused(str(refused)) from None  # B4: one refusal class at the entry, same fixed tokens
    policy = {"authorship": sp.AUTHORSHIP_ROSTER, "review_signal_excluded": True,
              "policy_sha256": resolved["policy_sha256"], "roster_sha256": resolved["roster_sha256"]}
    projection_bytes = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
    projection_sha256 = hashlib.sha256(projection_bytes).hexdigest()
    if source is None:
        if allow_unbound_source is not True:
            raise BuildRefused("source_binding_required")  # the strict publication path has no unbound memory
        source_binding = None
    else:
        source_binding = bind_source(source, projection, projection_sha256, resolved["policy_sha256"],
                                     resolved["roster_sha256"])  # binds the DELTA; never a merged projection
    delta_projection_sha256 = projection_sha256
    history, memory_history = None, None
    if predecessor is not None:
        if not projection["records"]:
            raise BuildRefused("projection_empty")  # an empty delta is a checkpoint pointer event, never a generation
        previous, summary, continuity_checked = load_predecessor(predecessor, policy, source_binding)
        delta_ids = {record["record_id"] for record in projection["records"]}
        previous_ids = {record["record_id"] for record in previous["records"]}
        if len(previous_ids | delta_ids) > MAX_RECORDS:
            raise BuildRefused("records_over_bound")  # the merged set is bounded before the helper and before any write
        merge = _history_merger()
        try:
            merged = merge(previous, projection, policy_bytes=pins["policy_bytes"], policy_sha256=resolved["policy_sha256"],
                           roster_bytes=pins["roster_bytes"], roster_sha256=resolved["roster_sha256"])
            if (not isinstance(merged, dict) or merged.get("authorship") != sp.AUTHORSHIP_ROSTER
                    or merged.get("review_signal_excluded") is not True):
                raise ValueError("merge")
            sp.validate_projection(merged, approved_tasks, include_refs, roster, exclude_review_signal)
            if {record["record_id"] for record in merged["records"]} != previous_ids | delta_ids:
                raise ValueError("merge")  # every prior and every delta record survives, nothing else appears
        except Exception:  # noqa: BLE001 - HistoryRefused, ProjectionRefused, KeyError, TypeError: one token, no text
            raise BuildRefused("history_refused") from None
        delta_rows, delta_refused = len(projection["records"]), dict(sorted(projection["refused"].items()))
        projection = merged
        projection_bytes = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
        projection_sha256 = hashlib.sha256(projection_bytes).hexdigest()
        # delta_refused repeats the delta's own refusal counters (policy exclusions are never counted by the
        # projection, so no review-signal count can appear here); the refused-row contract itself is Lead's.
        history = {"schema": HISTORY_SCHEMA, "helper": HISTORY_MODULE, "predecessor": summary,
                   "delta_projection_sha256": delta_projection_sha256, "delta_records": delta_rows,
                   "delta_refused": delta_refused, "overlap_records": len(previous_ids & delta_ids),
                   "merged_records": len(projection["records"]), "merged_projection_sha256": projection_sha256,
                   "continuity_checked": continuity_checked}
        memory_history = {"predecessor_memory_sha256": summary["memory_sha256"],
                          "predecessor_projection_sha256": summary["projection_sha256"],
                          "delta_projection_sha256": delta_projection_sha256}
    runtime_record = {"identity_source": "caller_asserted"}
    if runtime is not None:
        derived = runtime_identity(runtime)
        if identity is not None and encoder_identity(identity) != derived["identity"]:
            raise BuildRefused("encoder_identity_mismatch")
        encoder = derived["identity"]
        runtime_record = {"generation": derived["generation"], "manifest_sha256": derived["manifest_sha256"],
                          "weights_bytes": derived["weights_bytes"], "identity_source": "encode.model_identity"}
    else:
        encoder = encoder_identity(identity)
    dim = check_dim(dim)
    if not callable(encode):
        raise BuildRefused("encoder_invalid")
    if not isinstance(name, str) or MEMORY_NAME.fullmatch(name) is None:
        raise BuildRefused("memory_name_invalid")
    _publication_supported()
    try:
        root = sp._output_root(root)
    except sp.ProjectionRefused as refused:
        raise BuildRefused(str(refused)) from None
    _check_bounds(projection["records"])
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
        stage = "finalize"  # every final object is computed and guarded before any of them is written
        memory = {"schema": memory_mod.SCHEMA, "authority": "none", "scope": SCOPE, "cutoff_utc": cutoff_utc,
                  "projection_sha256": projection_sha256, "projection_schema": sp.SCHEMA,
                  "projection_file": "projection.json", "encoder": encoder, "dim": dim, "placement": PLACEMENT_RULE,
                  "topology_source_sha256": cell_mod.file_sha256(TOPOLOGY_FILE), "cells": pointers,
                  "records": len(records), "duplicates": duplicates, "views": list(VIEWS),
                  "independent_views": False, "policy": policy, "history": memory_history}
        sp.corpus_mod.ws.guard_object(memory)
        memory_bytes = cell_mod.canonical(memory).encode("utf-8")
        memory_sha256 = hashlib.sha256(memory_bytes).hexdigest()
        descriptor = {"schema": DESCRIPTOR_SCHEMA, "scope": SCOPE, "memory_sha256": memory_sha256, "dim": dim,
                      "encoder": encoder}
        if set(descriptor) != DESCRIPTOR_KEYS:
            raise BuildRefused("descriptor_shape")
        sp.corpus_mod.ws.guard_object(descriptor)
        descriptor_bytes = cell_mod.canonical(descriptor).encode("utf-8")
        descriptor_sha256 = hashlib.sha256(descriptor_bytes).hexdigest()
        build_receipt = {"schema": BUILD_RECEIPT_SCHEMA, "authority": "none", "scope": SCOPE, "memory_name": name,
                         "memory_sha256": memory_sha256, "descriptor_sha256": descriptor_sha256,
                         "descriptor_schema": DESCRIPTOR_SCHEMA, "projection_sha256": projection_sha256,
                         "delta_projection_sha256": delta_projection_sha256, "history": history,
                         "projection_schema": sp.SCHEMA, "projection_file": "projection.json", "encoder": encoder,
                         "dim": dim, "records": len(records), "duplicates": duplicates,
                         "cells": {cell_id: row["count"] for cell_id, row in pointers.items()},
                         "cutoff_utc": cutoff_utc, "views": list(VIEWS), "independent_views": False,
                         "canonical_event": False, "authenticated": False, "policy": policy,
                         "runtime": runtime_record, "source_binding": source_binding,
                         "source_bound": source_binding is not None, "source_authenticated": False,
                         "bounds": {"max_records": MAX_RECORDS, "max_input_bytes": MAX_INPUT_BYTES,
                                    "encode_batch": ENCODE_BATCH},
                         "watchdog": WATCHDOG_RULE, "built_utc": _utc_now()}
        sp.corpus_mod.ws.guard_object(build_receipt)
        receipt_bytes = cell_mod.canonical(build_receipt).encode("utf-8")
        stage = "build_receipt"
        _write_new(memory_dir / "build_receipt.json", receipt_bytes)
        stage = "descriptor"
        _write_new(memory_dir / "descriptor.json", descriptor_bytes)
        stage = "memory"  # the commit point: memory.json is the LAST file and appears only complete
        _commit_memory(memory_dir, memory_bytes)
    except BuildRefused as refused:
        _report_incomplete(memory_dir, name, str(refused), stage)
        raise
    except sp.corpus_mod.ws.WorkspaceError:
        _report_incomplete(memory_dir, name, "output_guarded", stage)
        raise BuildRefused("output_guarded") from None
    except Exception:  # noqa: BLE001 - B1: faiss RuntimeError, AttributeError or anything else after mkdir is one
        _report_incomplete(memory_dir, name, "write_failed", stage)  # fixed token with INCOMPLETE, never its text
        raise BuildRefused("write_failed") from None
    return {"memory_sha256": memory_sha256, "descriptor_sha256": descriptor_sha256, "descriptor": descriptor,
            "build_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(), "build_receipt": build_receipt,
            "projection_sha256": projection_sha256}
