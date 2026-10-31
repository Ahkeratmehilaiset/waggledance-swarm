"""HEX MEMORY STEP 2, shared projection: the default-deny record set every lane may hold in full.

The Q4 memory directory is plain files whose cell metadata carries every stored text, so whatever is projected here
is readable by every lane that can read the memory. This module therefore builds records from APPROVED bridge event
METADATA only (``APPROVED_FIELDS``): time, lane agent, a non-decision event type, a status from a closed enum, a
task id only when it is in a caller-supplied pre-approved set, and optional PR number, 40-hex head and CI value from
a closed enum. Everything else on an event (message, payload, paths, identities, nonces, digests, unknown keys) is
never read, never rendered and never persisted; it is only counted.

v2 (independent review of v1): a regex-shaped field is a free-text channel. ``status`` and ``ci`` are therefore
CLOSED ENUMS (``ALLOWED_STATUS``, ``ALLOWED_CI``): any other text maps to ``UNKNOWN`` and never appears. ``task_id``
is rendered ONLY when it is a member of ``approved_tasks`` (a frozenset the caller pins from an operator-approved
list; nothing in this module names a task); any other id, including a path-shaped one, becomes ``UNKNOWN`` and is
not echoed anywhere. ``agent`` and ``type`` were already closed sets (``LANES``, ``ALLOWED_TYPES``). Memory notes
are excluded from the shared path entirely: ``project_notes`` always refuses, a caller-provided hash list is not an
authorization. ``write_projection`` never overwrites an existing file, stages in an exclusively created unique file
beside the target, removes only that staging on failure, and turns filesystem errors into fixed path-free refusals.

The one text a record carries (``stored_text`` = ``summary`` = the encoder input, see ``encoder_input``) is the head
line ``<type> <status> <agent> <task_id> [pr:N] [head:HEX] [ci:VALUE]`` rebuilt from the approved fields, so the
persisted bytes, the BM25 text and the vector input are one and the same string. A record's reference is the sha256
of its PROJECTED approved fields in canonical JSON (``event:<hex64>``): it identifies this projection of the
metadata, NOT the canonical bridge event (``provenance.canonical_event`` is false), and nothing here builds or
claims a Q4 index generation.

v3 (independent review of v2): two publication races and a forgeable writer input. ``write_projection`` now sets
an ownership flag only after its exclusive staging creation succeeded and removes only a staging it owns (a
collision with a foreign staging file refuses ``staging_collision`` and leaves that file alone); publication is an
atomic no-replace primitive per platform (``_publish``: Windows ``os.rename`` which fails on an existing
destination, POSIX ``os.link`` which never replaces, then unlink of the staging) and fails closed with
``publication_unsupported`` on any other platform or a filesystem without hard links; a destination another
publisher wrote first is never overwritten or deleted (``output_exists``). The writer additionally REVALIDATES the
whole object (``validate_projection``): exact key sets, closed values, refusal reasons from ``REASONS``, and every
record re-derived from its own approved fields through the same path as ``project_event`` with the same
caller-pinned ``approved_tasks``; any difference refuses ``projection_invalid`` before anything is written. A UTC
boundary instant whose offset conversion overflows is refused as ``time_invalid`` instead of aborting the batch.

v4 (independent review of v3): shape-only ``pr`` and ``head`` can carry arbitrary producer-chosen bits and a
caller-chosen output path could leave any intended output location. By default the projection now OMITS ``pr`` and
``head`` (still shape-validated so a malformed one refuses the event; ``include_refs=True`` opts in, and the writer
must be told the same or it refuses ``projection_invalid``), and the event time is FLOORED to the minute
(``TIME_PRECISION_SECONDS``), so seconds and fractions carry nothing. Publication takes a caller-supplied trusted
output ROOT plus one safe ``.json`` file NAME (``OUTPUT_NAME``: no separator, drive, leading dot, upper case or
space): the root must be an existing real directory and never a symlink, junction or other reparse point, the
target must not exist in any form (a link there is ``output_exists`` and is never followed), staging lives inside
the root, and no directory is ever created. The writer cannot be pointed outside the root by its arguments.

v5 (independent review of v4): flooring the display minute and using the same floor as ``available_utc`` made an
event at 12:34:59 available to a 12:34:10 query (future-data leakage). ``time_utc`` stays the floored minute for
privacy, but ``available_utc`` (record and provenance) is now the NEXT minute boundary after it (``_available``):
a record is never visible inside the minute its event happened in, and an exact-boundary event waits a full
minute (conservative by design). ``visible_at`` states the rule in code with exactly the compare ``q4.ask`` applies
to ``provenance.available_utc`` (strictly below ``situation_time - guard_band_s`` in ``utc_text`` form), so Q4
source/cutoff semantics bind as: visible iff next-minute-boundary < situation_time - guard_band. The last minute of
year 9999 has no next boundary and refuses ``time_invalid`` instead of overflowing. ``include_refs`` is a strict
bool on every entry point (``include_refs_invalid``): the string "false" is truthy and would have opened the refs
channel.

Trust assumptions, disclosed: revalidation is in-process (Python has no forgery-proof object), so it proves
content, never who built it, and it is not authentication or canonical provenance; the Windows no-replace rename
and the POSIX no-replace link are kernel semantics of local filesystems (verified here on NTFS), network or FUSE
filesystems may differ and are then simply unsupported; the components ABOVE the output root are the caller's
trust (the root is used as named, not resolved). Remaining bounded channels, disclosed rather than closed: with
``include_refs=True`` ``pr`` (a positive integer) and ``head`` (40 lowercase hex characters) are shape-only
producer-chosen values; ``ts_utc`` still carries the minute a producer chose (about 20 bits a day, far fewer when
events are near real time); which lane, type, status and ci enum member an event carries is producer-chosen (a few
bits); ``dropped_fields`` and the per-reason refusal counts are producer-influenced integers; batch order and size
are observable. ``approved_tasks`` is trusted as given once it is safe-shaped.

Default deny everywhere else: a lane outside ``LANES``, a type outside ``ALLOWED_TYPES`` (every peer-review and
decision type is outside), a sealed-named approved task, a malformed time, PR or head, or any text field over its
UTF-8 byte cap (a 4-byte character costs four) refuses that event whole with one fixed reason. Synthetic only: no
real capture, memory, index or model is touched by this module or its tests.

v6 (RCO2 production fix spec, operator 16:35Z "solve them"): two pinned policy parameters, both recorded in the
projection and re-pinned by ``validate_projection`` / ``write_projection`` so a projection built under a weaker
policy can never pass as a stricter one. S5 ``exclude_review_signal=True``: every row authored by a review lane
(``REVIEW_LANES``) refuses ``review_lane_excluded`` and every row whose task id contains ``/`` (a PR branch, the
canonical decision task) refuses ``pr_branch_task_excluded`` instead of rendering UNKNOWN, so message/claim/done
rows can no longer reveal that a peer review finished. S2 ``roster`` (lane -> uuid from the pinned wd-fleet.json via
``load_roster(path, sha256)`` / ``roster_from_fleet``): a row projects only when its declared ``agent_uuid`` equals
the roster entry of its lane (``agent_identity_mismatch`` otherwise); the uuid is compared and never persisted, and
the projection records ``authorship = declared_roster_consistent``. This is a declaration-consistency check, NOT
authentication (all lanes run as one OS user; HMAC Phase A is observe-only elsewhere); without a roster the
projection records ``declared_unverified``. DEVIATION from the spec wording "required": both parameters default to
the v5 behaviour because the Lead synthetic suite at 3c19279f (``test_shared_projection_q4_synthetic``) projects
review-lane rows without uuids and this module must not break a consumer it may not edit; the production chain is
made strict by the BUILDER, which refuses any projection not built under both policies. Flipping the defaults is a
one-line Lead decision once that suite carries a roster.
"""

