"""Inactive synthetic public-status publisher and session reader.

No starter, service, scheduler or live source is wired here. Pins and lane/UUID
are caller assertions, NOT authentication between same-user processes. The
caller also supplies the clock: freshness covers publication age only, not
completeness or freshness of the source bridge. Filesystem checks and atomic
replace do not prevent hostile same-user replacement or TOCTOU. The selected
.codex-audit directory must already exist and be independently access-controlled.
Each publication REPLACES the previous one with only its latest delta: empty
records never mean that the source has no historical events. Cooperating
v2 writers serialize and compare the prior publication digest; this is not a
source-cursor CAS, a merge of deltas, or authentication of the writer. A v1
publication is refused for both reads and writes, not migrated or overwritten.
Before rollout, old v1 writers must be stopped or handed off externally: they
do not honor the v2 lock. Revision is not an independent anti-rollback anchor.
A writer that must survive an AMBIGUOUS publish failure (the replace may or
may not have landed) first obtains the exact digest of the bytes it intends to
write with ``prepare_status_attempt`` (same lock, same CAS, no publication
write; like every lock user it may create the empty lock slot file), passes it
to ``publish_status`` as ``attempt_sha256`` (the publish refuses before any write
if the bytes would differ), and after a failure asks ``recover_status_attempt``
with its own prior digest and that attempt digest. Recovery answers only
"landed" or "unchanged" and refuses any third digest, so an unrelated
publication by another writer is never taken as the authority for a retry and
no digest is ever learned from the file. No freshness, anti-rollback or
authenticity claim follows from any of this.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat

from sandbox.mind.step2 import shared_capture_adapter as adapter, shared_projection as projection
from sandbox.mind.step2 import status_metadata_producer as producer
from sandbox.mind.step2.reader_slot import reader_slot

SCHEMA = "wd.mind-public-status-publication.v2"
FILENAME = "public-status.json"
LOCK_FILENAME = ".public-status.lock"
_UNSET = object()
_MAX_REVISION = (1 << 63) - 1
MAX_BYTES = 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_FLOORED_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:00\.000000Z\Z")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class PublicStatusRefused(ValueError):
    """Fixed error without content, paths, identities or cursor details."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _object(raw):
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise ValueError()
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=adapter._strict_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if type(value) is not dict:
        raise ValueError()
    return value


def _digest(value):
    return type(value) is str and _HEX.fullmatch(value) is not None


