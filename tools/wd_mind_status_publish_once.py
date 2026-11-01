"""Default-off one-shot status for caller-supplied snapshot bytes.

The caller must independently authenticate the content hash, source, policies,
approval from trusted config, and the policy/projection pairing; choosing all
pins locally does not establish their origin. The caller must also authenticate
and separately private audit directory. Its name and caller assertion are not
an ACL or an isolation guarantee. The intermediate v2 publication and raw
snapshot are private, never reader output. Exclusive creation prevents overwriting an existing
name; it does NOT make a file immutable. This prototype assumes the directory
and snapshot stay unchanged through the call. Same-user replacement, ABA,
source authenticity, live freshness, completeness and crash durability are not
proved. The caller is responsible for ensuring source separation from the live
bridge; file paths do not establish origin or rule out hard links. Caller-supplied
bytes are not proved synthetic or safe to retain. No cursor is committed,
and nothing is scheduled, installed, activated, or retried. Raw snapshot and
generation files may remain in the caller-controlled audit directory after
success, refusal, exception, or crash. There is no automatic cleanup.
The optional caller-anchored variant consumes a prepared private snapshot and
caller-pinned anchor and generation. Pin matches do not authenticate a source;
same-user replacement and races across the separate file reads remain possible.
"""
from dataclasses import asdict
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import status_metadata_producer as metadata
from tools import wd_mind_public_session as session
from tools import wd_mind_status_projection_v3 as projection
from waggledance.core.bridge_log_reader import BridgeCursor, _file_identity

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_KEYS = frozenset({"private_audit_root", "private_storage_assumed", "snapshot_root", "snapshot_bytes", "expected_snapshot_sha256",
                   "policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256", "public_policy_bytes",
                   "public_policy_sha256", "approval_bytes", "expected_approval_sha256", "as_of",
                   "expected_previous_sha256"})
_CALLER_ANCHOR_KEYS = frozenset({"anchor_bytes", "expected_anchor_sha256", "expected_generation_sha256"})


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _read_exact(path, expected_sha, budget):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError()
    with path.open("rb") as stream:
        raw = stream.read(budget + 1)
    if len(raw) > budget or not hmac.compare_digest(_sha(raw), expected_sha):
        raise ValueError()
    return raw


def _preflight_previous(audit, prior):
    """Advisory status CAS before raw writes; a later racing CAS still decides."""
    lock, target = audit / session.LOCK_FILENAME, audit / session.FILENAME
    if os.path.lexists(lock):
        session._regular(lock)
    with session.reader_slot(lock, timeout_seconds=0.2):
        session._regular(lock)
        if not os.path.lexists(target):
            if prior is not None:
                raise ValueError()
            return
        if prior is None:
            raise ValueError()
        session._structure(_read_exact(target, prior, session.MAX_BYTES))


