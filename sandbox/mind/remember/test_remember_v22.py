"""Tests for REMEMBER v2.2 dev on synthetic data (no e5, no real memory, no sealed cases). Written BEFORE the code.

RCO2 rounds 1, 1a and 1b (proposals in the RCO2 session audit folder, NOT run by RCO2) found two defects that the
selected Q4 revision 6cf81dbb keeps as stated limits: F1, an opposite inside the head's topic group is hidden and never
marked; F3, packing stops at the first group that does not fit. Each test names the proposal test it adapts; where
fable-5's design differs from the proposal (module docstring), the test says so. Every fixture is a measured
precondition: the v2.1 card of the same memory is built first and shows the defect. After the first run against the
code, two fixtures (the flood and the backdated extra) were corrected to a memory without the step-2 records: beside
them the extra had no room (measured, debug round 1), which is the room rule's own test; no assertion changed.
Three tests were added after that run, for stated rules no RED test pinned: the fence-trust order of hidden
opponents, the claimed-day place of an extra the fence trusts, and MAX_EXTRAS where a second extra would fit (its
mutant survived mutant run 1; fixtures measured, debug round 2).

run:C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/remember -q -p no:cacheprovider
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import remember_v22 as r22  # noqa: E402

r21 = r22.v21
rv = r22.v2
sys.path.insert(0, str(HERE.parent / "step2"))
import test_step2 as t2  # noqa: E402  (step-2 fixtures: FakeEncoder, lesson, event, write_inputs)

WHEN = "2026-10-14T12:00:00Z"
EARLY = "2026-10-13T12:00:00Z"
SITUATION = "alpha shard rebalance quota window error lease token queue"
D = "retry the shard rebalance on any quota window error and keep the lease token while the queue drains"
OPPOSITE = "never " + D
REORDERED = "shard rebalance retry on any quota window error and keep the lease token while the queue drains the"
GUARDED = "P" + "RIVATE" + "_MARKER"  # built at run time, never typed whole
WIDE = f"{chr(0x6F22) * 70} {chr(0x5B57) * 70}"  # 3-byte letters: a line of them costs about 500 bytes
SMALL = "the queue drains slowly under a lease"  # a small one-line topic after the wide one


def note(name, body, day, hour=10, extra=None, record_name=None, claimed=None):
    """A lesson written (mtime) on 2026-10-<day> at <hour>:00 that claims that day, unless ``claimed``. ``extra`` is
    body text below the description (the summary view never sees it); ``record_name`` replaces the name in the id."""
    row = t2.lesson(name, body, mtime=f"2026-10-{day:02d}T{hour:02d}:00:00.000000Z")
    row.update(time_utc=claimed or f"2026-10-{day:02d}T00:00:00.000000Z")
    if extra is not None or record_name is not None:
        text = row["text"] + ("\n\n" + extra + "\n" if extra is not None else "")
        digest = hashlib.sha256(text.encode()).hexdigest()[:12]
        row = dict(row, text=text, stored_text=text, record_id=f"lesson:{record_name or name}@{digest}")
    return row


def topic(head_body=D, opposite_at=None, **extra):
    """Four notes on one topic, each in its own hour (no flood): b1 day 5, b2 day 6, alpha day 9 (the head: the
    situation names it), b3 day 11; and the opposite x on day ``opposite_at`` when given, hidden by v2.1."""
    rows = [note("shard_alpha", head_body, 9, 10), note("shard_b1", D, 5, 11), note("shard_b2", D, 6, 12),
            note("shard_b3", D, 11, 14)]
    if opposite_at is not None:
        rows.append(note("shard_x", OPPOSITE, opposite_at, 13, **extra))
    return rows


def wide(count, days=None, head=None):
    """``count`` notes of 3-byte letters on one topic; ``days`` gives each its own day, ``head`` names one more word."""
    return [note(f"wide_{k}", f"{WIDE} quota window lease" + (" token queue" if days else f" {k}")
                 + (" alpha" if k == head else ""), days[k] if days else 12, 9 if days else 10 + k)
            for k in range(count)]


def build(tmp_path, rows):
    corpus_dir, vectors_dir = t2.write_inputs(tmp_path, sorted(rows, key=lambda row: row["record_id"]))
    memory_dir = tmp_path / "memory"
    rv.memory_mod.build(corpus_dir, vectors_dir, memory_dir)
    return memory_dir


def q4(memory_dir):
    return rv.q4_mod.Q4(rv.memory_mod.Memory(memory_dir), t2.FakeEncoder())


def both(tmp_path, rows, when=WHEN, base=True):
    """(the v2.1 listing, the v2.2 listing) of one memory for the situation, arm a; ``base`` adds the step-2 records
    (single records and a filler flood, two more topics on the card)."""
    memory = build(tmp_path, (t2.records() if base else []) + rows)
    old = r21.RememberV21(q4(memory), "v21a").shortlist(SITUATION, when, [SITUATION])
    new = r22.RememberV22(q4(memory), "v22a").shortlist(SITUATION, when, [SITUATION])
    assert old["status"] == new["status"] == "LISTED"
    return old, new


def refs(listed):
    return [line["ref"] for line in listed["lines"]]


def group_of(listed, ref):
    number = next(line["topic"]["group"] for line in listed["lines"] if line["ref"] == ref)
    return [line for line in listed["lines"] if line["topic"]["group"] == number]


def opposed(line):
    return [mark["ref"] for mark in line.get("may_oppose") or ()]


def hidden_marked(listed):
    return [line["ref"] for line in listed["lines"] if line.get("hidden_opposed")]


def text(listed):
    return r22.render_shortlist("C1", SITUATION, listed)


def test_an_opposite_hidden_in_the_heads_group_is_shown_beside_it_and_marked(tmp_path):
    """RCO2 R1 test 2 (F1): the opposite is neither the head, the oldest nor the newest of its group."""
    old, new = both(tmp_path, topic(opposite_at=7))
    assert "shard_x.md" not in refs(old) and old["telemetry"]["may_oppose_pairs"] == 0  # the defect, on v2.1
    lines = group_of(new, "shard_alpha.md")
    assert [line["ref"] for line in lines] == ["shard_b1.md", "shard_x.md", "shard_alpha.md", "shard_b3.md"]
    by_ref = {line["ref"]: line for line in lines}
    assert opposed(by_ref["shard_alpha.md"]) == ["shard_x.md"]
    assert opposed(by_ref["shard_x.md"]) == ["shard_alpha.md", "shard_b1.md", "shard_b3.md"]
    assert {line["topic"]["header"] for line in lines} == {group_of(old, "shard_alpha.md")[0]["topic"]["header"]}
    assert [ref for ref in refs(new) if ref != "shard_x.md"] == refs(old)  # every v2.1 line stays, in its order
    assert hidden_marked(new) == [] and "[MAY_OPPOSE shard_x.md reason=polarity_flip" in text(new)
    assert new["telemetry"]["conflict_extras"] == 1 and new["telemetry"]["skipped_groups"] == []


def test_a_group_without_an_opponent_keeps_exactly_its_v21_lines(tmp_path):
    """RCO2 R1 test 3, adapted: v2.2 keeps the fence's lines (no head-only groups), so nothing changes here."""
    old, new = both(tmp_path, topic())
    assert new["lines"] == old["lines"] and text(new) == r21.render_shortlist("C1", SITUATION, old)
    assert new["telemetry"]["conflict_extras"] == 0 and new["telemetry"]["hidden_marks"] == 0