import datetime as dt
import hashlib
import json
import os
import re
import secrets
import stat
from pathlib import Path

from sandbox.mind.step2 import corpus as corpus_mod

SCHEMA = "wd.bridge-mind-shared-projection.v6"
TIME_PRECISION_SECONDS = 60  # event time is floored to the minute; seconds and fractions never reach the record
# One safe file name inside the trusted output root: no separator, no drive, no leading dot, lower case, .json
OUTPUT_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,122}\.json")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
SCOPE = "shared_metadata_projection_not_gate"
PROJECTION_KEYS = frozenset({"schema", "authority", "scope", "accepted", "refused", "dropped_fields", "records",
                             "authorship", "review_signal_excluded"})
META_KEYS = frozenset({"type", "status", "agent", "task_id", "pr", "head", "ci"})
# Every per-event refusal reason the projection may count; a refused key outside this set is a forged projection.
REASONS = frozenset({"event_not_object", "time_invalid", "agent_invalid", "agent_over_bytes", "agent_not_lane",
                     "type_invalid", "type_over_bytes", "type_not_allowed", "status_invalid", "status_over_bytes",
                     "task_id_invalid", "task_id_over_bytes", "sealed_named_excluded", "pr_invalid", "head_invalid",
                     "ci_invalid", "ci_over_bytes", "head_line_over_bytes",
                     "review_lane_excluded", "pr_branch_task_excluded", "agent_identity_mismatch"})
