# SPDX-License-Identifier: BUSL-1.1
"""Tests for the MANUAL-A read-only admission preview (G2, plan T05-T09).

Every runner here is a deterministic fake: the evidence class is unit_mock and
nothing in this file is live evidence.  No key material exists here; the
"signature" is an armored placeholder and ssh-keygen is a fake runner.
"""
from __future__ import annotations

import ast
import base64
import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import struct
import subprocess

import pytest

from tools import manual_bridge_merge as mbm
from tools import manual_bridge_merge_receipt as mmr
from tools import manual_bridge_merge_statement as mms
from tools.manual_bridge_merge_statement import ALLOWED_SIGNERS_PATH, RunResult

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "manual_bridge_merge.py"
BASE = "b" * 40
HEAD = "a" * 40
MOVED_HEAD = "c" * 40
PR = 1800
BRANCH = "fable-5/example-change-20261007"
NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
EXPIRY = "2026-10-07T13:00:00Z"
NONCE = "0123456789abcdef0123456789abcdef"
PATHS = ("src/example.py", "tests/test_example.py")
SESSION = "wd-reboot-20261006T210149Z"
UUIDS = {
    "claude-rco-1": "2b2f6ff9-0000-4000-8000-000000000001",
    "claude-rco-2": "76739997-0000-4000-8000-000000000002",
    "codex-lead-1": "d3c9d1d1-0000-4000-8000-000000000003",
    "codex-tools-1": "a1b2c3d4-0000-4000-8000-000000000004",
}
REQUESTS = {"rco": "req-rco-1800", "build_lead": "req-lead-1800", "build_tools": "req-tools-1800"}
REQUIRED = ("test (3.11)", "test (3.12)", "test (3.13)")
SIGNATURE = b"-----BEGIN SSH SIGNATURE-----\nU1NIU0lHAAAAAQ==\n-----END SSH SIGNATURE-----\n"


def raw_record(path: str) -> bytes:
    return f":100644 100644 {'1' * 40} {'2' * 40} M".encode("ascii") + b"\x00" + path.encode("utf-8") + b"\x00"


def raw_diff(paths=PATHS) -> bytes:
    return b"".join(raw_record(path) for path in sorted(paths))


def ssh_string(value: bytes) -> bytes:
    return struct.pack(">I", len(value)) + value


ANCHOR = (
    "# operator-supplied public line (synthetic test value)\n"
    + f"{mms.PRINCIPAL} {mms.ANCHOR_OPTIONS} ssh-ed25519 "
    + base64.b64encode(ssh_string(b"ssh-ed25519") + ssh_string(bytes(range(32)))).decode("ascii")
    + "\n"
).encode("ascii")


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\x00" + data, usedforsecurity=False).hexdigest()


class FakeGit:
    """unit_mock git for the anchor load, the G1 live facts and the ancestry check."""

    def __init__(self, *, diff: bytes | None = None, ancestor_rc: int = 0,
                 live_commits: tuple[str, ...] = (BASE, HEAD, MOVED_HEAD)):
        self.diff = raw_diff() if diff is None else diff
        self.ancestor_rc = ancestor_rc
        self.live_commits = live_commits
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, input_bytes, timeout, env):
        self.calls.append(list(argv))
        args = list(argv[3:])
        if args[:1] == ["--no-replace-objects"]:
            args = args[1:]
            if args[:3] == ["rev-parse", "--verify", "--quiet"]:
                sha = args[3][: -len("^{commit}")]
                return RunResult(0, (sha + "\n").encode(), b"") if sha in self.live_commits else RunResult(1, b"", b"")
            if args[:1] == ["diff-tree"] and tuple(args[1:-2]) == mms.GIT_DIFF_ARGS:
                return RunResult(0, self.diff, b"")
            if args[:2] == ["merge-base", "--is-ancestor"]:
                return RunResult(self.ancestor_rc, b"", b"")
            raise AssertionError(f"unexpected live git call {argv}")
        if args[:3] == ["rev-parse", "--verify", "--quiet"] and args[3].endswith("^{commit}"):
            sha = args[3][: -len("^{commit}")]
            return RunResult(0, (sha + "\n").encode(), b"") if sha in self.live_commits else RunResult(1, b"", b"")
        if args[:3] == ["rev-parse", "--verify", "--quiet"] and args[3].endswith(ALLOWED_SIGNERS_PATH):
            return RunResult(0, (git_blob_sha(ANCHOR) + "\n").encode(), b"")
        if args[:2] == ["cat-file", "-t"]:
            return RunResult(0, b"blob\n", b"")
        if args[:2] == ["cat-file", "blob"]:
            return RunResult(0, ANCHOR, b"")
        raise AssertionError(f"unexpected git call {argv}")


def load_anchor() -> mms.TrustAnchor:
    return mms.load_trust_anchor(repo_root=ROOT, trusted_commit=BASE, runner=FakeGit())


