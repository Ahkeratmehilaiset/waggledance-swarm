"""Synthetic CLI contract; environment pins and lane identity are not authenticated."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from sandbox.mind.step2.test_status_public_filter import ROSTER, event, read_fixture
from tools import wd_mind_public_session as session
from tools import wd_mind_public_session_cli as cli


@pytest.fixture
def ready(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    result, pins, _ = read_fixture(source, [event(message="")])
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    session.publish_status(audit, result, as_of=datetime.now(timezone.utc), enabled=True, **pins)
    for item in ("anchor", "policy", "roster", "public_policy"):
        file = tmp_path / (item + ".json")
        file.write_bytes(pins[item + "_bytes"])
        monkeypatch.setenv("WD_MIND_PUBLIC_" + item.upper() + "_PATH", str(file))
        monkeypatch.setenv("WD_MIND_PUBLIC_" + item.upper() + "_SHA256", pins[item + "_sha256"])
    monkeypatch.setenv("WD_MIND_PUBLIC_AUDIT_ROOT", str(audit))
    monkeypatch.setenv("WD_MIND_PUBLIC_MAX_AGE_SECONDS", "900")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", "fable-5")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", ROSTER["fable-5"])
    monkeypatch.delenv("WD_MIND_PUBLIC_ENABLED", raising=False)
    return audit


def check_refused(capsys):
    result = capsys.readouterr()
    assert result.out == ""
    assert json.loads(result.err) == {"error": "public_session_refused", "authority": "none"}
    assert "synthetic" not in result.err and "sha256" not in result.err


def test_cli_defaults_off_and_never_falls_back(ready, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_bounded_pin", lambda *args: pytest.fail("disabled must not read pins"))
    assert cli.main([]) == 2
    check_refused(capsys)


@pytest.mark.parametrize("lane", sorted(ROSTER))
def test_cli_reads_actual_synthetic_publication_for_five_lanes(ready, monkeypatch, capsys, lane):
    monkeypatch.setenv("WD_MIND_PUBLIC_ENABLED", "1")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", lane)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", ROSTER[lane])
    assert cli.main([]) == 0
    captured = capsys.readouterr()
    obj = json.loads(captured.out)
    assert captured.err == "" and obj["lane"] == lane and obj["authority"] == "none"
    assert obj["identity_verification"] == "caller_declared_not_authenticated" and obj["source_freshness"] == "UNKNOWN"
    assert len(obj["records"]) == 1 and obj["records"][0]["task_id"] == "public-task"
    assert str(ready) not in captured.out and "sha256" not in captured.out and ROSTER[lane] not in captured.out


@pytest.mark.parametrize("name,value", [
    ("WD_MIND_PUBLIC_ENABLED", "true"), ("AGENT_BRIDGE_AGENT", "unknown"),
    ("AGENT_BRIDGE_AGENT_UUID", ROSTER["codex-lead-1"]),
    ("WD_MIND_PUBLIC_MAX_AGE_SECONDS", "0"), ("WD_MIND_PUBLIC_MAX_AGE_SECONDS", "901"),
    ("WD_MIND_PUBLIC_MAX_AGE_SECONDS", "1.5"), ("WD_MIND_PUBLIC_ANCHOR_SHA256", "0" * 64),
    ("WD_MIND_PUBLIC_POLICY_PATH", "relative-private-path"),
])
def test_invalid_configuration_is_fixed_refusal_without_output(ready, monkeypatch, capsys, name, value):
    monkeypatch.setenv("WD_MIND_PUBLIC_ENABLED", "1")
    monkeypatch.setenv(name, value)
    assert cli.main([]) == 2
    check_refused(capsys)


def test_oversized_pin_and_unexpected_cli_argument_refuse(ready, monkeypatch, capsys):
    monkeypatch.setenv("WD_MIND_PUBLIC_ENABLED", "1")
    Path(os.environ["WD_MIND_PUBLIC_POLICY_PATH"]).write_bytes(b"x" * (65536 + 1))
    assert cli.main([]) == 2
    check_refused(capsys)
    assert cli.main(["synthetic-secret-argument"]) == 2
    check_refused(capsys)


def test_reparse_or_symlink_pin_is_refused(ready, monkeypatch, capsys):
    monkeypatch.setenv("WD_MIND_PUBLIC_ENABLED", "1")
    pinned = Path(os.environ["WD_MIND_PUBLIC_POLICY_PATH"])
    original = Path.lstat
    def fake_lstat(path):
        if path == pinned:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original(path)
    monkeypatch.setattr(Path, "lstat", fake_lstat)
    assert cli.main([]) == 2
    check_refused(capsys)


def test_expired_publication_refuses(ready, monkeypatch, capsys):
    monkeypatch.setenv("WD_MIND_PUBLIC_ENABLED", "1")
    path = ready / "public-status.json"
    doc = json.loads(path.read_bytes())
    doc["as_of_utc"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert cli.main([]) == 2
    check_refused(capsys)


def test_process_entrypoint_has_clean_json_stdout(ready, monkeypatch):
    monkeypatch.setenv("WD_MIND_PUBLIC_ENABLED", "1")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", "-m", "tools.wd_mind_public_session_cli"],
                            capture_output=True, text=True, env=env, timeout=30, check=False)
    assert result.returncode == 0 and result.stderr == ""
    assert json.loads(result.stdout)["lane"] == "fable-5"


@pytest.mark.parametrize("receipt", [1, None, 0])
def test_short_or_missing_stdout_write_receipt_is_failure(ready, monkeypatch, capsys, receipt):
    monkeypatch.setenv("WD_MIND_PUBLIC_ENABLED", "1")
    class ShortOutput:
        def __init__(self):
            self.delivered = ""
        def write(self, value):
            # Bytes already delivered to a pipe cannot be retracted.
            self.delivered += value[:1] if receipt != 0 else ""
            return receipt
    output = ShortOutput()
    with monkeypatch.context() as changes:
        changes.setattr(sys, "stdout", output)
        assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.err) == {"error": "public_session_refused", "authority": "none"}
    assert output.delivered in ("", "{")


def test_stdout_flush_failure_is_not_reported_as_success(ready, monkeypatch, capsys):
    monkeypatch.setenv("WD_MIND_PUBLIC_ENABLED", "1")
    class FailedFlush:
        def write(self, value):
            return len(value)
        def flush(self):
            raise OSError("synthetic-private-detail")
    with monkeypatch.context() as changes:
        changes.setattr(sys, "stdout", FailedFlush())
        assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.err) == {"error": "public_session_refused", "authority": "none"}
    assert "synthetic-private-detail" not in captured.err
