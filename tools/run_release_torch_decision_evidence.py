#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write v3.12 torch dependency decision evidence from an operator pack.

This artifact can authorize the dependency implementation path. It never marks
``security_privacy_gate`` pass; the gate can pass only after the dependency
change lands and fresh Bandit/privacy/pip-audit evidence is clean.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.operator_decision_pack import DecisionPackError, is_signed, load_pack


SCHEMA_VERSION = "waggledance.release_torch_decision.v1"
AUTH_SCHEMA_VERSION = "waggledance.operator_torch_dependency_authorization.v1"
DEFAULT_TARGET_VERSION = "v3.12.0"
DEFAULT_OUTPUT = (
    Path("docs")
    / "runs"
    / "release_soak_evidence"
    / "v3.12.0_torch_decision.json"
)
TORCH_DECISION_PACK_ID = "torch-cuda-vs-cpu"
TORCH_DECISION_PACK_CATEGORY = "dependency_security"
REQUIRED_DECISION_PACK_INVARIANTS = (
    "dependency_change_lands_via_pr",
    "agent_must_not_self_resolve",
)
TORCH_DECISION_OPTIONS: dict[str, dict[str, Any]] = {
    "A1_cpu_only": {
        "implementation_strategy": "cpu_only",
        "packages": [
            "torch==2.11.0",
            "torchvision==0.26.0",
            "torchaudio==2.11.0",
        ],
        "keeps_gpu": False,
        "requires_cuda_12_6_driver": False,
        "xformers_cu126_verification_required": False,
        "descope_torch_family": False,
        "lock_followups_required": [
            "torchao>=0.17.0",
            "drop_or_cpu-pin_xformers",
        ],
    },
    "A2_cu126": {
        "implementation_strategy": "cuda_12_6",
        "packages": [
            "torch==2.11.0+cu126",
            "torchvision==0.26.0+cu126",
            "torchaudio==2.11.0+cu126",
        ],
        "index_url": "https://download.pytorch.org/whl/cu126",
        "keeps_gpu": True,
        "requires_cuda_12_6_driver": True,
        "xformers_cu126_verification_required": True,
        "descope_torch_family": False,
        "lock_followups_required": [
            "torchao_compatible_with_torch_2.11",
            "xformers_cu126_wheel_or_drop",
        ],
    },
    "B_descope": {
        "implementation_strategy": "descope_torch_family",
        "packages": [],
        "keeps_gpu": False,
        "requires_cuda_12_6_driver": False,
        "xformers_cu126_verification_required": False,
        "descope_torch_family": True,
        "lock_followups_required": [
            "move_torch_family_to_optional_extra",
            "move_xformers_to_optional_extra",
        ],
    },
}


