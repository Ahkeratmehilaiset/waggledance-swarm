"""JOIN card v0.6: the one text a joining agent reads first, at most 2 KB. Sandbox, advisory, never a gate.

The operator's frame (2026-10-14 ~18:28Z): "minä kuulen, minä näen, minä ymmärrän, minä muistan - ja maind oppii"
(I hear, I see, I understand, I remember, and the Mind learns). ``card(lane, delivery, view, remember, learn_ref=...,
learn=...)`` renders one lane's card from outputs that are already verified and guarded upstream. It derives nothing
those outputs do not state:

* HEAR        ``join_card_delivery.deliver`` (semantics v1.5): this lane's waits over 30 min on a slot it owns, new,
              unchanged, carried, unknown and below the threshold, with the first new wait's receipts and identity.
              The delivery must be of this same view (same schema, as_of and canonical view digest), else the card
              is refused.
* SEE         the step-1 lane view (``wd.mind-lane-view.v1a``): the PRs where this lane owns an empty slot, in the
              view's own order (the only empty slot first, then the oldest wait), at most ``SEE_LIMIT``.
* SLOT        (Grok f3206f6e) one fixed key=value line for the first SEE PR: pr, the full head, the credited slots at
              that head with their refs, whether this lane is among them, veto, CI at that head and
              ``last_open_slot``. The lane view makes an action record only when every required check is SUCCESS at
              that head and no veto is observed. ``last_open_slot`` is yes only when this lane's slot is the PR's
              only empty slot, the view's coverage is complete and the delivery reads the card as current; UNKNOWN
              (with the reason) when one of the last two fails; otherwise no.
* UNDERSTAND  derived from SLOT alone: the last open slot, an open slot among others, or UNKNOWN. It never names an
              outcome of a vote or of the gate; the canonical tools decide.
* REMEMBER    a REMEMBER v2 answer (frozen 2699c0fe; the PRIMARY arm is v2ca) inside a turn-bound envelope
              (``remember_envelope``): the joiner's picks with their TOPIC, CLAIMED SUPERSEDED and CONTESTED marks, or
              that nothing applies, or the refusal reason, and one citation ``<version> <arm> answer=<12>
              situation=<12>``. The envelope must name this lane, the view's as_of and a shown arm (``SHOWN_ARMS``:
              the joined v2a or v2ca, never v2c), and its answer must hash (``lane_view.canonical``) to its
              ``answer_sha256``; otherwise the card is refused (RCO1 probe P4b). That digest is self-consistency, not
              origin, and ``situation_sha256`` is cited, not verified: the card cannot see the situation text. With no
              envelope there is no REMEMBER line at all (P2b: an absence is omitted, never an uncited line).
* LEARN       (v0.5) with a turn-bound LEARN envelope (``learn_envelope``): the LEARN notes for this lane as
              ``learn_step.notes`` writes them, a warning line under each shown REMEMBER pick that
              ``learn_step.lesson_order`` warns, and one citation ``LEARN v1.1 [state=<12> params=<12> learn=<12>]
              notes <k> of <n>, warnings <k> of <n>`` (v0.6) with the LEARN contract (``learn_ref``). Without an
              envelope the v0 line stays: the card records nothing and names the LEARN contract.

Every line carries refs: identities, SHAs, vote and event refs, lesson files, or the semantics and contract names
it comes from. The card is at most ``CARD_BYTES`` UTF-8 bytes: when it would not fit, LEARN notes (v0.5; praise before
harm), then REMEMBER picks after the first and then SEE PRs after the first are dropped whole, and each dropped count
is printed (the notes by ``notes <k> of <n>``). The whole text passes the
workspace's decoded guard; a hit refuses the card and nothing is returned. Pure: no clock, file, network, process or
bridge write; the same inputs give the same bytes.

v0.2 (2026-10-15, the evening audit): the delivery is bound to the exact view by its canonical digest (v0.1 compared
only schema and as_of), and every view record and delivery field the card reads is checked first, so a malformed
input is refused with a fixed message and never raises. The card still trusts what those checked fields state: it
re-derives no card trust, reason or time.

v0.3 (2026-10-16, fable-5's own probes of v0.2): the card echoed input text as it was, so a REMEMBER envelope whose
summary, kind or ref held LF, CR, U+2028 or ESC rendered a forged UNDERSTAND line of its own, and a lone surrogate in a
delivery field raised a raw UnicodeEncodeError from the size check. Every line the card can render (the first size
budget, before any line is dropped) is now checked: a control, a line or paragraph separator, a surrogate, or a format
character other than ZWJ and ZWNJ in any field refuses the card with a fixed message. The format characters are the
bidi controls and the invisible ones a reader may not see although they stay in the text, such as ZWSP, the BOM and
the tag block; refusing those follows Grok 1035d2a9's advice and is a policy choice, not a measured acceptance. So no
input can add or split a card line, reorder one with a bidi control or hide text in a format character, and none
raises a raw error. The check is on the card's own code points, and the card's presentation contract is plain text:
no rendered view is certified (Lead triage 7126b428). A Markdown renderer may join the lines into one paragraph,
where the bidi algorithm can reorder text across them, and may decode raw HTML or character references in a field;
what a given renderer does is UNKNOWN here (none was tested). How often genuine REMEMBER text holds one of these
characters, and so refuses the card, was not measured.

RCO1's leak map of v0.2 (2026-10-16) found the same echo in each REMEMBER mark, the version, the refusal reason and
the view's coverage, which the check above covers, and a raw UnicodeEncodeError from ``remember_envelope``, which
now refuses an answer with no canonical digest with the card's fixed message. Each field the card printed without a
type check (the view's coverage, an observed receipt's ref, the answer's version and reason, a topic's count and
days) must now have the type the card prints, or the card is refused.

v0.4 (2026-10-18, operator direct "polish the Mind in the sandbox, fix defects and missing code"): PRODUCT.md held
"a v2c result is never shown as an answer" as a process rule only, because the card could not see which REMEMBER arm
produced an answer (REMEMBER v2's answer carries the same version in every arm). The envelope
(``join-card-remember-v2``) now carries the arm, ``remember_envelope`` takes it as a required keyword, and the card
renders only the joined arms in ``SHOWN_ARMS`` and prints the arm in the citation. Any other arm, v2c included, is
refused with a fixed message, at the envelope and again at the card. The arm is declared by the turn runner, like
``situation_sha256``: the card checks the declaration, not which code produced the answer.

v0.5 (2026-10-18, operator direct "polish the diamond"): LEARN v1 (``learn_step.py``, a pure re-fold of RCO2's card
outcome results) reaches the card. The turn runner verifies the state and puts LEARN's own notes and warnings for this
lane into an envelope (``join-card-learn-v1``) whose ``learn_sha256`` covers every other field; the notes are seeded by
this view's canonical digest, so their kind order is bound to this exact view. The card refuses the envelope unless it
names this lane, this view's as_of and this view's digest, and its digest matches; then every note and warning must be
one LEARN v1 writes: its exact form, LEARN's own thresholds (``learn_step``'s constants), the arithmetic each holds
(the reasons sum to the wrong count, no count above its denominator, the cited rows), harm before praise within a
kind, each kind's notes together and in the order the seed gives, and a warning only for one of the first three
REMEMBER picks. The card cannot check the counts against the state, and ``state_sha256`` and ``params_sha256`` are
cited, not verified (the runner verifies them); ``learn_sha256`` is self-consistency, not origin. From Grok
7534c72f's review of the design (advisory; each point was checked against the source before it was taken): the digest
covers the whole envelope, the hashed REMEMBER order is never changed (LEARN's reorder of a warned lesson is not
applied here: the answer digest would then name an order the card does not show, and a warning would choose which pick
the size bound keeps), so a warning is its own line under its pick and goes with it, and LEARN notes are the first thing
the size bound drops, praise before harm: no LEARN count is calibrated or a benefit claim, so none displaces a lesson
or a PR. With an envelope, the citation line is always printed, also when every note was dropped.

v0.6 (2026-10-18, D11, operator direct "polish the Mind in the sandbox, fix defects and missing code"): a supersede is
a claim. A lesson's ``supersedes:`` line is text its writer controls and nothing verifies, and REMEMBER already prints
it as ``[CLAIMED SUPERSEDED by <file> (unverified claim)]`` (claude-rco-1 round 7, fixed as D5 on the REMEMBER
branch), while the card still printed ``[SUPERSEDED by <file>]``, which states the claim as a fact. The card now
prints REMEMBER's mark. It cannot check the claim; it only stops wording it as a fact. LEARN v1.1 (D11, from
claude-rco-1's round 9 finding) counts a lesson's evidence in distinct actions on two or more PRs, so a warning is now
``LEARN: on <c> cards where you picked this, wrong <w> of taken <t> actions, on <p> PRs (rows <h1>, <h2>); association
only``, and the card checks LEARN v1.1's rule: c >= LESSON_SUPPORT, LESSON_WRONG <= w <= t, 2w >= t, LESSON_PRS <= p
<= w and two cited rows. v0.5's card-counted form is refused. The citation names LEARN v1.1; the fixed refusal texts
keep their v0.5 words.

usage: python -B sandbox/mind/join_card.py --lane <lane> --view <lane-view.json> --delivery <delivery.json>
       [--remember <envelope.json>] [--learn <learn-envelope.json>] --learn-ref <ref>
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1]
for _path in (str(SRC), str(HERE), str(HERE / "step1")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
from tools import bridge_mind_workspace as ws  # noqa: E402
import join_card_delivery as jcd  # noqa: E402
import lane_view as lv  # noqa: E402
import learn_step as ls  # noqa: E402  (v0.5: LEARN's own constants and kind rotation, never its state)

VERSION = "join-card-v0.6"
ENVELOPE_SCHEMA = "join-card-remember-v2"
ENVELOPE_KEYS = frozenset({"schema", "lane", "as_of_utc", "arm", "situation_sha256", "answer_sha256", "answer"})
# v0.4: the joined REMEMBER v2 arms (the joiner judges a shortlist). v2c (code only, no joiner) is NOT YET and is
# never shown as an answer (PRODUCT.md).
SHOWN_ARMS = ("v2a", "v2ca")
ARM_REFUSAL = "the REMEMBER answer is not from a shown arm (v2a or v2ca); a v2c result is never shown as an answer"
HEX64 = re.compile(r"[0-9a-f]{64}")
CARD_BYTES = 2048
SEE_LIMIT = 3
PICK_LIMIT = 3
UNKNOWN = ws.UNKNOWN
REMEMBER_STATUS = ("ANSWER", "ABSTAIN", "REFUSED", "DEGRADED")
SUMMARY_CHARS = 150  # REMEMBER v2's own cut (remember_v2.SUMMARY_CHARS at 2699c0fe)
REF_CHARS = 200
LEARN_REF = re.compile(r"[0-9A-Za-z][0-9A-Za-z:._/-]{3,79}")
SLOT_OF = {lane: slot for slot, lane in lv.SLOT_OWNER.items()}
# Budgets tried in order until the card fits: (SEE PRs, REMEMBER picks).
BUDGETS = ((SEE_LIMIT, PICK_LIMIT), (SEE_LIMIT, 2), (SEE_LIMIT, 1), (2, 1), (1, 1))
LEDGER_STATES = ("alert", "below_threshold", "unknown")
# v0.3: no card line may hold a control (Cc: C0, DEL, C1), a format character (Cf: the bidi marks, embeddings,
# overrides and isolates, and the invisible ones such as ZWSP, the word joiner, the BOM and the tag block) other than
# the two joiners that scripts and emoji need, a line or paragraph separator (Zl, Zp) or a surrogate (Cs: a lone one
# is not UTF-8 text). The categories come from the interpreter's Unicode data (15.1.0 on Python 3.13.7).
UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})
JOINERS = frozenset({chr(0x200C), chr(0x200D)})  # ZWNJ and ZWJ
LINE_REFUSAL = "a card line holds a control, format, line-break or surrogate character; nothing is returned"
RECEIPT_STATES = ("OBSERVED", "NONE", UNKNOWN)
# v0.5: the LEARN envelope and the notes and warnings LEARN v1 writes (learn_step.notes and learn_step.lesson_order)
LEARN_SCHEMA = "join-card-learn-v1"
LEARN_KEYS = frozenset({"schema", "lane", "as_of_utc", "seed", "state_sha256", "params_sha256", "notes", "warnings",
                        "learn_sha256"})
LEARN_DIGESTS = ("seed", "state_sha256", "params_sha256", "learn_sha256")
LEARN_REFUSAL = "the LEARN input is not a turn-bound LEARN envelope"
NOTE_REFUSAL = "a LEARN note is not a note LEARN v1 writes"
WARNING_REFUSAL = "a LEARN warning is not one LEARN v1 writes for one of the first three REMEMBER picks"
LEARN_KINDS = tuple(sorted({ls.action_kind(slot) for slot in ls.BUILD_SLOTS + ls.RCO_SLOTS}))
LEARN_REASONS = ls.WRONG_REASONS + ("other",)  # learn_step.summarize writes the reasons in this order
NOTE_LIMIT = 2 * len(LEARN_KINDS)  # at most one harm and one praise note per kind
NOTE_CHARS = 240  # more than any note or warning LEARN v1.1 writes; checked before a pattern is tried
_COUNT = r"(0|[1-9][0-9]{0,5})"
_ROWS = r"rows ([0-9a-f, ]{8,40})"
WRONG_NOTE = re.compile(rf"LEARN: similar ([a-z_]{{1,20}}) wrong {_COUNT} of taken {_COUNT} \(([0-9a-z_, ]{{1,99}}); "
                        rf"{_ROWS}\)")
CORRECT_NOTE = re.compile(rf"LEARN: similar ([a-z_]{{1,20}}) correct {_COUNT} of taken {_COUNT} \({_ROWS}\)"
                          rf"(?:; median read-to-action {_COUNT} min over {_COUNT})?")
LESSON_WARNING = re.compile(rf"LEARN: on {_COUNT} cards where you picked this, wrong {_COUNT} of taken {_COUNT} "
                            rf"actions, on {_COUNT} PRs \({_ROWS}\); association only")  # v0.6: LEARN v1.1's form
REASON = re.compile(r"([1-9][0-9]{0,5}) ([a-z_]{1,20})")
ROW = re.compile(r"[0-9a-f]{8}")


class JoinCardError(ValueError):
    """Fixed messages that never echo input content."""


# --- inputs -----------------------------------------------------------------------------------------------------------

def _check_inputs(lane: Any, delivery: Any, view: Any, remember: Any, learn_ref: Any, learn: Any = None) -> None:
    if lane not in lv.LANES:
        raise JoinCardError("the lane is not a fleet lane")
    if not isinstance(view, Mapping) or view.get("schema") != lv.SCHEMA or not isinstance(view.get("prs"), Mapping) \
            or not isinstance(view.get("as_of_utc"), str) or not isinstance(view.get("abstained"), list) \
            or not isinstance(view.get("coverage", UNKNOWN), str) or jcd.view_digest(view) is None:
        raise JoinCardError("the view is not a lane view v1a")
    _check_view_records(view)
    if not isinstance(delivery, Mapping) or delivery.get("schema") != jcd.SCHEMA \
            or delivery.get("semantics") != jcd.SEMANTICS or not isinstance(delivery.get("ledger"), list) \
            or not isinstance(delivery.get("card"), Mapping):
        raise JoinCardError("the delivery is not a JOIN-card delivery v1")
    _check_delivery(delivery)
    if not _same_view(delivery, view):
        raise JoinCardError("the delivery was made from another view")
    if remember is not None:
        _check_envelope(remember, lane, view["as_of_utc"])
    if not isinstance(learn_ref, str) or not LEARN_REF.fullmatch(learn_ref):
        raise JoinCardError("the LEARN contract ref is invalid")
    if learn is not None:
        _check_learn(learn, lane, view, remember)


def _check_view_records(view: Mapping[str, Any]) -> None:
    """v0.2: every view PR record is read as the delivery reads it (its S1), so a malformed one refuses."""
    try:
        for key, record in view["prs"].items():
            jcd._condition(key, record)
    except jcd.DeliveryError:
        raise JoinCardError("the view is not a lane view v1a") from None


def _check_delivery(delivery: Mapping[str, Any]) -> None:
    """v0.2: every delivery field the card reads has the type the card reads."""
    if not isinstance(delivery.get("now_utc"), str) or not isinstance(delivery.get("card_trust"), str):
        raise JoinCardError("the delivery is not a JOIN-card delivery v1")
    if not all(_ledger_row_ok(row) for row in delivery["ledger"]):
        raise JoinCardError("a delivery ledger row is not a JOIN-card delivery row")


def _ledger_row_ok(row: Any) -> bool:
    """One ledger row with each field the HEAR line reads; an unknown row has a reason and is never shown, any
    other row has its whole-second wait, and an observed receipt has its text ref (v0.3)."""
    if not isinstance(row, Mapping) or row.get("state") not in LEDGER_STATES:
        return False
    receipts = row.get("receipts")
    if not (all(isinstance(row.get(key), str) for key in ("lane", "slot", "waiting_since_utc"))
            and type(row.get("pr")) is int and isinstance(row.get("identity"), str) and HEX64.fullmatch(row["identity"])
            and all(type(row.get(key)) is bool for key in ("shown", "previously_notified", "carried"))
            and isinstance(receipts, Mapping)
            and all(isinstance(receipts.get(kind), Mapping) and receipts[kind].get("state") in RECEIPT_STATES
                    and (receipts[kind]["state"] != "OBSERVED" or isinstance(receipts[kind].get("ref"), str))
                    for kind in jcd.KINDS)):
        return False
    if row["state"] == "unknown":
        return isinstance(row.get("reason"), str) and row["shown"] is False
    return type(row.get("wait_seconds")) is int


def _same_view(delivery: Mapping[str, Any], view: Mapping[str, Any]) -> bool:
    """v0.2: the delivery was made from exactly this view: the same schema, as_of and canonical view digest."""
    card, digest = delivery["card"], jcd.view_digest(view)
    return digest is not None and card.get("schema") == view["schema"] \
        and card.get("as_of_utc") == view["as_of_utc"] and card.get("view_canonical_sha256") == digest


def remember_envelope(lane: str, as_of_utc: str, situation_sha256: str, answer: Any, *, arm: str) -> dict[str, Any]:
    """The turn-bound envelope a turn runner puts around one REMEMBER v2 answer for one lane's card. v0.3: an answer
    with no canonical digest (a lone surrogate, NaN, a set, bytes, deep nesting) is refused with the card's fixed
    message, never a raw error. v0.4: the runner names the arm that produced the answer; only ``SHOWN_ARMS``."""
    if not (isinstance(arm, str) and arm in SHOWN_ARMS):
        raise JoinCardError(ARM_REFUSAL)
    try:
        digest = lv.sha256_text(lv.canonical(answer))
    except (TypeError, ValueError, RecursionError):
        raise JoinCardError("the REMEMBER answer is not canonical JSON") from None
    return {"schema": ENVELOPE_SCHEMA, "lane": lane, "as_of_utc": as_of_utc, "arm": arm,
            "situation_sha256": situation_sha256, "answer_sha256": digest, "answer": answer}


def _check_envelope(envelope: Any, lane: str, as_of_utc: str) -> None:
    """RCO1 P4b: only an answer made for this lane at this view's as_of renders; the digest is self-consistency."""
    if not isinstance(envelope, Mapping) or set(envelope) != ENVELOPE_KEYS or envelope.get("schema") != ENVELOPE_SCHEMA:
        raise JoinCardError("the REMEMBER input is not a turn-bound REMEMBER envelope")
    if not (isinstance(envelope["arm"], str) and envelope["arm"] in SHOWN_ARMS):
        raise JoinCardError(ARM_REFUSAL)
    if envelope["lane"] != lane:
        raise JoinCardError("the REMEMBER answer was made for another lane")
    if envelope["as_of_utc"] != as_of_utc:
        raise JoinCardError("the REMEMBER answer was made for another turn")
    if not all(isinstance(envelope[key], str) and HEX64.fullmatch(envelope[key])
               for key in ("situation_sha256", "answer_sha256")):
        raise JoinCardError("a REMEMBER digest is not 64 lowercase hex")
    try:
        digest = lv.sha256_text(lv.canonical(envelope["answer"]))
    except (TypeError, ValueError, RecursionError):
        raise JoinCardError("the REMEMBER answer is not canonical JSON") from None
    if digest != envelope["answer_sha256"]:
        raise JoinCardError("the REMEMBER answer does not match its digest")
    answer = envelope["answer"]
    if not isinstance(answer, Mapping) or answer.get("status") not in REMEMBER_STATUS \
            or not isinstance(answer.get("version", UNKNOWN), str) \
            or not (answer.get("reason") is None or isinstance(answer["reason"], str)) \
            or not isinstance(answer.get("results"), list) or not all(_result_ok(result) for result in answer["results"]):
        raise JoinCardError("the REMEMBER answer is not a REMEMBER v2 answer")


