"""HEX MEMORY BAKE-OFF: the lanes' alternatives, frozen first, then measured on one shared test set.

usage: python -B sandbox/mind/hexmind/bakeoff.py --testset <testset.json> --capture <cycle>/capture.json
                 --lessons <dir> --out <dir> [--embedder e5|hash] [--axis-h]

Why (operator 2026-10-13 ~18:00Z): ask every lane, then test the proposed alternatives before choosing how to build.
Every variant stores its records in WaggleDance's own FAISS cells (``core.faiss_store.FaissRegistry``, one
``cell_<id>`` collection per cell); cells and neighbours come from WaggleDance's hex code: the 8 logical cells of
``HexCellTopology`` or the axial ``HexCoord`` mesh of ``waggledance.core.domain.hex_mesh``.

Variants (frozen definitions; any change is a new version, re-run and disclosed):

* ``F``   fable-5: raw bridge events + lesson notes, WD 8 cells, e5 over own + ring-1 cells, k each, merged by score.
* ``R1``  claude-rco-1: cards in the WD 8 cells (intent + keyword scan of the card), BM25 top 20 per cell then
          e5 rerank, own + ring-1 cells with k each, margin abstain.
* ``R2a`` claude-rco-2: cards in a 7-cell axial FAILURE-CLASS mesh (home = most keyword tags), BM25 top 20 then
          e5 rerank in the origin cell; ring-1 assist only when the origin top score is under the floor.
* ``R2o`` the same with the assist off.
* ``G``   Grok's first bet: the R2 mesh, BM25 top 20 then e5 rerank in the origin AND every ring-1 cell (k each,
          independent of the origin), merged by e5 score, margin abstain.
* ``U``   codex-lead-1: the R2 mesh, identifier-aware BM25 top 20 UNION e5 top 20 per cell, reciprocal rank
          fusion, origin + every ring-1 cell with k each, margin abstain.

Cards (frozen rule): every lesson note last written before the cutoff (file mtime, so no later edit leaks in), and
every bridge event before the cutoff whose type is ``finding`` or whose status is a blocking status. A card's text is
its key line plus the first 900 characters; no other distillation exists offline.

Margin: a variant answers only when its top score is above the 90th percentile of its own top scores for a frozen
background set (40 ordinary info messages before the cutoff, picked by event-ref order; a background query's own
event is not counted, and R2a computes its floor without the assist). At most 3 lines per answer.

Axis H (``--axis-h``): for every cell, corrupt that cell's FAISS file on disk, reload, detect it (WD's
``FaissCollection`` silently loads EMPTY on a corrupt file, so the count is checked against the build receipt),
quarantine it, re-run the cases (answered / hit / DEGRADED when the quarantined cell held a finding card), rebuild
only that cell from its records (timed, fresh embedding) and check the rebuilt files and every other cell's files
byte for byte.

Writes only under ``--out``. No network, no provider, no bridge event, no gate code. Every stored and printed
object passes the decoded privacy guard of the workspace.
"""

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
import hex_memory as hm  # noqa: E402
from core.faiss_store import FaissRegistry  # noqa: E402
from tools import bridge_mind_workspace as ws  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402
from waggledance.core.domain.hex_mesh import AXIAL_DIRECTIONS, HexCoord  # noqa: E402
from waggledance.core.hex_cell_topology import CELL_LEARNING, HexCellTopology  # noqa: E402

SCHEMA = "wd.bridge-mind-hex-bakeoff.v1"
LINES = 3
POOL = 20
BACKGROUND = 40
FLOOR_PERCENTILE = 90
BLOCKING = {"changes_requested", "confirmed_bug", "blocked", "veto"}
DEFAULT_BEFORE = "2026-10-13T18:00:00Z"
IDENT = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+")
WORDS = re.compile(r"[a-z0-9]+")

