# SPDX-License-Identifier: BUSL-1.1
"""Write a MAGMA receipt for a verified bridge-consensus merge plan.

This helper is deliberately separate from ``idle_consensus_auto_merge``: it
creates the receipt bundle that the merge gate already requires, but it does
not perform the merge or weaken the gate's fail-closed checks.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.idle_consensus_auto_merge import (  # noqa: E402
    REVIEW_POLICIES,
    REVIEW_POLICY_LEGACY,
    REVIEW_POLICY_RULE12,
    AutoMergeGateError,
    evaluate_auto_merge_gate,
)
from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.evaluation_result import (  # noqa: E402
    build_evaluation_result,
)
from waggledance.core.magma.receipt import build_magma_receipt  # noqa: E402
from waggledance.core.magma.receipt_bundle import (  # noqa: E402
    ReceiptBundleEntry,
    write_receipt_bundle,
)
from tools.bridge_accepted_queue_preflight import (  # noqa: E402
    bridge_events_path_matches_root,
)
from waggledance.core.work_queue import resolve_bridge_root  # noqa: E402
from tools.operator_path_exception import apply_operator_path_exception  # noqa: E402


class BridgeConsensusMergeReceiptError(ValueError):
    """Raised when the receipt cannot be written safely."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write a verified MAGMA receipt for a bridge-consensus merge.",
    )
    parser.add_argument("--pr-status-file", type=Path, required=True)
    parser.add_argument("--events", type=Path, default=None)
    parser.add_argument(
        "--bridge-root",
        type=Path,
        default=None,
        help=(
            "Runtime bridge root used when --events is omitted. Defaults to "
            "AGENT_BRIDGE_RUNTIME_ROOT, AGENT_BRIDGE_ROOT, then repo .agent-bridge."
        ),
    )
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-base-sha", default="")
    parser.add_argument("--consensus-proposal-id", required=True)
    parser.add_argument("--repo", default="")
    parser.add_argument("--from-agent", default="")
    parser.add_argument("--bridge-task-id", default="")
    parser.add_argument("--now", default="")
    parser.add_argument(
        "--review-policy",
        choices=sorted(REVIEW_POLICIES),
        default=REVIEW_POLICY_LEGACY,
        help="Opt-in rule12 review evaluator; the default is the Rule 9a verifier.",
    )
    parser.add_argument(
        "--grok-fallback",
        action="store_true",
        help=(
            "rule12 only: read bound Grok consultations from the fixed helper "
            "reports root so Grok may hold a vacant RCO slot (default off)."
        ),
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        bridge_root = resolve_bridge_root(args.bridge_root)
        events_path = (
            args.events
            if args.events is not None
            else bridge_root / "shared" / "events.jsonl"
        )
        if (
            args.events is not None
            and not bridge_events_path_matches_root(
                bridge_root=bridge_root,
                events_path=events_path,
            )
        ):
            raise ValueError(
                "--events must equal <bridge-root>/shared/events.jsonl"
            )
        pr_status = json.loads(args.pr_status_file.read_text(encoding="utf-8"))
        now_utc = _parse_utc(args.now) if args.now else datetime.now(timezone.utc)
        report = write_bridge_consensus_merge_receipt(
            pr_status=pr_status,
            events_path=events_path,
            out_dir=args.out_dir,
            expected_head=args.expected_head,
            expected_base_sha=args.expected_base_sha,
            consensus_proposal_id=args.consensus_proposal_id,
            repo=args.repo,
            from_agent=args.from_agent,
            bridge_task_id=args.bridge_task_id,
            now_utc=now_utc,
            review_policy=args.review_policy,
            grok_fallback=args.grok_fallback,
        )
    except BridgeConsensusMergeReceiptError as exc:
        report = exc.report
        exit_code = int(report.get("exit_code", 2))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        report = {
            "decision": "invalid_input",
            "ok": False,
            "errors": [exc.__class__.__name__],
        }
        exit_code = 2
    else:
        exit_code = 0

    if args.json:
        print(json.dumps(report, sort_keys=True))
    elif report.get("ok"):
        print(f"bridge consensus merge receipt OK: {report['receipt_bundle_path']}")
    else:
        print("bridge consensus merge receipt FAILED", file=sys.stderr)
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
    return exit_code


def write_bridge_consensus_merge_receipt(
    *,
    pr_status: Mapping[str, Any],
    events_path: Path,
    out_dir: Path,
    expected_head: str,
    expected_base_sha: str = "",
    consensus_proposal_id: str,
    repo: str = "",
    from_agent: str = "",
    bridge_task_id: str = "",
    now_utc: datetime | None = None,
    operator_path_exception: Mapping[str, Any] | None = None,
    review_policy: str = REVIEW_POLICY_LEGACY,
    grok_fallback: bool = False,
    _grok_reports_root: Path | None = None,
) -> dict[str, Any]:
    now = _validated_now_utc(now_utc)
    if type(grok_fallback) is not bool or (
        grok_fallback and review_policy != REVIEW_POLICY_RULE12
    ):
        raise BridgeConsensusMergeReceiptError(
            {
                "decision": "invalid_input",
                "ok": False,
                "errors": ["grok_fallback must be a boolean and needs review_policy=rule12"],
                "exit_code": 2,
            }
        )
    if type(review_policy) is not str or review_policy not in REVIEW_POLICIES:
        raise BridgeConsensusMergeReceiptError(
            {
                "decision": "invalid_input",
                "ok": False,
                "errors": [
                    "review_policy must be one of: "
                    + ", ".join(sorted(REVIEW_POLICIES))
                ],
                "exit_code": 2,
            }
        )
    manifest_path = out_dir / "manifest.json"
    gate_report = _merge_plan_report(
        pr_status=pr_status,
        events_path=events_path,
        expected_head=expected_head,
        expected_base_sha=expected_base_sha,
        consensus_proposal_id=consensus_proposal_id,
        receipt_bundle_path=str(manifest_path),
        repo=repo,
        from_agent=from_agent,
        bridge_task_id=bridge_task_id,
        now_utc=now,
        review_policy=review_policy,
        grok_fallback=grok_fallback,
        grok_reports_root=_grok_reports_root,
    )
    gate_report = apply_operator_path_exception(
        gate_report, grant=operator_path_exception, pr_status=pr_status,
        repo=repo, head=expected_head, base=expected_base_sha, now=now,
    )
    if not gate_report.get("ok", False):
        raise BridgeConsensusMergeReceiptError(
            {
                "decision": "merge_plan_not_receipt_eligible",
                "ok": False,
                "errors": list(gate_report.get("reasons", []))
                or ["merge plan gate did not pass"],
                "gate_report": gate_report,
                "exit_code": 2,
            }
        )
    bridge_consensus = gate_report.get("bridge_consensus")
    if not isinstance(bridge_consensus, Mapping) or not bridge_consensus.get("ok"):
        raise BridgeConsensusMergeReceiptError(
            {
                "decision": "bridge_consensus_not_verified",
                "ok": False,
                "errors": ["bridge consensus was not verified"],
                "gate_report": gate_report,
                "exit_code": 2,
            }
        )

    payload = _receipt_payload(
        pr_status=pr_status,
        gate_report=gate_report,
        bridge_consensus=bridge_consensus,
        expected_head=expected_head,
        expected_base_sha=expected_base_sha,
        consensus_proposal_id=consensus_proposal_id,
        repo=repo,
        manifest_path=manifest_path,
        now_utc=now,
    )
    receipt_bundle = _write_receipt_bundle(
        payload=payload,
        bridge_consensus=bridge_consensus,
        out_dir=out_dir,
        now_utc=now,
    )
    return {
        "decision": "bridge_consensus_merge_receipt_written",
        "ok": True,
        "receipt_bundle_path": receipt_bundle["manifest"],
        "receipt_bundle": receipt_bundle,
        "gate_report": gate_report,
    }


def _validated_now_utc(value: object) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if type(value) is not datetime:
        raise BridgeConsensusMergeReceiptError(
            {
                "decision": "invalid_input",
                "ok": False,
                "errors": [
                    "now_utc must be a timezone-aware datetime or null"
                ],
                "exit_code": 2,
            }
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise BridgeConsensusMergeReceiptError(
            {
                "decision": "invalid_input",
                "ok": False,
                "errors": ["now_utc must be timezone-aware"],
                "exit_code": 2,
            }
        )
    return value.astimezone(timezone.utc)


def _merge_plan_report(
    *,
    pr_status: Mapping[str, Any],
    events_path: Path,
    expected_head: str,
    expected_base_sha: str,
    consensus_proposal_id: str,
    receipt_bundle_path: str,
    repo: str,
    from_agent: str,
    bridge_task_id: str,
    now_utc: datetime,
    review_policy: str = REVIEW_POLICY_LEGACY,
    grok_fallback: bool = False,
    grok_reports_root: Path | None = None,
) -> dict[str, Any]:
    rule12_kwargs: dict[str, Any] = (
        {"review_policy": review_policy, "now_utc": now_utc}
        if review_policy == REVIEW_POLICY_RULE12
        else {}
    )
    if grok_fallback:
        rule12_kwargs["grok_fallback"] = True
        if grok_reports_root is not None:
            rule12_kwargs["_grok_reports_root"] = grok_reports_root
    try:
        return evaluate_auto_merge_gate(
            pr_status=pr_status,
            expected_head=expected_head,
            expected_base_sha=expected_base_sha,
            consensus_proposal_id=consensus_proposal_id,
            receipt_bundle_path=receipt_bundle_path,
            events_path=events_path,
            utc_date=now_utc.date().isoformat(),
            repo=repo,
            from_agent=from_agent,
            bridge_task_id=bridge_task_id,
            apply=False,
            require_bridge_consensus=True,
            **rule12_kwargs,
        )
    except AutoMergeGateError as exc:
        return dict(exc.report)


def _receipt_payload(
    *,
    pr_status: Mapping[str, Any],
    gate_report: Mapping[str, Any],
    bridge_consensus: Mapping[str, Any],
    expected_head: str,
    expected_base_sha: str,
    consensus_proposal_id: str,
    repo: str,
    manifest_path: Path,
    now_utc: datetime,
) -> dict[str, Any]:
    payload = {
        "artifact_version": "wd.bridge_consensus_merge_receipt.v0",
        "created_at_utc": _iso(now_utc),
        "repo": repo,
        "pr_number": gate_report.get("pr_number"),
        "pr_title": gate_report.get("pr_title"),
        "task_id": consensus_proposal_id,
        "head_sha": expected_head,
        "base_sha": expected_base_sha,
        "merge_command": gate_report.get("gh_command", []),
        "gate_decision": gate_report.get("decision"),
        "gate_reasons": list(gate_report.get("reasons", [])),
        "path_gate": dict(gate_report.get("path_gate", {})),
        "operator_path_exception": gate_report.get("operator_path_exception"),
        "original_gate": gate_report.get("original_gate"),
        "diff_gate": dict(gate_report.get("diff_gate", {})),
        "base_gate": dict(gate_report.get("base_gate", {})),
        "receipt_gate": dict(gate_report.get("receipt_gate", {})),
        "rco_pass_gate": dict(gate_report.get("rco_pass_gate", {})),
        "bridge_peer_gate": dict(gate_report.get("bridge_peer_gate", {})),
        "bridge_consensus": dict(bridge_consensus),
        "rate_gate": dict(gate_report.get("rate_gate", {})),
        "checks": list(pr_status.get("checks", [])),
        "changed_paths": list(pr_status.get("changed_paths", [])),
        "diff_digest": sha256_digest(str(pr_status.get("diff_text", ""))),
        "receipt_manifest_planned": str(manifest_path),
    }
    if _is_rule12(bridge_consensus):
        # Rule 12 receipts name the policy and keep the evaluator report so a
        # consumer can re-derive the slot holders; legacy payloads are unchanged.
        payload["review_policy"] = REVIEW_POLICY_RULE12
        payload["rule12_review"] = {
            "now_utc": bridge_consensus.get("now_utc"),
            "contributors": list(bridge_consensus.get("contributors") or []),
            "rco_pass_refs": list(bridge_consensus.get("rco_pass_refs") or []),
            "evaluation": bridge_consensus.get("rule12"),
        }
        evidence = gate_report.get("grok_fallback_evidence")
        if isinstance(evidence, Mapping):
            # The full Grok tuple the merge executor's fresh gate must reproduce
            # (plan v3 B5); a consumer re-derives it from the ledger request_id.
            payload["rule12_review"]["grok_fallback"] = dict(evidence)
    return payload


def _grok_evidence(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    review = payload.get("rule12_review")
    evidence = review.get("grok_fallback") if isinstance(review, Mapping) else None
    return evidence if isinstance(evidence, Mapping) else None


def _is_rule12(bridge_consensus: Mapping[str, Any]) -> bool:
    return bridge_consensus.get("review_policy") == REVIEW_POLICY_RULE12


def _write_receipt_bundle(
    *,
    payload: Mapping[str, Any],
    bridge_consensus: Mapping[str, Any],
    out_dir: Path,
    now_utc: datetime,
) -> dict[str, Any]:
    pr_number = str(payload.get("pr_number", "unknown"))
    head = str(payload["head_sha"])
    rule12 = _is_rule12(bridge_consensus)
    consensus_verifier = (
        "bridge_rule12_review_eligibility" if rule12 else "verify_bridge_consensus"
    )
    consensus_reason = (
        "rule12:best_available_consensus"
        if rule12
        else "bridge_consensus:three_identity_head_bound"
    )
    grok_evidence = _grok_evidence(payload) if rule12 else None
    rco_reason_codes = (
        ["rco:pass_present"]
        if not rule12 or bridge_consensus.get("rco_pass_ref") is not None
        else []
    )
    if grok_evidence is not None:
        rco_reason_codes = ["rco:grok_fallback"]
    rco_decision_basis: Any = (
        list(bridge_consensus.get("rco_pass_refs") or [])
        if rule12
        else bridge_consensus["rco_pass_ref"]
    )
    if grok_evidence is not None:
        rco_decision_basis = {
            "rco_pass_refs": rco_decision_basis,
            "grok_fallback": dict(grok_evidence),
        }
    evaluation = build_evaluation_result(
        case_id=f"case:bridge_consensus_merge:pr{pr_number}",
        subject_type="promotion",
        target_payload=dict(payload),
        risk_class="external_effect",
        expected_gate="require_approval",
        actual_gate="allow",
        verifier_path=[
            "idle_consensus_auto_merge_gate",
            consensus_verifier,
            *(["rule12_grok_ledger_adapter"] if grok_evidence is not None else []),
            "check_rco_pass_present",
            "check_bridge_changes_requested",
            "magma_receipt_verifier_v1",
        ],
        solver_selection=_solver_selection(bridge_consensus, grok_evidence),
        policy_version=(
            "policy:rule12_best_available_consensus_v1"
            if rule12
            else "policy:bridge_consensus_approval_v1"
        ),
        charter_version="charter:idle_autonomy_v1",
        domain_threshold_version="threshold:autonomous_merge:v1",
        verdict="pass",
        reason_codes=[
            consensus_reason,
            "ci:green",
            ("path_gate:explicit_operator_exception" if payload.get("operator_path_exception")
             else "path_gate:allowlist_clean"),
            *rco_reason_codes,
            "merge:match_head_commit",
        ],
        confidence_score=1.0,
        allow_external_effect=True,
    )
    receipt = build_magma_receipt(
        event_id=f"magma:bridge_consensus_merge:pr{pr_number}",
        ts_utc=_iso(now_utc),
        risk_class="external_effect",
        payload=dict(payload),
        evaluation_result=evaluation,
        policy_digest=sha256_digest(
            {
                "policy_version": evaluation["policy_version"],
                "path_gate": payload["path_gate"],
                "diff_gate": payload["diff_gate"],
            }
        ),
        charter_digest=sha256_digest(
            {
                "charter_version": evaluation["charter_version"],
                "task_id": payload["task_id"],
                "base_sha": payload["base_sha"],
            }
        ),
        rco_decision_digest=sha256_digest(rco_decision_basis),
        world_snapshot_digest=sha256_digest(
            {
                "repo": payload["repo"],
                "pr_number": payload["pr_number"],
                "head_sha": payload["head_sha"],
                "base_sha": payload["base_sha"],
                "checks": payload["checks"],
            }
        ),
        solver_contract_digest=sha256_digest(
            {
                "verifier_path": evaluation["verifier_path"],
                "solver_selection": evaluation["solver_selection"],
            }
        ),
        approval_id=f"bridge:consensus:pr{pr_number}:{head[:12]}",
        allow_external_effect=True,
    )
    return write_receipt_bundle(
        out_dir=out_dir,
        chain_id=f"magma:bridge_consensus_merge:pr{pr_number}:{head[:12]}:v0",
        entries=[
            ReceiptBundleEntry(
                label="merge",
                payload=dict(payload),
                evaluation_result=evaluation,
                receipt=receipt,
            )
        ],
        verify_manifest=verify_manifest,
    )


def _solver_selection(
    bridge_consensus: Mapping[str, Any],
    grok_evidence: Mapping[str, Any] | None = None,
) -> list[str]:
    if _is_rule12(bridge_consensus):
        identities = bridge_consensus.get("identities")
        holders: list[str] = []
        if isinstance(identities, Mapping):
            opposite = identities.get("opposite_family")
            if isinstance(opposite, Mapping):
                holders.extend(str(agent) for agent in opposite.get("holders") or [])
        holders.extend(
            str(ref.get("agent", ""))
            for ref in bridge_consensus.get("rco_pass_refs") or []
            if isinstance(ref, Mapping)
        )
        if grok_evidence is not None:
            holders.append(str(grok_evidence.get("reviewer", "")))
        return [agent for agent in holders if agent] or [
            "bridge_rule12_review_eligibility"
        ]
    identities = bridge_consensus.get("identities")
    if not isinstance(identities, Mapping):
        return ["verify_bridge_consensus"]
    result: list[str] = []
    for role in ("build_lead", "build_tools", "rco"):
        row = identities.get(role)
        if isinstance(row, Mapping):
            agent = str(row.get("agent", ""))
            if agent:
                result.append(agent)
    return result or ["verify_bridge_consensus"]


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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
