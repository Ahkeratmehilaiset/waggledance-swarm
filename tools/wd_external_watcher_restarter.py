"""Unwired LLM-free external Tools watcher recovery decision kernel.

No process collector, process launch, task registration, bridge writer or CLI
is installed here. A reviewed adapter must verify its source/config and the
session's authoritative producer record, serialize cycles, persist reserve()
with create-new+flush semantics before start(), and deliver alarm() visibly.
Boolean fields describe measured evidence, not self-granted capabilities.
Neither a verified process nor a fresh sentinel proves next-turn readiness.
"""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import PureWindowsPath


def _time(value):
    if not isinstance(value, str):
        raise ValueError("invalid_timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("naive_timestamp")
    return result.astimezone(timezone.utc)


def _identity(value):
    if not isinstance(value, dict) or type(value.get("pid")) is not int or value["pid"] <= 0:
        raise ValueError("invalid_process_identity")
    argv = value.get("argv")
    if not isinstance(argv, list) or not argv or any(
        not isinstance(a, str) or not a or "\x00" in a for a in argv
    ):
        raise ValueError("opaque_process_command")
    return value["pid"], _time(value.get("created_utc")), tuple(argv)


def _result(status, reason, restart=False, **facts):
    # A truthy "unknown" string is unsafe as a readiness bit. Process-only
    # outcomes never certify a model turn; keep the bit strictly false always.
    return {"status": status, "reason": reason, "restart": restart,
            "next_turn_ready": False, "readiness_state": "unknown",
            "authority": "none", **facts}


def _alarm(reason):
    return _result("alarm", reason)


def _watcher_argv(argv):
    # Strict reviewed standalone form: no shell snippets, embedded commands,
    # arbitrary switches, alternate agent, source suffix or baseline skipping.
    if (len(argv) != 9 or argv[1:4] != ["-NoProfile", "-NonInteractive", "-File"]
            or argv[5:8] != ["-Agent", "codex-tools-1", "-RuntimeRoot"]):
        raise ValueError("invalid_watcher_argv")
    for i in (0, 4, 8):
        path = PureWindowsPath(argv[i])
        if not path.is_absolute() or path.drive.lower() != "c:":
            raise ValueError("watcher_path_not_persistent")
    if PureWindowsPath(argv[0]).name.lower() not in ("powershell.exe", "pwsh.exe"):
        raise ValueError("unexpected_watcher_engine")
    if PureWindowsPath(argv[4]).name != "Watch-Bridge.ps1":
        raise ValueError("unexpected_watcher_source")


def _tools_watcher_candidate(argv):
    if not any(PureWindowsPath(a).name.lower() == "watch-bridge.ps1" for a in argv):
        return False
    agents = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a.lower() == "-agent"]
    # Ambiguous/missing agent is not evidence of absence. Unrelated exact lanes
    # are not adopted, stopped or treated as duplicate Tools watchers.
    return len(agents) != 1 or agents[0].lower() == "codex-tools-1"


