"""Inactive read-only public metadata reader for two independently pinned files.

The caller independently authenticates all expected hashes and their pairing,
including the approval from a trusted config; a caller choosing all pins proves
nothing about provenance. The caller approves task-label disclosure,
and protects paths against hostile replacement / TOCTOU for the whole call.
This module does not authenticate any lane, publisher or source and does not
prove source freshness or coverage. Hashes verify bytes, not provenance. The
caller also owns the body/descriptor pairing and safe handling of traceback
frame locals. No startup, broker, sharing or producer is activated here.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat

from tools import wd_mind_status_projection_v3 as projection
from tools import wd_mind_public_session as session

MAX_BODY_BYTES = 1024 * 1024
MAX_DESCRIPTOR_BYTES = 65536
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class ReaderRefused(ValueError):
    """Fixed refusal, without source paths, pins or input bytes."""


def _regular(info):
    return stat.S_ISREG(info.st_mode) and not getattr(info, "st_file_attributes", 0) & _REPARSE


def _read(path, expected, limit):
    if type(path) is not str and not isinstance(path, Path):
        raise ValueError()
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError()
    for item in (path, *path.parents):
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            raise ValueError()
        if item == path and (not _regular(info) or info.st_size > limit):
            raise ValueError()
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not _regular(before) or before.st_size > limit:
            raise ValueError()
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(data) > limit or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected)):
        raise ValueError()
    return data


def read_public(*, body_path, expected_body_sha256, descriptor_path, expected_descriptor_sha256,
                approval_bytes=None, expected_approval_sha256=None, enabled=False):
    """Read exactly the named pinned files and return the checked public projection."""
    if enabled is False:
        return {"status": "OFF"}
    result = None
    if enabled is True:
        try:
            if any(type(value) is not str or _HEX.fullmatch(value) is None
                   for value in (expected_body_sha256, expected_descriptor_sha256)):
                raise ValueError()
            descriptor = _read(descriptor_path, expected_descriptor_sha256, MAX_DESCRIPTOR_BYTES)
            projection.validate_public_approval(descriptor, expected_descriptor_sha256,
                                                approval_bytes, expected_approval_sha256)
            body = _read(body_path, expected_body_sha256, MAX_BODY_BYTES)
            result = projection.project_status(enabled=True, body_bytes=body, body_sha256=expected_body_sha256,
                                               descriptor_bytes=descriptor,
                                               descriptor_sha256=expected_descriptor_sha256,
                                               approval_bytes=approval_bytes,
                                               expected_approval_sha256=expected_approval_sha256)
        except Exception:
            pass
    if result is None:
        raise ReaderRefused("reader_refused")
    return result


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def read_projection(*, projection_path, expected_projection_sha256, descriptor_path,
                    expected_descriptor_sha256, approval_bytes=None, expected_approval_sha256=None, enabled=False):
    """Revalidate caller-pinned canonical v3 bytes against the separately pinned public policy.

    The body built in memory is only a schema-validation intermediate derived
    from the already caller-pinned projection; its digest is not a trust pin.
    No producer/source/identity or freshness claim follows from revalidation.
    """
    if enabled is False:
        return {"status": "OFF"}
    result = None
    if enabled is True:
        try:
            if any(type(value) is not str or _HEX.fullmatch(value) is None
                   for value in (expected_projection_sha256, expected_descriptor_sha256)):
                raise ValueError()
            descriptor = _read(descriptor_path, expected_descriptor_sha256, MAX_DESCRIPTOR_BYTES)
            projection.validate_public_approval(descriptor, expected_descriptor_sha256,
                                                approval_bytes, expected_approval_sha256)
            raw = _read(projection_path, expected_projection_sha256, MAX_BODY_BYTES)
            candidate = session._object(raw)
            body = _canonical({"schema": "wd.mind-status-metadata.v1", "authority": "none",
                               "scope": "in_memory_metadata_not_gate", "freshness": "UNKNOWN",
                               "records": candidate["records"]})
            verified = projection.project_status(enabled=True, body_bytes=body,
                body_sha256=hashlib.sha256(body).hexdigest(), descriptor_bytes=descriptor,
                descriptor_sha256=expected_descriptor_sha256, approval_bytes=approval_bytes,
                expected_approval_sha256=expected_approval_sha256)
            if not hmac.compare_digest(_canonical(verified), raw):
                raise ValueError()
            result = verified
        except Exception:
            pass
    if result is None:
        raise ReaderRefused("reader_refused")
    return result
