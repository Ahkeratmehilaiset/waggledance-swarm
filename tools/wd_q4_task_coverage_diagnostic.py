"""Per-task counts diagnostic for content memory build receipts.

Counts diagnostic, not a coverage guarantee. The content projection receipt counts accepted records and refused
rows for the whole build only, so an authorized task that contributed nothing is invisible there. This module makes
such a task visible with counts and closed reason codes alone: it calls the unchanged public
``build_content_projection`` once per approved task on that task's rows, so every number is exactly what a full
build counts for that task, and once more on all rows to show the per-task sums equal the full build (``match``).
Nothing here reads a live source, writes a file, or touches a producer, policy, trust check or runtime path.

The report never carries event text, excerpts, event ids, source refs, offsets or timestamps, only task ids from the
pinned policy, integer counts and the closed reason tokens of the projection (``DROP_TOKENS`` and ``ROW_TOKENS``).
Rows the projection excludes silently (reviewer-authored or reviewer-addressed rows, review-signal typed rows) stay
uncounted here too, exactly as in the receipt, because the public build never counts them. Rows whose task is not in
the pinned policy are summed into one ``unapproved_rows`` bucket (the build counts them as ``task_not_approved``).

``coverage_from_projection`` gives the record count per approved task from an already built projection alone (no
source rows needed), for receipts that exist today.
"""

import re

from sandbox.mind.step2 import shared_content_projection as cp

SCHEMA = "wd.q4-task-counts-diagnostic.v1"
PROJECTION_SCHEMA = "wd.q4-task-counts-from-projection.v1"
LABEL = "counts diagnostic, not a coverage guarantee"
MAX_TASKS = 4096
MAX_RECORDS = 1_000_000
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class CoverageRefused(Exception):
    """A closed refusal token; nothing partial is returned."""


def _refuse(token):
    raise CoverageRefused(token)


def _counts(projection):
    accepted, refused = projection.get("accepted"), projection.get("refused")
    if type(accepted) is not int or accepted < 0 or type(refused) is not dict:
        _refuse("projection_refused")
    out = {}
    for token, count in refused.items():
        if type(token) is not str or token not in cp.DROP_TOKENS | cp.ROW_TOKENS or type(count) is not int or count < 1:
            _refuse("projection_refused")
        out[token] = count
    return {"accepted": accepted, "refused": dict(sorted(out.items()))}


def _build(rows, policy_bytes, policy_sha256, roster):
    if not rows:
        return {"accepted": 0, "refused": {}}
    try:
        projection = cp.build_content_projection(enabled=True, rows=rows, policy_bytes=policy_bytes,
                                                 policy_sha256=policy_sha256, roster=roster)
    except cp.ContentRefused as exc:
        _refuse(str(exc))
    return _counts(projection)


def _add(total, part):
    total["accepted"] += part["accepted"]
    for token, count in part["refused"].items():
        total["refused"][token] = total["refused"].get(token, 0) + count


def task_coverage(*, rows=None, policy_bytes=None, policy_sha256=None, roster=None, enabled=True):
    """Counts per approved task, one unapproved bucket, totals, zero-coverage tasks and the full-build match."""
    if enabled is False:
        return {"status": "OFF"}
    if enabled is not True:
        _refuse("enabled_invalid")
    try:
        policy = cp.load_content_policy(policy_bytes, policy_sha256)
    except cp.ContentRefused:
        _refuse("policy_refused")
    if type(rows) is not dict:
        _refuse("rows_refused")
    for ref, event in rows.items():
        if type(ref) is not str or _HEX64.fullmatch(ref) is None or type(event) is not dict:
            _refuse("rows_refused")
    approved = list(policy["approved_tasks"])
    if len(approved) > MAX_TASKS:
        _refuse("policy_refused")
    buckets = {task: {} for task in approved}
    other = {}
    for ref, event in rows.items():
        task = event.get("task_id")
        target = buckets[task] if type(task) is str and task in buckets else other
        target[ref] = event
    tasks = {task: _build(buckets[task], policy_bytes, policy_sha256, roster) for task in sorted(buckets)}
    unapproved = _build(other, policy_bytes, policy_sha256, roster)
    totals = {"accepted": 0, "refused": {}}
    for part in list(tasks.values()) + [unapproved]:
        _add(totals, part)
    totals["refused"] = dict(sorted(totals["refused"].items()))
    full = _build(rows, policy_bytes, policy_sha256, roster)
    return {"schema": SCHEMA, "label": LABEL, "policy_sha256": policy_sha256, "tasks": tasks,
            "unapproved_rows": unapproved,
            "zero_coverage_tasks": sorted(task for task, entry in tasks.items() if entry["accepted"] == 0),
            "totals": totals, "match": "MATCH" if totals == full else "MISMATCH"}


def coverage_from_projection(projection, *, approved_tasks):
    """Record count per approved task from a built projection (or its ``messages`` wrapper); any record whose task
    is outside ``approved_tasks`` refuses, so the report can never name a task the policy did not approve."""
    if type(approved_tasks) not in (list, tuple) or not approved_tasks or len(approved_tasks) > MAX_TASKS:
        _refuse("tasks_refused")
    if any(type(task) is not str or not task for task in approved_tasks) or len(set(approved_tasks)) != len(approved_tasks):
        _refuse("tasks_refused")
    if type(projection) is dict and "records" not in projection and type(projection.get("messages")) is dict:
        projection = projection["messages"]
    if type(projection) is not dict or type(projection.get("records")) is not list:
        _refuse("projection_refused")
    records = projection["records"]
    if len(records) > MAX_RECORDS:
        _refuse("projection_refused")
    declared = projection.get("accepted")
    if declared is not None and (type(declared) is not int or declared != len(records)):
        _refuse("projection_refused")
    per_task = {task: 0 for task in approved_tasks}
    for record in records:
        meta = record.get("meta") if type(record) is dict else None
        task = meta.get("task_id") if type(meta) is dict else None
        if type(task) is not str or task not in per_task:
            _refuse("projection_refused")
        per_task[task] += 1
    per_task = dict(sorted(per_task.items()))
    return {"schema": PROJECTION_SCHEMA, "label": LABEL, "records_per_task": per_task,
            "zero_coverage_tasks": sorted(task for task, count in per_task.items() if count == 0),
            "accepted": len(records)}
