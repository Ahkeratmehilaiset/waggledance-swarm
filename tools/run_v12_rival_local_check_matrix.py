# SPDX-License-Identifier: BUSL-1.1
"""Build the V12 rival-side local-check matrix.

This tool makes the competitor-axis pilot's rival-local-check blocker
machine-readable. It deliberately does not install rival SDKs or execute
untrusted rival commands. Future local checks can be recorded as pinned evidence
manifests under an evidence directory, and this tool validates whether those
manifests are sufficient to upgrade a rival row from "not configured" to
"passed".
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PILOT_JSON = (
    ROOT / "docs" / "benchmarks" / "2026_05_20_competitor_axis_pilot.json"
)
REPORT_VERSION = "wd.v12.rival_local_check_matrix.v0"
EVIDENCE_MANIFEST_CONTRACT_VERSION = "wd.v12.rival_local_evidence_manifest.v1"
EVIDENCE_ARTIFACT_CONTRACT_VERSION = "wd.v12.rival_local_evidence_artifact.v1"
REQUIRED_EVIDENCE_FIELDS = (
    "evidence_manifest_contract_version",
    "rival",
    "pinned_revision",
    "local_artifact_path",
    "local_artifact_sha256",
    "smoke_command",
    "smoke_result",
    "cloud_dependency",
    "evidence_type",
)
REQUIRED_EVIDENCE_ARTIFACT_FIELDS = (
    "evidence_artifact_contract_version",
    "rival",
    "pinned_revision",
    "smoke_result",
    "offline",
    "ok",
    "evidence_type",
    "observations",
)
PASSING_SMOKE_RESULT = "passed"
ALLOWED_EVIDENCE_TYPES = {"local_inspection", "local_smoke"}
REQUIRED_OBSERVATIONS_BY_RIVAL = {
    "JamJet": ("policy_audit_or_replay_smoke",),
    "Asqav": ("local_sign_or_hash_chain_smoke",),
    "Microsoft AGT": ("policy_deny_smoke", "fail_closed_error_path_smoke"),
    "Preloop": ("mcp_allow_deny_approval_smoke",),
}
# Per-rival public-doc-claim surface assessment (sourced from
# docs/benchmarks/2026_05_20_competitor_axis_pilot.md and from upstream
# repository verification on 2026-05-23). Drives the specific blocker
# reported in the matrix when no honest local-installable surface
# exists for a rival. This is a HARD invariant against overclaim: a
# rival flagged "no_local_installable_surface_yet" cannot be promoted
# to passing even if a synthetic manifest is supplied later -- the
# registry must be updated first with proof of an installable surface.
#
# Upstream evidence (2026-05-23 verification pass, see
# iterations/codex_scout_tasks/jamjet_preloop_oss_surface_scout_2026_05_23.md):
#   - JamJet: github.com/jamjet-labs/jamjet, Apache-2.0, latest tag
#     python-sdk-v0.8.6 (2026-05-19), PyPI 'jamjet'. README:
#     "Hosted control plane available at app.jamjet.dev ... Optional.
#      The runtime, both SDKs, and Engram are Apache-2.0 with no usage
#      limits." -> open_source_installable.
#   - Preloop: github.com/preloop/preloop, Apache-2.0, latest tag v0.9.3
#     (2026-05-19), PyPI 'preloop'. README: "All shipped as Apache 2.0
#      software that runs on your infrastructure." -> open_source_installable.
#   - Microsoft AGT: open-source (MIT) governance runtime, unchanged.
#   - Asqav: PyPI-installable signer + cloud-dependent provenance
#     headline, unchanged.
PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL = {
    "JamJet": "open_source_installable",
    "Preloop": "open_source_installable",
    "Microsoft AGT": "open_source_installable",
    "Asqav": "pypi_installable_cloud_dependent_headline",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report the V12 competitor pilot's rival-side local-check status "
            "without installing SDKs or running cloud-dependent checks."
        ),
    )
    parser.add_argument(
        "--pilot-json",
        type=Path,
        default=DEFAULT_PILOT_JSON,
        help="Competitor-axis pilot JSON to read.",
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        help=(
            "Optional directory containing pinned rival evidence manifests "
            "named by rival slug, e.g. microsoft-agt.json."
        ),
    )
    parser.add_argument(
        "--init-evidence-dir",
        type=Path,
        default=None,
        help=(
            "Write non-passing rival evidence manifest templates to this "
            "directory, then report that evidence directory. Existing "
            "manifest files are not overwritten unless --overwrite-templates "
            "is also set."
        ),
    )
    parser.add_argument(
        "--overwrite-templates",
        action="store_true",
        help="Allow --init-evidence-dir to overwrite existing template manifests.",
    )
    parser.add_argument(
        "--markdown-out",
        type=Path,
        default=None,
        help="Optional markdown report path to write.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic output.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        template_init = None
        evidence_dir = args.evidence_dir
        if args.init_evidence_dir is not None:
            template_init = write_evidence_manifest_templates(
                pilot_json_path=args.pilot_json,
                evidence_dir=args.init_evidence_dir,
                overwrite=args.overwrite_templates,
            )
            evidence_dir = args.init_evidence_dir
        report = build_rival_local_check_matrix(
            pilot_json_path=args.pilot_json,
            evidence_dir=evidence_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
        if template_init is not None:
            report["template_init"] = template_init
    except ValueError as exc:
        print(f"rival local check matrix FAILED: {exc}", file=sys.stderr)
        return 1

    markdown = render_markdown(report)
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown, encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(markdown, end="")
    return 0


def build_rival_local_check_matrix(
    *,
    pilot_json_path: Path = DEFAULT_PILOT_JSON,
    evidence_dir: Path | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    pilot_json_path = pilot_json_path.resolve()
    if not pilot_json_path.exists():
        raise ValueError(f"pilot_json does not exist: {pilot_json_path}")
    try:
        pilot = json.loads(pilot_json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"pilot_json is not valid JSON: {exc}") from exc

    checks = pilot.get("rival_side_local_checks_required")
    if not isinstance(checks, list) or not checks:
        raise ValueError("pilot_json has no rival_side_local_checks_required list")

    evidence_root = evidence_dir.resolve() if evidence_dir else None
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows = [
        _build_check_row(check, evidence_root=evidence_root)
        for check in checks
    ]
    passed_count = sum(1 for row in rows if row["local_status"] == "passed")
    required_count = len(rows)
    blocked_count = required_count - passed_count
    consensus_grade = required_count > 0 and passed_count == required_count

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": generated_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "ok": True,
        "pilot_json": str(pilot_json_path),
        "pilot_status": pilot.get("status"),
        "pilot_consensus_grade_before": bool(pilot.get("consensus_grade")),
        "strategy_identity": pilot.get("strategy_identity"),
        "evidence_dir": str(evidence_root) if evidence_root else None,
        "evidence_dir_exists": bool(evidence_root and evidence_root.exists()),
        "required_count": required_count,
        "passed_count": passed_count,
        "blocked_count": blocked_count,
        "consensus_grade": consensus_grade,
        "rival_local_checks_status": (
            f"{passed_count}/{required_count} rival local checks passed"
        ),
        "no_overclaim_guardrails": {
            "not_a_competitor_benchmark": True,
            "does_not_install_rival_sdks": True,
            "does_not_execute_untrusted_rival_commands": True,
            "public_doc_claims_remain_public_doc_claims_until_local_evidence_passes": True,
            "requires_machine_readable_offline_artifact": True,
        },
        "evidence_manifest_contract_version": EVIDENCE_MANIFEST_CONTRACT_VERSION,
        "evidence_artifact_contract_version": EVIDENCE_ARTIFACT_CONTRACT_VERSION,
        "required_evidence_fields": list(REQUIRED_EVIDENCE_FIELDS),
        "required_evidence_artifact_fields": list(REQUIRED_EVIDENCE_ARTIFACT_FIELDS),
        "required_observations_by_rival": {
            rival: list(observations)
            for rival, observations in REQUIRED_OBSERVATIONS_BY_RIVAL.items()
        },
        "checks": rows,
    }


def write_evidence_manifest_templates(
    *,
    pilot_json_path: Path = DEFAULT_PILOT_JSON,
    evidence_dir: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    pilot_json_path = pilot_json_path.resolve()
    if not pilot_json_path.exists():
        raise ValueError(f"pilot_json does not exist: {pilot_json_path}")
    try:
        pilot = json.loads(pilot_json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"pilot_json is not valid JSON: {exc}") from exc

    checks = pilot.get("rival_side_local_checks_required")
    if not isinstance(checks, list) or not checks:
        raise ValueError("pilot_json has no rival_side_local_checks_required list")

    evidence_root = evidence_dir.resolve()
    evidence_root.mkdir(parents=True, exist_ok=True)
    existing = []
    template_paths = []
    for check in checks:
        rival = str(check.get("rival") or "unknown")
        slug = _slugify(rival)
        manifest_path = evidence_root / f"{slug}.json"
        if manifest_path.exists():
            existing.append(str(manifest_path))
        template_paths.append((manifest_path, _build_manifest_template(check)))
    if existing and not overwrite:
        raise ValueError(
            "template manifest already exists; use --overwrite-templates to replace: "
            + ", ".join(existing)
        )

    written = []
    for manifest_path, template in template_paths:
        manifest_path.write_text(
            json.dumps(template, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        written.append(str(manifest_path))

    return {
        "evidence_dir": str(evidence_root),
        "created_count": len(written),
        "overwrite_requested": overwrite,
        "overwrote_existing": bool(existing),
        "overwritten_count": len(existing),
        "manifest_paths": written,
        "safe_defaults": {
            "smoke_result": "not_run",
            "consensus_grade_contribution": False,
            "requires_local_artifact_digest_before_pass": True,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# V12 Rival Local Check Matrix",
        "",
        f"- report_version: `{report['report_version']}`",
        f"- evidence_manifest_contract_version: `{report['evidence_manifest_contract_version']}`",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- pilot_status: `{report.get('pilot_status')}`",
        f"- consensus_grade: `{str(report['consensus_grade']).lower()}`",
        f"- rival local checks passed: `{report['passed_count']}/{report['required_count']}`",
        f"- blocked rival local checks: `{report['blocked_count']}`",
        "",
        "This is not a competitor benchmark. It is a local evidence gate for the",
        "competitor-axis pilot. Rows remain non-consensus-grade until pinned local",
        "evidence manifests prove a rival-side smoke or inspection without a cloud",
        "dependency.",
        "",
        "| Rival | Local status | Evidence manifest | Blocker | Required check |",
        "|---|---|---|---|---|",
    ]
    for row in report["checks"]:
        manifest = row.get("evidence_manifest") or "-"
        blocker = row.get("blocker") or "-"
        lines.append(
            "| {rival} | {status} | `{manifest}` | {blocker} | {check} |".format(
                rival=_md(row["rival"]),
                status=_md(row["local_status"]),
                manifest=_md(manifest),
                blocker=_md(blocker),
                check=_md(row["required_check"]),
            )
        )
    lines.extend(
        [
            "",
            "## Required Evidence Manifest Fields",
            "",
            ", ".join(f"`{field}`" for field in REQUIRED_EVIDENCE_FIELDS),
            "",
            "A row only passes when `cloud_dependency=false`, `smoke_result=passed`,",
            "`evidence_type` is `local_inspection` or `local_smoke`, and all required",
            "fields are present. The `local_artifact_path` must name an existing",
            "file under the evidence directory and `local_artifact_sha256` must",
            "match that UTF-8 JSON artifact after CRLF/CR newlines are",
            "normalized to LF. The artifact itself must be a machine-readable",
            "offline evidence JSON whose rival, pinned revision, evidence type,",
            "pass status, and rival-specific observations match the manifest.",
            "",
            "## Required Evidence Artifact Fields",
            "",
            ", ".join(f"`{field}`" for field in REQUIRED_EVIDENCE_ARTIFACT_FIELDS),
            "",
            "## Required Rival Observations",
            "",
            ", ".join(
                f"`{rival}: {', '.join(observations)}`"
                for rival, observations in REQUIRED_OBSERVATIONS_BY_RIVAL.items()
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _build_check_row(
    check: dict[str, Any],
    *,
    evidence_root: Path | None,
) -> dict[str, Any]:
    rival = str(check.get("rival") or "unknown")
    required_check = str(check.get("check") or "")
    slug = _slugify(rival)
    manifest_path = evidence_root / f"{slug}.json" if evidence_root else None
    base = {
        "rival": rival,
        "rival_slug": slug,
        "required_check": required_check,
        "pilot_status": check.get("status", "unknown"),
        "expected_manifest_name": f"{slug}.json",
        "evidence_manifest": str(manifest_path) if manifest_path else None,
        "consensus_grade_contribution": False,
    }
    # Anti-overclaim early exit (hard invariant per Codex RCO on #607):
    # rivals whose public-doc-claim surface has no local-installable
    # component cannot be promoted past not_configured regardless of
    # any manifest someone might supply. Even a synthetic manifest
    # with cloud_dependency=false and a "passing" smoke_result MUST
    # NOT contribute to consensus_grade. Updating
    # PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL is the only legitimate channel
    # to unblock these rivals -- it forces the operator/agent to first
    # prove the installable surface exists.
    if PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL.get(rival) == "no_local_installable_surface_yet":
        return {
            **base,
            "local_status": "not_configured",
            "blocker": "no_local_installable_surface_yet",
        }
    if manifest_path is None:
        return {
            **base,
            "local_status": "not_configured",
            "blocker": "no evidence_dir provided",
        }
    if not manifest_path.exists():
        return {
            **base,
            "local_status": "not_configured",
            "blocker": "evidence manifest missing",
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            **base,
            "local_status": "invalid_manifest",
            "blocker": f"manifest JSON parse error: {exc.msg}",
        }

    missing = [
        field
        for field in REQUIRED_EVIDENCE_FIELDS
        if _is_missing_required_value(manifest.get(field))
    ]
    if missing:
        return {
            **base,
            "local_status": "invalid_manifest",
            "blocker": "missing required fields: " + ", ".join(missing),
        }
    if str(manifest.get("rival")) != rival:
        return {
            **base,
            "local_status": "invalid_manifest",
            "blocker": "manifest rival does not match pilot row",
        }
    if manifest.get("evidence_manifest_contract_version") != EVIDENCE_MANIFEST_CONTRACT_VERSION:
        return {
            **base,
            "local_status": "invalid_manifest",
            "blocker": "evidence_manifest_contract_version does not match v1",
        }
    if manifest.get("cloud_dependency") is not False:
        # Cloud-dependent rivals do NOT contribute to consensus_grade
        # (the headline feature requires a cloud service). But if the
        # manifest declares a local artifact AND that artifact exists,
        # is valid UTF-8 JSON, and matches the manifest digest, we
        # surface artifact_digest_verified=true so the operator can
        # see audit-traceable local proof without mistaking it for
        # a consensus-grade contribution. We deliberately apply only
        # the LIGHTWEIGHT digest + UTF-8 + JSON-parse check; the
        # full evidence-artifact contract validation is reserved
        # for passing local checks (consensus-grade-contributing).
        return {
            **base,
            "local_status": "cloud_dependent",
            "blocker": "cloud_dependency is not false",
            "blocked_artifact_reason": "cloud_dependency",
            "consensus_grade_contribution": False,
            "artifact_proof": _lightweight_artifact_proof(
                evidence_root=evidence_root,
                manifest=manifest,
            ),
        }
    if str(manifest.get("evidence_type")) not in ALLOWED_EVIDENCE_TYPES:
        return {
            **base,
            "local_status": "invalid_manifest",
            "blocker": "evidence_type is not local_inspection or local_smoke",
        }
    if str(manifest.get("smoke_result")) != PASSING_SMOKE_RESULT:
        return {
            **base,
            "local_status": "not_passed",
            "blocker": "smoke_result is not passed",
            "blocked_artifact_reason": "smoke_result",
            "consensus_grade_contribution": False,
            "artifact_proof": _lightweight_artifact_proof(
                evidence_root=evidence_root,
                manifest=manifest,
            ),
        }
    artifact_result = _validate_local_artifact(
        evidence_root=evidence_root,
        local_artifact_path=str(manifest.get("local_artifact_path")),
        expected_digest=str(manifest.get("local_artifact_sha256")),
        manifest=manifest,
    )
    if artifact_result["blocker"]:
        return {
            **base,
            "local_status": "invalid_artifact",
            "blocker": artifact_result["blocker"],
        }
    return {
        **base,
        "local_status": "passed",
        "blocker": None,
        "pinned_revision": manifest.get("pinned_revision"),
        "local_artifact_path": artifact_result["path"],
        "local_artifact_sha256": artifact_result["sha256"],
        "evidence_artifact_contract_version": artifact_result["contract_version"],
        "evidence_type": manifest.get("evidence_type"),
        "consensus_grade_contribution": True,
    }


def _build_manifest_template(check: dict[str, Any]) -> dict[str, Any]:
    rival = str(check.get("rival") or "unknown")
    slug = _slugify(rival)
    return {
        "evidence_manifest_contract_version": EVIDENCE_MANIFEST_CONTRACT_VERSION,
        "rival": rival,
        "pinned_revision": "TODO_PINNED_REVISION",
        "local_artifact_path": f"artifacts/{slug}-evidence.json",
        "local_artifact_sha256": "sha256:" + ("0" * 64),
        "smoke_command": "TODO_OFFLINE_LOCAL_COMMAND",
        "smoke_result": "not_run",
        "cloud_dependency": False,
        "evidence_type": "local_inspection",
        "expected_artifact": {
            "evidence_artifact_contract_version": EVIDENCE_ARTIFACT_CONTRACT_VERSION,
            "rival": rival,
            "pinned_revision": "same as manifest pinned_revision",
            "smoke_result": PASSING_SMOKE_RESULT,
            "offline": True,
            "ok": True,
            "evidence_type": "same as manifest evidence_type",
            "observations": {
                observation: {
                    "ok": True,
                    "offline": True,
                    "summary": "TODO_OBSERVATION_SUMMARY",
                }
                for observation in REQUIRED_OBSERVATIONS_BY_RIVAL.get(rival, ())
            },
        },
        "notes": (
            "Template only. Replace TODO values and write the local artifact "
            "under evidence_dir before setting smoke_result to passed."
        ),
    }


def _lightweight_artifact_proof(
    *,
    evidence_root: Path | None,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Run digest + UTF-8 + JSON-parse checks ONLY -- no contract validation.

    Used by the cloud-dependent rival branch to surface audit-traceable
    proof of a local artifact without claiming consensus-grade. Returns
    ``{artifact_digest_verified: bool, artifact_digest_reason: str | None,
    local_artifact_path?: str, local_artifact_sha256?: str}``.
    """

    base: dict[str, Any] = {
        "artifact_digest_verified": False,
        "artifact_digest_reason": None,
    }
    local_artifact_path = manifest.get("local_artifact_path")
    expected_digest = str(manifest.get("local_artifact_sha256", ""))
    if not local_artifact_path:
        base["artifact_digest_reason"] = (
            "manifest does not declare local_artifact_path"
        )
        return base
    if evidence_root is None:
        base["artifact_digest_reason"] = "no evidence_dir provided"
        return base

    rel = Path(local_artifact_path)
    if rel.is_absolute():
        base["artifact_digest_reason"] = (
            "local_artifact_path must be relative to evidence_dir"
        )
        return base

    artifact_path = (evidence_root / rel).resolve()
    try:
        artifact_path.relative_to(evidence_root)
    except ValueError:
        base["artifact_digest_reason"] = (
            "local_artifact_path escapes evidence_dir"
        )
        return base
    if not artifact_path.exists() or not artifact_path.is_file():
        base["artifact_digest_reason"] = (
            "local_artifact_path does not name an existing file"
        )
        return base

    payload = artifact_path.read_bytes()
    try:
        artifact_text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        base["artifact_digest_reason"] = (
            f"local artifact is not valid UTF-8 JSON: {exc}"
        )
        return base

    actual_digest = _canonical_json_artifact_sha256(artifact_text)
    if expected_digest != actual_digest:
        base["artifact_digest_reason"] = (
            "local_artifact_sha256 does not match artifact"
        )
        base["local_artifact_path"] = str(artifact_path)
        base["local_artifact_sha256"] = actual_digest
        return base

    try:
        json.loads(artifact_text)
    except json.JSONDecodeError as exc:
        base["artifact_digest_reason"] = (
            f"local artifact is not valid UTF-8 JSON: {exc}"
        )
        base["local_artifact_path"] = str(artifact_path)
        base["local_artifact_sha256"] = actual_digest
        return base

    base["artifact_digest_verified"] = True
    base["local_artifact_path"] = str(artifact_path)
    base["local_artifact_sha256"] = actual_digest
    return base


