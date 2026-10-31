"""Inactive in-memory public metadata projection; no source, file, clock or process I/O.

Both input digests must come from the caller's independent trust boundary. Hash
matching validates bytes, not their origin, the publisher, source freshness or
the caller's disclosure policy. In particular this is not a bridge reader,
semantic Mind, source-cursor checkpoint, broker or authentication mechanism.
The caller must independently ensure that the descriptor and approved task
labels contain no sensitive data and that the body was produced by an approved
source. Caller/debugger traceback frame locals may contain input bytes.
"""
import hashlib
import hmac
import re

from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_projection as projection
from tools import wd_mind_public_session as session

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_DESCRIPTOR_LIMIT = 65536


class ProjectionRefused(ValueError):
    """Fixed public refusal, without input content or paths."""


def _pinned(raw, expected, limit):
    if (type(raw) is not bytes or not 1 <= len(raw) <= limit or type(expected) is not str
            or _HEX.fullmatch(expected) is None
            or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected)):
        raise ValueError()


def _project(body_bytes, body_sha256, descriptor_bytes, descriptor_sha256):
    _pinned(body_bytes, body_sha256, session.MAX_BYTES)
    _pinned(descriptor_bytes, descriptor_sha256, _DESCRIPTOR_LIMIT)
    descriptor = session._object(descriptor_bytes)
    tasks = descriptor.get("public_tasks")
    if type(tasks) is not list or any(type(task) is not str for task in tasks):
        raise ValueError()
    approved = frozenset(tasks)
    tasks, types = adapter.public_status_policy(descriptor_bytes, descriptor_sha256, approved)
    if any(projection._task(task, tasks) != task or projection.UUID.search(task) is not None for task in tasks):
        raise ValueError()
    body = session._body(session._object(body_bytes), tasks, types)
    # Construct every outbound field explicitly, never reuse arbitrary input objects.
    records = [{key: record[key] for key in ("time_utc", "agent", "type", "status", "task_id")}
               for record in body["records"]]
    return {"schema": "wd.mind-status-projection.v3", "authority": "none", "coverage": "UNKNOWN",
            "records": records, "trust": {"source_authentication": "UNKNOWN", "source_freshness": "UNKNOWN",
                                          "publisher_authentication": "UNKNOWN"}}


def project_status(*, body_bytes, body_sha256, descriptor_bytes, descriptor_sha256, enabled=False):
    """Return only pinned, approved metadata; refuse on every malformed input."""
    if enabled is False:
        return {"status": "OFF"}
    result = None
    if enabled is True:
        try:
            result = _project(body_bytes, body_sha256, descriptor_bytes, descriptor_sha256)
        except Exception:
            pass
    if result is None:
        raise ProjectionRefused("projection_refused")
    return result
