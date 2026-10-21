"""HEX MEMORY: the bridge's events and the lanes' lesson notes in WaggleDance's own hexagonal FAISS memory.

usage: python -B sandbox/mind/hexmind/hex_memory.py build --capture <cycle>/capture.json --lessons <dir> --out <dir>
                 [--before <UTC>] [--embedder e5|hash]
       python -B sandbox/mind/hexmind/hex_memory.py query --index <dir> --text "<situation>" [--k 8]
                 [--route merge|wd_hybrid] [--ring2]

Why (operator 2026-10-13 ~17:30Z): the Mind exists to give the agents eyes and WaggleDance's hexagonal FAISS
database, not one more tool. So nothing here is a new store: records go into ``core.faiss_store.FaissRegistry``
collections ``cell_<id>`` exactly as ``HybridRetrievalService.ingest`` lays them out, and cells and their
ring-1/ring-2 neighbours come from WaggleDance's ``HexCellTopology``.

Records:

* every bridge event of the capture that says something (not claim/release/heartbeat bookkeeping), with its
  Compact event ref as ``doc_id`` (``tools.bridge_compact_view.event_id``), so each hit points at one event;
* every lesson note in ``--lessons`` (the lanes' shared memory notes), ``doc_id`` = ``lesson:<name>@<sha256-12>``.

Cell placement: lessons go to the ``learning`` cell; a finding, a veto or a changes-requested event goes through
the WaggleDance intent ``constraint`` (the ``safety`` cell); everything else through intent ``chat``, i.e.
WaggleDance's own keyword scan, else ``general``.

Embedders (the index records which one built it; a query always uses the same one):

* ``e5`` (default): ``intfloat/multilingual-e5-small``, the embedder WaggleDance's v3.13 defaults declare,
  384 dims, run in-process from the local Hugging Face cache only (``HF_HUB_OFFLINE``/``TRANSFORMERS_OFFLINE``
  are forced on and ``local_files_only`` is passed: nothing is downloaded, no network, no provider). Semantic and
  multilingual (the bridge mixes Finnish and English). CPU inference; same machine, same vectors.
* ``hash``: a deterministic lexical fallback (sha256 token buckets with tf-idf, 4096 dims), byte-identical in
  every process, kept to show what the semantic embedder adds.

Routes:

* ``merge`` (default): the query cell, its ring-1 cells (and ring-2 with ``--ring2``) are each searched for k and
  the hits are merged by score. Measured 2026-10-13: ``HybridRetrievalService.retrieve`` fills neighbour cells only
  up to the k the local cell left over, and a large local cell always returns k weak hits, so it never reached the
  ``learning`` cell where the lessons live.
* ``wd_hybrid``: ``HybridRetrievalService.retrieve`` unchanged, for comparison.

Every stored text passes the decoded privacy guard of the workspace; a refused record is counted, never stored.
Writes only the NEW directory ``--out``, whole or not at all (R5 write-new, ``build``): an existing one is refused
before any input is read. No network, no bridge event, no gate code.
"""

import argparse
import asyncio
import datetime as dt
import errno
import hashlib
import json
import math
import os
import re
import secrets
import sys
import time
from collections import Counter
from pathlib import Path

import faiss
import numpy as np

SRC = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SRC))
from core.faiss_store import FaissRegistry  # noqa: E402
from tools import bridge_mind_workspace as ws  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402
from waggledance.application.services.hybrid_retrieval_service import HybridRetrievalService  # noqa: E402
from waggledance.core.hex_cell_topology import CELL_LEARNING, HexCellTopology  # noqa: E402

SCHEMA = "wd.bridge-mind-hex-memory.v2"
SKIP_TYPES = {"claim", "release", "heartbeat", "wake", "wake_request"}
CONSTRAINT = re.compile(r"(?i)\b(finding|veto|changes_requested|blocked)\b")
WORD = re.compile(r"[^\W_]{2,}", re.UNICODE)
PR = re.compile(r"(?:PR|#)\s?(\d{3,5})\b")
TEXT_CAP = 4000
STORED_CAP = 600
E5_MODEL = "intfloat/multilingual-e5-small"