def _validate_local_artifact(
    *,
    evidence_root: Path | None,
    local_artifact_path: str,
    expected_digest: str,
    manifest: dict[str, Any],
) -> dict[str, str | None]:
    if evidence_root is None:
        return _artifact_error("no evidence_dir provided")

    rel = Path(local_artifact_path)
    if rel.is_absolute():
        return _artifact_error(
            "local_artifact_path must be relative to evidence_dir",
        )

    artifact_path = (evidence_root / rel).resolve()
    try:
        artifact_path.relative_to(evidence_root)
    except ValueError:
        return _artifact_error("local_artifact_path escapes evidence_dir")
    if not artifact_path.exists() or not artifact_path.is_file():
        return _artifact_error(
            "local_artifact_path does not name an existing file",
            path=str(artifact_path),
        )

    payload = artifact_path.read_bytes()
    try:
        artifact_text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _artifact_error(
            f"local artifact is not valid UTF-8 JSON: {exc}",
            path=str(artifact_path),
        )

    actual_digest = _canonical_json_artifact_sha256(artifact_text)
    if expected_digest != actual_digest:
        return _artifact_error(
            "local_artifact_sha256 does not match artifact",
            path=str(artifact_path),
            sha256=actual_digest,
        )
    try:
        artifact = json.loads(artifact_text)
    except json.JSONDecodeError as exc:
        return _artifact_error(
            f"local artifact is not valid UTF-8 JSON: {exc}",
            path=str(artifact_path),
            sha256=actual_digest,
        )
    payload_error = _validate_artifact_payload(
        artifact=artifact,
        manifest=manifest,
    )
    if payload_error:
        return _artifact_error(
            payload_error,
            path=str(artifact_path),
            sha256=actual_digest,
        )
    return {
        "blocker": None,
        "path": str(artifact_path),
        "sha256": actual_digest,
        "contract_version": str(artifact["evidence_artifact_contract_version"]),
    }