@pytest.mark.parametrize("situation", ["the decoded guard checks a json string with an escaped marker in raw text",
                                       "watcher retry reasons narrowed again in pr review",
                                       "powershell convertto json escaping digest recompute edition"])
def test_v22_equals_v21_on_cards_with_nothing_hidden_or_skipped(tmp_path, situation):
    """RCO2 R1 test 8: the step-2 fixture memory (single records and a filler flood), both arms."""
    memory = build(tmp_path, t2.records())
    rewrite = "a json string escaped in raw text"
    for old_arm, new_arm, queries in (("v21a", "v22a", [situation]), ("v21ca", "v22ca", [situation, rewrite])):
        old = r21.RememberV21(q4(memory), old_arm).shortlist(situation, WHEN, queries)
        new = r22.RememberV22(q4(memory), new_arm).shortlist(situation, WHEN, queries)
        assert old["status"] == new["status"] == "LISTED" and new["lines"] == old["lines"]
        assert r22.render_shortlist("C1", situation, new) == r21.render_shortlist("C1", situation, old)


def test_packing_skips_a_group_that_does_not_fit_and_tries_the_next(tmp_path):
    """RCO2 R1 test 1 (F3): the second group (three lines of 3-byte letters) does not fit; later small ones do."""
    rows = topic() + wide(4, days=[10, 11, 12, 13], head=1) + [note("small_gold", SMALL, 8, 9)]
    old, new = both(tmp_path, rows)
    assert old["telemetry"]["stopped_by"] == "bytes" and refs(old) == ["shard_b1.md", "shard_alpha.md", "shard_b3.md"]
    assert refs(new) == refs(old) + ["small_gold.md", "lesson_json.md"]  # the wide group is skipped, not the rest
    assert new["telemetry"]["skipped_groups"] == [2]
    assert len(new["lines"]) <= rv.CAP and r22.rendered_bytes(new["lines"]) <= rv.CASE_BYTES


