"""REMEMBER v2.3 dev: v2.2 with every lesson line carrying its own later correction. Sandbox, advisory, never a gate.

Why (RCO2 round 8, measured read-only on the 934 lessons; round 10 ranks it first): a card can show a lesson's head
claim with no sign that the lesson's own body withdraws or corrects it. Ranking cannot fix that: the correction has
to be shown WITH the claim, not found instead of it. So it is attached by file: a line is born with the corrections
of its own record's stored text (the first 4000 characters of the file, up to any guarded word). RCO2 found the first
correction marker inside the first 4000 characters in 197 of 280 marker files; the other 83 stay unseen (a stated
limit). What counts as a correction, its day and the one attach rule are in ``corrections.py``.

What v2.3 changes; ``remember_v2.py``, ``remember_v21.py``, ``remember_v3.py`` and ``remember_v22.py`` are not edited:

* A lesson line carries ``[CORRECTED <day|undated>[ HEAD][ (+n more)]: <note>]`` right before its summary. It shows
  the last correction of the head in the file, else the last one, and how many more there are. The summary itself is
  never shortened: it is the claim that matched, and MAY_OPPOSE reads it (CHANGED from R8, which shortens it first).
* A note is the writer's text. Its whitespace is collapsed and its brackets are shown as parentheses, so a note can
  never pose as a mark. A note with a guarded word is replaced by fixed words, so the guard never withholds the claim
  it corrects and never lets the word through.
* Packing is v2.2's: topics first, skip not break, then the hidden opponents, every trial measured as the joiner reads
  it. The marks count in that measure. When a card does not fit, every note on it is shortened to 40 characters, then
  to none, before v2.2 skips the group; the mark alone still says the claim was corrected, and when (CHANGED: R8 drops
  the group after the 40-character stage). A line is never shown without its mark (R8), and a topic is never dropped
  for a longer note.
* A correction is no opposition edge (R8): MAY_OPPOSE, the supersede claims and the hidden opponents stay v2.2's.
* Round 10's attach rule (``corrections.attachable``): every shown record is a candidate, and rule v1 admitted its
  text, every correction in it, as available before the situation. If the rule ever fails for a record (only a time
  that does not parse can do that), its line is withheld and counted: never shown without its correction, and never
  with a hint of a later edit. An event's text is never read for corrections; a lesson corrects itself.

Not adopted from the proposal, and why (fable-5 decides; each choice has a test):

* Every correction as its own mark, newest first: on a 2 KB card of up to 8 lines one mark per line bounds the cost
  (one note of at most 100 characters a line), and ``(+n more)`` keeps the count honest.
* Separate LIVE and REPLAY attach rules: rule v1 already admits a text only when it existed before the situation,
  so one rule serves both, and a replay can never see a correction its record did not hold then.

Limits, stated: the finder's precision is unmeasured (a non-author labelled sample comes first, round 8); a
correction past the stored text (the first 4000 characters of a file in the step-2 corpus) is never seen; a lead's
HEAD scope is R8's convention, and a note shows only the correction's first words; extras, marks and notes compete
for the room the topics leave, so a crowded card shows marks without notes; one engine answers one question at a
time (its claims, candidates, fence groups and situation time live on the instance, as in v2, v2.1 and v2.2); there
is no v2.3 runner yet, so a run needs its own runner, manifests and freeze first.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import corrections as corrections_mod  # noqa: E402
import remember_v22 as v22  # noqa: E402  (it loads remember_v21, remember_v3, remember_v2 and the step-2 modules once)

v21 = v22.v21
v2 = v22.v2
VERSION = "remember-v2.3-dev.0"
ARMS = ("v23a", "v23ca")  # v23a: the situation only; v23ca: the situation and the joiner's rewrite
STAGES = (100, 40, 0)  # note characters per packing stage: full, short, none (the mark alone)
WITHHELD_NOTE = "(note withheld by the guard)"
CORRECTION_GLOSS = ("CORRECTED = this lesson's own text later corrects or withdraws a claim of it (HEAD: the claim "
                    "shown; +n more: other corrections in it); the day is the writer's; not a verdict.")


class RememberV23Error(ValueError):
    """Fixed messages that never echo input content."""


def correction_mark(line: Mapping[str, Any]) -> str:
    """`` [CORRECTED <day>[ HEAD][ (+n more)][: <note>]]`` at the line's packing stage, or nothing."""
    correction = line.get("correction")
    if not correction:
        return ""
    chars = STAGES[correction["stage"]]
    note = v2.one_line(correction["note"], chars) if chars and correction["note"] else ""
    if note and (v2.guarded(note) or v2.corpus_mod.ws._has_marker(note)):
        note = WITHHELD_NOTE  # the note was guarded when the line was born; a cut is checked again anyway
    head = " HEAD" if correction["head"] else ""
    more = f" (+{correction['more']} more)" if correction["more"] else ""
    return f" [CORRECTED {correction['day']}{head}{more}" + (f": {note}" if note else "") + "]"


