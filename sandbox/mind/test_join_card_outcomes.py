"""Tests of LEARN slice 1 (join_card_outcomes). Synthetic controls, each with its truth written next to it, and one
named mutant per control that must flip it, so no control is vacuous.

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/test_join_card_outcomes.py -q
"""

import hashlib
import json
import random
import sys
from datetime import timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import join_card_outcomes as jco  # noqa: E402
from join_card_outcomes import lane_view as lv  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402

PR, PR2 = 1905, 1906
BRANCH, BRANCH2 = "codex-lead-1/operator-manual-ten-train-20261013", "codex-tools-1/second-20261014"
HEAD_A = hashlib.sha1(b"head A").hexdigest()
HEAD_B = hashlib.sha1(b"head B").hexdigest()
HEAD_C = hashlib.sha1(b"head C").hexdigest()
HEAD_A_TWIN = HEAD_A[:12] + hashlib.sha1(b"twin").hexdigest()[12:]  # same 12-character prefix, another full SHA
LANE = "codex-tools-1"
ISSUED, READ = "2026-10-14T10:00:30Z", "2026-10-14T10:05:00Z"
VOTE_AT = "2026-10-14T10:20:00Z"  # 15 min after the read
NONCE = "nonce-0001"
SOURCE = {"source": "synthetic", "source_sha256": "0" * 64}


def line_text(pr, head, branch, slot="build_tools"):
    return (f"build vote on PR{pr} at {head[:12]} is the only empty slot ({slot}, task {branch}); filled at this "
            f"head: build_lead 0123456789abcdef; GitHub 6/6 at {head[:12]}; waiting since 2026-10-14T09:00:00Z; "
            "route UNKNOWN; the vote is still required")


def make_view(second=False, record_head=HEAD_A):
    records = {str(PR): {"pr": PR, "state": "action", "empty_slots": ["build_tools"], "github_head": record_head,
                         "branch": BRANCH}}
    actions = [line_text(PR, HEAD_A, BRANCH)]
    if second:
        records[str(PR2)] = {"pr": PR2, "state": "action", "empty_slots": ["build_tools"], "github_head": HEAD_C,
                             "branch": BRANCH2}
        actions.append(line_text(PR2, HEAD_C, BRANCH2))
    view = {"schema": lv.SCHEMA, "authority": "none", "scope": lv.SCOPE, "as_of_utc": "2026-10-14T10:00:00Z",
            "coverage": "complete", "lanes": {lane: {"actions": [], "more": 0} for lane in lv.LANES},
            "abstained": [], "refused_rows": [], "prs": records, "counts": {}}
    view["lanes"][LANE]["actions"] = actions
    return json.dumps(view, sort_keys=True).encode("utf-8")


def vote(ts=VOTE_AT, head=HEAD_A, task=BRANCH, agent=LANE, status="build_consensus_pass", kind="decision", pr=PR,
         all_keys=False):
    payload = {"pr": pr, "head": head}
    if all_keys:
        payload.update(exact_head=head, head_sha=head)
    return {"ts_utc": ts, "agent": agent, "type": kind, "status": status, "task_id": task, "payload": payload}


def truth(*rows):
    return [{"pr": PR, "head": head, "observed_utc": at, **SOURCE} for at, head in rows]


def pair(body):
    return {"ref": event_id(body), "body": body}


def base(**change):
    """The true-positive inputs; each control changes exactly the named inputs."""
    view = change.pop("view", make_view())
    card = change.pop("card", lv.render_text(json.loads(view), LANE).encode("utf-8"))
    sha = hashlib.sha256
    inputs = {
        "view_bytes": view, "card_bytes": card, "lane": LANE,
        "issue": {"schema": jco.ISSUE_SCHEMA, "lane": LANE, "card_sha256": sha(card).hexdigest(),
                  "view_sha256": sha(view).hexdigest(), "nonce": NONCE, "issued_utc": ISSUED},
        "receipts": [{"schema": jco.RECEIPT_SCHEMA, "lane": LANE, "card_sha256": sha(card).hexdigest(),
                      "nonce": NONCE, "read_utc": READ, "reader": "join-card-reader"}],
        "events": [pair(vote())],
        "coverage": [{"from_utc": "2026-10-14T10:00:00Z", "to_utc": "2026-10-14T12:30:00Z", "complete": True,
                      **SOURCE}],
        "head_truth": [{"pr": PR, "head": HEAD_A, "observed_utc": t, **SOURCE}
                       for t in ("2026-10-14T10:15:00Z", "2026-10-14T10:25:00Z")],
        "eligibility": [{"pr": PR, "head": HEAD_A, "slot": "build_tools", "lane": LANE, "eligible": True,
                         "observed_utc": VOTE_AT, **SOURCE}],
    }
    for key, value in change.items():
        inputs[key] = value(inputs) if callable(value) else value
    return inputs


