"""Independent synthetic-GitHub tests for the opt-in required-CI snapshot (Lead 2026-11-09T08:32:54Z).

Drives the real tools.pr_status_snapshot.build_pr_status_snapshot(require_ci_evidence=True) published by Tools at
556118c1 through its published runner seam only. A synthetic GitHub (no network, never a live CI claim) serves
classic branch protection, active branch rules, the exact-head check-run inventory with run, app and suite
identities, pagination and partial failures. The base state mirrors the live shape Lead read on main 23564ad4 at
2026-11-09T08:34:01Z (zero active branch rules, six completed required runs from app 15368) without copying any
live payload. Expected outcomes live in tests/fixtures/bridge_consensus_carry_forward/ci_snapshot_cases.json.

Before the opt-in exists (main) every case fails with "RED: missing API" and the non-opt-in control stays green.
"""
from __future__ import annotations

import copy
import inspect
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.pr_status_snapshot import PrStatusSnapshotError, build_pr_status_snapshot

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "tests" / "fixtures" / "bridge_consensus_carry_forward" / "ci_snapshot_cases.json")
                   .read_bytes())
REPO = "example-owner/example-repo"
PR = 9201
BRANCH = "fable-5/ci-snapshot-pr-9201"
HEAD = "3f" * 20
OLD_HEAD = "4e" * 20
BASE = "5d" * 20
APP = 15368
IMPOSTOR_APP = 990001
CHANGED_PATH = "tools/agent_next_task.py"
REQUIRED = ["bundle-closure", "docs-truth", "test (3.11)", "test (3.12)", "test (3.13)", "unified"]


def _run(run_id, name, *, head=HEAD, status="completed", conclusion="success", app=APP, repo=REPO, suite=None):
    return {"id": run_id, "name": name, "head_sha": head, "status": status, "conclusion": conclusion,
            "app": {"id": app, "slug": "github-actions" if app == APP else "impostor"},
            "check_suite": {"id": suite or 7000 + run_id},
            "url": f"https://api.github.com/repos/{repo}/check-runs/{run_id}"}


def _required_rule(names, app=APP):
    return {"type": "required_status_checks",
            "parameters": {"strict_required_status_checks_policy": True,
                           "required_status_checks": [{"context": n, "integration_id": app} for n in names]}}


