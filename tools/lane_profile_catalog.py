#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Operator-signed lane profile catalog: loader and validator (SHADOW ONLY).

A lane profile is the capacity layer's profile: (provider, account_pool, model,
effort) plus its quota buckets. The catalog is a versioned wrapper whose
validator covers every field. It carries the capacity advisor's policy as the
separate ``capacity_policy`` object, validated by the advisor itself and never
extended, so the advisor and the planner consume the same validated object and
profile identity has one definition. The wrapper adds two sections:

``lanes``
    Per bridge lane: ``allowed_profiles`` ordered strongest -> weakest (this
    order is the only definition of "raise" and "lower"), ``floor`` (the index
    below which Lead may not go without an operator ack), ``default``,
    ``max_relaunches_per_hour`` and ``cooldown_seconds``. ``reviewer`` marks
    lanes whose profile must never drop below their floor.

``fleet``
    ``mode`` (shadow | approve | auto), ``max_relaunches_per_hour_total``,
    ``verify_timeout_seconds`` and the ``shadow_exit`` / ``approve_exit``
    criteria.

This module only reads the one path it is given, validates it and answers pure
questions (``classify_transition``). It never launches, stops or signals a
process, never reads bridge history or credentials, and grants no authority:
a catalog that validates is still only operator policy, and the operator's
signature is recorded, not verified. See docs/BRIDGE_LANE_PROFILES.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.bridge_capacity_advisor import InputError, _validate_policy  # noqa: E402

SCHEMA = "wd.lane-profile-catalog.v1"
MODES = ("shadow", "approve", "auto")
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
REVIEWER_LANES = ("claude-rco-1", "claude-rco-2")
MAX_CATALOG_BYTES = 256 * 1024
# Exactly the fields tools/bridge_capacity_advisor.py reads. The advisor itself
# does not reject unknown keys, so the wrapper does: nothing may ride inside the
# capacity policy that one consumer honours and another silently ignores.
POLICY_KEYS = frozenset({"schema", "mode", "policy_ref", "observation_ttl_seconds",
                         "catalog_ttl_seconds", "switch_cooldown_seconds",
                         "max_switches_per_task", "profiles", "agents"})
PROFILE_KEYS = frozenset({"provider", "account_pool", "model", "effort", "billing", "approved",
                          "qualification_ref", "qualified_for", "roles", "limits"})
AGENT_BINDING_KEYS = frozenset({"role", "profiles"})


class CatalogError(ValueError):
    """The catalog is malformed or unsafe; never a request to fall back."""


def _positive_int(value: Any, label: str, upper: int) -> int:
    if type(value) is not int or not 1 <= value <= upper:
        raise CatalogError(f"{label} must be an integer in 1..{upper}")
    return value


def _nonneg_int(value: Any, label: str, upper: int) -> int:
    if type(value) is not int or not 0 <= value <= upper:
        raise CatalogError(f"{label} must be an integer in 0..{upper}")
    return value