# S5 exclusions leave NO trace in a batch projection: ``project_event`` raises them by name (in-process only), but
# ``project_events`` neither records nor COUNTS them, since a ``review_lane_excluded`` count would itself disclose
# that a review happened in that batch (Lead 2026-10-31 16:53Z). Other refusal reasons stay counted.
SILENT_EXCLUSIONS = frozenset({"review_lane_excluded", "pr_branch_task_excluded"})
# v6 (RCO2 production fix spec S5 + S2). S5: rows authored by a review lane, and rows whose task id names a PR
# branch (contains "/"), are REFUSED SILENTLY (no record, no count) when the caller pins
# ``exclude_review_signal=True``, so a peer review finishing never shows through message/claim/done rows. S2: with a pinned ``roster`` (lane -> uuid from the
# pinned wd-fleet.json) a row is projected only when its declared ``agent_uuid`` equals the roster entry; the uuid is
# compared and never persisted. Authorship is then "declared_roster_consistent" - a declaration check, NOT
# authentication (every lane runs as the same OS user); without a roster it is "declared_unverified".
REVIEW_LANES = frozenset({"claude-rco-1", "claude-rco-2"})
AUTHORSHIP_ROSTER = "declared_roster_consistent"
AUTHORSHIP_UNVERIFIED = "declared_unverified"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
HEX64 = re.compile(r"[0-9a-f]{64}")
ROSTER_BUDGET = 1 << 20  # the pinned fleet file is read through a byte budget and a sha256 pin
# Publication strategy per platform (module bindings so tests can pin them deterministically):
# nt    -> os.rename, which on Windows is MoveFileEx WITHOUT REPLACE_EXISTING and fails if the destination exists;
# posix -> os.link, which never replaces (EEXIST) and is atomic on local filesystems; unlink the staging after.
# Anything else, or a filesystem without hard links, fails closed with publication_unsupported.
SUPPORTED_PLATFORMS = frozenset({"nt", "posix"})
_PLATFORM = os.name
_rename = os.rename
_link = getattr(os, "link", None)
_fsync = os.fsync
LANES = frozenset({"codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"})
ALLOWED_TYPES = frozenset({"message", "wake_request", "handoff", "test", "claim", "release", "done", "milestone",
                           "status", "progress"})
# Documented for readers; the allowlist above is what decides. Peer review and decisions never enter the shared set.
EXCLUDED_TYPES = frozenset({"decision", "finding", "rco_pass", "vote", "veto", "changes_requested", "recusal",
                            "build_consensus", "review"})
ALLOWED_STATUS = frozenset({"none", "requested", "answered", "milestone", "info", "in_progress", "handoff", "done",
                            "active", "delivered", "blocked", "released"})
ALLOWED_CI = frozenset({"green", "red", "pending", "none"})
APPROVED_FIELDS = ("ts_utc", "agent", "type", "status", "task_id", "pr", "head", "ci")
OPTIONAL_FIELDS = ("pr", "head", "ci")
SEALED_MARKERS = ("sealed", "gold", "heldout", "judge")
BYTE_CAPS = {"agent": 40, "type": 32, "status": 32, "task_id": 128, "ci": 32}
HEAD_LINE_BYTES = 320
UNKNOWN = "UNKNOWN"