def receipt_with(**fields):
    return lambda inputs: [dict(inputs["receipts"][0], **fields)]


def run(inputs):
    return jco.outcomes(**inputs)


def row(inputs):
    result = run(inputs)
    assert len(result["rows"]) == 1
    return result["rows"][0]


# Each control: inputs, and the outcome and reason that are true for those inputs.
CONTROLS = {
    "true_positive_vote": (lambda: base(), ("TAKEN_CORRECT", None)),
    "absent_receipt": (lambda: base(receipts=[]), ("UNKNOWN", "receipt_missing")),
    "absent_coverage": (lambda: base(events=[], coverage=[]), ("UNKNOWN", "coverage_incomplete")),
    "wrong_nonce": (lambda: base(receipts=receipt_with(nonce="nonce-0002")), ("UNKNOWN", "receipt_nonce_mismatch")),
    "wrong_digest": (lambda: base(receipts=receipt_with(card_sha256="1" * 64)), ("UNKNOWN", "receipt_digest_mismatch")),
    "wrong_responder": (lambda: base(receipts=receipt_with(lane="claude-rco-1")),
                        ("UNKNOWN", "receipt_responder_mismatch")),
    "wrong_head": (lambda: base(events=[pair(vote(head=HEAD_A_TWIN))]), ("TAKEN_WRONG", "wrong_binding")),
    "timestamp_inversion": (lambda: base(receipts=receipt_with(read_utc="2026-10-14T09:55:00Z")),
                            ("UNKNOWN", "receipt_before_issue")),
    # The head moved to B at 10:10, before the 10:20 vote at A: both observations around the vote name B.
    "head_changed_inside_window": (lambda: base(head_truth=[
        {"pr": PR, "head": HEAD_A, "observed_utc": "2026-10-14T10:04:00Z", **SOURCE},
        {"pr": PR, "head": HEAD_B, "observed_utc": "2026-10-14T10:10:00Z", **SOURCE},
        {"pr": PR, "head": HEAD_B, "observed_utc": "2026-10-14T10:25:00Z", **SOURCE}]),
        ("TAKEN_STALE", "head_moved_before_vote")),
    "duplicate_events": (lambda: base(events=[pair(vote()), pair(vote())]), ("TAKEN_CORRECT", None)),
    "head_truth_missing": (lambda: base(head_truth=[]), ("UNKNOWN", "head_truth_unknown")),
    "eligibility_missing": (lambda: base(eligibility=[]), ("UNKNOWN", "eligibility_unknown")),
    "not_eligible": (lambda: base(eligibility=lambda i: [dict(i["eligibility"][0], eligible=False)]),
                     ("TAKEN_WRONG", "not_eligible")),
    "pre_read_vote": (lambda: base(events=[pair(vote(ts="2026-10-14T10:03:00Z"))]),
                      ("PRE_READ", "valid_vote_before_read")),
    "other_action": (lambda: base(events=[pair(vote(kind="message", status="info"))]), ("OTHER", None)),
    "no_action": (lambda: base(events=[]), ("NONE", None)),
    "ref_mismatch_dropped": (lambda: base(events=[{"ref": "f" * 64, "body": vote()}]), ("NONE", None)),
    "line_record_mismatch": (lambda: base(view=make_view(record_head=HEAD_B)), ("UNKNOWN", "line_record_mismatch")),
    # Contract v1.1 controls (Grok xhigh consultation and RCO2's own finding (a)).
    "wrong_head_all_keys": (lambda: base(events=[pair(vote(head=HEAD_A_TWIN, all_keys=True))]),
                            ("TAKEN_WRONG", "wrong_binding")),
    # Two valid receipts, read at 10:05 and 10:30; the vote at 10:20 follows the EARLIEST read.
    "two_receipts_earliest_read": (lambda: base(receipts=lambda i: [
        i["receipts"][0], dict(i["receipts"][0], read_utc="2026-10-14T10:30:00Z", reader="second-reader")]),
        ("TAKEN_CORRECT", None)),
    # The view is as of 10:00:00 and the issue at 10:00:30; a vote at 10:00:10 means the card was already stale.
    "vote_before_issue": (lambda: base(events=[pair(vote(ts="2026-10-14T10:00:10Z"))]),
                          ("PRE_READ", "valid_vote_before_read")),
    # The PR moved from A to B at 10:10; the lane voted at B, the live head, at 10:20: the right act, not harm.
    "vote_at_live_head": (lambda: base(events=[pair(vote(head=HEAD_B))],
                                       head_truth=truth(("2026-10-14T10:10:00Z", HEAD_B),
                                                        ("2026-10-14T10:25:00Z", HEAD_B))),
                          ("OTHER", "vote_at_live_head")),
    # A nearer observation (10:19, B) sits between the vote and an older same-head one (10:15, A).
    "nearer_conflicting_observation": (lambda: base(head_truth=truth(
        ("2026-10-14T10:15:00Z", HEAD_A), ("2026-10-14T10:19:00Z", HEAD_B), ("2026-10-14T10:25:00Z", HEAD_A))),
        ("UNKNOWN", "head_truth_unknown")),
    "truth_gap_too_wide": (lambda: base(head_truth=truth(("2026-10-14T10:04:00Z", HEAD_A),
                                                         ("2026-10-14T10:30:00Z", HEAD_A))),
                           ("UNKNOWN", "head_truth_unknown")),
    "single_observation_at_vote": (lambda: base(head_truth=truth((VOTE_AT, HEAD_A))), ("TAKEN_CORRECT", None)),
    "payload_pr_mismatch": (lambda: base(events=[pair(vote(pr=PR2))]), ("TAKEN_WRONG", "pr_mismatch")),
}


