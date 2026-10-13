"""HEX MEMORY BAKE-OFF v2: the round-1 winner's layout with the lanes' round-2 fixes, frozen before measuring.

usage: python -B sandbox/mind/hexmind/bakeoff_v2.py --calibration <testset.json> --test <testset.json> [...]
                 --capture <capture.json> --lessons <dir> --out <dir> [--embedder e5|hash]

Round 1 (``bakeoff.py`` b9156f94, results on the bridge 2026-10-13 18:57Z): full lesson text in WaggleDance's own
8 ``HexCellTopology`` cells, read over the origin and ring-1 cells, won the held-out set; no abstain worked. Round 2
measures the lanes' proposals from 18:57Z, unchanged layout and read path (WD 8 cells, lessons in ``learning``,
e5 dense over the origin and every ring-1 cell, merged by score):

* ``F1`` the round-1 F record (one record per lesson, text[:4000]) as the baseline;
* ``FC`` claude-rco-1: each lesson CHUNKED at blank-line blocks packed up to 1200 characters, every chunk prefixed
  with the lesson's key line and keeping the lesson ref (``lesson:<stem>@<sha12>#<n>``); lines are deduplicated by
  source ref, the best chunk speaks for its lesson.

Abstain rules (computed on the same ranked list; each variant reads 10 per cell):

* ``gap4`` claude-rco-2 (a): answer only if top-1 minus the mean of ranks 4-10 of THIS query is above tau;
* ``gap5`` claude-rco-1: the same with ranks 5-10;
* ``lex``  claude-rco-2 (b): answer only if the top line's stored text (its first 600 characters) shares at least one
  rare token with the situation: a word of 4+ letters or an identifier found in at most 1 percent of the records
  (RCO2 said "IDF above the median"; most of the vocabulary occurs once, so the median IDF is the maximum and
  that literal rule kept only tokens unique to one record, 2 of 160 background and 0 positives in the hash smoke run);
* ``gap5+lex`` both.

tau is chosen ONLY on the calibration split: positives whose expected item is in the corpus versus 40 calibration
background queries (ordinary info messages before the cutoff, the first 40 by event ref); tau maximises answered
positives minus answered background (ties: the larger tau). The test splits then report hits, answered positives and
answered background on the NEXT 40 info messages (a disjoint background set). A background query's own event never
counts. Background messages are proxy negatives: some may really have an earlier lesson.

Writes only under ``--out``. No network, no provider, no bridge event, no gate code; output passes the decoded
privacy guard of the workspace.
"""

import argparse
import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bakeoff as v1  # noqa: E402
from bakeoff import hm, ws  # noqa: E402

SCHEMA = "wd.bridge-mind-hex-bakeoff.v2"
DEPTH = 10
CHUNK = 1200
RARE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+|[a-z]{4,}")


def chunk_records(path, data):
    text = data.decode("utf-8", "replace")
    description = re.search(r"(?m)^description:\s*(.+)$", text)
    body = text.split("---", 2)[-1].strip() if text.startswith("---") else text
    key = path.stem.replace("_", " ") + ". " + (description.group(1).strip() if description else "")
    blocks, current = [], ""
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if not block:
            continue
        if current and len(current) + len(block) + 2 > CHUNK:
            blocks.append(current)
            current = ""
        while len(block) > CHUNK:
            blocks.append(block[:CHUNK])
            block = block[CHUNK:]
        current = (current + "\n\n" + block).strip()
    if current or not blocks:
        blocks.append(current)
    base = f"lesson:{path.stem}@{v1.sha(data)[:12]}"
    return [{"doc_id": f"{base}#{n}", "kind": "lesson", "finding": False, "text": key + "\n" + chunk,
             "meta": {"kind": "lesson", "file": path.name, "chunk": n}} for n, chunk in enumerate(blocks)]


