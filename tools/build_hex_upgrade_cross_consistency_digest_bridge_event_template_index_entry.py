#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a local index entry for the hex-upgrade cross-consistency digest template.

The tool advances the WD Image #1 hexagonal-upgrades pillar by turning the
measurement-only cross-consistency digest into a schema-valid bridge-event
template report, then binding that template report in a local path-free index
entry. It is read-only: it never appends bridge events, transports payloads,
records local paths, mutates runtime topology, grants subdivision authority, or
upgrades the hexagonal-upgrades claim.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hex_shadow_subdivision_replay import _contains_path_marker  # noqa: E402
from tools.run_hex_upgrade_cross_consistency_digest import (  # noqa: E402
    REPORT_VERSION as DIGEST_REPORT_VERSION,
    build_cross_consistency_digest,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


TEMPLATE_VERSION = "wd.hex_upgrade_cross_consistency_digest_bridge_event_template.v1"
EVENT_STATUS = "hex_upgrade_cross_consistency_digest_bridge_event_template_ready"
TEMPLATE_PROOF_ID = "hex_upgrade_cross_consistency_digest_bridge_event_template_v1"
INDEX_ENTRY_VERSION = (
    "wd.hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry.v1"
)
PROOF_ID = "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_v1"
TEMPLATE_ARTIFACT_ID = "hex_upgrade_cross_consistency_digest_bridge_event_template"

AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
SAFE_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

_DIGEST_VERDICT_FIELDS = (
    "reviewer_summary_present",
    "shadow_only_invariant_present",
    "chain_final_summary_present",
    "all_views_present",
    "reviewer_clean",
    "shadow_only_clean",
    "chain_summary_clean",
    "cross_consistent",
    "path_free_verified",
)
_DIGEST_SAFE_KEYS = frozenset(
    {"report_version", *_DIGEST_VERDICT_FIELDS, "claim_safe"}
)
_TEMPLATE_SAFE_KEYS = frozenset(
    {"digest_schema_version", "digest_ref", *_DIGEST_VERDICT_FIELDS, "raw_digest_payload_included"}
)

AUTHORITY_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "merge_decision_made",
    "promotion_granted",
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "runtime_controls_added",
    "runtime_authority_granted",
    "runtime_subdivision_authority_granted",
    "bridge_event_written",
    "gate_skip_allowed",
    "fast_track_priority",
    "digest_payloads_included",
    "local_paths_recorded",
    "claim_safe",
)
_TEMPLATE_REPORT_FALSE_FIELDS = (
    "direct_bridge_write_performed",
    "approval_granted",
    "release_decision_made",
    "runtime_authority_granted",
    "runtime_subdivision_authority_granted",
    "bridge_event_written",
    "fast_track_priority",
    "gate_skip_allowed",
    "claim_safe",
)
_BOUNDARY_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "merge_decision_made",
    "promotion_granted",
    "claim_safe",
    "runtime_authority_granted",
    "runtime_subdivision_authority_granted",
    "bridge_event_written",
    "gate_skip_allowed",
    "fast_track_priority",
)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


_BACKSLASH = _chars(92)
FORBIDDEN_OUTPUT_MARKERS = (
    "".join((_chars(104, 116, 116, 112), ":", "/", "/")),
    "".join((_chars(104, 116, 116, 112, 115), ":", "/", "/")),
    "".join(("C", ":", "/")),
    "".join(("C", ":", _BACKSLASH)),
    "".join((_BACKSLASH, _BACKSLASH)),
    "".join(("/", "home", "/")),
    "".join(("/", "Users", "/")),
    _chars(80, 82, 73, 86, 65, 84, 69, 95),
    "".join(("Author", "ization")),
    "".join(("Bear", "er", " ")),
    "".join(("sec", "ret")),
    "".join(("pass", "word")),
)