# RCO2's 7 failure classes (bridge 2026-10-13 ~18:06Z), centre first, then the ring in RCO2's order; keyword stems
# are the words of RCO2's own class definitions (input-validation: schema, nested extra keys, regex anchoring,
# coercion; encoding: raw vs decoded checks, unicode escapes, JSON escaping across shell versions; privacy: marker
# leaks; identity-binding: exact head, task==branch, author; failure-paths: failing cleanup, fail-closed on a normal
# startup state, narrowed retry sets; filesystem-atomicity: partial writes, atomic replace; test-strength: CI skips,
# negatives that do not pin a reason, required-set completeness). Nothing added from the scored cases.
FAILURE_CLASSES = [
    ("input_validation", ("schema", "nested", "extra", "key", "regex", "anchor", "coerc", "validat")),
    ("encoding", ("raw", "decod", "unicode", "escap", "json", "shell")),
    ("privacy", ("privacy", "private", "marker", "leak")),
    ("identity_binding", ("identity", "head", "exact", "task", "branch", "author", "bind")),
    ("failure_paths", ("fail", "cleanup", "startup", "retry", "narrow")),
    ("filesystem_atomicity", ("filesystem", "partial", "write", "atomic", "replace")),
    ("test_strength", ("test", "ci", "skip", "negative", "reason", "required", "complete")),
]
AXIAL = {FAILURE_CLASSES[0][0]: HexCoord(0, 0)}
AXIAL.update({name: HexCoord(*direction) for (name, _), direction in zip(FAILURE_CLASSES[1:], AXIAL_DIRECTIONS)})


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def lex_tokens(text):
    return hm.tokens(text) + ["id=" + ident.lower() for ident in IDENT.findall(text)]


def class_tags(text):
    words = WORDS.findall(text.lower())
    return Counter({name: sum(1 for w in words for stem in stems if (w == stem if len(stem) <= 2 else w.startswith(stem)))
                    for name, stems in FAILURE_CLASSES})


def axial_home(text, replica):
    tags = class_tags(text)
    order = [name for name, _ in FAILURE_CLASSES]
    ranked = sorted(order, key=lambda name: (-tags[name], order.index(name)))
    home = ranked[0] if tags[ranked[0]] > 0 else order[0]
    if not replica:
        return [home]
    second = [name for name in ranked if name != home and tags[name] > 0 and AXIAL[name].is_adjacent(AXIAL[home])]
    return [home] + second[:1]


def axial_ring(cell):
    return [name for name, _ in FAILURE_CLASSES if AXIAL[name].is_adjacent(AXIAL[cell])]


def parse_utc(text):
    return hm.parse_utc(text or DEFAULT_BEFORE)


def lessons_before(directory, before):
    out = []
    for path in sorted(directory.glob("*.md")):
        if path.name == "MEMORY.md":
            continue
        written = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
        if written >= before:
            continue
        data = path.read_bytes()
        out.append((path, data))
    return out


def lesson_card(path, data):
    text = data.decode("utf-8", "replace")
    description = re.search(r"(?m)^description:\s*(.+)$", text)
    body = text.split("---", 2)[-1].strip() if text.startswith("---") else text
    key = path.stem.replace("_", " ") + ". " + (description.group(1).strip() if description else "")
    return {"doc_id": f"lesson:{path.stem}@{sha(data)[:12]}", "kind": "lesson", "finding": False,
            "text": (key + "\n" + body)[:900 + len(key)], "meta": {"kind": "lesson", "file": path.name}}


def event_card(event):
    message = str(event.get("message") or "")
    prs = sorted(set(hm.PR.findall(message)))
    key = f"{event.get('type')} {event.get('status') or ''} {event.get('agent')} {event.get('task_id') or ''}" + \
        (" PR" + " PR".join(prs) if prs else "")
    return {"doc_id": event_id(event), "kind": "event", "finding": True, "text": key + ": " + message[:900],
            "meta": {"kind": "event", "ts_utc": event.get("ts_utc"), "agent": event.get("agent"),
                     "type": event.get("type"), "status": event.get("status"), "task_id": event.get("task_id")}}


def is_card_event(event):
    return isinstance(event, dict) and event.get("type") not in hm.SKIP_TYPES and bool(event.get("message")) and (
        event.get("type") == "finding" or str(event.get("status") or "").lower() in BLOCKING)


def events_before(capture, before):
    for event in capture.get("events") or []:
        when = hm.parse_utc(event.get("ts_utc")) if isinstance(event, dict) else None
        if when is not None and when < before:
            yield event


def card_corpus(capture, lessons, before):
    records = [event_card(e) for e in events_before(capture, before) if is_card_event(e)]
    return records + [lesson_card(path, data) for path, data in lessons]


