# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/rule12_grok_ledger_adapter.py (Rule 12 Grok adapter, dormant)."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import bridge_rule12_review_eligibility as r12  # noqa: E402
from tools import rule12_grok_ledger_adapter as adapter  # noqa: E402
from tools import wd_grok_helper as helper  # noqa: E402

TASK = "fable-5/example-change-20261006"
H = "a" * 40
OLD = "b" * 40
NONCE = "c" * 32
NONCE2 = "d" * 32
GATE_NOW = "2026-10-06T17:00:00.0000000Z"
GATE_DT = datetime(2026, 10, 6, 17, 0, tzinfo=timezone.utc)
RUN_DT = datetime(2026, 10, 6, 16, 50, tzinfo=timezone.utc)
FABLE_AUTHOR = [{"agent": "fable-5", "role": "author"}]
PATHS = ["tools/a.py", "tests/test_a.py"]
DIFF = (
    "diff --git a/tools/a.py b/tools/a.py\n"
    "--- a/tools/a.py\n+++ b/tools/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    "diff --git a/tests/test_a.py b/tests/test_a.py\n"
    "--- a/tests/test_a.py\n+++ b/tests/test_a.py\n@@ -1 +1 @@\n-assert 1\n+assert 2\n"
)
COMMAND = ["fake-grok", "--model", "grok-test", "--effort", "high"]


def ev(agent, type_, status, head=H, task=TASK, ts="2026-10-06T16:30:00Z", **payload):
    event = {"agent": agent, "type": type_, "status": status, "task_id": task, "ts_utc": ts}
    event["payload"] = {"head": head, **payload}
    return event


def request(nonce=NONCE, slot="opposite_family", head=H, ts="2026-10-06T16:45:00Z", agent="claude-rco-1"):
    payload = adapter.rule12_grok_request_payload(head=head, slot=slot, nonce=nonce)
    event = ev(agent, "message", adapter.REQUEST_STATUS, head=head, ts=ts)
    event["payload"] = payload
    return event


REVIEWS = [
    ev("codex-lead-1", "message", "rco_recused"),
    ev("codex-tools-1", "message", "review_recused"),
    ev("claude-rco-1", "decision", "rco_pass"),
    ev("claude-rco-2", "decision", "rco_pass"),
]


class _HelperClock(datetime):
    """The helper stamps finished_at_utc from its own clock, not from consult(now=...)."""
    current = None

    @classmethod
    def now(cls, tz=None):
        return cls.current if cls.current is not None else datetime.now(tz)


@pytest.fixture(autouse=True)
def helper_clock(monkeypatch):
    monkeypatch.setattr(helper, "datetime", _HelperClock)
    _HelperClock.current = None
    yield _HelperClock
    _HelperClock.current = None


@pytest.fixture
def grok_root(tmp_path):
    root = tmp_path / "grok-scout-reports"
    root.mkdir()
    helper.write_state(root, {"schema": helper.SCHEMA, "status": "answered",
                              "last_attempt_utc": (RUN_DT - timedelta(hours=1)).isoformat()})
    return root


def run_grok(root, answer, nonce=NONCE, slot="opposite_family", now=RUN_DT, command=COMMAND,
             diff=DIFF, task=TASK, returncode=0, finished=None, requested_by="claude-rco-1"):
    _HelperClock.current = finished or now + timedelta(seconds=30)
    prompt = adapter.build_rule12_grok_prompt(task_id=TASK, head=H, slot=slot, nonce=nonce,
                                              diff_text=diff, changed_paths=PATHS)
    result = helper.consult(root, task, prompt, list(command), now=now, requested_by=requested_by,
                            runner=lambda *a, **k: SimpleNamespace(returncode=returncode, stdout=answer))
    return result


def collect(root, events, diff=DIFF, paths=PATHS, now=GATE_DT, head=H):
    return adapter.collect_rule12_grok_consultations(
        task_id=TASK, head=head, request_events=events, diff_text=diff, changed_paths=paths,
        reports_root=root, now_utc=now)


