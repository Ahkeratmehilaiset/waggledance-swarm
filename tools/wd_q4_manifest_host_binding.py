"""Resolve fixed Q4 deployment pins from independently anchored manifest bytes.

This pure, opt-in resolver performs no filesystem access or execution.  It does
not verify the named files, the caller's identity, or protect against TOCTOU.
The manifest digest and source commit must come from an independent trusted
channel, not from the manifest or from an untrusted caller.
"""

import hashlib
import json
import re


_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_FIXED_PATHS = (
    "Invoke-WdQ4SessionCommand.ps1",
    "Invoke-WdQ4SessionHost.ps1",
    "WdQ4ProcessJob.ps1",
    "tools-bootstrap/sandbox/mind/step2/runtime_bootstrap.py",
    "tools-bootstrap/tools/wd_q4_session_outer_entry.py",
)
_MAX_MANIFEST_BYTES = 8 * 1024 * 1024


class ManifestBindingError(ValueError):
    """The externally anchored manifest cannot provide the fixed bindings."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _path_is_safe(path):
    if not isinstance(path, str) or not path or any(c in path for c in '\\:<>|"?*\x00'):
        return False
    parts = path.split('/')
    return all(part not in ('', '.', '..') and not part.endswith(('.', ' ')) and
               all(ord(char) >= 32 for char in part) for part in parts)


def resolve_manifest_host_binding(manifest_bytes, *, expected_manifest_sha256,
                                  expected_commit, enabled=False):
    """Return manifest-derived fixed-path SHA256 pins, or None while disabled."""
    if enabled is False:
        return None
    try:
        if enabled is not True or type(manifest_bytes) is not bytes or not 0 < len(manifest_bytes) <= _MAX_MANIFEST_BYTES:
            raise ValueError("input")
        if not isinstance(expected_manifest_sha256, str) or not _SHA256.fullmatch(expected_manifest_sha256):
            raise ValueError("anchor")
        if not isinstance(expected_commit, str) or not _COMMIT.fullmatch(expected_commit):
            raise ValueError("anchor")
        if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256.lower():
            raise ValueError("digest")
        document = json.loads(manifest_bytes.decode('utf-8-sig'), object_pairs_hook=_unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
        if (not isinstance(document, dict) or type(document.get('schema_version')) is not int or
                document['schema_version'] != 1 or document.get('source_commit') != expected_commit or
                not isinstance(document.get('files'), dict)):
            raise ValueError("schema")
        files = document['files']
        folded = set()
        for name in files:
            if not _path_is_safe(name) or name.casefold() in folded:
                raise ValueError("path")
            folded.add(name.casefold())
        result = {}
        for name in _FIXED_PATHS:
            value = files.get(name)
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError("pin")
            result[name] = value.lower()
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ManifestBindingError('q4_manifest_binding_refused') from None
