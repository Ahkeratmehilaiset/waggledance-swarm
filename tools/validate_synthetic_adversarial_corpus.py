# SPDX-License-Identifier: BUSL-1.1
"""Validate the MAGMA synthetic adversarial corpus v0 fixtures."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import jsonschema
from waggledance.core.magma.adversarial_corpus_eval import REQUIRED_DEFECT_TYPES
from waggledance.core.magma.schema_validation import redacted_schema_errors

SCHEMA_DIR = ROOT / "schemas" / "v3_13_0"
CORPUS_SCHEMA = SCHEMA_DIR / "synthetic_adversarial_corpus.v0.json"
CASE_SCHEMA = SCHEMA_DIR / "synthetic_adversarial_case.v0.json"
EXPECTATION_SCHEMA = SCHEMA_DIR / "synthetic_adversarial_expectation.v0.json"
DEFAULT_DIR = ROOT / "tests" / "fixtures" / "magma_adversarial_corpus"
DEFAULT_CORPUS = DEFAULT_DIR / "v0.json"
DEFAULT_EXPECTATIONS = DEFAULT_DIR / "v0_expectations.json"
REQUIRED_RISK_CLASSES = {
    "informational",
    "internal_memory",
    "local_artifact",
    "external_effect",
}
REQUIRED_GATES = {"allow", "refuse", "review", "require_approval"}
REQUIRED_VERDICTS = {
    "pass",
    "fail",
    "review",
    "refuse",
    "insufficient_evidence",
    "abstain",
}
SPLIT_VERSION = "magma.synthetic_adversarial_split.v0"
MIN_HELD_OUT_CASES = 6
CRITICAL_DEFECT_TYPES = frozenset(
    {
        "fail-open",
        "governance_bypass",
        "hallucinated-success",
        "path_escape",
        "regression-process",
        "spec-gaming",
    }
)
MIN_CRITICAL_DEFECT_CASES = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate synthetic adversarial corpus v0 JSON fixtures.",
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--expectations", type=Path, default=DEFAULT_EXPECTATIONS)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = validate_corpus(args.corpus, args.expectations)
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


def validate_corpus(corpus_path: Path, expectations_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    corpus = _read_json(corpus_path, errors, "corpus")
    expectations_doc = _read_json(expectations_path, errors, "expectations")
    full_coverage_required = _full_coverage_required(corpus)
    corpus_validator = _validator(CORPUS_SCHEMA)
    cases = _cases(corpus, errors)
    expectations = _expectations(expectations_doc, errors)
    case_validator = _validator(CASE_SCHEMA)
    expectation_validator = _validator(EXPECTATION_SCHEMA)
    coverage = _empty_coverage()

    if isinstance(corpus, dict):
        _schema_errors(corpus_validator, corpus, "corpus", errors)
    case_ids = _validate_cases(cases, case_validator, coverage, errors)
    expectation_ids = _validate_expectations(
        expectations,
        expectation_validator,
        coverage,
        errors,
    )
    _validate_cross_refs(case_ids, expectation_ids, errors)
    if full_coverage_required:
        _validate_split(corpus, case_ids, coverage, errors)
        _validate_coverage(coverage, errors)

    return {
        "ok": not errors,
        "corpus": "<redacted>",
        "expectations": "<redacted>",
        "case_count": len(cases),
        "full_coverage_required": full_coverage_required,
        "split_required": full_coverage_required,
        "coverage": _coverage_report(coverage),
        "errors": errors,
    }


def _full_coverage_required(corpus: Any) -> bool:
    if not isinstance(corpus, dict):
        return True
    return not bool(corpus.get("expansion_label"))


def _validate_cases(
    cases: list[Any],
    validator: jsonschema.Draft7Validator,
    coverage: dict[str, Any],
    errors: list[str],
) -> set[str]:
    seen_ids: set[str] = set()
    seen_canaries: set[str] = set()
    for index, case in enumerate(cases, 1):
        label = f"case {index}"
        if not isinstance(case, dict):
            errors.append(f"{label}: must be an object")
            continue
        _schema_errors(validator, case, label, errors)
        case_id = str(case.get("case_id", f"<missing:{index}>"))
        if case_id in seen_ids:
            errors.append(f"{label}: duplicate case_id {case_id}")
        seen_ids.add(case_id)

        defect_type = str(case.get("defect_type", ""))
        risk_class = str(case.get("risk_class", ""))
        coverage["defect_type"].add(defect_type)
        coverage["defect_type_counts"][defect_type] += 1
        coverage["risk_class"].add(risk_class)

        trap = str(case.get("peer_review_trap_marker", "none"))
        if trap != "none":
            coverage["peer_review_trap_count"] += 1

        canary = case.get("privacy_canary")
        if isinstance(canary, str):
            if canary in seen_canaries:
                errors.append(f"{label}: duplicate privacy_canary")
            seen_canaries.add(canary)
            coverage["privacy_canary_count"] += 1
            if canary in str(case.get("intent", "")):
                errors.append(f"{label}: privacy canary leaked into intent for {case_id}")
    return seen_ids


def _validate_expectations(
    expectations: list[Any],
    validator: jsonschema.Draft7Validator,
    coverage: dict[str, Any],
    errors: list[str],
) -> set[str]:
    seen_ids: set[str] = set()
    for index, expectation in enumerate(expectations, 1):
        label = f"expectation {index}"
        if not isinstance(expectation, dict):
            errors.append(f"{label}: must be an object")
            continue
        _schema_errors(validator, expectation, label, errors)
        case_id = str(expectation.get("case_id", f"<missing:{index}>"))
        if case_id in seen_ids:
            errors.append(f"{label}: duplicate expectation case_id {case_id}")
        seen_ids.add(case_id)
        coverage["expected_gate"].add(str(expectation.get("expected_gate", "")))
        coverage["expected_verdict"].add(str(expectation.get("expected_verdict", "")))
        if not expectation.get("should_claude_catch") and not expectation.get("should_codex_catch"):
            errors.append(f"{label}: no expected catching agent for {case_id}")
    return seen_ids


def _validate_cross_refs(
    case_ids: set[str],
    expectation_ids: set[str],
    errors: list[str],
) -> None:
    missing_expectations = sorted(case_ids - expectation_ids)
    dangling_expectations = sorted(expectation_ids - case_ids)
    if missing_expectations:
        errors.append("corpus: missing expectations for: " + ", ".join(missing_expectations))
    if dangling_expectations:
        errors.append("expectations: dangling case_id values: " + ", ".join(dangling_expectations))


def _validate_split(
    corpus: Any,
    case_ids: set[str],
    coverage: dict[str, Any],
    errors: list[str],
) -> None:
    if not isinstance(corpus, dict):
        return
    split = corpus.get("split")
    if not isinstance(split, dict):
        errors.append("corpus: split must be present for full coverage corpus")
        return
    if split.get("split_version") != SPLIT_VERSION:
        errors.append(f"corpus: split_version must be {SPLIT_VERSION}")
    raw_held_out = split.get("held_out_case_ids")
    if not isinstance(raw_held_out, list) or not raw_held_out:
        errors.append("corpus: split.held_out_case_ids must be a non-empty array")
        return

    held_out: set[str] = set()
    duplicate_seen: set[str] = set()
    for index, raw_case_id in enumerate(raw_held_out, 1):
        if not isinstance(raw_case_id, str):
            errors.append(f"corpus: split.held_out_case_ids[{index}] must be a case_id string")
            continue
        if raw_case_id in held_out:
            duplicate_seen.add(raw_case_id)
        held_out.add(raw_case_id)

    if duplicate_seen:
        errors.append(
            "corpus: duplicate held_out_case_id values: "
            + ", ".join(sorted(duplicate_seen))
        )

    unknown = sorted(held_out - case_ids)
    if unknown:
        errors.append(
            "corpus: split.held_out_case_ids contains unknown case_id values: "
            + ", ".join(unknown)
        )

    known_held_out = held_out & case_ids
    coverage["held_out_case_ids"].update(known_held_out)
    if len(known_held_out) < MIN_HELD_OUT_CASES:
        errors.append(
            "coverage: held_out_case_ids must include at least "
            f"{MIN_HELD_OUT_CASES} known cases"
        )


def _validate_coverage(coverage: dict[str, Any], errors: list[str]) -> None:
    _require_coverage("defect_type", REQUIRED_DEFECT_TYPES, coverage, errors)
    _require_coverage("risk_class", REQUIRED_RISK_CLASSES, coverage, errors)
    _require_coverage("expected_gate", REQUIRED_GATES, coverage, errors)
    _require_coverage("expected_verdict", REQUIRED_VERDICTS, coverage, errors)
    _validate_critical_defect_floors(coverage, errors)
    if coverage["privacy_canary_count"] < 2:
        errors.append("coverage: privacy_canary_count must be at least 2")
    if coverage["peer_review_trap_count"] < 2:
        errors.append("coverage: peer_review_trap_count must be at least 2")


def _require_coverage(
    name: str,
    required: set[str],
    coverage: dict[str, Any],
    errors: list[str],
) -> None:
    missing = sorted(required - coverage[name])
    if missing:
        errors.append(f"coverage: missing {name}: " + ", ".join(missing))


def _validate_critical_defect_floors(
    coverage: dict[str, Any],
    errors: list[str],
) -> None:
    counts = coverage["defect_type_counts"]
    for defect_type in sorted(CRITICAL_DEFECT_TYPES):
        count = counts.get(defect_type, 0)
        if count < MIN_CRITICAL_DEFECT_CASES:
            errors.append(
                f"coverage: critical defect_type {defect_type} must include at least "
                f"{MIN_CRITICAL_DEFECT_CASES} cases (found {count})"
            )


def _empty_coverage() -> dict[str, Any]:
    return {
        "defect_type": set(),
        "defect_type_counts": Counter(),
        "risk_class": set(),
        "expected_gate": set(),
        "expected_verdict": set(),
        "privacy_canary_count": 0,
        "peer_review_trap_count": 0,
        "held_out_case_ids": set(),
    }


def _coverage_report(coverage: dict[str, Any]) -> dict[str, Any]:
    critical_counts = {
        defect_type: coverage["defect_type_counts"].get(defect_type, 0)
        for defect_type in sorted(CRITICAL_DEFECT_TYPES)
    }
    return {
        "defect_type": sorted(coverage["defect_type"]),
        "critical_defect_type_counts": critical_counts,
        "risk_class": sorted(coverage["risk_class"]),
        "expected_gate": sorted(coverage["expected_gate"]),
        "expected_verdict": sorted(coverage["expected_verdict"]),
        "privacy_canary_count": coverage["privacy_canary_count"],
        "peer_review_trap_count": coverage["peer_review_trap_count"],
        "held_out_case_count": len(coverage["held_out_case_ids"]),
        "min_critical_defect_cases": MIN_CRITICAL_DEFECT_CASES,
    }


def _validator(path: Path) -> jsonschema.Draft7Validator:
    schema = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def _schema_errors(
    validator: jsonschema.Draft7Validator,
    value: dict[str, Any],
    label: str,
    errors: list[str],
) -> None:
    errors.extend(redacted_schema_errors(validator, value, label))


def _read_json(path: Path, errors: list[str], label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        errors.append(f"{label}: cannot read JSON file ({exc.__class__.__name__})")
    except json.JSONDecodeError as exc:
        errors.append(f"{label}: invalid JSON at line {exc.lineno} column {exc.colno}")
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


def _expectations(expectations_doc: Any, errors: list[str]) -> list[Any]:
    if not isinstance(expectations_doc, dict):
        errors.append("expectations: must be an object")
        return []
    if expectations_doc.get("expectations_version") != "magma.synthetic_adversarial_expectations.v0":
        errors.append(
            "expectations: expectations_version must be "
            "magma.synthetic_adversarial_expectations.v0"
        )
    expectations = expectations_doc.get("expectations")
    if not isinstance(expectations, list) or not expectations:
        errors.append("expectations: expectations must be a non-empty array")
        return []
    return expectations


if __name__ == "__main__":
    raise SystemExit(main())
