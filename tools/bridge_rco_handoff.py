#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Durable, provider-free RCO *task custody* ledger (advisory only).

This implements handoff/handback, NOT a provider adapter or a merge gate. It
never starts/stops a model, reads credentials, sends bridge events, changes a
claim, or grants RCO approval. The owning controller must authenticate policy,
capacity, identity and quiescence evidence before calling it. JSON assertions
are not signatures. Never feed untrusted peer payloads directly to this API.

SQLite serializes commands; revisions reject racing writers and epochs fence
old-owner results. Exactly repeated command IDs are read-only replays returning
CURRENT state, not stale permission. Physical process exclusion still requires
the host's owning-session adapter to enforce the fence and verify quiescence.

CLI: --db <local.sqlite> --policy <operator-policy.json> --stdin
     --db <local.sqlite> --policy <operator-policy.json> --show <review-id>
See docs/BRIDGE_RCO_HANDOFF.md. No runtime wiring or new dependencies.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

MAX_BYTES = 2 * 1024 * 1024
ACTOR_KEYS = {"agent", "agent_uuid", "session_id", "native_thread_id", "profile_id",
              "provider", "model", "effort", "account_pool"}
PROFILE_KEYS = {"agent", "agent_uuid", "slot", "provider", "model", "effort",
                "account_pool", "qualification_ref"}
TASK_KEYS = {"task_id", "head", "request_id", "claim_id", "scope_digest",
             "required_reviewers", "author_uuid", "author_thread_id", "author_provider",
             "base", "pr_ref", "request_digest"}
CHECK_LISTS = {"completed_checks", "remaining_checks", "findings", "veto_refs", "evidence_refs"}
COMMON = {"command_id", "review_id", "op", "expected_revision", "binding", "actor", "epoch"}
FIELDS = {
    "create": {"command_id", "review_id", "op", "expected_revision", "task", "slot", "actor"},
    "begin": COMMON | {"target", "reason", "next_request_id", "source_capacity", "target_capacity"},
    "release": COMMON | {"checkpoint", "quiescence"},
    "host_release": COMMON | {"checkpoint_sha256", "quiescence", "source_capacity",
                              "lease_expired_at", "control_ref"},
    "accept": COMMON | {"assignment_request_id", "checkpoint_sha256", "target_capacity"},
    "progress": COMMON | {"checkpoint"},
    "complete": COMMON | {"checkpoint"},
    "hold": {"command_id", "review_id", "op", "expected_revision", "binding", "control_ref"},
    "cancel": {"command_id", "review_id", "op", "expected_revision", "binding", "control_ref"},
}