@pytest.mark.parametrize("name", sorted(CONTROLS))
def test_control_truth(name):
    build, (outcome, reason) = CONTROLS[name]
    got = row(build())
    assert (got["outcome"], got["reason"]) == (outcome, reason)


def test_true_positive_details():
    got = row(base())
    assert got["head"] == HEAD_A and len(got["head"]) == 40 and got["task"] == BRANCH and got["slot"] == "build_tools"
    assert got["read_to_action_ms"] == 15 * 60 * 1000
    assert got["event_ref"] == event_id(vote())


def test_duplicates_are_counted_once_and_reported():
    result = run(CONTROLS["duplicate_events"][0]())
    assert result["inputs"]["events"]["duplicates"] == 1 and len(result["rows"]) == 1
    assert result["counts"]["follow_through"] == {"numerator": 1, "denominator": 1}


def test_ref_mismatch_is_counted():
    result = run(CONTROLS["ref_mismatch_dropped"][0]())
    assert result["inputs"]["events"]["ref_mismatch"] == 1


def test_timing_unknown_when_coverage_does_not_reach_the_vote():
    inputs = base(coverage=[{"from_utc": "2026-10-14T10:00:00Z", "to_utc": "2026-10-14T10:10:00Z", "complete": True,
                             **SOURCE}])
    got = row(inputs)
    assert got["outcome"] == "TAKEN_CORRECT" and got["read_to_action_ms"] is None


def test_incomplete_coverage_flag_is_not_coverage():
    inputs = base(events=[], coverage=lambda i: [dict(i["coverage"][0], complete="true")])
    assert row(inputs)["reason"] == "coverage_incomplete"


def test_card_not_rendered_from_view_is_refused_but_counted():
    result = run(base(card=b"lane view 1a for codex-tools-1: something else\n"))
    assert result["card"].startswith("refused")
    assert [(r["outcome"], r["reason"]) for r in result["rows"]] == [("UNKNOWN", "card_not_rendered_from_view")]
    assert result["counts"]["unknown_reasons"] == {"card_not_rendered_from_view": 1}


