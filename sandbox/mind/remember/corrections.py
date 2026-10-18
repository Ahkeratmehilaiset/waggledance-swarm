"""REMEMBER corrections: the later corrections a lesson writes into its own text, found by file, never retrieved.
Sandbox, advisory, never a gate. Pure text rules: no memory, no model, no clock but the times it is given.

Why (RCO2 round 8, measured read-only on the 934 lessons; proposal NOT run by RCO2): a card can show a lesson's head
claim (its description, cut to 150 characters) with no sign that the lesson's own body withdraws or corrects it.
Strong-verb correction lines were counted in 168 files (283 lines; 198 of them carry no date). Round 10 keeps one
attach rule, provable only. A correction is attached to its own record's line, so no retrieval view has to find it.

What is a correction of THIS lesson (RCO2's classifier from the Grok consultation 8b06f31d; fable-5's changes are
marked CHANGED):

* Line ends are normalized and the frontmatter is never read: the description is the head claim itself, and a lesson
  that rewrote it has nothing to attach. A text that opens a frontmatter and never closes it is all body (CHANGED: R8
  does not say; reading it keeps a correction from being lost). Paragraphs are split at blank lines; each paragraph
  yields at most one legacy correction.
* A paragraph with a ``CORRECTION_STAMP`` line is read from its stamps only. A stamp is exactly
  ``CORRECTION_STAMP: at=<YYYY-MM-DDTHH:MM:SSZ> scope=<head|section|line> note=<1..200 characters>`` with a real time
  and a note that is not blank. A malformed stamp is dropped and counted, and its paragraph's legacy text is never
  read in its place.
* Otherwise a LEAD corrects the head claim. A lead is a line that starts (after an optional list mark and bold) with
  the upper-case CORRECTED, CORRECTION, WITHDRAWN or SUPERSEDED. A strong PHRASE (was wrong, Lead caught it, withdrew,
  withdrawn, retracted) is a correction only beside SELF-deixis (this lesson/claim/note, the above/head/opening claim,
  I wrote, I claimed, earlier I, earlier this). Its scope is the line, or the head when the deixis names the head or
  opening claim (CHANGED: R8 gives every phrase the line scope). A bare WRONG, MISS or NEAR MISS is not a correction:
  many lessons are about mistakes.
* Its note is its own words, whitespace collapsed. For a lead, the keyword, its date and the separators after them
  are cut off; for a phrase, the note starts at the line of the first of the phrase and the self-deixis.
* Its day, a label only (it never decides attachment):
  * a date right after the lead keyword (CHANGED: R8 counts it with every other date);
  * else the one date in the correction's text, a full date or MM-DD in the year of the record's available time (the
    year before when that day is later);
  * else none. With two or more dates and no lead date, the correction is kept UNDATED (CHANGED: R8 drops it, which
    hides it from the card, the very risk round 8 names).
  * A day that is not a calendar day, or that is later than the day its record became available (the file holding it
    was not written yet), is no date (CHANGED).

What a card line shows (``selected``): one correction per line. It is the last correction of the head in the file,
else the last one; the count of the others rides with it (CHANGED: R8 lists every mark, newest first). The file order
decides "last" because 198 of the 283 measured lines have no date to sort by.

The attach rule (``attachable``, round 10): a record's corrections attach when its text, every correction in it, was
available at or before the situation time. A correction's own day is the writer's claim (the date fence trusts no
claimed day either), so it is no argument (CHANGED: R8 also attaches on ``at <= situation``).

Limits, stated: the rules are RCO2's measured regexes, and their precision is unmeasured (round 8 asks a non-author
to label 40 files first); a correction in a markdown heading or block quote is not a lead; a correction that lies
beyond the stored text is never seen. The step-2 corpus keeps the first 4000 characters of a file, up to any guarded
word, and RCO2 counted 83 files whose first correction marker lies beyond 4000; raising that limit is a corpus
decision, not this module's. The scope of a legacy lead is R8's convention, not verified.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Mapping, Sequence

LEAD = re.compile(r"^[ \t]*(?:[-*][ \t]+)?\**[ \t]*(?:CORRECTED|CORRECTION|WITHDRAWN|SUPERSEDED)\b"
                  r"(?:[ \t]*\(?(?:(\d{4})-)?(\d{2})-(\d{2})(?:T[0-9:.]+Z?)?\)?(?![\d-]))?[ \t:*.,;-]*", re.M)
PHRASE = re.compile(r"\b(?:was wrong|Lead caught it|withdrew|withdrawn|retracted)\b", re.I)
SELF = re.compile(r"\b(?:this (?:lesson|claim|note)|the (?:above|head|opening) claim|I (?:wrote|claimed)"
                  r"|earlier (?:I|this))\b", re.I)
HEAD_SELF = re.compile(r"\bthe (?:head|opening) claim\b", re.I)
STAMP_LINE = re.compile(r"^[ \t]*CORRECTION_STAMP", re.M)  # an attempt: it must then match STAMP exactly
STAMP = re.compile(r"CORRECTION_STAMP: at=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) scope=(head|section|line) "
                   r"note=(.{1,200})")
DATE = re.compile(r"(?<![\d-])(?:(\d{4})-)?(\d{2})-(\d{2})(?![\d-])")
FRONTMATTER = re.compile(r"---[ \t]*\n.*?\n---[ \t]*(?:\n|\Z)", re.S)
BLANK = re.compile(r"\n(?:[ \t]*\n)+")


def corrections(text: str, available: dt.datetime | None) -> tuple[list[dict[str, Any]], int]:
    """(the corrections of THIS lesson in ``text``, in file order; the number of malformed stamps). ``available`` is
    the time its record became available (its file mtime), or None when unknown. Each correction is {offset, scope,
    source, day, dated_by, note}: ``offset`` where it starts in the line-normalized text, ``scope`` head, section or
    line, ``source`` stamp, lead or phrase, ``day`` YYYY-MM-DD or None, ``dated_by`` stamp, lead, single, ambiguous or
    none."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    front = FRONTMATTER.match(text)
    found: list[dict[str, Any]] = []
    malformed = 0
    for offset, paragraph in _paragraphs(text, front.end() if front else 0):
        attempts = list(STAMP_LINE.finditer(paragraph))
        if attempts:
            for attempt in attempts:
                end = paragraph.find("\n", attempt.start())
                stamp = _stamp(paragraph[attempt.start():end if end >= 0 else None], available)
                if stamp is None:
                    malformed += 1
                    continue
                found.append(dict(stamp, offset=offset + attempt.start()))
            continue  # a stamped paragraph is read from its stamps only
        legacy = _legacy(paragraph, available)
        if legacy is not None:
            found.append(dict(legacy, offset=offset + legacy["offset"]))
    return found, malformed


