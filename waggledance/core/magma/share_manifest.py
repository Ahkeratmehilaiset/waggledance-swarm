# SPDX-License-Identifier: BUSL-1.1
"""Operator-gated MAGMA share manifest exporter.

The exporter is a local artifact bridge from a verified MAGMA receipt bundle to
the payload-free ``magma.share_manifest.v0`` contract. It does not enable
runtime receipt emission or cross-instance transport.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
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
IMPORT_ADMISSION_CONTRACT_VERSION = "magma.share_manifest_replay_admission_contract.v0"
IMPORT_HANDOFF_VERSION = "magma.share_manifest_import_handoff.v0"
IMPORT_HANDOFF_STATUS_VERSION = "magma.share_manifest_import_handoff_status.v0"
IMPORT_ADMISSION_STATUS_VERSION = "magma.share_manifest_import_admission_status.v0"
SHARE_MANIFEST_NAME = "share_manifest.json"
EXPORT_REPORT_NAME = "share_export_report.json"
IMPORT_HANDOFF_NAME = "share_import_peer_review_handoff.json"
DEFAULT_IMPORT_MAX_AGE_HOURS = 168
DEFAULT_IMPORT_HANDOFF_EXPECTED_PURPOSE = "cross_instance_replay"
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
IMPORT_HANDOFF_DECISIONS = frozenset(
    {
        "accepted_for_peer_review",
        "deferred_for_operator_review",
        "rejected_for_peer_review",
    }
)
IMPORT_HANDOFF_SCOPE = "peer_review_only"
IMPORT_HANDOFF_DECISION_STATUS = {
    "accepted_for_peer_review": "ready_for_peer_review",
    "deferred_for_operator_review": "operator_review_deferred",
    "rejected_for_peer_review": "operator_rejected",
}
IMPORT_HANDOFF_HISTORY_LIMIT = 5
IMPORT_HANDOFF_HISTORY_MAX_SNAPSHOT = 20
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
                "evaluation_result_digest": share_entry["evaluation_result_digest"],
                "subject_type": share_entry["subject_type"],
                "risk_class": share_entry["risk_class"],
                "expected_gate": share_entry["expected_gate"],
                "actual_gate": share_entry["actual_gate"],
                "verdict": share_entry["verdict"],
            }
        )

    contract_expected_share_id = (
        expected_share_id
        if expected_share_id is not None
        else share_manifest["share_id"]
    )
    contract_expected_purpose = (
        expected_purpose if expected_purpose is not None else share_manifest["purpose"]
    )
    admission_contract = _replay_admission_contract(
        max_age_hours=max_age_hours,
        expected_share_id=contract_expected_share_id,
        expected_purpose=contract_expected_purpose,
    )
    return {
        "report_version": IMPORT_REPORT_VERSION,
        "ok": True,
        "blockers": [],
        "admission_contract": admission_contract,
        "admission_contract_digest": sha256_digest(admission_contract),
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


def build_magma_share_import_peer_review_handoff(
    *,
    import_report: Mapping[str, Any],
    operator_decision_id: str,
    operator_agent_id: str,
    bridge_event_ref: str,
    import_decision: str = "accepted_for_peer_review",
    decision_reason_ref: str = "reason:operator_peer_review_handoff",
    expected_purpose: str = DEFAULT_IMPORT_HANDOFF_EXPECTED_PURPOSE,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Build an operator-owned peer-review handoff from an import report.

    The artifact records the operator import decision and digest/categorical
    replay references only. It never copies payloads, records local paths,
    grants runtime authority, or changes runtime export state.
    """
    if not isinstance(expected_purpose, str) or expected_purpose not in PURPOSES:
        raise ValueError("expected_purpose is not allowed")
    _ensure_import_report_ready_for_handoff(
        import_report,
        expected_purpose=expected_purpose,
    )
    _ensure_ref("operator_decision_id", operator_decision_id)
    _ensure_ref("operator_agent_id", operator_agent_id)
    _ensure_ref("bridge_event_ref", bridge_event_ref)
    _ensure_ref("decision_reason_ref", decision_reason_ref)
    if import_decision not in IMPORT_HANDOFF_DECISIONS:
        raise ValueError("import_decision is not allowed")

    replay_plan = import_report["replay_plan"]
    entries = replay_plan["entries"]
    replay_refs = [
        {
            "entry_id": entry["entry_id"],
            "receipt_digest": entry["receipt_digest"],
            "evaluation_result_digest": entry["evaluation_result_digest"],
            "subject_type": entry["subject_type"],
            "risk_class": entry["risk_class"],
            "expected_gate": entry["expected_gate"],
            "actual_gate": entry["actual_gate"],
            "verdict": entry["verdict"],
        }
        for entry in entries
    ]
    payload = {
        "handoff_version": IMPORT_HANDOFF_VERSION,
        "ok": True,
        "blockers": [],
        "created_at_utc": _format_utc(now_utc or datetime.now(timezone.utc)),
        "share_id": import_report["share_id"],
        "purpose": import_report["purpose"],
        "import_report_digest": sha256_digest(import_report),
        "share_manifest_digest": import_report["share_manifest_digest"],
        "source_manifest_digest": import_report["source_manifest_digest"],
        "operator_ownership": {
            "operator_owned": True,
            "operator_agent_id": operator_agent_id,
            "operator_decision_ref": "<redacted>",
            "operator_decision_id_recorded": False,
            "bridge_event_ref": bridge_event_ref,
            "import_decision": import_decision,
            "import_decision_recorded": True,
            "decision_reason_ref": decision_reason_ref,
        },
        "authority": {
            "operator_gate_required": True,
            "operator_gate_satisfied": True,
            "handoff_scope": IMPORT_HANDOFF_SCOPE,
            "runtime_export_enabled": False,
            "runtime_authority_granted": False,
            "runtime_authority_changed": False,
            "runtime_traffic_mutation_applied": False,
            "runtime_receipt_emission_changed": False,
        },
        "privacy": {
            "replay_metadata_only": True,
            "no_authority_import": True,
            "local_paths_recorded": False,
            "payload_files_imported": 0,
            "payload_digest_imported": False,
            "raw_material_imported": False,
            "replacement_map_imported": False,
        },
        "replay_plan": {
            "mode": replay_plan["mode"],
            "entry_count": replay_plan["entry_count"],
            "entries": replay_refs,
        },
    }
    handoff_digest = sha256_digest(payload)
    payload["handoff_digest"] = handoff_digest
    payload["handoff_id"] = (
        "magma:share-import-handoff:" f"{handoff_digest.removeprefix('sha256:')[:16]}"
    )
    return payload


