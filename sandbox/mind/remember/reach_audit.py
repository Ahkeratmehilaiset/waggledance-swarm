"""REMEMBER reachability and crowding audit: for every lesson, does a question that asks for what the lesson teaches
reach it on the card, and when it does not, who crowds it out? Sandbox, read-only, advisory, never a gate.

Why. RCO2's diamond round 4 (part B) asks this per lesson, and round 10 orders the audit first, read-only, on the
current memory, run by a Medium non-author: a lesson that no question reaches is memory the joiner never sees, and a
blinded recall number alone cannot say which lessons those are or what stands in front of them.

Per lesson L, in ref order (``audit``):

1. ``not_available``: L did not exist before the time (rule v1's guard band). ``stored_text_cut``: the corpus cut L's
   stored text at a guarded line, so its sections may be gone.
2. The probe (``probe_of``): the LAST sentence of L's How-to-apply section(s) that has at least ``MIN_PROBE_WORDS``
   words and names neither L's file stem nor its frontmatter name (case-folded), nor a ``[[link]]``, nor a ``.md``
   file: the line a reader acts on, in L's own words. A section starts at a ``**How to apply:**`` marker (any case) at
   a line start after the frontmatter and runs to the next ``**<label>:**`` marker; a sentence ends at ``.``, ``!`` or
   ``?`` before whitespace, at a blank line, or before a list item (whose mark is not part of it). No probe is
   ``no_probe``; a probe the decoded guard trips is ``probe_guarded`` and is never asked.
3. The probe span is ALWAYS cut out of L (``masked_q4``): a view of the loaded memory equal to one rebuilt with L's
   stored text without it. R4 cut it only from a lesson with ONE qualifying sentence; left in, the probe is a verbatim
   span of L, and rule v1's self-match removes L from its own question (measured on the fixtures). When the cut L
   still self-matches the probe (it says the same thing elsewhere), no such question can reach L: ``probe_not_clean``.
4. The arm's own engine, built over that view, answers the probe as the situation at the time, with the probe as its
   one query. A card that lists nothing is ``card_empty`` (status EMPTY) or ``card_refused`` (REFUSED), never
   ``unreachable``; any other card status is refused. On a listed card, L is ``reachable`` when it is one of its
   lines, else ``unreachable``.

An audited row carries: the probe as its sha256, word count and qualifying count, never its text; the card's status,
reason (the engine's own; None on a listed card), line count, L's line, head flag and score (the engine's own fused
score; None off the card), and ``packing``, the engine's own telemetry ``stopped_by``, ``skipped_groups`` and
``withheld_by_guard`` (None where the engine has no such key: v2 and v2.1 skip no group, v2.2 and v2.3 skip groups
instead of stopping, a refused card carries no telemetry; v2 and v2.1 also say None when nothing stopped); L's rank in
each view (w, d, b); ``fused``, L's reciprocal-rank fusion (k 60) of the three views, as the engines fuse, and
``fused_rank``, L's place among every candidate by it, ties by id; ``crowding``, how many records, lessons and events
fuse before L, and ``crowders``, the first ``CROWDERS_SHOWN`` of them (a file, or an event_ref); ``topic_on_card``,
whether a shown line other than L is on L's topic (summary cosine >= the engines' ``TOPIC_COSINE``).

The report: one row per lesson (refs, statuses and numbers, never any record text), counts per status, the unreachable
lessons by crowding (most first) then ref, and the near-duplicate clusters among the available lessons (union-find at
summary cosine >= ``PAIR_COSINE``); it passes the decoded guard or is refused. The command line adds the memory hash and
the code blobs, writes one new file (never overwrites) and prints the counts.

Limits, measured on synthetic memories only; no precision or recall of this audit is known:

* One probe per lesson, in the lesson's own words; a real situation words it otherwise (R4's vocabulary gap is not
  measured here). A How-to-apply past the stored text (the corpus keeps 4000 characters) is ``no_probe``. This is
  own-word, masked-probe reachability: not a blind Q4 recall, not parity with the real e5 encoder (the tests use a
  synthetic one) and not utility.
* ``crowding`` and ``crowders`` are the records fused before L, not a cause: the card's packing (its cap, its
  card-line bytes, a skipped group), the topic-group show and the guard can each keep L off a card, so an
  ``unreachable`` row is read with its ``fused_rank`` and ``packing``; counts alone do not establish a retrieval loss.
* Records that hold the probe verbatim are excluded by rule v1 for that question, so they never count as crowders.
* A report names lessons by file and events by event_ref, never by text; these refs are still identifying metadata,
  so a report of a real memory is not public-safe.
* One engine is built per lesson over the whole memory (slow on the real memory), and the encoder re-encodes each cut
  lesson unless ``window`` shows that it reads the same text.
* Run on the real memory only by a Medium non-author (round 10). fable-5 wrote REMEMBER v2.1-v2.3 and this audit; it
  runs it on synthetic data only, and this module never chooses a memory.

The step-2 memory, encoder and v1 rule and the engines are reused unchanged; nothing there is edited. No network, no
bridge event, no gate code; writes only ``--out``.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import remember_v23 as v23  # noqa: E402  (it loads v2.2, v2.1, v3, v2 and the step-2 modules once)

v22, v21, v2 = v23.v22, v23.v21, v23.v2
q4_mod, corpus_mod, cell_mod = v2.q4_mod, v2.corpus_mod, v2.memory_mod.cell_mod
SCHEMA = "fable5.remember-reach-audit.v0"
MIN_PROBE_WORDS = 4
CROWDERS_SHOWN = 10
STATUSES = ("not_available", "stored_text_cut", "no_probe", "probe_guarded", "probe_not_clean", "card_refused",
            "card_empty", "reachable", "unreachable")
CARD_STATUSES = {"REFUSED": "card_refused", "EMPTY": "card_empty"}  # a card that lists nothing has its own status
PACKING = ("stopped_by", "skipped_groups", "withheld_by_guard")  # card telemetry; None where an engine has no such key
ENGINES = {"v2a": (lambda q: v2.RememberV2(q), v2.VERSION), "v21a": (lambda q: v21.RememberV21(q, "v21a"), v21.VERSION),
           "v22a": (lambda q: v22.RememberV22(q, "v22a"), v22.VERSION),
           "v23a": (lambda q: v23.RememberV23(q, "v23a"), v23.VERSION)}  # one-query arms: the probe is the situation
FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n.*?^---[ \t]*\r?$", re.S | re.M)
NAME = re.compile(r"(?m)^name:[ \t]*(\S.*?)[ \t]*\r?$")
MARKER = re.compile(r"(?m)^[ \t]*(?:[-*>][ \t]+)*\*\*([^*\n]{1,40}?):\*\*")
SECTION = "how to apply"
STOP = re.compile(r"[.!?]+(?=\s|\Z)|\r?\n[ \t]*\r?\n|\r?\n(?=[ \t]*(?:[-*+]|\d+[.)])[ \t])")
ITEM = re.compile(r"\s*(?:(?:[-*+]|\d+[.)])[ \t]+)?")


class ReachAuditError(ValueError):
    """Fixed messages that never echo input content."""


# --- the probe -----------------------------------------------------------------------------------------------------

def sentences(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """(start, end) of every sentence of ``text[start:end]``, without its surrounding space or list-item mark."""
    pieces, begin = [], start
    for stop in STOP.finditer(text[start:end]):
        pieces.append((begin, start + stop.end()))
        begin = start + stop.end()
    pieces.append((begin, end))  # what follows the last stop is a sentence too
    spans = []
    for a, b in pieces:
        piece = text[a:b]
        lead, size = ITEM.match(piece).end(), len(piece.rstrip())
        if size > lead:
            spans.append((a + lead, a + size))
    return spans


def qualifies(probe: str, banned: set[str]) -> bool:
    folded = probe.casefold()
    return len(q4_mod.WORDS.findall(probe)) >= MIN_PROBE_WORDS and "[[" not in probe and ".md" not in folded \
        and not any(name in folded for name in banned)


def probe_of(text: str, file: str) -> dict[str, Any] | None:
    """L's probe (module docstring, step 2), or None: the whitespace-collapsed sentence, its raw span in ``text``
    (``start``, ``end``), its sha256 and word count, and how many sentences qualified (``single``: only this one)."""
    front = FRONTMATTER.match(text)
    body = front.end() if front else 0
    name = NAME.search(front.group(0)) if front else None
    banned = {PurePosixPath(str(file).replace("\\", "/")).stem.casefold()}
    if name:
        banned.add(name.group(1).strip("'\"").casefold())
    banned.discard("")
    markers = list(MARKER.finditer(text, body))
    spans = []
    for k, marker in enumerate(markers):
        if " ".join(marker.group(1).split()).casefold() == SECTION:
            spans += sentences(text, marker.end(), markers[k + 1].start() if k + 1 < len(markers) else len(text))
    good = [(a, b) for a, b in spans if qualifies(" ".join(text[a:b].split()), banned)]
    if not good:
        return None
    start, end = good[-1]
    probe = " ".join(text[start:end].split())
    return {"probe": probe, "start": start, "end": end, "sha256": hashlib.sha256(probe.encode("utf-8")).hexdigest(),
            "words": len(q4_mod.WORDS.findall(probe)), "qualifying": len(good), "single": len(good) == 1}


# --- the masked view -----------------------------------------------------------------------------------------------

def _self_match(situation: str, joined: str, own: set[str], rule: dict[str, Any]) -> bool:
    """Rule v1's self-match (``Q4._self_matches``) for ONE record, from its joined words and its shingles."""
    size, share = rule["self_match_shingle"], rule["self_match_share"]
    words = q4_mod.WORDS.findall(situation.lower())
    if not words:
        return False
    if len(words) < size:
        return " " + " ".join(words) + " " in joined
    wanted = q4_mod.shingles(words, size)
    return len(wanted & own) >= share * len(wanted)


