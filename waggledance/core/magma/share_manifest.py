# SPDX-License-Identifier: BUSL-1.1
"""Operator-gated MAGMA share manifest exporter.

The exporter is a local artifact bridge from a verified MAGMA receipt bundle to
the payload-free ``magma.share_manifest.v0`` contract. It does not enable
runtime receipt emission or cross-instance transport.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
from typing import Any

import jsonschema

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.schema_validation import redacted_schema_errors


ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DIR = ROOT / "schemas" / "v3_13_0"
SCHEMA_NAME = "magma_share_manifest.v0.json"
MANIFEST_VERSION = "magma.share_manifest.v0"
EXPORT_REPORT_VERSION = "magma.share_manifest_export.v0"
IMPORT_REPORT_VERSION = "magma.share_manifest_import.v0"
SHARE_MANIFEST_NAME = "share_manifest.json"
EXPORT_REPORT_NAME = "share_export_report.json"
DEFAULT_IMPORT_MAX_AGE_HOURS = 168
IMPORT_CLOCK_SKEW = timedelta(minutes=5)
FORBIDDEN_MATERIAL = (
    "raw_payload",
    "replacement_map",
    "raw_context",
    "raw_solver_output",
    "raw_query_digest",
)
PRODUCER_ROLES = frozenset({"lead", "tools", "rco", "operator"})
PURPOSES = frozenset(
    {"peer_review", "rco_review", "cross_instance_replay", "operator_archive"}
)
_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9:._-]{2,180}$")


VerifySourceManifest = Callable[[Path], Mapping[str, Any]]


def build_magma_share_manifest(
    *,
    source_manifest_path: Path,
    verify_source_manifest: VerifySourceManifest,
    share_id: str,
    producer_agent_id: str,
    producer_role: str,
    bridge_event_ref: str,
    purpose: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Build a schema-valid payload-free MAGMA share manifest.

    ``verify_source_manifest`` must be the local offline receipt verifier or a
    test double with the same fail-closed shape. The source payload files are
    not copied or referenced in the emitted share manifest.
    """
    _ensure_ref("share_id", share_id)
    _ensure_ref("producer_agent_id", producer_agent_id)
    _ensure_ref("bridge_event_ref", bridge_event_ref)
    if producer_role not in PRODUCER_ROLES:
        raise ValueError("producer_role is not allowed")
    if purpose not in PURPOSES:
        raise ValueError("purpose is not allowed")

    source_manifest_path = source_manifest_path.resolve()
    verification = dict(verify_source_manifest(source_manifest_path))
    if verification.get("ok") is not True:
        error_count = len(list(verification.get("errors") or []))
        raise ValueError(
            "source receipt manifest verification failed"
            f" ({error_count} redacted errors)"
        )

    source_manifest = _read_json(source_manifest_path, "source manifest")
    source_entries = _load_source_entries(source_manifest_path, source_manifest)
    if int(verification.get("receipt_count", 0) or 0) != len(source_entries):
        raise ValueError("source receipt manifest count mismatch")

    entries: list[dict[str, Any]] = []
    for index, entry in enumerate(source_entries, 1):
        receipt = _read_json(entry["receipt"], f"entry {index} receipt")
        evaluation = _read_json(
            entry["evaluation_result"],
            f"entry {index} evaluation_result",
        )
        if not isinstance(receipt, dict) or not isinstance(evaluation, dict):
            raise ValueError(f"entry {index}: receipt/evaluation must be objects")
        _ensure_receipt_evaluation_consistency(receipt, evaluation, index)
        entries.append(_share_entry(share_id, index, receipt, evaluation))

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "share_id": share_id,
        "created_at_utc": _format_utc(now_utc or datetime.now(timezone.utc)),
        "producer": {
            "agent_id": producer_agent_id,
            "role": producer_role,
            "bridge_event_ref": bridge_event_ref,
        },
        "purpose": purpose,
        "runtime_export_enabled": False,
        "sanitized_source_manifest_digest": sha256_digest(source_manifest),
        "export_policy": {
            "contract": "sanitization_v0",
            "payload_visibility": "no_payload",
            "allow_payload_digests": False,
            "allow_raw_payloads": False,
            "allow_replacement_maps": False,
            "allow_raw_context": False,
            "allow_raw_solver_outputs": False,
            "allow_deterministic_query_digests": False,
        },
        "artifact_counts": {
            "entries": len(entries),
            "receipts": len(entries),
            "evaluation_results": len(entries),
            "payload_files": 0,
        },
        "forbidden_material_absent": list(FORBIDDEN_MATERIAL),
        "entries": entries,
    }
    validate_magma_share_manifest(manifest)
    return manifest


