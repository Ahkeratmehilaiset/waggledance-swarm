"""Hex-Mind step 1a: an exact-context lane view of one frozen Mind cycle. Sandbox, advisory, never a gate.

Why (PR1905, night of 2026-10-13/14): the core moves a PR head only on a head-update status, so the bridge view
kept head 35fa474f while GitHub had 4a5cdfdd with 6/6 required checks and the Lead and RCO1 votes bound to it. The
V4 lens suppressed the card (head_mismatch) in 8 of 8 cycles and the Tools lane never saw that its build vote was
the only empty slot.

``lane_view(snapshot, github, bodies, as_of_utc=...)`` evaluates every PR the core snapshot names:

1. Head: only from the GitHub capture of the same cycle. The PR list, the PR context, the raw status list and the
   derived status file must name one head (else ``head_ambiguous``). Bridge events never move it and SHAs are never
   ordered by arrival: a late vote at an old head stays stale (counterexample E4D970BB).
2. Votes: every vote row of the snapshot (current or stale; the core's split is relative to its own, possibly
   lagging, head) is read by the workspace (``bridge_mind_workspace._Inputs``: declared only with a valid
   ``agent_uuid`` and ``verification`` ``declared``; a row whose two fields disagree is refused). Its event body,
   looked up by its content ref, must be a ``decision`` on this PR bound to exactly the GitHub head (full SHA), its
   task must be the PR branch (GitHub ``headRefName``), and its identity must fill a slot: ``build_lead``
   codex-lead-1, ``build_tools`` codex-tools-1, one slot per recognized RCO. An RCO that is a commit author of the
   PR, or that declared ``rco_recused`` at this head on this task, fills no slot and is never asked. Anything else
   is listed as not credited, never counted.
3. CI: green only when the required set is non-empty without duplicates, every required check is present exactly
   once in the rollup at that head and SUCCESS, and the derived status file says the same.
4. Abstain (no action line; an ``UNKNOWN`` reason in ``abstained``) when GitHub is missing, stale or ambiguous, or
   older than the newest bridge evidence of a different head; when the checks are not all SUCCESS; when a veto is
   observed; or when a vote row, an event body or the commit authors cannot be verified. A whole PR row that the
   workspace refuses has no record and no line; it is named in ``refused_rows``.
5. Lines: a PR with at least one credited vote and an empty slot gives each lane owning an empty slot one line, the
   only empty slot first, at most ``LINE_LIMIT`` per lane. The route is named only from declared fields of the
   credited votes; no wording ever says the PR may merge.

Pure: the caller passes the snapshot, the GitHub capture objects, the event bodies by ref and the as-of time. No
clock, file, network, process or bridge write. Only identifiers, refs, SHAs, times, counts and fixed wording reach
the output, and the whole view passes the workspace's decoded privacy guard.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

SRC = Path(__file__).resolve().parents[3]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from tools import bridge_mind_workspace as ws  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402

SCHEMA = "wd.mind-lane-view.v1a"
SCOPE = "advisory_lane_view_not_gate"
UNKNOWN = ws.UNKNOWN
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
SLOT_OWNER = {**{slot: agent for agent, slot in ws.BUILD_SLOT.items()}, **{rco: rco for rco in ws.RECOGNIZED_RCOS}}
LINE_LIMIT = 3
STATUS_SCHEMA = "wd.bridge-mind-gh-status.v1"
GITHUB_MAX_AGE = ws.CONTEXT_MAX_AGE
HEAD_KEYS = ("exact_head", "head_sha", "head")  # the core's head binding keys, all must agree
TASK_KEYS = ("task_id", "canonical_task_id", "canonical_task")  # declared task fields that must agree with task_id
VOTE_TYPE = "decision"
ROUTE_OWNER = "operator"
# The capture step's own check-name alphabet (sandbox/mind/capture/capture_round.py CHECK_NAME).
CHECK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ ()/:+-]{0,99}")
REASON_TEXT = {
    "bridge_input_blocked": "the bridge view is blocked, so it is not current",
    "github_capture_missing": "no usable GitHub capture in this cycle",
    "github_pr_missing": "the PR is not in the GitHub capture",
    "head_ambiguous": "the GitHub sources do not name one head",
    "required_set_unknown": "the required check set is missing or empty",
    "required_set_duplicate": "the required check set lists a name twice",
    "required_check_missing": "a required check is not reported at this head",
    "required_check_duplicate": "a required check is reported more than once at this head",
    "required_check_not_success": "a required check is not SUCCESS at this head",
    "required_checks_disagree": "the raw rollup and the derived status file disagree",
    "veto_observed": "a veto is observed on this PR (activity UNKNOWN)",
    "vote_row_refused": "a vote row is refused by the workspace (verification UNKNOWN)",
    "event_body_missing": "an event body named by the snapshot is not available",
    "vote_body_disagrees": "a vote row and its event body disagree",
    "recusal_unverified": "a recusal at this head cannot be verified",
    "commit_authors_unknown": "the commit authors are not known, so RCO eligibility is UNKNOWN",
    "github_older_than_bridge_head": "the GitHub capture is older than bridge evidence of a different head",
}


class LaneViewError(ValueError):
    """Fixed messages that never echo input content."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stamp(moment: datetime | None) -> str:
    """Whole UTC seconds (a fraction is cut, never rounded up), or UNKNOWN."""
    return UNKNOWN if moment is None else moment.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def bound_head(payload: Mapping[str, Any]) -> str | None:
    """The one full head SHA an event binds (the core's keys, all equal), or None."""
    values = [payload[key] for key in HEAD_KEYS if key in payload]
    if not values or any(value != values[0] for value in values):
        return None
    return values[0] if isinstance(values[0], str) and ws.HEAD.fullmatch(values[0]) else None


