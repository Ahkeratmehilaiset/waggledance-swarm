"""REMEMBER v2.1 dev: v2's joined shortlist plus OPPOSITE marks. Sandbox, advisory, never a gate.

Why. RCO1 measured that on fresh, independently worded pairs the true lesson is shown and its OPPOSITE is shown too,
unmarked, in 80/80 v2a observations: an independently worded opposite usually lands in another topic group and below
``PAIR_COSINE``, so neither TOPIC nor PAIR says anything. The joiner may flag two picks as contested, but almost never
does. v2.1 adds a mechanical mark that exists without any model call.

``MAY_OPPOSE`` (design: one direct Grok xhigh consultation, answer sha256 050b9f27..., fable-5's audit folder). Each
shown line's summary, the text the joiner reads, is parsed into up to ``CLAUSE_CAP`` directive frames: a polarity
(negation cues counted mod 2 in the main clause, a condition never scanned), a quantifier (wide or exclusive), an
optional ordered frame (``prefer A over B``, ``A before B``) and a content skeleton. Two lines are marked when their
frames conflict on a shared action: ``argument_swap``, ``prefer_conflict``, ``polarity_flip``, ``quantifier_flip``,
``antonym_head`` and, only when the lexical gate nearly held, ``cosine_backfill`` on the summary vectors. The mark
says that the lines may say the reverse of each other; it never says which one is right. Pairs inside one topic
group are checked too: TOPIC says that lines may disagree, not which ones.

fable-5's changes to the consultation's pseudocode, each where its own table and its rules disagreed:

* an ordered frame keeps the clause polarity, so ``never fail to validate X before Y`` against ``do not validate X
  before Y`` is a ``polarity_flip`` (table row 7), while the same order with the same sign is no mark;
* ``prefer_conflict`` compares the other line's head with each arm's head (the first content word of the arm),
  because the arms share context words (``for an idempotent GET``) that make a set overlap useless (row 22);
* the content of a condition belongs to the skeleton (it is not scanned for polarity), and a condition word in the
  middle of a clause starts the condition even without a comma (row 12); a leading prepositional phrase before a
  comma is context, not the directive;
* words are compared after a light, deterministic stemmer (plural, -ing, -ed, final e), not only a plural rule
  (row 21: ``validate``/``validating``, ``write``/``writing``).

Known misses (stated in every product card): opposites with no shared content word and a summary cosine below
``T_COS``; an implicit conflict with no cue and no listed antonym; paraphrased negations outside the cue lists;
Finnish outside the gloss table; a fourth directive in one line. The idf weights are flat in this version.

The DATE FENCE (design: Grok round B, answer sha256 3580b6f3..., its 16 cases are the tests). A probe measured that a
forged-date flood hid the true lesson in 8/20 v2 shortlists. A topic group's head, earliest and latest lines now come
only from records that pass the fence; the TOPIC text says ``n trusted`` (with the trusted claimed span),
``+u untrusted`` and ``FLOOD k from ...``, and each line carries its date marks. fable-5's mapping onto this memory,
weaker than the consultation assumed (its prompt overstated the verification, so stated here):

* there is no per-record ingestion generation, so the batch is the consultation's degraded fallback, a clock floor:
  the UTC hour of ``available_utc``;
* a lesson's ``available_utc`` is its file mtime and a writer can set it; its identity is its directory, and the
  step-2 corpus keeps only a file name, so every lesson has one identity, ``(memory dir)``: three notes on one topic
  written in one clock hour are a FLOOD even when they are real history (one line is then shown, marked);
* an event's identity is the agent in its bridge ``event_ref`` and its claimed time is its ``available_utc``, so an
  event day is never forged; a burst of three events by one agent on one topic in one hour is still a FLOOD;
* a writer who forges mtimes AND stays under ``FLOOD_MIN`` can still bracket a note (the consultation's pinned
  residual, case 16); the fence makes that visible in the marks only when the days are impossible or backdated;
* because a lesson's mtime is its LAST EDIT, an old lesson edited after a newer record on its topic arrives in a
  later wave and is BACKDATED (a spare line at most). A controlled-helper Grok review (2af02293) found this and the
  same-hour lesson flood; both fixes it proposed would reopen the measured flood or drop BACKDATED for far
  forgeries, so both are pinned as stated trade-offs (a test each). Their real-data rates are UNKNOWN until measured.

Lead's boundary probe after the first push: a missing time field raised KeyError and 9999-12-31 overflowed the far
check; both now go through the fixed error boundary or plain day differences (regression tests).
"""

from __future__ import annotations

import datetime
import hashlib
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

VERSION = "remember-v2.1-dev"
CLAUSE_CAP = 3
T_OV = 0.40
MIN_SHARED = 2
T_CTX = 0.50
T_ARG = 0.50
T_COS = 0.90
BACKFILL_FLOOR = 0.15
REASONS = ("argument_swap", "prefer_conflict", "polarity_flip", "quantifier_flip", "antonym_head", "cosine_backfill")
MARK_GLOSS = "MAY_OPPOSE = possible reverse directive on a shared action; not a verdict."

# Closed Finnish gloss, longest first; unknown words stay as they are.
GLOSS = {
    "ei saa": "must not", "ei koskaan": "never", "ei pidä": "must not", "yritä uudelleen": "retry",
    "älä": "do not", "älkää": "do not", "ei": "not", "aina": "always", "vain": "only",
    "ainoastaan": "only", "pitää": "must", "täytyy": "must", "on pakko": "must",
    "vältä": "avoid", "ohita": "skip", "kiellä": "forbid", "yritä": "retry", "kutsu": "call",
    "kutsua": "call", "virhe": "error", "virheen": "error", "koko": "whole", "ennen": "before",
    "jälkeen": "after", "luota": "trust", "välimuisti": "cache", "loki": "log", "jos": "if", "kun": "when",
}
CONTRACTIONS = {"can't": "can not", "cannot": "can not", "won't": "will not", "don't": "do not",
                "doesn't": "does not", "isn't": "is not", "aren't": "are not", "shouldn't": "should not",
                "mustn't": "must not", "didn't": "did not", "never-ever": "never"}
