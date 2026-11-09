"""Per-task counts diagnostic for content memory build receipts.

Counts diagnostic, not a coverage guarantee; a receipt artifact for the build audience, not a public card. The content
projection receipt counts accepted records and refused rows for the whole build only, so an authorized task that
contributed nothing is invisible there. This module makes such a task visible with counts and closed reason codes
alone: it calls the unchanged public ``build_content_projection`` once per approved task on that task's rows, so every
number is exactly what a full build counts for that task, and once more on all rows to show the per-task sums equal
the full build (``match``). Nothing here reads a live source, writes a file, or touches a producer, policy, trust
check or runtime path.

Disclosure rules. The report never carries event text, excerpts, event ids, source refs, offsets or timestamps, only
task ids, integer counts and closed reason tokens. The task ids come from the caller-supplied policy bytes that match
the caller-supplied sha256 (a pin, not an authenticated authority), never from a free-form task list. Per task, the
projection's row tokens (lane, type, task, removal, no message) stay explicit, while every content drop class
(credential, identifier, personal, sealed, encoding and the rest of ``DROP_TOKENS``) is one generic
``content_refused`` count, so the existence of a sensitive class is never disclosed per task. A refusal token the
projection emits that this module does not know is one ``unsupported`` count, and any unsupported presence forces
``match`` to ``UNSUPPORTED``, never ``MATCH``. Rows the projection excludes silently (reviewer-authored or
reviewer-addressed rows, review-signal typed rows) stay uncounted here too, because the public build never counts
them. Rows whose task is not in the pinned policy are summed into one ``unapproved_rows`` bucket.

Trust. Every input here is caller-supplied: rows, roster, policy bytes and projection. The module can therefore prove
re-derivation only (``rederivation`` MATCH means the projection is exactly what the pinned rows, roster, policy and
identifier-policy options rebuild), never source authenticity or freshness, which the report states as UNKNOWN. The
``audience`` and ``access_control`` fields are labels: a label is not access control, and the caller must restrict
distribution. ``coverage_from_projection`` refuses an unrederived projection (it may be fabricated and may carry
review-lane records) unless the caller passes ``allow_untrusted=True``; the report is then marked ``trust``
``untrusted``, labelled untrusted and ``distribution`` ``do_not_publish``. Identifier-policy options
(``require_identifier_policy``, ``identifier_policy_binding``) are validated like the projection validates them and
passed into every per-task build and into the re-derivation; a projection carrying the identifier hmac without a
binding, or a binding for a projection built without one, refuses explicitly. In the untrusted branch the binding
tag is compared to the projection tag in constant time and reported as ``bound_unverified`` (the projection itself
was not re-derived); only the re-derived branch reports ``bound``.

Exception hygiene. A binding carries key bytes. Every refusal path clears the binding from its frame locals before
raising and raises outside any exception handler, so neither the traceback frames nor an exception context chain of a
``CoverageRefused`` can reach the binding (the pattern of ``content_identifier_policy.load_policy``).
"""

import hmac
import re

from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import shared_projection as sp

SCHEMA = "wd.q4-task-counts-diagnostic.v3"
PROJECTION_SCHEMA = "wd.q4-task-counts-from-projection.v3"
LABEL = "counts diagnostic, not a coverage guarantee"
LABEL_UNTRUSTED = "untrusted counts diagnostic from an unrederived projection; not a coverage guarantee; do not publish"
AUDIENCE = "build receipt for operator and lead, not a public card"
ACCESS_CONTROL = "none; this label is not access control, the caller must restrict distribution"
CONTENT_REFUSED = "content_refused"
UNSUPPORTED = "unsupported"
UNKNOWN = "UNKNOWN"
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


def _build(rows, policy_bytes, policy_sha256, roster, options):
    """One public build; a build refusal is re-raised outside the handler with the options cleared first."""
    if not rows:
        return {"accepted": 0, "refused": {}}, False
    projection = token = None
    try:
        try:
            projection = cp.build_content_projection(enabled=True, rows=rows, policy_bytes=policy_bytes,
                                                     policy_sha256=policy_sha256, roster=roster, **options)
        except cp.ContentRefused as exc:
            token = str(exc)
    finally:
        options = None
    if token is not None:
        _refuse(token)
    return _counts(projection)


def _add(total, part):
    total["accepted"] += part["accepted"]
    for token, count in part["refused"].items():
        total["refused"][token] = total["refused"].get(token, 0) + count


def _identifier_options(require_identifier_policy, identifier_policy_binding):
    """The identifier-policy options exactly as the projection validates them, or identifier_policy_refused; the
    binding is cleared from this frame before any refusal is raised, and the refusal is raised outside the handler."""
    options = None
    try:
        if type(require_identifier_policy) is not bool:
            pass
        elif require_identifier_policy:
            binding = identifier_policy_binding
            if (type(binding) is tuple and len(binding) == 3 and type(binding[0]) is bytes
                    and type(binding[1]) is bytes and type(binding[2]) is str and _HEX64.fullmatch(binding[2]) is not None):
                loaded = False
                try:
                    from sandbox.mind.step2 import content_identifier_policy as ip
                    ip.load_policy(*binding)  # the bytes, key and tag must load before any build runs
                    loaded = True
                except Exception:
                    loaded = False
                if loaded:
                    options = {"require_identifier_policy": True, "identifier_policy_binding": binding}
        elif identifier_policy_binding is None:
            options = {"require_identifier_policy": False, "identifier_policy_binding": None}
    finally:
        binding = identifier_policy_binding = None
    if options is None:
        _refuse("identifier_policy_refused")
    return options