def evaluate(collected, events=REVIEWS):
    return r12.evaluate_rule12_review_eligibility(
        task_id=TASK, head=H, contributors=FABLE_AUTHOR, events=events, now_utc=GATE_NOW,
        grok_consultations=collected["consultations"],
        expected_diff_sha256=collected["expected_diff_sha256"],
        expected_prompt_sha256=collected["expected_prompt_sha256"],
        expected_files_total=collected["expected_files_total"],
        expected_nonce=collected["expected_nonce"])


def ledger_lines(root):
    return [json.loads(line) for line in (root / helper.LEDGER_NAME).read_text(encoding="utf-8").splitlines()]


# --- prompt and preamble ----------------------------------------------------


def test_preamble_is_the_helper_rules_text_byte_for_byte():
    tree = ast.parse((ROOT / "tools" / "wd_grok_helper.py").read_text(encoding="utf-8"))
    values = [ast.literal_eval(node.value) for node in ast.walk(tree)
              if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "rules" for t in node.targets)]
    assert values == [adapter.HELPER_RULES_PREAMBLE]


def test_prompt_is_deterministic_and_binds_every_input():
    base = dict(task_id=TASK, head=H, slot="rco", nonce=NONCE, diff_text=DIFF, changed_paths=PATHS)
    prompt = adapter.build_rule12_grok_prompt(**base)
    assert prompt == adapter.build_rule12_grok_prompt(**base)
    for key, value in (("task_id", TASK + "x"), ("head", OLD), ("slot", "opposite_family"),
                       ("nonce", NONCE2), ("diff_text", DIFF + "+y\n"), ("changed_paths", PATHS[::-1])):
        assert adapter.build_rule12_grok_prompt(**{**base, key: value}) != prompt, key
    assert prompt.startswith("WD Rule 12 Grok review request (wd.rule12-grok-prompt.v1)\n")
    assert DIFF in prompt and prompt.endswith("--- END DIFF ---\n")


@pytest.mark.parametrize("key,value", [
    ("task_id", " bad"), ("head", "A" * 40), ("head", "a" * 39), ("slot", "build"), ("nonce", "C" * 32),
    ("nonce", "c" * 31), ("diff_text", " \n"), ("diff_text", DIFF + "--- END DIFF ---\n"),
    ("changed_paths", []), ("changed_paths", "tools/a.py"), ("changed_paths", ["a", "a"]),
    ("changed_paths", ["a\nb"]), ("changed_paths", [" a"]),
])
def test_prompt_builder_refuses_malformed_input(key, value):
    base = dict(task_id=TASK, head=H, slot="rco", nonce=NONCE, diff_text=DIFF, changed_paths=PATHS)
    with pytest.raises(ValueError):
        adapter.build_rule12_grok_prompt(**{**base, key: value})


def test_prompt_over_the_helper_cap_is_refused():
    big = DIFF + "+" + "z" * adapter.MAX_PROMPT_BYTES + "\n"
    with pytest.raises(ValueError, match="cap"):
        adapter.build_rule12_grok_prompt(task_id=TASK, head=H, slot="rco", nonce=NONCE, diff_text=big,
                                         changed_paths=PATHS)


def test_request_bytes_follow_the_helper_text_mode_line_ending():
    prompt = adapter.build_rule12_grok_prompt(task_id=TASK, head=H, slot="rco", nonce=NONCE, diff_text=DIFF,
                                              changed_paths=PATHS)
    lf = adapter.rule12_grok_request_bytes(prompt, "\n")
    crlf = adapter.rule12_grok_request_bytes(prompt, "\r\n")
    assert lf == (adapter.HELPER_RULES_PREAMBLE + prompt).encode("utf-8")
    assert crlf == lf.replace(b"\n", b"\r\n")
    with pytest.raises(ValueError):
        adapter.rule12_grok_request_bytes(prompt, "\r")


# --- round trip through the real helper ledger ---------------------------------


