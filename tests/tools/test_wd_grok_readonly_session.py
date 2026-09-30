"""Grok read-only session controller (5246ce10): Bridge-only fake-port fixtures (AUTHORED, NOT RUN).

Every model process is a scripted fake, the broker is a fake and every path is a temporary directory:
nothing here launches Grok, inspects a provider or account, or reads or changes the real user profile
(the inventory's home list is monkeypatched to a temp home and GROK_HOME is unset). An acknowledged
surface is NOT isolation: read_only_guarantee stays False everywhere.
"""
from __future__ import annotations

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
            "--permission-mode", "plan", "--disable-web-search", "--no-memory"]


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
                         ([a for a in argv if a != "--no-memory"], "mandatory no-native-tools")):
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
