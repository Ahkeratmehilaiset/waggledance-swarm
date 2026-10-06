# SPDX-License-Identifier: BUSL-1.1
"""CLAUDE.md Rule 12 review-eligibility evaluator (pure, fail-closed, UNWIRED).

Computes, for one task at one exact head, whether the Rule 12 review slots are
held by eligible approvers:

* the **opposite-family** slot: an approval at the head from a lane whose model
  family is outside every implementer family (GPT ``codex-lead-1`` /
  ``codex-tools-1``; Claude ``fable-5`` / ``claude-rco-1`` / ``claude-rco-2``);
* the **RCO** slot: ``rco_pass`` at the head from EVERY eligible recognized RCO
  (not an implementer, not self-recused, not absent);
* a **Grok fallback** may fill one slot whose primaries are all ineligible,
  recused or absent, bound to one helper-ledger consultation.

Implementers are every author / concept / design / measurement source of the
change. A recusal is a self-posted ``type=message`` event with a status in
``RECUSAL_STATUSES`` bound to the task and the exact head; anything else (for
example an effort setting such as ``medium``) is not a recusal. A primary is
absent only when a bound review request at the head got no answer from it for
``ABSENCE_SECONDS``, computed here from bridge timestamps.

A recognized-RCO veto is evaluated over ALL recognized RCOs, including
implementers and recused ones, and outranks every pass including Grok. A Grok
answer that is missing, partial, unclear, negative, at another head or not the
first at that head leaves its slot empty; it never blocks by itself and it is
never recorded as an ``rco_pass``.

This module is NOT read by any merge gate yet. It decides nothing on its own;
wiring it into ``tools/idle_consensus_auto_merge.py`` and the receipt writer is
a separate (a)-class gate-code PR.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
# Must equal tools.check_promotion_eligible.DEFAULT_RCO_AGENTS (drift-guard test).
RECOGNIZED_RCOS: tuple[str, ...] = ("claude-rco-1", "claude-rco-2")
# Must equal tools.check_rco_pass_present.RCO_PASS_STATUSES (drift-guard test).
RCO_PASS_STATUSES = frozenset({"rco_pass"})
PASS_EVENT_TYPES = frozenset({"decision", "rco_review"})
# Exact approval statuses for the opposite-family slot; nothing looser.
OPPOSITE_FAMILY_APPROVAL_STATUSES = frozenset({"rco_pass", "build_consensus_pass"})
# Any recognized-RCO event of these types blocks unless its status is a pass.
RCO_BLOCK_TYPES = frozenset({"finding", "blocked"})
RECUSAL_EVENT_TYPE = "message"
RECUSAL_STATUSES = frozenset({"rco_recused", "review_recused"})
IMPLEMENTER_ROLES = frozenset({"author", "concept", "design", "measurement"})
ABSENCE_SECONDS = 60 * 60
GROK_MAX_SLOTS = 1
GROK_REQUIRED_EFFORT = "high"
GROK_APPROVE_VERDICT = "approve"
SLOT_RCO = "rco"
SLOT_OPPOSITE_FAMILY = "opposite_family"
SLOTS = (SLOT_OPPOSITE_FAMILY, SLOT_RCO)

SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")


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


def _is_rco_pass(event: Mapping[str, Any], head: str) -> bool:
    return (
        _text(event.get("type")) in PASS_EVENT_TYPES
        and _text(event.get("status")) in RCO_PASS_STATUSES
        and _event_head(event) == head
    )


def _is_rco_block(event: Mapping[str, Any], head: str) -> bool:
    """A recognized-RCO event that blocks at ``head`` (or carries no valid head)."""
    event_type = _text(event.get("type"))
    status = _text(event.get("status"))
    event_head = _event_head(event)
    if event_head not in ("", None) and event_head != head:
        return False  # bound to another head: superseded by review at this head
    if event_type in RCO_BLOCK_TYPES:
        return True
    if event_type in PASS_EVENT_TYPES:
        if status in RCO_PASS_STATUSES:
            return False  # a pass never blocks; without the exact head it binds nothing
        return True  # exact allowlist: every non-pass decision blocks
    return False


def _self_recused(events: Sequence[Mapping[str, Any]], agent: str, task_id: str, head: str) -> bool:
    return any(
        _text(event.get("agent")) == agent
        and _text(event.get("task_id")) == task_id
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
) -> bool:
    """No answer from ``agent`` for ABSENCE_SECONDS after a bound request at ``head``."""
    for index, event in enumerate(events):
        if (
            _text(event.get("task_id")) != task_id
            or not _text(event.get("request_id"))
            or agent not in _recipients(event)
            or _text(event.get("agent")) == agent
            or _event_head(event) != head
        ):
            continue
        requested = _parse_utc(event.get("ts_utc"))
        if requested is None or (now - requested).total_seconds() < ABSENCE_SECONDS:
            continue
        answered = any(
            _text(later.get("agent")) == agent and _text(later.get("task_id")) == task_id
            for later in events[index + 1 :]
        )
        if not answered:
            return True
    return False


def _first_grok_consultation(
    consultations: Sequence[Mapping[str, Any]], task_id: str, head: str, slot: str
) -> Mapping[str, Any] | None:
    """First consultation at (task, head) for ``slot``; slotless ones count for every slot."""
    for consultation in consultations:
        if _text(consultation.get("task_id")) != task_id:
            continue
        if _text(consultation.get("head")) != head:
            continue
        consultation_slot = _text(consultation.get("slot"))
        if consultation_slot in ("", slot) or consultation_slot not in SLOTS:
            return consultation
    return None


def _grok_reasons(
    consultation: Mapping[str, Any] | None,
    *,
    task_id: str,
    head: str,
    expected_diff_sha256: str,
    forbidden_requesters: set[str],
) -> list[str]:
    if consultation is None:
        return ["no Grok consultation bound to this task and exact head"]
    reasons: list[str] = []
    if not REQUEST_ID_RE.match(_text(consultation.get("request_id"))):
        reasons.append("Grok request_id missing or not a ledger id")
    requester = _text(consultation.get("requester"))
    if requester not in FAMILIES or requester == GROK_AGENT:
        reasons.append(f"Grok requester {requester!r} is not a known bridge lane")
    elif requester in forbidden_requesters:
        reasons.append(f"Grok requester {requester!r} is an implementer or a primary of the slot")
    if _text(consultation.get("effort")) != GROK_REQUIRED_EFFORT:
        reasons.append("Grok effort is not high")
    for key in ("prompt_sha256", "input_sha256", "answer_sha256"):
        if not SHA256_RE.match(_text(consultation.get(key))):
            reasons.append(f"Grok {key} missing or malformed")
    if not SHA256_RE.match(expected_diff_sha256):
        reasons.append("expected diff sha256 of the exact head is missing")
    elif _text(consultation.get("input_sha256")) != expected_diff_sha256:
        reasons.append("Grok input is not the exact-head diff")
    coverage = consultation.get("coverage")
    coverage = coverage if isinstance(coverage, Mapping) else {}
    total = coverage.get("files_total")
    reviewed = coverage.get("files_reviewed")
    if not (
        coverage.get("complete") is True
        and type(total) is int
        and type(reviewed) is int
        and total > 0
        and reviewed == total
    ):
        reasons.append("Grok coverage is not complete")
    if _text(consultation.get("verdict")) != GROK_APPROVE_VERDICT:
        reasons.append("Grok verdict is not an explicit approve")
    return reasons


def evaluate_rule12_review_eligibility(
    *,
    task_id: str,
    head: str,
    contributors: Iterable[Mapping[str, Any]],
    events: Iterable[Mapping[str, Any]],
    now_utc: str,
    grok_consultations: Iterable[Mapping[str, Any]] = (),
    expected_diff_sha256: str = "",
) -> dict[str, Any]:
    """Evaluate the Rule 12 review slots for ``task_id`` at ``head``.

    ``decision`` is one of ``refused`` (invalid input), ``blocked`` (a
    recognized-RCO veto), ``not_satisfied`` or ``satisfied``. Only
    ``satisfied`` means every review slot is held; CI, charter and receipt
    checks stay with the gate.
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
        implementers.add(agent)
    event_list = [event for event in events if isinstance(event, Mapping)]
    if reasons:
        return result
    assert now is not None

    implementer_families = {FAMILIES[agent] for agent in implementers}
    result["implementers"] = sorted(implementers)
    result["implementer_families"] = sorted(implementer_families)

    # 1. Recognized-RCO veto over ALL recognized RCOs; a later own pass at the
    #    head clears that RCO's earlier block, a recusal never does.
    blocking: list[str] = []
    for rco in RECOGNIZED_RCOS:
        state = None
        for event in event_list:
            if _text(event.get("agent")) != rco or _text(event.get("task_id")) != task_id:
                continue
            if _is_rco_pass(event, head):
                state = "pass"
            elif _is_rco_block(event, head):
                state = "block"
        if state == "block":
            blocking.append(rco)
    result["blocking_rcos"] = blocking

    def standing(agent: str) -> str:
        if agent in implementers:
            return "implementer"
        if _self_recused(event_list, agent, task_id, head):
            return "recused"
        if _absent(event_list, agent, task_id, head, now):
            return "absent"
        return "eligible"

    def approved(agent: str, statuses: frozenset[str]) -> bool:
        return any(
            _text(event.get("agent")) == agent
            and _text(event.get("task_id")) == task_id
            and _text(event.get("type")) in PASS_EVENT_TYPES
            and _text(event.get("status")) in statuses
            and _event_head(event) == head
            for event in event_list
        )

    # 2. RCO slot: every eligible recognized RCO must pass at the head.
    rco_standing = {rco: standing(rco) for rco in RECOGNIZED_RCOS}
    rco_required = [rco for rco, s in rco_standing.items() if s == "eligible"]
    rco_missing = [rco for rco in rco_required if not approved(rco, RCO_PASS_STATUSES)]
    rco_slot: dict[str, Any] = {"standing": rco_standing, "required": rco_required}
    if rco_required:
        rco_slot["state"] = "held" if not rco_missing else "pending"
        rco_slot["missing_rco_pass"] = rco_missing
        rco_slot["holders"] = [rco for rco in rco_required if rco not in rco_missing]
    else:
        rco_slot["state"] = "vacant"

    # 3. Opposite-family slot: a lane outside every implementer family.
    candidates = [
        agent
        for agent, family in FAMILIES.items()
        if agent != GROK_AGENT and family not in implementer_families
    ]
    opposite_standing = {agent: standing(agent) for agent in candidates}
    opposite_eligible = [a for a, s in opposite_standing.items() if s == "eligible"]
    opposite_holders = [
        a for a in opposite_eligible if approved(a, OPPOSITE_FAMILY_APPROVAL_STATUSES)
    ]
    opposite_slot: dict[str, Any] = {"standing": opposite_standing}
    if opposite_eligible:
        opposite_slot["state"] = "held" if opposite_holders else "pending"
        opposite_slot["holders"] = opposite_holders
    else:
        opposite_slot["state"] = "vacant"

    slots = {SLOT_OPPOSITE_FAMILY: opposite_slot, SLOT_RCO: rco_slot}
    result["slots"] = slots

    # 4. Grok fallback for vacant slots only, at most GROK_MAX_SLOTS.
    vacant = [name for name in SLOTS if slots[name]["state"] == "vacant"]
    grok: dict[str, Any] = {"vacant_slots": vacant, "filled": []}
    result["grok_fallback"] = grok
    consultations = [c for c in grok_consultations if isinstance(c, Mapping)]
    if GROK_AGENT in implementers:
        grok["reasons"] = ["Grok is an implementer of this change"]
    elif len(vacant) > GROK_MAX_SLOTS:
        grok["reasons"] = [
            f"{len(vacant)} slots vacant; Rule 12 lets Grok fill at most {GROK_MAX_SLOTS}"
        ]
    else:
        grok["reasons"] = []
        for name in vacant:
            primaries = set(RECOGNIZED_RCOS) if name == SLOT_RCO else set(candidates)
            consultation = _first_grok_consultation(consultations, task_id, head, name)
            slot_reasons = _grok_reasons(
                consultation,
                task_id=task_id,
                head=head,
                expected_diff_sha256=expected_diff_sha256,
                forbidden_requesters=implementers | primaries,
            )
            if slot_reasons:
                grok["reasons"].extend(f"{name}: {reason}" for reason in slot_reasons)
                continue
            assert consultation is not None
            slots[name]["state"] = "held_by_grok_fallback"
            slots[name]["grok"] = {
                "request_id": consultation["request_id"],
                "requester": consultation["requester"],
                "prompt_sha256": consultation["prompt_sha256"],
                "input_sha256": consultation["input_sha256"],
                "answer_sha256": consultation["answer_sha256"],
            }
            grok["filled"].append(name)

    # 5. Verdict: a veto outranks everything, then every slot must be held.
    if blocking:
        result["decision"] = "blocked"
        reasons.append(f"recognized RCO veto at this head: {', '.join(blocking)}")
        return result
    unheld = [name for name in SLOTS if not slots[name]["state"].startswith("held")]
    if unheld:
        result["decision"] = "not_satisfied"
        reasons.extend(f"{name} slot is {slots[name]['state']}" for name in unheld)
        reasons.extend(grok["reasons"])
        return result
    result["decision"] = "satisfied"
    return result