def test_two_receipts_time_from_the_earliest_read():
    got = row(CONTROLS["two_receipts_earliest_read"][0]())
    assert got["read_utc"] == "2026-10-14T10:05:00.000000Z" and got["read_to_action_ms"] == 15 * 60 * 1000


def test_historical_replay_without_cards_is_unknown_and_explained():
    result = run(base(issue=None, receipts=[]))
    assert [r["outcome"] for r in result["rows"]] == ["UNKNOWN"]
    assert result["rows"][0]["reason"] == "receipt_missing" and result["gap"] == jco.HISTORICAL_GAP
    assert result["counts"]["follow_through"] == {"numerator": 0, "denominator": 0}


def test_counts_name_their_denominators():
    view = make_view(second=True)
    inputs = base(view=view)  # line 1 taken correctly at 10:20; for line 2 (PR1906) that vote is another action
    result = run(inputs)
    assert [r["outcome"] for r in result["rows"]] == ["TAKEN_CORRECT", "OTHER"]
    assert result["counts"]["follow_through"] == {"numerator": 1, "denominator": 2}
    assert result["counts"]["correct_of_taken"] == {"numerator": 1, "denominator": 1}
    assert result["counts"]["harm_of_taken"] == {"numerator": 0, "denominator": 1}
    stale = run(base(view=view, head_truth=CONTROLS["head_changed_inside_window"][0]()["head_truth"]))
    assert stale["counts"]["harm_of_taken"] == {"numerator": 1, "denominator": 1}
    assert stale["counts"]["correct_of_taken"] == {"numerator": 0, "denominator": 1}
    pre = run(base(view=view, events=[pair(vote(ts="2026-10-14T10:03:00Z"))]))
    assert pre["counts"]["outcomes"]["PRE_READ"] == 1 and pre["counts"]["follow_through"]["denominator"] == 1


def test_byte_identical_rebuild_in_any_input_order():
    inputs = base(events=[pair(vote()), pair(vote(kind="message", status="info", ts="2026-10-14T10:30:00Z"))],
                  head_truth=CONTROLS["head_changed_inside_window"][0]()["head_truth"])
    first = jco.result_bytes(run(inputs))
    assert first == jco.result_bytes(run(inputs))
    rng = random.Random(20261014)
    for _ in range(5):
        shuffled = dict(inputs)
        for key in ("receipts", "events", "coverage", "head_truth", "eligibility"):
            items = list(inputs[key])
            rng.shuffle(items)
            shuffled[key] = items
        assert jco.result_bytes(run(shuffled)) == first


def test_output_is_ascii_and_names_the_frozen_contract():
    data = jco.result_bytes(run(base()))
    data.decode("ascii")
    assert json.loads(data)["contract_sha256"] == jco.CONTRACT_SHA256


# --- Named mutants: each must flip the control written against it ---------------------------------------------------

def _first_snapshot(truth, pr, head, moment):
    rows = sorted((jco._time(item["observed_utc"]), item["head"]) for item in truth if item.get("pr") == pr)
    return "CURRENT" if rows and rows[0][1] == head else jco.UNKNOWN


def _dedupe_without_counting(pairs):
    kept, counts = {}, {"pairs": len(pairs), "ref_mismatch": 0, "duplicates": 0, "unusable": 0}
    for index, (ref, body) in enumerate(pairs):
        if isinstance(body, dict) and event_id(body) == ref:
            kept[f"{ref}#{index}"] = {"ref": ref, "ts": jco._time(body["ts_utc"]), "body": body}
    return kept, counts


def _any_pair_bracket(rows, pr, head, moment):
    """Any same-head pair around the moment within the gap counts, even with a nearer observation of another head."""
    seen = [(jco._time(item["observed_utc"]), item["head"]) for item in rows if item.get("pr") == pr]
    before = [at for at, value in seen if at <= moment and value == head]
    after = [at for at, value in seen if at >= moment and value == head]
    return "CURRENT" if any(b - a <= jco.MAX_TRUTH_GAP for a in before for b in after) else jco.UNKNOWN