class FakeGitHub:
    """Synthetic GitHub behind the published gh runner seam; records every command."""

    def __init__(self):
        self.calls = []
        self.protection = {"url": f"https://api.github.com/repos/{REPO}/branches/main/protection/required_status_checks",
                           "strict": True, "contexts": list(REQUIRED),
                           "checks": [{"context": n, "app_id": APP} for n in REQUIRED]}
        self.rules_pages = [[]]
        self.runs = [_run(100 + i, name) for i, name in enumerate(REQUIRED)]
        self.run_reads = None      # optional list of run lists, one per complete inventory read
        self.total_override = {}   # page -> total_count served on that page
        self.page_rows_cut = {}    # page -> number of rows to drop from that page
        self.drop_total = False
        self.fail = set()          # {(kind, page)} served as a failed gh call
        self.raw = {}              # kind -> raw stdout replacing the JSON body
        self._run_reads_seen = 0

    @staticmethod
    def _page(args):
        for item in args:
            if item.startswith("page="):
                return int(item.split("=", 1)[1])
        return None

    def _payload(self):
        return {"number": PR, "title": "feat(tools): ci snapshot test", "headRefOid": HEAD, "headRefName": BRANCH,
                "baseRefOid": BASE, "baseRefName": "main", "mergeable": "MERGEABLE", "state": "OPEN",
                "isDraft": False, "url": f"https://github.com/{REPO}/pull/{PR}", "reviewDecision": "",
                "updatedAt": "2026-11-09T08:30:00Z", "changedFiles": 1,
                "author": {"login": "example-author", "name": "", "email": ""},
                "commits": [{"oid": HEAD, "authors": [{"name": "Example", "email": "author@example.invalid",
                                                        "login": ""}]}],
                "statusCheckRollup": [{"name": n, "conclusion": "SUCCESS", "status": "COMPLETED"} for n in REQUIRED]}

    def _ok(self, body):
        return SimpleNamespace(returncode=0, stderr="", stdout=body if isinstance(body, str) else json.dumps(body))

    def __call__(self, command):
        args = list(command)
        self.calls.append(args)
        if args[:3] == ["gh", "pr", "view"]:
            return self._ok(self._payload())
        if args[:3] == ["gh", "pr", "diff"]:
            return self._ok(f"diff --git a/{CHANGED_PATH} b/{CHANGED_PATH}\n")
        assert args[:2] == ["gh", "api"], args
        endpoint, page = args[4], self._page(args)
        if endpoint.endswith("/protection/required_status_checks"):
            kind = "protection"
        elif "/rules/branches/" in endpoint:
            kind = "rules"
        elif endpoint.endswith(f"/commits/{HEAD}/check-runs"):
            kind = "runs"
        elif "/git/ref/heads/" in endpoint:
            return self._ok({"ref": "refs/heads/main", "object": {"type": "commit", "sha": BASE}})
        elif endpoint.endswith(f"/pulls/{PR}/files"):
            return self._ok([{"filename": CHANGED_PATH, "status": "modified"}] if page in (None, 1) else [])
        else:
            raise AssertionError(f"unexpected endpoint: {args}")
        if (kind, page) in self.fail:
            return SimpleNamespace(returncode=1, stdout="", stderr="HTTP 502: Bad Gateway")
        if kind in self.raw:
            return self._ok(self.raw[kind])
        if kind == "protection":
            return self._ok(self.protection)
        if kind == "rules":
            return self._ok(self.rules_pages[page - 1] if page <= len(self.rules_pages) else [])
        if page == 1:
            self._run_reads_seen += 1
        reads = self.run_reads or [self.runs]
        runs = reads[min(self._run_reads_seen, len(reads)) - 1]
        rows = runs[(page - 1) * 100: page * 100]
        if page in self.page_rows_cut:
            rows = rows[: len(rows) - self.page_rows_cut[page]]
        body = {"total_count": self.total_override.get(page, len(runs)), "check_runs": rows}
        if self.drop_total:
            body.pop("total_count")
        return self._ok(body)


def _require_opt_in():
    if "require_ci_evidence" not in inspect.signature(build_pr_status_snapshot).parameters:
        pytest.fail("RED: missing API: build_pr_status_snapshot has no require_ci_evidence opt-in yet "
                    "(published by Tools at 556118c1)")


def _snapshot(gh, **extra):
    return build_pr_status_snapshot(pr_number=PR, repo=REPO, receipt_verified=True, expected_base_sha=BASE,
                                    runner=gh, **extra)


def _set(rows, name, **fields):
    for row in rows:
        if row["name"] == name and row["app"]["id"] == APP:
            row.update(fields)
            return
    raise AssertionError(name)


def _extra_runs(count, start=500):
    return [_run(start + i, f"optional-job-{i:03d}") for i in range(count)]