def test_round_trip_approve_fills_the_vacant_slot(grok_root):
    run = run_grok(grok_root, "APPROVE\nNo concrete defect.")
    assert run["status"] == "answered"
    collected = collect(grok_root, [request()])
    assert collected["reasons"] == [] and collected["wired"] is False
    [consultation] = collected["consultations"]
    started = [e for e in ledger_lines(grok_root) if e["event"] == "started"]
    assert consultation["request_id"] == run["request_id"] == started[0]["request_id"]
    assert consultation["prompt_sha256"] == started[0]["request_sha256"] == collected["expected_prompt_sha256"]
    assert collected["expected_nonce"] == NONCE
    assert collected["expected_files_total"] == 2
    assert collected["expected_diff_sha256"] == hashlib.sha256(DIFF.encode("utf-8")).hexdigest()
    result = evaluate(collected)
    assert result["decision"] == "satisfied", result["reasons"]
    assert result["slots"]["opposite_family"]["state"] == "held_by_grok_fallback"
    assert result["slots"]["opposite_family"]["grok"]["request_id"] == run["request_id"]


def test_reject_answer_does_not_fill_and_does_not_block(grok_root):
    run_grok(grok_root, "REJECT\n- tools/a.py:1 wrong value")
    collected = collect(grok_root, [request()])
    assert collected["reasons"] == []
    result = evaluate(collected)
    assert result["decision"] == "not_satisfied"
    assert any("APPROVE" in reason for reason in result["reasons"])


def test_reject_then_reask_with_a_new_record_stays_not_satisfied(grok_root):
    run_grok(grok_root, "REJECT\n- defect")
    run_grok(grok_root, "APPROVE", nonce=NONCE2, now=RUN_DT + timedelta(minutes=5))
    events = [request(), request(nonce=NONCE2, ts="2026-10-06T16:52:00Z")]
    collected = collect(grok_root, events)
    assert collected["expected_nonce"] == NONCE
    assert len(collected["consultations"]) == 2
    assert evaluate(collected)["decision"] == "not_satisfied"


def test_record_order_follows_record_time_not_list_order(grok_root):
    run_grok(grok_root, "REJECT\n- defect")
    run_grok(grok_root, "APPROVE", nonce=NONCE2, now=RUN_DT + timedelta(minutes=5))
    events = [request(nonce=NONCE2, ts="2026-10-06T16:52:00Z"), request()]
    collected = collect(grok_root, events)
    assert collected["expected_nonce"] == NONCE
    assert evaluate(collected)["decision"] == "not_satisfied"


def test_same_prompt_run_twice_is_ambiguous_and_unanswered(grok_root):
    run_grok(grok_root, "REJECT")
    run_grok(grok_root, "APPROVE", now=RUN_DT + timedelta(minutes=1))
    collected = collect(grok_root, [request()])
    assert any("more than once" in reason for reason in collected["reasons"])
    assert collected["consultations"][0]["answer_text"] is None
    assert evaluate(collected)["decision"] == "not_satisfied"


def test_run_before_its_request_record_does_not_count(grok_root):
    run_grok(grok_root, "APPROVE", now=datetime(2026, 10, 6, 16, 40, tzinfo=timezone.utc))
    collected = collect(grok_root, [request(ts="2026-10-06T16:45:00Z")])
    assert any("not after its request record" in reason for reason in collected["reasons"])
    assert evaluate(collected)["decision"] == "not_satisfied"


def test_run_after_the_gate_clock_does_not_count(grok_root):
    run_grok(grok_root, "APPROVE")
    collected = collect(grok_root, [request()], now=RUN_DT - timedelta(seconds=1))
    assert any("gate clock" in reason for reason in collected["reasons"])


def _set_finished(root, value):
    path = root / helper.LEDGER_NAME
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if entry["event"] == "finished":
            if value is None:
                entry.pop("finished_at_utc")
            else:
                entry["finished_at_utc"] = value
        lines.append(json.dumps(entry) + "\n")
    path.write_text("".join(lines), encoding="utf-8")


def test_answer_finished_after_the_gate_clock_does_not_count(grok_root):
    # Tools 21:11:56Z on 0a146a2f: a run reserved before the gate clock but answered after it was accepted.
    run_grok(grok_root, "APPROVE", finished=GATE_DT + timedelta(days=1))
    collected = collect(grok_root, [request()])
    assert any("not finished after its run started and before the gate clock" in reason
               for reason in collected["reasons"])
    assert collected["consultations"][0]["answer_text"] is None
    assert evaluate(collected)["decision"] == "not_satisfied"