NEGATION_MULTI = [("under", "no", "circumstances"), ("on", "no", "account"), ("in", "no", "event"),
                  ("at", "no", "point"), ("do", "not"), ("does", "not"), ("did", "not"), ("must", "not"),
                  ("should", "not"), ("shall", "not"), ("need", "not"), ("can", "not"), ("will", "not"),
                  ("is", "not"), ("are", "not")]
FOCUS = [("not", "only"), ("not", "just"), ("not", "merely")]
NEGATION = {"not", "no", "never", "neither", "nor", "without", "nobody", "nothing", "nowhere"}
LEXICAL_NEGATIVE_MULTI = [("fail", "to"), ("failure", "to")]
LEXICAL_NEGATIVE = {"avoid", "refrain", "omit", "skip", "bypass", "forbid", "prohibit", "refuse", "disallow"}
MODAL = {"must", "shall", "should", "may", "might", "can", "could", "will", "would", "required", "mandatory",
         "need", "preferably", "saa"}
WIDE = {"always", "any", "all", "every", "each", "whenever", "jokainen", "kaikki"}
EXCLUSIVE = {"only", "exactly", "solely", "just"}
STOPWORDS = {"a", "an", "the", "of", "to", "for", "on", "in", "with", "and", "or", "then", "that", "this", "is", "be",
             "it", "its", "as", "by", "from", "at", "into", "via", "you", "your", "we", "our", "than", "over", "also",
             "do", "does", "are", "was", "were", "been", "if", "when", "unless"}
CONDITION_WORDS = {"if", "when", "unless"}
LEADING_CONTEXT = {"if", "when", "unless", "on", "in", "for", "at", "during", "under", "upon", "with", "without"}
ANTONYMS_RAW = [("enable", "disable"), ("allow", "deny"), ("allow", "forbid"), ("allow", "disallow"),
                ("trust", "distrust"), ("include", "exclude"), ("add", "remove"), ("create", "delete"),
                ("open", "close"), ("start", "stop"), ("acquire", "release"), ("lock", "unlock"),
                ("increase", "decrease"), ("raise", "swallow"), ("surface", "swallow"), ("honor", "ignore"),
                ("respect", "ignore"), ("accept", "reject"), ("retry", "abort"), ("cache", "bypass"),
                ("broaden", "narrow"), ("grant", "revoke"), ("show", "hide"), ("verbose", "silent"),
                ("sync", "async"), ("block", "unblock")]
WORD = re.compile(r"[0-9a-zÀ-ɏ][0-9a-zÀ-ɏ'-]*")
CLAUSE_SPLIT = re.compile(r"[;.]|\b(?:but|whereas|however)\b")


def stem(token: str) -> str:
    """A light, deterministic stemmer: plural, -ing, -ed, -ies/-ied, then a final e."""
    if len(token) >= 5 and token.endswith(("ies", "ied")):
        token = token[:-3] + "y"
    elif len(token) >= 6 and token.endswith("ing"):
        token = token[:-3]
    elif len(token) >= 6 and token.endswith("ed"):
        token = token[:-2]
    elif len(token) >= 5 and token.endswith("es") and token[-3] in "sxz":
        token = token[:-2]
    elif len(token) >= 4 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    if len(token) >= 5 and token.endswith("e"):
        token = token[:-1]
    return token


ANTONYMS = {frozenset((stem(a), stem(b))) for a, b in ANTONYMS_RAW}


def _normalize(text: str) -> str:
    text = text.casefold().replace("’", "'")
    for key in sorted(GLOSS, key=len, reverse=True):
        text = re.sub(r"(?<![\w'-])" + re.escape(key) + r"(?![\w'-])", GLOSS[key], text)
    for key, value in CONTRACTIONS.items():
        text = re.sub(r"(?<![\w'-])" + re.escape(key) + r"(?![\w'-])", value, text)
    return re.sub(r"(\w)n't\b", r"\1 not", text)


def _words(text: str) -> list[str]:
    return WORD.findall(text)


@dataclass
class Frame:
    pol: int
    quant: str
    head: str | None
    skeleton: frozenset
    cues: tuple
    kind: str = "plain"  # plain | prefer | seq
    arm_a: frozenset = field(default_factory=frozenset)
    arm_b: frozenset = field(default_factory=frozenset)
    head_a: str | None = None
    head_b: str | None = None


def _match(tokens: Sequence[str], i: int, phrase: Sequence[str]) -> bool:
    return tuple(tokens[i:i + len(phrase)]) == tuple(phrase)


def _scan(tokens: Sequence[str]) -> tuple[int, list[str], list[str], str, bool]:
    """(flips, cue words, content stems in order, quantifier, any cue) over one token list."""
    flips, cues, content, quant, cued = 0, [], [], "none", False
    i = 0
    while i < len(tokens):
        hit = next((p for p in FOCUS if _match(tokens, i, p)), None)
        if hit:
            cues.append(" ".join(hit))
            i += len(hit)
            continue
        hit = next((p for p in NEGATION_MULTI + LEXICAL_NEGATIVE_MULTI if _match(tokens, i, p)), None)
        if hit:
            flips += 1
            cues.append(" ".join(hit))
            cued = True
            i += len(hit)
            continue
        token = tokens[i]
        if token in NEGATION or token in LEXICAL_NEGATIVE:
            flips += 1
            cues.append(token)
            cued = True
        elif token in MODAL:
            cues.append(token)
            cued = True
        elif token in WIDE or token in EXCLUSIVE:
            quant = "wide" if token in WIDE else ("exclusive" if quant != "wide" else quant)
            cues.append(token)
            cued = True
        elif token not in STOPWORDS:
            content.append(stem(token))
        i += 1
    return flips, cues, content, quant, cued