def forged(name, body, claimed):
    """A note written 2026-10-13T08 (one hour and one directory for all of them: a FLOOD) that claims ``claimed``."""
    return note(name, body, 13, 8, claimed=claimed)


def forged_rows(opposite):
    return [forged("shard_f0", D, "2019-01-01T00:00:00.000000Z"),
            forged("shard_f1", OPPOSITE if opposite else D, "2026-10-13T00:00:00.000000Z"),
            forged("shard_f2", D, "2026-10-13T00:00:00.000000Z")]


def test_a_forged_date_flood_keeps_the_fence_lines_byte_for_byte(tmp_path):
    """RCO2 R1 test 4: the forged-dates defense is v2.1's, unchanged."""
    old, new = both(tmp_path, topic() + forged_rows(opposite=False))
    assert "FLOOD 3 from path (memory dir)" in group_of(old, "shard_alpha.md")[0]["topic"]["header"]
    assert new["lines"] == old["lines"] and text(new) == r21.render_shortlist("C1", SITUATION, old)


def test_a_hidden_flood_opposite_is_one_marked_extra_and_the_fence_lines_stay(tmp_path):
    """RCO2 R1a test 10: the three fence lines stay byte-equal, plus exactly one opposite, marked FLOOD and MAY_OPPOSE,
    placed by the time it became available (the fence never trusts its claimed day). The memory holds this topic alone:
    an extra that opposes all three fence lines costs about 1.2 KB (its line and six MAY_OPPOSE marks)."""
    old, new = both(tmp_path, topic() + forged_rows(opposite=True), base=False)
    fence = [(line["ref"], line["date_marks"], line["topic"]["header"]) for line in group_of(old, "shard_alpha.md")]
    lines = group_of(new, "shard_alpha.md")
    assert [(line["ref"], line["date_marks"], line["topic"]["header"]) for line in lines[:3]] == fence
    assert [line["ref"] for line in lines] == ["shard_b1.md", "shard_alpha.md", "shard_b3.md", "shard_f1.md"]
    assert lines[3]["date_marks"] == ["FLOOD", "DATE_UNVERIFIED"] and "shard_alpha.md" in opposed(lines[3])


