# SPDX-License-Identifier: BUSL-1.1
"""CLAUDE.md Rule 12 review-eligibility evaluator (pure, fail-closed, UNWIRED).

Computes, for one task at one exact head, whether the Rule 12 review slots are
held by eligible approvers:

* the **opposite-family** slot: an approval at the head from a lane whose model
  family is outside every implementer family (GPT ``codex-lead-1`` /
  ``codex-tools-1``; Claude ``fable-5`` / ``claude-rco-1`` / ``claude-rco-2``);
* the **RCO** slot: ``rco_pass`` at the head from EVERY eligible recognized RCO
  (not an implementer, not self-recused, not absent);
* a **pool fallback** for the RCO slot (operator 2026-10-10 06:56Z): when no
  recognized RCO is present, the first eligible lane of ``RCO_POOL_FALLBACK``
  (Tools, Lead, Fable) with an exact-head ``rco_pass`` and no uncleared block of
  its own holds it. It never also holds the opposite-family slot. While any
  such lane is still available (eligible, not the opposite-family holder) the
  slot waits for it (``pending``) and Grok does not fill it; an uncleared block
  from any present pool lane, the opposite-family holder included, blocks the
  slot (``blocked_by_pool_candidate``);
* a **Grok fallback** may fill one slot whose primaries (for the RCO slot: the
  recognized RCOs AND the pool lanes) are all ineligible, recused or absent,
  bound to one helper-ledger consultation;
* when the WHOLE review pool (Lead, Tools, Fable, RCO1, RCO2) is genuinely
  ineligible for this change (implementer or self-recused), one Grok
  **external review** under Grok's own identity covers the review (operator
  2026-10-06 16:23Z and 16:58Z). It is recorded once, never as two agents and
  never as an ``rco_pass``. ``fable-5`` is a pool reviewer for other lanes'
  work (before Grok), not a recognized RCO.

Implementers are every author / concept / design / measurement source of the
change; Grok counts as one only when it authored code (advice, a design choice
and read-only review never do). A recusal is a self-posted ``type=message`` event with a status in
``RECUSAL_STATUSES`` bound to the task and the exact head; anything else (for
example an effort setting such as ``medium``) is not a recusal. A lane that
both recuses and approves at the head is ``conflicting``: its approval does not
count and its slot is not vacant. A primary is absent only when a
``requested`` event from another known, non-implementer lane, bound to the
head, got no answer from it for ``ABSENCE_SECONDS``; any event of the primary
on the task bound to the head, or later than the request, is an answer.

The two slots are held by distinct identities: a recognized RCO that sits in
the RCO slot is never also the opposite-family approver.

A recognized-RCO veto is evaluated over ALL recognized RCOs, including
implementers and recused ones, and outranks every pass including Grok. Order
is read from ``ts_utc``, never from list position: a block is cleared only by a
strictly later own ``rco_pass`` at the block's head or at this head that is not
dated after the gate clock; a block or pass without a parseable timestamp can
never clear or be cleared. Every piece of positive evidence (an approval, a
recusal, a clearing pass, a Grok attempt) needs a parseable timestamp no later
than ``now_utc``. A Grok answer
that is missing, partial, unclear, negative, at another head, not the first
attempt at that head for that slot, not bound to the gate nonce, prompt and
diff, or whose ORIGINAL answer text does not begin with a line that is exactly
``APPROVE`` leaves its slot empty; it never blocks by itself and it is never
recorded as an ``rco_pass``. The requester is a pure relay and may itself be
ineligible: it never supplies or interprets the verdict, which is read from
the answer text whose sha256 must equal ``answer_sha256``.

Trust boundary: callers must pass identity-verified events (agent + agent_uuid
checked against the bridge identity registry), the full event log for the task
and the gate's own clock; this function cannot see forged or missing events.

This module is NOT read by any merge gate yet. It decides nothing on its own;
wiring it into ``tools/idle_consensus_auto_merge.py`` and the receipt writer is
a separate (a)-class gate-code PR.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "wd.rule12-review-eligibility.v1"

FAMILIES: Mapping[str, str] = {
    "codex-lead-1": "gpt",
    "codex-tools-1": "gpt",
    "fable-5": "claude",
    "claude-rco-1": "claude",
    "claude-rco-2": "claude",
    "grok-scout-1": "grok",
}
GROK_AGENT = "grok-scout-1"
# The ordinary review pool; Grok is only the fallback after it.
REVIEW_POOL: tuple[str, ...] = (
    "codex-lead-1",
    "codex-tools-1",
    "fable-5",
    "claude-rco-1",
    "claude-rco-2",
)
# Must equal tools.check_promotion_eligible.DEFAULT_RCO_AGENTS (drift-guard test).
RECOGNIZED_RCOS: tuple[str, ...] = ("claude-rco-1", "claude-rco-2")
# Operator 2026-10-10 06:56Z, first-hand in the fable-5 session: "RCO paikan voi
# täyttää tools, lead, fabel tai jos ei mikään niiistä niin grok". With no
# recognized RCO present, these lanes may hold the RCO slot, in this order,
# before the Grok fallback. They are never recognized RCOs: their blocks are
# not recognized-RCO vetoes and no other gate reads their rco_pass.
RCO_POOL_FALLBACK: tuple[str, ...] = ("codex-tools-1", "codex-lead-1", "fable-5")
POOL_FALLBACK_STATE = "held_by_pool_fallback"
POOL_BLOCKED_STATE = "blocked_by_pool_candidate"
# Must equal tools.check_rco_pass_present.RCO_PASS_STATUSES (drift-guard test).
RCO_PASS_STATUSES = frozenset({"rco_pass"})
PASS_EVENT_TYPES = frozenset({"decision", "rco_review"})
# Exact approval statuses for the opposite-family slot; nothing looser.
OPPOSITE_FAMILY_APPROVAL_STATUSES = frozenset({"rco_pass", "build_consensus_pass"})
# Exact tooling record statuses (as in #1762): neither a block nor a clear.
RCO_NEUTRAL_RECORD_STATUSES = frozenset(
    {
        "autonomous_merge_receipt",
        "merged_operator_authorized",
        "operator_authorized",
        "rco_closed_postmerge",
    }
)
# Any recognized-RCO event of these types blocks, whatever its status.
RCO_BLOCK_TYPES = frozenset({"finding", "blocked"})
RECUSAL_EVENT_TYPE = "message"
RECUSAL_STATUSES = frozenset({"rco_recused", "review_recused"})
REVIEW_REQUEST_STATUS = "requested"
IMPLEMENTER_ROLES = frozenset({"author", "concept", "design", "measurement"})
# Operator 2026-10-06 16:57Z: read-only reviews or advice do not make Grok
# ineligible because it is not an implementer; only authoring code does.
GROK_DISQUALIFYING_ROLES = frozenset({"author"})
ABSENCE_SECONDS = 60 * 60
GROK_MAX_SLOTS = 1
GROK_REQUIRED_EFFORT = "high"
GROK_APPROVE_LINE = "APPROVE"
SLOT_RCO = "rco"
SLOT_OPPOSITE_FAMILY = "opposite_family"
SLOTS = (SLOT_OPPOSITE_FAMILY, SLOT_RCO)
SLOT_EXTERNAL_REVIEW = "external_review"
GROK_SLOT_TAGS = (*SLOTS, SLOT_EXTERNAL_REVIEW)

SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32,64}$")


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _event_head(event: Mapping[str, Any]) -> str | None:
    """Structured head of an event; ``""`` when it carries none, None when invalid."""
    payload = _payload(event)
    heads = {
        _text(payload.get(key))
        for key in ("exact_head", "head")
        if payload.get(key) is not None
    }
    if not heads:
        return ""
    if len(heads) != 1:
        return None
    head = heads.pop()
    return head if SHA1_RE.match(head) else None


def _parse_utc(value: Any) -> datetime | None:
    text = _text(value).strip()
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|\+00:00)$", text
    )
    if not match:
        return None
    fraction = (match.group(2) or "0")[:6].ljust(6, "0")
    try:
        parsed = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(microsecond=int(fraction), tzinfo=timezone.utc)


def _recipients(event: Mapping[str, Any]) -> set[str]:
    to = event.get("to")
    if isinstance(to, str):
        return {part.strip() for part in to.split(",") if part.strip()}
    if isinstance(to, (list, tuple)):
        return {item.strip() for item in to if isinstance(item, str) and item.strip()}
    return set()


def _evidence_time_ok(event: Mapping[str, Any], now: datetime, key: str = "ts_utc") -> bool:
    """Positive evidence needs a parseable timestamp no later than the gate clock."""
    stamped = _parse_utc(event.get(key))
    return stamped is not None and stamped <= now


def _on_task(event: Mapping[str, Any], agent: str, task_id: str) -> bool:
    return _text(event.get("agent")) == agent and _text(event.get("task_id")) == task_id


def _is_pass_status(event: Mapping[str, Any], statuses: frozenset[str]) -> bool:
    return (
        _text(event.get("type")) in PASS_EVENT_TYPES
        and _text(event.get("status")) in statuses
    )


def _is_rco_block(event: Mapping[str, Any]) -> bool:
    """A recognized-RCO event that is a block at whatever head it names."""
    event_type = _text(event.get("type"))
    status = _text(event.get("status"))
    if event_type in RCO_BLOCK_TYPES:
        # Stricter than the peer gate on purpose (as check_rco_pass_present):
        # an informational finding or an exact retraction is still a block here
        # until the same RCO posts a strictly later exact-head rco_pass.
        return True
    if event_type in PASS_EVENT_TYPES:
        # Exact allowlist: approvals and tooling records are not blocks.
        return status not in OPPOSITE_FAMILY_APPROVAL_STATUSES | RCO_NEUTRAL_RECORD_STATUSES
    return False


def _rco_vetoes(
    events: Sequence[Mapping[str, Any]], rco: str, task_id: str, head: str, now: datetime
) -> bool:
    """True when ``rco`` has a block on the task that no later own pass clears.

    A clearing pass must be strictly later than the block and no later than the
    gate clock; a future-dated pass never clears (Tools T1 on 0aabaaab).
    """
    own = [event for event in events if _on_task(event, rco, task_id)]
    passes = [
        (_parse_utc(event.get("ts_utc")), _event_head(event))
        for event in own
        if _is_pass_status(event, RCO_PASS_STATUSES)
    ]
    for event in own:
        if not _is_rco_block(event):
            continue
        blocked_at = _parse_utc(event.get("ts_utc"))
        if blocked_at is None:
            return True
        block_head = _event_head(event)
        clear_heads = {head} | ({block_head} if block_head else set())
        cleared = any(
            passed_at is not None
            and blocked_at < passed_at <= now
            and pass_head in clear_heads
            for passed_at, pass_head in passes
        )
        if not cleared:
            return True
    return False


def _self_recused(
    events: Sequence[Mapping[str, Any]],
    agent: str,
    task_id: str,
    head: str,
    now: datetime,
    *,
    timed: bool,
) -> bool:
    """A head-bound self-recusal; ``timed`` also requires ts_utc <= now.

    Only a timed recusal may vacate a slot; any head-bound recusal, timed or
    not, still makes the same lane's approval conflicting (RCO1 F2 on e2948bf7).
    """
    return any(
        _on_task(event, agent, task_id)
        and (not timed or _evidence_time_ok(event, now))
        and _text(event.get("type")) == RECUSAL_EVENT_TYPE
        and _text(event.get("status")) in RECUSAL_STATUSES
        and _event_head(event) == head
        for event in events
    )


def _absent(
    events: Sequence[Mapping[str, Any]],
    agent: str,
    task_id: str,
    head: str,
    now: datetime,
    implementers: set[str],
) -> bool:
    """No answer from ``agent`` for ABSENCE_SECONDS after a bound review request."""
    own = [event for event in events if _on_task(event, agent, task_id)]
    if any(_event_head(event) == head for event in own):
        return False  # it already spoke at this head
    for event in events:
        requester = _text(event.get("agent"))
        if (
            _text(event.get("task_id")) != task_id
            or _text(event.get("status")) != REVIEW_REQUEST_STATUS
            or not _text(event.get("request_id"))
            or requester not in FAMILIES
            or requester in (GROK_AGENT, agent)
            or requester in implementers
            or agent not in _recipients(event)
            or _event_head(event) != head
        ):
            continue
        requested = _parse_utc(event.get("ts_utc"))
        if requested is None or (now - requested).total_seconds() < ABSENCE_SECONDS:
            continue
        answered = False
        for later in own:
            said_at = _parse_utc(later.get("ts_utc"))
            if said_at is None or said_at >= requested:
                answered = True
                break
        if not answered:
            return True
    return False


def _first_grok_consultation(
    consultations: Sequence[Mapping[str, Any]],
    task_id: str,
    head: str,
    slot: str,
    now: datetime,
) -> tuple[Mapping[str, Any] | None, list[str]]:
    """First attempt at (task, head) that counts for ``slot``.

    External-review and mistagged attempts count for every slot, and every
    attempt at the head counts for the external review, so changing the tag
    can never shop for a second answer.
    """
    attempts = [
        consultation
        for consultation in consultations
        if _text(consultation.get("task_id")) == task_id
        and _text(consultation.get("head")) == head
        and (
            slot == SLOT_EXTERNAL_REVIEW
            or _text(consultation.get("slot")) == slot
            or _text(consultation.get("slot")) not in SLOTS
        )
    ]
    if not attempts:
        return None, ["no Grok consultation bound to this task and exact head"]
    timed = [(_parse_utc(c.get("started_utc")), c) for c in attempts]
    if any(started is None for started, _ in timed):
        return None, ["a Grok attempt at this head has no parseable started_utc"]
    if any(started > now for started, _ in timed):
        return None, ["a Grok attempt at this head is dated after the gate clock"]
    timed.sort(key=lambda pair: pair[0])
    if len(timed) > 1 and timed[0][0] == timed[1][0]:
        return None, ["two Grok attempts at this head share the first started_utc"]
    first = timed[0][1]
    if _text(first.get("slot")) != slot:
        return None, ["the first Grok attempt at this head is not tagged for this slot"]
    return first, []


def _grok_reasons(
    consultation: Mapping[str, Any],
    *,
    expected_diff_sha256: str,
    expected_prompt_sha256: str,
    expected_files_total: int,
    expected_nonce: str,
    excluded_requesters: frozenset[str] = frozenset(),
) -> list[str]:
    reasons: list[str] = []
    if not REQUEST_ID_RE.match(_text(consultation.get("request_id"))):
        reasons.append("Grok request_id missing or not a ledger id")
    # The requester is a relay: it never supplies the verdict, but it must be a
    # known, non-Grok bridge lane outside ``excluded_requesters`` (Rule 12
    # condition 3, enforced in its stricter form; see the evaluator docstring).
    requester = _text(consultation.get("requester"))
    if requester not in FAMILIES or requester == GROK_AGENT:
        reasons.append(f"Grok requester {requester!r} is not a known bridge lane")
    elif requester in excluded_requesters:
        reasons.append(
            f"Grok requester {requester!r} is an implementer or a candidate for this slot"
        )
    if not NONCE_RE.match(expected_nonce):
        reasons.append("expected gate nonce is missing")
    elif _text(consultation.get("nonce")) != expected_nonce:
        reasons.append("Grok nonce is not the gate nonce")
    if _text(consultation.get("effort")) != GROK_REQUIRED_EFFORT:
        reasons.append("Grok effort is not high")
    for key in ("prompt_sha256", "input_sha256", "answer_sha256"):
        if not SHA256_RE.match(_text(consultation.get(key))):
            reasons.append(f"Grok {key} missing or malformed")
    for key, expected, label in (
        ("input_sha256", expected_diff_sha256, "exact-head diff"),
        ("prompt_sha256", expected_prompt_sha256, "gate-built prompt"),
    ):
        if not SHA256_RE.match(expected):
            reasons.append(f"expected {label} sha256 is missing")
        elif _text(consultation.get(key)) != expected:
            reasons.append(f"Grok {key} is not the {label}")
    coverage = consultation.get("coverage")
    coverage = coverage if isinstance(coverage, Mapping) else {}
    total = coverage.get("files_total")
    reviewed = coverage.get("files_reviewed")
    if not (
        coverage.get("complete") is True
        and type(expected_files_total) is int
        and expected_files_total > 0
        and type(total) is int
        and type(reviewed) is int
        and total == expected_files_total
        and reviewed == total
    ):
        reasons.append("Grok coverage is not the complete exact-head diff")
    answer_text = consultation.get("answer_text")
    if not isinstance(answer_text, str):
        reasons.append("Grok original answer text is missing")
    elif hashlib.sha256(answer_text.encode("utf-8")).hexdigest() != _text(
        consultation.get("answer_sha256")
    ):
        reasons.append("Grok answer text does not match answer_sha256")
    elif _answer_verdict_line(answer_text) != GROK_APPROVE_LINE:
        reasons.append("Grok original answer does not begin with an exact APPROVE line")
    return reasons


def _answer_verdict_line(answer_text: str) -> str:
    """First non-empty line of the original answer, markdown emphasis removed."""
    for line in answer_text.splitlines():
        stripped = line.strip().strip("*_#> \t").rstrip(".").strip()
        if stripped:
            return stripped
    return ""


def evaluate_rule12_review_eligibility(
    *,
    task_id: str,
    head: str,
    contributors: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
    now_utc: str,
    grok_consultations: Iterable[Mapping[str, Any]] = (),
    expected_diff_sha256: str = "",
    expected_prompt_sha256: str = "",
    expected_files_total: int = 0,
    expected_nonce: str = "",
) -> dict[str, Any]:
    """Evaluate the Rule 12 review slots for ``task_id`` at ``head``.

    ``decision`` is one of ``refused`` (invalid input), ``blocked`` (a
    recognized-RCO veto), ``not_satisfied`` or ``satisfied``. Only
    ``satisfied`` means every review slot is held; CI, charter and receipt
    checks stay with the gate. The ``expected_*`` values must be computed by
    the gate from the exact head, never taken from the requester.

    A Grok requester that is an implementer, or a candidate for the slot it
    asks Grok to fill, is refused. That is stricter than Rule 12 condition 3
    (author and the filled slot's build peer only); the rule text is unchanged.
    """
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "task_id": task_id,
        "head": head,
        "decision": "refused",
        "reasons": [],
        "wired": False,
    }
    reasons: list[str] = result["reasons"]
    if not isinstance(task_id, str) or not task_id.strip():
        reasons.append("task_id is required")
    if not isinstance(head, str) or not SHA1_RE.match(head):
        reasons.append("head must be a 40-char lowercase sha")
    now = _parse_utc(now_utc)
    if now is None:
        reasons.append("now_utc must be an ISO UTC timestamp")
    implementers: set[str] = set()
    contributor_list = list(contributors)
    if not contributor_list:
        reasons.append("at least one implementer (author) is required")
    for contributor in contributor_list:
        agent = _text(contributor.get("agent")) if isinstance(contributor, Mapping) else ""
        role = _text(contributor.get("role")) if isinstance(contributor, Mapping) else ""
        if agent not in FAMILIES:
            reasons.append(f"unknown contributor agent {agent!r}")
        if role not in IMPLEMENTER_ROLES:
            reasons.append(f"unknown contributor role {role!r} for {agent!r}")
        if agent == GROK_AGENT and role not in GROK_DISQUALIFYING_ROLES:
            continue  # Grok advice/review is not implementation
        implementers.add(agent)
    if contributor_list and not reasons and not implementers:
        # Only Grok advice/review was named: nobody implemented the change, so no
        # slot can be checked against an implementer (PR1810 Grok review c0402).
        reasons.append("no implementer remains: Grok advice or review is not implementation")
    event_list = [event for event in events if isinstance(event, Mapping)]
    if reasons:
        return result
    assert now is not None

    implementer_families = {FAMILIES[agent] for agent in implementers}
    result["implementers"] = sorted(implementers)
    result["implementer_families"] = sorted(implementer_families)

    # 1. Recognized-RCO veto over ALL recognized RCOs, ordered by ts_utc.
    blocking = [rco for rco in RECOGNIZED_RCOS if _rco_vetoes(event_list, rco, task_id, head, now)]
    result["blocking_rcos"] = blocking

    def approved(agent: str, statuses: frozenset[str]) -> bool:
        return any(
            _on_task(event, agent, task_id)
            and _is_pass_status(event, statuses)
            and _event_head(event) == head
            and _evidence_time_ok(event, now)
            for event in event_list
        )

    def standing(agent: str, statuses: frozenset[str]) -> str:
        if agent in implementers:
            return "implementer"
        if _self_recused(event_list, agent, task_id, head, now, timed=False) and approved(
            agent, statuses
        ):
            return "conflicting"
        if _self_recused(event_list, agent, task_id, head, now, timed=True):
            return "recused"
        if _absent(event_list, agent, task_id, head, now, implementers):
            return "absent"
        return "eligible"

    def fill(statuses: frozenset[str], agents: Sequence[str]) -> dict[str, Any]:
        slot_standing = {agent: standing(agent, statuses) for agent in agents}
        present = [a for a, s in slot_standing.items() if s in ("eligible", "conflicting")]
        holders = [a for a in present if slot_standing[a] == "eligible" and approved(a, statuses)]
        return {"standing": slot_standing, "present": present, "holders": holders}

    # 2. RCO slot: every present recognized RCO must pass at the head.
    rco_slot = fill(RCO_PASS_STATUSES, RECOGNIZED_RCOS)
    rco_slot["missing_rco_pass"] = [a for a in rco_slot["present"] if a not in rco_slot["holders"]]
    if rco_slot["present"]:
        rco_slot["state"] = "pending" if rco_slot["missing_rco_pass"] else "held"
    else:
        rco_slot["state"] = "vacant"

    # 2b. Pool fallback (operator 2026-10-10 06:56Z): with no recognized RCO
    #     present, the first eligible pool lane, in RCO_POOL_FALLBACK order, with an
    #     exact-head rco_pass and no uncleared block of its own holds the RCO slot.
    pool: dict[str, Any] = {
        "order": list(RCO_POOL_FALLBACK),
        "standing": {},
        "holder": "",
        "available": [],
        "blocking": [],
    }
    rco_slot["pool_fallback"] = pool
    if rco_slot["state"] == "vacant":
        pool["standing"] = {
            agent: standing(agent, RCO_PASS_STATUSES) for agent in RCO_POOL_FALLBACK
        }
        for agent in RCO_POOL_FALLBACK:
            if (
                pool["standing"][agent] == "eligible"
                and approved(agent, RCO_PASS_STATUSES)
                and not _rco_vetoes(event_list, agent, task_id, head, now)
            ):
                pool["holder"] = agent
                break

    # 3. Opposite-family slot: one approval from a lane outside every implementer
    #    family that does not already sit in the RCO slot, so one identity never
    #    holds both slots (Rule 9a distinct identities; RCO1 F1 on 0aabaaab).
    candidates = [
        agent
        for agent, family in FAMILIES.items()
        if agent != GROK_AGENT
        and family not in implementer_families
        and agent not in rco_slot["present"]
        and agent != pool["holder"]
    ]
    opposite_slot = fill(OPPOSITE_FAMILY_APPROVAL_STATUSES, candidates)
    if opposite_slot["present"]:
        opposite_slot["state"] = "held" if opposite_slot["holders"] else "pending"
    else:
        opposite_slot["state"] = "vacant"

    # 3b. Settle the pool fallback now that the opposite-family holders are known.
    #     A pool lane that holds the opposite-family slot is not available for the
    #     RCO slot. An uncleared block from any present pool lane (eligible or
    #     conflicting, the opposite-family holder included) blocks the slot (a
    #     block outranks a pass, as for recognized RCOs); otherwise the holder
    #     holds it, or the slot waits for an available lane, and only when none is
    #     left is it vacant for Grok ("tai jos ei mikään niistä niin grok").
    if rco_slot["state"] == "vacant":
        opposite_holders = set(opposite_slot["holders"])
        pool["available"] = [
            agent
            for agent in RCO_POOL_FALLBACK
            if pool["standing"][agent] in ("eligible", "conflicting")
            and agent not in opposite_holders
        ]
        pool["blocking"] = [
            agent
            for agent in RCO_POOL_FALLBACK
            if pool["standing"][agent] in ("eligible", "conflicting")
            and _rco_vetoes(event_list, agent, task_id, head, now)
        ]
        if pool["blocking"]:
            rco_slot["state"] = POOL_BLOCKED_STATE
        elif pool["holder"]:
            rco_slot["state"] = POOL_FALLBACK_STATE
            rco_slot["holders"] = [pool["holder"]]
        elif pool["available"]:
            rco_slot["state"] = "pending"

    slots = {SLOT_OPPOSITE_FAMILY: opposite_slot, SLOT_RCO: rco_slot}
    result["slots"] = slots

    # 4. Grok after the pool: one vacant slot, or one external review when the
    #    whole pool is genuinely ineligible (implementer or self-recused).
    vacant = [name for name in SLOTS if slots[name]["state"] == "vacant"]
    pool_standing = {
        agent: standing(
            agent,
            RCO_PASS_STATUSES if agent in RECOGNIZED_RCOS else OPPOSITE_FAMILY_APPROVAL_STATUSES,
        )
        for agent in REVIEW_POOL
    }
    whole_pool_ineligible = all(s in ("implementer", "recused") for s in pool_standing.values())
    grok: dict[str, Any] = {
        "vacant_slots": vacant,
        "pool_standing": pool_standing,
        "whole_pool_ineligible": whole_pool_ineligible,
        "filled": [],
        "reasons": [],
    }
    result["grok_fallback"] = grok
    consultations = [c for c in grok_consultations if isinstance(c, Mapping)]

    # Rule 12 condition 3 says the requester is neither the PR author nor the
    # build peer whose slot Grok fills. This evaluator enforces a STRICTER form:
    # no implementer of any role, and no lane that is itself a candidate for the
    # slot being filled (every recognized RCO for the rco slot, every
    # opposite-family candidate for that slot). External review keeps the
    # implementer exclusion only, because the whole pool is out by definition.
    # Pool-fallback lanes are not added to the rco set: Grok fills the rco slot
    # only when no pool lane is available, and adding them would leave no lane
    # able to relay (every non-Grok lane would be excluded).
    slot_candidates = {
        SLOT_RCO: frozenset(RECOGNIZED_RCOS),
        SLOT_OPPOSITE_FAMILY: frozenset(candidates),
        SLOT_EXTERNAL_REVIEW: frozenset(),
    }

    def qualifying(tag: str) -> Mapping[str, Any] | None:
        consultation, tag_reasons = _first_grok_consultation(
            consultations, task_id, head, tag, now
        )
        if consultation is not None:
            tag_reasons = _grok_reasons(
                consultation,
                expected_diff_sha256=expected_diff_sha256,
                expected_prompt_sha256=expected_prompt_sha256,
                expected_files_total=expected_files_total,
                expected_nonce=expected_nonce,
                excluded_requesters=frozenset(implementers) | slot_candidates[tag],
            )
        if tag_reasons:
            grok["reasons"].extend(f"{tag}: {reason}" for reason in tag_reasons)
            return None
        return consultation

    def grok_record(consultation: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "reviewer": GROK_AGENT,
            "request_id": consultation["request_id"],
            "requester_relay": consultation["requester"],
            "nonce": consultation["nonce"],
            "prompt_sha256": consultation["prompt_sha256"],
            "input_sha256": consultation["input_sha256"],
            "answer_sha256": consultation["answer_sha256"],
        }

    if GROK_AGENT in implementers:
        grok["reasons"].append("Grok is an implementer of this change")
    elif whole_pool_ineligible:
        consultation = qualifying(SLOT_EXTERNAL_REVIEW)
        if consultation is not None:
            record = grok_record(consultation)
            for name in SLOTS:
                slots[name]["state"] = "held_by_grok_external_review"
            grok["external_review"] = record
            grok["filled"].append(SLOT_EXTERNAL_REVIEW)
    elif len(vacant) > GROK_MAX_SLOTS:
        grok["reasons"].append(
            f"{len(vacant)} slots vacant but the whole pool is not genuinely ineligible; "
            f"Grok fills at most {GROK_MAX_SLOTS} slot"
        )
    else:
        for name in vacant:
            consultation = qualifying(name)
            if consultation is None:
                continue
            slots[name]["state"] = "held_by_grok_fallback"
            slots[name]["grok"] = grok_record(consultation)
            grok["filled"].append(name)

    # 5. Verdict: a veto outranks everything, then every slot must be held.
    if blocking:
        result["decision"] = "blocked"
        reasons.append(f"recognized RCO veto: {', '.join(blocking)}")
        return result
    unheld = [name for name in SLOTS if not slots[name]["state"].startswith("held")]
    if unheld:
        result["decision"] = "not_satisfied"
        reasons.extend(f"{name} slot is {slots[name]['state']}" for name in unheld)
        if pool["blocking"]:
            reasons.append(
                "rco pool fallback lane block not cleared by its own later exact-head "
                f"rco_pass: {', '.join(pool['blocking'])}"
            )
        reasons.extend(grok["reasons"])
        return result
    result["decision"] = "satisfied"
    return result
