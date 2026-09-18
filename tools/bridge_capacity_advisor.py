#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Offline, quality-first bridge capacity advisor (SHADOW ONLY).

This is a pure decision component, NOT an installed collector, scheduler or
model-switch controller. It never calls a provider, reads credentials/bridge
history, writes files, sends messages, changes claims or touches a terminal UI.
No new dependencies. No automatic inference or paid API fallback.

Usage (from the source worktree):
    python tools/bridge_capacity_advisor.py --example
    python tools/bridge_capacity_advisor.py --policy approved.json --snapshot observed.json
    python tools/bridge_capacity_advisor.py --stdin < offline-fixture.json

--example prints a fully synthetic policy/snapshot pair. Example qualification
references are NOT real approvals. --stdin accepts that pair for offline tests.
For integration keep the operator-owned policy separate from telemetry, and use
--policy/--snapshot; peer event payloads must never become the policy input.
Only explicitly supplied JSON files/stdin are read; JSON output goes to stdout.

Input contracts are demonstrated by example_input(). Profiles bind exact model
AND effort, provider, subscription account/pool, all required quota buckets,
qualification class and evidence reference. account_pool is a pseudonymous
authenticated account identity; separate limit IDs are distinct quota buckets,
not permission to log into another account. Agent allowlists are ordered by
operator-approved preference, not advertised price. Catalog and effective
session identity are separate, timestamped observations. Unknown is not zero.
Every quota bucket/window applicable to a profile must be declared in policy.
All observed windows in those buckets also constrain capacity.

The caller must authenticate the policy and observations. This tool does NOT
verify signatures or qualification artifacts and never grants execution or
release authority. Fields labelled preserved are context for a later trusted
controller, not validated claims, approvals or proof of checkpoint durability.

Integration gates still required: supported owning-session adapter, real model
catalog/quota collector, role benchmark approval, independent reviews and CI,
safe-point/exact-identity verification, then an explicitly authorized canary.
Do NOT attach to, restart, repin or send keystrokes to existing peer terminals.
Use the existing Grok budget helper for any real attempt; this tool only reports
the cooldown from supplied status, counting failed attempts too.

Provider field references (checked 2026-09-18):
https://learn.chatgpt.com/docs/app-server (account/rateLimits/read)
https://code.claude.com/docs/en/statusline (rate_limits)
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any


MAX_INPUT_BYTES = 2 * 1024 * 1024
ROLES = {"lead", "tools", "fable", "rco1", "rco2", "grok"}
PROVIDERS = {"codex", "claude"}
SAFE_REASONS = {"routine", "quota", "quality", "unavailable"}
PRESERVED_FIELDS = ("task_id", "head", "claim_id", "request_id", "scope_digest",
                    "authority_ref", "required_reviewers")


class InputError(ValueError):
    """Malformed input, never a request to silently fall back."""


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 2048


def _number(value: Any) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _fresh(value: Any, now: datetime, seconds: int) -> bool:
    parsed = _time(value)
    return parsed is not None and 0 <= (now - parsed).total_seconds() <= seconds


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _strings(value: Any, *, nonempty: bool = True) -> bool:
    return (isinstance(value, list) and (bool(value) or not nonempty)
            and all(_text(x) for x in value) and len(set(value)) == len(value))


def _required_object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise InputError(f"{label} must be an object")
    return value


def _indexed(rows: Any, key: str, label: str) -> dict:
    if not isinstance(rows, list):
        raise InputError(f"{label} must be an array")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or not _text(row.get(key)) or row[key] in result:
            raise InputError(f"{label} requires unique nonempty identifiers")
        result[row[key]] = row
    return result


def _validate_limits(limits: Any, provider: str) -> None:
    if not isinstance(limits, list) or not limits:
        raise InputError("profile requires explicit quota buckets/windows")
    ids = set()
    permitted = {"primary", "secondary"} if provider == "codex" else {
        "five_hour", "seven_day", "spend_limit"}
    for limit in limits:
        if (not isinstance(limit, dict) or not _text(limit.get("id"))
                or limit["id"] in ids or not _strings(limit.get("windows"))
                or not set(limit["windows"]) <= permitted):
            raise InputError("invalid or duplicate quota bucket/windows")
        if provider == "claude" and limit["id"] != "claude":
            raise InputError("Claude statusline requires the claude quota bucket")
        ids.add(limit["id"])