def test_one_extra_per_group_however_many_opponents_hide_in_it(tmp_path):
    """RCO2 R1a test 11, adapted: at most MAX_EXTRAS = 1 shown per group (the proposal allowed two beside a head-only
    line); every shown line then has a shown opponent, so none carries the hidden mark."""
    rows = topic(opposite_at=7) + [note("shard_y", OPPOSITE + " again", 8, 15), note("shard_z", "do not " + D, 10, 16)]
    old, new = both(tmp_path, rows)
    assert not {"shard_x.md", "shard_y.md", "shard_z.md"} & set(refs(old))
    lines = group_of(new, "shard_alpha.md")
    assert len(lines) == 4 and len({"shard_x.md", "shard_y.md", "shard_z.md"} & {line["ref"] for line in lines}) == 1
    assert hidden_marked(new) == [] and new["telemetry"]["conflict_extras"] == 1


def test_two_hidden_claimants_of_the_head_show_one_extra_with_room_for_more(tmp_path):
    """MAX_EXTRAS = 1 where a second extra would fit (no MAY_OPPOSE marks: a claimant costs only its line): one claimant
    is shown; the other stays behind the CLAIMED SUPERSEDED mark that already names both, and alpha is covered."""
    claim = "supersedes: shard_alpha.md"
    rows = topic() + [note("shard_c2", D, 10, 13, extra=claim), note("shard_c4", D, 10, 18, extra=claim)]
    old, new = both(tmp_path, rows)
    assert not {"shard_c2.md", "shard_c4.md"} & set(refs(old))
    head = {line["ref"]: line for line in new["lines"]}["shard_alpha.md"]
    assert head["supersede_claims"] == ["shard_c2.md", "shard_c4.md"]
    lines = group_of(new, "shard_alpha.md")
    assert [line["ref"] for line in lines] == ["shard_b1.md", "shard_alpha.md", "shard_c2.md", "shard_b3.md"]
    assert hidden_marked(new) == [] and new["telemetry"]["conflict_extras"] == 1
    assert r22.rendered_bytes(new["lines"]) + 400 <= rv.CASE_BYTES  # measured room: the other claimant's line would fit


def test_an_opposite_of_a_shown_line_other_than_the_head_is_found(tmp_path):
    """RCO2 R1a (fable-5's open point 225A7D63): x opposes the oldest and newest lines, not the reordered head."""
    old, new = both(tmp_path, topic(head_body=REORDERED, opposite_at=7))
    assert all(r21.opposition(REORDERED, OPPOSITE, "lesson", "lesson", cosine) is None for cosine in (0.5, 0.999))
    assert "shard_x.md" not in refs(old)
    lines = {line["ref"]: line for line in group_of(new, "shard_alpha.md")}
    assert list(lines) == ["shard_b1.md", "shard_x.md", "shard_alpha.md", "shard_b3.md"]
    assert opposed(lines["shard_b1.md"]) == ["shard_x.md"] and opposed(lines["shard_alpha.md"]) == []
    assert hidden_marked(new) == []


def test_an_opponent_that_does_not_fit_leaves_a_fixed_hidden_mark_and_never_drops_a_topic(tmp_path):
    """fable-5's room rule: topics first; the opposite would break CASE_BYTES, so each line it opposes says so."""
    old, new = both(tmp_path, topic(opposite_at=7) + wide(3))
    assert refs(new) == refs(old) and len({line["topic"]["group"] for line in old["lines"]}) == 4
    assert hidden_marked(new) == ["shard_b1.md", "shard_alpha.md", "shard_b3.md"]
    shown = text(new)
    assert shown.count(r22.HIDDEN_MARK) == 3 and shown.count(r22.HIDDEN_GLOSS) == 1 and "shard_x" not in shown
    assert r22.rendered_bytes(new["lines"]) <= rv.CASE_BYTES
    assert (new["telemetry"]["conflict_extras"], new["telemetry"]["hidden_marks"]) == (0, 3)


def test_a_guarded_opponent_is_passed_over_and_never_removes_its_group(tmp_path):
    """The proposal skipped a group whose conflict line the guard withholds; a planted guarded opposite could then
    remove the true lesson. v2.2 passes it over and marks the lines it opposes, in fixed words."""
    old, new = both(tmp_path, topic(opposite_at=7, record_name="x-" + GUARDED.lower()))
    assert refs(new) == refs(old)
    assert hidden_marked(new) == ["shard_b1.md", "shard_alpha.md", "shard_b3.md"]
    assert new["telemetry"]["conflict_withheld"] == 1 and new["telemetry"]["withheld_by_guard"] == 0
    assert not rv.guarded(new) and not rv.guarded(text(new)) and not rv.guarded(json.dumps(new))