LANE = re.compile(r"[a-z][a-z0-9-]{1,39}")
TYPE = re.compile(r"[a-z][a-z0-9_]{0,31}")
TASK = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")  # no separator, no drive letter, no space: a path never fits
HEAD = re.compile(r"[0-9a-f]{40}")
PR_MAX = 10 ** 9


class ProjectionRefused(ValueError):
    """One fixed reason token; never the offending value."""


def _bytes(text):
    return len(text.encode("utf-8", "surrogatepass"))


def _text(name, value):
    """A text field within its byte cap; anything else refuses with a fixed reason."""
    if not isinstance(value, str):
        raise ProjectionRefused(f"{name}_invalid")
    if _bytes(value) > BYTE_CAPS[name]:
        raise ProjectionRefused(f"{name}_over_bytes")
    return value


def _closed(name, value, pattern, allowed):
    """A required field that must match its pattern AND be a member of a closed set."""
    value = _text(name, value)
    if pattern.fullmatch(value) is None:
        raise ProjectionRefused(f"{name}_invalid")
    return value if value in allowed else None


def _enum(name, value, allowed, empty=None):
    """An enum field: a member renders itself, any other text becomes UNKNOWN, a non-text refuses."""
    if value is None or value == "":
        return empty
    value = _text(name, value)
    return value if value in allowed else UNKNOWN


def _approved_tasks(approved_tasks):
    if approved_tasks is None:
        return frozenset()
    if not isinstance(approved_tasks, frozenset) or not all(isinstance(t, str) and TASK.fullmatch(t) is not None
                                                            for t in approved_tasks):
        raise ProjectionRefused("approved_tasks_invalid")
    return approved_tasks


def _task(value, approved_tasks):
    """The task id only when pre-approved and safe-shaped; a sealed-named approved task refuses; else UNKNOWN."""
    value = _text("task_id", value)
    if TASK.fullmatch(value) is None or value not in approved_tasks:
        return UNKNOWN
    if any(marker in value for marker in SEALED_MARKERS):
        raise ProjectionRefused("sealed_named_excluded")
    return value


def _strict_flag(include_refs):
    """``include_refs`` must be a real bool: the string "false" is truthy and would enable the refs channel."""
    if type(include_refs) is not bool:
        raise ProjectionRefused("include_refs_invalid")
    return include_refs


def _exclude_flag(exclude_review_signal):
    """``exclude_review_signal`` must be a real bool for the same reason."""
    if type(exclude_review_signal) is not bool:
        raise ProjectionRefused("exclude_review_signal_invalid")
    return exclude_review_signal


def _roster(roster):
    """None, or a closed non-empty mapping whose keys are projection lanes and whose values are distinct lowercase
    uuids. A lane absent from the roster cannot project while the roster is pinned (``agent_identity_mismatch``)."""
    if roster is None:
        return None
    if (not isinstance(roster, dict) or not roster or not set(roster) <= LANES
            or not all(isinstance(uuid, str) and UUID.fullmatch(uuid) is not None for uuid in roster.values())
            or len(set(roster.values())) != len(roster)):
        raise ProjectionRefused("roster_invalid")
    return {agent: roster[agent] for agent in sorted(roster)}


def roster_from_fleet(document):
    """The lane -> uuid roster of a parsed wd-fleet.json: each listed projection lane once with a valid uuid; lanes
    outside ``LANES`` are ignored (they never project); a duplicate lane or a bad uuid refuses; a projection lane the
    fleet file does not list is simply absent (and then cannot project under this roster)."""
    if not isinstance(document, dict) or not isinstance(document.get("lanes"), list):
        raise ProjectionRefused("roster_invalid")
    roster = {}
    entries = list(document["lanes"])
    supervisor = document.get("tools_supervisor")  # the real fleet file keeps the Tools lane here, not under lanes
    if isinstance(supervisor, dict):
        entries.append(supervisor)
    for lane in entries:
        if not isinstance(lane, dict) or not isinstance(lane.get("agent"), str):
            raise ProjectionRefused("roster_invalid")
        if lane["agent"] not in LANES:
            continue
        if lane["agent"] in roster:
            raise ProjectionRefused("roster_invalid")
        roster[lane["agent"]] = lane.get("agent_uuid")
    return _roster(roster)