def raw_corpus(capture, lessons, before):
    records = []
    for record in hm.event_records({"events": list(events_before(capture, before))}, None):
        finding = record["intent"] == "constraint"
        records.append({"doc_id": record["doc_id"], "kind": "event", "finding": finding, "text": record["text"],
                        "embed": record["body"], "meta": record["meta"]})
    for path, data in lessons:
        text = data.decode("utf-8", "replace")[:hm.TEXT_CAP]
        records.append({"doc_id": f"lesson:{path.stem}@{sha(data)[:12]}", "kind": "lesson", "finding": False,
                        "text": text, "meta": {"kind": "lesson", "file": path.name}})
    return records


def background_queries(capture, before):
    pool = [e for e in events_before(capture, before)
            if e.get("type") == "message" and e.get("status") == "info" and e.get("message")]
    pool.sort(key=event_id)
    return [(event_id(e), str(e["message"])[:300]) for e in pool[:BACKGROUND]]


class Embeddings:
    """One embedder, a content-addressed vector cache (same text, same vector) and a counter of fresh encodes."""

    def __init__(self, kind, corpus_texts):
        self.kind = kind
        self.embedder = hm.EMBEDDERS[kind].fit(corpus_texts)
        self.cache, self.fresh = {}, 0

    def passages(self, texts, fresh=False):
        if fresh:
            self.fresh += len(texts)
            return np.asarray(self.embedder.passages(list(texts)), dtype=np.float32)
        keys = [sha("p:" + text[:2000]) for text in texts]
        todo = sorted({k: t for k, t in zip(keys, texts) if k not in self.cache}.items())
        if todo:
            vectors = self.embedder.passages([t for _, t in todo])
            self.fresh += len(todo)
            for (k, _), v in zip(todo, vectors):
                self.cache[k] = np.asarray(v, dtype=np.float32)
        return np.vstack([self.cache[k] for k in keys]) if keys else np.zeros((0, self.embedder.dim), np.float32)

    def query(self, text):
        k = sha("q:" + text[:2000])
        if k not in self.cache:
            vector = np.asarray(self.embedder.query(text), dtype=np.float32)
            norm = float(np.linalg.norm(vector))
            self.cache[k] = vector / norm if norm else vector
        return self.cache[k]


class BM25:
    def __init__(self, texts, k1=1.2, b=0.75):
        self.docs = [Counter(lex_tokens(t)) for t in texts]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = (sum(self.lengths) / len(self.lengths)) if self.lengths else 1.0
        df = Counter(token for d in self.docs for token in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.k1, self.b = k1, b

    def top(self, text, k):
        query = set(lex_tokens(text))
        scores = []
        for i, (doc, length) in enumerate(zip(self.docs, self.lengths)):
            s = 0.0
            for t in query:
                f = doc.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / self.avg))
            if s > 0:
                scores.append((s, i))
        scores.sort(key=lambda row: (-row[0], row[1]))
        return scores[:k]