def write_magma_share_import_peer_review_handoff(
    *,
    import_report: Mapping[str, Any],
    out_path: Path,
    operator_decision_id: str,
    operator_agent_id: str,
    bridge_event_ref: str,
    import_decision: str = "accepted_for_peer_review",
    decision_reason_ref: str = "reason:operator_peer_review_handoff",
    expected_purpose: str = DEFAULT_IMPORT_HANDOFF_EXPECTED_PURPOSE,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Write a local peer-review handoff for a verified import report."""
    out_path = out_path.resolve()
    if out_path.exists():
        raise ValueError(f"out_path must not exist: {out_path}")
    if not out_path.parent.exists():
        raise ValueError(f"out_path parent does not exist: {out_path.parent}")
    handoff = build_magma_share_import_peer_review_handoff(
        import_report=import_report,
        operator_decision_id=operator_decision_id,
        operator_agent_id=operator_agent_id,
        bridge_event_ref=bridge_event_ref,
        import_decision=import_decision,
        decision_reason_ref=decision_reason_ref,
        expected_purpose=expected_purpose,
        now_utc=now_utc,
    )
    _write_json(out_path, handoff)
    return handoff


def build_magma_share_import_admission_status_summary(
    import_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a path-free no-authority admission status summary.

    This is a reviewer-facing view over an import report, not a handoff and
    not a bridge writer. It intentionally emits only digest/categorical status
    fields so a receiving instance can inspect admission readiness without
    local paths, payload material, transport, or runtime authority.
    """
    source = "magma_share_manifest_import_report"
    if import_report is None:
        return _empty_import_admission_status_summary("not_configured")
    if not isinstance(import_report, Mapping):
        return _blocked_import_admission_status_summary(
            source,
            "import_report_invalid",
        )

    try:
        expected_purpose = _import_report_expected_purpose(import_report)
        _ensure_import_report_ready_for_handoff(
            import_report,
            expected_purpose=expected_purpose,
        )
        admission_contract = import_report.get("admission_contract")
        if not isinstance(admission_contract, Mapping):
            raise ValueError(
                "import report is not admission-summary-ready: " "admission_contract"
            )
        admission_contract_digest = import_report.get("admission_contract_digest")
        _ensure_sha256_digest(
            "admission_contract_digest",
            admission_contract_digest,
        )
        replay_plan = import_report.get("replay_plan")
        if not isinstance(replay_plan, Mapping):
            raise ValueError(
                "import report is not admission-summary-ready: replay_plan"
            )
        entry_count = replay_plan.get("entry_count")
        if (
            isinstance(entry_count, bool)
            or not isinstance(entry_count, int)
            or entry_count < 0
        ):
            raise ValueError(
                "import report is not admission-summary-ready: "
                "replay_plan.entry_count"
            )
        age_seconds = import_report.get("age_seconds")
        if (
            isinstance(age_seconds, bool)
            or not isinstance(age_seconds, int)
            or age_seconds < 0
        ):
            raise ValueError(
                "import report is not admission-summary-ready: age_seconds"
            )
    except (TypeError, ValueError) as exc:
        return _blocked_import_admission_status_summary(
            source,
            _classify_import_admission_blocker(str(exc)),
        )

    return {
        "summary_version": IMPORT_ADMISSION_STATUS_VERSION,
        "source": source,
        "status": "ready_for_peer_review_handoff",
        "severity": "none",
        "ok": True,
        "blocker_class": "none",
        "blockers": [],
        "controls_present": False,
        "transport_enabled": False,
        "operator_handoff_required_for_peer_review": (
            admission_contract["operator_handoff_required_for_peer_review"]
        ),
        "share_id": import_report["share_id"],
        "purpose": import_report["purpose"],
        "report_digest": sha256_digest(import_report),
        "admission_contract_digest": admission_contract_digest,
        "share_manifest_digest": import_report["share_manifest_digest"],
        "source_manifest_digest": import_report["source_manifest_digest"],
        "entry_count": entry_count,
        "age_seconds": age_seconds,
        "max_age_hours": import_report["max_age_hours"],
        "context_verified": import_report["context_verified"],
        "context_drift_detected": import_report["context_drift_detected"],
        "replay_metadata_only": import_report["replay_metadata_only"],
        "no_authority_import": import_report["no_authority_import"],
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
        "local_paths_recorded": False,
    }


def build_magma_share_import_handoff_status_summary(
    handoff: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None = None,
    *,
    history_limit: int = IMPORT_HANDOFF_HISTORY_LIMIT,
) -> dict[str, Any]:
    """Return a sanitized read-only operator summary for an import handoff."""
    if handoff is None:
        return _empty_import_handoff_status_summary("not_configured")

    limit = _ensure_import_handoff_history_limit(history_limit)
    handoffs = _coerce_import_handoff_history(handoff)
    if not handoffs:
        return _empty_import_handoff_status_summary(
            "magma_share_import_peer_review_handoff"
        )

    entries = [_import_handoff_status_entry(item) for item in handoffs]
    entries.sort(
        key=lambda item: (item["created_at_utc"], item["handoff_digest"]),
        reverse=True,
    )
    retained = entries[:limit]
    latest = retained[0]
    active = [
        item
        for item in retained
        if item["import_decision"] == "accepted_for_peer_review"
    ]
    severity = (
        "warning" if any(item["severity"] == "warning" for item in retained) else "none"
    )
    return {
        "summary_version": IMPORT_HANDOFF_STATUS_VERSION,
        "source": "magma_share_import_peer_review_handoff",
        "status": latest["status"],
        "severity": severity,
        "controls_present": False,
        "operator_owned": True,
        "handoff_count": len(entries),
        "active_count": len(active),
        "history_limit": limit,
        "history_retained_count": len(retained),
        "history_dropped_count": max(0, len(entries) - len(retained)),
        "history_truncated": len(entries) > len(retained),
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
        "local_paths_recorded": False,
        "latest": latest,
        "active": active,
        "history": retained,
    }


def _import_handoff_status_entry(handoff: Mapping[str, Any]) -> dict[str, Any]:
    _ensure_import_handoff_ready_for_summary(handoff)
    ownership = handoff["operator_ownership"]
    authority = handoff["authority"]
    privacy = handoff["privacy"]
    replay_plan = handoff["replay_plan"]
    decision = ownership["import_decision"]
    status = IMPORT_HANDOFF_DECISION_STATUS[decision]
    severity = "none" if decision == "accepted_for_peer_review" else "warning"
    return {
        "handoff_id": handoff["handoff_id"],
        "handoff_digest": handoff["handoff_digest"],
        "created_at_utc": handoff["created_at_utc"],
        "share_id": handoff["share_id"],
        "purpose": handoff["purpose"],
        "status": status,
        "severity": severity,
        "handoff_scope": authority["handoff_scope"],
        "import_decision": decision,
        "entry_count": replay_plan["entry_count"],
        "operator_owned": ownership["operator_owned"],
        "operator_agent_id": ownership["operator_agent_id"],
        "bridge_event_ref": ownership["bridge_event_ref"],
        "decision_reason_ref": ownership["decision_reason_ref"],
        "runtime_export_enabled": authority["runtime_export_enabled"],
        "runtime_authority_granted": authority["runtime_authority_granted"],
        "runtime_authority_changed": authority["runtime_authority_changed"],
        "payload_files_imported": privacy["payload_files_imported"],
        "local_paths_recorded": privacy["local_paths_recorded"],
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
            errors.append(
                f"magma_share_manifest: count mismatch at artifact_counts.{field}"
            )
    if counts.get("payload_files") != 0:
        errors.append(
            "magma_share_manifest: count mismatch at artifact_counts.payload_files"
        )
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
        raise ValueError(
            f"source entry {index}: {field} path must use POSIX separators"
        )
    if PurePosixPath(raw_path).is_absolute() or PureWindowsPath(raw_path).is_absolute():
        raise ValueError(f"source entry {index}: {field} path must be relative")
    parts = raw_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"source entry {index}: {field} unsafe relative path")
    path = (manifest_path.parent / Path(*parts)).resolve()
    try:
        path.relative_to(manifest_path.parent)
    except ValueError as exc:
        raise ValueError(
            f"source entry {index}: {field} path escapes manifest"
        ) from exc
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


def _ensure_import_report_ready_for_handoff(
    import_report: Mapping[str, Any],
    *,
    expected_purpose: str,
) -> None:
    required_values = {
        "report_version": IMPORT_REPORT_VERSION,
        "ok": True,
        "blockers": [],
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
    }
    for field, expected in required_values.items():
        if import_report.get(field) != expected:
            raise ValueError(f"import report is not handoff-ready: {field}")
    for field in ("share_id", "purpose"):
        if not isinstance(import_report.get(field), str):
            raise ValueError(f"import report is not handoff-ready: {field}")
    _ensure_ref("share_id", import_report["share_id"])
    if import_report["purpose"] not in PURPOSES:
        raise ValueError("import report is not handoff-ready: purpose")
    if import_report["purpose"] != expected_purpose:
        raise ValueError("import report is not handoff-ready: expected_purpose")
    admission_contract = import_report.get("admission_contract")
    admission_contract_digest = import_report.get("admission_contract_digest")
    require_replay_identity_binding = False
    if admission_contract is None and admission_contract_digest is None:
        pass
    elif not isinstance(admission_contract, Mapping):
        raise ValueError("import report is not handoff-ready: admission_contract")
    else:
        _ensure_replay_admission_contract_matches_import_report(
            import_report,
            admission_contract,
            admission_contract_digest,
            expected_purpose=expected_purpose,
        )
        require_replay_identity_binding = True
    for field in ("share_manifest_digest", "source_manifest_digest"):
        _ensure_sha256_digest(field, import_report.get(field))
    replay_plan = import_report.get("replay_plan")
    if not isinstance(replay_plan, Mapping):
        raise ValueError("import report is not handoff-ready: replay_plan")
    if replay_plan.get("mode") != "no_authority_metadata_replay":
        raise ValueError("import report is not handoff-ready: replay_plan.mode")
    entries = replay_plan.get("entries")
    if not isinstance(entries, list):
        raise ValueError("import report is not handoff-ready: replay_plan.entries")
    if replay_plan.get("entry_count") != len(entries):
        raise ValueError("import report is not handoff-ready: replay_plan.entry_count")
    required_entry_fields = {
        "entry_id",
        "receipt_digest",
        "evaluation_result_digest",
        "subject_type",
        "risk_class",
        "expected_gate",
        "actual_gate",
        "verdict",
    }
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, Mapping):
            raise ValueError("import report is not handoff-ready: replay_plan entry")
        missing = sorted(required_entry_fields - set(entry))
        if missing:
            raise ValueError(
                "import report is not handoff-ready: "
                f"replay_plan entry {index} missing {missing}"
            )
        _ensure_sha256_digest(
            f"replay_plan entry {index} receipt_digest",
            entry.get("receipt_digest"),
        )
        _ensure_sha256_digest(
            f"replay_plan entry {index} evaluation_result_digest",
            entry.get("evaluation_result_digest"),
        )
        if require_replay_identity_binding:
            entry_id = entry.get("entry_id")
            if not isinstance(entry_id, str):
                raise ValueError(
                    "import report is not handoff-ready: "
                    f"replay_plan entry {index} entry_id"
                )
            _ensure_ref(f"replay_plan entry {index} entry_id", entry_id)
            if not entry_id.startswith(f"{import_report['share_id']}:entry:"):
                raise ValueError(
                    "import report is not handoff-ready: "
                    f"replay_plan entry {index} share_id"
                )


