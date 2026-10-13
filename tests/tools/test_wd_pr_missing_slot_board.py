"""Missing-slot board: every gate blocker lands in exactly one place, and the board stays observe-only."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import re
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
        "path gate failed: paths not on allowlist",
        "missing exact-head RCO_PASS from recognized non-author RCO",
        RECEIPT,
        "bridge consensus incomplete: build_lead (codex-lead-1): no head-bound approval at " + HEAD,
        "bridge consensus incomplete: build_tools (codex-tools-1): no head-bound approval at " + HEAD,
        "bridge consensus incomplete: rco (recognized non-author RCO): no head-bound approval at " + HEAD,
        "bridge consensus incomplete: rule12 review is not satisfied",
        "a gate reason this board has never seen",
    ]
    path_gate = {"allowed": False, "blocked_paths": [], "unmatched_paths": ["ops/windows/reboot/X.ps1"],
                 "reason": "paths not on allowlist"}
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
    assert elements["signature"]["details"] == ["path gate failed: paths not on allowlist (ops/windows/reboot/X.ps1)"]
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
    off_allowlist = {"allowed": False, "blocked_paths": [], "unmatched_paths": ["ops/windows/reboot/X.ps1"],
                     "reason": "paths not on allowlist"}
    all_slots = ("build_lead", "build_tools", "claude-rco-2")
    gates = {
        11: _gate(["path gate failed: paths not on allowlist", RECEIPT], approved=all_slots,
                  path_gate=off_allowlist),
        12: _gate(["path gate failed: paths not on allowlist", RECEIPT,
                   "missing exact-head RCO_PASS from recognized non-author RCO"],
                  approved=("build_lead", "build_tools"), path_gate=off_allowlist),
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


def test_a_denylisted_path_is_its_own_element_and_never_on_the_signature_list(tmp_path, monkeypatch):
    # Codex P2 PRRT_kwDORYf3RM6r0aK-: the dry-run gate never admits a denylisted path (gate_command passes no
    # --standing-consensus-sign, and that admission drops only an off-allowlist path blocker), so a signature
    # cannot clear it. An off-allowlist path stays the signature element.
    denylist = {"allowed": False, "blocked_paths": [".agent-bridge/bin/X.ps1"], "unmatched_paths": [],
                "reason": "denylist hit"}
    off_allowlist = {"allowed": False, "blocked_paths": [], "unmatched_paths": ["ops/windows/reboot/x.ps1"],
                     "reason": "paths not on allowlist"}
    all_slots = ("build_lead", "build_tools", "claude-rco-2")
    gates = {81: _gate(["path gate failed: denylist hit", RECEIPT], approved=all_slots, path_gate=denylist),
             82: _gate(["path gate failed: paths not on allowlist", RECEIPT], approved=all_slots,
                       path_gate=off_allowlist)}
    fake = _FakeGitHub([_pr(81), _pr(82)], gates)
    monkeypatch.setattr(board, "_run", fake)
    out = tmp_path / "board.json"
    assert board.main(["--repo", "o/r", "--out", str(out)]) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    rows = {row["pr"]: row for row in result["rows"]}
    assert rows[81]["state"] == "path_denylisted"
    assert rows[81]["missing"] == [{"element": "path_denylist", "actors": [], "details": [
        "path gate failed: denylist hit (.agent-bridge/bin/X.ps1)", board.DENYLIST_REMEDIATION]}]
    assert rows[82]["state"] == "signature_only"
    assert rows[82]["missing"] == [{"element": "signature", "actors": ["operator"], "details": [
        "path gate failed: paths not on allowlist (ops/windows/reboot/x.ps1)"]}]
    assert [item["pr"] for item in result["signature_list"]] == [82]
    assert result["path_denylisted"] == [81]
    assert "#81 aaaaaaaa path_denylisted: path_denylist" in board.render_text(result)


def test_a_requested_pr_missing_from_the_open_listing_refuses_and_writes_nothing(tmp_path, monkeypatch, capsys):
    # Codex P2 PRRT_kwDORYf3RM6r0aLI: gh pr list returns at most --limit items, so a requested PR outside that
    # page (or one that is not open) would vanish from a board that still exits 0. The board refuses instead.
    fake = _FakeGitHub([_pr(91), _pr(92)], {91: _gate([RECEIPT]), 92: _gate([RECEIPT])})
    monkeypatch.setattr(board, "_run", fake)
    out = tmp_path / "board.json"
    assert board.main(["--repo", "o/r", "--out", str(out), "--pr", "91", "--pr", "93"]) == 2
    assert not out.exists() and fake.gate_runs == []
    captured = capsys.readouterr()
    assert captured.out == "" and "refused" in captured.err and "#93" in captured.err and "#91" not in captured.err
    assert board.main(["--repo", "o/r", "--out", str(out), "--pr", "92"]) == 0
    assert [row["pr"] for row in json.loads(out.read_text(encoding="utf-8"))["rows"]] == [92]


def test_a_full_listing_at_its_limit_refuses_because_it_may_be_cut_off(tmp_path, monkeypatch, capsys):
    # Same failure as PRRT_kwDORYf3RM6r0aLI without --pr: 197 PRs were open at 11:05Z 2026-10-13, so a listing
    # that returns exactly its --limit items may be cut off and the board would silently lack the rest.
    monkeypatch.setattr(board, "LIST_LIMIT", 3)
    fake = _FakeGitHub([_pr(41), _pr(42), _pr(43)], {41: _gate([RECEIPT]), 42: _gate([RECEIPT]), 43: _gate([RECEIPT])})
    monkeypatch.setattr(board, "_run", fake)
    out = tmp_path / "board.json"
    assert board.main(["--repo", "o/r", "--out", str(out)]) == 2
    assert not out.exists() and fake.gate_runs == []
    assert "refused" in capsys.readouterr().err
    assert any(command[:3] == ["gh", "pr", "list"] and "3" in command for command in fake.commands)
    # Explicit PRs that are all in the listing still run: the absent check above covers them.
    assert board.main(["--repo", "o/r", "--out", str(out), "--pr", "42"]) == 0
    fewer = _FakeGitHub([_pr(41), _pr(42)], {41: _gate([RECEIPT]), 42: _gate([RECEIPT])})
    monkeypatch.setattr(board, "_run", fewer)
    assert board.main(["--repo", "o/r", "--out", str(tmp_path / "two.json")]) == 0


def test_the_default_listing_limit_leaves_room_above_the_current_open_pr_count():
    # RCO1 11:38Z 2026-10-13: about 197 PRs were open, so a limit of 200 would refuse the default board soon.
    assert board.LIST_LIMIT >= 1000


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
    assert {"draft", "merge_state", "path_denylist"} <= set(elements) and "signature" not in elements
    assert elements["path_denylist"]["details"] == [
        "path gate failed: denylist hit (.agent-bridge/bin/Write-AgentEvent.ps1)", board.DENYLIST_REMEDIATION]


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
    off_allowlist = {"allowed": False, "blocked_paths": [], "unmatched_paths": ["docs/" + GUARD_WORDS[1] + ".md"],
                     "reason": "paths not on allowlist"}
    all_slots = ("build_lead", "build_tools", "claude-rco-2")
    gates = {61: _gate([RECEIPT], approved=all_slots),
             62: _gate(["path gate failed: paths not on allowlist", RECEIPT], approved=all_slots,
                       path_gate=off_allowlist),
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


# -- Dormant mind mode: one owned reflected Mind snapshot, shown with its refs; never the gate ----------------------

# The actual reflected snapshot of ten bridge events (below): rebuilt by the Mind core (PR1900 at d47f3ef9) and
# reflected by the workspace (Mind slice 1 at 6d60d69e). MIND_R_SHA256 and MIND_RB_SHA256 are the sha256 of the
# workspace's serialized --json text (indent 2, sorted keys, final newline; a Windows console turns each newline
# into CRLF on stdout). The blocked view differs only in source.blocked and the three reflection fields.
MIND_R_SHA256 = "4014ce48279e03b0cf29bb9195bdd3f88db9422ce8fcf36e9b1c98bd1f4852ca"
MIND_RB_SHA256 = "2d9c17b66321341d7c37aeb5e618ef01113c6253857cd1bbfbd2b7b9b4eb9a4d"
MIND_R_TEXT = """{
 "attention": [
  {
   "actors": [
    "claude-rco-2"
   ],
   "detail": "PR2001 at 6b6b6b6b6b6b is reported CI-green; missing: claude-rco-2",
   "kind": "last_missing_vote",
   "rank": 1,
   "refs": [
    "332e143886aa09a92898ec5c1e88e14222194dd662832162170968fa653aaea7",
    "5368df0abc5d563871d236aadf4b42fe278080c5ad38227307c675d234191b45",
    "80216e38e03c0bd5b555e4c1786efb5078aa0375e0128479e987895338a0737b",
    "82f7bc9b61936ac0e615423bddcc94a6767b63816a5d3bf49bebd00abda2cf46"
   ],
   "subject": "pr:2001",
   "suggestion_only": true
  },
  {
   "actors": [
    "codex-tools-1"
   ],
   "detail": "veto_observed: codex-tools-1 sent blocked on PR2003 at 9e9e9e9e9e9e; the veto is observed; whether it is still active is UNKNOWN, never cleared here",
   "kind": "tension_high",
   "rank": 2,
   "refs": [
    "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6"
   ],
   "subject": "tension:ce522ddcc4c505c8",
   "suggestion_only": true
  },
  {
   "actors": [
    "claude-rco-1",
    "fable-5"
   ],
   "detail": "claim_overlap: claude-rco-1 and fable-5 both hold write claims that are not expired; overlapping paths: tools ~ tools/x.py",
   "kind": "tension_high",
   "rank": 3,
   "refs": [
    "417c8fa562606237e117251c58d28e6412b8b114a6895d61a6c56204c808d3df",
    "4622a41682f06aa36a1dbe6e35b2e43cf76c2a1537547f257331601da64a6533"
   ],
   "subject": "tension:f1a70115052a010a",
   "suggestion_only": true
  },
  {
   "actors": [
    "claude-rco-2"
   ],
   "detail": "stale_vote: PR2002 at 8d8d8d8d8d8d has 1 stale vote(s); claude-rco-2 rco_pass@7c7c7c7c7c7c; a stale vote cannot approve it",
   "kind": "tension",
   "rank": 4,
   "refs": [
    "61cd7b7524b15664b5a3ab5518cf606a878606956fbe777bd19a712f38793b44",
    "cf2926b527d8307c04bafe96ba15da9c794a6f83ed739e487a262cd384d60ff7",
    "da6e6b357d30235f2e6eb69ce701c3efe9e26ca75785f00c2fc37715d4ff2f77"
   ],
   "subject": "tension:bb14e7638ad28277",
   "suggestion_only": true
  }
 ],
 "authority": "none",
 "facts": [
  {
   "canonical_sha256": "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6",
   "fact_id": "1a8d53e42bdc6eaa0fe40fc1d6dd687b4a3702001e239d60bf254944510b376b",
   "kind": "pr_observation",
   "observed_at_utc": "2026-10-13T05:09:00Z",
   "salience": 100,
   "source_refs": [
    "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6"
   ],
   "ttl_seconds": 300,
   "value": {
    "head": "9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e",
    "pr": 2003
   },
   "verification": "declared"
  },
  {
   "canonical_sha256": "da6e6b357d30235f2e6eb69ce701c3efe9e26ca75785f00c2fc37715d4ff2f77",
   "fact_id": "d7b8228486fe759adfbb2e402ac975a47e59464ed3802118b549bc48c671cf3e",
   "kind": "pr_observation",
   "observed_at_utc": "2026-10-13T05:06:00Z",
   "salience": 80,
   "source_refs": [
    "61cd7b7524b15664b5a3ab5518cf606a878606956fbe777bd19a712f38793b44",
    "da6e6b357d30235f2e6eb69ce701c3efe9e26ca75785f00c2fc37715d4ff2f77"
   ],
   "ttl_seconds": 300,
   "value": {
    "head": "8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d",
    "pr": 2002
   },
   "verification": "declared"
  },
  {
   "canonical_sha256": "4622a41682f06aa36a1dbe6e35b2e43cf76c2a1537547f257331601da64a6533",
   "fact_id": "f66bf932b5e74ef1067a8d5bafcd5aa9843bcaaefc0da6b4108cfadd2af29ee0",
   "kind": "lane_observation",
   "observed_at_utc": "2026-10-13T05:07:00Z",
   "salience": 50,
   "source_refs": [
    "4622a41682f06aa36a1dbe6e35b2e43cf76c2a1537547f257331601da64a6533"
   ],
   "ttl_seconds": 300,
   "value": {
    "agent": "claude-rco-1",
    "agent_uuid": "00000000-0000-4000-8000-0000000000c1",
    "current_task": "claude-rco-1/notes"
   },
   "verification": "declared"
  },
  {
   "canonical_sha256": "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6",
   "fact_id": "9cdb159f675a8fd307d4e28e1b62b17ae6dc44477b25821d5831daaae648f98e",
   "kind": "lane_observation",
   "observed_at_utc": "2026-10-13T05:09:00Z",
   "salience": 50,
   "source_refs": [
    "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6"
   ],
   "ttl_seconds": 300,
   "value": {
    "agent": "codex-tools-1",
    "agent_uuid": "00000000-0000-4000-8000-0000000000b1",
    "current_task": "codex-tools-1/train-2003"
   },
   "verification": "declared"
  },
  {
   "canonical_sha256": "417c8fa562606237e117251c58d28e6412b8b114a6895d61a6c56204c808d3df",
   "fact_id": "2ca3c571a3ad454cd5d0a8d79c19577834f0832ee4bf6cdf1f35460ab34c2d4b",
   "kind": "lane_observation",
   "observed_at_utc": "2026-10-13T05:08:00Z",
   "salience": 50,
   "source_refs": [
    "417c8fa562606237e117251c58d28e6412b8b114a6895d61a6c56204c808d3df"
   ],
   "ttl_seconds": 300,
   "value": {
    "agent": "fable-5",
    "agent_uuid": "00000000-0000-4000-8000-0000000000f5",
    "current_task": "fable-5/board"
   },
   "verification": "declared"
  }
 ],
 "lanes": {
  "claude-rco-1|00000000-0000-4000-8000-0000000000c1": {
   "agent": "claude-rco-1",
   "agent_uuid": "00000000-0000-4000-8000-0000000000c1",
   "claims": {
    "by_task": [
     {
      "expired": "UNKNOWN",
      "lease_expires_utc": "UNKNOWN",
      "owner_run_id": "r-c1",
      "owner_session_id": "s-c1",
      "source_refs": [
       "4622a41682f06aa36a1dbe6e35b2e43cf76c2a1537547f257331601da64a6533"
      ],
      "task_id": "claude-rco-1/notes",
      "verification": "declared",
      "write_scope": [
       "tools/x.py"
      ]
     }
    ],
    "expired": "UNKNOWN",
    "lease_expires_utc": "UNKNOWN",
    "source_refs": [
     "4622a41682f06aa36a1dbe6e35b2e43cf76c2a1537547f257331601da64a6533"
    ],
    "verification": "declared",
    "write_scope": [
     "tools/x.py"
    ]
   },
   "current_task": "claude-rco-1/notes",
   "idle_status": "UNKNOWN",
   "last_event_ref": "4622a41682f06aa36a1dbe6e35b2e43cf76c2a1537547f257331601da64a6533",
   "last_event_utc": "2026-10-13T05:07:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 180.0,
   "run_id": "r-c1",
   "session_id": "s-c1",
   "source_refs": [
    "4622a41682f06aa36a1dbe6e35b2e43cf76c2a1537547f257331601da64a6533"
   ],
   "uncertainty": [
    "origin_not_authenticated",
    "native_readiness_UNKNOWN"
   ],
   "verification": "declared",
   "waits": "UNKNOWN"
  },
  "claude-rco-2|00000000-0000-4000-8000-0000000000c2": {
   "agent": "claude-rco-2",
   "agent_uuid": "00000000-0000-4000-8000-0000000000c2",
   "claims": "UNKNOWN",
   "current_task": "claude-rco-2/review-2002",
   "idle_status": "UNKNOWN",
   "last_event_ref": "cf2926b527d8307c04bafe96ba15da9c794a6f83ed739e487a262cd384d60ff7",
   "last_event_utc": "2026-10-13T05:05:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 300.0,
   "run_id": "r-c2",
   "session_id": "s-c2",
   "source_refs": [
    "cf2926b527d8307c04bafe96ba15da9c794a6f83ed739e487a262cd384d60ff7"
   ],
   "uncertainty": [
    "origin_not_authenticated",
    "native_readiness_UNKNOWN"
   ],
   "verification": "declared",
   "waits": "UNKNOWN"
  },
  "codex-lead-1|00000000-0000-4000-8000-0000000000a1": {
   "agent": "codex-lead-1",
   "agent_uuid": "00000000-0000-4000-8000-0000000000a1",
   "claims": "UNKNOWN",
   "current_task": "codex-lead-1/train-2002",
   "idle_status": "UNKNOWN",
   "last_event_ref": "61cd7b7524b15664b5a3ab5518cf606a878606956fbe777bd19a712f38793b44",
   "last_event_utc": "2026-10-13T05:04:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 360.0,
   "run_id": "r-a1",
   "session_id": "s-a1",
   "source_refs": [
    "61cd7b7524b15664b5a3ab5518cf606a878606956fbe777bd19a712f38793b44"
   ],
   "uncertainty": [
    "origin_not_authenticated",
    "native_readiness_UNKNOWN"
   ],
   "verification": "declared",
   "waits": "UNKNOWN"
  },
  "codex-tools-1|00000000-0000-4000-8000-0000000000b1": {
   "agent": "codex-tools-1",
   "agent_uuid": "00000000-0000-4000-8000-0000000000b1",
   "claims": "UNKNOWN",
   "current_task": "codex-tools-1/train-2003",
   "idle_status": "UNKNOWN",
   "last_event_ref": "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6",
   "last_event_utc": "2026-10-13T05:09:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 60.0,
   "run_id": "r-b1",
   "session_id": "s-b1",
   "source_refs": [
    "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6"
   ],
   "uncertainty": [
    "origin_not_authenticated",
    "native_readiness_UNKNOWN"
   ],
   "verification": "declared",
   "waits": "UNKNOWN"
  },
  "fable-5|00000000-0000-4000-8000-0000000000f5": {
   "agent": "fable-5",
   "agent_uuid": "00000000-0000-4000-8000-0000000000f5",
   "claims": {
    "by_task": [
     {
      "expired": "UNKNOWN",
      "lease_expires_utc": "UNKNOWN",
      "owner_run_id": "r-f5",
      "owner_session_id": "s-f5",
      "source_refs": [
       "417c8fa562606237e117251c58d28e6412b8b114a6895d61a6c56204c808d3df"
      ],
      "task_id": "fable-5/board",
      "verification": "declared",
      "write_scope": [
       "tools"
      ]
     }
    ],
    "expired": "UNKNOWN",
    "lease_expires_utc": "UNKNOWN",
    "source_refs": [
     "417c8fa562606237e117251c58d28e6412b8b114a6895d61a6c56204c808d3df"
    ],
    "verification": "declared",
    "write_scope": [
     "tools"
    ]
   },
   "current_task": "fable-5/board",
   "idle_status": "UNKNOWN",
   "last_event_ref": "417c8fa562606237e117251c58d28e6412b8b114a6895d61a6c56204c808d3df",
   "last_event_utc": "2026-10-13T05:08:00Z",
   "next_question": "UNKNOWN",
   "observation_age_seconds": 120.0,
   "run_id": "r-f5",
   "session_id": "s-f5",
   "source_refs": [
    "417c8fa562606237e117251c58d28e6412b8b114a6895d61a6c56204c808d3df"
   ],
   "uncertainty": [
    "origin_not_authenticated",
    "native_readiness_UNKNOWN"
   ],
   "verification": "declared",
   "waits": "UNKNOWN"
  }
 },
 "next_question": {
  "global": {
   "attention_rank": 1,
   "question": "PR2001 at 6b6b6b6b6b6b is reported CI-green; missing: claude-rco-2. Can claude-rco-2 give it at this exact head?",
   "refs": [
    "332e143886aa09a92898ec5c1e88e14222194dd662832162170968fa653aaea7",
    "5368df0abc5d563871d236aadf4b42fe278080c5ad38227307c675d234191b45",
    "80216e38e03c0bd5b555e4c1786efb5078aa0375e0128479e987895338a0737b",
    "82f7bc9b61936ac0e615423bddcc94a6767b63816a5d3bf49bebd00abda2cf46"
   ]
  },
  "lanes": {
   "claude-rco-1": {
    "attention_rank": 3,
    "question": "How can claude-rco-1 resolve claim_overlap: claude-rco-1 and fable-5 both hold write claims that are not expired; overlapping paths: tools ~ tools/x.py?",
    "refs": [
     "417c8fa562606237e117251c58d28e6412b8b114a6895d61a6c56204c808d3df",
     "4622a41682f06aa36a1dbe6e35b2e43cf76c2a1537547f257331601da64a6533"
    ]
   },
   "claude-rco-2": {
    "attention_rank": 1,
    "question": "PR2001 at 6b6b6b6b6b6b is reported CI-green; missing: claude-rco-2. Can claude-rco-2 give it at this exact head?",
    "refs": [
     "332e143886aa09a92898ec5c1e88e14222194dd662832162170968fa653aaea7",
     "5368df0abc5d563871d236aadf4b42fe278080c5ad38227307c675d234191b45",
     "80216e38e03c0bd5b555e4c1786efb5078aa0375e0128479e987895338a0737b",
     "82f7bc9b61936ac0e615423bddcc94a6767b63816a5d3bf49bebd00abda2cf46"
    ]
   },
   "codex-lead-1": {
    "attention_rank": null,
    "question": "UNKNOWN",
    "refs": [
     "UNKNOWN"
    ]
   },
   "codex-tools-1": {
    "attention_rank": 2,
    "question": "How can codex-tools-1 resolve veto_observed: codex-tools-1 sent blocked on PR2003 at 9e9e9e9e9e9e; the veto is observed; whether it is still active is UNKNOWN, never cleared here?",
    "refs": [
     "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6"
    ]
   },
   "fable-5": {
    "attention_rank": 3,
    "question": "How can fable-5 resolve claim_overlap: claude-rco-1 and fable-5 both hold write claims that are not expired; overlapping paths: tools ~ tools/x.py?",
    "refs": [
     "417c8fa562606237e117251c58d28e6412b8b114a6895d61a6c56204c808d3df",
     "4622a41682f06aa36a1dbe6e35b2e43cf76c2a1537547f257331601da64a6533"
    ]
   }
  }
 },
 "prs": {
  "2001": {
   "base": "UNKNOWN",
   "class": "b",
   "head": "6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b",
   "missing": [
    "gate_eligibility_UNKNOWN"
   ],
   "pr": 2001,
   "reported_ci": {
    "head": "6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b",
    "source_refs": [
     "82f7bc9b61936ac0e615423bddcc94a6767b63816a5d3bf49bebd00abda2cf46"
    ],
    "status": "reported_green",
    "verification": "declared"
   },
   "source_refs": [
    "80216e38e03c0bd5b555e4c1786efb5078aa0375e0128479e987895338a0737b",
    "82f7bc9b61936ac0e615423bddcc94a6767b63816a5d3bf49bebd00abda2cf46"
   ],
   "stale_votes": [],
   "veto_observations": [],
   "votes": [
    {
     "agent": "claude-rco-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c1",
     "head": "6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b",
     "source_refs": [
      "80216e38e03c0bd5b555e4c1786efb5078aa0375e0128479e987895338a0737b"
     ],
     "status": "rco_pass",
     "verification": "declared"
    },
    {
     "agent": "codex-lead-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000a1",
     "head": "6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b",
     "source_refs": [
      "5368df0abc5d563871d236aadf4b42fe278080c5ad38227307c675d234191b45"
     ],
     "status": "build_consensus_pass",
     "verification": "declared"
    },
    {
     "agent": "codex-tools-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000b1",
     "head": "6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b6b",
     "source_refs": [
      "332e143886aa09a92898ec5c1e88e14222194dd662832162170968fa653aaea7"
     ],
     "status": "build_consensus_pass",
     "verification": "declared"
    }
   ]
  },
  "2002": {
   "base": "UNKNOWN",
   "class": "a",
   "head": "8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d8d",
   "missing": [
    "gate_eligibility_UNKNOWN"
   ],
   "pr": 2002,
   "reported_ci": {
    "status": "UNKNOWN"
   },
   "source_refs": [
    "61cd7b7524b15664b5a3ab5518cf606a878606956fbe777bd19a712f38793b44",
    "da6e6b357d30235f2e6eb69ce701c3efe9e26ca75785f00c2fc37715d4ff2f77"
   ],
   "stale_votes": [
    {
     "agent": "claude-rco-2",
     "agent_uuid": "00000000-0000-4000-8000-0000000000c2",
     "head": "7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c7c",
     "source_refs": [
      "cf2926b527d8307c04bafe96ba15da9c794a6f83ed739e487a262cd384d60ff7"
     ],
     "status": "rco_pass",
     "verification": "declared"
    }
   ],
   "veto_observations": [],
   "votes": []
  },
  "2003": {
   "base": "UNKNOWN",
   "class": "UNKNOWN",
   "head": "9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e",
   "missing": [
    "gate_eligibility_UNKNOWN"
   ],
   "pr": 2003,
   "reported_ci": {
    "status": "UNKNOWN"
   },
   "source_refs": [
    "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6"
   ],
   "stale_votes": [],
   "veto_observations": [
    {
     "activity": "UNKNOWN",
     "agent": "codex-tools-1",
     "agent_uuid": "00000000-0000-4000-8000-0000000000b1",
     "head": "9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e9e",
     "source_refs": [
      "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6"
     ],
     "status": "blocked",
     "verification": "declared"
    }
   ],
   "votes": []
  }
 },
 "schema": "wd.bridge-mind.v1",
 "scope": "bounded_projection_not_gate",
 "source": {
  "as_of_utc": "2026-10-13T05:10:00Z",
  "blocked": null,
  "coverage": "complete",
  "cursor_end": 1000,
  "cursor_start": 0,
  "duplicate_count": 0,
  "event_count": 10,
  "generation": "g1",
  "last_event_ref": "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6",
  "log_identity": "fixture-log",
  "max_event_utc": "2026-10-13T05:09:00Z",
  "unique_event_count": 10
 },
 "tensions": [
  {
   "actors": [
    "claude-rco-2"
   ],
   "claim": "PR2002 at 8d8d8d8d8d8d has 1 stale vote(s)",
   "lifecycle": "new",
   "observation": "claude-rco-2 rco_pass@7c7c7c7c7c7c; a stale vote cannot approve it",
   "refs": [
    "61cd7b7524b15664b5a3ab5518cf606a878606956fbe777bd19a712f38793b44",
    "cf2926b527d8307c04bafe96ba15da9c794a6f83ed739e487a262cd384d60ff7",
    "da6e6b357d30235f2e6eb69ce701c3efe9e26ca75785f00c2fc37715d4ff2f77"
   ],
   "rule": "stale_vote",
   "severity": "medium",
   "subject": "pr:2002:stale_votes",
   "tension_id": "bb14e7638ad28277"
  },
  {
   "actors": [
    "codex-tools-1"
   ],
   "claim": "codex-tools-1 sent blocked on PR2003 at 9e9e9e9e9e9e",
   "lifecycle": "new",
   "observation": "the veto is observed; whether it is still active is UNKNOWN, never cleared here",
   "refs": [
    "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6"
   ],
   "rule": "veto_observed",
   "severity": "high",
   "subject": "pr:2003:veto:codex-tools-1:9e9e9e9e9e9e",
   "tension_id": "ce522ddcc4c505c8"
  },
  {
   "actors": [
    "claude-rco-1",
    "fable-5"
   ],
   "claim": "claude-rco-1 and fable-5 both hold write claims that are not expired",
   "lifecycle": "new",
   "observation": "overlapping paths: tools ~ tools/x.py",
   "refs": [
    "417c8fa562606237e117251c58d28e6412b8b114a6895d61a6c56204c808d3df",
    "4622a41682f06aa36a1dbe6e35b2e43cf76c2a1537547f257331601da64a6533"
   ],
   "rule": "claim_overlap",
   "severity": "high",
   "subject": "claims:claude-rco-1|00000000-0000-4000-8000-0000000000c1|fable-5|00000000-0000-4000-8000-0000000000f5",
   "tension_id": "f1a70115052a010a"
  }
 ]
}"""
MIND_RB_BLOCKED = {"active": True, "offset": 1000, "reason": "canonical_event_invalid"}
MIND_RB_REFLECTION_TEXT = """{
 "attention": [
  {
   "actors": [],
   "detail": "input_blocked: the log row at offset 1000; was blocked by the projector (canonical_event_invalid); the cursor did not advance past it",
   "kind": "tension_high",
   "rank": 1,
   "refs": [
    "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6"
   ],
   "subject": "tension:9d6934a6ebf58ad7",
   "suggestion_only": true
  },
  {
   "actors": [
    "codex-tools-1"
   ],
   "detail": "veto_observed: codex-tools-1 sent blocked on PR2003 at 9e9e9e9e9e9e; the veto is observed; whether it is still active is UNKNOWN, never cleared here",
   "kind": "tension_high",
   "rank": 2,
   "refs": [
    "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6"
   ],
   "subject": "tension:ce522ddcc4c505c8",
   "suggestion_only": true
  },
  {
   "actors": [
    "claude-rco-1",
    "fable-5"
   ],
   "detail": "claim_overlap: claude-rco-1 and fable-5 both hold write claims that are not expired; overlapping paths: tools ~ tools/x.py",
   "kind": "tension_high",
   "rank": 3,
   "refs": [
    "417c8fa562606237e117251c58d28e6412b8b114a6895d61a6c56204c808d3df",
    "4622a41682f06aa36a1dbe6e35b2e43cf76c2a1537547f257331601da64a6533"
   ],
   "subject": "tension:f1a70115052a010a",
   "suggestion_only": true
  },
  {
   "actors": [
    "claude-rco-2"
   ],
   "detail": "stale_vote: PR2002 at 8d8d8d8d8d8d has 1 stale vote(s); claude-rco-2 rco_pass@7c7c7c7c7c7c; a stale vote cannot approve it",
   "kind": "tension",
   "rank": 4,
   "refs": [
    "61cd7b7524b15664b5a3ab5518cf606a878606956fbe777bd19a712f38793b44",
    "cf2926b527d8307c04bafe96ba15da9c794a6f83ed739e487a262cd384d60ff7",
    "da6e6b357d30235f2e6eb69ce701c3efe9e26ca75785f00c2fc37715d4ff2f77"
   ],
   "subject": "tension:bb14e7638ad28277",
   "suggestion_only": true
  }
 ],
 "next_question": {
  "global": {
   "attention_rank": 1,
   "question": "How can the fleet resolve input_blocked: the log row at offset 1000; was blocked by the projector (canonical_event_invalid); the cursor did not advance past it?",
   "refs": [
    "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6"
   ]
  },
  "lanes": {
   "claude-rco-1": {
    "attention_rank": 3,
    "question": "How can claude-rco-1 resolve claim_overlap: claude-rco-1 and fable-5 both hold write claims that are not expired; overlapping paths: tools ~ tools/x.py?",
    "refs": [
     "417c8fa562606237e117251c58d28e6412b8b114a6895d61a6c56204c808d3df",
     "4622a41682f06aa36a1dbe6e35b2e43cf76c2a1537547f257331601da64a6533"
    ]
   },
   "claude-rco-2": {
    "attention_rank": 4,
    "question": "How can claude-rco-2 resolve stale_vote: PR2002 at 8d8d8d8d8d8d has 1 stale vote(s); claude-rco-2 rco_pass@7c7c7c7c7c7c; a stale vote cannot approve it?",
    "refs": [
     "61cd7b7524b15664b5a3ab5518cf606a878606956fbe777bd19a712f38793b44",
     "cf2926b527d8307c04bafe96ba15da9c794a6f83ed739e487a262cd384d60ff7",
     "da6e6b357d30235f2e6eb69ce701c3efe9e26ca75785f00c2fc37715d4ff2f77"
    ]
   },
   "codex-lead-1": {
    "attention_rank": null,
    "question": "UNKNOWN",
    "refs": [
     "UNKNOWN"
    ]
   },
   "codex-tools-1": {
    "attention_rank": 2,
    "question": "How can codex-tools-1 resolve veto_observed: codex-tools-1 sent blocked on PR2003 at 9e9e9e9e9e9e; the veto is observed; whether it is still active is UNKNOWN, never cleared here?",
    "refs": [
     "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6"
    ]
   },
   "fable-5": {
    "attention_rank": 3,
    "question": "How can fable-5 resolve claim_overlap: claude-rco-1 and fable-5 both hold write claims that are not expired; overlapping paths: tools ~ tools/x.py?",
    "refs": [
     "417c8fa562606237e117251c58d28e6412b8b114a6895d61a6c56204c808d3df",
     "4622a41682f06aa36a1dbe6e35b2e43cf76c2a1537547f257331601da64a6533"
    ]
   }
  }
 },
 "tensions": [
  {
   "actors": [],
   "claim": "the log row at offset 1000",
   "lifecycle": "new",
   "observation": "was blocked by the projector (canonical_event_invalid); the cursor did not advance past it",
   "refs": [
    "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6"
   ],
   "rule": "input_blocked",
   "severity": "high",
   "subject": "offset:1000",
   "tension_id": "9d6934a6ebf58ad7"
  },
  {
   "actors": [
    "claude-rco-2"
   ],
   "claim": "PR2002 at 8d8d8d8d8d8d has 1 stale vote(s)",
   "lifecycle": "new",
   "observation": "claude-rco-2 rco_pass@7c7c7c7c7c7c; a stale vote cannot approve it",
   "refs": [
    "61cd7b7524b15664b5a3ab5518cf606a878606956fbe777bd19a712f38793b44",
    "cf2926b527d8307c04bafe96ba15da9c794a6f83ed739e487a262cd384d60ff7",
    "da6e6b357d30235f2e6eb69ce701c3efe9e26ca75785f00c2fc37715d4ff2f77"
   ],
   "rule": "stale_vote",
   "severity": "medium",
   "subject": "pr:2002:stale_votes",
   "tension_id": "bb14e7638ad28277"
  },
  {
   "actors": [
    "codex-tools-1"
   ],
   "claim": "codex-tools-1 sent blocked on PR2003 at 9e9e9e9e9e9e",
   "lifecycle": "new",
   "observation": "the veto is observed; whether it is still active is UNKNOWN, never cleared here",
   "refs": [
    "cf598881b1996b716a4870dde3ff551274cc22419cd56eb9cde03b20e8ddc9b6"
   ],
   "rule": "veto_observed",
   "severity": "high",
   "subject": "pr:2003:veto:codex-tools-1:9e9e9e9e9e9e",
   "tension_id": "ce522ddcc4c505c8"
  },
  {
   "actors": [
    "claude-rco-1",
    "fable-5"
   ],
   "claim": "claude-rco-1 and fable-5 both hold write claims that are not expired",
   "lifecycle": "new",
   "observation": "overlapping paths: tools ~ tools/x.py",
   "refs": [
    "417c8fa562606237e117251c58d28e6412b8b114a6895d61a6c56204c808d3df",
    "4622a41682f06aa36a1dbe6e35b2e43cf76c2a1537547f257331601da64a6533"
   ],
   "rule": "claim_overlap",
   "severity": "high",
   "subject": "claims:claude-rco-1|00000000-0000-4000-8000-0000000000c1|fable-5|00000000-0000-4000-8000-0000000000f5",
   "tension_id": "f1a70115052a010a"
  }
 ]
}"""


H_2001, H_2002_OLD, H_2002, H_2003 = "6b" * 20, "7c" * 20, "8d" * 20, "9e" * 20
MIND_UUID = {"fable-5": "f5", "claude-rco-1": "c1", "claude-rco-2": "c2", "codex-lead-1": "a1", "codex-tools-1": "b1"}
MIND_SOURCE = {"log_identity": "fixture-log", "generation": "g1", "cursor_start": 0, "cursor_end": 1000,
               "coverage": "complete", "event_count": 10}
MIND_AS_OF = "2026-10-13T05:10:00Z"


def _mind_event(ts, agent, kind, status, task, payload):
    code = MIND_UUID[agent]
    return {"ts_utc": f"2026-10-13T{ts}Z", "agent": agent, "agent_uuid": "00000000-0000-4000-8000-0000000000" + code,
            "session_id": f"s-{code}", "run_id": f"r-{code}", "type": kind, "status": status, "task_id": task,
            "payload": payload}


MIND_EVENTS = [
    _mind_event("05:00:00", "codex-lead-1", "message", "ready", "codex-lead-1/train-2001",
                {"pr": 2001, "head": H_2001, "class": "b", "required_checks_green": True}),
    _mind_event("05:01:00", "codex-lead-1", "decision", "build_consensus_pass", "codex-lead-1/train-2001",
                {"pr": 2001, "head": H_2001}),
    _mind_event("05:02:00", "codex-tools-1", "decision", "build_consensus_pass", "codex-tools-1/train-2001",
                {"pr": 2001, "head": H_2001}),
    _mind_event("05:03:00", "claude-rco-1", "decision", "rco_pass", "claude-rco-1/review-2001",
                {"pr": 2001, "head": H_2001}),
    _mind_event("05:04:00", "codex-lead-1", "message", "ready", "codex-lead-1/train-2002",
                {"pr": 2002, "head": H_2002_OLD, "class": "a", "required_checks_green": True}),
    _mind_event("05:05:00", "claude-rco-2", "decision", "rco_pass", "claude-rco-2/review-2002",
                {"pr": 2002, "head": H_2002_OLD}),
    _mind_event("05:06:00", "fable-5", "message", "head_updated", "fable-5/pr-2002", {"pr": 2002, "head": H_2002}),
    _mind_event("05:07:00", "claude-rco-1", "claim", "active", "claude-rco-1/notes", {"write_scope": ["tools/x.py"]}),
    _mind_event("05:08:00", "fable-5", "claim", "active", "fable-5/board", {"write_scope": ["tools"]}),
    _mind_event("05:09:00", "codex-tools-1", "blocked", "blocked", "codex-tools-1/train-2003",
                {"pr": 2003, "head": H_2003}),
]


def _cli_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _mind_r():
    return json.loads(MIND_R_TEXT)


def _mind_rb():
    value = _mind_r()
    value["source"]["blocked"] = dict(MIND_RB_BLOCKED)
    value.update(json.loads(MIND_RB_REFLECTION_TEXT))
    return value


def _audit(tmp_path):
    root = tmp_path / ".codex-audit"
    (root / "board").mkdir(parents=True)
    return root


def _put(root, value, name="r.json"):
    path = root / "board" / name
    path.write_bytes(value if isinstance(value, bytes) else _cli_bytes(value))
    return path


def _mind(root, path, *extra):
    return board.main(["--mind-snapshot", str(path), "--audit-root", str(root), *extra])


def _view(tmp_path, capsys, value):
    root = _audit(tmp_path)
    assert _mind(root, _put(root, value), "--json") == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


def _refused(tmp_path, capsys, value, *extra, root=None, path=None):
    """Exit 2 with one fixed line, nothing on stdout, and no file written under the audit root."""
    root = root or _audit(tmp_path)
    path = path or _put(root, value)
    before = sorted(str(item) for item in root.rglob("*"))
    assert _mind(root, path, *extra) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("missing-slot board mind mode refused: ") and captured.err.count("\n") == 1
    assert sorted(str(item) for item in root.rglob("*")) == before
    return captured.err


def _keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _keys(item)


def test_the_embedded_snapshots_are_the_actual_reflected_bytes():
    assert hashlib.sha256(_cli_bytes(_mind_r())).hexdigest() == MIND_R_SHA256
    assert hashlib.sha256(_cli_bytes(_mind_rb())).hexdigest() == MIND_RB_SHA256


def test_the_embedded_snapshots_rebuild_from_the_actual_core_and_workspace_when_both_are_on_the_tree():
    if not all((board.REPO_ROOT / "tools" / name).exists() for name in ("wd_bridge_mind.py", "bridge_mind_workspace.py")):
        pytest.skip("the Mind core (PR1900) and workspace (PR1901) are not both on this tree yet")
    core = importlib.import_module("tools.wd_bridge_mind")
    workspace = importlib.import_module("tools.bridge_mind_workspace")
    event_id = importlib.import_module("tools.bridge_compact_view").event_id
    state = core.full_rebuild([{"ref": event_id(event), "event": event} for event in MIND_EVENTS],
                              source=MIND_SOURCE, as_of_utc=MIND_AS_OF)
    views = ((core.snapshot(state, as_of_utc=MIND_AS_OF), MIND_R_SHA256),
             (core.blocked_snapshot(state, as_of_utc=MIND_AS_OF, offset=1000, reason="canonical_event_invalid"),
              MIND_RB_SHA256))
    for snapshot, expected in views:
        text = workspace.serialize(workspace.reflect(json.loads(core.canonical_bytes(snapshot))), as_json=True)
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == expected


def test_the_mind_view_shows_the_snapshot_prs_attention_and_questions_with_their_refs(tmp_path, capsys):
    r = _mind_r()
    view = _view(tmp_path, capsys, r)
    assert (view["schema"], view["mode"], view["authority"]) == (board.MIND_VIEW_SCHEMA, "mind_snapshot", "none")
    assert view["observe_only"] is True and view["gate_evaluated"] is False
    assert view["snapshot_sha256"] == MIND_R_SHA256
    assert view["source"]["blocked"] is False and view["source"]["coverage"] == "complete"
    prs = {pr["pr"]: pr for pr in view["prs"]}
    assert list(prs) == [2001, 2002, 2003]
    assert prs[2001]["ci"]["green_at_head"] is True and prs[2001]["ci"]["head"] == H_2001
    assert [(vote["agent"], vote["at_head"]) for vote in prs[2001]["votes"]] == [
        ("claude-rco-1", True), ("codex-lead-1", True), ("codex-tools-1", True)]
    assert prs[2002]["ci"] == {"status": "UNKNOWN", "head": "UNKNOWN", "verification": "UNKNOWN",
                               "source_refs": ["UNKNOWN"], "green_at_head": False}
    assert [(vote["head"], vote["at_head"]) for vote in prs[2002]["stale_votes"]] == [(H_2002_OLD, False)]
    assert [(veto["agent"], veto["activity"]) for veto in prs[2003]["veto_observations"]] == [
        ("codex-tools-1", "UNKNOWN")]
    for pr, record in prs.items():
        assert record["source_refs"] == r["prs"][str(pr)]["source_refs"]
        assert record["snapshot_missing"] == ["gate_eligibility_UNKNOWN"]
        # Not in the snapshot, so never guessed from agent or git names.
        assert all(record[name] == "UNKNOWN" for name in ("author", "recusal", "rco_presence", "gate_slots"))
    # Attention and the next questions are the reflected ones, unchanged, refs and all.
    assert view["attention"] == r["attention"]
    assert view["next_question"] == r["next_question"]
    assert view["next_question"]["global"]["attention_rank"] == 1 and view["attention"][0]["subject"] == "pr:2001"
    assert view["suppressed_by_block"] == {"attention_ranks": [], "global_question": False, "lane_questions": []}
    # No ready, merge or slot state is inferred.
    assert not {"state", "merge_ready", "signature_list", "would_merge", "approved", "missing"} & set(_keys(view))
    assert view["not_in_snapshot"] == ["author", "recusal", "rco_presence", "gate_slots"]


def test_the_mind_text_and_out_file_carry_the_same_view(tmp_path, capsys):
    root = _audit(tmp_path)
    path = _put(root, _mind_r())
    out = root / "board" / "view.json"
    assert _mind(root, path, "--out", str(out)) == 0
    text = capsys.readouterr().out
    assert text.startswith("mind snapshot " + MIND_R_SHA256[:12])
    assert "attention 1 last_missing_vote pr:2001 [claude-rco-2]" in text
    assert "next question: PR2001 at 6b6b6b6b6b6b is reported CI-green; missing: claude-rco-2." in text
    assert out.read_bytes() == (json.dumps(json.loads(out.read_text(encoding="utf-8")), indent=2, sort_keys=True,
                                           ensure_ascii=False) + "\n").encode("utf-8")
    assert json.loads(out.read_text(encoding="utf-8"))["attention"] == _mind_r()["attention"]


def _json_refs(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("refs", "source_refs"):
                yield from item
            else:
                yield from _json_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _json_refs(item)


@pytest.mark.parametrize("blocked", [False, True], ids=["current", "blocked"])
def test_the_text_carries_every_event_ref_of_the_json_view(tmp_path, capsys, blocked):
    """Kills MUT_TEXT_OMITS_REFS: the default text keeps the provenance of the JSON view, line by line."""
    snapshot = _mind_r()
    if blocked:
        snapshot["source"]["blocked"] = dict(MIND_RB_BLOCKED)
    root = _audit(tmp_path)
    out = root / "board" / "view.json"
    assert _mind(root, _put(root, snapshot), "--out", str(out)) == 0
    text = capsys.readouterr().out
    view = json.loads(out.read_text(encoding="utf-8"))
    refs = {ref for ref in _json_refs(view) if ref != "UNKNOWN"}
    # The actual reflected snapshot: one event ref for each of the ten bridge events, all in the view.
    assert len(refs) == 10
    assert set(re.findall(r"\b[0-9a-f]{64}\b", text)) == refs | {view["snapshot_sha256"]}
    joined = ",".join
    for pr in view["prs"]:
        assert f"#{pr['pr']} {pr['head'][:12]} class {pr['class']}: refs={joined(pr['source_refs'])}\n" in text
        assert f" green at head {pr['ci']['green_at_head']} refs={joined(pr['ci']['source_refs'])}\n" in text
        for vote in pr["votes"] + pr["stale_votes"]:
            assert f"{vote['agent']} {vote['status']}@{vote['head'][:12]} at head {vote['at_head']} " \
                   f"refs={joined(vote['source_refs'])}\n" in text
        for veto in pr["veto_observations"]:
            assert f"veto {veto['agent']} {veto['status']}@{veto['head'][:12]} activity UNKNOWN " \
                   f"refs={joined(veto['source_refs'])}\n" in text
    for item in view["attention"]:
        assert f"{item['detail']} refs={joined(item['refs'])}\n" in text
    for entry in [view["next_question"]["global"], *view["next_question"]["lanes"].values()]:
        assert f"{entry['question']} (attention rank {entry['attention_rank']}, refs={joined(entry['refs'])})" in text
    # UNKNOWN stays explicit: PR2002 has no reported CI, codex-lead-1 has no question.
    assert " CI UNKNOWN@UNKNOWN green at head " in text and "refs=UNKNOWN\n" in text
    assert "  codex-lead-1: UNKNOWN (attention rank None, refs=UNKNOWN)\n" in text
    # A blocked source still hides the PR suggestion and the questions bound to it.
    assert ("pr:2001" in text) is not blocked


@pytest.mark.parametrize("field,value", [("authority", "gate"), ("authority", "merge"), ("scope", "gate"),
                                         ("schema", "wd.bridge-mind.v2")])
def test_a_snapshot_claiming_authority_or_another_scope_is_refused_never_trusted(tmp_path, capsys, field, value):
    """Kills MUT_BOARD_TRUSTS_SNAPSHOT: the board reads only a bounded projection with no authority."""
    snapshot = _mind_r()
    snapshot[field] = value
    assert "schema, authority or scope" in _refused(tmp_path, capsys, snapshot)


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_a_snapshot_with_other_top_level_fields_is_refused(tmp_path, capsys, change):
    snapshot = _mind_r()
    if change == "extra":
        snapshot["merge_ready"] = [2001]
    else:
        del snapshot["lanes"]
    _refused(tmp_path, capsys, snapshot)


@pytest.mark.parametrize("marker", ["object", "missing"])
def test_a_blocked_source_suppresses_every_current_pr_suggestion(tmp_path, capsys, marker):
    """Kills MUT_BLOCK_SHOWS_CURRENT: the reflection still has its PR suggestion, the source says blocked."""
    snapshot = _mind_r()
    if marker == "object":
        snapshot["source"]["blocked"] = dict(MIND_RB_BLOCKED)
    else:
        del snapshot["source"]["blocked"]  # not known to be current is treated as blocked
    view = _view(tmp_path, capsys, snapshot)
    assert view["source"]["blocked"] == (True if marker == "object" else "UNKNOWN")
    assert [item["subject"] for item in view["attention"]] == [item["subject"] for item in snapshot["attention"][1:]]
    assert not any(item["subject"].startswith("pr:") for item in view["attention"])
    assert view["next_question"]["global"] == {"question": "UNKNOWN", "attention_rank": None, "refs": ["UNKNOWN"]}
    assert view["next_question"]["lanes"]["claude-rco-2"]["question"] == "UNKNOWN"
    assert view["next_question"]["lanes"]["codex-tools-1"] == snapshot["next_question"]["lanes"]["codex-tools-1"]
    assert view["suppressed_by_block"] == {"attention_ranks": [1], "global_question": True,
                                           "lane_questions": ["claude-rco-2"]}
    for pr in view["prs"]:
        assert pr["ci"]["green_at_head"] == "UNKNOWN"
        assert all(vote["at_head"] == "UNKNOWN" for vote in pr["votes"])
        assert all(vote["at_head"] is False for vote in pr["stale_votes"])


def test_the_actual_blocked_view_shows_nothing_green_at_a_head(tmp_path, capsys):
    rb = _mind_rb()
    view = _view(tmp_path, capsys, rb)
    assert view["snapshot_sha256"] == MIND_RB_SHA256 and view["source"]["blocked"] is True
    assert view["source"]["blocked_reason"] == "canonical_event_invalid"
    assert view["attention"] == rb["attention"] and view["next_question"] == rb["next_question"]
    assert view["attention"][0]["detail"].startswith("input_blocked:")
    assert all(pr["ci"]["green_at_head"] == "UNKNOWN" for pr in view["prs"])
    assert [vote["at_head"] for vote in view["prs"][0]["votes"]] == ["UNKNOWN"] * 3


def test_old_head_ci_and_votes_are_never_green(tmp_path, capsys):
    snapshot = _mind_r()
    record = snapshot["prs"]["2002"]
    # CI reported green, but for the old head: shown, never green at the current head.
    record["reported_ci"] = {"status": "reported_green", "head": H_2002_OLD, "verification": "declared",
                             "source_refs": record["source_refs"][:1]}
    # A vote in the current list that is bound to an old head, and one not declared, are not current.
    lead, tools = snapshot["prs"]["2001"]["votes"][1], snapshot["prs"]["2001"]["votes"][2]
    lead["head"], tools["verification"] = H_2002_OLD, "unverified"
    # A stale vote stays not current even if it names the current head.
    record["stale_votes"][0]["head"] = H_2002
    view = _view(tmp_path, capsys, snapshot)
    prs = {pr["pr"]: pr for pr in view["prs"]}
    assert prs[2002]["ci"]["status"] == "reported_green" and prs[2002]["ci"]["green_at_head"] is False
    assert [(vote["agent"], vote["at_head"]) for vote in prs[2001]["votes"]] == [
        ("claude-rco-1", True), ("codex-lead-1", False), ("codex-tools-1", False)]
    assert prs[2002]["stale_votes"][0]["at_head"] is False


@pytest.mark.parametrize("change", ["ci_old_head", "ci_unverified", "ci_not_green", "veto", "pr_gone"])
def test_a_pr_suggestion_without_green_ci_at_the_pr_head_or_with_a_veto_is_refused(tmp_path, capsys, change):
    snapshot = _mind_r()
    record = snapshot["prs"]["2001"]
    if change == "ci_old_head":
        record["reported_ci"]["head"] = H_2002_OLD
    elif change == "ci_unverified":
        record["reported_ci"]["verification"] = "unverified"
    elif change == "ci_not_green":
        record["reported_ci"]["status"] = "reported_not_green"
    elif change == "veto":
        record["veto_observations"] = snapshot["prs"]["2003"]["veto_observations"]
    else:
        del snapshot["prs"]["2001"]
    assert "PR suggestion" in _refused(tmp_path, capsys, snapshot)


@pytest.mark.parametrize("change", ["rank_unknown", "refs_differ", "unranked_text", "unknown_rank_refs"])
def test_a_next_question_not_bound_to_its_attention_item_is_refused(tmp_path, capsys, change):
    snapshot = _mind_r()
    entry = snapshot["next_question"]["lanes"]["claude-rco-1"]
    if change == "rank_unknown":
        entry["attention_rank"] = 9
    elif change == "refs_differ":
        entry["refs"] = snapshot["attention"][0]["refs"]
    elif change == "unranked_text":
        snapshot["next_question"]["lanes"]["codex-lead-1"]["question"] = "Merge PR2001 now?"
    else:
        snapshot["next_question"]["lanes"]["codex-lead-1"]["refs"] = snapshot["attention"][0]["refs"]
    assert "next question" in _refused(tmp_path, capsys, snapshot)


@pytest.mark.parametrize("change", ["not_suggestion", "pr_unknown_refs", "bad_ref", "subject_case", "rank_order",
                                    "rank_bool", "extra_key"])
def test_an_attention_item_off_the_interface_is_refused(tmp_path, capsys, change):
    snapshot = _mind_r()
    first = snapshot["attention"][0]
    if change == "not_suggestion":
        first["suggestion_only"] = False
    elif change == "pr_unknown_refs":
        # Every question bound to it carries the same refs, so only the item's own ref check can refuse it.
        first["refs"] = ["UNKNOWN"]
        for entry in [snapshot["next_question"]["global"], *snapshot["next_question"]["lanes"].values()]:
            if entry["attention_rank"] == 1:
                entry["refs"] = ["UNKNOWN"]
        assert "no valid event ref" in _refused(tmp_path, capsys, snapshot)
        return
    elif change == "bad_ref":
        snapshot["attention"][1]["refs"] = ["cf5988"]
    elif change == "subject_case":
        first["subject"] = "PR:2001"
    elif change == "rank_order":
        first["rank"], snapshot["attention"][1]["rank"] = 2, 1
    elif change == "rank_bool":
        first["rank"] = True
    else:
        first["ready"] = True
    _refused(tmp_path, capsys, snapshot)


def test_a_tension_may_carry_unknown_refs_and_is_shown_as_it_is(tmp_path, capsys):
    snapshot = _mind_r()
    snapshot["attention"][3]["refs"] = ["UNKNOWN"]  # no question is bound to rank 4
    view = _view(tmp_path, capsys, snapshot)
    assert view["attention"][3]["refs"] == ["UNKNOWN"]


@pytest.mark.parametrize("change", ["vote_ref", "pr_ref", "ci_ref", "vote_key", "head", "pr_key"])
def test_a_pr_observation_off_the_interface_is_refused(tmp_path, capsys, change):
    snapshot = _mind_r()
    record = snapshot["prs"]["2001"]
    if change == "vote_ref":
        record["votes"][0]["source_refs"] = []
    elif change == "pr_ref":
        record["source_refs"] = ["UNKNOWN"]
    elif change == "ci_ref":
        record["reported_ci"]["source_refs"] = ["not-a-ref"]
    elif change == "vote_key":
        record["votes"][0]["slot"] = "build_lead"
    elif change == "head":
        record["head"] = "6b6b"
    else:
        snapshot["prs"]["2009"] = snapshot["prs"].pop("2001")
    _refused(tmp_path, capsys, snapshot)


def test_a_snapshot_veto_marked_inactive_is_still_shown_with_activity_unknown(tmp_path, capsys):
    snapshot = _mind_r()
    snapshot["prs"]["2003"]["veto_observations"][0]["activity"] = "cleared"
    view = _view(tmp_path, capsys, snapshot)
    assert view["prs"][2]["veto_observations"][0]["activity"] == "UNKNOWN"


# -- Mind mode input and output guard: whole input, decoded, any case or form; refusals never echo ---------------

def _with_detail(word):
    snapshot = _mind_r()
    snapshot["attention"][2]["detail"] += " " + word
    return snapshot


@pytest.mark.parametrize("form", ["plain", "lower", "fullwidth", "escaped", "double_escaped", "key"])
def test_a_guarded_word_anywhere_in_the_snapshot_refuses_the_whole_input(tmp_path, capsys, form):
    word = GUARD_WORDS[0]
    if form == "plain":
        data = _cli_bytes(_with_detail(word))
    elif form == "lower":
        data = _cli_bytes(_with_detail(word.lower()))
    elif form == "fullwidth":
        data = _cli_bytes(_with_detail("".join(chr(ord(char) + 0xFEE0) if char != "_" else char for char in word)))
    elif form == "escaped":
        data = _cli_bytes(_with_detail(word)).replace(word.encode(), _escaped(word).encode())
    elif form == "double_escaped":
        # The text holds a backslash-u escape of a backslash-u escape: two decodes reveal the word.
        data = _cli_bytes(_with_detail("\\u005cu%04x" % ord(word[0]) + word[1:]))
    else:
        snapshot = _mind_r()
        snapshot["lanes"]["k" + word] = snapshot["lanes"].pop("claude-rco-1|00000000-0000-4000-8000-0000000000c1")
        data = _cli_bytes(snapshot)
    root = _audit(tmp_path)
    err = _refused(tmp_path, capsys, None, root=root, path=_put(root, data))
    assert "guarded word" in err
    _assert_no_guard_word(err)


def _nested(text, depth):
    """The text with its first character backslash-u escaped ``depth`` times, one escape inside the next."""
    for _ in range(depth):
        text = "\\u%04x" % ord(text[0]) + text[1:]
    return text


def test_the_guard_unescapes_four_rounds_and_refuses_anything_deeper(tmp_path, capsys):
    # A harmless text escaped three times settles within the four rounds and is shown as it is.
    view = _view(tmp_path, capsys, _with_detail(_nested("A", 3)))
    assert view["attention"][2]["detail"].endswith(" " + _nested("A", 3))
    # A guarded word escaped five times is never decoded far enough to see; it is refused all the same.
    root = _audit(tmp_path / "deep")
    err = _refused(tmp_path, capsys, _with_detail(_nested(GUARD_WORDS[1], 5)), root=root)
    assert "guarded word" in err


def test_every_mind_refusal_text_is_fixed_and_passes_the_guard():
    source = Path(board.__file__).read_text(encoding="utf-8")
    texts = re.findall(r'MindRefused\("([^"]+)"\)', source)
    assert len(texts) >= 20
    for text in texts:
        assert not board._guarded_form(text) and "{" not in text


@pytest.mark.parametrize("data,reason", [(b'{"schema": "wd.bridge-mind.v1", "schema": "x"}', "canonical JSON"),
                                         (b"[]", "canonical JSON"), (b'{"a": NaN}', "canonical JSON"),
                                         (b'{"a": 1e999}', "canonical JSON"), (b'{"a": 9007199254740993}',
                                                                              "canonical JSON"),
                                         (b"\xef\xbb\xbf{}", "canonical JSON"), (b'{"a": "\xff"}', "not UTF-8")],
                         ids=["duplicate_key", "list", "nan", "infinite", "unsafe_int", "bom", "not_utf8"])
def test_input_that_is_not_one_strict_canonical_json_object_is_refused(tmp_path, capsys, data, reason):
    root = _audit(tmp_path)
    assert reason in _refused(tmp_path, capsys, None, root=root, path=_put(root, data))


def test_a_guarded_word_in_input_that_is_not_json_is_refused_as_guarded(tmp_path, capsys):
    root = _audit(tmp_path)
    data = b'{"detail": "' + _escaped(GUARD_WORDS[1]).encode() + b'"'
    assert "guarded word" in _refused(tmp_path, capsys, None, root=root, path=_put(root, data))


def test_the_view_is_guarded_before_its_text(tmp_path):
    with pytest.raises(board.MindRefused) as raised:
        board.serialize_mind_view({"detail": "x " + GUARD_WORDS[0].lower()}, as_json=True)
    assert "board view" in str(raised.value)
    _assert_no_guard_word(str(raised.value))


def test_missing_source_fields_stay_unknown(tmp_path, capsys):
    snapshot = _mind_r()
    for name in ("coverage", "generation", "as_of_utc"):
        del snapshot["source"][name]
    view = _view(tmp_path, capsys, snapshot)
    assert [view["source"][name] for name in ("coverage", "generation", "as_of_utc")] == ["UNKNOWN"] * 3
    assert view["source"]["log_identity"] == "fixture-log" and view["source"]["blocked"] is False


def test_a_snapshot_swapped_after_its_path_check_is_refused(tmp_path, capsys, monkeypatch):
    real_fstat = os.fstat

    def other_file(fd):
        info = real_fstat(fd)
        return os.stat_result((info.st_mode, info.st_ino + 1) + tuple(info)[2:])

    monkeypatch.setattr(board.os, "fstat", other_file)
    assert "changed while it was opened" in _refused(tmp_path, capsys, _mind_r())


def test_an_input_over_the_size_limit_is_refused(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(board, "MIND_INPUT_LIMIT", 1000)
    assert "larger" in _refused(tmp_path, capsys, _mind_r())


# -- Mind mode paths: only an owned .codex-audit tree, no traversal, link, junction or hard link -------------------

def test_a_path_with_a_parent_reference_or_outside_the_audit_root_is_refused(tmp_path, capsys):
    root = _audit(tmp_path)
    path = _put(root, _mind_r())
    _refused(tmp_path, capsys, None, root=root, path=root / "board" / ".." / "board" / "r.json")
    outside = tmp_path / "r.json"
    outside.write_bytes(_cli_bytes(_mind_r()))
    _refused(tmp_path, capsys, None, root=root, path=outside)
    assert "audit root itself" in _refused(tmp_path, capsys, None, root=root, path=root)
    assert "regular file" in _refused(tmp_path, capsys, None, root=root, path=root / "board")
    _refused(tmp_path, capsys, None, root=root, path=root / "board" / "missing.json")
    other = tmp_path / "audit"
    other.mkdir()
    (other / "r.json").write_bytes(path.read_bytes())
    _refused(tmp_path, capsys, None, root=other, path=other / "r.json")


def test_a_hard_linked_snapshot_is_refused(tmp_path, capsys):
    root = _audit(tmp_path)
    path = _put(root, _mind_r())
    os.link(path, root / "board" / "alias.json")
    assert "one link" in _refused(tmp_path, capsys, None, root=root, path=path)


def test_a_symlinked_snapshot_or_parent_is_refused(tmp_path, capsys):
    root = _audit(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    (real / "r.json").write_bytes(_cli_bytes(_mind_r()))
    try:
        os.symlink(real / "r.json", root / "board" / "link.json")
        os.symlink(real, root / "linked", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need a privilege this run does not have")
    assert "link" in _refused(tmp_path, capsys, None, root=root, path=root / "board" / "link.json")
    assert "link" in _refused(tmp_path, capsys, None, root=root, path=root / "linked" / "r.json")


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows reparse point")
def test_a_junction_parent_is_refused(tmp_path, capsys):
    import _winapi

    root = _audit(tmp_path)
    real = tmp_path / "real"
    real.mkdir()
    (real / "r.json").write_bytes(_cli_bytes(_mind_r()))
    _winapi.CreateJunction(str(real), str(root / "junction"))
    assert "link or junction" in _refused(tmp_path, capsys, None, root=root, path=root / "junction" / "r.json")


def test_out_must_be_a_new_file_in_the_same_audit_root(tmp_path, capsys):
    root = _audit(tmp_path)
    path = _put(root, _mind_r())
    existing = root / "board" / "old.json"
    existing.write_text("keep", encoding="utf-8")
    assert _mind(root, path, "--out", str(existing)) == 2
    assert existing.read_text(encoding="utf-8") == "keep"
    outside = tmp_path / "view.json"
    assert _mind(root, path, "--out", str(outside)) == 2
    assert not outside.exists()
    assert _mind(root, path, "--out", str(root / "board" / "new" / "view.json")) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.count("refused") == 3


def test_a_failed_out_write_leaves_no_file_so_the_next_attempt_can_write(tmp_path, capsys, monkeypatch):
    # Codex P2 PRRT_kwDORYf3RM6r0aLN: the exclusive create succeeds, then the write fails (a full audit volume).
    # The refusal must keep its "writes nothing" contract: no empty or partial file stays to block the retry.
    root = _audit(tmp_path)
    path = _put(root, _mind_r())
    out = root / "board" / "view.json"
    real_open = open

    class _FullVolume:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()
            return False

        def write(self, text):
            self.handle.write(text[:10])
            self.handle.flush()
            raise OSError(28, "No space left on device")

    def failing_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        return _FullVolume(handle) if "x" in mode else handle

    monkeypatch.setattr(board, "open", failing_open, raising=False)
    assert _mind(root, path, "--out", str(out)) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "refused" in captured.err and "nothing is kept" in captured.err
    assert not out.exists()
    monkeypatch.undo()
    assert _mind(root, path, "--out", str(out)) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["attention"] == _mind_r()["attention"]


def test_a_failed_cleanup_keeps_the_partial_file_and_never_says_nothing_is_kept(tmp_path, capsys, monkeypatch):
    # Tools finding tools-pr1902-partial-output-cleanup-refusal-20261013 (12:13Z): the write fails with OSError 28
    # and removing the partial file fails with PermissionError 13. The partial file stays as evidence, the refusal
    # says so with a fixed reason that echoes neither the path nor the error, and a file that existed before the
    # run is never touched.
    root = _audit(tmp_path)
    path = _put(root, _mind_r())
    out = root / "board" / "view.json"
    other = root / "board" / "earlier.json"
    other.write_text("keep", encoding="utf-8")
    real_open, real_unlink = open, board.os.unlink
    removed = []

    class _FullVolume:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()
            return False

        def write(self, text):
            self.handle.write(text[:10])
            self.handle.flush()
            raise OSError(28, "No space left on device")

    def failing_open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        return _FullVolume(handle) if "x" in mode else handle

    def locked_unlink(target, *args, **kwargs):
        removed.append(Path(target))
        if Path(target) == out:
            raise PermissionError(13, "Access is denied", str(target))
        return real_unlink(target, *args, **kwargs)

    monkeypatch.setattr(board, "open", failing_open, raising=False)
    monkeypatch.setattr(board.os, "unlink", locked_unlink)
    assert _mind(root, path, "--out", str(out)) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "refused" in captured.err
    assert "partial file could not be removed" in captured.err and "nothing is kept" not in captured.err
    assert str(out) not in captured.err and "view.json" not in captured.err and "Access is denied" not in captured.err
    assert removed == [out]
    assert out.exists() and len(out.read_bytes()) == 10  # the partial file stays as evidence
    assert other.read_text(encoding="utf-8") == "keep"


# -- Mind mode isolation and the unchanged default gate-audit mode ------------------------------------------------

_ISOLATION_CHILD = r"""
import json, os, sys
repo, args, result = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
seen = []
WATCH = ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile", "os.fork")
def hook(event, hook_args):
    if event in WATCH or event.startswith(("socket.", "http.", "urllib.", "ftplib.", "smtplib.")):
        seen.append(event)
    elif event == "open" and hook_args and isinstance(hook_args[0], (str, bytes, os.PathLike)):
        name = os.fsdecode(hook_args[0]).replace("\\", "/")
        if name.endswith(("idle_consensus_auto_merge.py", "pr_status_snapshot.py", "events.jsonl")) \
                or "/.agent-bridge/" in name:
            seen.append("open:" + name.rsplit("/", 1)[-1])
