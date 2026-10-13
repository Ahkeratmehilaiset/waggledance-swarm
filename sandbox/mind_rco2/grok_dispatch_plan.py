# SPDX-License-Identifier: BUSL-1.1
"""Guarded Grok dispatcher, plan step only (claude-rco-2 sandbox; operator ~10:44Z via RCO1: the Mind may dispatch
Grok consultations itself, always through Invoke-WdGrok, inside its single-flight, with the prompt guard and size
check first, answers routed to the requester).

``decide`` and ``route`` follow RCO1's executable spec (sandbox/mind_rco1/grok_dispatch_spec.py, fbc24158): its whole
corpus runs against them in test_grok_dispatch_plan.py. ``plan`` applies ``decide`` to the candidates of one cycle:

* the shared fold (``v3_lane_inbox.build_fold``): the Grok consultations seen in the window;
* one captured ``Invoke-WdGrok.ps1 -Status`` object (read-only, taken by a lane outside this code);
* candidates ``{"requested_by", "task_id", "prompt_path", "request_id", "patch_id", "operator"}``.

This module never calls Grok, the wrapper, a process or the network. At most ONE candidate may start per plan
(single-flight); the call is a separate, later step, and its report goes to ``route``'s requester only.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from tools import bridge_mind_workspace as workspace

PLAN_SCHEMA = "rco2.sandbox.grok-dispatch-plan.v2"
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
MAX_PROMPT_BYTES = 12 * 1024  # one narrow question under 12 KB (larger prompts timed out at 900 s, 10-12)
SECRET = re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN [A-Z ]*PRIVATE KEY|"
                    r"(?i:password\s*[:=])|(?i:oauth_token)")
RAW_ROW = re.compile(r'"ts_utc"\s*:.*"agent"\s*:|"agent_uuid"\s*:')


def prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def decoded_views(prompt: str) -> list[str]:
    """The prompt as written and every JSON-string decoding of it, so an escaped marker cannot hide."""
    views = [prompt]
    for chunk in re.findall(r'"(?:[^"\\]|\\.)*"', prompt):
        try:
            value = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(value, str):
            views.append(value)
    return views


def workspace_guard(text: str) -> bool:
    """True when the Mind's decoded privacy guard (NFKC, case fold, repeated unescape) finds no guarded word."""
    try:
        workspace.guard_object(text)
    except workspace.WorkspaceError:
        return False
    return True


def decide(ask: Any, status: Any, rotation: Mapping[str, Any], seen: set, guard: Callable[[str], bool]) -> tuple[str, str]:
    """("DISPATCH", requester) | ("QUEUE", reason) | ("REFUSE", reason), in RCO1's spec order: shape, size, empty,
    secrets and guard on every decoded view, raw bridge rows, one consultation per patch or prompt, then the helper
    (available, single-flight, breaker, eligibility) and the rotation (an operator ask skips it)."""
    if not isinstance(ask, Mapping) or ask.get("requester") not in LANES or not isinstance(ask.get("prompt"), str):
        return ("REFUSE", "ask_malformed_or_unknown_requester")
    prompt = ask["prompt"]
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        return ("REFUSE", "prompt_too_large")
    if not prompt.strip():
        return ("REFUSE", "prompt_empty")
    for view in decoded_views(prompt):
        if SECRET.search(view):
            return ("REFUSE", "secret_shape")
        if not guard(view):
            return ("REFUSE", "privacy_guard")
    if RAW_ROW.search(prompt):
        return ("REFUSE", "raw_bridge_rows_in_prompt")
    key = ask.get("patch_id") or prompt_digest(prompt)
    if key in seen:
        return ("REFUSE", "already_consulted_for_this_patch")
    if not isinstance(status, Mapping) or status.get("local_availability") != "available":
        return ("QUEUE", "grok_unavailable")
    if status.get("status") in ("reserved", "interrupted_or_unknown"):
        return ("QUEUE", "single_flight_busy")
    if (status.get("model_breaker") or {}).get("state") != "closed":
        return ("QUEUE", "model_breaker_open")
    if status.get("eligible") is not True:
        return ("QUEUE", "budget_wait")
    if not ask.get("operator") and rotation.get("next") not in (None, ask["requester"]):
        return ("QUEUE", "not_this_lane_rotation_turn")
    return ("DISPATCH", ask["requester"])


def route(answer: Mapping[str, Any], asks: Iterable[Mapping[str, Any]]) -> str | None:
    """The report goes only to the lane whose ask carries the same request id and prompt digest."""
    for ask in asks:
        if ask.get("request_id") == answer.get("request_id") \
                and prompt_digest(ask.get("prompt", "")) == answer.get("prompt_sha256"):
            return ask.get("requester")
    return None


def rotation_from_fold(fold: Mapping[str, Any], requesters: Iterable[str]) -> dict[str, Any]:
    """The next turn: among these requesters, the one whose last answered consultation is oldest (never answered
    first); ties by lane order. Derived from the log, so every lane computes the same turn."""
    last: dict[str, str] = {}
    for item in fold.get("grok", []):
        if item.get("end") and item["end"]["status"] == "answered" and item.get("requested_by"):
            last[item["requested_by"]] = max(last.get(item["requested_by"], ""), item["end"]["utc"])
    ordered = sorted(set(requesters) & set(LANES), key=lambda lane: (last.get(lane, ""), LANES.index(lane)))
    return {"next": ordered[0] if ordered else None, "last_answer_utc": last}


def plan(fold: Mapping[str, Any], status: Mapping[str, Any], candidates: Iterable[Mapping[str, Any]],
         guard: Callable[[str], bool] = workspace_guard) -> dict[str, Any]:
    candidates = list(candidates)
    seen = {item["prompt_sha256"] for item in fold.get("grok", [])
            if item.get("end") and item["end"]["status"] == "answered" and item.get("prompt_sha256")}
    rotation = rotation_from_fold(fold, [c.get("requested_by") for c in candidates])
    decisions, chosen = [], None
    for candidate in candidates:
        path, text = candidate.get("prompt_path"), None
        if isinstance(path, str) and path:
            try:
                text = Path(path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = None
        ask = {"requester": candidate.get("requested_by"), "prompt": text, "request_id": candidate.get("request_id"),
               "patch_id": candidate.get("patch_id"), "operator": bool(candidate.get("operator"))}
        verdict = ("REFUSE", "prompt_missing_or_unreadable") if text is None else decide(ask, status, rotation, seen, guard)
        if verdict[0] == "DISPATCH":
            if chosen is None:
                chosen = len(decisions)
            else:
                verdict = ("QUEUE", "single_flight_one_per_plan")
        decisions.append({"requested_by": candidate.get("requested_by"), "task_id": candidate.get("task_id"),
                          "request_id": candidate.get("request_id"),
                          "prompt_sha256": prompt_digest(text) if text is not None else None,
                          "prompt_bytes": len(text.encode("utf-8")) if text is not None else None,
                          "verdict": verdict[0], "reason": verdict[1]})
    result = {"schema": PLAN_SCHEMA, "authority": "none", "as_of_utc": fold["as_of_utc"], "calls_made": 0,
              "rotation_next": rotation["next"], "may_start": decisions[chosen] if chosen is not None else None,
              "decisions": decisions,
              "next_step": "none" if chosen is None else
              "a separate step runs Invoke-WdGrok.ps1 -ReadOnly with this task id and prompt path; route() sends the "
              "report ref to its requester only; this plan grants nothing"}
    workspace.guard_object(result)
    return result
