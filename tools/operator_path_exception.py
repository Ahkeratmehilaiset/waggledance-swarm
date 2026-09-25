# SPDX-License-Identifier: BUSL-1.1
"""Explicit operator invocation only; never used by the autonomous merge gate.

The caller must verify the operator's instruction outside this module. The
reference records that instruction; a JSON document is NOT authentication or a
signature. This helper binds a caller-attested, time-limited instruction to one
PR/head/base/diff and preserves the original refused gate report for audit.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from waggledance.core.magma.canonical import sha256_digest


PATH_REASON = "path gate failed: paths not on allowlist"


def apply_operator_path_exception(
    gate: Mapping[str, Any], *, grant: Mapping[str, Any] | None,
    pr_status: Mapping[str, Any], repo: str, head: str, base: str,
    now: datetime,
) -> dict[str, Any]:
    if grant is None:
        return dict(gate)
    if not isinstance(grant, Mapping):
        raise ValueError("operator exception must be an object")
    required = {"schema", "repo", "pr_number", "head", "base", "diff_digest",
                "paths", "approval_reference", "issued_at", "expires_at"}
    if set(grant) != required:
        raise ValueError("operator exception fields do not match the contract")
    expected = {"schema": "wd.operator-path-exception.v1", "repo": repo,
                "pr_number": pr_status.get("pr_number"), "head": head, "base": base,
                "diff_digest": sha256_digest(str(pr_status.get("diff_text", "")))}
    if not repo or not head or not base or type(grant["pr_number"]) is not int:
        raise ValueError("operator exception requires exact repository and PR binding")
    if any(grant[key] != value for key, value in expected.items()):
        raise ValueError("operator exception binding mismatch")
    reference = grant["approval_reference"]
    if not isinstance(reference, str) or not reference.strip() or len(reference) > 2048:
        raise ValueError("operator approval reference required")
    try:
        issued = datetime.fromisoformat(grant["issued_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
        if any(value.tzinfo is None or value.utcoffset() is None for value in (issued, expires, now)):
            raise ValueError("timezone required")
        if not (issued <= now < expires and timedelta(0) < expires - issued <= timedelta(hours=24)):
            raise ValueError("expired or invalid lifetime")
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("operator exception lifetime invalid") from exc
    path_gate = gate.get("path_gate", {})
    paths = grant["paths"]
    if (not isinstance(paths, list) or not paths
            or any(not isinstance(path, str) or not path for path in paths)
            or len(set(paths)) != len(paths)
            or set(paths) != set(path_gate.get("unmatched_paths", []))):
        raise ValueError("operator exception paths mismatch")
    if (gate.get("decision") != "operator_review_required"
            or gate.get("ok") is not False
            or gate.get("reasons") != [PATH_REASON]
            or path_gate.get("reason") != "paths not on allowlist"
            or path_gate.get("allowed") is not False
            or path_gate.get("blocked_paths") != []
            or path_gate.get("code_pattern_hits") != []):
        raise ValueError("operator exception cannot override any other refusal")
    for name, flag in (("bridge_consensus", "ok"), ("rco_pass_gate", "ok"),
                       ("bridge_peer_gate", "clear_to_merge"),
                       ("accepted_queue_preflight", "complete"),
                       ("diff_gate", "allowed"), ("base_gate", "allowed"),
                       ("rate_gate", "allowed")):
        if gate.get(name, {}).get(flag) is not True:
            raise ValueError(f"operator exception requires verified {name}")
    result = deepcopy(dict(gate))
    result["original_gate"] = deepcopy(dict(gate))
    result["operator_path_exception"] = {
        "grant": deepcopy(dict(grant)), "grant_digest": sha256_digest(dict(grant)),
        "authority_basis": "explicit_operator_instruction_verified_by_caller",
        "cryptographic_authentication": False,
        "observed_at_utc": now.astimezone(timezone.utc).isoformat(),
    }
    result.update(ok=True, decision="operator_path_exception_admitted",
                  reasons=[], operator_review_required=False)
    return result
