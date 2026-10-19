"""REMEMBER v2.2 dev: v2.1 with no opposite hidden inside a topic group and packing that skips instead of breaking.
Sandbox, advisory, never a gate.

Why (RCO2 rounds 1, 1a and 1b, code-verified at 61044a85; Lead records both as known limits of the selected Q4
revision 6cf81dbb, which this module does not change):

* F1. A topic group holds every applicable record at least ``TOPIC_COSINE`` alike to its head, and v2's calibration
  says a written opposite of a real lesson is that alike in 23/24 cases, so an opposite usually lands INSIDE the head's
  group. The group shows its head, its earliest and its latest record (the date fence keeps those three slots), and
  MAY_OPPOSE compares only shown lines: an opposite that is none of the three is hidden and never marked.
* F3. Packing stops at the first group that does not fit, so a later small group is never tried.

What v2.2 changes; ``remember_v2.py``, ``remember_v21.py`` and ``remember_v3.py`` are not edited:

* Topics first, skip, not break (RCO2 V22-2). Groups are packed in fused order with exactly v2.1's lines, and every
  trial is measured as the joiner reads it (the fence's TOPIC text, date marks, PAIR, CLAIMED SUPERSEDED and
  MAY_OPPOSE). A group that does not fit is skipped whole and the next one is tried, so v2.1's trailing-group drop is
  never needed.
* Hidden opponents, in the room the topics leave (RCO2 V22-1, 1a and 1b, adapted). Two records are OPPONENTS when one
  claims to supersede the other in this question (every claimant among its own candidates: RCO1 round 7, RCO2 1b) or
  MAY_OPPOSE pairs them as ``opposed`` does. A shown line is COVERED when an opponent of it is shown in its group. For
  each listed group in order, the first uncovered line that has an opponent hidden in its group (the head first, then
  the card order) gets that opponent shown beside it, at most ``MAX_EXTRAS`` per group: the hidden opponents the fence
  trusts come first, then the higher fused score, then the id; one whose line the guard withholds is passed over; one
  that would break ``CAP`` or ``CASE_BYTES`` is not shown, and the next is tried. The extra takes its place in the
  fence's time order (the day the fence trusts, else the time it became available), so a backdated or flooded opposite
  can never pose as the oldest line of its topic. Then every shown line that is still uncovered while an opponent of
  it is hidden carries ``[MAY_OPPOSE hidden]``, room permitting: fixed words, so a mark can never show a hidden
  record's text or name. A topic is never dropped to make room for either.

Not adopted from the proposals, and why (fable-5 decides; each choice has a test):

* Head-only groups (V22-1's capacity part): the earliest and latest lines are v2's structural defense against an
  opposite flood (RCO1 conflict probe v1.1) whenever MAY_OPPOSE misses an opposite (its known misses, stated in
  ``remember_v21.py``); RCO2's own ceiling for the gain is one more gold on the used DEV sets. v2.2 keeps every v2.1
  line and adds; it never trades a line for room.
* Skipping a group whose conflict line the guard withholds (V22-2's second clause): a planted guarded opposite could
  then remove the true lesson from the card. The withheld opponent is passed over and the lines it opposes keep the
  hidden mark.
* The bounded closure over extras (1a) and two extras per head-only group: with every v2.1 line kept, an extra always
  has a shown opponent (its anchor), so a closure step adds nothing; one extra per group bounds the cost at
  ``GROUP_LINES + 1`` lines, R1a's own bound for a fence-kept group.
* Two extra topics from depth 5 (V22-3): more text for the joiner on negative cases; it needs a negatives
  measurement by a non-author first (RCO2 round 10 agrees).

Limits, stated: the selection is only as good as MAY_OPPOSE (its known misses stay misses, and an opposite it misses
is still hidden and unmarked); opponents are looked for in a line's own topic group only; a supersede claim stays a
writer-controlled claim; extras and marks compete for the room the topics leave, and an extra that opposes all three
fence lines costs about 1.1 KB of the 2 KB card (its line and six MAY_OPPOSE marks, measured on the synthetic tests),
so on a full card the hidden mark is the common outcome (a shorter MAY_OPPOSE form would change every marked card, so
it needs its own measurement); one engine answers one question at a time (its claims, candidates and fence groups live
on the instance, as in v2 and v2.1); there is no v2.2 runner yet, so a run needs its own runner, manifests and freeze
first.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import remember_v21 as v21  # noqa: E402  (it loads remember_v3, remember_v2 and the step-2 modules once)

v2 = v21.v2
VERSION = "remember-v2.2-dev.0"
ARMS = ("v22a", "v22ca")  # v22a: the situation only; v22ca: the situation and the joiner's rewrite
MAX_EXTRAS = 1  # hidden opponents shown per group, so a group costs at most GROUP_LINES + 1 lines (R1a's fence bound)
HIDDEN_MARK = " [MAY_OPPOSE hidden]"
HIDDEN_GLOSS = ("MAY_OPPOSE hidden = a record of this line's topic that is not shown may say the reverse of it; "
                "not a verdict.")
UNTRUSTED = frozenset(("FLOOD", "CLAIMED_AFTER_AVAILABLE", "BACKDATED"))  # the date marks of a day the fence refused


class RememberV22Error(ValueError):
    """Fixed messages that never echo input content."""


def line_text(n: int, line: Mapping[str, Any]) -> str:
    """v2.1's line with ``[MAY_OPPOSE hidden]`` after its marks when an opponent of the line is hidden in its topic."""
    text = v21.line_text(n, line)
    if not line.get("hidden_opposed"):
        return text
    tail = ": " + line["summary"]
    if not text.endswith(tail):
        raise RememberV22Error("a v2.1 line does not end with its summary")
    return text[:-len(tail)] + HIDDEN_MARK + tail