def test_answer_finished_before_its_run_started_does_not_count(grok_root):
    run_grok(grok_root, "APPROVE", finished=RUN_DT - timedelta(seconds=1))
    collected = collect(grok_root, [request()])
    assert any("not finished after its run started" in reason for reason in collected["reasons"])
    assert evaluate(collected)["decision"] == "not_satisfied"


@pytest.mark.parametrize("value", [None, "", "not-a-time", "2026-10-06T16:55:00", 1760000000])
def test_answer_without_a_parseable_finish_time_does_not_count(grok_root, value):
    run_grok(grok_root, "APPROVE")
    _set_finished(grok_root, value)
    collected = collect(grok_root, [request()])
    assert any("not finished after its run started" in reason for reason in collected["reasons"])
    assert evaluate(collected)["decision"] == "not_satisfied"


@pytest.mark.parametrize("finished", [RUN_DT, GATE_DT])
def test_answer_finished_at_either_bound_counts(grok_root, finished):
    run_grok(grok_root, "APPROVE", finished=finished)
    collected = collect(grok_root, [request()])
    assert collected["reasons"] == []
    assert evaluate(collected)["decision"] == "satisfied"


def test_effort_below_high_does_not_fill(grok_root):
    run_grok(grok_root, "APPROVE", command=["fake-grok", "--model", "grok-test", "--effort", "medium"])
    collected = collect(grok_root, [request()])
    assert any("effort" in reason for reason in collected["reasons"])
    result = evaluate(collected)
    assert result["decision"] == "not_satisfied"
    assert any("effort" in reason for reason in result["reasons"])


def test_failed_run_has_no_answer(grok_root):
    run_grok(grok_root, "APPROVE", returncode=1)
    collected = collect(grok_root, [request()])
    assert any("no single answered outcome" in reason for reason in collected["reasons"])
    assert evaluate(collected)["decision"] == "not_satisfied"


def test_tampered_report_is_refused(grok_root):
    run = run_grok(grok_root, "REJECT\n- defect")
    report = grok_root / f"{run['request_id']}-response.md"
    report.write_bytes(b"APPROVE\n")
    collected = collect(grok_root, [request()])
    assert any("report bytes do not match" in reason for reason in collected["reasons"])
    assert evaluate(collected)["decision"] == "not_satisfied"


@pytest.mark.skipif(sys.platform != "win32", reason="directory junctions are Windows-only")
def test_report_replaced_by_a_junction_is_refused(grok_root, tmp_path):
    run = run_grok(grok_root, "APPROVE")
    report = grok_root / f"{run['request_id']}-response.md"
    report.unlink()
    target = tmp_path / "elsewhere"
    target.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(report), str(target)], check=True, capture_output=True)
    collected = collect(grok_root, [request()])
    assert any("not a regular file" in reason for reason in collected["reasons"])


def test_reports_root_that_is_a_junction_is_refused(grok_root, tmp_path):
    if sys.platform != "win32":
        pytest.skip("directory junctions are Windows-only")
    run_grok(grok_root, "APPROVE")
    link = tmp_path / "root-link"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(grok_root)], check=True, capture_output=True)
    collected = collect(link, [request()])
    assert any("reparse point" in reason for reason in collected["reasons"])
    assert evaluate(collected)["decision"] == "not_satisfied"


def test_malformed_ledger_line_refuses_every_answer(grok_root):
    run_grok(grok_root, "APPROVE")
    with open(grok_root / helper.LEDGER_NAME, "ab") as stream:
        stream.write(b"{torn\n")
    collected = collect(grok_root, [request()])
    assert any("malformed lines" in reason for reason in collected["reasons"])
    assert evaluate(collected)["decision"] == "not_satisfied"


