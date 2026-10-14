"""HEX MEMORY STEP 2, run: answer one case file with Q4, blind to everything but id, situation and time.

usage: python -B sandbox/mind/step2/run_q4.py --memory <dir> --cases <file> --out <dir> [--format fresh|testset]

Only the id, the situation text and the situation time of each case are read before every output is written:
* ``fresh``: ``cases[].{id, situation, situation_time_utc}`` (the step-2 blinded sets);
* ``testset``: the used 10-13 sets, ``cases[]`` and ``negative[]`` with ``{id, situation, before}``.
Expected refs, kinds and reasons are never read here; grading is a separate step by another lane.

Writes ``<out>/outputs.jsonl`` in the RCO2 grader v1.2 output contract, one object per case in file order: ``id``,
``abstain`` (JSON boolean), ``results`` (at most 3 objects, each with a string ``ref``: a lesson's file basename or an
event's ``ts_utc|agent|type|task_id``), ``degraded``/``fallback``/``crash`` (JSON booleans), ``status`` and, when
there is one, ``reason`` (strings). Everything else (view ranks, fused scores, timings, quarantined cells) goes to
``<out>/diagnostics.jsonl``. ``<out>/run-manifest.json`` holds the sha256 of the case file and of both outputs, the
memory sha256, the rule, and the git blob id of every step-2 source file, so a reader can check the run against the
frozen commit. No network, no bridge event, no gate code; writes only under ``--out``.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import corpus as corpus_mod  # noqa: E402
import encode as encode_mod  # noqa: E402
import memory as memory_mod  # noqa: E402
import q4 as q4_mod  # noqa: E402

SCHEMA = "wd.bridge-mind-hex-q4-run.v1"


def git_blob_id(path):
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


TIME_KEYS = ("situation_time_utc", "time_utc", "situation_time")
RESULT_KEYS = ("ref", "score", "doc_id", "kind", "cell", "time_utc", "tier", "fallback")


def blind_cases(data, kind):
    """(id, situation, time) per case, nothing else."""
    if kind == "fresh":
        return [(case["id"], case.get("situation"), next((case[key] for key in TIME_KEYS if key in case), None))
                for case in data["cases"]]
    rows = [(case["id"], case["situation"], case.get("before")) for case in data.get("cases") or []]
    rows += [(case["id"], case["situation"], case.get("before")) for case in data.get("negative") or []]
    return rows


def split(case_id, answer):
    """The grader-contract object and the diagnostics object of one answer."""
    contract = {"id": case_id, "status": answer["status"], "abstain": bool(answer["abstain"]),
                "degraded": bool(answer.get("degraded")), "fallback": bool(answer.get("degraded")),
                "crash": answer["status"] == "CRASH",
                "results": [{key: result[key] for key in RESULT_KEYS} for result in answer["results"][:3]]}
    if answer.get("reason"):
        contract["reason"] = answer["reason"]
    if answer["status"] == "CRASH":
        contract["error"] = answer["reason"]
    diagnostics = {"id": case_id, "rule": answer.get("rule"), "telemetry": answer.get("telemetry"),
                   "degraded_cells": answer.get("degraded_cells"),
                   "results": [{"doc_id": result["doc_id"], "fused": result["fused"],
                                "view_ranks": result["view_ranks"]} for result in answer["results"]]}
    return contract, diagnostics


def run(memory_dir, cases_path, out_dir, kind, default_time=None):
    raw = cases_path.read_bytes()
    cases = blind_cases(json.loads(raw), kind)
    if len({case_id for case_id, _, _ in cases}) != len(cases):
        raise SystemExit("case_ids_not_unique")
    started = time.perf_counter()
    loaded = memory_mod.Memory(memory_dir)
    encoder = encode_mod.E5()
    q4 = q4_mod.Q4(loaded, encoder)
    ready_s = round(time.perf_counter() - started, 2)
    lines, diagnostics = [], []
    for case_id, situation, when in cases:
        try:
            answer = q4.ask(situation, when or default_time)
        except Exception as error:  # a crash is recorded as a crash, never as ABSTAIN
            answer = {"status": "CRASH", "abstain": False, "reason": type(error).__name__, "degraded": False,
                      "results": []}
        contract, diagnostic = split(case_id, answer)
        lines.append(corpus_mod.canonical(contract))
        diagnostics.append(corpus_mod.canonical(diagnostic))
    body = ("\n".join(lines) + "\n").encode("utf-8")
    side = ("\n".join(diagnostics) + "\n").encode("utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "outputs.jsonl").write_bytes(body)
    (out_dir / "diagnostics.jsonl").write_bytes(side)
    manifest = {
        "schema": SCHEMA, "authority": "none", "scope": "shadow_memory_not_gate", "case_format": kind,
        "cases_path": str(cases_path), "cases_sha256": hashlib.sha256(raw).hexdigest(), "cases": len(cases),
        "memory_dir": str(memory_dir), "memory_sha256": memory_mod.memory_sha256(memory_dir),
        "cells": loaded.status(), "rule": q4.rule,
        "code_git_blobs": {path.name: git_blob_id(path) for path in sorted(HERE.glob("*.py"))},
        "outputs_sha256": hashlib.sha256(body).hexdigest(), "diagnostics_sha256": hashlib.sha256(side).hexdigest(),
        "ready_seconds": ready_s,
        "answer_ms": [(json.loads(line).get("telemetry") or {}).get("ms") for line in diagnostics],
    }
    corpus_mod.ws.guard_object(manifest)
    (out_dir / "run-manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--memory", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--format", choices=("fresh", "testset"), default="fresh")
    parser.add_argument("--default-time", default=None, help="testset negatives without a time (smoke only)")
    args = parser.parse_args(argv)
    if (args.out / "outputs.jsonl").exists():
        raise SystemExit("outputs_exist")
    manifest = run(args.memory, args.cases, args.out, args.format, args.default_time)
    print(json.dumps({key: manifest[key] for key in ("cases", "memory_sha256", "outputs_sha256", "ready_seconds")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
