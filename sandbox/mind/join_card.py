"""JOIN card v0.3: the one text a joining agent reads first, at most 2 KB. Sandbox, advisory, never a gate.

The operator's frame (2026-10-14 ~18:28Z): "minä kuulen, minä näen, minä ymmärrän, minä muistan - ja maind oppii"
(I hear, I see, I understand, I remember, and the Mind learns). ``card(lane, delivery, view, remember, learn_ref=...)``
renders one lane's card from outputs that are already verified and guarded upstream. It derives nothing those
outputs do not state:

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
              (``remember_envelope``): the joiner's picks with their TOPIC, SUPERSEDED and CONTESTED marks, or that
              nothing applies, or the refusal reason, and one citation ``answer=<12> situation=<12>``. The envelope
              must name this lane and the view's as_of, and its answer must hash (``lane_view.canonical``) to its
              ``answer_sha256``; otherwise the card is refused (RCO1 probe P4b). That digest is self-consistency, not
              origin, and ``situation_sha256`` is cited, not verified: the card cannot see the situation text. With no
              envelope there is no REMEMBER line at all (P2b: an absence is omitted, never an uncited line).
* LEARN       v0 learns and records nothing; the line names the LEARN contract (``learn_ref``) the next step uses.

Every line carries refs: identities, SHAs, vote and event refs, lesson files, or the semantics and contract names
it comes from. The card is at most ``CARD_BYTES`` UTF-8 bytes: when it would not fit, REMEMBER picks after the first
and then SEE PRs after the first are dropped whole, and each dropped count is printed. The whole text passes the
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

usage: python -B sandbox/mind/join_card.py --lane <lane> --view <lane-view.json> --delivery <delivery.json>
       [--remember <envelope.json>] --learn-ref <ref>
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

VERSION = "join-card-v0.3"
ENVELOPE_SCHEMA = "join-card-remember-v1"
ENVELOPE_KEYS = frozenset({"schema", "lane", "as_of_utc", "situation_sha256", "answer_sha256", "answer"})
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


class JoinCardError(ValueError):
    """Fixed messages that never echo input content."""


# --- inputs -----------------------------------------------------------------------------------------------------------

def _check_inputs(lane: Any, delivery: Any, view: Any, remember: Any, learn_ref: Any) -> None:
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


def remember_envelope(lane: str, as_of_utc: str, situation_sha256: str, answer: Any) -> dict[str, Any]:
    """The turn-bound envelope a turn runner puts around one REMEMBER v2 answer for one lane's card. v0.3: an answer
    with no canonical digest (a lone surrogate, NaN, a set, bytes, deep nesting) is refused with the card's fixed
    message, never a raw error."""
    try:
        digest = lv.sha256_text(lv.canonical(answer))
    except (TypeError, ValueError, RecursionError):
        raise JoinCardError("the REMEMBER answer is not canonical JSON") from None
    return {"schema": ENVELOPE_SCHEMA, "lane": lane, "as_of_utc": as_of_utc, "situation_sha256": situation_sha256,
            "answer_sha256": digest, "answer": answer}


def _check_envelope(envelope: Any, lane: str, as_of_utc: str) -> None:
    """RCO1 P4b: only an answer made for this lane at this view's as_of renders; the digest is self-consistency."""
    if not isinstance(envelope, Mapping) or set(envelope) != ENVELOPE_KEYS or envelope.get("schema") != ENVELOPE_SCHEMA:
        raise JoinCardError("the REMEMBER input is not a turn-bound REMEMBER envelope")
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


def remember_lines(envelope: Mapping[str, Any] | None, limit: int) -> list[str]:
    if envelope is None:
        return []  # P2b: no REMEMBER input, no line
    remember = envelope["answer"]
    cite = (f"[{remember.get('version', UNKNOWN)} answer={envelope['answer_sha256'][:12]} "
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
        if result.get("superseded_by"):
            marks += f" [SUPERSEDED by {result['superseded_by']}]"
        if result.get("contested_with"):
            marks += f" [CONTESTED with {', '.join(result['contested_with'])}]"
        summary = f": {result['summary']}" if isinstance(result.get("summary"), str) and result["summary"] else ""
        lines.append(f"REMEMBER{degraded if number == 1 else ''} [{number}] {result['ref']} ({result['kind']}, "
                     f"{result['time_utc'][:10]}){marks}{summary}")
    more = f"; +{len(results) - limit} more picks not shown" if len(results) > limit else ""
    lines.append(f"REMEMBER picks {cite}{more}")
    return lines


# --- the card ---------------------------------------------------------------------------------------------------------

def _line_unsafe(char: str) -> bool:
    """v0.3: a character that could add, split or visually reorder a card line, or hide text in it."""
    return unicodedata.category(char) in UNSAFE_CATEGORIES and char not in JOINERS


def card(lane: str, delivery: Mapping[str, Any], view: Mapping[str, Any], remember: Mapping[str, Any] | None = None,
         *, learn_ref: str) -> str:
    """One lane's JOIN card text (module docstring). Raises JoinCardError, never returns a partial card."""
    _check_inputs(lane, delivery, view, remember, learn_ref)
    slot, records = lane_records(view, lane)
    fields = slot_fields(view, delivery, slot, records[0]) if records else None
    header = (f"JOIN {lane} as_of={view['as_of_utc']} now={delivery.get('now_utc', UNKNOWN)} {VERSION}; advisory, "
              "not a gate; check the canonical tools before any vote")
    learn = f"LEARN v0 records nothing from this card; LEARN contract {learn_ref}"

    def lines_within(see_limit: int, pick_limit: int) -> list[str]:
        return [header, hear_line(delivery, lane), see_line(view, slot, records, see_limit), slot_line(fields),
                understand_line(fields), *remember_lines(remember, pick_limit), learn]

    # v0.3: the first budget renders every line the card can show; a smaller one only drops or counts whole items
    if any(_line_unsafe(char) for line in lines_within(*BUDGETS[0]) for char in line):
        raise JoinCardError(LINE_REFUSAL)
    for see_limit, pick_limit in BUDGETS:
        lines = lines_within(see_limit, pick_limit)
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
    parser.add_argument("--learn-ref", required=True)
    args = parser.parse_args(argv)
    try:
        remember = jcd._load(args.remember) if args.remember else None
        text = card(args.lane, jcd._load(args.delivery), jcd._load(args.view), remember, learn_ref=args.learn_ref)
    except (JoinCardError, jcd.DeliveryError, ValueError, OSError, RecursionError) as error:
        print(f"refused: {error if isinstance(error, (JoinCardError, jcd.DeliveryError)) else 'an input is unreadable'}")
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