def tokens(text):
    words = [w.lower() for w in WORD.findall(text)]
    out = words + [a + " " + b for a, b in zip(words, words[1:])]
    out += ["pr=" + n for n in PR.findall(text)]
    return out


class HashEmbedder:
    """Deterministic: each token adds +-(1 + log tf) * idf to one of DIM buckets chosen by its sha256."""

    name, dim = "sha256-token-buckets-tfidf-4096-v2", 4096

    def __init__(self, idf, ceiling):
        self.idf, self.ceiling = idf, ceiling

    @classmethod
    def fit(cls, texts):
        df = Counter()
        for text in texts:
            df.update(set(tokens(text[:TEXT_CAP])))
        n = len(texts)
        idf = {token: round(math.log((n + 1) / (count + 1)) + 1.0, 6) for token, count in sorted(df.items())}
        return cls(idf, round(math.log(n + 1) + 1.0, 6))

    @classmethod
    def load(cls, data):
        return cls(data["idf"], data["ceiling"])

    def state(self):
        return {"embedder": self.name, "dim": self.dim, "ceiling": self.ceiling, "idf": self.idf}

    def one(self, text):
        vector = np.zeros(self.dim, dtype=np.float32)
        for token, count in Counter(tokens(text[:TEXT_CAP])).items():
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            weight = (1.0 + math.log(count)) * self.idf.get(token, self.ceiling)
            vector[bucket] += weight * (1.0 if digest[4] & 1 else -1.0)
        return vector

    def passages(self, texts):
        return np.vstack([self.one(text) for text in texts])

    def query(self, text):
        return self.one(text)