def chunked_corpus(capture, lessons, before):
    records = [r for r in v1.raw_corpus(capture, [], before)]
    for path, data in lessons:
        records += chunk_records(path, data)
    return records


def base_ref(doc_id):
    return doc_id.split("#", 1)[0]


def rare_vocabulary(records):
    df = Counter()
    for record in records:
        df.update(set(RARE.findall(record["text"].lower())))
    n = len(records)
    idf = {t: math.log((n + 1) / (c + 1)) for t, c in df.items()}
    return idf, math.log((n + 1) / (max(1, int(0.01 * n)) + 1))


def read(store, text, qv, skip=None):
    """Origin + ring-1 cells, DEPTH each, merged by score, one line per source ref (best chunk)."""
    started = time.perf_counter()
    origin, ring = store.origin_and_ring(text)
    best = {}
    for cell in [origin] + ring:
        if cell not in store.loaded:
            continue
        for result in store.loaded[cell].search(qv, DEPTH):
            ref = base_ref(result.doc_id)
            if ref == skip:
                continue
            if ref not in best or result.score > best[ref][0]:
                best[ref] = (float(result.score), cell, result.text)
    ranked = sorted(((s, ref, cell, body) for ref, (s, cell, body) in best.items()), key=lambda r: (-r[0], r[1]))
    return ranked[:DEPTH], round((time.perf_counter() - started) * 1000, 2)


def features(ranked, situation, idf, median):
    scores = [row[0] for row in ranked]
    top = scores[0] if scores else 0.0

    def gap(first):
        tail = scores[first - 1:DEPTH]
        return top - (sum(tail) / len(tail)) if tail else 0.0

    shared = []
    if ranked:
        query = set(RARE.findall(situation.lower()))
        shared = sorted(t for t in query & set(RARE.findall(ranked[0][3].lower())) if idf.get(t, 0.0) >= median)
    return {"top": round(top, 4), "gap4": round(gap(4), 5), "gap5": round(gap(5), 5), "lex": bool(shared),
            "shared": shared[:5]}


def choose_tau(pos, neg):
    candidates = sorted(set(pos + neg))
    best = (-2.0, None)
    for i, value in enumerate(candidates):
        tau = value if i == 0 else (candidates[i - 1] + value) / 2
        score = sum(p >= tau for p in pos) / max(len(pos), 1) - sum(n >= tau for n in neg) / max(len(neg), 1)
        if score > best[0] or (score == best[0] and best[1] is not None and tau > best[1]):
            best = (score, tau)
    return best[1], round(best[0], 4)


def answered(rule, f, taus):
    gap_ok = {name: f[name] >= taus[name] for name in ("gap4", "gap5")}
    return {"none": True, "gap4": gap_ok["gap4"], "gap5": gap_ok["gap5"], "lex": f["lex"],
            "gap5+lex": gap_ok["gap5"] and f["lex"]}[rule]


RULES = ("none", "gap4", "gap5", "lex", "gap5+lex")
VARIANTS = {"F1": lambda capture, lessons, before: v1.raw_corpus(capture, lessons, before), "FC": chunked_corpus}


