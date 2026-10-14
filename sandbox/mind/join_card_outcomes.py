"""LEARN slice 1: did a lane take the action its card showed, how fast, and was it right? Offline, never a gate.

Why: the Mind's join card should learn from what happens after a lane reads it. The night of 2026-10-13/14 showed
that "seen" is not "done": the step-1 lane view named the PR1905 build vote in 8 of 8 frozen cycles, but no card
was delivered or read, so that night cannot tell anything about read-to-action. This slice measures only what the
inputs prove and says UNKNOWN for everything else.

Row contract: claude-rco-2 ``ROW_CONTRACT_RCO2.v1.md`` (``CONTRACT_V1_SHA256``), frozen before this code, for the
request codex-lead-1/join-card-outcomes-20261014 (proposal 72823BAF, v0.1 DB362695 as reviewed by Lead); delta
``v1_1`` (``CONTRACT_V1_1_SHA256``: RCO2's finding (a) and a direct Grok xhigh consultation); and the corrected
``v1_2`` (``CONTRACT_SHA256``), registered before its semantics were implemented, which applies codex-lead-1's four
corrections (ABA currency, verified identity, first relevant action, coverage before any positive claim). No weights,
no ranking, no learning step: this slice records outcome rows and counts, nothing else. Its read receipts and the
delivery receipts of sandbox/mind/join_card_delivery.py are different contracts; no adapter is promised.

``outcomes(view_bytes, card_bytes, lane, issue, receipts, events, coverage, head_truth, eligibility, identity)`` is
pure: no clock, file, network, process or bridge write, and the same inputs in any order give the same
``result_bytes``. Inputs (explicit and immutable; a missing one is UNKNOWN, never a default):

* ``view_bytes``: one lane view (``wd.mind-lane-view.v1a``, sandbox/mind/step1/lane_view.py).
* ``card_bytes``: the card the lane was shown; it must be ``lane_view.render_text(view, lane)`` byte for byte.
* ``issue``: ``{schema, lane, card_sha256, view_sha256, nonce, issued_utc}``; ``receipts``: a list of
  ``{schema, lane, card_sha256, nonce, read_utc, reader}``.
* ``events``: ``{ref: body}`` or a list of ``{ref, body}``; a body counts only when ``event_id(body) == ref``.
* ``coverage``: ``[{from_utc, to_utc, complete, source, source_sha256}]``, the spans in which every event of the lane
  is claimed present.
* ``head_truth``: ``{kind: "interval", pr, head, from_utc, to_utc, complete, source, source_sha256}`` rows (the head
  was that SHA the whole time) and ``{kind: "sample", pr, head, observed_utc, source, source_sha256}`` point samples.
* ``eligibility``: ``[{pr, head, slot, lane, eligible, observed_utc, source, source_sha256}]``, independent gate truth.
* ``identity``: ``[{event_ref, agent, verified, method, source, source_sha256}]`` from an independent verifier.

Outcomes per ACTION line (first rule wins): UNKNOWN(receipt or issue reason); PRE_READ (any valid vote before the
read: reported, outside every denominator). Else the FIRST relevant action in the window decides, whether a valid
vote, a vote with another PR number or a vote bound to another head; later ones are listed and counted, never hidden
and never a rescue. Every claim from an action needs coverage from the view's time through it
(UNKNOWN(coverage_incomplete_before_action)) and a verified identity for its exact ref (UNKNOWN(identity_unverified)).
A valid vote is TAKEN_STALE or TAKEN_CORRECT/TAKEN_WRONG (independent eligibility) only when a complete head INTERVAL
proves the head at its time; point samples never verify (ABA) and give UNKNOWN(head_currency_unverified) with
``head_sampled``. A vote with another PR number is TAKEN_WRONG(pr_mismatch); one bound to another head is OTHER
(vote_at_live_head) when an interval proves that head live, TAKEN_WRONG(wrong_binding) when it proves another one.
OTHER or NONE only when coverage holds every moment from the view's time to the window's end, else
UNKNOWN(coverage_incomplete). Counts: ``follow_through`` = taken/known, ``correct_of_taken`` and ``harm_of_taken``
over taken, each as a numerator and a denominator, never a bare ratio, plus ``corrected_after_wrong`` and
``attempts_after_first``. An aggregate over several cards must count each ``event_ref`` once. Times are UTC instants
(``ws.parse_time``); durations are whole milliseconds, cut. OTHER and NONE still rest on agent labels (stated limit).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
if str(HERE / "step1") not in sys.path:
    sys.path.insert(0, str(HERE / "step1"))
import lane_view  # noqa: E402  (its import puts the repository root on sys.path)
from tools import bridge_mind_workspace as ws  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402

SCHEMA = "wd.mind-join-card-outcomes.v1"
SCOPE = "offline_outcome_rows_not_gate"
CONTRACT_V1_SHA256 = "c645ef27642b88ba750a3f0e055cbb0ad6b8c88580bb3a9188fe96f0362b7019"
CONTRACT_V1_1_SHA256 = "0cc935fbb9f476f1df3d9a08e74abe04bc8e78c8bd4f97febb0640cce3863b9c"
CONTRACT_SHA256 = "96223ad35f1a9e17787fe361ba5773c9d73af80d6301e56a7df2b6344cd401fa"  # v1.2, registered 19:15:29Z
ISSUE_SCHEMA = "wd.mind-card-issue.v1"
RECEIPT_SCHEMA = "wd.mind-card-read-receipt.v1"
UNKNOWN = ws.UNKNOWN
WINDOW = timedelta(seconds=7200)
MAX_TRUTH_GAP = timedelta(seconds=900)
VOTE_TYPE = "decision"
PASS_STATUS = {"build_lead": "build_consensus_pass", "build_tools": "build_consensus_pass",
               **{rco: "rco_pass" for rco in ws.RECOGNIZED_RCOS}}
TAKEN = ("TAKEN_CORRECT", "TAKEN_STALE", "TAKEN_WRONG")
KNOWN = TAKEN + ("OTHER", "NONE")
# The line wording lane_view.action_line writes; the record behind it must say the same.
LINE = re.compile(r"(build|RCO) vote on PR([0-9]+) at ([0-9a-f]{12}) is (?:the only empty slot|empty) "
                  r"\(([A-Za-z0-9_-]+), task ([^)]+)\)")
RECEIPT_ORDER = ("receipt_responder_mismatch", "receipt_digest_mismatch", "receipt_nonce_mismatch",
                 "receipt_before_issue", "receipt_invalid")
HISTORICAL_GAP = ("no card was issued to or read by this lane in this cycle: there is no issue record and no read "
                  "receipt, so no read-to-action outcome exists; an observed queue wait is a descriptive trace only, "
                  "never a no-card baseline or a read-to-action time")


class OutcomeError(ValueError):
    """Fixed messages that never echo input content."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_set(items: Iterable[Any]) -> str:
    """One order-free digest of a list of objects: the sorted canonical forms, hashed together."""
    return sha256_bytes("\n".join(sorted(canonical(item) for item in items)).encode("utf-8"))


