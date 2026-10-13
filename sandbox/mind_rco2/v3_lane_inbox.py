# SPDX-License-Identifier: BUSL-1.1
"""Sandbox variant V3 (claude-rco-2): one lane's inbox, by identity and currentness. Sandbox code only.

A lane reads only what is its own: open PRs where its vote slot is empty at the GitHub head, PRs it is a declared
author of (recused), its unanswered bound asks, its own vetoes and its own claims. There is no topology.

Inputs are the frozen files of one round, all inside one ``.codex-audit`` root:

* ``--publication``: the reflected Mind publication of the round, read with the core ``read_snapshot``;
* ``--context``: the PR context of the round (``tools/bridge_mind_workspace.py`` ``read_context``);
* ``--capture``: the pinned reader capture the publication was built from (its file sha256 must be named by the
  snapshot's ``log_identity``), used for bound asks, replies and declared authors. The context binds by time
  only (``read_context`` refuses one observed more than 15 minutes from the capture); its own ``capture_sha256``
  names the GitHub metadata capture, not the bridge capture.

The run is a pure function of those files: "now" is the capture's ``observed_utc``, never the wall clock. It never
reads the bridge log, calls GitHub, a provider or gate code, starts a process or writes a bridge event. Every item
carries event refs, a decoded privacy guard covers the whole output, and an unsupported fact stays UNKNOWN. The
inbox is advisory: before any vote, the canonical gh checks, veto scan and gate read decide.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import bridge_mind_workspace as workspace  # noqa: E402
from tools import wd_bridge_mind as core  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402
from waggledance.core.bridge_log_reader import parse_bridge_json_object  # noqa: E402

VARIANT = "V3"  # the common tournament driver interface (sandbox/mind/tournament_driver.py): VARIANT and render()
SCHEMA = "rco2.sandbox.v3-lane-inbox.v1"
UNKNOWN = workspace.UNKNOWN
RCOS = workspace.RECOGNIZED_RCOS
BUILD = workspace.BUILD_SLOT
SLOTS = workspace.VOTE_SLOTS
LEASE_SECONDS = 1800  # the default claim lease; a heartbeat is not in the log, so a live lease stays UNKNOWN
OLD_REQUEST_SECONDS = 12 * 3600  # an unanswered request with no PR older than this is shown as stale, not as work
INPUT_LIMIT = 64 * 1024 * 1024
CLOSE_STATUSES = frozenset({"superseded", "cancelled", "canceled", "withdrawn", "closed", "obsolete", "retracted"})
SUPERSEDE_KEYS = ("supersedes_request_id", "replaces_request_id")
SUPERSEDE_LIST_KEYS = ("superseded_request_ids",)
_PR_IN_TASK = re.compile(r"(?:^|[^0-9a-z])pr-?([1-9][0-9]{2,5})(?:[^0-9]|$)", re.IGNORECASE)
_LANE = re.compile(r"[a-z][a-z0-9-]{1,40}\Z")


class InboxRefused(ValueError):
    """Fixed messages only; never input content."""


def _short(ref: str) -> str:
    return ref[:10]


def _read_json(path: Path) -> tuple[Any, str]:
    try:
        with open(path, "rb") as handle:
            data = handle.read(INPUT_LIMIT + 1)
    except OSError:
        raise InboxRefused("an input file cannot be read") from None
    if len(data) > INPUT_LIMIT:
        raise InboxRefused("an input file is too large")
    try:
        return parse_bridge_json_object(data.decode("utf-8")), hashlib.sha256(data).hexdigest()
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise InboxRefused("an input file is not one canonical JSON object") from None


def _inside(path: Path, root: Path) -> Path:
    if root.name != ".codex-audit" or not root.is_dir():
        raise InboxRefused("the audit root is not a .codex-audit directory")
    target = Path(os.path.abspath(path))
    if os.path.normcase(str(target.resolve(strict=False))) != os.path.normcase(str(target)):
        raise InboxRefused("a path goes through a link")
    if root.resolve(strict=True) not in target.parents:
        raise InboxRefused("a path is not inside the audit root")
    return target


def _to(event: Mapping[str, Any]) -> set[str]:
    return {part.strip() for part in str(event.get("to") or "").split(",") if part.strip()}


def _payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _pr_of(event: Mapping[str, Any]) -> int | None:
    pr = _payload(event).get("pr")
    return pr if type(pr) is int and pr > 0 else None


class Capture:
    """The rows of one capture with their canonical refs, and the request/answer facts V3 needs."""

    def __init__(self, events: list[Mapping[str, Any]]):
        self.rows = [(event_id(event), event) for event in events]
        self.ts = {ref: str(event.get("ts_utc") or "") for ref, event in self.rows}
        self.position = {ref: index for index, (ref, _) in enumerate(self.rows)}
        self.first_utc = self.rows[0][1].get("ts_utc", UNKNOWN) if self.rows else UNKNOWN

    def declared_authors(self) -> dict[int, dict[str, Any]]:
        """Per PR and head, the latest declared ``source_authors`` of a request payload (declared, not authenticated)."""
        out: dict[int, dict[str, Any]] = {}
        for ref, event in self.rows:
            authors, pr = _payload(event).get("source_authors"), _pr_of(event)
            if pr is None or not isinstance(authors, list) or not all(isinstance(a, str) and _LANE.match(a) for a in authors):
                continue
            head = _payload(event).get("head")
            out.setdefault(pr, {})[head if isinstance(head, str) else UNKNOWN] = {"authors": sorted(set(authors)), "ref": ref}
        return out

    def recusals(self) -> dict[int, dict[str, dict[str, Any]]]:
        """Per PR, each recognized RCO's latest typed ``rco_recused`` event (authorship does not end at a new head)."""
        out: dict[int, dict[str, dict[str, Any]]] = {}
        for ref, event in self.rows:
            pr, agent = _pr_of(event), event.get("agent")
            if pr is not None and agent in RCOS and str(event.get("status") or "").lower() == "rco_recused":
                head = _payload(event).get("head")
                out.setdefault(pr, {})[agent] = {"head": head if isinstance(head, str) else UNKNOWN, "ref": ref}
        return out

    def released_after(self, task: str, lane: str, ref: str) -> str | None:
        """The ref of a release of the task (by anyone, the stale-lease sweep included) or the lane's own ``done`` that
        follows the claim event in log position or in time; None when there is none.

        Both orders count because the log is not time-ordered: a claim can be appended after a ``done`` that is
        stamped later than it (seen 10-13 on rco2-pr1893-review). The claim is the lane's latest one by position."""
        start, claimed = self.position.get(ref), self.ts.get(ref, "")
        if start is None:
            return None
        for index, (other, event) in enumerate(self.rows):
            if index == start or event.get("task_id") != task:
                continue
            if event.get("type") == "release" or (event.get("type") == "done" and event.get("agent") == lane):
                if index > start or (claimed and str(event.get("ts_utc") or "") > claimed):
                    return other
        return None

    def asks(self, lane: str) -> list[dict[str, Any]]:
        """Bound asks the lane must answer, with how each one ended (or that it has not).

        A request is the lane's to answer when the lane is its first or only addressee or the request id names the
        lane; the other addressees are copied, and an answer by the first addressee ends it for them."""
        asks: dict[str, dict[str, Any]] = {}
        answered: dict[str, str] = {}
        closed: dict[str, str] = {}
        for ref, event in self.rows:
            rid, bound = event.get("request_id"), event.get("in_reply_to_request_id")
            payload = _payload(event)
            to = [part.strip() for part in str(event.get("to") or "").split(",") if part.strip()]
            mine = bool(to) and (to[0] == lane or (isinstance(rid, str) and lane in rid))
            if isinstance(rid, str) and rid and not bound and lane in to and mine and event.get("agent") != lane:
                expects = isinstance(payload.get("result_contract"), Mapping) or payload.get("requires_reply") is True \
                    or str(event.get("status") or "").lower() in ("requested", "review_requested")
                if expects:
                    asks[rid] = {"request_id": rid, "from": str(event.get("agent")), "task": str(event.get("task_id")),
                                     "pr": _pr_of(event), "head": payload.get("head") if isinstance(payload.get("head"), str) else None,
                                     "ts": self.ts[ref], "ref": ref}
            for key in SUPERSEDE_KEYS:
                if isinstance(payload.get(key), str):
                    closed.setdefault(payload[key], ref)
            for key in SUPERSEDE_LIST_KEYS:
                if isinstance(payload.get(key), list):
                    for old in payload[key]:
                        if isinstance(old, str):
                            closed.setdefault(old, ref)
            if isinstance(bound, str) and bound:
                if event.get("agent") == lane:
                    answered.setdefault(bound, ref)
                elif str(event.get("status") or "").lower() in CLOSE_STATUSES:
                    closed.setdefault(bound, ref)
        out = []
        for rid, item in asks.items():
            if rid in answered:
                continue
            item = dict(item)
            if rid in closed:
                item["ended"] = "closed_or_superseded"
                item["end_ref"] = closed[rid]
            out.append(item)
        return sorted(out, key=lambda item: (item["ts"], item["request_id"]))