def normalize_capacity(observations: list, *, provider: str, account_pool: str,
                       limits: list, now: datetime, max_age_seconds: int) -> dict:
    """Reduce a provider snapshot without inferring quota from tokens/credits.

    Use the newest observation for the exact provider/account pool. Tied but
    different payloads are unknown; never prefer the more optimistic one. A
    reset in the past requires a new observation, not an assumed refill.
    """
    if provider not in PROVIDERS or not _text(account_pool):
        raise InputError("explicit supported provider/account pool required")
    _validate_limits(limits, provider)
    result = {"provider": provider, "account_pool": account_pool,
              "state": "unknown", "reason": "missing_observation",
              "observed_at": None, "source_ref": None, "windows": []}
    matches = [row for row in observations if isinstance(row, dict)
               and row.get("provider") == provider and row.get("account_pool") == account_pool]
    if not matches:
        return result
    if any(_time(row.get("observed_at")) is None for row in matches):
        result["reason"] = "invalid_observation_time"
        return result
    newest = max(_time(row["observed_at"]) for row in matches)
    latest = [row for row in matches if _time(row["observed_at"]) == newest]
    row = latest[0]
    result.update(observed_at=row["observed_at"], source_ref=row.get("source_ref"))
    if any(other.get("payload") != row.get("payload") for other in latest[1:]):
        result["reason"] = "conflicting_observations"
        return result
    if not _fresh(row["observed_at"], now, max_age_seconds):
        result["reason"] = "stale_or_future_observation"
        return result
    if not _text(row.get("source_ref")):
        result["reason"] = "missing_source_reference"
        return result
    payload = _dict(row.get("payload"))
    unknown = False
    exhausted = False
    for limit in limits:
        if provider == "codex":
            # Presence of the multi-bucket map is authoritative, even if empty.
            if "rateLimitsByLimitId" in payload:
                bucket = _dict(_dict(payload["rateLimitsByLimitId"]).get(limit["id"]))
            else:
                bucket = _dict(payload.get("rateLimits"))
            if bucket.get("limitId") != limit["id"]:
                unknown = True
                continue
            if bucket.get("rateLimitReachedType") is not None:
                exhausted = True
            names = set(limit["windows"]) | {x for x in ("primary", "secondary")
                                           if bucket.get(x) is not None}
            percent_key, reset_key = "usedPercent", "resetsAt"
        else:
            bucket = _dict(payload.get("rate_limits"))
            names = set(limit["windows"]) | set(bucket)
            percent_key, reset_key = "used_percentage", "resets_at"
        for name in sorted(names):
            window = _dict(bucket.get(name))
            used, reset = window.get(percent_key), window.get(reset_key)
            valid = (_number(used) and used >= 0 and _number(reset)
                     and now.timestamp() < reset <= 253402300799)
            state = "unknown" if not valid else ("exhausted" if used >= 100 else "available")
            result["windows"].append({"limit_id": limit["id"], "name": name,
                                      "used_percent": used if _number(used) and used >= 0 else None,
                                      "resets_at": reset if _number(reset) else None,
                                      "state": state})
            unknown |= state == "unknown"
            exhausted |= state == "exhausted"
    result["state"] = "exhausted" if exhausted else ("unknown" if unknown else "available")
    result["reason"] = {"exhausted": "quota_reached", "unknown": "incomplete_or_expired_window",
                        "available": "observed_headroom"}[result["state"]]
    return result