def selected(found: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any] | None, int]:
    """(the one correction a card line shows, how many more the record has): the last correction of the head in the
    file, else the last one; (None, 0) when there is none."""
    if not found:
        return None, 0
    heads = [c for c in found if c["scope"] == "head"]
    return max(heads or found, key=lambda c: c["offset"]), len(found) - 1


def attachable(available: dt.datetime | None, situation: dt.datetime | None) -> bool:
    """Round 10's one rule, provable only: True when the record's text was available at or before the situation."""
    return available is not None and situation is not None and available <= situation


def _paragraphs(text: str, start: int) -> list[tuple[int, str]]:
    """(offset, paragraph) of every paragraph of ``text[start:]`` that is not blank: the runs between blank lines."""
    out, at = [], start
    for gap in BLANK.finditer(text, start):
        out.append((at, text[at:gap.start()]))
        at = gap.end()
    out.append((at, text[at:]))
    return [(offset, paragraph) for offset, paragraph in out if paragraph.strip()]


def _stamp(line: str, available: dt.datetime | None) -> dict[str, Any] | None:
    """The correction of one stamp line, or None when it is malformed."""
    match = STAMP.fullmatch(line)
    if match is None or not match[3].strip():
        return None
    try:
        at = dt.datetime.strptime(match[1], "%Y-%m-%dT%H:%M:%SZ").date()
    except ValueError:
        return None
    day = at if available is None or at <= available.date() else None
    return {"scope": match[2], "source": "stamp", "day": day.isoformat() if day else None,
            "dated_by": "stamp" if day else "none", "note": " ".join(match[3].split())}


def _legacy(paragraph: str, available: dt.datetime | None) -> dict[str, Any] | None:
    """The lead or phrase correction of one paragraph without stamps, or None. Its ``offset`` is in the paragraph."""
    lead = LEAD.search(paragraph)
    if lead is not None:
        own = _day(lead[1], lead[2], lead[3], available) if lead[2] else None
        day, dated_by = (own, "lead") if own else _dated(paragraph[lead.start():], available)
        return {"offset": lead.start(), "scope": "head", "source": "lead", "day": day, "dated_by": dated_by,
                "note": " ".join(paragraph[lead.end():].split())}
    phrase, deixis = PHRASE.search(paragraph), SELF.search(paragraph)
    if phrase is None or deixis is None:
        return None
    start = paragraph.rfind("\n", 0, min(phrase.start(), deixis.start())) + 1
    day, dated_by = _dated(paragraph[start:], available)
    return {"offset": start, "scope": "head" if HEAD_SELF.search(paragraph) else "line", "source": "phrase",
            "day": day, "dated_by": dated_by, "note": " ".join(paragraph[start:].split())}


def _dated(text: str, available: dt.datetime | None) -> tuple[str | None, str]:
    """(day, dated_by) from the real days in a correction's text: one distinct day dates it; more are ambiguous."""
    days = {day for day in (_day(*match.groups(), available) for match in DATE.finditer(text)) if day}
    if len(days) == 1:
        return days.pop(), "single"
    return None, "ambiguous" if days else "none"


def _day(year: str | None, month: str, day: str, available: dt.datetime | None) -> str | None:
    """A real day as YYYY-MM-DD, or None: MM-DD takes the year of ``available`` (the year before when that day is
    later); a day that is not a calendar day, or later than the day ``available`` falls on, is no day."""
    m, d = int(month), int(day)
    if year is None:
        if available is None:
            return None
        y = available.year if (m, d) <= (available.month, available.day) else available.year - 1
    else:
        y = int(year)
    try:
        value = dt.date(y, m, d)
    except ValueError:
        return None
    return None if available is not None and value > available.date() else value.isoformat()
