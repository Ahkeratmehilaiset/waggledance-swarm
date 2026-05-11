#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Deterministic EIG2 regression classifier.

This is a bridge/orchestrator reference shim. It is intentionally standalone:
no runtime imports, no network calls, and no dependency on the live bridge
schema beyond plain text or JSON fed through stdin.
"""

from __future__ import annotations

import json
import re
import sys
from enum import Enum
from typing import Any


class RegressionClass(str, Enum):
    INVARIANT_BREAK = "invariant_break"
    AUDIT_PROVENANCE_INVARIANT_FAILURE = "audit_provenance_invariant_failure"
    HOT_PATH_LLM_VIOLATION = "hot_path_llm_violation"
    SEMANTIC_REGRESSION = "semantic_regression"
    API_BREAK = "api_break"
    DETERMINISM_REGRESSION = "determinism_regression"
    LATENCY_PERFORMANCE_REGRESSION = "latency_performance_regression"
    RACE_CONCURRENCY_ISSUE = "race_concurrency_issue"
    FLAKY_TEST = "flaky_test"
    DEPENDENCY_ENV_ISSUE = "dependency_env_issue"
    TYPE_LINT_ISSUE = "type_lint_issue"
    LICENSE_COMPLIANCE_ISSUE = "license_compliance_issue"
    STORAGE_RESOURCE_ISSUE = "storage_resource_issue"
    UNKNOWN_ROOT_CAUSE_NEEDED = "unknown_root_cause_needed"


# Order matters: invariant, audit, and hot-path LLM classes are safety gates.
PATTERNS: tuple[tuple[RegressionClass, tuple[str, ...]], ...] = (
    (
        RegressionClass.INVARIANT_BREAK,
        (
            r"hard rule",
            r"invariant[_ -]?break",
            r"m0.*preflight.*missing",
            r"m0.*gate.*not.*closed",
            r"runtime.*before.*m0",
            r"m0.*scope.*leak",
            r"waggledance/core/.+\.py",
            r"adapter.*hook.*not.*inventory",
            r"hook.*not.*present.*m0[-_ ]reality[-_ ]check",
            r"unlisted.*hook",
            r"breaker.*before.*backpressure",
            r"write.*without.*backpressure",
            r"unbounded.*write",
            r"version.*collision",
            r"reuse.*release.*version",
            r"non[-_ ]monotonic.*version",
            r"ask_human",
            r"human prompt",
            r"manual approval",
            r"human review required",
            r"compact.*replac.*raw",
            r"mutat.*audit",
            r"reorder.*audit",
            r"remove.*\.eig2\.halt",
            r"forbidden.*phrase",
        ),
    ),
    (
        RegressionClass.HOT_PATH_LLM_VIOLATION,
        (
            r"hot[-_ ]path.*llm",
            r"provider.*called.*routing",
            r"cloud.*fallback.*hot",
        ),
    ),
    (
        RegressionClass.AUDIT_PROVENANCE_INVARIANT_FAILURE,
        (
            r"append[-_ ]only",
            r"provenance",
            r"hash mismatch",
            r"source_hash",
            r"event_hash",
            r"magma.*invariant",
        ),
    ),
    (
        RegressionClass.RACE_CONCURRENCY_ISSUE,
        (
            r"deadlock",
            r"race",
            r"lock",
            r"concurrent",
            r"stale heartbeat",
            r"wait-for",
        ),
    ),
    (
        RegressionClass.LATENCY_PERFORMANCE_REGRESSION,
        (
            r"p99",
            r"p99\.9",
            r"timeout",
            r"latency",
            r"benchmark.*regress",
            r"tail amplification",
        ),
    ),
    (
        RegressionClass.DETERMINISM_REGRESSION,
        (
            r"non[-_ ]determin",
            r"random seed",
            r"order differs",
            r"bit-for-bit",
        ),
    ),
    (
        RegressionClass.LICENSE_COMPLIANCE_ISSUE,
        (
            r"license",
            r"busl",
            r"apache",
            r"mit",
            r"commercial use",
            r"change date",
        ),
    ),
    (
        RegressionClass.STORAGE_RESOURCE_ISSUE,
        (
            r"disk full",
            r"no space left",
            r"oom",
            r"out of memory",
            r"memory pressure",
            r"storage",
        ),
    ),
    (
        RegressionClass.TYPE_LINT_ISSUE,
        (
            r"mypy",
            r"ruff",
            r"pyright",
            r"typing",
            r"lint",
            r"import error",
        ),
    ),
    (
        RegressionClass.DEPENDENCY_ENV_ISSUE,
        (
            r"module not found",
            r"no module named",
            r"dependency",
            r"environment",
            r"venv",
            r"pip",
        ),
    ),
    (
        RegressionClass.API_BREAK,
        (
            r"attributeerror",
            r"typeerror",
            r"unexpected keyword",
            r"missing required",
            r"schema",
            r"contract",
        ),
    ),
    (
        RegressionClass.SEMANTIC_REGRESSION,
        (
            r"semantic",
            r"behavior differs",
            r"route decision differs",
            r"expected route",
            r"actual route",
            r"baseline behavior",
        ),
    ),
    (
        RegressionClass.FLAKY_TEST,
        (
            r"flaky",
            r"intermittent",
            r"rerun passed",
        ),
    ),
)

_COMPILED_PATTERNS = tuple(
    (klass, tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns))
    for klass, patterns in PATTERNS
)


def _json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _coerce_text(payload: str | dict[str, Any] | list[Any]) -> str:
    if isinstance(payload, str):
        return payload
    return _json_text(payload)


def _has_property_invariant_failure(payload: str | dict[str, Any] | list[Any]) -> bool:
    if not isinstance(payload, dict):
        try:
            parsed = json.loads(payload) if isinstance(payload, str) else payload
        except json.JSONDecodeError:
            return False
    else:
        parsed = payload
    if not isinstance(parsed, dict):
        return False
    property_failed = bool(
        parsed.get("property_test_failed")
        or parsed.get("property_failed")
        or parsed.get("invariant_test_failed")
    )
    invariant_failed = bool(
        parsed.get("invariant_failed")
        or parsed.get("safety_invariant_failed")
        or parsed.get("hard_rule_failed")
    )
    return property_failed and invariant_failed


def classify(payload: str | dict[str, Any] | list[Any]) -> RegressionClass:
    """Classify a failure report into one deterministic regression class."""
    if _has_property_invariant_failure(payload):
        return RegressionClass.INVARIANT_BREAK

    text = _coerce_text(payload)
    for klass, patterns in _COMPILED_PATTERNS:
        if any(pattern.search(text) for pattern in patterns):
            return klass
    return RegressionClass.UNKNOWN_ROOT_CAUSE_NEEDED


def main() -> int:
    payload = sys.stdin.read()
    klass = classify(payload)
    print(
        json.dumps(
            {
                "classifier": "bridge_classify.py",
                "version": "eig2-v1.1",
                "classification": klass.value,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