def _replay_admission_contract(
    *,
    max_age_hours: int,
    expected_share_id: str | None,
    expected_purpose: str | None,
) -> dict[str, Any]:
    """Describe the fail-closed checks for no-authority share replay."""
    required_checks = [
        {
            "name": "share_manifest_schema_valid",
            "input": "share_manifest",
            "rejects_as": "schema_error",
        },
        {
            "name": "expected_share_id_matches",
            "input": "share_manifest.share_id",
            "rejects_as": "expected_share_id_mismatch",
            "required": expected_share_id is not None,
        },
        {
            "name": "expected_purpose_matches",
            "input": "share_manifest.purpose",
            "rejects_as": "expected_purpose_mismatch",
            "required": expected_purpose is not None,
        },
        {
            "name": "freshness_window_satisfied",
            "input": "share_manifest.created_at_utc",
            "rejects_as": "stale_or_future_manifest",
        },
        {
            "name": "source_receipt_manifest_verified",
            "input": "source_manifest",
            "rejects_as": "source_receipt_manifest_verification_failed",
        },
        {
            "name": "sanitized_source_manifest_digest_matches",
            "input": "share_manifest.sanitized_source_manifest_digest",
            "rejects_as": "sanitized_source_manifest_digest_context_drift",
        },
        {
            "name": "entry_count_matches",
            "input": "share_manifest.entries",
            "rejects_as": "share_manifest_entry_count_context_drift",
        },
        {
            "name": "entry_receipt_digests_match",
            "input": "share_manifest.entries[].receipt_digest",
            "rejects_as": "receipt_digest_context_drift",
        },
        {
            "name": "entry_evaluation_result_digests_match",
            "input": "share_manifest.entries[].evaluation_result_digest",
            "rejects_as": "evaluation_result_digest_context_drift",
        },
        {
            "name": "entry_categorical_fields_match",
            "input": "share_manifest.entries[] categorical replay refs",
            "rejects_as": "categorical_context_drift",
        },
        {
            "name": "forbidden_material_absence_preserved",
            "input": "share_manifest export_policy and sanitization inventory",
            "rejects_as": "raw_material_export_or_policy_relaxation",
        },
    ]
    rejection_modes = [
        {
            "reason_code": item["rejects_as"],
            "decision": "reject",
            "blocker_visible": True,
        }
        for item in required_checks
    ]
    return {
        "contract_version": IMPORT_ADMISSION_CONTRACT_VERSION,
        "manifest_version": MANIFEST_VERSION,
        "scope": "no_authority_metadata_replay",
        "max_age_hours": max_age_hours,
        "expected_share_id": expected_share_id,
        "expected_purpose": expected_purpose,
        "transport_enabled": False,
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
        "local_paths_recorded": False,
        "operator_handoff_required_for_peer_review": True,
        "required_checks": required_checks,
        "rejection_modes": rejection_modes,
        "report_invariants": {
            "ok_requires_blockers_empty": True,
            "ok_requires_context_verified": True,
            "ok_requires_context_drift_detected_false": True,
            "ok_requires_replay_metadata_only": True,
            "ok_requires_no_authority_import": True,
            "ok_requires_runtime_authority_granted_false": True,
            "ok_requires_payload_files_imported_zero": True,
            "ok_requires_raw_material_imported_false": True,
        },
    }