def masked_q4(base: Any, position: int, text: str, passage: Callable[[str], np.ndarray], window: int | None = None):
    """A view of the loaded ``base`` in which record ``position`` stores ``text``, equal to a memory rebuilt with that
    stored text; ``base`` itself is never changed. The record's w row is ``passage(text)`` stored as a build stores it
    (normalised by the cell module), unless ``window`` is given and the first ``window`` characters are unchanged (the
    encoder reads no more: e5 cuts at ``encode.TRUNCATE``). BM25 is rebuilt over the view's texts; rule v1's
    self-match is the base's for every other record and is recomputed for this one."""
    old = base.entries[position]
    view = copy.copy(base)
    entry = dict(old, text=text)
    if window is None or text[:window] != old["text"][:window]:
        row = cell_mod._normalized(np.asarray(passage(text), dtype=np.float32).reshape(1, -1))[0]
        view.w = base.w.copy()
        view.w[position] = entry["w"] = row
    view.entries = list(base.entries)
    view.entries[position] = entry
    view.lexical = q4_mod.Lexical([e["text"] for e in view.entries], base.rule["bm25_k1"], base.rule["bm25_b"])
    words = q4_mod.WORDS.findall(text.lower())
    view.joined = list(base.joined)
    view.joined[position] = " " + " ".join(words) + " "
    view.shingle_index = None  # stale for this record: the view answers self-match only through the method below
    own, joined, rule, others = q4_mod.shingles(words, base.rule["self_match_shingle"]), view.joined[position], \
        base.rule, base._self_matches

    def self_matches(situation):
        found = others(situation).copy()
        found[position] = _self_match(situation, joined, own, rule)
        return found

    view._self_matches = self_matches
    return view