class Store:
    """One variant's cells: a WD FaissRegistry on disk plus an in-process BM25 per cell."""

    def __init__(self, root, layout, replica, records, emb):
        self.root, self.layout, self.replica, self.emb = root, layout, replica, emb
        self.topology = HexCellTopology()
        self.cells = {}
        for record in records:
            for cell in self.place(record):
                self.cells.setdefault(cell, []).append(record)
        for cell in self.cells:
            self.cells[cell].sort(key=lambda r: r["doc_id"])
        self.receipt = {cell: len(rows) for cell, rows in sorted(self.cells.items())}
        self.findings = {cell: any(r["finding"] for r in rows) for cell, rows in self.cells.items()}
        if root.exists():
            shutil.rmtree(root)
        for cell in sorted(self.cells):
            self.write_cell(cell)
        self.load()

    def place(self, record):
        if self.layout == "axial7":
            return axial_home(record["text"], self.replica)
        if self.layout == "wd8_raw" and record["kind"] == "lesson":
            return [CELL_LEARNING]
        intent = "constraint" if record["finding"] else "chat"
        return [self.topology.assign_cell(intent, record["text"].lower()).cell_id]

    def write_cell(self, cell, fresh=False):
        rows = self.cells[cell]
        registry = FaissRegistry(base_dir=str(self.root), default_dim=self.emb.embedder.dim)
        collection = registry.get_or_create(f"cell_{cell}")
        vectors = self.emb.passages([r.get("embed") or r["text"] for r in rows], fresh=fresh)
        collection.add_batch([r["doc_id"] for r in rows], [r["text"][:600] for r in rows], vectors,
                             [dict(r["meta"], finding=r["finding"]) for r in rows])
        collection.save()

    def load(self):
        registry = FaissRegistry(base_dir=str(self.root), default_dim=self.emb.embedder.dim)
        self.loaded, self.bm25, self.quarantined = {}, {}, set()
        for cell in sorted(self.cells):
            collection = registry.get_existing(f"cell_{cell}")
            if collection is None or collection.count != self.receipt[cell]:
                self.quarantined.add(cell)
                continue
            self.loaded[cell] = collection
            self.bm25[cell] = BM25(collection._texts)

    def files(self):
        return {str(p.relative_to(self.root)).replace("\\", "/"): sha(p.read_bytes())
                for p in sorted(self.root.rglob("*")) if p.is_file()}

    def origin_and_ring(self, text):
        if self.layout == "axial7":
            origin = axial_home(text, False)[0]
            return origin, axial_ring(origin)
        assignment = self.topology.assign_cell("chat", text.lower())
        return assignment.cell_id, list(assignment.neighbors_ring1)

    def vectors(self, cell):
        collection = self.loaded[cell]
        return collection._index.reconstruct_n(0, collection.count)

    # Every search returns (rank score, doc_id, cosine): the rank score orders lines, the cosine is what the margin
    # and the R2a assist trigger compare (for F, R1, R2 and G they are the same number; U ranks by fusion).

    def dense(self, cell, text, qv, k):
        return [(float(r.score), r.doc_id, float(r.score)) for r in self.loaded[cell].search(qv, k)]

    def rerank(self, cell, text, qv, k):
        pool = self.bm25[cell].top(text, POOL)
        if not pool:
            return []
        matrix = self.vectors(cell)[[i for _, i in pool]]
        ids = self.loaded[cell]._doc_ids
        scored = sorted(((float(np.dot(matrix[j], qv)), ids[i]) for j, (_, i) in enumerate(pool)),
                        key=lambda row: (-row[0], row[1]))
        return [(cos, doc, cos) for cos, doc in scored[:k]]

    def fused(self, cell, text, qv, k):
        ids = self.loaded[cell]._doc_ids
        vectors = self.vectors(cell)
        lexical = [i for _, i in self.bm25[cell].top(text, POOL)]
        dense = [ids.index(doc) for _, doc, _ in self.dense(cell, text, qv, POOL)]
        score = Counter()
        for ranking in (lexical, dense):
            for rank, i in enumerate(ranking):
                score[i] += 1.0 / (60 + rank + 1)
        ranked = sorted(((s, ids[i], i) for i, s in score.items()), key=lambda row: (-row[0], row[1]))[:k]
        return [(s, doc, float(np.dot(vectors[i], qv))) for s, doc, i in ranked]


def run_variant(name, store, text, qv, floor):
    """One query through one variant: up to 3 lines or an abstain, with the cells it read and DEGRADED."""
    started = time.perf_counter()
    origin, ring = store.origin_and_ring(text)
    method = {"F": "dense", "R1": "rerank", "R2a": "rerank", "R2o": "rerank", "G": "rerank", "U": "fused"}[name]
    search = getattr(store, method)

    def read(cells):
        return [(s, d, cos, cell) for cell in cells if cell in store.loaded for s, d, cos in search(cell, text, qv, LINES)]

    wanted = [origin] + ([] if name in ("R2a", "R2o") else ring)
    hits = read(wanted)
    if name == "R2a" and floor is not None and (not hits or max(h[2] for h in hits) < floor):
        wanted += ring
        hits += read(ring)
    best = {}
    for s, d, cos, cell in hits:
        if d not in best or s > best[d][0]:
            best[d] = (s, cos, cell)
    ranked = sorted(((s, d, cos, c) for d, (s, cos, c) in best.items()), key=lambda row: (-row[0], row[1]))[:LINES]
    top = max((row[2] for row in ranked), default=None)
    abstain = floor is not None and (top is None or top < floor)
    lines = [{"doc_id": d, "cell": c, "score": round(s, 4), "cos": round(cos, 4)} for s, d, cos, c in ranked]
    degraded = sorted(c for c in wanted if c in store.quarantined and store.findings.get(c))
    return {"lines": [] if abstain else lines, "raw_lines": lines, "top": None if top is None else round(top, 4),
            "abstain": abstain, "origin": origin, "cells_read": [c for c in wanted if c in store.loaded],
            "degraded": degraded, "ms": round((time.perf_counter() - started) * 1000, 2)}


