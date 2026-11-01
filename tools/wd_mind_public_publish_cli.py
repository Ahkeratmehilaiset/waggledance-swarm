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
than truncating or skipping; a longer delta needs a fresh caller-authenticated
anchor. Then prepare_status_attempt, publish_status bound to that attempt digest
and, after any publish failure, recover_status_attempt with the caller's own prior
digest: "landed" is reported as landed_after_ambiguous_failure, anything else is
the fixed refusal and nothing is repaired or retried.

stdout is one canonical JSON line with fixed keys: the publication digest (the
caller keeps it as the next prior), the record count and lag_bytes_observed (size
of the source file observed after the publish minus the window end; disclosure
only, a live writer can append at any time). Every other outcome is one fixed
refusal on stderr and exit code 2; a refusal can follow a landed publication only
if the lag measurement fails (the next run's prior-digest check still protects it).

NOT established here: source or publisher authenticity (same-user writers are
indistinguishable; the publisher identity is caller declared), source freshness or
currentness after the read, completeness (each publication replaces the previous
one with the latest delta only), ACLs, or protection against same-user replacement
and TOCTOU of any path. Pins chosen by the caller prove no provenance.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

MAX_WINDOW_BYTES = 1024 * 1024
MAX_WINDOW_ROWS = 2000
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_REFUSAL = '{"error":"public_publish_refused","authority":"none"}'


def _previous():
    value = os.environ.get("WD_MIND_PUBLIC_PREVIOUS_SHA256")
    if value == "none":
        return None
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError()
    return value


def _anchor_source(anchor_bytes):
    from sandbox.mind.step2 import shared_capture_adapter as adapter
    from waggledance.core.bridge_log_reader import BridgeCursor

    anchor = json.loads(anchor_bytes.decode("utf-8"), object_pairs_hook=adapter._strict_pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if type(anchor) is not dict or type(anchor.get("cursor_start")) is not dict:
        raise ValueError()
    source, generation, start = anchor.get("source_path"), anchor.get("generation_path"), anchor["cursor_start"]
    if (type(source) is not str or type(generation) is not str or not Path(source).is_absolute()
            or not Path(generation).is_absolute() or set(start) != {"offset", "file_identity", "generation"}
            or type(start["offset"]) is not int or start["offset"] < 0
            or type(start["file_identity"]) is not str or type(start["generation"]) is not str):
        raise ValueError()
    return Path(source), Path(generation), BridgeCursor(start["offset"], start["file_identity"], start["generation"])


def main(argv=None):
    try:
        if argv is None:
            argv = sys.argv[1:]
        if argv or os.environ.get("WD_MIND_PUBLIC_PUBLISH_ENABLED") != "1":
            raise ValueError()
        from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
        from sandbox.mind.step2 import shared_capture_adapter as adapter
        from sandbox.mind.step2 import status_metadata_producer as producer
        from tools import wd_mind_public_session as session
        from tools import wd_mind_public_session_cli as reader

        previous = _previous()
        audit_root = Path(reader._required("WD_MIND_PUBLIC_AUDIT_ROOT"))
        pins = {}
        for name, limit in reader._PIN_LIMITS.items():
            data, digest = reader._bounded_pin(name, limit)
            pins[name.lower() + "_bytes"] = data
            pins[name.lower() + "_sha256"] = digest
        source, generation, cursor = _anchor_source(pins["anchor_bytes"])
        tasks, _ = adapter.strict_pins(pins["policy_bytes"], pins["policy_sha256"], pins["roster_bytes"],
                                       pins["roster_sha256"])
        result = snapshot.read_anchored_delta(
            source, cursor, tasks, anchor_bytes=pins["anchor_bytes"], anchor_sha256=pins["anchor_sha256"],
            generation_path=generation, max_bytes=MAX_WINDOW_BYTES, max_rows=MAX_WINDOW_ROWS,
            policy_bytes=pins["policy_bytes"], policy_sha256=pins["policy_sha256"],
            roster_bytes=pins["roster_bytes"], roster_sha256=pins["roster_sha256"],
            public_policy_bytes=pins["public_policy_bytes"], public_policy_sha256=pins["public_policy_sha256"])
        records = json.loads(producer.build_status(result, enabled=True, **pins))["records"]
        as_of = datetime.now(timezone.utc)
        attempt = session.prepare_status_attempt(audit_root, result, as_of=as_of, enabled=True,
                                                 expected_previous_sha256=previous, **pins)
        try:
            digest = session.publish_status(audit_root, result, as_of=as_of, enabled=True,
                                            expected_previous_sha256=previous, attempt_sha256=attempt, **pins)
            outcome = "published"
        except Exception:
            # Bound to the caller's own prior and this attempt: never adopts a third publication.
            if session.recover_status_attempt(audit_root, prior_sha256=previous, attempt_sha256=attempt,
                                              enabled=True) != "landed":
                raise
            digest, outcome = attempt, "landed_after_ambiguous_failure"
        lag = os.stat(source).st_size - result["candidate_cursor"].offset
        if type(digest) is not str or not _HEX.fullmatch(digest) or lag < 0:
            raise ValueError()
        output = json.dumps({"schema": "wd.mind-public-publish-result.v1", "outcome": outcome,
                             "publication_sha256": digest, "records": len(records), "lag_bytes_observed": lag,
                             "coverage": "last_delta_only", "authority": "none",
                             "source_authentication": "UNKNOWN", "source_freshness": "UNKNOWN",
                             "publisher_identity": "caller_declared_not_authenticated"},
                            sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        written = sys.stdout.write(output)
        if type(written) is not int or written != len(output):
            raise OSError("stdout_incomplete")
        sys.stdout.flush()
        return 0
    except Exception:
        sys.stderr.write(_REFUSAL + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