def _result_ok(result: Any) -> bool:
    """One REMEMBER v2 pick: string ref, kind and time; a summary within REMEMBER's own cut; bounded ref marks; a
    topic, when present, with the int count and the two string days its TOPIC mark prints (v0.3)."""
    if not isinstance(result, Mapping) or not all(isinstance(result.get(key), str) for key in ("ref", "kind", "time_utc")):
        return False
    topic = result.get("topic")
    if topic is not None and not (isinstance(topic, Mapping) and type(topic.get("records")) is int
                                  and all(isinstance(topic.get(key), str) for key in ("first_day", "last_day"))):
        return False
    lists = [result.get(key) or [] for key in ("topic_others", "contested_with")]
    if not all(isinstance(value, list) for value in lists):
        return False
    refs = [result["ref"], *lists[0], *lists[1]] + ([result["superseded_by"]] if result.get("superseded_by") else [])
    summary = result.get("summary")
    return all(isinstance(ref, str) and 0 < len(ref) <= REF_CHARS for ref in refs) \
        and (summary is None or (isinstance(summary, str) and len(summary) <= SUMMARY_CHARS))


def learn_envelope(lane: str, as_of_utc: str, seed: str, notes: Any, warnings: Any, *, state_sha256: str,
                   params_sha256: str) -> dict[str, Any]:
    """v0.5: the turn-bound envelope a turn runner puts around LEARN v1's notes (``learn_step.notes(state, lane,
    seed)``) and warnings (``learn_step.lesson_order(state, lane, refs)[1]``) for one lane's card; ``seed`` is the
    view's canonical digest. ``learn_sha256`` covers every other field. A value with no canonical digest is refused
    with a fixed message, never a raw error."""
    envelope = {"schema": LEARN_SCHEMA, "lane": lane, "as_of_utc": as_of_utc, "seed": seed,
                "state_sha256": state_sha256, "params_sha256": params_sha256, "notes": notes, "warnings": warnings}
    try:
        digest = lv.sha256_text(lv.canonical(envelope))
    except (TypeError, ValueError, RecursionError):
        raise JoinCardError("the LEARN envelope is not canonical JSON") from None
    return dict(envelope, learn_sha256=digest)