def evaluate(config, snapshot, now):
    """Validate complete, fresh, same-session input; return decision, no effect."""
    try:
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ValueError("invalid_clock")
        if (not isinstance(config, dict) or config.get("schema") != "wd.external-watcher-restarter.v1"
                or config.get("agent") != "codex-tools-1" or config.get("producer_kind") != "external"
                or not isinstance(config.get("session_id"), str) or not config["session_id"]):
            raise ValueError("invalid_or_native_configuration")
        owner = _identity(config.get("owner"))
        watcher = _identity(config.get("watcher"))
        _watcher_argv(list(watcher[2]))
        if owner[0] == watcher[0] or max(owner[1], watcher[1]) > now:
            raise ValueError("invalid_expected_processes")
        if not isinstance(snapshot, dict) or snapshot.get("complete") is not True:
            raise ValueError("incomplete_snapshot")
        if snapshot.get("producer_record_verified") is not True:
            raise ValueError("unverified_producer_record")
        if snapshot.get("producer") != {"session_id": config["session_id"], "kind": "external"}:
            raise ValueError("producer_generation_changed")
        age = (now - _time(snapshot.get("observed_at_utc"))).total_seconds()
        if not 0 <= age <= 10:
            raise ValueError("snapshot_outside_freshness_window")
        rows = snapshot.get("processes")
        if not isinstance(rows, list):
            raise ValueError("invalid_process_snapshot")
        identities = [_identity(row) for row in rows]
        if any(identity[1] > now for identity in identities):
            raise ValueError("future_process_creation")
        if len({p[0] for p in identities}) != len(identities):
            raise ValueError("duplicate_process_pid")
        if owner not in identities:
            raise ValueError("owner_identity_not_live")
        candidates = [p for p in identities if _tools_watcher_candidate(p[2])]
        if any(p[0] == watcher[0] and p != watcher for p in identities):
            raise ValueError("watcher_pid_reused")
        if candidates and candidates != [watcher]:
            raise ValueError("unaccounted_or_duplicate_watcher")
        sentinel = snapshot.get("sentinel_at_utc")
        if sentinel is not None:
            sentinel_age = (now - _time(sentinel)).total_seconds()
            if sentinel_age < 0:
                raise ValueError("future_sentinel")
        else:
            sentinel_age = None
        if watcher not in identities:
            return _result("restart_required", "verified_external_watcher_absent", restart=True)
        if sentinel_age is not None and sentinel_age > 300:
            return _alarm("stale_unconsumed_sentinel")
        return _result("healthy", "exact_process_live")
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return _alarm(str(exc))


def run_cycle(config, now, services):
    """One serialized, at-most-once attempt using a reviewed durable adapter.

reserve must never erase a previous intent, including a successful one; explicit
reviewed reconciliation creates the next generation/config. No uncertain start
is retried. Two absence observations narrow but cannot eliminate the race with
an uncoordinated producer: the eventual launcher and adapter MUST share a lock.
"""
    def alert(result):
        try:
            services.alarm(result)
        except Exception:
            # Keep the failure visible to the caller; never label an alarm sent.
            return {**result, "alarm_delivery": "failed"}
        return {**result, "alarm_delivery": "adapter_returned_not_receipt_verified"}

    clock = getattr(services, "clock", lambda: now)
    try:
        first = services.observe()
        decision = evaluate(config, first, clock())
        if decision["status"] != "restart_required":
            return alert(decision) if decision["status"] == "alarm" else decision
        config_hash = sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        intent = {"schema": "wd.watcher-restart-intent.v1", "session_id": config["session_id"],
                  "config_sha256": config_hash, "created_at_utc": clock().isoformat(),
                  "watcher_argv": list(config["watcher"]["argv"])}
        if services.reserve(intent) is not True:
            return alert(_alarm("prior_or_uncertain_restart_intent"))
        second = services.observe()
        if evaluate(config, second, clock())["status"] != "restart_required":
            return alert(_alarm("evidence_changed_after_intent"))
        receipt = services.start(list(config["watcher"]["argv"]))
        identity = _identity(receipt)
        if (identity[0] in {config["owner"]["pid"], config["watcher"]["pid"]}
                or identity[2] != tuple(config["watcher"]["argv"])
                or not _time(first["observed_at_utc"]) <= identity[1] <= clock()):
            return alert(_alarm("unexpected_started_identity"))
        expected = deepcopy(config)
        expected["watcher"] = receipt
        after = evaluate(expected, services.observe(), clock())
        if after["status"] != "healthy":
            return alert(_alarm("started_process_not_verified"))
        return _result("restarted_verified", "exact_new_process_only", watcher=receipt)
    except Exception as exc:
        # Adapter failures never authorize another attempt or hide the intent.
        return alert(_alarm("adapter_failure:" + type(exc).__name__))
