# SPDX-License-Identifier: BUSL-1.1
"""Write an operator-review artifact for idle-protocol consensus.

The tool is deliberately manual and local. It never creates work-queue tasks,
branches, pull requests, or bridge events. It converts a completed soft/hard
idle convergence into evidence for an operator decision.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.idle_check import DEFAULT_EVENTS_PATH
from tools.verify_magma_receipt import verify_manifest
from waggledance.core.autonomy_growth.counterfactual_replay import (
    summarize_counterfactual_observability,
)
from waggledance.core.idle_consensus_charter import (
    evaluate_diff_content,
    evaluate_paths,
    load_charter,
)
from waggledance.core.idle_protocol import (
    detect_idle_convergence,
    validate_idle_proposal,
)
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.evaluation_result import build_evaluation_result
from waggledance.core.magma.receipt import build_magma_receipt
from waggledance.core.magma.receipt_bundle import (
    ReceiptBundleEntry,
    write_receipt_bundle,
)
from waggledance.core.work_queue import resolve_bridge_root

DEFAULT_OUT_DIR = Path("docs") / "architecture" / "consensus_artifacts"
REPLAY_SEED_VERSION = "idle_consensus_replay_seed.v0"
CANDIDATE_DIFF_REPLAY_ADMISSION_VERSION = (
    "idle_consensus_candidate_diff_replay_admission.v0"
)
COUNTERFACTUAL_EVAL_ADMISSION_SUMMARY_VERSION = (
    "idle_consensus_counterfactual_eval_admission_summary.v0"
)
COUNTERFACTUAL_EVAL_BINDING_TEMPLATE_VERSION = (
    "idle_consensus_counterfactual_eval_binding_template.v0"
)
OPERATOR_DECISION_REFERENCE_TEMPLATE_VERSION = (
    "idle_consensus_operator_decision_reference_template.v0"
)
OPERATOR_DECISION_REFERENCE_VERSION = (
    "idle_consensus_operator_decision_reference.v0"
)
OPERATOR_DECISION_AUTHENTICATION_VERSION = (
    "idle_consensus_operator_decision_authentication.v0"
)
OPERATOR_DECISION_AUTHENTICATION_METHOD = "operator_signed_decision"
OPERATOR_DECISION_APPROVED_FOR_DRAFT_PR = "approved_for_draft_pr_creation"
POLICY_VERSION = "policy:idle_consensus_artifact:v1"
CHARTER_VERSION = "charter:idle_autonomy:v1"
PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")
CANDIDATE_MATERIAL_KEYS = (
    "candidate_diff",
    "candidate_diff_text",
    "candidate_changed_paths",
    "changed_paths",
    "diff_text",
)
REPLAY_SEED_REQUIRED_FALSE_KEYS = (
    "candidate_diff_included",
    "external_effect",
    "writes_applied",
    "would_create_task",
    "would_create_branch",
    "would_create_pr",
    "would_merge",
)
IMPLEMENTATION_HINTS = (
    "create pr",
    "open pr",
    "git checkout",
    "git switch",
    "new branch",
    "implement next",
    "scaffold code",
    "work_queue",
)
COUNTERFACTUAL_EVAL_READY_STATES = frozenset(
    {"measured_local_partial", "runtime_measured"}
)
COUNTERFACTUAL_EVAL_BINDING_VERSION = (
    "idle_consensus_counterfactual_eval_binding.v0"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write an operator-review artifact for idle consensus.",
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help="Bridge events JSONL path. Defaults to <bridge-root>/shared/events.jsonl.",
    )
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help=(
            "Path to .agent-bridge directory (default: "
            "AGENT_BRIDGE_RUNTIME_ROOT/AGENT_BRIDGE_ROOT or repo-local)."
        ),
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--receipt-out-dir",
        type=Path,
        default=None,
        help="Optional non-existing output directory for a local MAGMA receipt bundle.",
    )
    parser.add_argument(
        "--candidate-diff-replay-admission",
        action="store_true",
        help=(
            "Emit a report-only admission check for a candidate diff against an "
            "idle replay seed. Writes no artifacts or bridge events."
        ),
    )
    parser.add_argument(
        "--counterfactual-eval-binding-template",
        action="store_true",
        help=(
            "Emit a digest-only replay_binding template for a future "
            "counterfactual eval receipt. Writes no artifacts or bridge events."
        ),
    )
    parser.add_argument(
        "--operator-decision-reference-template",
        action="store_true",
        help=(
            "Emit a digest-only operator decision reference template for the "
            "exact replay seed and candidate diff. Writes no artifacts or "
            "bridge events."
        ),
    )
    parser.add_argument(
        "--replay-seed",
        type=Path,
        default=None,
        help="Replay seed JSON, or an idle consensus artifact JSON containing replay_seed.",
    )
    parser.add_argument(
        "--candidate-diff",
        type=Path,
        default=None,
        help="Candidate diff text file for report-only replay admission.",
    )
    parser.add_argument(
        "--counterfactual-eval-receipt",
        type=Path,
        default=None,
        help=(
            "Optional counterfactual eval receipt JSON. The admission report "
            "exports only a digest and privacy-safe observability summary."
        ),
    )
    parser.add_argument(
        "--operator-decision-reference",
        type=Path,
        default=None,
        help=(
            "Optional operator decision reference JSON for candidate-diff "
            "replay admission. The report exports only digest-bound status."
        ),
    )
    parser.add_argument(
        "--changed-path",
        action="append",
        default=[],
        dest="changed_paths",
        help="Changed path for the candidate diff. Repeat for multiple paths.",
    )
    parser.add_argument("--now", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if (
        args.candidate_diff_replay_admission
        or args.counterfactual_eval_binding_template
        or args.operator_decision_reference_template
        or args.replay_seed is not None
        or args.candidate_diff is not None
        or args.counterfactual_eval_receipt is not None
        or args.operator_decision_reference is not None
        or args.changed_paths
    ):
        try:
            mode_count = sum(
                bool(mode)
                for mode in (
                    args.candidate_diff_replay_admission,
                    args.counterfactual_eval_binding_template,
                    args.operator_decision_reference_template,
                )
            )
            if mode_count > 1:
                raise ArtifactError(
                    "candidate diff replay modes are mutually exclusive",
                    {
                        "decision": "candidate_diff_replay_refused",
                        "errors": [
                            "--candidate-diff-replay-admission, "
                            "--counterfactual-eval-binding-template, and "
                            "--operator-decision-reference-template are mutually exclusive"
                        ],
                        "exit_code": 2,
                    },
                )
            if args.operator_decision_reference_template:
                if args.counterfactual_eval_receipt is not None:
                    raise ArtifactError(
                        "operator decision reference template does not consume receipts",
                        {
                            "decision": "operator_decision_reference_template_refused",
                            "errors": [
                                "--counterfactual-eval-receipt is not accepted "
                                "in operator-decision-reference-template mode"
                            ],
                            "exit_code": 2,
                        },
                    )
                if args.operator_decision_reference is not None:
                    raise ArtifactError(
                        "operator decision reference template emits operator decisions",
                        {
                            "decision": "operator_decision_reference_template_refused",
                            "errors": [
                                "--operator-decision-reference is not accepted "
                                "in operator-decision-reference-template mode"
                            ],
                            "exit_code": 2,
                        },
                    )
                report = build_operator_decision_reference_template_from_files(
                    enabled=bool(args.operator_decision_reference_template),
                    replay_seed_path=args.replay_seed,
                    candidate_diff_path=args.candidate_diff,
                    changed_paths=args.changed_paths,
                )
            if args.counterfactual_eval_binding_template:
                if args.operator_decision_reference is not None:
                    raise ArtifactError(
                        "counterfactual eval binding template does not consume operator decisions",
                        {
                            "decision": "counterfactual_eval_binding_template_refused",
                            "errors": [
                                "--operator-decision-reference is not accepted in "
                                "binding-template mode"
                            ],
                            "exit_code": 2,
                        },
                    )
                report = build_counterfactual_eval_binding_template_from_files(
                    enabled=bool(args.counterfactual_eval_binding_template),
                    replay_seed_path=args.replay_seed,
                    candidate_diff_path=args.candidate_diff,
                    changed_paths=args.changed_paths,
                    counterfactual_eval_receipt_path=args.counterfactual_eval_receipt,
                )
            elif not args.operator_decision_reference_template:
                report = build_candidate_diff_replay_admission_from_files(
                    enabled=bool(args.candidate_diff_replay_admission),
                    replay_seed_path=args.replay_seed,
                    candidate_diff_path=args.candidate_diff,
                    changed_paths=args.changed_paths,
                    counterfactual_eval_receipt_path=args.counterfactual_eval_receipt,
                    operator_decision_reference_path=(
                        args.operator_decision_reference
                    ),
                )
        except ArtifactError as exc:
            if args.json:
                print(json.dumps(exc.report, sort_keys=True))
            else:
                print(f"candidate diff replay admission FAILED: {exc}", file=sys.stderr)
                for error in exc.report.get("errors", []):
                    print(f"- {error}", file=sys.stderr)
            return int(exc.report.get("exit_code", 2))

        if args.json:
            print(json.dumps(report, sort_keys=True))
        else:
            print(report["decision"])
            print(f"ok: {str(report['ok']).lower()}")
            print(f"candidate_diff_digest: {report['candidate_diff']['digest']}")
        return int(report["exit_code"])

    try:
        events_path = args.events
        if events_path is None:
            events_path = resolve_bridge_root(args.bridge_root) / "shared" / "events.jsonl"
        report = write_idle_consensus_artifact(
            events_path=events_path,
            out_dir=args.out_dir,
            receipt_out_dir=args.receipt_out_dir,
            now_utc=_parse_utc(args.now) if args.now else datetime.now(timezone.utc),
        )
    except ArtifactError as exc:
        if args.json:
            print(json.dumps(exc.report, sort_keys=True))
        else:
            print(f"idle consensus artifact FAILED: {exc}", file=sys.stderr)
            for error in exc.report.get("errors", []):
                print(f"- {error}", file=sys.stderr)
        return int(exc.report.get("exit_code", 2))

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        print(f"json: {report['json_path']}")
        print(f"markdown: {report['markdown_path']}")
    return 0


def write_idle_consensus_artifact(
    *,
    events_path: Path,
    out_dir: Path,
    receipt_out_dir: Path | None = None,
    now_utc: datetime,
) -> dict[str, Any]:
    events = _read_events(events_path)
    payloads = _idle_payloads(events)
    if not payloads:
        raise ArtifactError(
            "no idle-protocol payloads found",
            {
                "decision": "no_consensus",
                "errors": ["no idle-protocol payloads found"],
                "exit_code": 3,
            },
        )
    _refuse_private_markers(payloads)

    for payload in payloads:
        ok, errors = validate_idle_proposal(payload)
        if not ok:
            raise ArtifactError(
                "idle transcript contains invalid payload",
                {
                    "decision": "invalid_payload",
                    "errors": errors,
                    "exit_code": 2,
                },
            )

    convergence = detect_idle_convergence(payloads)
    if convergence is None:
        raise ArtifactError(
            "no idle consensus to convert into operator artifact",
            {
                "decision": "no_consensus",
                "errors": ["soft or hard convergence has not been reached"],
                "exit_code": 3,
            },
        )
    status = str(convergence["status"])
    if status == "charter_violation":
        raise ArtifactError(
            "charter violation terminates the idle instance",
            {
                "decision": "charter_violation",
                "errors": ["terminated idle instances are not eligible for artifacts"],
                "convergence": convergence,
                "exit_code": 4,
            },
        )
    if status not in {"soft_convergence", "hard_convergence"}:
        raise ArtifactError(
            "idle convergence is not artifact eligible",
            {
                "decision": status,
                "errors": [f"unsupported convergence status: {status}"],
                "convergence": convergence,
                "exit_code": 4,
            },
        )

    artifact_id = _artifact_id(convergence, payloads)
    json_path = out_dir / f"{artifact_id}.json"
    markdown_path = out_dir / f"{artifact_id}.md"
    if json_path.exists() or markdown_path.exists():
        raise ArtifactError(
            "artifact output already exists",
            {
                "decision": "refuse_overwrite",
                "errors": [f"artifact already exists: {artifact_id}"],
                "exit_code": 5,
            },
        )
    if receipt_out_dir is not None and receipt_out_dir.exists():
        raise ArtifactError(
            "receipt output already exists",
            {
                "decision": "refuse_receipt_overwrite",
                "errors": [f"receipt output already exists: {receipt_out_dir}"],
                "exit_code": 5,
            },
        )

    artifact = {
        "artifact_version": "idle_consensus_operator_review.v1",
        "artifact_id": artifact_id,
        "created_at_utc": _iso(now_utc),
        "decision": "operator_review_required",
        "auto_execute": False,
        "operator_gate_required": True,
        "convergence": convergence,
        "transcript": payloads,
        "prohibited_actions": [
            "no_task_creation",
            "no_branch_creation",
            "no_pull_request_creation",
            "no_external_effect",
        ],
    }
    artifact["replay_seed"] = build_idle_consensus_replay_seed(artifact)
    markdown = _markdown(artifact)
    _assert_no_implementation_hints(markdown)
    receipt_bundle = None
    if receipt_out_dir is not None:
        try:
            receipt_bundle = _emit_receipt_bundle(
                artifact=artifact,
                out_dir=receipt_out_dir,
                now_utc=now_utc,
            )
        except ValueError as exc:
            raise ArtifactError(
                "idle consensus artifact receipt bundle failed",
                {
                    "decision": "invalid_receipt_bundle",
                    "errors": [str(exc)],
                    "exit_code": 2,
                },
            ) from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8"
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    report = {
        "decision": "operator_review_required",
        "artifact_id": artifact_id,
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "convergence_status": status,
        "auto_execute": False,
        "operator_gate_required": True,
    }
    if receipt_bundle is not None:
        report["receipt_bundle"] = receipt_bundle
    return report


class ArtifactError(ValueError):
    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


def build_idle_consensus_replay_seed(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Build digest-only metadata for later counterfactual replay admission."""
    if "replay_seed" in artifact:
        raise ArtifactError(
            "idle consensus artifact is not replay-seed eligible",
            {
                "decision": "replay_seed_refused",
                "errors": ["existing replay_seed must not be provided"],
                "exit_code": 2,
            },
        )
    material_keys = sorted(key for key in CANDIDATE_MATERIAL_KEYS if key in artifact)
    if material_keys:
        raise ArtifactError(
            "idle consensus artifact is not replay-seed eligible",
            {
                "decision": "replay_seed_refused",
                "errors": [
                    "candidate diff material is not allowed in replay seed source"
                ],
                "candidate_material_keys": material_keys,
                "exit_code": 2,
            },
        )
    if artifact.get("operator_gate_required") is not True:
        raise ArtifactError(
            "idle consensus artifact is not replay-seed eligible",
            {
                "decision": "replay_seed_refused",
                "errors": ["operator gate is required for replay seed"],
                "exit_code": 2,
            },
        )
    if artifact.get("auto_execute") is not False:
        raise ArtifactError(
            "idle consensus artifact is not replay-seed eligible",
            {
                "decision": "replay_seed_refused",
                "errors": ["auto_execute must be false for replay seed"],
                "exit_code": 2,
            },
        )
    artifact_without_seed = dict(artifact)
    convergence = artifact.get("convergence", {})
    transcript = artifact.get("transcript", [])
    return {
        "seed_version": REPLAY_SEED_VERSION,
        "purpose": "future_counterfactual_candidate_diff_replay",
        "dry_run_only": True,
        "candidate_diff_included": False,
        "external_effect": False,
        "writes_applied": False,
        "would_create_task": False,
        "would_create_branch": False,
        "would_create_pr": False,
        "would_merge": False,
        "consensus_artifact": {
            "artifact_version": str(artifact.get("artifact_version", "")),
            "artifact_id": str(artifact.get("artifact_id", "")),
            "digest": sha256_digest(artifact_without_seed),
        },
        "convergence_digest": sha256_digest(convergence),
        "transcript_digest": sha256_digest(transcript),
        "policy_ref": POLICY_VERSION,
        "charter_ref": CHARTER_VERSION,
        "required_future_inputs": [
            "changed_paths",
            "candidate_diff_digest",
            "candidate_diff_charter_gates",
            "counterfactual_eval_receipt",
            "operator_review_decision",
        ],
        "next_required_gates": [
            "candidate_changed_paths_confinement",
            "candidate_diff_digest_rederived",
            "candidate_diff_charter_gate",
            "counterfactual_eval_receipt",
            "operator_review_gate",
        ],
    }


