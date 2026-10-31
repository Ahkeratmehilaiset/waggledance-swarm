"""Default-off synthetic lane-to-Q4 seam; neither a CLI nor an installer.

The caller must independently authenticate the bundle, manifest and wrapper
pins and provide a separately trusted transport which executes that exact
wrapper, writes ``stdin_bytes`` only to its standard input, captures its
standard output and enforces its own process-tree and time bounds. This module
does not implement that transport, authenticate a lane, enable a producer,
prove privacy/freshness, or defeat same-user path replacement/TOCTOU. The
caller must obtain complete query bytes under its own bounded read policy;
this module never reads a stream. Never serialize query bytes, responses or
exception details into logs.
"""

import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat


class LaneLinkRefused(ValueError):
    """Fixed public refusal; no paths, query or backend details."""


_LANES = frozenset({"codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"})
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_PIN64 = re.compile(r"[0-9A-Fa-f]{64}\Z")
_TEXT = re.compile(r"[A-Za-z0-9._: -]{1,320}\Z")
_SCOPE = "wd.q4-shared-projected-events.v1"
_WRAPPER = "Invoke-WdQ4Query.ps1"
_MAX_QUERY = 65536
_MAX_REPLY = 65536
_MAX_MANIFEST = 2 * 1024 * 1024
_MAX_WRAPPER = 1024 * 1024


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError()
        value[key] = item
    return value


def _json(data):
    return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _path(value, *, directory):
    path = Path(value)
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
        raise ValueError()
    if any(part in (".", "..") or part.endswith((" ", ".")) for part in path.parts[1:]):
        raise ValueError()
    if os.name == "nt":
        text = str(path)
        if len(text) < 3 or text[1:3] != ":\\" or ":" in text[2:]:
            raise ValueError()
        drive_type = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
        drive_type.argtypes = (wintypes.LPCWSTR,)
        drive_type.restype = wintypes.UINT
        if drive_type(path.anchor) != 3:
            raise ValueError()
    current = path
    while True:
        info = os.lstat(current)
        if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
                or (current != path and not stat.S_ISDIR(info.st_mode))):
            raise ValueError()
        if current == path and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) is not True:
            raise ValueError()
        if current.parent == current:
            return path
        current = current.parent


def _read(path, limit):
    _path(path, directory=False)
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError()
    return raw


def _binding(bundle_root, commit, manifest_hash, wrapper_hash):
    if (not isinstance(commit, str) or _HEX40.fullmatch(commit) is None
            or any(not isinstance(value, str) or _PIN64.fullmatch(value) is None
                   for value in (manifest_hash, wrapper_hash))):
        raise ValueError()
    root = _path(bundle_root, directory=True)
    manifest = _read(root / "deployment-manifest.json", _MAX_MANIFEST)
    if not hmac.compare_digest(hashlib.sha256(manifest).hexdigest(), manifest_hash.lower()):
        raise ValueError()
    parsed = _json(manifest)
    if (type(parsed) is not dict or type(parsed.get("schema_version")) is not int
            or parsed["schema_version"] != 1 or parsed.get("source_commit") != commit
            or type(parsed.get("files")) is not dict):
        raise ValueError()
    manifest_pin = parsed["files"].get(_WRAPPER)
    if (not isinstance(manifest_pin, str) or _PIN64.fullmatch(manifest_pin) is None
            or not hmac.compare_digest(manifest_pin.lower(), wrapper_hash.lower())):
        raise ValueError()
    wrapper = root / _WRAPPER
    if not hmac.compare_digest(hashlib.sha256(_read(wrapper, _MAX_WRAPPER)).hexdigest(), wrapper_hash.lower()):
        raise ValueError()
    return str(root), str(wrapper)


def _result(raw, code):
    if type(raw) is not bytes or len(raw) > _MAX_REPLY or type(code) is not int:
        raise ValueError()
    value = _json(raw)
    if type(value) is not dict or value.get("schema") not in ("wd.q4-agent-wrapper.v1", "wd.q4-agent-wrapper.v2"):
        raise ValueError()
    status = value.get("status")
    if status == "REFUSED":
        if code != 2 or set(value) != {"schema", "status", "reason", "results"} or value["results"] != []:
            raise ValueError()
        return {"schema": value["schema"], "status": "REFUSED", "reason": "runtime_refused", "results": []}
    if status not in ("ANSWER", "ABSTAIN", "DEGRADED") or code != 0:
        raise ValueError()
    resolved = value["schema"] == "wd.q4-agent-wrapper.v2"
    keys = {"schema", "status", "scope", "memory_sha256", "content_mode", "results"}
    keys |= {"projection_sha256", "policy_sha256", "roster_sha256"} if resolved else {"descriptor_sha256"}
    if (set(value) != keys or value["scope"] != _SCOPE or type(value["results"]) is not list
            or len(value["results"]) > 100 or (status == "ANSWER" and not value["results"])
            or (status == "ABSTAIN" and value["results"])
            or value["content_mode"] != ("sanitized_projected_metadata" if resolved else "hash_only_unresolved")):
        raise ValueError()
    for key in (keys & {"memory_sha256", "descriptor_sha256", "projection_sha256", "policy_sha256", "roster_sha256"}):
        if not isinstance(value[key], str) or _HEX64.fullmatch(value[key]) is None:
            raise ValueError()
    for item in value["results"]:
        item_keys = {"ref_sha256", "doc_id_sha256", "ref_kind", "canonical_event"}
        if resolved:
            item_keys.add("stored_text")
        if (type(item) is not dict or set(item) != item_keys
                or item["ref_kind"] != "sha256_of_projected_metadata" or item["canonical_event"] is not False
                or any(not isinstance(item[key], str) or _HEX64.fullmatch(item[key]) is None
                       for key in ("ref_sha256", "doc_id_sha256"))
                or (resolved and (not isinstance(item["stored_text"], str)
                                  or _TEXT.fullmatch(item["stored_text"]) is None))):
            raise ValueError()
    return value


def invoke(*, lane, bundle_root, expected_commit, expected_manifest_sha256, expected_wrapper_sha256,
           query_bytes, transport=None, enabled=False):
    """Call an injected trusted transport; ``lane`` is a declaration, not identity proof."""
    if enabled is not True:
        raise LaneLinkRefused("disabled")
    if not isinstance(lane, str) or lane not in _LANES:
        raise LaneLinkRefused("invalid_lane")
    if not callable(transport):
        raise LaneLinkRefused("transport_unavailable")
    try:
        root, wrapper = _binding(bundle_root, expected_commit, expected_manifest_sha256, expected_wrapper_sha256)
    except Exception:
        raise LaneLinkRefused("binding_refused") from None
    if type(query_bytes) is not bytes or len(query_bytes) > _MAX_QUERY:
        raise LaneLinkRefused("invalid_query")
    try:
        if _binding(bundle_root, expected_commit, expected_manifest_sha256, expected_wrapper_sha256) != (root, wrapper):
            raise ValueError()
    except Exception:
        raise LaneLinkRefused("binding_refused") from None
    try:
        raw, code = transport(wrapper=wrapper, arguments=("-BundleRoot", root, "-ExpectedBundleCommit", expected_commit,
            "-ExpectedManifestSha256", expected_manifest_sha256), stdin_bytes=query_bytes)
        return _result(raw, code)
    except Exception:
        raise LaneLinkRefused("transport_refused") from None