def _mapping(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise CatalogError(f"{label} must be an object")
    return value


def _validate_exit(block: Any, label: str, keys: tuple[str, ...]) -> None:
    if not isinstance(block, dict) or set(block) != set(keys):
        raise CatalogError(f"{label} must define exactly {', '.join(keys)}")
    for key in keys:
        _nonneg_int(block[key], f"{label}.{key}", 10_000)


def _validate_providers(providers: Any, profiles: dict) -> None:
    if not isinstance(providers, dict) or not providers:
        raise CatalogError("providers must map each provider to its effort enum")
    for name, spec in providers.items():
        efforts = spec.get("efforts") if isinstance(spec, dict) else None
        if (not isinstance(efforts, list) or not efforts
                or not all(isinstance(e, str) and e for e in efforts)
                or len(set(efforts)) != len(efforts)):
            raise CatalogError(f"providers.{name}.efforts must be a unique nonempty list")
    for profile_id, profile in profiles.items():
        provider = providers.get(profile["provider"])
        if provider is None:
            raise CatalogError(f"profile {profile_id} uses an undeclared provider")
        if profile["effort"] not in provider["efforts"]:
            raise CatalogError(f"profile {profile_id} effort is not in the provider enum")


def _validate_lanes(lanes: Any, policy: dict) -> None:
    if not isinstance(lanes, dict) or not lanes:
        raise CatalogError("lanes must be a nonempty object")
    profiles = policy["profiles"]
    agents = policy["agents"]
    for lane, spec in lanes.items():
        if lane not in LANES:
            raise CatalogError(f"unknown lane {lane!r}")
        if not isinstance(spec, dict):
            raise CatalogError(f"lane {lane} must be an object")
        expected = {"allowed_profiles", "floor", "default", "max_relaunches_per_hour",
                    "cooldown_seconds", "reviewer"}
        if set(spec) != expected:
            raise CatalogError(f"lane {lane} must define exactly {sorted(expected)}")
        allowed = spec["allowed_profiles"]
        if (not isinstance(allowed, list) or not allowed
                or not all(isinstance(p, str) for p in allowed)
                or len(set(allowed)) != len(allowed)):
            raise CatalogError(f"lane {lane} allowed_profiles must be a unique nonempty list")
        unknown = [p for p in allowed if p not in profiles]
        if unknown:
            raise CatalogError(f"lane {lane} references unknown profiles {unknown}")
        binding = agents.get(lane)
        if binding is None:
            raise CatalogError(f"lane {lane} has no capacity-policy agent binding")
        # The lane may narrow the advisor allowlist but never widen or reorder it.
        ordered = [p for p in binding["profiles"] if p in allowed]
        if ordered != allowed:
            raise CatalogError(
                f"lane {lane} allowed_profiles must be an in-order subset of the policy agent allowlist")
        floor = spec["floor"]
        if type(floor) is not int or not 0 <= floor < len(allowed):
            raise CatalogError(f"lane {lane} floor must index allowed_profiles")
        if spec["default"] not in allowed:
            raise CatalogError(f"lane {lane} default must be an allowed profile")
        if allowed.index(spec["default"]) > floor:
            raise CatalogError(f"lane {lane} default must not be below its floor")
        _check_runtime_admissible(lane, allowed, binding["role"], profiles)
        _positive_int(spec["max_relaunches_per_hour"], f"lane {lane} max_relaunches_per_hour", 12)
        _positive_int(spec["cooldown_seconds"], f"lane {lane} cooldown_seconds", 86_400)
        if type(spec["reviewer"]) is not bool:
            raise CatalogError(f"lane {lane} reviewer must be a boolean")
        if (lane in REVIEWER_LANES) != spec["reviewer"]:
            raise CatalogError(f"lane {lane} reviewer flag does not match the reviewer lane set")


def _check_runtime_admissible(lane: str, allowed: list, role: str, profiles: dict) -> None:
    """Refuse statically what the advisor's _profile_checks would refuse at runtime.

    A profile the advisor can never select is not a real option; listing it would
    only make the catalog claim a choice the runtime does not have.
    """
    first = profiles[allowed[0]]
    for profile_id in allowed:
        profile = profiles[profile_id]
        if profile.get("approved") is not True or not (
                isinstance(profile.get("qualification_ref"), str)
                and profile["qualification_ref"].strip()):
            raise CatalogError(f"lane {lane} profile {profile_id} is not approved with a qualification_ref")
        qualified_for = profile.get("qualified_for")
        if (not isinstance(qualified_for, list) or not qualified_for
                or not all(isinstance(q, str) and q for q in qualified_for)):
            raise CatalogError(f"lane {lane} profile {profile_id} has no qualification classes")
        roles = profile.get("roles")
        if not isinstance(roles, list) or role not in roles:
            raise CatalogError(f"lane {lane} profile {profile_id} is not qualified for role {role}")
        if profile.get("billing") != "subscription":
            raise CatalogError(f"lane {lane} profile {profile_id} is not subscription billing")
        # Resume keeps the conversation; it cannot move it across providers or accounts.
        if (profile["provider"] != first["provider"]
                or profile["account_pool"] != first["account_pool"]):
            raise CatalogError(f"lane {lane} mixes providers or account pools")


def _validate_fleet(fleet: Any, lanes: dict) -> None:
    expected = {"mode", "max_relaunches_per_hour_total", "verify_timeout_seconds",
                "shadow_exit", "approve_exit"}
    if not isinstance(fleet, dict) or set(fleet) != expected:
        raise CatalogError(f"fleet must define exactly {sorted(expected)}")
    if fleet["mode"] not in MODES:
        raise CatalogError(f"fleet.mode must be one of {MODES}")
    total = _positive_int(fleet["max_relaunches_per_hour_total"],
                          "fleet.max_relaunches_per_hour_total", 30)
    if any(spec["max_relaunches_per_hour"] > total for spec in lanes.values()):
        raise CatalogError("a lane budget exceeds the fleet total")
    _positive_int(fleet["verify_timeout_seconds"], "fleet.verify_timeout_seconds", 3600)
    _validate_exit(fleet["shadow_exit"], "fleet.shadow_exit",
                   ("min_decisions", "min_days", "max_operator_marked_wrong"))
    _validate_exit(fleet["approve_exit"], "fleet.approve_exit",
                   ("min_transitions", "min_induced_rollbacks", "max_wrong_process_kills"))


def validate_catalog(catalog: Any) -> dict:
    """Validate a parsed catalog and return it unchanged, or raise CatalogError."""
    if not isinstance(catalog, dict):
        raise CatalogError("catalog must be a JSON object")
    expected = {"schema", "catalog_ref", "operator_signature", "providers", "capacity_policy",
                "lanes", "fleet"}
    if set(catalog) != expected:
        raise CatalogError(f"catalog must define exactly {sorted(expected)}")
    if catalog["schema"] != SCHEMA:
        raise CatalogError(f"schema must be {SCHEMA}")
    for field in ("catalog_ref", "operator_signature"):
        if not isinstance(catalog[field], str) or not catalog[field].strip():
            raise CatalogError(f"{field} required")
    policy = catalog["capacity_policy"]
    if not isinstance(policy, dict):
        raise CatalogError("capacity_policy must be the capacity advisor policy object")
    if set(policy) != POLICY_KEYS:
        raise CatalogError("capacity_policy must carry exactly the advisor v1 fields")
    for profile_id, profile in _mapping(policy.get("profiles"), "capacity_policy.profiles").items():
        if not isinstance(profile, dict) or set(profile) != PROFILE_KEYS:
            raise CatalogError(f"profile {profile_id} must carry exactly the advisor profile fields")
    for agent_id, binding in _mapping(policy.get("agents"), "capacity_policy.agents").items():
        if not isinstance(binding, dict) or set(binding) != AGENT_BINDING_KEYS:
            raise CatalogError(f"agent binding {agent_id} must carry exactly role and profiles")
    try:
        _validate_policy(policy)
    except InputError as exc:
        raise CatalogError(f"embedded capacity policy is invalid: {exc}") from None
    _validate_providers(catalog["providers"], policy["profiles"])
    _validate_lanes(catalog["lanes"], policy)
    _validate_fleet(catalog["fleet"], catalog["lanes"])
    return catalog


def effective_mode(catalog: dict) -> str:
    """The one mode consumers may act on: the weaker of fleet.mode and the policy mode.

    fleet.mode is not a second switch. The advisor accepts only a shadow policy,
    so while the capacity policy is shadow the effective mode is shadow whatever
    fleet.mode says.
    """
    policy_mode = catalog["capacity_policy"]["mode"]
    fleet_mode = catalog["fleet"]["mode"]
    if policy_mode not in MODES or fleet_mode not in MODES:
        return "shadow"
    return MODES[min(MODES.index(policy_mode), MODES.index(fleet_mode))]


def load_catalog(path: str | Path) -> tuple[dict, str]:
    """Read exactly one catalog file; return (catalog, sha256 of its bytes)."""
    data = Path(path).read_bytes()
    if len(data) > MAX_CATALOG_BYTES:
        raise CatalogError("catalog exceeds the size bound")
    try:
        catalog = json.loads(data.decode("utf-8"),
                             parse_constant=lambda name: (_ for _ in ()).throw(
                                 CatalogError(f"non-finite JSON constant {name}")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"catalog is not UTF-8 JSON: {exc.__class__.__name__}") from None
    return validate_catalog(catalog), hashlib.sha256(data).hexdigest()


def classify_transition(catalog: dict, lane: str, current: str | None, target: str) -> dict:
    """Classify a lane profile change against the catalog; pure, no side effects.

    Returns ``verdict``: ``same``, ``raise``, ``lower`` or ``park``; ``park``
    always carries ``operator_ack_required`` and a reason. ``current`` may be
    None when the running profile is unknown, which is itself a reason to park:
    raising from an unknown profile cannot be proven to be a raise.
    """
    spec = catalog["lanes"].get(lane)
    if spec is None:
        return {"verdict": "park", "operator_ack_required": True, "reason": "lane_not_in_catalog"}
    allowed = spec["allowed_profiles"]
    if target not in allowed:
        return {"verdict": "park", "operator_ack_required": True, "reason": "target_not_allowed"}
    target_index = allowed.index(target)
    if target_index > spec["floor"]:
        return {"verdict": "park", "operator_ack_required": True, "reason": "below_floor"}
    if current is None or current not in allowed:
        return {"verdict": "park", "operator_ack_required": True, "reason": "current_profile_unknown"}
    current_index = allowed.index(current)
    if target_index == current_index:
        return {"verdict": "same", "operator_ack_required": False, "reason": "no_change"}
    if target_index < current_index:
        return {"verdict": "raise", "operator_ack_required": False, "reason": "stronger_profile"}
    if spec["reviewer"]:
        # A reviewed party must never weaken its reviewer, even inside the floor.
        return {"verdict": "park", "operator_ack_required": True, "reason": "reviewer_lowering"}
    return {"verdict": "lower", "operator_ack_required": False, "reason": "weaker_profile_within_floor"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the lane profile catalog (read-only).")
    parser.add_argument("catalog", type=Path)
    args = parser.parse_args(argv)
    try:
        catalog, digest = load_catalog(args.catalog)
    except (CatalogError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps({"ok": True, "catalog_sha256": digest, "mode": effective_mode(catalog),
                      "lanes": sorted(catalog["lanes"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