def _artifact_error(
    blocker: str,
    *,
    path: str | None = None,
    sha256: str | None = None,
) -> dict[str, str | None]:
    return {
        "blocker": blocker,
        "path": path,
        "sha256": sha256,
        "contract_version": None,
    }


def _canonical_json_artifact_sha256(text: str) -> str:
    canonical_text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()


def _validate_artifact_payload(
    *,
    artifact: Any,
    manifest: dict[str, Any],
) -> str | None:
    if not isinstance(artifact, dict):
        return "local artifact JSON must be an object"

    missing = [
        field
        for field in REQUIRED_EVIDENCE_ARTIFACT_FIELDS
        if _is_missing_required_value(artifact.get(field))
    ]
    if missing:
        return "local artifact missing required fields: " + ", ".join(missing)
    if artifact.get("evidence_artifact_contract_version") != EVIDENCE_ARTIFACT_CONTRACT_VERSION:
        return "evidence_artifact_contract_version does not match v1"
    if artifact.get("rival") != manifest.get("rival"):
        return "local artifact rival does not match manifest"
    if artifact.get("pinned_revision") != manifest.get("pinned_revision"):
        return "local artifact pinned_revision does not match manifest"
    if artifact.get("evidence_type") != manifest.get("evidence_type"):
        return "local artifact evidence_type does not match manifest"
    if artifact.get("smoke_result") != PASSING_SMOKE_RESULT:
        return "local artifact smoke_result is not passed"
    if artifact.get("offline") is not True:
        return "local artifact offline is not true"
    if artifact.get("ok") is not True:
        return "local artifact ok is not true"
    observations = artifact.get("observations")
    if not isinstance(observations, dict):
        return "local artifact observations must be an object"
    required_observations = REQUIRED_OBSERVATIONS_BY_RIVAL.get(
        str(manifest.get("rival")),
        (),
    )
    missing_observations = [
        name
        for name in required_observations
        if name not in observations
    ]
    if missing_observations:
        return "local artifact missing required observations: " + ", ".join(
            missing_observations
        )
    for name in required_observations:
        observation = observations.get(name)
        if not isinstance(observation, dict):
            return f"local artifact observation {name} must be an object"
        if observation.get("ok") is not True:
            return f"local artifact observation {name} ok is not true"
        if observation.get("offline") is not True:
            return f"local artifact observation {name} offline is not true"
    return None


def _is_missing_required_value(value: Any) -> bool:
    return value is None or value == ""


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "unknown"


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("--now requires a UTC timestamp with Z or +00:00 suffix")
    return parsed.astimezone(timezone.utc)


def _md(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