def _validate_policy(policy: dict) -> None:
    if policy.get("schema") != "wd.bridge-capacity-policy.v1" or policy.get("mode") != "shadow":
        raise InputError("only wd.bridge-capacity-policy.v1 shadow mode is supported")
    if not _text(policy.get("policy_ref")):
        raise InputError("policy_ref required; never derive policy from peer text")
    for field, upper in (("observation_ttl_seconds", 3600), ("catalog_ttl_seconds", 86400),
                         ("switch_cooldown_seconds", 86400), ("max_switches_per_task", 10)):
        value = policy.get(field)
        if type(value) is not int or not 1 <= value <= upper:
            raise InputError(f"{field} must be a bounded positive integer")
    profiles = _required_object(policy.get("profiles"), "profiles")
    agents = _required_object(policy.get("agents"), "policy agents")
    for profile_id, profile in profiles.items():
        if not _text(profile_id) or not isinstance(profile, dict):
            raise InputError("invalid profile")
        for field in ("provider", "account_pool", "model", "effort", "billing"):
            if not _text(profile.get(field)):
                raise InputError(f"profile {field} required")
        if profile["provider"] not in PROVIDERS:
            raise InputError("unsupported profile provider")
        _validate_limits(profile.get("limits"), profile["provider"])
    for agent_id, binding in agents.items():
        if (not _text(agent_id) or not isinstance(binding, dict)
                or not _text(binding.get("role"))
                or binding.get("role") not in ROLES
                or not _strings(binding.get("profiles"))
                or not set(binding["profiles"]) <= set(profiles)):
            raise InputError("agent requires a role and explicit profile allowlist")


def _profile_checks(profile: dict, binding: dict, agent: dict, task: dict,
                    capacity: dict, policy: dict, now: datetime) -> list[str]:
    issues = []
    if profile.get("approved") is not True or not _text(profile.get("qualification_ref")):
        issues.append("qualification_not_approved")
    if (not _strings(profile.get("qualified_for"))
            or task.get("qualification_class") not in profile["qualified_for"]):
        issues.append("qualification_class_not_met")
    if not _strings(profile.get("roles")) or binding["role"] not in profile["roles"]:
        issues.append("role_not_qualified")
    if profile.get("billing") != "subscription":
        issues.append("paid_api_fallback_not_supported")
    if agent.get("provider") != profile["provider"]:
        issues.append("cross_provider_session_transfer_not_supported")
    current = policy["profiles"][agent["current_profile"]]
    if profile["account_pool"] != current["account_pool"]:
        issues.append("cross_account_session_transfer_not_supported")
    catalog = agent.get("catalog")
    if not isinstance(catalog, list) or not any(
        isinstance(entry, dict) and entry.get("model") == profile["model"]
        and entry.get("effort") == profile["effort"]
        and _text(entry.get("source_ref"))
        and _fresh(entry.get("observed_at"), now, policy["catalog_ttl_seconds"])
        for entry in catalog
    ):
        issues.append("catalog_unknown_or_stale")
    if capacity["state"] != "available":
        issues.append("capacity_" + capacity["state"])
    return issues


def _switch_checks(agent: dict, task: dict, policy: dict, now: datetime) -> list[str]:
    issues = []
    if agent.get("idle") is not True or agent.get("pending_effects") is not False:
        issues.append("not_at_safe_boundary")
    count = agent.get("switches_this_task")
    if type(count) is not int or count < 0 or count >= policy["max_switches_per_task"]:
        issues.append("switch_budget_exhausted_or_unknown")
    last = agent.get("last_switch_at")
    if "last_switch_at" not in agent or (last is None and count != 0):
        issues.append("switch_history_unknown")
    elif last is not None:
        stamp = _time(last)
        if stamp is None or (now - stamp).total_seconds() < policy["switch_cooldown_seconds"]:
            issues.append("switch_cooldown_or_invalid_history")
    checkpoint = _dict(agent.get("checkpoint"))
    expected = {name: task.get(name) for name in
                ("task_id", "head", "claim_id", "request_id", "scope_digest")}
    expected.update(session_id=agent.get("session_id"), native_thread_id=agent.get("native_thread_id"),
                    profile_id=agent.get("current_profile"))
    if not _text(checkpoint.get("reference")) or any(
        not _text(value) or checkpoint.get(name) != value for name, value in expected.items()
    ):
        issues.append("checkpoint_binding_mismatch_or_missing")
    return issues


