"""HEX MEMORY STEP 2, cell: one WaggleDance hex cell as a verified FAISS memory with generations.

A cell generation is a directory ``gen-NNNNNN`` holding:

* ``faiss/cell_<id>/index.faiss`` and ``meta.json``: WaggleDance's own ``core.faiss_store.FaissCollection`` layout,
  written by ``FaissCollection.save`` unchanged;
* ``vectors.f32``: the retained float32 vectors, row-major, count x dim (the rebuild source, no re-encoding);
* ``provenance.jsonl``: one row per vector in index order (record id, kind, time, cell, provenance);
* ``receipt.json``: count, dim, cutoff, encoder policy, the sha256 of every file above, ``ids_sha256``, and
  ``pairs_sha256`` binding each (doc id, vector bytes) pair in order, so a rotated id list cannot pass a count check.

The cell directory holds the pointer ``current.json`` (generation, seq, receipt sha256) and ``LOG.jsonl``, an
append-only hash chain with one line per published generation. The receipt's own sha256 is kept in both, outside
the receipt. Publishing stages the generation in a temporary directory, verifies it from disk, renames it, appends
the log line and only then swaps the pointer with ``os.replace``. A second writer is refused by an exclusive lock.

D18 (2026-10-19, fable-5): a new generation is numbered one past both the last logged generation and every
canonical ``gen-NNNNNN`` entry in the cell directory (``next_generation``). The rename comes before the log line, so
an error at the append (a full disk, say) or a crash leaves an orphan directory the log never names; it is skipped,
never adopted, verified, changed or removed, and it no longer refuses every later publish as ``generation_exists``.
After a hard crash the writer lock also stays, as before, until it is removed by hand. A torn last log line (a crash
inside the append) still quarantines the cell and refuses publishing until it is repaired by hand.

R5 (2026-10-21, fable-5): ``write_generation`` writes a generation whole into a fresh staging directory only. An
existing directory that holds anything is refused as ``staging_not_empty`` before a byte is written (the FAISS count
check alone let a published empty generation, or a directory holding only the other files, be rewritten); each file
is created exclusively (``write_new_file``), and all five files are fsynced before ``publish`` renames the generation
into place, as the log line and the pointer already were. A cut file in a staging directory is never published:
``publish`` removes the directory on any exception, and one a hard crash leaves is hidden and never adopted.
``write_atomic`` stays a replace by contract (the pointer and ``memory.json``): the old bytes stay on any failure.

Loading verifies the pointer, the log chain, the receipt and every file BEFORE ``FaissCollection`` reads anything:
``FaissCollection._try_load`` turns a missing or corrupt file into an empty index with only a warning. Any failure
QUARANTINES the cell with a fixed reason code. A quarantined cell never answers and never abstains; the query layer
reports it as DEGRADED. A missing ``vectors.f32`` alone keeps the cell answering but sets ``rebuild_possible`` false.

Limitation (stated, not hidden): a writer that rewrites the pointer, the log and the memory file consistently is
caught only against an externally posted freeze hash.
"""

import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import sys
import uuid
from pathlib import Path

import numpy as np

SRC = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SRC))
from core.faiss_store import FaissCollection  # noqa: E402

SCHEMA = "wd.bridge-mind-hex-cell.v1"
LOG_SCHEMA = "wd.bridge-mind-hex-cell-log.v1"
ZERO = "0" * 64
FILES = ("index", "meta", "vectors", "provenance")
PROVENANCE_KEYS = ("record_id", "kind", "time_utc", "cell_id", "provenance")
GEN_NAME = re.compile(r"gen-([0-9]{6,})")  # D18: f"gen-{generation:06d}" names, at least six digits


