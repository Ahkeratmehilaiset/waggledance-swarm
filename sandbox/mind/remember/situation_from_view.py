"""Situation from the lane view: the question REMEMBER is asked when a lane turns to its next action (D10, fable-5).

Step 1's lane view says what a lane must do now; REMEMBER (v2.1 and later) answers a situation text with lesson lines.
Nothing joined the two: JOIN turn v0.3 takes a hand-written REMEMBER envelope, so a live card had no memory
(claude-rco-2's round 3 finding). ``situation_from_view`` builds that situation from the view alone. It is pure: the
same inputs give the same text; it reads and writes nothing, has no clock and runs no model.

The rule (claude-rco-2's round 3 design, corrected against step 1's code):

1. The view must be at or before ``as_of_utc`` (else ``view_time``). The head is the lane's first action: of the
   view's ``action`` records with an empty slot the lane owns, the least (only empty slot first,
   ``waiting_since_utc``, PR number), lane_view's own order, which compares its own whole-second stamps (or
   ``UNKNOWN``, last) as text; none gives ``no_action``. The lane's first action line must name that PR at its
   head, with that slot and task, or the view is refused (``view_mismatch``): a changed order is never guessed.
2. The words: the lane and its slot kind (``rco review`` or ``build vote``); the newest ``wake_request`` addressed to
   the lane at or before ``as_of_utc``, when the caller passes the lane's open requests (a tie in time, such as two
   bridge stamps that differ past the microsecond, goes to the greater event ref, then body: never to input order);
   and the PR branch's words, each once, in first-seen order. An action record has no reasons and no failing checks
   (lane_view keeps both only for a PR it abstains on, and an action needs every required check green), so neither
   is used. ``[[links]]``, UUIDs and hex runs of 7 or more characters (a git short sha) are dropped, text is
   lower-cased, and the branch loses plain numbers.
3. Identifier-only: fewer than ``MIN_WORDS`` distinct words in the request and branch (REMEMBER's tokenizer, without
   numbers and the parts of lane names) give no situation (``identifier_only``). ``MIN_WORDS`` is a sandbox default,
   NOT calibrated: no real capture was read.
4. Privacy: the workspace's decoded guard runs on the raw request body (one that trips it is dropped whole, never
   echoed), on the raw branch before it is split into words (where a split could hide a guarded word) and on the
   finished situation; a branch or a situation that trips it gives none (``guarded``).
5. The caller's, the view's and the requests' times are compared as times (the workspace's ``parse_time``), never
   as text, where a fraction of a second sorts wrongly; a request without a readable time is ignored, and an
   unreadable ``as_of_utc`` raises ``ValueError``. Only the head order compares text, as lane_view does (rule 1).

A situation is at most 1750 characters (lane and slot kind, request ``CAPS["request"]``, branch ``CAPS["branch"]``),
inside the 2000-character e5 window. ``basis`` names the parts that carried words, so a caller can tell a branch-only
situation from one with the lane's task. Limits: a sandbox module that nothing calls. The capture holds no request
bodies, so a live head would mostly be branch-only until it does; claude-rco-2's round 10 keeps JOIN unwired till then.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Mapping, NamedTuple, Sequence

SRC = Path(__file__).resolve().parents[3]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from tools import bridge_mind_workspace as ws  # noqa: E402

LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")  # step 1's lanes (lane_view.LANES)
SLOT_OWNER = {**{slot: agent for agent, slot in ws.BUILD_SLOT.items()}, **{rco: rco for rco in ws.RECOGNIZED_RCOS}}
TOKEN = re.compile(r"[^\W_]{2,}", re.UNICODE)  # REMEMBER's own tokenizer (step2/q4.py TOKEN)
WORD = re.compile(r"[^\W_]+", re.UNICODE)
DROP = re.compile(r"\[\[[^\]]*\]\]|" + ws.UUID.pattern + r"|\b[0-9a-fA-F]{7,}\b")
LANE_PARTS = frozenset(part for lane in LANES for part in TOKEN.findall(lane))
REQUEST_TYPE = "wake_request"
MIN_WORDS = 3  # a sandbox default, NOT calibrated: no real capture was read
CAPS = {"request": 1600, "branch": 120}


class Situation(NamedTuple):
    text: str  # the situation, or "" when none is given
    reason: str  # "" when one is given, else why not
    basis: tuple[str, ...]  # the parts that carried words, in order: "lane", "request", "branch"


def _guarded(text: str) -> bool:
    """True when the text holds a guarded word (the workspace's decoded check)."""
    try:
        ws.guard_object(text)
    except ws.WorkspaceError:
        return True
    return False


def _clean(text: Any) -> str:
    return " ".join(DROP.sub(" ", str(text)).lower().split())


def _clip(text: str, cap: int) -> str:
    """At most ``cap`` characters, cut at a space; a word the cap ends stays, and one word longer than the cap is cut."""
    if len(text) <= cap:
        return text
    cut = text[:cap + 1].rsplit(" ", 1)[0]
    return cut if len(cut) <= cap else text[:cap]


def _branch_words(branch: Any) -> str:
    words: dict[str, None] = {}
    for word in WORD.findall(_clean(branch)):
        if not word.isdigit():
            words.setdefault(word, None)
    return _clip(" ".join(words), CAPS["branch"])


def head_record(view: Mapping[str, Any], lane: str) -> tuple[str, Mapping[str, Any]] | None:
    """(slot, record) of the lane's first action in lane_view's own order, or None."""
    rows = []
    for record in (view.get("prs") or {}).values():
        if record.get("state") != "action":
            continue
        slots = record["empty_slots"]
        for slot in slots:
            if SLOT_OWNER.get(slot) == lane:
                rows.append(((0 if len(slots) == 1 else 1, record["waiting_since_utc"], record["pr"]), slot, record))
    if not rows:
        return None
    _, slot, record = min(rows, key=lambda row: row[0])
    return slot, record


def _names_head(view: Mapping[str, Any], lane: str, slot: str, record: Mapping[str, Any]) -> bool:
    """The lane's first action line is this record's line (lane_view.action_line's opening words and its task)."""
    actions = ((view.get("lanes") or {}).get(lane) or {}).get("actions") or []
    kind = "RCO" if slot in ws.RECOGNIZED_RCOS else "build"
    return bool(actions) and actions[0].startswith(f"{kind} vote on PR{record['pr']} at {record['github_head'][:12]} ") \
        and f"({slot}, task {record['branch']})" in actions[0]


def _recipients(value: Any) -> list[str]:
    items = value.split(",") if isinstance(value, str) else value if isinstance(value, (list, tuple)) else []
    return [str(item).strip() for item in items]


def _request(requests: Sequence[Mapping[str, Any]], lane: str, as_of: Any) -> str:
    """The newest request addressed to the lane at or before as_of, cleaned and clipped; "" when there is none or when
    its body holds a guarded word (dropped whole, never echoed)."""
    own = []
    for row in requests:
        when = ws.parse_time(row.get("ts_utc"))
        if row.get("type") == REQUEST_TYPE and lane in _recipients(row.get("to")) and when is not None and when <= as_of:
            own.append((when, str(row.get("ref", "")), str(row.get("message", ""))))
    if not own:
        return ""
    body = max(own)[2]
    return "" if _guarded(body) else _clip(_clean(body), CAPS["request"])


def situation_from_view(view: Mapping[str, Any], lane: str, as_of_utc: str,
                        requests: Sequence[Mapping[str, Any]] = ()) -> Situation:
    """The situation REMEMBER is asked for the lane's first action, or "" and why not (``no_action``, ``view_time``,
    ``view_mismatch``, ``identifier_only``, ``guarded``)."""
    as_of = ws.parse_time(as_of_utc)
    if as_of is None:
        raise ValueError("an explicit as_of_utc is required")
    seen = ws.parse_time(view.get("as_of_utc"))
    if seen is None or seen > as_of:
        return Situation("", "view_time", ())
    head = head_record(view, lane)
    if head is None:
        return Situation("", "no_action", ())
    slot, record = head
    if not _names_head(view, lane, slot, record):
        return Situation("", "view_mismatch", ())
    if _guarded(str(record["branch"])):
        return Situation("", "guarded", ())
    request, branch = _request(requests, lane, as_of), _branch_words(record["branch"])
    parts = {"lane": f"{lane} {'rco review' if slot in ws.RECOGNIZED_RCOS else 'build vote'}", "request": request,
             "branch": branch}
    basis = tuple(name for name, text in parts.items() if text)
    words = {word for word in TOKEN.findall(f"{request} {branch}") if not word.isdigit() and word not in LANE_PARTS}
    if len(words) < MIN_WORDS:
        return Situation("", "identifier_only", basis)
    text = " | ".join(parts[name] for name in basis)
    if _guarded(text):
        return Situation("", "guarded", basis)
    return Situation(text, "", basis)