def rendered_bytes(lines: Sequence[Mapping[str, Any]]) -> int:
    return sum(len(line_text(n, line).encode("utf-8")) + 1 for n, line in enumerate(lines, 1))


def render_shortlist(case_id: str, situation: str, listed: Mapping[str, Any]) -> str:
    """What the joining agent reads: v2.1's case text with v2.2 lines, and each gloss once when a line needs it."""
    lines = [f"CASE {case_id}", f"SITUATION: {v2.situation_text(situation)}"]
    if listed["status"] != "LISTED":
        lines.append(f"SHORTLIST: none ({listed['reason']})")
    if any(line.get("may_oppose") for line in listed["lines"]):
        lines.append(v21.MARK_GLOSS)
    if any(line.get("hidden_opposed") for line in listed["lines"]):
        lines.append(HIDDEN_GLOSS)
    if any(line.get("date_marks") or (line.get("topic") or {}).get("untrusted") for line in listed["lines"]):
        lines.append(v21.FENCE_GLOSS)
    lines += [line_text(n, line) for n, line in enumerate(listed["lines"], 1)]
    text = "\n".join(lines) + "\n"
    if v2.corpus_mod.ws._has_marker(text):
        raise RememberV22Error("a guarded word is in a shortlist; nothing is written")
    return text


