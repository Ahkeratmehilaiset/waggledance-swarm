"""Read-only verifier for frozen Q4 cell generations from 6cf81dbb."""
import copy
import hashlib
import json
from pathlib import Path
import faiss
import numpy as np
from core.faiss_store import FaissCollection
from waggledance.core.hex_cell_topology import ALL_CELLS

SCHEMA = "wd.bridge-mind-hex-cell.v1"
LOG_SCHEMA = "wd.bridge-mind-hex-cell-log.v1"
ZERO = "0" * 64
FILES = ("index", "meta", "vectors", "provenance")
PROVENANCE_KEYS = ("record_id", "kind", "time_utc", "cell_id", "provenance")
# These bounds apply only to control documents, not the memory's FAISS data.
MAX_POINTER_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_LOG_BYTES = 16 * 1024 * 1024
# New Q4-reader availability limit: legacy indices above 256 MiB are refused.
# It bounds serialized input only, not FAISS native-deserializer allocations.
MAX_Q4_INDEX_BYTES = 256 * 1024 * 1024
# Opt-in in-memory cell snapshots are limited to 512 MiB in total. This is an
# availability limit, not a bound on FAISS native-deserializer allocations.
MAX_Q4_CELL_SNAPSHOT_BYTES = 512 * 1024 * 1024


class CellError(Exception):
    """A refused cell operation; ``code`` is a fixed reason code that never echoes input."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _read_control(path, limit, kind):
    """Read at most limit+1 bytes, including when a file grows during the read."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        raise CellError(f"{kind}_unreadable") from None
    if len(raw) > limit:
        raise CellError(f"{kind}_over_budget")
    return raw


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

def read_log(cell_dir):
    """The log lines after checking the hash chain, or a CellError."""
    log_path = cell_dir / "LOG.jsonl"
    if not log_path.exists():
        return []
    return _parse_log_bytes(_read_control(log_path, MAX_LOG_BYTES, "log"))


def _parse_log_bytes(data):
    lines, previous = [], ZERO
    raw_lines = data.split(b"\n")
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


class _ReadOnlySnapshotIndex:
    """Only bounded reconstruction, with no writable native result exposed."""

    __slots__ = ("__index", "__count", "__dim")

    def __init__(self, index, count, dim):
        object.__setattr__(self, "_ReadOnlySnapshotIndex__index", index)
        object.__setattr__(self, "_ReadOnlySnapshotIndex__count", count)
        object.__setattr__(self, "_ReadOnlySnapshotIndex__dim", dim)

    def __setattr__(self, name, value):
        raise AttributeError("snapshot_index_read_only")

    def reconstruct_n(self, start, count):
        if (type(start) is not int or type(count) is not int or start < 0 or count < 0
                or start + count > self.__count or count * self.__dim * 4 > MAX_Q4_INDEX_BYTES):
            raise CellError("snapshot_reconstruct_invalid")
        result = np.array(self.__index.reconstruct_n(start, count), dtype=np.float32, copy=True, order="C")
        if result.shape != (count, self.__dim):
            raise CellError("snapshot_reconstruct_invalid")
        return result