sys.addaudithook(hook)
LEGACY = ("subprocess", "tempfile", "tools.idle_consensus_auto_merge", "tools.pr_status_snapshot")
before = [name for name in LEGACY if name in sys.modules]
sys.path.insert(0, repo)
from tools import wd_pr_missing_slot_board as board
if args == ["legacy-run"]:
    code = board._run([sys.executable, "-c", "pass"]).returncode
else:
    code = board.main(args)
after = [name for name in LEGACY if name in sys.modules]
with open(result, "w", encoding="utf-8") as handle:
    json.dump({"code": code, "before": before, "after": after, "seen": seen}, handle)
"""


def _isolated(tmp_path, args):
    result = tmp_path / "isolation.json"
    done = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", _ISOLATION_CHILD, str(board.REPO_ROOT),
                           json.dumps(args), str(result)], capture_output=True, text=True, timeout=300)
    assert result.exists(), done.stderr[-2000:]
    return json.loads(result.read_text(encoding="utf-8"))


def test_the_mind_mode_never_imports_legacy_modules_runs_a_process_or_opens_the_gate(tmp_path):
    root = _audit(tmp_path)
    path = _put(root, _mind_r())
    seen = _isolated(tmp_path, ["--mind-snapshot", str(path), "--audit-root", str(root), "--out",
                                str(root / "board" / "view.json"), "--json"])
    assert seen == {"code": 0, "before": [], "after": [], "seen": []}
    assert json.loads((root / "board" / "view.json").read_text(encoding="utf-8"))["gate_evaluated"] is False


def test_the_isolation_probe_sees_the_legacy_process_runner(tmp_path):
    """Control: the same probe reports the gate-audit mode's process runner and its import."""
    seen = _isolated(tmp_path, ["legacy-run"])
    assert seen["code"] == 0 and seen["before"] == [] and "subprocess" in seen["after"]
    assert "subprocess.Popen" in seen["seen"]


