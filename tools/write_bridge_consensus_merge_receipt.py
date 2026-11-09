# SPDX-License-Identifier: BUSL-1.1
"""Write a MAGMA receipt for a verified bridge-consensus merge plan.

This helper is deliberately separate from ``idle_consensus_auto_merge``: it
creates the receipt bundle that the merge gate already requires, but it does
not perform the merge or weaken the gate's fail-closed checks.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
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
from tools.bridge_rco_enrollment import using_rco_enrollment_context  # noqa: E402
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


_CARRY_PAYLOAD_KEYS = frozenset({
    "artifact_version", "created_at_utc", "repo", "pr_number", "pr_title", "task_id", "head_sha", "base_sha",
    "merge_command", "gate_decision", "gate_reasons", "path_gate", "operator_path_exception", "original_gate",
    "diff_gate", "base_gate", "receipt_gate", "rco_pass_gate", "bridge_peer_gate", "bridge_consensus", "rate_gate",
    "checks", "changed_paths", "diff_digest", "receipt_manifest_planned", "carry_forward",
})


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
    rco_enrollment_context: Mapping[str, Any] | None = None,
    carry_forward: Mapping[str, Any] | None = None,
    runner=None,
    accepted_queue_checker=None,
) -> dict[str, Any]:
    """Write after unchanged gate checks; optionally inject a loader-issued context.

    Omitted/None keeps the installed manifest reader path. Only Python callers
    can supply a context; there is no CLI flag or environment override for it.
    """
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
    carry_kwargs: dict[str, Any] = {}
    if carry_forward is not None:
        from tools.bridge_approval_carry_forward import derive_live_carry_forward
        live = derive_live_carry_forward(
            request=carry_forward, events_path=events_path, repo=repo, pr_number=pr_status.get("pr_number"),
            task_id=bridge_task_id or consensus_proposal_id, expected_head=expected_head,
            expected_base_sha=expected_base_sha, runner=runner, accepted_queue_checker=accepted_queue_checker)
        if live.get("carry") is not True:
            raise BridgeConsensusMergeReceiptError(dict(ok=False, decision="carry_receipt_refused",
                                                        errors=[live["reason"]], exit_code=2))
        # The caller's paths/checks/diff cannot be copied into a carry receipt.
        pr_status = dict(live["_fresh_snapshot"], receipt_verified=pr_status.get("receipt_verified") is True)
        carry_kwargs = dict(carry_forward=carry_forward, runner=runner,
                            accepted_queue_checker=accepted_queue_checker)
    enrollment_scope = (
        nullcontext() if rco_enrollment_context is None
        else using_rco_enrollment_context(rco_enrollment_context)
    )
    with enrollment_scope:
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
            **carry_kwargs,
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
    if carry_forward is not None:
        from tools.idle_consensus_auto_merge import _read_bridge_events
        payload.update(_historical_carry_verdicts(payload["carry_forward"], _read_bridge_events(events_path)))
        bridge_consensus = payload["bridge_consensus"]
    receipt_bundle = _write_receipt_bundle(
        payload=payload,
        bridge_consensus=bridge_consensus,
        out_dir=out_dir,
        now_utc=now,
    )
    semantic = None
    if carry_forward is not None:
        semantic = verify_carry_forward_merge_receipt(
            manifest_path=Path(receipt_bundle["manifest"]), carry_forward=carry_forward,
            events_path=events_path, repo=repo, pr_number=pr_status["pr_number"],
            task_id=bridge_task_id or consensus_proposal_id, expected_head=expected_head,
            expected_base_sha=expected_base_sha, runner=runner, accepted_queue_checker=accepted_queue_checker)
        if semantic.get("ok") is not True:
            raise BridgeConsensusMergeReceiptError(dict(ok=False, decision="carry_receipt_semantics_refused",
                                                        errors=semantic["errors"], exit_code=2))
    return {
        "decision": "bridge_consensus_merge_receipt_written",
        "ok": True,
        "receipt_bundle_path": receipt_bundle["manifest"],
        "receipt_bundle": receipt_bundle,
        "gate_report": gate_report,
        **({"carry_semantic_verification": semantic} if semantic is not None else {}),
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
    carry_forward: Mapping[str, Any] | None = None,
    runner=None,
    accepted_queue_checker=None,
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
    if carry_forward is not None:
        rule12_kwargs.update(carry_forward=carry_forward, runner=runner,
                             accepted_queue_checker=accepted_queue_checker)
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
    if gate_report.get("carry_forward") is not None:
        from tools.bridge_approval_carry_forward import carry_forward_receipt_material
        payload["artifact_version"] = "wd.bridge_consensus_merge_receipt.v1"
        payload["carry_forward"] = carry_forward_receipt_material(gate_report["carry_forward"])
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
        pool_evidence = gate_report.get("pool_rco_fallback_evidence")
        if isinstance(pool_evidence, Mapping):
            # The pool-holder tuple the merge executor's fresh gate must
            # reproduce (operator 2026-10-10 06:56Z pool fallback).
            payload["rule12_review"]["pool_rco_fallback"] = dict(pool_evidence)
    return payload


def _historical_carry_verdicts(material: Mapping, events: list) -> dict:
    """Rebuild full historical reports from the bound prefix, not latest chatter.

    This does not validate current authority. The caller separately requires
    full live proof verification, including every later veto and current CI.
    """
    from tools.bridge_approval_carry_forward import _canonical_sha
    from tools.idle_consensus_auto_merge import verify_bridge_consensus
    from tools.check_rco_pass_present import DEFAULT_RCO_AGENTS, check_rco_pass_present
    proof = material["approval_receipt"]
    count = proof["accepted_event_count"]
    if (type(count) is not int or not 0 < count <= len(events)
            or _canonical_sha(events[:count]) != proof["accepted_events_sha256"]):
        raise ValueError("historical review report prefix is missing or changed")
    prefix = events[:count]
    consensus = verify_bridge_consensus(events=prefix, task_id=proof["task_id"],
        head_sha=proof["approved_head"], pr_number=proof["pr_number"], author_agent=proof["pr_author"])
    rco = check_rco_pass_present(events=prefix, task_id=proof["task_id"], head=proof["approved_head"],
        rco_agent=DEFAULT_RCO_AGENTS, author_agent=proof["pr_author"])
    if consensus.get("ok") is not True or rco.get("ok") is not True:
        raise ValueError("historical review report has no qualifying original approvals")
    heads = dict(carried_from_head=proof["approved_head"], carried_to_head=proof["proposed_head"])
    return dict(bridge_consensus=dict(consensus, required=True, **heads), rco_pass_gate=dict(rco, **heads))


def verify_carry_forward_merge_receipt(*, manifest_path: Path, carry_forward: Mapping,
                                      events_path: Path, repo: str, pr_number: int, task_id: str,
                                      expected_head: str, expected_base_sha: str,
                                      runner=None, accepted_queue_checker=None) -> dict[str, Any]:
    """Verify the actual MAGMA files and rederive their live carry semantics.

    This is review/receipt evidence, not permission to merge. An executor must
    still run its whole fresh gate, signature/path policy and late base check.
    The reader accepts only this writer's single-entry fixed file layout.
    """
    from tools.bridge_approval_carry_forward import (
        _file_bytes, _trusted_paths, verify_live_carry_forward_receipt,
    )
    try:
        def require(value, reason):
            if not value:
                raise ValueError(reason)
        require(isinstance(manifest_path, Path) and manifest_path.is_absolute()
                and manifest_path.name == "manifest.json" and ".." not in manifest_path.parts,
                "carry receipt manifest must be an absolute fixed-layout path")
        paths = [manifest_path] + [manifest_path.parent / name for name in (
            "payload-001-merge.json", "evaluation-001-merge.json", "receipt-001-merge.json")]
        snapshots = {path: _file_bytes(path, 32 * 1024 * 1024) for path in paths}
        parse = lambda path: json.loads(snapshots[path][0].decode("utf-8"),
                                        object_pairs_hook=_trusted_paths._pairs,
                                        parse_constant=_trusted_paths._constant)
        manifest = parse(manifest_path)
        require(isinstance(manifest, Mapping) and set(manifest) == {"chain_id", "entries"}
                and manifest.get("entries") == [dict(
            payload=paths[1].name, evaluation_result=paths[2].name, receipt=paths[3].name)],
            "carry receipt manifest is not the writer's single-entry layout")
        require(verify_manifest(manifest_path).get("ok") is True, "MAGMA receipt integrity verification failed")
        payload = parse(paths[1])
        require(isinstance(payload, Mapping) and set(payload) == _CARRY_PAYLOAD_KEYS
                and payload.get("artifact_version") == "wd.bridge_consensus_merge_receipt.v1",
                "carry receipt payload schema is missing")
        expected = dict(repo=repo, pr_number=pr_number, task_id=task_id,
                        head_sha=expected_head, base_sha=expected_base_sha)
        require(type(payload.get("pr_number")) is int
                and all(payload.get(key) == value for key, value in expected.items()),
                "carry receipt belongs to another repository/PR/task/head/base")
        semantic = verify_live_carry_forward_receipt(
            material=payload.get("carry_forward"), request=carry_forward, events_path=events_path,
            repo=repo, pr_number=pr_number, task_id=task_id, expected_head=expected_head,
            expected_base_sha=expected_base_sha, runner=runner, accepted_queue_checker=accepted_queue_checker)
        require(semantic.get("ok") is True, "; ".join(semantic.get("reasons", [])) or "live carry proof refused")
        fresh = semantic["_fresh"]
        snapshot = fresh["_fresh_snapshot"]
        require(sha256_digest(payload.get("changed_paths")) == sha256_digest(snapshot["changed_paths"])
                and sha256_digest(payload.get("checks")) == sha256_digest(snapshot["checks"])
                and payload.get("diff_digest") == sha256_digest(str(snapshot["diff_text"])),
                "carry receipt paths/checks/diff do not match current snapshot")
        historical = _historical_carry_verdicts(payload["carry_forward"], fresh["_events"])
        for field, expected_report in historical.items():
            require(sha256_digest(payload.get(field)) == sha256_digest(expected_report),
                    "carry receipt original review report differs from its bound historical prefix")
        require(all(_file_bytes(path, 32 * 1024 * 1024) == before for path, before in snapshots.items()),
                "carry receipt files changed during verification")
        return dict(ok=True, decision="carry_merge_receipt_rederived", head=expected_head, base_ref=snapshot["base_ref"],
                    approved_head=fresh["receipt"]["approved_head"], authority="receipt_evidence_only_no_merge")
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return dict(ok=False, decision="carry_merge_receipt_refused", errors=[str(exc)], authority="none")


def verify_carry_forward_apply(*, manifest_path: Path, carry_forward: Mapping,
                               events_path: Path, repo: str, pr_number: int, task_id: str,
                               expected_head: str, expected_base_sha: str,
                               runner=None, accepted_queue_checker=None) -> dict[str, Any]:
    """Reconsume the real proof and observe the target tip immediately pre-effect.

    The caller must already have passed its full fresh merge gate. GitHub's
    match-head flag protects the PR head, not the base: this late read narrows
    but cannot eliminate the remaining server-side base movement race.
    """
    from tools.pr_status_snapshot import PrStatusSnapshotError, _fetch_base_ref_tip, _github_ci_runner, _run_command
    semantic = verify_carry_forward_merge_receipt(
        manifest_path=manifest_path, carry_forward=carry_forward, events_path=events_path,
        repo=repo, pr_number=pr_number, task_id=task_id, expected_head=expected_head,
        expected_base_sha=expected_base_sha, runner=runner, accepted_queue_checker=accepted_queue_checker)
    if semantic.get("ok") is not True:
        return semantic
    try:
        # The actual consumer returns its freshly derived target ref, not a
        # caller hint or a second, unverified read of the on-disk payload.
        base_ref = semantic["base_ref"]
        base = _fetch_base_ref_tip(run=_github_ci_runner(runner or _run_command, repo=repo),
                                   repo=repo, base_ref_name=base_ref)
        if base != expected_base_sha:
            raise ValueError("target base moved after semantic receipt verification")
        return dict(ok=True, decision="carry_apply_reverified", semantic=semantic,
                    target_base_observed=base, atomic_base_guard=False,
                    limitation="target base can still move between this read and GitHub merge")
    except (PrStatusSnapshotError, ValueError, TypeError, KeyError, OSError) as exc:
        return dict(ok=False, decision="carry_apply_refused", errors=[str(exc)])


def _pool_evidence(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    review = payload.get("rule12_review")
    evidence = review.get("pool_rco_fallback") if isinstance(review, Mapping) else None
    return evidence if isinstance(evidence, Mapping) else None


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
    pool_evidence = _pool_evidence(payload) if rule12 else None
    if grok_evidence is not None:
        rco_reason_codes = ["rco:grok_fallback"]
    elif pool_evidence is not None:
        rco_reason_codes = ["rco:pool_fallback"]
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
    elif pool_evidence is not None:
        rco_decision_basis = {
            "rco_pass_refs": rco_decision_basis,
            "pool_rco_fallback": dict(pool_evidence),
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