class FakeSsh:
    """unit_mock ssh-keygen: answers the exact Good line (never a cryptographic check)."""

    def __init__(self, returncode: int = 0):
        anchor = load_anchor()
        self.stdout = (f'Good "{mms.NAMESPACE}" signature for {mms.PRINCIPAL} with {anchor.key_label} key '
                       f"{anchor.fingerprint}\n").encode("ascii")
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, input_bytes, timeout, env):
        self.calls.append(list(argv))
        return RunResult(self.returncode, self.stdout, b"")


def gh_pr(number=PR, *, state="OPEN", draft=False, head=HEAD, branch=BRANCH, base=BASE, base_ref="main",
          mergeable="MERGEABLE", merge_state="CLEAN") -> dict:
    return {"number": number, "state": state, "isDraft": draft, "headRefOid": head, "headRefName": branch,
            "baseRefOid": base, "baseRefName": base_ref, "mergeable": mergeable, "mergeStateStatus": merge_state}


def check_run(name, *, head=HEAD, status="completed", conclusion="success") -> dict:
    return {"name": name, "head_sha": head, "status": status, "conclusion": conclusion}


class FakeGh:
    """unit_mock gh: read-only answers only; any other argv is a test failure."""

    def __init__(self):
        self.prs = {PR: gh_pr()}
        self.required = {"contexts": list(REQUIRED), "checks": [{"context": name} for name in REQUIRED]}
        self.required_rc = 0
        self.runs = [check_run(name) for name in REQUIRED]
        self.total = None
        self.remaining = 4999
        self.view_rc = 0
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, input_bytes, timeout, env):
        self.calls.append(list(argv))
        args = list(argv[1:])
        if args[:2] == ["pr", "view"]:
            number = int(args[2])
            if self.view_rc or number not in self.prs:
                return RunResult(self.view_rc or 1, b"", b"not found")
            return RunResult(0, json.dumps(self.prs[number]).encode(), b"")
        if args[0] == "api" and args[1].endswith("/protection/required_status_checks"):
            return RunResult(self.required_rc, json.dumps(self.required).encode(), b"")
        if args[0] == "api" and "/check-runs" in args[1]:
            total = len(self.runs) if self.total is None else self.total
            return RunResult(0, json.dumps({"total_count": total, "check_runs": self.runs}).encode(), b"")
        if args == ["api", "rate_limit"]:
            return RunResult(0, json.dumps({"resources": {"core": {"remaining": self.remaining}}}).encode(), b"")
        raise AssertionError(f"unexpected gh call {argv}")


def event(agent, status, *, type_=mmr.DECISION_TYPE, task=BRANCH, head=HEAD, request=None, ts="2026-10-07T11:00:00Z",
          payload_extra=None, uuid=None, session=SESSION, exact=True) -> dict:
    payload = {"head": head, "pr": PR}
    if exact:
        payload["exact_head"] = head
    if request is not None:
        payload["lead_request"] = request
    payload.update(payload_extra or {})
    return {"ts_utc": ts, "agent": agent, "type": type_, "task_id": task, "status": status, "message": "",
            "payload": payload, "agent_uuid": uuid or UUIDS.get(agent, "00000000-0000-4000-8000-00000000ffff"),
            "session_id": session}


def approvals(rco="claude-rco-1") -> list[dict]:
    return [
        event(rco, mmr.RCO_PASS_STATUS, request=REQUESTS["rco"]),
        event("codex-lead-1", mmr.BUILD_CONSENSUS_STATUS, request=REQUESTS["build_lead"], exact=False),
        event("codex-tools-1", mmr.BUILD_CONSENSUS_STATUS, request=REQUESTS["build_tools"], exact=False),
    ]


def registry() -> mbm.IdentityRegistry:
    return mbm.IdentityRegistry({agent: mbm.IdentityBinding(uuid, SESSION) for agent, uuid in UUIDS.items()})


def make_statement(**overrides) -> mms.Statement:
    anchor = load_anchor()
    diff = overrides.pop("diff", raw_diff(overrides.get("exact_paths", PATHS)))
    fields = dict(
        pull_request=PR, head_sha=HEAD, base_sha=BASE, diff_digest_sha256=hashlib.sha256(diff).hexdigest(),
        exact_paths=tuple(sorted(PATHS)), batch_id="mma-20261007-example", batch_order=1, dependencies=(),
        expires_at_utc=EXPIRY, nonce=NONCE, allowed_signers_blob_sha=anchor.blob_sha,
        key_fingerprint=anchor.fingerprint,
    )
    fields.update(overrides)
    return mms.build_statement(**fields)


REFUSAL_EVENT = {
    "ts_utc": "2026-10-07T10:00:00Z", "agent": "codex-lead-1", "type": "status", "task_id": BRANCH,
    "status": "auto_merge_refused", "message": "autonomous gate refused (a)-class", "payload": {"pr": PR},
}