def _format_utc(value: dt.datetime) -> str:
    normalized = value.astimezone(dt.UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _parse_utc(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _current_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return completed.stdout.strip()


def _operator_signoff(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    parts = value.strip().split(":", 2)
    if len(parts) != 3 or parts[0] != "operator" or not parts[1].strip():
        return None
    signed_at = _parse_utc(parts[2])
    if signed_at is None:
        return None
    return parts[1].strip(), _format_utc(signed_at)


def _pack_scalar(pack: Mapping[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = pack.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _option_data(pack: Mapping[str, Any], chosen: str) -> Mapping[str, Any]:
    options = pack.get("options")
    if not isinstance(options, list):
        return {}
    for option in options:
        if not isinstance(option, Mapping):
            continue
        if str(option.get("id", "") or "").strip() != chosen:
            continue
        data = option.get("data")
        return data if isinstance(data, Mapping) else {}
    return {}


def _pack_option_matches_expected(
    pack: Mapping[str, Any],
    chosen: str,
    expected: Mapping[str, Any],
) -> bool:
    data = _option_data(pack, chosen)
    packages = data.get("packages")
    if chosen in {"A1_cpu_only", "A2_cu126"}:
        if list(packages or []) != expected["packages"]:
            return False
    elif packages not in (None, []):
        return False

    if "keeps_gpu" in data and data.get("keeps_gpu") is not expected["keeps_gpu"]:
        return False
    if data.get("fixes_osv_vulns") is False:
        return False
    if chosen == "A2_cu126" and data.get("index_url") != expected["index_url"]:
        return False
    return True


def implementation_authorization_from_decision_pack(
    path: Path | str,
    *,
    commit: str,
    target_version: str,
) -> dict[str, Any] | None:
    """Convert a signed torch decision pack into implementation authorization."""

    try:
        pack = load_pack(path)
    except (OSError, DecisionPackError):
        return None
    if pack.get("decision_id") != TORCH_DECISION_PACK_ID:
        return None
    if pack.get("category") != TORCH_DECISION_PACK_CATEGORY:
        return None
    if not is_signed(pack):
        return None

    invariants = pack.get("structural_invariants")
    if not isinstance(invariants, Mapping):
        return None
    if any(
        invariants.get(name) is not True
        for name in REQUIRED_DECISION_PACK_INVARIANTS
    ):
        return None

    pack_target = _pack_scalar(pack, ("target_version", "release_version"))
    if pack_target and pack_target != target_version:
        return None
    pack_commit = _pack_scalar(pack, ("commit", "target_commit", "subject_commit"))
    if pack_commit and pack_commit != commit:
        return None

    signoff = pack.get("operator_signoff")
    if not isinstance(signoff, Mapping):
        return None
    chosen = str(signoff.get("chosen_option", "") or "").strip()
    expected = TORCH_DECISION_OPTIONS.get(chosen)
    signed = _operator_signoff(signoff.get("signed_by"))
    if expected is None or signed is None:
        return None
    if not _pack_option_matches_expected(pack, chosen, expected):
        return None
    operator_id, authorized_at_utc = signed

    return {
        "schema_version": AUTH_SCHEMA_VERSION,
        "target_version": target_version,
        "commit": commit,
        "implementation_authorized": True,
        "security_privacy_gate_pass_authorized": False,
        "clean_osv_or_pip_audit_required": True,
        "pip_audit_skip_is_not_clean": True,
        "source": "operator_decision_pack",
        "decision_id": TORCH_DECISION_PACK_ID,
        "chosen_option": chosen,
        "operator_id": operator_id,
        "authorized_at_utc": authorized_at_utc,
        "authorization_id": (
            f"decision-pack:{TORCH_DECISION_PACK_ID}:{chosen}:{operator_id}"
        ),
        **expected,
    }


def evaluate_report(
    report: Mapping[str, Any],
    *,
    expected_commit: str | None = None,
    target_version: str = DEFAULT_TARGET_VERSION,
) -> list[str]:
    blockers: list[str] = []
    if report.get("schema_version") != SCHEMA_VERSION:
        blockers.append("schema_version_invalid")
    if report.get("target_version") != target_version:
        blockers.append("target_version_mismatch")
    commit = report.get("commit")
    if not isinstance(commit, str) or not commit.strip():
        blockers.append("commit_missing")
    elif expected_commit is not None and commit != expected_commit:
        blockers.append("commit_mismatch")
    if report.get("release_gate_effect") != "none":
        blockers.append("release_gate_effect_must_be_none")
    if report.get("post_change_clean_audit_required") is not True:
        blockers.append("post_change_clean_audit_not_required")
    if report.get("dependency_change_lands_via_pr_required") is not True:
        blockers.append("dependency_change_pr_not_required")
    if report.get("pip_audit_skip_is_not_clean") is not True:
        blockers.append("pip_audit_skip_must_not_be_clean")

    authorization = report.get("implementation_authorization")
    if not isinstance(authorization, Mapping):
        blockers.append("operator_decision_pack_unsigned_or_invalid")
        return blockers

    if authorization.get("schema_version") != AUTH_SCHEMA_VERSION:
        blockers.append("authorization_schema_invalid")
    if authorization.get("target_version") != target_version:
        blockers.append("authorization_target_mismatch")
    if expected_commit is not None and authorization.get("commit") != expected_commit:
        blockers.append("authorization_commit_mismatch")
    if authorization.get("implementation_authorized") is not True:
        blockers.append("implementation_not_authorized")
    if authorization.get("security_privacy_gate_pass_authorized") is not False:
        blockers.append("security_gate_must_not_be_pack_authorized")
    if authorization.get("clean_osv_or_pip_audit_required") is not True:
        blockers.append("clean_osv_or_pip_audit_not_required")
    if authorization.get("pip_audit_skip_is_not_clean") is not True:
        blockers.append("authorization_pip_audit_skip_must_not_be_clean")
    chosen = authorization.get("chosen_option")
    expected = TORCH_DECISION_OPTIONS.get(str(chosen))
    if expected is None:
        blockers.append("chosen_option_invalid")
    else:
        if authorization.get("packages") != expected["packages"]:
            blockers.append("authorized_packages_mismatch")
        if authorization.get("keeps_gpu") is not expected["keeps_gpu"]:
            blockers.append("keeps_gpu_mismatch")
        if authorization.get("descope_torch_family") is not expected["descope_torch_family"]:
            blockers.append("descope_torch_family_mismatch")
        if authorization.get("xformers_cu126_verification_required") is not expected[
            "xformers_cu126_verification_required"
        ]:
            blockers.append("xformers_cu126_requirement_mismatch")
        if authorization.get("lock_followups_required") != expected[
            "lock_followups_required"
        ]:
            blockers.append("lock_followups_mismatch")
    if _parse_utc(authorization.get("authorized_at_utc")) is None:
        blockers.append("authorized_at_invalid")
    if not isinstance(
        authorization.get("authorization_id"),
        str,
    ) or not authorization.get("authorization_id"):
        blockers.append("authorization_id_missing")
    return blockers


def build_report(
    *,
    commit: str,
    target_version: str = DEFAULT_TARGET_VERSION,
    implementation_authorization: dict[str, Any] | None = None,
    generated_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    generated_at_utc = generated_at_utc or dt.datetime.now(dt.UTC)
    report = {
        "schema_version": SCHEMA_VERSION,
        "target_version": target_version,
        "commit": commit,
        "generated_at_utc": _format_utc(generated_at_utc),
        "implementation_authorization": implementation_authorization,
        "release_gate_effect": "none",
        "security_privacy_gate_status": "unchanged",
        "dependency_change_lands_via_pr_required": True,
        "post_change_clean_audit_required": True,
        "pip_audit_skip_is_not_clean": True,
    }
    blockers = evaluate_report(
        report,
        expected_commit=commit,
        target_version=target_version,
    )
    report["blockers"] = blockers
    report["torch_decision_status"] = (
        "implementation_authorized" if not blockers else "draft"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default="")
    parser.add_argument("--target-version", default=DEFAULT_TARGET_VERSION)
    parser.add_argument("--operator-decision-pack", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="Exit 0 even when the decision evidence remains draft/fail-closed.",
    )
    args = parser.parse_args(argv)

    commit = args.commit or _current_commit()
    authorization = (
        implementation_authorization_from_decision_pack(
            args.operator_decision_pack,
            commit=commit,
            target_version=args.target_version,
        )
        if args.operator_decision_pack is not None
        else None
    )
    report = build_report(
        commit=commit,
        target_version=args.target_version,
        implementation_authorization=authorization,
    )
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    if report["torch_decision_status"] == "implementation_authorized" or args.allow_draft:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
