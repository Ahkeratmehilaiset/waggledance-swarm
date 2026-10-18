"""Tests for the REMEMBER correction finder on synthetic texts (no memory, no e5, no real lessons). Written BEFORE
the code.

RCO2 round 8 (proposal in the RCO2 session audit folder, NOT run by RCO2; Grok consultation 8b06f31d) asks that a card
never show a lesson's head claim without the correction the lesson's own text writes later; round 10 keeps one attach
rule, provable only. Each test names the round-8 test or rule it adapts; where fable-5's design differs (the module
docstring of ``corrections.py``), the test says so.

run:C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/remember -q -p no:cacheprovider
"""

import datetime as dt
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import corrections as cm  # noqa: E402

AVAILABLE = dt.datetime(2026, 10, 14, 9, 0, tzinfo=dt.timezone.utc)  # the record's available time (its file mtime)
FRONT = "---\nname: shard_alpha\ndescription: retry the shard rebalance on any quota window error\n---\n\n"
NOTE = "the cap is 8, not 6"


def found(body, available=AVAILABLE, front=FRONT):
    return cm.corrections(front + body, available)


def one(body, **kw):
    out, malformed = found(body, **kw)
    assert len(out) == 1 and malformed == 0, (out, malformed)
    return out[0]


def none(body, **kw):
    out, malformed = found(body, **kw)
    return out == [] and malformed == 0


@pytest.mark.parametrize("body, day, dated_by", [
    (f"CORRECTED 10-12: {NOTE}", "2026-10-12", "lead"),
    (f"**CORRECTED 10-12:** {NOTE}", "2026-10-12", "lead"),
    (f"- WITHDRAWN 2026-10-11: {NOTE}", "2026-10-11", "lead"),
    (f"* **SUPERSEDED (10-12):** {NOTE}", "2026-10-12", "lead"),
    (f"CORRECTION 2026-10-12T08:30:00Z - {NOTE}", "2026-10-12", "lead"),
    (f"CORRECTION: {NOTE}", None, "none"),
    (f"Some context first.\nCORRECTED 10-12: {NOTE}", "2026-10-12", "lead"),
])
def test_a_line_start_lead_is_a_correction_of_the_head_claim(body, day, dated_by):
    """R8 detection, the lead: a line that starts (after a list mark and bold) with an upper-case lead keyword; its
    note is the correction's own words, without the keyword, its date and the separators after them."""
    c = one(body)
    assert (c["scope"], c["source"], c["day"], c["dated_by"], c["note"]) == ("head", "lead", day, dated_by, NOTE)


def test_the_offset_is_where_the_correction_starts_in_the_file():
    text = FRONT + f"Some context first.\nCORRECTED 10-12: {NOTE}"
    out, _ = cm.corrections(text, AVAILABLE)
    assert [c["offset"] for c in out] == [text.index("CORRECTED 10-12")]


@pytest.mark.parametrize("body", ["Corrected the shard map before the retry.", "corrected: the shard map",
                                  "CORRECTIONS to the shard map are listed below.",
                                  "The shard map was CORRECTED on 10-12 by the lane."])
def test_a_lead_is_an_upper_case_keyword_at_a_line_start_only(body):
    """R8's lead is case-sensitive and line-initial: a sentence that only uses the word is not a lead."""
    assert none(body)


@pytest.mark.parametrize("body", ["The model was wrong about the quota window; NEAR MISS for the lane.",
                                  "WRONG: never trust a cached lease. MISS: the lane retried twice.",
                                  "Lead caught it in review: the agent retried the shard.",
                                  "They withdrew the shard claim after the review."])
def test_a_lesson_about_mistakes_is_not_a_correction_of_itself(body):
    """R8 test 3: a strong phrase without self-deixis, or a bare WRONG / MISS / NEAR MISS, is a topic lesson."""
    assert none(body)


@pytest.mark.parametrize("body, scope, note", [
    ("Earlier I claimed the cap is 6; that was wrong, the cap is 8.", "line",
     "Earlier I claimed the cap is 6; that was wrong, the cap is 8."),
    ("Context first.\nI wrote that the cap is 6.\nThat was wrong; it is 8.", "line",
     "I wrote that the cap is 6. That was wrong; it is 8."),
    ("The head claim was wrong: the cap is 8.", "head", "The head claim was wrong: the cap is 8."),
    ("Lead caught it: the opening claim retracted, the cap is 8.", "head",
     "Lead caught it: the opening claim retracted, the cap is 8."),
])
def test_a_strong_phrase_beside_self_deixis_is_a_correction(body, scope, note):
    """R8 detection, the phrase: its note starts at the line of the first of the phrase and the self-deixis. fable-5's
    change: a phrase that names the head or opening claim corrects the head (R8 gives every phrase the line scope)."""
    c = one(body)
    assert (c["scope"], c["source"], c["note"]) == (scope, "phrase", note)