class HandoffError(ValueError):
    """Explicit refusal, never permission to fall back or retry a side effect."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise HandoffError(code)


def text(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= 4096


def strings(value: Any, *, empty: bool = True) -> bool:
    return (isinstance(value, list) and len(value) <= 256 and (empty or bool(value))
            and all(text(x) for x in value) and len(value) == len(set(value)))


def encode(value: Any) -> str:
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise HandoffError("invalid_json_value") from exc
    require(len(result.encode("utf-8")) <= MAX_BYTES, "document_too_large")
    return result


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def timestamp(value: Any) -> datetime:
    require(isinstance(value, str), "invalid_timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(result.utcoffset() is not None, "timezone_required")
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise HandoffError("invalid_timestamp") from exc


def same_worker(a: dict, b: dict) -> bool:
    return any(a[k] == b[k] for k in ("agent", "agent_uuid", "native_thread_id"))


def validate_policy(policy: Any) -> dict:
    require(isinstance(policy, dict), "policy_object_required")
    required = {"schema", "mode", "authority_ref", "max_age_seconds",
                "recovery_stability_seconds", "cooldown_seconds", "profiles", "slots"}
    require(set(policy) == required, "policy_fields_invalid")
    require(policy["schema"] == "wd.rco-handoff-policy.v1"
            and policy["mode"] == "advisory_only", "advisory_only_policy_required")
    require(text(policy["authority_ref"]), "policy_reference_required")
    for key in ("max_age_seconds", "recovery_stability_seconds", "cooldown_seconds"):
        require(type(policy[key]) is int and 1 <= policy[key] <= 86400, "invalid_policy_duration")
    profiles, slots = policy["profiles"], policy["slots"]
    require(isinstance(profiles, dict) and bool(profiles), "profiles_required")
    require(isinstance(slots, dict) and bool(slots) and set(slots) <= {"rco1", "rco2"},
            "rco_slots_required")
    agents, uuids, used = set(), set(), set()
    for key, profile in profiles.items():
        require(text(key) and isinstance(profile, dict) and set(profile) == PROFILE_KEYS,
                "profile_fields_invalid")
        require(all(text(v) for v in profile.values()), "profile_values_invalid")
        require(profile["provider"] in {"claude", "codex"}, "unsupported_provider")
        require(profile["agent"] not in agents and profile["agent_uuid"] not in uuids,
                "profiles_require_distinct_reviewers")
        agents.add(profile["agent"])
        uuids.add(profile["agent_uuid"])
    for slot, config in slots.items():
        require(isinstance(config, dict) and set(config) == {"primary", "substitutes"},
                "slot_fields_invalid")
        require(text(config["primary"]) and strings(config["substitutes"], empty=False)
                and len(config["substitutes"]) == 1,
                "slot_profiles_required")
        for key in [config["primary"], *config["substitutes"]]:
            require(key in profiles and profiles[key]["slot"] == slot and key not in used,
                    "profile_slot_binding_invalid")
            used.add(key)
    require(used == set(profiles), "unassigned_profile")
    return deepcopy(policy)


class HandoffStore:
    """Local single-database custody transactions; not a distributed lock service."""

    def __init__(self, path: str | Path, policy: dict):
        self.policy = validate_policy(policy)
        self.policy_digest = digest(self.policy)
        # Caller owns/protects the local DB path; do not share this over a network FS.
        self.db = sqlite3.connect(str(path), timeout=2, isolation_level=None)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS reviews (review_id TEXT PRIMARY KEY, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS commands (
                command_id TEXT PRIMARY KEY, command_digest TEXT NOT NULL,
                review_id TEXT NOT NULL, applied_revision INTEGER NOT NULL,
                observed_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS transitions (
                command_id TEXT PRIMARY KEY, command_json TEXT NOT NULL,
                state_json TEXT NOT NULL);
        """)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def get(self, review_id: str) -> dict:
        # One read snapshot avoids comparing old state with a concurrently
        # committed journal. This detects corruption, not an authenticated DB.
        row = self.db.execute("""
            SELECT r.state, t.state_json, t.command_json, c.command_digest, c.applied_revision
            FROM reviews r
            LEFT JOIN commands c ON c.review_id=r.review_id AND c.applied_revision=(
                SELECT MAX(applied_revision) FROM commands WHERE review_id=r.review_id)
            LEFT JOIN transitions t ON t.command_id=c.command_id
            WHERE r.review_id=?
        """, (review_id,)).fetchone()
        require(row is not None, "review_not_found")
        try:
            state = load_json(row[0])
            require(isinstance(state, dict) and row[0] == row[1], "corrupt_state")
            require(state["schema"] == "wd.rco-handoff-state.v1"
                    and state["review_id"] == review_id and state["revision"] == row[4]
                    and digest(load_json(row[2])) == row[3], "corrupt_state")
            require(text(state["policy_digest"]), "corrupt_state")
        except (HandoffError, KeyError, TypeError, UnicodeError) as exc:
            raise HandoffError("corrupt_state") from exc
        require(state["policy_digest"] == self.policy_digest, "policy_changed_reconcile_required")
        return state

    def _actor(self, actor: Any, slot: str, task: dict) -> None:
        require(isinstance(actor, dict) and set(actor) == ACTOR_KEYS
                and all(text(v) for v in actor.values()), "actor_fields_invalid")
        profile = self.policy["profiles"].get(actor["profile_id"])
        require(profile is not None and profile["slot"] == slot, "actor_profile_not_qualified")
        require(all(actor[k] == profile[k] for k in PROFILE_KEYS & ACTOR_KEYS),
                "observed_actor_profile_mismatch")
        require(actor["agent_uuid"] != task["author_uuid"]
                and actor["native_thread_id"] != task["author_thread_id"], "author_cannot_review")

    def _unique_worker(self, state: dict, actor: dict) -> None:
        for (review_id,) in self.db.execute("SELECT review_id FROM reviews"):
            other = self.get(review_id)
            if (other["review_id"] == state["review_id"]
                    or other["task"]["task_id"] != state["task"]["task_id"]):
                continue
            require(other["task"] == state["task"], "review_round_binding_conflict")
            require(other["slot"] != state["slot"], "review_slot_already_owned")
            workers = list(other["participants"])
            if other["pending"]:
                workers.append(other["pending"]["target"])
            require(not any(w and same_worker(w, actor) for w in workers),
                    "reviewer_independence_conflict")

    def _fresh(self, value: Any, now: datetime) -> datetime:
        when = timestamp(value)
        require(0 <= (now - when).total_seconds() <= self.policy["max_age_seconds"],
                "stale_or_future_observation")
        return when

    def _capacity(self, capacity: Any, actor: dict, expected: str, now: datetime) -> datetime:
        require(isinstance(capacity, dict), "capacity_required")
        require(capacity.get("provider") == actor["provider"]
                and capacity.get("account_pool") == actor["account_pool"], "capacity_pool_mismatch")
        require(capacity.get("state") == expected and text(capacity.get("source_ref")),
                "capacity_not_confirmed")
        return self._fresh(capacity.get("observed_at"), now)

    def _checkpoint(self, state: dict, checkpoint: Any) -> None:
        require(isinstance(checkpoint, dict) and set(checkpoint) ==
                CHECK_LISTS | {"binding", "source_actor", "source_epoch"}, "checkpoint_fields_invalid")
        require(checkpoint["binding"] == state["task"], "checkpoint_binding_mismatch")
        require(checkpoint["source_actor"] == state["owner"]
                and type(checkpoint["source_epoch"]) is int
                and checkpoint["source_epoch"] == state["epoch"], "checkpoint_owner_mismatch")
        require(all(strings(checkpoint[k]) for k in CHECK_LISTS), "checkpoint_lists_invalid")
        require(bool(checkpoint["evidence_refs"]), "checkpoint_evidence_required")
        old = state["checkpoint"]
        if old:
            for key in CHECK_LISTS - {"remaining_checks"}:
                require(set(old[key]) <= set(checkpoint[key]), "checkpoint_dropped_evidence:" + key)
        state["checkpoint"] = deepcopy(checkpoint)
        state["checkpoint_sha256"] = digest(checkpoint)

    @staticmethod
    def _owner(state: dict, cmd: dict) -> None:
        require(state["owner"] is not None and cmd["actor"] == state["owner"], "not_current_owner")
        require(type(cmd["epoch"]) is int and cmd["epoch"] == state["epoch"], "stale_owner_epoch")

    def _create(self, cmd: dict, now: datetime) -> dict:
        require(cmd["expected_revision"] == -1, "create_revision_must_be_minus_one")
        require(self.db.execute("SELECT 1 FROM reviews WHERE review_id=?", (cmd["review_id"],)).fetchone()
                is None, "review_already_exists")
        task, slot = cmd["task"], cmd["slot"]
        require(isinstance(task, dict) and set(task) == TASK_KEYS, "task_fields_invalid")
        require(all(text(task[k]) for k in TASK_KEYS - {"required_reviewers"}), "task_values_invalid")
        require(re.fullmatch(r"[0-9a-f]{40}", task["head"]) is not None
                and re.fullmatch(r"[0-9a-f]{40}", task["base"]) is not None
                and re.fullmatch(r"[0-9a-f]{64}", task["scope_digest"]) is not None
                and re.fullmatch(r"[0-9a-f]{64}", task["request_digest"]) is not None,
                "exact_head_and_scope_required")
        require(task["author_provider"] in {"claude", "codex", "human"}, "author_provider_required")
        require(strings(task["required_reviewers"], empty=False)
                and set(task["required_reviewers"]) <= set(self.policy["slots"])
                and slot in task["required_reviewers"], "required_reviewers_invalid")
        self._actor(cmd["actor"], slot, task)
        require(cmd["actor"]["profile_id"] == self.policy["slots"][slot]["primary"],
                "initial_owner_must_be_primary")
        state = {"schema": "wd.rco-handoff-state.v1", "review_id": cmd["review_id"],
                 "task": deepcopy(task), "slot": slot, "policy_digest": self.policy_digest,
                 "phase": "active", "revision": 0, "epoch": 0, "owner": deepcopy(cmd["actor"]),
                 "participants": [deepcopy(cmd["actor"])],
                 "pending": None, "checkpoint": None, "checkpoint_sha256": None,
                 "assignment_request_id": task["request_id"], "assignment_ids": [task["request_id"]],
                 "last_transfer_at": None, "updated_at": now.isoformat()}
        self._unique_worker(state, cmd["actor"])
        return state

    def _begin(self, state: dict, cmd: dict, now: datetime) -> None:
        require(state["phase"] == "active", "active_review_required")
        self._owner(state, cmd)
        target, owner = cmd["target"], state["owner"]
        self._actor(target, state["slot"], state["task"])
        self._unique_worker(state, target)
        require(not same_worker(owner, target), "separate_substitute_required")
        require(text(cmd["next_request_id"]) and cmd["next_request_id"] not in state["assignment_ids"],
                "fresh_assignment_request_required")
        config = self.policy["slots"][state["slot"]]
        observed = self._capacity(cmd["target_capacity"], target, "available", now)
        if cmd["reason"] == "quota":
            require(owner["profile_id"] == config["primary"]
                    and target["profile_id"] in config["substitutes"], "invalid_substitution_route")
            self._capacity(cmd["source_capacity"], owner, "exhausted", now)
            require((owner["provider"], owner["account_pool"]) !=
                    (target["provider"], target["account_pool"]), "same_exhausted_pool")
        elif cmd["reason"] == "recovered":
            require(owner["profile_id"] in config["substitutes"]
                    and target["profile_id"] == config["primary"], "invalid_handback_route")
            self._stable_recovery(cmd["target_capacity"], observed, now)
        else:
            raise HandoffError("only_quota_or_verified_recovery")
        if state["last_transfer_at"]:
            require((now - timestamp(state["last_transfer_at"])).total_seconds() >=
                    self.policy["cooldown_seconds"], "transfer_cooldown")
        state["phase"] = "releasing"
        state["pending"] = {"target": deepcopy(target), "request_id": cmd["next_request_id"],
                            "reason": cmd["reason"], "started_at": now.isoformat(),
                            "released_at": None}
        state["assignment_ids"].append(cmd["next_request_id"])

    def _stable_recovery(self, capacity: dict, observed: datetime, now: datetime) -> None:
        since = timestamp(capacity.get("available_since"))
        require(since <= observed and
                (now - since).total_seconds() >= self.policy["recovery_stability_seconds"],
                "recovery_not_stable")

    def _release(self, state: dict, cmd: dict, now: datetime) -> None:
        require(state["phase"] == "releasing", "release_not_requested")
        self._owner(state, cmd)
        proof = cmd["quiescence"]
        require(isinstance(proof, dict) and proof.get("actor") == state["owner"]
                and type(proof.get("epoch")) is int and proof["epoch"] == state["epoch"],
                "quiescence_binding_mismatch")
        observed = self._fresh(proof.get("observed_at"), now)
        require(observed >= timestamp(state["pending"]["started_at"]), "quiescence_predates_handoff")
        require(proof.get("idle") is True and proof.get("pending_effects") is False
                and text(proof.get("evidence_ref")), "quiescence_not_verified")
        if cmd["op"] == "host_release":
            # Host proof is NOT a signature: the caller must authenticate it and
            # actually enforce fencing. Neither silence nor lease expiry suffices.
            require(state["pending"]["reason"] == "quota", "host_release_quota_only")
            self._capacity(cmd["source_capacity"], state["owner"], "exhausted", now)
            require(text(cmd["control_ref"]) and proof.get("execution_fenced") is True,
                    "host_fencing_required")
            require(timestamp(cmd["lease_expired_at"]) <= observed, "source_lease_not_expired")
            require(state["checkpoint"] is not None and
                    cmd["checkpoint_sha256"] == state["checkpoint_sha256"],
                    "durable_checkpoint_required")
            self._checkpoint(state, state["checkpoint"])
        else:
            self._checkpoint(state, cmd["checkpoint"])
        state["pending"]["release_evidence"] = deepcopy(proof)
        state["pending"]["released_at"] = now.isoformat()
        state["owner"] = None
        state["epoch"] += 1
        state["phase"] = "awaiting_accept"

    def _accept(self, state: dict, cmd: dict, now: datetime) -> None:
        require(state["phase"] == "awaiting_accept" and state["owner"] is None,
                "source_release_required")
        require(type(cmd["epoch"]) is int and cmd["epoch"] == state["epoch"], "stale_owner_epoch")
        pending = state["pending"]
        require(cmd["actor"] == pending["target"], "target_identity_mismatch")
        self._actor(cmd["actor"], state["slot"], state["task"])
        self._unique_worker(state, cmd["actor"])
        require(cmd["assignment_request_id"] == pending["request_id"], "assignment_binding_mismatch")
        require(cmd["checkpoint_sha256"] == state["checkpoint_sha256"], "checkpoint_hash_mismatch")
        observed = self._capacity(cmd["target_capacity"], cmd["actor"], "available", now)
        require(observed >= timestamp(pending["released_at"]), "capacity_predates_release")
        if pending["reason"] == "recovered":
            self._stable_recovery(cmd["target_capacity"], observed, now)
        state["owner"] = deepcopy(cmd["actor"])
        if cmd["actor"] not in state["participants"]:
            state["participants"].append(deepcopy(cmd["actor"]))
        state["assignment_request_id"] = pending["request_id"]
        state["last_transfer_at"] = now.isoformat()
        state["last_handoff"] = deepcopy(pending)
        state["pending"] = None
        state["phase"] = "active"

    def execute(self, command: dict, *, now: datetime | None = None) -> dict:
        """Apply one CAS command atomically. Refusals roll back every state change."""
        require(isinstance(command, dict), "command_object_required")
        cmd = deepcopy(command)
        require(isinstance(cmd.get("op"), str) and cmd["op"] in FIELDS, "unknown_operation")
        require(set(cmd) == FIELDS[cmd["op"]], "command_fields_invalid")
        require(text(cmd["command_id"]) and text(cmd["review_id"]), "command_ids_required")
        require(type(cmd["expected_revision"]) is int, "integer_revision_required")
        command_digest = digest(cmd)
        now = datetime.now(timezone.utc) if now is None else now
        require(isinstance(now, datetime) and now.utcoffset() is not None, "aware_clock_required")
        now = now.astimezone(timezone.utc)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            replay = self.db.execute(
                "SELECT command_digest, review_id, applied_revision FROM commands WHERE command_id=?",
                (cmd["command_id"],)).fetchone()
            if replay:
                require(replay[0] == command_digest, "command_id_content_conflict")
                state = self.get(replay[1])
                result = self._result(state, replay[2], replayed=True)
                self.db.execute("COMMIT")
                return result
            if cmd["op"] == "create":
                state = self._create(cmd, now)
            else:
                state = self.get(cmd["review_id"])
                require(state["revision"] == cmd["expected_revision"], "revision_conflict")
                require(state["task"] == cmd["binding"], "task_binding_changed")
                require(now >= timestamp(state["updated_at"]), "clock_moved_backwards")
                require(state["phase"] not in {"completed", "cancelled"}, "review_terminal")
                op = cmd["op"]
                if op in {"hold", "cancel"}:
                    require(text(cmd["control_ref"]), "control_reference_required")
                    if "suspended_owner" not in state:
                        state["suspended_owner"] = deepcopy(state["owner"])
                    state["owner"] = None
                    state["epoch"] += 1
                    state["phase"] = "held" if op == "hold" else "cancelled"
                    state["control_ref"] = cmd["control_ref"]
                elif op == "begin":
                    self._begin(state, cmd, now)
                elif op in {"release", "host_release"}:
                    self._release(state, cmd, now)
                elif op == "accept":
                    self._accept(state, cmd, now)
                else:
                    require(state["phase"] == "active", "active_review_required")
                    self._owner(state, cmd)
                    self._checkpoint(state, cmd["checkpoint"])
                    if op == "complete":
                        require(not state["checkpoint"]["remaining_checks"], "review_checks_remaining")
                        state["completed_by"] = state["owner"]
                        state["owner"] = None
                        state["epoch"] += 1
                        state["phase"] = "completed"
                state["revision"] += 1
                state["updated_at"] = now.isoformat()
            self.db.execute("INSERT OR REPLACE INTO reviews VALUES (?, ?)",
                            (state["review_id"], encode(state)))
            self.db.execute("INSERT INTO commands VALUES (?, ?, ?, ?, ?)",
                            (cmd["command_id"], command_digest, state["review_id"],
                             state["revision"], now.isoformat()))
            self.db.execute("INSERT INTO transitions VALUES (?, ?, ?)",
                            (cmd["command_id"], encode(cmd), encode(state)))
            result = self._result(state, state["revision"], replayed=False)
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    @staticmethod
    def _result(state: dict, revision: int, *, replayed: bool) -> dict:
        owner = state["owner"]
        return {"schema": "wd.rco-handoff-result.v1", "state": state,
                "applied_revision": revision, "replayed": replayed,
                "execution_allowed": False, "rco_approval_allowed": False,
                "release_allowed": False, "authority_effect": "none",
                "same_provider_as_author": (owner["provider"] == state["task"]["author_provider"]
                                            if owner else None)}


