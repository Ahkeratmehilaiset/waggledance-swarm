"""Missing-slot board: every gate blocker lands in exactly one place, and the board stays observe-only."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools import wd_pr_missing_slot_board as board

HEAD = "a" * 40
BASE = "b" * 40
RECEIPT = "receipt_bundle_path is required before merge"
RCOS = ("claude-rco-1", "claude-rco-2")


def _gate(reasons, *, approved=(), blocking=(), latest_block=None, path_gate=None, author="fable-5"):
    identities = {"build_lead": {"agent": "codex-lead-1", "approved": "build_lead" in approved},
                  "build_tools": {"agent": "codex-tools-1", "approved": "build_tools" in approved},
                  "rco": {"by_agent": {agent: {"agent": agent, "approved": agent in approved} for agent in RCOS}}}
    return {"decision": "operator_review_required", "reasons": list(reasons),
            "author_resolution": {"author_agent": author},
            "path_gate": path_gate or {"allowed": True, "blocked_paths": [], "unmatched_paths": [], "reason": ""},
            "bridge_peer_gate": {"latest_blocking_event": latest_block},
            "bridge_consensus": {"recognized_rco_agents": list(RCOS), "eligible_rco_agents": list(RCOS),
                                 "blocking_rco_agents": list(blocking), "identities": identities}}


def _snapshot(**checks):
    rows = []
    for name, (status, conclusion) in checks.items():
        rows.append({"name": name.replace("_", " "), "status": status, "conclusion": conclusion, "state": ""})
    return {"head_sha": HEAD, "base_sha": BASE, "checks": rows}


def _elements(row):
    return {entry["element"]: entry for entry in row["missing"]}


def test_every_gate_blocker_lands_in_exactly_one_place():
    reasons = [
        "PR author resolution requires operator review: no valid UUID-bound canonical write claim covers this PR",
        "PR must not be a draft",
        "mergeable state is not clean: CONFLICTING",
        "path gate failed: denylist hit",
        "missing exact-head RCO_PASS from recognized non-author RCO",
        RECEIPT,
        "bridge consensus incomplete: build_lead (codex-lead-1): no head-bound approval at " + HEAD,
        "bridge consensus incomplete: build_tools (codex-tools-1): no head-bound approval at " + HEAD,
        "bridge consensus incomplete: rco (recognized non-author RCO): no head-bound approval at " + HEAD,
        "bridge consensus incomplete: rule12 review is not satisfied",
        "a gate reason this board has never seen",
    ]
    path_gate = {"allowed": False, "blocked_paths": [".agent-bridge/bin/X.ps1"], "unmatched_paths": [],
                 "reason": "denylist hit"}
    row = board.classify(_gate(reasons, path_gate=path_gate), _snapshot())
    elements = _elements(row)
    assert set(elements) == {"author", "draft", "merge_state", "signature", "rco", "build_lead", "build_tools",
                             "other"}
    # Unknown texts, including an unrecognized consensus reason, are kept verbatim, never dropped or guessed.
    assert elements["other"]["details"] == ["bridge consensus incomplete: rule12 review is not satisfied",
                                            "a gate reason this board has never seen"]
    assert row["merge_time"] == [RECEIPT]
    placed = [detail for entry in row["missing"] for detail in entry["details"]] + row["merge_time"]
    for reason in reasons:
        assert sum(detail.startswith(reason) for detail in placed) == 1, reason
    assert elements["signature"]["details"] == ["path gate failed: denylist hit (.agent-bridge/bin/X.ps1)"]
    assert elements["signature"]["actors"] == ["operator"]
    assert elements["build_lead"]["actors"] == ["codex-lead-1"]
    assert elements["build_tools"]["actors"] == ["codex-tools-1"]
    assert elements["draft"]["actors"] == ["fable-5"]
    assert row["state"] == "signature_after_consensus"


def test_ci_names_split_into_pending_and_failed_by_the_snapshot_state():
    snapshot = _snapshot(unified=("IN_PROGRESS", ""), **{"test_(3.11)": ("COMPLETED", "FAILURE"),
                                                         "security-scan": ("COMPLETED", "SUCCESS")})
    row = board.classify(_gate(["status checks not green: unified, test (3.11)", RECEIPT]), snapshot)
    elements = _elements(row)
    assert elements["ci_pending"]["details"] == ["unified"]
    assert elements["ci_failed"]["details"] == ["test (3.11)"]
    assert row["state"] == "in_progress"


def test_a_veto_outranks_a_present_rco_pass():
    reasons = ["unresolved peer bridge block: agent=claude-rco-2 status=changes_requested",
               "bridge consensus incomplete: recognized RCO veto blocks consensus: claude-rco-2", RECEIPT]
    gate = _gate(reasons, approved=("build_lead", "build_tools", "claude-rco-1"), blocking=("claude-rco-2",),
                 latest_block={"agent": "claude-rco-2", "status": "changes_requested"})
    row = board.classify(gate, _snapshot())
    assert set(_elements(row)) == {"veto"}
    assert _elements(row)["veto"]["actors"] == ["claude-rco-2"]
    assert row["approved"] == ["build_lead:codex-lead-1", "build_tools:codex-tools-1", "rco:claude-rco-1"]
    assert row["state"] == "in_progress"


def test_a_block_from_a_lane_that_is_not_an_rco_is_a_block_not_a_veto():
    gate = _gate(["unresolved peer bridge block: agent=codex-lead-1 status=blocked", RECEIPT],
                 latest_block={"agent": "codex-lead-1", "status": "blocked"})
    row = board.classify(gate, _snapshot())
    assert set(_elements(row)) == {"block"}
    assert _elements(row)["block"]["actors"] == ["codex-lead-1"]


def test_the_rco_slot_names_only_eligible_rcos_that_are_not_the_author():
    gate = _gate(["missing exact-head RCO_PASS from recognized non-author RCO", RECEIPT], author="claude-rco-1")
    assert _elements(board.classify(gate, _snapshot()))["rco"]["actors"] == ["claude-rco-2"]


def test_gate_command_is_the_dry_run_form_only():
    command = board.gate_command("py", Path("s.json"), HEAD, "fable-5/x", "o/r", Path("root"))
    for flag in ("--apply", "--receipt-out-dir", "--artifact-out-dir", "--standing-consensus-sign",
                 "--allow-lead-stall-failover", "--receipt-bundle-path"):
        assert flag not in command
    assert "--require-bridge-consensus" in command and command[command.index("--expected-head") + 1] == HEAD
    assert command[command.index("--consensus-proposal-id") + 1] == "fable-5/x"
    assert command[command.index("--bridge-task-id") + 1] == "fable-5/x"


class _FakeGitHub:
    """Answers gh pr list, the snapshot tool and the gate; records every command."""

    def __init__(self, prs, gates, snapshots=None, gate_stdout=None, refusals=None, list_run=None,
                 snapshot_errors=None):
        self.prs, self.gates, self.snapshots = prs, gates, snapshots or {}
        self.gate_stdout = gate_stdout or {}
        self.refusals = refusals or {}
        self.list_run = list_run
        self.snapshot_errors = snapshot_errors or {}
        self.gate_runs: list[int] = []
        self.commands: list[list[str]] = []
        self.snapshot_dirs: list[Path] = []

    def __call__(self, command):
        command = list(command)
        self.commands.append(command)
        if command[:3] == ["gh", "pr", "list"]:
            return self.list_run or SimpleNamespace(returncode=0, stdout=json.dumps(self.prs), stderr="")
        if str(board.SNAPSHOT_TOOL) in command:
            number = int(command[command.index(str(board.SNAPSHOT_TOOL)) + 1])
            out = Path(command[command.index("--out") + 1])
            self.snapshot_dirs.append(out.parent)
            if number in self.refusals:
                code, refusal = self.refusals[number]
                return SimpleNamespace(returncode=code, stdout=json.dumps(refusal), stderr="")
            if number in self.snapshot_errors:
                return SimpleNamespace(returncode=1, stdout="", stderr=self.snapshot_errors[number])
            out.write_text(json.dumps(self.snapshots.get(number, _snapshot())), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")
        if str(board.GATE_TOOL) in command:
            number = int(Path(command[command.index("--pr-status-file") + 1]).stem.split("-")[1])
            self.gate_runs.append(number)
            if number in self.gate_stdout:
                stdout, stderr = (self.gate_stdout[number] if isinstance(self.gate_stdout[number], tuple)
                                  else (self.gate_stdout[number], "boom"))
                return SimpleNamespace(returncode=2, stdout=stdout, stderr=stderr)
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.gates[number]), stderr="")
        raise AssertionError(f"unexpected command {command}")


def _pr(number):
    return {"number": number, "headRefName": f"fable-5/pr-{number}", "headRefOid": HEAD, "isDraft": False,
            "title": f"pr {number}"}


def test_signature_only_rows_form_the_morning_list_and_nothing_else_is_on_it(tmp_path, monkeypatch):
    denylist = {"allowed": False, "blocked_paths": [".agent-bridge/bin/X.ps1"], "unmatched_paths": [],
                "reason": "denylist hit"}
    all_slots = ("build_lead", "build_tools", "claude-rco-2")
    gates = {
        11: _gate(["path gate failed: denylist hit", RECEIPT], approved=all_slots, path_gate=denylist),
        12: _gate(["path gate failed: denylist hit", RECEIPT,
                   "missing exact-head RCO_PASS from recognized non-author RCO"],
                  approved=("build_lead", "build_tools"), path_gate=denylist),
        13: _gate([RECEIPT], approved=all_slots),
    }
    fake = _FakeGitHub([_pr(13), _pr(11), _pr(12)], gates)
    monkeypatch.setattr(board, "_run", fake)
    out = tmp_path / "board.json"
    assert board.main(["--repo", "o/r", "--out", str(out), "--main-sha", BASE]) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    states = {row["pr"]: row["state"] for row in result["rows"]}
    assert states == {11: "signature_only", 12: "signature_after_consensus", 13: "merge_ready"}
    assert [item["pr"] for item in result["signature_list"]] == [11]
    assert result["signature_list"][0]["approved"] == ["build_lead:codex-lead-1", "build_tools:codex-tools-1",
                                                       "rco:claude-rco-2"]
    assert result["merge_ready"] == [13] and result["errors"] == []
    assert all(row["base_is_main"] is True for row in result["rows"])
    assert result["observe_only"] is True


def test_the_board_writes_only_its_out_file_and_runs_only_read_commands(tmp_path, monkeypatch):
    fake = _FakeGitHub([_pr(21)], {21: _gate([RECEIPT])})
    monkeypatch.setattr(board, "_run", fake)
    out = tmp_path / "board.json"
    board.main(["--repo", "o/r", "--out", str(out)])
    assert sorted(path.name for path in tmp_path.iterdir()) == ["board.json"]
    assert fake.snapshot_dirs and not any(path.exists() for path in fake.snapshot_dirs)
    for command in fake.commands:
        text = " ".join(command)
        assert "--apply" not in command and "merge" not in command[:3] and "Write-AgentEvent" not in text
    assert [command[:3] for command in fake.commands if command[0] == "gh"] == [["gh", "pr", "list"]]


def test_an_unreadable_gate_output_is_a_row_error_and_exit_one(tmp_path, monkeypatch):
    fake = _FakeGitHub([_pr(31), _pr(32)], {32: _gate([RECEIPT])}, gate_stdout={31: "Traceback: not json"})
    monkeypatch.setattr(board, "_run", fake)
    out = tmp_path / "board.json"
    assert board.main(["--repo", "o/r", "--out", str(out)]) == 1
    result = json.loads(out.read_text(encoding="utf-8"))
    rows = {row["pr"]: row for row in result["rows"]}
    assert rows[31]["error"].startswith("gate output unreadable:") and "state" not in rows[31]
    assert rows[32]["state"] == "merge_ready" and result["errors"] == [31]


# Built from parts, as the repo's other guard tests do, so this file never carries the literal tokens.
GUARD_WORDS = ("PRIVATE_" + "MARKER", "_DO_NOT_" + "LEAK")


def test_a_snapshot_refusal_is_a_row_state_with_only_its_decision_name(tmp_path, monkeypatch):
    refusals = {
        41: (1, {"decision": "stale_base_ref", "errors": ["PR baseRefOid does not match the live base branch tip"],
                 "exit_code": 1, "ok": False}),
        42: (2, {"decision": "privacy_marker_refused", "errors": ["privacy marker refused: " + GUARD_WORDS[0]],
                 "exit_code": 2, "ok": False}),
    }
    fake = _FakeGitHub([_pr(41), _pr(42), _pr(43)], {43: _gate([RECEIPT])}, refusals=refusals)
    monkeypatch.setattr(board, "_run", fake)
    out = tmp_path / "board.json"
    assert board.main(["--repo", "o/r", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    result = json.loads(text)
    rows = {row["pr"]: row for row in result["rows"]}
    assert rows[41]["state"] == rows[42]["state"] == "snapshot_refused"
    assert rows[41]["missing"] == [{"element": "base", "details": ["stale_base_ref"], "actors": []}]
    assert rows[42]["missing"] == [{"element": "snapshot", "details": ["privacy_marker_refused"], "actors": []}]
    assert not any(word in text for word in GUARD_WORDS)
    assert result["errors"] == [] and result["snapshot_refused"] == [41, 42] and fake.gate_runs == [43]


def test_a_refusal_without_a_plain_decision_name_is_a_row_error(tmp_path, monkeypatch):
    refusals = {44: (2, {"decision": "refused: " + GUARD_WORDS[1], "ok": False})}
    fake = _FakeGitHub([_pr(44)], {}, refusals=refusals)
    monkeypatch.setattr(board, "_run", fake)
    out = tmp_path / "board.json"
    assert board.main(["--repo", "o/r", "--out", str(out)]) == 1
    assert json.loads(out.read_text(encoding="utf-8"))["errors"] == [44]


def test_queue_and_diff_gate_blockers_classify_and_a_code_pattern_needs_the_signature():
    reasons = ["accepted bridge queue is not fully visible in canonical history: accepted_queue_incomplete",
               "diff gate failed: code pattern denylist hit", RECEIPT]
    row = board.classify(_gate(reasons, approved=("build_lead", "build_tools", "claude-rco-1")), _snapshot())
    elements = _elements(row)
    assert set(elements) == {"accepted_queue", "signature"}
    assert elements["signature"]["details"] == ["diff gate failed: code pattern denylist hit"]
    assert row["state"] == "signature_after_consensus"


def test_a_lead_hold_keeps_a_gate_ready_pr_off_the_merge_ready_list(tmp_path, monkeypatch):
    fake = _FakeGitHub([_pr(51), _pr(52)], {51: _gate([RECEIPT]), 52: _gate([RECEIPT])})
    monkeypatch.setattr(board, "_run", fake)
    out = tmp_path / "board.json"
    assert board.main(["--repo", "o/r", "--out", str(out), "--hold", "51=waits for the M227 activation"]) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    rows = {row["pr"]: row for row in result["rows"]}
    assert rows[51]["state"] == "held"
    assert rows[51]["missing"] == [{"element": "hold", "details": ["waits for the M227 activation"],
                                    "actors": ["codex-lead-1"]}]
    assert result["held"] == [51] and result["merge_ready"] == [52]
    with pytest.raises(SystemExit):
        board.main(["--repo", "o/r", "--hold", "fifty-one=reason"])


# -- Contract with the real gate: its blocker texts must still classify (no silent drift into "other") ------

def _real_gate(tmp_path: Path, **snapshot_fields) -> dict:
    snapshot = {"pr_number": 4242, "title": "synthetic", "state": "OPEN", "is_draft": False,
                "mergeable": "MERGEABLE", "head_sha": HEAD, "base_sha": BASE, "base_tip_sha": BASE,
                "base_ref": "main", "head_ref": "fable-5/synthetic-board-probe",
                "changed_paths": ["docs/operations/x.md"],
                "diff_text": "diff --git a/docs/operations/x.md b/docs/operations/x.md\n+hello\n",
                "checks": [{"name": "unified", "status": "IN_PROGRESS", "conclusion": "", "state": ""},
                           {"name": "security-scan", "status": "COMPLETED", "conclusion": "FAILURE", "state": ""}],
                "git_identities": [], "git_identity_evidence": {}, "operator_approved": False,
                "receipt_verified": False, "review_decision": "", "statusCheckRollup": [],
                "updated_at": "2026-10-13T00:00:00Z", "url": "https://example.invalid/pr/4242"}
    snapshot.update(snapshot_fields)
    path = tmp_path / "pr-4242.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    root = tmp_path / "bridge"
    (root / "shared").mkdir(parents=True)
    (root / "shared" / "events.jsonl").write_text("", encoding="utf-8")
    command = board.gate_command(sys.executable, path, HEAD, "fable-5/synthetic-board-probe", "o/r", root)
    done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=300,
                          cwd=board.REPO_ROOT)
    gate = json.loads(done.stdout)
    assert gate.get("would_merge") is False and gate.get("external_effect") in (False, None)
    return board.classify(gate, snapshot)


def test_the_real_gate_blockers_for_an_unclaimed_pr_all_classify(tmp_path):
    row = _real_gate(tmp_path)
    elements = _elements(row)
    assert "other" not in elements, elements.get("other")
    assert {"author", "ci_pending", "ci_failed", "rco"} <= set(elements)
    assert elements["ci_pending"]["details"] == ["unified"] and elements["ci_failed"]["details"] == ["security-scan"]
    assert row["merge_time"] == [RECEIPT]


def test_the_real_gate_blockers_for_a_draft_conflicting_denylisted_pr_all_classify(tmp_path):
    row = _real_gate(tmp_path, is_draft=True, mergeable="CONFLICTING",
                     changed_paths=[".agent-bridge/bin/Write-AgentEvent.ps1"],
                     diff_text=("diff --git a/.agent-bridge/bin/Write-AgentEvent.ps1 "
                                "b/.agent-bridge/bin/Write-AgentEvent.ps1\n+# comment\n"))
    elements = _elements(row)
    assert "other" not in elements, elements.get("other")
    assert {"draft", "merge_state", "signature"} <= set(elements)
    assert elements["signature"]["details"] == [
        "path gate failed: denylist hit (.agent-bridge/bin/Write-AgentEvent.ps1)"]


# -- Decoded-value guard: no guarded word from a title, branch, gate text, hold or error tail reaches the output --

def _escaped(word):
    """The word with its first letter as a JSON backslash-u escape, so only a decoded check can see it."""
    return "\\u%04x" % ord(word[0]) + word[1:]


def _assert_no_guard_word(text):
    assert not any(word in text for word in GUARD_WORDS)
    decoded = json.dumps(json.loads(text), ensure_ascii=False) if text.lstrip().startswith("{") else text
    assert not any(word in decoded for word in GUARD_WORDS)


def test_a_guarded_word_from_any_outside_text_is_withheld_and_keeps_its_state(tmp_path, monkeypatch, capsys):
    prs = [_pr(61), _pr(62), _pr(63)]
    prs[0]["title"] = "fix " + GUARD_WORDS[0] + " title"
    prs[1]["headRefName"] = "fable-5/x" + GUARD_WORDS[1]
    # gh pr list output as raw JSON text with the word escaped: the board sees it only after decoding.
    list_text = json.dumps(prs).replace(GUARD_WORDS[0], _escaped(GUARD_WORDS[0]))
    assert GUARD_WORDS[0] not in list_text and _escaped(GUARD_WORDS[0]) in list_text
    denylist = {"allowed": False, "blocked_paths": ["docs/" + GUARD_WORDS[1] + ".md"], "unmatched_paths": [],
                "reason": "denylist hit"}
    all_slots = ("build_lead", "build_tools", "claude-rco-2")
    gates = {61: _gate([RECEIPT], approved=all_slots),
             62: _gate(["path gate failed: denylist hit", RECEIPT], approved=all_slots, path_gate=denylist),
             63: _gate(["gate text naming " + GUARD_WORDS[0], RECEIPT], approved=all_slots)}
    fake = _FakeGitHub(prs, gates, list_run=SimpleNamespace(returncode=0, stdout=list_text, stderr=""))
    monkeypatch.setattr(board, "_run", fake)
    out = tmp_path / "board.json"
    assert board.main(["--repo", "o/r", "--out", str(out), "--hold", "61=waits on " + GUARD_WORDS[1]]) == 0
    text = out.read_text(encoding="utf-8")
    _assert_no_guard_word(text)
    _assert_no_guard_word(capsys.readouterr().out)
    result = json.loads(text)
    rows = {row["pr"]: row for row in result["rows"]}
    assert rows[61]["title"] == board.WITHHELD and rows[61]["withheld"] == 2
    assert rows[61]["missing"] == [{"element": "hold", "details": [board.WITHHELD], "actors": ["codex-lead-1"]}]
    assert rows[62]["branch"] == board.WITHHELD and rows[62]["withheld"] == 2
    assert rows[62]["missing"][0]["details"] == [board.WITHHELD]
    assert _elements(rows[63])["other"]["details"] == [board.WITHHELD] and rows[63]["withheld"] == 1
    # Withholding a text never changes what is missing.
    assert {pr: row["state"] for pr, row in rows.items()} == {61: "held", 62: "signature_only", 63: "in_progress"}
    assert [item["pr"] for item in result["signature_list"]] == [62]
    assert result["withheld"] == [61, 62, 63] and result["errors"] == []


def test_an_error_tail_with_a_guarded_word_is_withheld_plain_or_escaped(tmp_path, monkeypatch, capsys):
    fake = _FakeGitHub([_pr(71), _pr(72), _pr(73)], {73: _gate([RECEIPT])},
                       snapshot_errors={71: "Traceback: bad value " + GUARD_WORDS[0]},
                       gate_stdout={72: ("not json", 'echoed line {"m": "' + _escaped(GUARD_WORDS[1]) + '"}')})
    monkeypatch.setattr(board, "_run", fake)
    out = tmp_path / "board.json"
    assert board.main(["--repo", "o/r", "--out", str(out), "--main-sha", "m" + GUARD_WORDS[0]]) == 1
    text = out.read_text(encoding="utf-8")
    _assert_no_guard_word(text)
    _assert_no_guard_word(capsys.readouterr().out)
    assert json.loads(text)["main_sha"] == board.WITHHELD
    rows = {row["pr"]: row for row in json.loads(text)["rows"]}
    assert rows[71]["error"] == rows[72]["error"] == board.WITHHELD
    assert rows[71]["withheld"] == rows[72]["withheld"] == 1 and "withheld" not in rows[73]
    failed = SimpleNamespace(returncode=1, stdout="", stderr="gh: " + _escaped(GUARD_WORDS[0]))
    with pytest.raises(SystemExit) as raised:
        board.list_open_prs("o/r", run=lambda command: failed)
    _assert_no_guard_word(str(raised.value))
    assert board.WITHHELD in str(raised.value)
    # Keys are the board's own; one holding a guarded word means nothing is written at all.
    with pytest.raises(ValueError) as raised_key:
        board._withhold({"rows": [{"k" + GUARD_WORDS[1]: 1}]})
    _assert_no_guard_word(str(raised_key.value))