def _policy(policy_bytes, policy_sha256):
    policy = None
    try:
        policy = cp.load_content_policy(policy_bytes, policy_sha256)
    except cp.ContentRefused:
        policy = None
    if policy is None:
        _refuse("policy_refused")  # raised outside the handler: no context chain into the policy loader
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


def _trust_fields():
    return {"source_authenticity": UNKNOWN, "freshness": UNKNOWN, "audience": AUDIENCE,
            "access_control": ACCESS_CONTROL}


def task_coverage(*, rows=None, policy_bytes=None, policy_sha256=None, roster=None, enabled=True,
                  require_identifier_policy=False, identifier_policy_binding=None):
    """Counts per approved task, one unapproved bucket, totals, zero-coverage tasks and the full-build match."""
    options = None
    try:
        if enabled is False:
            return {"status": "OFF"}
        if enabled is not True:
            _refuse("enabled_invalid")
        options = _identifier_options(require_identifier_policy, identifier_policy_binding)
        identifier_policy_binding = None
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
            tasks[task], seen = _build(buckets[task], policy_bytes, policy_sha256, roster, options)
            unsupported = unsupported or seen
        unapproved, seen = _build(other, policy_bytes, policy_sha256, roster, options)
        unsupported = unsupported or seen
        totals = {"accepted": 0, "refused": {}}
        for part in list(tasks.values()) + [unapproved]:
            _add(totals, part)
        totals["refused"] = dict(sorted(totals["refused"].items()))
        full, seen = _build(rows, policy_bytes, policy_sha256, roster, options)
        unsupported = unsupported or seen
        if unsupported:
            match = "UNSUPPORTED"
        else:
            match = "MATCH" if totals == full else "MISMATCH"
        report = {"schema": SCHEMA, "label": LABEL, "policy_sha256": policy_sha256,
                  "identifier_policy": "bound" if require_identifier_policy else "none", "tasks": tasks,
                  "unapproved_rows": unapproved,
                  "zero_coverage_tasks": sorted(task for task, entry in tasks.items() if entry["accepted"] == 0),
                  "totals": totals, "unsupported_refusals": unsupported, "match": match}
        report.update(_trust_fields())
        return report
    finally:
        identifier_policy_binding = options = None


def coverage_from_projection(projection, *, policy_bytes=None, policy_sha256=None, rows=None, roster=None,
                             allow_untrusted=False, require_identifier_policy=False, identifier_policy_binding=None):
    """Record count per approved task of the hash-pinned policy from a built projection (or its ``messages``
    wrapper). With ``rows`` and ``roster`` the projection must re-derive exactly (``rederivation`` MATCH, ``trust``
    rederived); without them the projection is untrusted and refused unless ``allow_untrusted=True`` (then ``trust``
    untrusted, ``distribution`` do_not_publish). Any record whose task is outside the policy, or whose lane or
    recipient is a review lane, refuses; no caller label is echoed."""
    options = None
    try:
        if type(allow_untrusted) is not bool:
            _refuse("options_refused")
        options = _identifier_options(require_identifier_policy, identifier_policy_binding)
        identifier_policy_binding = None
        approved = _policy(policy_bytes, policy_sha256)
        if type(projection) is dict and "records" not in projection and type(projection.get("messages")) is dict:
            projection = projection["messages"]
        if (type(projection) is not dict
                or not PROJECTION_KEYS <= set(projection) <= PROJECTION_KEYS | OPTIONAL_PROJECTION_KEYS):
            _refuse("projection_refused")
        if ("identifier_policy_hmac" in projection) is not require_identifier_policy:
            _refuse("identifier_policy_refused")
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
            to = meta.get("to") if type(meta) is dict else None
            if (type(task) is not str or task not in per_task or type(agent) is not str or agent in sp.REVIEW_LANES
                    or (to is not None and (type(to) is not str or to in sp.REVIEW_LANES))):
                _refuse("projection_refused")
            per_task[task] += 1
        identifier_policy = "none"
        if rows is None and roster is None:
            if allow_untrusted is not True:
                _refuse("untrusted_refused")
            if require_identifier_policy:
                tag = projection["identifier_policy_hmac"]
                if type(tag) is not str or not hmac.compare_digest(tag, options["identifier_policy_binding"][2]):
                    _refuse("identifier_policy_refused")
                identifier_policy = "bound_unverified"  # tag equal, projection not re-derived
            trust, rederivation, label, distribution = "untrusted", "NONE", LABEL_UNTRUSTED, "do_not_publish"
        else:
            if rows is None or roster is None:
                _refuse("projection_refused")
            _roster(roster)
            _rows(rows)
            token = None
            try:
                try:
                    cp.validate_content_projection(projection, rows=rows, policy_bytes=policy_bytes,
                                                   policy_sha256=policy_sha256, roster=roster, **options)
                except cp.ContentRefused:
                    token = "projection_refused"
            finally:
                options = None
            if token is not None:
                _refuse(token)
            if require_identifier_policy:
                identifier_policy = "bound"
            trust, rederivation, label, distribution = "rederived", "MATCH", LABEL, "receipt_only"
        per_task = dict(sorted(per_task.items()))
        report = {"schema": PROJECTION_SCHEMA, "label": label, "policy_sha256": policy_sha256, "trust": trust,
                  "rederivation": rederivation, "distribution": distribution, "identifier_policy": identifier_policy,
                  "records_per_task": per_task,
                  "zero_coverage_tasks": sorted(task for task, count in per_task.items() if count == 0),
                  "accepted": len(records), "refused": counts["refused"], "unsupported_refusals": unsupported}
        report.update(_trust_fields())
        return report
    finally:
        identifier_policy_binding = options = None
