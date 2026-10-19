"""HEX MEMORY STEP 2, calibrate: abstain statistics on the USED 10-13 Q4 sets only (smoke data, never the sealed sets).

usage: python -B sandbox/mind/step2/calibrate.py --corpus <dir> --vectors <dir> --set <testset.json> [--set ...]
           [--background <capture.json>] --out <file.json>

The 10-13 sets were used to pick and tune earlier variants, so nothing measured here is evidence; it only chooses a
rule to freeze BEFORE the fresh blinded sets are opened. Per case the corpus is cut at the case time: a record counts
only when its ``available_utc`` (lesson file mtime, event ``ts_utc``) is before it. For each case this writes the
rank and score of the best expected record and a set of candidate abstain statistics of the top hit:

* ``s1``, ``gap2``, ``gap5``, ``gap10``: top cosine and its lead over rank 2, 5 and 10;
* ``z``: the top cosine as a z-score over every applicable record (how far the best hit stands out for THIS query);
* ``hub``: the top cosine minus the record's own hubness (mean cosine to its 10 nearest corpus records), the CSLS
  correction for records that are close to everything;
* ``lex``: the IDF-weighted share of the query's rare tokens that also occur in the top record.

``--background`` adds unlabeled post-cutoff bridge events as situations; only the share of them each rule would
answer is reported, never a correctness number. No network, no bridge event, no gate code; writes only ``--out``, as a
NEW file whole or not at all (``write_new``; an existing one is refused before any input is read). A case with no
record available before it is an UNKNOWN row (D22): every statistic is None and ``unknown`` says why; no query is
encoded for it and no score is made up. So is a case whose before time is missing, malformed, timezone-less or out of
range (D23; no time is made up), and the encoder is built only at the first case with an applicable record (D23).
"""

import argparse
import datetime as dt
import errno
import json
import math
import os
import re
import secrets
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import corpus as corpus_mod  # noqa: E402
import encode as encode_mod  # noqa: E402
import memory as memory_mod  # noqa: E402

WORD = re.compile(r"[^\W_]{3,}", re.UNICODE)
HUB_K = 10
STATISTICS = ("s1", "gap2", "gap5", "gap10", "z", "hub", "lex")


class CalibrateError(ValueError):
    """A calibration output that cannot be published as given."""


def write_new(out, data):
    """D22 (RCO1's R5 class, write-new; the D17 ``join_card_delivery.write_new`` shape): publish ``data`` as the NEW
    file ``out``, whole, or nothing at that path. A sibling staging file is created exclusively, written, flushed,
    fsynced, closed and read back, then hard-linked to ``out``: a link never replaces, so a failed link while the name
    is taken is ``output_exists`` whatever errno the platform gives (D21.1), and any other OS failure is
    ``output_unwritten``. The staging name is removed after its exclusive create in every case, best effort; a file
    already at that name fails the create and is never used or removed. Directories are not synced."""
    out = Path(out)
    staging = out.parent / f".{out.name}.staging-{secrets.token_hex(8)}"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        handle = staging.open("xb")
    except OSError:
        raise CalibrateError("output_unwritten") from None
    reason = "output_unwritten"
    try:
        with handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if staging.read_bytes() != data:  # a short or altered write is never published
            raise OSError(errno.EIO, "read-back differs")
        try:
            os.link(staging, out)  # never replaces: an existing output refuses the link on every platform
        except OSError:
            if os.path.lexists(out):  # the name is taken: another writer's output, kept
                reason = "output_exists"
            raise
    except OSError:
        raise CalibrateError(reason) from None
    finally:
        try:
            staging.unlink()  # only the staging name: a published output keeps its bytes under its own name
        except OSError:
            pass


def words(text):
    return {word.lower() for word in WORD.findall(text)}


def idf_table(texts):
    df = Counter()
    for text in texts:
        df.update(words(text))
    n = len(texts)
    return {word: math.log((n + 1) / (count + 1)) + 1.0 for word, count in df.items()}, math.log(n + 1) + 1.0


def lexical_share(query_words, record_words, idf, ceiling, floor):
    rare = {word: idf.get(word, ceiling) for word in query_words if idf.get(word, ceiling) >= floor}
    total = sum(rare.values())
    if total == 0:
        return 0.0
    return sum(weight for word, weight in rare.items() if word in record_words) / total


def statistics(scores, order, hubness, query_words, record_words, idf, ceiling, floor):
    if len(order) == 0:  # D22: no applicable record: every statistic is UNKNOWN, never an IndexError or a made-up score
        return dict.fromkeys(STATISTICS)
    top = order[0]
    s = scores[order]

    def at(rank):
        return float(s[min(rank, len(s) - 1)])

    return {"s1": float(s[0]), "gap2": float(s[0]) - at(1), "gap5": float(s[0]) - at(4),
            "gap10": float(s[0]) - at(9), "z": float((s[0] - scores.mean()) / (scores.std() + 1e-9)),
            "hub": float(s[0] - hubness[top]),
            "lex": lexical_share(query_words, record_words[top], idf, ceiling, floor)}