def inputs(tmp_path, **overrides) -> dict:
    keygen = tmp_path / "ssh-keygen-fake.exe"
    keygen.write_bytes(b"not a binary")
    base = dict(
        pull_request=PR,
        statement_bytes=mms.canonical_statement_bytes(make_statement()),
        signature_bytes=SIGNATURE,
        repo_root=ROOT,
        ssh_keygen=keygen,
        now_utc=NOW,
        bridge=mbm.BridgeSnapshot(tuple(approvals())),
        lineage=mbm.AuthorLineage(("fable-5",), ("codex-lead-1",)),
        registry=registry(),
        autonomous_refusal=mmr.AutonomousRefusalEvidence("recorded", event=copy.deepcopy(REFUSAL_EVENT)),
        expected_requests=dict(REQUESTS),
        gh_runner=FakeGh(),
        git_runner=FakeGit(),
        ssh_runner=FakeSsh(),
    )
    base.update(overrides)
    return base


def preview(tmp_path, **overrides) -> mbm.AdmissionPreview:
    return mbm.preview_admission(**inputs(tmp_path, **overrides))


def refusal_reasons(result: mbm.AdmissionPreview) -> set[str]:
    return {check.reason for check in result.refusals}


def with_events(*extra, base=None) -> mbm.BridgeSnapshot:
    return mbm.BridgeSnapshot(tuple((approvals() if base is None else base) + list(extra)))


# --- nominal fixture: everything implementable passes, yet never admitted ------


def test_nominal_fixture_has_no_refusal_and_is_still_only_unknown(tmp_path):
    result = preview(tmp_path)
    assert result.refusals == ()
    assert result.verdict == mbm.VERDICT_UNKNOWN
    assert result.execute_available is False and result.effects == ()
    unknown = {check.name for check in result.unknowns}
    assert {f"prerequisite:{name}" for name in mbm.EXPECTED_UNKNOWN_PREREQUISITES} <= unknown
    assert {"statement_provenance", "live_pr"} <= unknown  # unit_mock runners are never live evidence
    for name in ("statement_verification", "signed_head", "signed_base", "approval_rco", "approval_build_lead",
                 "approval_build_tools", "rco_blocking_decision", "ci_required_checks", "batch_queue",
                 "bootstrap_self_admission", "head_contains_base", "autonomous_refusal"):
        assert result.check(name).status == mbm.CHECK_PASS, name


def test_there_is_no_admitted_verdict():
    assert {mbm.VERDICT_REFUSED, mbm.VERDICT_UNKNOWN} == {
        value for name, value in vars(mbm).items() if name.startswith("VERDICT_")}
    assert mbm.EXECUTE_AVAILABLE is False


# --- T05: signed-field tampering and head drift ---------------------------------


def test_t05_live_head_moved_after_signing(tmp_path):
    gh = FakeGh()
    gh.prs[PR] = gh_pr(head=MOVED_HEAD)
    result = preview(tmp_path, gh_runner=gh)
    assert result.check("signed_head").reason == "signed_head_stale"
    assert result.verdict == mbm.VERDICT_REFUSED


def test_t05_live_base_advanced_after_signing(tmp_path):
    gh = FakeGh()
    gh.prs[PR] = gh_pr(base=MOVED_HEAD)
    result = preview(tmp_path, gh_runner=gh)
    assert "base_mismatch" in refusal_reasons(result)


@pytest.mark.parametrize("live_diff,reason", [
    (raw_diff(PATHS + ("src/extra.py",)), "exact_paths_mismatch"),
    (raw_diff(PATHS[:1]), "exact_paths_mismatch"),
    (raw_record(PATHS[1]) + raw_record(PATHS[0]).replace(b"2" * 40, b"3" * 40), "exact_paths_mismatch"),
])
def test_t05_paths_and_diff_are_bound_to_live_git(tmp_path, live_diff, reason):
    result = preview(tmp_path, git_runner=FakeGit(diff=live_diff))
    assert result.check("statement_verification").reason in (reason, "diff_digest_mismatch")
    assert result.verdict == mbm.VERDICT_REFUSED


def test_t05_same_paths_different_blob_is_a_digest_mismatch(tmp_path):
    tampered = b"".join(raw_record(p).replace(b"2" * 40, b"3" * 40) for p in sorted(PATHS))
    result = preview(tmp_path, git_runner=FakeGit(diff=tampered))
    assert result.check("statement_verification").reason == "diff_digest_mismatch"