def _task_advice(task: dict, policy: dict, agents: dict, capacities: dict, now: datetime) -> dict:
    result = {"task_id": task["task_id"], "agent_id": task.get("agent_id"),
              "action": "blocked", "proposed_profile": None, "reasons": [],
              "candidates": [], "execution_allowed": False, "release_allowed": False,
              "preserved": {key: deepcopy(task.get(key)) for key in PRESERVED_FIELDS}}
    issues = result["reasons"]
    if task.get("hold") is not False or task.get("cancelled") is not False:
        issues.append("task_held_cancelled_or_unknown")
    if not _text(task.get("authority_ref")) or not _text(task.get("qualification_class")):
        issues.append("task_authority_or_quality_class_missing")
    if not _text(task.get("reason")) or task.get("reason") not in SAFE_REASONS:
        issues.append("failure_requires_diagnosis_not_model_switch")
    if not _strings(task.get("required_reviewers"), nonempty=False):
        issues.append("review_gate_unknown")
    if not _text(task.get("kind")) or task.get("kind") not in {"implementation", "review", "advisory"}:
        issues.append("unknown_task_kind")
    if not _text(task.get("agent_id")):
        issues.append("agent_binding_or_observation_missing")
        return result
    binding = policy["agents"].get(task.get("agent_id"))
    agent = agents.get(task.get("agent_id"))
    if not binding or not agent:
        issues.append("agent_binding_or_observation_missing")
        return result
    if task.get("kind") == "review":
        if not _text(task.get("author_agent")) or task["author_agent"] == task["agent_id"]:
            issues.append("review_not_independent")
        if not isinstance(task.get("required_reviewers"), list) or task["agent_id"] not in task["required_reviewers"]:
            issues.append("reviewer_not_assigned")
    if binding["role"] in {"rco1", "rco2"} and task.get("kind") != "review":
        issues.append("reviewer_not_implementation_worker")
    if binding["role"] == "grok":
        issues.append("grok_requires_existing_budget_helper_not_routing")
    if agent.get("hold") is not False:
        issues.append("agent_held_or_unknown")
    if (not _text(agent.get("session_id")) or not _text(agent.get("native_thread_id"))
            or not _fresh(agent.get("observed_at"), now, policy["observation_ttl_seconds"])):
        issues.append("session_identity_unknown_or_stale")
    current_id = agent.get("current_profile")
    if not _text(current_id):
        issues.append("current_profile_not_allowed")
        return result
    current = policy["profiles"].get(current_id)
    if current_id not in binding["profiles"] or not current:
        issues.append("current_profile_not_allowed")
        return result
    if (agent.get("model_observed") != current["model"]
            or agent.get("effort_observed") != current["effort"]
            or agent.get("provider") != current["provider"]):
        issues.append("effective_model_effort_or_provider_unverified")
    if issues:
        return result
    eligible = []
    for profile_id in binding["profiles"]:
        profile = policy["profiles"][profile_id]
        problems = _profile_checks(profile, binding, agent, task, capacities[profile_id], policy, now)
        if profile_id == current_id and task["reason"] in {"quality", "unavailable"}:
            problems.append("current_profile_requires_alternative")
        result["candidates"].append({"profile_id": profile_id, "reasons": problems,
                                     "eligible": not problems})
        if not problems:
            eligible.append(profile_id)
    if current_id in eligible:
        result.update(action="keep_current", proposed_profile=current_id)
        return result
    if not eligible:
        result.update(action="wait_capacity" if task["reason"] == "quota" else "blocked")
        issues.append("no_qualified_available_profile")
        return result
    issues.extend(_switch_checks(agent, task, policy, now))
    if issues:
        return result
    result.update(action="switch_proposed", proposed_profile=eligible[0])
    result["verification_required"] = [
        "owning_session_adapter_acceptance", "recheck_policy_claims_holds_and_safe_boundary",
        "recheck_catalog_and_all_quota_pools", "verify_actual_model_effort_and_same_identity",
        "preserve_exact_request_and_review_gates", "record_idempotent_transition",
    ]
    return result


