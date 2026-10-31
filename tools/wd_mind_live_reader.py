# SPDX-License-Identifier: BUSL-1.1
"""Read-only, metadata-only candidate boundary for a Mind snapshot.

This module does not start a producer, authenticate a lane, verify event origin,
or grant access to a bridge or a gate. Callers must bind those separately.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from pathlib import Path
import re

from tools import wd_bridge_mind as mind


LANES = frozenset({"codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"})
HEX = re.compile(r"[0-9a-f]{64}\Z")
GENERATION = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
MAX_AGE = timedelta(minutes=10)


class LiveReadRefused(ValueError):
    """A safe fixed refusal; never includes snapshot contents or local paths."""


def read_current(*, path: Path, audit_root: Path, expected_digest: str, expected_log: str,
                 expected_generation: str, lane: str, now: datetime) -> dict:
    """Verify one explicitly pinned snapshot and return allowlisted metadata only.

    ``expected_digest`` is the canonical snapshot digest, not the hash of the
    enclosing publication file. The supplied clock and lane are assertions by
    the caller, not evidence of a trusted clock or authenticated process.
    """
    if (not isinstance(lane, str) or lane not in LANES
            or not isinstance(expected_digest, str) or not HEX.fullmatch(expected_digest)
            or not isinstance(expected_generation, str) or not GENERATION.fullmatch(expected_generation)
            or not isinstance(expected_log, str) or not expected_log
            or not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None):
        raise LiveReadRefused("invalid_context")
    try:
        value = mind.read_snapshot(Path(path), audit_root=Path(audit_root))
        source = value["source"]
        digest = hashlib.sha256(mind.canonical_bytes(value)).hexdigest()
        as_of = datetime.fromisoformat(source["as_of_utc"].replace("Z", "+00:00"))
        age = now.astimezone(timezone.utc) - as_of.astimezone(timezone.utc)
        ref = source["last_event_ref"]
        if (not hmac.compare_digest(digest, expected_digest) or source["log_identity"] != expected_log
                or source["generation"] != expected_generation or source["blocked"] is not None
                or source["coverage"] != "complete" or source["event_count"] < 1
                or not isinstance(ref, str) or not HEX.fullmatch(ref)
                or age < timedelta(0) or age > MAX_AGE):
            raise LiveReadRefused("snapshot_not_current")
    except LiveReadRefused:
        raise
    except (mind.ProjectionError, KeyError, TypeError, ValueError, OverflowError):
        raise LiveReadRefused("snapshot_unavailable") from None
    return {"lane": lane, "as_of_utc": source["as_of_utc"], "generation": source["generation"],
            "snapshot_sha256": digest, "last_event_ref": ref, "event_count": source["event_count"]}
