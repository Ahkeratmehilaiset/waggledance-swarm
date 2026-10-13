# SPDX-License-Identifier: BUSL-1.1
"""Guarded Grok dispatcher, plan step only (claude-rco-2 sandbox; operator ~10:44Z via RCO1: the Mind may dispatch
Grok consultations itself, always through Invoke-WdGrok, inside its single-flight, with the prompt guard and size
check first, answers routed to the requester).

This module never calls Grok, the wrapper, a process or the network. It is a pure function of:

* the shared fold (``v3_lane_inbox.build_fold``): Grok consultations seen in the window and the open asks;
* one captured ``Invoke-WdGrok.ps1 -Status`` JSON object (read-only, taken by a lane outside this code);
* candidate items: ``{"requested_by", "task_id", "prompt_path"}`` (a prompt file each, or none).

It returns a plan: at most ONE item that may start now (single-flight), and every other candidate with the
reasons it waits or is refused. The call itself is a separate, later step (after the RCO1 privacy tests and Lead
agreement); its arguments are the item's task id and prompt path, and the answer goes to ``requested_by``.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools import bridge_mind_workspace as workspace

PLAN_SCHEMA = "rco2.sandbox.grok-dispatch-plan.v1"
PROMPT_LIMIT = 12 * 1024  # one narrow question under 12 KB (larger prompts timed out at 900 s, 10-12)
BUSY = frozenset({"reserved", "interrupted_or_unknown"})


def _helper_blockers(status: Mapping[str, Any], now) -> list[str]:
    blockers = []
    if status.get("status") in BUSY:
        blockers.append(f"helper_{status.get('status')}")  # single-flight: one consultation at a time
    if status.get("eligible") is False:
        blockers.append("helper_not_eligible")
    until = workspace.parse_time(status.get("next_eligible_utc"))
    if until is not None and now is not None and until > now:
        blockers.append("helper_next_eligible_later")
    if status.get("model_holds"):
        blockers.append("model_hold")
    breaker = status.get("model_breaker")
    if isinstance(breaker, Mapping) and breaker.get("open"):
        blockers.append("model_breaker_open")
    return blockers


def _prompt_checks(path: Any) -> tuple[list[str], str | None, int | None]:
    if not isinstance(path, str) or not path:
        return ["prompt_missing"], None, None
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ["prompt_unreadable"], None, None
    reasons = []
    if len(data) > PROMPT_LIMIT:
        reasons.append("prompt_over_12kb")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return reasons + ["prompt_not_utf8"], hashlib.sha256(data).hexdigest(), len(data)
    try:
        workspace.guard_object(text)  # the decoded privacy guard, the same one every Mind output passes
    except workspace.WorkspaceError:
        reasons.append("prompt_guard_hit")
    return reasons, hashlib.sha256(data).hexdigest(), len(data)


def plan(fold: Mapping[str, Any], status: Mapping[str, Any], candidates: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    now = workspace.parse_time(fold["as_of_utc"])
    consultations = fold.get("grok", [])
    helper = _helper_blockers(status, now)
    open_tasks = {c["task"] for c in consultations if c["end"] is None and c["start_ref"]
                  and (now - (workspace.parse_time(c["started_utc"]) or now)).total_seconds() <= 3600}
    answered_prompts = {c.get("prompt_sha256") for c in consultations
                        if c["end"] and c["end"]["status"] == "answered" and c.get("prompt_sha256")}
    last_answer: dict[str, str] = {}
    for c in consultations:
        if c["end"] and c["end"]["status"] == "answered" and c["requested_by"]:
            last_answer[c["requested_by"]] = max(last_answer.get(c["requested_by"], ""), c["end"]["utc"])
    items = []
    for candidate in candidates:
        reasons, digest, size = _prompt_checks(candidate.get("prompt_path"))
        if candidate.get("task_id") in open_tasks:
            reasons.append("task_has_open_consultation")
        if digest is not None and digest in answered_prompts:
            reasons.append("same_prompt_already_answered")  # one consultation per prompt content
        items.append({"requested_by": candidate.get("requested_by"), "task_id": candidate.get("task_id"),
                      "prompt_sha256": digest, "prompt_bytes": size, "reasons": reasons,
                      "last_answer_utc": last_answer.get(candidate.get("requested_by"), "")})
    # Rotation: the requester whose last answered consultation is oldest goes first; ties by task id.
    ready = sorted((i for i in items if not i["reasons"]), key=lambda i: (i["last_answer_utc"], str(i["task_id"])))
    chosen = ready[0] if ready and not helper else None
    waiting = []
    for item in items:
        if item is chosen:
            continue
        why = list(item["reasons"]) or (helper or ["single_flight_one_per_plan"])
        waiting.append(dict(item, reasons=why))
    result = {"schema": PLAN_SCHEMA, "authority": "none", "as_of_utc": fold["as_of_utc"], "calls_made": 0,
              "helper_blockers": helper, "may_start": chosen, "waiting": waiting,
              "next_step": "none" if chosen is None else
              "a separate step runs Invoke-WdGrok.ps1 -ReadOnly with this task id and prompt path and routes the "
              "report ref to requested_by; this plan grants nothing"}
    workspace.guard_object(result)
    return result