@pytest.mark.parametrize("field,value,reason", [
    ("repository", "someone/else", "invalid_field:repository"),
    ("merge_method", "merge", "invalid_field:merge_method"),
    ("operation_scope", "merge-batch", "invalid_field:operation_scope"),
    ("namespace", "other-namespace", "invalid_field:namespace"),
    ("batch_order", 0, "invalid_field:batch_order"),
    ("dependencies", [PR], "invalid_field:dependencies"),
])
def test_t05_tampered_constant_or_shape_refuses_at_parse(tmp_path, field, value, reason):
    data = json.loads(mms.canonical_statement_bytes(make_statement()))
    data[field] = value
    raw = (json.dumps(data, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
    result = preview(tmp_path, statement_bytes=raw)
    assert result.check("statement_parse").reason == reason
    assert result.verdict == mbm.VERDICT_REFUSED


def test_t05_statement_for_another_pr_refuses(tmp_path):
    raw = mms.canonical_statement_bytes(make_statement(pull_request=PR + 1))
    result = preview(tmp_path, statement_bytes=raw)
    assert result.check("statement_pull_request").reason == "pull_request_mismatch"


def test_t05_unmerged_dependency_refuses(tmp_path):
    gh = FakeGh()
    gh.prs[1700] = gh_pr(1700, state="OPEN")
    raw = mms.canonical_statement_bytes(make_statement(dependencies=(1700,)))
    result = preview(tmp_path, statement_bytes=raw, gh_runner=gh)
    assert result.check("batch_queue").reason == "prerequisite_not_merged"
    gh.prs[1700] = gh_pr(1700, state="MERGED")
    assert preview(tmp_path, statement_bytes=raw, gh_runner=gh).check("batch_queue").status == mbm.CHECK_PASS


def test_t05_expired_statement_refuses(tmp_path):
    result = preview(tmp_path, now_utc=datetime(2026, 10, 7, 13, 0, 0, tzinfo=timezone.utc))
    assert result.check("statement_verification").reason == "statement_expired"


def test_t05_signature_failure_refuses(tmp_path):
    result = preview(tmp_path, ssh_runner=FakeSsh(returncode=1))
    assert result.check("statement_verification").reason == "signature_invalid"


# --- T06: lineage, recognized RCO, identity bindings ----------------------------


def test_t06_missing_lineage_refuses(tmp_path):
    assert preview(tmp_path, lineage=None).check("author_lineage_map").reason == "lineage_missing"
    empty = mbm.AuthorLineage(())
    assert preview(tmp_path, lineage=empty).check("author_lineage_map").reason == "lineage_missing"


@pytest.mark.parametrize("lineage", [
    mbm.AuthorLineage(("claude-rco-1",)),
    mbm.AuthorLineage(("fable-5",), ("claude-rco-1",)),
])
def test_t06_author_or_contributor_rco_cannot_fill_the_slot(tmp_path, lineage):
    result = preview(tmp_path, lineage=lineage)
    assert result.check("approval_rco").reason == "rco_is_author_or_contributor"


def test_t06_other_recognized_rco_fills_the_slot_when_the_first_is_an_author(tmp_path):
    bridge = with_events(event("claude-rco-2", mmr.RCO_PASS_STATUS, request=REQUESTS["rco"]))
    result = preview(tmp_path, lineage=mbm.AuthorLineage(("claude-rco-1",)), bridge=bridge)
    assert result.check("approval_rco").status == mbm.CHECK_PASS
    assert "claude-rco-2" in result.check("approval_rco").detail


@pytest.mark.parametrize("agent", ["operator", "grok", "gpt-reviewer", "codex-lead-1", "fable-5"])
def test_t06_non_rco_identities_never_fill_the_rco_slot(tmp_path, agent):
    base = approvals()[1:] + [event(agent, mmr.RCO_PASS_STATUS, request=REQUESTS["rco"])]
    result = preview(tmp_path, bridge=mbm.BridgeSnapshot(tuple(base)))
    assert result.check("approval_rco").reason == "approval_missing"


def test_t06_missing_recognized_pass_refuses(tmp_path):
    result = preview(tmp_path, bridge=mbm.BridgeSnapshot(tuple(approvals()[1:])))
    assert result.check("approval_rco").reason == "approval_missing"


@pytest.mark.parametrize("field", ["agent_uuid", "session_id"])
def test_t06_uuid_or_session_mismatch_refuses(tmp_path, field):
    events = approvals()
    events[0][field] = "ffffffff-0000-4000-8000-00000000dead"
    result = preview(tmp_path, bridge=mbm.BridgeSnapshot(tuple(events)))
    assert result.check("approval_identity_binding").reason == "identity_binding_mismatch"


def test_t06_missing_registry_is_unknown_not_pass(tmp_path):
    result = preview(tmp_path, registry=None)
    assert result.check("approval_identity_binding").status == mbm.CHECK_UNKNOWN


def test_t06_request_binding_mismatch_refuses(tmp_path):
    requests = dict(REQUESTS, build_tools="req-other")
    result = preview(tmp_path, expected_requests=requests)
    assert result.check("approval_request_binding").reason == "request_binding_mismatch"
    assert preview(tmp_path, expected_requests=None).check("approval_request_binding").status == mbm.CHECK_UNKNOWN


def test_t06_duplicate_identity_refuses(tmp_path):
    events = approvals()
    events[2]["agent_uuid"] = events[1]["agent_uuid"]
    reg = mbm.IdentityRegistry(dict(registry().entries, **{"codex-tools-1": mbm.IdentityBinding(events[1]["agent_uuid"], SESSION)}))
    result = preview(tmp_path, bridge=mbm.BridgeSnapshot(tuple(events)), registry=reg)
    assert result.check("approval_identities_distinct").reason == "approval_identity_duplicate"


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(task_id=BRANCH.replace("/", "-")),
    lambda e: e["payload"].update(exact_head=MOVED_HEAD),
    lambda e: e["payload"].pop("exact_head"),
    lambda e: e.update(type="message"),
    lambda e: e.update(status="RCO_PASS"),
])
def test_t06_rco_pass_binding_is_exact(tmp_path, mutate):
    events = approvals()
    mutate(events[0])
    result = preview(tmp_path, bridge=mbm.BridgeSnapshot(tuple(events)))
    assert result.check("approval_rco").status == mbm.CHECK_REFUSE


