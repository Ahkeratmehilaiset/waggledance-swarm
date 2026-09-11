"""Ordinary read-view contracts; the view never supplies gate authority."""
import pytest
import json
from pathlib import Path
import subprocess
import sys

from tools.bridge_compact_view import compact_view, event_id


def event(kind="message", **fields):
    return dict(ts_utc="2026-09-11T08:00:00Z", agent="codex-tools-1",
                session_id="session-1", task_id="task-1", type=kind,
                status="progress", message="x" * 1000, **fields)


def test_heartbeat_separate_exact_duplicates_removed_and_input_unchanged():
    row = event()
    rows = [event("heartbeat"), row, dict(row)]
    result = compact_view(rows)
    assert result["stats"]["heartbeats"] == 1
    assert result["stats"]["duplicates"] == 1
    assert len(result["events"]) == 1
    assert len(row["message"]) == 1000
    assert result["events"][0]["ref"] == event_id(row)
    assert result["authority"] == "none"


def test_delta_and_unknown_cursor():
    first, second = event(), event("test")
    with pytest.raises(ValueError, match="cursor"):
        compact_view([second], after=event_id(first))


@pytest.mark.parametrize("kind", ["decision", "finding", "blocked"])
def test_authority_relevant_events_preserve_full_original(kind):
    row = event(kind, payload={"head_sha": "a" * 40, "evidence": ["E1"]})
    assert compact_view([row])["events"][0]["detail"] == row


def test_latest_observation_is_per_author_session_task_and_head_not_a_verdict():
    first = event(payload={"head_sha": "a" * 40})
    second = event(payload={"head_sha": "b" * 40})
    result = compact_view([first, second])
    assert len(result["observations"]) == 2
    assert len(result["events"]) == 2


def test_canonical_exact_head_separates_observations_and_conflict_is_visible():
    rows = [event("rco_review", payload={"exact_head": head, "head": "c" * 40})
            for head in ("a" * 40, "b" * 40)]
    result = compact_view(rows)
    assert {row["head"] for row in result["observations"]} == {"a" * 40, "b" * 40}
    assert all(row["head_conflict"] for row in result["events"])


def test_unknown_fields_require_original_and_explicit_supersession_is_only_reference():
    first = event()
    second = event("handoff", payload={"supersedes_event_id": event_id(first)})
    result = compact_view([first, second])
    assert len(result["events"]) == 2
    assert result["events"][1]["supersedes"] == event_id(first)
    assert result["events"][1]["requires_detail"]


def test_exact_duplicates_do_not_drop_different_recipient_or_head():
    assert len(compact_view([event(to="lead"), event(to="tools")])["events"]) == 2


def test_reference_is_key_order_independent():
    row = event()
    assert event_id(row) == event_id(dict(reversed(list(row.items()))))


def test_empty_delta_has_no_events():
    assert compact_view([])["events"] == []


def test_cli_delta_detail_and_no_log_mutation(tmp_path, capsys):
    from tools.bridge_compact_view import main
    path = tmp_path / "events.jsonl"
    row = event()
    original = json.dumps(row).encode() + b"\n"
    path.write_bytes(original)
    assert main(["--events", str(path)]) == 0
    cursor = json.loads(capsys.readouterr().out)["cursor"]
    assert main(["--events", str(path), "--after", cursor]) == 0
    assert main(["--events", str(path), "--event-id", event_id(row)]) == 0
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("content", [b"not json\n", b"[]\n"])
def test_cli_invalid_input_reports_error_not_empty_work(tmp_path, capsys, content):
    from tools.bridge_compact_view import main
    path = tmp_path / "events.jsonl"
    path.write_bytes(content)
    assert main(["--events", str(path)]) == 2
    assert "do not infer no work" in capsys.readouterr().err


def test_duplicate_cursor_replays_instead_of_skipping_intervening_events():
    first, second = event(), event("test")
    with pytest.raises(ValueError, match="cursor"):
        compact_view([second, first], after=event_id(first))


