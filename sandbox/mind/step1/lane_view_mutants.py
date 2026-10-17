"""In-memory negative controls of hex-Mind step 1a. Every function returns a changed deep copy; files are never touched.

A case is ``{"snapshot", "github", "bodies", "as_of_utc"}`` (see ``lane_view_frozen``). The mutants stay internally
consistent the way the producers would have written them:

* a changed event gets its own new content ref, and the snapshot rows that named the old ref name the new one (a
  vote row moves between ``votes`` and ``stale_votes`` by the core's rule: current only at the snapshot head);
* a changed raw GitHub list gets its new digest, written into the derived file that links it, so the change is
  tested by the view's rules and not refused as an unlinked capture.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Iterable

import lane_view as lv
from lane_view import event_id


def clone(case: dict[str, Any]) -> dict[str, Any]:
    """A copy whose snapshot and GitHub objects are deep copies; the large body store is copied as a mapping."""
    return {**case, "snapshot": copy.deepcopy(case["snapshot"]), "github": copy.deepcopy(case["github"]),
            "bodies": dict(case["bodies"])}


def relink(github: dict[str, Any]) -> None:
    """New digests of both raw lists, written where the derived files link them."""
    github["pr_list_sha256"] = github["context"]["capture_sha256"] = \
        hashlib.sha256(lv.canonical(github["pr_list"]).encode("utf-8")).hexdigest()
    github["open_status_sha256"] = github["status"]["list_sha256"] = \
        hashlib.sha256(lv.canonical(github["open_status"]).encode("utf-8")).hexdigest()


def check_result(case: dict[str, Any], number: int, name: str, result: str = "FAILURE") -> dict[str, Any]:
    """(a) One required check of the PR is ``result`` at its head, in the raw rollup and the derived file alike."""
    case = clone(case)
    github = case["github"]
    raw = next(row for row in github["open_status"] if row.get("number") == number)
    for check in raw["statusCheckRollup"]:
        if (check.get("name") or check.get("context")) == name:
            check["conclusion" if "conclusion" in check else "state"] = result
    derived = github["status"]["prs"][str(number)]
    derived["required_results"][name] = [result]
    derived["all_required_success"] = False
    relink(github)
    return case


def rebind_events(case: dict[str, Any], number: int, refs: Iterable[str], *, task: str | None = None,
                  head: str | None = None) -> tuple[dict[str, Any], dict[str, str]]:
    """(b), (c) The events of ``refs`` moved to another task (every task field) or bound to another head (every head
    key). Returns the case and the map old ref -> new ref."""
    case = clone(case)
    renamed = {}
    for old in refs:
        event = copy.deepcopy(case["bodies"][old])
        payload = event["payload"]
        if task is not None:
            event["task_id"] = task
            for key in lv.TASK_KEYS:
                if key in payload:
                    payload[key] = task
        if head is not None:
            for key in lv.HEAD_KEYS:
                if key in payload:
                    payload[key] = head
        renamed[old] = event_id(event)
        case["bodies"][renamed[old]] = event
    pr = case["snapshot"]["prs"][str(number)]
    rows = pr["votes"] + pr["stale_votes"]
    for row in rows:
        if any(ref in renamed for ref in row["source_refs"]):
            row["source_refs"] = sorted(renamed.get(ref, ref) for ref in row["source_refs"])
            row["head"] = head or row["head"]
    pr["source_refs"] = sorted({renamed.get(ref, ref) for ref in pr["source_refs"]})
    pr["votes"] = [row for row in rows if row["head"] == pr["head"]]
    pr["stale_votes"] = [row for row in rows if row["head"] != pr["head"]]
    return case, renamed


def add_vote(case: dict[str, Any], number: int, *, agent: str, agent_uuid: str, status: str, head: str, task: str,
             ts_utc: str) -> tuple[dict[str, Any], str]:
    """(d) One more declared ``decision`` vote, as the core would fold it (current only at the snapshot head)."""
    case = clone(case)
    event = {"ts_utc": ts_utc, "agent": agent, "agent_uuid": agent_uuid, "type": "decision", "status": status,
             "task_id": task, "payload": {"pr": number, "head": head}}
    ref = event_id(event)
    case["bodies"][ref] = event
    pr = case["snapshot"]["prs"][str(number)]
    row = {"agent": agent, "agent_uuid": agent_uuid, "head": head, "status": status, "source_refs": [ref],
           "verification": "declared"}
    (pr["votes"] if head == pr["head"] else pr["stale_votes"]).append(row)
    pr["source_refs"] = sorted(set(pr["source_refs"]) | {ref})
    return case, ref


def drop_github(case: dict[str, Any]) -> dict[str, Any]:
    """(e) The cycle without its GitHub capture."""
    case = clone(case)
    case["github"] = None
    return case


def set_verification(case: dict[str, Any], number: int, ref: str, verification: str | None) -> dict[str, Any]:
    """(f) The snapshot vote row of ``ref`` with another ``verification`` (None removes the field); uuid unchanged."""
    case = clone(case)
    pr = case["snapshot"]["prs"][str(number)]
    for row in pr["votes"] + pr["stale_votes"]:
        if ref in row["source_refs"]:
            if verification is None:
                row.pop("verification", None)
            else:
                row["verification"] = verification
    return case