def test_incomplete_ledger_read_refuses_every_answer(grok_root, monkeypatch):
    run_grok(grok_root, "APPROVE")
    real = helper.read_ledger
    monkeypatch.setattr(helper, "read_ledger", lambda root: {**real(root), "complete": False})
    collected = collect(grok_root, [request()])
    assert any("incomplete" in reason for reason in collected["reasons"])
    assert collected["consultations"][0]["answer_text"] is None
    assert evaluate(collected)["decision"] == "not_satisfied"


def test_diff_changed_after_the_run_matches_no_helper_run(grok_root):
    run_grok(grok_root, "APPROVE")
    changed = DIFF.replace("+x = 2", "+x = 3")
    collected = collect(grok_root, [request()], diff=changed)
    assert any("no helper run" in reason for reason in collected["reasons"])
    assert evaluate(collected)["decision"] == "not_satisfied"


def test_helper_run_on_another_task_label_does_not_count(grok_root):
    run_grok(grok_root, "APPROVE", task="other/task")
    collected = collect(grok_root, [request()])
    assert any("no helper run" in reason for reason in collected["reasons"])


def test_records_for_another_head_or_task_are_ignored(grok_root):
    run_grok(grok_root, "APPROVE")
    other_task = request()
    other_task["task_id"] = "codex-lead-1/other"
    collected = collect(grok_root, [request(head=OLD), other_task])
    assert collected["consultations"] == []
    assert any("no grok_review_requested record" in reason for reason in collected["reasons"])


def test_changed_paths_must_equal_the_diff_headers(grok_root):
    run_grok(grok_root, "APPROVE")
    collected = collect(grok_root, [request()], paths=["tools/a.py"])
    assert collected["consultations"] == [] and collected["expected_diff_sha256"] == ""
    assert any("file headers" in reason for reason in collected["reasons"])


def test_malformed_record_nonce_leaves_no_expected_nonce(grok_root):
    run_grok(grok_root, "APPROVE")
    bad = request()
    bad["payload"]["nonce"] = "not-hex"
    collected = collect(grok_root, [bad, request(ts="2026-10-06T16:46:00Z")])
    assert collected["expected_nonce"] == ""
    assert evaluate(collected)["decision"] == "not_satisfied"


def test_naive_gate_clock_is_refused(grok_root):
    collected = collect(grok_root, [request()], now=datetime(2026, 10, 6, 17, 0))
    assert collected["consultations"] == []
    assert any("timezone-aware" in reason for reason in collected["reasons"])


def test_request_payload_validates_its_fields():
    assert adapter.rule12_grok_request_payload(head=H, slot="rco", nonce=NONCE) == {
        "head": H, "slot": "rco", "nonce": NONCE}
    for kwargs in ({"head": OLD.upper(), "slot": "rco", "nonce": NONCE},
                   {"head": H, "slot": "x", "nonce": NONCE},
                   {"head": H, "slot": "rco", "nonce": "c" * 64}):
        with pytest.raises(ValueError):
            adapter.rule12_grok_request_payload(**kwargs)


def test_adapter_is_not_wired_into_any_gate_or_runtime_path():
    for path in (ROOT / "tools").glob("*.py"):
        if path.name == "rule12_grok_ledger_adapter.py":
            continue
        assert "rule12_grok_ledger_adapter" not in path.read_text(encoding="utf-8"), path.name
    definition = json.loads((ROOT / "ops/windows/reboot/bridge-code-files.json").read_text(encoding="utf-8"))
    assert "tools/rule12_grok_ledger_adapter.py" not in json.dumps(definition)


# --- RCO2 F1/F2 (2026-10-07): a run binds to the record it was made for; the run's model is exported ---


def _rewrite_started(root, **changes):
    lines = ledger_lines(root)
    for entry in lines:
        if entry["event"] == "started":
            for key, value in changes.items():
                if value is _DROP:
                    entry.pop(key, None)
                else:
                    entry[key] = value
    (root / helper.LEDGER_NAME).write_text("".join(json.dumps(e) + "\n" for e in lines), encoding="utf-8")


_DROP = object()