class _ReadOnlySnapshotCollection:
    """Reader facade, not a security boundary against code in this process."""

    __slots__ = ("__search", "__texts", "__doc_ids", "__count", "__index", "__metadata")

    def __init__(self, collection, *, verified_dim):
        object.__setattr__(self, "_ReadOnlySnapshotCollection__search", collection.search)
        if any(type(text) is not str for text in collection._texts):
            raise CellError("snapshot_text_invalid")
        object.__setattr__(self, "_ReadOnlySnapshotCollection__texts", tuple(collection._texts))
        object.__setattr__(self, "_ReadOnlySnapshotCollection__doc_ids", tuple(collection._doc_ids))
        # The per-row metadata of the receipt-hashed meta.json, copied once; readers
        # (Q4 origin checks) treat anything but a dict per row as unverifiable.
        object.__setattr__(self, "_ReadOnlySnapshotCollection__metadata",
                           tuple(copy.deepcopy(list(getattr(collection, "_metadata", ())))))
        object.__setattr__(self, "_ReadOnlySnapshotCollection__count", collection.count)
        object.__setattr__(self, "_ReadOnlySnapshotCollection__index",
                           _ReadOnlySnapshotIndex(collection._index, collection.count, verified_dim))

    def __setattr__(self, name, value):
        raise AttributeError("snapshot_collection_read_only")

    @property
    def count(self):
        return self.__count

    @property
    def _texts(self):
        return self.__texts

    @property
    def _index(self):
        return self.__index

    @property
    def _metadata(self):
        return self.__metadata

    def has_doc_id(self, doc_id):
        return doc_id in self.__doc_ids

    def search(self, vector, k=5):
        return copy.deepcopy(self.__search(vector, k))


def verify_generation(gen_dir, cell_id, receipt_sha):
    """Check one generation directory completely; return (receipt, rows, collection, rebuild_possible)."""
    return _verify_generation(gen_dir, cell_id, receipt_sha, snapshot=None)


def verify_generation_snapshot(cell_id, receipt_sha, *, snapshot_bytes, enabled=False):
    """Opt-in cell verification over caller-pinned immutable input bytes only.

    Caller must independently authenticate the receipt pin and safely obtain the
    snapshot. This neither verifies a current pointer/log nor authenticates a
    caller, proves freshness, or bounds native FAISS allocations.
    """
    snapshot = receipt = rows = collection = None
    try:
        if (enabled is not True or type(snapshot_bytes) is not dict
                or type(cell_id) is not str or cell_id not in ALL_CELLS or type(receipt_sha) is not str
                or len(receipt_sha) != 64 or any(c not in "0123456789abcdef" for c in receipt_sha)):
            raise CellError("snapshot_invalid")
        snapshot = dict(snapshot_bytes)
        if (not {"receipt", "index", "meta", "provenance"} <= snapshot.keys()
                or not snapshot.keys() <= {"receipt", "index", "meta", "provenance", "vectors"}
                or any(type(key) is not str for key in snapshot)
                or any(type(value) is not bytes for value in snapshot.values())
                or sum(map(len, snapshot.values())) > MAX_Q4_CELL_SNAPSHOT_BYTES
                or len(snapshot["receipt"]) > MAX_RECEIPT_BYTES):
            raise CellError("snapshot_invalid")
        # This is a synthetic label only. The verified metadata and index bytes
        # force FaissCollection's strict path; it must never reopen this path.
        receipt, rows, collection, rebuild = _verify_generation(
            Path("__q4_snapshot_inert__"), cell_id, receipt_sha, snapshot=snapshot)
        return receipt, rows, _ReadOnlySnapshotCollection(collection, verified_dim=int(receipt["content"]["dim"])), rebuild
    except Exception:
        pass
    del snapshot, snapshot_bytes, receipt, rows, collection
    raise CellError("snapshot_invalid") from None


def _parse_generation_receipt(receipt_bytes, cell_id, receipt_sha, *, strict_snapshot=False):
    if sha256(receipt_bytes) != receipt_sha:
        raise CellError("receipt_mismatch")
    try:
        receipt = json.loads(receipt_bytes)
        content = receipt["content"]
        if receipt.get("schema") != SCHEMA or receipt["content_sha256"] != sha256(canonical(content).encode("utf-8")):
            raise CellError("receipt_unverifiable")
        if strict_snapshot and type(content["dim"]) is not int:
            raise CellError("snapshot_invalid")
        count, dim = int(content["count"]), int(content["dim"])
    except CellError:
        raise
    except (ValueError, KeyError, TypeError):
        raise CellError("receipt_unreadable") from None
    if content.get("cell_id") != cell_id:
        raise CellError("receipt_cell_mismatch")
    return receipt, content, count, dim


