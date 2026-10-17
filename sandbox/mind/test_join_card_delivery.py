"""Tests of the JOIN-card delivery observer (frozen semantics v1, file sha256 d48223d7 posted 18:59:50Z 2026-10-14).

Cases go through the real core and the real step-1 lane view (fixtures of ``step1/test_lane_view.py``), so every card
is what the lane view really writes. The named mutants are applied with monkeypatch and must be killed. The v1.1
repairs after RCO1's probes of 2302ee59 (result 1185E841) follow; each was red on 2302ee59 first. The v1.2 repairs
after the 10-15 evening audit, the v1.3 repair after my own probe of the JOIN card (10-16), the v1.4 repair after RCO1's
design observation (10-16) and the v1.5 repair after RCO1's static review (10-16) are at the end; each was red on the
prior branch head first (v1.5 changed three older tests on purpose; see its section).

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/test_join_card_delivery.py -q
"""

import copy
import hashlib
import json
import re
import sys
import unicodedata
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
    assert "last_open_slot" not in preview and "no new wait over 30 min" not in preview  # v1.2: no absence
    assert "no wait is stated: card_trust=card_coverage_not_complete" in preview


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
    later = shift(as_of, seconds=1)  # v1.5: the read lies inside its own coverage, and the clock reached both
    rows = [rrow("requested", shift(SINCE, minutes=2)), rrow("requested", shift(SINCE, minutes=1)),
            rrow("read", later)]  # a read after the observed time is not used
    out = jcd.deliver(case(as_of), receipts(rows, requested=as_of, read=later, answered=shift(as_of, seconds=-1)),
                      now_utc=later)
    [row] = out["ledger"]
    states = row["receipts"]
    assert states["requested"] == {"state": "OBSERVED", "ref": rows[1]["ref"], "at_utc": shift(SINCE, minutes=1)}
    assert states["delivered"] == {"state": "UNKNOWN", "reason": "no_source"}
    assert states["read"] == {"state": "NONE", "through_utc": as_of}  # N1: through observed, not its source
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


def test_cli_refuses_an_output_path_outside_the_sandbox_root_and_prints_the_preview(tmp_path, capsys, monkeypatch):
    # The sandbox root is a temporary directory here: the module default names the data disk, which no test resolves.
    monkeypatch.setattr(jcd, "SANDBOX_ROOT", tmp_path / "sandbox")
    (tmp_path / "sandbox").mkdir()
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


def night_views():
    """Synthetic stand-ins for the frozen PR1905-night step-1 views the reference test read from the data disk: the
    real core and the real lane view over the synthetic PR1905 case at four as_of times of one night, each under
    capture-window and complete coverage. No data disk is read; nothing here claims parity with historical views."""
    times = [shift(SINCE, minutes=minutes) for minutes in (20, 60, 240, 586)]
    return [case(as_of, complete=complete) for as_of in times for complete in (False, True)]


def test_synthetic_night_views_never_assert_a_wait_from_a_capture_window():
    """The reference test_frozen_pr1905_night_views_never_assert_a_wait_from_a_capture_window, on synthetic views."""
    views = night_views()
    assert {view["coverage"] for view in views} == {"complete", lv.UNKNOWN}  # both kinds are exercised
    for view in views:
        out = jcd.deliver(view, receipts(), now_utc=view["as_of_utc"])
        if view["coverage"] != "complete":
            assert out["operator_lines"] == [] and out["counts"]["alert"] == 0
            assert all(row["reason"] == "card_coverage_not_complete" for row in out["ledger"])


# --- v1.1 repairs after RCO1's probes of 2302ee59 (plan 4FEAA88E, result 1185E841) ------------------------------------

TYPES = (None, 0, 1.5, True, "x", [], {}, [None], {"x": None})


def substitutions(value):
    """Copies of a JSON value with ONE position (the root, or any mapping value or list item at any depth) replaced
    by each value of TYPES."""
    for substitute in TYPES:
        yield copy.deepcopy(substitute)
    if isinstance(value, dict):
        for key in value:
            for inner in substitutions(value[key]):
                yield {**value, key: inner}
    elif isinstance(value, list):
        for index in range(len(value)):
            for inner in substitutions(value[index]):
                yield value[:index] + [inner] + value[index + 1:]