def supersede_rows():
    """alpha's claimants: shard_c2 (its topic, hidden by v2.1), other_c3 (another topic, the newest claimant) and
    shard_c1 (its topic, stored first, written after the situation, so never a candidate)."""
    claim = "supersedes: shard_alpha.md"
    return topic() + [note("shard_c2", D, 10, 13, extra=claim),
                      note("other_c3", "pin the lockfile before every install of the build", 12, 9, extra=claim),
                      note("shard_c1", D, 13, 20, extra=claim)]


def test_a_supersede_claim_of_this_question_is_an_opponent_from_every_claimant(tmp_path):
    """RCO2 R1b test 12: the relation comes from this question's claims and every claimant counts, not only
    ``superseded_by`` (the newest, which is on another topic here)."""
    old, new = both(tmp_path, supersede_rows(), when=EARLY)
    head = {line["ref"]: line for line in new["lines"]}["shard_alpha.md"]
    assert head["supersede_claims"] == ["shard_c2.md", "other_c3.md"] and head["superseded_by"] == "other_c3.md"
    assert "shard_c2.md" not in refs(old)
    assert "shard_c2.md" in [line["ref"] for line in group_of(new, "shard_alpha.md")]
    assert "shard_c1" not in text(new) + json.dumps(new)


def test_the_extra_takes_its_place_in_the_fence_time_order(tmp_path):
    """A BACKDATED opposite claims 2026-10-01 but was written on 2026-10-13: it is placed by the time the fence trusts
    (when it became available), so it can never pose as the oldest line of its topic. This topic alone, for room."""
    old, new = both(tmp_path, topic() + [note("shard_bd", OPPOSITE, 13, 9, claimed="2026-10-01T00:00:00.000000Z")],
                    base=False)
    assert "shard_bd.md" not in refs(old)
    lines = group_of(new, "shard_alpha.md")
    assert [line["ref"] for line in lines] == ["shard_b1.md", "shard_alpha.md", "shard_b3.md", "shard_bd.md"]
    assert lines[-1]["date_marks"] == ["BACKDATED"]


def test_an_extra_the_fence_trusts_by_its_claimed_day_takes_that_days_place(tmp_path):
    """The other side of the fence time order: an opposite written on 2026-10-10 that claims 2026-09-20 is far
    (DATE_UNVERIFIED) but eligible, so the fence orders it by the day it claims, not by when it was written."""
    rows = topic() + [note("shard_b0", D, 1, 9, claimed="2026-09-01T00:00:00.000000Z"),
                      note("shard_x", OPPOSITE, 10, 15, claimed="2026-09-20T00:00:00.000000Z")]
    old, new = both(tmp_path, rows, base=False)
    assert refs(old) == ["shard_b0.md", "shard_alpha.md", "shard_b3.md"]
    lines = group_of(new, "shard_alpha.md")
    assert [line["ref"] for line in lines] == ["shard_b0.md", "shard_x.md", "shard_alpha.md", "shard_b3.md"]
    assert lines[1]["date_marks"] == ["DATE_UNVERIFIED"]