def _grok_status(status: Any, now: datetime, ttl: int) -> dict:
    status = _dict(status)
    result = {"state": "unknown", "execution_allowed": False,
              "seconds_until_eligible": None, "next_eligible_at": None,
              "note": "Report only; existing shared budget helper owns every real attempt."}
    if not _fresh(status.get("observed_at"), now, ttl) or not _text(status.get("source_ref")):
        return result
    if status.get("in_flight") is True:
        result["state"] = "in_flight"
        return result
    last = _time(status.get("last_attempt_at"))
    if status.get("in_flight") is not False or last is None or last > now:
        return result
    next_at = last + timedelta(hours=1)
    remaining = max(0, math.ceil((next_at - now).total_seconds()))
    result.update(state="cooldown" if remaining else "recheck_shared_budget",
                  seconds_until_eligible=remaining, next_eligible_at=next_at.isoformat())
    return result


def build_report(policy: dict, snapshot: dict, *, now: datetime | None = None) -> dict:
    """Evaluate trusted caller-supplied metadata, with no side effects."""
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise InputError("now requires an explicit timezone")
    policy = _required_object(policy, "policy")
    snapshot = _required_object(snapshot, "snapshot")
    _validate_policy(policy)
    if snapshot.get("schema") != "wd.bridge-capacity-snapshot.v1":
        raise InputError("unsupported snapshot schema")
    agents = _indexed(snapshot.get("agents"), "agent_id", "agents")
    tasks = _indexed(snapshot.get("tasks"), "task_id", "tasks")
    observations = snapshot.get("observations")
    if not isinstance(observations, list) or not all(isinstance(row, dict) for row in observations):
        raise InputError("observations must be an array of objects")
    capacities = {name: normalize_capacity(
        observations, provider=profile["provider"], account_pool=profile["account_pool"],
        limits=profile["limits"], now=now, max_age_seconds=policy["observation_ttl_seconds"])
        for name, profile in policy["profiles"].items()}
    rows = [_task_advice(task, policy, agents, capacities, now) for task in tasks.values()]
    agent_rows = {}
    for agent_id, binding in policy["agents"].items():
        agent = agents.get(agent_id, {})
        current_id = agent.get("current_profile")
        current = policy["profiles"].get(current_id, {}) if _text(current_id) else {}
        identity_ok = (_text(agent.get("session_id")) and _text(agent.get("native_thread_id"))
                       and _fresh(agent.get("observed_at"), now, policy["observation_ttl_seconds"]))
        agent_rows[agent_id] = {
            "role": binding["role"], "identity_state": "observed" if identity_ok else "unknown_or_stale",
            "session_id": agent.get("session_id"), "native_thread_id": agent.get("native_thread_id"),
            "observed_at": agent.get("observed_at"), "provider": agent.get("provider"),
            "model_requested": current.get("model"), "model_observed": agent.get("model_observed"),
            "effort_requested": current.get("effort"), "effort_observed": agent.get("effort_observed"),
            "account_pool": current.get("account_pool"), "current_profile": current_id,
        }
    return {"schema": "wd.bridge-capacity-report.v1", "mode": "shadow",
            "observed_at": now.isoformat(), "policy_ref": policy["policy_ref"],
            "execution_allowed": False, "qualification_evidence": "caller_asserted_not_verified",
            "profiles": capacities, "agents": agent_rows, "tasks": rows,
            "grok": _grok_status(snapshot.get("grok"), now, policy["observation_ttl_seconds"]),
            "limitations": ["no_live_collection", "no_model_switch", "no_task_dispatch",
                            "no_gate_approval", "no_policy_signature_verification",
                            "no_24h_acceptance_claim"]}