def test_f1_a_source_entry_of_another_type_is_unreadable_and_never_drops_the_alert():
    as_of = shift(SINCE, minutes=60)
    view = case(as_of)
    doc = receipts([rrow("requested", shift(SINCE, minutes=1))], requested=as_of, read=as_of)
    for value in (None, 5, 1.5, True, "complete_through_utc", ["complete_through_utc"], []):
        out = jcd.deliver(view, dict(doc, sources=dict(doc["sources"], read=value)), now_utc=as_of)
        [row] = out["ledger"]
        assert out["receipts_readable"] is False and row["state"] == "alert" and len(out["operator_lines"]) == 1
        assert all(state == {"state": "UNKNOWN", "reason": "receipts_unreadable"} for state in row["receipts"].values())


def test_f1_reading_receipts_never_raises_for_any_type_at_any_position():
    as_of = shift(SINCE, minutes=60)
    view = case(as_of)
    doc = receipts([rrow("requested", shift(SINCE, minutes=1))], requested=as_of)
    count = 0
    for broken in substitutions(doc):
        jcd.read_receipts(broken, jcd._time(as_of))
        out = jcd.deliver(view, broken, now_utc=as_of)
        assert out["counts"]["alert"] == 1 and len(out["operator_lines"]) == 1
        count += 1
    # positions: the root, 3 top-level values, the source entry and its time, the row and its 6 values
    assert count == len(TYPES) * 13


def cli(tmp_path, capsys, view_text, receipts_text, now, notified_text=None):
    view_path, receipts_path = tmp_path / "view.json", tmp_path / "receipts.json"
    view_path.write_text(view_text, encoding="utf-8")
    receipts_path.write_text(receipts_text, encoding="utf-8")
    argv = ["--view", str(view_path), "--receipts", str(receipts_path), "--now", now]
    if notified_text is not None:
        (tmp_path / "notified.json").write_text(notified_text, encoding="utf-8")
        argv += ["--notified", str(tmp_path / "notified.json")]
    code = jcd.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_f2_cli_duplicate_receipts_key_is_unreadable_and_never_an_affirmative_none(tmp_path, capsys):
    as_of = shift(SINCE, minutes=60)
    view_text = json.dumps(case(as_of))
    read = rrow("read", shift(SINCE, minutes=2))
    clean = json.dumps(receipts([read], read=as_of))
    code, out, _ = cli(tmp_path, capsys, view_text, clean, as_of)
    assert code == 0 and f"read={read['ref'][:16]}@{shift(SINCE, minutes=2)} " in out  # control
    # RCO1's fixture: the observed read is in the first "rows" and an empty "rows" comes last. A plain reader keeps
    # the last key and printed read=none_through_... for a file that holds an observed read.
    duplicated = clean[:-1] + ', "rows": []}'
    assert json.loads(duplicated)["rows"] == []
    for text in (duplicated, clean[:-1] + ', "schema": "' + jcd.RECEIPTS_SCHEMA + '"}', "{", "\ufeff" + clean):
        code, out, _ = cli(tmp_path, capsys, view_text, text, as_of)
        assert code == 0 and "last_open_slot=yes" in out  # an unreadable receipts file never removes the alert
        assert "read=UNKNOWN(receipts_unreadable) " in out and "none_through" not in out


def test_f2_cli_view_or_notified_file_that_is_not_strict_json_refuses_the_turn(tmp_path, capsys):
    as_of = shift(SINCE, minutes=60)
    partial = case(as_of, complete=False)
    assert partial["coverage"] != "complete"
    upgraded = json.dumps(partial)[:-1] + ', "coverage": "complete"}'  # a later duplicate key upgrades the coverage
    assert json.loads(upgraded)["coverage"] == "complete"
    whole_text = json.dumps(case(as_of))
    nested = whole_text.replace('"state": "action"', '"state": "abstain", "state": "action"', 1)
    assert nested != whole_text
    empty = json.dumps(receipts())
    for view_text in (upgraded, nested, whole_text[:-1] + ', "x": NaN}'):
        code, out, err = cli(tmp_path, capsys, view_text, empty, as_of)
        assert code == 2 and out == "" and err.startswith("refused: ")
    code, out, _ = cli(tmp_path, capsys, json.dumps(partial), empty, as_of)
    assert code == 0 and "reason=card_coverage_not_complete" in out  # control: the single key is read as written
    for notified_text in (json.dumps({"0" * 64: True}), '["' + "0" * 64 + '"', "5"):
        code, out, err = cli(tmp_path, capsys, whole_text, empty, as_of, notified_text)
        assert code == 2 and out == "" and err.startswith("refused: ")


