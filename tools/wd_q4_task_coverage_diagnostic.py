"""Per-task counts diagnostic for content memory build receipts.

Counts diagnostic, not a coverage guarantee; a restricted receipt artifact, not a public card. The content projection
receipt counts accepted records and refused rows for the whole build only, so an authorized task that contributed
nothing is invisible there. This module makes such a task visible with counts and closed reason codes alone: it calls
the unchanged public ``build_content_projection`` once per approved task on that task's rows, so every number is
exactly what a full build counts for that task, and once more on all rows to show the per-task sums equal the full
build (``match``). Nothing here reads a live source, writes a file, or touches a producer, policy, trust check or
runtime path.

Disclosure rules. The report never carries event text, excerpts, event ids, source refs, offsets or timestamps, only
task ids taken from the hash-pinned policy (never from caller input), integer counts and closed reason tokens. Per
task, the projection's row tokens (lane, type, task, removal, no message) stay explicit, while every content drop
class (credential, identifier, personal, sealed, encoding and the rest of ``DROP_TOKENS``) is one generic
``content_refused`` count, so the existence of a sensitive class is never disclosed per task. A refusal token the
projection emits that this module does not know is one ``unsupported`` count, and any unsupported presence forces
``match`` to ``UNSUPPORTED``, never ``MATCH``. Rows the projection excludes silently (reviewer-authored or
reviewer-addressed rows, review-signal typed rows) stay uncounted here too, because the public build never counts
them. Rows whose task is not in the pinned policy are summed into one ``unapproved_rows`` bucket.

``coverage_from_projection`` gives the record count per approved task from an already built projection, for receipts
that exist today. Its task ids come from the hash-pinned policy, the projection must carry the projection schema,
exactly the projection keys, the same policy pin and consistent counts, a record of a task outside the policy or of a
review lane refuses the whole report, and ``source_integrity`` is ``UNKNOWN`` unless the caller also passes the
pinned rows and roster and ``validate_content_projection`` re-derives the projection exactly (``VERIFIED``).
"""

import re

from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import shared_projection as sp

SCHEMA = "wd.q4-task-counts-diagnostic.v2"
PROJECTION_SCHEMA = "wd.q4-task-counts-from-projection.v2"
LABEL = "counts diagnostic, not a coverage guarantee"
AUDIENCE = "restricted: build receipt for operator and lead, not a public card"
CONTENT_REFUSED = "content_refused"
UNSUPPORTED = "unsupported"
MAX_TASKS = 4096
MAX_RECORDS = 1_000_000
PROJECTION_KEYS = frozenset({"schema", "authority", "scope", "policy_sha256", "text_encoding", "accepted", "refused",
                             "records", "authorship", "review_signal_excluded"})
OPTIONAL_PROJECTION_KEYS = frozenset({"identifier_policy_hmac"})
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class CoverageRefused(Exception):
    """A closed refusal token; nothing partial is returned."""


def _refuse(token):
    raise CoverageRefused(token)


def _aggregate(refused):
    """Closed aggregation of a projection's refused mapping; returns (counts, unsupported_seen)."""
    if type(refused) is not dict:
        _refuse("projection_refused")
    out, unsupported = {}, False
    for token, count in refused.items():
        if type(token) is not str or type(count) is not int or count < 1:
            _refuse("projection_refused")
        if token in cp.ROW_TOKENS:
            key = token
        elif token in cp.DROP_TOKENS:
            key = CONTENT_REFUSED
        else:
            key, unsupported = UNSUPPORTED, True
        out[key] = out.get(key, 0) + count
    return dict(sorted(out.items())), unsupported


def _counts(projection):
    accepted = projection.get("accepted") if type(projection) is dict else None
    if type(accepted) is not int or accepted < 0:
        _refuse("projection_refused")
    refused, unsupported = _aggregate(projection.get("refused"))
    return {"accepted": accepted, "refused": refused}, unsupported


def _build(rows, policy_bytes, policy_sha256, roster):
    if not rows:
        return {"accepted": 0, "refused": {}}, False
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


def _policy(policy_bytes, policy_sha256):
    try:
        policy = cp.load_content_policy(policy_bytes, policy_sha256)
    except cp.ContentRefused:
        _refuse("policy_refused")
    approved = list(policy["approved_tasks"])
    if len(approved) > MAX_TASKS:
        _refuse("policy_refused")
    return approved


def _roster(roster):
    try:
        pinned = sp._roster(roster)
    except sp.ProjectionRefused:
        pinned = None
    if pinned is None:
        _refuse("roster_refused")