def _check_learn(envelope: Any, lane: str, view: Mapping[str, Any], remember: Mapping[str, Any] | None) -> None:
    """v0.5: only LEARN v1's notes for this lane, this view's as_of and this view's digest render (module docstring)."""
    if not isinstance(envelope, Mapping) or set(envelope) != LEARN_KEYS or envelope.get("schema") != LEARN_SCHEMA:
        raise JoinCardError(LEARN_REFUSAL)
    if envelope["lane"] != lane:
        raise JoinCardError("the LEARN envelope was made for another lane")
    if envelope["as_of_utc"] != view["as_of_utc"]:
        raise JoinCardError("the LEARN envelope was made for another turn")
    if not all(isinstance(envelope[key], str) and HEX64.fullmatch(envelope[key]) for key in LEARN_DIGESTS):
        raise JoinCardError("a LEARN digest is not 64 lowercase hex")
    try:
        digest = lv.sha256_text(lv.canonical({key: value for key, value in envelope.items() if key != "learn_sha256"}))
    except (TypeError, ValueError, RecursionError):
        raise JoinCardError("the LEARN envelope is not canonical JSON") from None
    if digest != envelope["learn_sha256"]:
        raise JoinCardError("the LEARN envelope does not match its digest")
    if envelope["seed"] != jcd.view_digest(view):
        raise JoinCardError("the LEARN envelope was made for another view")
    if not _notes_ok(envelope["notes"], lane, envelope["seed"]):
        raise JoinCardError(NOTE_REFUSAL)
    warnings = envelope["warnings"]
    top = _warnable_refs(remember)
    if not isinstance(warnings, Mapping) or not all(isinstance(ref, str) and ref in top and _warning_ok(text)
                                                    for ref, text in warnings.items()):
        raise JoinCardError(WARNING_REFUSAL)


