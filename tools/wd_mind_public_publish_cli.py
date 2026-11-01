"""Default-off one-shot public-status publisher; not a starter, scheduler, service or live route.

One run publishes the status of ONE bounded anchored delta for the existing reader
(tools/wd_mind_public_session_cli.py) and exits. It is enabled only by
WD_MIND_PUBLIC_PUBLISH_ENABLED=1 with no CLI arguments; the reader's
WD_MIND_PUBLIC_ENABLED never enables it. Inputs are the reader's own pin variables,
read with the reader's own bounded helper: WD_MIND_PUBLIC_AUDIT_ROOT and the pairs
WD_MIND_PUBLIC_{ANCHOR,POLICY,ROSTER,PUBLIC_POLICY}_{PATH,SHA256}, plus the
caller-owned prior publication digest WD_MIND_PUBLIC_PREVIOUS_SHA256 (64 lowercase
hex, or the literal none for the first publication; never inferred from the file).

The source log, generation sidecar and start cursor come ONLY from the pinned
anchor. The window (read_anchored_delta) must reach the end of the snapshot it
observes within MAX_WINDOW_BYTES/MAX_WINDOW_ROWS, otherwise the run refuses rather
than truncating or skipping. Then prepare_status_attempt and, BEFORE any publication
write, an exclusive fsynced recovery artifact (PENDING_FILENAME in the private audit
root: caller prior, attempt digest, the four pin digests, start and candidate cursor,
record count; no rows, messages or paths). Then publish_status bound to that attempt
and, after a publish failure, recover_status_attempt with the caller's own prior:
"landed" continues as landed_after_ambiguous_failure, "unchanged" removes the
artifact and refuses, anything else refuses and KEEPS the artifact.

Only after landed is established, the continuation anchor (NEXT_ANCHOR_FILENAME:
the pinned anchor with cursor_start = the window's candidate cursor) is written. A
later run with an artifact present must pin that landed digest as its prior and an
anchor starting exactly at the artifact's candidate cursor (a re-read or a skip is
refused); the artifact is consumed only then. With no artifact and a non-genesis
prior, the anchor must start at the issued continuation's cursor.

WD_MIND_PUBLIC_PUBLISH_MODE=recover (same switch and pins, no prior needed) is the
crash-equivalent restart: bound to the saved artifact and its pins it answers
landed_recovered (rewrites the identical continuation anchor, keeps the artifact,
publishes nothing, repeatable), unchanged_recovered (removes the artifact) or the
refusal (a third publication, no artifact, other pins).

stdout is one canonical JSON line with fixed keys: the publication digest (the
caller keeps it as the next prior), the record count, lag_bytes_observed (size of
the source file observed after the publish minus the window end; disclosure only, a
live writer can append at any time) and the continuation anchor digest. Every other
outcome is one fixed refusal on stderr and exit code 2; a refusal after a landed
publication (anchor write, lag or stdout failure) leaves the artifact for recover.

NOT established here: source or publisher authenticity (same-user writers are
indistinguishable; the publisher identity is caller declared), source freshness or
currentness after the read, completeness (each publication replaces the previous
one with the latest delta only), ACLs, or protection against same-user replacement
and TOCTOU of any path, including the artifact and the continuation anchor (the
anchor write is unlink-then-exclusive-create, not atomic; recover rewrites it).
Pins chosen by the caller prove no provenance.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys

MAX_WINDOW_BYTES = 1024 * 1024
MAX_WINDOW_ROWS = 2000
PENDING_FILENAME = ".public-publish-pending.json"
NEXT_ANCHOR_FILENAME = "public-publish-next-anchor.json"
_PENDING_SCHEMA = "wd.mind-public-publish-pending.v1"
_PIN_DIGESTS = ("anchor_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256")
_PENDING_KEYS = frozenset({"schema", "prior_sha256", "attempt_sha256", "cursor_start", "candidate_cursor",
                           "records", *_PIN_DIGESTS})
_STATE_LIMIT = 64 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_REFUSAL = '{"error":"public_publish_refused","authority":"none"}'


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _strict(raw):
    from sandbox.mind.step2 import shared_capture_adapter as adapter

    return json.loads(raw.decode("utf-8"), object_pairs_hook=adapter._strict_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _previous():
    value = os.environ.get("WD_MIND_PUBLIC_PREVIOUS_SHA256")
    if value == "none":
        return None
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError()
    return value


def _cursor(value):
    if (type(value) is not dict or set(value) != {"offset", "file_identity", "generation"}
            or type(value["offset"]) is not int or value["offset"] < 0
            or type(value["file_identity"]) is not str or type(value["generation"]) is not str):
        raise ValueError()
    return value


def _anchor_source(anchor_bytes):
    from waggledance.core.bridge_log_reader import BridgeCursor

    anchor = _strict(anchor_bytes)
    if type(anchor) is not dict:
        raise ValueError()
    source, generation, start = anchor.get("source_path"), anchor.get("generation_path"), anchor.get("cursor_start")
    if (type(source) is not str or type(generation) is not str or not Path(source).is_absolute()
            or not Path(generation).is_absolute()):
        raise ValueError()
    start = _cursor(start)
    return (anchor, Path(source), Path(generation),
            BridgeCursor(start["offset"], start["file_identity"], start["generation"]))


def _read_state(root, name):
    """Bytes of a private state file in the audit root, or None; never follows a link or reparse point."""
    from tools import wd_mind_public_session as session

    target = root / name
    if not os.path.lexists(target):
        return None
    session._regular(target)
    with target.open("rb") as stream:
        raw = stream.read(_STATE_LIMIT + 1)
    if len(raw) > _STATE_LIMIT:
        raise ValueError()
    return raw


def _create(target, data):
    with target.open("xb") as stream:
        if stream.write(data) != len(data):
            raise OSError("short_write")
        stream.flush()
        os.fsync(stream.fileno())


def _pending(root):
    raw = _read_state(root, PENDING_FILENAME)
    if raw is None:
        return None
    value = _strict(raw)
    if (type(value) is not dict or set(value) != _PENDING_KEYS or value["schema"] != _PENDING_SCHEMA
            or (value["prior_sha256"] is not None and not _is_hex(value["prior_sha256"]))
            or not all(_is_hex(value[name]) for name in ("attempt_sha256", *_PIN_DIGESTS))
            or type(value["records"]) is not int or value["records"] < 0):
        raise ValueError()
    _cursor(value["cursor_start"])
    _cursor(value["candidate_cursor"])
    return value


def _is_hex(value):
    return type(value) is str and _HEX.fullmatch(value) is not None


def _same(left, right):
    return _canonical(left) == _canonical(right)


def _write_next_anchor(root, anchor, candidate):
    """Continuation anchor = the pinned anchor with cursor_start moved to the landed window's candidate cursor.
    Idempotent: identical bytes are left alone; other bytes are replaced by unlink + exclusive create."""
    data = _canonical(dict(anchor, cursor_start=_cursor(candidate)))
    target = root / NEXT_ANCHOR_FILENAME
    present = _read_state(root, NEXT_ANCHOR_FILENAME)
    if present != data:
        if present is not None:
            target.unlink()
        _create(target, data)
    return hashlib.sha256(data).hexdigest()


def _lag(source, end):
    lag = os.stat(source).st_size - end
    if type(lag) is not int or lag < 0:
        raise ValueError()
    return lag


def _emit(outcome, digest, records, lag, next_anchor_sha256):
    output = json.dumps({"schema": "wd.mind-public-publish-result.v1", "outcome": outcome,
                         "publication_sha256": digest, "records": records, "lag_bytes_observed": lag,
                         "next_anchor_sha256": next_anchor_sha256,
                         "coverage": "last_delta_only", "authority": "none",
                         "source_authentication": "UNKNOWN", "source_freshness": "UNKNOWN",
                         "publisher_identity": "caller_declared_not_authenticated"},
                        sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    written = sys.stdout.write(output)
    if type(written) is not int or written != len(output):
        raise OSError("stdout_incomplete")
    sys.stdout.flush()


def _recover(session, root, pins, anchor, source):
    """Crash-equivalent restart bound to the saved artifact and the SAME pins; never publishes."""
    saved = _pending(root)
    if saved is None or not all(hmac.compare_digest(saved[name], pins[name]) for name in _PIN_DIGESTS):
        raise ValueError()
    if not _same(saved["cursor_start"], anchor["cursor_start"]):
        raise ValueError()
    state = session.recover_status_attempt(root, prior_sha256=saved["prior_sha256"],
                                           attempt_sha256=saved["attempt_sha256"], enabled=True)
    if state == "unchanged":
        (root / PENDING_FILENAME).unlink()
        _emit("unchanged_recovered", None, None, None, None)
        return
    if state != "landed":
        raise ValueError()
    next_sha = _write_next_anchor(root, anchor, saved["candidate_cursor"])
    _emit("landed_recovered", saved["attempt_sha256"], saved["records"],
          _lag(source, saved["candidate_cursor"]["offset"]), next_sha)


def _continue(session, root, previous, anchor):
    """A new window must continue the LANDED previous publication exactly: no re-read, no skip."""
    saved = _pending(root)
    if saved is not None:
        if (previous is None or not hmac.compare_digest(previous, saved["attempt_sha256"])
                or not _same(anchor["cursor_start"], saved["candidate_cursor"])):
            raise ValueError()
        if session.recover_status_attempt(root, prior_sha256=saved["prior_sha256"],
                                          attempt_sha256=saved["attempt_sha256"], enabled=True) != "landed":
            raise ValueError()
        (root / PENDING_FILENAME).unlink()
    elif previous is not None:
        issued = _read_state(root, NEXT_ANCHOR_FILENAME)
        if issued is None or not _same(anchor["cursor_start"], _strict(issued).get("cursor_start")):
            raise ValueError()


def main(argv=None):
    try:
        if argv is None:
            argv = sys.argv[1:]
        if argv or os.environ.get("WD_MIND_PUBLIC_PUBLISH_ENABLED") != "1":
            raise ValueError()
        mode = os.environ.get("WD_MIND_PUBLIC_PUBLISH_MODE")
        if mode not in (None, "recover"):
            raise ValueError()
        from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
        from sandbox.mind.step2 import shared_capture_adapter as adapter
        from sandbox.mind.step2 import status_metadata_producer as producer
        from tools import wd_mind_public_session as session
        from tools import wd_mind_public_session_cli as reader

        previous = _previous() if mode is None else None
        root = session._directory(Path(reader._required("WD_MIND_PUBLIC_AUDIT_ROOT")))
        pins = {}
        for name, limit in reader._PIN_LIMITS.items():
            data, digest = reader._bounded_pin(name, limit)
            pins[name.lower() + "_bytes"] = data
            pins[name.lower() + "_sha256"] = digest
        anchor, source, generation, cursor = _anchor_source(pins["anchor_bytes"])
        if mode == "recover":
            _recover(session, root, pins, anchor, source)
            return 0
        _continue(session, root, previous, anchor)
        tasks, _ = adapter.strict_pins(pins["policy_bytes"], pins["policy_sha256"], pins["roster_bytes"],
                                       pins["roster_sha256"])
        result = snapshot.read_anchored_delta(
            source, cursor, tasks, anchor_bytes=pins["anchor_bytes"], anchor_sha256=pins["anchor_sha256"],
            generation_path=generation, max_bytes=MAX_WINDOW_BYTES, max_rows=MAX_WINDOW_ROWS,
            policy_bytes=pins["policy_bytes"], policy_sha256=pins["policy_sha256"],
            roster_bytes=pins["roster_bytes"], roster_sha256=pins["roster_sha256"],
            public_policy_bytes=pins["public_policy_bytes"], public_policy_sha256=pins["public_policy_sha256"])
        records = json.loads(producer.build_status(result, enabled=True, **pins))["records"]
        candidate = asdict(result["candidate_cursor"])
        as_of = datetime.now(timezone.utc)
        attempt = session.prepare_status_attempt(root, result, as_of=as_of, enabled=True,
                                                 expected_previous_sha256=previous, **pins)
        # Durable BEFORE any publication write; exclusive, so a concurrent or unresolved attempt refuses here.
        _create(root / PENDING_FILENAME, _canonical(
            {"schema": _PENDING_SCHEMA, "prior_sha256": previous, "attempt_sha256": attempt,
             **{name: pins[name] for name in _PIN_DIGESTS}, "cursor_start": asdict(cursor),
             "candidate_cursor": candidate, "records": len(records)}))
        try:
            digest = session.publish_status(root, result, as_of=as_of, enabled=True,
                                            expected_previous_sha256=previous, attempt_sha256=attempt, **pins)
            outcome = "published"
        except Exception:
            # Bound to the caller's own prior and this attempt: never adopts a third publication.
            state = session.recover_status_attempt(root, prior_sha256=previous, attempt_sha256=attempt,
                                                   enabled=True)
            if state == "unchanged":
                (root / PENDING_FILENAME).unlink()
            if state != "landed":
                raise
            digest, outcome = attempt, "landed_after_ambiguous_failure"
        if not _is_hex(digest) or not hmac.compare_digest(digest, attempt):
            raise ValueError()
        next_sha = _write_next_anchor(root, anchor, candidate)
        _emit(outcome, digest, len(records), _lag(source, candidate["offset"]), next_sha)
        return 0
    except Exception:
        sys.stderr.write(_REFUSAL + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
