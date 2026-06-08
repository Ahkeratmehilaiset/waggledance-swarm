# SPDX-License-Identifier: BUSL-1.1
"""Validate agent bridge events.jsonl against the v1 bridge event schema."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.bridge_event_schema import (
    AGENT_ID_PATTERN,
    AGENT_UUID_PATTERN,
    BridgeEventValidationResult,
    validate_event_file,
)


DEFAULT_EVENTS_PATH = Path(".agent-bridge") / "shared" / "events.jsonl"
DEFAULT_WAIVERS_PATH = ROOT / "configs" / "bridge_event_validation_waivers.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate agent bridge JSONL events.",
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=DEFAULT_EVENTS_PATH,
        help="Path to bridge events.jsonl.",
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
        "--json",
        action="store_true",
        help="Print machine-readable JSON summary.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.events.exists():
        result = BridgeEventValidationResult(
            schema_version="agent-bridge-event.v1",
            checked=0,
            valid=0,
            invalid=1,
            issues=(),
        )
        _print_result(result, json_output=args.json, missing_path=args.events)
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

    result = validate_event_file(
        args.events,
        tail=args.tail,
        max_errors=args.max_errors,
        waived_line_sha256=waiver_hashes,
        waived_line_errors=waiver_errors,
        agent_uuid_by_id=agent_uuid_by_id,
    )
    _print_result(
        result,
        json_output=args.json,
        waivers_path=None if args.no_waivers else args.waivers,
        waivers_loaded=len(waiver_hashes),
        agent_profiles_path=args.agent_profiles,
        agent_profiles_loaded=len(agent_uuid_by_id),
    )
    return 0 if result.ok else 1


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


def _print_result(
    result: BridgeEventValidationResult,
    *,
    json_output: bool,
    missing_path: Path | None = None,
    waivers_path: Path | None = None,
    waivers_loaded: int = 0,
    agent_profiles_path: Path | None = None,
    agent_profiles_loaded: int = 0,
) -> None:
    payload = result.to_dict()
    if missing_path is not None:
        payload["missing_path"] = str(missing_path)
    if waivers_path is not None:
        payload["waivers_path"] = str(waivers_path)
        payload["waivers_loaded"] = waivers_loaded
    if agent_profiles_path is not None:
        payload["agent_profiles_path"] = str(agent_profiles_path)
        payload["agent_profiles_loaded"] = agent_profiles_loaded
    if json_output:
        print(json.dumps(payload, sort_keys=True))
        return

    if result.ok:
        waived = (
            f", {result.waived_invalid} waived invalid"
            if result.waived_invalid
            else ""
        )
        print(
            f"bridge event schema OK: {result.valid}/{result.checked} valid "
            f"({result.schema_version}{waived})"
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
