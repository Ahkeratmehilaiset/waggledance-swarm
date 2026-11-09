"""Per-task coverage and refusal-count diagnostic for content memory build receipts (synthetic inputs only).

The diagnostic reuses the public ``build_content_projection`` per approved task, so every count is exactly what a
full build counts for that task; it reports counts and closed reason codes only, never event text, refs or ids.
Silently excluded review-signal rows stay uncounted, exactly as in the projection itself."""

import hashlib
import json

import pytest

from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import test_shared_content_projection as fx
from tools import wd_q4_task_coverage_diagnostic as diag

LEAD, TOOLS, FABLE, RCO1 = fx.LEAD, fx.TOOLS, fx.FABLE, fx.RCO1
TASK_A = fx.TASK
TASK_B = "synthetic-content-task-v2"
TASK_C = "synthetic-content-task-v3-empty"
TASKS = [TASK_A, TASK_B, TASK_C]
SEALED_WORD = "".join(("se", "aled"))  # assembled so the test file itself carries no marker word in clear


def policy():
    return fx.pinned(fx.policy_doc(approved_tasks=TASKS))


def rows():
    """Three approved tasks: A has two kept rows and one personal drop, B has one kept row, one row of a type the
    policy does not list and a row about the restricted class, C has no rows at all; one row of an unapproved task;
    one reviewer-authored row and one review-signal typed row (both silently excluded, never counted)."""
    events = [
        fx.row("task a first synthetic work note", minute=1),
        fx.row("task a second synthetic work note", agent=TOOLS, minute=2),
        fx.row("task a personal note: call +358 40 1234567 tomorrow", agent=FABLE, minute=3),
        fx.row("task b synthetic work note", minute=4, task_id=TASK_B),
        fx.row("task b intent typed row", minute=5, task_id=TASK_B, type="intent"),
        fx.row("task b row about the " + SEALED_WORD + " material class", agent=TOOLS, minute=6, task_id=TASK_B),
        fx.row("row of an unapproved task", minute=7, task_id="synthetic-not-approved"),
        fx.row("reviewer row is silently excluded", agent=RCO1, minute=8),
        fx.row("decision typed review signal row is silently excluded", minute=9, type="decision"),
        fx.row("task b personal note: call +358 40 7654321 later", agent=FABLE, minute=10, task_id=TASK_B),
    ]
    return fx.rows_of(*events)


def coverage():
    raw, pin = policy()
    return diag.task_coverage(rows=rows(), policy_bytes=raw, policy_sha256=pin, roster=fx.ROSTER)


def test_per_task_counts_match_a_full_build_per_task():
    report = coverage()
    assert report["schema"] == diag.SCHEMA
    assert set(report["tasks"]) == set(TASKS)
    assert report["tasks"][TASK_A] == {"accepted": 2, "refused": {"personal": 1}}
    assert report["tasks"][TASK_B] == {"accepted": 1, "refused": {"personal": 1, "sealed": 1, "type_not_allowed": 1}}
    assert report["tasks"][TASK_C] == {"accepted": 0, "refused": {}}
    assert report["totals"] == {"accepted": 3, "refused": {"personal": 2, "sealed": 1, "task_not_approved": 1,
                                                           "type_not_allowed": 1}}
    assert report["label"] == diag.LABEL == "counts diagnostic, not a coverage guarantee"


def test_zero_coverage_tasks_are_named_and_sorted():
    report = coverage()
    assert report["zero_coverage_tasks"] == [TASK_C]
    raw, pin = fx.pinned(fx.policy_doc(approved_tasks=[TASK_C, TASK_B, "synthetic-also-empty"]))
    report = diag.task_coverage(rows=rows(), policy_bytes=raw, policy_sha256=pin, roster=fx.ROSTER)
    assert report["zero_coverage_tasks"] == sorted([TASK_C, "synthetic-also-empty"])


def test_totals_equal_the_full_build_and_unapproved_rows_are_one_bucket():
    raw, pin = policy()
    report = diag.task_coverage(rows=rows(), policy_bytes=raw, policy_sha256=pin, roster=fx.ROSTER)
    full = cp.build_content_projection(enabled=True, rows=rows(), policy_bytes=raw, policy_sha256=pin,
                                       roster=fx.ROSTER)
    assert report["totals"] == {"accepted": full["accepted"], "refused": full["refused"]}
    assert report["unapproved_rows"] == {"accepted": 0, "refused": {"task_not_approved": 1}}
    assert report["policy_sha256"] == pin
    assert report["match"] == "MATCH"


def test_silent_review_signal_rows_leave_no_count_anywhere():
    report = coverage()
    dumped = json.dumps(report)
    assert "silent" not in dumped and "review" not in dumped and RCO1 not in dumped
    counted = sum(report["totals"]["refused"].values()) + report["totals"]["accepted"]
    assert counted == 8  # ten rows: the reviewer row and the decision-typed row leave no count anywhere