def test_a_single_run_made_for_another_requester_does_not_fill(grok_root):
    run_grok(grok_root, "APPROVE", requested_by="claude-rco-2")
    collected = collect(grok_root, [request()])
    [consultation] = collected["consultations"]
    assert consultation["answer_text"] is None and consultation["unbound_runs"] == 1
    assert any("made for this record's requester" in reason for reason in collected["reasons"])
    assert evaluate(collected)["decision"] == "not_satisfied"


def test_own_and_foreign_runs_of_one_prompt_fill_from_the_own_run_only(grok_root):
    run = run_grok(grok_root, "APPROVE")
    run_grok(grok_root, "REJECT", now=RUN_DT + timedelta(minutes=1), requested_by="fable-5")
    collected = collect(grok_root, [request()])
    [consultation] = collected["consultations"]
    assert collected["reasons"] == [] and consultation["request_id"] == run["request_id"]
    assert consultation["unbound_runs"] == 1
    assert evaluate(collected)["decision"] == "satisfied"


def test_two_runs_made_for_the_same_requester_stay_ambiguous(grok_root):
    run_grok(grok_root, "APPROVE")
    run_grok(grok_root, "APPROVE", now=RUN_DT + timedelta(minutes=1))
    collected = collect(grok_root, [request()])
    assert any("more than once" in reason for reason in collected["reasons"])
    assert evaluate(collected)["decision"] == "not_satisfied"


def test_a_lead_record_binds_only_to_a_run_without_requested_by(grok_root):
    run_grok(grok_root, "APPROVE", requested_by=None)
    collected = collect(grok_root, [request(agent="codex-lead-1")])
    assert collected["reasons"] == [] and collected["consultations"][0]["answer_text"] is not None
    other = collect(grok_root, [request()])           # an rco-1 record cannot take Lead's own run
    assert other["consultations"][0]["answer_text"] is None


@pytest.mark.parametrize("agent", ["grok-scout-1", "operator", "Claude-rco-1", ""])
def test_a_record_agent_the_helper_never_names_binds_no_run(grok_root, agent):
    run_grok(grok_root, "APPROVE", requested_by=None)
    collected = collect(grok_root, [request(agent=agent)])
    assert collected["consultations"][0]["answer_text"] is None
    assert any("never a helper requester" in reason for reason in collected["reasons"])


@pytest.mark.parametrize("value", [_DROP, 5, "Claude-rco-1", ["claude-rco-1"]],
                         ids=["missing", "int", "case", "list"])
def test_a_missing_or_malformed_requested_by_never_fills(grok_root, value):
    run_grok(grok_root, "APPROVE")
    _rewrite_started(grok_root, requested_by=value)
    collected = collect(grok_root, [request()])
    assert collected["consultations"][0]["answer_text"] is None
    assert evaluate(collected)["decision"] == "not_satisfied"


def test_the_run_model_is_exported(grok_root):
    run_grok(grok_root, "APPROVE")
    [consultation] = collect(grok_root, [request()])["consultations"]
    assert consultation["model"] == "grok-test"


@pytest.mark.parametrize("value", [_DROP, None, 7, "bad model"], ids=["missing", "null", "int", "space"])
def test_a_missing_or_malformed_run_model_is_unknown_and_changes_nothing_else(grok_root, value):
    run_grok(grok_root, "APPROVE")
    _rewrite_started(grok_root, model=value)
    collected = collect(grok_root, [request()])
    [consultation] = collected["consultations"]
    if collected["reasons"]:
        assert consultation["answer_text"] is None          # the helper's own ledger check refused it
    else:
        assert consultation["model"] == "UNKNOWN" and evaluate(collected)["decision"] == "satisfied"


def test_no_bound_run_reports_an_unknown_model(grok_root):
    [consultation] = collect(grok_root, [request()])["consultations"]
    assert consultation["model"] == "UNKNOWN"


def test_rewriting_the_ledger_unchanged_still_fills(grok_root):
    run_grok(grok_root, "APPROVE")
    _rewrite_started(grok_root)
    collected = collect(grok_root, [request()])
    assert collected["reasons"] == [] and evaluate(collected)["decision"] == "satisfied"
