"""Default-off one-shot v3 public publisher; no scheduler, starter or live wiring.

Uses the v2 controller's mechanical helpers, never its transaction functions or
v2 state filenames. The v2 enable/mode/prior interface is retained, with required
WD_MIND_PUBLIC_{ROOT_ANCHOR,PREVIOUS_ANCHOR}_{PATH,SHA256} pins in addition to
the current ANCHOR, POLICY, ROSTER and PUBLIC_POLICY pins. The original root
stays fixed; PREVIOUS_ANCHOR names the previous publication's window anchor.
Pin files must be retained caller-owned copies, not v3 outputs: in particular,
copy the issued next anchor before using it as a later window's pinned input.

An exclusive private v3 pending record binds all six pin digests, the caller's
prior, prepared attempt, start/candidate cursors and intended next-anchor digest
BEFORE publish_status_v3 may replace the publication. The actual next anchor is
issued only after that exact attempt is known to have landed. Publish/recover
hold one bounded v3 transaction lock; session v3 locking is acquired inside it.
Recover never republishes: it accepts only the saved prior or saved attempt.
A landed pending record remains until a caller supplies the exact issued next
anchor and landed digest for a verified continuation.

Only fixed result fields leave stdout; cursors remain in private audit state.
Exceptions refuse without details. SystemExit/KeyboardInterrupt propagate and
leave any already-created pending record for explicit recovery. A torn stage
is not state. No power-loss durability, directory fsync, ACL, caller/source
authentication, hostile same-user race protection or source freshness is
claimed. Pins are caller assertions; each publication contains its latest delta
only. Neither this module nor its existence authorizes a live invocation.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys

from tools import wd_mind_public_publish_cli as mechanics

MAX_WINDOW_BYTES = 1024 * 1024
MAX_WINDOW_ROWS = 2000
LOCK_TIMEOUT_SECONDS = 2.0
LOCK_FILENAME = ".public-publish-v3.lock"
PENDING_FILENAME = ".public-publish-v3-pending.json"
NEXT_ANCHOR_FILENAME = "public-publish-v3-next-anchor.json"
_PENDING_SCHEMA = "wd.mind-public-publish-pending.v3"
_PIN_DIGESTS = ("anchor_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256",
                "root_anchor_sha256", "previous_anchor_sha256")
_STABLE_PINS = ("root_anchor_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256")
_PENDING_KEYS = frozenset({"schema", "prior_sha256", "attempt_sha256", "cursor_start",
                           "candidate_cursor", "next_anchor_sha256", "records", *_PIN_DIGESTS})
_REFUSAL = '{"error":"public_publish_v3_refused","authority":"none"}'


def _cursor(value):
    value = mechanics._cursor(value)
    if (value["offset"] > (1 << 63) - 1 or any(
            not value[key] or len(value[key]) > 16384 or "\x00" in value[key]
            for key in ("file_identity", "generation"))):
        raise ValueError()
    return value


def _digests(pins, lineage):
    return {**{key: pins[key] for key in _PIN_DIGESTS[:4]},
            "root_anchor_sha256": lineage["root_anchor_sha256"],
            "previous_anchor_sha256": lineage["previous_anchor_sha256"]}


def _next_data(anchor, candidate):
    return mechanics._canonical(dict(anchor, cursor_start=_cursor(candidate)))


def _pending(root):
    raw = mechanics._read_state(root, PENDING_FILENAME)
    if raw is None:
        return None
    saved = mechanics._strict(raw)
    if (type(saved) is not dict or set(saved) != _PENDING_KEYS or saved["schema"] != _PENDING_SCHEMA
            or (saved["prior_sha256"] is not None and not mechanics._is_hex(saved["prior_sha256"]))
            or not all(mechanics._is_hex(saved[key]) for key in
                       ("attempt_sha256", "next_anchor_sha256", *_PIN_DIGESTS))
            or saved["prior_sha256"] == saved["attempt_sha256"]
            or type(saved["records"]) is not int or not 0 <= saved["records"] <= MAX_WINDOW_ROWS):
        raise ValueError()
    start, end = _cursor(saved["cursor_start"]), _cursor(saved["candidate_cursor"])
    if (end["offset"] < start["offset"]
            or any(start[key] != end[key] for key in ("file_identity", "generation"))):
        raise ValueError()
    return saved


def _write_next_anchor(root, anchor, saved):
    data = _next_data(anchor, saved["candidate_cursor"])
    digest = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(digest, saved["next_anchor_sha256"]):
        raise ValueError()
    present = mechanics._read_state(root, NEXT_ANCHOR_FILENAME)
    if present != data:
        # Only the current window's issued anchor may be replaced. A third
        # state is not repaired or adopted, even when publication recovery lands.
        if present is not None:
            if present != mechanics._canonical(anchor):
                raise ValueError()
            (root / NEXT_ANCHOR_FILENAME).unlink()
        mechanics._create(root, NEXT_ANCHOR_FILENAME, data)
    return digest


def _recover(session, root, pins, lineage, anchor, source):
    saved = _pending(root)
    digests = _digests(pins, lineage)
    if (saved is None or any(not hmac.compare_digest(saved[key], digests[key]) for key in _PIN_DIGESTS)
            or not mechanics._same(saved["cursor_start"], anchor["cursor_start"])
            or hashlib.sha256(_next_data(anchor, saved["candidate_cursor"])).hexdigest()
               != saved["next_anchor_sha256"]):
        raise ValueError()
    state = session.recover_status_attempt_v3(
        root, prior_sha256=saved["prior_sha256"], attempt_sha256=saved["attempt_sha256"], enabled=True)
    if state == "unchanged":
        (root / PENDING_FILENAME).unlink()
        return "unchanged_recovered", None, None, None, None
    if state != "landed":
        raise ValueError()
    next_sha = _write_next_anchor(root, anchor, saved)
    return ("landed_recovered", saved["attempt_sha256"], saved["records"],
            mechanics._lag(source, saved["candidate_cursor"]["offset"]), next_sha)


def _continue(session, root, previous, pins, lineage, anchor):
    saved = _pending(root)
    issued = mechanics._read_state(root, NEXT_ANCHOR_FILENAME)
    if saved is not None:
        digests = _digests(pins, lineage)
        if (previous is None or not hmac.compare_digest(previous, saved["attempt_sha256"])
                or any(not hmac.compare_digest(saved[key], digests[key]) for key in _STABLE_PINS)
                or not hmac.compare_digest(lineage["previous_anchor_sha256"], saved["anchor_sha256"])
                or not hmac.compare_digest(pins["anchor_sha256"], saved["next_anchor_sha256"])
                or not mechanics._same(anchor["cursor_start"], saved["candidate_cursor"])
                or issued is None or hashlib.sha256(issued).hexdigest() != pins["anchor_sha256"]
                or not mechanics._same(mechanics._strict(issued), anchor)):
            raise ValueError()
        if session.recover_status_attempt_v3(
                root, prior_sha256=saved["prior_sha256"],
                attempt_sha256=saved["attempt_sha256"], enabled=True) != "landed":
            raise ValueError()
        # Never consume the only record capable of recreating a missing/stale
        # next anchor. The exact issued bytes and landed attempt were proved.
        (root / PENDING_FILENAME).unlink()
    elif previous is not None:
        if (issued is None or hashlib.sha256(issued).hexdigest() != pins["anchor_sha256"]
                or not mechanics._same(mechanics._strict(issued), anchor)):
            raise ValueError()
    elif issued is not None:
        raise ValueError()


def _publish(session, snapshot, adapter, producer, root, previous, pins, lineage,
             anchor, source, generation, cursor):
    _continue(session, root, previous, pins, lineage, anchor)
    tasks, _ = adapter.strict_pins(pins["policy_bytes"], pins["policy_sha256"],
                                   pins["roster_bytes"], pins["roster_sha256"])
    result = snapshot.read_anchored_delta(
        source, cursor, tasks, anchor_bytes=pins["anchor_bytes"], anchor_sha256=pins["anchor_sha256"],
        generation_path=generation, max_bytes=MAX_WINDOW_BYTES, max_rows=MAX_WINDOW_ROWS,
        policy_bytes=pins["policy_bytes"], policy_sha256=pins["policy_sha256"],
        roster_bytes=pins["roster_bytes"], roster_sha256=pins["roster_sha256"],
        public_policy_bytes=pins["public_policy_bytes"], public_policy_sha256=pins["public_policy_sha256"])
    records = json.loads(producer.build_status(result, enabled=True, **pins))["records"]
    candidate = _cursor(asdict(result["candidate_cursor"]))
    start = _cursor(asdict(cursor))
    now = datetime.now(timezone.utc)
    arguments = dict(pins, **lineage, as_of=now, enabled=True, expected_previous_sha256=previous,
                     expected_previous_candidate=start)
    attempt = session.prepare_status_attempt_v3(root, result, **arguments)
    if not mechanics._is_hex(attempt) or attempt == previous or not 0 <= len(records) <= MAX_WINDOW_ROWS:
        raise ValueError()
    saved = {"schema": _PENDING_SCHEMA, "prior_sha256": previous, "attempt_sha256": attempt,
             **_digests(pins, lineage), "cursor_start": start, "candidate_cursor": candidate,
             "next_anchor_sha256": hashlib.sha256(_next_data(anchor, candidate)).hexdigest(),
             "records": len(records)}
    # The mechanical helper writes/fsyncs a stage then exclusively hard-links
    # it into place. No publication write precedes the complete pending record.
    mechanics._create(root, PENDING_FILENAME, mechanics._canonical(saved))
    try:
        digest = session.publish_status_v3(root, result, attempt_sha256=attempt, **arguments)
        outcome = "published"
    except Exception:
        state = session.recover_status_attempt_v3(
            root, prior_sha256=previous, attempt_sha256=attempt, enabled=True)
        if state == "unchanged":
            (root / PENDING_FILENAME).unlink()
        if state != "landed":
            raise
        digest, outcome = attempt, "landed_after_ambiguous_failure"
    if not mechanics._is_hex(digest) or not hmac.compare_digest(digest, attempt):
        raise ValueError()
    next_sha = _write_next_anchor(root, anchor, saved)
    return outcome, digest, len(records), mechanics._lag(source, candidate["offset"]), next_sha


def _emit(outcome, digest, records, lag, next_anchor_sha256):
    data = {"schema": "wd.mind-public-publish-result.v3", "outcome": outcome,
            "publication_sha256": digest, "records": records, "lag_bytes_observed": lag,
            "next_anchor_sha256": next_anchor_sha256, "coverage": "last_delta_only", "authority": "none",
            "source_authentication": "UNKNOWN", "source_freshness": "UNKNOWN",
            "publisher_identity": "caller_declared_not_authenticated"}
    output = mechanics._canonical(data).decode("ascii") + "\n"
    written = sys.stdout.write(output)
    if type(written) is not int or written != len(output):
        raise OSError("stdout_incomplete")
    sys.stdout.flush()


def _refuse_state_pin_aliases(root, reader, session):
    outputs = {os.path.normcase(os.path.abspath(root / name)) for name in
               (LOCK_FILENAME, PENDING_FILENAME, NEXT_ANCHOR_FILENAME,
                session.V3_FILENAME, session.V3_LOCK_FILENAME)}
    for name in (*reader._PIN_LIMITS, "ROOT_ANCHOR", "PREVIOUS_ANCHOR"):
        supplied = reader._required("WD_MIND_PUBLIC_" + name + "_PATH")
        if os.path.normcase(os.path.abspath(supplied)) in outputs:
            raise ValueError()


def main(argv=None):
    try:
        if argv is None:
            argv = sys.argv[1:]
        if type(argv) is not list or argv or os.environ.get("WD_MIND_PUBLIC_PUBLISH_ENABLED") != "1":
            raise ValueError()
        mode = os.environ.get("WD_MIND_PUBLIC_PUBLISH_MODE")
        if mode not in (None, "recover"):
            raise ValueError()
        from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
        from sandbox.mind.step2 import shared_capture_adapter as adapter
        from sandbox.mind.step2 import status_metadata_producer as producer
        from sandbox.mind.step2.reader_slot import reader_slot
        from tools import wd_mind_public_session as session, wd_mind_public_session_cli as reader
        from tools import wd_mind_public_series as series

        previous = mechanics._previous() if mode is None else None
        root = session._directory(Path(reader._required("WD_MIND_PUBLIC_AUDIT_ROOT")))
        _refuse_state_pin_aliases(root, reader, session)
        pins = {}
        for name, limit in reader._PIN_LIMITS.items():
            raw, digest = reader._bounded_pin(name, limit)
            pins[name.lower() + "_bytes"], pins[name.lower() + "_sha256"] = raw, digest
        lineage = {}
        for name in ("ROOT_ANCHOR", "PREVIOUS_ANCHOR"):
            raw, digest = reader._bounded_pin(name, 16384)
            lineage[name.lower() + "_bytes"], lineage[name.lower() + "_sha256"] = raw, digest
        # Authenticate no caller: these checks prove only byte/pin and internal
        # consistency. They run BEFORE opening the anchored source log.
        session._pins(**pins)
        session._pins(**dict(pins, anchor_bytes=lineage["root_anchor_bytes"],
                             anchor_sha256=lineage["root_anchor_sha256"]))
        anchor, source, generation, cursor = mechanics._anchor_source(pins["anchor_bytes"])
        series.check_continuation(
            enabled=True, **lineage, next_anchor_bytes=pins["anchor_bytes"],
            next_anchor_sha256=pins["anchor_sha256"], expected_candidate_cursor=_cursor(asdict(cursor)))
        if previous is None and mode is None and (
                pins["anchor_sha256"] != lineage["root_anchor_sha256"]
                or lineage["previous_anchor_sha256"] != lineage["root_anchor_sha256"]):
            raise ValueError()
        lock = root / LOCK_FILENAME
        if os.path.lexists(lock):
            session._regular(lock)
        with reader_slot(lock, timeout_seconds=LOCK_TIMEOUT_SECONDS):
            session._regular(lock)
            if mode == "recover":
                emitted = _recover(session, root, pins, lineage, anchor, source)
            else:
                emitted = _publish(session, snapshot, adapter, producer, root, previous, pins, lineage,
                                   anchor, source, generation, cursor)
        _emit(*emitted)
        return 0
    except Exception:
        sys.stderr.write(_REFUSAL + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