def _instant(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError()
    return value.astimezone(timezone.utc)


def _directory(audit_root):
    root = Path(audit_root)
    if not root.is_absolute() or root.name != ".codex-audit" or ".." in root.parts:
        raise ValueError()
    for item in (root, *root.parents):
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            raise ValueError()
    if not root.is_dir():
        raise ValueError()
    return root


def _regular(path):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
        raise ValueError()


def _pins(anchor_bytes, anchor_sha256, policy_bytes, policy_sha256, roster_bytes, roster_sha256,
          public_policy_bytes, public_policy_sha256):
    tasks, roster = adapter.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
    public_tasks, public_types = adapter.public_status_policy(public_policy_bytes, public_policy_sha256, tasks)
    if (not isinstance(anchor_sha256, str) or _HEX.fullmatch(anchor_sha256) is None
            or type(anchor_bytes) is not bytes or len(anchor_bytes) > 16384
            or hashlib.sha256(anchor_bytes).hexdigest() != anchor_sha256):
        raise ValueError()
    anchor = _object(anchor_bytes)
    if (set(anchor) != {"schema", "source_path", "generation_path", "cursor_start", "policy_sha256",
                        "roster_sha256", "status_mode", "public_policy_sha256"}
            or anchor["schema"] != "wd.q4-source-anchor.v1" or anchor["status_mode"] != "public-v1"
            or anchor["policy_sha256"] != policy_sha256 or anchor["roster_sha256"] != roster_sha256
            or anchor["public_policy_sha256"] != public_policy_sha256):
        raise ValueError()
    return roster, public_tasks, public_types


_PINS = ("anchor_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256")
_KEYS = frozenset({"schema", "revision", "authority", "coverage", "as_of_utc", *_PINS, "body_sha256", "body"})


def _shape(value):
    """Pin-free structural check of a status body: closed shape, floored UTC times, non-review lanes, allowed
    statuses, string type and task fields. Policy MEMBERSHIP of type and task is checked by _body only."""
    if (type(value) is not dict or set(value) != {"schema", "authority", "scope", "freshness", "records"}
            or value["schema"] != "wd.mind-status-metadata.v1" or value["authority"] != "none"
            or value["scope"] != "in_memory_metadata_not_gate" or value["freshness"] != "UNKNOWN"
            or type(value["records"]) is not list or len(value["records"]) > 10000):
        raise ValueError()
    for item in value["records"]:
        if (type(item) is not dict or set(item) != {"time_utc", "agent", "type", "status", "task_id"}
                or type(item["time_utc"]) is not str or _FLOORED_UTC.fullmatch(item["time_utc"]) is None
                or item["agent"] not in projection.LANES or item["agent"] in ("claude-rco-1", "claude-rco-2")
                or type(item["type"]) is not str or item["status"] not in projection.ALLOWED_STATUS
                or type(item["task_id"]) is not str):
            raise ValueError()
        parsed = datetime.fromisoformat(item["time_utc"].replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError()
    return value


def _body(value, tasks, types):
    _shape(value)
    for item in value["records"]:
        if item["type"] not in types or item["task_id"] not in tasks:
            raise ValueError()
    return value


def _structure(raw):
    """One structurally COMPLETE v2 publication, independent of pins and clock: closed key set, schema, revision
    range, authority, coverage, hex pin digests, parseable aware as_of_utc, body shape and body digest. Returns the
    document. It does not check pin equality, policy membership or age."""
    doc = _object(raw)
    if (set(doc) != _KEYS or doc["schema"] != SCHEMA or doc["authority"] != "none"
            or type(doc["revision"]) is not int or not 1 <= doc["revision"] <= _MAX_REVISION
            or doc["coverage"] != "last_delta_only" or any(not _digest(doc[name]) for name in _PINS)
            or type(doc["as_of_utc"]) is not str or len(doc["as_of_utc"]) > 40 or not _digest(doc["body_sha256"])):
        raise ValueError()
    _instant(datetime.fromisoformat(doc["as_of_utc"]))
    _shape(doc["body"])
    if hashlib.sha256(_json(doc["body"])).hexdigest() != doc["body_sha256"]:
        raise ValueError()
    return doc


def _attempt(audit_root, snapshot_result, enabled, expected_previous_sha256, as_of, pins):
    """Shared pre-lock checks of a publish attempt: switch, explicit prior, pins, clock, body and directory.
    Returns root, lock, body, now, tasks, types."""
    if enabled is not True or expected_previous_sha256 is _UNSET or (
            expected_previous_sha256 is not None and not _digest(expected_previous_sha256)):
        raise ValueError()
    _, tasks, types = _pins(**pins)
    now = _instant(as_of)
    data = producer.build_status(snapshot_result, enabled=True, **pins)
    body = _body(_object(data), tasks, types)
    root = _directory(audit_root)
    lock = root / LOCK_FILENAME
    if os.path.lexists(lock):
        _regular(lock)
    return root, lock, body, now, tasks, types


def _intended(target, expected_previous_sha256, body, now, pins, tasks, types):
    """The exact bytes this attempt writes over the CURRENT publication (the caller holds the lock): CAS on the
    prior digest, explicit genesis, revision + 1. The prior must be a structurally COMPLETE v2 publication
    (_structure) even when its exact digest is supplied; a prior under the SAME pins must also pass policy
    membership of its records; a prior under other pins (rotation) is accepted on structure and digest only, its
    membership under its own policy is not checkable here. Age is never a reason to refuse a prior."""
    revision = 1
    if os.path.lexists(target):
        _regular(target)
        with target.open("rb") as stream:
            old = stream.read(MAX_BYTES + 1)
        if expected_previous_sha256 is None or hashlib.sha256(old).hexdigest() != expected_previous_sha256:
            raise ValueError()
        old_doc = _structure(old)
        if not old_doc["revision"] < _MAX_REVISION:
            raise ValueError()
        if all(old_doc[name] == pins[name] for name in _PINS):
            _body(old_doc["body"], tasks, types)
        revision = old_doc["revision"] + 1
    elif expected_previous_sha256 is not None:
        raise ValueError()
    doc = {"schema": SCHEMA, "revision": revision, "authority": "none",
           "coverage": "last_delta_only", "as_of_utc": now.isoformat(),
           "anchor_sha256": pins["anchor_sha256"], "policy_sha256": pins["policy_sha256"],
           "roster_sha256": pins["roster_sha256"], "public_policy_sha256": pins["public_policy_sha256"],
           "body_sha256": hashlib.sha256(_json(body)).hexdigest(), "body": body}
    payload = _json(doc)
    if len(payload) > MAX_BYTES:
        raise ValueError()
    return payload


def prepare_status_attempt(audit_root, snapshot_result, *, as_of, enabled=False,
                           expected_previous_sha256=_UNSET, timeout_seconds=0.2, **pins):
    """Bounded attempt preparation: the digest of the exact bytes publish_status would write for these inputs
    over the current publication, computed under the same lock with the same CAS. It writes NO publication and no
    staging file; its only filesystem side effect is the one every lock user has: the empty cooperating-writer lock
    slot file may be created. The caller keeps the digest and passes it to publish_status(attempt_sha256=...) and,
    after an ambiguous failure, to recover_status_attempt. Returns only the digest; every other outcome is the fixed
    refusal. OFF by default."""
    try:
        root, lock, body, now, tasks, types = _attempt(audit_root, snapshot_result, enabled,
                                                       expected_previous_sha256, as_of, pins)
        with reader_slot(lock, timeout_seconds=timeout_seconds):
            _regular(lock)
            return hashlib.sha256(_intended(root / FILENAME, expected_previous_sha256, body, now, pins,
                                            tasks, types)).hexdigest()
    except Exception:
        raise PublicStatusRefused("public_status_refused") from None


def recover_status_attempt(audit_root, *, prior_sha256, attempt_sha256, enabled=False, timeout_seconds=0.2):
    """Recovery after an AMBIGUOUS publish failure, bound to the caller's own digests: under the same lock, answer
    "landed" when the publication bytes are exactly the prepared attempt, "unchanged" when they are exactly the
    caller's prior (None = genesis, no file), and refuse anything else, including a valid publication by another
    writer. Never returns a digest, records, pins or paths; never repairs, removes or replaces the file. OFF by
    default."""
    try:
        if (enabled is not True or not _digest(attempt_sha256) or
                (prior_sha256 is not None and not _digest(prior_sha256)) or prior_sha256 == attempt_sha256):
            raise ValueError()
        root = _directory(audit_root)
        target = root / FILENAME
        lock = root / LOCK_FILENAME
        if os.path.lexists(lock):
            _regular(lock)
        with reader_slot(lock, timeout_seconds=timeout_seconds):
            _regular(lock)
            if not os.path.lexists(target):
                if prior_sha256 is None:
                    return "unchanged"
                raise ValueError()
            _regular(target)
            with target.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError()
            present = hashlib.sha256(raw).hexdigest()
            if present == attempt_sha256:
                return "landed"
            if present == prior_sha256:
                return "unchanged"
            raise ValueError()
    except Exception:
        raise PublicStatusRefused("public_status_refused") from None


def publish_status(audit_root, snapshot_result, *, as_of, enabled=False, expected_previous_sha256=_UNSET,
                   attempt_sha256=None, timeout_seconds=0.2, **pins):
    """Publish with a cooperating-writer CAS; return only the publication digest. With attempt_sha256 (from
    prepare_status_attempt) the publish refuses BEFORE any write unless the bytes are exactly that attempt."""
    stage = None
    owned = False
    try:
        if attempt_sha256 is not None and not _digest(attempt_sha256):
            raise ValueError()
        root, lock, body, now, tasks, types = _attempt(audit_root, snapshot_result, enabled,
                                                       expected_previous_sha256, as_of, pins)
        target = root / FILENAME
        with reader_slot(lock, timeout_seconds=timeout_seconds):
            _regular(lock)
            payload = _intended(target, expected_previous_sha256, body, now, pins, tasks, types)
            if attempt_sha256 is not None and hashlib.sha256(payload).hexdigest() != attempt_sha256:
                raise ValueError()
            stage = root / (".public-status-" + secrets.token_hex(16) + ".tmp")
            with stage.open("xb") as stream:
                owned = True
                if stream.write(payload) != len(payload):
                    raise OSError("short_write")
                stream.flush()
                os.fsync(stream.fileno())
            if os.path.lexists(target):
                _regular(target)
            os.replace(stage, target)
            owned = False
            return hashlib.sha256(payload).hexdigest()
    except Exception:
        raise PublicStatusRefused("public_status_refused") from None
    finally:
        if owned:
            try:
                stage.unlink()
            except OSError:
                pass  # An orphan is never interpreted as the commit pointer.


def _publication(raw, pins, tasks, types, now, max_age_seconds):
    """One validated v2 publication under THIS caller's pins: structure (_structure), pin equality, caller-clock
    age and policy membership; returns the validated body and the publication age. Anything else (v1, foreign pins,
    stale, tampered, incomplete) refuses."""
    doc = _structure(raw)
    if any(doc[name] != pins[name] for name in _PINS):
        raise ValueError()
    age = (now - _instant(datetime.fromisoformat(doc["as_of_utc"]))).total_seconds()
    if age < 0 or age > max_age_seconds:
        raise ValueError()
    return _body(doc["body"], tasks, types), age


def read_status(audit_root, *, lane, agent_uuid, as_of, max_age_seconds, enabled=False, **pins):
    """Read a publication with declared roster identity; never returns pins."""
    try:
        if enabled is not True or type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 900:
            raise ValueError()
        roster, tasks, types = _pins(**pins)
        if (not isinstance(lane, str) or lane not in roster or not isinstance(agent_uuid, str)
                or roster[lane].lower() != agent_uuid.lower()):
            raise ValueError()
        now = _instant(as_of)
        path = _directory(audit_root) / FILENAME
        _regular(path)
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        body, age = _publication(raw, pins, tasks, types, now, max_age_seconds)
        return _json({"schema": "wd.mind-public-session.v1", "authority": "none", "coverage": "last_delta_only", "lane": lane,
                      "identity_verification": "caller_declared_not_authenticated",
                      "as_of_utc": now.isoformat(), "publication_age_seconds": age,
                      "source_freshness": "UNKNOWN", "records": body["records"]})
    except Exception:
        raise PublicStatusRefused("public_status_refused") from None


# Opt-in v3 is a separate publication and lock: existing v2 callers, schemas,
# filenames and enable switches are unchanged. It is not a service, launcher,
# identity authenticator or source-freshness proof. Callers must retain their
# exact prior publication digest, previous window anchor and observed candidate.
V3_SCHEMA = "wd.mind-public-status-publication.v3"
V3_FILENAME = "public-status-v3.json"
V3_LOCK_FILENAME = ".public-status-v3.lock"
_V3_PINS = ("root_anchor_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256")
_V3_KEYS = frozenset({"schema", "revision", "authority", "coverage", "as_of_utc", *_V3_PINS,
                      "window_anchor_sha256", "next_anchor_sha256", "body_sha256", "body"})


def _v3_structure(raw):
    doc = _object(raw)
    if (set(doc) != _V3_KEYS or doc["schema"] != V3_SCHEMA or doc["authority"] != "none"
            or doc["coverage"] != "last_delta_only" or type(doc["revision"]) is not int
            or not 1 <= doc["revision"] <= _MAX_REVISION
            or any(not _digest(doc[name]) for name in (*_V3_PINS, "window_anchor_sha256", "next_anchor_sha256"))
            or type(doc["as_of_utc"]) is not str or len(doc["as_of_utc"]) > 40
            or not _digest(doc["body_sha256"])):
        raise ValueError()
    _instant(datetime.fromisoformat(doc["as_of_utc"]))
    _shape(doc["body"])
    if hashlib.sha256(_json(doc["body"])).hexdigest() != doc["body_sha256"]:
        raise ValueError()
    return doc


def _v3_attempt(audit_root, snapshot_result, *, as_of, enabled, expected_previous_sha256,
                root_anchor_bytes, root_anchor_sha256, previous_anchor_bytes, previous_anchor_sha256,
                expected_previous_candidate, pins):
    from tools import wd_mind_public_series as series

    if enabled is not True or expected_previous_sha256 is _UNSET or (
            expected_previous_sha256 is not None and not _digest(expected_previous_sha256)):
        raise ValueError()
    root_pins = dict(pins, anchor_bytes=root_anchor_bytes, anchor_sha256=root_anchor_sha256)
    _, tasks, types = _pins(**root_pins)
    _pins(**pins)
    series.check_continuation(enabled=True, root_anchor_bytes=root_anchor_bytes,
        root_anchor_sha256=root_anchor_sha256, previous_anchor_bytes=previous_anchor_bytes,
        previous_anchor_sha256=previous_anchor_sha256, next_anchor_bytes=pins["anchor_bytes"],
        next_anchor_sha256=pins["anchor_sha256"], expected_candidate_cursor=expected_previous_candidate)
    if expected_previous_sha256 is None and (
            pins["anchor_sha256"] != root_anchor_sha256 or previous_anchor_sha256 != root_anchor_sha256):
        raise ValueError()
    now = _instant(as_of)
    # This verifies the entire current snapshot projection against its own
    # window anchor, policy and roster before metadata is made public.
    body = _body(_object(producer.build_status(snapshot_result, enabled=True, **pins)), tasks, types)
    anchor = _object(pins["anchor_bytes"])
    candidate = asdict(snapshot_result["candidate_cursor"])
    next_bytes = _json(dict(anchor, cursor_start=candidate))
    next_sha = hashlib.sha256(next_bytes).hexdigest()
    # Validate both the source identity and bounded cursor on the issued next
    # anchor. Candidate was checked against the in-process projection above.
    series.check_continuation(enabled=True, root_anchor_bytes=root_anchor_bytes,
        root_anchor_sha256=root_anchor_sha256, previous_anchor_bytes=pins["anchor_bytes"],
        previous_anchor_sha256=pins["anchor_sha256"], next_anchor_bytes=next_bytes,
        next_anchor_sha256=next_sha, expected_candidate_cursor=candidate)
    root = _directory(audit_root)
    lock = root / V3_LOCK_FILENAME
    if os.path.lexists(lock):
        _regular(lock)
    return root, lock, body, now, tasks, types, next_sha


def _v3_intended(target, expected_previous_sha256, root_anchor_sha256, previous_anchor_sha256,
                 body, now, tasks, types, next_sha, pins):
    revision = 1
    if os.path.lexists(target):
        _regular(target)
        with target.open("rb") as stream:
            old = stream.read(MAX_BYTES + 1)
        if expected_previous_sha256 is None or hashlib.sha256(old).hexdigest() != expected_previous_sha256:
            raise ValueError()
        doc = _v3_structure(old)
        required = {"root_anchor_sha256": root_anchor_sha256,
                    **{key: pins[key] for key in ("policy_sha256", "roster_sha256", "public_policy_sha256")}}
        if (any(doc[key] != value for key, value in required.items())
                or doc["window_anchor_sha256"] != previous_anchor_sha256
                or doc["next_anchor_sha256"] != pins["anchor_sha256"] or doc["revision"] >= _MAX_REVISION):
            raise ValueError()
        _body(doc["body"], tasks, types)
        revision = doc["revision"] + 1
    elif expected_previous_sha256 is not None:
        raise ValueError()
    doc = {"schema": V3_SCHEMA, "revision": revision, "authority": "none", "coverage": "last_delta_only",
           "as_of_utc": now.isoformat(), "root_anchor_sha256": root_anchor_sha256,
           "policy_sha256": pins["policy_sha256"], "roster_sha256": pins["roster_sha256"],
           "public_policy_sha256": pins["public_policy_sha256"], "window_anchor_sha256": pins["anchor_sha256"],
           "next_anchor_sha256": next_sha, "body_sha256": hashlib.sha256(_json(body)).hexdigest(), "body": body}
    payload = _json(doc)
    if len(payload) > MAX_BYTES:
        raise ValueError()
    return payload


def _v3_build(audit_root, snapshot_result, *, as_of, enabled, expected_previous_sha256,
              root_anchor_bytes, root_anchor_sha256, previous_anchor_bytes, previous_anchor_sha256,
              expected_previous_candidate, pins):
    state = _v3_attempt(audit_root, snapshot_result, as_of=as_of, enabled=enabled,
        expected_previous_sha256=expected_previous_sha256, root_anchor_bytes=root_anchor_bytes,
        root_anchor_sha256=root_anchor_sha256, previous_anchor_bytes=previous_anchor_bytes,
        previous_anchor_sha256=previous_anchor_sha256, expected_previous_candidate=expected_previous_candidate,
        pins=pins)
    root, lock, body, now, tasks, types, next_sha = state
    payload = _v3_intended(root / V3_FILENAME, expected_previous_sha256, root_anchor_sha256,
                           previous_anchor_sha256, body, now, tasks, types, next_sha, pins)
    return root, lock, payload


def prepare_status_attempt_v3(audit_root, snapshot_result, *, as_of, enabled=False,
        expected_previous_sha256=_UNSET, root_anchor_bytes=None, root_anchor_sha256=None,
        previous_anchor_bytes=None, previous_anchor_sha256=None, expected_previous_candidate=None,
        timeout_seconds=0.2, **pins):
    """Return the exact v3 attempt digest; lock slot may be created, no publication write."""
    try:
        if enabled is not True:
            raise ValueError()
        root = _directory(audit_root)
        lock = root / V3_LOCK_FILENAME
        if os.path.lexists(lock):
            _regular(lock)
        with reader_slot(lock, timeout_seconds=timeout_seconds):
            _regular(lock)
            _, _, payload = _v3_build(audit_root, snapshot_result, as_of=as_of, enabled=enabled,
                expected_previous_sha256=expected_previous_sha256, root_anchor_bytes=root_anchor_bytes,
                root_anchor_sha256=root_anchor_sha256, previous_anchor_bytes=previous_anchor_bytes,
                previous_anchor_sha256=previous_anchor_sha256,
                expected_previous_candidate=expected_previous_candidate, pins=pins)
            return hashlib.sha256(payload).hexdigest()
    except Exception:
        raise PublicStatusRefused("public_status_refused") from None


def publish_status_v3(audit_root, snapshot_result, *, as_of, attempt_sha256, enabled=False,
        expected_previous_sha256=_UNSET, root_anchor_bytes=None, root_anchor_sha256=None,
        previous_anchor_bytes=None, previous_anchor_sha256=None, expected_previous_candidate=None,
        timeout_seconds=0.2, **pins):
    """Publish only a prepared v3 attempt; an exception can mean the replace landed."""
    stage = None
    owned = False
    try:
        if enabled is not True or not _digest(attempt_sha256):
            raise ValueError()
        root = _directory(audit_root)
        lock = root / V3_LOCK_FILENAME
        if os.path.lexists(lock):
            _regular(lock)
        with reader_slot(lock, timeout_seconds=timeout_seconds):
            _regular(lock)
            _, _, payload = _v3_build(audit_root, snapshot_result, as_of=as_of, enabled=enabled,
                expected_previous_sha256=expected_previous_sha256, root_anchor_bytes=root_anchor_bytes,
                root_anchor_sha256=root_anchor_sha256, previous_anchor_bytes=previous_anchor_bytes,
                previous_anchor_sha256=previous_anchor_sha256,
                expected_previous_candidate=expected_previous_candidate, pins=pins)
            if hashlib.sha256(payload).hexdigest() != attempt_sha256:
                raise ValueError()
            stage = root / (".public-status-v3-" + secrets.token_hex(16) + ".tmp")
            with stage.open("xb") as stream:
                owned = True
                if stream.write(payload) != len(payload):
                    raise OSError("short_write")
                stream.flush()
                os.fsync(stream.fileno())
            target = root / V3_FILENAME
            if os.path.lexists(target):
                _regular(target)
            os.replace(stage, target)
            owned = False
            return attempt_sha256
    except Exception:
        raise PublicStatusRefused("public_status_refused") from None
    finally:
        if owned:
            try:
                stage.unlink()
            except OSError:
                pass


def recover_status_attempt_v3(audit_root, *, prior_sha256, attempt_sha256, enabled=False, timeout_seconds=0.2):
    """Resolve only the caller's prepared attempt versus its exact prior; never retry or adopt another value."""
    try:
        if (enabled is not True or not _digest(attempt_sha256)
                or (prior_sha256 is not None and not _digest(prior_sha256)) or prior_sha256 == attempt_sha256):
            raise ValueError()
        root = _directory(audit_root)
        lock, target = root / V3_LOCK_FILENAME, root / V3_FILENAME
        if os.path.lexists(lock):
            _regular(lock)
        with reader_slot(lock, timeout_seconds=timeout_seconds):
            _regular(lock)
            if not os.path.lexists(target):
                if prior_sha256 is None:
                    return "unchanged"
                raise ValueError()
            _regular(target)
            with target.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError()
            present = hashlib.sha256(raw).hexdigest()
            if present == attempt_sha256:
                return "landed"
            if present == prior_sha256:
                return "unchanged"
            raise ValueError()
    except Exception:
        raise PublicStatusRefused("public_status_refused") from None


def read_status_v3(audit_root, *, lane, agent_uuid, as_of, max_age_seconds, enabled=False, **pins):
    """Read v3 against the original fixed root, not a changing window anchor."""
    try:
        if enabled is not True or type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 900:
            raise ValueError()
        roster, tasks, types = _pins(**pins)
        if (not isinstance(lane, str) or lane not in roster or not isinstance(agent_uuid, str)
                or roster[lane].lower() != agent_uuid.lower()):
            raise ValueError()
        now = _instant(as_of)
        path = _directory(audit_root) / V3_FILENAME
        _regular(path)
        with path.open("rb") as stream:
            doc = _v3_structure(stream.read(MAX_BYTES + 1))
        if (doc["root_anchor_sha256"] != pins["anchor_sha256"]
                or any(doc[name] != pins[name] for name in ("policy_sha256", "roster_sha256", "public_policy_sha256"))):
            raise ValueError()
        age = (now - _instant(datetime.fromisoformat(doc["as_of_utc"]))).total_seconds()
        if age < 0 or age > max_age_seconds:
            raise ValueError()
        body = _body(doc["body"], tasks, types)
        return _json({"schema": "wd.mind-public-session.v1", "authority": "none", "coverage": "last_delta_only",
                      "lane": lane, "identity_verification": "caller_declared_not_authenticated",
                      "as_of_utc": now.isoformat(), "publication_age_seconds": age,
                      "source_freshness": "UNKNOWN", "records": body["records"]})
    except Exception:
        raise PublicStatusRefused("public_status_refused") from None