def _split_condition(clause: str) -> tuple[str, str]:
    """(condition, main). A leading if/when/unless or prepositional phrase before a comma is context; a condition
    word in the middle of the clause starts a trailing condition, with or without a comma."""
    stripped = clause.strip()
    first = _words(stripped[:24])
    if first and first[0] in LEADING_CONTEXT and "," in stripped:
        head, _, rest = stripped.partition(",")
        return head, rest
    words = _words(stripped)
    for position, word in enumerate(words):
        if position > 0 and word in CONDITION_WORDS:
            match = list(re.finditer(r"\b" + word + r"\b", stripped))
            cut = match[0].start() if word != words[0] else match[1].start()
            return stripped[cut:], stripped[:cut]
    return "", stripped


def _order(tokens: list[str]) -> tuple[str, list[str], list[str], list[str]] | None:
    """(kind, first arm, second arm, the tokens left for the polarity) for an ordered frame, else None."""
    def find(phrase, start=0):
        return next((i for i in range(start, len(tokens)) if _match(tokens, i, phrase)), None)
    for verb in ("prefer", "choose"):
        p = find((verb,))
        if p is not None:
            for marker in (("rather", "than"), ("instead", "of"), ("over",), ("to",), ("not",)):
                m = find(marker, p + 1)
                if m is not None and m > p + 1:
                    return "prefer", tokens[p + 1:m], tokens[m + len(marker):], tokens[:p]
    for marker in (("rather", "than"), ("instead", "of")):
        m = find(marker)
        if m is not None and 0 < m < len(tokens) - len(marker):
            return "prefer", tokens[:m], tokens[m + len(marker):], []
    m = find(("before",))
    if m is not None and 0 < m < len(tokens) - 1:
        return "seq", tokens[:m], tokens[m + 1:], tokens[:m]
    m = find(("after",))
    if m is not None and 0 < m < len(tokens) - 1:
        return "seq", tokens[m + 1:], tokens[:m], tokens[:m]
    return None


def frames(text: str, kind: str) -> list[Frame]:
    out = []
    for clause in [c for c in CLAUSE_SPLIT.split(_normalize(text)) if c and c.strip()][:CLAUSE_CAP]:
        condition, main = _split_condition(clause)
        tokens = _words(main)
        if not tokens:
            continue
        cond_content = _scan(_words(condition))[2]
        order = _order(tokens)
        if order:
            order_kind, first, second, polar = order
            flips, cues, _, quant, cued = _scan(polar if order_kind == "prefer" else tokens)
            arm_a, arm_b = _scan(first)[2], _scan(second)[2]
            if not arm_a or not arm_b:
                order = None
        if order:
            pol = -1 if flips % 2 else 1
            out.append(Frame(pol=pol, quant=quant, head=arm_a[0] if order_kind == "seq" else None,
                             skeleton=frozenset(arm_a + arm_b + cond_content), cues=tuple(cues), kind=order_kind,
                             arm_a=frozenset(arm_a), arm_b=frozenset(arm_b), head_a=arm_a[0], head_b=arm_b[0]))
            continue
        flips, cues, content, quant, cued = _scan(tokens)
        if not content:
            continue
        pol = -1 if flips % 2 else 1
        if not cued and kind != "lesson":
            pol = 0  # a bare event states no directive
        out.append(Frame(pol=pol, quant=quant, head=content[0], skeleton=frozenset(content + cond_content),
                         cues=tuple(cues)))
    return out


def _overlap(a: frozenset, b: frozenset) -> tuple[float, list[str]]:
    shared = sorted(a & b)
    small = min(len(a), len(b))
    return (len(shared) / small if small else 0.0), shared


def _jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def _rule(f: Frame, g: Frame, cosine: float) -> tuple[str | None, bool]:
    """(reason or None, decided): decided=True with no reason means agreement that ends the search for this pair."""
    ov, shared = _overlap(f.skeleton, g.skeleton)
    if f.kind != "plain" and g.kind != "plain":
        if _jaccard(f.arm_a, g.arm_b) >= T_ARG and _jaccard(f.arm_b, g.arm_a) >= T_ARG \
                and _jaccard(f.arm_a, g.arm_a) < T_ARG:
            return "argument_swap", True
        if _jaccard(f.arm_a, g.arm_a) >= T_ARG and _jaccard(f.arm_b, g.arm_b) >= T_ARG:
            return ("polarity_flip", True) if f.pol * g.pol == -1 else (None, True)
    for p, q in ((f, g), (g, f)):
        if p.kind == "prefer" and q.kind == "plain" and q.pol != 0:
            if q.head == p.head_a:
                return ("prefer_conflict", True) if q.pol == -1 else (None, True)
            if q.head == p.head_b:
                return ("prefer_conflict", True) if q.pol == 1 else (None, True)
    antonym = f.head is not None and g.head is not None and frozenset((f.head, g.head)) in ANTONYMS
    if antonym and f.pol * g.pol == -1 and ov >= 0.5:
        return None, True  # the negation of an antonym is agreement
    if antonym and f.pol * g.pol == 1:
        ctx_ov, ctx_shared = _overlap(f.skeleton - {f.head}, g.skeleton - {g.head})
        if ctx_ov >= T_CTX and ctx_shared:
            return "antonym_head", True
    heads = f.head is not None and f.head == g.head
    if heads and f.pol * g.pol == -1 and ov >= T_OV and len(shared) >= MIN_SHARED:
        return "polarity_flip", True
    if heads and f.pol == 1 and g.pol == 1 and ov >= T_OV and len(shared) >= MIN_SHARED \
            and {f.quant, g.quant} == {"wide", "exclusive"}:
        return "quantifier_flip", True
    if heads and f.pol * g.pol == -1 and BACKFILL_FLOOR <= ov < T_OV and shared and cosine >= T_COS:
        return "cosine_backfill", True
    return None, False


