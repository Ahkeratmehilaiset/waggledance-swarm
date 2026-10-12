"""RED-first restarter kernel tests; all processes and starts are inert doubles."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib
from pathlib import Path

import pytest

NOW = datetime(2026, 10, 12, 20, 30, tzinfo=timezone.utc)
CREATED = "2026-10-12T18:47:10+00:00"
ARGV = [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "-NoProfile", "-NonInteractive", "-File", r"C:\pinned\Watch-Bridge.ps1",
        "-Agent", "codex-tools-1", "-RuntimeRoot", r"C:\bridge"]


@pytest.fixture
def kernel():
    return importlib.import_module("tools.wd_external_watcher_restarter")


@pytest.fixture
def config():
    return {"schema": "wd.external-watcher-restarter.v1", "agent": "codex-tools-1",
            "session_id": "tools-generation-one", "producer_kind": "external",
            "owner": {"pid": 200, "created_utc": CREATED, "argv": ["codex", "resume", "saved-thread"]},
            "watcher": {"pid": 201, "created_utc": CREATED, "argv": deepcopy(ARGV)}}


@pytest.fixture
def snapshot(config):
    return {"complete": True, "observed_at_utc": NOW.isoformat(),
            "producer_record_verified": True,
            "producer": {"session_id": config["session_id"], "kind": "external"},
            "processes": [deepcopy(config["owner"]), deepcopy(config["watcher"])],
            "sentinel_at_utc": None}


def test_missing_watcher_requires_one_restart(kernel, config, snapshot):
    snapshot["processes"] = [deepcopy(config["owner"])]
    assert kernel.evaluate(config, snapshot, NOW)["status"] == "restart_required"


def test_idle_missing_sentinel_is_not_failure(kernel, config, snapshot):
    assert kernel.evaluate(config, snapshot, NOW)["status"] == "healthy"


def test_old_dirty_bit_alarms_but_never_restarts_live_watcher(kernel, config, snapshot):
    snapshot["sentinel_at_utc"] = (NOW - timedelta(minutes=6)).isoformat()
    result = kernel.evaluate(config, snapshot, NOW)
    assert result["status"] == "alarm"
    assert result["reason"] == "stale_unconsumed_sentinel"


@pytest.mark.parametrize("field,value", [("complete", False), ("complete", "true"),
    ("producer_record_verified", False), ("producer_record_verified", "true"),
    ("observed_at_utc", (NOW - timedelta(seconds=11)).isoformat()),
    ("observed_at_utc", (NOW + timedelta(seconds=1)).isoformat())])
def test_uncertain_or_stale_snapshot_fail_closed(kernel, config, snapshot, field, value):
    snapshot[field] = value
    assert kernel.evaluate(config, snapshot, NOW)["status"] == "alarm"


@pytest.mark.parametrize("field,value", [("pid", True), ("created_utc", "2026-10-12T18:47:11Z"),
    ("argv", ["wrong", "command"]), ("argv", "opaque")])
def test_owner_identity_never_adopted(kernel, config, snapshot, field, value):
    snapshot["processes"][0][field] = value
    assert kernel.evaluate(config, snapshot, NOW)["status"] == "alarm"


def test_pid_reuse_or_unaccounted_watchers_alarm(kernel, config, snapshot):
    snapshot["processes"][1]["created_utc"] = "2026-10-12T20:29:00Z"
    assert kernel.evaluate(config, snapshot, NOW)["status"] == "alarm"
    snapshot["processes"][1]["pid"] = 202
    assert kernel.evaluate(config, snapshot, NOW)["status"] == "alarm"


@pytest.mark.parametrize("mutation", ["native", "foreign_session", "duplicate", "opaque", "reused_pid"])
def test_new_or_unknown_producer_blocks_restart(kernel, config, snapshot, mutation):
    snapshot["processes"] = [deepcopy(config["owner"])]
    if mutation == "native":
        snapshot["producer"]["kind"] = "native"
    elif mutation == "foreign_session":
        snapshot["producer"]["session_id"] = "new-session"
    elif mutation == "duplicate":
        snapshot["processes"].append(deepcopy(config["owner"]))
    elif mutation == "opaque":
        snapshot["processes"].append({"pid": 999, "created_utc": CREATED, "argv": None})
    else:
        snapshot["processes"][0]["created_utc"] = "2026-10-12T20:29:00Z"
    assert kernel.evaluate(config, snapshot, NOW)["status"] == "alarm"


class Services:
    def __init__(self, snapshots, receipt):
        self.snapshots = iter(snapshots)
        self.receipt = receipt
        self.calls = []
        self.intent = None

    def observe(self):
        self.calls.append("observe")
        return next(self.snapshots)

    def reserve(self, intent):
        self.calls.append("reserve")
        if self.intent is not None:
            return False
        self.intent = intent
        return True

    def start(self, argv):
        self.calls.append(("start", argv))
        return self.receipt

    def alarm(self, result):
        self.calls.append(("alarm", result["reason"]))


def test_intent_before_effect_recheck_and_exact_new_identity(kernel, config, snapshot):
    missing = deepcopy(snapshot)
    missing["processes"] = [deepcopy(config["owner"])]
    receipt = {"pid": 300, "created_utc": NOW.isoformat(), "argv": ARGV}
    after = deepcopy(snapshot)
    after["processes"][1] = receipt
    s = Services([missing, missing, after], receipt)
    result = kernel.run_cycle(config, NOW, s)
    assert result["status"] == "restarted_verified"
    assert s.calls[:4] == ["observe", "reserve", "observe", ("start", ARGV)]
    assert s.intent["schema"] == "wd.watcher-restart-intent.v1"
    # Intent retained, no retry after a lost receipt or process dying again.
    s.snapshots = iter([missing])
    assert kernel.run_cycle(config, NOW, s)["status"] == "alarm"
    assert sum(isinstance(c, tuple) and c[0] == "start" for c in s.calls) == 1


def test_watcher_arrives_between_intent_and_start_never_double_starts(kernel, config, snapshot):
    missing = deepcopy(snapshot)
    missing["processes"] = [deepcopy(config["owner"])]
    s = Services([missing, snapshot], {})
    assert kernel.run_cycle(config, NOW, s)["status"] == "alarm"
    assert not any(isinstance(c, tuple) and c[0] == "start" for c in s.calls)
    assert s.intent is not None


@pytest.mark.parametrize("receipt", [None, {}, {"pid": True}, {"pid": 200, "created_utc": CREATED, "argv": ARGV}])
def test_uncertain_start_never_healthy_and_retains_intent(kernel, config, snapshot, receipt):
    missing = deepcopy(snapshot)
    missing["processes"] = [deepcopy(config["owner"])]
    s = Services([missing, missing, snapshot], receipt)
    result = kernel.run_cycle(config, NOW, s)
    assert result["status"] == "alarm"
    assert s.intent is not None
    assert any(isinstance(c, tuple) and c[0] == "alarm" for c in s.calls)


@pytest.mark.parametrize("mutant", ["MUTANT_IGNORE_INCOMPLETE_SNAPSHOT", "MUTANT_IGNORE_PID_CREATION",
    "MUTANT_RESTART_STALE_LIVE_WATCHER", "MUTANT_SKIP_POST_INTENT_RECHECK", "MUTANT_RETRY_UNCERTAIN_START"])
def test_named_mutant_twins(kernel, config, snapshot, mutant):
    if mutant == "MUTANT_IGNORE_INCOMPLETE_SNAPSHOT":
        snapshot["complete"] = False
        expected, unsafe = "alarm", "healthy"
        actual = kernel.evaluate(config, snapshot, NOW)["status"]
    elif mutant == "MUTANT_IGNORE_PID_CREATION":
        snapshot["processes"][1]["created_utc"] = "2026-10-12T20:29:00Z"
        expected, unsafe = "alarm", "healthy"
        actual = kernel.evaluate(config, snapshot, NOW)["status"]
    elif mutant == "MUTANT_RESTART_STALE_LIVE_WATCHER":
        snapshot["sentinel_at_utc"] = (NOW - timedelta(minutes=6)).isoformat()
        expected, unsafe = "alarm", "restart_required"
        actual = kernel.evaluate(config, snapshot, NOW)["status"]
    else:
        missing = deepcopy(snapshot)
        missing["processes"] = [deepcopy(config["owner"])]
        s = Services([missing, snapshot], {})
        if mutant == "MUTANT_RETRY_UNCERTAIN_START":
            s.intent = {"retained": True}
        expected, unsafe = "alarm", "restarted_verified"
        actual = kernel.run_cycle(config, NOW, s)["status"]
        assert not any(isinstance(c, tuple) and c[0] == "start" for c in s.calls)
    assert actual == expected
    assert actual != unsafe  # Named faulty outcome, not a claimed executable mutant kill.


@pytest.mark.parametrize("mutant", ["MUTANT_IGNORE_INCOMPLETE_SNAPSHOT", "MUTANT_IGNORE_PID_CREATION",
    "MUTANT_RESTART_STALE_LIVE_WATCHER", "MUTANT_SKIP_POST_INTENT_RECHECK", "MUTANT_RETRY_UNCERTAIN_START"])
def test_executable_in_memory_mutants_are_killed(kernel, config, snapshot, mutant):
    replacements = {
        "MUTANT_IGNORE_INCOMPLETE_SNAPSHOT": (' or snapshot.get("complete") is not True', ''),
        "MUTANT_IGNORE_PID_CREATION": ('_time(value.get("created_utc"))', f'_time("{CREATED}")'),
        "MUTANT_RESTART_STALE_LIVE_WATCHER": ('return _alarm("stale_unconsumed_sentinel")',
            'return {"status": "restart_required", "restart": True, "reason": "mutant"}'),
        "MUTANT_SKIP_POST_INTENT_RECHECK": ('if evaluate(config, second, clock())["status"] != "restart_required":',
            'if False:'),
        "MUTANT_RETRY_UNCERTAIN_START": ('if services.reserve(intent) is not True:',
            'if services.reserve(intent) is None:'),
    }
    old, new = replacements[mutant]
    source = Path(kernel.__file__).read_text(encoding="utf-8")
    assert source.count(old) == 1
    namespace = {}
    exec(compile(source.replace(old, new), f"<in-memory-{mutant}>", "exec"), namespace)
    # All start/process/notice effects below remain inert Services doubles.
    if mutant == "MUTANT_IGNORE_INCOMPLETE_SNAPSHOT":
        snapshot["complete"] = False
        assert kernel.evaluate(config, snapshot, NOW)["status"] == "alarm"
        assert namespace["evaluate"](config, snapshot, NOW)["status"] == "healthy"
    elif mutant == "MUTANT_IGNORE_PID_CREATION":
        snapshot["processes"][1]["created_utc"] = "2026-10-12T20:29:00Z"
        assert kernel.evaluate(config, snapshot, NOW)["status"] == "alarm"
        assert namespace["evaluate"](config, snapshot, NOW)["status"] == "healthy"
    elif mutant == "MUTANT_RESTART_STALE_LIVE_WATCHER":
        snapshot["sentinel_at_utc"] = (NOW - timedelta(minutes=6)).isoformat()
        assert kernel.evaluate(config, snapshot, NOW)["restart"] is False
        assert namespace["evaluate"](config, snapshot, NOW)["restart"] is True
    else:
        missing = deepcopy(snapshot)
        missing["processes"] = [deepcopy(config["owner"])]
        receipt = {"pid": 300, "created_utc": NOW.isoformat(), "argv": ARGV}
        after = deepcopy(snapshot)
        after["processes"][1] = receipt
        second = snapshot if mutant == "MUTANT_SKIP_POST_INTENT_RECHECK" else missing
        control = Services([missing, second, after], receipt)
        faulty = Services([missing, second, after], receipt)
        if mutant == "MUTANT_RETRY_UNCERTAIN_START":
            control.intent = faulty.intent = {"retained": True}
        assert kernel.run_cycle(config, NOW, control)["status"] == "alarm"
        namespace["run_cycle"](config, NOW, faulty)
        assert not any(isinstance(c, tuple) and c[0] == "start" for c in control.calls)
        assert any(isinstance(c, tuple) and c[0] == "start" for c in faulty.calls)


def test_unrelated_exact_peer_watcher_not_touched(kernel, config, snapshot):
    peer = deepcopy(config["watcher"])
    peer["pid"] = 777
    peer["argv"][6] = "claude-rco-1"
    snapshot["processes"].append(peer)
    assert kernel.evaluate(config, snapshot, NOW)["status"] == "healthy"


@pytest.mark.parametrize("mutation", ["bool_pid", "opaque", "native_config", "missing_runtime_root", "relative_path",
    "wrong_agent", "command_injection", "same_pid", "future_creation", "naive_timestamp"])
def test_invalid_expected_config_never_starts(kernel, config, snapshot, mutation):
    if mutation == "bool_pid":
        config["watcher"]["pid"] = True
    elif mutation == "opaque":
        config["watcher"]["argv"] = "unparsed"
    elif mutation == "native_config":
        config["producer_kind"] = "native"
    elif mutation == "missing_runtime_root":
        config["watcher"]["argv"] = ARGV[:-2]
    elif mutation == "relative_path":
        config["watcher"]["argv"][4] = "Watch-Bridge.ps1"
    elif mutation == "wrong_agent":
        config["watcher"]["argv"][6] = "codex-lead-1"
    elif mutation == "command_injection":
        config["watcher"]["argv"] += ["-Command", "irrelevant"]
    elif mutation == "same_pid":
        config["watcher"]["pid"] = config["owner"]["pid"]
    elif mutation == "future_creation":
        config["watcher"]["created_utc"] = (NOW + timedelta(seconds=1)).isoformat()
    else:
        config["watcher"]["created_utc"] = "2026-10-12T18:47:10"
    assert kernel.evaluate(config, snapshot, NOW)["status"] == "alarm"


def test_boundary_age_values_and_failed_notice_not_claimed(kernel, config, snapshot):
    snapshot["observed_at_utc"] = (NOW - timedelta(seconds=10)).isoformat()
    snapshot["sentinel_at_utc"] = (NOW - timedelta(seconds=300)).isoformat()
    assert kernel.evaluate(config, snapshot, NOW)["status"] == "healthy"
    snapshot["complete"] = False
    s = Services([snapshot], None)
    def refused_notice(result):
        raise OSError("alarm destination unavailable")
    s.alarm = refused_notice
    assert kernel.run_cycle(config, NOW, s)["alarm_delivery"] == "failed"


def test_start_exception_retains_intent_and_never_claims_delivery(kernel, config, snapshot):
    snapshot["processes"] = [deepcopy(config["owner"])]
    s = Services([snapshot, snapshot], None)
    def uncertain_start(argv):
        raise OSError("lost outcome")
    s.start = uncertain_start
    result = kernel.run_cycle(config, NOW, s)
    assert result["status"] == "alarm"
    assert s.intent is not None
    assert result["alarm_delivery"] == "adapter_returned_not_receipt_verified"