def expected_ids(case):
    lessons = ["lesson:" + Path(item["file"]).stem + "@" for item in case.get("expect_lessons") or []]
    events = [item["ref"][:12] for item in case.get("expect_events") or [] if item.get("ref")]
    return lessons, events


def matches(doc_id, want):
    return doc_id.startswith(want) if want.startswith("lesson:") else doc_id[:12] == want


def score_case(case, result, corpus_ids, key="lines"):
    lessons, events = expected_ids(case)
    wanted = lessons + events
    got = [line["doc_id"] for line in result[key]]
    found = [w for w in wanted if any(matches(g, w) for g in got)]
    in_corpus = [w for w in wanted if any(matches(c, w) for c in corpus_ids)]
    return {"expected": len(wanted), "in_corpus": len(in_corpus), "found_at3": len(found),
            "case_hit": bool(found), "ceiling_hit": bool(in_corpus), "found": found}


VARIANTS = [("F", "raw", "wd8_raw", False), ("R1", "cards", "wd8", False), ("R2a", "cards", "axial7", False),
            ("R2o", "cards", "axial7", False), ("G", "cards", "axial7", False), ("U", "cards", "axial7", False)]
REPLICA = [("G", "cards", "axial7", True), ("U", "cards", "axial7", True)]


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(p / 100 * len(ordered)) - 1))]


def evaluate(args):
    testset = json.loads(args.testset.read_text(encoding="utf-8"))
    capture_bytes = args.capture.read_bytes()
    capture = json.loads(capture_bytes)
    cases = [dict(c, positive=True) for c in testset["cases"]] + \
            [dict(c, positive=False) for c in testset.get("negative") or []]
    by_cutoff = {}
    for case in cases:
        by_cutoff.setdefault(case.get("before") or DEFAULT_BEFORE, []).append(case)
    report = {"schema": SCHEMA, "authority": "none", "scope": "sandbox_measurement_not_gate",
              "testset_sha256": sha(args.testset.read_bytes()), "capture_sha256": sha(capture_bytes),
              "definition_sha256": sha(Path(__file__).read_bytes()), "embedder": args.embedder,
              "variants": {}, "cases": [], "axis_h": []}
    emb = None
    variants = VARIANTS + (REPLICA if args.axis_h else [])
    for cutoff, group in sorted(by_cutoff.items()):
        before = parse_utc(cutoff)
        lessons = lessons_before(args.lessons, before)
        corpora = {"raw": raw_corpus(capture, lessons, before), "cards": card_corpus(capture, lessons, before)}
        for kind, rows in corpora.items():
            kept = []
            for row in rows:
                if hm.guarded({"doc_id": row["doc_id"], "text": row["text"][:600], "meta": row["meta"]}):
                    kept.append(row)
                else:
                    report.setdefault("refused_by_privacy_guard", Counter())[kind] += 1
            corpora[kind] = kept
        if emb is None:
            emb = Embeddings(args.embedder, [r.get("embed") or r["text"] for r in corpora["raw"] + corpora["cards"]])
        background = background_queries(capture, before)
        for name, corpus, layout, replica in variants:
            label = name + ("+replica" if replica else "")
            root = args.out / "stores" / cutoff.replace(":", "") / label
            build_started = time.perf_counter()
            store = Store(root, layout, replica, corpora[corpus], emb)
            build_ms = round((time.perf_counter() - build_started) * 1000)
            corpus_ids = [r["doc_id"] for r in corpora[corpus]]
            floor = None
            if name != "F":
                tops = []
                for ref, text in background:
                    result = run_variant(name, store, text, emb.query(text), None)
                    others = [line["cos"] for line in result["raw_lines"] if line["doc_id"] != ref]
                    if others:
                        tops.append(max(others))
                floor = percentile(tops, FLOOR_PERCENTILE)
            summary = report["variants"].setdefault(label, {"cutoffs": {}})
            summary["cutoffs"][cutoff] = {"records": len(corpus_ids), "by_cell": store.receipt, "build_ms": build_ms,
                                          "floor": None if floor is None else round(floor, 4)}
            for case in group:
                text = case["situation"]
                result = run_variant(name, store, text, emb.query(text), floor)
                row = {"variant": label, "case": case["id"], "cutoff": cutoff, "positive": case["positive"],
                       "result": result}
                if case["positive"]:
                    row["score"] = score_case(case, result, corpus_ids)
                    row["score_no_margin"] = score_case(case, result, corpus_ids, "raw_lines")
                report["cases"].append(row)
            if args.axis_h and cutoff == DEFAULT_BEFORE and (name in ("F", "R1", "G", "U") or replica):
                report["axis_h"] += axis_h(label, name, store, group, floor, corpus_ids, emb)
    report["summary"] = summarize(report)
    report["fresh_encodes"] = emb.fresh if emb else 0
    report["refused_by_privacy_guard"] = dict(report.get("refused_by_privacy_guard") or {})
    ws.guard_object(report)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "bakeoff-report.json").write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(report["summary"], indent=1, sort_keys=True))
    return 0