class CellError(Exception):
    """A refused cell operation; ``code`` is a fixed reason code that never echoes input."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def write_atomic(path, data):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_new_file(path, data):
    """R5: create ``path`` exclusively, so a file already there is never replaced (FileExistsError), then write, flush
    and fsync ``data`` (the ``corpus.publish_new`` file shape), for a generation's fresh staging directory."""
    with open(path, "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def pairs_sha256(ids, vectors):
    digest = hashlib.sha256()
    for doc_id, row in zip(ids, vectors):
        digest.update(doc_id.encode("utf-8") + b"\x00" + np.ascontiguousarray(row, dtype="<f4").tobytes())
    return digest.hexdigest()


def paths(gen_dir, cell_id):
    faiss_dir = gen_dir / "faiss" / f"cell_{cell_id}"
    return {"index": faiss_dir / "index.faiss", "meta": faiss_dir / "meta.json",
            "vectors": gen_dir / "vectors.f32", "provenance": gen_dir / "provenance.jsonl"}


def check_vectors(vectors, dim):
    if vectors.ndim != 2 or vectors.shape[1] != dim:
        raise CellError("vector_shape")
    if not np.isfinite(vectors).all():
        raise CellError("vector_not_finite")
    if (np.linalg.norm(vectors, axis=1) == 0.0).any():
        raise CellError("vector_zero_norm")


class Lock:
    """Exclusive writer lock: a second writer is refused, a stale lock is never broken automatically."""

    def __init__(self, cell_dir):
        self.path = cell_dir / "writer.lock"

    def __enter__(self):
        try:
            handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise CellError("writer_busy") from None
        os.write(handle, f"{os.getpid()} {utc_now()}".encode("ascii"))
        os.close(handle)
        return self

    def __exit__(self, *exc):
        self.path.unlink()
        return False


def read_log(cell_dir):
    """The log lines after checking the hash chain, or a CellError."""
    log_path = cell_dir / "LOG.jsonl"
    if not log_path.exists():
        return []
    lines, previous = [], ZERO
    try:
        raw_lines = log_path.read_bytes().split(b"\n")
    except OSError:
        raise CellError("log_unreadable") from None
    if raw_lines and raw_lines[-1] == b"":
        raw_lines.pop()
    for number, raw in enumerate(raw_lines, start=1):
        try:
            line = json.loads(raw)
        except ValueError:
            raise CellError("log_unreadable") from None
        if not isinstance(line, dict) or line.get("schema") != LOG_SCHEMA or line.get("seq") != number \
                or line.get("prev_line_sha256") != previous:
            raise CellError("log_chain_broken")
        lines.append(line)
        previous = sha256(raw)
    return lines


def write_generation(gen_dir, cell_id, records, vectors, *, dim, cutoff_utc, encoder, corpus_sha256, source):
    """Write one complete generation into ``gen_dir`` (a fresh staging directory) and return its receipt. R5: an
    existing ``gen_dir`` that holds anything is refused before a byte is written, nothing is ever replaced, and every
    file is fsynced before this returns (module docstring)."""
    vectors = np.ascontiguousarray(vectors, dtype="<f4")
    ids = [record["record_id"] for record in records]
    if len(ids) != len(set(ids)):
        raise CellError("duplicate_id")
    if len(ids) != vectors.shape[0]:
        raise CellError("count_mismatch")
    check_vectors(vectors, dim)
    for record in records:
        if record["time_utc"] >= cutoff_utc:
            raise CellError("provenance_after_cutoff")
    files = paths(gen_dir, cell_id)
    if gen_dir.exists() and os.listdir(gen_dir):  # R5: never a directory that already holds a file to replace
        raise CellError("staging_not_empty")
    collection = FaissCollection(f"cell_{cell_id}", dim=dim, persist_dir=str(files["index"].parent))
    if collection.count:
        raise CellError("staging_not_empty")
    if ids:
        collection.add_batch(ids, [record["stored_text"] for record in records], vectors,
                             [dict(record["meta"], cell_id=cell_id) for record in records])
    if collection.count != len(ids):
        raise CellError("count_mismatch")
    collection.save()
    for name in ("index", "meta"):  # R5: FaissCollection.save does not sync; opened only to fsync, nothing written
        with open(files[name], "rb+") as handle:
            os.fsync(handle.fileno())
    write_new_file(files["vectors"], vectors.tobytes())
    rows = [{"record_id": r["record_id"], "kind": r["kind"], "time_utc": r["time_utc"], "cell_id": cell_id,
             "provenance": r["provenance"]} for r in records]
    write_new_file(files["provenance"], "".join(canonical(row) + "\n" for row in rows).encode("utf-8"))
    stored = np.vstack([collection._index.reconstruct(i) for i in range(collection.count)]) if ids \
        else np.zeros((0, dim), dtype="<f4")
    content = {
        "cell_id": cell_id, "count": len(ids), "dim": dim, "cutoff_utc": cutoff_utc, "encoder": encoder,
        "corpus_sha256": corpus_sha256, "ids_sha256": sha256("\n".join(ids).encode("utf-8")),
        "pairs_sha256": pairs_sha256(ids, stored),
        "files": {name: file_sha256(files[name]) for name in FILES},
    }
    receipt = {"schema": SCHEMA, "authority": "none", "scope": "shadow_memory_not_gate", "content": content,
               "content_sha256": sha256(canonical(content).encode("utf-8")), "source": source,
               "built_utc": utc_now()}
    write_new_file(gen_dir / "receipt.json", canonical(receipt).encode("utf-8"))
    return receipt


def next_generation(cell_dir, log):
    """D18: one past the last logged generation and every canonical ``gen-NNNNNN`` entry (a file or a directory) in
    ``cell_dir``, so an orphan left between the rename and the log line never blocks a publish (module docstring)."""
    present = [int(match.group(1)) for match in map(GEN_NAME.fullmatch, os.listdir(cell_dir))
               if match and match.group(0) == f"gen-{int(match.group(1)):06d}"]
    return max([log[-1]["generation"] if log else 0] + present) + 1


def publish(cell_dir, cell_id, records, vectors, *, dim, cutoff_utc, encoder, corpus_sha256, source="encode"):
    """Stage, verify, rename, log, then swap the pointer. Returns the new pointer."""
    cell_dir.mkdir(parents=True, exist_ok=True)
    with Lock(cell_dir):
        log = read_log(cell_dir)
        generation = next_generation(cell_dir, log)  # D18: never a name an orphan already holds
        staging = cell_dir / f".staging-{generation:06d}-{uuid.uuid4().hex}"
        staging.mkdir()
        try:
            receipt = write_generation(staging, cell_id, records, vectors, dim=dim, cutoff_utc=cutoff_utc,
                                       encoder=encoder, corpus_sha256=corpus_sha256, source=source)
            receipt_sha = file_sha256(staging / "receipt.json")
            verify_generation(staging, cell_id, receipt_sha)
            final = cell_dir / f"gen-{generation:06d}"
            if final.exists():
                raise CellError("generation_exists")
            os.rename(staging, final)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        previous = sha256(canonical(log[-1]).encode("utf-8")) if log else ZERO
        line = {"schema": LOG_SCHEMA, "seq": len(log) + 1, "generation": generation, "receipt_sha256": receipt_sha,
                "content_sha256": receipt["content_sha256"], "prev_line_sha256": previous,
                "published_utc": utc_now()}
        with open(cell_dir / "LOG.jsonl", "ab") as handle:
            handle.write(canonical(line).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        pointer = {"generation": generation, "seq": line["seq"], "receipt_sha256": receipt_sha}
        write_atomic(cell_dir / "current.json", canonical(pointer).encode("utf-8"))
        return pointer


class CellView:
    """A loaded cell: HEALTHY with a verified collection, or QUARANTINED with a reason code."""

    def __init__(self, cell_id, status, reason=None, collection=None, rows=None, receipt=None, pointer=None,
                 rebuild_possible=False):
        self.cell_id, self.status, self.reason = cell_id, status, reason
        self.collection, self.rows, self.receipt, self.pointer = collection, rows or [], receipt, pointer
        self.rebuild_possible = rebuild_possible
        self.by_id = {row["record_id"]: row for row in self.rows}

    @property
    def healthy(self):
        return self.status == "HEALTHY"

    def search(self, vector, k):
        """(record_id, score) pairs, best first; refuses a wrong-width or non-finite query."""
        if not self.healthy:
            raise CellError("cell_quarantined")
        vector = np.asarray(vector, dtype=np.float32).reshape(-1)
        if vector.shape[0] != self.receipt["content"]["dim"]:
            raise CellError("query_dim")
        if not np.isfinite(vector).all() or float(np.linalg.norm(vector)) == 0.0:
            raise CellError("query_invalid")
        if self.collection.count == 0:
            return []
        return [(result.doc_id, float(result.score)) for result in self.collection.search(vector, k)]


def verify_generation(gen_dir, cell_id, receipt_sha):
    """Check one generation directory completely; return (receipt, rows, collection, rebuild_possible)."""
    if not gen_dir.is_dir():
        raise CellError("generation_missing")
    receipt_path = gen_dir / "receipt.json"
    if not receipt_path.exists():
        raise CellError("receipt_missing")
    if file_sha256(receipt_path) != receipt_sha:
        raise CellError("receipt_mismatch")
    try:
        receipt = json.loads(receipt_path.read_bytes())
        content = receipt["content"]
        if receipt.get("schema") != SCHEMA or receipt["content_sha256"] != sha256(canonical(content).encode("utf-8")):
            raise CellError("receipt_unverifiable")
        count, dim = int(content["count"]), int(content["dim"])
    except CellError:
        raise
    except (ValueError, KeyError, TypeError):
        raise CellError("receipt_unreadable") from None
    if content.get("cell_id") != cell_id:
        raise CellError("receipt_cell_mismatch")
    files = paths(gen_dir, cell_id)
    rebuild_possible = True
    for name in FILES:
        if not files[name].exists():
            if name == "vectors":
                rebuild_possible = False
                continue
            raise CellError(f"{name}_missing")
        if file_sha256(files[name]) != content["files"][name]:
            raise CellError(f"{name}_checksum")
    try:
        meta = json.loads(files["meta"].read_bytes())
        meta_ids, meta_dim = list(meta["doc_ids"]), int(meta["dim"])
    except (ValueError, KeyError, TypeError):
        raise CellError("meta_unreadable") from None
    if meta_dim != dim:
        raise CellError("dim_mismatch")
    try:
        rows = [json.loads(line) for line in files["provenance"].read_bytes().decode("utf-8").splitlines() if line]
    except (ValueError, UnicodeDecodeError):
        raise CellError("provenance_unreadable") from None
    ids = [row.get("record_id") for row in rows]
    if len(rows) != count or len(meta_ids) != count:
        raise CellError("count_mismatch")
    if ids != meta_ids or sha256("\n".join(meta_ids).encode("utf-8")) != content["ids_sha256"]:
        raise CellError("ids_mismatch")
    if len(set(ids)) != len(ids):
        raise CellError("duplicate_id")
    for row in rows:
        if any(key not in row for key in PROVENANCE_KEYS) or not isinstance(row["provenance"], dict):
            raise CellError("provenance_row_incomplete")
        if row["cell_id"] != cell_id:
            raise CellError("provenance_cell_mismatch")
        if not isinstance(row["time_utc"], str) or row["time_utc"] >= content["cutoff_utc"]:
            raise CellError("provenance_after_cutoff")
    collection = FaissCollection(f"cell_{cell_id}", dim=dim, persist_dir=str(files["index"].parent))
    index = collection._index
    if index.ntotal != count or index.d != dim or collection.count != count or collection._doc_ids != meta_ids:
        raise CellError("index_unreadable")
    stored = np.vstack([index.reconstruct(i) for i in range(count)]) if count else np.zeros((0, dim), dtype="<f4")
    try:
        check_vectors(stored, dim)
    except CellError:
        raise CellError("index_vector_invalid") from None
    if pairs_sha256(meta_ids, stored) != content["pairs_sha256"]:
        raise CellError("pairs_mismatch")
    if rebuild_possible:
        raw = files["vectors"].read_bytes()
        if len(raw) != count * dim * 4:
            raise CellError("vectors_size")
        retained = np.frombuffer(raw, dtype="<f4").reshape(count, dim) if count else stored
        if count and pairs_sha256(meta_ids, _normalized(retained)) != content["pairs_sha256"]:
            raise CellError("vectors_pairs_mismatch")
    return receipt, rows, collection, rebuild_possible


def _normalized(vectors):
    return FaissCollection._normalize(np.asarray(vectors, dtype=np.float32))


def load(cell_dir, cell_id):
    """A CellView: ABSENT when the cell was never built, else HEALTHY or QUARANTINED, never a silent empty."""
    if not cell_dir.exists():
        return CellView(cell_id, "ABSENT", "cell_absent")
    try:
        pointer_path = cell_dir / "current.json"
        if not pointer_path.exists():
            raise CellError("pointer_missing")
        try:
            pointer = json.loads(pointer_path.read_bytes())
            generation, seq, receipt_sha = int(pointer["generation"]), int(pointer["seq"]), str(pointer["receipt_sha256"])
        except (ValueError, KeyError, TypeError):
            raise CellError("pointer_unreadable") from None
        log = read_log(cell_dir)
        if not log:
            raise CellError("log_missing")
        last = log[-1]
        if (last["seq"], last["generation"], last["receipt_sha256"]) != (seq, generation, receipt_sha):
            raise CellError("pointer_log_mismatch")
        receipt, rows, collection, rebuild_possible = verify_generation(
            cell_dir / f"gen-{generation:06d}", cell_id, receipt_sha)
    except CellError as error:
        return CellView(cell_id, "QUARANTINED", error.code)
    return CellView(cell_id, "HEALTHY", None, collection, rows, receipt, pointer, rebuild_possible)


def rebuild_from_retained(cell_dir, cell_id):
    """Publish a new generation from the current one's retained vectors (no re-encoding). Returns the pointer."""
    view = load(cell_dir, cell_id)
    if not view.healthy:
        raise CellError(view.reason)
    if not view.rebuild_possible:
        raise CellError("rebuild_impossible")
    content = view.receipt["content"]
    gen_dir = cell_dir / f"gen-{view.pointer['generation']:06d}"
    files = paths(gen_dir, cell_id)
    vectors = np.frombuffer(files["vectors"].read_bytes(), dtype="<f4").reshape(content["count"], content["dim"])
    meta = json.loads(files["meta"].read_bytes())
    records = [{"record_id": row["record_id"], "kind": row["kind"], "time_utc": row["time_utc"],
                "provenance": row["provenance"], "stored_text": text,
                "meta": {key: value for key, value in metadata.items() if key != "cell_id"}}
               for row, text, metadata in zip(view.rows, meta["texts"], meta["metadata"])]
    return publish(cell_dir, cell_id, records, vectors, dim=content["dim"], cutoff_utc=content["cutoff_utc"],
                   encoder=content["encoder"], corpus_sha256=content["corpus_sha256"],
                   source=f"rebuild_from_retained:gen-{view.pointer['generation']:06d}")


def norm_ok(vector):
    value = float(np.linalg.norm(vector))
    return math.isfinite(value) and value > 0.0
