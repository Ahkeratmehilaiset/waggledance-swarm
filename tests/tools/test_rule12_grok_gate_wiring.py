# SPDX-License-Identifier: BUSL-1.1
"""Rule 12 Grok RCO-slot wiring of the merge gate, receipt writer and executor.

Plan v3 (joint: codex-lead-1, Grok, fable-5; 2026-10-08). Under the opt-in
``review_policy=rule12`` plus ``grok_fallback`` switch, one bound Grok answer
from the helper ledger may hold a VACANT RCO slot next to a non-Grok
opposite-family holder. Every other case keeps the exact-head RCO_PASS blocker,
and every independent gate (peer block, CI, draft, base, receipt) still applies.
Each consultation here runs through the real ``wd_grok_helper.consult`` ledger
with a fake model runner.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import rule12_grok_ledger_adapter as adapter
from tools import wd_grok_helper as helper
from tools.bridge_pr_author import github_pr_git_identity_evidence
from tools.check_bridge_changes_requested import _author_task_id_aliases
from tools.check_standing_consensus_sign_class import classify_ab
from tools.idle_consensus_auto_merge import (
    GROK_REPORTS_ROOT,
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
BASE = "abcdef1234567890abcdef1234567890abcdef12"
TASK = "fable-5/rule12-grok-gate-fixture"
NOW = datetime(2026, 6, 7, 18, 0, tzinfo=timezone.utc)
DATE = "2026-06-07"
RUN_AT = datetime(2026, 6, 7, 17, 30, tzinfo=timezone.utc)
NONCE = "c" * 32
NONCE2 = "d" * 32
PATH = "tools/idle_daily_summary.py"
DIFF = (
    f"diff --git a/{PATH} b/{PATH}\n"
    f"--- a/{PATH}\n+++ b/{PATH}\n@@ -1 +1,2 @@\n def helper():\n+    return 1\n"
)
COMMAND = ["fake-grok", "--model", "grok-test", "--effort", "high"]
MISSING_RCO = "missing exact-head RCO_PASS from recognized non-author RCO"
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}


class _HelperClock(datetime):
    """The helper stamps finished_at_utc from its own clock."""

    current = None

    @classmethod
    def now(cls, tz=None):
        return cls.current if cls.current is not None else datetime.now(tz)


@pytest.fixture(autouse=True)
def helper_clock(monkeypatch):
    monkeypatch.setattr(helper, "datetime", _HelperClock)
    _HelperClock.current = None
    yield
    _HelperClock.current = None


@pytest.fixture
def root(tmp_path: Path) -> Path:
    reports = tmp_path / "grok-scout-reports"
    reports.mkdir()
    helper.write_state(reports, {"schema": helper.SCHEMA, "status": "answered",
                                 "last_attempt_utc": (RUN_AT - timedelta(hours=2)).isoformat()})
    return reports


def _identity_fields(author: str) -> dict:
    material = github_pr_git_identity_evidence(
        {
            "author": {"login": "Ahkeratmehilaiset", "name": "", "email": ""},
            "commits": [{"oid": HEAD, "authors": [
                {"name": author, "email": f"{author}@users.noreply.github.com", "login": ""}
            ]}],
        },
        expected_head_sha=HEAD,
    )
    identities = material.pop("identities")
    return {"git_identities": identities, "git_identity_evidence": material}


def _status(**overrides) -> dict:
    status = {
        "pr_number": 478,
        "head_sha": HEAD,
        "head_ref": TASK,
        "base_sha": BASE,
        "base_ref": "main",
        "base_tip_sha": BASE,
        "title": "Rule 12 Grok gate fixture",
        "mergeable": "clean",
        "state": "OPEN",
        "is_draft": False,
        "updated_at": "2026-06-07T17:00:00Z",
        "author_agent": "fable-5",
        "operator_approved": False,
        "receipt_verified": True,
        "changed_paths": [PATH],
        "diff_text": DIFF,
        "checks": [{"name": "unified", "state": "success", "status": "", "conclusion": ""}],
        **_identity_fields("fable-5"),
    }
    status.update(overrides)
    return status


def _event(agent: str, type_: str, status: str, ts: str, *, head: str | None = HEAD,
           task_id: str = TASK, uuid: str | None = None, payload: dict | None = None) -> dict:
    event = {
        "ts_utc": ts,
        "agent": agent,
        "type": type_,
        "status": status,
        "task_id": task_id,
        "message": f"{status} exact head {head}" if head is not None else "",
        "payload": payload if payload is not None else ({"pr": 478, "head": head} if head else {}),
    }
    event["agent_uuid"] = AGENT_UUIDS.get(agent, "") if uuid is None else uuid
    return event


def _claim() -> dict:
    event = _event("fable-5", "claim", "active", "2026-06-07T16:00:00Z", head=None)
    event["write_scope"] = ["*"]
    return event


def _request(agent: str = "codex-tools-1", nonce: str = NONCE, slot: str = "rco",
             ts: str = "2026-06-07T17:20:00Z") -> dict:
    payload = adapter.rule12_grok_request_payload(head=HEAD, slot=slot, nonce=nonce)
    return _event(agent, "message", adapter.REQUEST_STATUS, ts, payload=payload)


def _events(*extra: dict, requests: list[dict] | None = None) -> list[dict]:
    """Claude-authored; both RCOs recused at the head; Lead approves opposite family."""
    return [
        _claim(),
        _event("codex-lead-1", "decision", "build_consensus_pass", "2026-06-07T17:10:00Z"),
        _event("claude-rco-1", "message", "rco_recused", "2026-06-07T17:00:00Z"),
        _event("claude-rco-2", "message", "rco_recused", "2026-06-07T17:01:00Z"),
        *([_request()] if requests is None else requests),
        *extra,
    ]


def _run(root: Path, answer: str = "APPROVE\nNo concrete defect.", *, nonce: str = NONCE,
         slot: str = "rco", at: datetime = RUN_AT, diff: str = DIFF, command=COMMAND,
         requested_by: str = "codex-tools-1") -> dict:
    _HelperClock.current = at + timedelta(seconds=30)
    prompt = adapter.build_rule12_grok_prompt(task_id=TASK, head=HEAD, slot=slot, nonce=nonce,
                                              diff_text=diff, changed_paths=[PATH])
    return helper.consult(root, TASK, prompt, list(command), now=at, requested_by=requested_by,
                          runner=lambda *a, **k: SimpleNamespace(returncode=0, stdout=answer))


def _events_path(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e, sort_keys=True) for e in events), encoding="utf-8")
    return path


def _gate(tmp_path: Path, root: Path | None, events: list[dict] | None = None, **overrides) -> dict:
    kwargs = dict(
        pr_status=_status(),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _events() if events is None else events),
        bridge_task_id=TASK,
        utc_date=DATE,
        require_bridge_consensus=True,
        review_policy=REVIEW_POLICY_RULE12,
        now_utc=NOW,
        grok_fallback=root is not None,
    )
    if root is not None:
        kwargs["_grok_reports_root"] = root
    kwargs.update(overrides)
    return evaluate_auto_merge_gate(**kwargs)


def _blocked_on_rco(report: dict) -> None:
    assert report["ok"] is False
    assert MISSING_RCO in report["reasons"]
    if "grok_rco_slot_lift" in report:
        assert report["grok_rco_slot_lift"]["lifted"] is False
        assert report["grok_fallback_evidence"] is None


# --- the one admitted shape --------------------------------------------------
def test_grok_holds_the_vacant_rco_slot_and_the_gate_is_ready(tmp_path, root):
    run = _run(root)
    report = _gate(tmp_path, root)
    assert report["decision"] == "auto_merge_plan_ready", report["reasons"]
    assert MISSING_RCO not in report["reasons"]
    assert report["rco_pass_gate"]["ok"] is False  # no recognized RCO passed
    lift = report["grok_rco_slot_lift"]
    assert lift["lifted"] is True and lift["reasons"] == []
    assert lift["opposite_family_holders"] == ["codex-lead-1"]
    assert lift["veto_scan"]["decision"] == "no_recognized_rco_veto"
    slots = report["bridge_consensus"]["rule12"]["slots"]
    assert slots["rco"]["state"] == "held_by_grok_fallback"
    assert slots["rco"]["holders"] == []  # never recorded as an rco_pass
    evidence = report["grok_fallback_evidence"]
    assert evidence == {
        "task_id": TASK,
        "pr_number": 478,
        "base_sha": BASE,
        "head_sha": HEAD,
        "slot": "rco",
        "reviewer": "grok-scout-1",
        "requester": "codex-tools-1",
        "nonce": NONCE,
        "request_id": run["request_id"],
        "input_sha256": evidence["input_sha256"],
        "prompt_sha256": evidence["prompt_sha256"],
        "answer_sha256": evidence["answer_sha256"],
        "coverage": {"complete": True, "files_total": 1, "files_reviewed": 1},
        "review_policy": REVIEW_POLICY_RULE12,
        "reports_source": str(root),
    }
    # The answer text itself never enters the report.
    assert "No concrete defect" not in json.dumps(report)


# --- the switch and its inputs -----------------------------------------------
def test_rule12_without_the_switch_never_reads_grok_data(tmp_path, root):
    _run(root)
    report = _gate(tmp_path, None)
    _blocked_on_rco(report)
    assert "grok_adapter" not in report["bridge_consensus"]
    assert not any(key.startswith("grok_") for key in report)
    assert report["bridge_consensus"]["rule12"]["slots"]["rco"]["state"] == "vacant"


def test_the_fixed_root_is_the_helper_reports_root():
    assert GROK_REPORTS_ROOT == Path(r"C:\Python\grok-scout-reports")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"review_policy": REVIEW_POLICY_LEGACY, "now_utc": None}, "only read by review_policy=rule12"),
        ({"grok_fallback": 1}, "grok_fallback must be a boolean"),
        ({"grok_fallback": False}, "test seam for grok_fallback"),
    ],
)
def test_switch_inputs_are_validated(tmp_path, root, overrides, message):
    with pytest.raises(AutoMergeGateError) as excinfo:
        _gate(tmp_path, root, **overrides)
    assert any(message in error for error in excinfo.value.report["errors"])


def test_the_seam_must_be_a_path(tmp_path, root):
    with pytest.raises(AutoMergeGateError):
        _gate(tmp_path, root, _grok_reports_root=str(root))


def test_missing_reports_root_fills_nothing(tmp_path, root):
    _run(root)
    report = _gate(tmp_path, tmp_path / "no-such-root")
    _blocked_on_rco(report)


# --- Grok answers that never fill (and never veto) -----------------------------
def test_reject_does_not_fill(tmp_path, root):
    _run(root, "REJECT\n- tools/idle_daily_summary.py:2 wrong value")
    _blocked_on_rco(_gate(tmp_path, root))


def test_no_helper_run_does_not_fill(tmp_path, root):
    _blocked_on_rco(_gate(tmp_path, root))


def test_first_attempt_counts_a_new_nonce_after_reject_cannot_shop(tmp_path, root):
    _run(root, "REJECT\n- defect")
    _run(root, "APPROVE", nonce=NONCE2, at=RUN_AT + timedelta(minutes=5))
    events = _events(requests=[_request(), _request(nonce=NONCE2, ts="2026-06-07T17:33:00Z")])
    _blocked_on_rco(_gate(tmp_path, root, events))


def test_the_same_prompt_run_twice_is_ambiguous(tmp_path, root):
    _run(root, "REJECT")
    _run(root, "APPROVE", at=RUN_AT + timedelta(minutes=1))
    _blocked_on_rco(_gate(tmp_path, root))


def test_a_run_before_its_request_does_not_count(tmp_path, root):
    _run(root)
    events = _events(requests=[_request(ts="2026-06-07T17:40:00Z")])
    _blocked_on_rco(_gate(tmp_path, root, events))


def test_a_run_after_the_gate_clock_does_not_count(tmp_path, root):
    _run(root)
    _blocked_on_rco(_gate(tmp_path, root, now_utc=NOW - timedelta(minutes=45)))


def test_effort_below_high_does_not_fill(tmp_path, root):
    _run(root, command=["fake-grok", "--model", "grok-test", "--effort", "medium"])
    _blocked_on_rco(_gate(tmp_path, root))


def test_tampered_report_bytes_do_not_fill(tmp_path, root):
    run = _run(root)
    (root / f"{run['request_id']}-response.md").write_text("APPROVE\nforged", encoding="utf-8")
    _blocked_on_rco(_gate(tmp_path, root))


def test_malformed_ledger_line_refuses_every_answer(tmp_path, root):
    _run(root)
    with open(root / helper.LEDGER_NAME, "a", encoding="utf-8") as stream:
        stream.write("{not json\n")
    _blocked_on_rco(_gate(tmp_path, root))


def test_a_diff_one_byte_away_from_the_reviewed_prompt_does_not_fill(tmp_path, root):
    _run(root)
    _blocked_on_rco(_gate(tmp_path, root, pr_status=_status(diff_text=DIFF + "+\n")))


def test_changed_paths_must_equal_the_diff_headers(tmp_path, root):
    _run(root)
    status = _status(changed_paths=[PATH, "tools/other.py"])
    _blocked_on_rco(_gate(tmp_path, root, pr_status=status))


def test_oversize_diff_is_refused_not_claimed_reviewed(tmp_path, root):
    big = DIFF + "+" + "z" * adapter.MAX_PROMPT_BYTES + "\n"
    report = _gate(tmp_path, root, pr_status=_status(diff_text=big))
    _blocked_on_rco(report)
    assert any("cap" in reason for reason in report["bridge_consensus"]["grok_adapter"]["reasons"])


def test_binary_content_is_refused_not_claimed_reviewed(tmp_path, root):
    binary = f"diff --git a/{PATH} b/{PATH}\nindex 1..2 100644\nGIT binary patch\nliteral 3\n"
    report = _gate(tmp_path, root, pr_status=_status(diff_text=binary))
    _blocked_on_rco(report)
    assert any("binary" in reason for reason in report["bridge_consensus"]["grok_adapter"]["reasons"])


# --- Rule 12 condition 3 (stricter form) ---------------------------------------
@pytest.mark.parametrize("requester", ["fable-5", "claude-rco-1", "claude-rco-2"])
def test_author_or_rco_candidate_cannot_relay_the_rco_slot(tmp_path, root, requester):
    _run(root, requested_by=requester)
    report = _gate(tmp_path, root, _events(requests=[_request(agent=requester)]))
    _blocked_on_rco(report)
    reasons = report["bridge_consensus"]["rule12"]["grok_fallback"]["reasons"]
    assert any("implementer or a candidate" in reason for reason in reasons)


def test_an_unbound_request_record_is_not_an_attempt(tmp_path, root):
    _run(root)
    request = _request()
    request["agent_uuid"] = AGENT_UUIDS["codex-lead-1"]  # borrowed identity
    _blocked_on_rco(_gate(tmp_path, root, _events(requests=[request])))


# --- the slot shape ----------------------------------------------------------
def test_a_silent_eligible_rco_keeps_the_slot_pending(tmp_path, root):
    _run(root)
    events = [e for e in _events() if not (e["agent"] == "claude-rco-2" and e["type"] == "message"
                                          and e["status"] == "rco_recused")]
    report = _gate(tmp_path, root, events)
    _blocked_on_rco(report)
    assert report["bridge_consensus"]["rule12"]["slots"]["rco"]["state"] == "pending"


def test_whole_pool_external_review_stays_unavailable(tmp_path, root):
    _run(root, slot="external_review")
    events = [
        _claim(),
        _event("codex-lead-1", "message", "rco_recused", "2026-06-07T17:02:00Z"),
        _event("codex-tools-1", "message", "review_recused", "2026-06-07T17:03:00Z"),
        _event("claude-rco-1", "message", "rco_recused", "2026-06-07T17:00:00Z"),
        _event("claude-rco-2", "message", "rco_recused", "2026-06-07T17:01:00Z"),
        _request(slot="external_review"),
    ]
    report = _gate(tmp_path, root, events)
    _blocked_on_rco(report)
    consensus = report["bridge_consensus"]
    assert consensus["rule12"]["grok_fallback"]["filled"] == ["external_review"]
    assert consensus["ok"] is False
    assert any("report-only" in reason for reason in consensus["reasons"])


def test_an_opposite_family_grok_fill_never_lifts_the_rco_blocker(tmp_path, root):
    # Lead and Tools recused, so Grok holds the opposite-family slot; one RCO is
    # recused and the other silent, so the RCO slot is pending and nobody passed.
    _run(root, slot="opposite_family", requested_by="claude-rco-1")
    events = [
        _claim(),
        _event("codex-lead-1", "message", "rco_recused", "2026-06-07T17:02:00Z"),
        _event("codex-tools-1", "message", "review_recused", "2026-06-07T17:03:00Z"),
        _event("claude-rco-1", "message", "rco_recused", "2026-06-07T17:00:00Z"),
        _request(agent="claude-rco-1", slot="opposite_family"),
    ]
    report = _gate(tmp_path, root, events)
    _blocked_on_rco(report)
    assert report["bridge_consensus"]["identities"]["rco"]["agent"] == ""


# --- recognized-RCO vetoes survive a Grok APPROVE ------------------------------
@pytest.mark.parametrize(
    "veto",
    [
        _event("claude-rco-1", "finding", "changes_requested", "2026-06-07T17:40:00Z"),
        # An older veto from the other RCO, posted before its recusal.
        _event("claude-rco-2", "finding", "changes_requested", "2026-06-07T16:30:00Z"),
        # A veto at another head still blocks this path (no retraction is honoured).
        _event("claude-rco-1", "decision", "changes_requested", "2026-06-07T17:40:00Z",
               head="0" * 40),
        # Identity mismatch: a veto-shaped event still latches.
        _event("claude-rco-2", "finding", "changes_requested", "2026-06-07T17:40:00Z",
               uuid=AGENT_UUIDS["codex-lead-1"]),
        _event("claude-rco-2", "finding", "changes_requested", "2026-06-07T17:40:00Z", uuid=""),
    ],
)
def test_a_recognized_rco_veto_blocks_the_grok_lift(tmp_path, root, veto):
    _run(root)
    report = _gate(tmp_path, root, _events(veto))
    _blocked_on_rco(report)


def test_a_veto_on_an_author_alias_task_blocks_the_grok_lift(tmp_path, root):
    _run(root)
    aliases = _author_task_id_aliases(TASK, "fable-5")
    alias = next(iter(aliases), None)
    if alias is None:
        pytest.skip("the author has no task-id alias for this task")
    veto = _event("claude-rco-1", "finding", "changes_requested", "2026-06-07T17:40:00Z",
                  task_id=alias)
    _blocked_on_rco(_gate(tmp_path, root, _events(veto)))


def test_liveness_limit_an_authorized_retraction_does_not_reopen_the_grok_path(tmp_path, root):
    # Lead 17:32Z: the Grok path honours NO retraction (deliberately conservative,
    # unlike the existing clearing semantics). A veto followed by the same RCO's
    # authorized clear still keeps the Grok lift shut; only a recognized RCO's own
    # RCO_PASS (or the operator) can then fill the slot. Named limitation, no
    # history edit, no automatic clearance.
    _run(root)
    veto = _event("claude-rco-1", "finding", "changes_requested", "2026-06-07T16:30:00Z")
    clear = _event("claude-rco-1", "decision", "no_changes_requested", "2026-06-07T16:40:00Z")
    report = _gate(tmp_path, root, _events(veto, clear))
    assert report["bridge_peer_gate"]["clear_to_merge"] is True  # existing semantics: cleared
    _blocked_on_rco(report)  # the Grok path: still shut
    scan = report["grok_rco_slot_lift"]["veto_scan"]
    assert scan["decision"] == "recognized_rco_veto_present"


def test_the_veto_scan_runs_when_check_rco_pass_present_returns_early(tmp_path, root):
    # check_rco_pass_present stops at no_qualifying_pass before computing vetoes.
    _run(root)
    veto = _event("claude-rco-1", "finding", "changes_requested", "2026-06-07T17:40:00Z")
    report = _gate(tmp_path, root, _events(veto))
    assert report["rco_pass_gate"]["blocking_rco_agents"] == []
    scan = report["grok_rco_slot_lift"]["veto_scan"]
    assert scan["decision"] == "recognized_rco_veto_present"
    assert [row["agent"] for row in scan["veto_events"]] == ["claude-rco-1"]


# --- independent gates stay in force -------------------------------------------
@pytest.mark.parametrize(
    ("status_overrides", "reason"),
    [
        ({"is_draft": True}, "PR must not be a draft"),
        ({"checks": [{"name": "unified", "state": "failure", "status": "", "conclusion": ""}]},
         "status checks not green: unified"),
        ({"mergeable": "dirty"}, "mergeable state is not clean: dirty"),
        ({"head_sha": "f" * 40}, "exact head mismatch"),
    ],
)
def test_other_gates_still_block_a_grok_held_slot(tmp_path, root, status_overrides, reason):
    _run(root)
    report = _gate(tmp_path, root, pr_status=_status(**status_overrides))
    assert report["ok"] is False
    assert reason in report["reasons"]


def test_receipt_check_still_applies(tmp_path, root):
    _run(root)
    report = _gate(tmp_path, root, receipt_bundle_path="")
    assert report["ok"] is False
    assert "receipt_bundle_path is required before merge" in report["reasons"]


# --- receipt writer ----------------------------------------------------------
def test_receipt_binds_the_whole_grok_tuple(tmp_path, root):
    run = _run(root)
    report = write_bridge_consensus_merge_receipt(
        pr_status=_status(),
        events_path=_events_path(tmp_path, _events()),
        out_dir=tmp_path / "receipt",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        repo="example/repo",
        bridge_task_id=TASK,
        now_utc=NOW,
        review_policy=REVIEW_POLICY_RULE12,
        grok_fallback=True,
        _grok_reports_root=root,
    )
    manifest = Path(report["receipt_bundle_path"])
    assert verify_manifest(manifest)["ok"] is True
    payload = json.loads((manifest.parent / "payload-001-merge.json").read_text())
    evidence = payload["rule12_review"]["grok_fallback"]
    assert evidence == report["gate_report"]["grok_fallback_evidence"]
    assert evidence["request_id"] == run["request_id"]
    bundle_text = "".join(p.read_text() for p in manifest.parent.glob("*.json"))
    assert "rco:grok_fallback" in bundle_text
    assert "rco:pass_present" not in bundle_text
    assert "rule12_grok_ledger_adapter" in bundle_text


def test_receipt_refuses_the_switch_without_rule12(tmp_path):
    with pytest.raises(BridgeConsensusMergeReceiptError) as excinfo:
        write_bridge_consensus_merge_receipt(
            pr_status=_status(),
            events_path=_events_path(tmp_path, _events()),
            out_dir=tmp_path / "receipt",
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id=TASK,
            now_utc=NOW,
            grok_fallback=True,
        )
    assert excinfo.value.report["decision"] == "invalid_input"


def test_receipt_refuses_when_grok_did_not_fill(tmp_path, root):
    _run(root, "REJECT\n- defect")
    with pytest.raises(BridgeConsensusMergeReceiptError) as excinfo:
        write_bridge_consensus_merge_receipt(
            pr_status=_status(),
            events_path=_events_path(tmp_path, _events()),
            out_dir=tmp_path / "receipt",
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id=TASK,
            bridge_task_id=TASK,
            now_utc=NOW,
            review_policy=REVIEW_POLICY_RULE12,
            grok_fallback=True,
            _grok_reports_root=root,
        )
    assert excinfo.value.report["decision"] == "merge_plan_not_receipt_eligible"


def test_cli_switches_default_off():
    receipt_args = receipt_build_parser().parse_args(
        ["--pr-status-file", "s.json", "--out-dir", "o", "--expected-head", HEAD,
         "--consensus-proposal-id", TASK]
    )
    merge_args = merge_tool.build_parser().parse_args(
        ["478", "--out-dir", "o", "--expected-head", HEAD, "--expected-base-sha", BASE,
         "--consensus-proposal-id", TASK]
    )
    assert receipt_args.grok_fallback is False
    assert merge_args.grok_fallback is False


# --- merge executor ----------------------------------------------------------
EVIDENCE = {
    "task_id": TASK, "pr_number": 478, "base_sha": BASE, "head_sha": HEAD, "slot": "rco",
    "reviewer": "grok-scout-1", "requester": "codex-tools-1", "nonce": NONCE,
    "request_id": "0" * 32, "input_sha256": "1" * 64, "prompt_sha256": "2" * 64,
    "answer_sha256": "3" * 64, "coverage": {"complete": True, "files_total": 1, "files_reviewed": 1},
    "review_policy": REVIEW_POLICY_RULE12, "reports_source": str(GROK_REPORTS_ROOT),
}


def _execute(tmp_path, monkeypatch, receipt_evidence, fresh_evidence, **kwargs):
    seen: dict[str, list] = {"receipt": [], "gate": [], "commands": []}
    monkeypatch.setattr(merge_tool, "build_pr_status_snapshot", lambda **_: _status())

    def fake_receipt(**receipt_kwargs):
        seen["receipt"].append(receipt_kwargs)
        return {"receipt_bundle_path": str(tmp_path / "m.json"),
                "gate_report": {"ok": True, "grok_fallback_evidence": receipt_evidence}}

    def fake_gate(**gate_kwargs):
        seen["gate"].append(gate_kwargs)
        return {"ok": True, "reasons": [], "grok_fallback_evidence": fresh_evidence}

    def runner(command):
        seen["commands"].append(list(command))
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(merge_tool, "write_bridge_consensus_merge_receipt", fake_receipt)
    monkeypatch.setattr(merge_tool, "evaluate_auto_merge_gate", fake_gate)
    report = merge_tool.merge_with_bridge_receipt(
        pr_number=478, repo="example/repo", events_path=tmp_path / "events.jsonl",
        out_dir=tmp_path / "out", expected_head=HEAD, expected_base_sha=BASE,
        consensus_proposal_id=TASK, apply=True, review_policy=REVIEW_POLICY_RULE12,
        runner=runner, **kwargs,
    )
    merge_calls = [c for c in seen["commands"] if c[:3] == ["gh", "pr", "merge"]]
    return report, seen, merge_calls


@pytest.mark.parametrize("field", sorted(EVIDENCE))
def test_executor_rejects_any_swapped_tuple_field_before_merge(tmp_path, monkeypatch, field):
    fresh = dict(EVIDENCE)
    fresh[field] = {"swapped": True}
    report, _, merge_calls = _execute(tmp_path, monkeypatch, EVIDENCE, fresh, grok_fallback=True)
    assert report["decision"] == "apply_gate_recheck_failed"
    assert "differs from the receipt" in report["errors"][0]
    assert merge_calls == []


@pytest.mark.parametrize(("receipt", "fresh"), [(EVIDENCE, None), (None, EVIDENCE)])
def test_executor_rejects_a_grok_tuple_that_appears_or_vanishes(tmp_path, monkeypatch, receipt, fresh):
    report, _, merge_calls = _execute(tmp_path, monkeypatch, receipt, fresh, grok_fallback=True)
    assert report["decision"] == "apply_gate_recheck_failed"
    assert merge_calls == []


def test_executor_passes_the_switch_and_merges_only_on_an_equal_tuple(tmp_path, monkeypatch):
    report, seen, merge_calls = _execute(tmp_path, monkeypatch, EVIDENCE, dict(EVIDENCE),
                                         grok_fallback=True)
    assert seen["receipt"][0]["grok_fallback"] is True
    assert seen["gate"][0]["grok_fallback"] is True
    assert "now_utc" not in seen["gate"][0]  # the fresh gate reads the real clock
    assert len(merge_calls) == 1
    assert report["decision"] != "apply_gate_recheck_failed"


def test_executor_without_the_switch_passes_no_grok_arguments(tmp_path, monkeypatch):
    _, seen, _ = _execute(tmp_path, monkeypatch, None, None)
    assert "grok_fallback" not in seen["receipt"][0]
    assert "grok_fallback" not in seen["gate"][0]


def test_executor_refuses_the_switch_without_rule12(tmp_path):
    with pytest.raises(ValueError, match="needs review_policy=rule12"):
        merge_tool.merge_with_bridge_receipt(
            pr_number=478, repo="example/repo", events_path=tmp_path / "events.jsonl",
            out_dir=tmp_path / "out", expected_head=HEAD, expected_base_sha=BASE,
            consensus_proposal_id=TASK, grok_fallback=True,
        )


# --- governance class ----------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    ["tools/rule12_grok_ledger_adapter.py", "tests/tools/test_rule12_grok_gate_wiring.py",
     "tests/tools/test_rule12_grok_ledger_adapter.py"],
)
def test_grok_wiring_files_are_a_class_for_standing_sign(path):
    assert classify_ab([path])["ab_class"] == "a"
