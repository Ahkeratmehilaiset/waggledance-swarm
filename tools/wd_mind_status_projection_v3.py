"""Inactive in-memory public metadata projection; no source, file, clock or process I/O.

All input digests must come from the caller's independent trust boundary. Hash
matching validates bytes, not their origin, the publisher, source freshness or
the caller's disclosure policy. In particular this is not a bridge reader,
semantic Mind, source-cursor checkpoint, broker or authentication mechanism.
The caller must independently ensure that the descriptor and approved task
labels contain no sensitive data and that the body was produced by an approved
source. The caller must independently bind its trusted approval, descriptor and
projection; a caller choosing every pin proves no provenance. Caller/debugger
traceback frame locals may contain input bytes.
"""
import hashlib
import hmac
import re

from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_projection as projection
from tools import wd_mind_public_session as session

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_DESCRIPTOR_LIMIT = 65536
_APPROVAL_LIMIT = 65536


class ProjectionRefused(ValueError):
    """Fixed public refusal, without input content or paths."""


def _pinned(raw, expected, limit):
    if (type(raw) is not bytes or not 1 <= len(raw) <= limit or type(expected) is not str
            or _HEX.fullmatch(expected) is None
            or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected)):
        raise ValueError()


def _approved_policy(descriptor_bytes, descriptor_sha256, approval_bytes, expected_approval_sha256):
    # The independently supplied approval pin is checked before parsing any approval.
    _pinned(approval_bytes, expected_approval_sha256, _APPROVAL_LIMIT)
    approval = session._object(approval_bytes)
    if (set(approval) != {"schema", "descriptor_sha256", "approved_tasks", "approved_types"}
            or approval["schema"] != "wd.mind-public-approval.v1"
            or type(approval["descriptor_sha256"]) is not str
            or _HEX.fullmatch(approval["descriptor_sha256"]) is None
            or type(approval["approved_tasks"]) is not list or type(approval["approved_types"]) is not list
            or not 1 <= len(approval["approved_tasks"]) <= 512
            or not 1 <= len(approval["approved_types"]) <= 64
            or any(type(item) is not str for item in approval["approved_tasks"] + approval["approved_types"])
            or len(set(approval["approved_tasks"])) != len(approval["approved_tasks"])
            or len(set(approval["approved_types"])) != len(approval["approved_types"])):
        raise ValueError()
    approved_tasks, approved_types = frozenset(approval["approved_tasks"]), frozenset(approval["approved_types"])
    if (not approved_types <= projection.ALLOWED_TYPES
            or any("review" in task or "rco" in task or adapter._REVIEW_TASK.search(task) or adapter._RCO_NAME.search(task)
                   or projection.UUID.search(task) is not None
                   or projection._task(task, approved_tasks) != task for task in approved_tasks)):
        raise ValueError()
    _pinned(descriptor_bytes, descriptor_sha256, _DESCRIPTOR_LIMIT)
    if not hmac.compare_digest(approval["descriptor_sha256"], descriptor_sha256):
        raise ValueError()
    tasks, types = adapter.public_status_policy(descriptor_bytes, descriptor_sha256, approved_tasks)
    if not types <= approved_types:
        raise ValueError()
    return tasks, types


def validate_public_approval(descriptor_bytes, descriptor_sha256, approval_bytes, expected_approval_sha256):
    """Check caller-pinned approval and descriptor; no origin authentication is implied."""
    result = None
    try:
        result = _approved_policy(descriptor_bytes, descriptor_sha256, approval_bytes, expected_approval_sha256)
    except Exception:
        pass
    if result is None:
        raise ProjectionRefused("projection_refused")
    return result


def _project(body_bytes, body_sha256, descriptor_bytes, descriptor_sha256, approval_bytes, expected_approval_sha256):
    tasks, types = _approved_policy(descriptor_bytes, descriptor_sha256, approval_bytes, expected_approval_sha256)
    _pinned(body_bytes, body_sha256, session.MAX_BYTES)
    body = session._body(session._object(body_bytes), tasks, types)
    # Construct every outbound field explicitly, never reuse arbitrary input objects.
    records = [{key: record[key] for key in ("time_utc", "agent", "type", "status", "task_id")}
               for record in body["records"]]
    return {"schema": "wd.mind-status-projection.v3", "authority": "none", "coverage": "UNKNOWN",
            "records": records, "trust": {"source_authentication": "UNKNOWN", "source_freshness": "UNKNOWN",
                                          "publisher_authentication": "UNKNOWN"}}


def project_status(*, body_bytes, body_sha256, descriptor_bytes, descriptor_sha256, approval_bytes=None,
                   expected_approval_sha256=None, enabled=False):
    """Return only pinned, approved metadata; refuse on every malformed input."""
    if enabled is False:
        return {"status": "OFF"}
    result = None
    if enabled is True:
        try:
            result = _project(body_bytes, body_sha256, descriptor_bytes, descriptor_sha256,
                              approval_bytes, expected_approval_sha256)
        except Exception:
            pass
    if result is None:
        raise ProjectionRefused("projection_refused")
    return result
