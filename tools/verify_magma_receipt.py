# SPDX-License-Identifier: BUSL-1.1
"""Offline verifier for MAGMA receipt fixture chains.

v1 intentionally avoids signing, anchoring, network calls, and runtime
integration. It verifies the thin spine: schema shape, canonical digest
bindings, EvaluationResult binding, and receipt hash-chain topology.
Unsigned v1 detects in-chain receipt tampering; tail receipt field tamper needs
a successor receipt or a future signature verifier.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys
from typing import Any, Sequence

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.chat_served_receipt import (  # noqa: E402
    validate_chat_served_payload,
)
from waggledance.core.magma.schema_validation import redacted_schema_errors  # noqa: E402


SCHEMA_DIR = ROOT / "schemas" / "v3_13_0"
EVALUATION_SCHEMA_BY_VERSION = {
    "magma.evaluation_result.v0": "evaluation_result.v0.json",
    "magma.evaluation_result.v1": "evaluation_result.v1.json",
}


# ── Chat-served (informational, no-governance-gate) receipt enforcement ──
#
# A chat-served response (waggledance.core.magma.chat_served_receipt) runs NO
# RCO/approval gate and has NO solver contract, so its rco_decision_digest and
# solver_contract_digest carry fixed "not-applicable" sentinels rather than a
# real content digest. Those receipt fields are constrained to
# ^sha256:[a-f0-9]{64}$, so a forged *real-looking* value is SCHEMA-VALID -- the
# only thing that stops a chat receipt from masquerading a governed decision is
# requiring the exact known sentinel here. We RE-DERIVE the expected sentinels
# INDEPENDENTLY from the same self-describing preimage (we do NOT import the
# builder's constant), so the verifier recomputes and REQUIRES the value instead
# of trusting the emitter. An anti-drift test asserts this re-derivation equals
# the builder's constant.
_CHAT_SERVED_LEGACY_PAYLOAD_VERSION = "magma.chat_served_receipt_payload.v0"
_CHAT_SERVED_PAYLOAD_VERSION = "magma.chat_served_receipt_payload.v1"
_CHAT_SERVED_PAYLOAD_VERSIONS = frozenset(
    {_CHAT_SERVED_LEGACY_PAYLOAD_VERSION, _CHAT_SERVED_PAYLOAD_VERSION}
)
_CHAT_SERVED_CHAIN_ID = "magma:chat_service:served:v0"
_CHAT_SERVED_EVENT_PREFIX = "magma:chat_served:"
_CHAT_SERVED_CASE_PREFIX = "case:chat_served:"
_CHAT_SERVED_EVENT_RE = re.compile(
    r"^magma:chat_served:(?P<timestamp>[0-9]{8}T[0-9]{12}Z)-[0-9]{6}$"
)
_CHAT_PAYLOAD_FINGERPRINT_KEYS = frozenset(
    {
        "query_digest",
        "response_digest",
        "route_stage_trace",
        "route_stage_trace_digest",
        "digest_semantics",
    }
)
_MANIFEST_KEYS = frozenset({"chain_id", "entries"})
_ENTRY_KEYS = frozenset({"receipt", "payload", "evaluation_result"})
_CHAT_RCO_DECISION_NA_SENTINEL = sha256_digest(
    {
        "not_applicable": True,
        "route_class": "chat",
        "field": "rco_decision_digest",
        "reason": "no_rco_decision_gate",
    }
)
_CHAT_SOLVER_CONTRACT_NA_SENTINEL = sha256_digest(
    {
        "not_applicable": True,
        "route_class": "chat",
        "field": "solver_contract_digest",
        "reason": "no_solver_contract",
    }
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify an offline MAGMA receipt manifest.",
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--policy-surface",
        type=Path,
        default=None,
        help=(
            "Validate a Policy Surface v0 artifact and require each receipt "
            "to bind its policy_digest and charter_digest."
        ),
    )
    parser.add_argument("--expected-charter-digest", default=None)
    parser.add_argument("--expected-policy-digest", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = verify_manifest(
        args.manifest,
        policy_surface_path=args.policy_surface,
        expected_charter_digest=args.expected_charter_digest,
        expected_policy_digest=args.expected_policy_digest,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True))
    elif report["ok"]:
        print(f"magma receipt verification OK: {report['receipt_count']} receipts")
    else:
        print(
            f"magma receipt verification FAILED: {len(report['errors'])} errors",
            file=sys.stderr,
        )
        for error in report["errors"]:
            print(f"- {error}", file=sys.stderr)
    return 0 if report["ok"] else 1


def verify_manifest(
    manifest_path: Path,
    *,
    policy_surface_path: Path | None = None,
    expected_charter_digest: str | None = None,
    expected_policy_digest: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    manifest_path = manifest_path.resolve()
    manifest = _read_json(manifest_path, errors, "manifest")
    entries = _entries(manifest, errors)
    receipt_validator = _validator("magma_receipt.v1.json")
    policy_surface = _policy_surface_binding(policy_surface_path, errors)
    if policy_surface is not None:
        if (
            expected_policy_digest is not None
            and expected_policy_digest != policy_surface["policy_digest"]
        ):
            errors.append("policy_surface: expected_policy_digest argument mismatch")
        if (
            expected_charter_digest is not None
            and expected_charter_digest != policy_surface["charter_digest"]
        ):
            errors.append("policy_surface: expected_charter_digest argument mismatch")
        expected_policy_digest = policy_surface["policy_digest"]
        expected_charter_digest = policy_surface["charter_digest"]
    verified = 0
    nodes: list[dict[str, str | None]] = []
    payload_versions: list[Any] = []
    chat_entry_flags: list[bool] = []
    manifest_chain_id = (
        manifest.get("chain_id") if isinstance(manifest, dict) else None
    )
    manifest_entry_count = (
        len(manifest.get("entries", [])) if isinstance(manifest, dict) else 0
    )

    for index, entry in enumerate(entries, 1):
        label = f"entry {index}"
        receipt_path = _entry_path(manifest_path, entry, "receipt", errors, label)
        payload_path = _entry_path(manifest_path, entry, "payload", errors, label)
        evaluation_path = _entry_path(
            manifest_path, entry, "evaluation_result", errors, label
        )
        if receipt_path is None or payload_path is None or evaluation_path is None:
            continue

        receipt = _read_json(receipt_path, errors, label)
        payload = _read_json(payload_path, errors, label)
        evaluation = _read_json(evaluation_path, errors, label)
        payload_versions.append(
            payload.get("payload_version") if isinstance(payload, dict) else None
        )
        chat_entry_flags.append(
            _looks_like_chat_served(
                manifest_chain_id=manifest_chain_id,
                receipt=receipt,
                payload=payload,
                evaluation=evaluation,
            )
        )
        invalid_object = False
        for artifact_name, artifact in (
            ("receipt", receipt),
            ("payload", payload),
            ("evaluation_result", evaluation),
        ):
            if not isinstance(artifact, dict):
                errors.append(f"{label}: {artifact_name} must be a JSON object")
                invalid_object = True
        if invalid_object:
            continue

        _validate_schema(receipt_validator, receipt, errors, f"{label} receipt")
        _validate_evaluation_result(evaluation, errors, f"{label} evaluation_result")

        payload_digest = _safe_sha256_digest(
            payload, errors, f"{label}: payload is not canonically hashable"
        )
        evaluation_digest = _safe_sha256_digest(
            evaluation,
            errors,
            f"{label}: evaluation_result is not canonically hashable",
        )
        if payload_digest is not None:
            if receipt.get("canonical_payload_digest") != payload_digest:
                errors.append(f"{label}: canonical_payload_digest mismatch")
            if evaluation.get("target_digest") != payload_digest:
                errors.append(f"{label}: evaluation target_digest mismatch")
        if evaluation.get("risk_class") != receipt.get("risk_class"):
            errors.append(f"{label}: evaluation risk_class mismatch")

        if (
            evaluation_digest is not None
            and receipt.get("evaluation_result_digest") != evaluation_digest
        ):
            errors.append(f"{label}: evaluation_result_digest mismatch")

        if (
            expected_charter_digest is not None
            and receipt.get("charter_digest") != expected_charter_digest
        ):
            errors.append(f"{label}: charter_digest mismatch")
        if (
            expected_policy_digest is not None
            and receipt.get("policy_digest") != expected_policy_digest
        ):
            errors.append(f"{label}: policy_digest mismatch")

        if chat_entry_flags[-1]:
            _enforce_chat_served_contract(
                receipt,
                payload,
                evaluation,
                payload_digest,
                evaluation_digest,
                errors,
                label,
            )

        receipt_hash = _safe_sha256_digest(
            receipt, errors, f"{label}: receipt is not canonically hashable"
        )
        if receipt_hash is not None:
            nodes.append(
                {
                    "node_id": label,
                    "receipt_hash": receipt_hash,
                    "prev_receipt_hash": receipt.get("prev_receipt_hash"),
                }
            )
        verified += 1

    if manifest_chain_id == _CHAT_SERVED_CHAIN_ID or any(chat_entry_flags):
        _enforce_current_chat_manifest(
            manifest,
            entries,
            manifest_entry_count,
            payload_versions,
            chat_entry_flags,
            errors,
        )
    _validate_chain_topology(nodes, errors)

    return {
        "ok": not errors,
        "manifest": "<redacted>",
        "chain_id": "<redacted>" if isinstance(manifest, dict) and "chain_id" in manifest else None,
        "receipt_count": verified,
        "policy_surface": policy_surface,
        "errors": errors,
    }


def _validator(schema_name: str) -> jsonschema.Draft7Validator:
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )


def _enforce_chat_served_contract(
    receipt: dict[str, Any],
    payload: dict[str, Any],
    evaluation: dict[str, Any],
    payload_digest: str | None,
    evaluation_digest: str | None,
    errors: list[str],
    label: str,
) -> None:
    """Validate current chat payload privacy and all chat N/A sentinels.

    Chat classification uses the closed chain id plus independent receipt,
    payload, and evaluation markers, so changing only the payload version cannot
    downgrade out of this contract. A chat-served receipt runs no RCO/approval
    gate and has no solver contract. Its ``rco_decision_digest`` and
    ``solver_contract_digest`` MUST therefore equal the fixed N/A sentinels; any
    other (real-looking) value would let a chat path masquerade a governed
    decision/contract and is rejected. Only the current strict payload version
    is accepted; legacy v0 remains identifiable but is not proof-eligible.
    """
    try:
        validate_chat_served_payload(payload)
    except (TypeError, ValueError, OverflowError):
        # Never reflect payload values into a verifier report. The public
        # validator's detailed exception remains available to trusted callers.
        errors.append(f"{label}: invalid or unsupported chat_served payload")
    _enforce_chat_served_evaluation(
        evaluation, payload, payload_digest, errors, label
    )
    _enforce_chat_served_receipt(
        receipt, payload, evaluation_digest, errors, label
    )


def _enforce_chat_served_evaluation(
    evaluation: dict[str, Any],
    payload: dict[str, Any],
    payload_digest: str | None,
    errors: list[str],
    label: str,
) -> None:
    route_type = payload.get("route_type")
    source = payload.get("source")
    cached = payload.get("cached")
    expected = {
        "evaluation_version": "magma.evaluation_result.v0",
        "case_id": f"case:chat_served:{route_type}",
        "subject_type": "policy",
        "target_digest": payload_digest,
        "risk_class": "informational",
        "expected_gate": "allow",
        "actual_gate": "allow",
        "verifier_path": [
            "chat_service_handle",
            "chat_served_payload_v1",
            "magma_receipt_v1",
            "offline_receipt_verifier",
        ],
        "solver_selection": ["solver"] if route_type == "solver" else [],
        "policy_version": "policy:chat_service_route:v0",
        "charter_version": "charter:runtime_truth:v1",
        "domain_threshold_version": "threshold:chat_service_route:v0",
        "verdict": "abstain",
        "reason_codes": [
            "chat_served",
            "informational_no_governance_gate",
            f"route_type:{route_type}",
            f"source:{source}",
            "cached:true" if cached is True else "cached:false",
        ],
        "operator_required": False,
        "confidence_score": payload.get("confidence"),
        "uncertainty_sources": [],
    }
    if evaluation != expected:
        errors.append(f"{label}: invalid chat_served evaluation_result")


def _enforce_chat_served_receipt(
    receipt: dict[str, Any],
    payload: dict[str, Any],
    evaluation_digest: str | None,
    errors: list[str],
    label: str,
) -> None:
    event_id = receipt.get("event_id")
    event_match = (
        _CHAT_SERVED_EVENT_RE.fullmatch(event_id)
        if isinstance(event_id, str)
        else None
    )
    if event_match is None:
        errors.append(f"{label}: invalid chat_served event_id")
    elif not _chat_timestamp_matches(
        event_match.group("timestamp"), receipt.get("ts_utc")
    ):
        errors.append(f"{label}: chat_served event_id timestamp mismatch")
    if receipt.get("risk_class") != "informational":
        errors.append(
            f"{label}: chat_served receipt must be risk_class=informational"
        )
    if receipt.get("rco_decision_digest") != _CHAT_RCO_DECISION_NA_SENTINEL:
        errors.append(
            f"{label}: chat_served rco_decision_digest must be the N/A sentinel "
            "(a chat path runs no RCO/approval gate)"
        )
    if receipt.get("solver_contract_digest") != _CHAT_SOLVER_CONTRACT_NA_SENTINEL:
        errors.append(
            f"{label}: chat_served solver_contract_digest must be the N/A sentinel "
            "(a chat path has no solver contract)"
        )
    expected_fields = {
        "payload_visibility": "digest_only",
        "policy_digest": sha256_digest(
            {"policy_version": "policy:chat_service_route:v0"}
        ),
        "charter_digest": sha256_digest(
            {"charter_version": "charter:runtime_truth:v1"}
        ),
        "world_snapshot_digest": sha256_digest(
            {
                "profile": payload.get("profile"),
                "world_snapshot_ref": payload.get("world_snapshot_ref"),
            }
        ),
        "approval_id": None,
        "operator_gate_required": False,
        "signature_algorithm": None,
        "signature": None,
        "key_id": None,
        "anchored_at": None,
        "evaluation_result_digest": evaluation_digest,
    }
    if any(receipt.get(key) != value for key, value in expected_fields.items()):
        errors.append(f"{label}: invalid chat_served receipt envelope")


def _enforce_current_chat_manifest(
    manifest: Any,
    entries: list[dict[str, Any]],
    manifest_entry_count: int,
    payload_versions: list[Any],
    chat_entry_flags: list[bool],
    errors: list[str],
) -> None:
    """Require the closed bundle shape only for current strict chat payloads."""
    if not isinstance(manifest, dict):
        return
    if set(manifest) != _MANIFEST_KEYS:
        errors.append(
            "chat_served manifest must contain exactly chain_id and entries"
        )
    if manifest.get("chain_id") != _CHAT_SERVED_CHAIN_ID:
        errors.append("chat_served chain_id mismatch")
    if (
        manifest_entry_count != len(payload_versions)
        or len(payload_versions) != len(chat_entry_flags)
        or not all(chat_entry_flags)
        or any(
            version != _CHAT_SERVED_PAYLOAD_VERSION
            for version in payload_versions
        )
    ):
        errors.append("chat_served manifest entries must all use payload v1")
    for index, entry in enumerate(entries, 1):
        if set(entry) != _ENTRY_KEYS:
            errors.append(
                f"entry {index}: chat_served entry must contain exactly receipt, "
                "payload, and evaluation_result"
            )


def _looks_like_chat_served(
    *,
    manifest_chain_id: Any,
    receipt: Any,
    payload: Any,
    evaluation: Any,
) -> bool:
    if manifest_chain_id == _CHAT_SERVED_CHAIN_ID:
        return True
    if isinstance(payload, dict):
        if _CHAT_PAYLOAD_FINGERPRINT_KEYS.issubset(payload):
            return True
        if payload.get("payload_version") in _CHAT_SERVED_PAYLOAD_VERSIONS:
            return True
        if payload.get("served_path") == "ChatService.handle":
            return True
    if isinstance(receipt, dict):
        event_id = receipt.get("event_id")
        if isinstance(event_id, str) and event_id.startswith(
            _CHAT_SERVED_EVENT_PREFIX
        ):
            return True
        if (
            receipt.get("rco_decision_digest")
            == _CHAT_RCO_DECISION_NA_SENTINEL
            and receipt.get("solver_contract_digest")
            == _CHAT_SOLVER_CONTRACT_NA_SENTINEL
        ):
            return True
    if isinstance(evaluation, dict):
        case_id = evaluation.get("case_id")
        if isinstance(case_id, str) and case_id.startswith(
            _CHAT_SERVED_CASE_PREFIX
        ):
            return True
        reason_codes = evaluation.get("reason_codes")
        if isinstance(reason_codes, list) and "chat_served" in reason_codes:
            return True
    return False


def _chat_timestamp_matches(event_timestamp: str, receipt_timestamp: Any) -> bool:
    if not isinstance(receipt_timestamp, str):
        return False
    try:
        event_time = datetime.strptime(
            event_timestamp, "%Y%m%dT%H%M%S%fZ"
        ).replace(tzinfo=timezone.utc)
        receipt_time = datetime.fromisoformat(
            receipt_timestamp.replace("Z", "+00:00")
        )
    except ValueError:
        return False
    if receipt_time.tzinfo is None:
        return False
    return event_time == receipt_time.astimezone(timezone.utc)


def _validate_evaluation_result(
    evaluation: dict[str, Any],
    errors: list[str],
    label: str,
) -> None:
    version = evaluation.get("evaluation_version")
    schema_name = EVALUATION_SCHEMA_BY_VERSION.get(version)
    if schema_name is None:
        expected = ", ".join(sorted(EVALUATION_SCHEMA_BY_VERSION))
        errors.append(f"{label}: unknown evaluation_version; expected one of: {expected}")
        return
    _validate_schema(_validator(schema_name), evaluation, errors, label)


def _policy_surface_binding(
    policy_surface_path: Path | None,
    errors: list[str],
) -> dict[str, Any] | None:
    if policy_surface_path is None:
        return None
    path = policy_surface_path.resolve()
    policy = _read_json(path, errors, "policy_surface")
    if not isinstance(policy, dict):
        return None

    before_errors = len(errors)
    _validate_schema(
        _validator("policy_surface.v0.json"),
        policy,
        errors,
        "policy_surface",
    )
    if errors[before_errors:]:
        return None

    bindings = policy.get("digest_bindings", {})
    if not isinstance(bindings, dict):
        errors.append("policy_surface: digest_bindings must be an object")
        return None
    canonicalization = bindings.get("canonicalization")
    if canonicalization != "magma-jcs-subset-v1":
        errors.append(
            "policy_surface: canonicalization must be magma-jcs-subset-v1"
        )
        return None

    charter_sections = policy.get("charter_sections")
    if not isinstance(charter_sections, list):
        errors.append("policy_surface: charter_sections must be an array")
        return None
    return {
        "provided": True,
        "policy_id": "<redacted>",
        "canonicalization": str(canonicalization),
        "policy_digest": sha256_digest(policy),
        "charter_digest": sha256_digest(charter_sections),
    }


def _read_json(path: Path, errors: list[str], label: str) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_nonfinite_json_constant,
        )
    except OSError as exc:
        errors.append(f"{label}: cannot read JSON file ({exc.__class__.__name__})")
    except json.JSONDecodeError as exc:
        errors.append(f"{label}: invalid JSON at line {exc.lineno} column {exc.colno}")
    except ValueError:
        errors.append(f"{label}: invalid JSON non-finite numeric constant")
    return None


def _reject_nonfinite_json_constant(_: str) -> None:
    raise ValueError("non-finite JSON numeric constant")


def _safe_sha256_digest(
    value: Any,
    errors: list[str],
    error: str,
) -> str | None:
    try:
        return sha256_digest(value)
    except (TypeError, ValueError, OverflowError):
        errors.append(error)
        return None


def _entries(manifest: Any, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict):
        errors.append("manifest: must be a JSON object")
        return []
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("manifest: entries must be a non-empty array")
        return []

    normalized: list[dict[str, Any]] = []
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            errors.append(f"entry {index}: must be an object")
            continue
        missing = [
            field
            for field in ("receipt", "payload", "evaluation_result")
            if not isinstance(entry.get(field), str) or not entry.get(field)
        ]
        if missing:
            errors.append(f"entry {index}: missing path fields: {', '.join(missing)}")
            continue
        normalized.append(entry)
    return normalized


def _entry_path(
    manifest_path: Path,
    entry: dict[str, Any],
    field: str,
    errors: list[str],
    label: str,
) -> Path | None:
    raw_path = entry[field]
    context = f"{label}: {field}"
    if "\\" in raw_path:
        errors.append(f"{context} path must use POSIX separators")
        return None
    if (
        PurePosixPath(raw_path).is_absolute()
        or PureWindowsPath(raw_path).is_absolute()
    ):
        errors.append(f"{context} path must be relative")
        return None
    parts = raw_path.split("/")
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


def _validate_chain_topology(
    nodes: list[dict[str, str | None]],
    errors: list[str],
) -> None:
    if not nodes:
        return

    by_hash: dict[str, dict[str, str | None]] = {}
    duplicate_hashes: set[str] = set()
    for node in nodes:
        receipt_hash = str(node["receipt_hash"])
        if receipt_hash in by_hash:
            duplicate_hashes.add(receipt_hash)
        by_hash[receipt_hash] = node
    for receipt_hash in sorted(duplicate_hashes):
        errors.append(f"chain: duplicate_receipt_hash {receipt_hash}")

    genesis = [node for node in nodes if node["prev_receipt_hash"] is None]
    if not genesis:
        errors.append("chain: no_genesis")
    elif len(genesis) > 1:
        node_ids = ", ".join(str(node["node_id"]) for node in genesis)
        errors.append(f"chain: multiple_genesis {node_ids}")

    children_by_prev: dict[str, list[dict[str, str | None]]] = {}
    for node in nodes:
        prev_hash = node["prev_receipt_hash"]
        if prev_hash is None:
            continue
        if prev_hash not in by_hash:
            errors.append(f"chain: missing_parent for {node['node_id']}")
        children_by_prev.setdefault(str(prev_hash), []).append(node)

    for prev_hash, children in sorted(children_by_prev.items()):
        if len(children) > 1:
            node_ids = ", ".join(str(node["node_id"]) for node in children)
            errors.append(f"chain: ambiguous_child -> {node_ids}")

    _detect_parent_cycle(nodes, by_hash, errors)
    if len(genesis) != 1:
        return

    visited: set[str] = set()
    current = genesis[0]
    while True:
        receipt_hash = str(current["receipt_hash"])
        if receipt_hash in visited:
            errors.append(f"chain: cycle_detected at {current['node_id']}")
            break
        visited.add(receipt_hash)
        children = children_by_prev.get(receipt_hash, [])
        if len(children) != 1:
            break
        current = children[0]

    orphans = [
        str(node["node_id"])
        for node in nodes
        if str(node["receipt_hash"]) not in visited
    ]
    if orphans:
        errors.append(f"chain: orphan_receipt {', '.join(orphans)}")


def _detect_parent_cycle(
    nodes: list[dict[str, str | None]],
    by_hash: dict[str, dict[str, str | None]],
    errors: list[str],
) -> None:
    for node in nodes:
        seen: set[str] = set()
        current = node
        while True:
            receipt_hash = str(current["receipt_hash"])
            if receipt_hash in seen:
                errors.append(f"chain: cycle_detected at {current['node_id']}")
                return
            seen.add(receipt_hash)
            prev_hash = current["prev_receipt_hash"]
            if prev_hash is None or prev_hash not in by_hash:
                break
            current = by_hash[str(prev_hash)]


def _validate_schema(
    validator: jsonschema.Draft7Validator,
    value: dict[str, Any],
    errors: list[str],
    label: str,
) -> None:
    errors.extend(redacted_schema_errors(validator, value, label))


if __name__ == "__main__":
    raise SystemExit(main())
