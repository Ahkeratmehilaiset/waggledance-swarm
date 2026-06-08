# SPDX-License-Identifier: BUSL-1.1
"""Local bridge agent identity profile provisioning.

Profiles live under ``.agent-bridge/agents/<agent_id>.json`` and describe a
local agent process well enough for bridge tools to display and validate it.
This tool does not write bridge events, call git/GitHub, or provision secrets.
Default operations are local filesystem reads/writes inside the supplied
``--bridge-root``.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.work_queue import (  # noqa: E402
    AGENT_ID_PATTERN,
    DEFAULT_BRIDGE_ROOT,
)


SCHEMA_VERSION = "agent_profile.v1"
PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")
SENSITIVE_MARKERS = ("secret", "token", "credential", "password", ".env")
KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,64}$")


class AgentIdentityError(ValueError):
    """Raised when an agent identity profile operation is refused."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bridge agent identity CLI.")
    parser.add_argument("--bridge-root", type=Path, default=DEFAULT_BRIDGE_ROOT)
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register")
    register.add_argument("--agent", required=True)
    register.add_argument("--kind", required=True)
    register.add_argument("--agent-uuid", default="")
    register.add_argument("--display-name", default="")
    register.add_argument("--capability", action="append", default=[])
    register.add_argument("--force", action="store_true")

    show = sub.add_parser("show")
    show.add_argument("--agent", required=True)

    sub.add_parser("list")

    validate = sub.add_parser("validate")
    validate.add_argument("--agent", default="")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = _dispatch(args)
    except AgentIdentityError as exc:
        report = exc.report
        exit_code = int(report.get("exit_code", 2))
    else:
        exit_code = int(report.pop("exit_code", 0))

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        _print_human(report)
    return exit_code


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    bridge_root = Path(args.bridge_root)
    if args.command == "register":
        profile = register_profile(
            agent_id=args.agent,
            kind=args.kind,
            agent_uuid=args.agent_uuid,
            display_name=args.display_name,
            capabilities=args.capability,
            bridge_root=bridge_root,
            force=args.force,
        )
        return {"ok": True, "decision": "registered", "profile": profile}
    if args.command == "show":
        profile = read_profile(agent_id=args.agent, bridge_root=bridge_root)
        return {"ok": True, "decision": "profile", "profile": profile}
    if args.command == "list":
        return {
            "ok": True,
            "decision": "listed",
            "profiles": list_profiles(bridge_root=bridge_root),
        }
    if args.command == "validate":
        if args.agent:
            profile = read_profile(agent_id=args.agent, bridge_root=bridge_root)
            errors = validate_profile(profile)
            return {
                "ok": not errors,
                "decision": "valid" if not errors else "invalid",
                "profile": profile,
                "errors": errors,
                "exit_code": 0 if not errors else 2,
            }
        profiles = list_profiles(bridge_root=bridge_root)
        errors_by_agent = {
            str(profile.get("agent_id", "")): validate_profile(profile)
            for profile in profiles
        }
        errors_by_agent = {
            agent: errors for agent, errors in errors_by_agent.items() if errors
        }
        return {
            "ok": not errors_by_agent,
            "decision": "valid" if not errors_by_agent else "invalid",
            "profiles": profiles,
            "errors_by_agent": errors_by_agent,
            "exit_code": 0 if not errors_by_agent else 2,
        }
    raise _error("unsupported_command", f"unsupported command: {args.command}", 2)


