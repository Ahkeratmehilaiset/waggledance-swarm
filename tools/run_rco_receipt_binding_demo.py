# SPDX-License-Identifier: BUSL-1.1
"""Build a local RCO decision artifact bound into a MAGMA receipt."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.evaluation_result import build_evaluation_result  # noqa: E402
from waggledance.core.magma.rco_decision_artifact import (  # noqa: E402
    build_rco_decision_artifact,
    validate_rco_decision_artifact,
)
from waggledance.core.magma.receipt import build_magma_receipt  # noqa: E402


DEMO_VERSION = "magma.rco_receipt_binding_demo.v0"
PRIVATE_MARKER = "operator_rco_secret_marker_DO_NOT_LEAK"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and verify a local RCO decision receipt-binding demo.",
    )
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--now",
        default="2026-05-20T12:00:00Z",
        help="UTC timestamp for deterministic output.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_rco_receipt_binding_demo(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now),
        )
    except ValueError as exc:
        print(f"RCO receipt binding demo FAILED: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            "RCO receipt binding demo OK: "
            f"{report['binding_report']['receipt_count']} receipt in {report['out_dir']}"
        )
    return 0


def build_rco_receipt_binding_demo(
    *,
    out_dir: Path,
    now_utc: datetime,
) -> dict[str, Any]:
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=False)

    # This local-only marker must never enter any emitted artifact.
    _private_operator_note = {"secret": PRIVATE_MARKER}
    write_payload = {
        "artifact_path": "docs/runs/example-local-report.md",
        "operation": "write_text",
        "content_digest": sha256_digest({"redacted_content": "example"}),
    }
    intent = {
        "intent_id": "intent:rco-demo:001",
        "agent_id": "codex",
        "session_id": "session:v12-rco-demo",
        "tool_descriptor_id": "tool:local_artifact_writer",
        "target_state_ref": "state:filesystem_artifact:demo",
        "action": "write",
        "payload_digest": sha256_digest(write_payload),
    }
    rco_decision = build_rco_decision_artifact(
        decision_id="rco:decision:demo:001",
        ts_utc=_iso(now_utc),
        intent=intent,
        write_payload=write_payload,
        risk_class="local_artifact",
        gate_decision="review",
        approved=False,
        operator_required=False,
        policy_version="policy:write_rco_gate:v1",
        charter_version="charter:v1",
        scope_policy_decision="requires_operator",
        peer_rco_verdict="pass",
        verifier_path=[
            "write_rco_gate_v1",
            "rco_decision_artifact_v0",
            "magma_receipt_v1",
        ],
        reason_codes=[
            "rco:local_artifact_requires_review",
            "rco:payload_digest_only",
        ],
        audit_event_ids=[
            "write.intent_classified:demo:001",
            "write.scope_policy_decided:demo:001",
        ],
    )
    evaluation = build_evaluation_result(
        case_id="case:rco:receipt-binding:001",
        subject_type="policy",
        target_payload=intent,
        risk_class=rco_decision["risk_class"],
        expected_gate="review",
        actual_gate=rco_decision["gate_decision"],
        verifier_path=[
            "rco_decision_artifact_v0",
            "magma_evaluation_result_v0",
            "magma_receipt_v1",
            "offline_receipt_verifier",
        ],
        solver_selection=[],
        policy_version=rco_decision["policy_version"],
        charter_version=rco_decision["charter_version"],
        domain_threshold_version="threshold:write_rco_gate:v1",
        verdict="review",
        reason_codes=list(rco_decision["reason_codes"]),
        confidence_score=0.88,
        uncertainty_sources=[
            {
                "kind": "operator_override",
                "detail": "Local artifact write remains review-gated in this demo.",
            }
        ],
    )
    receipt = build_magma_receipt(
        event_id="magma:rco_receipt_binding:001",
        ts_utc=_iso(now_utc),
        risk_class=rco_decision["risk_class"],
        payload=intent,
        evaluation_result=evaluation,
        policy_digest=sha256_digest({"policy_version": rco_decision["policy_version"]}),
        charter_digest=sha256_digest({"charter_version": rco_decision["charter_version"]}),
        rco_decision_digest=sha256_digest(rco_decision),
        world_snapshot_digest=sha256_digest({"world": "rco-demo", "version": 0}),
        solver_contract_digest=sha256_digest({"solver_selection": []}),
    )

    _write_json(out_dir / "intent-001.json", intent)
    _write_json(out_dir / "rco-decision-001.json", rco_decision)
    _write_json(out_dir / "evaluation-001.json", evaluation)
    _write_json(out_dir / "receipt-001.json", receipt)
    manifest = {
        "chain_id": "magma:rco_receipt_binding:v0",
        "entries": [
            {
                "payload": "intent-001.json",
                "rco_decision_artifact": "rco-decision-001.json",
                "evaluation_result": "evaluation-001.json",
                "receipt": "receipt-001.json",
            }
        ],
    }
    _write_json(out_dir / "manifest.json", manifest)
    binding_report = verify_rco_receipt_binding(out_dir / "manifest.json")

    return {
        "demo_version": DEMO_VERSION,
        "writes_applied": False,
        "out_dir": str(out_dir),
        "manifest": str(out_dir / "manifest.json"),
        "rco_decision_digest": sha256_digest(rco_decision),
        "binding_report": binding_report,
    }


def verify_rco_receipt_binding(manifest_path: Path) -> dict[str, Any]:
    """Verify receipt basics plus rco_decision_digest artifact binding."""
    manifest_path = manifest_path.resolve()
    errors: list[str] = []
    verifier_report = verify_manifest(manifest_path)
    errors.extend(str(error) for error in verifier_report.get("errors", []))
    manifest = _read_json_object(manifest_path, errors, "manifest")
    entries = manifest.get("entries", []) if manifest is not None else []
    verified = 0
    if manifest is not None and (not isinstance(entries, list) or not entries):
        errors.append("manifest: entries must be a non-empty array")
        entries = []
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            errors.append(f"entry {index}: must be an object")
            continue
        rco_name = entry.get("rco_decision_artifact")
        receipt_name = entry.get("receipt")
        evaluation_name = entry.get("evaluation_result")
        if not all(
            isinstance(value, str) and value
            for value in (rco_name, receipt_name, evaluation_name)
        ):
            errors.append(
                f"entry {index}: missing "
                "rco_decision_artifact/receipt/evaluation_result"
            )
            continue
        rco_path = _entry_path(
            manifest_path,
            str(rco_name),
            "rco_decision_artifact",
            errors,
            index,
        )
        receipt_path = _entry_path(
            manifest_path, str(receipt_name), "receipt", errors, index
        )
        evaluation_path = _entry_path(
            manifest_path,
            str(evaluation_name),
            "evaluation_result",
            errors,
            index,
        )
        if rco_path is None or receipt_path is None or evaluation_path is None:
            continue
        if (
            not rco_path.is_file()
            or not receipt_path.is_file()
            or not evaluation_path.is_file()
        ):
            errors.append(f"entry {index}: referenced JSON artifact missing")
            continue

        rco_artifact = _read_json_object(
            rco_path,
            errors,
            f"entry {index}: rco_decision_artifact",
        )
        receipt = _read_json_object(receipt_path, errors, f"entry {index}: receipt")
        evaluation = _read_json_object(
            evaluation_path,
            errors,
            f"entry {index}: evaluation_result",
        )
        if rco_artifact is None or receipt is None or evaluation is None:
            continue
        try:
            validate_rco_decision_artifact(rco_artifact)
        except ValueError as exc:
            errors.append(f"entry {index}: {exc}")
            continue
        if receipt.get("rco_decision_digest") != sha256_digest(rco_artifact):
            errors.append(f"entry {index}: rco_decision_digest mismatch")
        if receipt.get("risk_class") != rco_artifact.get("risk_class"):
            errors.append(f"entry {index}: receipt risk_class does not match RCO artifact")
        if evaluation.get("actual_gate") != rco_artifact.get("gate_decision"):
            errors.append(f"entry {index}: evaluation actual_gate does not match RCO artifact")
        verified += 1
    deduped_errors = _dedupe_errors(errors)
    return {
        "ok": not deduped_errors,
        "receipt_count": int(verifier_report.get("receipt_count", 0)),
        "rco_artifact_count": verified,
        "errors": deduped_errors,
    }


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("--now requires a UTC timestamp with Z or +00:00 suffix")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json_object(
    path: Path,
    errors: list[str],
    label: str,
) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        errors.append(f"{label}: cannot read JSON file ({exc.__class__.__name__})")
        return None
    except json.JSONDecodeError as exc:
        errors.append(f"{label}: invalid JSON at line {exc.lineno} column {exc.colno}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{label}: must be a JSON object")
        return None
    return value


def _dedupe_errors(errors: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for error in errors:
        if error in seen:
            continue
        seen.add(error)
        deduped.append(error)
    return deduped


def _entry_path(
    manifest_path: Path,
    raw_path: str,
    field: str,
    errors: list[str],
    index: int,
) -> Path | None:
    context = f"entry {index}: {field}"
    if "\\" in raw_path:
        errors.append(f"{context} path must use POSIX separators")
        return None
    if (
        PurePosixPath(raw_path).is_absolute()
        or PureWindowsPath(raw_path).is_absolute()
    ):
        errors.append(f"{context} path must be relative")
        return None
    parts = PurePosixPath(raw_path).parts
    if any(part in {"", ".", ".."} for part in parts):
        errors.append(f"{context} unsafe relative path")
        return None

    path = (manifest_path.parent / Path(*parts)).resolve()
    try:
        path.relative_to(manifest_path.parent)
    except ValueError:
        errors.append(f"{context} path escapes manifest directory")
        return None
    return path


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
