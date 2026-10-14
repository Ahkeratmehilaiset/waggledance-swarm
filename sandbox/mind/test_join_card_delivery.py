"""Tests of the JOIN-card delivery observer (frozen semantics v1, file sha256 d48223d7 posted 18:59:50Z 2026-10-14).

Cases go through the real core and the real step-1 lane view (fixtures of ``step1/test_lane_view.py``), so every card
is what the lane view really writes. The two named mutants are applied with monkeypatch and must be killed.

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/test_join_card_delivery.py -q
"""

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _path in (str(HERE), str(HERE / "step1")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
import join_card_delivery as jcd  # noqa: E402
import lane_view as lv  # noqa: E402
import test_lane_view as tlv  # noqa: E402  (step-1 fixtures: the real core writes the snapshot rows)
from tools.bridge_compact_view import event_id  # noqa: E402

COMPLETE = "events:" + "a" * 64  # any log identity that is not a capture window: the lane view reports complete
SINCE = "2026-10-13T17:40:44Z"  # the later credited vote of the PR1905 fixture, cut to whole seconds
TOOLS = "codex-tools-1"
FROZEN_VIEWS = Path(r"F:\waggledance_memory\sandbox\fable-5\hexmind\step1\views")


def shift(text, **delta):
    moment = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) + timedelta(**delta)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def case(as_of, *, events=None, github=None, complete=True):
    events = tlv.base_events() if events is None else events
    github = tlv.github_of(observed=shift(as_of, minutes=-5)) if github is None else github
    snapshot = tlv.snapshot_of(events, as_of, log_identity=COMPLETE if complete else "capture-window:" + "0" * 64)
    return lv.lane_view(snapshot, github, {event_id(row): row for row in events}, as_of_utc=as_of)


def receipts(rows=(), **through):
    return {"schema": jcd.RECEIPTS_SCHEMA, "rows": list(rows),
            "sources": {kind: {"complete_through_utc": when} for kind, when in through.items()}}


def rrow(kind, at, lane=TOOLS, head=tlv.NEW, pr=tlv.NUMBER):
    ref = hashlib.sha256(f"{kind} {at} {lane} {head} {pr}".encode()).hexdigest()
    return {"kind": kind, "lane": lane, "pr": pr, "head": head, "ref": ref, "at_utc": at}


def many(count, as_of):
    """``count`` CI-green PRs whose only empty slot is the Tools build vote, one minute apart."""
    events, githubs = [], []
    for index in range(count):
        number, branch = 2000 + index, f"codex-lead-1/wait-{index}-20261013"
        head = hashlib.sha1(f"head {index}".encode()).hexdigest()
        events += [tlv.vote(f"2026-10-13T17:4{index}:00Z", "claude-rco-1", head=head, task=branch, number=number),
                   tlv.vote(f"2026-10-13T17:4{index}:01Z", "codex-lead-1", head=head, task=branch, number=number)]
        githubs.append(tlv.github_of(head=head, number=number, branch=branch, observed=shift(as_of, minutes=-5)))
    return case(as_of, events=events, github=tlv.merge_github(*githubs))


# --- the gap, reproduced before the patch ----------------------------------------------------------------------------