def write_magma_share_manifest_export(
    *,
    source_manifest_path: Path,
    out_dir: Path,
    operator_approval_id: str,
    verify_source_manifest: VerifySourceManifest,
    share_id: str,
    producer_agent_id: str,
    producer_role: str,
    bridge_event_ref: str,
    purpose: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Write an explicitly operator-gated local share manifest export."""
    _ensure_ref("operator_approval_id", operator_approval_id)
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")

    manifest = build_magma_share_manifest(
        source_manifest_path=source_manifest_path,
        verify_source_manifest=verify_source_manifest,
        share_id=share_id,
        producer_agent_id=producer_agent_id,
        producer_role=producer_role,
        bridge_event_ref=bridge_event_ref,
        purpose=purpose,
        now_utc=now_utc,
    )
    validate_magma_share_manifest(manifest)

    out_dir.mkdir(parents=False, exist_ok=False)
    share_manifest_path = out_dir / SHARE_MANIFEST_NAME
    _write_json(share_manifest_path, manifest)

    report = {
        "report_version": EXPORT_REPORT_VERSION,
        "ok": True,
        "blockers": [],
        "operator_gate_required": True,
        "operator_gate_satisfied": True,
        "operator_approval_id_recorded": False,
        "operator_approval_ref": "<redacted>",
        "runtime_export_enabled": False,
        "default_runtime_receipt_emission_changed": False,
        "payload_files_exported": 0,
        "payload_digest_exported": False,
        "replacement_map_exported": False,
        "source_manifest": str(source_manifest_path.resolve()),
        "share_manifest": str(share_manifest_path),
        "share_manifest_digest": sha256_digest(manifest),
        "artifact_counts": dict(manifest["artifact_counts"]),
        "forbidden_material_absent": list(FORBIDDEN_MATERIAL),
    }
    _write_json(out_dir / EXPORT_REPORT_NAME, report)
    return report


def build_magma_share_manifest_import_report(
    *,
    share_manifest_path: Path,
    source_manifest_path: Path,
    verify_source_manifest: VerifySourceManifest,
    now_utc: datetime | None = None,
    max_age_hours: int = DEFAULT_IMPORT_MAX_AGE_HOURS,
    expected_share_id: str | None = None,
    expected_purpose: str | None = None,
) -> dict[str, Any]:
    """Verify a share manifest as no-authority replay metadata.

    The importer does not copy payloads, grant runtime authority, or rebuild a
    receipt bundle. It proves that the payload-free share manifest is fresh
    enough for local review and that its digest/categorical references still
    match a separately supplied local receipt-bundle manifest.
    """
    if max_age_hours <= 0:
        raise ValueError("max_age_hours must be positive")
    if expected_share_id is not None:
        _ensure_ref("expected_share_id", expected_share_id)
    if expected_purpose is not None and expected_purpose not in PURPOSES:
        raise ValueError("expected_purpose is not allowed")

    now = _ensure_utc(now_utc or datetime.now(timezone.utc), "now_utc")
    share_manifest_path = share_manifest_path.resolve()
    source_manifest_path = source_manifest_path.resolve()

    share_manifest = _read_json(share_manifest_path, "share manifest")
    if not isinstance(share_manifest, dict):
        raise ValueError("share manifest must be a JSON object")
    validate_magma_share_manifest(share_manifest)
    if (
        expected_share_id is not None
        and share_manifest.get("share_id") != expected_share_id
    ):
        raise ValueError("expected_share_id mismatch")
    if (
        expected_purpose is not None
        and share_manifest.get("purpose") != expected_purpose
    ):
        raise ValueError("expected_purpose mismatch")

    created_at = _parse_created_at_utc(share_manifest)
    age = now - created_at
    max_age = timedelta(hours=max_age_hours)
    if age < -IMPORT_CLOCK_SKEW:
        raise ValueError("share manifest timestamp is in the future")
    if age > max_age:
        raise ValueError("share manifest is stale")

    verification = dict(verify_source_manifest(source_manifest_path))
    if verification.get("ok") is not True:
        error_count = len(list(verification.get("errors") or []))
        raise ValueError(
            "source receipt manifest verification failed"
            f" ({error_count} redacted errors)"
        )

    source_manifest = _read_json(source_manifest_path, "source manifest")
    source_entries = _load_source_entries(source_manifest_path, source_manifest)
    if int(verification.get("receipt_count", 0) or 0) != len(source_entries):
        raise ValueError("source receipt manifest count mismatch")
    if (
        sha256_digest(source_manifest)
        != share_manifest["sanitized_source_manifest_digest"]
    ):
        raise ValueError("sanitized_source_manifest_digest context drift")

    share_entries = share_manifest["entries"]
    if len(source_entries) != len(share_entries):
        raise ValueError("share manifest entry count context drift")

    replay_entries: list[dict[str, Any]] = []
    for index, (share_entry, source_entry) in enumerate(
        zip(share_entries, source_entries, strict=True),
        1,
    ):
        receipt = _read_json(source_entry["receipt"], f"entry {index} receipt")
        evaluation = _read_json(
            source_entry["evaluation_result"],
            f"entry {index} evaluation_result",
        )
        if (
            not isinstance(receipt, dict)
            or not isinstance(evaluation, dict)
            or not isinstance(share_entry, dict)
        ):
            raise ValueError(f"entry {index}: replay references must be objects")
        _ensure_receipt_evaluation_consistency(receipt, evaluation, index)
        _ensure_share_entry_context(share_entry, receipt, evaluation, index)
        replay_entries.append(
            {
                "entry_id": share_entry["entry_id"],
                "receipt_digest": share_entry["receipt_digest"],
                "evaluation_result_digest": share_entry[
                    "evaluation_result_digest"
                ],
                "subject_type": share_entry["subject_type"],
                "risk_class": share_entry["risk_class"],
                "expected_gate": share_entry["expected_gate"],
                "actual_gate": share_entry["actual_gate"],
                "verdict": share_entry["verdict"],
            }
        )

    return {
        "report_version": IMPORT_REPORT_VERSION,
        "ok": True,
        "blockers": [],
        "share_id": share_manifest["share_id"],
        "purpose": share_manifest["purpose"],
        "created_at_utc": share_manifest["created_at_utc"],
        "max_age_hours": max_age_hours,
        "age_seconds": max(0, int(age.total_seconds())),
        "share_manifest_digest": sha256_digest(share_manifest),
        "source_manifest_digest": sha256_digest(source_manifest),
        "source_receipt_verification_ok": True,
        "context_verified": True,
        "context_drift_detected": False,
        "replay_metadata_only": True,
        "no_authority_import": True,
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
        "artifact_counts": dict(share_manifest["artifact_counts"]),
        "replay_plan": {
            "mode": "no_authority_metadata_replay",
            "entry_count": len(replay_entries),
            "entries": replay_entries,
        },
    }


def validate_magma_share_manifest(value: dict[str, Any]) -> None:
    """Validate schema, date-time formats, and count invariants."""
    errors = redacted_schema_errors(
        _share_manifest_validator(),
        value,
        "magma_share_manifest",
    )
    errors.extend(_date_time_errors(value))
    errors.extend(_artifact_count_errors(value))
    if errors:
        raise ValueError("; ".join(errors))


def _share_manifest_validator() -> jsonschema.Draft7Validator:
    schema = json.loads((SCHEMA_DIR / SCHEMA_NAME).read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )


def _artifact_count_errors(value: Mapping[str, Any]) -> list[str]:
    counts = value.get("artifact_counts")
    entries = value.get("entries")
    if not isinstance(counts, Mapping) or not isinstance(entries, list):
        return []
    expected = len(entries)
    errors: list[str] = []
    for field in ("entries", "receipts", "evaluation_results"):
        if counts.get(field) != expected:
            errors.append(f"magma_share_manifest: count mismatch at artifact_counts.{field}")
    if counts.get("payload_files") != 0:
        errors.append("magma_share_manifest: count mismatch at artifact_counts.payload_files")
    return errors


def _date_time_errors(value: Mapping[str, Any]) -> list[str]:
    raw = value.get("created_at_utc")
    if not isinstance(raw, str):
        return []
    try:
        parsed = _parse_created_at_utc(value)
    except ValueError:
        return ["magma_share_manifest: schema error at created_at_utc"]
    return []


def _load_source_entries(
    manifest_path: Path,
    manifest: Any,
) -> list[dict[str, Path]]:
    if not isinstance(manifest, dict):
        raise ValueError("source manifest must be a JSON object")
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("source manifest entries must be a non-empty array")

    loaded: list[dict[str, Path]] = []
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"source entry {index} must be an object")
        loaded.append(
            {
                "receipt": _entry_path(manifest_path, entry, "receipt", index),
                "evaluation_result": _entry_path(
                    manifest_path,
                    entry,
                    "evaluation_result",
                    index,
                ),
            }
        )
    return loaded


def _entry_path(
    manifest_path: Path,
    entry: Mapping[str, Any],
    field: str,
    index: int,
) -> Path:
    raw_path = entry.get(field)
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(f"source entry {index}: missing {field}")
    if "\\" in raw_path:
        raise ValueError(f"source entry {index}: {field} path must use POSIX separators")
    if (
        PurePosixPath(raw_path).is_absolute()
        or PureWindowsPath(raw_path).is_absolute()
    ):
        raise ValueError(f"source entry {index}: {field} path must be relative")
    parts = raw_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"source entry {index}: {field} unsafe relative path")
    path = (manifest_path.parent / Path(*parts)).resolve()
    try:
        path.relative_to(manifest_path.parent)
    except ValueError as exc:
        raise ValueError(f"source entry {index}: {field} path escapes manifest") from exc
    return path


def _share_entry(
    share_id: str,
    index: int,
    receipt: Mapping[str, Any],
    evaluation: Mapping[str, Any],
) -> dict[str, Any]:
    redaction_count = 0
    sanitization = evaluation.get("sanitization_audit")
    if isinstance(sanitization, Mapping):
        redaction_count = int(sanitization.get("redaction_count", 0) or 0)
    return {
        "entry_id": f"{share_id}:entry:{index:03d}",
        "receipt_digest": sha256_digest(receipt),
        "evaluation_result_digest": sha256_digest(evaluation),
        "subject_type": evaluation["subject_type"],
        "risk_class": evaluation["risk_class"],
        "expected_gate": evaluation["expected_gate"],
        "actual_gate": evaluation["actual_gate"],
        "verdict": evaluation["verdict"],
        "sanitization": {
            "contract": "sanitization_v0",
            "redaction_count": redaction_count,
            "raw_material_removed": list(FORBIDDEN_MATERIAL),
            "payload_digest_exported": False,
            "replacement_map_exported": False,
        },
    }


def _ensure_receipt_evaluation_consistency(
    receipt: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    index: int,
) -> None:
    if receipt.get("evaluation_result_digest") != sha256_digest(evaluation):
        raise ValueError(f"entry {index}: evaluation_result_digest mismatch")
    if receipt.get("risk_class") != evaluation.get("risk_class"):
        raise ValueError(f"entry {index}: risk_class mismatch")


def _ensure_share_entry_context(
    share_entry: Mapping[str, Any],
    receipt: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    index: int,
) -> None:
    if share_entry.get("receipt_digest") != sha256_digest(receipt):
        raise ValueError(f"entry {index}: receipt_digest context drift")
    if share_entry.get("evaluation_result_digest") != sha256_digest(evaluation):
        raise ValueError(f"entry {index}: evaluation_result_digest context drift")
    for field in (
        "subject_type",
        "risk_class",
        "expected_gate",
        "actual_gate",
        "verdict",
    ):
        if share_entry.get(field) != evaluation.get(field):
            raise ValueError(f"entry {index}: {field} context drift")


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"{label}: cannot read JSON file ({exc.__class__.__name__})") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}: invalid JSON at line {exc.lineno} column {exc.colno}") from exc


def _ensure_ref(label: str, value: str) -> None:
    if not _REF_RE.fullmatch(value):
        raise ValueError(f"{label} must be a MAGMA ref")


def _format_utc(value: datetime) -> str:
    return _ensure_utc(value, "timestamp").replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def _ensure_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def _parse_created_at_utc(value: Mapping[str, Any]) -> datetime:
    raw = value.get("created_at_utc")
    if not isinstance(raw, str):
        raise ValueError("created_at_utc must be a string")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at_utc must be a UTC date-time") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise ValueError("created_at_utc must be UTC")
    return parsed.astimezone(timezone.utc)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
