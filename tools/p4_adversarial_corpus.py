#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Read-only WD P4 adversarial corpus validator.

The 72h P4 runtime-readiness sprint requires a concrete corpus of gate-bypass
families and a deterministic validator proving that each vector fails closed.
This tool is intentionally offline/read-only: it evaluates existing classifiers
and small local binding checks, emits a report, and never writes bridge events,
merges PRs, grants rollback authority, or activates runtime behavior.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.auto_rollback_eligibility import (  # noqa: E402
    OPERATOR_ESCALATE,
    decide_rollback,
)
from tools.bridge_event_taxonomy import classify as classify_bridge_events  # noqa: E402
from tools.bridge_event_taxonomy import normalize_raw  # noqa: E402
from tools.check_bridge_wake_delivery import check_wake_delivery  # noqa: E402
from tools.check_gate_signal_taskid_convention import (  # noqa: E402
    find_gate_signal_taskid_warnings,
)
from tools.check_standing_consensus_sign_class import (  # noqa: E402
    evaluate_standing_consensus_sign,
)
from tools.idle_consensus_auto_merge import verify_bridge_consensus  # noqa: E402


REPORT_VERSION = "wd.p4_adversarial_corpus.v0"
MIN_CASES = 32
REQUIRED_FAMILIES = (
    "novel_governance_doc",
    "vocabulary_name_list_drift",
    "active_on_merge",
    "stale_pass_head_drift",
    "author_slot_confusion",
    "wake_delivery_silence",
    "receipt_mismatch",
    "cause_b_ordering",
    "fake_digest_consistency",
    "observability_state_name_scans",
    "exact_token_shape_mismatch",
    "task_alias_consensus_splits",
)

HEAD = "1234567890abcdef1234567890abcdef12345678"
OLD_HEAD = "abcdef1234567890abcdef1234567890abcdef12"
BASE = "fedcba9876543210fedcba9876543210fedcba98"
TASK_ID = "codex-lead-1/p4-adversarial-corpus-fixture"
PR_NUMBER = 1437
NOW_UTC = "2026-06-30T12:30:00Z"

AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}