def _ensure_replay_admission_contract_ready(
    admission_contract: Mapping[str, Any],
) -> None:
    required_values = {
        "contract_version": IMPORT_ADMISSION_CONTRACT_VERSION,
        "manifest_version": MANIFEST_VERSION,
        "scope": "no_authority_metadata_replay",
        "transport_enabled": False,
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
        "local_paths_recorded": False,
        "operator_handoff_required_for_peer_review": True,
    }
    for field, expected in required_values.items():
        if admission_contract.get(field) != expected:
            raise ValueError(
                "import report is not handoff-ready: " f"admission_contract.{field}"
            )
    if not isinstance(admission_contract.get("required_checks"), list):
        raise ValueError(
            "import report is not handoff-ready: admission_contract.required_checks"
        )
    if not isinstance(admission_contract.get("rejection_modes"), list):
        raise ValueError(
            "import report is not handoff-ready: admission_contract.rejection_modes"
        )
    if not isinstance(admission_contract.get("report_invariants"), Mapping):
        raise ValueError(
            "import report is not handoff-ready: admission_contract.report_invariants"
        )


def _ensure_replay_admission_contract_matches_import_report(
    import_report: Mapping[str, Any],
    admission_contract: Mapping[str, Any],
    admission_contract_digest: Any,
    *,
    expected_purpose: str,
) -> None:
    if admission_contract.get("contract_version") != IMPORT_ADMISSION_CONTRACT_VERSION:
        raise ValueError(
            "import report is not handoff-ready: admission_contract.version"
        )
    _ensure_replay_admission_contract_ready(admission_contract)
    if admission_contract_digest != sha256_digest(admission_contract):
        raise ValueError(
            "import report is not handoff-ready: admission_contract_digest"
        )

    max_age_hours = import_report.get("max_age_hours")
    if (
        not isinstance(max_age_hours, int)
        or isinstance(max_age_hours, bool)
        or max_age_hours <= 0
        or max_age_hours > DEFAULT_IMPORT_MAX_AGE_HOURS
    ):
        raise ValueError("import report is not handoff-ready: max_age_hours")
    expected_share_id = admission_contract.get("expected_share_id")
    if expected_share_id is None or expected_share_id != import_report["share_id"]:
        raise ValueError(
            "import report is not handoff-ready: "
            "admission_contract.expected_share_id"
        )
    admission_expected_purpose = admission_contract.get("expected_purpose")
    if (
        admission_expected_purpose is None
        or admission_expected_purpose != import_report["purpose"]
        or admission_expected_purpose != expected_purpose
    ):
        raise ValueError(
            "import report is not handoff-ready: " "admission_contract.expected_purpose"
        )

    canonical_contract = _replay_admission_contract(
        max_age_hours=import_report["max_age_hours"],
        expected_share_id=expected_share_id,
        expected_purpose=admission_expected_purpose,
    )
    if dict(admission_contract) != canonical_contract:
        raise ValueError(
            "import report is not handoff-ready: admission_contract.canonical"
        )