def fraction(text, digits):
    return text[:-1] + "." + digits + "Z"


def fractional_view(as_of_text, since_text, whole_as_of):
    view = json.loads(json.dumps(case(whole_as_of)))
    view["as_of_utc"] = as_of_text
    view["prs"][str(tlv.NUMBER)]["waiting_since_utc"] = since_text
    return view


@pytest.mark.parametrize("since, seconds, digits, state, wait", [
    (SINCE, 1800, "999", "below_threshold", 1800),  # RCO1's fixture: 30:00.999 alerted with wait_min=30
    (fraction(SINCE, "999"), 1801, "000", "alert", 1801),
    (fraction(SINCE, "500"), 1800, "400", "below_threshold", 1800),
    (SINCE, 1801, "999", "alert", 1801)])
def test_f3_times_are_cut_to_whole_seconds_before_the_threshold(since, seconds, digits, state, wait):
    whole = shift(SINCE, seconds=seconds)
    observed = fraction(whole, digits)
    out = jcd.deliver(fractional_view(observed, since, whole), receipts(), now_utc=observed)
    [row] = out["ledger"]
    assert row["state"] == state and row["wait_seconds"] == wait and row["observed_utc"] == whole
    assert row["waiting_since_utc"] == SINCE
    assert row["identity"] == jcd.identity(tlv.NUMBER, tlv.NEW, "build_tools", TOOLS, SINCE)
    assert len(out["operator_lines"]) == (state == "alert")


@pytest.mark.parametrize("as_of_digits, now_seconds, now_digits, trust", [
    ("900", 900, "950", "current"),  # uncut: 900.05 s old
    ("000", 901, "000", "card_stale"),
    ("900", 0, "100", "current"),  # uncut: 0.8 s in the future
    ("000", -1, "500", "card_from_future")])
def test_f3_times_are_cut_to_whole_seconds_before_the_card_age(as_of_digits, now_seconds, now_digits, trust):
    whole = shift(SINCE, minutes=60)
    out = jcd.deliver(fractional_view(fraction(whole, as_of_digits), SINCE, whole), receipts(),
                      now_utc=fraction(shift(whole, seconds=now_seconds), now_digits))
    assert out["card_trust"] == trust and out["ledger"][0]["state"] == ("alert" if trust == "current" else "unknown")


def test_f3_receipt_times_and_the_identity_since_are_cut_too():
    whole = shift(SINCE, minutes=60)
    view = fractional_view(fraction(whole, "900"), SINCE, whole)
    requested = dict(rrow("requested", whole), at_utc=fraction(whole, "950"))  # after the uncut as_of, same second
    out = jcd.deliver(view, receipts([requested], requested=whole, read=fraction(whole, "500")), now_utc=whole)
    states = out["ledger"][0]["receipts"]
    assert states["requested"] == {"state": "OBSERVED", "ref": requested["ref"], "at_utc": whole}
    assert states["read"] == {"state": "NONE", "through_utc": whole}
    for since in (fraction(SINCE, "999"), SINCE[:-1] + "+00:00"):  # the same S2 since in other spellings
        out = jcd.deliver(fractional_view(whole, since, whole), receipts(), now_utc=whole)
        assert out["ledger"][0]["identity"] == jcd.identity(tlv.NUMBER, tlv.NEW, "build_tools", TOOLS, SINCE)


def test_named_mutant_uncut_times_is_killed(monkeypatch):
    monkeypatch.setattr(jcd, "_time", lambda value: jcd.ws.parse_time(value) if isinstance(value, str) else None)
    with pytest.raises(AssertionError):
        test_f3_times_are_cut_to_whole_seconds_before_the_threshold(SINCE, 1800, "999", "below_threshold", 1800)


# --- v1.2 repairs after the 10-15 evening audit ----------------------------------------------------------------------
# R1: RCO2's public G-L result (19:02:55Z, processed by Lead at 19:13:18Z) found that a malformed view record raised a
# raw TypeError (empty_slots an int) or KeyError (a credited vote without ref). The sweeps below are my own; RCO2's
# fixtures and harness were not read. R2: my static G-L map (19:08:10Z) and my registered lane-card age observation
# (19:35:33Z): a lane card did not name the card trust, so a stale, future or partial card of a lane without a
# condition said "no wait over 30 min". R3: the delivery names the canonical digest of the exact view it read.