# --- T07: blocking decisions, exact clear, both build votes ---------------------


def finding(agent="claude-rco-2", **kwargs) -> dict:
    return event(agent, "changes_requested", type_=mmr.DECISION_TYPE, **kwargs)


@pytest.mark.parametrize("position", ["before", "after"])
def test_t07_blocking_decision_wins_before_or_after_pass(tmp_path, position):
    block = finding()
    base = approvals()
    events = [block] + base if position == "before" else base + [block]
    result = preview(tmp_path, bridge=mbm.BridgeSnapshot(tuple(events)))
    assert result.check("rco_blocking_decision").reason == "rco_veto_active"
    assert result.verdict == mbm.VERDICT_REFUSED


@pytest.mark.parametrize("block", [
    event("claude-rco-1", "rco_pass", type_="finding"),
    event("claude-rco-2", "changes_requested", type_="message"),
    event("claude-rco-2", "rco_pass_withheld"),
    event("claude-rco-2", "some_new_status"),
    event("claude-rco-2", "changes_requested", task=BRANCH.replace("/", "-")),
    event("claude-rco-2", "changes_requested", task="claude-rco-2/review-pr1800"),
    event("claude-rco-2", "changes_requested", task="other/task", payload_extra={"pull_request": PR}),
])
def test_t07_veto_variants_in_the_negative_scope_all_block(tmp_path, block):
    result = preview(tmp_path, bridge=with_events(block))
    assert result.check("rco_blocking_decision").reason == "rco_veto_active"


def test_t07_same_rco_later_pass_does_not_clear_its_own_veto(tmp_path):
    events = [finding("claude-rco-1")] + approvals("claude-rco-1")
    result = preview(tmp_path, bridge=mbm.BridgeSnapshot(tuple(events)))
    assert result.check("rco_blocking_decision").reason == "rco_veto_active"


def retraction(block: dict, *, agent=None, head=HEAD, target=None) -> dict:
    return event(agent or block["agent"], mbm.RETRACTION_STATUS, head=head,
                 payload_extra={"retracts_event_id": target or mmr.bridge_event_id(block)})


def test_t07_only_an_exactly_bound_retraction_clears(tmp_path):
    block = finding()
    cleared = preview(tmp_path, bridge=with_events(block, retraction(block)))
    assert cleared.check("rco_blocking_decision").status == mbm.CHECK_PASS
    for bad in (retraction(block, agent="claude-rco-1"), retraction(block, head=MOVED_HEAD),
                retraction(block, target="0" * 64)):
        result = preview(tmp_path, bridge=with_events(block, bad))
        assert result.check("rco_blocking_decision").reason == "rco_veto_active"


def test_t07_free_text_never_clears(tmp_path):
    block = finding()
    prose = event("claude-rco-2", "answered", type_="message")
    prose["message"] = "finding retracted, RCO_PASS, all clear"
    result = preview(tmp_path, bridge=with_events(block, prose))
    assert result.check("rco_blocking_decision").reason == "rco_veto_active"


def test_t07_the_other_rco_veto_outranks_a_pass(tmp_path):
    result = preview(tmp_path, bridge=with_events(finding("claude-rco-2")))
    assert result.check("approval_rco").status == mbm.CHECK_PASS
    assert result.check("rco_blocking_decision").reason == "rco_veto_active"


@pytest.mark.parametrize("missing", [1, 2])
def test_t07_both_build_votes_are_required(tmp_path, missing):
    events = [e for index, e in enumerate(approvals()) if index != missing]
    result = preview(tmp_path, bridge=mbm.BridgeSnapshot(tuple(events)))
    role = "approval_build_lead" if missing == 1 else "approval_build_tools"
    assert result.check(role).reason == "approval_missing"


def test_t07_runtime_model_or_effort_claims_are_not_approval(tmp_path):
    claims = [
        event("codex-tools-1", "approved", type_="message",
              payload_extra={"model": "claude-opus", "effort": "max", "approval": True}),
        event("grok", mmr.BUILD_CONSENSUS_STATUS, payload_extra={"model": "grok-4.7", "effort": "high"}),
    ]
    result = preview(tmp_path, bridge=mbm.BridgeSnapshot(tuple(approvals()[:2] + claims)))
    assert result.check("approval_build_tools").reason == "approval_missing"