def _setup(case, gh):
    """Mutate the synthetic GitHub for one case; every case names the real-world shape it models."""
    unified = "unified"
    if case == "fresh_exact_head_success":
        return
    if case == "extra_non_required_run_failed":
        gh.runs.append(_run(300, "optional-lint", conclusion="failure"))
    elif case == "impostor_app_failure_beside_real_success":
        gh.runs.append(_run(301, unified, conclusion="failure", app=IMPOSTOR_APP))
    elif case == "impostor_app_success_beside_real_failure":
        _set(gh.runs, unified, conclusion="failure")
        gh.runs.append(_run(301, unified, conclusion="success", app=IMPOSTOR_APP))
    elif case == "non_ci_rules_beside_required_checks":
        gh.rules_pages = [[{"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
                           {"type": "required_signatures"}, {"type": "non_fast_forward"},
                           _required_rule([unified])]]
    elif case in ("rules_requirement_on_page_two_present", "rules_requirement_on_page_two_missing_run"):
        gh.rules_pages = [[{"type": "deletion"}] * 100, [_required_rule(["late-rule-check"])]]
        if case.endswith("present"):
            gh.runs.append(_run(302, "late-rule-check"))
    elif case == "runs_paginated_required_on_page_two":
        gh.runs = _extra_runs(144) + gh.runs   # 150 runs, all six required rows on page two
    elif case in ("failed_required_check", "skipped_required_check", "neutral_required_check",
                  "cancelled_required_check"):
        _set(gh.runs, unified, conclusion=case.split("_")[0].replace("failed", "failure"))
    elif case == "pending_required_check":
        _set(gh.runs, unified, status="in_progress", conclusion=None)
    elif case == "queued_required_check":
        _set(gh.runs, unified, status="queued", conclusion=None)
    elif case == "stale_row_for_another_head":
        _set(gh.runs, unified, head_sha=OLD_HEAD)
    elif case in ("required_run_only_on_old_head", "missing_required_check"):
        gh.runs = [row for row in gh.runs if row["name"] != unified]
    elif case == "stale_rerun_same_name_same_app":
        gh.runs.append(_run(90, unified, conclusion="failure"))
    elif case == "wrong_app_only":
        _set(gh.runs, unified, app={"id": IMPOSTOR_APP, "slug": "impostor"})
    elif case == "wrong_repository_run_url":
        _set(gh.runs, unified, url=f"https://api.github.com/repos/other-owner/other-repo/check-runs/105")
    elif case == "duplicate_run_id":
        gh.runs[1]["id"] = gh.runs[0]["id"]
        gh.runs[1]["url"] = gh.runs[0]["url"]
    elif case == "run_without_app_identity":
        _set(gh.runs, unified, app={})
    elif case == "unknown_run_status":
        _set(gh.runs, unified, status="mystery")
    elif case == "protection_check_without_app":
        gh.protection["checks"][0]["app_id"] = None
    elif case == "legacy_context_without_app_bound_check":
        gh.protection["contexts"].append("legacy-status")
    elif case == "protection_malformed":
        gh.protection.pop("strict")
    elif case == "protection_read_fails":
        gh.fail.add(("protection", None))
    elif case == "unsupported_rule_type":
        gh.rules_pages = [[{"type": "workflows", "parameters": {"workflows": [{"path": ".github/workflows/x.yml"}]}}]]
    elif case == "rules_page_two_read_fails":
        gh.rules_pages = [[{"type": "deletion"}] * 100]
        gh.fail.add(("rules", 2))
    elif case == "required_inventory_empty":
        gh.protection["checks"], gh.protection["contexts"] = [], []
    elif case == "runs_page_short_of_total":
        gh.runs = _extra_runs(144) + gh.runs
        gh.page_rows_cut[2] = 10
    elif case == "runs_total_changes_between_pages":
        gh.runs = _extra_runs(144) + gh.runs
        gh.total_override[2] = 151
    elif case == "runs_total_count_missing":
        gh.drop_total = True
    elif case == "runs_page_two_read_fails":
        gh.runs = _extra_runs(144) + gh.runs
        gh.fail.add(("runs", 2))
    elif case == "runs_malformed_json":
        gh.raw["runs"] = '{"total_count": 6, "check_runs": ['
    elif case == "ci_changes_between_bracket_reads":
        first = copy.deepcopy(gh.runs)
        _set(first, unified, status="in_progress", conclusion=None)
        gh.run_reads = [first, gh.runs]
    else:
        raise AssertionError(f"no setup for {case}")


def _outcome(gh):
    try:
        snapshot = _snapshot(gh, require_ci_evidence=True)
    except PrStatusSnapshotError as exc:
        return "refuse", exc.report.get("decision"), " ".join(map(str, exc.report.get("errors", []))), None
    evidence = snapshot.get("ci_evidence")
    assert isinstance(evidence, dict), "opt-in snapshot carries no ci_evidence"
    return ("green" if evidence.get("all_required_success") is True else "non_green"), None, "", snapshot


def _assert_bound(snapshot, gh, required_names):
    evidence = snapshot["ci_evidence"]
    assert evidence["host"] == "github.com" and evidence["repository"] == REPO
    assert evidence["head_sha"] == HEAD and evidence["base_sha"] == BASE and evidence["pr_number"] == PR
    assert sorted((r["name"], r["app_id"]) for r in evidence["required"]) == sorted((n, APP) for n in required_names)
    by_name = {r["name"]: r for r in evidence["runs"]}
    assert set(by_name) == set(required_names)
    served = {(row["name"], row["app"].get("id")): row for row in gh.runs}
    for name, row in by_name.items():
        assert row["head_sha"] == HEAD and row["app_id"] == APP
        assert row["id"] == served[(name, APP)]["id"]  # the bound app's run, never an impostor's
        assert row["url"].lower() == f"https://api.github.com/repos/{REPO}/check-runs/{row['id']}".lower()


def test_every_fixture_case_has_a_setup():
    for case in CASES["cases"]:
        _setup(case, FakeGitHub())


def test_control_without_opt_in_no_ci_reads_and_no_ci_evidence():
    gh = FakeGitHub()
    snapshot = _snapshot(gh)
    assert "ci_evidence" not in snapshot
    assert not any("check-runs" in arg or "/rules/" in arg or "required_status_checks" in arg
                   for call in gh.calls for arg in call)


@pytest.mark.parametrize("case", sorted(CASES["cases"]))
def test_required_ci_snapshot_case(case):
    _require_opt_in()
    spec = CASES["cases"][case]
    gh = FakeGitHub()
    _setup(case, gh)
    outcome, decision, message, snapshot = _outcome(gh)
    assert outcome == spec["expect"], (case, outcome, decision, message)
    if outcome == "refuse":
        assert decision == spec["decision"], (case, decision, message)
        assert re.search(spec["message"], message), (case, message)
        return
    required = list(REQUIRED)
    if case == "rules_requirement_on_page_two_present":
        required.append("late-rule-check")
    _assert_bound(snapshot, gh, required)


def test_fresh_success_reads_are_pinned_bracketed_and_sourced():
    """The fresh exact-head success case: every API read is pinned to github.com, PR reads to github.com/REPO,
    the complete inventory is read twice (bracket), and the evidence names its sources and observation time."""
    _require_opt_in()
    gh = FakeGitHub()
    outcome, _, _, snapshot = _outcome(gh)
    assert outcome == "green"
    for call in gh.calls:
        if call[:2] == ["gh", "api"]:
            assert call[-2:] == ["--hostname", "github.com"], call
        else:
            assert call[call.index("--repo") + 1] == f"github.com/{REPO}", call
    run_reads = [c for c in gh.calls if c[:2] == ["gh", "api"] and c[4].endswith("/check-runs")]
    assert len(run_reads) == 2 and all("filter=latest" in c for c in run_reads)
    evidence = snapshot["ci_evidence"]
    endpoints = {s["endpoint"] for s in evidence["sources"]}
    assert {f"repos/{REPO}/branches/main/protection/required_status_checks",
            f"repos/{REPO}/rules/branches/main", f"repos/{REPO}/commits/{HEAD}/check-runs"} <= endpoints
    assert all(len(s["response_sha256"]) == 64 for s in evidence["sources"])
    assert evidence["strict_branch_protection"] is True and evidence["observed_at_utc"]


def test_a_stored_green_flag_is_not_authority():
    """Changing the recorded boolean does not change what a fresh read observes: a second snapshot of a failing
    head is non-green whatever an earlier snapshot said."""
    _require_opt_in()
    good = FakeGitHub()
    assert _outcome(good)[0] == "green"
    bad = FakeGitHub()
    _set(bad.runs, "unified", conclusion="failure")
    assert _outcome(bad)[0] == "non_green"