def test_the_frontmatter_is_never_read():
    """R8: the description is the head claim itself; a lesson that rewrote it has nothing to attach."""
    front = ("---\nname: shard_alpha\ndescription: this claim was wrong, the cap is 8\nCORRECTED: 10-12\n---\n\n")
    assert none("the cap is 8 when the queue drains", front=front)
    assert one(f"CORRECTED 10-12: {NOTE}", front=front)["note"] == NOTE


def test_an_unclosed_frontmatter_is_read_as_body():
    """A text that opens a frontmatter and never closes it has no frontmatter to skip: a correction is never lost."""
    c = one(f"CORRECTED 10-12: {NOTE}", front="---\nname: shard_alpha\n")
    assert (c["scope"], c["day"], c["note"]) == ("head", "2026-10-12", NOTE)


def test_line_ends_are_normalized():
    body = f"some text\n\nCORRECTED 10-12: the cap\nis 8, not 6\n\nEarlier I claimed 6; that was wrong."
    lf = found(body)
    assert len(lf[0]) == 2 and lf[0][0]["note"] == NOTE
    assert found(body.replace("\n", "\r\n"), front=FRONT.replace("\n", "\r\n")) == lf
    assert found(body.replace("\n", "\r"), front=FRONT.replace("\n", "\r")) == lf


@pytest.mark.parametrize("scope", ["head", "section", "line"])
def test_a_stamp_is_read_exactly(scope):
    """R8 write side: ``CORRECTION_STAMP: at=<ISO Z> scope=<head|section|line> note=<one line>``."""
    c = one(f"CORRECTION_STAMP: at=2026-10-12T08:30:00Z scope={scope} note=  {NOTE} ")
    assert (c["scope"], c["source"], c["day"], c["dated_by"], c["note"]) == (scope, "stamp", "2026-10-12", "stamp",
                                                                            NOTE)


def test_a_stamp_wins_over_legacy_text_in_its_paragraph():
    """R8 test 4: one correction, the stamp's."""
    c = one(f"CORRECTED 10-11: an older wording\nCORRECTION_STAMP: at=2026-10-12T08:30:00Z scope=line note={NOTE}")
    assert (c["source"], c["scope"], c["note"]) == ("stamp", "line", NOTE)


@pytest.mark.parametrize("stamp", ["CORRECTION_STAMP: at=2026-10-12 scope=head note=x",
                                   "CORRECTION_STAMP: at=2026-10-12T08:30:00Z scope=all note=x",
                                   "  CORRECTION_STAMP: at=2026-10-12T08:30:00Z scope=head note=x",
                                   "CORRECTION_STAMP: at=2026-10-12T08:30:00Z scope=head note=",
                                   "CORRECTION_STAMP: at=2026-10-12T08:30:00Z scope=head note=   ",
                                   "CORRECTION_STAMP: at=2026-02-30T08:30:00Z scope=head note=x",
                                   "CORRECTION_STAMP: at=2026-10-12T08:30:00z scope=head note=x",
                                   "CORRECTION_STAMP: at=2026-10-12T08:30:00Z scope=head note=" + "x" * 201])
def test_a_malformed_stamp_is_dropped_with_no_legacy_fallback(stamp):
    """R8 test 8: the paragraph of a malformed stamp yields nothing, its legacy lead included; it is counted. A lead
    in another paragraph is still read."""
    assert found(f"{stamp}\nCORRECTED 10-12: {NOTE}") == ([], 1)
    out, malformed = found(f"{stamp}\n\nCORRECTED 10-12: {NOTE}")
    assert [c["source"] for c in out] == ["lead"] and malformed == 1


def test_the_day_after_the_lead_keyword_is_the_corrections_own():
    """fable-5's change: R8 calls a paragraph with two dates ambiguous; the day right after the lead keyword is the
    correction's own day by the convention every lead uses, and any later date names what it corrects."""
    c = one("CORRECTED 10-12: the claim of 10-05 was wrong")
    assert (c["day"], c["dated_by"]) == ("2026-10-12", "lead")


@pytest.mark.parametrize("body, scope", [("CORRECTED: the claims of 10-05 and 10-07 were wrong", "head"),
                                         ("Earlier I claimed on 10-05 and on 2026-10-07 that the cap is 6; that was "
                                          "wrong.", "line")])