def axis_h(label, name, store, group, floor, corpus_ids, emb):
    rows = []
    positives = [c for c in group if c.get("positive")]
    for cell in sorted(store.cells):
        before_files = store.files()
        before_vectors = store.vectors(cell).copy()
        target = store.root / f"cell_{cell}" / "index.faiss"
        target.write_bytes(b"corrupt")
        store.load()
        detected = cell in store.quarantined
        answered = hit = degraded = 0
        for case in positives:
            result = run_variant(name, store, case["situation"], emb.query(case["situation"]), floor)
            answered += bool(result["lines"])
            hit += score_case(case, result, corpus_ids)["case_hit"]
            degraded += bool(result["degraded"])
        started = time.perf_counter()
        shutil.rmtree(store.root / f"cell_{cell}")
        store.write_cell(cell, fresh=True)
        rebuild_ms = round((time.perf_counter() - started) * 1000)
        store.load()
        after_files = store.files()
        delta = float(np.max(np.abs(store.vectors(cell) - before_vectors))) if cell in store.loaded else None
        own = {k for k in before_files if k.startswith(f"cell_{cell}/")}
        rows.append({"variant": label, "cutoff": next(iter({c.get("before") or DEFAULT_BEFORE for c in group})),
                     "broken_cell": cell, "records": store.receipt[cell], "held_finding": store.findings[cell],
                     "corrupt_detected": detected, "positives": len(positives), "answered": answered,
                     "case_hits": hit, "degraded_flagged": degraded, "rebuild_ms": rebuild_ms,
                     "max_abs_vector_delta": delta,
                     "rebuilt_byte_identical": all(after_files.get(k) == before_files[k] for k in own),
                     "other_cells_unchanged": all(after_files.get(k) == v for k, v in before_files.items()
                                                  if k not in own),
                     "restored": not store.quarantined})
    return rows


def summarize(report):
    out = {}
    for label in report["variants"]:
        rows = [r for r in report["cases"] if r["variant"] == label]
        pos = [r for r in rows if r["positive"]]
        neg = [r for r in rows if not r["positive"]]
        ms = [r["result"]["ms"] for r in rows]
        out[label] = {
            "case_hits_at3": sum(r["score"]["case_hit"] for r in pos), "positives": len(pos),
            "ceiling_cases": sum(r["score"]["ceiling_hit"] for r in pos),
            "items_found_at3": sum(r["score"]["found_at3"] for r in pos),
            "items_expected": sum(r["score"]["expected"] for r in pos),
            "items_in_corpus": sum(r["score"]["in_corpus"] for r in pos),
            "positive_abstains": sum(r["result"]["abstain"] for r in pos),
            "negatives_abstained": sum(r["result"]["abstain"] for r in neg), "negatives": len(neg),
            "p50_ms": percentile(ms, 50), "p95_ms": percentile(ms, 95),
            "hit_cases": sorted(r["case"] for r in pos if r["score"]["case_hit"]),
            "no_margin_case_hits_at3": sum(r["score_no_margin"]["case_hit"] for r in pos),
            "no_margin_items_found_at3": sum(r["score_no_margin"]["found_at3"] for r in pos),
            "no_margin_hit_cases": sorted(r["case"] for r in pos if r["score_no_margin"]["case_hit"])}
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--testset", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--lessons", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--embedder", choices=sorted(hm.EMBEDDERS), default="e5")
    parser.add_argument("--axis-h", action="store_true")
    return evaluate(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