def _verify_generation(gen_dir, cell_id, receipt_sha, *, snapshot):
    if snapshot is None:
        if not gen_dir.is_dir():
            raise CellError("generation_missing")
        receipt_path = gen_dir / "receipt.json"
        if not receipt_path.exists():
            raise CellError("receipt_missing")
        receipt_bytes = _read_control(receipt_path, MAX_RECEIPT_BYTES, "receipt")
    else:
        receipt_bytes = snapshot["receipt"]
    receipt, content, count, dim = _parse_generation_receipt(
        receipt_bytes, cell_id, receipt_sha, strict_snapshot=snapshot is not None)
    files = paths(gen_dir, cell_id)
    rebuild_possible = True
    for name in FILES:
        missing = not files[name].exists() if snapshot is None else name not in snapshot
        if missing:
            if name == "vectors":
                rebuild_possible = False
                continue
            raise CellError(f"{name}_missing")
        if name in ("meta", "provenance"):
            # The parsed object must come from the very bytes that were hashed.
            # Process one file at a time: legacy generations have no size bound.
            raw = files[name].read_bytes() if snapshot is None else snapshot[name]
            if sha256(raw) != content["files"][name]:
                raise CellError(f"{name}_checksum")
            try:
                if name == "meta":
                    meta = json.loads(raw)
                    meta_ids, meta_dim = list(meta["doc_ids"]), int(meta["dim"])
                else:
                    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]
            except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                raise CellError(f"{name}_unreadable") from None
            finally:
                del raw
        elif name == "index":
            # The bounded snapshot below checks the exact bytes passed to FAISS.
            # Do not stream the same file without a size bound first.
            continue
        elif (file_sha256(files[name]) if snapshot is None else sha256(snapshot[name])) != content["files"][name]:
            raise CellError(f"{name}_checksum")
    if meta_dim != dim:
        raise CellError("dim_mismatch")
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
    if snapshot is None:
        try:
            with files["index"].open("rb") as index_file:
                index_bytes = index_file.read(MAX_Q4_INDEX_BYTES + 1)
        except OSError:
            raise CellError("index_unreadable") from None
    else:
        index_bytes = snapshot["index"]
    if len(index_bytes) > MAX_Q4_INDEX_BYTES:
        raise CellError("index_over_budget")
    if sha256(index_bytes) != content["files"]["index"]:
        raise CellError("index_checksum")
    try:
        collection = FaissCollection(f"cell_{cell_id}", dim=dim, persist_dir=str(files["index"].parent),
                                     verified_meta=meta, verified_index_bytes=index_bytes)
    except ValueError:
        raise CellError("index_unreadable") from None
    index = collection._index
    try:
        # Q4 ranks inner-product similarity. Matching vectors alone do not prove
        # that a path-loaded index has those search semantics.
        metric = index.metric_type
        correct_semantics = (type(index) is faiss.IndexFlatIP and type(metric) is int
                             and metric == faiss.METRIC_INNER_PRODUCT)
    except Exception:
        correct_semantics = False
    if not correct_semantics:
        raise CellError("index_semantics_invalid") from None
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
        raw = files["vectors"].read_bytes() if snapshot is None else snapshot["vectors"]
        # The earlier streaming check is retained; bind the bytes actually used too.
        if sha256(raw) != content["files"]["vectors"]:
            raise CellError("vectors_checksum")
        if len(raw) != count * dim * 4:
            raise CellError("vectors_size")
        retained = np.frombuffer(raw, dtype="<f4").reshape(count, dim) if count else stored
        if count and pairs_sha256(meta_ids, _normalized(retained)) != content["pairs_sha256"]:
            raise CellError("vectors_pairs_mismatch")
    return receipt, rows, collection, rebuild_possible


