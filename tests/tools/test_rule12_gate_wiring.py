# SPDX-License-Identifier: BUSL-1.1
"""Rule 12 opt-in wiring of the merge gate, receipt writer and merge executor.

The default ``review_policy`` stays the Rule 9a verifier. ``rule12`` swaps in
tools/bridge_rule12_review_eligibility.py over identity-bound evidence with an
explicit UTC clock. Grok consultations are not wired, so Grok never fills a
slot through the gate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from tools.bridge_pr_author import github_pr_git_identity_evidence
from tools.check_standing_consensus_sign_class import classify_ab
from tools.idle_consensus_auto_merge import (
    REVIEW_POLICY_LEGACY,
    REVIEW_POLICY_RULE12,
    AutoMergeGateError,
    evaluate_auto_merge_gate,
)
import tools.merge_with_bridge_receipt as merge_tool
from tools.verify_magma_receipt import verify_manifest
from tools.write_bridge_consensus_merge_receipt import (
    BridgeConsensusMergeReceiptError,
    build_parser as receipt_build_parser,
    write_bridge_consensus_merge_receipt,
)

HEAD = "1234567890abcdef1234567890abcdef12345678"
OTHER_HEAD = "0123456789abcdef0123456789abcdef01234567"
BASE = "abcdef1234567890abcdef1234567890abcdef12"
TASK = "fable-5/rule12-wiring-fixture"
NOW = datetime(2026, 6, 7, 18, 0, tzinfo=timezone.utc)
DATE = "2026-06-07"
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}


def _identity_fields(*commit_authors: dict) -> dict:
    material = github_pr_git_identity_evidence(
        {
            "author": {"login": "Ahkeratmehilaiset", "name": "", "email": ""},
            "commits": [{"oid": HEAD, "authors": list(commit_authors)}],
        },
        expected_head_sha=HEAD,
    )
    identities = material.pop("identities")
    return {"git_identities": identities, "git_identity_evidence": material}


def _agent_commit_author(agent: str) -> dict:
    return {"name": agent, "email": f"{agent}@users.noreply.github.com", "login": ""}


def _status(author: str = "fable-5", **overrides) -> dict:
    status = {
        "pr_number": 477,
        "head_sha": HEAD,
        "head_ref": TASK,
        "base_sha": BASE,
        "base_ref": "main",
        "base_tip_sha": BASE,
        "title": "Rule 12 wiring fixture",
        "mergeable": "clean",
        "state": "OPEN",
        "is_draft": False,
        "updated_at": "2026-06-07T17:00:00Z",
        "author_agent": author,
        "operator_approved": False,
        "receipt_verified": True,
        "changed_paths": ["tools/idle_daily_summary.py"],
        "diff_text": "+ def helper():\n+     return 1\n",
        "checks": [
            {"name": "unified", "state": "success", "status": "", "conclusion": ""},
        ],
        **_identity_fields(_agent_commit_author(author)),
    }
    status.update(overrides)
    return status


def _event(agent: str, type_: str, status: str, ts: str, *, head: str | None = HEAD,
           task_id: str = TASK, uuid: str | None = None) -> dict:
    event = {
        "ts_utc": ts,
        "agent": agent,
        "type": type_,
        "status": status,
        "task_id": task_id,
        "message": f"{status} exact head {head}" if head is not None else "",
        "payload": {"pr": 477, "head": head} if head is not None else {"pr": 477},
    }
    event["agent_uuid"] = AGENT_UUIDS.get(agent, "") if uuid is None else uuid
    return event


def _claim(agent: str = "fable-5") -> dict:
    event = _event(agent, "claim", "active", "2026-06-07T16:00:00Z", head=None)
    event["payload"] = {}
    event["write_scope"] = ["*"]
    return event


def _rule12_events(author: str = "fable-5") -> list[dict]:
    """Claude-authored: one GPT approval plus both recognized RCO passes."""
    return [
        _claim(author),
        _event("codex-lead-1", "decision", "build_consensus_pass", "2026-06-07T17:10:00Z"),
        _event("claude-rco-1", "decision", "rco_pass", "2026-06-07T17:20:00Z"),
        _event("claude-rco-2", "decision", "rco_pass", "2026-06-07T17:25:00Z"),
    ]


def _events_path(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events),
        encoding="utf-8",
    )
    return path


def _gate(tmp_path: Path, events: list[dict] | None = None, **overrides) -> dict:
    kwargs = dict(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _rule12_events() if events is None else events),
        bridge_task_id=TASK,
        utc_date=DATE,
        require_bridge_consensus=True,
        review_policy=REVIEW_POLICY_RULE12,
        now_utc=NOW,
    )
    kwargs.update(overrides)
    return evaluate_auto_merge_gate(**kwargs)


# --- default stays legacy --------------------------------------------------
def test_default_policy_is_legacy_and_needs_both_build_slots(tmp_path: Path) -> None:
    report = _gate(tmp_path, review_policy=REVIEW_POLICY_LEGACY, now_utc=None)
    assert report["review_policy"] == REVIEW_POLICY_LEGACY
    assert report["ok"] is False
    assert "review_policy" not in report["bridge_consensus"]
    assert any("build_tools" in reason for reason in report["reasons"])


def test_omitted_policy_equals_explicit_legacy(tmp_path: Path) -> None:
    kwargs = dict(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _rule12_events()),
        bridge_task_id=TASK,
        utc_date=DATE,
        require_bridge_consensus=True,
    )
    omitted = evaluate_auto_merge_gate(**kwargs)
    explicit = evaluate_auto_merge_gate(**kwargs, review_policy=REVIEW_POLICY_LEGACY)
    assert omitted == explicit


# --- rule12 satisfied ------------------------------------------------------
def test_rule12_one_opposite_family_plus_every_rco_is_ready(tmp_path: Path) -> None:
    report = _gate(tmp_path)
    assert report["decision"] == "auto_merge_plan_ready", report["reasons"]
    consensus = report["bridge_consensus"]
    assert consensus["review_policy"] == REVIEW_POLICY_RULE12
    assert consensus["decision"] == "rule12_best_available_consensus"
    assert consensus["rule12"]["decision"] == "satisfied"
    assert consensus["now_utc"] == "2026-06-07T18:00:00Z"
    assert consensus["contributors"] == [{"agent": "fable-5", "role": "author"}]
    assert consensus["identities"]["opposite_family"]["holders"] == ["codex-lead-1"]
    assert consensus["identities"]["build_lead"]["approved"] is True
    assert consensus["identities"]["build_tools"]["approved"] is False
    assert [ref["agent"] for ref in consensus["rco_pass_refs"]] == [
        "claude-rco-1",
        "claude-rco-2",
    ]
    assert consensus["rco_pass_ref"]["agent"] == "claude-rco-1"


def test_rule12_tools_alone_also_fills_the_opposite_family_slot(tmp_path: Path) -> None:
    events = _rule12_events()
    events[1] = _event("codex-tools-1", "decision", "build_consensus_pass",
                       "2026-06-07T17:10:00Z")
    report = _gate(tmp_path, events)
    assert report["ok"] is True, report["reasons"]
    assert report["bridge_consensus"]["identities"]["opposite_family"]["holders"] == [
        "codex-tools-1"
    ]


# --- rule12 fail-closed ----------------------------------------------------
def test_rule12_every_present_rco_must_pass(tmp_path: Path) -> None:
    events = [e for e in _rule12_events() if e["agent"] != "claude-rco-2"]
    report = _gate(tmp_path, events)
    assert report["ok"] is False
    assert any("rco slot is pending" in reason for reason in report["reasons"])


def test_rule12_rco_veto_outranks_passes(tmp_path: Path) -> None:
    events = _rule12_events() + [
        _event("claude-rco-2", "finding", "changes_requested", "2026-06-07T17:40:00Z"),
    ]
    report = _gate(tmp_path, events)
    assert report["ok"] is False
    assert report["bridge_consensus"]["blocking_rco_agents"] == ["claude-rco-2"]
    assert report["bridge_consensus"]["rule12"]["decision"] == "blocked"


def test_rule12_approval_after_the_clock_does_not_count(tmp_path: Path) -> None:
    events = _rule12_events()
    events[1] = _event("codex-lead-1", "decision", "build_consensus_pass",
                       "2026-06-07T18:30:00Z")
    report = _gate(tmp_path, events)
    assert report["ok"] is False
    assert any("opposite_family slot is pending" in r for r in report["reasons"])
    # The same evidence passes once the clock is past it.
    later = _gate(tmp_path, events, now_utc=NOW + timedelta(hours=1))
    assert later["ok"] is True, later["reasons"]


def test_rule12_ignores_an_approval_with_a_borrowed_uuid(tmp_path: Path) -> None:
    events = _rule12_events()
    events[1] = _event("codex-lead-1", "decision", "build_consensus_pass",
                       "2026-06-07T17:10:00Z", uuid=AGENT_UUIDS["codex-tools-1"])
    report = _gate(tmp_path, events)
    assert report["ok"] is False
    ignored = report["bridge_consensus"]["ignored_identity_mismatch_events"]
    assert [row["agent"] for row in ignored] == ["codex-lead-1"]
    assert ignored[0]["identity_binding_status"] == "mismatch_uuid"


def test_rule12_ignores_an_approval_without_a_uuid(tmp_path: Path) -> None:
    events = _rule12_events()
    events[1] = _event("codex-lead-1", "decision", "build_consensus_pass",
                       "2026-06-07T17:10:00Z", uuid="")
    report = _gate(tmp_path, events)
    assert report["ok"] is False
    ignored = report["bridge_consensus"]["ignored_identity_mismatch_events"]
    assert ignored[0]["identity_binding_status"] == "missing_uuid"


def test_rule12_approval_at_another_head_does_not_count(tmp_path: Path) -> None:
    events = _rule12_events()
    events[1] = _event("codex-lead-1", "decision", "build_consensus_pass",
                       "2026-06-07T17:10:00Z", head=OTHER_HEAD)
    report = _gate(tmp_path, events)
    assert report["ok"] is False


def test_rule12_same_family_approval_does_not_fill_the_slot(tmp_path: Path) -> None:
    # Tools-authored change: Lead is the same family, so a Claude lane is needed.
    task = "codex-tools-1/rule12-wiring-fixture"
    events = [dict(e, task_id=task) for e in _rule12_events(author="codex-tools-1")]
    status = _status(author="codex-tools-1", head_ref=task)
    report = _gate(tmp_path, events, pr_status=status, consensus_proposal_id=task,
                   bridge_task_id=task)
    assert report["author_resolution"]["author_agent"] == "codex-tools-1"
    assert report["ok"] is False
    assert report["bridge_consensus"]["contributors"] == [
        {"agent": "codex-tools-1", "role": "author"}
    ]
    standing = report["bridge_consensus"]["rule12"]["slots"]["opposite_family"]["standing"]
    assert "codex-lead-1" not in standing
    assert any("opposite_family slot" in reason for reason in report["reasons"])


def test_rule12_refuses_unknown_commit_identity(tmp_path: Path) -> None:
    # A commit identity that maps to no registered agent could be any lane, so
    # it cannot prove that a reviewer did not implement the change.
    status = _status(
        **_identity_fields(
            _agent_commit_author("fable-5"),
            {"name": "Jani", "email": "jani@jkhservice.fi", "login": ""},
        )
    )
    report = _gate(tmp_path, pr_status=status)
    assert report["ok"] is False
    unknown = report["bridge_consensus"]["unknown_contributor_identities"]
    assert [row["name"] for row in unknown] == ["Jani"]
    assert any("unknown commit identities" in reason for reason in report["reasons"])


def test_rule12_pr_opener_account_is_not_an_unknown_contributor(tmp_path: Path) -> None:
    report = _gate(tmp_path)
    assert report["bridge_consensus"]["unknown_contributor_identities"] == []


def test_rule12_refuses_when_the_author_is_unresolved(tmp_path: Path) -> None:
    events = [e for e in _rule12_events() if e["type"] != "claim"]
    report = _gate(tmp_path, events)
    assert report["ok"] is False
    assert report["bridge_consensus"]["rule12"] is None
    assert any("resolved PR author" in reason for reason in report["reasons"])


def test_rule12_keeps_the_legacy_rco_pass_blocker(tmp_path: Path) -> None:
    # No RCO speaks at all: the RCO slot would be "eligible but no pass" and the
    # always-on exact-head RCO_PASS blocker also stays in force under rule12.
    events = [e for e in _rule12_events() if not e["agent"].startswith("claude-rco")]
    report = _gate(tmp_path, events)
    assert report["ok"] is False
    assert "missing exact-head RCO_PASS from recognized non-author RCO" in report["reasons"]


def test_rule12_never_fills_a_slot_with_grok(tmp_path: Path) -> None:
    events = _rule12_events() + [
        _event("grok-scout-1", "decision", "rco_pass", "2026-06-07T17:30:00Z", uuid=""),
        _event("grok-scout-1", "decision", "build_consensus_pass",
               "2026-06-07T17:30:00Z", uuid=""),
    ]
    events = [e for e in events if e["agent"] != "codex-lead-1"]
    report = _gate(tmp_path, events)
    assert report["ok"] is False
    assert report["bridge_consensus"]["rule12"]["grok_fallback"]["filled"] == []


# --- input validation ------------------------------------------------------
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"review_policy": "rule13"}, "review_policy must be one of"),
        ({"review_policy": None}, "review_policy must be one of"),
        ({"now_utc": datetime(2026, 6, 7, 18, 0)}, "timezone-aware"),
        ({"now_utc": "2026-06-07T18:00:00Z"}, "timezone-aware"),
        ({"review_policy": REVIEW_POLICY_LEGACY}, "only read by review_policy=rule12"),
        ({"require_bridge_consensus": False}, "requires require_bridge_consensus"),
        ({"standing_consensus_sign": True}, "standing_consensus_sign is not supported"),
        ({"allow_lead_stall_failover": True}, "lead-stall failover is not supported"),
        ({"apply": True}, "now_utc must be omitted when apply is true"),
        ({"utc_date": "2026-06-08"}, "rate-limit UTC date"),
    ],
)
def test_rule12_invalid_inputs_refuse(tmp_path: Path, overrides: dict, message: str) -> None:
    with pytest.raises(AutoMergeGateError) as excinfo:
        _gate(tmp_path, **overrides)
    assert excinfo.value.report["decision"] == "invalid_input"
    assert any(message in error for error in excinfo.value.report["errors"])


# --- receipt writer --------------------------------------------------------
def test_rule12_receipt_names_the_policy_and_slot_holders(tmp_path: Path) -> None:
    report = write_bridge_consensus_merge_receipt(
        pr_status=_status(),
        events_path=_events_path(tmp_path, _rule12_events()),
        out_dir=tmp_path / "receipt",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        repo="example/repo",
        bridge_task_id=TASK,
        now_utc=NOW,
        review_policy=REVIEW_POLICY_RULE12,
    )
    manifest = Path(report["receipt_bundle_path"])
    assert verify_manifest(manifest)["ok"] is True
    directory = manifest.parent
    payload = json.loads((directory / "payload-001-merge.json").read_text())
    assert payload["review_policy"] == REVIEW_POLICY_RULE12
    assert payload["rule12_review"]["now_utc"] == "2026-06-07T18:00:00Z"
    assert payload["rule12_review"]["evaluation"]["decision"] == "satisfied"
    assert [ref["agent"] for ref in payload["rule12_review"]["rco_pass_refs"]] == [
        "claude-rco-1",
        "claude-rco-2",
    ]
    bundle_text = "".join(p.read_text() for p in directory.glob("*.json"))
    assert "rule12:best_available_consensus" in bundle_text
    assert "policy:rule12_best_available_consensus_v1" in bundle_text
    assert "bridge_consensus:three_identity_head_bound" not in bundle_text


def test_rule12_receipt_refuses_when_rule12_is_not_satisfied(tmp_path: Path) -> None:
    events = [e for e in _rule12_events() if e["agent"] != "claude-rco-2"]
    with pytest.raises(BridgeConsensusMergeReceiptError) as excinfo:
        write_bridge_consensus_merge_receipt(
            pr_status=_status(),
            events_path=_events_path(tmp_path, events),
            out_dir=tmp_path / "receipt",
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id=TASK,
            bridge_task_id=TASK,
            now_utc=NOW,
            review_policy=REVIEW_POLICY_RULE12,
        )
    assert excinfo.value.report["decision"] == "merge_plan_not_receipt_eligible"
    assert not (tmp_path / "receipt").exists()


def test_receipt_writer_refuses_unknown_policy(tmp_path: Path) -> None:
    with pytest.raises(BridgeConsensusMergeReceiptError) as excinfo:
        write_bridge_consensus_merge_receipt(
            pr_status=_status(),
            events_path=_events_path(tmp_path, _rule12_events()),
            out_dir=tmp_path / "receipt",
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id=TASK,
            now_utc=NOW,
            review_policy="rule13",
        )
    assert excinfo.value.report["decision"] == "invalid_input"


def test_receipt_cli_defaults_to_legacy_policy() -> None:
    args = receipt_build_parser().parse_args(
        ["--pr-status-file", "s.json", "--out-dir", "o", "--expected-head", HEAD,
         "--consensus-proposal-id", TASK]
    )
    assert args.review_policy == REVIEW_POLICY_LEGACY


# --- merge executor --------------------------------------------------------
def test_merge_executor_cli_defaults_to_legacy_policy() -> None:
    args = merge_tool.build_parser().parse_args(
        ["477", "--out-dir", "o", "--expected-head", HEAD, "--expected-base-sha", BASE,
         "--consensus-proposal-id", TASK]
    )
    assert args.review_policy == REVIEW_POLICY_LEGACY


def test_merge_executor_refuses_unknown_policy(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="review_policy must be one of"):
        merge_tool.merge_with_bridge_receipt(
            pr_number=477,
            repo="example/repo",
            events_path=tmp_path / "events.jsonl",
            out_dir=tmp_path / "out",
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id=TASK,
            review_policy="rule13",
        )


def test_merge_executor_passes_the_policy_to_receipt_and_fresh_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, list] = {"receipt": [], "gate": []}

    monkeypatch.setattr(merge_tool, "build_pr_status_snapshot",
                        lambda **_: _status())

    def fake_receipt(**kwargs):
        seen["receipt"].append(kwargs)
        return {"receipt_bundle_path": str(tmp_path / "receipt" / "manifest.json")}

    def fake_gate(**kwargs):
        seen["gate"].append(kwargs)
        return {"ok": False, "reasons": ["stop before merge"]}

    monkeypatch.setattr(merge_tool, "write_bridge_consensus_merge_receipt", fake_receipt)
    monkeypatch.setattr(merge_tool, "evaluate_auto_merge_gate", fake_gate)
    report = merge_tool.merge_with_bridge_receipt(
        pr_number=477,
        repo="example/repo",
        events_path=tmp_path / "events.jsonl",
        out_dir=tmp_path / "out",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        apply=True,
        review_policy=REVIEW_POLICY_RULE12,
        runner=lambda command: None,
    )
    assert report["decision"] == "apply_gate_recheck_failed"
    assert seen["receipt"][0]["review_policy"] == REVIEW_POLICY_RULE12
    assert seen["gate"][0]["review_policy"] == REVIEW_POLICY_RULE12
    # The fresh gate reads the real clock: no injected now_utc under apply.
    assert "now_utc" not in seen["gate"][0]


def test_merge_executor_legacy_passes_no_rule12_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict] = []
    monkeypatch.setattr(merge_tool, "build_pr_status_snapshot", lambda **_: _status())
    monkeypatch.setattr(
        merge_tool,
        "write_bridge_consensus_merge_receipt",
        lambda **kwargs: {"receipt_bundle_path": str(tmp_path / "m.json")},
    )

    def fake_gate(**kwargs):
        seen.append(kwargs)
        return {"ok": False, "reasons": ["stop"]}

    monkeypatch.setattr(merge_tool, "evaluate_auto_merge_gate", fake_gate)
    merge_tool.merge_with_bridge_receipt(
        pr_number=477,
        repo="example/repo",
        events_path=tmp_path / "events.jsonl",
        out_dir=tmp_path / "out",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        apply=True,
        runner=lambda command: None,
    )
    assert "review_policy" not in seen[0]
    assert "now_utc" not in seen[0]


# --- governance class ------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    [
        "tools/bridge_rule12_review_eligibility.py",
        "tests/tools/test_bridge_rule12_review_eligibility.py",
        "tests/tools/test_rule12_gate_wiring.py",
    ],
)
def test_rule12_files_are_a_class_for_standing_sign(path: str) -> None:
    assert classify_ab([path])["ab_class"] == "a"
