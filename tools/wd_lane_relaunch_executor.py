#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Lane relaunch executor (D4 steps 3-9 of lane profile switching), ports only.

Drives one lane from its current profile to a catalog target and records every
step in the existing capacity recovery journal (``RecoveryStore``). It is a
restart-based transition, so it does not use the journal's same-process
``advance()`` driver, whose binding fences one continuing process (see that
module's docstring). Instead it journals both epochs explicitly:

* source epoch: pid + creation time of the process being replaced, measured
  before the stop;
* target epoch: bound once, only from facts the launcher recorded in the lane's
  D2 record, cross-checked against execution-evidence ancestry. A caller-supplied
  target pid is never accepted.

All side effects go through an injected ``Ports`` object. There is no default,
production implementation here and nothing in the runtime calls this module:
the launcher and supervisor wiring is PR-4, which is (a)-class. Order, fail
closed at every step (spec v3 D4, as amended by the Lead and RCO reviews):

1. ``check_request`` (catalog, budgets, cooldown); 2. mode gate: shadow only
   returns would_relaunch, approve fails closed without a verifiable operator
   ack, auto proceeds; Lead's own lane only via the supervisor executor;
3. measure the lane, ``check_safe_boundary``, and verify the CURRENT profile from
   D3 binding evidence (never from the record's self-declared previous_profile);
4. take the claim (record, transition lock and readiness paths), then re-measure
   and abort if anything changed (check-then-claim TOCTOU);
5. continuity: provider resume or a fresh checkpoint, else abort;
6. launch preconditions of the NEW process verified before the old one stops;
7. journal planned -> quiesced (D2 record written) -> checkpointed;
8. stop the verified source instance, then apply_pending + launch;
9. verify within the timeout; else exactly one rollback to the previous profile;
   rollback failure leaves the lane stopped and the journal reservation held for
   operator reconciliation;
10. a ``decision/profile_transition`` receipt for every outcome; release the claim.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Protocol

from tools.bridge_capacity_advisor import InputError
from tools.bridge_capacity_recovery import RecoveryStore
from tools.lane_profile_binding import bind_lane
from tools.lane_profile_catalog import effective_mode, is_signed
from tools.lane_profile_record import RecordError, _utc, record_path, validate_record
from tools.wd_lane_relaunch import PROCEED, check_request, check_safe_boundary

LEAD = "codex-lead-1"
REVIEWERS = ("claude-rco-1", "claude-rco-2")


class Ports(Protocol):
    """Every side effect and measurement the executor needs; injected, never defaulted."""

    def now(self) -> datetime: ...
    def sleep(self, seconds: float) -> None: ...
    def measure(self, lane: str) -> dict: ...
    def evidence(self, lane: str) -> dict: ...
    def observations(self, lane: str) -> dict: ...
    def take_claim(self, lane: str, scope: list[str], lease_seconds: int) -> str: ...
    def release_claim(self, claim_id: str) -> None: ...
    def preflight_launch(self, lane: str, profile: dict | None) -> list[str]: ...
    def checkpoint(self, lane: str) -> str: ...
    def stop(self, lane: str, pid: int, started_at: str) -> bool: ...
    def launch(self, lane: str, profile: dict | None) -> None: ...
    def read_record(self, lane: str) -> dict: ...
    def write_record(self, lane: str, record: dict) -> None: ...
    def emit(self, event: dict) -> None: ...


class ClaimConflict(Exception):
    """The relaunch claim could not be taken; the lane or its files are held."""


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Executor:
    """One relaunch attempt. Construct per request; ``run()`` returns the receipt."""

    def __init__(self, *, catalog: dict, catalog_sha256: str, store: RecoveryStore, ports: Ports,
                 runtime_root: str, request: dict, executor: str, lease_seconds: int | None = None):
        self.catalog, self.digest, self.store, self.ports = catalog, catalog_sha256, store, ports
        self.runtime_root, self.request, self.executor = runtime_root, request, executor
        self.lane = request.get("lane")
        timeout = catalog["fleet"]["verify_timeout_seconds"]
        self.lease_seconds = lease_seconds or timeout * 2 + 600
        self.claim_id: str | None = None
        self.tid: int | None = None
        self.steps: list[str] = []

    # ------------------------------------------------------------ helpers

    def _profile(self, profile_id: str) -> dict:
        profile = self.catalog["capacity_policy"]["profiles"][profile_id]
        return {"profile_id": profile_id, "provider": profile["provider"], "model": profile["model"],
                "effort": profile["effort"]}

    def _receipt(self, outcome: str, reasons: list[str], **extra: Any) -> dict:
        event = {
            "type": "decision", "status": "profile_transition", "task_id": "lane-profile-switching",
            "payload": {
                "schema": "wd.lane-profile-transition-receipt.v1", "lane": self.lane,
                "transition_id": self.tid, "outcome": outcome, "reasons": reasons,
                "from_profile": self.request.get("current_profile"),
                "to_profile": self.request.get("target_profile"),
                "reason": self.request.get("reason"), "catalog_sha256": self.digest,
                "mode": effective_mode(self.catalog), "executor": self.executor, "steps": self.steps,
                **extra,
            },
        }
        self.ports.emit(event)
        if self.claim_id is not None:
            self.ports.release_claim(self.claim_id)
            self.claim_id = None
        return event

    def _scope(self) -> list[str]:
        root = str(record_path(self.runtime_root, self.lane))
        return [root, root + ".transition.lock", f"{self.runtime_root}/readiness/{self.lane}.json"]

    def _verify_current(self, state: dict) -> str | None:
        """The current profile, verified from measured evidence (rco-2 residual B).

        The request's current_profile (and any record's previous_profile) is
        only a claim. It counts once the live process the port measured - pid,
        creation time and native thread, all from execution evidence - shows
        that model and effort in an observation taken after that process
        started. A lane first launched natively has no record, so the binding
        is built from the measurement itself, never from a record.
        """
        current = self.request.get("current_profile")
        started = state.get("process_started_at")
        measured = {
            "lane": self.lane, "desired_profile": current, "created_at": started,
            "launched": {"native_thread_id": state.get("native_thread_id"), "pid": state.get("pid"),
                         "process_started_at": started, "session_id": state.get("current_session_id"),
                         "run_id": state.get("current_session_id"), "launched_at": started},
        }
        if (current not in self.catalog["lanes"][self.lane]["allowed_profiles"]
                or type(state.get("pid")) is not int or _utc(started) is None):
            return None
        obs = self.ports.observations(self.lane)
        result = bind_lane(measured, self.catalog, live_processes={state["pid"]: started},
                           claude_observations=obs.get("claude"), codex_native=obs.get("codex"))
        if result["session_identity"] == "valid" and result["profile_observed"] == "match":
            return current
        return None

    # ---------------------------------------------------------------- run

    def run(self) -> dict:
        now = self.ports.now()
        check = check_request(self.catalog, self.request, self.request.get("history") or [], now=now)
        if check["verdict"] != PROCEED:
            return self._receipt(check["verdict"], check["reasons"])
        self.steps.append("request_checked")
        mode = effective_mode(self.catalog)
        if mode == "shadow":
            return self._receipt("would_relaunch", ["shadow_mode"])
        if mode == "approve":
            return self._receipt("parked", ["operator_ack_unverifiable"])
        if not is_signed(self.catalog):
            return self._receipt("parked", ["catalog_unsigned"])
        if self.lane == LEAD and self.executor != "supervisor":
            return self._receipt("aborted", ["self_transition_requires_supervisor"])

        state = self.ports.measure(self.lane)
        boundary = check_safe_boundary(state, now=now)
        if boundary["verdict"] != PROCEED:
            return self._receipt("aborted", boundary["reasons"])
        if self._verify_current(state) is None:
            return self._receipt("aborted", ["current_profile_unverified"])
        self.steps.append("boundary_and_current_verified")

        try:
            self.claim_id = self.ports.take_claim(self.lane, self._scope(), self.lease_seconds)
        except ClaimConflict as exc:
            return self._receipt("aborted", ["claim_conflict", str(exc)])
        again = self.ports.measure(self.lane)
        if (check_safe_boundary(again, now=self.ports.now())["verdict"] != PROCEED
                or again.get("current_session_id") != state.get("current_session_id")
                or again.get("pid") != state.get("pid")
                or again.get("process_started_at") != state.get("process_started_at")):
            return self._receipt("aborted", ["lane_changed_after_claim"])
        self.steps.append("claimed_and_remeasured")

        target = self._profile(self.request["target_profile"])
        previous = self._profile(self.request["current_profile"])
        issues = self.ports.preflight_launch(self.lane, target)
        if issues:
            return self._receipt("aborted", ["target_launch_preconditions_failed", *issues])
        if not state.get("resume_supported"):
            try:
                checkpoint = self.ports.checkpoint(self.lane)
            except Exception as exc:  # noqa: BLE001 - any failure means no continuity
                return self._receipt("aborted", ["no_continuity", exc.__class__.__name__])
            if not isinstance(checkpoint, str) or not checkpoint.strip():
                return self._receipt("aborted", ["no_continuity"])
        else:
            checkpoint = "provider_resume"
        self.steps.append("continuity_and_target_preflight")

        try:
            self.tid = self.store.plan(self._request_key(), self._plan(state, target, previous))
        except InputError as exc:
            return self._receipt("parked", ["journal_refused", str(exc)])
        self.store.move(self.tid, "planned", "quiesced")
        created = self.ports.now()
        self.ports.write_record(self.lane, self._record(created, target, previous))
        self.store.move(self.tid, "quiesced", "checkpointed", checkpoint=checkpoint)
        self.steps.append("journaled_and_recorded")

        if not self.ports.stop(self.lane, state["pid"], state["process_started_at"]):
            self.store.move(self.tid, "checkpointed", "cancelled_before_apply", reason="source_stop_failed")
            return self._receipt("failed", ["source_stop_failed"])
        self.store.move(self.tid, "checkpointed", "apply_pending")
        self.steps.append("source_stopped")
        self.ports.launch(self.lane, target)
        bound = self._await_target(created, target)
        if bound is not None:
            self.store.move(self.tid, "apply_pending", "verified", reason=json.dumps(bound, sort_keys=True))
            self.store.move(self.tid, "verified", "resume_pending")
            self.store.move(self.tid, "resume_pending", "resumed")
            return self._receipt("applied", ["target_verified"], target_epoch=bound,
                                 source_epoch=self._source_epoch(state))
        return self._rollback(state, target, previous)

    def _rollback(self, state: dict, target: dict, previous: dict) -> dict:
        self.steps.append("rollback")
        stray = self._launched_facts()
        if stray is not None and not self.ports.stop(self.lane, stray["pid"], stray["process_started_at"]):
            self.store.move(self.tid, "apply_pending", "apply_pending", reason="rollback_failed:target_not_stopped")
            return self._receipt("failed", ["verify_timeout", "rollback_failed", "operator_required"])
        created = self.ports.now()
        # A rollback restores the verified previous profile: previous -> previous
        # is "same", never a (reviewer) lowering that the record rules would park.
        self.ports.write_record(self.lane, self._record(created, previous, previous))
        self.ports.launch(self.lane, previous)
        bound = self._await_target(created, previous)
        if bound is None:
            # Leave the lane stopped and the quota reservation held: only an operator reconciles.
            self.store.move(self.tid, "apply_pending", "apply_pending", reason="rollback_failed:not_verified")
            return self._receipt("failed", ["verify_timeout", "rollback_failed", "operator_required"])
        self.store.move(self.tid, "apply_pending", "resumed", reason="rolled_back_to_previous")
        return self._receipt("rolled_back", ["verify_timeout"], target_epoch=bound,
                             source_epoch=self._source_epoch(state))

    # ------------------------------------------------------ target binding

    def _launched_facts(self) -> dict | None:
        try:
            record = self.ports.read_record(self.lane)
        except (RecordError, KeyError, TypeError):
            return None
        launched = record.get("launched") if isinstance(record, dict) else None
        return launched if isinstance(launched, dict) else None

    def _await_target(self, created: datetime, profile: dict) -> dict | None:
        deadline = created + timedelta(seconds=self.catalog["fleet"]["verify_timeout_seconds"])
        while self.ports.now() <= deadline:
            bound = self._bind_target(profile)
            if bound is not None:
                return bound
            self.ports.sleep(5)
        return None

    def _bind_target(self, profile: dict) -> dict | None:
        """Bind the target epoch once, from launcher-recorded facts plus evidence ancestry."""
        try:
            record = validate_record(self.ports.read_record(self.lane), self.catalog, self.digest,
                                     now=self.ports.now())
        except (RecordError, KeyError, TypeError):
            return None
        launched = record.get("launched")
        if launched is None or record["desired_profile"] != profile["profile_id"]:
            return None
        evidence = self.ports.evidence(self.lane)
        if (evidence.get("pin_status") != "manifest_and_launcher_verified"
                or evidence.get("pid") != launched["pid"]
                or _utc(evidence.get("process_started_at")) != _utc(launched["process_started_at"])
                or evidence.get("native_conversation_id") != launched["native_thread_id"]):
            return None
        obs = self.ports.observations(self.lane)
        live = {launched["pid"]: evidence.get("process_started_at")}
        result = bind_lane(record, self.catalog, live_processes=live,
                           claude_observations=obs.get("claude"), codex_native=obs.get("codex"))
        if result["session_identity"] != "valid" or result["profile_observed"] != "match":
            return None
        return {"pid": launched["pid"], "process_started_at": launched["process_started_at"],
                "native_thread_id": launched["native_thread_id"], "session_id": launched["session_id"],
                "launched_at": launched["launched_at"], "profile": profile["profile_id"]}

    # ------------------------------------------------------------- journal

    def _request_key(self) -> str:
        return f"{self.lane}:{self.request['request_id']}"

    def _source_epoch(self, state: dict) -> dict:
        return {"pid": state["pid"], "process_started_at": state["process_started_at"],
                "session_id": state["current_session_id"], "native_thread_id": state["native_thread_id"]}

    def _plan(self, state: dict, target: dict, previous: dict) -> dict:
        policy = self.catalog["capacity_policy"]
        profile = policy["profiles"][target["profile_id"]]
        return {
            "binding": {
                "agent_id": self.lane, "session_id": state["current_session_id"],
                "native_thread_id": state["native_thread_id"], "task_id": "lane-profile-switching",
                "request_id": self.request["request_id"], "head": self.digest,
                "claim_id": self.claim_id, "scope_digest": _digest(self._scope()),
                "authority_ref": self.catalog["operator_signature"], "policy_digest": self.digest,
                "permission_digest": _digest([effective_mode(self.catalog), self.executor]),
                "native_pid": state["pid"], "native_process_started_at": state["process_started_at"],
            },
            "from_profile": previous["profile_id"], "to_profile": target["profile_id"],
            "qualified": profile["approved"] is True,
            "qualification_ref": profile["qualification_ref"],
            "owning_adapter_verified": True,
            "trusted_adapter_identity": {"principal": self.executor,
                                         "verification_ref": self.request["request_id"]},
            "hold": False, "cancelled": False, "billing": profile["billing"],
            "required_reviewers": [r for r in REVIEWERS if r != self.lane],
            "profiles": {p["profile_id"]: {"model": p["model"], "effort": p["effort"]}
                         for p in (previous, target)},
            "pools": [[profile["provider"], profile["account_pool"], limit["id"], window]
                      for limit in profile["limits"] for window in limit["windows"]],
        }

    def _record(self, created: datetime, desired: dict, previous: dict) -> dict:
        requester = self.request["requested_by"]
        return {
            "schema": "wd.lane-profile-record.v1", "lane": self.lane,
            "desired_profile": desired["profile_id"], "previous_profile": previous["profile_id"],
            "reason": self.request.get("reason") or "profile transition",
            "requested_by": requester, "request_id": self.request["request_id"],
            "transition_id": str(self.tid), "created_at": _iso(created),
            "expires_at": _iso(created + timedelta(hours=24)), "catalog_sha256": self.digest,
            "launched": None,
        }