class E5Embedder:
    """WaggleDance's declared multilingual-e5-small, from the local cache only (offline forced)."""

    name, dim = "multilingual-e5-small-local-v1", 384

    def __init__(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(E5_MODEL, device="cpu", local_files_only=True)

    @classmethod
    def fit(cls, texts):
        return cls()

    @classmethod
    def load(cls, data):
        return cls()

    def state(self):
        return {"embedder": self.name, "dim": self.dim, "model": E5_MODEL}

    def passages(self, texts):
        return self.model.encode(["passage: " + text[:2000] for text in texts], batch_size=32,
                                 normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)

    def query(self, text):
        return self.model.encode("query: " + text[:2000], normalize_embeddings=True, convert_to_numpy=True)


EMBEDDERS = {"e5": E5Embedder, "hash": HashEmbedder}


def load_embedder(index_dir):
    data = json.loads((index_dir / "embedder.json").read_text(encoding="utf-8"))
    kind = next(key for key, cls in EMBEDDERS.items() if cls.name == data["embedder"])
    return EMBEDDERS[kind].load(data)


def parse_utc(text):
    try:
        value = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value.astimezone(dt.timezone.utc) if value.tzinfo is not None else None


def guarded(record):
    try:
        ws.guard_object(record)
    except ws.WorkspaceError:
        return False
    return True


def event_records(capture, before):
    for event in capture.get("events") or []:
        if not isinstance(event, dict) or event.get("type") in SKIP_TYPES or not event.get("message"):
            continue
        when = parse_utc(event.get("ts_utc"))
        if before is not None and (when is None or when >= before):
            continue
        head = f"{event.get('type')} {event.get('status') or ''} {event.get('agent')} {event.get('task_id') or ''}"
        text = (head + ": " + str(event["message"]))[:TEXT_CAP]
        constraint = event.get("type") == "finding" or CONSTRAINT.search(str(event.get("status") or ""))
        yield {"doc_id": event_id(event), "text": text, "body": str(event["message"])[:TEXT_CAP],
               "intent": "constraint" if constraint else "chat",
               "meta": {"kind": "event", "ts_utc": event.get("ts_utc"), "agent": event.get("agent"),
                        "type": event.get("type"), "task_id": event.get("task_id")}}


def lesson_records(directory, before):
    for path in sorted(directory.glob("*.md")):
        if path.name == "MEMORY.md":
            continue
        stamp = re.search(r"(20\d{6})", path.stem)
        if before is not None:
            if stamp is None or dt.datetime.strptime(stamp.group(1), "%Y%m%d").replace(
                    tzinfo=dt.timezone.utc) >= before.replace(hour=0, minute=0, second=0, microsecond=0):
                continue
        data = path.read_bytes()
        text = data.decode("utf-8", "replace")[:TEXT_CAP]
        yield {"doc_id": f"lesson:{path.stem}@{hashlib.sha256(data).hexdigest()[:12]}",
               "text": text, "body": text, "intent": "lesson",
               "meta": {"kind": "lesson", "file": path.name, "date": stamp.group(1) if stamp else None}}


class HexMemoryError(ValueError):
    """An index that cannot be published as given: ``output_exists`` or ``output_unwritten`` (R5 write-new)."""


def _text_bytes(text):
    """The bytes the text-mode ``write_text(text, encoding="ascii")`` of before R5 wrote: each newline as os.linesep."""
    return text.replace("\n", os.linesep).encode("ascii")


def _stage_file(path, data):
    """One new file of the staged index: created exclusively, written, flushed, synced, closed and read back."""
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if path.read_bytes() != data:  # a short or altered write is never published
        raise OSError(errno.EIO, "read-back differs")


def _sync_file(path):
    """A file core.faiss_store wrote and closed without a sync: synced before the index is published."""
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _unique_keys(pairs):
    """``object_pairs_hook``: an object with a repeated key is refused, never resolved to its last value."""
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key")
    return dict(pairs)


def _parallel(values, count, kind):
    """A list of exactly ``count`` items of ``kind``: ``core.faiss_store`` search reads ``texts[i]`` and
    ``metadata[i]`` for every hit ``i``, so both arrays must stay parallel to the doc_ids."""
    return isinstance(values, list) and len(values) == count and all(isinstance(value, kind) for value in values)


def _check_cell(folder, ids, dim):
    """Root GPT review 2026-10-21: one FAISS cell as ``core.faiss_store`` saved it, read back before the index is
    published. ``index.faiss`` must load with exactly ``len(ids)`` vectors of ``dim`` and ``meta.json`` must parse
    (a repeated key refused) with exactly ``ids`` as its doc_ids and, parallel to them, a ``texts`` list of strings
    and a ``metadata`` list of objects (the review of 6c96dcad: right doc_ids with an emptied, dropped or mistyped
    array passed); anything else is an OSError like a JSON read-back that differs. Only these are checked: a file
    that raised nothing and differs only in its vector values or in the contents of texts and metadata is published
    (a stated limit)."""
    try:
        index = faiss.read_index(str(folder / "index.faiss"))
        meta = json.loads((folder / "meta.json").read_bytes(), object_pairs_hook=_unique_keys)
    except (OSError, RuntimeError, ValueError):
        raise OSError(errno.EIO, "FAISS read-back failed") from None
    if index.ntotal != len(ids) or index.d != dim or not isinstance(meta, dict) or meta.get("doc_ids") != ids \
            or not _parallel(meta.get("texts"), len(ids), str) or not _parallel(meta.get("metadata"), len(ids), dict):
        raise OSError(errno.EIO, "FAISS read-back differs")


def _discard(staging, cells):
    """After a failed build, best effort: remove only the names this build staged (its two JSON files, each cell's
    two FAISS files and directory, the faiss directory, then the staging directory). A removal that fails never
    changes the outcome, and a name this build did not stage keeps its directory."""
    folders = [staging / "faiss" / f"cell_{cell}" for cell in sorted(cells)]
    files = [folder / name for folder in folders for name in ("index.faiss", "meta.json")]
    for path in files + [staging / "embedder.json", staging / "hex-memory-receipt.json"]:
        try:
            path.unlink()
        except OSError:
            pass
    for path in folders + [staging / "faiss", staging]:
        try:
            path.rmdir()
        except OSError:
            pass


def build(args):
    """Build the index and publish it as the NEW directory ``args.out``, whole, or nothing at that path (R5
    write-new, the ``step2/corpus.publish_new`` shape). An existing ``args.out`` (a previous index, an empty
    directory, a file or a link) raises ``HexMemoryError("output_exists")`` before any input is read and is never
    replaced or written into (``core.faiss_store`` would load a previous cell and append to it). Every file is
    written in a fresh sibling staging directory ``.staging-<16 random hex>`` made exclusively, 25 characters
    whatever the output is called (Root GPT review 2026-10-21: a valid output name never makes an invalid staging
    name; only an output name under 25 characters has a longer staging path, a stated limit where long paths are
    off): the two JSON files created exclusively, flushed, synced and read back, each FAISS cell saved by
    ``core.faiss_store``, then synced and read back (``_check_cell``, the same review: a FAISS file that raised
    nothing is never published with another vector count, dim or doc_ids, or with a texts or metadata array not
    parallel to them; vector values and the contents of texts and metadata are not compared); only then is the
    staging directory renamed onto ``args.out``. That rename refuses an existing name on
    Windows and an existing file, link or non-empty directory on POSIX, and a failed rename while the name is taken is
    ``output_exists``; on POSIX it replaces an EMPTY directory made after the check (a stated limit). Any other OS
    failure while publishing is ``output_unwritten``; any other error is raised unchanged. After a failure the names
    this call staged are removed, best effort; a staging name already taken fails its create and is never used or
    removed, and a hard crash leaves the staging directory behind, never published and never touched by a later
    build. Directories are not synced; another process of the same user that changes the staging directory or a
    parent directory while this runs is not defended."""
    if os.path.lexists(args.out):
        raise HexMemoryError("output_exists")  # before any input is read: a previous index is never replaced
    started = time.perf_counter()
    before = parse_utc(args.before) if args.before else None
    capture_bytes = args.capture.read_bytes()
    capture = json.loads(capture_bytes)
    topology = HexCellTopology()
    records = list(event_records(capture, before))
    if args.lessons is not None:
        records += list(lesson_records(args.lessons, before))
    kept, refused, seen = [], Counter(), set()
    for record in records:
        if record["doc_id"] in seen:
            continue
        seen.add(record["doc_id"])
        stored = {"doc_id": record["doc_id"], "text": record["text"][:STORED_CAP], "meta": record["meta"]}
        if not guarded(stored):
            refused[record["meta"]["kind"]] += 1
            continue
        cell = CELL_LEARNING if record["intent"] == "lesson" else \
            topology.assign_cell(record["intent"], record["text"].lower()).cell_id
        kept.append((cell, record, stored["text"]))
    embedder = EMBEDDERS[args.embedder].fit([record["body"] for _, record, _ in kept])
    staging = args.out.parent / f".staging-{secrets.token_hex(8)}"  # never the output's name (docstring)
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        staging.mkdir()
    except OSError:
        raise HexMemoryError("output_unwritten") from None
    cells, reason = {}, "output_unwritten"
    try:
        _stage_file(staging / "embedder.json", _text_bytes(json.dumps(embedder.state(), ensure_ascii=True,
                                                                      sort_keys=True, separators=(",", ":"))))
        embed_started = time.perf_counter()
        vectors = embedder.passages([record["body"] for _, record, _ in kept])
        embed_ms = round((time.perf_counter() - embed_started) * 1000)
        registry = FaissRegistry(base_dir=str(staging / "faiss"), default_dim=embedder.dim)
        for (cell, record, text), vector in zip(kept, vectors):
            bucket = cells.setdefault(cell, ([], [], [], []))
            bucket[0].append(record["doc_id"])
            bucket[1].append(text)
            bucket[2].append(vector)
            bucket[3].append(dict(record["meta"], cell_id=cell))
        for cell, (ids, texts, cell_vectors, metas) in sorted(cells.items()):
            collection = registry.get_or_create(f"cell_{cell}")
            collection.add_batch(ids, texts, np.vstack(cell_vectors), metas)
            collection.save()
            for name in ("index.faiss", "meta.json"):
                _sync_file(staging / "faiss" / f"cell_{cell}" / name)
            _check_cell(staging / "faiss" / f"cell_{cell}", ids, embedder.dim)
        receipt = {"schema": SCHEMA, "authority": "none", "scope": "shadow_memory_not_gate",
                   "embedder": embedder.name, "dim": embedder.dim,
                   "capture_sha256": hashlib.sha256(capture_bytes).hexdigest(),
                   "before_utc": args.before, "records": len(kept),
                   "by_cell": {cell: len(v[0]) for cell, v in sorted(cells.items())},
                   "refused_by_privacy_guard": dict(refused), "embed_ms": embed_ms,
                   "build_ms": round((time.perf_counter() - started) * 1000)}
        ws.guard_object(receipt)
        _stage_file(staging / "hex-memory-receipt.json", _text_bytes(json.dumps(receipt, indent=1, sort_keys=True)))
        try:
            os.rename(staging, args.out)  # never replaces on Windows (POSIX: see the docstring)
        except OSError:
            if os.path.lexists(args.out):  # the name is taken: another writer's output, kept
                reason = "output_exists"
            raise
    except BaseException as error:
        _discard(staging, cells)
        if isinstance(error, OSError):
            raise HexMemoryError(reason) from None
        raise
    print(json.dumps(receipt))
    return 0


class Memory:
    """One loaded index: the registry, the topology and the embedder that built it."""

    def __init__(self, index_dir):
        self.index_dir = index_dir
        self.embedder = load_embedder(index_dir)
        self.registry = FaissRegistry(base_dir=str(index_dir / "faiss"), default_dim=self.embedder.dim)
        self.topology = HexCellTopology()

    def recall(self, text, k=8, route="merge", ring2=False, intent="chat", min_score=0.0, sufficient_score=0.9):
        started = time.perf_counter()
        if route == "wd_hybrid":
            hybrid = HybridRetrievalService(self.registry, self.topology, vector_store=None,
                                            embed_fn=self.embedder.query, enabled=True, ring2_enabled=ring2,
                                            mode="shadow", min_score=min_score, sufficient_score=sufficient_score)
            trace = asyncio.run(hybrid.retrieve(text, intent, k))
            hits = [(hit.score, hit.cell_id, hit.source_layer, hit.doc_id, hit.text, hit.metadata)
                    for hit in trace.hits]
            where = trace.route_source
        else:
            assignment = self.topology.assign_cell(intent, text.lower())
            order = [(assignment.cell_id, "local")] + [(cell, "ring1") for cell in assignment.neighbors_ring1]
            if ring2:
                order += [(cell, "ring2") for cell in assignment.neighbors_ring2]
            vector = self.embedder.query(text)
            hits, done = [], set()
            for cell, layer in order:
                collection = self.registry.get_existing(f"cell_{cell}")
                if collection is None:
                    continue
                for result in collection.search(np.asarray(vector), k):
                    if result.doc_id not in done:
                        done.add(result.doc_id)
                        hits.append((result.score, cell, layer, result.doc_id, result.text, result.metadata))
            hits.sort(key=lambda row: (-row[0], row[3]))
            hits = hits[:k]
            where = "+".join(cell for cell, _ in order)
        rows = [{"doc_id": doc_id, "cell": cell, "layer": layer, "score": round(float(score), 4), "meta": meta,
                 "text": body[:200]} for score, cell, layer, doc_id, body, meta in hits]
        return {"hits": rows, "route": where, "ms": round((time.perf_counter() - started) * 1000, 1)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("build")
    one.add_argument("--capture", type=Path, required=True)
    one.add_argument("--lessons", type=Path, default=None)
    one.add_argument("--out", type=Path, required=True)
    one.add_argument("--before", default=None, help="only records strictly before this UTC time (no future leak)")
    one.add_argument("--embedder", choices=sorted(EMBEDDERS), default="e5")
    two = sub.add_parser("query")
    two.add_argument("--index", type=Path, required=True)
    two.add_argument("--text", required=True)
    two.add_argument("--k", type=int, default=8)
    two.add_argument("--route", choices=("merge", "wd_hybrid"), default="merge")
    two.add_argument("--ring2", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "build":
        return build(args)
    result = Memory(args.index).recall(args.text, args.k, args.route, args.ring2)
    ws.guard_object(result)
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