def _rows_ok(rows: str, count: int) -> bool:
    """The cited row hashes: as many as LEARN cites (its newest two), each 8 lowercase hex."""
    cited = rows.split(", ")
    return len(cited) == min(2, count) and all(ROW.fullmatch(row) for row in cited)


def _note(note: Any) -> tuple[str, str] | None:
    """The kind and sense ("wrong" or "correct") of a note LEARN v1 writes, else None: its exact form, LEARN's own
    thresholds and the arithmetic every such note holds. The counts themselves cannot be checked here."""
    if not isinstance(note, str) or len(note) > NOTE_CHARS:
        return None
    match = WRONG_NOTE.fullmatch(note)
    if match:
        kind, wrong, taken, reasons, rows = match.groups()
        wrong, taken = int(wrong), int(taken)
        parts = [REASON.fullmatch(part) for part in reasons.split(", ")]
        if not all(parts):
            return None
        names = [part.group(2) for part in parts]
        ok = (names == [name for name in LEARN_REASONS if name in names]
              and sum(int(part.group(1)) for part in parts) == wrong
              and ls.MIN_WRONG <= wrong <= taken and taken >= ls.MIN_TAKEN and _rows_ok(rows, wrong))
        return (kind, "wrong") if ok and kind in LEARN_KINDS else None
    match = CORRECT_NOTE.fullmatch(note)
    if match:
        kind, correct, taken, rows, _median, timed = match.groups()
        correct, taken = int(correct), int(taken)
        ok = (ls.MIN_CORRECT <= correct <= taken and taken >= ls.MIN_TAKEN and _rows_ok(rows, correct)
              and (timed is None or ls.MIN_TIMED <= int(timed) <= correct))
        return (kind, "correct") if ok and kind in LEARN_KINDS else None
    return None