def opposition(text_a: str, text_b: str, kind_a: str, kind_b: str, cosine: float) -> dict[str, Any] | None:
    """The strongest MAY_OPPOSE reason between two shown lines, or None. Symmetric in its two lines."""
    best, best_rank = None, len(REASONS)
    for f in frames(text_a, kind_a):
        for g in frames(text_b, kind_b):
            found, _ = _rule(f, g, cosine)
            if found is None:
                continue
            rank = REASONS.index(found)
            if rank < best_rank:
                ov, shared = _overlap(f.skeleton, g.skeleton)
                best_rank = rank
                best = {"reason": found, "cues": sorted(set(f.cues) | set(g.cues)), "shared": shared,
                        "ov": round(ov, 2), "pol": f"{f.pol:+d}/{g.pol:+d}"}
    return best


def mark_text(ref: str, mark: Mapping[str, Any]) -> str:
    return (f"[MAY_OPPOSE {ref} reason={mark['reason']} cues={'+'.join(mark['cues']) or '-'} "
            f"shared={'+'.join(mark['shared']) or '-'} ov={mark['ov']:.2f} pol={mark['pol']}]")


# --- the date fence: which lines of a topic group are shown (design: Grok round B, answer sha256 3580b6f3...) ---------
#
# A forged-date flood hid the true lesson in 8/20 v2 shortlists: near-duplicate notes with writer-chosen early and late
# days took the earliest and latest slots. The fence admits a record to the slots only if it is not part of a burst
# (FLOOD_MIN records from one identity in one batch, or FLOOD_MIN identical texts in one batch) and its claimed day is
# possible (not after it was available) and not earlier than every day an earlier wave already claimed (BACKDATED).
# Among the records that pass, the slots stay v2's: head, earliest, latest; spare lines go to non-flood records that
# failed, marked. The TOPIC text counts trusted records, untrusted units and the raw flood size.

FLOOD_MIN = 3
FAR_GAP_DAYS = 14
WAVE_GAP_S = 24 * 3600
DATE_MARKS = ("FLOOD", "CLAIMED_AFTER_AVAILABLE", "BACKDATED", "DATE_UNVERIFIED")
FENCE_GLOSS = ("TOPIC n trusted = records whose own day passed the date fence; FLOOD, CLAIMED_AFTER_AVAILABLE, "
               "BACKDATED and DATE_UNVERIFIED mark a day the memory cannot confirm; not a verdict.")
FENCE_KINDS = ("lesson", "event")
UTC_TEXT = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?Z")


def _utc(value: Any) -> datetime.datetime:
    match = UTC_TEXT.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise RememberV21Error("a fence row time is not a UTC timestamp")
    *parts, fraction = match.groups()
    try:
        return datetime.datetime(*map(int, parts), int((fraction or "0")[:6].ljust(6, "0")),
                                 tzinfo=datetime.timezone.utc)
    except ValueError:
        raise RememberV21Error("a fence row time is not a UTC timestamp") from None


def _fence_row(row: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise RememberV21Error("a fence row is malformed")
    score = row.get("score")
    if (not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score)
            or row.get("kind") not in FENCE_KINDS
            or any(not isinstance(row.get(key), str) or not row[key] for key in ("id", "identity", "batch"))
            or not isinstance(row.get("text"), str)):
        raise RememberV21Error("a fence row is malformed")
    folded = " ".join(unicodedata.normalize("NFKC", row["text"]).casefold().split())
    return {"id": row["id"], "kind": row["kind"], "identity": row["identity"], "batch": row["batch"],
            "available": _utc(row.get("available")), "claimed": _utc(row.get("claimed")), "score": float(score),
            "key": hashlib.sha256(folded.encode("utf-8")).hexdigest()}