def _prepare(request):
    if type(request) is not dict or set(request) not in (_KEYS, _KEYS | _CALLER_ANCHOR_KEYS):
        raise ValueError()
    caller_anchored = "anchor_bytes" in request
    projection.validate_public_approval(request["public_policy_bytes"], request["public_policy_sha256"],
                                        request["approval_bytes"], request["expected_approval_sha256"])
    data, expected = request["snapshot_bytes"], request["expected_snapshot_sha256"]
    if (type(data) is not bytes or len(data) > snapshot.MAX_BYTES or (data and not data.endswith(b"\n"))
            or data.count(b"\n") > snapshot.MAX_ROWS or type(expected) is not str
            or _HEX.fullmatch(expected) is None or not hmac.compare_digest(_sha(data), expected)):
        raise ValueError()
    tasks, _ = adapter.strict_pins(request["policy_bytes"], request["policy_sha256"],
                                   request["roster_bytes"], request["roster_sha256"])
    adapter.public_status_policy(request["public_policy_bytes"], request["public_policy_sha256"], tasks)
    session._instant(request["as_of"])
    prior = request["expected_previous_sha256"]
    if prior is not None and not session._digest(prior):
        raise ValueError()
    if request["private_storage_assumed"] is not True:
        raise ValueError()
    audit = session._directory(request["private_audit_root"])
    if audit.parent.name != "mind-status-private":
        raise ValueError()
    root = Path(request["snapshot_root"])
    if not root.is_absolute() or not os.path.samefile(root.parent, audit):
        raise ValueError()
    info = os.lstat(root)
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError()
    _preflight_previous(audit, prior)
    if caller_anchored:
        anchor = request["anchor_bytes"]
        anchor_sha = request["expected_anchor_sha256"]
        generation_sha = request["expected_generation_sha256"]
        if type(generation_sha) is not str or _HEX.fullmatch(generation_sha) is None:
            raise ValueError()
        session._pins(anchor, anchor_sha, request["policy_bytes"], request["policy_sha256"],
                      request["roster_bytes"], request["roster_sha256"],
                      request["public_policy_bytes"], request["public_policy_sha256"])
        parsed_anchor = session._object(anchor)
        cursor_data = parsed_anchor["cursor_start"]
        if (type(cursor_data) is not dict or set(cursor_data) != {"offset", "file_identity", "generation"}
                or type(cursor_data["offset"]) is not int or cursor_data["offset"] != 0
                or type(cursor_data["file_identity"]) is not str
                or type(cursor_data["generation"]) is not str
                or type(parsed_anchor["source_path"]) is not str
                or type(parsed_anchor["generation_path"]) is not str):
            raise ValueError()
        source, gen = Path(parsed_anchor["source_path"]), Path(parsed_anchor["generation_path"])
        if (not source.is_absolute() or not gen.is_absolute() or ".." in source.parts or ".." in gen.parts
                or source == gen or not os.path.samefile(source.parent, root)
                or not os.path.samefile(gen.parent, root) or os.path.samefile(source, gen)):
            raise ValueError()
        cursor = BridgeCursor(**cursor_data)
        gen_data = _read_exact(gen, generation_sha, 16384)
        if _read_exact(source, expected, snapshot.MAX_BYTES) != data:
            raise ValueError()
    else:
        nonce = secrets.token_hex(16)
        generation = "synthetic-" + nonce
        source, gen = root / (nonce + ".jsonl"), root / (nonce + ".generation.json")
        gen_data = _json({"generation": generation})
        with gen.open("xb") as stream:
            if stream.write(gen_data) != len(gen_data):
                raise OSError()
            stream.flush()
            os.fsync(stream.fileno())
        with source.open("x+b") as stream:
            cursor = BridgeCursor(0, _file_identity(stream), generation)
            if stream.write(data) != len(data):
                raise OSError()
            stream.flush()
            os.fsync(stream.fileno())
        anchor = _json({"schema": "wd.q4-source-anchor.v1", "source_path": str(source), "generation_path": str(gen),
                        "cursor_start": asdict(cursor), "policy_sha256": request["policy_sha256"],
                        "roster_sha256": request["roster_sha256"], "status_mode": "public-v1",
                        "public_policy_sha256": request["public_policy_sha256"]})
        anchor_sha = _sha(anchor)
    _read_exact(source, expected, snapshot.MAX_BYTES)
    _read_exact(gen, _sha(gen_data), 16384)
    pins = {"anchor_bytes": anchor, "anchor_sha256": anchor_sha,
            **{key: request[key] for key in ("policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256",
                                               "public_policy_bytes", "public_policy_sha256")}}
    result = snapshot.read_anchored_delta(source, cursor, tasks, generation_path=gen,
        max_bytes=max(1, len(data)), max_rows=max(1, data.count(b"\n")), **pins)
    body = metadata.build_status(result, enabled=True, **pins)
    body_sha = _sha(body)
    public = projection.project_status(enabled=True, body_bytes=body, body_sha256=body_sha,
        descriptor_bytes=request["public_policy_bytes"], descriptor_sha256=request["public_policy_sha256"],
        approval_bytes=request["approval_bytes"], expected_approval_sha256=request["expected_approval_sha256"])
    _read_exact(source, expected, snapshot.MAX_BYTES)
    _read_exact(gen, _sha(gen_data), 16384)
    attempt = session.prepare_status_attempt(audit, result, enabled=True, as_of=request["as_of"],
                                             expected_previous_sha256=prior, **pins)
    _read_exact(source, expected, snapshot.MAX_BYTES)
    _read_exact(gen, _sha(gen_data), 16384)
    return audit, result, pins, prior, attempt, public, body_sha


def publish_once(*, enabled=False, request=None):
    """One caller-supplied snapshot attempt; UNKNOWN means never retry blindly."""
    if enabled is False:
        return {"status": "OFF"}
    if enabled is not True:
        return {"status": "REFUSED"}
    try:
        audit, result, pins, prior, attempt, public, body_sha = _prepare(request)
    except Exception:
        return {"status": "REFUSED", "private_artifacts": "MAY_REMAIN"}
    try:
        published = session.publish_status(audit, result, enabled=True, as_of=request["as_of"],
            expected_previous_sha256=prior, attempt_sha256=attempt, **pins)
        if published != attempt:
            return {"status": "UNKNOWN", "private_artifacts": "MAY_REMAIN"}
        raw = _read_exact(audit / session.FILENAME, attempt, session.MAX_BYTES)
        document = session._structure(raw)
        if (document["body_sha256"] != body_sha
                or any(document[name] != pins[name] for name in session._PINS)):
            return {"status": "UNKNOWN", "private_artifacts": "MAY_REMAIN"}
        return {"projection": public, "private_artifacts": "RETAINED"}
    except Exception:
        return {"status": "UNKNOWN", "private_artifacts": "MAY_REMAIN"}