def _pairs_without_duplicates(pairs):
    """``object_pairs_hook``: a JSON object that spells one key twice refuses instead of letting the last value win
    (``json.loads`` default), so a second ``agent_uuid`` or a second ``lanes`` cannot shadow the pinned one."""
    out = {}
    for key, value in pairs:
        if key in out:
            raise ProjectionRefused("roster_duplicate_key")
        out[key] = value
    return out


def load_roster(path, expected_sha256):
    """The roster from the pinned fleet file: bytes within budget whose sha256 equals the caller's pin, parsed with
    duplicate JSON keys refused (``roster_duplicate_key``)."""
    if not isinstance(expected_sha256, str) or HEX64.fullmatch(expected_sha256) is None:
        raise ProjectionRefused("roster_pin_invalid")
    try:
        with open(path, "rb") as handle:
            raw = handle.read(ROSTER_BUDGET + 1)
    except OSError:
        raise ProjectionRefused("roster_unreadable") from None
    if len(raw) > ROSTER_BUDGET:
        raise ProjectionRefused("roster_over_budget")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ProjectionRefused("roster_pin_mismatch")
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_without_duplicates)
    except ProjectionRefused:
        raise  # roster_duplicate_key keeps its own token (ProjectionRefused is a ValueError)
    except (ValueError, RecursionError):
        raise ProjectionRefused("roster_unreadable") from None
    return roster_from_fleet(document)


def _available(ts_text):
    """Conservative availability: the NEXT minute boundary after the floored display minute, so a record is never
    visible inside the minute its event happened in; past the last representable minute it refuses."""
    try:
        when = corpus_mod.parse_utc(ts_text) + dt.timedelta(seconds=TIME_PRECISION_SECONDS)
    except (OverflowError, ValueError, TypeError):
        raise ProjectionRefused("time_invalid") from None
    return corpus_mod.utc_text(when)


def visible_at(record, query_utc, guard_band_s=0):
    """Whether a projected record may be seen by a query at ``query_utc``: ``provenance.available_utc`` must be
    strictly below ``query_utc - guard_band_s`` in ``utc_text`` form, exactly the compare ``q4.ask`` applies
    (``self.available < limit``). An unparseable query time sees nothing."""
    when = corpus_mod.parse_utc(query_utc) if isinstance(query_utc, str) else None
    if when is None:
        return False
    try:
        limit = corpus_mod.utc_text(when - dt.timedelta(seconds=guard_band_s))
    except (OverflowError, TypeError):
        return False
    return record["provenance"]["available_utc"] < limit


def approved_fields(event, approved_tasks=frozenset(), include_refs=False, roster=None, exclude_review_signal=False):
    """The approved fields of one event, validated; forbidden and unknown keys are counted, never read.

    ``pr`` and ``head`` are always shape-validated (a malformed one refuses the event) but are projected as None
    unless the caller opts in with ``include_refs=True`` (a strict bool): they are producer-chosen integers and hex
    strings and so a covert channel by default. The time is floored to ``TIME_PRECISION_SECONDS``. With a
    ``roster`` the declared ``agent_uuid`` must equal the roster entry of the lane (compared, never kept); with
    ``exclude_review_signal`` a review-lane row or a PR-branch task id refuses instead of projecting."""
    include_refs = _strict_flag(include_refs)
    exclude_review_signal = _exclude_flag(exclude_review_signal)
    if not isinstance(event, dict):
        raise ProjectionRefused("event_not_object")
    dropped = sum(1 for key in event if key not in APPROVED_FIELDS)
    try:  # a boundary instant such as year 1 or 9999 with an offset overflows on conversion: refuse, never abort
        when = corpus_mod.parse_utc(event.get("ts_utc")) if isinstance(event.get("ts_utc"), str) else None
    except (OverflowError, ValueError):
        when = None
    if when is None:
        raise ProjectionRefused("time_invalid")
    when = when.replace(second=0, microsecond=0)  # floored display minute (UTC-aware after parse_utc)
    _available(corpus_mod.utc_text(when))  # the next boundary must exist: the last minute of year 9999 refuses
    agent = _closed("agent", event.get("agent"), LANE, LANES)
    if agent is None:
        raise ProjectionRefused("agent_not_lane")
    if exclude_review_signal and agent in REVIEW_LANES:
        raise ProjectionRefused("review_lane_excluded")
    if roster is not None:
        declared = event.get("agent_uuid")  # read for the compare only; it is not an approved field
        if not isinstance(declared, str) or declared != roster.get(agent):
            raise ProjectionRefused("agent_identity_mismatch")
    kind = _closed("type", event.get("type"), TYPE, ALLOWED_TYPES)
    if kind is None:
        raise ProjectionRefused("type_not_allowed")
    status = _enum("status", event.get("status"), ALLOWED_STATUS, empty="none")
    if exclude_review_signal and isinstance(event.get("task_id"), str) and "/" in event["task_id"]:
        raise ProjectionRefused("pr_branch_task_excluded")  # a PR branch is the canonical decision task: refuse
    task = _task(event.get("task_id"), approved_tasks)
    pr = event.get("pr")
    if pr is not None and (type(pr) is not int or pr < 1 or pr > PR_MAX):
        raise ProjectionRefused("pr_invalid")
    head = event.get("head")
    if head is not None and (not isinstance(head, str) or HEAD.fullmatch(head) is None):
        raise ProjectionRefused("head_invalid")
    ci = _enum("ci", event.get("ci"), ALLOWED_CI, empty=None)
    if not include_refs:
        pr, head = None, None  # validated above, omitted here: shape-only fields are a channel by default
    fields = {"ts_utc": corpus_mod.utc_text(when), "agent": agent, "type": kind, "status": status, "task_id": task,
              "pr": pr, "head": head, "ci": ci}
    return fields, dropped