def _notes_ok(notes: Any, lane: str, seed: str) -> bool:
    """Notes in the shape ``learn_step.notes`` writes: per kind a harm note, a praise note or both in that order, each
    kind's notes together, and the kinds in the order this seed rotates them to when both have notes."""
    if not isinstance(notes, list) or len(notes) > NOTE_LIMIT:
        return False
    parsed = [_note(note) for note in notes]
    if not all(parsed):
        return False
    kinds = [kind for kind, _ in parsed]
    order = list(dict.fromkeys(kinds))
    if kinds != sorted(kinds, key=order.index):
        return False
    if not all([sense for each, sense in parsed if each == kind] in (["wrong"], ["correct"], ["wrong", "correct"])
               for kind in order):
        return False
    return len(order) < len(LEARN_KINDS) or order == ls.rotate(list(LEARN_KINDS), seed, lane)


def _warning_ok(text: Any) -> bool:
    """A warning ``learn_step.lesson_order`` writes: its form, LEARN v1.1's own warning rule and the cited rows."""
    match = LESSON_WARNING.fullmatch(text) if isinstance(text, str) and len(text) <= NOTE_CHARS else None
    if match is None:
        return False
    cards, wrong, taken, prs = (int(match.group(number)) for number in range(1, 5))
    return cards >= ls.LESSON_SUPPORT and ls.LESSON_WRONG <= wrong <= taken and 2 * wrong >= taken \
        and ls.LESSON_PRS <= prs <= wrong and _rows_ok(match.group(5), wrong)


