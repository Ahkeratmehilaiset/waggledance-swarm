# SPDX-License-Identifier: BUSL-1.1
"""Validate agent bridge events.jsonl against the v1 bridge event schema."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.bridge_event_schema import (
    AGENT_ID_PATTERN,
    AGENT_UUID_PATTERN,
    BridgeEventValidationResult,
    KNOWN_EVENT_TYPES,
    validate_event_file,
)
from waggledance.core.work_queue import resolve_bridge_root


DEFAULT_WAIVERS_PATH = ROOT / "configs" / "bridge_event_validation_waivers.json"
IDENTITY_AUDIT_VERSION = "bridge-identity-registry-audit.v2"
EVENT_HYGIENE_AUDIT_VERSION = "bridge-event-hygiene-audit.v1"
GATE_RELEVANT_STATUSES = frozenset({
    "autonomous_merge_receipt",
    "build_consensus_pass",
    "merged_operator_authorized",
    "operator_authorized",
    "rco_pass",
})
SUSPICIOUS_PAYLOAD_KEY_MARKERS = (
    "creds",
    "do_not_leak",
    "password",
    "private_marker",
    "secret",
    "token",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate agent bridge JSONL events.",
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=None,
        help="Path to bridge events.jsonl (default: <bridge-root>/shared/events.jsonl).",
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
    parser.add_argument(
        "--tail",
        type=int,
        default=None,
        help="Validate only the last N non-empty physical lines.",
    )
    parser.add_argument(
        "--max-errors",
        type=int,
        default=20,
        help="Maximum validation issues to include in output.",
    )
    parser.add_argument(
        "--waivers",
        type=Path,
        default=DEFAULT_WAIVERS_PATH,
        help="Known-invalid historical event waivers JSON.",
    )
    parser.add_argument(
        "--no-waivers",
        action="store_true",
        help="Disable known-invalid historical event waivers.",
    )
    parser.add_argument(
        "--agent-profiles",
        type=Path,
        default=None,
        help=(
            "Optional .agent-bridge/agents directory. When supplied, events "
            "from profiled agents must carry the profile's agent_uuid."
        ),
    )
    parser.add_argument(
        "--identity-registry",
        type=Path,
        default=None,
        help=(
            "Optional configs/bridge_identity_registry.json path. When supplied, "
            "emit a registered-agent UUID hygiene audit summary."
        ),
    )
    parser.add_argument(
        "--identity-registry-mode",
        choices=["warn", "strict"],
        default="warn",
        help=(
            "Whether identity-registry UUID audit findings are warnings or "
            "non-zero failures. Defaults to warn."
        ),
    )
    parser.add_argument(
        "--event-hygiene-mode",
        choices=["warn", "strict"],
        default="warn",
        help=(
            "Whether unknown event types, non-object payloads, missing payload "
            "fields, and sensitive-looking payload key names are warnings or "
            "non-zero failures. Defaults to warn."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON summary.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    bridge_root = resolve_bridge_root(args.bridge_root)
    events_path = args.events or bridge_root / "shared" / "events.jsonl"
    if not events_path.exists():
        result = BridgeEventValidationResult(
            schema_version="agent-bridge-event.v1",
            checked=0,
            valid=0,
            invalid=1,
            issues=(),
        )
        _print_result(result, json_output=args.json, missing_path=events_path)
        return 1

    try:
        waiver_hashes, waiver_errors = (
            ({}, {}) if args.no_waivers else _load_waivers(args.waivers)
        )
    except ValueError as exc:
        print(f"bridge event schema FAILED: invalid waiver file: {exc}", file=sys.stderr)
        return 2

    try:
        agent_uuid_by_id = _load_agent_profile_uuids(args.agent_profiles)
    except ValueError as exc:
        print(f"bridge event schema FAILED: invalid agent profiles: {exc}", file=sys.stderr)
        return 2

    try:
        identity_registry_uuids = _load_identity_registry_uuids(
            args.identity_registry,
        )
    except ValueError as exc:
        print(
            f"bridge event schema FAILED: invalid identity registry: {exc}",
            file=sys.stderr,
        )
        return 2

    result = validate_event_file(
        events_path,
        tail=args.tail,
        max_errors=args.max_errors,
        waived_line_sha256=waiver_hashes,
        waived_line_errors=waiver_errors,
        agent_uuid_by_id=agent_uuid_by_id,
    )
    identity_registry_audit = None
    if args.identity_registry is not None:
        identity_registry_audit = _audit_identity_registry_uuids(
            events_path,
            identity_registry_uuids,
            tail=args.tail,
            max_examples=args.max_errors,
            mode=args.identity_registry_mode,
            registry_path=args.identity_registry,
        )
    event_hygiene_audit = _audit_event_hygiene(
        events_path,
        tail=args.tail,
        max_examples=args.max_errors,
        mode=args.event_hygiene_mode,
    )
    _print_result(
        result,
        json_output=args.json,
        waivers_path=None if args.no_waivers else args.waivers,
        waivers_loaded=len(waiver_hashes),
        agent_profiles_path=args.agent_profiles,
        agent_profiles_loaded=len(agent_uuid_by_id),
        identity_registry_audit=identity_registry_audit,
        event_hygiene_audit=event_hygiene_audit,
    )
    identity_registry_ok = (
        identity_registry_audit is None
        or args.identity_registry_mode == "warn"
        or bool(identity_registry_audit["ok"])
    )
    event_hygiene_ok = (
        args.event_hygiene_mode == "warn"
        or bool(event_hygiene_audit["ok"])
    )
    return 0 if result.ok and identity_registry_ok and event_hygiene_ok else 1


def _load_waivers(path: Path) -> tuple[dict[int, str], dict[int, str]]:
    if not path.exists():
        return {}, {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc.msg}") from exc
    entries = payload.get("waivers") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f"{path}: expected object with waivers list")
    waiver_hashes: dict[int, str] = {}
    waiver_errors: dict[int, str] = {}
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: waiver {index} must be an object")
        line_no = entry.get("line_no")
        raw_sha256 = entry.get("raw_line_sha256")
        error = entry.get("error")
        reason = entry.get("reason")
        if not isinstance(line_no, int) or line_no <= 0:
            raise ValueError(f"{path}: waiver {index} line_no must be a positive integer")
        if not isinstance(raw_sha256, str) or not _is_sha256_digest(raw_sha256):
            raise ValueError(
                f"{path}: waiver {index} raw_line_sha256 must be sha256:<64 hex>"
            )
        if not isinstance(error, str) or not error.strip():
            raise ValueError(f"{path}: waiver {index} error must be non-empty")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{path}: waiver {index} reason must be non-empty")
        waiver_hashes[line_no] = raw_sha256
        waiver_errors[line_no] = error
    return waiver_hashes, waiver_errors


def _is_sha256_digest(value: str) -> bool:
    if not value.startswith("sha256:") or len(value) != len("sha256:") + 64:
        return False
    return all(char in "0123456789abcdef" for char in value.removeprefix("sha256:"))


def _load_agent_profile_uuids(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    if not path.exists():
        return {}
    if not path.is_dir():
        raise ValueError(f"{path}: expected directory")
    profiles: dict[str, str] = {}
    for profile_path in sorted(path.glob("*.json")):
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{profile_path}: invalid JSON: {exc.msg}") from exc
        if not isinstance(profile, dict):
            raise ValueError(f"{profile_path}: profile must be a JSON object")
        agent_id = profile.get("agent_id")
        if not isinstance(agent_id, str) or not re.fullmatch(AGENT_ID_PATTERN, agent_id):
            raise ValueError(f"{profile_path}: agent_id must match bridge agent id")
        if profile_path.stem != agent_id:
            raise ValueError(f"{profile_path}: filename must match agent_id")
        agent_uuid = profile.get("agent_uuid", "")
        if not agent_uuid:
            continue
        if not isinstance(agent_uuid, str) or not re.fullmatch(
            AGENT_UUID_PATTERN,
            agent_uuid,
        ):
            raise ValueError(f"{profile_path}: agent_uuid must be a UUID")
        profiles[agent_id] = agent_uuid
    return profiles


def _load_identity_registry_uuids(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    if not path.exists():
        raise ValueError(f"{path}: missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: invalid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: registry must be a JSON object")
    identities = payload.get("identities")
    if not isinstance(identities, dict):
        raise ValueError(f"{path}: identities must be an object")
    registry: dict[str, str] = {}
    uuid_owners: dict[str, str] = {}
    for agent_id, agent_uuid in sorted(identities.items()):
        if not isinstance(agent_id, str) or not re.fullmatch(
            AGENT_ID_PATTERN,
            agent_id,
        ):
            raise ValueError(f"{path}: identity key must match bridge agent id")
        if not isinstance(agent_uuid, str) or not re.fullmatch(
            AGENT_UUID_PATTERN,
            agent_uuid,
        ):
            raise ValueError(f"{path}: {agent_id} value must be a UUID")
        normalized_uuid = agent_uuid.casefold()
        prior_owner = uuid_owners.get(normalized_uuid)
        if prior_owner is not None:
            raise ValueError(
                f"{path}: UUID reused by {prior_owner} and {agent_id}"
            )
        uuid_owners[normalized_uuid] = agent_id
        registry[agent_id] = agent_uuid
    return registry


def _audit_identity_registry_uuids(
    events_path: Path,
    registry: Mapping[str, str],
    *,
    tail: int | None,
    max_examples: int,
    mode: str,
    registry_path: Path,
) -> dict[str, Any]:
    checked = 0
    registered = 0
    non_registry = 0
    missing = 0
    mismatched = 0
    aliased = 0
    gate_missing = 0
    gate_mismatched = 0
    gate_aliased = 0
    skipped_unreadable = 0
    examples: list[dict[str, Any]] = []
    lines = _select_event_lines(
        events_path.read_text(encoding="utf-8").splitlines(),
        tail=tail,
    )
    uuid_owners = {
        registered_uuid.casefold(): registered_agent
        for registered_agent, registered_uuid in registry.items()
    }
    for line_no, line in lines:
        if not line.strip():
            continue
        checked += 1
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            skipped_unreadable += 1
            continue
        if not isinstance(event, dict):
            skipped_unreadable += 1
            continue
        agent = event.get("agent")
        if not isinstance(agent, str):
            skipped_unreadable += 1
            continue
        observed_uuid = event.get("agent_uuid")
        status = event.get("status")
        event_type = event.get("type")
        gate_relevant = (
            isinstance(status, str)
            and status in GATE_RELEVANT_STATUSES
        )
        expected_uuid = registry.get(agent)
        if not expected_uuid:
            non_registry += 1
            registered_uuid_owner = (
                uuid_owners.get(observed_uuid.casefold())
                if isinstance(observed_uuid, str)
                else None
            )
            if registered_uuid_owner is not None:
                aliased += 1
                if gate_relevant:
                    gate_aliased += 1
                _append_identity_example(
                    examples,
                    max_examples=max_examples,
                    line_no=line_no,
                    agent=agent,
                    status=status,
                    event_type=event_type,
                    reason="registered_uuid_alias",
                    gate_relevant=gate_relevant,
                    registered_uuid_owner=registered_uuid_owner,
                )
            continue
        registered += 1
        if not observed_uuid:
            missing += 1
            if gate_relevant:
                gate_missing += 1
            _append_identity_example(
                examples,
                max_examples=max_examples,
                line_no=line_no,
                agent=agent,
                status=status,
                event_type=event_type,
                reason="missing_uuid",
                gate_relevant=gate_relevant,
            )
            continue
        if observed_uuid != expected_uuid:
            mismatched += 1
            if gate_relevant:
                gate_mismatched += 1
            _append_identity_example(
                examples,
                max_examples=max_examples,
                line_no=line_no,
                agent=agent,
                status=status,
                event_type=event_type,
                reason="mismatched_uuid",
                gate_relevant=gate_relevant,
            )
    issue_count = missing + mismatched + aliased
    return {
        "schema_version": IDENTITY_AUDIT_VERSION,
        "mode": mode,
        "registry_path": str(registry_path),
        "registry_identities_loaded": len(registry),
        "checked_events": checked,
        "registered_agent_event_count": registered,
        "non_registry_agent_event_count": non_registry,
        "skipped_unreadable_event_count": skipped_unreadable,
        "missing_uuid_registered_events": missing,
        "mismatched_uuid_registered_events": mismatched,
        "registered_uuid_alias_events": aliased,
        "gate_relevant_missing_uuid": gate_missing,
        "gate_relevant_mismatched_uuid": gate_mismatched,
        "gate_relevant_registered_uuid_alias": gate_aliased,
        "ok": issue_count == 0,
        "issue_count": issue_count,
        "examples": examples,
    }


def _audit_event_hygiene(
    events_path: Path,
    *,
    tail: int | None,
    max_examples: int,
    mode: str,
) -> dict[str, Any]:
    checked = 0
    skipped_unreadable = 0
    unknown_event_types: Counter[str] = Counter()
    non_object_payload_types: Counter[str] = Counter()
    missing_payload = 0
    suspicious_payload_keys: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    lines = _select_event_lines(
        events_path.read_text(encoding="utf-8").splitlines(),
        tail=tail,
    )
    for line_no, line in lines:
        if not line.strip():
            continue
        checked += 1
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            skipped_unreadable += 1
            continue
        if not isinstance(event, dict):
            skipped_unreadable += 1
            continue

        event_type = event.get("type")
        if (
            isinstance(event_type, str)
            and event_type
            and event_type not in KNOWN_EVENT_TYPES
        ):
            unknown_event_types[event_type] += 1
            _append_hygiene_example(
                examples,
                max_examples=max_examples,
                line_no=line_no,
                agent=event.get("agent"),
                event_type=event_type,
                reason="unknown_event_type",
                detail=event_type,
            )

        if "payload" not in event:
            missing_payload += 1
            _append_hygiene_example(
                examples,
                max_examples=max_examples,
                line_no=line_no,
                agent=event.get("agent"),
                event_type=event_type,
                reason="missing_payload",
            )
            continue

        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload_type = type(payload).__name__
            non_object_payload_types[payload_type] += 1
            _append_hygiene_example(
                examples,
                max_examples=max_examples,
                line_no=line_no,
                agent=event.get("agent"),
                event_type=event_type,
                reason="non_object_payload",
                detail=payload_type,
            )
            continue

        for key in payload:
            key_text = str(key).lower()
            if any(marker in key_text for marker in SUSPICIOUS_PAYLOAD_KEY_MARKERS):
                suspicious_payload_keys[key_text] += 1
                _append_hygiene_example(
                    examples,
                    max_examples=max_examples,
                    line_no=line_no,
                    agent=event.get("agent"),
                    event_type=event_type,
                    reason="sensitive_payload_key_name",
                    detail=key_text,
                )

    issue_count = (
        sum(unknown_event_types.values())
        + sum(non_object_payload_types.values())
        + missing_payload
        + sum(suspicious_payload_keys.values())
    )
    return {
        "schema_version": EVENT_HYGIENE_AUDIT_VERSION,
        "mode": mode,
        "checked_events": checked,
        "skipped_unreadable_event_count": skipped_unreadable,
        "unknown_event_type_count": sum(unknown_event_types.values()),
        "unknown_event_types": _counter_rows(unknown_event_types),
        "non_object_payload_count": sum(non_object_payload_types.values()),
        "non_object_payload_types": _counter_rows(non_object_payload_types),
        "missing_payload_count": missing_payload,
        "sensitive_payload_key_name_count": sum(suspicious_payload_keys.values()),
        "sensitive_payload_key_names": _counter_rows(suspicious_payload_keys),
        "ok": issue_count == 0,
        "issue_count": issue_count,
        "examples": examples,
    }


def _select_event_lines(
    lines: Sequence[str],
    *,
    tail: int | None,
) -> list[tuple[int, str]]:
    numbered = list(enumerate(lines, start=1))
    if tail is None:
        return numbered
    if tail <= 0:
        return []
    return numbered[-tail:]


def _append_identity_example(
    examples: list[dict[str, Any]],
    *,
    max_examples: int,
    line_no: int,
    agent: str,
    status: object,
    event_type: object,
    reason: str,
    gate_relevant: bool,
    registered_uuid_owner: str = "",
) -> None:
    if len(examples) >= max_examples:
        return
    example = {
        "line_no": line_no,
        "agent": agent,
        "type": event_type if isinstance(event_type, str) else "",
        "status": status if isinstance(status, str) else "",
        "reason": reason,
        "gate_relevant": gate_relevant,
    }
    if registered_uuid_owner:
        example["registered_uuid_owner"] = registered_uuid_owner
    examples.append(example)


def _append_hygiene_example(
    examples: list[dict[str, Any]],
    *,
    max_examples: int,
    line_no: int,
    agent: object,
    event_type: object,
    reason: str,
    detail: str = "",
) -> None:
    if len(examples) >= max_examples:
        return
    examples.append({
        "line_no": line_no,
        "agent": agent if isinstance(agent, str) else "",
        "type": event_type if isinstance(event_type, str) else "",
        "reason": reason,
        "detail": detail,
    })


def _counter_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count}
        for value, count in counter.most_common()
    ]


def _print_result(
    result: BridgeEventValidationResult,
    *,
    json_output: bool,
    missing_path: Path | None = None,
    waivers_path: Path | None = None,
    waivers_loaded: int = 0,
    agent_profiles_path: Path | None = None,
    agent_profiles_loaded: int = 0,
    identity_registry_audit: Mapping[str, Any] | None = None,
    event_hygiene_audit: Mapping[str, Any] | None = None,
) -> None:
    payload = result.to_dict()
    overall_ok = result.ok
    if missing_path is not None:
        payload["missing_path"] = str(missing_path)
    if waivers_path is not None:
        payload["waivers_path"] = str(waivers_path)
        payload["waivers_loaded"] = waivers_loaded
    if agent_profiles_path is not None:
        payload["agent_profiles_path"] = str(agent_profiles_path)
        payload["agent_profiles_loaded"] = agent_profiles_loaded
    if identity_registry_audit is not None:
        payload["identity_registry_audit"] = dict(identity_registry_audit)
        if identity_registry_audit.get("mode") == "strict":
            overall_ok = overall_ok and bool(identity_registry_audit.get("ok"))
            payload["ok"] = overall_ok
    if event_hygiene_audit is not None:
        payload["event_hygiene_audit"] = dict(event_hygiene_audit)
        if event_hygiene_audit.get("mode") == "strict":
            overall_ok = overall_ok and bool(event_hygiene_audit.get("ok"))
            payload["ok"] = overall_ok
    if json_output:
        print(json.dumps(payload, sort_keys=True))
        return

    if overall_ok:
        waived = (
            f", {result.waived_invalid} waived invalid"
            if result.waived_invalid
            else ""
        )
        print(
            f"bridge event schema OK: {result.valid}/{result.checked} valid "
            f"({result.schema_version}{waived})"
        )
        if identity_registry_audit is not None:
            issue_count = int(identity_registry_audit.get("issue_count", 0))
            mode = str(identity_registry_audit.get("mode", "warn"))
            if issue_count:
                print(
                    "identity registry audit "
                    f"{mode.upper()}: {issue_count} registered-agent UUID "
                    "issue(s)"
                )
        if event_hygiene_audit is not None:
            issue_count = int(event_hygiene_audit.get("issue_count", 0))
            mode = str(event_hygiene_audit.get("mode", "warn"))
            if issue_count:
                print(
                    "event hygiene audit "
                    f"{mode.upper()}: {issue_count} bridge event hygiene "
                    "issue(s)"
                )
        return

    if (
        result.ok
        and identity_registry_audit is not None
        and identity_registry_audit.get("mode") == "strict"
    ):
        print(
            "bridge event schema FAILED: identity registry audit found "
            f"{identity_registry_audit.get('issue_count', 0)} UUID issues",
            file=sys.stderr,
        )
        return

    if (
        result.ok
        and event_hygiene_audit is not None
        and event_hygiene_audit.get("mode") == "strict"
    ):
        print(
            "bridge event schema FAILED: event hygiene audit found "
            f"{event_hygiene_audit.get('issue_count', 0)} issue(s)",
            file=sys.stderr,
        )
        return

    if missing_path is not None:
        print(f"bridge event schema FAILED: missing {missing_path}", file=sys.stderr)
        return

    print(
        f"bridge event schema FAILED: {result.invalid}/{result.checked} invalid "
        f"({result.waived_invalid} waived) "
        f"({result.schema_version})",
        file=sys.stderr,
    )
    for issue in result.issues:
        print(f"line {issue.line_no}: {issue.error}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