def head_line(fields):
    """The only text a shared record carries, rebuilt from approved fields; refused over the byte cap."""
    parts = [fields["type"], fields["status"], fields["agent"], fields["task_id"]]
    if fields["pr"] is not None:
        parts.append(f"pr:{fields['pr']}")
    if fields["head"] is not None:
        parts.append(f"head:{fields['head']}")
    if fields["ci"] is not None:
        parts.append(f"ci:{fields['ci']}")
    line = " ".join(parts)
    if _bytes(line) > HEAD_LINE_BYTES:
        raise ProjectionRefused("head_line_over_bytes")
    return line


def projected_ref(fields):
    """sha256 of the projected approved fields; identifies this projection, not the canonical bridge event."""
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def project_event(event, approved_tasks=frozenset(), include_refs=False, roster=None, exclude_review_signal=False):
    """One shared record from one event, or ProjectionRefused; nothing partial is returned."""
    fields, dropped = approved_fields(event, approved_tasks, _strict_flag(include_refs), _roster(roster),
                                      _exclude_flag(exclude_review_signal))
    return _record(fields), dropped


def _record(fields):
    """The exact record for validated approved fields; the writer re-derives every record through this."""
    text = head_line(fields)
    ref = projected_ref(fields)
    available = _available(fields["ts_utc"])
    return {"record_id": "event:" + ref, "kind": "event", "time_utc": fields["ts_utc"],
            "available_utc": available, "stored_text": text, "summary": text,
            "meta": {"type": fields["type"], "status": fields["status"], "agent": fields["agent"],
                     "task_id": fields["task_id"], "pr": fields["pr"], "head": fields["head"], "ci": fields["ci"]},
            "provenance": {"source_kind": "projected_bridge_metadata", "event_ref": ref,
                           "ref_kind": "sha256_of_projected_metadata", "canonical_event": False,
                           "tier": {"source_type": "bridge_event"}, "available_utc": available}}


def encoder_input(record):
    """What the encoder would embed for this record: exactly the stored text, nothing appended."""
    return record["stored_text"]


def _projection(records, refused, dropped, roster, exclude_review_signal):
    out = {"schema": SCHEMA, "authority": "none", "scope": SCOPE,
           "accepted": len(records), "refused": dict(sorted(refused.items())), "dropped_fields": dropped,
           "records": records, "authorship": AUTHORSHIP_ROSTER if roster is not None else AUTHORSHIP_UNVERIFIED,
           "review_signal_excluded": exclude_review_signal}
    corpus_mod.ws.guard_object(out)
    return out


