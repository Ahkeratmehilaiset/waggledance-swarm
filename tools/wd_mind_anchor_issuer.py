"""Default-off, in-memory construction of a public-v1 Q4 source anchor.

This module does not read or authenticate a source, sidecar, cursor, policy,
roster or their owners. All inputs and their pins must be independently
authenticated by the caller. Path checks are lexical only: they do not resolve
symlinks, hard links, junctions, short names or other filesystem aliases. The
caller must establish path identity and protect inputs separately. The returned
hash establishes byte integrity, not authorization, provenance or freshness.
Nothing is written, deployed or published here.
"""

import hashlib
import os
from dataclasses import asdict

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import shared_capture_adapter as adapter
from waggledance.core.bridge_log_reader import BridgeCursor, _validate_cursor


def _path(value):
    if (type(value) is not str or not value or not os.path.isabs(value)
            or os.path.normpath(value) != value or "\x00" in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("anchor_input_invalid")
    return value


def issue_anchor(*, enabled=False, source_path, generation_path, cursor,
                 policy_bytes, policy_sha256, roster_bytes, roster_sha256,
                 public_policy_bytes, public_policy_sha256):
    """Return canonical public anchor bytes and SHA256; never publish or read paths."""
    try:
        if enabled is not True:
            raise ValueError()
        source = _path(source_path)
        generation = _path(generation_path)
        if os.path.normcase(source) == os.path.normcase(generation):
            raise ValueError()
        if (type(cursor) is not BridgeCursor or _validate_cursor(cursor) is not None
                or cursor.offset >= 2**63
                or cursor.generation is None
                or adapter._IDENTITY.fullmatch(cursor.file_identity) is None
                or adapter._GENERATION.fullmatch(cursor.generation) is None):
            raise ValueError()
        tasks, _ = adapter.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
        adapter.public_status_policy(public_policy_bytes, public_policy_sha256, tasks)
        document = {"schema": "wd.q4-source-anchor.v1", "source_path": source,
                    "generation_path": generation, "cursor_start": asdict(cursor),
                    "policy_sha256": policy_sha256, "roster_sha256": roster_sha256,
                    "status_mode": "public-v1", "public_policy_sha256": public_policy_sha256}
        raw = snapshot._canonical(document)
        if len(raw) > snapshot._ANCHOR_MAX_BYTES:
            raise ValueError()
        return raw, hashlib.sha256(raw).hexdigest()
    except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
        raise ValueError("anchor_input_invalid") from None
