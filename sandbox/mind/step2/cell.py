"""Read-only verifier for frozen Q4 cell generations from 6cf81dbb."""
import hashlib
import json
import numpy as np
from core.faiss_store import FaissCollection

SCHEMA = "wd.bridge-mind-hex-cell.v1"
LOG_SCHEMA = "wd.bridge-mind-hex-cell-log.v1"
ZERO = "0" * 64
FILES = ("index", "meta", "vectors", "provenance")
PROVENANCE_KEYS = ("record_id", "kind", "time_utc", "cell_id", "provenance")


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