def test_t07_build_vote_at_another_head_does_not_count(tmp_path):
    events = approvals()
    events[2]["payload"]["head"] = MOVED_HEAD
    result = preview(tmp_path, bridge=mbm.BridgeSnapshot(tuple(events)))
    assert result.check("approval_build_tools").reason == "approval_wrong_or_unbound_head"


# --- T08: queue, controls, CI, mergeability, base, rate -------------------------


def test_t08_batch_queue_incomplete_refuses(tmp_path):
    raw = mms.canonical_statement_bytes(make_statement(batch_order=2))
    result = preview(tmp_path, statement_bytes=raw)
    assert result.check("batch_queue").reason == "batch_queue_incomplete"


def test_t08_earlier_batch_member_must_be_merged(tmp_path):
    gh = FakeGh()
    gh.prs[1799] = gh_pr(1799, state="OPEN")
    first = mms.canonical_statement_bytes(make_statement(pull_request=1799, batch_order=1, nonce="1" * 32))
    raw = mms.canonical_statement_bytes(make_statement(batch_order=2))
    result = preview(tmp_path, statement_bytes=raw, batch_statements=(first,), gh_runner=gh)
    assert result.check("batch_queue").reason == "prerequisite_not_merged"
    gh.prs[1799] = gh_pr(1799, state="MERGED")
    assert preview(tmp_path, statement_bytes=raw, batch_statements=(first,), gh_runner=gh).check(
        "batch_queue").status == mbm.CHECK_PASS


def test_t08_foreign_batch_member_refuses(tmp_path):
    other = mms.canonical_statement_bytes(make_statement(pull_request=1799, batch_id="mma-20261007-other"))
    result = preview(tmp_path, batch_statements=(other,))
    assert result.check("batch_queue").reason == "batch_member_foreign"


def test_t08_changed_controls_refuse(tmp_path):
    first = preview(tmp_path)
    assert first.controls_digest is not None
    same = preview(tmp_path, expected_controls_digest=first.controls_digest)
    assert same.check("controls_unchanged").status == mbm.CHECK_PASS
    block = finding()
    changed = preview(tmp_path, expected_controls_digest=first.controls_digest, bridge=with_events(block))
    assert changed.check("controls_unchanged").reason == "controls_changed"
    # a later exact retraction clears the veto but the control set still differs from the preview
    cleared = preview(tmp_path, expected_controls_digest=first.controls_digest,
                      bridge=with_events(block, retraction(block)))
    assert cleared.check("rco_blocking_decision").status == mbm.CHECK_PASS
    assert cleared.check("controls_unchanged").reason == "controls_changed"


@pytest.mark.parametrize("runs,total,reason", [
    ([check_run(REQUIRED[0], conclusion="failure")] + [check_run(n) for n in REQUIRED[1:]], None, "ci_failed"),
    ([check_run(n) for n in REQUIRED[1:]], None, "ci_missing"),
    ([check_run(REQUIRED[0], status="in_progress", conclusion=None)] + [check_run(n) for n in REQUIRED[1:]], None, "ci_pending"),
    ([check_run(REQUIRED[0], head=MOVED_HEAD)] + [check_run(n) for n in REQUIRED[1:]], None, "ci_wrong_head"),
    ([check_run(REQUIRED[0], conclusion="skipped")] + [check_run(n) for n in REQUIRED[1:]], None, "ci_required_skipped"),
])
def test_t08_ci_must_be_green_at_the_exact_head(tmp_path, runs, total, reason):
    gh = FakeGh()
    gh.runs = runs
    result = preview(tmp_path, gh_runner=gh)
    check = result.check("ci_required_checks")
    assert check.status == mbm.CHECK_REFUSE and check.reason.startswith(reason)
    if reason == "ci_required_skipped":
        assert REQUIRED[0] in check.detail  # a required skip is named, never bypassed


def test_t08_partial_or_unreadable_ci_is_unknown(tmp_path):
    gh = FakeGh()
    gh.total = len(gh.runs) + 1
    assert preview(tmp_path, gh_runner=gh).check("ci_required_checks").reason == "ci_partial"
    gh = FakeGh()
    gh.required_rc = 1
    assert preview(tmp_path, gh_runner=gh).check("ci_required_checks").reason == "required_checks_unknown"
    gh = FakeGh()
    gh.required = {"contexts": [], "checks": []}
    assert preview(tmp_path, gh_runner=gh).check("ci_required_checks").status == mbm.CHECK_UNKNOWN