def _class(path_class: Mapping[str, Any] | None, charter_ok: bool) -> str:
    """The computed route class from the frozen context oracle (never a gate import)."""
    if path_class is None or not charter_ok:
        return UNKNOWN
    if path_class["denylist_hits"] or path_class["charter_allowed"] is not True:
        return "off_allowlist (both RCOs plus an explicit operator sign)"
    if path_class["a_hits"]:
        return "a_class (explicit operator sign while #1762 is open)"
    return "allowlist (both eligible RCOs under rule 12)"


def _age_seconds(now: str, then: str) -> float | None:
    a, b = workspace.parse_time(now), workspace.parse_time(then)
    return None if a is None or b is None else (a - b).total_seconds()


def build_inbox(snapshot: Mapping[str, Any], context: Mapping[str, Any], capture: Capture, lane: str, now: str) -> dict[str, Any]:
    if not _LANE.match(lane):
        raise InboxRefused("the lane id is not lane-shaped")
    declared = capture.declared_authors()
    recusals = capture.recusals()
    open_prs = {number: row for number, row in context["prs"].items() if row["state"] == "OPEN"}
    states = {number: row["state"] for number, row in context["prs"].items()}
    asks = capture.asks(lane)
    pending = [r for r in asks if "ended" not in r]
    branch_pr: dict[str, int] = {}
    for number, row in sorted(context["prs"].items(), key=lambda kv: (kv[1]["state"] == "OPEN", kv[0])):
        branch_pr[row["branch"]] = number  # an open PR on the branch wins over a closed one
    for request in pending:
        if request["pr"] is None and request["task"] in branch_pr:  # the canonical task is the branch name
            request["pr"] = branch_pr[request["task"]]
    pending_by_pr: dict[int, list[dict[str, Any]]] = {}
    for request in pending:
        if request["pr"] is not None:
            pending_by_pr.setdefault(request["pr"], []).append(request)

    act, wait, own, stale, notes, dormant = [], [], [], [], [], []
    numbers = sorted({int(key) for key in snapshot["prs"]} | set(pending_by_pr))
    for number in numbers:
        mind = snapshot["prs"].get(str(number)) or {}
        ctx = open_prs.get(number)
        mine = pending_by_pr.get(number, [])
        if ctx is None:
            for request in mine:  # a request for a PR that is not open is stale for the lane, never an action
                stale.append({"kind": "request_for_pr_not_open", "pr": number, "state": states.get(number, UNKNOWN),
                              "request_id": request["request_id"], "refs": [request["ref"]]})
            continue
        gh_head = ctx["head"]
        for request in [r for r in mine if r["head"] and r["head"] != gh_head]:
            # A head-exact request for a head GitHub does not show is stale: the requester asks again at the real head.
            stale.append({"kind": "request_for_other_head", "pr": number, "request_head": request["head"],
                          "github_head": gh_head, "request_id": request["request_id"], "refs": [request["ref"]]})
        mine = [r for r in mine if not r["head"] or r["head"] == gh_head]
        drift = bool(mind) and mind.get("head") != gh_head
        votes = [v for v in list(mind.get("votes", [])) + list(mind.get("stale_votes", [])) if v.get("head") == gh_head]
        filled = {workspace.vote_slot(v.get("agent", ""), v.get("status", "")) for v in votes} - {None}
        commit_authors = set(ctx["authors"] or [])
        by_head = declared.get(number, {})
        source = by_head.get(gh_head)
        source_note = "declared_source_authors@head"
        if source is None and by_head:  # Source authorship survives a new head; say that it was declared earlier
            source, source_note = list(by_head.values())[-1], "declared_source_authors@other_head"
        declared_authors = set(source["authors"]) if source else set()
        typed = recusals.get(number, {})
        authors = commit_authors | declared_authors | set(typed)
        author_basis = "+".join(([("commit_authors")] if commit_authors else []) + ([source_note] if declared_authors else [])
                                + (["rco_recused_event"] if typed else []))
        missing = [slot for slot in SLOTS if slot not in filled and slot not in authors]
        recused = sorted(set(RCOS) & authors)
        ci = mind.get("reported_ci") or {}
        ci_green = "green_reported" if ci.get("status") == "reported_green" and ci.get("head") == gh_head else "not_reported_green"
        my_slot = lane if lane in RCOS else BUILD.get(lane)
        refs = sorted(set(mind.get("source_refs", [])) | ({source["ref"]} if source else set()) | {r["ref"] for r in mine}
                      | {t["ref"] for t in typed.values()})
        my_vetoes = [v for v in mind.get("veto_observations", []) if v.get("agent") == lane]
        my_pass_now = any(v.get("agent") == lane and v.get("status") == "rco_pass" for v in votes)
        line = {"pr": number, "github_head": gh_head, "mind_head": mind.get("head", UNKNOWN) if mind else UNKNOWN,
                "head_drift": drift, "ci": ci_green, "missing": missing, "recused_authors": recused,
                "author_basis": author_basis or UNKNOWN, "class": _class(ctx["path_class"], context["charter_ok"]),
                "asks": [r["request_id"] for r in mine], "refs": refs}
        for veto in my_vetoes:
            label = "own_veto_superseded_by_own_pass_at_head" if my_pass_now else (
                "own_veto_active_at_head" if veto.get("head") == gh_head else "own_veto_at_older_head")
            stale.append({"kind": label, "pr": number, "veto_head": veto.get("head", UNKNOWN), "github_head": gh_head,
                          "refs": sorted(set(veto.get("source_refs", [])))})
        if lane in authors:
            if mine:  # an ask to the author on its own PR (a repair, a rebase) is work, not just an own PR line
                act.append(dict(line, role="request_on_pr", rank=2))
            else:
                own.append(dict(line, role="author_recused" if lane in RCOS else "author"))
            continue
        if my_slot is None or my_slot not in missing:
            if mine and my_slot is None:
                act.append(dict(line, role="request_on_pr", rank=2))
            elif mine:  # the lane's vote is at the GitHub head but not bound to the request: never vote twice
                stale.append({"kind": "request_met_by_unbound_vote", "pr": number, "github_head": gh_head,
                              "request_id": mine[0]["request_id"], "refs": sorted({r["ref"] for r in mine})})
            continue
        last = missing == [my_slot]
        if ci_green == "green_reported":
            act.append(dict(line, role="my_slot_missing", rank=0 if last else 1, last_missing=last))
        elif mine:
            wait.append(dict(line, role="my_slot_missing_ci_not_green", rank=3, last_missing=last))
        else:  # open with my slot empty, but no request to this lane at the GitHub head and no green CI: quiet
            dormant.append(number)
            continue
        if drift:
            notes.append(f"PR{number}: Mind head {str(mind.get('head'))[:12]} differs from the GitHub head {gh_head[:12]}; the GitHub head is used")

    for request in pending:
        if request["pr"] is None:
            age = _age_seconds(now, request["ts"])
            item = {"request_id": request["request_id"], "from": request["from"], "task": request["task"], "age_s": age,
                    "refs": [request["ref"]]}
            if age is not None and age > OLD_REQUEST_SECONDS:  # shown for Q3, but it never drives the next action
                stale.append(dict(item, kind="request_unanswered_over_12h"))
            else:
                act.append(dict(item, role="unanswered_request", rank=2))

    rows = [key for key, row in snapshot["lanes"].items() if row.get("agent") == lane]
    identity = {"lane_rows": sorted(rows), "verification": sorted({snapshot["lanes"][k].get("verification", UNKNOWN) for k in rows}),
                "uncertainty": sorted({u for k in rows for u in snapshot["lanes"][k].get("uncertainty", [])})}
    if len(rows) != 1:
        notes.append(f"identity: {len(rows)} lane rows carry this agent name; one is expected")
    claims, released = [], 0
    for key in rows:
        by_task = (snapshot["lanes"][key].get("claims") or {}).get("by_task", [])
        for claim in by_task if isinstance(by_task, list) else []:
            refs = sorted(set(claim.get("source_refs", [])))
            ages = [_age_seconds(now, capture.ts.get(ref, "")) for ref in refs]
            age = min((a for a in ages if a is not None), default=None)
            task = str(claim.get("task_id", UNKNOWN))
            latest = max(refs, key=lambda ref: capture.position.get(ref, -1)) if refs else None
            release = capture.released_after(task, lane, latest) if latest else None
            if release is not None:  # the log released it after the claim; the Mind still lists it (counted only)
                released += 1
                continue
            match = _PR_IN_TASK.search(task)
            pr_state = states.get(int(match.group(1)), UNKNOWN) if match else None
            branch_state = next((row["state"] for row in context["prs"].values() if row["branch"] == task), None)
            state = branch_state or pr_state
            item = {"task": task, "claim_event_age_s": age, "lease_state": UNKNOWN, "linked_pr_state": state or UNKNOWN,
                    "refs": refs}
            claims.append(item)
            if state in ("MERGED", "CLOSED"):
                stale.append(dict(item, kind="claim_on_finished_pr"))
            elif age is not None and age > LEASE_SECONDS:
                stale.append(dict(item, kind="claim_event_older_than_lease"))
    def _open_subject(subject: str) -> bool:
        match = re.match(r"pr:([1-9][0-9]*)", subject)
        return match is None or int(match.group(1)) in open_prs

    tensions = [{"rule": t["rule"], "subject": t["subject"], "severity": t["severity"], "refs": t["refs"]}
                for t in snapshot.get("tensions", [])
                if lane in t.get("actors", []) and t["rule"] not in ("stale_vote",) and _open_subject(t["subject"])]

    act.sort(key=lambda item: (item["rank"], -(item.get("pr") or 0)))
    wait.sort(key=lambda item: (0 if item["asks"] else 1, -item["pr"]))  # asked first, then the newest PR
    if act:
        top = act[0]
        if top["role"] == "my_slot_missing":
            nxt = f"{'last missing ' if top['last_missing'] else ''}vote on PR{top['pr']} at {top['github_head'][:12]} (CI reported green); verify with gh checks, veto scan and gate read first"
        elif top["role"] == "request_with_slot_filled":
            nxt = f"answer request {top['asks'][0]} on PR{top['pr']} (my slot is already filled at the GitHub head)"
        elif top["role"] == "request_on_pr":
            nxt = f"answer request {top['asks'][0]} on PR{top['pr']}"
        else:
            nxt = f"answer request {top['request_id']} from {top['from']}"
    elif wait:
        top = wait[0]
        nxt = (f"PR{top['pr']} at {top['github_head'][:12]}: request {top['asks'][0]} is pending; review now and vote once CI is green at that head"
               if top["asks"] else "review now, vote after CI: " + ", ".join(f"PR{w['pr']}" for w in wait[:4]))
    else:
        nxt = "nothing addressed to this lane in the round"
    return {"schema": SCHEMA, "authority": "none", "advisory": "canonical gh checks, veto scan and gate read decide",
            "lane": lane, "as_of_utc": now, "capture_first_utc": capture.first_utc, "identity": identity,
            "act": act, "wait": wait, "own": own, "stale": stale, "dormant_open_prs": dormant,
            "asks_actionable": sum(1 for i in act + wait if i.get("asks") or i.get("request_id")),
            "asks_stale": sum(1 for i in stale if i["kind"].startswith("request_")),
            "asks_ended_unanswered": len(asks) - len(pending), "claims": claims,
            "claims_released_but_listed_by_mind": released, "tensions": tensions,
            "next_action": nxt, "notes": notes,
            "limits": ["capture window only: asks and votes before the first captured event are unseen",
                       "authors are declared (commit authors or request source_authors), never authenticated",
                       "CI is what the bridge reported at the GitHub head, not gh checks",
                       "claim lease heartbeats are not in the log, so a live lease stays UNKNOWN"]}