def register_profile(
    *,
    agent_id: str,
    kind: str,
    agent_uuid: str = "",
    display_name: str = "",
    capabilities: Sequence[str] = (),
    bridge_root: Path | None = None,
    force: bool = False,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Write a local agent profile, refusing overwrite unless force=True."""
    bridge = bridge_root or DEFAULT_BRIDGE_ROOT
    _validate_agent_id(agent_id)
    _validate_kind(kind)
    _validate_agent_uuid(agent_uuid)
    normalized_capabilities = _normalize_capabilities(capabilities)
    _assert_no_private_markers(
        {
            "agent_id": agent_id,
            "kind": kind,
            "agent_uuid": agent_uuid,
            "display_name": display_name,
            "capabilities": normalized_capabilities,
        }
    )
    profile_path = _profile_path(bridge, agent_id)

    created_at = _iso(now_utc or datetime.now(timezone.utc))
    if profile_path.exists():
        if not force:
            raise _error("profile_exists", f"profile already exists: {agent_id}", 1)
        existing = _read_profile_path(profile_path)
        created_at = str(existing.get("created_at_utc", created_at))

    profile = {
        "schema_version": SCHEMA_VERSION,
        "agent_id": agent_id,
        "kind": kind,
        "display_name": display_name.strip() or agent_id,
        "capabilities": normalized_capabilities,
        "status": "active",
        "operator_approved": False,
        "write_scope_policy": "claim_required",
        "created_at_utc": created_at,
        "updated_at_utc": _iso(now_utc or datetime.now(timezone.utc)),
    }
    if agent_uuid:
        profile["agent_uuid"] = agent_uuid
    errors = validate_profile(profile)
    if errors:
        raise _error("invalid_profile", "; ".join(errors), 2)

    profile_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(profile_path, profile, create_new=not force)
    return profile


def read_profile(*, agent_id: str, bridge_root: Path | None = None) -> dict[str, Any]:
    """Read and validate one local agent profile."""
    bridge = bridge_root or DEFAULT_BRIDGE_ROOT
    _validate_agent_id(agent_id)
    path = _profile_path(bridge, agent_id)
    if not path.exists():
        raise _error("profile_missing", f"profile not found: {agent_id}", 1)
    profile = _read_profile_path(path)
    if profile.get("agent_id") != agent_id:
        raise _error(
            "profile_agent_mismatch",
            f"profile file {agent_id}.json contains agent_id={profile.get('agent_id')!r}",
            2,
        )
    errors = validate_profile(profile)
    if errors:
        raise _error("invalid_profile", "; ".join(errors), 2)
    return profile


def list_profiles(bridge_root: Path | None = None) -> list[dict[str, Any]]:
    """Return all valid profiles sorted by agent_id."""
    bridge = bridge_root or DEFAULT_BRIDGE_ROOT
    agents_dir = bridge / "agents"
    if not agents_dir.exists():
        return []
    profiles: list[dict[str, Any]] = []
    for path in sorted(agents_dir.glob("*.json")):
        profile = _read_profile_path(path)
        errors = validate_profile(profile)
        if errors:
            raise _error("invalid_profile", f"{path.name}: {'; '.join(errors)}", 2)
        profiles.append(profile)
    return sorted(profiles, key=lambda item: str(item.get("agent_id", "")))


def validate_profile(profile: Mapping[str, Any]) -> list[str]:
    """Return validation errors for an agent profile."""
    errors: list[str] = []
    marker = _find_private_marker(profile)
    if marker is not None:
        errors.append(f"privacy marker refused: {marker}")
    if profile.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    agent_id = str(profile.get("agent_id", ""))
    if not AGENT_ID_PATTERN.fullmatch(agent_id):
        errors.append(f"agent_id must match {AGENT_ID_PATTERN.pattern}")
    kind = str(profile.get("kind", ""))
    if not KIND_RE.fullmatch(kind):
        errors.append(f"kind must match {KIND_RE.pattern}")
    agent_uuid = str(profile.get("agent_uuid", ""))
    if agent_uuid and not _is_agent_uuid(agent_uuid):
        errors.append("agent_uuid must be a UUID")
    display_name = str(profile.get("display_name", ""))
    if not display_name.strip():
        errors.append("display_name required")
    if profile.get("status") != "active":
        errors.append("status must be active")
    capabilities = profile.get("capabilities", [])
    if not isinstance(capabilities, list):
        errors.append("capabilities must be a list")
    else:
        for capability in capabilities:
            if not isinstance(capability, str) or not CAPABILITY_RE.fullmatch(capability):
                errors.append(f"invalid capability: {capability!r}")
    if profile.get("write_scope_policy") != "claim_required":
        errors.append("write_scope_policy must be claim_required")
    if profile.get("operator_approved") is not False:
        errors.append("operator_approved must default to false in v1")
    for field in ("created_at_utc", "updated_at_utc"):
        value = str(profile.get(field, ""))
        if not value:
            errors.append(f"{field} required")
        else:
            try:
                _parse_utc(value)
            except ValueError:
                errors.append(f"{field} must be ISO-8601 UTC")
    return errors


def _profile_path(bridge_root: Path, agent_id: str) -> Path:
    return bridge_root / "agents" / f"{agent_id}.json"


def _read_profile_path(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _error("invalid_profile_json", f"unreadable profile: {path.name}", 2) from exc
    if not isinstance(data, dict):
        raise _error("invalid_profile_json", f"profile must be a JSON object: {path.name}", 2)
    _assert_no_private_markers(data)
    return data


def _write_json(path: Path, payload: Mapping[str, Any], *, create_new: bool) -> None:
    body = json.dumps(dict(payload), indent=2, sort_keys=True) + "\n"
    _assert_no_private_markers(body)
    if create_new:
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(body)
        except FileExistsError as exc:
            raise _error("profile_exists", f"profile already exists: {path.stem}", 1) from exc
        return
    tmp = path.with_name(f"{path.name}.tmp.{uuid4().hex}")
    try:
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _normalize_capabilities(capabilities: Sequence[str]) -> list[str]:
    normalized = sorted({capability.strip() for capability in capabilities if capability.strip()})
    for capability in normalized:
        if not CAPABILITY_RE.fullmatch(capability):
            raise _error("invalid_capability", f"invalid capability: {capability}", 2)
    return normalized


def _validate_agent_id(agent_id: str) -> None:
    if not AGENT_ID_PATTERN.fullmatch(agent_id):
        raise _error("invalid_agent_id", f"agent must match {AGENT_ID_PATTERN.pattern}", 2)


def _validate_kind(kind: str) -> None:
    if not KIND_RE.fullmatch(kind):
        raise _error("invalid_kind", f"kind must match {KIND_RE.pattern}", 2)


def _validate_agent_uuid(agent_uuid: str) -> None:
    if agent_uuid and not _is_agent_uuid(agent_uuid):
        raise _error("invalid_agent_uuid", "agent_uuid must be a UUID", 2)


def _is_agent_uuid(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
            value,
        )
    )


def _error(decision: str, message: str, exit_code: int) -> AgentIdentityError:
    return AgentIdentityError(
        {
            "ok": False,
            "decision": decision,
            "errors": [message],
            "exit_code": exit_code,
        }
    )


def _assert_no_private_markers(value: object) -> None:
    marker = _find_private_marker(value)
    if marker is not None:
        raise _error("privacy_marker_refused", f"privacy marker refused: {marker}", 2)


def _find_private_marker(value: object) -> str | None:
    if isinstance(value, str):
        for marker in PRIVATE_MARKERS:
            if marker in value:
                return marker
        lowered = value.lower()
        for marker in SENSITIVE_MARKERS:
            if marker in lowered:
                return marker
        return None
    if isinstance(value, Mapping):
        for key, item in value.items():
            marker = _find_private_marker(key)
            if marker is not None:
                return marker
            marker = _find_private_marker(item)
            if marker is not None:
                return marker
        return None
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            marker = _find_private_marker(item)
            if marker is not None:
                return marker
    return None


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


def _print_human(report: Mapping[str, Any]) -> None:
    print(report.get("decision", "unknown"))
    if not report.get("ok", False):
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
        return
    profile = report.get("profile")
    if isinstance(profile, Mapping):
        print(f"agent_id: {profile.get('agent_id', '')}")
    profiles = report.get("profiles")
    if isinstance(profiles, list):
        print(f"profiles: {len(profiles)}")


if __name__ == "__main__":
    raise SystemExit(main())