def test_output_carries_counts_and_reason_codes_only():
    report = coverage()
    dumped = json.dumps(report)
    for text in ("synthetic work note", "1234567", SEALED_WORD + " material", "intent typed", "decision typed"):
        assert text not in dumped
    for ref in rows():
        assert ref not in dumped
    allowed = cp.DROP_TOKENS | cp.ROW_TOKENS
    for task, entry in report["tasks"].items():
        assert set(entry) == {"accepted", "refused"}
        assert type(entry["accepted"]) is int
        assert set(entry["refused"]) <= allowed
        assert all(type(n) is int and n > 0 for n in entry["refused"].values())


@pytest.mark.parametrize("bad", [None, [], "rows", {"not-hex": {}}])
def test_malformed_rows_refuse_before_any_count(bad):
    raw, pin = policy()
    with pytest.raises(diag.CoverageRefused) as info:
        diag.task_coverage(rows=bad, policy_bytes=raw, policy_sha256=pin, roster=fx.ROSTER)
    assert str(info.value) == "rows_refused"


@pytest.mark.parametrize("given", [lambda: rows(), lambda: {}])
def test_policy_pin_mismatch_refuses_before_any_count(given):
    raw, _ = policy()
    with pytest.raises(diag.CoverageRefused) as info:
        diag.task_coverage(rows=given(), policy_bytes=raw, policy_sha256="0" * 64, roster=fx.ROSTER)
    assert str(info.value) == "policy_refused"


def test_no_rows_at_all_reports_every_task_as_zero_coverage():
    raw, pin = policy()
    report = diag.task_coverage(rows={}, policy_bytes=raw, policy_sha256=pin, roster=fx.ROSTER)
    assert report["tasks"] == {task: {"accepted": 0, "refused": {}} for task in TASKS}
    assert report["zero_coverage_tasks"] == sorted(TASKS)
    assert report["totals"] == {"accepted": 0, "refused": {}} and report["match"] == "MATCH"


def test_match_reports_mismatch_when_the_full_build_disagrees_with_the_per_task_sums(monkeypatch):
    raw, pin = policy()
    real = cp.build_content_projection
    everything = len(rows())

    def skewed(**kwargs):
        projection = real(**kwargs)
        if len(kwargs["rows"]) == everything:
            projection = dict(projection, accepted=projection["accepted"] + 1)
        return projection

    monkeypatch.setattr(diag.cp, "build_content_projection", skewed)
    report = diag.task_coverage(rows=rows(), policy_bytes=raw, policy_sha256=pin, roster=fx.ROSTER)
    assert report["match"] == "MISMATCH"
    assert report["totals"]["accepted"] == 3


def test_default_off_reads_nothing():
    raw, pin = policy()
    assert diag.task_coverage(rows=rows(), policy_bytes=raw, policy_sha256=pin, roster=fx.ROSTER,
                              enabled=False) == {"status": "OFF"}


def test_coverage_from_an_existing_projection_counts_records_per_task():
    raw, pin = policy()
    full = cp.build_content_projection(enabled=True, rows=rows(), policy_bytes=raw, policy_sha256=pin,
                                       roster=fx.ROSTER)
    report = diag.coverage_from_projection(full, approved_tasks=TASKS)
    assert report["schema"] == diag.PROJECTION_SCHEMA
    assert report["records_per_task"] == {TASK_A: 2, TASK_B: 1, TASK_C: 0}
    assert report["zero_coverage_tasks"] == [TASK_C]
    assert report["accepted"] == full["accepted"] == 3
    dumped = json.dumps(report)
    assert "synthetic work note" not in dumped and full["records"][0]["record_id"] not in dumped


def test_receipt_wrapper_with_a_messages_projection_is_read_the_same_way():
    raw, pin = policy()
    full = cp.build_content_projection(enabled=True, rows=rows(), policy_bytes=raw, policy_sha256=pin,
                                       roster=fx.ROSTER)
    wrapped = {"schema": "synthetic-wrapper", "documents": None, "messages": full}
    assert diag.coverage_from_projection(wrapped, approved_tasks=TASKS) == \
        diag.coverage_from_projection(full, approved_tasks=TASKS)
    with pytest.raises(diag.CoverageRefused):
        diag.coverage_from_projection({"messages": "not a projection"}, approved_tasks=TASKS)


def test_projection_with_a_task_outside_the_approved_list_is_refused():
    raw, pin = policy()
    full = cp.build_content_projection(enabled=True, rows=rows(), policy_bytes=raw, policy_sha256=pin,
                                       roster=fx.ROSTER)
    with pytest.raises(diag.CoverageRefused) as info:
        diag.coverage_from_projection(full, approved_tasks=[TASK_B])
    assert str(info.value) == "projection_refused"


@pytest.mark.parametrize("bad", [None, "x", {"records": "no"}, {"records": [{"meta": {}}]}, {"records": [{}]}])
def test_malformed_projection_refuses(bad):
    with pytest.raises(diag.CoverageRefused) as info:
        diag.coverage_from_projection(bad, approved_tasks=TASKS)
    assert str(info.value) == "projection_refused"


def test_report_is_canonical_json_serializable_and_deterministic():
    one, two = coverage(), coverage()
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)
    digest = hashlib.sha256(json.dumps(one, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    assert len(digest) == 64
