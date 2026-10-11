# SPDX-License-Identifier: BUSL-1.1
"""Observe-only signing-package route check.

Three layers:
* every installed-driver profile is re-derived from the driver source at its own
  commit (admitted options, default policy, refusal wording, RCO set, charter);
* the expected refusals are proven against reports from the real driver
  (``merge_with_bridge_receipt`` with a fake ``gh`` runner), including extra
  refusals the manual route must not skip (retained accepted-queue WAL, draft);
* named mutants show the oracles fail when the route or the comparison breaks.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
from typing import Any, Sequence

import pytest

import tools.check_signing_package_routes as routes
import tools.idle_consensus_auto_merge as gate_module
from tools.check_signing_package_routes import (
    DRIVER_PROFILES,
    PACKAGE_SCHEMA,
    DriverProfile,
    charter_digest,
    check_package,
    main,
)
from tools.merge_with_bridge_receipt import merge_with_bridge_receipt
from waggledance.core.idle_consensus_charter import DEFAULT_CHARTER_PATH

ROOT = Path(__file__).resolve().parents[2]

HEAD = "1234567890abcdef1234567890abcdef12345678"
BASE = "fedcba0987654321fedcba0987654321fedcba09"
AUTHOR = "claude-rco-2"
TASK = f"{AUTHOR}/signing-route-check-test"
NOW = datetime(2026, 6, 14, 6, 20, tzinfo=timezone.utc)
PR = 1174
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
}
TEST_COMMIT = "abcdefabcdefabcdefabcdefabcdefabcdefabcd"
TEST_MANIFEST = "AB" * 32
POLICY_OPTIONS = {"--review-policy", "--grok-fallback", "--operator-path-exception-json"}
A5_ADMITTED = {
    "--expected-head", "--expected-base-sha", "--from-agent", "--repo", "--out-dir",
    "--consensus-proposal-id", "--bridge-task-id", "--json", "--method", "--apply",
    "--pr-status-file",
}
DENYLIST_REFUSAL = "path gate failed: denylist hit"


# --- profile re-derivation from the driver source at its commit ---------------

def _git_show(commit: str, path: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{commit}:{path}"],
        check=True, capture_output=True,
    ).stdout.decode("utf-8")


def _commit_available(commit: str) -> bool:
    probe = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-e", f"{commit}^{{commit}}"],
        capture_output=True,
    )
    return probe.returncode == 0


def _derive_profile_errors(profile: DriverProfile) -> list[str]:
    """Every profile field that the driver source at its commit contradicts."""
    commit = profile.source_commit
    errors: list[str] = []
    context = _git_show(commit, "ops/windows/reboot/BridgeCodeContext.ps1")
    region = re.search(r"\$valued = @\((.*?)\$options = @\{\}", context, re.S)
    admitted = set(re.findall(r"'(--[a-z-]+)'", region.group(1))) if region else set()
    if admitted != A5_ADMITTED or admitted & POLICY_OPTIONS:
        errors.append(f"A5 admitted options {sorted(admitted)}")
    if f"[string]$options['--repo'] -cne '{profile.repo}'" not in context:
        errors.append("A5 repository binding differs")
    driver = _git_show(commit, "tools/merge_with_bridge_receipt.py")
    if not re.search(
        r'"--review-policy",\s*choices=sorted\(REVIEW_POLICIES\),\s*default=REVIEW_POLICY_LEGACY',
        driver,
    ):
        errors.append("driver default review policy is not legacy")
    gate = _git_show(commit, "tools/idle_consensus_auto_merge.py")
    legacy = re.search(r'^REVIEW_POLICY_LEGACY = "([^"]+)"$', gate, re.M)
    if legacy is None or legacy.group(1) != profile.review_policy:
        errors.append("legacy policy name differs")
    if 'blockers.append(f"path gate failed: {path_gate.reason}")' not in gate:
        errors.append("path gate refusal wording differs")
    if "BRIDGE_CONSENSUS_RCO_AGENTS = DEFAULT_RCO_AGENTS" not in gate:
        errors.append("legacy RCO set is not DEFAULT_RCO_AGENTS")
    rco = _git_show(commit, "tools/check_rco_pass_present.py")
    rco_set = re.search(r"^DEFAULT_RCO_AGENTS: tuple\[str, \.\.\.\] = \(([^)]*)\)", rco, re.M)
    if rco_set is None or tuple(re.findall(r'"([^"]+)"', rco_set.group(1))) != profile.recognized_rcos:
        errors.append("recognized RCO identities differ")
    charter_code = _git_show(commit, "waggledance/core/idle_consensus_charter.py")
    for reason, refusal in profile.path_refusals.items():
        if f'reason="{reason}"' not in charter_code or refusal != f"path gate failed: {reason}":
            errors.append(f"path refusal for {reason!r} differs")
    if set(profile.path_refusals) != {"denylist hit", "paths not on allowlist"}:
        errors.append("path refusal reasons differ")
    charter = subprocess.run(
        ["git", "-C", str(ROOT), "show", f"{commit}:docs/architecture/IDLE_AUTONOMY_CHARTER.md"],
        check=True, capture_output=True,
    ).stdout.replace(b"\r\n", b"\n")
    if hashlib.sha256(charter).hexdigest().upper() != profile.charter_sha256:
        errors.append("charter digest differs")
    if not re.fullmatch(r"[0-9A-F]{64}", profile.manifest_sha256):
        errors.append("manifest digest malformed")
    return errors


@pytest.mark.parametrize("commit", sorted(DRIVER_PROFILES))
def test_driver_profile_matches_its_own_source(commit: str) -> None:
    if not _commit_available(commit):
        pytest.skip(f"driver commit {commit} is not in this clone (CI fetches full history)")
    profile = DRIVER_PROFILES[commit]
    assert profile.source_commit == commit
    assert _derive_profile_errors(profile) == []


@pytest.mark.parametrize(
    "mutant",
    [
        pytest.param({"review_policy": "rule12"}, id="MUT_PROFILE_POLICY_DRIFT"),
        pytest.param({"recognized_rcos": ("claude-rco-1", "claude-rco-2", "codex-tools-1")},
                     id="MUT_PROFILE_RCO_SET_WIDENED"),
        pytest.param({"path_refusals": {"denylist hit": "path gate failed: denylist",
                                        "paths not on allowlist": "path gate failed: paths not on allowlist"}},
                     id="MUT_PROFILE_REFUSAL_WORDING"),
        pytest.param({"charter_sha256": "00" * 32}, id="MUT_PROFILE_CHARTER_DIGEST"),
        pytest.param({"repo": "example/repo"}, id="MUT_PROFILE_REPO"),
    ],
)
def test_mutated_profile_is_caught_by_source_rederivation(mutant: dict[str, Any]) -> None:
    commit = sorted(DRIVER_PROFILES)[0]
    if not _commit_available(commit):
        pytest.skip(f"driver commit {commit} is not in this clone (CI fetches full history)")
    assert _derive_profile_errors(replace(DRIVER_PROFILES[commit], **mutant)) != []


# --- the real driver produces the expected refusals ---------------------------

def _runner(paths: Sequence[str], *, draft: bool = False):
    payload = {
        "number": PR, "title": "test(bridge): signing route", "headRefOid": HEAD,
        "headRefName": TASK, "baseRefOid": BASE, "baseRefName": "main",
        "mergeable": "MERGEABLE", "state": "OPEN", "isDraft": draft,
        "url": f"https://github.com/example/repo/pull/{PR}", "reviewDecision": "",
        "updatedAt": "2026-07-24T09:00:00Z", "changedFiles": len(paths),
        "author": {"login": "Ahkeratmehilaiset", "name": "", "email": ""},
        "commits": [{"oid": HEAD, "authors": [{"name": "Test Author", "email": "author@example.invalid", "login": ""}]}],
        "statusCheckRollup": [
            {"name": "unified", "conclusion": "SUCCESS", "status": "COMPLETED"},
            {"name": "test (3.13)", "conclusion": "SUCCESS", "status": "COMPLETED"},
        ],
    }

    def run(command: Sequence[str]) -> SimpleNamespace:
        call = list(command)
        if call[:3] == ["gh", "pr", "view"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
        if call[:2] == ["gh", "api"] and "/git/ref/heads/" in call[4]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                {"ref": "refs/heads/main", "object": {"type": "commit", "sha": BASE}}), stderr="")
        if call[:2] == ["gh", "api"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(
                [{"filename": p, "status": "modified"} for p in paths]), stderr="")
        if call[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(returncode=0, stdout="".join(
                f"diff --git a/{p} b/{p}\n" for p in paths))
        raise AssertionError(f"no-apply run issued an unexpected command: {call}")

    return run


def _event(agent: str, status: str, ts_utc: str) -> dict[str, Any]:
    return {
        "ts_utc": ts_utc, "agent": agent, "agent_uuid": AGENT_UUIDS[agent],
        "type": "decision", "status": status, "task_id": TASK,
        "message": f"{status} at head {HEAD}", "payload": {"head": HEAD, "pr": PR},
    }


def _events(tmp_path: Path, paths: Sequence[str]) -> Path:
    events = [
        {
            "ts_utc": "2026-06-14T06:09:00Z", "agent": AUTHOR, "agent_uuid": AGENT_UUIDS[AUTHOR],
            "type": "claim", "status": "active", "task_id": TASK, "write_scope": list(paths),
            "message": f"claim at head {HEAD}", "payload": {"head": HEAD, "pr": PR},
        },
        _event("codex-lead-1", "build_consensus_pass", "2026-06-14T06:10:00Z"),
        _event("codex-tools-1", "build_consensus_pass", "2026-06-14T06:11:00Z"),
        _event("claude-rco-1", "rco_pass", "2026-06-14T06:12:00Z"),
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e, sort_keys=True) for e in events), encoding="utf-8")
    return path


def _no_apply(tmp_path: Path, paths: Sequence[str], *, draft: bool = False) -> dict[str, Any]:
    return merge_with_bridge_receipt(
        pr_number=PR, repo="example/repo", events_path=_events(tmp_path, paths),
        out_dir=tmp_path / "out", expected_head=HEAD, expected_base_sha=BASE,
        consensus_proposal_id=TASK, from_agent="codex-lead-1", bridge_task_id=TASK,
        now_utc=NOW, runner=_runner(paths, draft=draft),
    )


def _test_profiles() -> dict[str, DriverProfile]:
    # Same refusal contract as the installed profile, bound to the checkout's
    # charter so the end-to-end runs exercise the current driver source.
    installed = DRIVER_PROFILES[sorted(DRIVER_PROFILES)[0]]
    return {TEST_COMMIT: replace(
        installed, source_commit=TEST_COMMIT, manifest_sha256=TEST_MANIFEST,
        charter_sha256=charter_digest(DEFAULT_CHARTER_PATH), repo="example/repo",
    )}


def _package(paths: Sequence[str], report: Any = None, *, holder: str = "claude-rco-1",
             commit: str = TEST_COMMIT, manifest: str = TEST_MANIFEST) -> dict[str, Any]:
    entry: dict[str, Any] = {"pr": PR, "head": HEAD, "base": BASE,
                             "changed_paths": list(paths), "rco_slot_holder": holder}
    if report is not None:
        entry["no_apply_report"] = report
    return {"schema": PACKAGE_SCHEMA, "prs": [entry],
            "driver": {"source_commit": commit, "manifest_sha256": manifest}}


def _only(result: dict[str, Any]) -> dict[str, Any]:
    assert len(result["prs"]) == 1
    return result["prs"][0]


def _assert_driver_route_consistent(tmp_path: Path) -> None:
    paths = ["tools/agent_next_task.py"]
    report = _no_apply(tmp_path, paths)
    assert report["decision"] == "merge_receipt_ready"
    pr = _only(check_package(_package(paths, report), profiles=_test_profiles()))
    assert (pr["route"], pr["no_apply"]["status"], pr["verdict"]) == ("driver", "match", "consistent")


def _assert_denylist_manual_route_consistent(tmp_path: Path) -> None:
    paths = ["CLAUDE.md"]
    report = _no_apply(tmp_path, paths)
    assert report["errors"] == [DENYLIST_REFUSAL]
    pr = _only(check_package(_package(paths, report), profiles=_test_profiles()))
    assert pr["route"] == "manual"
    assert pr["expected_refusals"] == [DENYLIST_REFUSAL]
    assert (pr["no_apply"]["status"], pr["verdict"]) == ("match", "consistent")


def _assert_retained_wal_refusal_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate_module, "check_accepted_queue_complete", lambda **_: {
        "ok": False, "complete": False, "decision": "retained_wal_unresolved"})
    paths = ["CLAUDE.md"]
    report = _no_apply(tmp_path, paths)
    pr = _only(check_package(_package(paths, report), profiles=_test_profiles()))
    assert pr["route"] == "manual"
    assert pr["no_apply"]["status"] == "mismatch"
    assert pr["no_apply"]["unexpected_refusals"] == [
        "accepted bridge queue is not fully visible in canonical history: retained_wal_unresolved"]
    assert pr["verdict"] == "operator_review_required"


def test_driver_route_matches_real_ready_report(tmp_path: Path) -> None:
    _assert_driver_route_consistent(tmp_path)


def test_denylisted_path_is_manual_with_exactly_the_denylist_refusal(tmp_path: Path) -> None:
    _assert_denylist_manual_route_consistent(tmp_path)


def test_allowlist_miss_is_manual_with_exactly_the_allowlist_refusal(tmp_path: Path) -> None:
    paths = ["waggledance/adapters/signing_route_probe.py"]
    report = _no_apply(tmp_path, paths)
    pr = _only(check_package(_package(paths, report), profiles=_test_profiles()))
    assert pr["route"] == "manual"
    assert pr["expected_refusals"] == ["path gate failed: paths not on allowlist"]
    assert (pr["no_apply"]["status"], pr["verdict"]) == ("match", "consistent")


def test_package_that_omits_a_denylisted_path_does_not_bind(tmp_path: Path) -> None:
    report = _no_apply(tmp_path, ["CLAUDE.md", "tools/agent_next_task.py"])
    assert report["errors"] == [DENYLIST_REFUSAL]
    result = check_package(_package(["tools/agent_next_task.py"], report),
                           profiles=_test_profiles())
    pr = _only(result)
    assert pr["route"] == "driver"
    assert pr["no_apply"]["status"] == "binding_mismatch"
    assert pr["no_apply"]["detail"] == "gate path decision differs from the package paths"
    assert result["verdict"] == "operator_review_required"


def test_driver_identity_is_reported_as_the_package_assertion(tmp_path: Path) -> None:
    paths = ["CLAUDE.md"]
    result = check_package(_package(paths, _no_apply(tmp_path, paths)),
                           profiles=_test_profiles())
    assert result["driver"]["identity_source"] == "package_assertion"
    assert _only(result)["no_apply"]["driver_identity"] == "unverified"


@pytest.mark.parametrize(
    "command",
    [
        ["gh", "pr", "merge", str(PR), "--repo", "example/repo",
         "--match-head-commit=" + "a" * 40, "--squash"],
        ["gh", "pr", "merge", str(PR + 1), "--repo", "example/repo",
         f"--match-head-commit={HEAD}", "--squash"],
        ["gh", "pr", "merge", str(PR), "--repo", "other/repo",
         f"--match-head-commit={HEAD}", "--squash"],
        ["gh", "pr", "merge", str(PR), f"--match-head-commit={HEAD}", "--squash"],
        ["gh", "pr", "merge", str(PR), "--repo", "example/repo",
         f"--match-head-commit={HEAD}", "--squash", "--admin"],
        ["gh", "pr", "merge", str(PR), "--repo", "example/repo",
         f"--match-head-commit={HEAD}", "--auto"],
        "gh pr merge",
    ],
)
def test_ready_report_must_carry_the_exact_merge_command(
    tmp_path: Path, command: Any,
) -> None:
    paths = ["tools/agent_next_task.py"]
    report = _no_apply(tmp_path, paths)
    assert report["gh_command"] == ["gh", "pr", "merge", str(PR), "--repo", "example/repo",
                                    f"--match-head-commit={HEAD}", "--squash"]
    pr = _only(check_package(_package(paths, {**report, "gh_command": command}),
                             profiles=_test_profiles()))
    assert pr["no_apply"]["status"] == "binding_mismatch"
    assert pr["verdict"] == "operator_review_required"


def test_retained_accepted_queue_wal_is_an_unexpected_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_retained_wal_refusal_flagged(tmp_path, monkeypatch)


def test_draft_pr_refusal_is_not_hidden_behind_the_denylist(tmp_path: Path) -> None:
    paths = ["CLAUDE.md"]
    report = _no_apply(tmp_path, paths, draft=True)
    pr = _only(check_package(_package(paths, report), profiles=_test_profiles()))
    assert pr["no_apply"]["unexpected_refusals"] == ["PR must not be a draft"]
    assert pr["verdict"] == "operator_review_required"


def test_driver_route_with_a_real_refusal_is_flagged(tmp_path: Path) -> None:
    paths = ["tools/agent_next_task.py"]
    report = _no_apply(tmp_path, paths, draft=True)
    pr = _only(check_package(_package(paths, report), profiles=_test_profiles()))
    assert pr["route"] == "driver"
    assert pr["no_apply"]["unexpected_refusals"] == ["PR must not be a draft"]
    assert pr["verdict"] == "operator_review_required"


def test_MUT_ROUTE_IGNORES_DENYLIST_is_caught(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = routes.evaluate_paths
    monkeypatch.setattr(routes, "evaluate_paths", lambda charter, paths: real(charter, ["tools/x.py"]))
    with pytest.raises(AssertionError):
        _assert_denylist_manual_route_consistent(tmp_path)


def test_MUT_SUBSET_ACCEPTS_EXTRA_REFUSAL_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def subset(observed: list[str], expected: list[str]) -> dict[str, Any]:
        ok = set(expected) <= set(observed)
        return {"status": "match" if ok else "mismatch", "unexpected_refusals": [],
                "missing_refusals": [], "duplicate_refusals": False}

    monkeypatch.setattr(routes, "_compare_refusals", subset)
    with pytest.raises(AssertionError):
        _assert_retained_wal_refusal_flagged(tmp_path, monkeypatch)


# --- binding and fail-closed cases ------------------------------------------

def _ready(head: str = HEAD, pr: int = PR, base: str = BASE) -> dict[str, Any]:
    return {"ok": True, "decision": "merge_receipt_ready", "pr_number": pr,
            "expected_head": head, "expected_base_sha": base,
            "gh_command": ["gh", "pr", "merge", str(PR), "--repo", "example/repo",
                           f"--match-head-commit={HEAD}", "--squash"],
            "gh_merge_attempted": False, "merge_executed": False, "apply": False,
            "errors": []}


def _refusal(reasons: list[str], *, head: str = HEAD, policy: str = "bridge_consensus_v1",
             errors: list[str] | None = None, base: str = BASE,
             blocked: list[str] | None = None) -> dict[str, Any]:
    return {
        "ok": False, "decision": "receipt_preflight_failed", "stage": "receipt",
        "pr_number": PR, "errors": list(reasons if errors is None else errors),
        "gh_merge_attempted": False, "merge_executed": False,
        "receipt_report": {"decision": "merge_plan_not_receipt_eligible", "gate_report": {
            "pr_number": PR, "expected_head": head, "review_policy": policy,
            "reasons": list(reasons), "base_gate": {"expected_base_sha": base},
            "path_gate": {"allowed": False, "reason": "denylist hit",
                          "blocked_paths": ["CLAUDE.md"] if blocked is None else blocked,
                          "unmatched_paths": [], "code_pattern_hits": []}}},
    }


@pytest.mark.parametrize(
    ("commit", "manifest", "charter_change", "error"),
    [
        ("0" * 40, TEST_MANIFEST, False, "no driver profile for this source commit"),
        (TEST_COMMIT, "CD" * 32, False, "deployment manifest differs from the profile"),
        (TEST_COMMIT, TEST_MANIFEST, True, "charter differs from the profiled driver's charter"),
    ],
)
def test_unbound_driver_never_reports_consistent(
    tmp_path: Path, commit: str, manifest: str, charter_change: bool, error: str,
) -> None:
    charter = None
    if charter_change:
        charter = tmp_path / "charter.md"
        charter.write_bytes(DEFAULT_CHARTER_PATH.read_bytes() + b"\n* `extra/**`\n")
    result = check_package(_package(["CLAUDE.md"], _refusal([DENYLIST_REFUSAL]),
                                    commit=commit, manifest=manifest),
                           charter_path=charter, profiles=_test_profiles())
    assert result["driver"]["bound"] is False
    assert result["driver"]["binding_errors"] == [error]
    pr = _only(result)
    assert pr["route"] == "manual"
    assert pr["expected_refusals"] is None
    assert pr["no_apply"]["status"] == "unbound_expectation"
    assert result["verdict"] == pr["verdict"] == "operator_review_required"


def test_charter_digest_ignores_checkout_line_endings(tmp_path: Path) -> None:
    lf = DEFAULT_CHARTER_PATH.read_bytes().replace(b"\r\n", b"\n")
    crlf = tmp_path / "crlf.md"
    crlf.write_bytes(lf.replace(b"\n", b"\r\n"))
    assert charter_digest(crlf) == charter_digest(DEFAULT_CHARTER_PATH)


@pytest.mark.parametrize("holder", ["codex-tools-1", "grok-scout-1"])
def test_rco_slot_the_legacy_driver_cannot_seat_is_manual_and_unbound(holder: str) -> None:
    result = check_package(_package(["tools/agent_next_task.py"], _refusal(["x"]), holder=holder),
                           profiles=_test_profiles())
    pr = _only(result)
    assert pr["route"] == "manual"
    assert pr["route_reasons"] == [
        f"driver policy bridge_consensus_v1 cannot seat RCO slot holder {holder}"]
    assert pr["expected_refusals"] is None
    assert pr["verdict"] == "operator_review_required"


def test_missing_report_is_pending_not_consistent() -> None:
    result = check_package(_package(["CLAUDE.md"]), profiles=_test_profiles())
    assert _only(result)["no_apply"]["status"] == "no_report"
    assert result["verdict"] == "pending_no_apply"


def test_missing_expected_refusal_is_a_mismatch() -> None:
    pr = _only(check_package(_package(["CLAUDE.md"], _ready()), profiles=_test_profiles()))
    assert pr["no_apply"]["missing_refusals"] == [DENYLIST_REFUSAL]
    assert pr["verdict"] == "operator_review_required"


def test_duplicate_refusal_is_a_mismatch() -> None:
    report = _refusal([DENYLIST_REFUSAL, DENYLIST_REFUSAL])
    pr = _only(check_package(_package(["CLAUDE.md"], report), profiles=_test_profiles()))
    assert pr["no_apply"]["duplicate_refusals"] is True
    assert pr["no_apply"]["status"] == "mismatch"


@pytest.mark.parametrize(
    ("report", "status"),
    [
        (_ready(head="a" * 40), "binding_mismatch"),
        (_ready(pr=PR + 1), "binding_mismatch"),
        (_ready(base="c" * 40), "binding_mismatch"),
        (_refusal([DENYLIST_REFUSAL], base="c" * 40), "binding_mismatch"),
        (_refusal([DENYLIST_REFUSAL], blocked=["CLAUDE.md", "AGENTS.md"]), "binding_mismatch"),
        (_refusal([DENYLIST_REFUSAL], blocked=[1]), "binding_mismatch"),
        ({**_ready(), "apply": True}, "not_a_no_apply_report"),
        ({**_ready(), "gh_merge_attempted": True}, "not_a_no_apply_report"),
        ({**_ready(), "merge_executed": None}, "not_a_no_apply_report"),
        ({**_ready(), "errors": ["late"]}, "unexpected_decision"),
        ({**_ready(), "ok": "true"}, "malformed_report"),
        (_refusal([DENYLIST_REFUSAL], head="b" * 40), "binding_mismatch"),
        (_refusal([DENYLIST_REFUSAL], policy="rule12"), "binding_mismatch"),
        (_refusal([DENYLIST_REFUSAL], errors=[]), "malformed_report"),
        (_refusal([DENYLIST_REFUSAL], errors=["other"]), "malformed_report"),
        ({**_refusal([DENYLIST_REFUSAL]), "decision": "unexpected_head"}, "unexpected_decision"),
        ({**_refusal([DENYLIST_REFUSAL]), "receipt_report": {}}, "malformed_report"),
        ({**_refusal([DENYLIST_REFUSAL]), "errors": "path gate failed"}, "malformed_report"),
        (["not", "an", "object"], "malformed_report"),
    ],
)
def test_report_that_does_not_bind_is_never_consistent(report: Any, status: str) -> None:
    pr = _only(check_package(_package(["CLAUDE.md"], report), profiles=_test_profiles()))
    assert pr["no_apply"]["status"] == status
    assert pr["verdict"] == "operator_review_required"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(schema="other"),
        lambda p: p.update(driver={"source_commit": "ABC", "manifest_sha256": TEST_MANIFEST}),
        lambda p: p.update(driver={"source_commit": TEST_COMMIT, "manifest_sha256": "xyz"}),
        lambda p: p.update(prs=[]),
        lambda p: p["prs"].append(dict(p["prs"][0])),
        lambda p: p["prs"][0].update(pr=0),
        lambda p: p["prs"][0].update(pr=True),
        lambda p: p["prs"][0].update(head=HEAD.upper()),
        lambda p: p["prs"][0].pop("base"),
        lambda p: p["prs"][0].update(changed_paths=[]),
        lambda p: p["prs"][0].update(changed_paths=[" CLAUDE.md"]),
        lambda p: p["prs"][0].update(rco_slot_holder="Claude-RCO-1"),
    ],
)
def test_invalid_package_is_refused(mutate: Any) -> None:
    package = _package(["CLAUDE.md"])
    mutate(package)
    with pytest.raises(ValueError):
        check_package(package, profiles=_test_profiles())


def test_cli_is_observe_only_and_exits_zero_for_a_review_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    package = tmp_path / "package.json"
    package.write_text(json.dumps(_package(["CLAUDE.md"], commit="0" * 40)), encoding="utf-8")
    assert main([str(package), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["observe_only"] is True
    assert report["verdict"] == "operator_review_required"


def test_cli_invalid_input_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    package = tmp_path / "package.json"
    package.write_text("{", encoding="utf-8")
    assert main([str(package)]) == 2
    assert json.loads(capsys.readouterr().out)["verdict"] == "invalid_input"