def project_events(events, approved_tasks=None, include_refs=False, roster=None, exclude_review_signal=False):
    """Every acceptable event as a record; refusals are counted by reason only; a non-object input refuses whole.

    ``approved_tasks`` is a frozenset of safe-shaped task ids the caller pins from an operator-approved list; with
    none, every task id renders as UNKNOWN. ``include_refs`` opts in to projecting pr and head (off by default).
    ``roster`` (lane -> uuid, see ``load_roster``) turns on the S2 authorship compare; ``exclude_review_signal``
    turns on the S5 refusals. Both are recorded in the projection and re-pinned by ``validate_projection``."""
    include_refs = _strict_flag(include_refs)
    exclude_review_signal = _exclude_flag(exclude_review_signal)
    approved = _approved_tasks(approved_tasks)
    roster = _roster(roster)
    records, refused, dropped = [], {}, 0
    for event in events:
        if not isinstance(event, dict):
            raise ProjectionRefused("event_not_object")
        try:
            record, count = project_event(event, approved, include_refs, roster, exclude_review_signal)
        except ProjectionRefused as exc:
            if str(exc) in SILENT_EXCLUSIONS:
                continue  # S5: not even a count, a per-reason count would itself say a review happened in this batch
            refused[str(exc)] = refused.get(str(exc), 0) + 1
            continue
        records.append(record)
        dropped += count
    return _projection(records, refused, dropped, roster, exclude_review_signal)


def project_notes(notes, allowlist=None):
    """Memory notes never enter the shared path; a caller-provided hash list is not an authorization."""
    raise ProjectionRefused("notes_excluded")


def validate_projection(projection, approved_tasks=None, include_refs=False, roster=None, exclude_review_signal=False):
    """Strict exact revalidation of a projection object before it is published.

    Every record is re-derived from its own approved fields through the same validation path as ``project_event``
    (closed sets, byte caps, the caller-pinned approved set) and must be byte-for-byte equal to the re-derivation;
    the top level must carry exactly the projection keys with closed values, refusal reasons from ``REASONS`` and
    non-negative integer counts. The caller re-pins the v6 policy: ``authorship`` must match whether a ``roster``
    is given (the uuid itself is not persisted, so only lane membership in the roster is re-checked) and
    ``review_signal_excluded`` must equal ``exclude_review_signal`` (then no record may come from a review lane).
    Anything else refuses with ``projection_invalid`` and nothing is written. This is in-process validation, not
    authentication: Python offers no forgery-proof object, so the writer trusts the content only after re-deriving
    it, never because of who built it."""
    include_refs = _strict_flag(include_refs)
    exclude_review_signal = _exclude_flag(exclude_review_signal)
    approved = _approved_tasks(approved_tasks)
    roster = _roster(roster)
    try:
        if not isinstance(projection, dict) or set(projection) != PROJECTION_KEYS:
            raise ProjectionRefused("projection_invalid")
        records, refused, dropped = projection["records"], projection["refused"], projection["dropped_fields"]
        expected_authorship = AUTHORSHIP_ROSTER if roster is not None else AUTHORSHIP_UNVERIFIED
        if (projection["schema"] != SCHEMA or projection["authority"] != "none" or projection["scope"] != SCOPE
                or not isinstance(records, list) or type(projection["accepted"]) is not int
                or projection["accepted"] != len(records) or type(dropped) is not int or dropped < 0
                or not isinstance(refused, dict)
                or any(reason not in REASONS or type(count) is not int or count < 1
                       for reason, count in refused.items())
                or projection["authorship"] != expected_authorship
                or projection["review_signal_excluded"] is not exclude_review_signal):
            raise ProjectionRefused("projection_invalid")
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("meta"), dict):
                raise ProjectionRefused("projection_invalid")
            # Python considers 0 and 0.0 equal to False in dict equality; the
            # provenance flag must be the actual JSON boolean, not a number.
            if not isinstance(record.get("provenance"), dict) or record["provenance"].get("canonical_event") is not False:
                raise ProjectionRefused("projection_invalid")
            meta = record["meta"]
            if set(meta) != META_KEYS or not isinstance(record.get("time_utc"), str):
                raise ProjectionRefused("projection_invalid")
            if roster is not None and meta["agent"] not in roster:
                raise ProjectionRefused("projection_invalid")
            synthetic = {"ts_utc": record["time_utc"], **meta}
            fields, _ = approved_fields(synthetic, approved, include_refs, None, exclude_review_signal)
            if fields["ts_utc"] != record["time_utc"] or _record(fields) != record:
                raise ProjectionRefused("projection_invalid")
    except ProjectionRefused:
        raise ProjectionRefused("projection_invalid") from None
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ProjectionRefused("projection_invalid") from None
    return projection


