from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from tools.wd_grok_helper import SCHEMA, consult, exclusive, status, write_state

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


def seed(root, age=3600):
    write_state(root, {"schema": SCHEMA, "last_attempt_utc": (NOW-timedelta(seconds=age)).isoformat(), "status": "answered"})


def test_one_call_per_rolling_hour_survives_reload(tmp_path):
    seed(tmp_path)
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        assert status(tmp_path, NOW)["status"] == "reserved"
        assert command[command.index("--tools") + 1] == ""
        assert "--no-subagents" in command and "--always-approve" not in command
        return SimpleNamespace(returncode=0, stdout="Evidence-based advice")
    assert consult(tmp_path, "test/task", "Review supplied evidence", ["fake"], runner=runner, now=NOW)["status"] == "answered"
    assert not status(tmp_path, NOW+timedelta(seconds=3599))["eligible"]
    assert consult(tmp_path, "next", "Second ask", ["fake"], runner=runner, now=NOW)["decision"] == "deferred_hourly_limit"
    assert len(calls) == 1
    assert status(tmp_path, NOW+timedelta(hours=1))["eligible"]


@pytest.mark.parametrize("failure", ["exit", "exception"])
def test_failure_consumes_hour(tmp_path, failure):
    seed(tmp_path)
    def runner(*args, **kwargs):
        if failure == "exception":
            raise TimeoutError()
        return SimpleNamespace(returncode=1, stdout="")
    result = consult(tmp_path, "test", "Ask", ["fake"], runner=runner, now=NOW)
    assert result["status"] == "failed"
    assert not status(tmp_path, NOW)["eligible"]


def test_corrupt_or_missing_state_blocks(tmp_path):
    with pytest.raises(ValueError):
        status(tmp_path, NOW)
    (tmp_path / "hourly-state.json").write_text("{}")
    with pytest.raises(ValueError):
        status(tmp_path, NOW)


def test_clock_rollback_does_not_open_budget(tmp_path):
    seed(tmp_path, age=-500)
    assert not status(tmp_path, NOW)["eligible"]


def test_status_is_read_only(tmp_path):
    seed(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    status(tmp_path, NOW)
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_competing_process_lock_blocks_second_request(tmp_path):
    seed(tmp_path)
    with exclusive(tmp_path):
        with pytest.raises(OSError):
            with exclusive(tmp_path):
                pytest.fail("Second lock acquired")