def evaluate(args):
    capture_bytes = args.capture.read_bytes()
    capture = json.loads(capture_bytes)
    splits = [("calibration", args.calibration)] + [(f"test{i + 1}", path) for i, path in enumerate(args.test)]
    report = {"schema": SCHEMA, "authority": "none", "scope": "sandbox_measurement_not_gate",
              "definition_sha256": v1.sha(Path(__file__).read_bytes()), "capture_sha256": v1.sha(capture_bytes),
              "embedder": args.embedder, "splits": {}, "taus": {}, "rows": []}
    emb = v1.Embeddings(args.embedder, [])
    for split, path in splits:
        testset = json.loads(path.read_text(encoding="utf-8"))
        report["splits"][split] = {"testset_sha256": v1.sha(path.read_bytes()), "file": path.name}
        groups = {}
        for case in testset["cases"]:
            groups.setdefault(case.get("before") or v1.DEFAULT_BEFORE, []).append(case)
        for cutoff, cases in sorted(groups.items()):
            before = v1.parse_utc(cutoff)
            lessons = v1.lessons_before(args.lessons, before)
            pool = v1.background_queries(capture, before) if split == "calibration" else []
            if split != "calibration":
                infos = [e for e in v1.events_before(capture, before)
                         if e.get("type") == "message" and e.get("status") == "info" and e.get("message")]
                infos.sort(key=v1.event_id)
                pool = [(v1.event_id(e), str(e["message"])[:300]) for e in infos[v1.BACKGROUND:2 * v1.BACKGROUND]]
            for name, build in VARIANTS.items():
                records = [r for r in build(capture, lessons, before)
                           if hm.guarded({"doc_id": r["doc_id"], "text": r["text"][:600], "meta": r["meta"]})]
                root = args.out / "stores" / split / cutoff.replace(":", "") / name
                store = v1.Store(root, "wd8_raw", False, records, emb)
                idf, median = rare_vocabulary(records)
                refs = sorted({base_ref(r["doc_id"]) for r in records})
                for case in cases:
                    ranked, ms = read(store, case["situation"], emb.query(case["situation"]))
                    f = features(ranked, case["situation"], idf, median)
                    lines = [{"doc_id": ref, "cell": cell, "score": round(s, 4)} for s, ref, cell, _ in ranked[:v1.LINES]]
                    score = v1.score_case(case, {"lines": lines}, refs)
                    report["rows"].append({"split": split, "variant": name, "case": case["id"], "kind": "positive",
                                           "features": f, "lines": lines, "ms": ms, "score": score})
                for ref, text in pool:
                    ranked, ms = read(store, text, emb.query(text), skip=ref)
                    report["rows"].append({"split": split, "variant": name, "case": "bg:" + ref[:12],
                                           "kind": "background", "features": features(ranked, text, idf, median),
                                           "ms": ms})
    for name in VARIANTS:
        cal = [r for r in report["rows"] if r["split"] == "calibration" and r["variant"] == name]
        pos = [r for r in cal if r["kind"] == "positive" and r["score"]["ceiling_hit"]]
        neg = [r for r in cal if r["kind"] == "background"]
        report["taus"][name] = {rule: choose_tau([r["features"][rule] for r in pos], [r["features"][rule] for r in neg])
                                for rule in ("gap4", "gap5")}
    report["summary"] = summarize(report)
    ws.guard_object(report)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "bakeoff-v2-report.json").write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps({"taus": report["taus"], "summary": report["summary"]}, indent=1, sort_keys=True))
    return 0


def summarize(report):
    out = {}
    for split in report["splits"]:
        for name in VARIANTS:
            taus = {rule: report["taus"][name][rule][0] for rule in ("gap4", "gap5")}
            rows = [r for r in report["rows"] if r["split"] == split and r["variant"] == name]
            pos = [r for r in rows if r["kind"] == "positive"]
            neg = [r for r in rows if r["kind"] == "background"]
            entry = {"positives": len(pos), "ceiling": sum(r["score"]["ceiling_hit"] for r in pos),
                     "background": len(neg), "p95_ms": v1.percentile([r["ms"] for r in rows], 95)}
            for rule in RULES:
                kept = [r for r in pos if answered(rule, r["features"], taus)]
                entry[rule] = {"hits": sum(r["score"]["case_hit"] for r in kept), "answered": len(kept),
                               "background_answered": sum(answered(rule, r["features"], taus) for r in neg),
                               "hit_cases": sorted(r["case"] for r in kept if r["score"]["case_hit"])}
            out[f"{split}/{name}"] = entry
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--test", type=Path, action="append", default=[])
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--lessons", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--embedder", choices=sorted(hm.EMBEDDERS), default="e5")
    return evaluate(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