def _rows(rows):
    if type(rows) is not dict:
        _refuse("rows_refused")
    for ref, event in rows.items():
        if type(ref) is not str or _HEX64.fullmatch(ref) is None or type(event) is not dict:
            _refuse("rows_refused")


def task_coverage(*, rows=None, policy_bytes=None, policy_sha256=None, roster=None, enabled=True):
    """Counts per approved task, one unapproved bucket, totals, zero-coverage tasks and the full-build match."""
    if enabled is False:
        return {"status": "OFF"}
    if enabled is not True:
        _refuse("enabled_invalid")
    approved = _policy(policy_bytes, policy_sha256)
    _roster(roster)
    _rows(rows)
    buckets = {task: {} for task in approved}
    other = {}
    for ref, event in rows.items():
        task = event.get("task_id")
        target = buckets[task] if type(task) is str and task in buckets else other
        target[ref] = event
    unsupported = False
    tasks = {}
    for task in sorted(buckets):
        tasks[task], seen = _build(buckets[task], policy_bytes, policy_sha256, roster)
        unsupported = unsupported or seen
    unapproved, seen = _build(other, policy_bytes, policy_sha256, roster)
    unsupported = unsupported or seen
    totals = {"accepted": 0, "refused": {}}
    for part in list(tasks.values()) + [unapproved]:
        _add(totals, part)
    totals["refused"] = dict(sorted(totals["refused"].items()))
    full, seen = _build(rows, policy_bytes, policy_sha256, roster)
    unsupported = unsupported or seen
    if unsupported:
        match = "UNSUPPORTED"
    else:
        match = "MATCH" if totals == full else "MISMATCH"
    return {"schema": SCHEMA, "label": LABEL, "audience": AUDIENCE, "policy_sha256": policy_sha256, "tasks": tasks,
            "unapproved_rows": unapproved,
            "zero_coverage_tasks": sorted(task for task, entry in tasks.items() if entry["accepted"] == 0),
            "totals": totals, "unsupported_refusals": unsupported, "match": match}


def coverage_from_projection(projection, *, policy_bytes=None, policy_sha256=None, rows=None, roster=None):
    """Record count per approved task of the hash-pinned policy from a built projection (or its ``messages``
    wrapper). The projection is UNKNOWN unless ``rows`` and ``roster`` re-derive it exactly (then VERIFIED); any
    record whose task is outside the policy or whose lane is a review lane refuses, and no caller label is echoed."""
    approved = _policy(policy_bytes, policy_sha256)
    if type(projection) is dict and "records" not in projection and type(projection.get("messages")) is dict:
        projection = projection["messages"]
    if type(projection) is not dict or not PROJECTION_KEYS <= set(projection) <= PROJECTION_KEYS | OPTIONAL_PROJECTION_KEYS:
        _refuse("projection_refused")
    if projection["schema"] != cp.SCHEMA or projection["policy_sha256"] != policy_sha256:
        _refuse("projection_refused")
    if projection["review_signal_excluded"] is not True or type(projection["records"]) is not list:
        _refuse("projection_refused")
    records = projection["records"]
    if len(records) > MAX_RECORDS:
        _refuse("projection_refused")
    counts, unsupported = _counts(projection)
    if counts["accepted"] != len(records):
        _refuse("projection_refused")
    per_task = {task: 0 for task in approved}
    for record in records:
        meta = record.get("meta") if type(record) is dict else None
        task = meta.get("task_id") if type(meta) is dict else None
        agent = meta.get("agent") if type(meta) is dict else None
        if type(task) is not str or task not in per_task or type(agent) is not str or agent in sp.REVIEW_LANES:
            _refuse("projection_refused")
        per_task[task] += 1
    if rows is None and roster is None:
        integrity = "UNKNOWN"
    else:
        if rows is None or roster is None:
            _refuse("projection_refused")
        _roster(roster)
        _rows(rows)
        try:
            cp.validate_content_projection(projection, rows=rows, policy_bytes=policy_bytes,
                                           policy_sha256=policy_sha256, roster=roster)
        except cp.ContentRefused:
            _refuse("projection_refused")
        integrity = "VERIFIED"
    per_task = dict(sorted(per_task.items()))
    return {"schema": PROJECTION_SCHEMA, "label": LABEL, "audience": AUDIENCE, "policy_sha256": policy_sha256,
            "source_integrity": integrity, "records_per_task": per_task,
            "zero_coverage_tasks": sorted(task for task, count in per_task.items() if count == 0),
            "accepted": len(records), "refused": counts["refused"], "unsupported_refusals": unsupported}