def test_gap_pr1905_lane_view_alone_has_no_wait_threshold_receipts_or_operator_line():
    as_of = shift(SINCE, minutes=586)
    view = case(as_of)
    record = view["prs"][str(tlv.NUMBER)]
    assert view["coverage"] == "complete" and record["state"] == "action" and record["empty_slots"] == ["build_tools"]
    # The gap: the only signal is one line inside the Tools card. It has no wait length or threshold and no
    # receipt state, and no other card (and nothing for the operator) carries it.
    [line] = view["lanes"][TOOLS]["actions"]
    for absent in ("586", "min", "delivered", "answered", "last_open_slot"):
        assert absent not in line
    assert [lane for lane, entry in view["lanes"].items() if entry["actions"]] == [TOOLS]
    assert "no action line" in lv.render_text(view, "operator")
    # The patch: one operator line with fixed fields, refs, the full head and the observed time and coverage.
    out = jcd.deliver(view, receipts(), now_utc=as_of)
    [alert] = out["operator_lines"]
    for cite in (f"pr={tlv.NUMBER} ", f"head={tlv.NEW} ", "slot=build_tools ", f"lane={TOOLS} ", "last_open_slot=yes ",
                 "wait_min=586 ", f"since={SINCE} ", f"observed={as_of} ", "coverage=complete ", "ci_at_head=6/6 ",
                 "veto=none_observed ", "requested=UNKNOWN(no_source) ", "delivered=UNKNOWN(no_source) ",
                 "read=UNKNOWN(no_source) ", "answered=UNKNOWN(no_source) ", f"id={out['notified'][0][:12]}"):
        assert cite in alert
    for vote in record["credited"]:
        assert f"{vote['slot']}:{vote['ref'][:16]}" in alert
    assert alert in out["lanes"][TOOLS]
    assert all(alert not in text for lane, text in out["lanes"].items() if lane != TOOLS)


# --- S2/S3 threshold twins ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("seconds, state", [(1799, "below_threshold"), (1800, "below_threshold"), (1801, "alert")])
def test_threshold_twins_around_thirty_minutes(seconds, state):
    as_of = shift(SINCE, seconds=seconds)
    out = jcd.deliver(case(as_of), receipts(), now_utc=as_of)
    [row] = out["ledger"]
    assert row["state"] == state and row["wait_seconds"] == seconds
    assert len(out["operator_lines"]) == (state == "alert")
    assert ("last_open_slot=yes" in out["lanes"][TOOLS]) == (state == "alert")
    assert out["notified"] == ([row["identity"]] if state == "alert" else [])


# --- S4 card trust -----------------------------------------------------------------------------------------------------

def test_missing_coverage_is_unknown_and_never_a_wait():
    as_of = shift(SINCE, minutes=586)
    out = jcd.deliver(case(as_of, complete=False), receipts(), now_utc=as_of)
    [row] = out["ledger"]
    assert row["state"] == "unknown" and row["reason"] == "card_coverage_not_complete" and "wait_seconds" not in row
    assert out["operator_lines"] == [] and out["notified"] == [] and out["counts"]["unknown"] == 1
    card = out["lanes"][TOOLS]
    assert f"UNKNOWN, not asserted: 1 (pr{tlv.NUMBER} head={tlv.NEW[:12]} slot=build_tools " \
           "reason=card_coverage_not_complete)" in card
    assert "last_open_slot" not in card and "wait_min" not in card
    preview = jcd.operator_preview(out)
    assert f"UNKNOWN, not asserted: 1 (pr{tlv.NUMBER} head={tlv.NEW[:12]} slot=build_tools lane={TOOLS} " \
           "reason=card_coverage_not_complete)" in preview
    assert "last_open_slot" not in preview and "no new wait over 30 min" in preview


@pytest.mark.parametrize("delta, trust", [(timedelta(minutes=15), "current"),
                                          (timedelta(minutes=15, seconds=1), "card_stale"),
                                          (timedelta(seconds=-1), "card_from_future")])