def _warnable_refs(remember: Mapping[str, Any] | None) -> list[str]:
    """The refs LEARN may warn: the first ``learn_step.LESSON_TOP`` picks the card shows; none for a refusal."""
    if remember is None or remember["answer"]["status"] == "REFUSED":
        return []
    return [result["ref"] for result in remember["answer"]["results"][:ls.LESSON_TOP]]


def lane_records(view: Mapping[str, Any], lane: str) -> tuple[str | None, list[Mapping[str, Any]]]:
    """The lane's slot and the action records where it is empty, in the lane view's own line order."""
    slot = SLOT_OF.get(lane)
    if slot is None:
        return None, []
    rows = [record for record in view["prs"].values() if isinstance(record, Mapping)
            and record.get("state") == "action" and slot in (record.get("empty_slots") or [])]
    return slot, sorted(rows, key=lambda record: (0 if len(record["empty_slots"]) == 1 else 1,
                                                  str(record.get("waiting_since_utc")), record["pr"]))


# --- the lines --------------------------------------------------------------------------------------------------------

def _receipt(state: Mapping[str, Any]) -> str:
    if state.get("state") == "OBSERVED":
        return str(state.get("ref"))[:12]
    return "none" if state.get("state") == "NONE" else UNKNOWN


def hear_line(delivery: Mapping[str, Any], lane: str) -> str:
    rows = [row for row in delivery["ledger"] if isinstance(row, Mapping) and row.get("lane") == lane]
    new = [row for row in rows if row.get("shown")]
    unknown = [row for row in rows if row.get("state") == "unknown"]
    text = (f"HEAR waits over 30 min on your slots: new {len(new)}, unchanged "
            f"{sum(row.get('state') == 'alert' and bool(row.get('previously_notified')) for row in rows)}, carried "
            f"{sum(bool(row.get('carried')) for row in rows)}, unknown {len(unknown)}, below "
            f"{sum(row.get('state') == 'below_threshold' for row in rows)}")
    if new:
        row = new[0]
        receipts = " ".join(f"{kind}={_receipt(row['receipts'][kind])}" for kind in jcd.KINDS)
        text += (f"; first pr={row['pr']} slot={row['slot']} wait_min={row['wait_seconds'] // 60} "
                 f"since={row['waiting_since_utc']} {receipts} id={row['identity'][:12]}")
    elif unknown:
        text += f"; first unknown pr={unknown[0]['pr']} reason={unknown[0].get('reason', UNKNOWN)}"
    return text + f" ({jcd.SEMANTICS}, card_trust {delivery.get('card_trust', UNKNOWN)})"