AUTHORITY_FALSE_FIELDS = (
    "active_on_merge",
    "bridge_append_allowed",
    "gate_bypass_allowed",
    "merge_allowed",
    "production_activation_ready",
    "rollback_execution_allowed",
    "runtime_mutation_authority",
    "scheduler_enqueue_allowed",
    "transport",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=ROOT / "tests" / "tools" / "p4_adversarial_corpus.json",
        help="Path to the WD P4 adversarial corpus JSON.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        corpus = load_corpus(args.corpus)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        report = {
            "report_version": REPORT_VERSION,
            "ok": False,
            "decision": "p4_adversarial_corpus_error",
            "errors": [str(exc)],
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"P4 adversarial corpus error: {exc}", file=sys.stderr)
        return 2

    report = evaluate_corpus(corpus)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(
            "P4 adversarial corpus PASS: "
            f"{report['blocked_count']}/{report['case_count']} cases failed closed."
        )
    else:
        print(
            "P4 adversarial corpus FAILED: "
            f"{', '.join(report['errors'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 3


def load_corpus(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("corpus root must be a JSON object")
    return payload


def evaluate_corpus(corpus: Mapping[str, Any]) -> dict[str, Any]:
    cases = corpus.get("cases")
    errors: list[str] = []
    if not isinstance(cases, list):
        errors.append("cases must be a list")
        cases = []

    results: list[dict[str, Any]] = []
    family_counts = {family: 0 for family in REQUIRED_FAMILIES}
    for index, raw_case in enumerate(cases, 1):
        if not isinstance(raw_case, Mapping):
            results.append(
                {
                    "id": f"invalid_case_{index}",
                    "family": "",
                    "driver": "",
                    "blocked": False,
                    "decision": "invalid_case",
                    "reasons": ["case must be an object"],
                }
            )
            continue
        family = _string(raw_case.get("family"))
        if family in family_counts:
            family_counts[family] += 1
        else:
            errors.append(f"{_case_id(raw_case, index)}: unknown family {family!r}")
        try:
            result = evaluate_case(raw_case, index=index)
        except Exception as exc:  # fail the corpus on classifier exceptions
            result = {
                "id": _case_id(raw_case, index),
                "family": family,
                "driver": _string(raw_case.get("driver")),
                "blocked": False,
                "decision": "case_evaluation_error",
                "reasons": [exc.__class__.__name__],
            }
        results.append(result)

    missing_families = [
        family for family, count in family_counts.items() if count <= 0
    ]
    if len(cases) < MIN_CASES:
        errors.append(f"case_count {len(cases)} below minimum {MIN_CASES}")
    if missing_families:
        errors.append("missing required families: " + ", ".join(missing_families))

    unblocked = [result["id"] for result in results if not result.get("blocked")]
    if unblocked:
        errors.append("cases did not fail closed: " + ", ".join(unblocked))

    blocked_count = sum(1 for result in results if result.get("blocked") is True)
    ok = not errors
    return {
        "report_version": REPORT_VERSION,
        "ok": ok,
        "decision": (
            "p4_adversarial_corpus_pass"
            if ok
            else "p4_adversarial_corpus_blocked"
        ),
        "case_count": len(cases),
        "blocked_count": blocked_count,
        "min_cases": MIN_CASES,
        "required_families": list(REQUIRED_FAMILIES),
        "family_counts": family_counts,
        "errors": errors,
        "case_results": results,
        "authority_boundary": _authority_boundary(),
    }


def evaluate_case(case: Mapping[str, Any], *, index: int = 0) -> dict[str, Any]:
    case_id = _case_id(case, index)
    family = _string(case.get("family"))
    driver = _string(case.get("driver"))
    payload = _mapping(case.get("payload"))
    if driver == "standing_sign":
        return _standing_sign_case(case_id, family, driver, payload)
    if driver == "bridge_consensus":
        return _bridge_consensus_case(case_id, family, driver, payload)
    if driver == "taxonomy":
        return _taxonomy_case(case_id, family, driver, payload)
    if driver == "wake_delivery":
        return _wake_delivery_case(case_id, family, driver, payload)
    if driver == "rollback":
        return _rollback_case(case_id, family, driver, payload)
    if driver == "receipt_binding":
        return _receipt_binding_case(case_id, family, driver, payload)
    if driver == "authority_boundary":
        return _authority_boundary_case(case_id, family, driver, payload)
    if driver == "gate_taskid":
        return _gate_taskid_case(case_id, family, driver, payload)
    return {
        "id": case_id,
        "family": family,
        "driver": driver,
        "blocked": False,
        "decision": "unknown_driver",
        "reasons": [f"unknown driver {driver!r}"],
    }


def _standing_sign_case(
    case_id: str,
    family: str,
    driver: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    changed_paths = _string_list(
        payload.get("changed_paths"),
        default=["tools/run_hex_runtime_readiness_proof.py"],
    )
    bridge_consensus = _deep_update(
        _full_standing_bridge_consensus(),
        _mapping(payload.get("bridge_consensus")),
    )
    report = evaluate_standing_consensus_sign(
        enabled=payload.get("enabled", True) is True,
        changed_paths=changed_paths,
        bridge_consensus=bridge_consensus,
        ci_all_green=payload.get("ci_all_green", True) is True,
        diff_gate_allowed=payload.get("diff_gate_allowed", True) is True,
        head_matches=payload.get("head_matches", True) is True,
        receipt_present=payload.get("receipt_present", True) is True,
    )
    blocked = report.get("admitted") is not True
    return _case_report(
        case_id,
        family,
        driver,
        blocked=blocked,
        decision="standing_sign_blocked" if blocked else "standing_sign_admitted",
        reasons=list(report.get("reasons") or []),
        classifier_report={
            "admitted": report.get("admitted"),
            "ab_class": report.get("ab_class"),
            "a_hits": report.get("a_hits", []),
            "unrecognized": report.get("unrecognized", []),
        },
    )


def _bridge_consensus_case(
    case_id: str,
    family: str,
    driver: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    task_id = _string(payload.get("task_id"), TASK_ID)
    head_sha = _string(payload.get("head_sha"), HEAD)
    author_agent = _string(payload.get("author_agent"), "fable-5")
    events = [
        _consensus_event(event, task_id=task_id, head_sha=head_sha)
        for event in _sequence(payload.get("events"))
    ]
    report = verify_bridge_consensus(
        events=events,
        task_id=task_id,
        head_sha=head_sha,
        pr_number=int(payload.get("pr_number", PR_NUMBER)),
        author_agent=author_agent,
        identity_registry=AGENT_UUIDS,
    )
    blocked = report.get("ok") is not True
    return _case_report(
        case_id,
        family,
        driver,
        blocked=blocked,
        decision=_string(report.get("decision"), "bridge_consensus_unknown"),
        reasons=list(report.get("reasons") or []),
        classifier_report={
            "ok": report.get("ok"),
            "identities": report.get("identities", {}),
            "blocking_rco_agents": report.get("blocking_rco_agents", []),
            "build_consensus_reemit_guidance": report.get(
                "build_consensus_reemit_guidance", []
            ),
        },
    )


def _taxonomy_case(
    case_id: str,
    family: str,
    driver: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    task_id = _string(payload.get("task_id"), TASK_ID)
    head_sha = _string(payload.get("head_sha"), HEAD)
    raw_events = list(_sequence(payload.get("events")))
    events = [dict(event) for event in raw_events if isinstance(event, Mapping)]
    if payload.get("normalize_raw") is True:
        events = [normalize_raw(event) for event in events]
    verdict = classify_bridge_events(
        events,
        task_id=task_id,
        head_sha=head_sha,
        recognized_rcos=("claude-rco-1", "claude-rco-2"),
    )
    report = verdict.as_dict()
    blocked = verdict.clear_to_merge is not True
    return _case_report(
        case_id,
        family,
        driver,
        blocked=blocked,
        decision=f"taxonomy_{verdict.decision}",
        reasons=[verdict.reason],
        classifier_report=report,
    )


def _wake_delivery_case(
    case_id: str,
    family: str,
    driver: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    now_utc = _parse_utc(_string(payload.get("now_utc"), NOW_UTC))
    events = [dict(event) for event in _sequence(payload.get("events"))]
    report = check_wake_delivery(
        events=events,
        min_age_minutes=float(payload.get("min_age_minutes", 12)),
        min_repeats=int(payload.get("min_repeats", 2)),
        max_age_hours=float(payload.get("max_age_hours", 12)),
        self_liveness_window_minutes=float(
            payload.get("self_liveness_window_minutes", 40)
        ),
        now_utc=now_utc,
    )
    blocked = report.get("decision") == "wake_delivery_stalled"
    return _case_report(
        case_id,
        family,
        driver,
        blocked=blocked,
        decision=_string(report.get("decision"), "wake_delivery_unknown"),
        reasons=[_string(report.get("delivery_escalation", {}).get("reason"))],
        classifier_report={
            "stalled_count": report.get("stalled_count"),
            "delivery_escalation": report.get("delivery_escalation", {}),
        },
    )


def _rollback_case(
    case_id: str,
    family: str,
    driver: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    decision_input = _deep_update(_rollback_fixture(), payload)
    report = decide_rollback(decision_input)
    blocked = report.get("decision") == OPERATOR_ESCALATE
    return _case_report(
        case_id,
        family,
        driver,
        blocked=blocked,
        decision=_string(report.get("decision"), "rollback_unknown"),
        reasons=[_string(report.get("reason"))],
        classifier_report={
            "eligible": report.get("eligible"),
            "receipt": report.get("receipt", {}),
        },
    )


def _receipt_binding_case(
    case_id: str,
    family: str,
    driver: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    report = _validate_receipt_binding(_deep_update(_receipt_fixture(), payload))
    blocked = report["ok"] is not True
    return _case_report(
        case_id,
        family,
        driver,
        blocked=blocked,
        decision=(
            "receipt_binding_blocked" if blocked else "receipt_binding_verified"
        ),
        reasons=list(report["blockers"]),
        classifier_report=report,
    )


def _authority_boundary_case(
    case_id: str,
    family: str,
    driver: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    boundary = _deep_update(_false_authority_fixture(), payload)
    blockers = [
        f"{field}_not_exact_false"
        for field in AUTHORITY_FALSE_FIELDS
        if boundary.get(field) is not False
    ]
    report = {"ok": not blockers, "blockers": blockers, "boundary": boundary}
    blocked = report["ok"] is not True
    return _case_report(
        case_id,
        family,
        driver,
        blocked=blocked,
        decision=(
            "authority_boundary_blocked"
            if blocked
            else "authority_boundary_verified"
        ),
        reasons=blockers,
        classifier_report=report,
    )


def _gate_taskid_case(
    case_id: str,
    family: str,
    driver: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    known_headrefs = _string_list(payload.get("known_headrefs"), default=[TASK_ID])
    warnings = find_gate_signal_taskid_warnings(
        _sequence(payload.get("events")),
        known_headrefs,
    )
    blocked = bool(warnings)
    return _case_report(
        case_id,
        family,
        driver,
        blocked=blocked,
        decision="gate_taskid_warned" if blocked else "gate_taskid_silent",
        reasons=[warning.get("reason", "") for warning in warnings],
        classifier_report={"warning_count": len(warnings), "warnings": warnings},
    )


def _case_report(
    case_id: str,
    family: str,
    driver: str,
    *,
    blocked: bool,
    decision: str,
    reasons: Sequence[str],
    classifier_report: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "id": case_id,
        "family": family,
        "driver": driver,
        "blocked": bool(blocked),
        "decision": decision,
        "reasons": [reason for reason in reasons if reason],
        "classifier_report": dict(classifier_report),
    }


def _validate_receipt_binding(payload: Mapping[str, Any]) -> dict[str, Any]:
    material = _mapping(payload.get("material"))
    receipt = _mapping(payload.get("receipt"))
    blockers: list[str] = []

    diff_text = _string(material.get("diff_text"))
    expected = {
        "head_sha": _string(material.get("head_sha")),
        "base_sha": _string(material.get("base_sha")),
        "task_id": _string(material.get("task_id")),
        "consensus_task_id": _string(material.get("consensus_task_id")),
        "changed_paths": _string_list(material.get("changed_paths")),
        "diff_digest": _sha256_digest(diff_text),
    }
    for key, expected_value in expected.items():
        actual = receipt.get(key)
        if actual != expected_value:
            blockers.append(f"{key}_mismatch")

    gate_decision = receipt.get("gate_decision")
    if gate_decision not in {"auto_merge_plan_ready", "operator_review_required"}:
        blockers.append("gate_decision_unknown")

    return {
        "ok": not blockers,
        "blockers": blockers,
        "expected": expected,
        "receipt_subset": {key: receipt.get(key) for key in expected},
    }


def _full_standing_bridge_consensus() -> dict[str, Any]:
    return {
        "ok": True,
        "identities": {
            "build_lead": {"agent": "codex-lead-1", "approved": True},
            "build_tools": {"agent": "codex-tools-1", "approved": True},
            "rco": {
                "by_agent": {
                    "claude-rco-1": {"approved": True},
                    "claude-rco-2": {"approved": True},
                }
            },
        },
        "blocking_rco_agents": [],
    }


def _consensus_event(
    event: Mapping[str, Any],
    *,
    task_id: str,
    head_sha: str,
) -> dict[str, Any]:
    agent = _string(event.get("agent"))
    out: dict[str, Any] = {
        "ts_utc": _string(event.get("ts_utc"), "2026-06-30T12:00:00Z"),
        "agent": agent,
        "type": _string(event.get("type"), "decision"),
        "status": _string(event.get("status"), "build_consensus_pass"),
        "task_id": _string(event.get("task_id"), task_id),
        "message": _string(event.get("message")),
        "payload": dict(_mapping(event.get("payload"))),
    }
    if "head" not in out["payload"] and event.get("head") is not False:
        out["payload"]["head"] = _string(event.get("head"), head_sha)
    if "pr" not in out["payload"] and event.get("pr") is not False:
        out["payload"]["pr"] = int(event.get("pr", PR_NUMBER))
    if agent in AGENT_UUIDS:
        out["agent_uuid"] = _string(event.get("agent_uuid"), AGENT_UUIDS[agent])
    return out


def _rollback_fixture() -> dict[str, Any]:
    return {
        "offending_sha": "1" * 40,
        "target_sha": "2" * 40,
        "result_tree": "tree-current",
        "target_tree": "tree-current",
        "known_green_registry": {
            "2" * 40: {"ci_green": True, "consensus_approved": True}
        },
        "failure_signal": {
            "source": "p4-canary",
            "confirmed": True,
            "required_confirmations": 2,
            "observed_confirmations": 2,
        },
        "target_paths": ["tools/run_hex_runtime_readiness_proof.py"],
    }


def _receipt_fixture() -> dict[str, Any]:
    diff_text = "+ runtime evidence only\n+ production_activation_ready = False\n"
    digest = _sha256_digest(diff_text)
    return {
        "material": {
            "head_sha": HEAD,
            "base_sha": BASE,
            "task_id": TASK_ID,
            "consensus_task_id": TASK_ID,
            "changed_paths": ["docs/runs/board.md"],
            "diff_text": diff_text,
        },
        "receipt": {
            "head_sha": HEAD,
            "base_sha": BASE,
            "task_id": TASK_ID,
            "consensus_task_id": TASK_ID,
            "changed_paths": ["docs/runs/board.md"],
            "diff_digest": digest,
            "gate_decision": "auto_merge_plan_ready",
        },
    }


def _false_authority_fixture() -> dict[str, Any]:
    return {field: False for field in AUTHORITY_FALSE_FIELDS} | {
        "runtime_ready_evidence_available": True,
    }


def _authority_boundary() -> dict[str, bool]:
    return {
        "read_only_report": True,
        "bridge_append_allowed": False,
        "queue_write_allowed": False,
        "github_write_allowed": False,
        "merge_allowed": False,
        "rollback_execution_allowed": False,
        "scheduler_enqueue_allowed": False,
        "runtime_activation_allowed": False,
        "runtime_mutation_authority": False,
        "transport": False,
    }


def _deep_update(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    out = deepcopy(dict(base))
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _deep_update(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def _sha256_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _case_id(case: Mapping[str, Any], index: int) -> str:
    return _string(case.get("id"), f"case_{index}")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return [item for item in value if isinstance(item, Mapping)]


def _string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _string_list(value: Any, *, default: Sequence[str] = ()) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return list(default)
    return [str(item) for item in value if isinstance(item, str) and item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