RECORD_REFUSALS = ("a card record is not a lane view v1a record", "a card record is not a lane view v1a action record")
FILLED_AT = "2026-10-13T17:45:00Z"  # the Tools build vote that fills the last slot: no lane has a condition


def plain(value):
    """A plain JSON copy, as the CLI reads it."""
    return json.loads(json.dumps(value))


def deletions(value):
    """Copies of a JSON value with ONE mapping key removed, at any depth."""
    if isinstance(value, dict):
        for key in value:
            yield {other: item for other, item in value.items() if other != key}
            for inner in deletions(value[key]):
                yield {**value, key: inner}
    elif isinstance(value, list):
        for index in range(len(value)):
            for inner in deletions(value[index]):
                yield value[:index] + [inner] + value[index + 1:]


def deep_list(depth=100000):
    """A list nested ``depth`` times, beyond the recursion limit of the JSON reader and writer."""
    value = []
    for _ in range(depth):
        value = [value]
    return value


def test_v12_r1_any_type_at_any_position_of_a_view_record_refuses_or_is_read_never_raises():
    as_of = shift(SINCE, minutes=60)
    view, key = plain(case(as_of)), str(tlv.NUMBER)
    assert len(jcd.deliver(view, receipts(), now_utc=as_of)["operator_lines"]) == 1  # control
    refused = read = 0
    for record in [*substitutions(view["prs"][key]), *deletions(view["prs"][key])]:
        try:
            jcd.deliver(dict(view, prs=dict(view["prs"], **{key: record})), receipts(), now_utc=as_of)
            read += 1
        except jcd.DeliveryError as error:
            assert str(error) in RECORD_REFUSALS
            refused += 1
    assert refused and read  # both outcomes occur, so the sweep is not vacuous


def test_v12_r1_the_pr_order_never_raises_for_any_key():
    as_of = shift(SINCE, minutes=60)
    view = plain(case(as_of))
    # A superscript two is isdigit() but not int(); 5000 digits exceed int()'s default string limit.
    for key in ("\u00b2", "\u0661\u0662", "-1", "", "x", "9" * 5000):
        out = jcd.deliver(dict(view, prs=dict(view["prs"], **{key: {"state": "abstain"}})), receipts(), now_utc=as_of)
        assert len(out["operator_lines"]) == 1


def test_v12_r1_cli_deeply_nested_files_are_unreadable_or_refused_never_raised(tmp_path, capsys):
    as_of = shift(SINCE, minutes=60)
    view_text, deep = json.dumps(case(as_of)), "[" * 100000 + "]" * 100000
    code, out, _ = cli(tmp_path, capsys, view_text, deep, as_of)
    assert code == 0 and "last_open_slot=yes" in out and "read=UNKNOWN(receipts_unreadable) " in out
    for view_in, notified in ((deep, None), (view_text, deep)):
        code, out, err = cli(tmp_path, capsys, view_in, json.dumps(receipts()), as_of, notified)
        assert code == 2 and out == "" and err.startswith("refused: ")


@pytest.mark.parametrize("complete, seconds, trust", [(True, 900, "current"), (True, 901, "card_stale"),
                                                      (True, -1, "card_from_future"),
                                                      (False, 0, "card_coverage_not_complete")])
def test_v12_r2_every_lane_card_names_the_trust_and_an_untrusted_card_states_no_absence(complete, seconds, trust):
    as_of = shift(SINCE, minutes=60)
    view = case(as_of, events=tlv.base_events() + [tlv.vote(FILLED_AT, TOOLS)], complete=complete)
    out = jcd.deliver(view, receipts(), now_utc=shift(as_of, seconds=seconds))
    assert out["card_trust"] == trust and out["ledger"] == []
    for text in out["lanes"].values():
        head, *body = text.splitlines()
        assert f" card_trust={trust};" in head
        assert body == (["no wait over 30 min on a slot this lane owns"] if trust == "current"
                        else [f"no wait is stated for this lane: card_trust={trust}"])
    preview = jcd.operator_preview(out)
    assert ("no new wait over 30 min on a last open slot" in preview) == (trust == "current")
    assert (f"no wait is stated: card_trust={trust}" in preview) == (trust != "current")


def test_v12_r2_a_lane_with_an_unknown_condition_names_the_trust_and_lists_it():
    as_of = shift(SINCE, minutes=60)
    out = jcd.deliver(case(as_of), receipts(), now_utc=shift(as_of, seconds=901))
    head, *body = out["lanes"][TOOLS].splitlines()
    assert " card_trust=card_stale;" in head
    assert body == [f"UNKNOWN, not asserted: 1 (pr{tlv.NUMBER} head={tlv.NEW[:12]} slot=build_tools reason=card_stale)"]