@pytest.mark.parametrize("pr,check_name,status,reason", [
    (gh_pr(mergeable="CONFLICTING", merge_state="DIRTY"), "mergeability", mbm.CHECK_REFUSE, "not_mergeable"),
    (gh_pr(merge_state="BLOCKED"), "mergeability", mbm.CHECK_REFUSE, "not_mergeable"),
    (gh_pr(mergeable="UNKNOWN", merge_state="UNKNOWN"), "mergeability", mbm.CHECK_UNKNOWN, "mergeability_unknown"),
    (gh_pr(base_ref="release"), "live_base_ref", mbm.CHECK_REFUSE, "base_ref_mismatch"),
    (gh_pr(state="CLOSED"), "live_pr_state", mbm.CHECK_REFUSE, "pull_request_not_open"),
    (gh_pr(state="MERGED"), "live_pr_state", mbm.CHECK_REFUSE, "pull_request_not_open"),
    (gh_pr(draft=True), "live_pr_draft", mbm.CHECK_REFUSE, "pull_request_draft"),
])
def test_t08_live_pr_state(tmp_path, pr, check_name, status, reason):
    gh = FakeGh()
    gh.prs[PR] = pr
    check = preview(tmp_path, gh_runner=gh).check(check_name)
    assert (check.status, check.reason) == (status, reason)


def test_t08_head_not_based_on_base_refuses(tmp_path):
    result = preview(tmp_path, git_runner=FakeGit(ancestor_rc=1))
    assert result.check("head_contains_base").reason == "head_not_based_on_base"
    assert preview(tmp_path, git_runner=FakeGit(ancestor_rc=128)).check("head_contains_base").status == mbm.CHECK_UNKNOWN


def test_t08_rate_exhausted_refuses(tmp_path):
    gh = FakeGh()
    gh.remaining = 3
    assert preview(tmp_path, gh_runner=gh).check("api_rate").reason == "rate_exhausted"


@pytest.mark.parametrize("payload", [
    {**gh_pr(), "extra": 1},
    {key: value for key, value in gh_pr().items() if key != "isDraft"},
    {**gh_pr(), "isDraft": "false"},
    {**gh_pr(), "headRefOid": HEAD.upper()},
])
def test_t08_ambiguous_live_pr_output_is_never_admitted(tmp_path, payload):
    gh = FakeGh()
    gh.prs[PR] = payload
    result = preview(tmp_path, gh_runner=gh)
    assert result.check("live_pr").reason == "live_fact_malformed"
    assert result.verdict != "admitted" and result.refusals == ()  # stops before any further check


def test_t08_duplicate_key_in_gh_output_refuses_parse():
    def runner(argv, *, input_bytes, timeout, env):
        return RunResult(0, b'{"number":1800,"number":1800}', b"")
    with pytest.raises(mbm.AdmissionError) as err:
        mbm.read_live_pull_request(PR, runner=runner)
    assert err.value.reason == "live_fact_malformed"


def test_t08_prerequisites_are_always_unknown_never_waived(tmp_path):
    result = preview(tmp_path)
    for name in mbm.EXPECTED_UNKNOWN_PREREQUISITES:
        check = result.check(f"prerequisite:{name}")
        assert (check.status, check.reason) == (mbm.CHECK_UNKNOWN, "no_genuine_adapter_in_g2")
    assert set(mmr.INTEGRATION_PREREQUISITES) <= set(mbm.EXPECTED_UNKNOWN_PREREQUISITES)


# --- T09: bootstrap, autonomous refusal, no effects -----------------------------


@pytest.mark.parametrize("route_path", [path for path in mbm.ROUTE_PATHS if path != ALLOWED_SIGNERS_PATH])
def test_t09_route_cannot_admit_its_own_change(tmp_path, route_path):
    paths = tuple(sorted(set(PATHS) | {route_path}))
    raw = mms.canonical_statement_bytes(make_statement(exact_paths=paths, diff=raw_diff(paths)))
    result = preview(tmp_path, statement_bytes=raw, git_runner=FakeGit(diff=raw_diff(paths)))
    assert result.check("bootstrap_self_admission").reason == "route_cannot_admit_itself"


def test_t09_anchor_change_is_refused_before_any_admission(tmp_path):
    paths = tuple(sorted(set(PATHS) | {ALLOWED_SIGNERS_PATH}))
    with pytest.raises(mms.StatementError):
        make_statement(exact_paths=paths, diff=raw_diff(paths))


def test_t09_missing_autonomous_refusal_is_unknown_and_never_built(tmp_path):
    result = preview(tmp_path, autonomous_refusal=None)
    check = result.check("autonomous_refusal")
    assert (check.status, check.reason) == (mbm.CHECK_UNKNOWN, "refusal_absent_unknown")
    absent = mmr.AutonomousRefusalEvidence("absent_unknown", absence_note="gate emitted no refusal event")
    check = preview(tmp_path, autonomous_refusal=absent).check("autonomous_refusal")
    assert (check.status, check.reason) == (mbm.CHECK_UNKNOWN, "refusal_absent_unknown")