def line_text(n: int, line: Mapping[str, Any]) -> str:
    """v2.2's line with the correction mark after its other marks, right before the summary it corrects."""
    text = v22.line_text(n, line)
    mark = correction_mark(line)
    if not mark:
        return text
    tail = ": " + line["summary"]
    if not text.endswith(tail):
        raise RememberV23Error("a v2.2 line does not end with its summary")
    return text[:-len(tail)] + mark + tail


def rendered_bytes(lines: Sequence[Mapping[str, Any]]) -> int:
    return sum(len(line_text(n, line).encode("utf-8")) + 1 for n, line in enumerate(lines, 1))


def render_shortlist(case_id: str, situation: str, listed: Mapping[str, Any]) -> str:
    """What the joining agent reads: v2.2's case text with v2.3 lines, and each gloss once when a line needs it."""
    lines = [f"CASE {case_id}", f"SITUATION: {v2.situation_text(situation)}"]
    if listed["status"] != "LISTED":
        lines.append(f"SHORTLIST: none ({listed['reason']})")
    if any(line.get("may_oppose") for line in listed["lines"]):
        lines.append(v21.MARK_GLOSS)
    if any(line.get("hidden_opposed") for line in listed["lines"]):
        lines.append(v22.HIDDEN_GLOSS)
    if any(line.get("date_marks") or (line.get("topic") or {}).get("untrusted") for line in listed["lines"]):
        lines.append(v21.FENCE_GLOSS)
    if any(line.get("correction") for line in listed["lines"]):
        lines.append(CORRECTION_GLOSS)
    lines += [line_text(n, line) for n, line in enumerate(listed["lines"], 1)]
    text = "\n".join(lines) + "\n"
    if v2.corpus_mod.ws._has_marker(text):
        raise RememberV23Error("a guarded word is in a shortlist; nothing is written")
    return text


