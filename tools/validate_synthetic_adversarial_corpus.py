# SPDX-License-Identifier: BUSL-1.1
"""Validate the MAGMA synthetic adversarial corpus v0 fixture."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "v3_13_0" / "synthetic_adversarial_case.v0.json"
DEFAULT_CORPUS = ROOT / "tests" / "fixtures" / "magma_adversarial_corpus" / "v0.json"
REQUIRED_DEFECT_TYPES = {
    "charter_violation",
    "risk_escalation",
    "subtle_drift",
    "privilege_leak",
    "payload_leak",
    "correlated_review_trap",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a synthetic adversarial corpus v0 JSON file.",
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = validate_corpus(args.corpus)
    if args.json:
        print(json.dumps(report, sort_keys=True))
    elif report["ok"]:
        print(f"synthetic adversarial corpus OK: {report['case_count']} cases")
    else:
        print(
            f"synthetic adversarial corpus FAILED: {len(report['errors'])} errors",
            file=sys.stderr,
        )
        for error in report["errors"]:
            print(f"- {error}", file=sys.stderr)
    return 0 if report["ok"] else 1


def validate_corpus(corpus_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    corpus = _read_json(corpus_path, errors)
    cases = _cases(corpus, errors)
    validator = _validator()
    seen: set[str] = set()
    defect_types: set[str] = set()

    for index, case in enumerate(cases, 1):
        label = f"case {index}"
        if not isinstance(case, dict):
            errors.append(f"{label}: must be an object")
            continue
        for error in sorted(validator.iter_errors(case), key=lambda item: list(item.path)):
            path = ".".join(str(part) for part in error.path) or "<root>"
            errors.append(f"{label}: schema error at {path}: {error.message}")

        case_id = str(case.get("case_id", f"<missing:{index}>"))
        if case_id in seen:
            errors.append(f"{label}: duplicate case_id {case_id}")
        seen.add(case_id)
        defect_types.add(str(case.get("defect_type", "")))

        if not case.get("should_claude_catch") and not case.get("should_codex_catch"):
            errors.append(f"{label}: no expected catching agent for {case_id}")

        canary = case.get("privacy_canary")
        if isinstance(canary, str) and canary and canary in str(case.get("intent", "")):
            errors.append(f"{label}: privacy canary leaked into intent for {case_id}")

    missing = sorted(REQUIRED_DEFECT_TYPES - defect_types)
    if missing:
        errors.append("corpus: missing required defect types: " + ", ".join(missing))

    return {
        "ok": not errors,
        "corpus": str(corpus_path),
        "case_count": len(cases),
        "defect_types": sorted(defect_types),
        "errors": errors,
    }


def _validator() -> jsonschema.Draft7Validator:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def _read_json(path: Path, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        errors.append(f"cannot read corpus {path}: {exc}")
    except json.JSONDecodeError as exc:
        errors.append(f"invalid JSON in corpus {path}: {exc}")
    return None


def _cases(corpus: Any, errors: list[str]) -> list[Any]:
    if not isinstance(corpus, dict):
        errors.append("corpus: must be an object")
        return []
    if corpus.get("corpus_version") != "magma.synthetic_adversarial_corpus.v0":
        errors.append("corpus: corpus_version must be magma.synthetic_adversarial_corpus.v0")
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("corpus: cases must be a non-empty array")
        return []
    return cases


if __name__ == "__main__":
    raise SystemExit(main())