def test_t09_recorded_refusal_is_preserved_unchanged(tmp_path):
    original = copy.deepcopy(REFUSAL_EVENT)
    evidence = mmr.AutonomousRefusalEvidence("recorded", event=original)
    check = preview(tmp_path, autonomous_refusal=evidence).check("autonomous_refusal")
    assert check.status == mbm.CHECK_PASS and mmr.bridge_event_id(REFUSAL_EVENT) in check.detail
    assert original == REFUSAL_EVENT


def test_t09_contradictory_refusal_evidence_refuses(tmp_path):
    bad = mmr.AutonomousRefusalEvidence("absent_unknown", event=copy.deepcopy(REFUSAL_EVENT), absence_note="x")
    assert preview(tmp_path, autonomous_refusal=bad).check("autonomous_refusal").status == mbm.CHECK_REFUSE


def test_t09_execute_is_unavailable():
    with pytest.raises(mbm.AdmissionError) as err:
        mbm.execute()
    assert err.value.reason == "execute_unavailable"


def test_t09_preview_has_no_effect_parameters():
    params = set(inspect.signature(mbm.preview_admission).parameters)
    assert not params & {"ledger", "nonce_ledger", "out_root", "receipt_dir", "merge_runner", "writer"}
    public = {name for name in vars(mbm) if not name.startswith("_") and callable(vars(mbm)[name])}
    assert not {name for name in public if any(word in name.lower() for word in ("merge_pr", "ready", "undraft", "reserve", "write"))}


@pytest.mark.parametrize("variant", ["nominal", "refused"])
def test_t09_preview_never_invokes_an_effect(tmp_path, monkeypatch, variant):
    hits: list[str] = []

    def spy(label):
        def _hit(*args, **kwargs):
            hits.append(label)
            raise AssertionError(f"effect called: {label}")
        return _hit

    monkeypatch.setattr(mms.NonceLedger, "__init__", spy("NonceLedger.__init__"))
    monkeypatch.setattr(mms.NonceLedger, "reserve", spy("NonceLedger.reserve"))
    monkeypatch.setattr(mms.NonceLedger, "transition", spy("NonceLedger.transition"))
    monkeypatch.setattr(mmr, "write_manual_merge_receipt", spy("write_manual_merge_receipt"))
    monkeypatch.setattr(mmr, "build_magma_triple", spy("build_magma_triple"))
    monkeypatch.setattr(subprocess, "run", spy("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", spy("subprocess.Popen"))
    gh, git = FakeGh(), FakeGit()
    extra = {} if variant == "nominal" else {"bridge": with_events(finding())}
    result = preview(tmp_path, gh_runner=gh, git_runner=git, **extra)
    assert hits == []
    assert result.effects == () and result.execute_available is False
    assert result.verdict == (mbm.VERDICT_UNKNOWN if variant == "nominal" else mbm.VERDICT_REFUSED)
    for argv in gh.calls:
        assert argv[1:3] == ["pr", "view"] or (argv[1] == "api" and len(argv) == 3)
    for argv in git.calls:
        assert not {"push", "merge", "commit", "update-ref", "checkout", "reset"} & set(argv)


@pytest.mark.parametrize("argv", [
    ["gh", "pr", "merge", "1800", "--squash"],
    ["gh", "pr", "ready", "1800"],
    ["gh", "pr", "edit", "1800"],
    ["gh", "api", "-X", "PUT", "repos/x/pulls/1800/merge"],
    ["gh", "api", "--method=PATCH", "repos/x"],
    ["gh", "api", "repos/x/issues/1/comments", "-f", "body=x"],
    ["gh", "api", "graphql", "--input", "q.json"],
])
def test_t09_gh_allowlist_refuses_every_mutation(argv):
    with pytest.raises(mbm.AdmissionError) as err:
        mbm.require_read_only_gh(argv)
    assert err.value.reason == "effect_refused"


@pytest.mark.parametrize("argv", [
    ["git", "-C", "r", "push", "origin", "x"],
    ["git", "-C", "r", "--no-replace-objects", "merge", "x"],
    ["git", "-C", "r", "--no-replace-objects", "merge-base", "--is-ancestor", "a"],
])
def test_t09_git_allowlist_refuses_everything_but_the_ancestry_read(argv):
    with pytest.raises(mbm.AdmissionError):
        mbm.require_read_only_git(argv)


def test_module_imports_only_public_route_apis():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tools."):
            assert node.module in ("tools.manual_bridge_merge_statement", "tools.manual_bridge_merge_receipt")
            imported += [alias.name for alias in node.names]
    assert imported and not [name for name in imported if name.startswith("_")]
    assert "write_manual_merge_receipt" not in imported and "NonceLedger" not in imported


def test_module_top_level_has_no_side_effect_statements():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        assert isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef, ast.Assign,
                                 ast.AnnAssign, ast.If, ast.Expr)), type(node).__name__
        if isinstance(node, ast.Expr):
            assert isinstance(node.value, ast.Constant)  # the docstring only