def fence(members: Sequence[Mapping[str, Any]], group: Any) -> dict[str, Any]:
    """One topic group -> {header, show (ids in display order), marks (per surviving id), head, n, u, flood}.

    Pure and order-free. Rows: id, kind (lesson|event), identity (a verified writer for an event; a lesson's parent
    directory, NOT verified), batch (the ingestion batch that set ``available``), available, claimed, score (higher
    is better), text. The rules are the consultation's ``select``; its 16 cases are the tests."""
    rows = [_fence_row(row) for row in members]
    if not rows or len({row["id"] for row in rows}) != len(rows):
        raise RememberV21Error("a fence group is empty or repeats an id")
    by_writer = Counter((row["identity"], row["batch"]) for row in rows)
    by_text = Counter((row["batch"], row["key"]) for row in rows)
    for row in rows:
        row["flood"] = (by_writer[row["identity"], row["batch"]] >= FLOOD_MIN
                        or by_text[row["batch"], row["key"]] >= FLOOD_MIN)
        row["impossible"] = row["claimed"].date() > row["available"].date()

    def head_key(row):
        return -row["score"], row["available"], row["id"]

    def time_key(row):
        return row["trusted"], row["available"], row["id"]

    copies: dict[tuple, dict[str, Any]] = {}
    for row in sorted(rows, key=head_key):  # an exact copy below the flood size never takes a second slot
        if not row["flood"]:
            copies.setdefault((row["identity"], row["batch"], row["key"]), row)
    survivors = [row for row in rows if row["flood"]] + list(copies.values())

    spans: dict[str, tuple] = {}
    for row in rows:
        low, high = spans.get(row["batch"], (row["available"], row["available"]))
        spans[row["batch"]] = (min(low, row["available"]), max(high, row["available"]))
    wave_of, wave, wave_end = {}, -1, None
    for batch in sorted(spans, key=lambda name: (spans[name][0], name)):
        low, high = spans[batch]
        if wave_end is None or (low - wave_end).total_seconds() > WAVE_GAP_S:
            wave, wave_end = wave + 1, high
        else:
            wave_end = max(wave_end, high)
        wave_of[batch] = wave
    running_min = None
    for number in range(wave + 1):
        current = [row for row in survivors if wave_of[row["batch"]] == number]
        for row in current:
            row["backdated"] = (not row["flood"] and not row["impossible"] and running_min is not None
                                and row["claimed"].date() < running_min)
        days = [row["claimed"].date() for row in current
                if not (row["flood"] or row["impossible"] or row["backdated"])]
        if days:
            running_min = min(days + ([running_min] if running_min is not None else []))

    for row in survivors:
        row["eligible"] = not (row["flood"] or row["impossible"] or row["backdated"])
        row["far"] = (row["available"].date() - row["claimed"].date()).days > FAR_GAP_DAYS  # no calendar overflow
        row["trusted"] = row["claimed"] if row["eligible"] else row["available"]
        found = {"FLOOD": row["flood"], "CLAIMED_AFTER_AVAILABLE": row["impossible"], "BACKDATED": row["backdated"],
                 "DATE_UNVERIFIED": row["flood"] or (row["eligible"] and row["far"])}
        row["marks"] = [mark for mark in DATE_MARKS if found[mark]]
    eligible = [row for row in survivors if row["eligible"]]
    non_flood = [row for row in survivors if not row["flood"]]
    if eligible:
        shown: list[dict[str, Any]] = []
        for row in (min(eligible, key=head_key), min(eligible, key=time_key), max(eligible, key=time_key)):
            if all(row["id"] != other["id"] for other in shown):
                shown.append(row)
        for row in sorted((row for row in non_flood if not row["eligible"]), key=head_key):
            if len(shown) >= 3:
                break
            shown.append(row)  # a spare line only; never in place of an eligible one
        head = shown[0]["id"]
    elif non_flood:
        shown = sorted(non_flood, key=head_key)[:3]
        head = shown[0]["id"]
    else:
        shown = [min(survivors, key=head_key)]  # a pure flood: one line, never its forged ends
        head = shown[0]["id"]
    shown.sort(key=time_key)

    units = {("text", row["batch"], row["key"]) if by_text[row["batch"], row["key"]] >= FLOOD_MIN
             else ("writer", row["identity"], row["batch"]) for row in survivors if not row["eligible"]}
    flooded = [row for row in rows if row["flood"]]
    untrusted = f"; +{len(units)} untrusted" if units else ""
    if eligible:
        low, high = (min(row["claimed"] for row in eligible).date(), max(row["claimed"] for row in eligible).date())
        unverified = any(row["far"] and row["claimed"].date() in (low, high) for row in eligible)
        span = f"{low}..{high}{' DATE_UNVERIFIED' if unverified else ''}"
        header = f"[TOPIC {group}: {len(eligible)} trusted, {span}{untrusted}]"
    else:
        low, high = min(row["claimed"] for row in rows).date(), max(row["claimed"] for row in rows).date()
        header = f"[TOPIC {group}: 0 trusted, untrusted {low}..{high}{untrusted}]"
    if flooded:
        sources = sorted({row["identity"] for row in flooded})
        kinds = {row["kind"] for row in flooded}
        noun = "writer" if kinds == {"event"} else "path" if kinds == {"lesson"} else "source"
        header += (f" FLOOD {len(flooded)} from {noun} {sources[0]}" if len(sources) == 1
                   else f" FLOOD {len(flooded)} from {len(sources)} {noun}s")
    return {"header": header, "show": [row["id"] for row in shown], "head": head,
            "marks": {row["id"]: list(row["marks"]) for row in survivors}, "n": len(eligible), "u": len(units),
            "flood": len(flooded)}


# --- the engine: v2's joined arms over RCO2's per-situation lexical view (remember_v3 arm v3t) ------------------------

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path, PurePosixPath  # noqa: E402

import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import remember_v3 as v3  # noqa: E402  (it loads remember_v2 and the step-2 modules once)

v2 = v3.v2
ARMS = ("v21a", "v21ca")  # v21a: the situation only; v21ca: the situation and the joiner's rewrite


class RememberV21Error(ValueError):
    """Fixed messages that never echo input content."""


def line_text(n: int, line: Mapping[str, Any]) -> str:
    """v2's line with the fence's TOPIC text in place of v2's, the line's date marks, v2's PAIR and SUPERSEDED marks
    and the MAY_OPPOSE marks, before the summary."""
    marks = ""
    topic = line.get("topic")
    if topic and topic.get("header"):
        marks += " " + topic["header"]
    if line.get("date_marks"):
        marks += f" [{' '.join(line['date_marks'])}]"
    if line.get("pair_with"):
        marks += f" [PAIR with {', '.join(line['pair_with'])}]"
    if line.get("superseded_by"):
        marks += f" [SUPERSEDED by {line['superseded_by']}]"
    marks += "".join(" " + mark_text(mark["ref"], mark) for mark in line.get("may_oppose") or ())
    return f"[{n}] {line['ref']} ({line['kind']}, {line['time_utc'][:10]}){marks}: {line['summary']}"


def rendered_bytes(lines: Sequence[Mapping[str, Any]]) -> int:
    return sum(len(line_text(n, line).encode("utf-8")) + 1 for n, line in enumerate(lines, 1))