def _import_report_expected_purpose(import_report: Mapping[str, Any]) -> str:
    purpose = import_report.get("purpose")
    if not isinstance(purpose, str):
        raise ValueError("import report is not admission-summary-ready: purpose")
    if purpose not in PURPOSES:
        raise ValueError("import report is not admission-summary-ready: purpose")
    return purpose


def _empty_import_admission_status_summary(source: str) -> dict[str, Any]:
    return {
        "summary_version": IMPORT_ADMISSION_STATUS_VERSION,
        "source": source,
        "status": "not_configured",
        "severity": "none",
        "ok": False,
        "blocker_class": "not_configured",
        "blockers": [],
        "controls_present": False,
        "transport_enabled": False,
        "operator_handoff_required_for_peer_review": True,
        "entry_count": 0,
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
        "local_paths_recorded": False,
    }


def _blocked_import_admission_status_summary(
    source: str,
    blocker_class: str,
) -> dict[str, Any]:
    return {
        "summary_version": IMPORT_ADMISSION_STATUS_VERSION,
        "source": source,
        "status": "blocked",
        "severity": "warning",
        "ok": False,
        "blocker_class": blocker_class,
        "blockers": [blocker_class],
        "controls_present": False,
        "transport_enabled": False,
        "operator_handoff_required_for_peer_review": True,
        "entry_count": 0,
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
        "local_paths_recorded": False,
    }


