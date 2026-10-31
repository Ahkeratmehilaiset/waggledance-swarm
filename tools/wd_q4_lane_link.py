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


_PUBLIC_PLAN_KEYS = frozenset({"schema", "config", "root", "policy_path", "roster_path", "public_policy_path",
    "pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256"})
_PUBLIC_BIND_PINS = frozenset({"pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
                               "roster_sha256", "public_policy_sha256", "descriptor_sha256"})
_PUBLIC_BIND_KEYS = _PUBLIC_BIND_PINS | {"memory_sha256", "projection_sha256", "receipt_sha256"}
_PUBLIC_UNKNOWN = frozenset({"freshness", "publisher_authentication", "source_filter_attestation", "same_user_aba"})


def _public_plan(path, expected_sha256):
    if type(expected_sha256) is not str or _HEX64.fullmatch(expected_sha256) is None:
        raise ValueError()
    path = _path(path, directory=False)
    raw = _read(path, 65536)
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256):
        raise ValueError()
    plan = _json(raw)
    if (type(plan) is not dict or set(plan) != _PUBLIC_PLAN_KEYS
            or plan["schema"] != "wd.q4-public-wrapper-plan.v1" or type(plan["config"]) is not dict
            or any(type(plan[field]) is not str or not plan[field]
                   for field in ("root", "policy_path", "roster_path", "public_policy_path"))):
        raise ValueError()
    pins = {key: (plan["config"].get(key) if key == "descriptor_sha256" else plan[key])
            for key in _PUBLIC_BIND_PINS}
    if any(type(value) is not str or _HEX64.fullmatch(value) is None for value in pins.values()):
        raise ValueError()
    return str(path), raw, pins


def _public_result(raw, code, pins):
    if type(raw) is not bytes or len(raw) > 32768 or type(code) is not int:
        raise ValueError()
    value = _json(raw)
    if type(value) is not dict or value.get("schema") != "wd.q4-public-query.v2":
        raise ValueError()
    if value.get("status") == "REFUSED":
        if code != 2 or set(value) != {"schema", "status", "reason"} or type(value["reason"]) is not str:
            raise ValueError()
        return {"schema": "wd.q4-public-query.v2", "status": "REFUSED", "reason": "runtime_refused"}
    if (code != 0 or value.get("status") not in ("ANSWER", "ABSTAIN", "DEGRADED")
            or set(value) != ({"schema", "status", "binding", "results"} | _PUBLIC_UNKNOWN)
            or any(value[key] != "UNKNOWN" for key in _PUBLIC_UNKNOWN)
            or type(value["binding"]) is not dict or set(value["binding"]) != _PUBLIC_BIND_KEYS
            or type(value["results"]) is not list or len(value["results"]) > 100
            or (value["status"] == "ANSWER" and not value["results"])
            or (value["status"] == "ABSTAIN" and value["results"])):
        raise ValueError()
    for key, binding in value["binding"].items():
        if type(binding) is not str or _HEX64.fullmatch(binding) is None:
            raise ValueError()
        if key in pins and not hmac.compare_digest(binding, pins[key]):
            raise ValueError()
    for item in value["results"]:
        if (type(item) is not dict or set(item) != {"stored_text", "ref_sha256", "doc_id_sha256", "ref_kind", "canonical_event"}
                or type(item["stored_text"]) is not str or len(item["stored_text"].encode("utf-8", errors="strict")) > 320
                or item["ref_kind"] != "sha256_of_projected_metadata" or item["canonical_event"] is not False
                or any(type(item[key]) is not str or _HEX64.fullmatch(item[key]) is None
                       for key in ("ref_sha256", "doc_id_sha256"))):
            raise ValueError()
    return value


def invoke_public(*, lane, bundle_root, expected_commit, expected_manifest_sha256, expected_wrapper_sha256,
                  public_plan_path, expected_public_plan_sha256, query_bytes, transport=None, enabled=False):
    """Send a pinned public-plan call over caller-trusted transport, not caller authentication.

    Plan path and pins occur in process arguments and may be visible to the OS.
    The caller remains responsible for authenticating pins, transport and source,
    for privacy/freshness, and for process-tree and whole-call time limits.
    """
    if enabled is not True:
        raise LaneLinkRefused("disabled")
    if type(lane) is not str or lane not in _LANES:
        raise LaneLinkRefused("invalid_lane")
    if not callable(transport):
        raise LaneLinkRefused("transport_unavailable")
    try:
        root, wrapper = _binding(bundle_root, expected_commit, expected_manifest_sha256, expected_wrapper_sha256)
        path, raw_plan, pins = _public_plan(public_plan_path, expected_public_plan_sha256)
    except Exception:
        raise LaneLinkRefused("binding_refused") from None
    if type(query_bytes) is not bytes or not query_bytes or len(query_bytes) > _MAX_QUERY:
        raise LaneLinkRefused("invalid_query")
    try:
        if (_binding(bundle_root, expected_commit, expected_manifest_sha256, expected_wrapper_sha256) != (root, wrapper)
                or _public_plan(public_plan_path, expected_public_plan_sha256) != (path, raw_plan, pins)):
            raise ValueError()
    except Exception:
        raise LaneLinkRefused("binding_refused") from None
    try:
        raw, code = transport(wrapper=wrapper, arguments=("-BundleRoot", root, "-ExpectedBundleCommit", expected_commit,
            "-ExpectedManifestSha256", expected_manifest_sha256, "-Public", "-PublicPlanPath", path,
            "-ExpectedPublicPlanSha256", expected_public_plan_sha256), stdin_bytes=query_bytes)
        if (_binding(bundle_root, expected_commit, expected_manifest_sha256, expected_wrapper_sha256) != (root, wrapper)
                or _public_plan(public_plan_path, expected_public_plan_sha256) != (path, raw_plan, pins)):
            raise ValueError()
        return _public_result(raw, code, pins)
    except Exception:
        raise LaneLinkRefused("transport_refused") from None
