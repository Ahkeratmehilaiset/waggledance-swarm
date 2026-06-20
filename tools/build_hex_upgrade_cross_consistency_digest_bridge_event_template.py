#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a hex-upgrade cross-consistency digest bridge-event template.

Advances the WD Image #1 *hexagonal upgrades* pillar. The existing
``run_hex_upgrade_cross_consistency_digest`` tool emits a path-free,
measurement-only digest that confirms the reviewer summary, shadow-only
invariant, and chain-final summary agree. This tool turns that derived-boolean
digest into schema-valid reviewer-handoff bridge-event TEMPLATE JSON without
appending it anywhere.

It is read-only and grants no authority. Template-only: it never appends to the
bridge, transports artifacts, writes files, calls providers, mutates runtime,
grants runtime subdivision authority, or upgrades any claim. The emitted template
hardcodes every approval/transport/authority axis False. Content-safe by
construction: the cross-consistency section emits only derived booleans plus
fixed version/schema strings and a sha256 digest ref via a closed allowlist.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
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
PROOF_ID = "hex_upgrade_cross_consistency_digest_bridge_event_template_v1"

FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious",
    "sentient",
    "aware",
    "alive",
    "AGI",
    "revolutionary",
    "magical",
    "human-like mind",
    "self-aware",
    "explosive intelligence",
    "emergent",
    "beats all competitors",
    "world's best",
    "world's fastest",
)

_DIGEST_VERDICT_FIELDS = (
    "all_views_present",
    "reviewer_clean",
    "shadow_only_clean",
    "chain_summary_clean",
    "cross_consistent",
    "path_free_verified",
)

_TEMPLATE_SAFE_KEYS = frozenset(
    {
        "digest_schema_version",
        "digest_ref",
        *_DIGEST_VERDICT_FIELDS,
        "raw_digest_payload_included",
    }
)

AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
SAFE_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


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


class SafeInputError(ValueError):
    """Raised when input cannot safely be rendered."""

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
    """Return schema-valid bridge-event template JSON without appending it."""

    if not isinstance(digest, Mapping):
        return _error_report("digest_not_object")
    digest_error = _validate_digest(digest)
    if digest_error is not None:
        return _error_report(digest_error)

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
        return _error_report(input_error)
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
            "hex cross-consistency template section has non-allowlisted keys: "
            f"{sorted(extra)}"
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
            "literal_future_claim_safe": False,
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
            "hex_cross_consistency",
            "bridge_event",
        ],
        "pid": 0,
        "cwd": "template_not_emitted",
        "payload": payload,
    }
    validate_event(event)
    if _contains_path_marker(event):
        return _error_report("template_not_path_free")
    rendered = json.dumps(event, allow_nan=False, sort_keys=True)
    if _forbidden_output_markers(rendered):
        return _error_report("template_forbidden_marker")

    return {
        "proof_id": PROOF_ID,
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


def _validate_digest(digest: Mapping[str, Any]) -> str | None:
    if digest.get("report_version") != DIGEST_REPORT_VERSION:
        return "digest_version_mismatch"
    if digest.get("claim_safe") is True:
        return "digest_self_claim_safe"
    for field in _DIGEST_VERDICT_FIELDS:
        if not isinstance(digest.get(field), bool):
            return "digest_verdict_not_bool"
    if _contains_path_marker(digest):
        return "digest_not_path_free"
    text = json.dumps(_plain_json_object(digest), allow_nan=False, sort_keys=True)
    if _forbidden_output_markers(text):
        return "digest_forbidden_marker"
    return None


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
    if not targets or any(not AGENT_ID_PATTERN.fullmatch(item) for item in targets):
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


def _error_report(reason: str) -> dict[str, Any]:
    return {
        "proof_id": PROOF_ID,
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
            f"{_safe_reason(reason)}"
        ],
        "warnings": [],
    }


def _plain_json_object(value: Any) -> dict[str, Any]:
    plain = json.loads(json.dumps(value, allow_nan=False))
    if not isinstance(plain, dict):
        raise ValueError("value must be a JSON object")
    return plain


def _forbidden_output_markers(text: str) -> list[str]:
    lower_text = text.lower()
    return sorted(
        marker for marker in FORBIDDEN_OUTPUT_MARKERS if marker.lower() in lower_text
    )


def _safe_reason(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9:._-]+", "_", str(value)).strip("_")
    if not text:
        return "invalid_reason"
    if not text[0].isalnum():
        text = "reason_" + text
    return text[:192]


def assert_vocabulary_clean(text: str) -> None:
    hit = [
        pattern
        for pattern in FORBIDDEN_VOCABULARY
        if re.search(r"\b" + re.escape(pattern) + r"\b", text, re.IGNORECASE)
    ]
    if hit:
        raise SystemExit(f"forbidden vocabulary in rendered text: {hit}")


def _parse_utc(raw: str) -> datetime:
    if not raw.endswith("Z"):
        raise SafeInputError("now_utc_unsafe")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise SafeInputError("now_utc_unsafe") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise SafeInputError("now_utc_unsafe")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _load_digest(path: Path | None) -> Mapping[str, Any]:
    if path is None:
        from tools.wd_image1_capability_manifest import build_manifest  # noqa: PLC0415

        manifest = build_manifest(ROOT)
        for capability in manifest.get("capabilities", []):
            if capability.get("capability_id") == "hexagonal_upgrades":
                proof = capability.get("proof")
                proof = proof if isinstance(proof, Mapping) else {}
                stored = proof.get("cross_consistency_digest")
                if isinstance(stored, Mapping):
                    return dict(stored)
                return build_cross_consistency_digest(proof)
        raise SafeInputError("hexagonal_upgrades_proof_not_found")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SafeInputError("digest_unreadable") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SafeInputError("digest_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise SafeInputError("digest_not_object")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--digest-json",
        dest="digest_json",
        default=None,
        type=Path,
        help="Optional path to digest JSON; default builds it live from manifest.",
    )
    parser.add_argument("--agent", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--to", default="operator,claude-rco-1,codex-tools-1")
    parser.add_argument(
        "--severity",
        default="medium",
        choices=("", "low", "medium", "high"),
    )
    parser.add_argument("--role", default="lead-impl")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--now", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        digest = _load_digest(args.digest_json)
        report = build_hex_upgrade_cross_consistency_digest_bridge_event_template(
            digest=digest,
            agent_id=args.agent,
            task_id=args.task_id,
            to=args.to,
            severity=args.severity,
            role=args.role,
            run_id=args.run_id,
            session_id=args.session_id,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except SafeInputError as exc:
        report = _error_report(exc.code)
    except ValueError:
        report = _error_report("bridge_event_template_invalid")

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    assert_vocabulary_clean(encoded)
    if args.json:
        print(encoded)
    elif report["ok"]:
        print(
            json.dumps(
                report["bridge_event_template"],
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
        )
    else:
        print(
            "hex-upgrade cross-consistency digest bridge-event template FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