def test_v12_r3_the_delivery_names_the_canonical_digest_of_the_exact_view_it_read():
    as_of = shift(SINCE, minutes=60)
    view, partial = case(as_of), case(as_of, complete=False)
    assert view["as_of_utc"] == partial["as_of_utc"] and view["coverage"] != partial["coverage"]
    digest = jcd.deliver(view, receipts(), now_utc=as_of)["card"]["view_canonical_sha256"]
    assert digest == lv.sha256_text(lv.canonical(view)) == jcd.view_digest(plain(view))
    assert digest != jcd.deliver(partial, receipts(), now_utc=as_of)["card"]["view_canonical_sha256"]


def test_v12_r3_a_view_that_is_not_canonical_json_refuses_the_turn():
    as_of = shift(SINCE, minutes=60)
    view = plain(case(as_of))
    for extra in (float("nan"), float("inf"), {1, 2}, b"x", {1: "a", "b": "c"}, deep_list()):
        assert jcd.view_digest(dict(view, extra=extra)) is None
        with pytest.raises(jcd.DeliveryError) as caught:
            jcd.deliver(dict(view, extra=extra), receipts(), now_utc=as_of)
        assert str(caught.value) == "the card is not a lane view v1a"


def test_named_mutant_unchecked_view_records_is_killed(monkeypatch):
    def unchecked(key, record):  # the v1.1 reading: the S1 filter only, no shape checks
        if not isinstance(record, dict) or record.get("state") != "action" \
                or len(record.get("empty_slots") or []) != 1:
            return None
        return record["empty_slots"][0]
    monkeypatch.setattr(jcd, "_condition", unchecked)
    with pytest.raises((TypeError, KeyError, AttributeError, IndexError)):
        test_v12_r1_any_type_at_any_position_of_a_view_record_refuses_or_is_read_never_raises()


def test_named_mutant_lane_card_without_trust_is_killed(monkeypatch):
    real = jcd._card
    monkeypatch.setattr(jcd, "_card", lambda lane, observed, coverage, trust, *rows: real(lane, observed, coverage,
                                                                                          None, *rows))
    with pytest.raises(AssertionError):
        test_v12_r2_every_lane_card_names_the_trust_and_an_untrusted_card_states_no_absence(True, 901, "card_stale")


def test_named_mutant_delivery_without_the_view_digest_is_killed(monkeypatch):
    monkeypatch.setattr(jcd, "view_digest", lambda view: "0" * 64)
    with pytest.raises(AssertionError):
        test_v12_r3_the_delivery_names_the_canonical_digest_of_the_exact_view_it_read()


# --- v1.3 after my own probe of the JOIN card (2026-10-16) -----------------------------------------------------------
# An action record's checks was read as any text and echoed as ci_at_head in the alert line, so a line break in it
# added a forged line to the lane card and to the operator preview. Characters are built with chr(). Each repair test
# was red on the prior branch head first (the two controls pass on both).

FORGED_WAIT = f"WAIT pr={tlv.NUMBER} head={tlv.NEW} slot=build_tools lane={TOOLS} last_open_slot=yes forged"
BAD_CHECKS = ("6/6" + chr(0x0A) + FORGED_WAIT, "6/6" + chr(0x0A), "6/6" + chr(0x0D), "6/6" + chr(0x2028), "6/7",
              "0/0", "06/06", "UNKNOWN", " 6/6", "6/6 ", "1000/1000", "6", "6/6/6", "", "+6/+6",
              chr(0x0666) + "/" + chr(0x0666), "1" + chr(0x0666) + "/1" + chr(0x0666))  # Arabic-Indic six: isdigit()
GOOD_CHECKS = ("1/1", "6/6", "10/10", "999/999")
SWEEP = (0x0A, 0x0D, 0x1B, 0x85, 0x2028, 0x202E, 0x2066, 0x200B, 0xE0041, 0xD800)


def line_unsafe(char):
    """The JOIN card v0.3 rule, the tests' own copy: a control, format (except ZWJ and ZWNJ), surrogate, line or
    paragraph separator character."""
    return unicodedata.category(char) in ("Cc", "Cf", "Cs", "Zl", "Zp") and char not in (chr(0x200C), chr(0x200D))