def render_shortlist(case_id: str, situation: str, listed: Mapping[str, Any]) -> str:
    """What the joining agent reads: v2's case text with v2.1 lines, and the gloss once when a line is marked."""
    lines = [f"CASE {case_id}", f"SITUATION: {v2.situation_text(situation)}"]
    if listed["status"] != "LISTED":
        lines.append(f"SHORTLIST: none ({listed['reason']})")
    if any(line.get("may_oppose") for line in listed["lines"]):
        lines.append(MARK_GLOSS)
    if any(line.get("date_marks") or (line.get("topic") or {}).get("untrusted") for line in listed["lines"]):
        lines.append(FENCE_GLOSS)
    lines += [line_text(n, line) for n, line in enumerate(listed["lines"], 1)]
    text = "\n".join(lines) + "\n"
    if v2.corpus_mod.ws._has_marker(text):
        raise RememberV21Error("a guarded word is in a shortlist; nothing is written")
    return text


class RememberV21(v3.RememberV3):
    """v2a / v2ca on the v3t ranking (per-situation BM25, no hop, no WHEN, no BRD) with MAY_OPPOSE marks; the arm is
    checked from the queries, and every answer names its arm and this version."""

    def __init__(self, q4: Any, arm: str):
        if arm not in ARMS:
            raise RememberV21Error("unknown arm: v2.1 has v21a and v21ca only (a code-only answer is never shown)")
        super().__init__(q4)
        self.arm = arm
        self._fence_ranks: list | None = None  # the ranks of one shortlist call, so the fence sees v2's fused score
        self._fence_groups: list | None = None

    def ranks(self, text: str, index: np.ndarray) -> dict[str, np.ndarray]:
        ranks = super().ranks(text, index)
        if self._fence_ranks is not None:
            self._fence_ranks.append(ranks)
        return ranks

    def fence_row(self, position: int, score: float) -> dict[str, Any]:
        """One record as a fence row. There is no per-record ingestion generation in this memory, so the batch is the
        consultation's degraded fallback, a clock floor: the UTC hour of ``available_utc``. A lesson's
        ``available_utc`` is its file mtime and its identity its directory: neither is verified (module docstring)."""
        entry = self.q4.entries[position]
        row = entry["row"]
        if row["kind"] == "event":  # a cell row keeps no meta; event_ref is "ts|agent|type|task" (step-2 corpus)
            parts = str(row["provenance"].get("event_ref", "")).split("|")
            identity = parts[1] if len(parts) == 4 and parts[1] else "(unknown writer)"
        else:
            parent = PurePosixPath(str(row["provenance"].get("file", "")).replace("\\", "/")).parent.as_posix()
            identity = "(memory dir)" if parent in ("", ".") else parent.casefold()
        available = row["provenance"]["available_utc"]
        return {"id": self.q4.ids[position], "kind": row["kind"], "identity": identity, "batch": available[:13],
                "available": available, "claimed": row["time_utc"], "score": score, "text": entry["text"]}

    def topic_groups(self, index: np.ndarray, pool: Sequence[int], vectors: np.ndarray) -> list[dict[str, Any]]:
        """v2's groups; inside a shortlist call the shown lines and the head come from the date fence."""
        groups = super().topic_groups(index, pool, vectors)
        if self._fence_ranks is None:
            return groups
        fused = np.zeros(len(index))
        for ranks in self._fence_ranks:  # exactly v2's sum, in v2's order
            for name in ("w", "d", "b"):
                fused += 1.0 / (self.q4.rule["rrf_k"] + ranks[name])
        self._fence_groups = []
        for number, group in enumerate(groups, 1):
            rows = [self.fence_row(int(index[j]), float(fused[j])) for j in group["members"]]
            out = fence(rows, group=number)
            local = {row["id"]: int(j) for row, j in zip(rows, group["members"])}
            group["show"], group["head"] = [local[rid] for rid in out["show"]], local[out["head"]]
            self._fence_groups.append(out)
        return groups

    def fenced(self, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Each line's TOPIC from its group's fence (v2's claimed first..last days are dropped) and its date marks."""
        for line in lines:
            out = self._fence_groups[line["topic"]["group"] - 1]
            records = line["topic"]["records"]
            line["topic"] = {"group": line["topic"]["group"], "records": records, "head": line["topic"]["head"],
                             "trusted": out["n"], "untrusted": out["u"], "flood": out["flood"],
                             "header": out["header"] if records > 1 or out["n"] == 0 else None}
            line["date_marks"] = list(out["marks"][self.q4.ids[line["position"]]])
        return lines

    def opposed(self, lines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """Each line's MAY_OPPOSE marks against every other shown line, sorted by the other ref; (lines, pairs)."""
        for line in lines:
            line["may_oppose"] = []
        pairs = 0
        for a in range(len(lines)):
            for b in range(a + 1, len(lines)):
                one, two = lines[a], lines[b]
                cosine = float(self.q4.d[one["position"]] @ self.q4.d[two["position"]])
                mark = opposition(one["summary"], two["summary"], one["kind"], two["kind"], cosine)
                if mark is None:
                    continue
                pairs += 1
                one["may_oppose"].append(dict(mark, ref=two["ref"]))
                flipped = dict(mark, ref=one["ref"], pol="/".join(reversed(mark["pol"].split("/"))))
                two["may_oppose"].append(flipped)
        for line in lines:
            line["may_oppose"].sort(key=lambda mark: mark["ref"])
        return lines, pairs

    def shortlist(self, situation: str, situation_time_utc: str, queries: Sequence[str]) -> dict[str, Any]:
        expected = 1 if self.arm == "v21a" else 2
        if not isinstance(queries, (list, tuple)) or len(queries) != expected or queries[0] != situation:
            return {"status": "REFUSED", "reason": "queries_do_not_match_arm", "lines": []}
        self._fence_ranks, self._fence_groups = [], None
        try:
            listed = super().shortlist(situation, situation_time_utc, queries)
            if listed["status"] != "LISTED":
                return listed
            lines = self.fenced([dict(line) for line in listed["lines"]])
        finally:
            self._fence_ranks = None
        lines, pairs = self.opposed(lines)
        stopped = listed["telemetry"].get("stopped_by")
        while lines and rendered_bytes(lines) > v2.CASE_BYTES:
            last = lines[-1]["topic"]["group"]  # groups are listed whole or not at all, lowest first
            lines, pairs = self.opposed([line for line in lines if line["topic"]["group"] != last])
            stopped = "bytes_with_marks"
        withheld = 0
        for n, line in enumerate(list(lines), 1):
            if v2.corpus_mod.ws._has_marker(line_text(n, line)) or v2.guarded(line):
                withheld += 1
        if withheld:  # a mark repeats only shown words, so this cannot add a guarded word; refuse if it ever does
            return {"status": "REFUSED", "reason": "output_guarded", "lines": []}
        telemetry = dict(listed["telemetry"], may_oppose_pairs=pairs, stopped_by=stopped,
                         line_bytes=rendered_bytes(lines))
        return {"status": "LISTED" if lines else "EMPTY", "reason": None if lines else "nothing_listable",
                "lines": lines, "telemetry": telemetry}

    def apply_picks(self, listed: Mapping[str, Any], picks: Any) -> dict[str, Any]:
        """v2's picks; each picked result keeps its MAY_OPPOSE marks against every SHOWN line, picked or not."""
        out = super().apply_picks(listed, picks)
        if out["results"]:
            by_ref = {line["ref"]: line.get("may_oppose", []) for line in listed["lines"]}
            for result in out["results"]:
                result["may_oppose"] = [dict(mark) for mark in by_ref.get(result["ref"], [])]
            if v2.guarded(out):
                out.update(status="REFUSED", abstain=False, reason="output_guarded", results=[])
        return out

    def _answer(self, status, reason, results, telemetry, started) -> dict[str, Any]:
        out = super()._answer(status, reason, results, telemetry, started)
        out["version"], out["arm"] = VERSION, self.arm
        return out


# --- runner: v2's prepare (``remember_v2.py prepare``) makes blind.jsonl; this writes the v2.1 shortlists and answers --

CONSTANTS = {"clause_cap": CLAUSE_CAP, "t_ov": T_OV, "min_shared": MIN_SHARED, "t_ctx": T_CTX, "t_arg": T_ARG,
             "t_cos": T_COS, "backfill_floor": BACKFILL_FLOOR, "flood_min": FLOOD_MIN, "far_gap_days": FAR_GAP_DAYS,
             "wave_gap_s": WAVE_GAP_S, "batch": "utc_hour_of_available_utc", "depth": v2.DEPTH, "cap": v2.CAP,
             "pair_cosine": v2.PAIR_COSINE, "topic_cosine": v2.TOPIC_COSINE, "group_lines": v2.GROUP_LINES,
             "case_bytes": v2.CASE_BYTES}


def load_engine(memory_dir: Path, arm: str) -> RememberV21:
    import encode as encode_mod  # the e5 encoder; tests pass their own loader
    return RememberV21(v2.q4_mod.Q4(v2.memory_mod.Memory(memory_dir), encode_mod.E5()), arm)


def _blind(out: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (out / "blind.jsonl").read_text(encoding="utf-8").splitlines()]


SITUATION_CONTRACT = "wd.remember-v21-situation-run.v1"  # --arms situation: v21a alone, no rewrite, its own manifests


def situation_shortlists(args, load=load_engine) -> int:
    """``v21a`` alone (Lead round 4/10, which calls it primary): the situation as its only query, no rewrite input,
    one engine load; three files. The default ``--arms all`` below is the frozen run, unchanged."""
    out = args.out
    data = (out / "blind.jsonl").read_bytes()
    blind = [json.loads(line) for line in data.decode("utf-8").splitlines()]
    engine = load(args.memory, "v21a")
    lists, judge = [], [v2.prompts.JUDGE, ""]
    for row in blind:
        listed = engine.shortlist(row["situation"], row["time"], [row["situation"]])
        lists.append({"id": row["id"], "listed": listed})
        judge.append(render_shortlist(row["id"], row["situation"], listed))
    files = {"v21a/shortlists.jsonl": v2.jsonl_text(lists), "v21a/judge-input.md": "\n".join(judge)}
    hashes = {name: v2.text_sha256(text) for name, text in files.items()}
    manifest = {"contract": SITUATION_CONTRACT, "version": VERSION, "selected_arms": ["v21a"],
                "blind_sha256": hashlib.sha256(data).hexdigest(), "rewrites_sha256": None,
                "memory_sha256": v2.sha256_file(args.memory / "memory.json"), "outputs": hashes,
                "code_git_blobs": v2.source_blobs(), "prompts": v2.prompts.hashes(), "constants": CONSTANTS}
    files["shortlists-v21-situation-manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    v2.write_outputs({out / name: text for name, text in files.items()})
    print(json.dumps({"cases": len(blind), "outputs": hashes}, indent=1))
    return 0


def situation_compose(args, load=load_engine) -> int:
    """``v21a`` answers of a situation run: only v21a picks, and only over the shortlists its manifest binds."""
    if args.picks_v21a is None or args.picks_v21ca is not None:
        raise RememberV21Error("--arms situation composes v21a picks only")
    out = args.out
    bound = v2.situation_run_bound(out, "shortlists-v21-situation-manifest.json", "v21a", SITUATION_CONTRACT,
                                   args.memory, RememberV21Error)
    ids = [row["id"] for row in _blind(out)]
    picks = v2.read_picks(json.loads(args.picks_v21a.read_text(encoding="utf-8")), ids)
    listed = {json.loads(line)["id"]: json.loads(line)["listed"]
              for line in (out / "v21a" / "shortlists.jsonl").read_text(encoding="utf-8").splitlines()}
    engine = load(args.memory, "v21a")
    rows, diag = [], []
    for case_id in ids:
        answer = engine.apply_picks(listed[case_id], picks[case_id])
        rows.append(v2.contract(case_id, answer))
        diag.append({"id": case_id, "answer": answer, "picks": picks[case_id]})
    files = {"v21a/outputs.jsonl": v2.jsonl_text(rows), "v21a/diagnostics.jsonl": v2.jsonl_text(diag)}
    hashes = {"v21a/picks_sha256": v2.sha256_file(args.picks_v21a)}
    hashes.update({name: v2.text_sha256(text) for name, text in files.items()})
    manifest = {"contract": SITUATION_CONTRACT, "version": VERSION, "selected_arms": ["v21a"],
                "shortlists_manifest_sha256": bound, "memory_sha256": v2.sha256_file(args.memory / "memory.json"),
                "outputs": hashes, "code_git_blobs": v2.source_blobs(), "prompts": v2.prompts.hashes(),
                "constants": CONSTANTS}
    files["compose-v21-situation-manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    v2.write_outputs({out / name: text for name, text in files.items()})
    print(json.dumps(hashes, indent=1))
    return 0


def cmd_shortlists(args, load=load_engine) -> int:
    if v2.run_arms(args, RememberV21Error) == "situation":
        return situation_shortlists(args, load)
    out = args.out
    blind = _blind(out)
    ids = [row["id"] for row in blind]
    rewrites = v2.read_rewrites(json.loads(args.rewrites.read_text(encoding="utf-8")), ids)
    files = {}
    for arm in ARMS:
        engine = load(args.memory, arm)
        lists, judge = [], [v2.prompts.JUDGE, ""]
        for row in blind:
            queries = [row["situation"]] + ([rewrites[row["id"]]] if arm == "v21ca" else [])
            listed = engine.shortlist(row["situation"], row["time"], queries)
            lists.append({"id": row["id"], "listed": listed})
            judge.append(render_shortlist(row["id"], row["situation"], listed))
        files[f"{arm}/shortlists.jsonl"] = v2.jsonl_text(lists)
        files[f"{arm}/judge-input.md"] = "\n".join(judge)
    hashes = {name: v2.text_sha256(text) for name, text in files.items()}
    manifest = {"version": VERSION, "memory_sha256": v2.sha256_file(args.memory / "memory.json"),
                "rewrites_sha256": v2.sha256_file(args.rewrites), "outputs": hashes,
                "code_git_blobs": v2.source_blobs(), "prompts": v2.prompts.hashes(), "constants": CONSTANTS}
    files["shortlists-v21-manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    v2.write_outputs({out / name: text for name, text in files.items()})
    print(json.dumps({"cases": len(blind), "outputs": hashes}, indent=1))
    return 0


def cmd_compose(args, load=load_engine) -> int:
    if getattr(args, "arms", "all") == "situation":
        return situation_compose(args, load)
    if getattr(args, "arms", "all") != "all":
        raise RememberV21Error("unknown arm set")
    out = args.out
    ids = [row["id"] for row in _blind(out)]
    files, hashes = {}, {}
    for arm, path in (("v21a", args.picks_v21a), ("v21ca", args.picks_v21ca)):
        if path is None:
            continue
        engine = load(args.memory, arm)
        picks = v2.read_picks(json.loads(path.read_text(encoding="utf-8")), ids)
        listed = {json.loads(line)["id"]: json.loads(line)["listed"]
                  for line in (out / arm / "shortlists.jsonl").read_text(encoding="utf-8").splitlines()}
        rows, diag = [], []
        for case_id in ids:
            answer = engine.apply_picks(listed[case_id], picks[case_id])
            rows.append(v2.contract(case_id, answer))
            diag.append({"id": case_id, "answer": answer, "picks": picks[case_id]})
        files[f"{arm}/outputs.jsonl"] = v2.jsonl_text(rows)
        files[f"{arm}/diagnostics.jsonl"] = v2.jsonl_text(diag)
        hashes[f"{arm}/picks_sha256"] = v2.sha256_file(path)
    hashes.update({name: v2.text_sha256(text) for name, text in files.items()})
    manifest = {"version": VERSION, "memory_sha256": v2.sha256_file(args.memory / "memory.json"), "outputs": hashes,
                "code_git_blobs": v2.source_blobs(), "prompts": v2.prompts.hashes(), "constants": CONSTANTS}
    files["compose-v21-manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    v2.write_outputs({out / name: text for name, text in files.items()})
    print(json.dumps(hashes, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="REMEMBER v2.1 dev: v2.1 shortlists (MAY_OPPOSE, date fence) and answers")
    sub = parser.add_subparsers(dest="command", required=True)
    short = sub.add_parser("shortlists")
    short.add_argument("--memory", type=Path, required=True)
    short.add_argument("--rewrites", type=Path)  # required by --arms all, refused by --arms situation (v2.run_arms)
    short.add_argument("--arms", choices=("all", "situation"), default="all")
    short.add_argument("--out", type=Path, required=True)
    comp = sub.add_parser("compose")
    comp.add_argument("--memory", type=Path, required=True)
    comp.add_argument("--picks-v21a", type=Path)
    comp.add_argument("--picks-v21ca", type=Path)
    comp.add_argument("--arms", choices=("all", "situation"), default="all")
    comp.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return {"shortlists": cmd_shortlists, "compose": cmd_compose}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