def test_a_trusted_opponent_is_shown_before_a_higher_ranked_flood_one(tmp_path):
    """Hidden opponents are tried in the fence-trust order: the flood opposite ranks higher (its name repeats two
    situation words) and is passed over for the trusted one, which then covers every fence line. This topic alone."""
    rows = topic(opposite_at=7) + [forged("shard_f0", D, "2019-01-01T00:00:00.000000Z"),
                                   forged("shard_queue_lease", OPPOSITE, "2026-10-13T00:00:00.000000Z"),
                                   forged("shard_f2", D, "2026-10-13T00:00:00.000000Z")]
    memory = build(tmp_path, rows)
    engine = r22.RememberV22(q4(memory), "v22a")
    index, _ = engine.applicable(SITUATION, WHEN)
    ranks = engine.ranks(SITUATION, index)
    rrf_k = engine.q4.rule["rrf_k"]
    fused = {engine.q4.ids[int(i)].split("@")[0]: sum(1.0 / (rrf_k + ranks[name][n]) for name in "wdb")
             for n, i in enumerate(index)}
    assert fused["lesson:shard_queue_lease"] > fused["lesson:shard_x"]  # the measured precondition
    old = r21.RememberV21(q4(memory), "v21a").shortlist(SITUATION, WHEN, [SITUATION])
    new = engine.shortlist(SITUATION, WHEN, [SITUATION])
    assert not {"shard_x.md", "shard_queue_lease.md"} & set(refs(old))
    lines = group_of(new, "shard_alpha.md")
    assert [line["ref"] for line in lines] == ["shard_b1.md", "shard_x.md", "shard_alpha.md", "shard_b3.md"]
    assert hidden_marked(new) == [] and new["telemetry"]["conflict_extras"] == 1


def test_cards_never_break_cap_or_bytes_and_marks_name_only_shown_lines(tmp_path):
    """RCO2 R1 test 6, adapted (no extra topics in v2.2): many opposites, a flood and wide lines in one memory."""
    rows = (topic(opposite_at=7) + [note("shard_y", OPPOSITE + " again", 8, 15)]
            + wide(4, days=[10, 11, 12, 13], head=1) + forged_rows(opposite=True) + [note("small_gold", SMALL, 8, 9)])
    memory = build(tmp_path, t2.records() + rows)
    for arm, queries in (("v22a", [SITUATION]), ("v22ca", [SITUATION, OPPOSITE])):
        engine = r22.RememberV22(q4(memory), arm)
        listed = engine.shortlist(SITUATION, WHEN, queries)
        assert listed["status"] == "LISTED" and len(listed["lines"]) <= rv.CAP
        assert r22.rendered_bytes(listed["lines"]) <= rv.CASE_BYTES
        shown = set(refs(listed))
        assert all(ref in shown for line in listed["lines"] for ref in opposed(line) + line["pair_with"])
        for number in {line["topic"]["group"] for line in listed["lines"]}:
            fence = engine._fence_groups[number - 1]["show"]
            ids = [line["doc_id"] for line in listed["lines"] if line["topic"]["group"] == number]
            assert [rid for rid in ids if rid in fence] == fence and len(ids) <= len(fence) + r22.MAX_EXTRAS


def test_the_arm_is_checked_and_an_answer_names_its_arm_version_and_marks(tmp_path):
    memory = build(tmp_path, t2.records() + topic(opposite_at=7) + wide(3))
    a, ca = r22.RememberV22(q4(memory), "v22a"), r22.RememberV22(q4(memory), "v22ca")
    assert a.shortlist(SITUATION, WHEN, [SITUATION, "a rewrite"])["reason"] == "queries_do_not_match_arm"
    assert ca.shortlist(SITUATION, WHEN, [SITUATION])["reason"] == "queries_do_not_match_arm"
    for arm in ("v21a", "v2c", "v3t"):
        with pytest.raises(r22.RememberV22Error):
            r22.RememberV22(q4(memory), arm)
    listed = a.shortlist(SITUATION, WHEN, [SITUATION])
    number = 1 + refs(listed).index("shard_alpha.md")
    answer = a.apply_picks(listed, {"picks": [number], "nothing_applies": False})
    assert answer["status"] == "ANSWER" and answer["arm"] == "v22a" and answer["version"] == r22.VERSION
    assert answer["results"][0]["hidden_opposed"] is True and set(rv.contract("C1", answer)["results"][0]) == \
        set(rv.RESULT_KEYS)
    assert a.apply_picks(listed, {"picks": [], "nothing_applies": True})["status"] == "ABSTAIN"