def stamp(moment: datetime | None) -> str:
    return UNKNOWN if moment is None else moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _time(value: Any) -> datetime | None:
    return ws.parse_time(value)


# --- Card and lines -------------------------------------------------------------------------------------------------

def card_lines(view: Mapping[str, Any], lane: str) -> list[dict[str, Any]]:
    """The lane's shown ACTION lines, each joined to the structured PR record it was rendered from."""
    rows = []
    for index, text in enumerate(view["lanes"].get(lane, {"actions": []})["actions"], 1):
        row: dict[str, Any] = {"line": index, "text_sha256": sha256_bytes(text.encode("utf-8"))}
        match = LINE.match(text) if isinstance(text, str) else None
        record = view["prs"].get(match.group(2)) if match else None
        slot = match.group(4) if match else None
        if (match is None or not isinstance(record, Mapping) or record.get("state") != "action"
                or slot not in record.get("empty_slots", ()) or lane_view.SLOT_OWNER.get(slot) != lane
                or not isinstance(record.get("github_head"), str) or not ws.HEAD.fullmatch(record["github_head"])
                or record["github_head"][:12] != match.group(3) or record.get("branch") != match.group(5)
                or (match.group(1) == "RCO") != (slot in ws.RECOGNIZED_RCOS)):
            row.update(pr=None, slot=None, head=None, task=None, reason="line_record_mismatch")
        else:
            row.update(pr=int(match.group(2)), slot=slot, head=record["github_head"], task=record["branch"],
                       reason=None)
        rows.append(row)
    return rows