def body(bodies: Mapping[str, Any], ref: str, now: datetime) -> tuple[Mapping[str, Any], datetime] | None:
    """The event whose own canonical ref is ``ref`` and its time, if it is not after ``now``; else None."""
    event = bodies.get(ref)
    if not isinstance(event, Mapping) or event_id(event) != ref:
        return None
    when = ws.parse_time(event.get("ts_utc"))
    return None if when is None or when > now else (event, when)


# --- GitHub: one capture, one head per PR -------------------------------------------------------------------------

def read_github(github: Any, now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    """The cycle's GitHub capture as one usable whole, or None and the fixed reason it is not used.

    Four parts: the PR context (read by the workspace's ``read_context``), the derived status file, and the two raw
    lists they were derived from, linked by the digests the derived files carry. ``pr_list_sha256`` and
    ``open_status_sha256`` are the caller's digests of the raw bytes it parsed (the frozen loader computes them from
    the manifest-verified bytes); the derived files must name exactly those. Both observation times must be at or
    before ``now`` and within ``GITHUB_MAX_AGE`` of it. Any refusal makes every PR abstain.
    """
    if not isinstance(github, Mapping):
        return None, "github_absent"
    context, reason = ws.read_context(github.get("context"), now)
    if context is None:
        return None, reason or "context_absent"
    status = github.get("status")
    if not isinstance(status, Mapping) or status.get("schema") != STATUS_SCHEMA or status.get("authority") != "none" \
            or not isinstance(status.get("prs"), Mapping):
        return None, "status_absent_or_invalid"
    times = [ws.parse_time(context["observed"]), ws.parse_time(status.get("observed_utc"))]
    if None in times or any(moment > now or now - moment > GITHUB_MAX_AGE for moment in times):
        return None, "github_not_current"
    pr_list, open_status = github.get("pr_list"), github.get("open_status")
    if not isinstance(pr_list, list) or not isinstance(open_status, list):
        return None, "raw_lists_absent"
    if github.get("pr_list_sha256") != context["capture"] or github.get("open_status_sha256") != status.get("list_sha256"):
        return None, "raw_lists_not_linked"
    return {"context": context, "status": status, "pr_list": pr_list, "open_status": open_status,
            "context_observed": times[0], "status_observed": times[1], "observed": min(times)}, None


def required_checks(required: Any, derived: Mapping[str, Any], raw: Mapping[str, Any]
                    ) -> tuple[int | None, datetime | None, str | None, list[str]]:
    """(count, last completion, None, []) when every required check at this head is SUCCESS, else the reason.

    The required set must be non-empty without duplicates; each required name must appear exactly once in the raw
    rollup of this head and be SUCCESS; the derived file must report the same (all SUCCESS, nothing missing). A
    subset, an extra or a repeated row is never green.
    """
    if not isinstance(required, list) or not required \
            or not all(isinstance(name, str) and CHECK_NAME.fullmatch(name) for name in required):
        return None, None, "required_set_unknown", []
    if len(set(required)) != len(required):
        return None, None, "required_set_duplicate", []
    rows: dict[str, list[Mapping[str, Any]]] = {}
    rollup = raw.get("statusCheckRollup")
    for check in rollup if isinstance(rollup, list) else []:
        name = (check.get("name") or check.get("context")) if isinstance(check, Mapping) else None
        if name in required:
            rows.setdefault(name, []).append(check)
    missing = sorted(set(required) - set(rows))
    if missing:
        return None, None, "required_check_missing", missing
    repeated = sorted(name for name, found in rows.items() if len(found) > 1)
    if repeated:
        return None, None, "required_check_duplicate", repeated
    results = {name: found[0].get("conclusion") or found[0].get("state") or "PENDING" for name, found in rows.items()}
    failed = sorted(name for name, result in results.items() if result != "SUCCESS")
    if failed:
        return None, None, "required_check_not_success", failed
    said = derived.get("required_results")
    if derived.get("all_required_success") is not True or derived.get("missing_required") != [] \
            or not isinstance(said, Mapping) or set(said) != set(required) \
            or any(said[name] != ["SUCCESS"] for name in required):
        return None, None, "required_checks_disagree", []
    completed = [ws.parse_time(found[0].get("completedAt")) for found in rows.values()]
    return len(required), (None if None in completed else max(completed)), None, []


def github_pr(gh: Mapping[str, Any], number: int) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """GitHub's facts for one PR at one head: (facts, reasons, check names). A PR that is not open has no checks."""
    row = gh["context"]["prs"].get(number)
    listed = [item for item in gh["pr_list"] if isinstance(item, Mapping) and item.get("number") == number]
    if row is None or not listed:
        return None, ["github_pr_missing"], []
    if len(listed) != 1 or listed[0].get("headRefOid") != row["head"] \
            or listed[0].get("headRefName") != row["branch"] or listed[0].get("state") != row["state"]:
        return None, ["head_ambiguous"], []
    facts = {"state": row["state"], "head": row["head"], "branch": row["branch"], "authors": row["authors"]}
    if row["state"] != "OPEN":
        return facts, [], []
    derived = gh["status"]["prs"].get(str(number))
    raw = [item for item in gh["open_status"] if isinstance(item, Mapping) and item.get("number") == number]
    if not isinstance(derived, Mapping) or not raw:
        return None, ["github_pr_missing"], []
    if len(raw) != 1 or raw[0].get("headRefOid") != row["head"] or derived.get("head") != row["head"]:
        return None, ["head_ambiguous"], []
    count, completed, reason, names = required_checks(gh["status"].get("required_contexts"), derived, raw[0])
    facts.update(checks=None if count is None else f"{count}/{count}", checks_completed=completed)
    return facts, ([reason] if reason else []), names


# --- Bridge evidence of one PR ------------------------------------------------------------------------------------

def _task_ok(event: Mapping[str, Any]) -> bool:
    """Every declared task field of the payload agrees with the event's own task_id."""
    task = event.get("task_id")
    return all(_payload(event)[key] == task for key in TASK_KEYS if key in _payload(event))


def _matches_row(vote: Mapping[str, Any], event: Mapping[str, Any], number: int) -> bool:
    """The snapshot row says what its event body says: agent, status, PR, head and (when declared) a valid uuid."""
    status, uuid = event.get("status"), event.get("agent_uuid")
    return event.get("agent") == vote["agent"] and isinstance(status, str) and status.lower() == vote["status"] \
        and _payload(event).get("pr") == number and type(_payload(event).get("pr")) is int \
        and bound_head(_payload(event)) == vote["head"] \
        and (not vote["verified"] or (isinstance(uuid, str) and bool(ws.UUID.fullmatch(uuid))))


def _route(events: Sequence[Mapping[str, Any]]) -> str:
    """The merge route declared by the credited votes: an operator manual train or merge, else UNKNOWN."""
    owners = {value if isinstance(value, str) else UNKNOWN
              for value in (_payload(event)["merge_owner"] for event in events if "merge_owner" in _payload(event))}
    if owners != {ROUTE_OWNER}:
        return UNKNOWN
    train = any(isinstance(_payload(event).get("component_prs"), list) and _payload(event)["component_prs"]
                and all(type(item) is int and item > 0 for item in _payload(event)["component_prs"])
                for event in events)
    return "operator manual train" if train else "operator manual merge"


def evaluate_pr(inputs: Any, pr: Mapping[str, Any], raw: Mapping[str, Any], gh: Mapping[str, Any] | None,
                bodies: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """One PR: its GitHub head, the credited and not credited votes, the empty slots, or the abstain reasons."""
    number = pr["pr"]
    record: dict[str, Any] = {"pr": number, "bridge_view_head": pr["head"] or UNKNOWN, "reasons": []}
    if inputs.blocked is not None:
        return dict(record, state="abstain", reasons=["bridge_input_blocked"])
    if gh is None:
        return dict(record, state="abstain", reasons=["github_capture_missing"])
    facts, reasons, names = github_pr(gh, number)
    if facts is None:
        return dict(record, state="abstain", reasons=reasons)
    head, branch = facts["head"], facts["branch"]
    record.update(github_head=head, branch=branch, github_state=facts["state"])
    if facts["state"] != "OPEN":
        return dict(record, state="not_open")
    record.update(checks=facts["checks"] or UNKNOWN, failing_checks=names)
    if pr["vetoes"] or pr["veto_unreadable"]:
        reasons.append("veto_observed")
    candidates = pr["votes"] + pr["stale_votes"]
    if len(candidates) != len(raw.get("votes") or []) + len(raw.get("stale_votes") or []):
        reasons.append("vote_row_refused")
    recusals, refused = inputs._votes(raw.get("recusals") if isinstance(raw.get("recusals"), list) else [],
                                      f"pr{number}.recusals")
    if refused:
        reasons.append("vote_row_refused")
    authors = facts["authors"]
    if authors is None:
        reasons.append("commit_authors_unknown")
    # Every event the snapshot names for this PR must be available, so its head and time are known.
    seen: dict[str, tuple[Mapping[str, Any], datetime]] = {}
    for ref in sorted(set(pr["refs"]) | {ref for row in candidates + pr["vetoes"] + recusals for ref in row["refs"]}):
        found = body(bodies, ref, now)
        if found is None:
            reasons.append("event_body_missing")
            record["missing_ref"] = ref
            break
        seen[ref] = found
    newest_other = max(((when, bound_head(_payload(event))) for event, when in seen.values()
                        if _payload(event).get("pr") == number and bound_head(_payload(event)) not in (None, head)),
                       default=None)
    if newest_other is not None and newest_other[0] > gh["observed"]:
        reasons.append("github_older_than_bridge_head")
        record["newer_bridge_head"] = {"head": newest_other[1], "at_utc": stamp(newest_other[0])}
    credited, not_credited, excused = [], [], {}
    for rco in sorted(set(ws.RECOGNIZED_RCOS) & set(authors or ())):
        excused[rco] = "commit_author"
    for row in recusals:
        if row["head"] != head or row["agent"] not in ws.RECOGNIZED_RCOS or row["status"] != "rco_recused" \
                or not row["verified"]:
            continue
        events = [seen.get(ref) for ref in row["refs"]]
        if None in events or not all(event.get("task_id") == branch and _task_ok(event) for event, _ in events):
            reasons.append("recusal_unverified")
            continue
        excused.setdefault(row["agent"], "rco_recused")
    for vote in sorted(candidates, key=lambda item: (item["agent"], item["status"], item["head"], item["refs"])):
        events = [seen.get(ref) for ref in vote["refs"]]
        if None in events:
            continue  # already an abstain reason: event_body_missing
        if not all(_matches_row(vote, event, number) for event, _ in events):
            reasons.append("vote_body_disagrees")
            continue
        slot = ws.vote_slot(vote["agent"], vote["status"])
        why = ("other_head" if vote["head"] != head else
               "undeclared_identity" if not vote["verified"] else
               "not_a_decision" if any(str(event.get("type", "")).lower() != VOTE_TYPE for event, _ in events) else
               "task_fields_disagree" if not all(_task_ok(event) for event, _ in events) else
               "other_task" if any(event.get("task_id") != branch for event, _ in events) else
               "no_slot" if slot is None else
               "commit_author" if slot in ws.RECOGNIZED_RCOS and vote["agent"] in (authors or ()) else
               None)
        entry = {"agent": vote["agent"], "status": vote["status"], "head": vote["head"], "ref": vote["refs"][0],
                 "ts_utc": stamp(max(when for _, when in events))}
        if why is None:
            credited.append(dict(entry, slot=slot, events=[event for event, _ in events],
                                 at=max(when for _, when in events)))
        else:
            not_credited.append(dict(entry, reason=why))
    reasons = sorted(set(reasons), key=list(REASON_TEXT).index)
    record.update(not_credited=not_credited, excused=[{"agent": agent, "why": why} for agent, why in sorted(excused.items())])
    if reasons:
        return dict(record, state="abstain", reasons=reasons)
    filled = {vote["slot"] for vote in credited}
    empty = [slot for slot in ws.VOTE_SLOTS if slot not in filled and slot not in excused]
    times = [vote["at"] for vote in credited] + [facts["checks_completed"]]
    record.update(credited=[{key: vote[key] for key in ("slot", "agent", "head", "ref", "ts_utc")}
                            for vote in sorted(credited, key=lambda v: (ws.VOTE_SLOTS.index(v["slot"]), v["ref"]))],
                  empty_slots=empty, route=_route([event for vote in credited for event in vote["events"]]),
                  waiting_since_utc=UNKNOWN if None in times or not credited else stamp(max(times)))
    if not credited:
        return dict(record, state="no_credited_vote")
    return dict(record, state="votes_complete" if not empty else "action")


# --- Lines --------------------------------------------------------------------------------------------------------

def action_line(record: Mapping[str, Any], slot: str, lane: str, bounded: bool) -> str:
    """The one line a lane gets for its empty slot on one PR. Identifiers, refs and fixed wording only."""
    head = record["github_head"][:12]
    others = [other for other in record["empty_slots"] if other != slot]
    what = f"{'RCO' if slot in ws.RECOGNIZED_RCOS else 'build'} vote on PR{record['pr']} at {head}"
    text = (f"{what} is the only empty slot ({slot}, task {record['branch']})" if not others else
            f"{what} is empty ({slot}, task {record['branch']}); also empty: {', '.join(others)}")
    filled = ", ".join(f"{vote['slot']} {vote['ref'][:16]}" for vote in record["credited"])
    text += (f"; filled at this head: {filled}; GitHub {record['checks']} at {head}; waiting since "
             f"{record['waiting_since_utc']}; route {record['route']}; the vote is still required")
    own = [row for row in record["not_credited"] if row["agent"] == lane and row["head"] == record["github_head"]]
    if own:
        text += f"; your vote row at this head is not credited ({own[0]['reason']}, ref {own[0]['ref'][:16]})"
    return text + ("; coverage UNKNOWN" if bounded else "")


def _detail(record: Mapping[str, Any]) -> str:
    parts = [REASON_TEXT[reason] for reason in record["reasons"]]
    if record.get("failing_checks"):
        parts.append("checks: " + ", ".join(record["failing_checks"]))
    if "newer_bridge_head" in record:
        newer = record["newer_bridge_head"]
        parts.append(f"bridge evidence of {newer['head'][:12]} at {newer['at_utc']}")
    if "missing_ref" in record:
        parts.append(f"ref {record['missing_ref'][:16]}")
    head = record.get("github_head")
    return f"UNKNOWN: PR{record['pr']}" + (f" at {head[:12]}" if head else "") + ": " + "; ".join(parts)


def lane_view(snapshot: Mapping[str, Any], github: Mapping[str, Any] | None, bodies: Mapping[str, Any], *,
              as_of_utc: str) -> dict[str, Any]:
    """The lane view of one cycle. Pure and deterministic: the same inputs give the same bytes."""
    now = ws.parse_time(as_of_utc)
    if now is None:
        raise LaneViewError("an explicit as_of_utc is required")
    try:
        inputs = ws._Inputs(snapshot)  # the workspace's own allowlist and declared-identity reading
    except ws.WorkspaceError:
        raise LaneViewError("the snapshot is refused by the workspace") from None
    if inputs.now > now:
        raise LaneViewError("the snapshot is newer than as_of_utc")
    # Rows the workspace refused while reading the snapshot: a refused PR row has no record, so it is named here.
    refused = [{key: row[key] for key in ("where", "ref", "reason")} for row in inputs.refused]
    source = snapshot["source"]
    bounded = inputs.coverage != "complete" or str(source.get("log_identity", "")).startswith("capture-window:") \
        or inputs.blocked is not None
    gh, gh_reason = read_github(github, now)
    records = {str(pr["pr"]): evaluate_pr(inputs, pr, snapshot["prs"][str(pr["pr"])], gh, bodies, now)
               for pr in sorted(inputs.prs, key=lambda item: item["pr"])}
    lines: dict[str, list[tuple]] = {lane: [] for lane in LANES}
    for record in records.values():
        if record["state"] != "action":
            continue
        for slot in record["empty_slots"]:
            lane = SLOT_OWNER[slot]
            order = (0 if len(record["empty_slots"]) == 1 else 1, record["waiting_since_utc"], record["pr"])
            lines.setdefault(lane, []).append((order, action_line(record, slot, lane, bounded)))
    abstained = [{"pr": record["pr"], "reasons": record["reasons"], "detail": _detail(record)}
                 for record in records.values() if record["state"] == "abstain"]
    view = {
        "schema": SCHEMA, "authority": "none", "scope": SCOPE, "as_of_utc": stamp(now),
        "coverage": UNKNOWN if bounded else "complete",
        "snapshot": {"as_of_utc": source.get("as_of_utc"), "log_identity": source.get("log_identity"),
                     "event_count": source.get("event_count")},
        "github": ({"usable": False, "reason": gh_reason} if gh is None else
                   {"usable": True, "observed_utc": stamp(gh["observed"]),
                    "context_observed_utc": stamp(gh["context_observed"]),
                    "status_observed_utc": stamp(gh["status_observed"]),
                    "pr_list_sha256": gh["context"]["capture"], "open_status_sha256": gh["status"]["list_sha256"],
                    "required_contexts": len(gh["status"].get("required_contexts") or [])}),
        "lanes": {lane: {"actions": [text for _, text in sorted(rows)[:LINE_LIMIT]],
                         "more": max(0, len(rows) - LINE_LIMIT)} for lane, rows in sorted(lines.items())},
        "abstained": abstained,
        "refused_rows": refused,
        "prs": records,
        "counts": {state: sum(record["state"] == state for record in records.values())
                   for state in ("action", "abstain", "votes_complete", "no_credited_vote", "not_open")},
    }
    try:
        ws.guard_object(view)
    except ws.WorkspaceError:
        raise LaneViewError("a guarded word is in the output; nothing is written") from None
    return view


def render_text(view: Mapping[str, Any], lane: str) -> str:
    """A lane's few lines as plain text, guarded as a whole before it is returned."""
    entry = view["lanes"].get(lane, {"actions": [], "more": 0})
    lines = [f"lane view 1a for {lane} as of {view['as_of_utc']}; advisory, not a gate; check the canonical tools "
             "before any vote"]
    lines += [f"{index}. {text}" for index, text in enumerate(entry["actions"], 1)] or ["no action line"]
    if entry["more"]:
        lines.append(f"(+{entry['more']} more action lines in the view)")
    lines.append(f"abstained: {len(view['abstained'])} PRs without an action line, UNKNOWN reasons in the view; "
                 f"refused snapshot rows: {len(view['refused_rows'])}; coverage {view['coverage']}")
    text = "\n".join(lines) + "\n"
    if ws._has_marker(text):
        raise LaneViewError("a guarded word is in the output; nothing is written")
    return text