def see_line(view: Mapping[str, Any], slot: str | None, records: Sequence[Mapping[str, Any]], limit: int) -> str:
    tail = f"abstained {len(view['abstained'])} (UNKNOWN reasons in the view), coverage {view.get('coverage', UNKNOWN)}"
    if slot is None:
        return f"SEE this lane owns no vote slot; {tail}"
    if not records:
        return f"SEE no PR with your {slot} slot empty; {tail}"
    items = []
    for record in records[:limit]:
        credited = ",".join(f"{vote['slot']}:{str(vote['ref'])[:12]}" for vote in record.get("credited") or [])
        items.append(f"pr{record['pr']}@{str(record['github_head'])[:12]} empty={','.join(record['empty_slots'])} "
                     f"credited={credited or 'none'} ci={record.get('checks') or UNKNOWN}")
    more = f"; +{len(records) - limit} more PRs not shown" if len(records) > limit else ""
    return "SEE " + "; ".join(items) + more + f"; {tail}"


def slot_fields(view: Mapping[str, Any], delivery: Mapping[str, Any], slot: str,
                record: Mapping[str, Any]) -> dict[str, Any]:
    """Grok f3206f6e's conjunction, every field read from the lane view record or the delivery's card trust."""
    credited = record.get("credited") or []
    you_in_set = any(vote.get("slot") == slot for vote in credited)
    if view.get("coverage") != "complete":
        flag, why = UNKNOWN, "view_coverage_not_complete"
    elif delivery.get("card_trust") != "current":
        flag, why = UNKNOWN, str(delivery.get("card_trust"))
    else:
        flag, why = ("yes" if record["empty_slots"] == [slot] and not you_in_set else "no"), None
    return {"pr": record["pr"], "head": record["github_head"], "slot": slot, "credited": credited,
            "you_in_set": "yes" if you_in_set else "no", "ci": record.get("checks") or UNKNOWN,
            "others": [other for other in record["empty_slots"] if other != slot], "flag": flag, "why": why}


def slot_line(fields: Mapping[str, Any] | None) -> str:
    if fields is None:
        return "SLOT none: no PR in the view has your slot empty"
    credited = ",".join(f"{vote['slot']}:{str(vote['ref'])[:12]}" for vote in fields["credited"])
    return (f"SLOT pr={fields['pr']} head={fields['head']} credited_at_head={len(fields['credited'])}({credited}) "
            f"you_in_set={fields['you_in_set']} veto=none_observed ci_at_head={fields['ci']} "
            f"last_open_slot={fields['flag']}" + (f" reason={fields['why']}" if fields["why"] else ""))


def understand_line(fields: Mapping[str, Any] | None) -> str:
    if fields is None:
        return "UNDERSTAND nothing of yours is open in this view; nothing is derived"
    head = str(fields["head"])[:12]
    if fields["flag"] == UNKNOWN:
        return f"UNDERSTAND UNKNOWN for PR{fields['pr']} at {head} ({fields['why']}): no step is derived from this card"
    if fields["flag"] == "yes":
        return (f"UNDERSTAND your {fields['slot']} vote is the last open slot on PR{fields['pr']} at {head}: CI "
                f"{fields['ci']} at that head, no veto observed, {len(fields['credited'])} slots filled; the canonical "
                "tools decide what follows")
    return (f"UNDERSTAND PR{fields['pr']} at {head}: your {fields['slot']} slot is open and so is "
            f"{', '.join(fields['others'])}; the canonical tools decide what follows")


def remember_lines(envelope: Mapping[str, Any] | None, limit: int,
                   warnings: Mapping[str, str] | None = None) -> list[str]:
    """The REMEMBER lines in the answer's own (hashed) order; v0.5: a LEARN warning for a shown pick is its own line
    right under that pick, never a reorder."""
    warnings = warnings or {}
    if envelope is None:
        return []  # P2b: no REMEMBER input, no line
    remember = envelope["answer"]
    cite = (f"[{remember.get('version', UNKNOWN)} {envelope['arm']} answer={envelope['answer_sha256'][:12]} "
            f"situation={envelope['situation_sha256'][:12]}]")
    status, reason, results = remember["status"], remember.get("reason"), remember["results"]
    if status == "REFUSED":
        return [f"REMEMBER refused ({reason}) {cite}"]
    degraded = " degraded (a memory cell is quarantined, fallback)" if status == "DEGRADED" else ""
    if not results:
        return [f"REMEMBER{degraded} nothing applies ({reason}) {cite}"]
    lines = []
    for number, result in enumerate(results[:limit], 1):
        marks = ""
        topic = result.get("topic")
        if isinstance(topic, Mapping) and isinstance(topic.get("records"), int) and topic["records"] > 1:
            marks += (f" [TOPIC {topic['records']} records {topic.get('first_day')}..{topic.get('last_day')}, may "
                      f"disagree; also shown: {', '.join(result.get('topic_others') or []) or 'none'}]")
        if result.get("superseded_by"):  # v0.6: a claim nothing verifies, worded as REMEMBER words it (D5)
            marks += f" [CLAIMED SUPERSEDED by {result['superseded_by']} (unverified claim)]"
        if result.get("contested_with"):
            marks += f" [CONTESTED with {', '.join(result['contested_with'])}]"
        summary = f": {result['summary']}" if isinstance(result.get("summary"), str) and result["summary"] else ""
        lines.append(f"REMEMBER{degraded if number == 1 else ''} [{number}] {result['ref']} ({result['kind']}, "
                     f"{result['time_utc'][:10]}){marks}{summary}")
        if number <= ls.LESSON_TOP and result["ref"] in warnings:
            lines.append(f"LEARN on REMEMBER [{number}]: {warnings[result['ref']][len('LEARN: '):]}")
    more = f"; +{len(results) - limit} more picks not shown" if len(results) > limit else ""
    lines.append(f"REMEMBER picks {cite}{more}")
    return lines