def build_idle_consensus_counterfactual_eval_binding_template(
    *,
    replay_seed: Mapping[str, Any],
    changed_paths: Sequence[str],
    candidate_diff_text: str,
) -> dict[str, Any]:
    """Build a digest-only replay_binding template for a future receipt."""
    _ensure_replay_seed_ready_for_candidate_diff_admission(replay_seed)
    if not isinstance(candidate_diff_text, str):
        raise ArtifactError(
            "counterfactual eval binding template requires diff text",
            {
                "decision": "counterfactual_eval_binding_template_refused",
                "errors": ["candidate diff text must be a string"],
                "exit_code": 2,
            },
        )
    _refuse_private_text(candidate_diff_text, "candidate diff")
    normalized_paths = _normalize_changed_paths(changed_paths)
    charter = load_charter()
    path_gate = evaluate_paths(charter, normalized_paths)
    diff_gate = evaluate_diff_content(charter, candidate_diff_text)
    candidate_diff_allowed = bool(path_gate.allowed and diff_gate.allowed)
    replay_seed_digest = sha256_digest(replay_seed)
    candidate_diff_digest = sha256_digest(
        {
            "changed_paths": normalized_paths,
            "diff_text": candidate_diff_text,
        }
    )
    return {
        "report_version": COUNTERFACTUAL_EVAL_BINDING_TEMPLATE_VERSION,
        "ok": candidate_diff_allowed,
        "decision": (
            "counterfactual_eval_binding_template_ready"
            if candidate_diff_allowed
            else "operator_review_required"
        ),
        "dry_run": True,
        "external_effect": False,
        "writes_applied": False,
        "would_create_task": False,
        "would_create_branch": False,
        "would_create_pr": False,
        "would_merge": False,
        "receipt_payload_included": False,
        "candidate_diff_charter_allowed": candidate_diff_allowed,
        "replay_seed": {
            "seed_version": replay_seed["seed_version"],
            "digest": replay_seed_digest,
            "consensus_artifact_digest": replay_seed.get(
                "consensus_artifact",
                {},
            ).get("digest"),
            "transcript_digest": replay_seed.get("transcript_digest"),
            "convergence_digest": replay_seed.get("convergence_digest"),
        },
        "candidate_diff": {
            "changed_paths": normalized_paths,
            "digest": candidate_diff_digest,
            "line_count": len(candidate_diff_text.splitlines()),
            "diff_text_included": False,
        },
        "binding_field": "replay_binding",
        "binding_template": {
            "schema_version": COUNTERFACTUAL_EVAL_BINDING_VERSION,
            "replay_seed_digest": replay_seed_digest,
            "candidate_diff_digest": candidate_diff_digest,
        },
        "path_gate": _gate_decision_to_dict(path_gate),
        "diff_gate": _gate_decision_to_dict(diff_gate),
        "next_required_gates": [
            "attach_binding_to_counterfactual_eval_receipt",
            "candidate_diff_replay_admission",
            "operator_review_gate",
        ],
    }