class RememberV22(v21.RememberV21):
    """v2.1's arms on the v3t ranking, with every hidden opponent of a shown line disclosed and skip-not-break
    packing (module docstring); every answer names its arm and this version."""

    def __init__(self, q4: Any, arm: str):
        if arm not in ARMS:
            raise RememberV22Error("unknown arm: v2.2 has v22a and v22ca only (a code-only answer is never shown)")
        super(v21.RememberV21, self).__init__(q4)  # v3's engine; v2.1's own arm check knows only its arm names
        self.arm = arm
        self._fence_ranks: list | None = None
        self._fence_groups: list | None = None

    def shortlist(self, situation: str, situation_time_utc: str, queries: Sequence[str]) -> dict[str, Any]:
        expected = 1 if self.arm == "v22a" else 2
        if not isinstance(queries, (list, tuple)) or len(queries) != expected or queries[0] != situation:
            return {"status": "REFUSED", "reason": "queries_do_not_match_arm", "lines": []}
        self._fence_ranks, self._fence_groups = [], None
        try:
            return self._listed(situation, situation_time_utc, queries)
        finally:
            self._fence_ranks = None

    def _listed(self, situation: str, situation_time_utc: str, queries: Sequence[str]) -> dict[str, Any]:
        """v2's pool and v2.1's fenced groups (``remember_v2.py`` ``shortlist`` up to ``topic_groups``, verbatim), then
        the topics, skip not break, then their hidden opponents."""
        index, telemetry = self.applicable(situation, situation_time_utc)
        if index is None:
            return {"status": "REFUSED", "reason": telemetry, "lines": []}
        if any(not isinstance(text, str) or not text.strip() for text in queries) or not queries:
            return {"status": "REFUSED", "reason": "query_empty", "lines": []}
        if v2.guarded(list(queries)):
            return {"status": "REFUSED", "reason": "query_guarded", "lines": []}
        if not len(index):
            return {"status": "EMPTY", "reason": "empty_cell", "lines": [], "telemetry": telemetry}
        vectors = self.q4.d[index]
        fused = np.zeros(len(index))
        member = np.zeros(len(index), dtype=bool)
        all_ranks = []
        for text in queries:
            ranks = self.ranks(text, index)
            all_ranks.append(ranks)
            for name in ("w", "d", "b"):
                fused += 1.0 / (self.q4.rule["rrf_k"] + ranks[name])
                member[v2.top_topics(ranks[name], vectors)] = True
        pool = sorted(np.flatnonzero(member), key=lambda i: (-fused[i], self.q4.ids[index[i]]))
        groups = self.topic_groups(index, pool, vectors)
        context = (index, groups, fused, all_ranks)
        lines: list[dict[str, Any]] = []
        card, pairs, withheld, skipped = [], 0, 0, []
        for number, group in enumerate(groups, 1):
            batch, held = self._built(context, number, group["show"])
            withheld += held
            fitted = self._card(lines + batch)
            if fitted is None:
                skipped.append(number)
                continue  # V22-2: skip, not break; a group is listed whole or not at all
            lines, (card, pairs) = lines + batch, fitted
        card, pairs, counts = self._disclosed(context, lines, card, pairs)
        for n, line in enumerate(card, 1):
            if v2.corpus_mod.ws._has_marker(line_text(n, line)) or v2.guarded(line):
                return {"status": "REFUSED", "reason": "output_guarded", "lines": []}  # fixed marks: never expected
        telemetry = dict(telemetry, union=int(member.sum()), groups=len(groups),
                         grouped_records=int(sum(len(group["members"]) for group in groups)),
                         withheld_by_guard=withheld, skipped_groups=skipped, **counts, may_oppose_pairs=pairs,
                         line_bytes=rendered_bytes(card))  # D12: no clock reading
        return {"status": "LISTED" if card else "EMPTY", "reason": None if card else "nothing_listable",
                "lines": card, "telemetry": telemetry}

    def _built(self, context: tuple, number: int, positions: Sequence[int]) -> tuple[list[dict[str, Any]], int]:
        """v2's lines of group ``number`` for ``positions`` (its shown records, or one hidden opponent), each guarded as
        v2 guards it (``remember_v2.py`` ``shortlist``): (lines, withheld)."""
        index, groups, fused, all_ranks = context
        group = groups[number - 1]
        members = group["members"]
        days = (self.q4.entries[index[members[0]]]["row"]["time_utc"][:10],
                self.q4.entries[index[members[-1]]]["row"]["time_utc"][:10])
        lines, withheld = [], 0
        for i in positions:
            line = self._result(int(index[i]), float(fused[i]),
                                {f"{name}{qn}": int(ranks[name][i]) for qn, ranks in enumerate(all_ranks)
                                 for name in ("w", "d", "b")})
            line["topic"] = {"group": number, "records": len(members), "first_day": days[0],
                             "last_day": days[1], "head": i == group["head"]}
            if v2.guarded(line) or v2.corpus_mod.ws._has_marker(v2.line_text(1, line)):
                withheld += 1
                continue
            lines.append(line)
        return lines, withheld

    def _card(self, lines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int] | None:
        """v2.1's marks over copies of ``lines``, as the joiner reads them: (card, MAY_OPPOSE pairs), or None when the
        card would break ``CAP`` or ``CASE_BYTES``."""
        if len(lines) > v2.CAP:
            return None
        card, pairs = self.opposed(self.fenced(self._marked([dict(line) for line in lines])))
        return None if rendered_bytes(card) > v2.CASE_BYTES else (card, pairs)

    def _disclosed(self, context: tuple, lines: list[dict[str, Any]], card: list[dict[str, Any]], pairs: int):
        """Each listed group's hidden opponents, groups in card order, in the room the topics leave: at most
        ``MAX_EXTRAS`` shown per group, then the hidden mark on every shown line still uncovered. A topic is never
        removed for either. (card, pairs, counts)."""
        counts = {"conflict_extras": 0, "conflict_withheld": 0, "hidden_marks": 0, "hidden_unmarked": 0}
        numbers = list(dict.fromkeys(line["topic"]["group"] for line in lines))
        for number in numbers:
            hidden = self._hidden(context, number, lines)
            extras, tried = 0, set()
            for _, opponents in self._uncovered(lines, number, hidden):
                for j in opponents:
                    if extras == MAX_EXTRAS or j in tried:
                        continue
                    tried.add(j)
                    batch, held = self._built(context, number, [j])
                    counts["conflict_withheld"] += held
                    trial = self._placed(number, lines, batch[0]) if batch else None
                    fitted = self._card(trial) if trial is not None else None
                    if fitted is not None:
                        lines, (card, pairs) = trial, fitted
                        del hidden[j]
                        extras += 1
            counts["conflict_extras"] += extras
            for anchor, _ in self._uncovered(lines, number, hidden):
                trial = [dict(line, hidden_opposed=True) if line is anchor else line for line in lines]
                fitted = self._card(trial)
                if fitted is None:
                    counts["hidden_unmarked"] += 1
                    continue
                lines, (card, pairs) = trial, fitted
                counts["hidden_marks"] += 1
        return card, pairs, counts

    def _hidden(self, context: tuple, number: int, lines: Sequence[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
        """{member: its line} for every member of group ``number`` that is not on the card and that the fence keeps (an
        exact copy below the flood size is not kept), in the order an opponent is shown: the ones the fence trusts
        first, then the higher fused score, then the id."""
        index, groups, fused, _ = context
        marks = self._fence_groups[number - 1]["marks"]
        shown = {line["position"] for line in lines}
        hidden = [j for j in groups[number - 1]["members"]
                  if int(index[j]) not in shown and self.q4.ids[int(index[j])] in marks]
        hidden.sort(key=lambda j: (bool(UNTRUSTED & set(marks[self.q4.ids[int(index[j])]])), -fused[j],
                                   self.q4.ids[int(index[j])]))
        return {j: self._result(int(index[j]), float(fused[j]), {}) for j in hidden}

    def _uncovered(self, lines: Sequence[Mapping[str, Any]], number: int, hidden: Mapping[int, Mapping[str, Any]]):
        """[(shown line, its hidden opponents)] for every shown line of group ``number`` with no opponent shown in its
        group and at least one hidden in it: the head first, then the card order."""
        shown = sorted((line for line in lines if line["topic"]["group"] == number),
                       key=lambda line: not line["topic"]["head"])  # stable: the head first, then the card order
        out = []
        for line in shown:
            if any(self._opponents(line, other) for other in shown if other is not line):
                continue
            found = [j for j, other in hidden.items() if self._opponents(line, other)]
            if found:
                out.append((line, found))
        return out

    def _opponents(self, a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
        """One claims to supersede the other in this question (every claimant of its own candidates, RCO2 1b), or
        MAY_OPPOSE pairs them exactly as ``opposed`` would."""
        if b["ref"] in a["supersede_claims"] or a["ref"] in b["supersede_claims"]:
            return True
        cosine = float(self.q4.d[a["position"]] @ self.q4.d[b["position"]])
        return v21.opposition(a["summary"], b["summary"], a["kind"], b["kind"], cosine) is not None

    def _placed(self, number: int, lines: list[dict[str, Any]], extra: dict[str, Any]) -> list[dict[str, Any]]:
        """``lines`` with ``extra`` among the lines of its group in the fence's time order; the groups keep theirs."""
        where = [n for n, line in enumerate(lines) if line["topic"]["group"] == number]
        same = sorted([lines[n] for n in where] + [extra], key=lambda line: self._fence_time(number, line["position"]))
        return lines[:where[0]] + same + lines[where[-1] + 1:]

    def _fence_time(self, number: int, position: int) -> tuple:
        """The fence's ``time_key`` (``remember_v21.py`` ``fence``) of any record its group keeps: the claimed time of a
        record whose day it trusted, else the time it became available; then that time; then the id."""
        row = self.fence_row(position, 0.0)
        available = v21._utc(row["available"])
        trusted = not UNTRUSTED & set(self._fence_groups[number - 1]["marks"][row["id"]])
        return (v21._utc(row["claimed"]) if trusted else available), available, row["id"]

    def _answer(self, status, reason, results, telemetry) -> dict[str, Any]:
        out = super()._answer(status, reason, results, telemetry)
        out["version"] = VERSION
        return out