def with_checks(view, checks):
    view = plain(view)
    view["prs"][str(tlv.NUMBER)]["checks"] = checks
    return view


def strings_with(value, text):
    """Copies of a JSON value with ``text`` put before, then after, ONE string (a mapping value or list item at any
    depth)."""
    if isinstance(value, str):
        yield text + value
        yield value + text
    elif isinstance(value, dict):
        for key in value:
            for inner in strings_with(value[key], text):
                yield {**value, key: inner}
    elif isinstance(value, list):
        for index in range(len(value)):
            for inner in strings_with(value[index], text):
                yield value[:index] + [inner] + value[index + 1:]


def clean_lines(text):
    """Every line is free of the JOIN card v0.3 characters and none starts with the forged wait."""
    rows = text.split("\n")
    return rows[-1] == "" and not any(row.startswith("WAIT forged") or any(line_unsafe(char) for char in row)
                                      for row in rows)


@pytest.mark.parametrize("checks", BAD_CHECKS)
def test_v13_an_action_record_whose_checks_is_not_the_lane_view_form_refuses_the_turn(checks):
    as_of = shift(SINCE, minutes=60)
    with pytest.raises(jcd.DeliveryError) as caught:
        jcd.deliver(with_checks(case(as_of), checks), receipts(), now_utc=as_of)
    assert str(caught.value) == "a card record is not a lane view v1a action record"


@pytest.mark.parametrize("checks", GOOD_CHECKS)
def test_v13_the_lane_view_form_is_read_and_echoed_whole(checks):
    as_of = shift(SINCE, minutes=60)
    out = jcd.deliver(with_checks(case(as_of), checks), receipts(), now_utc=as_of)
    [alert] = out["operator_lines"]
    assert f" ci_at_head={checks} veto=" in alert
    assert alert in out["lanes"][TOOLS] and alert in jcd.operator_preview(out)


def test_v13_a_record_in_another_state_is_not_read_beyond_its_state():
    as_of = shift(SINCE, minutes=60)
    view = plain(case(as_of))
    view["prs"]["7"] = {"state": "abstain", "pr": 7, "checks": "6/6" + chr(0x0A) + FORGED_WAIT}
    out = jcd.deliver(view, receipts(), now_utc=as_of)
    outputs = [*out["lanes"].values(), jcd.operator_preview(out)]
    assert len(out["operator_lines"]) == 1 and all(clean_lines(text) and FORGED_WAIT not in text for text in outputs)


def test_v13_every_view_or_receipts_string_with_a_line_character_refuses_the_turn_or_reaches_no_line():
    as_of = shift(SINCE, minutes=60)
    view = plain(case(as_of))
    doc = receipts([rrow("requested", shift(SINCE, minutes=1))], requested=as_of, read=as_of)
    assert len(jcd.deliver(view, doc, now_utc=as_of)["operator_lines"]) == 1  # control
    refused = read = 0
    for point in SWEEP:
        text = chr(point) + "WAIT forged"
        for broken_view, broken_doc in [*((item, doc) for item in strings_with(view, text)),
                                        *((view, item) for item in strings_with(doc, text))]:
            try:
                out = jcd.deliver(broken_view, broken_doc, now_utc=as_of)
            except jcd.DeliveryError:
                refused += 1
                continue
            assert all(clean_lines(output) for output in [*out["lanes"].values(), jcd.operator_preview(out)])
            read += 1
        for now in (text + as_of, as_of + text):
            with pytest.raises(jcd.DeliveryError):
                jcd.deliver(view, doc, now_utc=now)
        with pytest.raises(jcd.DeliveryError):
            jcd.deliver(view, doc, now_utc=as_of, notified=["a" * 64 + text])
    assert refused and read  # both outcomes occur, so the sweep is not vacuous


def test_named_mutant_checks_read_as_any_text_is_killed(monkeypatch):
    monkeypatch.setattr(jcd, "CHECKS", re.compile(r"(?s).*"))
    with pytest.raises(pytest.fail.Exception):
        test_v13_an_action_record_whose_checks_is_not_the_lane_view_form_refuses_the_turn(BAD_CHECKS[0])


# --- v1.4 after RCO1's design observation in its rerun of the v0.3 line-safety harness (2026-10-16) ------------------
# With one condition unknown on a current card, the operator preview printed its absence line before the UNKNOWN line,
# which could read as an all-clear. Each repair test was red on the prior branch head first (the control passes on
# both).