def build_idle_consensus_operator_decision_reference_template(
    *,
    replay_seed: Mapping[str, Any],
    changed_paths: Sequence[str],
    candidate_diff_text: str,
) -> dict[str, Any]:
    """Build a digest-only operator decision reference for a candidate diff."""
    _ensure_replay_seed_ready_for_candidate_diff_admission(replay_seed)
    if not isinstance(candidate_diff_text, str):
        raise ArtifactError(
            "operator decision reference template requires diff text",
            {
                "decision": "operator_decision_reference_template_refused",
                "errors": ["candidate diff text must be a string"],
                "exit_code": 2,
            },
        )
    _refuse_private_text(candidate_diff_text, "candidate diff")
    normalized_paths = _normalize_changed_paths(changed_paths)
    charter = load_charter()
    path_gate = evaluate_paths(charter, normalized_paths)
    diff_gate = evaluate_diff_content(charter, candidate_diff_text)
    candidate_diff_allowed = bool(path_gate.allowed and diff_gate.allowed)
    replay_seed_digest = sha256_digest(replay_seed)
    candidate_diff_digest = sha256_digest(
        {
            "changed_paths": normalized_paths,
            "diff_text": candidate_diff_text,
        }
    )
    reference = {
        "schema_version": OPERATOR_DECISION_REFERENCE_VERSION,
        "decision": OPERATOR_DECISION_APPROVED_FOR_DRAFT_PR,
        "replay_seed_digest": replay_seed_digest,
        "candidate_diff_digest": candidate_diff_digest,
        "operator_gate_required": True,
        "auto_execute": False,
        "external_effect": False,
        "writes_applied": False,
        "would_create_task": False,
        "would_create_branch": False,
        "would_create_pr": False,
        "would_merge": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
    }
    return {
        "report_version": OPERATOR_DECISION_REFERENCE_TEMPLATE_VERSION,
        "ok": candidate_diff_allowed,
        "decision": (
            "operator_decision_reference_template_ready"
            if candidate_diff_allowed
            else "operator_review_required"
        ),
        "dry_run": True,
        "external_effect": False,
        "writes_applied": False,
        "would_create_task": False,
        "would_create_branch": False,
        "would_create_pr": False,
        "would_merge": False,
        "operator_reference_payload_included": True,
        "candidate_diff_charter_allowed": candidate_diff_allowed,
        "replay_seed": {
            "seed_version": replay_seed["seed_version"],
            "digest": replay_seed_digest,
            "consensus_artifact_digest": replay_seed.get(
                "consensus_artifact",
                {},
            ).get("digest"),
            "transcript_digest": replay_seed.get("transcript_digest"),
            "convergence_digest": replay_seed.get("convergence_digest"),
        },
        "candidate_diff": {
            "changed_paths": normalized_paths,
            "digest": candidate_diff_digest,
            "line_count": len(candidate_diff_text.splitlines()),
            "diff_text_included": False,
        },
        "operator_decision_reference": reference,
        "path_gate": _gate_decision_to_dict(path_gate),
        "diff_gate": _gate_decision_to_dict(diff_gate),
        "next_required_gates": [
            "operator_review_gate",
            "operator_decision_authentication",
            "counterfactual_eval_receipt",
            "candidate_diff_replay_admission",
        ],
    }