def render_text(inbox: Mapping[str, Any]) -> str:
    def refs(item: Mapping[str, Any]) -> str:
        return "[" + ",".join(_short(r) for r in item.get("refs", [])[:4]) + (",+" if len(item.get("refs", [])) > 4 else "") + "]"

    out = [f"V3 inbox {inbox['lane']} as of {inbox['as_of_utc']} (window from {inbox['capture_first_utc']}); authority none, advisory",
           f"NEXT {inbox['next_action']}"]
    for item in inbox["act"]:
        if "pr" in item:
            out.append(f"ACT  PR{item['pr']} {item['github_head'][:12]} {item['role']} ci={item['ci']} missing={'+'.join(item['missing']) or 'none'} class={item['class'].split(' ')[0]}"
                       + (f" req={','.join(item['asks'])}" if item['asks'] else "") + f" {refs(item)}")
        else:
            out.append(f"ACT  request {item['request_id']} from {item['from']} task {item['task']} {refs(item)}")
    for item in inbox["wait"]:
        out.append(f"WAIT PR{item['pr']} {item['github_head'][:12]} ci={item['ci']} missing={'+'.join(item['missing'])}"
                   + (f" req={','.join(item['asks'])}" if item['asks'] else "") + f" {refs(item)}")
    for item in inbox["own"]:
        out.append(f"OWN  PR{item['pr']} {item['github_head'][:12]} {item['role']} ci={item['ci']} missing={'+'.join(item['missing']) or 'none'} by={item['author_basis']} {refs(item)}")
    for item in inbox["stale"]:
        what = item.get("pr") and f"PR{item['pr']}" or item.get("task", UNKNOWN)
        out.append(f"OLD  {item['kind']} {what} {refs(item)}")
    for item in inbox["tensions"]:
        out.append(f"TEN  {item['rule']} {item['subject']} {item['severity']} {refs(item)}")
    quiet = inbox["dormant_open_prs"]
    if quiet:
        out.append(f"QUIET my slot empty, no request to me, CI not green: " + " ".join(f"PR{n}" for n in quiet[-12:])
                   + (f" (+{len(quiet) - 12} older)" if len(quiet) > 12 else ""))
    out.append(f"asks actionable {inbox['asks_actionable']}, stale {inbox['asks_stale']}, ended without my answer "
               f"{inbox['asks_ended_unanswered']}; claims held {len(inbox['claims'])}, released in the log but listed by the Mind "
               f"{inbox['claims_released_but_listed_by_mind']}; identity rows {len(inbox['identity']['lane_rows'])}")
    for note in inbox["notes"]:
        out.append(f"NOTE {note}")
    return "\n".join(out) + "\n"


