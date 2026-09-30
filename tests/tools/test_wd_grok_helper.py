from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from tools.wd_grok_helper import SCHEMA, consult, exclusive, status, write_state
from tools import wd_grok_helper
from tools.wd_grok_helper import GitBlobBroker, parse_broker_action

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)


def test_pure_broker_reads_only_pinned_git_blobs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True).stdout.decode().strip()
    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    (repo / "a.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    (repo / "sub").mkdir()
    (repo / "sub" / "b.txt").write_text("beta\ngamma\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "fixture")
    sha = git("rev-parse", "HEAD")
    broker = GitBlobBroker(repo, sha, Path(shutil.which("git")).resolve())
    (repo / "a.txt").write_text("dirty and private\n", encoding="utf-8")
    assert broker.dispatch(parse_broker_action('{"op":"read_file","path":"a.txt","start_line":1,"end_line":2}'))["text"] == "alpha\nbeta"
    assert broker.dispatch(parse_broker_action('{"op":"list_dir","path":""}'))["entries"] == ["a.txt", "sub/"]
    assert broker.dispatch(parse_broker_action('{"op":"grep","query":"beta"}'))["matches"] == [
        {"path": "a.txt", "line": 2, "text": "beta"},
        {"path": "sub/b.txt", "line": 1, "text": "beta"}]
    with pytest.raises(ValueError, match="not in pinned"):
        broker.dispatch({"op": "read_file", "path": ".git/config"})
    with pytest.raises(ValueError, match="Final text"):
        broker.dispatch({"op": "final", "text": "done"})


def test_pure_broker_rejects_symlink_and_large_blob(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args, input=None):
        return subprocess.run(["git", "-C", str(repo), *args], input=input,
                              check=True, capture_output=True).stdout.decode().strip()
    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    oid = git("hash-object", "-w", "--stdin", input=b"outside.txt")
    git("update-index", "--add", "--cacheinfo", f"120000,{oid},link")
    tree = git("write-tree")
    symlink_commit = git("commit-tree", tree, "-m", "symlink")
    with pytest.raises(ValueError, match="nonregular"):
        GitBlobBroker(repo, symlink_commit, Path(shutil.which("git")).resolve())
    git("read-tree", "--empty")
    (repo / "large.txt").write_bytes(b"x" * (128 * 1024 + 1))
    git("add", "large.txt")
    large_commit = git("commit-tree", git("write-tree"), "-m", "large")
    broker = GitBlobBroker(repo, large_commit, Path(shutil.which("git")).resolve())
    with pytest.raises(ValueError, match="size limit"):
        broker.dispatch({"op": "read_file", "path": "large.txt"})
    case_tree = git("mktree", input=(f"100644 blob {oid}\tA.txt\n"
                                     f"100644 blob {oid}\ta.txt\n").encode())
    case_commit = git("commit-tree", case_tree, "-m", "case collision")
    with pytest.raises(ValueError, match="case-colliding"):
        GitBlobBroker(repo, case_commit, Path(shutil.which("git")).resolve())


def test_pure_broker_rejects_nonfull_sha_and_git_env_override(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    with pytest.raises(ValueError, match="Full commit SHA"):
        GitBlobBroker(repo, "abcdef", Path(shutil.which("git")).resolve())
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(tmp_path / "missing"))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.worktree")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(tmp_path / "missing"))
    # There is no commit, but caller-controlled Git overrides cannot turn
    # this into an object-store read outside the selected repository.
    with pytest.raises(ValueError, match="Git plumbing"):
        GitBlobBroker(repo, "a" * 40, Path(shutil.which("git")).resolve())


def test_broker_rejects_relative_git_executable_and_deep_json():
    with pytest.raises(ValueError, match="absolute trusted Git"):
        GitBlobBroker(Path.cwd(), "a" * 40, Path("git"))
    nested = "[" * 3000 + "]" * 1000
    with pytest.raises(ValueError):
        parse_broker_action(nested)
    deep = []
    for _ in range(1100):
        deep = [deep]
    with pytest.raises(ValueError):
        GitBlobBroker.dispatch(object.__new__(GitBlobBroker), {"op": "list_dir", "path": "", "extra": deep})


def test_broker_deadline_blocks_git_spawn_and_mid_search(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True).stdout.decode().strip()
    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    for name in ("a.txt", "b.txt"):
        (repo / name).write_text("match\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "fixture")
    broker = GitBlobBroker(repo, git("rev-parse", "HEAD"), Path(shutil.which("git")).resolve())
    broker.started = 0
    broker.clock = lambda: 61
    monkeypatch.setattr(wd_grok_helper.subprocess, "Popen", lambda *a, **k: pytest.fail("expired session spawned git"))
    with pytest.raises(ValueError, match="time"):
        broker._git(["cat-file", "-s", next(iter(broker.blobs.values()))], 32)
    monkeypatch.undo()
    intervals = []
    class TimerStub:
        def __init__(self, interval, callback):
            intervals.append(interval)
        def start(self):
            pass
        def cancel(self):
            pass
    broker.clock = lambda: 59.5
    monkeypatch.setattr(wd_grok_helper.threading, "Timer", TimerStub)
    broker._git(["cat-file", "-s", next(iter(broker.blobs.values()))], 32)
    assert 0 < intervals[0] <= 0.5
    monkeypatch.undo()
    broker.clock = lambda: broker.started
    calls = []
    def blob(oid):
        calls.append(oid)
        broker.clock = lambda: broker.started + 61
        return b"match\n"
    monkeypatch.setattr(broker, "_blob", blob)
    with pytest.raises(ValueError, match="time"):
        broker.dispatch({"op": "grep", "query": "match"})
    assert len(calls) == 1


def test_broker_text_and_session_limits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True).stdout.decode().strip()
    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    (repo / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    (repo / "has-nul.txt").write_bytes(b"abc\0def")
    (repo / "bad.txt").write_bytes(b"\xff")
    (repo / "long.txt").write_bytes(b"x" * 17000)
    (repo / "matches.txt").write_text("hit\n" * 51, encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "fixture")
    broker = GitBlobBroker(repo, git("rev-parse", "HEAD"), Path(shutil.which("git")).resolve())
    assert broker.dispatch({"op": "read_file", "path": "crlf.txt"})["text"] == "one\ntwo"
    for name in ("has-nul.txt", "bad.txt"):
        with pytest.raises(ValueError, match="not text"):
            broker.dispatch({"op": "read_file", "path": name})
    with pytest.raises(ValueError, match="output/session"):
        broker.dispatch({"op": "read_file", "path": "long.txt"})
    with pytest.raises(ValueError, match="Search match limit"):
        broker.dispatch({"op": "grep", "path": "matches.txt", "query": "hit"})
    broker.source_bytes = 2 * 1024 * 1024
    with pytest.raises(ValueError, match="size limit"):
        broker.dispatch({"op": "read_file", "path": "crlf.txt"})
    broker.source_bytes = 0
    broker.output_bytes = 128 * 1024
    with pytest.raises(ValueError, match="output/session"):
        broker.dispatch({"op": "list_dir", "path": ""})
    broker.output_bytes = 0
    broker.calls = 30
    with pytest.raises(ValueError, match="action limit"):
        broker.dispatch({"op": "list_dir", "path": ""})


def test_broker_rejects_broad_listing_and_search(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True).stdout.decode().strip()
    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    for i in range(257):
        (repo / f"f{i:03}.txt").write_text("needle\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "fixture")
    broker = GitBlobBroker(repo, git("rev-parse", "HEAD"), Path(shutil.which("git")).resolve())
    with pytest.raises(ValueError, match="Directory entry limit"):
        broker.dispatch({"op": "list_dir", "path": ""})
    with pytest.raises(ValueError, match="too broad"):
        broker.dispatch({"op": "grep", "query": "needle"})


@pytest.mark.parametrize("raw", [
    '{"op":"read_file","path":"a.txt","path":"b.txt"}',
    '{"op":"read_file","path":"a.txt","extra":1}',
    '{"op":"read_file","path":"../secret"}',
    '{"op":"read_file","path":"C:/secret"}',
    '{"op":"read_file","path":"a\\\\b"}',
    '{"op":"read_file","path":"a/./b"}',
    '{"op":"read_file","path":"a//b"}',
    '{"op":"read_file","path":"a\u202e.txt"}',
    '{"op":"read_file","path":"a.txt","start_line":true}',
    '{"op":"read_file","path":"a.txt","start_line":100001,"end_line":100001}',
    '{"op":"grep","query":""}',
    '{"op":"grep","query":"x","path":"a*"}',
    '{"op":"shell","path":"a.txt"}',
    '[' * 3000 + ']' * 1000,
])
def test_broker_parser_fails_closed(raw):
    with pytest.raises(ValueError):
        parse_broker_action(raw)


def exception_file(root, **updates):
    import hashlib
    grant = dict(schema="wd.grok-task-exception.v1", exception_id="operator-brainstorm",
                 authorization_ref="operator: explicit three-round permission; round1 already used",
                 task_ids=["brainstorm/r2", "brainstorm/r3"], max_attempts=2,
                 issued_at_utc=NOW.isoformat(), expires_at_utc=(NOW+timedelta(hours=2)).isoformat())
    grant.update(updates)
    path = root / "grant.json"
    path.write_text(json.dumps(grant), encoding="utf-8")
    return dict(exception_path=path, exception_sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def test_task_exceptions_are_retired_and_recorded_history_is_carried_unchanged(tmp_path):
    history = {"operator-brainstorm": {"sha256": "a" * 64, "attempts": [
        {"task_id": "brainstorm/r2", "request_id": "old", "reserved_at_utc": NOW.isoformat()}]}}
    write_state(tmp_path, {"schema": SCHEMA, "last_attempt_utc": (NOW - timedelta(seconds=1)).isoformat(),
                           "status": "answered", "task_exceptions": history})
    before = (tmp_path / "hourly-state.json").read_bytes()
    with pytest.raises(ValueError, match="retired"):
        consult(tmp_path, "brainstorm/r3", "ask", ["fake"], now=NOW, **exception_file(tmp_path),
                runner=lambda *a, **k: pytest.fail("a retired exception launched"))
    assert (tmp_path / "hourly-state.json").read_bytes() == before
    result = consult(tmp_path, "brainstorm/r3", "ask", ["fake"], now=NOW,
                     runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout="ok"))
    assert result["status"] == "answered" and "budget_exception" not in result
    assert json.loads((tmp_path / "hourly-state.json").read_text())["task_exceptions"] == history


@pytest.mark.parametrize("update", [
    {"task_ids": ["brainstorm/r20"]}, {"max_attempts": True}, {"max_attempts": 4},
    {"authorization_ref": ""}, {"expires_at_utc": NOW.isoformat()},
    {"issued_at_utc": (NOW+timedelta(seconds=1)).isoformat()},
    {"expires_at_utc": (NOW+timedelta(days=2)).isoformat()},
    {"issued_at_utc": "2026-09-12T00:00:00"}, {"task_ids": ["brainstorm/r2", "brainstorm/r2"]},
])
def test_invalid_exception_never_launches_or_changes_state(tmp_path, update):
    seed(tmp_path, age=1)
    grant = exception_file(tmp_path, **update)
    before = (tmp_path / "hourly-state.json").read_bytes()
    with pytest.raises(ValueError):
        consult(tmp_path, "brainstorm/r2", "ask", ["fake"], now=NOW,
                runner=lambda *a, **k: pytest.fail("invalid exception launched"), **grant)
    assert (tmp_path / "hourly-state.json").read_bytes() == before


@pytest.mark.parametrize('failed', [False, True])
def test_consult_emits_lifecycle_without_exposing_prompt_and_needs_no_hour_wait(tmp_path, failed):
    seed(tmp_path)
    events = []
    def emit(stage, state):
        events.append((stage, dict(state)))
        if failed:
            raise OSError('bridge unavailable')
    result = consult(tmp_path, 'lifecycle', 'private prompt', ['fake'], now=NOW,
                     runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout='advice'), emitter=emit)
    assert [e[0] for e in events] == ['started', 'answered']
    assert events[0][1]['request_id'] == events[1][1]['request_id']
    assert 'private prompt' not in json.dumps(events)
    assert events[1][1]['report_sha256']
    assert result['status'] == 'answered' and result['eligible'] is True        # no local hour wait
    assert bool(result.get('bridge_event_errors')) is failed
    second = consult(tmp_path, 'second-task', 'ask', ['fake'], now=NOW,
                     runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout='more'), emitter=emit)
    assert second['status'] == 'answered' and second['task_id'] == 'second-task'
    assert [e[0] for e in events] == ['started', 'answered', 'started', 'answered']
    assert events[2][1]['request_id'] != events[0][1]['request_id']


def test_lifecycle_emitter_requires_canonical_receipt_and_never_runs_model(tmp_path, monkeypatch):
    bundle = tmp_path / 'bundle'
    packaged = bundle / 'tools-bootstrap/tools/wd_grok_helper.py'
    packaged.parent.mkdir(parents=True)
    (bundle / 'Invoke-WdGrok.ps1').write_text('# pinned wrapper fixture')
    monkeypatch.setattr(wd_grok_helper, '__file__', str(packaged))
    monkeypatch.setenv('SystemRoot', str(tmp_path / 'Windows'))
    records = []
    def run(command, **kwargs):
        import base64
        records.append(json.loads(base64.b64decode(command[-1])))
        assert command[-2] == '-LifecycleBase64'
        assert '--prompt-file' not in command
        return SimpleNamespace(returncode=0, stdout=json.dumps({'_bridge_delivery': {
            'accepted': True, 'canonical_durable': len(records) == 1}}))
    monkeypatch.setattr(wd_grok_helper.subprocess, 'run', run)
    wd_grok_helper.emit_bridge_event('answered', {'task_id': 'test', 'request_id': 'id'})
    with pytest.raises(OSError, match='canonical'):
        wd_grok_helper.emit_bridge_event('failed', {'task_id': 'test', 'request_id': 'id'})
    assert [r['stage'] for r in records] == ['answered', 'failed']


def seed(root, age=3600):
    write_state(root, {"schema": SCHEMA, "last_attempt_utc": (NOW-timedelta(seconds=age)).isoformat(), "status": "answered"})


def test_sequential_consultations_need_no_local_hour_wait(tmp_path):
    seed(tmp_path, age=1)
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        assert status(tmp_path, NOW)["status"] == "reserved"          # single-flight while running
        assert command[command.index("--tools") + 1] == ""
        assert "--no-subagents" in command and "--always-approve" not in command
        return SimpleNamespace(returncode=0, stdout="Evidence-based advice")
    for task in ("test/task", "next"):
        assert consult(tmp_path, task, "Review supplied evidence", ["fake"], runner=runner, now=NOW)["status"] == "answered"
        report = status(tmp_path, NOW)
        assert (report["local_availability"], report["eligible"], report["next_eligible_utc"]) == ("available", True, None)
        assert report["provider_quota"] == "unknown" and report["provider_evidence"] is None
    assert len(calls) == 2


@pytest.mark.parametrize("age", [1, 5000])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("durable", ["reserved", "interrupted_or_unknown"])
def test_unfinished_attempt_is_preserved_without_a_new_consultation(tmp_path, age, legacy, durable):
    state = {"schema": SCHEMA, "status": durable, "task_id": "previous/task",
             "request_id": "previous-request", "last_attempt_utc": (NOW-timedelta(seconds=age)).isoformat()}
    if not legacy:
        state["timeout_seconds"] = 300
    write_state(tmp_path, state)
    before = (tmp_path / "hourly-state.json").read_bytes()
    events = []
    report = consult(tmp_path, "brainstorm/r2", "ask", ["fake"], now=NOW,
                     runner=lambda *a, **k: pytest.fail("unfinished attempt bypass"),
                     emitter=lambda stage, event: events.append((stage, event)))
    assert report["decision"] == "deferred_unreconciled_attempt"
    assert report["status"] == "deferred" and report["consultation_attempted"] is False
    assert report["task_id"] == "brainstorm/r2" and report["request_id"] is None
    assert report["previous_attempt"]["task_id"] == "previous/task"
    assert report["previous_attempt"]["request_id"] == "previous-request"
    assert wd_grok_helper.consultation_exit_code(report) == 2
    assert (tmp_path / "hourly-state.json").read_bytes() == before
    assert [event[0] for event in events] == ["deferred"]
    assert events[0][1]["task_id"] == "brainstorm/r2"


@pytest.mark.parametrize("report, expected", [({"status": "answered"}, 0),
                                            ({"status": "failed"}, 1),
                                            ({"status": "deferred"}, 2),
                                            ({"status": "reserved"}, 2), ({}, 2)])
def test_only_answered_consultation_has_success_exit(report, expected):
    assert wd_grok_helper.consultation_exit_code(report) == expected


def test_consult_uses_verbatim_prompt_mode(tmp_path):
    seed(tmp_path)
    commands = []
    def runner(command, **kwargs):
        commands.append(command)
        sent = Path(command[command.index("--prompt-file") + 1]).read_text(encoding="utf-8")
        assert sent.startswith("IMPORTANT: This prompt is COMPLETE.")
        assert "You have NO tools" in sent
        assert "500 words" in sent
        return SimpleNamespace(returncode=0, stdout="advice")
    assert consult(tmp_path, "verbatim", "Review evidence", ["fake"], runner=runner, now=NOW)["status"] == "answered"
    assert "--verbatim" in commands[0]
    assert "--prompt-file" in commands[0]


def test_cli_prompt_carries_only_the_callers_evidence(tmp_path, monkeypatch):
    # Another task's answered attempt, its report and a lane checkpoint sit in the state root.
    other = tmp_path / "other-response.md"
    other.write_text("OTHER TASK REPORT", encoding="utf-8")
    write_state(tmp_path, {"schema": SCHEMA, "last_attempt_utc": (NOW - timedelta(seconds=5)).isoformat(),
                           "status": "answered", "task_id": "other/task", "report_path": str(other)})
    (tmp_path / "wd-current-state.json").write_text('{"task_id": "other/task"}', encoding="utf-8")
    monkeypatch.setattr(wd_grok_helper, "STATE_ROOT", tmp_path)
    ask = tmp_path / "ask.md"
    ask.write_text("Evidence for mine/task only.", encoding="utf-8")
    prompt = wd_grok_helper.cli_prompt(ask)
    assert prompt == "Evidence for mine/task only."                   # nothing appended
    sent = []
    def runner(command, **kwargs):
        sent.append(Path(command[command.index("--prompt-file") + 1]).read_text(encoding="utf-8"))
        return SimpleNamespace(returncode=0, stdout="advice")
    assert consult(tmp_path, "mine/task", prompt, ["fake"], runner=runner, now=NOW)["status"] == "answered"
    assert len(sent) == 1 and sent[0].endswith("\n\nEvidence for mine/task only.")
    for leaked in ("OTHER TASK REPORT", "other/task", "PREVIOUS GROK", "CONTEXT ", "codex-lead-1"):
        assert leaked not in sent[0]
    assert "codex-lead-1" not in status(tmp_path, NOW)["role"]
    ask.write_text("x" * 24001, encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds 24000 bytes"):
        wd_grok_helper.cli_prompt(ask)


def test_default_advisory_command_uses_medium_effort():
    assert wd_grok_helper.advisory_command(Path("grok.exe"), "grok-model") == [
        "grok.exe", "--model", "grok-model", "--effort", "medium"]


def test_failed_consult_records_bounded_stderr_as_uninterpreted_evidence(tmp_path):
    seed(tmp_path)
    stderr = "noise" * 1000 + "CLI error: invalid option"
    result = consult(tmp_path, "stderr", "Review evidence", ["fake"], now=NOW,
                     runner=lambda *a, **k: SimpleNamespace(returncode=2, stdout="", stderr=stderr))
    assert result["status"] == "failed"
    assert result["stderr_excerpt"].endswith("CLI error: invalid option")
    assert len(result["stderr_excerpt"]) <= 2048
    assert result["stderr_truncated"] is True
    report = status(tmp_path, NOW)
    assert report["eligible"] is True and report["provider_quota"] == "unknown"      # no hour wait
    assert report["provider_evidence"]["stderr_excerpt"] == result["stderr_excerpt"]  # verbatim evidence


@pytest.mark.parametrize("failure", ["exit", "exception"])
def test_a_failed_attempt_is_complete_and_needs_no_hour_wait(tmp_path, failure):
    seed(tmp_path)
    def runner(*args, **kwargs):
        if failure == "exception":
            raise TimeoutError()
        return SimpleNamespace(returncode=1, stdout="")
    result = consult(tmp_path, "test", "Ask", ["fake"], runner=runner, now=NOW)
    assert result["status"] == "failed"
    assert status(tmp_path, NOW)["local_availability"] == "available"


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


@pytest.mark.parametrize("returncode", [0, 7])
def test_consult_records_duration_in_existing_result_only(tmp_path, monkeypatch, returncode):
    seed(tmp_path)
    clock = iter([10.0, 10.125])
    monkeypatch.setattr(wd_grok_helper, "monotonic", lambda: next(clock))
    result = consult(tmp_path, "duration", "Review", ["fake"], now=NOW,
                     runner=lambda *a, **k: SimpleNamespace(returncode=returncode, stdout="answer"))
    assert result["duration_seconds"] == 0.125
    assert result["exit_code"] == returncode
    assert result["finished_at_utc"]
    assert result["timing_scope"] == "consultation_after_budget_reservation"
    names = {p.name for p in tmp_path.iterdir()}
    assert len(names) == 4  # state, lock, existing request and response artifacts
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    status(tmp_path, NOW)
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_timeout_records_timing_and_completes_the_attempt(tmp_path, monkeypatch):
    seed(tmp_path)
    clock = iter([20.0, 25.0])
    monkeypatch.setattr(wd_grok_helper, "monotonic", lambda: next(clock))
    def runner(*a, **k):
        raise subprocess.TimeoutExpired("fake", 5)
    result = consult(tmp_path, "timeout", "Review", ["fake"], now=NOW, runner=runner)
    assert result["status"] == "failed"
    assert result["error_type"] == "TimeoutExpired"
    assert result["duration_seconds"] == 5.0
    assert status(tmp_path, NOW)["eligible"] is True                       # completed: no hour wait


def test_timeout_preserves_bounded_partial_output_and_stderr(tmp_path):
    seed(tmp_path)
    def runner(*args, **kwargs):
        raise subprocess.TimeoutExpired("fake", 300, output=b"partial advice", stderr=b"CLI stalled")
    result = consult(tmp_path, "timeout-output", "Review", ["fake"], now=NOW, runner=runner)
    assert result["status"] == "failed"
    assert result["error_type"] == "TimeoutExpired"
    assert result["stderr_excerpt"] == "CLI stalled"
    assert result["partial_report"] is True
    assert Path(result["report_path"]).read_text(encoding="utf-8") == "partial advice"
    assert status(tmp_path, NOW)["eligible"] is True                       # a timeout completes the attempt


def test_interrupted_reservation_survives_new_process_and_partial_temp(tmp_path):
    seed(tmp_path)
    def interrupted(*a, **k):
        raise KeyboardInterrupt()  # emulate abrupt termination after durable reservation
    with pytest.raises(KeyboardInterrupt):
        consult(tmp_path, "interrupted", "Review", ["fake"], now=NOW, runner=interrupted)
    saved = (tmp_path / "hourly-state.json").read_bytes()
    assert json.loads(saved)["status"] == "reserved"
    (tmp_path / ".hourly-interrupted.tmp").write_text('{"schema":', encoding="utf-8")
    import sys
    result = subprocess.run(
        [sys.executable, "-B", "-c",
         "import json,sys; from pathlib import Path; from datetime import datetime; "
         "from tools.wd_grok_helper import status; "
         "print(json.dumps(status(Path(sys.argv[1]), datetime.fromisoformat(sys.argv[2]))))",
         str(tmp_path), (NOW + timedelta(minutes=30)).isoformat()],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["eligible"] is False
    assert (tmp_path / "hourly-state.json").read_bytes() == saved


def test_competing_process_lock_blocks_second_request(tmp_path):
    seed(tmp_path)
    with exclusive(tmp_path):
        with pytest.raises(OSError):
            with exclusive(tmp_path):
                pytest.fail("Second lock acquired")


ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / "ops/windows/reboot"
PS = shutil.which("powershell.exe") or shutil.which("pwsh")


@pytest.mark.skipif(PS is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", sorted({p for p in (PS, shutil.which("pwsh")) if p}))
def test_exception_parameters_reach_verified_python_wrapper(tmp_path, shell):
    import hashlib
    import os
    script = tmp_path / "Invoke-WdGrok.ps1"
    shutil.copyfile(REBOOT / script.name, script)
    stub = tmp_path / "Invoke-WdBridgePython.ps1"
    stub.write_text('param([string]$Tool,[switch]$VerifyPackage)\n'
                    '[pscustomobject]@{tool=$Tool;verified=[bool]$VerifyPackage;argv=@($args)} | ConvertTo-Json -Compress\n'
                    '$global:LASTEXITCODE = 0\n')  # like the real wrapper, the stub publishes its tool code
    manifest = tmp_path / "deployment-manifest.json"
    manifest.write_text(json.dumps({"source_commit": "fixture", "files": {
        stub.name: hashlib.sha256(stub.read_bytes()).hexdigest().upper()}}))
    expected = hashlib.sha256(manifest.read_bytes()).hexdigest().upper()
    grant_hash = "A"*64
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command",
        f"$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='{expected}'; & '{script}' "
        f"-PromptPath '{tmp_path / 'prompt.md'}' -TaskId task/r2 "
        f"-ExceptionPath '{tmp_path / 'grant.json'}' -ExceptionSha256 '{grant_hash}'"],
        capture_output=True, text=True, timeout=30,
        env={k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"})
    assert result.returncode == 0, result.stdout + result.stderr
    forwarded = json.loads(result.stdout)
    assert forwarded["verified"] and forwarded["tool"] == "tools/wd_grok_helper.py"
    assert forwarded["argv"][-4:] == ["--exception-path", str(tmp_path / "grant.json"),
                                        "--exception-sha256", grant_hash]
    generated = (REBOOT / "Deploy-WdRebootBundle.ps1").read_text()
    grok_parameters = generated.split("'grok' {", 1)[1].split("'@", 1)[0]
    assert "$ExceptionPath" in grok_parameters and "$ExceptionSha256" in grok_parameters


def test_the_single_flight_lock_refuses_a_concurrent_consultation_without_state_change(tmp_path):
    seed(tmp_path, age=1)
    before = (tmp_path / "hourly-state.json").read_bytes()
    with exclusive(tmp_path), pytest.raises(OSError):
        consult(tmp_path, "concurrent", "ask", ["fake"], now=NOW,
                runner=lambda *a, **k: pytest.fail("a second consultation ran"))
    status(tmp_path, NOW)
    assert (tmp_path / "hourly-state.json").read_bytes() == before


def test_reboot_uses_pinned_passive_grok_entrypoint():
    definition = json.loads((REBOOT / "bridge-code-files.json").read_text())
    assert definition["python_entrypoints"]["grok_helper"] == "tools/wd_grok_helper.py"
    assert "tools/wd_grok_helper.py" in definition["python_files"]
    assert "Initialize-WdGrokRecovery.ps1" in (REBOOT / "start-wd-all.ps1").read_text()
    assert "Invoke-WdGrok.ps1 -Status" in (REBOOT / "start-wd-agent.ps1").read_text()
    startup = (REBOOT / "start-wd-agent.ps1").read_text()
    grok_prompt = startup.split("' Grok is an on-demand", 1)[1].split("' Shared capacity status:", 1)[0]
    assert "per 60 minutes" not in grok_prompt
    assert "Only the lead requests" not in grok_prompt
    assert "no artificial local hour, week or per-agent quota" in grok_prompt
    assert "Only explicit caller evidence" in grok_prompt
    wrapper = (REBOOT / "Invoke-WdGrok.ps1").read_text()
    assert "WD_REBOOT_EXPECTED_MANIFEST_HASH" in wrapper
    assert "-VerifyPackage" in wrapper


@pytest.mark.skipif(PS is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", sorted({p for p in (PS, shutil.which("pwsh")) if p}))
def test_passive_recovery_preserves_budget_and_legacy_history(tmp_path, shell):
    # All machine paths and OS probes are isolated; no real task or model call.
    machine = tmp_path / "machine"
    reports = machine / "grok-scout-reports"
    reports.mkdir(parents=True)
    legacy = machine / "Update-GrokWorktree.ps1"
    legacy.write_text("old worktree updater", encoding="utf-8")
    report = reports / "grok-old-result.md"
    report.write_text("Previous result", encoding="utf-8")
    script = tmp_path / "recovery.ps1"
    source = (REBOOT / "Initialize-WdGrokRecovery.ps1").read_text()
    script.write_text(source.replace("C:\\Python", str(machine)), encoding="utf-8")
    command = f"""
    $ErrorActionPreference = 'Stop'
    function Get-ScheduledTask {{ @() }}
    function Get-CimInstance {{ @() }}
    & '{script}' -Apply | Out-Null
    $before = [IO.File]::ReadAllText('{reports / 'hourly-state.json'}')
    & '{script}' | ConvertTo-Json -Compress
    if ([IO.File]::ReadAllText('{reports / 'hourly-state.json'}') -cne $before) {{ throw 'Budget reset' }}
    """
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads((reports / "hourly-state.json").read_text())
    assert state["status"] == "initialized_conservative_cooldown"
    recovery = json.loads(result.stdout.strip().splitlines()[-1])
    assert "next_eligible_utc" not in recovery  # no artificial cooldown
    assert (recovery["provider_auth"], recovery["provider_quota"]) == ("unknown", "unknown")
    assert recovery["unfinished_attempt"].startswith("unknown")
    assert datetime.fromisoformat(recovery["last_attempt_utc"]) == (
        datetime.fromisoformat(state["last_attempt_utc"])
    )
    assert Path(state["previous_report"]) == report
    assert status(reports)["local_availability"] == "available"   # the helper imposes no recovery hour
    assert report.read_text() == "Previous result"
    assert "throw 'Use" in legacy.read_text()
    assert any(p.read_text() == "old worktree updater" for p in
               (machine / "wd-reboot-backups").rglob("Update-GrokWorktree.ps1"))


@pytest.mark.skipif(PS is None, reason="PowerShell unavailable")
def test_active_grok_blocks_migration_without_mutation(tmp_path):
    script = tmp_path / "recovery.ps1"
    source = (REBOOT / "Initialize-WdGrokRecovery.ps1").read_text()
    machine = tmp_path / "absent-machine"
    script.write_text(source.replace("C:\\Python", str(machine)), encoding="utf-8")
    command = f"""
    $ErrorActionPreference = 'Stop'
    function Get-ScheduledTask {{ @() }}
    function Get-CimInstance {{ [pscustomobject]@{{ Name='grok.exe' }} }}
    & '{script}' -Apply
    """
    result = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "invocation is active" in result.stderr
    assert not machine.exists()


# ---------------------------------------------------------------------------
# Deferral metadata and truthful wrapper exit codes (authored per the operator
# no-runs directive 2026-09-29; NOT executed by the author).
# ---------------------------------------------------------------------------

def _reserved(root, age, timeout_seconds=300):
    write_state(root, {"schema": SCHEMA, "status": "reserved", "task_id": "previous/task",
                       "request_id": "previous-request", "timeout_seconds": timeout_seconds,
                       "last_attempt_utc": (NOW - timedelta(seconds=age)).isoformat()})


@pytest.mark.parametrize("age", [10, 5000])
def test_status_of_an_unfinished_attempt_has_no_next_eligible_time(tmp_path, age):
    _reserved(tmp_path, age)
    report = status(tmp_path, NOW)
    assert report["next_eligible_utc"] is None and report["eligible"] is False
    assert report["local_availability"] == "unreconciled_attempt" and report["provider_quota"] == "unknown"
    assert not any(key.startswith("hourly_budget") for key in report)
    assert report["status"] == ("reserved" if age < 300 else "interrupted_or_unknown")


def test_unreconciled_deferral_has_no_next_eligible_time_and_mints_no_request_id(tmp_path):
    import re
    _reserved(tmp_path, 5000)
    before = (tmp_path / "hourly-state.json").read_bytes()
    events = []
    report = consult(tmp_path, "brainstorm/r2", "ask", ["fake"], now=NOW,
                     runner=lambda *a, **k: pytest.fail("unfinished attempt bypass"),
                     emitter=lambda stage, event: events.append((stage, event)))
    assert report["decision"] == "deferred_unreconciled_attempt" and report["status"] == "deferred"
    assert report["next_eligible_utc"] is None and report["local_availability"] == "unreconciled_attempt"
    assert report["request_id"] is None and re.fullmatch(r"[0-9a-f]{32}", report["observation_id"])
    (stage, event), = events
    assert stage == "deferred" and event["request_id"] is None
    assert event["observation_id"] == report["observation_id"]
    assert event["next_eligible_utc"] is None and event["local_availability"] == "unreconciled_attempt"
    assert (tmp_path / "hourly-state.json").read_bytes() == before
    assert wd_grok_helper.consultation_exit_code(report) == 2


def test_a_clock_regression_defers_without_a_launch_a_state_change_or_a_request_id(tmp_path):
    seed(tmp_path, age=-1)
    before = (tmp_path / "hourly-state.json").read_bytes()
    events = []
    report = consult(tmp_path, "later/task", "ask", ["fake"], now=NOW,
                     runner=lambda *a, **k: pytest.fail("clock regression launched"),
                     emitter=lambda stage, event: events.append((stage, event)))
    assert report["decision"] == "deferred_clock_regression" and report["local_availability"] == "clock_regressed"
    assert report["next_eligible_utc"] == (NOW + timedelta(seconds=1)).isoformat() and report["request_id"] is None
    (stage, event), = events
    assert event["request_id"] is None and event["observation_id"] == report["observation_id"]
    assert event["status"] == "deferred_clock_regression" and event["provider_quota"] == "unknown"
    assert (tmp_path / "hourly-state.json").read_bytes() == before
    assert wd_grok_helper.consultation_exit_code(report) == 2


def test_lifecycle_writer_separates_deferral_observations_from_consultations():
    # Static guard: the lifecycle branch needs the installed bundle and cannot run here.
    wrapper = (REBOOT / "Invoke-WdGrok.ps1").read_text()
    assert "'grok-deferral-' + $observationId" in wrapper and "'grok-consult-' + $requestId" in wrapper
    assert "Invalid Grok deferral observation" in wrapper and "Invalid Grok consultation id" in wrapper
    assert "$consultationId=$null" in wrapper and "'local_availability','provider_quota'" in wrapper


@pytest.mark.skipif(PS is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", sorted({p for p in (PS, shutil.which("pwsh")) if p}))
@pytest.mark.parametrize("code", [0, 1, 2])
def test_wrapper_exit_code_is_the_python_result_and_capture_is_kept(tmp_path, shell, code):
    import hashlib
    import os
    script = tmp_path / "Invoke-WdGrok.ps1"
    shutil.copyfile(REBOOT / script.name, script)
    stub = tmp_path / "Invoke-WdBridgePython.ps1"
    stub.write_text('param([string]$Tool,[switch]$VerifyPackage)\n'
                    "'{\"status\":\"stub\"}'\n"
                    f"$global:LASTEXITCODE = {code}\n")
    manifest = tmp_path / "deployment-manifest.json"
    manifest.write_text(json.dumps({"source_commit": "fixture", "files": {
        stub.name: hashlib.sha256(stub.read_bytes()).hexdigest().upper()}}))
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    env["WD_REBOOT_EXPECTED_MANIFEST_HASH"] = hashlib.sha256(manifest.read_bytes()).hexdigest().upper()
    # A top-level -File run exits with the Python code (it used to be 0 even after a failure).
    top = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(script), "-Status"],
                         capture_output=True, text=True, timeout=30, env=env)
    assert top.returncode == code, top.stdout + top.stderr
    assert '"status":"stub"' in top.stdout
    # An in-process caller keeps its output capture and reads the code from $LASTEXITCODE.
    captured = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command",
                               f"$out = & '{script}' -Status; if (-not ($out -match 'stub')) {{ exit 99 }}; "
                               "exit $LASTEXITCODE"],
                              capture_output=True, text=True, timeout=30, env=env)
    assert captured.returncode == code, captured.stdout + captured.stderr


# ---------------------------------------------------------------------------
# Caller-form twins, lifecycle identifiers and canonical-receipt refusal
# (Tools cffff481 via Lead adb5f93a; authored under the operator no-runs
# directive, NOT executed by the author). Every fixture uses an isolated copy of
# the wrapper with STUB packages: a stub Python wrapper, stub bridge context and a
# stub writer. The production emitter, the real bridge log and Grok are never reached.
# ---------------------------------------------------------------------------

SHELLS = sorted({p for p in (PS, shutil.which("pwsh")) if p})
OBSERVATION = "0123456789abcdef" * 2


def _ps_env(manifest):
    import hashlib
    import os
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    env["WD_REBOOT_EXPECTED_MANIFEST_HASH"] = hashlib.sha256(manifest.read_bytes()).hexdigest().upper()
    return env


def _run_ps(shell, *args, env):
    import re
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", *args],
                            capture_output=True, text=True, timeout=30, env=env)
    return result.returncode, result.stdout, re.sub(r"\x1b\[[0-9;]*m", "", result.stderr)


def _exit_bundle(tmp_path, code):
    """The wrapper plus a stub Python wrapper that publishes ``code`` (None: it publishes nothing)."""
    import hashlib
    script = tmp_path / "Invoke-WdGrok.ps1"
    shutil.copyfile(REBOOT / script.name, script)
    stub = tmp_path / "Invoke-WdBridgePython.ps1"
    stub.write_text('param([string]$Tool,[switch]$VerifyPackage)\n'
                    "'{\"status\":\"stub\"}'\n"
                    + ("" if code is None else f"$global:LASTEXITCODE = {code}\n"))
    manifest = tmp_path / "deployment-manifest.json"
    manifest.write_text(json.dumps({"source_commit": "fixture", "files": {
        stub.name: hashlib.sha256(stub.read_bytes()).hexdigest().upper()}}))
    return script, _ps_env(manifest)


@pytest.mark.skipif(PS is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("code", [0, 1, 2])
def test_a_dot_source_never_exits_its_caller_and_leaves_the_code(tmp_path, shell, code):
    script, env = _exit_bundle(tmp_path, code)
    rc, out, err = _run_ps(shell, "-Command",
                           f"$out = . '{script}' -Status; if (-not ($out -match 'stub')) {{ exit 99 }}; "
                           "'caller-continued'; exit $LASTEXITCODE", env=env)
    assert rc == code and "caller-continued" in out, out + err


@pytest.mark.skipif(PS is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("code", [0, 1, 2])
@pytest.mark.parametrize("call", ["&", "."])
def test_a_distinct_outer_file_target_keeps_running_and_capturing(tmp_path, shell, code, call):
    # The process's -File target is ANOTHER script: the wrapper must not exit it. Without the
    # marker line, an early exit with the same code would be indistinguishable.
    script, env = _exit_bundle(tmp_path, code)
    outer = tmp_path / "outer.ps1"
    outer.write_text(f"$out = {call} '{script}' -Status\n"
                     "if (-not ($out -match 'stub')) { exit 99 }\n"
                     "'outer-continued'\n"
                     "exit $LASTEXITCODE\n")
    rc, out, err = _run_ps(shell, "-File", str(outer), env=env)
    assert rc == code and "outer-continued" in out, out + err


@pytest.mark.skipif(PS is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", SHELLS)
def test_a_python_wrapper_that_publishes_no_code_is_a_failure(tmp_path, shell):
    script, env = _exit_bundle(tmp_path, None)
    rc, out, err = _run_ps(shell, "-File", str(script), "-Status", env=env)
    assert rc == 1 and '"status":"stub"' in out, out + err
    # A caller's earlier 0 is not inherited: the wrapper sets 1 before the call.
    rc, out, err = _run_ps(shell, "-Command",
                           f"$global:LASTEXITCODE = 0; $out = & '{script}' -Status; exit $LASTEXITCODE", env=env)
    assert rc == 1, out + err


def test_the_readme_separates_supported_file_from_unverified_positional_exit_codes():
    text = (REBOOT / "GROK-READONLY.md").read_text(encoding="utf-8")
    assert "SUPPORTED: a run where this script is the process's own explicit, top-level" in text
    assert "UNVERIFIED, not a runtime guarantee: a positional invocation" in text


def _lifecycle_bundle(tmp_path):
    """A trusted TEST package around the real wrapper: stub bridge context, fleet file and writer."""
    import hashlib
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    script = bundle / "Invoke-WdGrok.ps1"
    shutil.copyfile(REBOOT / script.name, script)
    (bundle / "BridgeCodeContext.ps1").write_text(
        "function Get-WdBridgeCodePackageDefinition { param([string]$Path)\n"
        "    [pscustomobject]@{ Hash = (Get-FileHash -LiteralPath $Path).Hash; Definition = $null } }\n"
        "function Assert-WdBridgeCodePackageIntegrity { param($BundleRoot, $Deployment, $Definition) $null }\n")
    (bundle / "bridge-code-files.json").write_text("{}")
    (bundle / "wd-fleet.json").write_text(json.dumps({"runtime_root": str(tmp_path / "runtime")}))
    writer = bundle / "tools-bootstrap/.agent-bridge/bin/Write-AgentEvent.ps1"
    writer.parent.mkdir(parents=True)
    writer.write_text(   # records what it was asked to write; it never touches a bridge log
        "param([string]$Agent,[string]$Type,[string]$Status,[string]$TaskId,[string]$Message,[string]$To,\n"
        "    [string]$Role,[string]$AgentUuid,[string]$SessionId,[string]$RunId,[string[]]$Capabilities,\n"
        "    [string]$PayloadJson,[switch]$ReceiptJson)\n"
        "[pscustomobject]@{agent=$Agent;status=$Status;to=$To;task=$TaskId;session=$SessionId;run=$RunId;\n"
        "    receipt=[bool]$ReceiptJson;payload=($PayloadJson | ConvertFrom-Json)} | ConvertTo-Json -Depth 8 -Compress\n")
    files = {name: hashlib.sha256((bundle / name).read_bytes()).hexdigest().upper()
             for name in ("BridgeCodeContext.ps1", "bridge-code-files.json", "wd-fleet.json")}
    manifest = bundle / "deployment-manifest.json"
    manifest.write_text(json.dumps({"source_commit": "fixture", "files": files}))
    return script, _ps_env(manifest)


def _lifecycle(shell, tmp_path, stage, state, task_id="lifecycle/task"):
    import base64
    script, env = _lifecycle_bundle(tmp_path)
    encoded = base64.b64encode(json.dumps({"stage": stage, "state": dict(state, task_id=task_id)})
                               .encode()).decode("ascii")
    return _run_ps(shell, "-File", str(script), "-LifecycleBase64", encoded, env=env)


@pytest.mark.skipif(PS is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("stage,state,error", [
    ("deferred", {}, "Invalid Grok deferral observation"),                                   # missing
    ("deferred", {"observation_id": ""}, "Invalid Grok deferral observation"),               # missing (empty)
    ("deferred", {"request_id": OBSERVATION}, "Invalid Grok deferral observation"),          # wrong: a consultation id
    ("deferred", {"observation_id": OBSERVATION, "request_id": OBSERVATION},
     "Invalid Grok deferral observation"),                                                   # mixed
    ("deferred", {"observation_id": OBSERVATION.upper()}, "Invalid Grok deferral observation"),   # wrong: case
    ("deferred", {"observation_id": OBSERVATION[:31]}, "Invalid Grok deferral observation"),      # wrong: length
    ("started", {}, "Invalid Grok consultation id"),                                         # missing
    ("answered", {"observation_id": OBSERVATION}, "Invalid Grok consultation id"),           # wrong: an observation
    ("answered", {"request_id": OBSERVATION, "observation_id": OBSERVATION},
     "Invalid Grok consultation id"),                                                        # mixed
    ("failed", {"request_id": OBSERVATION + "0"}, "Invalid Grok consultation id"),           # wrong: length
    ("failed", {"request_id": "g" * 32}, "Invalid Grok consultation id"),                    # wrong: not hex
    # G1: .NET $ also matches before a final newline; the checks end with \z.
    ("deferred", {"observation_id": OBSERVATION + "\n"}, "Invalid Grok deferral observation"),
    ("answered", {"request_id": OBSERVATION + "\n"}, "Invalid Grok consultation id"),
    ("started", {"request_id": OBSERVATION + "\r\n"}, "Invalid Grok consultation id"),
])
def test_lifecycle_refuses_wrong_missing_or_mixed_identifiers(tmp_path, shell, stage, state, error):
    rc, out, err = _lifecycle(shell, tmp_path, stage, state)
    assert rc != 0 and error in err, out + err
    assert '"session"' not in out                                    # the stub writer was never reached


@pytest.mark.skipif(PS is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("task_id", ["lifecycle/task\n", "\nlifecycle/task", "", "-dash-first", "x" * 161])
def test_lifecycle_refuses_a_task_with_a_final_newline_or_a_bad_form(tmp_path, shell, task_id):
    rc, out, err = _lifecycle(shell, tmp_path, "deferred", {"observation_id": OBSERVATION}, task_id=task_id)
    assert rc != 0 and "Invalid consultation task" in err, out + err
    assert '"session"' not in out


@pytest.mark.skipif(PS is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("task_id", ["a", "A.b_c-d/e", "x" * 160])
def test_lifecycle_keeps_every_allowed_task_form(tmp_path, shell, task_id):
    rc, out, err = _lifecycle(shell, tmp_path, "deferred", {"observation_id": OBSERVATION}, task_id=task_id)
    assert rc == 0 and json.loads(out)["task"] == task_id, out + err


def test_every_full_match_check_in_the_wrapper_ends_with_the_absolute_anchor():
    import re
    wrapper = (REBOOT / "Invoke-WdGrok.ps1").read_text(encoding="utf-8")
    checks = re.findall(r"-cnotmatch '(\^[^']*)'", wrapper)
    assert len(checks) >= 6 and all(check.endswith("\\z") for check in checks), checks


@pytest.mark.skipif(PS is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("stage,state,session,consultation,recipient", [
    ("deferred", {"observation_id": OBSERVATION, "request_id": None}, "grok-deferral-" + OBSERVATION, None,
     "operator"),
    ("started", {"request_id": OBSERVATION}, "grok-consult-" + OBSERVATION, OBSERVATION, "operator"),
    ("answered", {"request_id": OBSERVATION, "observation_id": None}, "grok-consult-" + OBSERVATION,
     OBSERVATION, "codex-lead-1"),
])
def test_lifecycle_binds_each_identifier_to_its_own_session(tmp_path, shell, stage, state, session,
                                                           consultation, recipient):
    rc, out, err = _lifecycle(shell, tmp_path, stage, state)
    assert rc == 0, out + err
    written = json.loads(out)
    assert written["agent"] == "grok-scout-1" and written["status"] == "consultation_" + stage
    assert written["session"] == written["run"] == session and written["receipt"] is True
    assert written["to"] == recipient and written["task"] == "lifecycle/task"
    assert written["payload"]["consultation_id"] == consultation
    assert written["payload"].get("observation_id") == (OBSERVATION if stage == "deferred" else None)
    assert ("observation_id" in written["payload"]) is (stage == "deferred")
    assert written["payload"]["authority_effect"] == "none" and written["payload"]["advisory_only"] is True


DELIVERED = {"_bridge_delivery": {"accepted": True, "canonical_durable": True}}
DEFERRAL = {"task_id": "test", "request_id": None, "observation_id": OBSERVATION}


def _stub_writer_process(tmp_path, monkeypatch, returncode, stdout):
    """emit_bridge_event against a STUB process: no PowerShell, no writer and no model ever start."""
    bundle = tmp_path / "bundle"
    packaged = bundle / "tools-bootstrap/tools/wd_grok_helper.py"
    packaged.parent.mkdir(parents=True)
    (bundle / "Invoke-WdGrok.ps1").write_text("# pinned wrapper fixture")
    monkeypatch.setattr(wd_grok_helper, "__file__", str(packaged))
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert command[-2] == "-LifecycleBase64" and "--prompt-file" not in command
        assert kwargs["encoding"] == "utf-8" and kwargs["errors"] == "strict"
        return SimpleNamespace(returncode=returncode, stdout=stdout)
    monkeypatch.setattr(wd_grok_helper.subprocess, "run", run)
    return calls


def _delivery(**receipt):
    return json.dumps({"_bridge_delivery": receipt})


@pytest.mark.parametrize("returncode,stdout", [
    (1, json.dumps(DELIVERED)),                                                  # the writer failed
    (0, _delivery(accepted=False, canonical_durable=True)),
    (0, _delivery(accepted=True)),                                               # no durability claim
    (0, _delivery(accepted=True, canonical_durable=None)),
    (0, _delivery()),
    (0, json.dumps({"receipt": DELIVERED["_bridge_delivery"]})),                 # not the delivery key
    # G2: only the literal JSON true counts, and every malformed shape is a visible OSError.
    (0, _delivery(accepted=True, canonical_durable="false")),                    # a truthy string
    (0, _delivery(accepted=True, canonical_durable="true")),
    (0, _delivery(accepted=True, canonical_durable=1)),                          # a truthy number
    (0, _delivery(accepted=1, canonical_durable=True)),
    (0, _delivery(accepted="yes", canonical_durable=True)),
    (0, json.dumps({"_bridge_delivery": [True, True]})),                         # delivery not an object
    (0, json.dumps({"_bridge_delivery": "accepted canonical_durable"})),
    (0, json.dumps([DELIVERED])),                                                # receipt not an object
    (0, json.dumps("accepted")),
    (0, json.dumps(1)),
    (0, "null"),
    (0, "not json"),
    (0, ""),
    (0, None),                                                                   # no stdout at all
    pytest.param(0, "[" * 100000 + "]" * 100000, id="nesting-beyond-parser"),
    (0, '{"_bridge_delivery":{"accepted":false,"accepted":true,"canonical_durable":true}}'),
    (0, '{"_bridge_delivery":{"accepted":true,"canonical_durable":true},"extra":NaN}'),
    (0, '{"_bridge_delivery":{"accepted":true,"canonical_durable":true},"extra":Infinity}'),
    (0, '{"_bridge_delivery":{"accepted":true,"canonical_durable":true},"extra":1e999}'),
    (0, json.dumps({**DELIVERED, "extra": chr(0xD800)})),                           # escaped lone surrogate value
    (0, json.dumps({**DELIVERED, chr(0xDC00): None})),                             # escaped lone surrogate key
    (0, chr(0xD800) + json.dumps(DELIVERED)),                                     # invalid Unicode before parsing
    (0, chr(0xFEFF) * 2 + json.dumps(DELIVERED)),                                 # not one optional BOM
    pytest.param(0, json.dumps({**DELIVERED, "extra": "x" * wd_grok_helper.MAX_LIFECYCLE_RECEIPT_BYTES}),
                 id="receipt-over-byte-limit"),
    pytest.param(0, '{"_bridge_delivery":{"accepted":true,"canonical_durable":true},"extra":'
                 + '[' * wd_grok_helper.MAX_LIFECYCLE_RECEIPT_DEPTH + '0'
                 + ']' * wd_grok_helper.MAX_LIFECYCLE_RECEIPT_DEPTH + '}', id="receipt-over-depth-limit"),
])
def test_lifecycle_receipt_refusals_are_one_visible_os_error(tmp_path, monkeypatch, returncode, stdout):
    calls = _stub_writer_process(tmp_path, monkeypatch, returncode, stdout)
    with pytest.raises(OSError, match="writer failed|not confirmed canonical"):
        wd_grok_helper.emit_bridge_event("deferred", dict(DEFERRAL))
    assert len(calls) == 1                                                       # one attempt, no retry


@pytest.mark.parametrize("stdout", [
    chr(0xFEFF) + json.dumps(DELIVERED),                                         # a BOM prefix
    json.dumps({"schema": "wd.bridge-event", "_bridge_delivery": {               # the writer's real receipt shape
        "schema": "waggledance.bridge.delivery-receipt.v1", "accepted": True, "delivery_status": "canonical",
        "canonical_durable": True, "checkpoint_advanced": True, "wal_id": None, "warning_messages": []}}),
])
def test_a_canonical_receipt_is_confirmed_and_carries_the_exact_event(tmp_path, monkeypatch, stdout):
    import base64
    calls = _stub_writer_process(tmp_path, monkeypatch, 0, stdout)
    assert wd_grok_helper.emit_bridge_event("deferred", dict(DEFERRAL)) is None  # success twins
    (command,) = calls
    assert json.loads(base64.b64decode(command[-1])) == {"stage": "deferred", "state": DEFERRAL}
    assert command[command.index("-File") + 1].endswith("Invoke-WdGrok.ps1")


def test_lifecycle_utf8_decode_failure_is_one_visible_os_error(tmp_path, monkeypatch):
    calls = _stub_writer_process(tmp_path, monkeypatch, 0, "")

    def invalid_decode(command, **kwargs):
        calls.append(command)
        assert kwargs["errors"] == "strict"
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(wd_grok_helper.subprocess, "run", invalid_decode)
    with pytest.raises(OSError, match="not confirmed canonical: invalid UTF-8"):
        wd_grok_helper.emit_bridge_event("deferred", dict(DEFERRAL))
    assert len(calls) == 1


def test_lifecycle_receipt_acceptance_bounds_have_success_twins():
    raw = json.dumps(DELIVERED)
    limit = wd_grok_helper.MAX_LIFECYCLE_RECEIPT_BYTES
    # ASCII padding is valid JSON whitespace; the byte limit is inclusive.
    wd_grok_helper._confirm_canonical_receipt(raw + " " * (limit - len(raw)))
    with pytest.raises(OSError, match="not confirmed canonical"):
        wd_grok_helper._confirm_canonical_receipt(raw + " " * (limit - len(raw) + 1))
    depth = wd_grok_helper.MAX_LIFECYCLE_RECEIPT_DEPTH - 1
    wd_grok_helper._confirm_canonical_receipt(
        '{"_bridge_delivery":{"accepted":true,"canonical_durable":true},"extra":'
        + '[' * depth + '0' + ']' * depth + '}')
    wd_grok_helper._confirm_canonical_receipt(json.dumps({**DELIVERED, "extra": "🦉"}))


@pytest.mark.parametrize("stdout", [
    _delivery(accepted=True),
    _delivery(accepted=True, canonical_durable="true"),
    '{"_bridge_delivery":{"accepted":false,"accepted":true,"canonical_durable":true}}',
    json.dumps({**DELIVERED, "extra": chr(0xD800)}),
    '{"_bridge_delivery":{"accepted":true,"canonical_durable":true},"extra":NaN}',
])
def test_a_refused_receipt_is_recorded_and_never_refunds_or_retries(tmp_path, monkeypatch, stdout):
    calls = _stub_writer_process(tmp_path, monkeypatch, 0, stdout)
    _reserved(tmp_path, 10)
    before = (tmp_path / "hourly-state.json").read_bytes()
    report = consult(tmp_path, "later/task", "ask", ["fake"], now=NOW,
                     runner=lambda *a, **k: pytest.fail("budget bypass"),
                     emitter=wd_grok_helper.emit_bridge_event)
    assert report["decision"] == "deferred_unreconciled_attempt" and len(calls) == 1
    assert [e["error_type"] for e in report["bridge_event_errors"]] == ["OSError"]
    assert (tmp_path / "hourly-state.json").read_bytes() == before


def test_status_keeps_a_legacy_state_and_reports_provider_evidence_verbatim(tmp_path):
    legacy = {"schema": SCHEMA, "last_attempt_utc": (NOW - timedelta(seconds=5)).isoformat(),
              "status": "initialized_conservative_cooldown", "previous_report": "old.md"}
    write_state(tmp_path, legacy)
    report = status(tmp_path, NOW)
    assert {k: report[k] for k in legacy} == legacy and report["local_availability"] == "available"
    failed = dict(legacy, status="failed", exit_code=3, stderr_excerpt="provider: rate limited", stderr_truncated=False)
    write_state(tmp_path, failed)
    report = status(tmp_path, NOW)
    assert report["provider_evidence"] == {"exit_code": 3, "stderr_excerpt": "provider: rate limited",
                                           "stderr_truncated": False}
    assert report["provider_quota"] == "unknown" and report["eligible"] is True        # never read as a quota