def build_idle_consensus_candidate_diff_replay_admission(
    *,
    replay_seed: Mapping[str, Any],
    changed_paths: Sequence[str],
    candidate_diff_text: str,
    counterfactual_eval_receipt: Mapping[str, Any] | None = None,
    operator_decision_reference: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a report-only admission check for replaying a candidate diff."""
    _ensure_replay_seed_ready_for_candidate_diff_admission(replay_seed)
    if not isinstance(candidate_diff_text, str):
        raise ArtifactError(
            "candidate diff replay admission requires diff text",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": ["candidate diff text must be a string"],
                "exit_code": 2,
            },
        )
    _refuse_private_text(candidate_diff_text, "candidate diff")
    normalized_paths = _normalize_changed_paths(changed_paths)
    charter = load_charter()
    path_gate = evaluate_paths(charter, normalized_paths)
    diff_gate = evaluate_diff_content(charter, candidate_diff_text)
    candidate_diff_allowed = bool(path_gate.allowed and diff_gate.allowed)
    decision = (
        "candidate_diff_charter_passed"
        if candidate_diff_allowed
        else "operator_review_required"
    )
    replay_seed_digest = sha256_digest(replay_seed)
    candidate_diff_digest = sha256_digest(
        {
            "changed_paths": normalized_paths,
            "diff_text": candidate_diff_text,
        }
    )
    counterfactual_eval = _counterfactual_eval_admission_summary(
        counterfactual_eval_receipt,
        replay_seed_digest=replay_seed_digest,
        candidate_diff_digest=candidate_diff_digest,
    )
    operator_decision = _operator_decision_reference_summary(
        operator_decision_reference,
        replay_seed_digest=replay_seed_digest,
        candidate_diff_digest=candidate_diff_digest,
    )
    eligible_for_draft_pr_gate = bool(
        candidate_diff_allowed
        and counterfactual_eval.get("satisfies_replay_gate") is True
        and operator_decision.get("satisfies_operator_gate") is True
    )
    return {
        "report_version": CANDIDATE_DIFF_REPLAY_ADMISSION_VERSION,
        "ok": candidate_diff_allowed,
        "decision": decision,
        "dry_run": True,
        "external_effect": False,
        "writes_applied": False,
        "would_create_task": False,
        "would_create_branch": False,
        "would_create_pr": False,
        "would_merge": False,
        "candidate_diff_charter_allowed": candidate_diff_allowed,
        "replay_seed": {
            "seed_version": replay_seed["seed_version"],
            "digest": replay_seed_digest,
            "consensus_artifact_digest": replay_seed.get(
                "consensus_artifact",
                {},
            ).get("digest"),
            "transcript_digest": replay_seed.get("transcript_digest"),
            "convergence_digest": replay_seed.get("convergence_digest"),
        },
        "candidate_diff": {
            "changed_paths": normalized_paths,
            "digest": candidate_diff_digest,
            "line_count": len(candidate_diff_text.splitlines()),
            "diff_text_included": False,
        },
        "counterfactual_eval": counterfactual_eval,
        "operator_decision_reference": operator_decision,
        "path_gate": _gate_decision_to_dict(path_gate),
        "diff_gate": _gate_decision_to_dict(diff_gate),
        "eligible_for_draft_pr_gate": eligible_for_draft_pr_gate,
        "draft_pr_gate_blockers": _candidate_diff_replay_blockers(
            counterfactual_eval,
            operator_decision,
        ),
        "next_required_gates": _candidate_diff_replay_next_required_gates(
            counterfactual_eval,
            operator_decision,
        ),
    }


def _counterfactual_eval_admission_summary(
    receipt: Mapping[str, Any] | None,
    *,
    replay_seed_digest: str,
    candidate_diff_digest: str,
) -> dict[str, Any]:
    if receipt is None:
        observability = summarize_counterfactual_observability(None)
        binding = _counterfactual_receipt_binding_summary(
            None,
            replay_seed_digest=replay_seed_digest,
            candidate_diff_digest=candidate_diff_digest,
        )
        return {
            "summary_version": COUNTERFACTUAL_EVAL_ADMISSION_SUMMARY_VERSION,
            "provided": False,
            "source_digest": None,
            "receipt_payload_included": False,
            "observability_satisfies_replay_gate": False,
            "satisfies_replay_gate": False,
            "dry_run_only": True,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
            "binding": binding,
            "observability": observability,
        }
    if not isinstance(receipt, Mapping):
        raise ArtifactError(
            "candidate diff replay admission requires a mapping counterfactual receipt",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": ["counterfactual eval receipt must be a mapping"],
                "exit_code": 2,
            },
        )
    try:
        receipt_text = json.dumps(receipt, sort_keys=True)
    except TypeError as exc:
        raise ArtifactError(
            "candidate diff replay admission requires a serializable counterfactual receipt",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": ["counterfactual eval receipt must be JSON serializable"],
                "exit_code": 2,
            },
        ) from exc
    _refuse_private_text(receipt_text, "counterfactual eval receipt")

    observability = summarize_counterfactual_observability(receipt)
    observability_satisfies_replay_gate = _counterfactual_observability_satisfies_replay_gate(
        observability
    )
    binding = _counterfactual_receipt_binding_summary(
        receipt,
        replay_seed_digest=replay_seed_digest,
        candidate_diff_digest=candidate_diff_digest,
    )
    satisfies_replay_gate = (
        observability_satisfies_replay_gate and binding["matches"]
    )
    return {
        "summary_version": COUNTERFACTUAL_EVAL_ADMISSION_SUMMARY_VERSION,
        "provided": True,
        "source_digest": sha256_digest(receipt),
        "receipt_payload_included": False,
        "observability_satisfies_replay_gate": observability_satisfies_replay_gate,
        "satisfies_replay_gate": satisfies_replay_gate,
        "dry_run_only": True,
        "runtime_authority_granted": (
            observability.get("runtime_authority_granted") is True
        ),
        "external_writes_applied": (
            observability.get("external_writes_applied") is True
        ),
        "binding": binding,
        "observability": observability,
    }


def _counterfactual_receipt_binding_summary(
    receipt: Mapping[str, Any] | None,
    *,
    replay_seed_digest: str,
    candidate_diff_digest: str,
) -> dict[str, Any]:
    binding = (
        receipt.get("replay_binding")
        if isinstance(receipt, Mapping)
        else None
    )
    provided = isinstance(binding, Mapping)
    replay_seed_matches = (
        provided and binding.get("replay_seed_digest") == replay_seed_digest
    )
    candidate_diff_matches = (
        provided and binding.get("candidate_diff_digest") == candidate_diff_digest
    )
    return {
        "schema_version": COUNTERFACTUAL_EVAL_BINDING_VERSION,
        "provided": provided,
        "expected_replay_seed_digest": replay_seed_digest,
        "expected_candidate_diff_digest": candidate_diff_digest,
        "replay_seed_digest_matches": replay_seed_matches,
        "candidate_diff_digest_matches": candidate_diff_matches,
        "matches": bool(replay_seed_matches and candidate_diff_matches),
        "receipt_payload_included": False,
    }


def _operator_decision_reference_summary(
    reference: Mapping[str, Any] | None,
    *,
    replay_seed_digest: str,
    candidate_diff_digest: str,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": OPERATOR_DECISION_REFERENCE_VERSION,
        "provided": False,
        "source_digest": None,
        "payload_included": False,
        "decision": "",
        "decision_approved": False,
        "schema_version_matches": False,
        "expected_replay_seed_digest": replay_seed_digest,
        "expected_candidate_diff_digest": candidate_diff_digest,
        "replay_seed_digest_matches": False,
        "candidate_diff_digest_matches": False,
        "operator_gate_required": None,
        "auto_execute": None,
        "external_effect": None,
        "writes_applied": None,
        "would_create_task": None,
        "would_create_branch": None,
        "would_create_pr": None,
        "would_merge": None,
        "runtime_authority_granted": None,
        "external_writes_applied": None,
        "authority_boundary_clear": False,
        "operator_authentication_provided": False,
        "operator_authentication_schema_matches": False,
        "operator_authentication_method_matches": False,
        "operator_authenticated": False,
        "operator_signature_digest": None,
        "operator_signature_digest_present": False,
        "expected_signed_reference_digest": None,
        "signed_reference_digest": None,
        "signed_reference_digest_matches": False,
        "operator_authentication_shape_valid": False,
        "operator_authentication_verifier_backed": False,
        "operator_authentication_valid": False,
        "satisfies_operator_gate": False,
        "blocker": "operator_review_gate_required",
    }
    if reference is None:
        return base
    if not isinstance(reference, Mapping):
        raise ArtifactError(
            "candidate diff replay admission requires a mapping operator decision reference",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": ["operator decision reference must be a mapping"],
                "exit_code": 2,
            },
        )
    try:
        reference_text = json.dumps(reference, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ArtifactError(
            "candidate diff replay admission requires a serializable operator decision reference",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": ["operator decision reference must be JSON serializable"],
                "exit_code": 2,
            },
        ) from exc
    _refuse_private_text(reference_text, "operator decision reference")

    summary = dict(base)
    decision = str(reference.get("decision", ""))
    unsigned_reference = {
        key: value
        for key, value in reference.items()
        if key != "operator_authentication"
    }
    expected_signed_reference_digest = sha256_digest(unsigned_reference)
    authentication = reference.get("operator_authentication")
    authentication_provided = isinstance(authentication, Mapping)
    authentication_schema_matches = False
    authentication_method_matches = False
    operator_authenticated = False
    operator_signature_digest: Any = None
    operator_signature_digest_present = False
    signed_reference_digest: Any = None
    signed_reference_digest_matches = False
    if authentication_provided:
        authentication_schema_matches = (
            authentication.get("schema_version")
            == OPERATOR_DECISION_AUTHENTICATION_VERSION
        )
        authentication_method_matches = (
            authentication.get("authentication_method")
            == OPERATOR_DECISION_AUTHENTICATION_METHOD
        )
        operator_authenticated = authentication.get("operator_authenticated") is True
        operator_signature_digest = authentication.get("operator_signature_digest")
        operator_signature_digest_present = _is_sha256_digest(
            operator_signature_digest
        )
        signed_reference_digest = authentication.get("signed_reference_digest")
        signed_reference_digest_matches = (
            signed_reference_digest == expected_signed_reference_digest
        )
    operator_authentication_shape_valid = bool(
        authentication_schema_matches
        and authentication_method_matches
        and operator_authenticated
        and operator_signature_digest_present
        and signed_reference_digest_matches
    )
    operator_authentication_verifier_backed = False
    operator_authentication_valid = bool(
        operator_authentication_shape_valid
        and operator_authentication_verifier_backed
    )
    summary.update(
        {
            "provided": True,
            "source_digest": sha256_digest(reference),
            "decision": decision,
            "decision_approved": decision == OPERATOR_DECISION_APPROVED_FOR_DRAFT_PR,
            "schema_version_matches": (
                reference.get("schema_version")
                == OPERATOR_DECISION_REFERENCE_VERSION
            ),
            "replay_seed_digest_matches": (
                reference.get("replay_seed_digest") == replay_seed_digest
            ),
            "candidate_diff_digest_matches": (
                reference.get("candidate_diff_digest") == candidate_diff_digest
            ),
            "operator_gate_required": reference.get("operator_gate_required"),
            "auto_execute": reference.get("auto_execute"),
            "external_effect": reference.get("external_effect"),
            "writes_applied": reference.get("writes_applied"),
            "would_create_task": reference.get("would_create_task"),
            "would_create_branch": reference.get("would_create_branch"),
            "would_create_pr": reference.get("would_create_pr"),
            "would_merge": reference.get("would_merge"),
            "runtime_authority_granted": reference.get(
                "runtime_authority_granted"
            ),
            "external_writes_applied": reference.get("external_writes_applied"),
            "operator_authentication_provided": authentication_provided,
            "operator_authentication_schema_matches": (
                authentication_schema_matches
            ),
            "operator_authentication_method_matches": (
                authentication_method_matches
            ),
            "operator_authenticated": operator_authenticated,
            "operator_signature_digest": operator_signature_digest,
            "operator_signature_digest_present": (
                operator_signature_digest_present
            ),
            "expected_signed_reference_digest": expected_signed_reference_digest,
            "signed_reference_digest": signed_reference_digest,
            "signed_reference_digest_matches": signed_reference_digest_matches,
            "operator_authentication_shape_valid": (
                operator_authentication_shape_valid
            ),
            "operator_authentication_verifier_backed": (
                operator_authentication_verifier_backed
            ),
            "operator_authentication_valid": operator_authentication_valid,
        }
    )
    authority_boundary_clear = all(
        summary[key] is False
        for key in (
            "auto_execute",
            "external_effect",
            "writes_applied",
            "would_create_task",
            "would_create_branch",
            "would_create_pr",
            "would_merge",
            "runtime_authority_granted",
            "external_writes_applied",
        )
    )
    summary["authority_boundary_clear"] = authority_boundary_clear
    summary["satisfies_operator_gate"] = bool(
        summary["schema_version_matches"]
        and summary["decision_approved"]
        and summary["replay_seed_digest_matches"]
        and summary["candidate_diff_digest_matches"]
        and summary["operator_gate_required"] is True
        and authority_boundary_clear
        and operator_authentication_valid
    )
    if summary["satisfies_operator_gate"]:
        summary["blocker"] = None
    elif not summary["schema_version_matches"]:
        summary["blocker"] = "operator_decision_reference_schema_mismatch"
    elif not summary["decision_approved"]:
        summary["blocker"] = "operator_decision_reference_not_approved"
    elif (
        not summary["replay_seed_digest_matches"]
        or not summary["candidate_diff_digest_matches"]
    ):
        summary["blocker"] = "operator_decision_reference_binding_mismatch"
    elif summary["operator_gate_required"] is not True:
        summary["blocker"] = "operator_decision_reference_gate_mismatch"
    elif not authority_boundary_clear:
        summary["blocker"] = "operator_decision_reference_authority_boundary_failed"
    elif not authentication_provided:
        summary["blocker"] = "operator_decision_reference_authentication_missing"
    elif not operator_authentication_valid:
        summary["blocker"] = "operator_decision_reference_authentication_invalid"
    return summary


def _is_sha256_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None
    )


def _counterfactual_observability_satisfies_replay_gate(
    observability: Mapping[str, Any],
) -> bool:
    return (
        observability.get("source_available") is True
        and observability.get("status") in COUNTERFACTUAL_EVAL_READY_STATES
        and observability.get("same_sample_set") is True
        and observability.get("deterministic") is True
        and observability.get("delta_digest_present") is True
        and observability.get("controls_present") is False
        and observability.get("runtime_authority_granted") is False
        and observability.get("external_writes_applied") is False
        and observability.get("payload_fields_exported") is False
    )


def _candidate_diff_replay_blockers(
    counterfactual_eval: Mapping[str, Any],
    operator_decision: Mapping[str, Any],
) -> list[str]:
    blockers: list[str] = []
    if counterfactual_eval.get("provided") is not True:
        blockers.append("counterfactual_eval_receipt_missing")
    elif counterfactual_eval.get("observability_satisfies_replay_gate") is not True:
        blockers.append("counterfactual_eval_receipt_insufficient")
    elif counterfactual_eval.get("binding", {}).get("provided") is not True:
        blockers.append("counterfactual_eval_receipt_unbound")
    elif counterfactual_eval.get("binding", {}).get("matches") is not True:
        blockers.append("counterfactual_eval_receipt_binding_mismatch")
    operator_blocker = operator_decision.get("blocker")
    if operator_blocker:
        blockers.append(str(operator_blocker))
    return blockers


def _candidate_diff_replay_next_required_gates(
    counterfactual_eval: Mapping[str, Any],
    operator_decision: Mapping[str, Any],
) -> list[str]:
    gates: list[str] = []
    if counterfactual_eval.get("provided") is not True:
        gates.append("counterfactual_eval_receipt")
    elif counterfactual_eval.get("observability_satisfies_replay_gate") is not True:
        gates.append("counterfactual_eval_receipt")
    elif counterfactual_eval.get("binding", {}).get("matches") is not True:
        gates.append("counterfactual_eval_receipt_binding")
    if operator_decision.get("satisfies_operator_gate") is not True:
        gates.append("operator_review_gate")
    gates.extend(
        [
            "draft_pr_creation",
            "ci_green",
            "mergeable_clean",
            "exact_head_merge",
        ]
    )
    return gates


def build_candidate_diff_replay_admission_from_files(
    *,
    enabled: bool,
    replay_seed_path: Path | None,
    candidate_diff_path: Path | None,
    changed_paths: Sequence[str],
    counterfactual_eval_receipt_path: Path | None = None,
    operator_decision_reference_path: Path | None = None,
) -> dict[str, Any]:
    """Load local files and build a report-only candidate diff admission."""
    if not enabled:
        raise ArtifactError(
            "candidate diff replay admission mode is required",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": [
                    "--candidate-diff-replay-admission is required with replay admission inputs"
                ],
                "exit_code": 2,
            },
        )
    missing = []
    if replay_seed_path is None:
        missing.append("--replay-seed")
    if candidate_diff_path is None:
        missing.append("--candidate-diff")
    if not changed_paths:
        missing.append("--changed-path")
    if missing:
        raise ArtifactError(
            "candidate diff replay admission inputs are incomplete",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": [f"missing required argument(s): {', '.join(missing)}"],
                "exit_code": 2,
            },
        )

    replay_seed = _read_replay_seed_file(replay_seed_path)
    candidate_diff_text = _read_text_file(candidate_diff_path, "candidate diff")
    counterfactual_eval_receipt = (
        _read_json_object(
            counterfactual_eval_receipt_path,
            "counterfactual eval receipt",
        )
        if counterfactual_eval_receipt_path is not None
        else None
    )
    operator_decision_reference = (
        _read_json_object(
            operator_decision_reference_path,
            "operator decision reference",
        )
        if operator_decision_reference_path is not None
        else None
    )
    report = build_idle_consensus_candidate_diff_replay_admission(
        replay_seed=replay_seed,
        changed_paths=changed_paths,
        candidate_diff_text=candidate_diff_text,
        counterfactual_eval_receipt=counterfactual_eval_receipt,
        operator_decision_reference=operator_decision_reference,
    )
    report["exit_code"] = 0 if report["ok"] else 1
    return report


def build_operator_decision_reference_template_from_files(
    *,
    enabled: bool,
    replay_seed_path: Path | None,
    candidate_diff_path: Path | None,
    changed_paths: Sequence[str],
) -> dict[str, Any]:
    """Load local files and build a digest-only operator decision reference."""
    if not enabled:
        raise ArtifactError(
            "operator decision reference template mode is required",
            {
                "decision": "operator_decision_reference_template_refused",
                "errors": [
                    "--operator-decision-reference-template is required with "
                    "operator decision reference template inputs"
                ],
                "exit_code": 2,
            },
        )
    missing = []
    if replay_seed_path is None:
        missing.append("--replay-seed")
    if candidate_diff_path is None:
        missing.append("--candidate-diff")
    if not changed_paths:
        missing.append("--changed-path")
    if missing:
        raise ArtifactError(
            "operator decision reference template inputs are incomplete",
            {
                "decision": "operator_decision_reference_template_refused",
                "errors": [f"missing required argument(s): {', '.join(missing)}"],
                "exit_code": 2,
            },
        )

    replay_seed = _read_replay_seed_file(replay_seed_path)
    candidate_diff_text = _read_text_file(candidate_diff_path, "candidate diff")
    report = build_idle_consensus_operator_decision_reference_template(
        replay_seed=replay_seed,
        changed_paths=changed_paths,
        candidate_diff_text=candidate_diff_text,
    )
    report["exit_code"] = 0 if report["ok"] else 1
    return report


def build_counterfactual_eval_binding_template_from_files(
    *,
    enabled: bool,
    replay_seed_path: Path | None,
    candidate_diff_path: Path | None,
    changed_paths: Sequence[str],
    counterfactual_eval_receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Load local files and build a report-only receipt binding template."""
    if not enabled:
        raise ArtifactError(
            "counterfactual eval binding template mode is required",
            {
                "decision": "counterfactual_eval_binding_template_refused",
                "errors": [
                    "--counterfactual-eval-binding-template is required with "
                    "binding-template inputs"
                ],
                "exit_code": 2,
            },
        )
    missing = []
    if replay_seed_path is None:
        missing.append("--replay-seed")
    if candidate_diff_path is None:
        missing.append("--candidate-diff")
    if not changed_paths:
        missing.append("--changed-path")
    if missing:
        raise ArtifactError(
            "counterfactual eval binding template inputs are incomplete",
            {
                "decision": "counterfactual_eval_binding_template_refused",
                "errors": [f"missing required argument(s): {', '.join(missing)}"],
                "exit_code": 2,
            },
        )
    if counterfactual_eval_receipt_path is not None:
        raise ArtifactError(
            "counterfactual eval binding template does not consume receipt payloads",
            {
                "decision": "counterfactual_eval_binding_template_refused",
                "errors": [
                    "--counterfactual-eval-receipt is not accepted in "
                    "binding-template mode"
                ],
                "exit_code": 2,
            },
        )

    replay_seed = _read_replay_seed_file(replay_seed_path)
    candidate_diff_text = _read_text_file(candidate_diff_path, "candidate diff")
    report = build_idle_consensus_counterfactual_eval_binding_template(
        replay_seed=replay_seed,
        changed_paths=changed_paths,
        candidate_diff_text=candidate_diff_text,
    )
    report["exit_code"] = 0 if report["ok"] else 1
    return report


def _read_replay_seed_file(path: Path) -> Mapping[str, Any]:
    value = _read_json_object(path, "replay seed")
    if isinstance(value.get("replay_seed"), Mapping):
        value = value["replay_seed"]
    return value


def _ensure_replay_seed_ready_for_candidate_diff_admission(
    replay_seed: Mapping[str, Any],
) -> None:
    if replay_seed.get("seed_version") != REPLAY_SEED_VERSION:
        raise ArtifactError(
            "candidate diff replay admission requires an idle replay seed",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": ["invalid replay seed version"],
                "exit_code": 2,
            },
        )
    if replay_seed.get("purpose") != "future_counterfactual_candidate_diff_replay":
        raise ArtifactError(
            "candidate diff replay admission requires an idle replay seed",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": ["invalid replay seed purpose"],
                "exit_code": 2,
            },
        )
    if replay_seed.get("dry_run_only") is not True:
        raise ArtifactError(
            "candidate diff replay admission requires a dry-run replay seed",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": ["replay seed dry_run_only must be true"],
                "exit_code": 2,
            },
        )
    for key in REPLAY_SEED_REQUIRED_FALSE_KEYS:
        if replay_seed.get(key) is not False:
            raise ArtifactError(
                "candidate diff replay admission requires a no-authority replay seed",
                {
                    "decision": "candidate_diff_replay_refused",
                    "errors": [f"replay seed {key} must be false"],
                    "exit_code": 2,
                },
            )
    material_keys = sorted(key for key in CANDIDATE_MATERIAL_KEYS if key in replay_seed)
    if material_keys:
        raise ArtifactError(
            "candidate diff replay admission requires a digest-only replay seed",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": ["candidate diff material is not allowed in replay seed"],
                "candidate_material_keys": material_keys,
                "exit_code": 2,
            },
        )


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise ArtifactError(
            f"missing bridge events file: {path}",
            {
                "decision": "missing_events",
                "errors": [f"missing bridge events file: {path}"],
                "exit_code": 2,
            },
        )
    events: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ArtifactError(
                "bridge events contain invalid JSON",
                {
                    "decision": "invalid_events",
                    "errors": [f"line {line_no}: {exc.msg}"],
                    "exit_code": 2,
                },
            ) from exc
        if not isinstance(event, dict):
            raise ArtifactError(
                "bridge events contain non-object JSON",
                {
                    "decision": "invalid_events",
                    "errors": [f"line {line_no}: event must be an object"],
                    "exit_code": 2,
                },
            )
        events.append(event)
    return events


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactError(
            f"missing {label} file: {path}",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": [f"missing {label} file: {path}"],
                "exit_code": 2,
            },
        ) from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ArtifactError(
            f"{label} file contains invalid JSON",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": [f"{label} JSON: {exc.msg}"],
                "exit_code": 2,
            },
        ) from exc
    if not isinstance(value, dict):
        raise ArtifactError(
            f"{label} file must contain a JSON object",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": [f"{label} must be a JSON object"],
                "exit_code": 2,
            },
        )
    return value