def _normalized(vectors):
    return FaissCollection._normalize(np.asarray(vectors, dtype=np.float32))


def _parse_pointer_bytes(raw):
    try:
        pointer = json.loads(raw)
        generation, seq, receipt_sha = int(pointer["generation"]), int(pointer["seq"]), str(pointer["receipt_sha256"])
    except (ValueError, KeyError, TypeError):
        raise CellError("pointer_unreadable") from None
    return pointer, generation, seq, receipt_sha


def _preflight_snapshot_controls(cell_id, *, pointer_bytes, log_bytes, artifact_bytes,
                                 expected_generation, expected_receipt_sha256):
    """Validate pinned cell controls and receipt, without loading an index."""
    if (type(cell_id) is not str or cell_id not in ALL_CELLS
            or type(pointer_bytes) is not bytes or len(pointer_bytes) > MAX_POINTER_BYTES
            or type(log_bytes) is not bytes or len(log_bytes) > MAX_LOG_BYTES
            or type(artifact_bytes) is not dict or type(artifact_bytes.get("receipt")) is not bytes
            or len(artifact_bytes["receipt"]) > MAX_RECEIPT_BYTES):
        raise CellError("snapshot_invalid")
    pointer, generation, seq, receipt_sha = _parse_pointer_bytes(pointer_bytes)
    if (type(pointer.get("generation")) is not int or generation < 0
            or type(pointer.get("seq")) is not int or seq < 1
            or type(pointer.get("receipt_sha256")) is not str
            or type(expected_generation) is not int or expected_generation != generation
            or type(expected_receipt_sha256) is not str or expected_receipt_sha256 != receipt_sha):
        raise CellError("snapshot_invalid")
    log = _parse_log_bytes(log_bytes)
    if not log:
        raise CellError("log_missing")
    if any(type(line.get("seq")) is not int or type(line.get("generation")) is not int for line in log):
        raise CellError("snapshot_invalid")
    last = log[-1]
    if (last["seq"], last["generation"], last["receipt_sha256"]) != (seq, generation, receipt_sha):
        raise CellError("pointer_log_mismatch")
    receipt, _, _, _ = _parse_generation_receipt(artifact_bytes["receipt"], cell_id, receipt_sha,
                                                  strict_snapshot=True)
    return pointer, receipt


def load_snapshot(cell_id, *, pointer_bytes, log_bytes, artifact_bytes,
                  expected_generation, expected_receipt_sha256, enabled=False):
    """Load caller-anchored snapshot bytes only; not freshness or caller authentication."""
    pointer = receipt = rows = collection = None
    try:
        if enabled is not True:
            raise CellError("snapshot_invalid")
        pointer, receipt = _preflight_snapshot_controls(
            cell_id, pointer_bytes=pointer_bytes, log_bytes=log_bytes, artifact_bytes=artifact_bytes,
            expected_generation=expected_generation, expected_receipt_sha256=expected_receipt_sha256)
        receipt, rows, collection, rebuild = verify_generation_snapshot(
            cell_id, expected_receipt_sha256, snapshot_bytes=artifact_bytes, enabled=True)
        return CellView(cell_id, "HEALTHY", None, collection, rows, receipt, pointer, rebuild)
    except Exception:
        pass
    del pointer, receipt, rows, collection, pointer_bytes, log_bytes, artifact_bytes
    raise CellError("snapshot_invalid") from None


def load(cell_dir, cell_id):
    """A CellView: ABSENT when the cell was never built, else HEALTHY or QUARANTINED, never a silent empty."""
    if not cell_dir.exists():
        return CellView(cell_id, "ABSENT", "cell_absent")
    try:
        pointer_path = cell_dir / "current.json"
        if not pointer_path.exists():
            raise CellError("pointer_missing")
        pointer, generation, seq, receipt_sha = _parse_pointer_bytes(
            _read_control(pointer_path, MAX_POINTER_BYTES, "pointer"))
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