def load_json(raw: str) -> Any:
    require(isinstance(raw, str), "json_text_required")
    require(len(raw.encode("utf-8")) <= MAX_BYTES, "document_too_large")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def constant(_):
        raise HandoffError("nonfinite_json_value")

    def finite_float(raw):
        value = float(raw)
        require(math.isfinite(value), "nonfinite_json_value")
        return value

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant,
                          parse_float=finite_float)
    except (ValueError, RecursionError) as exc:
        raise HandoffError("invalid_json:" + str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--policy", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stdin", action="store_true")
    group.add_argument("--show")
    args = parser.parse_args(argv)
    try:
        with Path(args.policy).open(encoding="utf-8") as handle:
            policy = load_json(handle.read(MAX_BYTES + 1))
        command = load_json(sys.stdin.read(MAX_BYTES + 1)) if args.stdin else None
        if args.show:
            require(Path(args.db).is_file(), "database_not_found")
        with HandoffStore(args.db, policy) as store:
            result = (store.execute(command) if args.stdin else
                      store._result(store.get(args.show), -1, replayed=False))
        print(encode(result))
        return 0
    except (HandoffError, OSError, UnicodeError, sqlite3.Error) as exc:
        print(json.dumps({"schema": "wd.rco-handoff-result.v1", "error": str(exc),
                          "execution_allowed": False, "rco_approval_allowed": False,
                          "release_allowed": False, "authority_effect": "none"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