def _latest_read(issue, receipts, lane, card_sha256, view_sha256):
    valid = [jco._time(item["read_utc"]) for item in receipts if jco.receipt_reason(item, issue) is None]
    return (max(valid), None, []) if valid else (None, "receipt_missing", [])


def _prefix_only(bound, head):
    return bool(bound) and bound[:12] == head[:12]


MUTANTS = {
    "M_receipt_optional": ("absent_receipt", "read_time", lambda *a: (jco._time(READ), None, [])),
    "M_coverage_assumed": ("absent_coverage", "covers", lambda *a: True),
    "M_nonce_unchecked": ("wrong_nonce", "nonce_ok", lambda *a: True),
    "M_digest_unchecked": ("wrong_digest", "digest_ok", lambda *a: True),
    "M_responder_unchecked": ("wrong_responder", "responder_ok", lambda *a: True),
    "M_head_prefix_only": ("wrong_head", "same_head", _prefix_only),
    "M_head_prefix_only_all_keys": ("wrong_head_all_keys", "same_head", _prefix_only),
    "M_no_time_order": ("timestamp_inversion", "order_ok", lambda *a: True),
    "M_currency_at_read_snapshot": ("head_changed_inside_window", "currency", _first_snapshot),
    "M_head_truth_assumed": ("head_truth_missing", "currency", lambda *a: "CURRENT"),
    "M_eligibility_assumed": ("eligibility_missing", "eligible", lambda *a: True),
    "M_eligibility_ignored": ("not_eligible", "eligible", lambda *a: True),
    "M_window_closed": ("true_positive_vote", "WINDOW", timedelta(0)),
    "M_pre_read_disabled": ("pre_read_vote", "before_read", lambda *a: False),
    "M_pre_read_from_issue_only": ("vote_before_issue", "before_read",
                                   lambda moment, read, issued: issued is not None and issued <= moment < read),
    "M_latest_receipt": ("two_receipts_earliest_read", "read_time", _latest_read),
    "M_wrong_binding_without_truth": ("vote_at_live_head", "judge_wrong", lambda *a: ("TAKEN_WRONG", "wrong_binding")),
    "M_any_pair_bracket": ("nearer_conflicting_observation", "currency", _any_pair_bracket),
    "M_wide_gap": ("truth_gap_too_wide", "MAX_TRUTH_GAP", timedelta(hours=1)),
    "M_pr_unchecked": ("payload_pr_mismatch", "same_pr", lambda *a: True),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_mutant_flips_its_control(name, monkeypatch):
    control, attribute, replacement = MUTANTS[name]
    build, expected = CONTROLS[control]
    got = row(build())
    assert (got["outcome"], got["reason"]) == expected  # the control holds on the real code
    monkeypatch.setattr(jco, attribute, replacement)
    got = row(build())
    assert (got["outcome"], got["reason"]) != expected, f"{name} survived control {control}"


def test_every_control_with_a_mutant_is_named():
    covered = {control for control, _, _ in MUTANTS.values()}
    positive_only = {"duplicate_events", "other_action", "no_action", "ref_mismatch_dropped", "line_record_mismatch",
                     "single_observation_at_vote"}  # truth controls; their counts or twins are asserted elsewhere
    assert set(CONTROLS) - positive_only <= covered


def test_mutant_dedupe_without_counting_is_caught(monkeypatch):
    monkeypatch.setattr(jco, "dedupe", _dedupe_without_counting)
    result = run(CONTROLS["duplicate_events"][0]())
    assert result["inputs"]["events"]["duplicates"] != 1  # the duplicate control's count assertion kills it


def test_mutant_timing_without_coverage_is_caught(monkeypatch):
    inputs = base(coverage=[{"from_utc": "2026-10-14T10:00:00Z", "to_utc": "2026-10-14T10:10:00Z", "complete": True,
                             **SOURCE}])
    assert row(inputs)["read_to_action_ms"] is None
    monkeypatch.setattr(jco, "timed", lambda coverage, read, moment: int((moment - read).total_seconds() * 1000))
    assert row(inputs)["read_to_action_ms"] is not None
