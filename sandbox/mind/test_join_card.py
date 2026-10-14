"""Tests of the JOIN card v0.1 renderer. Every card is rendered from the real core, the real step-1 lane view and the
real delivery observer (the fixtures of ``test_join_card_delivery.py``), so each line reads what upstream really
writes. REMEMBER answers have the shape ``remember_v2.RememberV2.apply_picks`` returns (frozen 2699c0fe) and reach the
card only inside a turn-bound envelope (v0.1, from RCO1's probe result 16214158: P2b and P4b).

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/test_join_card.py -q -p no:cacheprovider
"""

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _path in (str(HERE), str(HERE / "step1")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
import join_card as jc  # noqa: E402
import join_card_delivery as jcd  # noqa: E402
import lane_view as lv  # noqa: E402
import test_join_card_delivery as tjd  # noqa: E402  (real core + real lane view fixtures)
import test_lane_view as tlv  # noqa: E402

AS_OF = "2026-10-13T19:00:00Z"
NOW = "2026-10-13T19:05:00Z"
LEARN = "rco2-learn:f80a92c2"
TOOLS = "codex-tools-1"
SITUATION = hashlib.sha256(b"PR1905 build vote, the Tools lane's turn").hexdigest()
SECTIONS = ["JOIN", "HEAR", "SEE", "SLOT", "UNDERSTAND", "REMEMBER", "LEARN"]
NO_MEMORY = [section for section in SECTIONS if section != "REMEMBER"]
FORBIDDEN = re.compile(r"(?i)approv|merg")
GUARDED = "\\u0050" + "RIVATE" + "_MARKER"  # escaped; the decoded guard reveals it


def turn(as_of=AS_OF, now=NOW, view=None, receipts=None, notified=()):
    view = tjd.case(as_of) if view is None else view
    return view, jcd.deliver(view, tjd.receipts() if receipts is None else receipts, now_utc=now, notified=notified)


def answer(*results, status="ANSWER", reason=None):
    return {"status": status, "abstain": status == "ABSTAIN", "reason": reason, "degraded": status == "DEGRADED",
            "degraded_cells": [], "results": list(results), "version": "remember-v2.0", "telemetry": {}}


def bound(value, lane=TOOLS, as_of=AS_OF, situation=SITUATION):
    """The turn-bound envelope a turn runner makes around one REMEMBER v2 answer."""
    return jc.remember_envelope(lane, as_of, situation, value)


def pick(ref, summary="watcher retry reasons narrowed", day="2026-10-12", **extra):
    return dict({"ref": ref, "kind": "lesson", "time_utc": f"{day}T00:00:00.000000Z", "summary": summary,
                 "contested_with": [], "pair_with": [], "superseded_by": None}, **extra)


def cite(envelope):
    return f"[remember-v2.0 answer={envelope['answer_sha256'][:12]} situation={envelope['situation_sha256'][:12]}]"


def derived(text):
    """The lines the card words itself (REMEMBER quotes memory, so it is left out)."""
    return [line for line in text.splitlines() if not line.startswith("REMEMBER")]


def sections(text):
    return [row.split(" ", 1)[0] for row in text.splitlines()]


def line(text, section):
    return next(row for row in text.splitlines() if row.split(" ", 1)[0] == section)


# --- the PR1905 night: the Tools build vote is the last open slot ----------------------------------------------------

def test_pr1905_tools_lane_reads_the_whole_conjunction_in_one_card():
    view, delivery = turn()
    text = jc.card(TOOLS, delivery, view, None, learn_ref=LEARN)
    assert sections(text) == NO_MEMORY and len(text.encode()) <= jc.CARD_BYTES
    record = view["prs"][str(tlv.NUMBER)]
    slot = line(text, "SLOT")
    assert slot.startswith(f"SLOT pr={tlv.NUMBER} head={tlv.NEW} credited_at_head=2(")
    assert all(vote["ref"][:12] in slot for vote in record["credited"])
    assert "you_in_set=no veto=none_observed ci_at_head=" in slot and slot.endswith("last_open_slot=yes")
    assert line(text, "UNDERSTAND").startswith(f"UNDERSTAND your build_tools vote is the last open slot on PR{tlv.NUMBER}")
    [row] = [row for row in delivery["ledger"] if row["lane"] == TOOLS]
    hear = line(text, "HEAR")
    assert "new 1," in hear and f"id={row['identity'][:12]}" in hear and f"wait_min={row['wait_seconds'] // 60}" in hear
    assert line(text, "LEARN").endswith(LEARN) and text.startswith(f"JOIN {TOOLS} as_of={AS_OF} now={NOW} join-card-v0.1;")
    assert not any(FORBIDDEN.search(row) for row in derived(text))


def test_no_remember_input_gives_no_remember_line():
    """P2b: an absence is omitted, never an uncited 'not run' line."""
    view, delivery = turn()
    for lane in jc.lv.LANES:
        text = jc.card(lane, delivery, view, None, learn_ref=LEARN)
        assert sections(text) == NO_MEMORY and "REMEMBER" not in text


def test_lanes_without_an_open_slot_derive_nothing():
    view, delivery = turn()
    for lane in jc.lv.LANES:
        text = jc.card(lane, delivery, view, None, learn_ref=LEARN)
        slot, records = jc.lane_records(view, lane)
        if records:
            continue
        assert "SLOT none" in text and "UNDERSTAND nothing of yours is open" in text
        assert "HEAR waits over 30 min on your slots: new 0," in text
        assert ("SEE this lane owns no vote slot" in text) == (slot is None)
    assert jc.lane_records(view, "fable-5") == (None, [])


def test_two_open_slots_say_no_and_name_the_other():
    view, delivery = turn(view=tjd.case(AS_OF, events=tlv.base_events()[:2]))
    text = jc.card(TOOLS, delivery, view, None, learn_ref=LEARN)
    assert line(text, "SLOT").endswith("last_open_slot=no") and "credited_at_head=1(" in text
    assert "your build_tools slot is open and so is build_lead" in text
    assert not any(FORBIDDEN.search(row) for row in derived(text))


@pytest.mark.parametrize("complete, now, reason", [(False, NOW, "view_coverage_not_complete"),
                                                   (True, "2026-10-13T19:15:01Z", "card_stale"),
                                                   (True, "2026-10-13T18:59:59Z", "card_from_future")])
def test_untrusted_card_is_unknown_and_derives_no_step(complete, now, reason):
    view, delivery = turn(view=tjd.case(AS_OF, complete=complete), now=now)
    text = jc.card(TOOLS, delivery, view, None, learn_ref=LEARN)
    assert line(text, "SLOT").endswith(f"last_open_slot=UNKNOWN reason={reason}")
    assert line(text, "UNDERSTAND").startswith(f"UNDERSTAND UNKNOWN for PR{tlv.NUMBER}")
    assert "last open slot" not in text


def test_the_15_minute_twin_is_still_current():
    view, delivery = turn(now="2026-10-13T19:15:00Z")
    assert jc.card(TOOLS, delivery, view, None, learn_ref=LEARN).count("last_open_slot=yes") == 1


def test_dedupe_counts_an_unchanged_wait_and_never_hides_the_slot_truth():
    view, delivery = turn()
    [row] = [row for row in delivery["ledger"] if row["lane"] == TOOLS]
    _, again = turn(view=view, notified=[row["identity"]])
    text = jc.card(TOOLS, again, view, None, learn_ref=LEARN)
    assert "new 0, unchanged 1," in line(text, "HEAR") and "first pr=" not in text
    assert line(text, "SLOT").endswith("last_open_slot=yes")


def test_the_only_empty_slot_comes_first_and_gets_the_slot_line():
    heads = {n: hashlib.sha1(f"order {n}".encode()).hexdigest() for n in (2000, 2001)}
    branches = {n: f"codex-lead-1/order-{n}-20261013" for n in heads}
    events = [tlv.vote("2026-10-13T17:30:00Z", "claude-rco-1", head=heads[2000], task=branches[2000], number=2000),
              tlv.vote("2026-10-13T17:50:00Z", "claude-rco-1", head=heads[2001], task=branches[2001], number=2001),
              tlv.vote("2026-10-13T17:50:01Z", "codex-lead-1", head=heads[2001], task=branches[2001], number=2001)]
    github = tlv.merge_github(*(tlv.github_of(head=heads[n], number=n, branch=branches[n],
                                              observed=tjd.shift(AS_OF, minutes=-5)) for n in heads))
    view, delivery = turn(view=tjd.case(AS_OF, events=events, github=github))
    text = jc.card(TOOLS, delivery, view, None, learn_ref=LEARN)
    assert line(text, "SEE").startswith(f"SEE pr2001@{heads[2001][:12]} empty=build_tools ")
    assert f"; pr2000@{heads[2000][:12]} empty=build_lead,build_tools " in text  # older wait, but not the only slot
    assert line(text, "SLOT").startswith(f"SLOT pr=2001 head={heads[2001]} ")
    assert line(text, "SLOT").endswith("last_open_slot=yes")


def test_a_delivery_of_another_view_is_refused():
    view, delivery = turn()
    with pytest.raises(jc.JoinCardError):
        jc.card(TOOLS, delivery, tjd.case("2026-10-13T19:01:00Z"), None, learn_ref=LEARN)


# --- REMEMBER -------------------------------------------------------------------------------------------------------

def test_remember_shows_picks_with_their_marks_and_cites_its_binding():
    view, delivery = turn()
    topic = {"group": 1, "records": 3, "first_day": "2026-10-10", "last_day": "2026-10-13", "head": True}
    rich = bound(answer(pick("lesson_a.md", topic=topic, topic_others=["lesson_b.md"], superseded_by="lesson_c.md",
                             contested_with=["lesson_d.md"]), pick("lesson_d.md", contested_with=["lesson_a.md"])))
    text = jc.card(TOOLS, delivery, view, rich, learn_ref=LEARN)
    assert sections(text) == SECTIONS[:5] + ["REMEMBER"] * 3 + ["LEARN"]
    assert ("REMEMBER [1] lesson_a.md (lesson, 2026-10-12) [TOPIC 3 records 2026-10-10..2026-10-13, may disagree; "
            "also shown: lesson_b.md] [SUPERSEDED by lesson_c.md] [CONTESTED with lesson_d.md]: watcher retry reasons "
            "narrowed") in text
    assert "REMEMBER [2] lesson_d.md (lesson, 2026-10-12) [CONTESTED with lesson_a.md]" in text
    assert f"REMEMBER picks {cite(rich)}\n" in text and text.count("answer=") == 1
    assert rich["answer_sha256"][:12] in text and SITUATION[:12] in text


def test_remember_says_why_not_with_its_binding():
    view, delivery = turn()
    cases = {"REMEMBER nothing applies (nothing_applies) ": answer(status="ABSTAIN", reason="nothing_applies"),
             "REMEMBER refused (picks_invalid) ": answer(status="REFUSED", reason="picks_invalid"),
             "REMEMBER degraded (a memory cell is quarantined, fallback) [1] lesson_a.md": answer(
                 pick("lesson_a.md"), status="DEGRADED", reason="cell_quarantined"),
             "REMEMBER degraded (a memory cell is quarantined, fallback) nothing applies (cell_quarantined) ": answer(
                 status="DEGRADED", reason="cell_quarantined")}
    for expected, value in cases.items():
        envelope = bound(value)
        text = jc.card(TOOLS, delivery, view, envelope, learn_ref=LEARN)
        assert expected in text and cite(envelope) in text and text.count("answer=") == 1


def test_envelope_digest_is_the_canonical_answer():
    value = answer(pick("lesson_a.md"))
    envelope = bound(value)
    assert set(envelope) == jc.ENVELOPE_KEYS and envelope["schema"] == jc.ENVELOPE_SCHEMA
    assert envelope["answer_sha256"] == hashlib.sha256(lv.canonical(value).encode("utf-8")).hexdigest()
    assert envelope["lane"] == TOOLS and envelope["as_of_utc"] == AS_OF and envelope["situation_sha256"] == SITUATION


def _edited(**change):
    envelope = bound(answer(pick("lesson_a.md")))
    envelope.update(change)
    return envelope


@pytest.mark.parametrize("make, message", [
    (lambda: bound(answer(pick("lesson_a.md")), as_of="2026-10-13T18:45:00Z"), "another turn"),
    (lambda: bound(answer(pick("lesson_a.md")), lane="codex-lead-1"), "another lane"),
    (lambda: _edited(answer=answer(pick("lesson_b.md"))), "does not match its digest"),
    (lambda: _edited(answer_sha256="A" * 64), "64 lowercase hex"),
    (lambda: _edited(situation_sha256="0" * 63), "64 lowercase hex"),
    (lambda: _edited(schema="join-card-remember-v0"), "not a turn-bound REMEMBER envelope"),
    (lambda: _edited(extra=1), "not a turn-bound REMEMBER envelope"),
    (lambda: {k: v for k, v in bound(answer()).items() if k != "situation_sha256"}, "not a turn-bound REMEMBER envelope"),
    (lambda: answer(pick("lesson_a.md")), "not a turn-bound REMEMBER envelope"),
])
def test_a_remember_answer_not_bound_to_this_turn_and_lane_is_refused(make, message):
    """P4b: a stale or foreign answer never renders as this turn's memory."""
    view, delivery = turn()
    with pytest.raises(jc.JoinCardError) as caught:
        jc.card(TOOLS, delivery, view, make(), learn_ref=LEARN)
    assert message in str(caught.value) and "lesson" not in str(caught.value)


def test_size_bound_drops_picks_then_prs_whole_and_counts_them():
    view, delivery = turn(view=tjd.many(6, AS_OF))
    assert len(jc.lane_records(view, TOOLS)[1]) == 6
    long = chr(0x6F22) * jc.SUMMARY_CHARS  # 450 bytes
    envelope = bound(answer(*(pick(f"lesson_{i}.md", summary=long) for i in range(5))))
    text = jc.card(TOOLS, delivery, view, envelope, learn_ref=LEARN)
    assert len(text.encode()) <= jc.CARD_BYTES and sections(text)[:5] == SECTIONS[:5]
    assert "REMEMBER [1] lesson_0.md" in text and "REMEMBER [2]" not in text
    assert f"REMEMBER picks {cite(envelope)}; +4 more picks not shown\n" in text
    assert "+3 more PRs not shown" in text and text.endswith(f"LEARN contract {LEARN}\n")
    small = jc.card(TOOLS, delivery, view, bound(answer(pick("lesson_0.md"))), learn_ref=LEARN)
    assert "+3 more PRs not shown" in small and "REMEMBER [1] lesson_0.md" in small  # nothing else had to go


def test_a_card_that_cannot_fit_is_refused_whole():
    view, delivery = turn(view=tjd.many(6, AS_OF))
    ref = "r" * (jc.REF_CHARS - 3) + ".md"
    huge = pick(ref, summary=chr(0x6F22) * jc.SUMMARY_CHARS, topic_others=[ref, ref], contested_with=[ref, ref],
                superseded_by=ref, topic={"group": 1, "records": 4, "first_day": "2026-10-10", "last_day": "2026-10-13"})
    with pytest.raises(jc.JoinCardError):
        jc.card(TOOLS, delivery, view, bound(answer(huge)), learn_ref=LEARN)


def test_a_guarded_word_refuses_the_card():
    view, delivery = turn()
    with pytest.raises(jc.JoinCardError):
        jc.card(TOOLS, delivery, view, bound(answer(pick("lesson_a.md", summary=f"note {GUARDED}"))), learn_ref=LEARN)


@pytest.mark.parametrize("bad", [{"status": "MAYBE", "results": []}, {"status": "ANSWER", "results": [{"ref": "x"}]},
                                 {"status": "ANSWER"}, answer(pick("a.md", summary="x" * (jc.SUMMARY_CHARS + 1))),
                                 answer(pick("a.md", topic_others="b.md")), answer(pick("a.md", contested_with=[1])),
                                 answer(pick("r" * (jc.REF_CHARS + 1))), [], "x"])
def test_an_invalid_remember_answer_is_refused(bad):
    view, delivery = turn()
    with pytest.raises(jc.JoinCardError):
        jc.card(TOOLS, delivery, view, bound(bad), learn_ref=LEARN)


def test_invalid_inputs_are_refused_with_fixed_messages():
    view, delivery = turn()
    for lane, d, v, ref in (("nobody", delivery, view, LEARN), (TOOLS, dict(delivery, schema="x"), view, LEARN),
                            (TOOLS, delivery, dict(view, schema="x"), LEARN), (TOOLS, delivery, view, "x y z"),
                            (TOOLS, dict(delivery, ledger=None), view, LEARN), (TOOLS, delivery, None, LEARN)):
        with pytest.raises(jc.JoinCardError) as caught:
            jc.card(lane, d, v, None, learn_ref=ref)
        assert "x y z" not in str(caught.value)


def test_card_is_deterministic():
    view, delivery = turn()
    first = jc.card(TOOLS, delivery, view, bound(answer(pick("lesson_a.md"))), learn_ref=LEARN)
    assert first == jc.card(TOOLS, delivery, view, bound(answer(pick("lesson_a.md"))), learn_ref=LEARN)


def test_cli_prints_the_card_and_refuses_a_bad_input(tmp_path, capsys):
    view, delivery = turn()
    envelope = bound(answer(pick("lesson_a.md")))
    paths = {name: tmp_path / f"{name}.json" for name in ("view", "delivery", "remember")}
    for name, value in (("view", view), ("delivery", delivery), ("remember", envelope)):
        paths[name].write_text(json.dumps(value), encoding="utf-8")
    args = ["--lane", TOOLS, "--view", str(paths["view"]), "--delivery", str(paths["delivery"]), "--learn-ref", LEARN]
    assert jc.main(args + ["--remember", str(paths["remember"])]) == 0
    assert capsys.readouterr().out == jc.card(TOOLS, delivery, view, envelope, learn_ref=LEARN)
    paths["remember"].write_text('{"schema": "join-card-remember-v1", "schema": "x"}', encoding="utf-8")
    assert jc.main(args + ["--remember", str(paths["remember"])]) == 2
    assert capsys.readouterr().out.startswith("refused:")


@pytest.mark.skipif(not tjd.FROZEN_VIEWS.is_dir(), reason="frozen step-1 views are on the sandbox data disk")
def test_frozen_pr1905_night_views_give_bounded_guarded_cards_for_every_lane():
    paths = sorted(tjd.FROZEN_VIEWS.glob("*.lane-view.json"))
    assert paths
    for path in paths:
        view = json.loads(path.read_text(encoding="utf-8"))
        delivery = jcd.deliver(view, tjd.receipts(), now_utc=view["as_of_utc"])
        for lane in jc.lv.LANES:
            text = jc.card(lane, delivery, view, None, learn_ref=LEARN)
            assert len(text.encode()) <= jc.CARD_BYTES and not jc.ws._has_marker(text)
            assert not any(FORBIDDEN.search(row) for row in derived(text))
            if view["coverage"] != "complete":
                assert "last_open_slot=yes" not in text