def test_two_dates_without_a_lead_date_keep_the_correction_undated(body, scope):
    """R8 test 5, changed by fable-5: R8 drops such a correction (fail closed for the clock), which hides it from
    the card, the very risk round 8 names; undated, it is still provable through the record's own time."""
    c = one(body)
    assert (c["scope"], c["day"], c["dated_by"]) == (scope, None, "ambiguous")


@pytest.mark.parametrize("body, day", [("CORRECTED: on 2026-10-11 the cap was found to be 8", "2026-10-11"),
                                       ("CORRECTED: on 10-11 the cap was found to be 8", "2026-10-11"),
                                       ("CORRECTED: since 12-24 the cap is 8", "2025-12-24"),
                                       ("CORRECTED: the cap is 8 (seen 10-11, again 10-11)", "2026-10-11")])
def test_one_date_in_the_corrections_text_dates_it(body, day):
    """R8's time rule: a full date, or MM-DD in the year of the record's available time, the year before when that
    day is later than the available day; one distinct day is one date."""
    c = one(body)
    assert (c["day"], c["dated_by"]) == (day, "single")


@pytest.mark.parametrize("body, available, day, dated_by", [
    ("CORRECTED 02-30: the cap is 8", AVAILABLE, None, "none"),
    ("CORRECTED 2026-10-20: the cap is 8", AVAILABLE, None, "none"),
    ("CORRECTED: the cap is 8 since 13-01 and 10-11", AVAILABLE, "2026-10-11", "single"),
    ("CORRECTED: on 10-11 the cap was found to be 8", None, None, "none"),
    ("CORRECTED: on 2026-10-11 the cap was found to be 8", None, "2026-10-11", "single"),
    ("CORRECTION_STAMP: at=2026-10-15T08:30:00Z scope=head note=the cap is 8", AVAILABLE, None, "none"),
])
def test_a_date_that_is_not_real_or_is_after_the_record_is_no_date(body, available, day, dated_by):
    """fable-5's rule: a day that is not a calendar day, or later than the day its record became available (the file
    holding it was not written yet), is no date; MM-DD has no year when the available time is unknown."""
    c = one(body, available=available)
    assert (c["day"], c["dated_by"]) == (day, dated_by)


def test_one_correction_per_paragraph_in_file_order():
    out, _ = found("CORRECTED 10-10: first\nCORRECTED 10-11: same paragraph\n\nsome text\n\nCORRECTED 10-12: second")
    assert [(c["day"], c["note"]) for c in out] == [("2026-10-10", "first CORRECTED 10-11: same paragraph"),
                                                    ("2026-10-12", "second")]
    assert out[0]["offset"] < out[1]["offset"]


def test_the_note_is_the_corrections_own_words_collapsed():
    assert one("CORRECTED 10-12: the cap\n   is 8,\tnot 6")["note"] == NOTE
    assert one("CORRECTED 10-12:")["note"] == ""


def c_(offset, scope):
    return {"offset": offset, "scope": scope}


@pytest.mark.parametrize("found_, pick, more", [
    ([c_(10, "head"), c_(50, "head")], 50, 1),
    ([c_(10, "head"), c_(50, "line")], 10, 1),
    ([c_(10, "line"), c_(50, "section"), c_(90, "line")], 90, 2),
    ([c_(10, "line")], 10, 0),
])
def test_selected_is_the_last_head_correction_else_the_last_and_counts_the_rest(found_, pick, more):
    """Round 10's superseding, fable-5's form: one mark per line; a later correction of the head supersedes an
    earlier one, a correction of the head is shown before one of a line, and the rest are counted."""
    chosen, rest = cm.selected(found_)
    assert (chosen["offset"], rest) == (pick, more)


def test_selected_of_nothing_is_nothing():
    assert cm.selected([]) == (None, 0)


@pytest.mark.parametrize("available, situation, expected", [
    (AVAILABLE, AVAILABLE + dt.timedelta(seconds=1), True),
    (AVAILABLE, AVAILABLE, True),
    (AVAILABLE + dt.timedelta(seconds=1), AVAILABLE, False),
    (None, AVAILABLE, False),
    (AVAILABLE, None, False),
])
def test_attachable_is_provable_only(available, situation, expected):
    """Round 10's one rule: a record's corrections attach when its text was available at or before the situation;
    the correction's own day is the writer's claim and is no argument; an unknown time proves nothing."""
    assert cm.attachable(available, situation) is expected