def learn_lines(learn: Mapping[str, Any] | None, keep: int, warned: int, learn_ref: str) -> list[str]:
    """v0.5: the ``keep`` LEARN notes with the highest priority (harm notes first, then praise notes) in LEARN's own
    order, then the citation; without an envelope the v0 line."""
    if learn is None:
        return [f"LEARN v0 records nothing from this card; LEARN contract {learn_ref}"]
    notes = learn["notes"]
    senses = [(_note(note) or ("", ""))[1] for note in notes]
    priority = [i for i, sense in enumerate(senses) if sense == "wrong"] + \
        [i for i, sense in enumerate(senses) if sense != "wrong"]
    kept = sorted(priority[:keep])
    return [notes[i] for i in kept] + [
        f"LEARN v1.1 [state={learn['state_sha256'][:12]} params={learn['params_sha256'][:12]} "
        f"learn={learn['learn_sha256'][:12]}] notes {len(kept)} of {len(notes)}, warnings {warned} of "
        f"{len(learn['warnings'])}; LEARN contract {learn_ref}"]


def _warned(remember: Mapping[str, Any] | None, limit: int, warnings: Mapping[str, str]) -> int:
    """How many warned refs are among the picks a budget shows (each warning is for one of the first three)."""
    return len(set(_warnable_refs(remember)[:limit]) & set(warnings))


# --- the card ---------------------------------------------------------------------------------------------------------

def _line_unsafe(char: str) -> bool:
    """v0.3: a character that could add, split or visually reorder a card line, or hide text in it."""
    return unicodedata.category(char) in UNSAFE_CATEGORIES and char not in JOINERS


def card(lane: str, delivery: Mapping[str, Any], view: Mapping[str, Any], remember: Mapping[str, Any] | None = None,
         *, learn_ref: str, learn: Mapping[str, Any] | None = None) -> str:
    """One lane's JOIN card text (module docstring). Raises JoinCardError, never returns a partial card."""
    _check_inputs(lane, delivery, view, remember, learn_ref, learn)
    slot, records = lane_records(view, lane)
    fields = slot_fields(view, delivery, slot, records[0]) if records else None
    header = (f"JOIN {lane} as_of={view['as_of_utc']} now={delivery.get('now_utc', UNKNOWN)} {VERSION}; advisory, "
              "not a gate; check the canonical tools before any vote")
    warnings = learn["warnings"] if learn is not None else {}
    notes = len(learn["notes"]) if learn is not None else 0

    def lines_within(see_limit: int, pick_limit: int, keep: int) -> list[str]:
        return [header, hear_line(delivery, lane), see_line(view, slot, records, see_limit), slot_line(fields),
                understand_line(fields), *remember_lines(remember, pick_limit, warnings),
                *learn_lines(learn, keep, _warned(remember, pick_limit, warnings), learn_ref)]

    # v0.5: LEARN notes go first (praise before harm, learn_lines), then the v0.4 budgets with no note
    budgets = [(*BUDGETS[0], keep) for keep in range(notes, -1, -1)] + [(*budget, 0) for budget in BUDGETS[1:]]
    # v0.3: the first budget renders every line the card can show; a smaller one only drops or counts whole items
    if any(_line_unsafe(char) for line in lines_within(*budgets[0]) for char in line):
        raise JoinCardError(LINE_REFUSAL)
    for see_limit, pick_limit, keep in budgets:
        lines = lines_within(see_limit, pick_limit, keep)
        text = "\n".join(lines) + "\n"
        if len(text.encode("utf-8")) <= CARD_BYTES:
            break
    else:
        raise JoinCardError("the card exceeds its size bound")
    if ws._has_marker(text):
        raise JoinCardError("a guarded word is in the card; nothing is returned")
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lane", required=True)
    parser.add_argument("--view", type=Path, required=True)
    parser.add_argument("--delivery", type=Path, required=True)
    parser.add_argument("--remember", type=Path)
    parser.add_argument("--learn", type=Path)
    parser.add_argument("--learn-ref", required=True)
    args = parser.parse_args(argv)
    try:
        remember = jcd._load(args.remember) if args.remember else None
        learn = jcd._load(args.learn) if args.learn else None
        text = card(args.lane, jcd._load(args.delivery), jcd._load(args.view), remember, learn_ref=args.learn_ref,
                    learn=learn)
    except (JoinCardError, jcd.DeliveryError, ValueError, OSError, RecursionError) as error:
        print(f"refused: {error if isinstance(error, (JoinCardError, jcd.DeliveryError)) else 'an input is unreadable'}")
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