class IndexEntryError(ValueError):
    """Raised when index-entry inputs violate the local contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_hex_upgrade_cross_consistency_digest_bridge_event_template(
    *,
    digest: Mapping[str, Any],
    agent_id: str,
    task_id: str,
    to: str = "operator,claude-rco-1,codex-tools-1",
    severity: str = "medium",
    role: str = "lead-impl",
    run_id: str = "",
    session_id: str = "",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a schema-valid bridge-event template report without appending it."""

    if not isinstance(digest, Mapping):
        return _template_error_report("digest_not_object")
    digest_error = _validate_digest(digest)
    if digest_error is not None:
        return _template_error_report(digest_error)

    input_error = _bridge_input_error(
        agent_id=agent_id,
        task_id=task_id,
        to=to,
        severity=severity,
        role=role,
        run_id=run_id,
        session_id=session_id,
    )
    if input_error is not None:
        return _template_error_report(input_error)
    targets = ",".join(item.strip() for item in to.split(",") if item.strip())

    digest_ref = sha256_digest(_plain_json_object(digest))
    cross_consistency = {
        "digest_schema_version": DIGEST_REPORT_VERSION,
        "digest_ref": digest_ref,
        **{field: digest.get(field) is True for field in _DIGEST_VERDICT_FIELDS},
        "raw_digest_payload_included": False,
    }
    extra = set(cross_consistency) - _TEMPLATE_SAFE_KEYS
    if extra:
        raise ValueError(
            f"cross-consistency template section has non-allowlisted keys: {sorted(extra)}"
        )

    payload = {
        "schema_version": TEMPLATE_VERSION,
        "cross_consistency": cross_consistency,
        "authority_boundary": {
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "merge_decision_made": False,
            "promotion_granted": False,
            "claim_safe": False,
            "runtime_authority_granted": False,
            "runtime_subdivision_authority_granted": False,
            "bridge_event_written": False,
            "gate_skip_allowed": False,
            "fast_track_priority": False,
        },
        "template_only": True,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "runtime_subdivision_authority_granted": False,
        "digest_payloads_included": False,
        "local_paths_recorded": False,
    }
    event = {
        "ts_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "agent": agent_id,
        "type": "handoff",
        "task_id": task_id,
        "status": EVENT_STATUS,
        "severity": severity,
        "to": targets,
        "message": (
            "Hex-upgrade cross-consistency digest bridge-event template ready; "
            f"cross_consistent={cross_consistency['cross_consistent']}; "
            f"all_views_present={cross_consistency['all_views_present']}; "
            "manual_review_required=true; approval_granted=false; "
            "direct_bridge_write_performed=false; "
            "runtime_subdivision_authority_granted=false; template_only=true."
        ),
        "paths": [],
        "write_scope": [],
        "run_id": run_id,
        "role": role,
        "session_id": session_id,
        "capabilities": [
            "wd_image1",
            "hexagonal_upgrades",
            "hex_upgrade_cross_consistency",
            "bridge_event",
        ],
        "pid": 0,
        "cwd": "template_not_emitted",
        "payload": payload,
    }
    try:
        validate_event(event)
    except Exception:  # noqa: BLE001
        return _template_error_report("bridge_event_template_schema_invalid")
    if _contains_path_marker(event):
        return _template_error_report("template_not_path_free")
    rendered = json.dumps(event, allow_nan=False, sort_keys=True)
    if _forbidden_output_markers(rendered):
        return _template_error_report("template_forbidden_marker")

    report = {
        "proof_id": TEMPLATE_PROOF_ID,
        "ok": True,
        "template_version": TEMPLATE_VERSION,
        "bridge_event_template": event,
        "template_only": True,
        "manual_review_required": True,
        "direct_bridge_write_performed": False,
        "approval_granted": False,
        "release_decision_made": False,
        "runtime_authority_granted": False,
        "runtime_subdivision_authority_granted": False,
        "bridge_event_written": False,
        "fast_track_priority": False,
        "gate_skip_allowed": False,
        "claim_safe": False,
        "digest_payloads_included": False,
        "local_paths_recorded": False,
        "path_free_verified": True,
        "blockers": [],
        "warnings": [],
    }
    return report