# --- Receipts -------------------------------------------------------------------------------------------------------

def issue_reason(issue: Any, lane: str, card_sha256: str, view_sha256: str) -> str | None:
    if not isinstance(issue, Mapping):
        return "issue_missing"
    if (issue.get("schema") != ISSUE_SCHEMA or issue.get("lane") != lane or issue.get("card_sha256") != card_sha256
            or issue.get("view_sha256") != view_sha256 or not isinstance(issue.get("nonce"), str)
            or not issue["nonce"] or _time(issue.get("issued_utc")) is None):
        return "issue_invalid"
    return None


def responder_ok(receipt: Mapping[str, Any], issue: Mapping[str, Any]) -> bool:
    return receipt.get("lane") == issue["lane"]


def digest_ok(receipt: Mapping[str, Any], issue: Mapping[str, Any]) -> bool:
    return receipt.get("card_sha256") == issue["card_sha256"]


def nonce_ok(receipt: Mapping[str, Any], issue: Mapping[str, Any]) -> bool:
    return isinstance(receipt.get("nonce"), str) and receipt["nonce"] == issue["nonce"]


def order_ok(read: datetime, issued: datetime) -> bool:
    return issued <= read


def receipt_reason(receipt: Any, issue: Mapping[str, Any]) -> str | None:
    """None for a valid receipt of this issue, else the first failing check in a fixed order."""
    if not isinstance(receipt, Mapping) or receipt.get("schema") != RECEIPT_SCHEMA:
        return "receipt_invalid"
    read = _time(receipt.get("read_utc"))
    if read is None or not isinstance(receipt.get("reader"), str) or not receipt["reader"]:
        return "receipt_invalid"
    if not responder_ok(receipt, issue):
        return "receipt_responder_mismatch"
    if not digest_ok(receipt, issue):
        return "receipt_digest_mismatch"
    if not nonce_ok(receipt, issue):
        return "receipt_nonce_mismatch"
    if not order_ok(read, _time(issue["issued_utc"])):
        return "receipt_before_issue"
    return None


def read_time(issue: Any, receipts: Sequence[Any], lane: str, card_sha256: str,
              view_sha256: str) -> tuple[datetime | None, str | None, list[str]]:
    """The earliest valid read time, or None with the reason; receipts of other cards and lanes are ignored."""
    related = [item for item in receipts if isinstance(item, Mapping)
               and (item.get("lane") == lane or item.get("card_sha256") == card_sha256)]
    if not related:
        return None, "receipt_missing", []
    problem = issue_reason(issue, lane, card_sha256, view_sha256)
    if problem:
        return None, problem, []
    reasons, valid = [], []
    for item in related:
        reason = receipt_reason(item, issue)
        if reason is None:
            valid.append(_time(item["read_utc"]))
        else:
            reasons.append(reason)
    if valid:
        return min(valid), None, sorted(set(reasons))
    return None, min(reasons, key=RECEIPT_ORDER.index), sorted(set(reasons))


# --- Events, coverage and truth -------------------------------------------------------------------------------------

def event_pairs(events: Any) -> list[tuple[str, Any]]:
    if isinstance(events, Mapping):
        return [(ref, body) for ref, body in events.items()]
    if isinstance(events, (list, tuple)):
        return [(item.get("ref"), item.get("body")) for item in events if isinstance(item, Mapping)]
    raise OutcomeError("events must be a mapping or a list of ref and body pairs")