def _classify_import_admission_blocker(reason: str) -> str:
    if "admission_contract" in reason:
        return "admission_contract_invalid"
    if "replay_plan" in reason:
        return "replay_plan_invalid"
    if "context_drift" in reason or "digest" in reason:
        return "context_drift"
    if (
        "authority" in reason
        or "payload_" in reason
        or "raw_material" in reason
        or "replacement_map" in reason
        or "replay_metadata_only" in reason
        or "no_authority_import" in reason
    ):
        return "authority_or_privacy_boundary"
    if "purpose" in reason or "share_id" in reason:
        return "identity_or_purpose_invalid"
    return "import_report_invalid"


def _empty_import_handoff_status_summary(source: str) -> dict[str, Any]:
    return {
        "summary_version": IMPORT_HANDOFF_STATUS_VERSION,
        "source": source,
        "status": "not_configured",
        "severity": "none",
        "controls_present": False,
        "operator_owned": False,
        "handoff_count": 0,
        "active_count": 0,
        "history_limit": IMPORT_HANDOFF_HISTORY_LIMIT,
        "history_retained_count": 0,
        "history_dropped_count": 0,
        "history_truncated": False,
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
        "local_paths_recorded": False,
        "latest": None,
        "active": [],
        "history": [],
    }


def _coerce_import_handoff_history(
    snapshot: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    if isinstance(snapshot, Mapping):
        if snapshot.get("handoff_version") == IMPORT_HANDOFF_VERSION:
            return [snapshot]
        for field in ("history", "handoffs"):
            value = snapshot.get(field)
            if value is not None:
                return _bounded_import_handoff_history_items(value)
        latest = snapshot.get("latest")
        if (
            isinstance(latest, Mapping)
            and latest.get("handoff_version") == IMPORT_HANDOFF_VERSION
        ):
            return [latest]
        raise TypeError("import handoff snapshot must be a handoff or history")
    return _bounded_import_handoff_history_items(snapshot)


def _bounded_import_handoff_history_items(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise TypeError("import handoff history must be a bounded sequence")
    items = list(value)
    if len(items) > IMPORT_HANDOFF_HISTORY_MAX_SNAPSHOT:
        raise ValueError("import handoff history exceeds bounded snapshot limit")
    for item in items:
        if not isinstance(item, Mapping):
            raise TypeError("import handoff history entries must be objects")
    return items


def _ensure_import_handoff_history_limit(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > IMPORT_HANDOFF_HISTORY_MAX_SNAPSHOT
    ):
        raise ValueError("import handoff history_limit is out of bounds")
    return value


def _ensure_import_handoff_ready_for_summary(
    handoff: Mapping[str, Any],
) -> None:
    required_values = {
        "handoff_version": IMPORT_HANDOFF_VERSION,
        "ok": True,
        "blockers": [],
    }
    for field, expected in required_values.items():
        if handoff.get(field) != expected:
            raise ValueError(f"import handoff is not summary-ready: {field}")
    for field in (
        "handoff_id",
        "created_at_utc",
        "share_id",
        "purpose",
    ):
        if not isinstance(handoff.get(field), str):
            raise ValueError(f"import handoff is not summary-ready: {field}")
    _parse_created_at_utc(handoff)
    for field in ("handoff_id", "share_id", "purpose"):
        _ensure_ref(field, handoff[field])
    for field in (
        "handoff_digest",
        "import_report_digest",
        "share_manifest_digest",
        "source_manifest_digest",
    ):
        _ensure_sha256_digest(field, handoff.get(field))

    ownership = handoff.get("operator_ownership")
    authority = handoff.get("authority")
    privacy = handoff.get("privacy")
    replay_plan = handoff.get("replay_plan")
    if not isinstance(ownership, Mapping):
        raise ValueError("import handoff is not summary-ready: operator_ownership")
    if not isinstance(authority, Mapping):
        raise ValueError("import handoff is not summary-ready: authority")
    if not isinstance(privacy, Mapping):
        raise ValueError("import handoff is not summary-ready: privacy")
    if not isinstance(replay_plan, Mapping):
        raise ValueError("import handoff is not summary-ready: replay_plan")

    ownership_required = {
        "operator_owned": True,
        "operator_decision_ref": "<redacted>",
        "operator_decision_id_recorded": False,
        "import_decision_recorded": True,
    }
    for field, expected in ownership_required.items():
        if ownership.get(field) != expected:
            raise ValueError(f"import handoff is not summary-ready: {field}")
    decision = ownership.get("import_decision")
    if decision not in IMPORT_HANDOFF_DECISIONS:
        raise ValueError("import handoff is not summary-ready: import_decision")
    for field in (
        "operator_agent_id",
        "bridge_event_ref",
        "decision_reason_ref",
    ):
        if not isinstance(ownership.get(field), str):
            raise ValueError(f"import handoff is not summary-ready: {field}")
        _ensure_ref(field, ownership[field])

    authority_required = {
        "operator_gate_required": True,
        "operator_gate_satisfied": True,
        "handoff_scope": IMPORT_HANDOFF_SCOPE,
        "runtime_export_enabled": False,
        "runtime_authority_granted": False,
        "runtime_authority_changed": False,
        "runtime_traffic_mutation_applied": False,
        "runtime_receipt_emission_changed": False,
    }
    for field, expected in authority_required.items():
        if authority.get(field) != expected:
            raise ValueError(f"import handoff is not summary-ready: {field}")

    privacy_required = {
        "replay_metadata_only": True,
        "no_authority_import": True,
        "local_paths_recorded": False,
        "payload_files_imported": 0,
        "payload_digest_imported": False,
        "raw_material_imported": False,
        "replacement_map_imported": False,
    }
    for field, expected in privacy_required.items():
        if privacy.get(field) != expected:
            raise ValueError(f"import handoff is not summary-ready: {field}")

    entries = replay_plan.get("entries")
    if replay_plan.get("mode") != "no_authority_metadata_replay":
        raise ValueError("import handoff is not summary-ready: replay_plan.mode")
    if not isinstance(entries, list):
        raise ValueError("import handoff is not summary-ready: replay_plan.entries")
    if replay_plan.get("entry_count") != len(entries):
        raise ValueError("import handoff is not summary-ready: replay_plan.entry_count")
    required_entry_fields = {
        "entry_id",
        "receipt_digest",
        "evaluation_result_digest",
        "subject_type",
        "risk_class",
        "expected_gate",
        "actual_gate",
        "verdict",
    }
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, Mapping):
            raise ValueError("import handoff is not summary-ready: replay_plan entry")
        missing = sorted(required_entry_fields - set(entry))
        if missing:
            raise ValueError(
                "import handoff is not summary-ready: "
                f"replay_plan entry {index} missing {missing}"
            )
        _ensure_sha256_digest(
            f"replay_plan entry {index} receipt_digest",
            entry.get("receipt_digest"),
        )
        _ensure_sha256_digest(
            f"replay_plan entry {index} evaluation_result_digest",
            entry.get("evaluation_result_digest"),
        )


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(
            f"{label}: cannot read JSON file ({exc.__class__.__name__})"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{label}: invalid JSON at line {exc.lineno} column {exc.colno}"
        ) from exc


def _ensure_ref(label: str, value: str) -> None:
    if not _REF_RE.fullmatch(value):
        raise ValueError(f"{label} must be a MAGMA ref")


def _ensure_sha256_digest(label: str, value: Any) -> None:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != len("sha256:") + 64
    ):
        raise ValueError(f"{label} must be a sha256 digest")
    hexdigest = value.removeprefix("sha256:")
    if any(char not in "0123456789abcdef" for char in hexdigest):
        raise ValueError(f"{label} must be a sha256 digest")


def _format_utc(value: datetime) -> str:
    return (
        _ensure_utc(value, "timestamp")
        .replace(microsecond=0)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
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
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("created_at_utc must be UTC")
    return parsed.astimezone(timezone.utc)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
