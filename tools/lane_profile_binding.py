#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Session <-> lane binding (D3 of lane profile switching): pure, read-only.

Two independent states, never one flag (Lead review LPS-C1):

``session_identity``
    The observed native conversation is the one the launcher recorded for this
    lane, AND the recorded process instance (pid + creation time) is still the
    live one. Claude evidence is the capacity observation keyed by
    ``native_thread_id``; Codex evidence is ``read_native_codex`` for the
    recorded thread, requiring a turn later than the recorded process start.

``quota_pool_binding``
    The profile's provider, account pool and quota limit ids match the quota
    row's. A thread binding never authenticates an account quota row, and an
    account-level Codex quota row never authenticates a session.

``profile_observed`` compares the observed model and effort with the record's
desired profile, for the verification step of a relaunch.

Everything is decided from caller-supplied, already-measured inputs. No input
means ``unbound``/``unverified``, never a pass.
"""
from __future__ import annotations

import re
from typing import Any

from tools.lane_profile_record import _utc

PROCESS_EPOCH_SKEW_SECONDS = 2.0
_CONTEXT_SUFFIX = re.compile(r"\[[^\]]*\]$")


def _base_model(model: Any) -> str | None:
    """Strip a Claude context-window suffix such as ``[1m]``; keep the raw value elsewhere."""
    if not isinstance(model, str) or not model:
        return None
    return _CONTEXT_SUFFIX.sub("", model)


def process_instance_live(launched: dict, live_processes: dict) -> tuple[bool, str]:
    """True only when the recorded pid is live with the recorded creation time."""
    started = live_processes.get(launched["pid"]) if isinstance(live_processes, dict) else None
    if started is None:
        return False, "recorded_process_not_live"
    live, recorded = _utc(started), _utc(launched["process_started_at"])
    if live is None or recorded is None:
        return False, "process_epoch_unparseable"
    if abs((live - recorded).total_seconds()) > PROCESS_EPOCH_SKEW_SECONDS:
        return False, "pid_reused_by_another_process"
    return True, "process_instance_matches"


def bind_lane(record: dict, catalog: dict, *, live_processes: dict,
              claude_observations: list | None = None, codex_native: dict | None = None,
              quota_rows: list | None = None) -> dict:
    """Classify the binding of one validated record against measured evidence."""
    lane = record["lane"]
    profile = catalog["capacity_policy"]["profiles"][record["desired_profile"]]
    provider = profile["provider"]
    result = {"lane": lane, "provider": provider,
              "session_identity": "unbound", "session_reason": "launcher_record_not_written",
              "profile_observed": "unverified", "profile_reason": "no_session_identity",
              "observed_model_raw": None, "observed_effort": None,
              "quota_pool_binding": "unverified", "quota_reason": "no_quota_row"}
    launched = record.get("launched")
    if launched is not None:
        alive, reason = process_instance_live(launched, live_processes)
        if not alive:
            result.update(session_identity="invalid", session_reason=reason)
        elif provider == "claude":
            _bind_claude(result, launched, profile, claude_observations)
        elif provider == "codex":
            _bind_codex(result, launched, profile, codex_native)
        else:
            result.update(session_identity="invalid", session_reason="unsupported_provider")
    _bind_quota(result, profile, quota_rows)
    return result


def _bind_claude(result: dict, launched: dict, profile: dict, observations: list | None) -> None:
    thread = launched["native_thread_id"]
    rows = [o for o in (observations or []) if isinstance(o, dict)
            and o.get("provider") == "claude" and o.get("native_thread_id") == thread]
    if not rows:
        # A freshly launched Claude lane has no statusline row until its first turn.
        result.update(session_identity="unbound", session_reason="no_observation_for_recorded_thread")
        return
    dated = [(stamp, o) for o in rows if (stamp := _utc(o.get("observed_at"))) is not None]
    if not dated:
        result.update(session_identity="unbound", session_reason="no_observation_since_launch")
        return
    observed_at, newest = max(dated, key=lambda pair: pair[0])
    started = _utc(launched["process_started_at"])
    if observed_at is None or started is None or observed_at < started:
        result.update(session_identity="unbound", session_reason="no_observation_since_launch")
        return
    result.update(session_identity="valid", session_reason="thread_and_process_match")
    _compare(result, profile, newest.get("model"), newest.get("effort"))


def _bind_codex(result: dict, launched: dict, profile: dict, native: dict | None) -> None:
    if not isinstance(native, dict) or native.get("native_thread_id") != launched["native_thread_id"]:
        result.update(session_identity="unbound", session_reason="no_rollout_for_recorded_thread")
        return
    turn, started = _utc(native.get("observed_at")), _utc(launched["process_started_at"])
    if turn is None or started is None or turn <= started:
        # read_native_codex reports only the latest turn; it must postdate the new process.
        result.update(session_identity="unbound", session_reason="no_turn_since_launch")
        return
    result.update(session_identity="valid", session_reason="thread_and_process_match")
    _compare(result, profile, native.get("model"), native.get("effort"))


def _compare(result: dict, profile: dict, model: Any, effort: Any) -> None:
    result.update(observed_model_raw=model if isinstance(model, str) else None,
                  observed_effort=effort if isinstance(effort, str) else None)
    if _base_model(model) is None or not isinstance(effort, str):
        result.update(profile_observed="unverified", profile_reason="model_or_effort_not_observed")
    elif _base_model(model) == profile["model"] and effort == profile["effort"]:
        result.update(profile_observed="match", profile_reason="model_and_effort_equal")
    else:
        result.update(profile_observed="mismatch", profile_reason="model_or_effort_differs")


def _bind_quota(result: dict, profile: dict, rows: list | None) -> None:
    wanted = {(profile["provider"], limit["id"]) for limit in profile["limits"]}
    matching = [r for r in (rows or []) if isinstance(r, dict)
                and (r.get("provider"), r.get("limit_id")) in wanted]
    if not matching:
        return
    pools = {r.get("account_pool") for r in matching}
    if pools != {profile["account_pool"]}:
        result.update(quota_pool_binding="invalid", quota_reason="account_pool_differs_or_unknown")
        return
    if {(r["provider"], r["limit_id"]) for r in matching} != wanted:
        result.update(quota_pool_binding="unverified", quota_reason="not_every_limit_observed")
        return
    result.update(quota_pool_binding="valid", quota_reason="provider_pool_and_limits_match")