def dedupe(pairs: Sequence[tuple[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    """Bodies whose own canonical ref is their key, each counted once; mismatched refs and duplicates are counted."""
    kept: dict[str, dict[str, Any]] = {}
    counts = {"pairs": len(pairs), "ref_mismatch": 0, "duplicates": 0, "unusable": 0}
    for ref, body in pairs:
        if not isinstance(ref, str) or not isinstance(body, Mapping) or event_id(body) != ref:
            counts["ref_mismatch"] += 1
        elif ref in kept:
            counts["duplicates"] += 1
        elif _time(body.get("ts_utc")) is None:
            counts["unusable"] += 1
        else:
            kept[ref] = {"ref": ref, "ts": _time(body["ts_utc"]), "body": body}
    return kept, counts


def covers(coverage: Sequence[Any], start: datetime, end: datetime) -> bool:
    """True only when complete coverage spans, merged, contain every moment from start to end. A span must contain
    ``start`` itself, also when start == end: no span is never coverage (RCO1 FL2: v1.2 returned True for an empty
    list when start >= end)."""
    spans = []
    for item in coverage:
        if isinstance(item, Mapping) and item.get("complete") is True:
            low, high = _time(item.get("from_utc")), _time(item.get("to_utc"))
            if low is not None and high is not None and low <= high:
                spans.append((low, high))
    reach = None
    for low, high in sorted(spans):
        if reach is None:
            if low > start:
                return False  # sorted by start: no later span can contain ``start``
            if high >= start:
                reach = high
            continue
        if low > reach:
            break
        reach = max(reach, high)
    return reach is not None and reach >= end


def _head_rows(head_truth: Sequence[Any], pr: int, kind: str) -> list[Mapping[str, Any]]:
    return [item for item in head_truth
            if isinstance(item, Mapping) and item.get("kind") == kind and item.get("pr") == pr
            and isinstance(item.get("head"), str) and ws.HEAD.fullmatch(item["head"])]


def sampled(head_truth: Sequence[Any], pr: int, head: str, moment: datetime) -> str:
    """SAMPLED_CONSISTENT when the nearest point samples around ``moment``, at most MAX_TRUTH_GAP apart, both name
    ``head``; else UNKNOWN. A sample never proves currency: the head can change and change back in between (ABA)."""
    seen = [(_time(item.get("observed_utc")), item["head"]) for item in _head_rows(head_truth, pr, "sample")
            if _time(item.get("observed_utc")) is not None]
    before = [row for row in seen if row[0] <= moment]
    after = [row for row in seen if row[0] >= moment]
    if not before or not after:
        return UNKNOWN
    low_time, high_time = max(row[0] for row in before), min(row[0] for row in after)
    low = {row[1] for row in before if row[0] == low_time}
    high = {row[1] for row in after if row[0] == high_time}
    if high_time - low_time > MAX_TRUTH_GAP or low != high or low != {head}:
        return UNKNOWN
    return "SAMPLED_CONSISTENT"


def currency(head_truth: Sequence[Any], pr: int, head: str, moment: datetime) -> str:
    """Contract v1.2 C1: CURRENT or STALE only from a complete interval row that contains ``moment``. Disagreeing
    intervals give UNKNOWN; with no interval, the sample verdict (SAMPLED_CONSISTENT or UNKNOWN), never verified."""
    heads = set()
    for item in _head_rows(head_truth, pr, "interval"):
        low, high = _time(item.get("from_utc")), _time(item.get("to_utc"))
        if item.get("complete") is True and low is not None and high is not None and low <= moment <= high:
            heads.add(item["head"])
    if len(heads) == 1:
        return "CURRENT" if heads == {head} else "STALE"
    if heads:
        return UNKNOWN
    return sampled(head_truth, pr, head, moment)


def identity_ok(identity: Sequence[Any], event: Mapping[str, Any]) -> bool:
    """Contract v1.2 C2: independent verifier rows bound to this exact event ref, ALL naming the event's agent and
    ALL verified with a method and a source. An agent label, a self-hashed body or an eligibility row is never an
    identity, and conflicting attestations never resolve to verified (RCO1 FL1: v1.2 accepted ANY matching row)."""
    rows = [item for item in identity if isinstance(item, Mapping) and item.get("event_ref") == event["ref"]]
    return bool(rows) and all(
        item.get("agent") == event["body"].get("agent") and item.get("verified") is True
        and isinstance(item.get("method"), str) and bool(item["method"])
        and isinstance(item.get("source"), str) and isinstance(item.get("source_sha256"), str)
        for item in rows)


def eligible(rows: Sequence[Any], pr: int, head: str, slot: str, lane: str, moment: datetime) -> bool | None:
    """The independent eligibility of this lane for this slot at this head near ``moment``; None when unknown."""
    values = {item["eligible"] for item in rows
              if isinstance(item, Mapping) and item.get("pr") == pr and item.get("head") == head
              and item.get("slot") == slot and item.get("lane") == lane and isinstance(item.get("eligible"), bool)
              and _time(item.get("observed_utc")) is not None
              and abs(_time(item["observed_utc"]) - moment) <= MAX_TRUTH_GAP}
    return values.pop() if len(values) == 1 else None


def same_head(bound: str | None, head: str) -> bool:
    """The full 40-hex SHA, never a 12-character prefix."""
    return bound is not None and bound == head


def same_pr(payload: Mapping[str, Any], line: Mapping[str, Any]) -> bool:
    """Contract v1.1 (d): a declared PR number must be the line's PR; an absent one is judged by task and head."""
    return "pr" not in payload or str(payload["pr"]) == str(line["pr"])


def vote_binding(event: Mapping[str, Any], lane: str, line: Mapping[str, Any]) -> str | None:
    """VALID; WRONG_PR or WRONG_HEAD for a pass vote on the line's task with another PR number or another (or no)
    bound head; None when the event is not a pass vote of this lane on this task."""
    body = event["body"]
    if (body.get("agent") != lane or body.get("type") != VOTE_TYPE or body.get("status") != PASS_STATUS[line["slot"]]
            or body.get("task_id") != line["task"]):
        return None
    payload = body.get("payload") if isinstance(body.get("payload"), Mapping) else {}
    if not same_pr(payload, line):
        return "WRONG_PR"
    return "VALID" if same_head(lane_view.bound_head(payload), line["head"]) else "WRONG_HEAD"


def judge_wrong(kind: str, event: Mapping[str, Any], line: Mapping[str, Any],
                head_truth: Sequence[Any]) -> tuple[str, str]:
    """Contract v1.1 (c): a pass vote on the task at ANOTHER head is harm only when that head was not the live head
    at the vote. A vote at the live head after the PR moved on is the right act on a stale line: OTHER, not harm."""
    if kind == "WRONG_PR":
        return "TAKEN_WRONG", "pr_mismatch"
    payload = event["body"].get("payload") if isinstance(event["body"].get("payload"), Mapping) else {}
    bound = lane_view.bound_head(payload)
    if bound is None:
        return "TAKEN_WRONG", "wrong_binding"
    state = currency(head_truth, line["pr"], bound, event["ts"])
    if state == "CURRENT":
        return "OTHER", "vote_at_live_head"
    if state == "STALE":
        return "TAKEN_WRONG", "wrong_binding"
    return UNKNOWN, "head_currency_unverified"


def timed(coverage: Sequence[Any], read: datetime, moment: datetime) -> int | None:
    """Read-to-action in whole milliseconds, only when coverage holds every moment from the read to the action."""
    if not covers(coverage, read, moment):
        return None
    delta = moment - read
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


# --- Rows and counts ------------------------------------------------------------------------------------------------

def before_read(moment: datetime, read: datetime, issued: datetime | None) -> bool:
    """Contract v1.1 (a): ANY valid vote before the read, also one before the issue, means the line was done before
    it was read. v1 looked only from the issue on, so a card that was already stale turned into a misleading NONE."""
    return moment < read


def positive_coverage(coverage: Sequence[Any], as_of: datetime | None, read: datetime, moment: datetime) -> bool:
    """Contract v1.2 C4: a positive claim needs complete coverage from min(as_of, read) through the deciding action,
    because a gap can hide an EARLIER action. An action before that point must be covered from the action up to it
    (RCO1 FL2: v1.2 collapsed that span to the action instant)."""
    if as_of is None:
        return False
    anchor = min(as_of, read)
    return covers(coverage, min(anchor, moment), max(anchor, moment))


def deciding_action(inside: Sequence[tuple[Mapping[str, Any], str]]) -> tuple[Any, list]:
    """Contract v1.2 C3: the FIRST relevant action in time (ties by ref) decides; the later ones are listed."""
    return (inside[0], list(inside[1:])) if inside else (None, [])


def outcome_row(line: Mapping[str, Any], lane: str, read: datetime | None, read_reason: str | None,
                issued: datetime | None, as_of: datetime | None, lane_events: Sequence[Mapping[str, Any]],
                coverage: Sequence[Any], head_truth: Sequence[Any], eligibility: Sequence[Any],
                identity: Sequence[Any]) -> dict[str, Any]:
    row = {key: line[key] for key in ("line", "pr", "slot", "head", "task", "text_sha256")}
    row.update(read_utc=stamp(read), event_ref=None, event_utc=None, read_to_action_ms=None, head_sampled=None,
               later_actions=[], corrected_after_wrong=False)
    if line["reason"]:
        return dict(row, outcome=UNKNOWN, reason=line["reason"])
    if read is None:
        return dict(row, outcome=UNKNOWN, reason=read_reason)
    bindings = [(event, vote_binding(event, lane, line)) for event in lane_events]
    pre = [event for event, kind in bindings if kind == "VALID" and before_read(event["ts"], read, issued)]
    if pre:
        row.update(event_ref=pre[0]["ref"], event_utc=stamp(pre[0]["ts"]))
        if not positive_coverage(coverage, as_of, read, pre[0]["ts"]):
            return dict(row, outcome=UNKNOWN, reason="coverage_incomplete_before_action")
        if not identity_ok(identity, pre[0]):  # stricter than C2's TAKEN-only wording: every event-decided outcome
            return dict(row, outcome=UNKNOWN, reason="identity_unverified")
        return dict(row, outcome="PRE_READ", reason="valid_vote_before_read")
    inside = [(event, kind) for event, kind in bindings if kind is not None and read <= event["ts"] <= read + WINDOW]
    first, later = deciding_action(inside)
    if first is not None:
        event, kind = first
        row.update(event_ref=event["ref"], event_utc=stamp(event["ts"]),
                   later_actions=[{"ref": item["ref"], "kind": what, "utc": stamp(item["ts"])} for item, what in later],
                   corrected_after_wrong=kind != "VALID" and any(what == "VALID" for _, what in later))
        if not positive_coverage(coverage, as_of, read, event["ts"]):
            return dict(row, outcome=UNKNOWN, reason="coverage_incomplete_before_action")
        if not identity_ok(identity, event):
            return dict(row, outcome=UNKNOWN, reason="identity_unverified")
        if kind == "VALID":
            state = currency(head_truth, line["pr"], line["head"], event["ts"])
            if state == "STALE":
                outcome, reason = "TAKEN_STALE", "head_moved_before_vote"
            elif state != "CURRENT":
                return dict(row, outcome=UNKNOWN, reason="head_currency_unverified", head_sampled=state)
            else:
                verdict = eligible(eligibility, line["pr"], line["head"], line["slot"], lane, event["ts"])
                if verdict is None:
                    return dict(row, outcome=UNKNOWN, reason="eligibility_unknown")
                outcome, reason = ("TAKEN_CORRECT", None) if verdict else ("TAKEN_WRONG", "not_eligible")
        else:
            outcome, reason = judge_wrong(kind, event, line, head_truth)
        if outcome != UNKNOWN:
            row["read_to_action_ms"] = timed(coverage, read, event["ts"])
        return dict(row, outcome=outcome, reason=reason)
    # Contract v1.1 (a): a negative claim (OTHER or NONE) needs coverage from the view's own time, so that no vote
    # before the read can be missing, through the end of the window.
    if as_of is None or not covers(coverage, min(as_of, read), read + WINDOW):
        return dict(row, outcome=UNKNOWN, reason="coverage_incomplete")
    in_window = [event for event in lane_events if read <= event["ts"] <= read + WINDOW]
    return dict(row, outcome="OTHER" if in_window else "NONE", reason=None)


def counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tally = {name: sum(row["outcome"] == name for row in rows) for name in KNOWN + ("PRE_READ", UNKNOWN)}
    taken = sum(tally[name] for name in TAKEN)
    known = sum(tally[name] for name in KNOWN)
    unknown_reasons: dict[str, int] = {}
    for row in rows:
        if row["outcome"] == UNKNOWN:
            unknown_reasons[row["reason"]] = unknown_reasons.get(row["reason"], 0) + 1
    timed_ms = sorted(row["read_to_action_ms"] for row in rows
                      if row["outcome"] == "TAKEN_CORRECT" and row["read_to_action_ms"] is not None)
    return {"outcomes": tally, "unknown_reasons": unknown_reasons,
            "follow_through": {"numerator": taken, "denominator": known},
            "correct_of_taken": {"numerator": tally["TAKEN_CORRECT"], "denominator": taken},
            "harm_of_taken": {"numerator": tally["TAKEN_STALE"] + tally["TAKEN_WRONG"], "denominator": taken},
            "corrected_after_wrong": sum(bool(row.get("corrected_after_wrong")) for row in rows),
            "attempts_after_first": sum(len(row.get("later_actions") or ()) for row in rows),
            "read_to_action_ms_taken_correct": timed_ms}


def outcomes(view_bytes: bytes, card_bytes: bytes, lane: str, issue: Any, receipts: Sequence[Any], events: Any,
             coverage: Sequence[Any], head_truth: Sequence[Any], eligibility: Sequence[Any],
             identity: Sequence[Any] = ()) -> dict[str, Any]:
    """The outcome rows and counts of one card. Pure and deterministic."""
    if lane not in lane_view.LANES:
        raise OutcomeError("unknown lane")
    view_sha256, card_sha256 = sha256_bytes(view_bytes), sha256_bytes(card_bytes)
    try:
        view = json.loads(view_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise OutcomeError("the view is not UTF-8 JSON") from None
    if not isinstance(view, Mapping) or view.get("schema") != lane_view.SCHEMA:
        raise OutcomeError("the view schema is not the step-1 lane view")
    result: dict[str, Any] = {
        "schema": SCHEMA, "authority": "none", "scope": SCOPE, "contract_sha256": CONTRACT_SHA256, "lane": lane,
        "constants": {"window_s": int(WINDOW.total_seconds()), "max_truth_gap_s": int(MAX_TRUTH_GAP.total_seconds()),
                      "pass_status": PASS_STATUS},
        "inputs": {"view_sha256": view_sha256, "card_sha256": card_sha256,
                   "issue_sha256": digest_set([issue]) if issue is not None else None,
                   "receipts_sha256": digest_set(receipts), "coverage_sha256": digest_set(coverage),
                   "head_truth_sha256": digest_set(head_truth), "eligibility_sha256": digest_set(eligibility),
                   "identity_sha256": digest_set(identity)},
        "card": "accepted", "rows": [], "gap": None,
    }
    kept, event_counts = dedupe(event_pairs(events))
    result["inputs"]["events"] = dict(event_counts, refs_sha256=digest_set(sorted(kept)))
    if lane_view.render_text(view, lane).encode("utf-8") != card_bytes:
        # Contract v1.1 (e): the view's lines are still counted, each UNKNOWN, so a refused card never vanishes.
        rows = [dict({key: line[key] for key in ("line", "pr", "slot", "head", "task", "text_sha256")},
                     read_utc=UNKNOWN, event_ref=None, event_utc=None, read_to_action_ms=None, outcome=UNKNOWN,
                     reason="card_not_rendered_from_view") for line in card_lines(view, lane)]
        result.update(card="refused: card_not_rendered_from_view", rows=rows, receipt_reasons_seen=[],
                      counts=counts(rows))
        ws.guard_object(result)
        return result
    read, reason, receipt_reasons = read_time(issue, receipts, lane, card_sha256, view_sha256)
    issued = _time(issue.get("issued_utc")) if isinstance(issue, Mapping) else None
    lane_events = sorted((event for event in kept.values() if event["body"].get("agent") == lane),
                         key=lambda event: (event["ts"], event["ref"]))
    as_of = _time(view.get("as_of_utc"))
    rows = [outcome_row(line, lane, read, reason, issued, as_of, lane_events, coverage, head_truth, eligibility,
                        identity) for line in card_lines(view, lane)]
    result.update(rows=rows, receipt_reasons_seen=receipt_reasons, counts=counts(rows))
    if rows and all(row["reason"] == "receipt_missing" for row in rows) and issue is None:
        result["gap"] = HISTORICAL_GAP
    ws.guard_object(result)
    return result


def result_bytes(result: Mapping[str, Any]) -> bytes:
    return (canonical(result) + "\n").encode("ascii")


def aggregate(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Contract v1.1 (g) with delta v1.2.1 across cards: identical results count once; rows linked by a shared
    ``event_ref`` or by the same card line (card_sha256, lane, line), transitively, form one component that counts
    ONCE. Agreeing rows keep their outcome. A mix of event-bound and no-event rows (the same line read once without
    and once with its action) is UNKNOWN(mixed_history): a BOUND, not a resolution, because rows alone cannot rule
    out a wrong vote or a head change between the reads (the v1.3 action horizon would). Any other disagreement is
    UNKNOWN(aggregate_conflict). Pure and deterministic in any input order."""
    unique = {result_bytes(result): result for result in results}
    rows: list[tuple[Mapping[str, Any], tuple[str, str, int]]] = []
    for key in sorted(unique):
        result = unique[key]
        rows.extend((row, (result["inputs"]["card_sha256"], result["lane"], row["line"])) for row in result["rows"])
    # Delta v1.2.1 (24F508BD): rows linked by a shared event_ref or by the same card line, transitively, form one
    # component, and each component counts ONCE (RCO1 measured follow_through 1/2 for one taken line on f80a92c2).
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    first_seen: dict[tuple, int] = {}
    for index, (row, line_key) in enumerate(rows):
        keys = [("line",) + line_key] + ([("event", row["event_ref"])] if row.get("event_ref") else [])
        for link in keys:
            if link in first_seen:
                low, high = sorted((find(index), find(first_seen[link])))
                parent[high] = low
            else:
                first_seen[link] = index
    components: dict[int, list[Mapping[str, Any]]] = {}
    for index, (row, _) in enumerate(rows):
        components.setdefault(find(index), []).append(row)
    merged_rows: list[Mapping[str, Any]] = []
    mixed = 0
    for root in sorted(components):
        group = sorted(components[root], key=canonical)
        if len({(row["outcome"], row["reason"]) for row in group}) == 1:
            merged_rows.append(group[0])
        elif any(row.get("event_ref") for row in group) and any(not row.get("event_ref") for row in group):
            mixed += 1  # a BOUND, not a resolution: rows alone cannot rule out an action between the reads
            merged_rows.append(dict(group[0], outcome=UNKNOWN, reason="mixed_history"))
        else:
            merged_rows.append(dict(group[0], outcome=UNKNOWN, reason="aggregate_conflict"))
    by_event: dict[str, int] = {}
    by_line: dict[tuple[str, str, int], int] = {}
    for row, line_key in rows:
        if row.get("event_ref"):
            by_event[row["event_ref"]] = by_event.get(row["event_ref"], 0) + 1
        else:
            by_line[line_key] = by_line.get(line_key, 0) + 1
    merged = {"schema": SCHEMA + ".aggregate", "authority": "none", "scope": SCOPE, "cards": len(unique),
              "duplicate_results": len(results) - len(unique), "components": len(components),
              "rows_merged": len(rows) - len(components), "mixed_history_components": mixed,
              "rows_merged_by_event": sum(count - 1 for count in by_event.values()),
              "rows_merged_by_card_line": sum(count - 1 for count in by_line.values()), "counts": counts(merged_rows)}
    ws.guard_object(merged)
    return merged


# --- Command line (reads files, writes one new file; the function above stays pure) ---------------------------------

def _load(path: str | None, default: Any) -> Any:
    return default if path is None else json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LEARN slice 1 outcome rows (offline, advisory)")
    parser.add_argument("--view", required=True, help="lane-view JSON file (wd.mind-lane-view.v1a)")
    parser.add_argument("--lane", required=True)
    parser.add_argument("--card", help="card bytes; with --replay the card is rendered from the view")
    parser.add_argument("--replay", action="store_true", help="historical replay: no issue, no receipts")
    parser.add_argument("--issue")
    parser.add_argument("--receipts")
    parser.add_argument("--events")
    parser.add_argument("--coverage")
    parser.add_argument("--head-truth")
    parser.add_argument("--eligibility")
    parser.add_argument("--identity")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    out = Path(args.out)
    if out.exists():
        raise SystemExit("refusing to overwrite the output")
    view_bytes = Path(args.view).read_bytes()
    if args.replay:
        if args.card or args.issue or args.receipts:
            raise SystemExit("a replay takes no card, issue or receipts")
        card_bytes = lane_view.render_text(json.loads(view_bytes.decode("utf-8")), args.lane).encode("utf-8")
    elif args.card:
        card_bytes = Path(args.card).read_bytes()
    else:
        raise SystemExit("--card is required unless --replay")
    result = outcomes(view_bytes, card_bytes, args.lane, _load(args.issue, None), _load(args.receipts, []),
                      _load(args.events, {}), _load(args.coverage, []), _load(args.head_truth, []),
                      _load(args.eligibility, []), _load(args.identity, []))
    data = result_bytes(result)
    with open(out, "xb") as stream:
        stream.write(data)
    print(json.dumps({"out": str(out), "sha256": sha256_bytes(data), "rows": len(result["rows"]),
                      "outcomes": result["counts"]["outcomes"], "gap": result["gap"] is not None}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