def test_position_cursor_retains_work_before_later_identical_event(tmp_path, capsys):
    from tools.bridge_compact_view import main
    path = tmp_path / "events.jsonl"
    first, second = event(), event("test")
    first_line = json.dumps(first) + "\n"
    path.write_text(first_line, encoding="utf-8")
    assert main(["--events", str(path), "--tail", "2"]) == 0
    cursor = json.loads(capsys.readouterr().out)["cursor"]
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(second) + "\n" + first_line)
    assert main(["--events", str(path), "--tail", "2", "--after", cursor]) == 0
    report = json.loads(capsys.readouterr().out)
    assert any(row["ref"] == event_id(second) for row in report["events"])
    assert main(["--events", str(path), "--after", report["cursor"]]) == 0
    assert json.loads(capsys.readouterr().out)["events"] == []


def test_tail_cursor_does_not_skip_incomplete_line(tmp_path, capsys):
    from tools.bridge_compact_view import main
    path = tmp_path / "events.jsonl"
    first, second = event(), event("test")
    line = json.dumps(second).encode()
    path.write_bytes(json.dumps(first).encode() + b"\n" + line[:20])
    assert main(["--events", str(path)]) == 0
    cursor = json.loads(capsys.readouterr().out)["cursor"]
    with path.open("ab") as stream:
        stream.write(line[20:] + b"\n")
    assert main(["--events", str(path), "--after", cursor]) == 0
    assert json.loads(capsys.readouterr().out)["events"][0]["ref"] == event_id(second)


@pytest.mark.parametrize("change", ["truncate", "replace", "blank", "bad_token"])
def test_position_cursor_changes_fail_explicitly(tmp_path, capsys, change):
    from tools.bridge_compact_view import main
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(event()) + "\n", encoding="utf-8")
    assert main(["--events", str(path)]) == 0
    cursor = json.loads(capsys.readouterr().out)["cursor"]
    if change == "truncate":
        path.write_bytes(b"")
    elif change == "replace":
        path.rename(tmp_path / "old.jsonl")
        path.write_text(json.dumps(event("test")) + "\n", encoding="utf-8")
    elif change == "blank":
        with path.open("ab") as stream:
            stream.write(b"\n")
    else:
        cursor = "not-a-position-cursor"
    assert main(["--events", str(path), "--after", cursor]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "position_cursor" in captured.err


def test_private_marker_is_not_printed(tmp_path, capsys):
    from tools.bridge_compact_view import main
    path = tmp_path / "events.jsonl"
    row = event("decision")
    row["message"] = "PRIVATE_MARKER"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert main(["--events", str(path)]) == 2
    captured = capsys.readouterr()
    assert "PRIVATE_MARKER" not in captured.out + captured.err


def test_powershell_compact_path_is_read_only(tmp_path):
    import shutil
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell unavailable")
    import os
    env = dict(os.environ, AGENT_BRIDGE_RUNTIME_ROOT=str(tmp_path / "missing"))
    script = Path(__file__).resolve().parents[2] / ".agent-bridge/bin/Read-AgentBridge.ps1"
    result = subprocess.run([shell, "-NoProfile", "-File", str(script), "-Compact",
                             "-PythonExecutable", sys.executable],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["events"] == []
    assert not (tmp_path / "missing").exists()


def test_zero_tail_keeps_documented_bounded_snapshot_semantics(tmp_path):
    from tools.bridge_compact_view import main
    assert main(["--events", str(tmp_path / "absent"), "--tail", "0"]) == 0


@pytest.mark.parametrize("kind", [[], {}, None, 42])
def test_malformed_type_has_explicit_validation_error(kind):
    with pytest.raises(ValueError, match="type"):
        compact_view([event(kind)])


@pytest.mark.parametrize("extra", [
    ["-Agent", "codex-tools-1"], ["-Tail", "1"], ["-NoContinuity"],
    ["-NoAckReceived"],
])
def test_compact_rejects_ignored_legacy_options(tmp_path, extra):
    import os
    import shutil
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell unavailable")
    env = dict(os.environ, AGENT_BRIDGE_RUNTIME_ROOT=str(tmp_path / "missing"))
    script = Path(__file__).resolve().parents[2] / ".agent-bridge/bin/Read-AgentBridge.ps1"
    result = subprocess.run([shell, "-NoProfile", "-File", str(script), "-Compact",
                             "-PythonExecutable", sys.executable, *extra],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "legacy" in result.stderr
    assert not (tmp_path / "missing").exists()
