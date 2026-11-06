"""Default-off synthetic status adapter; neither an installed nor a live route.

The caller must provide an owned, protected synthetic audit root and separately
trusted expected hashes and their pairing. Directory names and hashes do not
authenticate inputs, prove data synthetic, freshness, ACLs or same-user safety.
No caller, scheduler, bridge source, agent, or service is authenticated or run.
Exclusive output prevents overwrite, not hostile directory replacement or ABA.
If a write or later read fails, an incomplete artifact may remain; no cleanup.
"""
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat

from tools import wd_mind_status_reader_v3 as reader


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_OUTPUT = re.compile(r"synthetic-[a-z0-9_-]+\.json\Z")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class AdapterRefused(ValueError):
    """Fixed refusal without input paths or data."""


def _root(value):
    path = Path(value)
    if (not path.is_absolute() or ".." in path.parts or path.name != "synthetic-mind-v3"
            or ".codex-audit" not in path.parts):
        raise ValueError()
    for item in (path, *path.parents):
        info = os.lstat(item)
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            raise ValueError()
    return path


def publish_and_read(*, synthetic_root, output_name, projection_bytes, expected_projection_sha256,
                     descriptor_path, expected_descriptor_sha256, approval_bytes,
                     expected_approval_sha256, enabled=False):
    """Materialize and re-read exactly one caller-pinned synthetic projection."""
    if enabled is False:
        return {"status": "OFF"}
    try:
        if enabled is not True:
            raise ValueError()
        root = _root(synthetic_root)
        if type(output_name) is not str or _OUTPUT.fullmatch(output_name) is None:
            raise ValueError()
        descriptor = Path(descriptor_path)
        if not descriptor.is_absolute() or descriptor.parent != root or descriptor.name == output_name:
            raise ValueError()
        for value in (expected_projection_sha256, expected_descriptor_sha256, expected_approval_sha256):
            if type(value) is not str or _HEX.fullmatch(value) is None:
                raise ValueError()
        if (type(projection_bytes) is not bytes or not 1 <= len(projection_bytes) <= reader.MAX_BODY_BYTES
                or not hmac.compare_digest(hashlib.sha256(projection_bytes).hexdigest(), expected_projection_sha256)):
            raise ValueError()
        descriptor_bytes = reader._read(descriptor, expected_descriptor_sha256, reader.MAX_DESCRIPTOR_BYTES)
        reader.projection.validate_public_approval(descriptor_bytes, expected_descriptor_sha256,
                                                   approval_bytes, expected_approval_sha256)
        candidate = reader.session._object(projection_bytes)
        body = reader._canonical({"schema": "wd.mind-status-metadata.v1", "authority": "none",
            "scope": "in_memory_metadata_not_gate", "freshness": "UNKNOWN", "records": candidate["records"]})
        verified = reader.projection.project_status(enabled=True, body_bytes=body,
            body_sha256=hashlib.sha256(body).hexdigest(), descriptor_bytes=descriptor_bytes,
            descriptor_sha256=expected_descriptor_sha256, approval_bytes=approval_bytes,
            expected_approval_sha256=expected_approval_sha256)
        if not hmac.compare_digest(reader._canonical(verified), projection_bytes):
            raise ValueError()
        target = root / output_name
        with target.open("xb") as stream:
            if stream.write(projection_bytes) != len(projection_bytes):
                raise OSError()
            stream.flush()
            os.fsync(stream.fileno())
        result = reader.read_projection(enabled=True, projection_path=target,
            expected_projection_sha256=expected_projection_sha256, descriptor_path=descriptor,
            expected_descriptor_sha256=expected_descriptor_sha256, approval_bytes=approval_bytes,
            expected_approval_sha256=expected_approval_sha256)
        return {"sha256": hashlib.sha256(projection_bytes).hexdigest(), "projection": result}
    except Exception:
        pass
    raise AdapterRefused("adapter_refused") from None
