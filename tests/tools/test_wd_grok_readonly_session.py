"""Grok read-only session controller (5246ce10): Bridge-only fake-port fixtures (AUTHORED, NOT RUN).

Every model process is a scripted fake, the broker is a fake and every path is a temporary directory:
nothing here launches Grok, inspects a provider or account, or reads or changes the real user profile
(the inventory's home list is monkeypatched to a temp home and GROK_HOME is unset). An acknowledged
surface is NOT isolation: read_only_guarantee stays False everywhere.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys

import pytest

from tools import wd_grok_readonly_session as session

REQUEST_ID = "a" * 32
LIST = json.dumps({"op": "list_dir", "path": "tools"})  # the controller's own documented action shape
FINAL = json.dumps({"op": "final", "text": "No blocker."})


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """An isolated temp home and working directory; the real profile is never inventoried."""
    home, cwd = tmp_path / "home", tmp_path / "cwd"
    home.mkdir()
    cwd.mkdir()
    monkeypatch.setattr(session, "_homes", lambda: [home])
    monkeypatch.delenv("GROK_HOME", raising=False)
    return home, cwd


def _hooks(home, event="PreToolUse"):
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"hooks": {event: [{"matcher": "*", "hooks": []}]}}), encoding="utf-8")


def _state(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return path


def _consult_argv(state):
    """Exactly the argv wd_grok_helper.consult builds, with a real helper request file."""
    prompt = state / (REQUEST_ID + "-request.md")
    prompt.write_text("Review the gate. COMPLETE, no tools.", encoding="utf-8")
    return ["grok", "--model", "grok-4", "--effort", "high", "--prompt-file", str(prompt), "--verbatim",
            "--no-alt-screen", "--no-subagents", "--max-turns", "1", "--tools", "", "--deny", "*",
            "--permission-mode", "plan", "--disable-web-search", "--no-memory", "--output-format", "json"]


def _replace(argv, option, value):
    changed = list(argv)
    changed[changed.index(option) + 1] = value
    return changed


def _never(*args, **kwargs):
    raise AssertionError("must never be reached: the refusal comes first")


class Model:
    """A scripted model_runner: one prepared (text, sessionId) reply per round; records every argv; never launches."""

    def __init__(self, replies, on_call=None):
        self.replies, self.on_call, self.calls = list(replies), on_call, []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        if self.on_call is not None:
            self.on_call(len(self.calls))
        text, session_id = self.replies[len(self.calls) - 1]
        body = json.dumps({"text": text, "stopReason": "EndTurn", "sessionId": session_id}).encode("utf-8")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout=body, stderr=b"")


class Broker:
    def __init__(self):
        self.actions = []

    def dispatch(self, action):
        self.actions.append(action)
        return {"entries": []}


def _session(state, cwd, model, ack=None):
    surface = session.surface_gate(cwd, ack)
    runner = session.ReadonlySessionRunner(Broker(), session.BusyClock(), "c" * 40, surface, max_rounds=3,
                                           acknowledged=ack, model_runner=model)
    result = runner(_consult_argv(state), timeout=900, env=None, cwd=str(cwd))
    summary = json.loads(result.stdout.split("READONLY SESSION SUMMARY\n", 1)[1])
    return result, summary


def test_an_unreadable_or_unacknowledged_surface_refuses_and_an_ack_is_never_isolation(iso):
    home, cwd = iso
    empty = session.surface_gate(cwd, None)  # the success twin: nothing inherited
    assert (empty["components"], empty["isolation"], empty["read_only_guarantee"]) == (
        0, "no_inherited_components_found_static", False)
    _hooks(home)
    digest = session.inherited_surface(cwd)["digest"]
    for ack in (None, "0" * 64, digest[:-1]):
        with pytest.raises(ValueError, match="Refusing without an exact digest acknowledgement"):
            session.surface_gate(cwd, ack)
    acked = session.surface_gate(cwd, digest.upper())  # the exact digest (compared case-insensitively)
    assert (acked["isolation"], acked["read_only_guarantee"]) == (
        "inherited_components_acknowledged_not_isolated", False)
    (home / ".claude" / "settings.json").write_text("{", encoding="utf-8")
    broken = session.inherited_surface(cwd)
    assert broken["problems"]
    with pytest.raises(ValueError, match="unreadable sources"):
        session.surface_gate(cwd, broken["digest"])  # no acknowledgement accepts an unreadable source


@pytest.mark.parametrize("setup, message", [
    (lambda home: _hooks(home), "Refusing without an exact digest acknowledgement"),
    (lambda home: (_hooks(home), (home / ".claude" / "settings.json").write_text("{", encoding="utf-8")),
     "unreadable sources"),
], ids=["unacknowledged", "unreadable"])
def test_the_entry_point_refuses_the_surface_before_any_reservation(iso, monkeypatch, capsys, setup, message):
    home, cwd = iso
    setup(home)
    monkeypatch.setattr(session.helper, "STATE_ROOT", cwd)
    monkeypatch.setattr(session.helper, "consult", _never)  # the reservation happens inside consult
    monkeypatch.setattr(session.helper, "GitBlobBroker", _never)
    monkeypatch.setattr(sys, "argv", ["wd_grok_readonly_session.py", "--task-id", "codex-lead-1/x",
                                      "--prompt-file", str(cwd / "p.md"), "--repo", str(cwd), "--commit", "c" * 40,
                                      "--git-executable", str(cwd / "git.exe")])
    assert session.main() == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "blocked" and message in out["error"]
    assert list(cwd.iterdir()) == []  # nothing reserved or written


def test_a_surface_that_changes_between_rounds_stops_before_the_next_model_process(iso, tmp_path):
    home, cwd = iso
    _hooks(home)
    ack = session.inherited_surface(cwd)["digest"]
    state = _state(tmp_path, "changed")
    model = Model([(LIST, "sess-1"), (FINAL, "sess-1")],
                  on_call=lambda n: _hooks(home, "PostToolUse") if n == 1 else None)  # installed mid-session
    result, summary = _session(state, cwd, model, ack)
    assert result.returncode == 1 and len(model.calls) == 1  # round 2 never launched
    assert summary["outcome"].startswith("failed:ValueError:") and summary["read_only_guarantee"] is False
    records = [json.loads(line) for line in (state / (REQUEST_ID + "-rounds.jsonl")).read_text().splitlines()]
    assert [r["outcome"].split(":")[0] for r in records] == ["continued", "failed"]
    assert "returncode" not in records[1]  # refused before the model process
    _hooks(home)  # restore the exact acknowledged baseline before the unchanged success twin
    twin = Model([(LIST, "sess-1"), (FINAL, "sess-1")])  # the unchanged twin completes
    result, summary = _session(_state(tmp_path, "unchanged"), cwd, twin, ack)
    assert (result.returncode, summary["outcome"], len(twin.calls)) == (0, "final", 2)


def test_only_the_exact_no_tools_argv_is_accepted_and_every_round_denies_native_tools(iso, tmp_path):
    home, cwd = iso
    state = _state(tmp_path, "argv")
    argv = _consult_argv(state)
    assert session.validate_consult_argv(argv)["request_id"] == REQUEST_ID  # the success twin
    for bad, message in ((_replace(argv, "--tools", "read_file"), "mandatory no-native-tools"),
                         (_replace(argv, "--deny", "none"), "mandatory no-native-tools"),
                         (argv + ["--allow-tools"], "Unexpected Grok argument"),
                         ([a for a in argv if a != "--no-memory"], "mandatory no-native-tools"),
                         # F4: consult asks for JSON output; another format or none is not its argv
                         (_replace(argv, "--output-format", "text"), "mandatory no-native-tools"),
                         (argv[:-2], "mandatory no-native-tools"),
                         (argv + ["--output-format", "json"], "Unexpected Grok argument")):
        with pytest.raises(ValueError, match=message):
            session.validate_consult_argv(bad)
    model = Model([(LIST, "sess-1"), (FINAL, "sess-1")])
    result, summary = _session(state, cwd, model)
    assert (result.returncode, summary["outcome"], summary["read_only_guarantee"]) == (0, "final", False)
    for command in model.calls:
        assert command[command.index("--tools") + 1] == "" and command[command.index("--deny") + 1] == "*"
        assert command[command.index("--max-turns") + 1] == "1" and "--output-format" in command
    assert "--resume" not in model.calls[0] and model.calls[1][-2:] == ["--resume", "sess-1"]


def test_a_resumed_round_with_another_session_id_is_refused(iso, tmp_path):
    home, cwd = iso
    model = Model([(LIST, "sess-1"), (FINAL, "sess-2")])
    result, summary = _session(_state(tmp_path, "resume"), cwd, model)
    assert result.returncode == 1 and len(model.calls) == 2
    assert summary["outcome"] == "failed:ValueError:Resumed Grok session id changed"
    assert summary["session_id"] == "sess-1" and summary["read_only_guarantee"] is False
    assert "READ-ONLY SESSION FAILED" in result.stdout


@pytest.mark.parametrize("arguments", [
    ["--inventory", "--requested-by", "fable-5"],
    ["--inventory", "--requested-by", "Claude-RCO-2"],
    ["--requested-by", "operator"],
])
def test_requester_refusal_precedes_inventory_surface_and_broker(iso, monkeypatch, capsys, arguments):
    _, cwd = iso
    monkeypatch.setattr(session.helper, "STATE_ROOT", cwd)
    monkeypatch.setattr(session, "inherited_surface", _never)
    monkeypatch.setattr(session, "surface_gate", _never)
    monkeypatch.setattr(session.helper, "GitBlobBroker", _never)
    monkeypatch.setattr(session.helper, "consult", _never)
    monkeypatch.setattr(sys, "argv", ["wd_grok_readonly_session.py", *arguments])
    assert session.main() == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "blocked" and "requester" in out["error"]
    assert list(cwd.iterdir()) == []


@pytest.mark.parametrize("requester", (None, "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"))
def test_every_lane_runs_read_only_rounds_at_high_effort_inside_the_unchanged_limits(tmp_path, requester):
    # Mock, not live: the real helper.consult reserves, writes the ledger and calls this runner, which
    # does what ReadonlySessionRunner does first (validate the consult argv, build round argv) and never
    # launches Grok. main() itself is not run here; it passes ROUND_EFFORT to advisory_command.
    assert (session.ROUND_EFFORT, session.ROUND_TIMEOUT_SECONDS, session.MAX_ROUNDS) == ("high", 600, 8)
    helper = session.helper
    helper.write_state(tmp_path, {"schema": helper.SCHEMA, "status": "answered",
                                  "last_attempt_utc": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()})
    rounds = []

    def runner(argv, **kwargs):
        base = session.validate_consult_argv(list(argv))
        rounds.append((base["options"]["--effort"], session.round_argv(base, base["prompt"], None),
                       session.round_argv(base, base["prompt"], "resume-1"), kwargs["timeout"]))
        return subprocess.CompletedProcess(argv, 0, stdout="advice", stderr="")

    command = helper.advisory_command("grok.exe", "grok-model", effort=session.ROUND_EFFORT)
    total = session.session_seconds(session.DEFAULT_ROUNDS)
    assert total == 2400
    result = helper.consult(tmp_path, "readonly/task", "ask", command, runner=runner, timeout_seconds=total,
                            requested_by=requester)
    assert result["status"] == "answered" and len(rounds) == 1
    consult_effort, first, resumed, timeout = rounds[0]
    assert consult_effort == "high" and timeout == total
    for argv in (first, resumed):
        assert argv[argv.index("--effort") + 1] == "high"
        assert [argv[argv.index(flag) + 1] for flag in ("--tools", "--deny", "--max-turns")] == ["", "*", "1"]
    rows = [json.loads(line) for line in (tmp_path / helper.LEDGER_NAME).read_text(encoding="utf-8").splitlines()]
    started = [row for row in rows if row.get("event") == "started"]
    assert [(row["effort"], row["requested_by"]) for row in started] == [("high", requester)]


@pytest.mark.parametrize("max_rounds, total", [(2, 1200), (3, 1800), (4, 2400), (6, 2400), (8, 2400)])
def test_a_session_total_never_exceeds_the_helper_ceiling(max_rounds, total):
    # 600 s rounds; the helper still refuses more than 2400 s, so the reservation window does not grow.
    assert session.session_seconds(max_rounds) == total
    assert session.MAX_SESSION_SECONDS == 2400


@pytest.mark.parametrize("session_timeout, low, high", [(2400, 599, 600), (900, 599, 600), (300, 299, 300)])
def test_a_round_gets_600_seconds_unless_the_session_deadline_is_nearer(iso, tmp_path, session_timeout, low, high):
    _, cwd = iso
    timeouts = []

    def model(command, **kwargs):
        timeouts.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])   # a cut-off round ends the session

    runner = session.ReadonlySessionRunner(Broker(), session.BusyClock(), "c" * 40, session.surface_gate(cwd, None),
                                           max_rounds=4, model_runner=model)
    result = runner(_consult_argv(_state(tmp_path, "round")), timeout=session_timeout, env=None, cwd=str(cwd))
    summary = json.loads(result.stdout.split("READONLY SESSION SUMMARY\n", 1)[1])
    assert result.returncode == 1 and summary["outcome"] == "failed:round timeout"
    assert len(timeouts) == 1 and low < timeouts[0] <= high