ABSENCE = "no new wait over 30 min on a last open slot"


def with_since(view, since):
    view = plain(view)
    view["prs"][str(tlv.NUMBER)]["waiting_since_utc"] = since
    return view


@pytest.mark.parametrize("since, reason", [(jcd.UNKNOWN, "wait_start_unknown"),
                                           ("2026-10-13T20:00:00Z", "wait_start_after_observed")])
def test_v14_a_current_preview_with_an_unknown_condition_states_no_absence(since, reason):
    as_of = shift(SINCE, minutes=79)
    out = jcd.deliver(with_since(case(as_of), since), receipts(), now_utc=as_of)
    assert out["card_trust"] == "current" and out["operator_lines"] == []
    assert [(row["state"], row["reason"]) for row in out["ledger"]] == [("unknown", reason)]
    unknown = (f"UNKNOWN, not asserted: 1 (pr{tlv.NUMBER} head={tlv.NEW[:12]} slot=build_tools lane={TOOLS} "
               f"reason={reason})")
    assert jcd.operator_preview(out).splitlines()[1:] == [
        unknown, "counts: alert=0 shown=0 unchanged=0 carried=0 below_threshold=0 unknown=1"]
    assert "no wait over 30 min" not in out["lanes"][TOOLS]  # the lane card stated none already


def test_v14_a_preview_whose_new_wait_was_carried_and_has_no_alert_line_states_no_absence():
    """The preview's own rule over an output whose new waits were carried and that holds no alert line."""
    first, _ = two_turns()
    assert first["card_trust"] == "current" and first["counts"]["carried"] > 0 and first["counts"]["unknown"] == 0
    preview = jcd.operator_preview(dict(first, operator_lines=[]))
    assert ABSENCE not in preview and "no wait is stated" not in preview


def test_v15_the_output_names_the_registered_semantics_change():
    as_of = shift(SINCE, minutes=79)
    assert jcd.deliver(case(as_of), receipts(), now_utc=as_of)["semantics"] == "join-card-delivery-semantics-v1.5"


def test_v14_control_a_current_preview_with_only_unchanged_waits_still_states_the_absence():
    as_of = shift(SINCE, minutes=79)
    first = jcd.deliver(case(as_of), receipts(), now_utc=as_of)
    again = jcd.deliver(case(as_of), receipts(), now_utc=as_of, notified=first["notified"])
    assert again["operator_lines"] == [] and again["counts"]["unchanged"] == 1 and again["counts"]["unknown"] == 0
    assert jcd.operator_preview(again).splitlines()[1] == ABSENCE


def test_named_mutant_preview_absence_beside_an_unknown_condition_is_killed(monkeypatch):
    def v13_rule(out, unknown):
        return [ABSENCE if out["card_trust"] == "current" else f"no wait is stated: card_trust={out['card_trust']}"]

    monkeypatch.setattr(jcd, "_preview_absence", v13_rule)
    with pytest.raises(AssertionError):
        test_v14_a_current_preview_with_an_unknown_condition_states_no_absence(jcd.UNKNOWN, "wait_start_unknown")


# --- v1.5 after RCO1's static review of 650e8870 for Lead (answered 06:35:29Z; Lead record 3AFDDC5F) ------------------
# G1 a source complete through a time after now gave an affirmative NONE; G2 the rows of a kind without a source gave
# OBSERVED; G3 a row after its own kind's complete-through time was accepted. RCO1 named the gaps statically; these
# cases are my own. N1 is my own finding while changing the S5 states case for G3: a NONE stated its absence through its
# source's complete-through time, although only rows at or before observed are checked. Each repair test was red on the
# prior branch head first (the controls pass on both). Three older tests changed on purpose: the S5 states case held a
# read one second after its own coverage (the G3 shape), so its read source now covers it and the clock is one second
# later; its expected states stay those of v1.4, and on the prior head it fails by N1; the f1 sweep passes the turn's
# clock to read_receipts; the semantics literal names v1.5.

LATE_THROUGH = {"one-second-late": 1, "year-2099": "2099-01-01T00:00:00Z"}


def unreadable_but_alerting(out):
    """An unreadable receipts document never removes the alert and never states an absence (S5)."""
    [row] = out["ledger"]
    return out["receipts_readable"] is False and row["state"] == "alert" and len(out["operator_lines"]) == 1 \
        and all(state == {"state": "UNKNOWN", "reason": "receipts_unreadable"} for state in row["receipts"].values()) \
        and "none_through" not in out["operator_lines"][0]