def example_input(now: datetime | None = None) -> dict:
    """Synthetic demonstration, NEVER an actual model/pool authorization."""
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    profiles = {}
    observations = []
    catalog = []
    buckets = {}
    for suffix in ("a", "b"):
        model = "example-model-" + suffix
        limit_id = "codex" if suffix == "a" else "example-independent-limit"
        profiles["qualified-" + suffix] = {
            "provider": "codex", "account_pool": "account-a",
            "model": model, "effort": "high", "billing": "subscription",
            "approved": True, "qualification_ref": "SYNTHETIC-NOT-A-LIVE-APPROVAL",
            "qualified_for": ["wd-critical-coding"], "roles": ["lead"],
            "limits": [{"id": limit_id, "windows": ["primary"]}],
        }
        buckets[limit_id] = {"limitId": limit_id, "primary": {"usedPercent": 25,
                              "resetsAt": (now + timedelta(hours=1)).timestamp()}}
        catalog.append({"model": model, "effort": "high", "observed_at": stamp,
                        "source_ref": "SYNTHETIC-CATALOG"})
    observations.append({"provider": "codex", "account_pool": "account-a",
                         "observed_at": stamp, "source_ref": "SYNTHETIC-FIXTURE",
                         "payload": {"rateLimitsByLimitId": buckets}})
    task = {"task_id": "example-task", "agent_id": "example-lead", "kind": "implementation",
            "authority_ref": "SYNTHETIC-NOT-A-LIVE-TASK", "qualification_class": "wd-critical-coding",
            "hold": False, "cancelled": False, "reason": "quota", "head": "example-head",
            "claim_id": "fixture-claim", "request_id": "example-request", "scope_digest": "example-scope",
            "required_reviewers": ["rco1", "rco2"], "author_agent": "example-lead"}
    checkpoint = {key: task[key] for key in ("task_id", "head", "claim_id", "request_id", "scope_digest")}
    checkpoint.update(session_id="example-session", native_thread_id="example-thread",
                      profile_id="qualified-a", reference="SYNTHETIC-CHECKPOINT")
    return {
        "policy": {"schema": "wd.bridge-capacity-policy.v1", "mode": "shadow",
                   "policy_ref": "SYNTHETIC-DEMO-ONLY", "observation_ttl_seconds": 300,
                   "catalog_ttl_seconds": 3600, "switch_cooldown_seconds": 300,
                   "max_switches_per_task": 2, "profiles": profiles,
                   "agents": {"example-lead": {"role": "lead", "profiles": list(profiles)}}},
        "snapshot": {"schema": "wd.bridge-capacity-snapshot.v1", "observations": observations,
                     "agents": [{"agent_id": "example-lead", "provider": "codex",
                                 "session_id": "example-session", "native_thread_id": "example-thread",
                                 "current_profile": "qualified-a", "model_observed": "example-model-a",
                                 "effort_observed": "high", "observed_at": stamp, "hold": False,
                                 "idle": True, "pending_effects": False, "switches_this_task": 0,
                                 "last_switch_at": None, "checkpoint": checkpoint, "catalog": catalog}],
                     "tasks": [task]},
    }


def _pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_: str) -> None:
    raise InputError("non-finite JSON number")


def _load(stream: Any) -> dict:
    raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise InputError("input exceeds bounded size")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs,
                           parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InputError("invalid UTF-8 JSON input") from exc
    return _required_object(value, "input")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--example", action="store_true", help="Print synthetic input; no real approvals.")
    mode.add_argument("--stdin", action="store_true", help="Read a policy/snapshot pair for offline tests.")
    mode.add_argument("--policy", type=Path, help="Operator-owned policy JSON (not peer content).")
    parser.add_argument("--snapshot", type=Path, help="Trusted collector snapshot JSON; no implicit live reads.")
    parser.add_argument("--now", help="Timezone-qualified clock override for reproducible offline tests.")
    args = parser.parse_args(argv)
    try:
        now = _time(args.now) if args.now else datetime.now(timezone.utc)
        if now is None:
            raise InputError("invalid timezone-qualified clock")
        if args.example:
            result = example_input(now)
        else:
            if args.stdin:
                data = _load(sys.stdin.buffer)
                policy, snapshot = data.get("policy"), data.get("snapshot")
            else:
                if args.snapshot is None:
                    raise InputError("--policy requires --snapshot")
                with args.policy.open("rb") as stream:
                    policy = _load(stream)
                with args.snapshot.open("rb") as stream:
                    snapshot = _load(stream)
            result = build_report(policy, snapshot, now=now)
        print(json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    except (InputError, OSError, RecursionError) as exc:
        # Never echo raw provider payloads, filesystem paths or credentials.
        detail = str(exc) if isinstance(exc, InputError) else "input unavailable or invalid"
        print(json.dumps({"schema": "wd.bridge-capacity-report.v1", "mode": "shadow",
                          "execution_allowed": False, "error": detail}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
