# SPDX-License-Identifier: BUSL-1.1
"""Validate the MAGMA synthetic adversarial corpus v0 fixtures."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import jsonschema
from waggledance.core.magma.adversarial_corpus_eval import (
    CRITICAL_DEFECT_TYPES,
    MIN_CRITICAL_DEFECT_CASES,
    REQUIRED_DEFECT_TYPES,
)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate synthetic adversarial corpus v0 JSON fixtures.",
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--expectations", type=Path, default=DEFAULT_EXPECTATIONS)
    parser.add_argument(
        "--folded-into-corpus",
        type=Path,
        default=None,
        help=(
            "Optional strict v0 corpus target used to verify that an expansion "
            "fixture marked folded_into_v0 is actually present in the baseline."
        ),
    )
    parser.add_argument(
        "--folded-into-expectations",
        type=Path,
        default=None,
        help=(
            "Optional strict v0 expectations target paired with "
            "--folded-into-corpus."
        ),
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = validate_corpus(
        args.corpus,
        args.expectations,
        folded_into_corpus_path=args.folded_into_corpus,
        folded_into_expectations_path=args.folded_into_expectations,
    )
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


def validate_corpus(
    corpus_path: Path,
    expectations_path: Path,
    *,
    folded_into_corpus_path: Path | None = None,
    folded_into_expectations_path: Path | None = None,
) -> dict[str, Any]:
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
    _validate_expansion_metadata(corpus, expectations_doc, errors)
    _validate_cross_refs(case_ids, expectation_ids, errors)
    fold_in = _validate_folded_into_target(
        corpus=corpus,
        expectations_doc=expectations_doc,
        cases=cases,
        expectations=expectations,
        folded_into_corpus_path=folded_into_corpus_path,
        folded_into_expectations_path=folded_into_expectations_path,
        errors=errors,
    )
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
        "expansion_summary": _expansion_summary(
            corpus=corpus,
            expectations_doc=expectations_doc,
            case_count=len(cases),
            expectation_count=len(expectations),
            coverage=coverage,
            fold_in=fold_in,
        ),
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
            errors.append(f"{label}: duplicate case_id {_case_id_label(case_id)}")
        seen_ids.add(case_id)

        defect_type = str(case.get("defect_type", ""))
        risk_class = str(case.get("risk_class", ""))
        coverage["defect_type"].add(defect_type)
        coverage["defect_type_counts"][defect_type] += 1
        coverage["risk_class"].add(risk_class)
        coverage["risk_class_counts"][risk_class] += 1

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
                errors.append(
                    f"{label}: privacy canary leaked into intent for "
                    f"{_case_id_label(case_id)}"
                )
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
            errors.append(
                f"{label}: duplicate expectation case_id {_case_id_label(case_id)}"
            )
        seen_ids.add(case_id)
        expected_gate = str(expectation.get("expected_gate", ""))
        expected_verdict = str(expectation.get("expected_verdict", ""))
        coverage["expected_gate"].add(expected_gate)
        coverage["expected_gate_counts"][expected_gate] += 1
        coverage["expected_verdict"].add(expected_verdict)
        coverage["expected_verdict_counts"][expected_verdict] += 1
        if not expectation.get("should_claude_catch") and not expectation.get("should_codex_catch"):
            errors.append(
                f"{label}: no expected catching agent for {_case_id_label(case_id)}"
            )
    return seen_ids


def _validate_expansion_metadata(
    corpus: Any,
    expectations_doc: Any,
    errors: list[str],
) -> None:
    if not isinstance(corpus, dict) or not isinstance(expectations_doc, dict):
        return
    corpus_label = _optional_str(corpus.get("expansion_label"))
    expectations_label = _optional_str(expectations_doc.get("expansion_label"))
    if corpus_label and expectations_label != corpus_label:
        errors.append("expectations: expansion_label must match corpus expansion_label")
    if expectations_label and not corpus_label:
        errors.append("expectations: expansion_label requires corpus expansion_label")
    if corpus_label and not _optional_str(corpus.get("expansion_status")):
        errors.append("corpus: expansion_status must be present for expansion corpus")


def _validate_folded_into_target(
    *,
    corpus: Any,
    expectations_doc: Any,
    cases: list[Any],
    expectations: list[Any],
    folded_into_corpus_path: Path | None,
    folded_into_expectations_path: Path | None,
    errors: list[str],
) -> dict[str, Any]:
    fold_errors: list[str] = []
    if folded_into_corpus_path is None and folded_into_expectations_path is None:
        return {
            "status": "not_requested",
            "missing_case_count": 0,
            "missing_expectation_count": 0,
            "error_count": 0,
        }
    if folded_into_corpus_path is None or folded_into_expectations_path is None:
        fold_errors.append(
            "expansion fold-in: folded corpus and expectations targets are both required"
        )
        errors.extend(fold_errors)
        return _fold_in_report("fail", [], [], fold_errors)
    if not isinstance(corpus, dict) or not isinstance(expectations_doc, dict):
        fold_errors.append("expansion fold-in: source corpus and expectations must be objects")
        errors.extend(fold_errors)
        return _fold_in_report("fail", [], [], fold_errors)

    expansion_label = _optional_str(corpus.get("expansion_label"))
    expansion_status = _optional_str(corpus.get("expansion_status"))
    if not expansion_label:
        fold_errors.append("expansion fold-in: check requires an expansion corpus")
    if expansion_status != "folded_into_v0":
        fold_errors.append(
            "expansion fold-in: expansion_status must be folded_into_v0 for target check"
        )

    target_corpus = _read_json(folded_into_corpus_path, fold_errors, "folded_into_corpus")
    target_expectations = _read_json(
        folded_into_expectations_path,
        fold_errors,
        "folded_into_expectations",
    )
    source_case_ids = _ids_from_items(cases, "case_id")
    source_expectation_ids = _ids_from_items(expectations, "case_id")
    target_case_ids = _case_ids_from_doc(target_corpus, "folded_into_corpus", fold_errors)
    target_expectation_ids = _expectation_ids_from_doc(
        target_expectations,
        "folded_into_expectations",
        fold_errors,
    )
    missing_cases = sorted(source_case_ids - target_case_ids)
    missing_expectations = sorted(source_expectation_ids - target_expectation_ids)
    if missing_cases:
        fold_errors.append(
            "expansion fold-in: target corpus missing case_id values: "
            + _case_id_labels(missing_cases)
        )
    if missing_expectations:
        fold_errors.append(
            "expansion fold-in: target expectations missing case_id values: "
            + _case_id_labels(missing_expectations)
        )

    errors.extend(fold_errors)
    status = "pass" if not fold_errors else "fail"
    return _fold_in_report(status, missing_cases, missing_expectations, fold_errors)


def _validate_cross_refs(
    case_ids: set[str],
    expectation_ids: set[str],
    errors: list[str],
) -> None:
    missing_expectations = sorted(case_ids - expectation_ids)
    dangling_expectations = sorted(expectation_ids - case_ids)
    if missing_expectations:
        errors.append(
            "corpus: missing expectations for: "
            + _case_id_labels(missing_expectations)
        )
    if dangling_expectations:
        errors.append(
            "expectations: dangling case_id values: "
            + _case_id_labels(dangling_expectations)
        )


def _ids_from_items(items: list[Any], key: str) -> set[str]:
    return {
        item[key]
        for item in items
        if isinstance(item, dict) and isinstance(item.get(key), str)
    }


def _case_ids_from_doc(doc: Any, label: str, errors: list[str]) -> set[str]:
    if not isinstance(doc, dict):
        errors.append(f"{label}: must be an object")
        return set()
    return _ids_from_array(doc.get("cases"), f"{label}: cases", errors)


def _expectation_ids_from_doc(doc: Any, label: str, errors: list[str]) -> set[str]:
    if not isinstance(doc, dict):
        errors.append(f"{label}: must be an object")
        return set()
    return _ids_from_array(doc.get("expectations"), f"{label}: expectations", errors)


def _ids_from_array(value: Any, label: str, errors: list[str]) -> set[str]:
    if not isinstance(value, list) or not value:
        errors.append(f"{label} must be a non-empty array")
        return set()
    ids: set[str] = set()
    duplicates: set[str] = set()
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict):
            errors.append(f"{label}[{index}] must be an object")
            continue
        case_id = item.get("case_id")
        if not isinstance(case_id, str):
            errors.append(f"{label}[{index}] must include a case_id string")
            continue
        if case_id in ids:
            duplicates.add(case_id)
        ids.add(case_id)
    if duplicates:
        errors.append(
            f"{label} duplicate case_id values: " + _case_id_labels(duplicates)
        )
    return ids


def _fold_in_report(
    status: str,
    missing_cases: Sequence[str],
    missing_expectations: Sequence[str],
    fold_errors: Sequence[str],
) -> dict[str, Any]:
    return {
        "status": status,
        "missing_case_count": len(missing_cases),
        "missing_expectation_count": len(missing_expectations),
        "error_count": len(fold_errors),
    }


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
            + _case_id_labels(duplicate_seen)
        )

    unknown = sorted(held_out - case_ids)
    if unknown:
        errors.append(
            "corpus: split.held_out_case_ids contains unknown case_id values: "
            + _case_id_labels(unknown)
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


def _case_id_label(case_id: str) -> str:
    digest = hashlib.sha256(
        case_id.encode("utf-8", errors="surrogatepass")
    ).hexdigest()
    return f"case_id_digest:{digest[:12]}"


def _case_id_labels(case_ids: Sequence[str]) -> str:
    return ", ".join(_case_id_label(case_id) for case_id in sorted(case_ids))


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
        "risk_class_counts": Counter(),
        "expected_gate": set(),
        "expected_gate_counts": Counter(),
        "expected_verdict": set(),
        "expected_verdict_counts": Counter(),
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
        "defect_type_counts": _sorted_counter(coverage["defect_type_counts"]),
        "risk_class": sorted(coverage["risk_class"]),
        "risk_class_counts": _sorted_counter(coverage["risk_class_counts"]),
        "expected_gate": sorted(coverage["expected_gate"]),
        "expected_gate_counts": _sorted_counter(coverage["expected_gate_counts"]),
        "expected_verdict": sorted(coverage["expected_verdict"]),
        "expected_verdict_counts": _sorted_counter(coverage["expected_verdict_counts"]),
        "privacy_canary_count": coverage["privacy_canary_count"],
        "peer_review_trap_count": coverage["peer_review_trap_count"],
        "held_out_case_count": len(coverage["held_out_case_ids"]),
        "min_critical_defect_cases": MIN_CRITICAL_DEFECT_CASES,
    }


def _expansion_summary(
    *,
    corpus: Any,
    expectations_doc: Any,
    case_count: int,
    expectation_count: int,
    coverage: dict[str, Any],
    fold_in: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(corpus, dict):
        return {
            "is_expansion": False,
            "label": "",
            "status": "",
            "case_count": 0,
            "expectation_count": 0,
            "fold_in": fold_in,
        }
    label = _optional_str(corpus.get("expansion_label"))
    status = _optional_str(corpus.get("expansion_status"))
    expectations_label = ""
    if isinstance(expectations_doc, dict):
        expectations_label = _optional_str(expectations_doc.get("expansion_label"))
    return {
        "is_expansion": bool(label),
        "label": label,
        "expectations_label": expectations_label,
        "status": status,
        "folded_into_v0_claim": status == "folded_into_v0",
        "case_count": case_count if label else 0,
        "expectation_count": expectation_count if label else 0,
        "defect_type_counts": _sorted_counter(coverage["defect_type_counts"]) if label else {},
        "risk_class_counts": _sorted_counter(coverage["risk_class_counts"]) if label else {},
        "expected_gate_counts": _sorted_counter(coverage["expected_gate_counts"]) if label else {},
        "expected_verdict_counts": _sorted_counter(coverage["expected_verdict_counts"]) if label else {},
        "fold_in": fold_in,
    }


def _sorted_counter(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _optional_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


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