@pytest.mark.parametrize("late", list(LATE_THROUGH), ids=list(LATE_THROUGH))
def test_v15_g1_a_source_complete_through_a_time_after_now_is_unreadable(late):
    as_of = shift(SINCE, minutes=60)
    value = LATE_THROUGH[late]
    through = shift(as_of, seconds=value) if isinstance(value, int) else value
    requested = rrow("requested", shift(SINCE, minutes=1))
    for doc in (receipts(read=through), receipts([requested], requested=as_of, read=through)):
        assert unreadable_but_alerting(jcd.deliver(case(as_of), doc, now_utc=as_of))


def test_v15_g1_control_coverage_through_the_clock_or_within_its_second_is_read():
    as_of = shift(SINCE, minutes=60)
    now = shift(as_of, minutes=1)
    for through, now_text in ((now, now), (fraction(now, "900"), fraction(now, "100"))):  # the same whole second
        out = jcd.deliver(case(as_of), receipts(read=through), now_utc=now_text)
        assert out["receipts_readable"] is True and out["card_trust"] == "current"
        assert out["ledger"][0]["receipts"]["read"]["state"] == "NONE"  # its time is the N1 case


def test_v15_g2_rows_of_a_kind_without_a_source_are_unreadable():
    as_of = shift(SINCE, minutes=60)
    at = shift(SINCE, minutes=1)
    for row in (rrow("read", at), rrow("read", at, lane="claude-rco-2"), rrow("read", at, pr=tlv.SECOND)):
        assert unreadable_but_alerting(jcd.deliver(case(as_of), receipts([row], requested=as_of), now_utc=as_of))
    row = rrow("read", at)  # control: with a read source the same row is read
    out = jcd.deliver(case(as_of), receipts([row], requested=as_of, read=as_of), now_utc=as_of)
    assert out["ledger"][0]["receipts"]["read"] == {"state": "OBSERVED", "ref": row["ref"], "at_utc": at}


def test_v15_g3_a_row_after_its_kind_coverage_is_unreadable():
    as_of = shift(SINCE, minutes=60)
    through = shift(SINCE, minutes=1)
    for row in (rrow("read", shift(through, seconds=1)), rrow("read", shift(through, seconds=1), head=tlv.OLD),
                rrow("read", shift(as_of, seconds=1))):
        assert unreadable_but_alerting(jcd.deliver(case(as_of), receipts([row], read=through), now_utc=as_of))


def test_v15_g3_control_a_row_at_its_kind_complete_through_time_is_read():
    as_of = shift(SINCE, minutes=60)
    through = shift(SINCE, minutes=1)
    for at, through_text in ((through, through), (fraction(through, "950"), fraction(through, "100"))):
        row = rrow("read", at)  # the second pair lies in one whole second
        out = jcd.deliver(case(as_of), receipts([row], read=through_text), now_utc=as_of)
        assert out["receipts_readable"] is True
        assert out["ledger"][0]["receipts"]["read"] == {"state": "OBSERVED", "ref": row["ref"], "at_utc": through}


def test_v15_n1_none_states_the_absence_through_observed_never_the_later_coverage_end():
    as_of = shift(SINCE, minutes=60)
    read_at, through, now = shift(as_of, minutes=3), shift(as_of, minutes=5), shift(as_of, minutes=6)
    row = rrow("read", read_at)  # a matching read after observed, inside its source's complete coverage
    for rows in ([row], []):
        doc = receipts(rows, requested=through, delivered=through, read=through, answered=through)
        out = jcd.deliver(case(as_of), doc, now_utc=now)
        assert out["receipts_readable"] is True and out["card_trust"] == "current"
        [ledger] = out["ledger"]
        assert ledger["receipts"] == {kind: {"state": "NONE", "through_utc": as_of} for kind in jcd.KINDS}
        [line] = out["operator_lines"]
        assert f"read=none_through_{as_of} " in line and f"none_through_{through}" not in line


def test_named_mutant_receipts_read_without_the_clock_and_coverage_checks_is_killed(monkeypatch):
    monkeypatch.setattr(jcd, "_consistent", lambda rows, through, now: True)  # the v1.4 reading
    with pytest.raises(AssertionError):
        test_v15_g1_a_source_complete_through_a_time_after_now_is_unreadable("year-2099")