class RememberV23(v22.RememberV22):
    """v2.2's arms with every lesson line carrying its own later correction (module docstring); every answer names its
    arm and this version."""

    def __init__(self, q4: Any, arm: str):
        if arm not in ARMS:
            raise RememberV23Error("unknown arm: v2.3 has v23a and v23ca only (a code-only answer is never shown)")
        super().__init__(q4, {"v23a": "v22a", "v23ca": "v22ca"}[arm])  # v2.2's own arm check knows only its names
        self.arm = arm
        self._found: dict[int, tuple[list[dict[str, Any]], int]] = {}  # a record's corrections, read once
        self._situation: Any = None
        self._unproven = 0

    def shortlist(self, situation: str, situation_time_utc: str, queries: Sequence[str]) -> dict[str, Any]:
        expected = 1 if self.arm == "v23a" else 2
        if not isinstance(queries, (list, tuple)) or len(queries) != expected or queries[0] != situation:
            return {"status": "REFUSED", "reason": "queries_do_not_match_arm", "lines": []}
        self._fence_ranks, self._fence_groups = [], None
        try:
            return self._listed(situation, situation_time_utc, queries)
        finally:
            self._fence_ranks = None

    def applicable(self, situation: str, situation_time_utc: str):
        """v2's rule v1, unchanged; it also keeps the situation time that the attach rule reads."""
        self._situation = None
        index, telemetry = super().applicable(situation, situation_time_utc)
        if index is not None:
            self._situation = v2.corpus_mod.parse_utc(situation_time_utc)
        return index, telemetry

    def _result(self, position: int, fused: float, ranks: Mapping[str, int], summary: bool = True) -> dict[str, Any]:
        """v2's line or result, born with its record's correction: None for an event or a lesson without one."""
        base = super()._result(position, fused, ranks, summary)
        base["correction"] = self._correction(position) if base["kind"] == "lesson" else None
        return base

    def _correction(self, position: int) -> dict[str, Any] | None:
        """The correction a lesson line shows, with its note made safe to show; {"unproven": True} when the attach rule
        cannot prove it prior to the situation (``_built`` withholds that line)."""
        if position not in self._found:
            self._found[position] = corrections_mod.corrections(self.q4.entries[position]["text"],
                                                                self._available(position))
        found, _ = self._found[position]
        if not found:
            return None
        if not corrections_mod.attachable(self._available(position), self._situation):
            return {"unproven": True}
        chosen, more = corrections_mod.selected(found)
        note = chosen["note"].replace("[", "(").replace("]", ")")
        if v2.guarded(note) or v2.corpus_mod.ws._has_marker(note):
            note = WITHHELD_NOTE
        return {"day": chosen["day"] or "undated", "head": chosen["scope"] == "head", "more": more, "note": note,
                "stage": 0, "unproven": False}

    def _listed(self, situation: str, situation_time_utc: str, queries: Sequence[str]) -> dict[str, Any]:
        """v2.2's listing through this class's lines and card, then the guard over the lines as v2.3 renders them and
        the correction telemetry."""
        self._unproven = 0
        listed = super()._listed(situation, situation_time_utc, queries)
        if "telemetry" not in listed:
            return listed
        card = listed["lines"]
        for n, line in enumerate(card, 1):
            if v2.corpus_mod.ws._has_marker(line_text(n, line)) or v2.guarded(line):
                return {"status": "REFUSED", "reason": "output_guarded", "lines": []}  # fixed words: never expected
        marks = [line["correction"] for line in card if line["correction"]]
        telemetry = dict(listed["telemetry"], corrections_shown=len(marks),
                         corrections_more=sum(mark["more"] for mark in marks),
                         correction_stage=marks[0]["stage"] if marks else None, withheld_unproven=self._unproven,
                         malformed_stamps=sum(self._found.get(line["position"], ((), 0))[1] for line in card),
                         line_bytes=rendered_bytes(card))
        return dict(listed, telemetry=telemetry)

    def _built(self, context: tuple, number: int, positions: Sequence[int]) -> tuple[list[dict[str, Any]], int]:
        """v2.2's lines, less every line whose correction the attach rule cannot prove (withheld and counted)."""
        lines, withheld = super()._built(context, number, positions)
        kept = [line for line in lines if not (line["correction"] or {}).get("unproven")]
        self._unproven += len(lines) - len(kept)
        return kept, withheld

    def _card(self, lines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int] | None:
        """v2.2's card of ``lines`` measured with their marks, at the first note stage that fits: (card, pairs), or
        None when even the marks alone would break ``CAP`` or ``CASE_BYTES``."""
        if len(lines) > v2.CAP:
            return None
        card, pairs = self.opposed(self.fenced(self._marked([dict(line) for line in lines])))
        stages = range(len(STAGES)) if any(line["correction"] for line in card) else (0,)
        for stage in stages:
            staged = [dict(line, correction=dict(line["correction"], stage=stage)) if line["correction"] else line
                      for line in card]
            if rendered_bytes(staged) <= v2.CASE_BYTES:
                return staged, pairs
        return None

    def _answer(self, status, reason, results, telemetry, started) -> dict[str, Any]:
        out = super()._answer(status, reason, results, telemetry, started)
        out["version"] = VERSION
        return out