def test_the_default_mode_still_requires_repo_and_never_reads_a_mind_snapshot(tmp_path, monkeypatch, capsys):
    def no_run(command):
        raise AssertionError("a command ran without --repo")

    monkeypatch.setattr(board, "_run", no_run)
    with pytest.raises(SystemExit) as raised:
        board.main([])
    assert raised.value.code == 2
    assert "error: the following arguments are required: --repo" in capsys.readouterr().err

    def no_mind(*_args, **_kwargs):
        raise AssertionError("the gate-audit mode read a mind snapshot")

    monkeypatch.setattr(board, "read_mind_snapshot", no_mind)
    monkeypatch.setattr(board, "build_mind_view", no_mind)
    fake = _FakeGitHub([_pr(81)], {81: _gate([RECEIPT], approved=("build_lead", "build_tools", "claude-rco-2"))})
    monkeypatch.setattr(board, "_run", fake)
    out = tmp_path / "board.json"
    assert board.main(["--repo", "o/r", "--out", str(out)]) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["schema"] == board.SCHEMA and result["merge_ready"] == [81] and fake.gate_runs == [81]


@pytest.mark.parametrize("legacy", [["--repo", "o/r"], ["--pr", "5"], ["--hold", "5=x"], ["--main-sha", "m"],
                                    ["--bridge-root", "b"]])
def test_gate_audit_flags_with_the_mind_mode_are_refused_and_run_nothing(tmp_path, capsys, monkeypatch, legacy):
    calls = []
    monkeypatch.setattr(board, "_run", lambda command: calls.append(command))
    root = _audit(tmp_path)
    assert "gate-audit flags" in _refused(tmp_path, capsys, _mind_r(), *legacy, root=root)
    assert calls == []


def test_the_mind_mode_needs_both_of_its_flags(tmp_path, capsys):
    root = _audit(tmp_path)
    path = _put(root, _mind_r())
    assert board.main(["--mind-snapshot", str(path)]) == 2
    assert board.main(["--audit-root", str(root)]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err.count("given together") == 2


def test_the_gate_audit_process_runner_still_runs_a_command():
    assert board._run([sys.executable, "-c", "print(7)"]).stdout.strip() == "7"
