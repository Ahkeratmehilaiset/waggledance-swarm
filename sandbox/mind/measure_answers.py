"""Measure one lane's tournament answers on rendered views and write template-v2 result files; offline.

usage: python -B sandbox/mind/measure_answers.py --plan <plan.json> --views-root <views/round> --out-dir <results dir>

The plan (lane data, never in git) holds per variant the answers and the canonical judgment (``correct``,
``wrong_facts``, ``misses``, ``evidence_refs``, ``canonical_bytes``) and the reads that answer each question:
``{"file": <view file>, "how": "whole" | "grep" | "head" | "after", "pattern": <text>, "lines": <n>}``. This
script performs exactly those reads on the variant's view files, and ``bytes_read`` and ``seconds`` are what the
reads returned and took (machine read time; the reader's own thinking time is not in it). A read of a file the
views manifest does not name refuses. A missing judgment stays null. Each result names the round's capture,
context and builder digests from the views manifest and every view file read with its sha256 and size.
"""

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

RESULT_SCHEMA = "wd.bridge-mind-tournament-result.v2"
QUESTIONS = ("Q1", "Q2", "Q3", "Q4")
JUDGED = ("correct", "wrong_facts", "misses", "evidence_refs", "canonical_bytes")


def perform(path, read):
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    how = read["how"]
    if how == "whole":
        return text
    if how == "head":
        return "".join(lines[:read["lines"]])
    pattern = re.compile(read["pattern"], re.IGNORECASE)
    if how == "grep":
        return "".join(line for line in lines if pattern.search(line))
    if how == "after":  # the first matching line and the next ``lines`` lines
        for index, line in enumerate(lines):
            if pattern.search(line):
                return "".join(lines[index:index + 1 + read["lines"]])
        return ""
    raise SystemExit("unknown read kind")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--views-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    lane = plan["lane"]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for variant, spec in plan["variants"].items():
        # A variant rendered by its owner elsewhere names its own views directory in the plan.
        base = Path(spec["views_dir"]) if spec.get("views_dir") else args.views_root / variant
        manifest = json.loads((base / "views-manifest.json").read_text(encoding="ascii"))
        files = manifest["lanes"][lane]["files"]
        read_files, answers = {}, []
        for question in QUESTIONS:
            item = spec["answers"].get(question, {})
            output, seconds = b"", None
            if item.get("reads"):
                started = time.perf_counter()
                for read in item["reads"]:
                    if read["file"] not in files:
                        raise SystemExit("read of a file the views manifest does not name")
                    path = base / lane / read["file"]
                    data = path.read_bytes()
                    if hashlib.sha256(data).hexdigest() != files[read["file"]]["sha256"]:
                        raise SystemExit("view file digest mismatch")
                    read_files[read["file"]] = {"path": str(path), "sha256": files[read["file"]]["sha256"],
                                                "bytes": files[read["file"]]["bytes"]}
                    output += perform(path, read).encode("utf-8")
                seconds = round(time.perf_counter() - started, 4)
            answer = {"id": question, "answer": item.get("answer"),
                      "bytes_read": len(output) if item.get("reads") else None, "seconds": seconds}
            if question == "Q1":
                answer["pr"] = item.get("pr")
            answer.update({key: item.get(key) for key in JUDGED})
            answer["reads"] = item.get("reads")
            answers.append(answer)
        # A plan key may name a view set (V3-capture); variant_id is the scored variant (V0..V6).
        variant_id = spec.get("variant_id", variant)
        result = {"schema": RESULT_SCHEMA, "lane": lane, "variant": variant_id,
                  "round_capture_sha256": manifest["capture_sha256"],
                  "round_context_sha256": manifest["context_sha256"],
                  "round_builder_sha256": manifest["round_builder_sha256"],
                  "views_manifest_sha256": hashlib.sha256((base / "views-manifest.json").read_bytes()).hexdigest(),
                  "view_files": sorted(read_files.values(), key=lambda item: item["path"]),
                  "answers": answers, "notes": spec.get("notes")}
        target = args.out_dir / f"{variant_id}.json"
        target.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="ascii")
        print(json.dumps({"variant": variant_id, "bytes_read": {a["id"]: a["bytes_read"] for a in answers},
                          "correct": {a["id"]: a["correct"] for a in answers}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