def _read_text_file(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ArtifactError(
            f"missing {label} file: {path}",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": [f"missing {label} file: {path}"],
                "exit_code": 2,
            },
        ) from exc


def _idle_payloads(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for event in events:
        if event.get("protocol_version") == "idle-protocol.v1":
            payloads.append(dict(event))
            continue
        payload = event.get("payload")
        if (
            isinstance(payload, Mapping)
            and payload.get("protocol_version") == "idle-protocol.v1"
        ):
            payloads.append(dict(payload))
    return payloads


def _refuse_private_markers(payloads: Sequence[Mapping[str, Any]]) -> None:
    text = json.dumps(payloads, sort_keys=True)
    _refuse_private_text(text, "idle transcript")


def _refuse_private_text(text: str, label: str) -> None:
    for marker in PRIVATE_MARKERS:
        if marker in text:
            raise ArtifactError(
                f"privacy marker detected in {label}",
                {
                    "decision": "privacy_marker_detected",
                    "errors": [f"{label} contains {marker}"],
                    "exit_code": 2,
                },
            )


def _normalize_changed_paths(changed_paths: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    for index, path in enumerate(changed_paths, 1):
        if not isinstance(path, str) or not path.strip():
            raise ArtifactError(
                "candidate diff replay admission requires changed paths",
                {
                    "decision": "candidate_diff_replay_refused",
                    "errors": [f"changed path {index} must be a non-empty string"],
                    "exit_code": 2,
                },
            )
        normalized.append(path.strip().replace("\\", "/"))
    if not normalized:
        raise ArtifactError(
            "candidate diff replay admission requires changed paths",
            {
                "decision": "candidate_diff_replay_refused",
                "errors": ["changed paths must be non-empty"],
                "exit_code": 2,
            },
        )
    return normalized


def _gate_decision_to_dict(gate: Any) -> dict[str, Any]:
    return {
        "allowed": bool(gate.allowed),
        "reason": gate.reason,
        "blocked_paths": list(gate.blocked_paths),
        "unmatched_paths": list(gate.unmatched_paths),
        "code_pattern_hits": list(gate.code_pattern_hits),
    }


def _artifact_id(
    convergence: Mapping[str, Any],
    payloads: Sequence[Mapping[str, Any]],
) -> str:
    target = (
        convergence.get("target_proposal_id")
        or "-".join(convergence.get("finalist_proposal_ids", [])[:3])
        or payloads[-1].get("proposal_id")
        or "unknown"
    )
    return _slug(f"idle-consensus-{target}")


def _slug(value: object) -> str:
    text = str(value).lower()
    text = re.sub(r"[^a-z0-9_.-]+", "-", text)
    return text.strip("-")[:120] or "idle-consensus-unknown"


def _markdown(artifact: Mapping[str, Any]) -> str:
    convergence = artifact["convergence"]
    lines = [
        "# Idle Consensus Operator Review",
        "",
        "Operator review required before any work begins.",
        "",
        f"- Artifact: `{artifact['artifact_id']}`",
        f"- Created UTC: `{artifact['created_at_utc']}`",
        f"- Convergence: `{convergence['status']}`",
        f"- Auto execute: `{str(artifact['auto_execute']).lower()}`",
        f"- Operator gate required: `{str(artifact['operator_gate_required']).lower()}`",
        "",
        "This artifact is evidence only. It authorizes no task creation, no branch creation, no pull request creation, and no external effect.",
        "",
        "## Transcript",
    ]
    for payload in artifact["transcript"]:
        lines.extend(
            [
                "",
                f"### Round {payload['round_number']}: {payload['event_type']}",
                "",
                f"- Proposal id: `{payload['proposal_id']}`",
                f"- Problem: {payload['problem_statement']}",
                f"- Tradeoff: {payload['tradeoff_axis']}",
            ]
        )
    if "replay_seed" in artifact:
        seed = artifact["replay_seed"]
        lines.extend(
            [
                "",
                "## Replay Seed",
                "",
                f"- Seed version: `{seed['seed_version']}`",
                f"- Consensus artifact digest: `{seed['consensus_artifact']['digest']}`",
                f"- Transcript digest: `{seed['transcript_digest']}`",
                f"- Convergence digest: `{seed['convergence_digest']}`",
                "",
                "The replay seed stores digest metadata only. It includes no candidate diff and authorizes no writes or external effect.",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _emit_receipt_bundle(
    *,
    artifact: Mapping[str, Any],
    out_dir: Path,
    now_utc: datetime,
) -> dict[str, Any]:
    artifact_id = str(artifact["artifact_id"])
    evaluation = build_evaluation_result(
        case_id=f"case:idle_consensus_artifact:{artifact_id}",
        subject_type="peer_review",
        target_payload=dict(artifact),
        risk_class="local_artifact",
        expected_gate="review",
        actual_gate="review",
        verifier_path=[
            "idle_protocol_quality_validator",
            "idle_protocol_convergence_detector",
            "idle_consensus_artifact_guard",
            "magma_receipt_verifier_v1",
        ],
        solver_selection=[
            "idle_protocol_convergence_detector",
            "idle_consensus_artifact_writer",
        ],
        policy_version="policy:idle_consensus_artifact:v1",
        charter_version="charter:v1",
        domain_threshold_version="threshold:idle_protocol:v1",
        verdict="review",
        reason_codes=[
            "idle_consensus_artifact:operator_review_required",
            f"convergence:{artifact['convergence']['status']}",
            "receipt_bundle:opt_in",
        ],
        confidence_score=1.0,
        uncertainty_sources=[],
    )
    evaluation["operator_required"] = True
    receipt = build_magma_receipt(
        event_id=f"magma:idle_consensus_artifact:{artifact_id}",
        ts_utc=_iso(now_utc),
        risk_class="local_artifact",
        payload=artifact,
        evaluation_result=evaluation,
        previous_receipt=None,
        policy_digest=sha256_digest({"policy_version": evaluation["policy_version"]}),
        charter_digest=sha256_digest(
            {
                "charter_version": evaluation["charter_version"],
                "operator_gate_required": artifact["operator_gate_required"],
                "auto_execute": artifact["auto_execute"],
            }
        ),
        rco_decision_digest=sha256_digest(
            {
                "actual_gate": evaluation["actual_gate"],
                "decision": artifact["decision"],
                "prohibited_actions": artifact["prohibited_actions"],
            }
        ),
        world_snapshot_digest=sha256_digest(
            {
                "artifact_id": artifact_id,
                "created_at_utc": artifact["created_at_utc"],
                "convergence_status": artifact["convergence"]["status"],
            }
        ),
        solver_contract_digest=sha256_digest(
            {
                "solver_selection": evaluation["solver_selection"],
                "policy_version": evaluation["policy_version"],
            }
        ),
    )
    receipt["operator_gate_required"] = True
    return write_receipt_bundle(
        out_dir=out_dir,
        chain_id=f"magma:idle_consensus_artifact:{artifact_id}:v0",
        entries=[
            ReceiptBundleEntry(
                label="artifact",
                payload=dict(artifact),
                evaluation_result=evaluation,
                receipt=receipt,
            )
        ],
        verify_manifest=verify_manifest,
    )


def _assert_no_implementation_hints(markdown: str) -> None:
    lowered = markdown.lower()
    for hint in IMPLEMENTATION_HINTS:
        if hint in lowered:
            raise ArtifactError(
                "artifact contains prohibited implementation hint",
                {
                    "decision": "artifact_hint_refused",
                    "errors": [f"prohibited phrase: {hint}"],
                    "exit_code": 2,
                },
            )


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