def render(inputs: Mapping[str, Any], lane: str) -> dict[str, str]:
    """The tournament driver entry: a pure function of the frozen round inputs, one text view per lane.

    The driver passes the snapshot and the raw context but no capture rows. When ``capture_events`` is absent, the
    asks, typed recusals, declared Source authors and claim releases are UNKNOWN, and a NOTE line says so; "now" is
    then the snapshot's ``as_of_utc``."""
    snapshot = inputs["snapshot"]
    rows = inputs.get("capture_events")
    now = inputs.get("capture_observed_utc") if isinstance(rows, list) else None
    now = now if workspace.parse_time(now) is not None else snapshot["source"].get("as_of_utc")
    context, _ = workspace.read_context(inputs["context"], workspace.parse_time(now))
    if context is None:
        raise InboxRefused("the context is not usable for this round")
    inbox = build_inbox(snapshot, context, Capture(rows if isinstance(rows, list) else []), lane, now)
    if not isinstance(rows, list):
        inbox["notes"].insert(0, "no capture rows in the driver inputs: asks, typed recusals, declared Source authors "
                                 "and claim releases are UNKNOWN here")
    text = render_text(inbox)
    workspace.guard_object(inbox)
    return {"inbox.txt": text}


def run(publication: Path, context_path: Path, capture_path: Path, audit_root: Path, lanes: Iterable[str],
        out_dir: Path) -> dict[str, Any]:
    started = time.perf_counter()
    root = Path(os.path.abspath(audit_root))
    snapshot = core.read_snapshot(_inside(publication, root), audit_root=root)
    capture_value, capture_sha = _read_json(_inside(capture_path, root))
    context_value, context_sha = _read_json(_inside(context_path, root))
    if not isinstance(capture_value, dict) or not isinstance(capture_value.get("events"), list):
        raise InboxRefused("the capture is not a capture object")
    now = capture_value.get("observed_utc")
    if workspace.parse_time(now) is None:
        raise InboxRefused("the capture has no observation time")
    if snapshot["source"].get("log_identity") != f"capture-window:{capture_sha}":
        raise InboxRefused("the publication was not built from this capture")
    context, reason = workspace.read_context(context_value, workspace.parse_time(now))
    if context is None:
        raise InboxRefused("the context is not usable for this capture")
    capture = Capture(capture_value["events"])
    loaded_ms = (time.perf_counter() - started) * 1000
    target = _inside(out_dir, root)
    if target.exists():
        raise InboxRefused("the output directory already exists")
    results, files = {}, {}
    for lane in lanes:
        t0 = time.perf_counter()
        inbox = build_inbox(snapshot, context, capture, lane, now)
        text = render_text(inbox)
        workspace.guard_object(inbox)
        workspace.guard_object(text)
        files[f"inbox-{lane}.json"] = json.dumps(inbox, sort_keys=True, ensure_ascii=False, indent=1).encode("utf-8")
        files[f"inbox-{lane}.txt"] = text.encode("utf-8")
        results[lane] = {"build_ms": round((time.perf_counter() - t0) * 1000, 1), "text_bytes": len(files[f"inbox-{lane}.txt"]),
                         "json_bytes": len(files[f"inbox-{lane}.json"]), "act": len(inbox["act"]), "wait": len(inbox["wait"]),
                         "own": len(inbox["own"]), "stale": len(inbox["stale"]), "next_action": inbox["next_action"]}
    target.mkdir()
    digests = {}
    for name, data in sorted(files.items()):
        with open(target / name, "xb") as handle:
            handle.write(data)
        digests[name] = hashlib.sha256(data).hexdigest()
    receipt = {"schema": SCHEMA + ".receipt", "authority": "none", "capture_sha256": capture_sha, "context_sha256": context_sha,
               "snapshot_log_identity": snapshot["source"]["log_identity"], "as_of_utc": now, "load_ms": round(loaded_ms, 1),
               "lanes": results, "outputs": digests}
    workspace.guard_object(receipt)
    with open(target / "receipt.json", "x", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, sort_keys=True, indent=1))
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--publication", required=True, type=Path)
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--audit-root", required=True, type=Path)
    parser.add_argument("--lane", action="append", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = run(args.publication, args.context, args.capture, args.audit_root, args.lane, args.out_dir)
    except (InboxRefused, core.ProjectionError, workspace.WorkspaceError) as exc:
        sys.stderr.write(f"refused: {exc}\n")
        return 2
    print(json.dumps(receipt["lanes"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