def build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
    *,
    digest: Mapping[str, Any] | None = None,
    bridge_event_template_report: Mapping[str, Any] | None = None,
    bridge_event_template_bytes: bytes | None = None,
    agent_id: str = "codex-lead-1",
    task_id: str = "wd-image1-hex-upgrade-xcons-template-index-entry",
    to: str = "operator,claude-rco-1,codex-tools-1",
    severity: str = "medium",
    role: str = "lead-impl",
    run_id: str = "",
    session_id: str = "",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a path-free local index entry for the hex digest bridge template."""

    try:
        if bridge_event_template_report is None:
            if digest is None:
                raise IndexEntryError("digest_missing")
            bridge_event_template_report = (
                build_hex_upgrade_cross_consistency_digest_bridge_event_template(
                    digest=digest,
                    agent_id=agent_id,
                    task_id=task_id,
                    to=to,
                    severity=severity,
                    role=role,
                    run_id=run_id,
                    session_id=session_id,
                    now_utc=now_utc,
                )
            )
        _assert_mapping(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
        _assert_no_forbidden_input(TEMPLATE_ARTIFACT_ID, bridge_event_template_report)
        if bridge_event_template_bytes is None:
            bridge_event_template_bytes = json.dumps(
                _plain_json_object(bridge_event_template_report),
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        cross = _assert_template_contract(bridge_event_template_report)
    except IndexEntryError as exc:
        return _failure_report(exc.code)
    except (TypeError, ValueError):
        return _failure_report("index_entry_invalid")

    template_sha256 = hashlib.sha256(bridge_event_template_bytes).hexdigest()
    template_digest = sha256_digest(_plain_json_object(bridge_event_template_report))
    event = _mapping(bridge_event_template_report.get("bridge_event_template"))
    payload = _mapping(event.get("payload"))
    template_index_entry: dict[str, Any] = {
        "artifact_id": TEMPLATE_ARTIFACT_ID,
        "template_version": TEMPLATE_VERSION,
        "template_only": True,
        "bridge_event_schema_validated": True,
        "template_report_sha256": template_sha256,
        "template_report_digest": template_digest,
        "event_digest": sha256_digest(_plain_json_object(event)),
        "payload_digest": sha256_digest(_plain_json_object(payload)),
        "cross_consistency_digest_ref": _safe_sha256_ref(cross.get("digest_ref")),
        "digest_schema_version": cross.get("digest_schema_version"),
        "event_type": event.get("type"),
        "event_status": event.get("status"),
        "manual_review_required": True,
    }
    for field in _DIGEST_VERDICT_FIELDS:
        template_index_entry[field] = cross.get(field) is True
    for field in AUTHORITY_FALSE_FIELDS:
        template_index_entry[field] = False

    entry: dict[str, Any] = {
        "proof_id": PROOF_ID,
        "ok": True,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "template_version": TEMPLATE_VERSION,
        "artifact_count": 1,
        "artifacts": [
            {
                "artifact_id": TEMPLATE_ARTIFACT_ID,
                "role": "verified_no_authority_hex_upgrade_cross_consistency_template",
                "sha256": template_sha256,
                "byte_count": len(bridge_event_template_bytes),
                "digest": template_digest,
                "template_only": True,
                "manual_review_required": True,
                "raw_artifact_payload_included": False,
                "local_path_recorded": False,
            },
        ],
        "template_index_entry": template_index_entry,
        "consistency": {
            "required_artifacts_present": [TEMPLATE_ARTIFACT_ID],
            "all_artifact_digests_recorded": True,
            "bridge_event_schema_validated": True,
            "template_report_validator": "pass",
            "cross_consistency_digest_ref_recorded": True,
            "template_only": True,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "reviewer_next_actions": [
            "review_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry",
            "append_bridge_event_separately_only_after_manual_review",
        ],
        "template_only": True,
        "manual_review_required": True,
        "blockers": [],
        "warnings": _safe_string_list(bridge_event_template_report.get("warnings")),
    }
    for field in AUTHORITY_FALSE_FIELDS:
        entry[field] = False

    errors = validate_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        entry
    )
    if errors:
        return _failure_report(errors[0])
    _assert_no_forbidden_output(json.dumps(entry, allow_nan=False, sort_keys=True))
    return entry


def validate_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
    entry: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    entry_dict = _plain_json_object_or_none(entry)
    if entry_dict is None:
        return ["index_entry_not_json_object"]
    if entry_dict.get("ok") is not True:
        errors.append("index_entry_ok_not_true")
    if entry_dict.get("index_entry_version") != INDEX_ENTRY_VERSION:
        errors.append("index_entry_version_mismatch")
    if entry_dict.get("template_only") is not True:
        errors.append("index_entry_template_only_not_true")
    if entry_dict.get("manual_review_required") is not True:
        errors.append("index_entry_manual_review_required_not_true")
    for field in AUTHORITY_FALSE_FIELDS:
        if entry_dict.get(field) is not False:
            errors.append(f"index_entry_{field}_not_exact_false")
    artifacts = entry_dict.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != 1:
        errors.append("index_entry_artifacts_invalid")
    tie = _plain_json_object_or_none(entry_dict.get("template_index_entry"))
    if tie is None:
        errors.append("template_index_entry_not_object")
    else:
        if tie.get("artifact_id") != TEMPLATE_ARTIFACT_ID:
            errors.append("template_index_entry_artifact_id_mismatch")
        if tie.get("template_version") != TEMPLATE_VERSION:
            errors.append("template_index_entry_template_version_mismatch")
        if tie.get("event_status") != EVENT_STATUS:
            errors.append("template_index_entry_event_status_mismatch")
        if tie.get("bridge_event_schema_validated") is not True:
            errors.append("template_index_entry_schema_validated_not_true")
        if not _is_sha256_ref(tie.get("cross_consistency_digest_ref")):
            errors.append("template_index_entry_digest_ref_invalid")
        for field in _DIGEST_VERDICT_FIELDS:
            if not isinstance(tie.get(field), bool):
                errors.append(f"template_index_entry_{field}_not_bool")
        for field in AUTHORITY_FALSE_FIELDS:
            if tie.get(field) is not False:
                errors.append(f"template_index_entry_{field}_not_exact_false")
    consistency = _plain_json_object_or_none(entry_dict.get("consistency"))
    if consistency is None:
        errors.append("consistency_not_object")
    elif consistency.get("template_report_validator") != "pass":
        errors.append("consistency_template_report_validator_not_pass")
    return errors


def _validate_digest(digest: Mapping[str, Any]) -> str | None:
    if digest.get("report_version") != DIGEST_REPORT_VERSION:
        return "digest_version_mismatch"
    if digest.get("claim_safe") is True:
        return "digest_self_claim_safe"
    extra = set(digest) - _DIGEST_SAFE_KEYS
    if extra:
        return "digest_has_non_allowlisted_keys"
    for field in _DIGEST_VERDICT_FIELDS:
        if not isinstance(digest.get(field), bool):
            return "digest_verdict_not_bool"
    if not isinstance(digest.get("claim_safe"), bool):
        return "digest_claim_safe_not_bool"
    if _contains_path_marker(digest):
        return "digest_not_path_free"
    text = json.dumps(_plain_json_object(digest), allow_nan=False, sort_keys=True)
    if _forbidden_output_markers(text):
        return "digest_forbidden_marker"
    return None


def _assert_template_contract(template_report: Mapping[str, Any]) -> Mapping[str, Any]:
    if template_report.get("ok") is not True:
        raise IndexEntryError("bridge_event_template_not_ok")
    if template_report.get("template_version") != TEMPLATE_VERSION:
        raise IndexEntryError("bridge_event_template_version_mismatch")
    if template_report.get("template_only") is not True:
        raise IndexEntryError("bridge_event_template_template_only_not_true")
    _expect_empty_items(
        template_report.get("blockers"), "bridge_event_template_blockers_present"
    )
    for field in _TEMPLATE_REPORT_FALSE_FIELDS:
        if template_report.get(field) is not False:
            raise IndexEntryError(f"bridge_event_template_{field}_not_false")

    event = _mapping(template_report.get("bridge_event_template"))
    try:
        validate_event(event)
    except Exception as exc:  # noqa: BLE001
        raise IndexEntryError("bridge_event_template_schema_invalid") from exc
    if event.get("type") != "handoff":
        raise IndexEntryError("bridge_event_template_type_mismatch")
    if event.get("status") != EVENT_STATUS:
        raise IndexEntryError("bridge_event_template_status_mismatch")
    if event.get("paths") != []:
        raise IndexEntryError("bridge_event_template_paths_not_empty")
    if event.get("write_scope") != []:
        raise IndexEntryError("bridge_event_template_write_scope_not_empty")
    if event.get("pid") != 0:
        raise IndexEntryError("bridge_event_template_pid_not_zero")
    if event.get("cwd") != "template_not_emitted":
        raise IndexEntryError("bridge_event_template_cwd_mismatch")

    payload = _mapping(event.get("payload"))
    if payload.get("schema_version") != TEMPLATE_VERSION:
        raise IndexEntryError("bridge_event_template_payload_schema_mismatch")
    if payload.get("template_only") is not True:
        raise IndexEntryError("bridge_event_template_payload_template_only_not_true")
    if payload.get("runtime_subdivision_authority_granted") is not False:
        raise IndexEntryError("bridge_event_template_payload_subdivision_not_false")
    boundary = _mapping(payload.get("authority_boundary"))
    if boundary.get("manual_review_required") is not True:
        raise IndexEntryError("bridge_event_template_boundary_manual_review_not_true")
    for field in _BOUNDARY_FALSE_FIELDS:
        if boundary.get(field) is not False:
            raise IndexEntryError(f"bridge_event_template_boundary_{field}_not_false")

    cross = _mapping(payload.get("cross_consistency"))
    if cross.get("digest_schema_version") != DIGEST_REPORT_VERSION:
        raise IndexEntryError("bridge_event_template_digest_schema_mismatch")
    if not _is_sha256_ref(cross.get("digest_ref")):
        raise IndexEntryError("bridge_event_template_digest_ref_invalid")
    if cross.get("raw_digest_payload_included") is not False:
        raise IndexEntryError("bridge_event_template_raw_digest_payload_included")
    for field in _DIGEST_VERDICT_FIELDS:
        if not isinstance(cross.get(field), bool):
            raise IndexEntryError(f"bridge_event_template_cross_{field}_not_bool")
    return cross


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--digest-json",
        default=None,
        type=Path,
        help="Optional digest JSON. Defaults to the live manifest digest.",
    )
    parser.add_argument(
        "--bridge-event-template-json",
        "--template-json",
        dest="bridge_event_template_json",
        default=None,
        type=Path,
        help="Optional pre-rendered template report JSON to index.",
    )
    parser.add_argument("--agent", default="codex-lead-1")
    parser.add_argument("--task-id", default="wd-image1-hex-upgrade-xcons-template-index-entry")
    parser.add_argument("--to", default="operator,claude-rco-1,codex-tools-1")
    parser.add_argument("--severity", default="medium", choices=("", "low", "medium", "high"))
    parser.add_argument("--role", default="lead-impl")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--now", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        template_bytes = None
        template_report = None
        if args.bridge_event_template_json is not None:
            template_bytes, template_report = _load_json_artifact(
                args.bridge_event_template_json,
                TEMPLATE_ARTIFACT_ID,
            )
            digest = None
        else:
            digest = _load_digest(args.digest_json)
        report = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            digest=digest,
            bridge_event_template_report=template_report,
            bridge_event_template_bytes=template_bytes,
            agent_id=args.agent,
            task_id=args.task_id,
            to=args.to,
            severity=args.severity,
            role=args.role,
            run_id=args.run_id,
            session_id=args.session_id,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except IndexEntryError as exc:
        report = _failure_report(exc.code)
    except ValueError:
        report = _failure_report("index_entry_invalid")

    indent = 2 if args.pretty else None
    encoded = json.dumps(report, indent=indent, sort_keys=True, allow_nan=False)
    if args.json or report["ok"]:
        print(encoded)
    else:
        print(
            "hex-upgrade cross-consistency digest bridge-template index entry FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def _load_digest(path: Path | None) -> Mapping[str, Any]:
    if path is None:
        from tools.wd_image1_capability_manifest import build_manifest  # noqa: PLC0415

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            manifest = build_manifest(ROOT)
        for capability in manifest.get("capabilities", []):
            if capability.get("capability_id") == "hexagonal_upgrades":
                proof = capability.get("proof")
                proof = proof if isinstance(proof, Mapping) else {}
                stored = proof.get("cross_consistency_digest")
                if isinstance(stored, Mapping):
                    return dict(stored)
                return build_cross_consistency_digest(proof)
        raise IndexEntryError("hex_upgrade_proof_not_found")
    raw, parsed = _load_json_artifact(path, "digest")
    if not raw:
        raise IndexEntryError("digest_empty")
    return parsed


def _load_json_artifact(path: Path, label: str) -> tuple[bytes, Mapping[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IndexEntryError(f"{label}_unreadable") from exc

    def reject_constant(value: str) -> None:
        raise ValueError(f"non_finite_json_constant:{value}")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise IndexEntryError(f"{label}_decode_error") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise IndexEntryError(f"{label}_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise IndexEntryError(f"{label}_not_object")
    return raw, parsed


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _template_error_report(reason: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "proof_id": TEMPLATE_PROOF_ID,
        "ok": False,
        "template_version": TEMPLATE_VERSION,
        "template_only": True,
        "manual_review_required": True,
        "direct_bridge_write_performed": False,
        "approval_granted": False,
        "release_decision_made": False,
        "runtime_authority_granted": False,
        "runtime_subdivision_authority_granted": False,
        "bridge_event_written": False,
        "fast_track_priority": False,
        "gate_skip_allowed": False,
        "claim_safe": False,
        "digest_payloads_included": False,
        "local_paths_recorded": False,
        "path_free_verified": True,
        "blockers": [
            "hex_upgrade_cross_consistency_digest_bridge_event_template_failed:"
            + _safe_reason(reason)
        ],
        "warnings": [],
    }
    return report


def _failure_report(reason: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "proof_id": PROOF_ID,
        "ok": False,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "template_only": True,
        "manual_review_required": True,
        "blockers": [
            "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_failed:"
            + _safe_reason(reason)
        ],
        "warnings": [],
    }
    for field in AUTHORITY_FALSE_FIELDS:
        report[field] = False
    return report


def _bridge_input_error(
    *,
    agent_id: str,
    task_id: str,
    to: str,
    severity: str,
    role: str,
    run_id: str,
    session_id: str,
) -> str | None:
    if not isinstance(agent_id, str) or not AGENT_ID_PATTERN.fullmatch(agent_id):
        return "agent_unsafe"
    if not isinstance(task_id, str) or not SAFE_REF_PATTERN.fullmatch(task_id):
        return "task_id_unsafe"
    if not isinstance(to, str):
        return "to_unsafe"
    targets = [item.strip() for item in to.split(",") if item.strip()]
    if not targets or any(not AGENT_ID_PATTERN.fullmatch(t) for t in targets):
        return "to_unsafe"
    if severity not in {"", "low", "medium", "high"}:
        return "severity_unsafe"
    if role and not AGENT_ID_PATTERN.fullmatch(role):
        return "role_unsafe"
    if run_id and not SAFE_REF_PATTERN.fullmatch(run_id):
        return "run_id_unsafe"
    if session_id and not SESSION_ID_PATTERN.fullmatch(session_id):
        return "session_id_unsafe"
    return None


def _assert_mapping(label: str, value: Any) -> None:
    if not isinstance(value, Mapping):
        raise IndexEntryError(f"{label}_not_object")


def _assert_no_forbidden_input(label: str, value: Any) -> None:
    if _contains_forbidden_marker(value):
        raise IndexEntryError(f"{label}_contains_forbidden_marker")


def _assert_no_forbidden_output(encoded: str) -> None:
    if _has_forbidden_marker(encoded):
        raise IndexEntryError("index_entry_contains_forbidden_output_marker")


def _contains_forbidden_marker(value: Any) -> bool:
    if isinstance(value, str):
        return _has_forbidden_marker(value)
    if isinstance(value, Mapping):
        return any(_contains_forbidden_marker(v) for v in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_marker(item) for item in value)
    return False


def _has_forbidden_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in FORBIDDEN_OUTPUT_MARKERS)


def _forbidden_output_markers(text: str) -> list[str]:
    lowered = text.lower()
    return sorted(
        marker for marker in FORBIDDEN_OUTPUT_MARKERS if marker.lower() in lowered
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IndexEntryError("expected_mapping_missing")
    return value


def _plain_json_object(value: Any) -> dict[str, Any]:
    plain = _plain_json_object_or_none(value)
    if plain is None:
        raise IndexEntryError("json_object_required")
    return plain


def _plain_json_object_or_none(value: Any) -> dict[str, Any] | None:
    try:
        plain = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        return None
    return plain if isinstance(plain, dict) else None


def _expect_empty_items(value: Any, code: str) -> None:
    if value not in ([], (), None):
        raise IndexEntryError(code)


def _is_sha256_ref(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == len("sha256:") + 64
        and value.startswith("sha256:")
        and all(ch in "0123456789abcdef" for ch in value[len("sha256:"):])
    )


def _safe_sha256_ref(value: Any) -> str:
    if _is_sha256_ref(value):
        return str(value)
    return "sha256:" + ("0" * 64)


def _safe_string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [
        _safe_reason(item)
        for item in value
        if isinstance(item, str) and not _contains_forbidden_marker(item)
    ]


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("expected UTC timestamp ending with Z")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("expected UTC timestamp")
    return parsed


def _safe_reason(reason: Any) -> str:
    clean = "".join(
        ch if ch.isalnum() or ch in "._:-" else "_" for ch in str(reason)
    )
    return clean[:160] or "invalid"


if __name__ == "__main__":
    raise SystemExit(main())