def test_card_age_twins(delta, trust):
    as_of = shift(SINCE, minutes=60)
    now = (datetime.strptime(as_of, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) + delta)
    out = jcd.deliver(case(as_of), receipts(), now_utc=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    [row] = out["ledger"]
    assert out["card_trust"] == trust
    assert row["state"] == ("alert" if trust == "current" else "unknown")
    assert len(out["operator_lines"]) == (trust == "current")


# --- S1 conditions -----------------------------------------------------------------------------------------------------

def test_filled_slot_and_two_empty_slots_are_no_condition():
    as_of = shift(SINCE, minutes=60)
    filled = tlv.base_events() + [tlv.vote("2026-10-13T17:45:00Z", TOOLS)]
    view = case(as_of, events=filled)
    assert view["prs"][str(tlv.NUMBER)]["state"] == "votes_complete"
    out = jcd.deliver(view, receipts(), now_utc=as_of)
    assert out["ledger"] == [] and out["operator_lines"] == []
    two_empty = tlv.base_events()[:2]  # only RCO1 voted: build_lead and build_tools are empty
    view = case(as_of, events=two_empty)
    assert view["prs"][str(tlv.NUMBER)]["empty_slots"] == ["build_lead", "build_tools"]
    out = jcd.deliver(view, receipts(), now_utc=as_of)
    assert out["ledger"] == [] and out["operator_lines"] == []


def test_wrong_lane_and_other_pr_receipts_never_count_and_only_the_owner_card_shows_it():
    as_of = shift(SINCE, minutes=60)
    rows = [rrow("read", shift(SINCE, minutes=3), lane="claude-rco-2"),
            rrow("answered", shift(SINCE, minutes=4), pr=tlv.SECOND)]
    out = jcd.deliver(case(as_of), receipts(rows, read=as_of, answered=as_of), now_utc=as_of)
    [row] = out["ledger"]
    assert row["receipts"]["read"] == {"state": "NONE", "through_utc": as_of}
    assert row["receipts"]["answered"] == {"state": "NONE", "through_utc": as_of}
    assert [lane for lane, text in out["lanes"].items() if "last_open_slot=yes" in text] == [TOOLS]


# --- S5 receipts -------------------------------------------------------------------------------------------------------

def test_stale_head_receipts_and_identity_never_apply_to_the_new_head():
    as_of = shift(SINCE, minutes=60)
    old_rows = [rrow(kind, shift(SINCE, minutes=1), head=tlv.OLD) for kind in jcd.KINDS]
    old_identity = jcd.identity(tlv.NUMBER, tlv.OLD, "build_tools", TOOLS, SINCE)
    out = jcd.deliver(case(as_of), receipts(old_rows, **{kind: as_of for kind in jcd.KINDS}), now_utc=as_of,
                      notified=[old_identity])
    [row] = out["ledger"]
    assert all(row["receipts"][kind]["state"] == "NONE" for kind in jcd.KINDS)
    assert row["shown"] and not row["previously_notified"] and len(out["operator_lines"]) == 1
    assert set(out["notified"]) == {old_identity, row["identity"]}


def test_receipt_states_observed_none_unknown():
    as_of = shift(SINCE, minutes=60)
    rows = [rrow("requested", shift(SINCE, minutes=2)), rrow("requested", shift(SINCE, minutes=1)),
            rrow("read", shift(as_of, seconds=1))]  # a read after the observed time is not used
    out = jcd.deliver(case(as_of), receipts(rows, requested=as_of, read=as_of, answered=shift(as_of, seconds=-1)),
                      now_utc=as_of)
    [row] = out["ledger"]
    states = row["receipts"]
    assert states["requested"] == {"state": "OBSERVED", "ref": rows[1]["ref"], "at_utc": shift(SINCE, minutes=1)}
    assert states["delivered"] == {"state": "UNKNOWN", "reason": "no_source"}
    assert states["read"] == {"state": "NONE", "through_utc": as_of}
    assert states["answered"] == {"state": "UNKNOWN", "reason": "source_coverage_short"}
    [line] = out["operator_lines"]
    assert f"requested={rows[1]['ref'][:16]}@{shift(SINCE, minutes=1)} " in line
    assert f"read=none_through_{as_of} " in line and "answered=UNKNOWN(source_coverage_short) " in line


def _broken_documents(doc, row, as_of):
    marker = chr(80) + "RIVATE" + "_MARKER"  # a guarded word, built at run time
    return [dict(doc, extra=1), dict(doc, schema="wd.mind-delivery-receipts.v0"),
            dict(doc, rows=[dict(row, note="x")]), dict(doc, rows=[dict(row, head=row["head"][:12])]),
            dict(doc, rows=[dict(row, lane="operator")]), dict(doc, rows=[dict(row, kind="seen")]),
            dict(doc, rows=[dict(row, pr=str(row["pr"]))]), dict(doc, rows=[dict(row, pr=True)]),
            dict(doc, rows=[dict(row, at_utc="2026-10-13 17:41:44")]), dict(doc, rows=[dict(row, ref=marker)]),
            dict(doc, sources={"seen": {"complete_through_utc": as_of}}),
            dict(doc, sources={"read": {"complete_through_utc": as_of, "note": "x"}}),
            dict(doc, sources={"read": {"complete_through_utc": None}}), dict(doc, rows={"0": row}), None, []]


def test_receipts_document_is_strict_and_an_unreadable_one_never_removes_the_alert():
    as_of = shift(SINCE, minutes=60)
    view = case(as_of)
    row = rrow("requested", shift(SINCE, minutes=1))
    doc = receipts([row], requested=as_of)
    assert jcd.deliver(view, doc, now_utc=as_of)["receipts_readable"] is True
    for broken in _broken_documents(doc, row, as_of):
        out = jcd.deliver(view, broken, now_utc=as_of)
        [ledger_row] = out["ledger"]
        assert out["receipts_readable"] is False and ledger_row["state"] == "alert"
        assert all(state == {"state": "UNKNOWN", "reason": "receipts_unreadable"}
                   for state in ledger_row["receipts"].values())
        assert len(out["operator_lines"]) == 1


# --- S6 dedupe ---------------------------------------------------------------------------------------------------------

def test_dedupe_hides_only_the_unchanged_condition():
    as_of = shift(SINCE, minutes=60)
    first = jcd.deliver(case(as_of), receipts(), now_utc=as_of)
    [ident] = first["notified"]
    later = shift(as_of, minutes=10)
    second = jcd.deliver(case(later), receipts(), now_utc=later, notified=first["notified"])
    assert second["operator_lines"] == [] and second["notified"] == [ident]
    assert second["counts"]["unchanged"] == 1 and second["ledger"][0]["previously_notified"]
    assert f"UNCHANGED waits already shown: 1 (pr{tlv.NUMBER}:{ident[:12]})" in second["lanes"][TOOLS]
    # CI completed again after the votes: since moves, so this is a new identity and it is shown.
    moved = case(later, github=tlv.github_of(observed=shift(later, minutes=-5), completed="2026-10-13T17:50:00Z"))
    third = jcd.deliver(moved, receipts(), now_utc=later, notified=first["notified"])
    [line] = third["operator_lines"]
    assert "since=2026-10-13T17:50:00Z " in line and third["ledger"][0]["identity"] != ident
    assert set(third["notified"]) == {ident, third["ledger"][0]["identity"]}


# --- S7 size bound and the named notification-loss mutant --------------------------------------------------------------

def two_turns(as_of="2026-10-13T19:00:00Z", count=6):
    view = many(count, as_of)
    first = jcd.deliver(view, receipts(), now_utc=as_of)
    second = jcd.deliver(view, receipts(), now_utc=as_of, notified=first["notified"])
    return first, second


def check_no_notification_loss(first, second):
    alerts = {row["identity"] for row in first["ledger"] if row["state"] == "alert"}
    shown_first = {row["identity"] for row in first["ledger"] if row["shown"]}
    shown_second = {row["identity"] for row in second["ledger"] if row["shown"]}
    assert first["counts"]["carried"] > 0  # the 2 KB bound really cut the first turn
    assert shown_first | shown_second == alerts  # every alert is shown in one of the two turns
    assert not shown_first & shown_second  # and none is shown twice


def test_size_bound_carries_overflow_to_the_next_turn_control():
    first, second = two_turns()
    assert first["counts"]["alert"] == 6
    check_no_notification_loss(first, second)
    assert "CARRIED to next turn: " in first["lanes"][TOOLS] and second["counts"]["carried"] == 0
    for out in (first, second):
        assert all(len(text.encode("utf-8")) <= jcd.CARD_BYTES for text in out["lanes"].values())


def test_named_mutant_notification_loss_marks_carried_alerts_and_is_killed(monkeypatch):
    monkeypatch.setattr(jcd, "notified_after", lambda prior, shown, alerts: sorted(prior | set(alerts)))
    first, second = two_turns()
    with pytest.raises(AssertionError):
        check_no_notification_loss(first, second)


def test_named_mutant_identity_without_head_is_killed(monkeypatch):
    real = jcd.identity
    monkeypatch.setattr(jcd, "identity", lambda pr, head, slot, lane, since: real(pr, "0" * 40, slot, lane, since))
    with pytest.raises(AssertionError):
        test_stale_head_receipts_and_identity_never_apply_to_the_new_head()


# --- S8 output ---------------------------------------------------------------------------------------------------------

def test_output_is_deterministic_guarded_and_observe_only():
    as_of = shift(SINCE, minutes=586)
    view = case(as_of)
    first = json.dumps(jcd.deliver(view, receipts(), now_utc=as_of), sort_keys=True)
    second = json.dumps(jcd.deliver(view, receipts(), now_utc=as_of), sort_keys=True)
    assert first == second
    out = json.loads(first)
    assert out["authority"] == "none" and out["scope"] == "observe_only_not_gate"
    text = jcd.operator_preview(out)
    for forbidden in tlv.FORBIDDEN + ("approve", "merge"):
        assert forbidden not in text.lower() and all(forbidden not in card.lower() for card in out["lanes"].values())


def test_refusals_are_loud_and_fixed():
    as_of = shift(SINCE, minutes=60)
    view = case(as_of)
    for call in (lambda: jcd.deliver(view, receipts(), now_utc="now"),
                 lambda: jcd.deliver(dict(view, schema="wd.mind-lane-view.v0"), receipts(), now_utc=as_of),
                 lambda: jcd.deliver(dict(view, as_of_utc=None), receipts(), now_utc=as_of),
                 lambda: jcd.deliver(view, receipts(), now_utc=as_of, notified=["abc"])):
        with pytest.raises(jcd.DeliveryError):
            call()


def test_cli_refuses_an_output_path_outside_the_sandbox_root_and_prints_the_preview(tmp_path, capsys):
    as_of = shift(SINCE, minutes=586)
    view_path, receipts_path = tmp_path / "view.json", tmp_path / "receipts.json"
    view_path.write_text(json.dumps(case(as_of)), encoding="utf-8")
    receipts_path.write_text(json.dumps(receipts()), encoding="utf-8")
    out_path = tmp_path / "out.json"
    assert jcd.main(["--view", str(view_path), "--receipts", str(receipts_path), "--now", as_of,
                     "--out", str(out_path)]) == 2
    assert not out_path.exists()
    assert jcd.main(["--view", str(view_path), "--receipts", str(receipts_path), "--now", as_of]) == 0
    printed = capsys.readouterr().out
    assert "last_open_slot=yes" in printed and "nothing was sent" in printed


@pytest.mark.skipif(not FROZEN_VIEWS.is_dir(), reason="frozen step-1 views are on the sandbox data disk")
def test_frozen_pr1905_night_views_never_assert_a_wait_from_a_capture_window():
    paths = sorted(FROZEN_VIEWS.glob("*.lane-view.json"))
    assert paths
    for path in paths:
        view = json.loads(path.read_text(encoding="utf-8"))
        out = jcd.deliver(view, receipts(), now_utc=view["as_of_utc"])
        if view["coverage"] != "complete":
            assert out["operator_lines"] == [] and out["counts"]["alert"] == 0
            assert all(row["reason"] == "card_coverage_not_complete" for row in out["ledger"])