def _staging_name(path):
    return path.with_name(f".{path.name}.{secrets.token_hex(8)}.staging")


def _publish(staging, path):
    """Atomic no-replace publication of ``staging`` at ``path``: ``output_exists`` if any publication is already
    there (theirs is never touched), ``publication_unsupported`` where no no-replace primitive is available."""
    if _PLATFORM == "nt":
        try:
            _rename(staging, path)  # MoveFileEx without REPLACE_EXISTING: fails when the destination exists
        except FileExistsError:
            raise ProjectionRefused("output_exists") from None
        return
    if _PLATFORM == "posix" and _link is not None:
        try:
            _link(staging, path)  # link(2) never replaces: EEXIST when another publisher won
        except FileExistsError:
            raise ProjectionRefused("output_exists") from None
        except OSError:
            raise ProjectionRefused("publication_unsupported") from None  # no hard links here: fail closed
        try:
            os.unlink(staging)  # the publication stands; a leftover staging holds the same guarded bytes
        except OSError:
            pass
        return
    raise ProjectionRefused("publication_unsupported")


def _output_root(root):
    """The caller-supplied trusted output root: an existing real directory, never a symlink, junction or other
    reparse point (never followed); anything else refuses output_root_invalid. Components above the root are the
    caller's trust: the root is used exactly as named, nothing is resolved through it."""
    root = Path(root)
    try:
        info = os.lstat(root)
    except OSError:
        raise ProjectionRefused("output_root_invalid") from None
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & _REPARSE):
        raise ProjectionRefused("output_root_invalid")
    return root


def write_projection(root, name, projection, approved_tasks=None, include_refs=False, roster=None,
                     exclude_review_signal=False):
    """Revalidate strictly, guard the whole object, then publish ``projection`` as ``root/name``: ``root`` is a
    caller-supplied trusted directory (``_output_root``), ``name`` one safe ``.json`` file name (``OUTPUT_NAME``), so
    nothing this call writes can leave the root. The data is staged in a unique exclusively created file inside the
    root and published atomically onto a target that must not exist in any form (a symlink or reparse point at the
    target is ``output_exists``, never followed). Only a staging file this call created is ever removed; a
    destination written by anyone else is never overwritten or deleted; on a refusal nothing of ours remains."""
    validate_projection(projection, approved_tasks, _strict_flag(include_refs), roster, _exclude_flag(exclude_review_signal))
    try:
        corpus_mod.ws.guard_object(projection)
    except corpus_mod.ws.WorkspaceError:
        raise ProjectionRefused("output_guarded") from None
    if _PLATFORM not in SUPPORTED_PLATFORMS:
        raise ProjectionRefused("publication_unsupported")
    if not isinstance(name, str) or OUTPUT_NAME.fullmatch(name) is None:
        raise ProjectionRefused("output_name_invalid")
    root = _output_root(root)
    data = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
    path = root / name
    staging, owned = None, False
    try:
        try:
            os.lstat(path)
        except FileNotFoundError:
            pass
        else:
            raise ProjectionRefused("output_exists")  # any entry, link or reparse point included; never followed
        staging = _staging_name(path)
        try:
            handle = open(staging, "xb")
        except FileExistsError:
            raise ProjectionRefused("staging_collision") from None  # not ours: never touched
        owned = True  # only now does this call own a file it may remove
        with handle:
            handle.write(data)
            handle.flush()
            _fsync(handle.fileno())
        _publish(staging, path)
    except ProjectionRefused:
        _discard_own(staging, owned)
        raise
    except OSError:
        _discard_own(staging, owned)
        raise ProjectionRefused("write_failed") from None
    return hashlib.sha256(data).hexdigest()


def _discard_own(staging, owned):
    if owned and staging is not None and staging.exists():
        try:
            staging.unlink()
        except OSError:
            pass