# --- the audit -----------------------------------------------------------------------------------------------------

def ref_of(q4: Any, position: int) -> str:
    """A record's ref as the engines show it: a lesson's file, an event's event_ref."""
    row = q4.entries[position]["row"]
    return row["provenance"]["file"] if row["kind"] == "lesson" else row["provenance"]["event_ref"]


def clusters(q4: Any, positions: Iterable[int]) -> list[list[str]]:
    """Near-duplicate clusters among ``positions``: union-find over summary cosines >= ``PAIR_COSINE``; every cluster of
    two or more records as its sorted refs, the clusters sorted."""
    positions = sorted({int(p) for p in positions})
    parent = list(range(len(positions)))

    def root(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    if positions:
        vectors = q4.d[positions]
        for a, b in np.argwhere(np.triu(vectors @ vectors.T >= v2.PAIR_COSINE, k=1)):
            parent[root(int(a))] = root(int(b))
    groups: dict[int, list[str]] = {}
    for k, position in enumerate(positions):
        groups.setdefault(root(k), []).append(ref_of(q4, position))
    return sorted(sorted(group) for group in groups.values() if len(group) > 1)


def lesson_row(q4: Any, ref: str, position: int, make: Callable, when: str, limit: str,
               passage: Callable[[str], np.ndarray], window: int | None) -> dict[str, Any]:
    """One lesson's row (module docstring, steps 1-4)."""
    entry = q4.entries[position]
    if not q4.available[position] < limit:
        return {"ref": ref, "status": "not_available"}
    if entry["row"]["provenance"].get("stored_text_cut_by_guard"):
        return {"ref": ref, "status": "stored_text_cut"}
    probe = probe_of(entry["text"], ref)
    if probe is None:
        return {"ref": ref, "status": "no_probe"}
    if v2.guarded(probe["probe"]):
        return {"ref": ref, "status": "probe_guarded"}
    facts = {key: probe[key] for key in ("sha256", "words", "qualifying", "single")}
    view = masked_q4(q4, position, entry["text"][:probe["start"]] + entry["text"][probe["end"]:], passage, window)
    engine, question = make(view), probe["probe"]
    index, _ = engine.applicable(question, when)
    if index is None:
        raise ReachAuditError("an engine refused a probe question")
    local = {int(p): k for k, p in enumerate(index)}
    if position not in local:
        return {"ref": ref, "status": "probe_not_clean", "probe": facts}
    ranks = engine.ranks(question, index)
    fused = sum(1.0 / (q4.rule["rrf_k"] + ranks[name]) for name in ("w", "d", "b"))
    order = sorted(range(len(index)), key=lambda k: (-fused[k], q4.ids[int(index[k])]))
    before = [int(index[k]) for k in order[:order.index(local[position])]]
    kinds = [q4.entries[p]["row"]["kind"] for p in before]
    listed = engine.shortlist(question, when, [question])
    if listed.get("status") not in ("LISTED", *CARD_STATUSES):
        raise ReachAuditError("an engine returned an unknown card status")
    lines, telemetry = listed.get("lines") or [], listed.get("telemetry") or {}
    mine = [n for n, line in enumerate(lines, 1) if line["doc_id"] == q4.ids[position]]
    shown = lines[mine[0] - 1] if mine else None
    status = CARD_STATUSES.get(listed["status"]) or ("reachable" if mine else "unreachable")
    return {"ref": ref, "status": status, "probe": facts,
            "card": {"status": listed["status"], "reason": listed.get("reason"), "lines": len(lines),
                     "line": mine[0] if mine else None,
                     "head": bool((shown.get("topic") or {}).get("head")) if shown else None,
                     "score": shown.get("score") if shown else None,
                     "packing": {key: telemetry.get(key) for key in PACKING}},
            "views": {name: int(ranks[name][local[position]]) for name in ("w", "d", "b")},
            "fused": round(float(fused[local[position]]), 6), "fused_rank": len(before) + 1, "candidates": len(index),
            "crowding": {"records": len(before), "lessons": kinds.count("lesson"), "events": kinds.count("event")},
            "crowders": [ref_of(q4, p) for p in before[:CROWDERS_SHOWN]],
            "topic_on_card": any(float(q4.d[position] @ q4.d[line["position"]]) >= v2.TOPIC_COSINE
                                 for line in lines if line["doc_id"] != q4.ids[position])}


def audit(q4: Any, arm: str, when: str, passage: Callable[[str], np.ndarray], window: int | None = None):
    """The report over every lesson of the loaded ``q4`` (module docstring). ``passage`` encodes one cut lesson's text
    as the memory's w view was encoded; the arm and the time are checked before any lesson is read."""
    if arm not in ENGINES:
        raise ReachAuditError("unknown arm: the audit asks one-query questions (v2a, v21a, v22a, v23a)")
    moment = corpus_mod.parse_utc(when) if isinstance(when, str) else None
    if moment is None:
        raise ReachAuditError("the time is not an aware UTC time")
    make, version = ENGINES[arm]
    limit = corpus_mod.utc_text(moment - dt.timedelta(seconds=q4.rule["guard_band_s"]))
    lessons = sorted((ref_of(q4, i), i) for i, entry in enumerate(q4.entries) if entry["row"]["kind"] == "lesson")
    rows = [lesson_row(q4, ref, i, make, when, limit, passage, window) for ref, i in lessons]
    counts = {status: sum(row["status"] == status for row in rows) for status in STATUSES}
    unreachable = sorted((row for row in rows if row["status"] == "unreachable"),
                         key=lambda row: (-row["crowding"]["records"], row["ref"]))
    report = {"schema": SCHEMA, "arm": arm, "version": version, "time_utc": when, "records": len(q4.ids),
              "lessons": len(rows), "counts": counts, "unreachable": [row["ref"] for row in unreachable],
              "clusters": clusters(q4, [i for _, i in lessons if q4.available[i] < limit]),
              "constants": {"min_probe_words": MIN_PROBE_WORDS, "crowders_shown": CROWDERS_SHOWN,
                            "pair_cosine": v2.PAIR_COSINE, "topic_cosine": v2.TOPIC_COSINE,
                            "rrf_k": q4.rule["rrf_k"], "depth": v2.DEPTH, "cap": v2.CAP},
              "rows": rows}
    if v2.guarded(report):
        raise ReachAuditError("a guarded word is in the report; it is refused")
    return report


# --- command line --------------------------------------------------------------------------------------------------

def load_memory(memory_dir: Path):
    """(Q4, passage, window) over a real memory with the e5 encoder; tests pass their own loader."""
    import encode as encode_mod  # the e5 encoder: loading never touches the network
    encoder = encode_mod.E5()
    return q4_mod.Q4(v2.memory_mod.Memory(memory_dir), encoder), (lambda text: encoder.passages([text])[0]), \
        encode_mod.TRUNCATE


def main(argv: list[str] | None = None, load: Callable | None = None) -> int:
    parser = argparse.ArgumentParser(description="REMEMBER reachability and crowding audit (read-only, advisory)")
    parser.add_argument("--memory", type=Path, required=True)
    parser.add_argument("--arm", choices=sorted(ENGINES), required=True)
    parser.add_argument("--time", required=True, help="the situation time, an aware UTC time")
    parser.add_argument("--out", type=Path, required=True, help="a new JSON file; never overwritten")
    args = parser.parse_args(argv)
    if corpus_mod.parse_utc(args.time) is None:
        raise ReachAuditError("the time is not an aware UTC time")
    if args.out.exists():
        raise ReachAuditError("the output file exists; it is never overwritten")
    q4, passage, window = (load or load_memory)(args.memory)
    report = audit(q4, args.arm, args.time, passage, window)
    report["memory_sha256"] = v2.sha256_file(args.memory / "memory.json")
    report["code_git_blobs"] = v2.source_blobs()
    text = json.dumps(report, indent=1, sort_keys=True) + "\n"
    if v2.guarded(report) or corpus_mod.ws._has_marker(text):
        raise ReachAuditError("a guarded word is in the report; nothing is written")
    with args.out.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    print(json.dumps({"out": str(args.out), "lessons": report["lessons"], "counts": report["counts"],
                      "unreachable": len(report["unreachable"]), "clusters": len(report["clusters"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