def load_cases(paths):
    cases = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for case in data.get("cases") or []:
            lessons = [row["file"] for row in case.get("expect_lessons") or []]
            events = [row["ref"] for row in case.get("expect_events") or []]
            if not lessons and not events:
                continue
            cases.append({"id": case["id"], "set": path.name, "kind": "positive", "situation": case["situation"],
                          "before": case.get("before"), "lessons": lessons, "events": events})
        for case in data.get("negative") or []:
            cases.append({"id": case["id"], "set": path.name, "kind": "negative", "situation": case["situation"],
                          "before": case.get("before") or "2026-10-13T18:00:00Z", "lessons": [], "events": []})
    return cases


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--vectors", type=Path, required=True)
    parser.add_argument("--set", type=Path, action="append", required=True)
    parser.add_argument("--background", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lex-floor", type=float, default=4.0)
    args = parser.parse_args(argv)
    if os.path.lexists(args.out):
        raise CalibrateError("output_exists")  # before any input is read: a previous result is never replaced
    corpus_manifest, records = corpus_mod.read_corpus(args.corpus)
    _, views = encode_mod.read_vectors(args.vectors, corpus_manifest)
    vectors = views["w"]
    cutoff = corpus_manifest["cutoff_utc"]
    available = np.array([memory_mod.available_utc(record, cutoff) for record in records])
    similarity = vectors @ vectors.T
    np.fill_diagonal(similarity, -1.0)
    hubness = np.sort(similarity, axis=1)[:, -HUB_K:].mean(axis=1)
    del similarity
    record_words = [words(record["text"]) for record in records]
    idf, ceiling = idf_table([record["text"] for record in records])
    by_file = {record["provenance"]["file"]: i for i, record in enumerate(records) if record["kind"] == "lesson"}
    by_event = {record["provenance"]["compact_event_id"]: i for i, record in enumerate(records)
                if record["kind"] == "event"}
    cases = load_cases(args.set)
    if args.background is not None:
        capture = json.loads(args.background.read_bytes())
        for event in capture.get("events") or []:
            if event.get("type") in corpus_mod.SKIP_TYPES or not event.get("message"):
                continue
            if str(event.get("ts_utc")) < cutoff:
                continue
            cases.append({"id": "bg:" + str(event.get("ts_utc")), "set": "background", "kind": "background",
                          "situation": str(event["message"])[:2000], "before": event.get("ts_utc"),
                          "lessons": [], "events": []})
    encoder = None  # D23: built at the first case with an applicable record, never for UNKNOWN rows alone
    rows = []
    for case in cases:
        row = {key: case[key] for key in ("id", "set", "kind")}
        try:
            parsed = corpus_mod.parse_utc(case["before"])
        except OverflowError:  # an offset that moves the time out of the datetime range
            parsed = None
        if parsed is None:  # D23: missing, malformed, timezone-less or out-of-range before time; no time made up
            row.update(dict.fromkeys(("applicable", *STATISTICS)), unknown="before_time_invalid", top3=[])
            if case["kind"] == "positive":
                row.update(dict.fromkeys(("expected_in_corpus", "missing_expected", "best_rank",
                                          "best_expected_score")))
            rows.append(row)
            continue
        mask = available < corpus_mod.utc_text(parsed)
        row["applicable"] = int(mask.sum())
        if row["applicable"]:
            if encoder is None:
                encoder = encode_mod.E5()
            scores = np.where(mask, vectors @ encoder.query(case["situation"]), -np.inf)
        else:  # D22: no record is available before the case: nothing to score, so no query is encoded
            scores = np.full(len(records), -np.inf)
            row["unknown"] = "no_applicable_records"
        order = np.argsort(-scores, kind="stable")[:row["applicable"]]
        live = scores[mask]
        stats = statistics(np.asarray(live), np.argsort(-live, kind="stable"), hubness[mask], words(case["situation"]),
                           [record_words[i] for i in np.flatnonzero(mask)], idf, ceiling, args.lex_floor)
        row.update(stats)
        expected = [by_file[name] for name in case["lessons"] if name in by_file]
        expected += [by_event[ref] for ref in case["events"] if ref in by_event]
        expected = [index for index in expected if mask[index]]
        if case["kind"] == "positive":
            ranks = {int(np.flatnonzero(order == index)[0]) + 1: index for index in expected}
            row["expected_in_corpus"] = len(expected)
            row["missing_expected"] = len(case["lessons"]) + len(case["events"]) - len(expected)
            row["best_rank"] = min(ranks) if ranks else None
            row["best_expected_score"] = float(scores[ranks[min(ranks)]]) if ranks else None
        row["top3"] = [records[i]["record_id"] for i in order[:3]]
        rows.append(row)
    result = {"schema": "wd.bridge-mind-hex-step2-calibration.v1", "authority": "none", "evidence": "smoke_only",
              "corpus_sha256": corpus_manifest["corpus_sha256"], "lex_floor": args.lex_floor, "hub_k": HUB_K,
              "sets": [str(path) for path in args.set], "rows": rows,
              "unknown_rows": sum(1 for row in rows if "unknown" in row)}
    corpus_mod.ws.guard_object(result)
    write_new(args.out, json.dumps(result, indent=1).encode("utf-8"))
    for row in rows:
        if row["kind"] == "background":
            continue
        if "unknown" in row:
            print(f"{row['kind'][:3]} {row['id']:<14} UNKNOWN {row['unknown']}")
            continue
        print(f"{row['kind'][:3]} {row['id']:<14} rank={str(row.get('best_rank')):<5} s1={row['s1']:.4f} "
              f"g2={row['gap2']:.4f} g5={row['gap5']:.4f} g10={row['gap10']:.4f} z={row['z']:.2f} "
              f"hub={row['hub']:.4f} lex={row['lex']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
