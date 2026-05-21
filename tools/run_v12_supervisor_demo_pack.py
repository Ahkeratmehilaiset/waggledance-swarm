# SPDX-License-Identifier: BUSL-1.1
"""Build a local V12 evidence pack for operator/supervisor demos."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.magma_receipt_adoption_report import (  # noqa: E402
    build_adoption_report,
    render_markdown as render_adoption_markdown,
)
from tools.run_magma_adversarial_eval import build_adversarial_eval_report  # noqa: E402
from tools.run_v12_a3_counterfactual_axis_proof import (  # noqa: E402
    build_a3_counterfactual_axis_proof,
    render_markdown as render_a3_axis_markdown,
)
from tools.run_v12_a4_solver_growth_axis_proof import (  # noqa: E402
    build_a4_solver_growth_axis_proof,
    render_markdown as render_a4_axis_markdown,
)
from tools.run_v12_rival_local_check_matrix import (  # noqa: E402
    build_rival_local_check_matrix,
    render_markdown as render_rival_matrix_markdown,
    write_evidence_manifest_templates,
)
from tools.verify_magma_receipt import verify_manifest  # noqa: E402


DEMO_VERSION = "wd.v12.supervisor_demo_pack.v0"
ARTIFACT_MANIFEST_VERSION = "wd.v12.supervisor_demo_pack.artifact_manifest.v0"
ARTIFACT_MANIFEST_NAME = "demo_pack_artifact_manifest.json"
DEFAULT_NOW = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Write a local WD V12 evidence pack: adversarial eval, MAGMA "
            "receipt bundle, offline verifier report, adoption report, and "
            "human-readable summary."
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="New output directory for the demo pack. It must not already exist.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic receipt output.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = build_demo_pack(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else DEFAULT_NOW,
        )
    except ValueError as exc:
        print(f"v12 supervisor demo pack FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"v12 supervisor demo pack OK: {result['out_dir']}")
        print(f"summary: {result['summary']}")
        print(f"receipt verifier ok: {result['receipt_verifier_ok']}")
    return 0


def build_demo_pack(*, out_dir: Path, now_utc: datetime | None = None) -> dict[str, Any]:
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")

    out_dir.mkdir()
    receipt_dir = out_dir / "adversarial_receipts"
    adversarial_report = build_adversarial_eval_report(
        receipt_out_dir=receipt_dir,
        now_utc=now_utc or datetime.now(timezone.utc),
    )
    verifier_report = verify_manifest(receipt_dir / "manifest.json")
    if not verifier_report.get("ok", False):
        raise ValueError("receipt verifier failed for generated demo pack")

    adoption_report = build_adoption_report(root=ROOT)
    adoption_markdown = render_adoption_markdown(adoption_report)
    a3_proof = build_a3_counterfactual_axis_proof(
        receipt_out_dir=out_dir / "a3_counterfactual_receipts",
        now_utc=now_utc,
    )
    a3_markdown = render_a3_axis_markdown(a3_proof)
    a4_proof = build_a4_solver_growth_axis_proof(
        out_dir=out_dir / "a4_solver_growth_axis",
        now_utc=now_utc,
    )
    a4_markdown = render_a4_axis_markdown(a4_proof)
    rival_template_dir = out_dir / "rival_evidence_templates"
    rival_template_init = write_evidence_manifest_templates(
        evidence_dir=rival_template_dir,
    )
    rival_matrix = build_rival_local_check_matrix(
        evidence_dir=rival_template_dir,
        now_utc=now_utc,
    )
    rival_matrix["template_init"] = rival_template_init
    rival_matrix_markdown = render_rival_matrix_markdown(rival_matrix)

    _write_json(out_dir / "adversarial_eval_report.json", adversarial_report)
    _write_json(out_dir / "receipt_verifier_report.json", verifier_report)
    _write_json(out_dir / "receipt_adoption_report.json", adoption_report)
    _write_json(out_dir / "a3_counterfactual_axis_proof.json", a3_proof)
    _write_json(out_dir / "a4_solver_growth_axis_proof.json", a4_proof)
    _write_json(out_dir / "rival_evidence_template_init.json", rival_template_init)
    _write_json(out_dir / "rival_local_check_matrix.json", rival_matrix)
    _write_text(out_dir / "receipt_adoption_report.md", adoption_markdown)
    _write_text(out_dir / "a3_counterfactual_axis_proof.md", a3_markdown)
    _write_text(out_dir / "a4_solver_growth_axis_proof.md", a4_markdown)
    _write_text(out_dir / "rival_local_check_matrix.md", rival_matrix_markdown)
    artifact_file_count = len(_pack_files_for_manifest(out_dir)) + 1
    summary = _summary_markdown(
        adversarial_report=adversarial_report,
        adoption_report=adoption_report,
        verifier_report=verifier_report,
        a3_proof=a3_proof,
        a4_proof=a4_proof,
        rival_matrix=rival_matrix,
        rival_template_init=rival_template_init,
        artifact_file_count=artifact_file_count,
    )
    _write_text(out_dir / "summary.md", summary)
    artifact_manifest = _build_artifact_manifest(
        out_dir=out_dir,
        generated_at_utc=now_utc or datetime.now(timezone.utc),
    )
    _write_json(out_dir / ARTIFACT_MANIFEST_NAME, artifact_manifest)

    return {
        "demo_version": DEMO_VERSION,
        "out_dir": str(out_dir),
        "summary": str(out_dir / "summary.md"),
        "artifact_manifest": str(out_dir / ARTIFACT_MANIFEST_NAME),
        "artifact_manifest_file_count": artifact_manifest["file_count"],
        "adversarial_case_count": adversarial_report["case_count"],
        "adversarial_pass_count": adversarial_report["pass_count"],
        "writes_applied": adversarial_report["writes_applied"],
        "receipt_verifier_ok": bool(verifier_report["ok"]),
        "receipt_count": verifier_report["receipt_count"],
        "high_criticality_gap_count": adoption_report["high_criticality_gap_count"],
        "status_counts": adoption_report["status_counts"],
        "a3_counterfactual_delta_proven": a3_proof["counterfactual_delta_proven"],
        "a3_receipt_chain_verified": a3_proof["receipt_chain_verified"],
        "a4_solver_growth_proven": a4_proof["solver_growth_proven"],
        "a4_receipt_chain_verified": a4_proof["receipt_chain_verified"],
        "a4_dispatch_success_count": a4_proof["dispatch"]["dispatch_success_count"],
        "a4_dispatch_case_count": a4_proof["dispatch"]["dispatch_case_count"],
        "a4_registered_solver_count": a4_proof["registration"]["registered_solver_count"],
        "rival_local_check_pass_count": rival_matrix["passed_count"],
        "rival_local_check_required_count": rival_matrix["required_count"],
        "competitor_consensus_grade": rival_matrix["consensus_grade"],
        "rival_evidence_template_count": rival_template_init["created_count"],
        "rival_evidence_template_dir": str(rival_template_dir),
    }


def _summary_markdown(
    *,
    adversarial_report: dict[str, Any],
    adoption_report: dict[str, Any],
    verifier_report: dict[str, Any],
    a3_proof: dict[str, Any],
    a4_proof: dict[str, Any],
    rival_matrix: dict[str, Any],
    rival_template_init: dict[str, Any],
    artifact_file_count: int,
) -> str:
    action_required = adoption_report.get("action_required_gap_count", "not_available")
    accepted_exceptions = adoption_report.get("accepted_exception_count", "not_available")
    return "\n".join(
        [
            "# WD V12 Supervisor Demo Pack",
            "",
            f"- demo_version: `{DEMO_VERSION}`",
            f"- adversarial eval: `{adversarial_report['pass_count']}/{adversarial_report['case_count']}` cases passed",
            f"- full matches: `{adversarial_report['full_match_count']}`",
            f"- writes_applied: `{str(adversarial_report['writes_applied']).lower()}`",
            f"- receipt verifier ok: `{str(verifier_report['ok']).lower()}`",
            f"- receipt count: `{verifier_report['receipt_count']}`",
            f"- high criticality adoption gaps: `{adoption_report['high_criticality_gap_count']}`",
            f"- action-required adoption gaps: `{action_required}`",
            f"- accepted observability exceptions: `{accepted_exceptions}`",
            f"- adoption status counts: `{json.dumps(adoption_report['status_counts'], sort_keys=True)}`",
            f"- A3 counterfactual delta proven: `{str(a3_proof['counterfactual_delta_proven']).lower()}`",
            f"- A3 receipt chain verified: `{str(a3_proof['receipt_chain_verified']).lower()}`",
            f"- A4 solver growth proven: `{str(a4_proof['solver_growth_proven']).lower()}`",
            f"- A4 dispatch success: `{a4_proof['dispatch']['dispatch_success_count']}/{a4_proof['dispatch']['dispatch_case_count']}`",
            f"- A4 receipt chain verified: `{str(a4_proof['receipt_chain_verified']).lower()}`",
            f"- rival local checks passed: `{rival_matrix['passed_count']}/{rival_matrix['required_count']}`",
            f"- competitor consensus grade: `{str(rival_matrix['consensus_grade']).lower()}`",
            f"- rival evidence templates: `{rival_template_init['created_count']}` safe non-passing manifests",
            f"- artifact manifest: `{ARTIFACT_MANIFEST_NAME}`",
            f"- artifact manifest file count: `{artifact_file_count}`",
            "",
            "## What This Proves",
            "",
            "WD can run a local adversarial corpus, bind the result to EvaluationResult digests, emit a MAGMA receipt bundle, verify the bundle offline without applying writes, and write a local SHA256 manifest for the generated demo artifacts.",
            "",
            "## What This Does Not Prove",
            "",
            "This pack does not claim semantic attack detection by an ML classifier, cryptographic signing, competitor-local benchmark results, or that every runtime observability event emits a receipt. It proves the current local evidence spine.",
            "",
            "## Files",
            "",
            "- `adversarial_eval_report.json`",
            "- `adversarial_receipts/manifest.json`",
            "- `receipt_verifier_report.json`",
            "- `receipt_adoption_report.json`",
            "- `receipt_adoption_report.md`",
            "- `a3_counterfactual_axis_proof.json`",
            "- `a3_counterfactual_axis_proof.md`",
            "- `a3_counterfactual_receipts/manifest.json`",
            "- `a4_solver_growth_axis_proof.json`",
            "- `a4_solver_growth_axis_proof.md`",
            "- `a4_solver_growth_axis/a4_solver_growth_receipts/manifest.json`",
            "- `rival_evidence_template_init.json`",
            "- `rival_evidence_templates/*.json`",
            "- `rival_local_check_matrix.json`",
            "- `rival_local_check_matrix.md`",
            f"- `{ARTIFACT_MANIFEST_NAME}`",
        ]
    ) + "\n"


def _build_artifact_manifest(
    *,
    out_dir: Path,
    generated_at_utc: datetime,
) -> dict[str, Any]:
    files = []
    for path in _pack_files_for_manifest(out_dir):
        rel = path.relative_to(out_dir).as_posix()
        payload = path.read_bytes()
        files.append(
            {
                "path": rel,
                "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
        )
    return {
        "manifest_version": ARTIFACT_MANIFEST_VERSION,
        "demo_version": DEMO_VERSION,
        "generated_at_utc": _format_utc(generated_at_utc),
        "file_count": len(files),
        "files": files,
    }


def _pack_files_for_manifest(out_dir: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in out_dir.rglob("*")
            if path.is_file() and path.name != ARTIFACT_MANIFEST_NAME
        ),
        key=lambda path: path.relative_to(out_dir).as_posix(),
    )


def _format_utc(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("--now requires a UTC timestamp with Z or +00:00 suffix")
    return parsed.astimezone(timezone.utc)


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
