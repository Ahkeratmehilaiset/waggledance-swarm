"""Rule 9b activation-receipt verifier.

Implemented so far: the canonical JSON encoder that produces the bytes the
ConfirmDigest is taken over (v5 slice 1), and the CLOSED receipt schema with
independent receipt verification (v5 slice 2). The security probes, task
probes, sealed runtime manifest checks and the full admission verdict land in
later slices and are deliberately absent rather than stubbed, so nothing can
mistake an unimplemented check for a passing one.

Why the encoder lives here and not in a shared utility: the digest has
exactly two producers -- this verifier and the elevated PowerShell activator
-- and they must agree byte for byte. Keeping the Python side next to the
code that re-derives the digest at verification time makes the pairing
visible; the parity contract itself is enforced by
``tests/tools/test_wd_rule9b_activation.py``, which runs both implementations
against a shared vector table under both PowerShell hosts.

The two encoders must also REFUSE the same inputs, not merely agree on the
ones they both accept. Where one side would silently coerce an input the
other rejects, the coercion is the bug: PowerShell's UTF-8 encoder replaces a
lone surrogate with U+FFFD while Python raises, and PowerShell's dictionary
path used to cast a non-string key to a string and then look the original
value up under that string, silently emitting null. Every such input is now
refused on both sides with a typed error.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

# Object keys are restricted to printable ASCII, and this is a real
# constraint rather than a stylistic one.
#
# Python's ``sort_keys=True`` orders keys by Unicode code point. The .NET
# ordinal comparer the PowerShell side must use orders them by UTF-16 code
# unit. Those two orders are identical across the Basic Multilingual Plane
# and DIVERGE for astral keys, which sort above U+E000..U+FFFF in UTF-16
# order and below them by code point. Rather than test that divergence and
# hope no receipt ever hits it, the encoders refuse the inputs that could
# reach it. The Rule 9b receipt schema is closed and every field name in it
# is ASCII, so this costs nothing and removes the class.
_KEY_MIN = 0x20
_KEY_MAX = 0x7E

# Integers are limited to what BOTH sides can represent. PowerShell has no
# arbitrary-precision integer in this path; Python does. An unbounded Python
# integer would encode here and throw there, which is a divergence even
# though the PowerShell side fails closed.
_INT_MIN = -(2 ** 63)
_INT_MAX = 2 ** 63 - 1

_SURROGATE_MIN = 0xD800
_SURROGATE_MAX = 0xDFFF


class CanonicalJsonError(ValueError):
    """Raised when a payload cannot be canonicalized deterministically."""


def _assert_string_encodable(value: str, path: str) -> None:
    """Refuse text that the two encoders would not agree how to encode.

    A lone surrogate is not valid Unicode text. Python's UTF-8 encoder
    raises on it; .NET's silently substitutes U+FFFD. Left unchecked the
    PowerShell side would produce bytes for a payload the Python side calls
    unencodable -- and would produce bytes that are not the payload.
    """
    for index, char in enumerate(value):
        code = ord(char)
        if _SURROGATE_MIN <= code <= _SURROGATE_MAX:
            raise CanonicalJsonError(
                f"{path}: lone UTF-16 surrogate U+{code:04X} at index {index}; "
                "this is not valid Unicode text and the two encoders disagree "
                "about it -- Python refuses it and .NET silently substitutes "
                "U+FFFD"
            )


def _assert_canonicalizable(value: Any, path: str = "$") -> None:
    """Refuse anything whose canonical form is not provably reproducible."""
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        _assert_string_encodable(value, path)
        return
    if isinstance(value, int):
        if not (_INT_MIN <= value <= _INT_MAX):
            raise CanonicalJsonError(
                f"{path}: integer {value} is outside signed 64-bit range; "
                "the PowerShell encoder cannot represent it, so the two sides "
                "would not agree"
            )
        return
    if isinstance(value, float):
        raise CanonicalJsonError(
            f"{path}: floats have no single reproducible JSON form; "
            "represent the value as an integer or a string"
        )
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalJsonError(
                    f"{path}: object keys must be strings, got {type(key)!r}"
                )
            for char in key:
                if not (_KEY_MIN <= ord(char) <= _KEY_MAX):
                    raise CanonicalJsonError(
                        f"{path}: object key {key!r} contains a non-printable-ASCII "
                        "character; Python and .NET order such keys differently, so "
                        "the canonical form would depend on which side produced it"
                    )
            _assert_canonicalizable(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_canonicalizable(item, f"{path}[{index}]")
        return
    raise CanonicalJsonError(f"{path}: unsupported type {type(value)!r}")


def canonical_json_bytes(payload: Any) -> bytes:
    """Return the exact bytes the ConfirmDigest is computed over.

    ``ensure_ascii=False`` is load-bearing. At its default of True, Python
    escapes every non-ASCII character, U+007F and astral pairs, while a
    conforming RFC 8259 encoder emits them literally -- so the two producers
    would disagree on any path or message containing such a character, and
    the digest the operator signs would not identify the payload applied.

    Escaping is therefore exactly what RFC 8259 requires and nothing more:
    the quotation mark, the reverse solidus, and control characters below
    U+0020. The solidus is not escaped. U+007F is not escaped.
    """
    _assert_canonicalizable(payload)
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return text.encode("utf-8")


def canonical_receipt_digest(payload: Any) -> str:
    """SHA-256 over the canonical bytes, lowercase hex."""
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


# ---------------------------------------------------------------------------
# Sealed runtime manifest contract
# ---------------------------------------------------------------------------
RUNTIME_MANIFEST_SCHEMA_ID = "wd.rule9b.runtime_manifest.v1"

# These files are the minimum authorization-bearing chain. A generation that
# omits one is not merely incomplete: it could execute a writable or current-
# checkout substitute for the missing policy byte. The future broker file is
# required now even though it lands in a later slice, which keeps activation
# impossible until that slice exists and is signed.
REQUIRED_RUNTIME_PATHS = (
    "CLAUDE.md",
    "ops/windows/reboot/Invoke-BridgeMergeDriver.ps1",
    "ops/windows/reboot/Invoke-WdRule9bMergeBroker.ps1",
    "ops/windows/reboot/check_rule9b_activation_receipt.py",
    "ops/windows/reboot/start-wd-all.ps1",
    "ops/windows/reboot/wd-fleet.json",
    "ops/windows/reboot/wd_supervisor.ps1",
    "tools/check_bridge_changes_requested.py",
    "tools/check_rco_pass_present.py",
    "tools/idle_consensus_auto_merge.py",
    "tools/merge_with_bridge_receipt.py",
)

_MANIFEST_FIELDS = {
    "schema",
    "activation_head",
    "activation_tree_sha",
    "runtime_generation_id",
    "files",
}
_MANIFEST_FILE_FIELDS = {"path", "git_blob_sha1", "byte_length", "sha256"}
_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class _DuplicateJsonKeyError(ValueError):
    pass


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKeyError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _runtime_relpath_blocker(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return "path must be a nonempty string"
    if "\\" in value or value.startswith("/") or ":" in value:
        return "path must be repository-relative POSIX text without colon"
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return "path is not valid Unicode text"
    parts = value.split("/")
    for part in parts:
        if not part or part in (".", ".."):
            return "path contains an empty or traversal component"
        if part.endswith((" ", ".")):
            return "path component has a trailing dot or space"
        if any(ord(ch) < 32 for ch in part):
            return "path component contains a control character"
        device_stem = part.split(".", 1)[0].casefold()
        if device_stem in _WINDOWS_RESERVED_NAMES:
            return "path component is a reserved Windows device name"
    return None


def validate_runtime_manifest_schema(manifest: Any) -> list[str]:
    """Validate the closed, exact-head runtime-manifest document."""
    if not isinstance(manifest, dict):
        return [f"runtime manifest must be a JSON object, got {type(manifest).__name__}"]

    blockers: list[str] = []
    unknown = sorted(set(manifest) - _MANIFEST_FIELDS)
    missing = sorted(_MANIFEST_FIELDS - set(manifest))
    blockers.extend(f"unknown runtime manifest field: {name}" for name in unknown)
    blockers.extend(f"missing runtime manifest field: {name}" for name in missing)
    if blockers:
        return blockers

    if manifest["schema"] != RUNTIME_MANIFEST_SCHEMA_ID:
        blockers.append("malformed runtime manifest field: schema")
    for name in ("activation_head", "activation_tree_sha"):
        if not _is_sha1(manifest[name]):
            blockers.append(f"malformed runtime manifest field: {name}")
    generation = manifest["runtime_generation_id"]
    if (
        not isinstance(generation, str)
        or not generation
        or len(generation) > 128
        or any(ord(ch) < 33 or ord(ch) > 126 for ch in generation)
    ):
        blockers.append("malformed runtime manifest field: runtime_generation_id")

    files = manifest["files"]
    if not isinstance(files, list) or not files:
        blockers.append("runtime manifest files must be a nonempty list")
        return blockers

    paths: list[str] = []
    folded_paths: dict[str, str] = {}
    for index, entry in enumerate(files):
        prefix = f"runtime manifest files[{index}]"
        if not isinstance(entry, dict):
            blockers.append(f"{prefix} must be an object")
            continue
        unknown_entry = sorted(set(entry) - _MANIFEST_FILE_FIELDS)
        missing_entry = sorted(_MANIFEST_FILE_FIELDS - set(entry))
        blockers.extend(f"{prefix} unknown field: {name}" for name in unknown_entry)
        blockers.extend(f"{prefix} missing field: {name}" for name in missing_entry)
        if unknown_entry or missing_entry:
            continue
        path = entry["path"]
        path_blocker = _runtime_relpath_blocker(path)
        if path_blocker:
            blockers.append(f"{prefix} {path_blocker}")
        else:
            assert isinstance(path, str)
            paths.append(path)
            folded = path.casefold()
            previous = folded_paths.get(folded)
            if previous is not None:
                blockers.append(
                    f"runtime manifest path collision: {previous!r} and {path!r}"
                )
            else:
                folded_paths[folded] = path
        if not _is_sha1(entry["git_blob_sha1"]):
            blockers.append(f"{prefix} malformed git_blob_sha1")
        if not _is_nonnegative_int(entry["byte_length"]):
            blockers.append(f"{prefix} malformed byte_length")
        if not _is_sha256(entry["sha256"]):
            blockers.append(f"{prefix} malformed sha256")

    expected_order = sorted(paths, key=lambda item: item.encode("utf-8"))
    if paths != expected_order:
        blockers.append("runtime manifest files are not sorted by UTF-8 path bytes")
    return blockers


def canonical_runtime_manifest_bytes(manifest: Any) -> bytes:
    """Canonical BOM-free UTF-8 manifest, terminated by exactly one LF."""
    blockers = validate_runtime_manifest_schema(manifest)
    if blockers:
        raise CanonicalJsonError("; ".join(blockers))
    return canonical_json_bytes(manifest) + b"\n"


def runtime_manifest_digest(manifest: Any) -> str:
    return hashlib.sha256(canonical_runtime_manifest_bytes(manifest)).hexdigest()


def _git_blob_sha1(payload: bytes) -> str:
    prefix = f"blob {len(payload)}\0".encode("ascii")
    return hashlib.sha1(
        prefix + payload,
        usedforsecurity=False,
    ).hexdigest()  # Git's object-id algorithm, never a security digest


def _has_unsafe_file_identity(path: Path) -> str | None:
    try:
        stat_result = path.lstat()
    except OSError as exc:
        return f"cannot stat runtime path: {exc.__class__.__name__}"
    if path.is_symlink():
        return "runtime path is a symbolic link"
    attributes = getattr(stat_result, "st_file_attributes", 0)
    reparse = getattr(os, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    sparse = getattr(os, "FILE_ATTRIBUTE_SPARSE_FILE", 0x200)
    if attributes & reparse:
        return "runtime path is a reparse point"
    if attributes & sparse:
        return "runtime path is sparse"
    if not path.is_file():
        return "runtime path is not a regular file"
    if getattr(stat_result, "st_nlink", 1) != 1:
        return "runtime path has an unexpected hard-link count"
    return None


def _has_unsafe_directory_identity(path: Path) -> str | None:
    try:
        stat_result = path.lstat()
    except OSError as exc:
        return f"cannot stat runtime directory: {exc.__class__.__name__}"
    if path.is_symlink():
        return "runtime directory is a symbolic link"
    attributes = getattr(stat_result, "st_file_attributes", 0)
    reparse = getattr(os, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if attributes & reparse:
        return "runtime directory is a reparse point"
    if not path.is_dir():
        return "runtime directory is not a directory"
    return None


def _alternate_stream_blocker(path: Path) -> str | None:
    """Refuse NTFS alternate data streams and inability to enumerate them."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class _StreamData(ctypes.Structure):
        _fields_ = [
            ("stream_size", ctypes.c_longlong),
            ("stream_name", wintypes.WCHAR * 296),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    find_first = kernel32.FindFirstStreamW
    find_first.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(_StreamData), wintypes.DWORD]
    find_first.restype = wintypes.HANDLE
    find_next = kernel32.FindNextStreamW
    find_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_StreamData)]
    find_next.restype = wintypes.BOOL
    find_close = kernel32.FindClose
    find_close.argtypes = [wintypes.HANDLE]
    find_close.restype = wintypes.BOOL

    data = _StreamData()
    handle = find_first(str(path), 0, ctypes.byref(data), 0)
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        error = ctypes.get_last_error()
        # ERROR_HANDLE_EOF means the filesystem reported no streams. Other
        # failures are ambiguity and therefore a blocker.
        if error == 38:
            return None
        return f"cannot enumerate alternate streams (Win32 error {error})"
    try:
        while True:
            name = str(data.stream_name)
            if name and name != "::$DATA":
                return f"runtime path has alternate data stream {name!r}"
            if not find_next(handle, ctypes.byref(data)):
                error = ctypes.get_last_error()
                if error == 38:
                    return None
                return f"alternate-stream enumeration failed (Win32 error {error})"
    finally:
        find_close(handle)


def verify_runtime_manifest(
    manifest_path: Any,
    generation_root: Any,
    *,
    expected_activation_head: str,
    expected_activation_tree_sha: str,
    expected_generation_id: str,
    expected_manifest_sha256: str,
    expected_file_count: int,
) -> dict[str, Any]:
    """Rehash a materialized runtime generation against its sealed manifest.

    This lower-level verifier does not grant authority. The protected broker
    will call it with fixed protected roots; the Limited admission path still
    refuses Apply until that broker exists.
    """
    blockers: list[str] = []
    root = Path(generation_root)
    manifest_file = Path(manifest_path)
    manifest_identity = _has_unsafe_file_identity(manifest_file)
    if manifest_identity:
        return {
            "verified": False,
            "blockers": [f"runtime manifest identity refused: {manifest_identity}"],
            "manifest": None,
        }
    manifest_stream = _alternate_stream_blocker(manifest_file)
    if manifest_stream:
        return {
            "verified": False,
            "blockers": [f"runtime manifest identity refused: {manifest_stream}"],
            "manifest": None,
        }
    try:
        raw_manifest = manifest_file.read_bytes()
    except OSError as exc:
        return {
            "verified": False,
            "blockers": [f"runtime manifest unreadable: {exc.__class__.__name__}"],
            "manifest": None,
        }
    if raw_manifest.startswith(b"\xef\xbb\xbf"):
        return {
            "verified": False,
            "blockers": ["runtime manifest has a UTF-8 BOM"],
            "manifest": None,
        }
    try:
        manifest = json.loads(
            raw_manifest.decode("utf-8"), object_pairs_hook=_closed_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKeyError) as exc:
        return {
            "verified": False,
            "blockers": [f"runtime manifest is invalid strict JSON: {exc}"],
            "manifest": None,
        }

    blockers.extend(validate_runtime_manifest_schema(manifest))
    if blockers:
        return {"verified": False, "blockers": blockers, "manifest": manifest}
    try:
        canonical = canonical_runtime_manifest_bytes(manifest)
    except CanonicalJsonError as exc:
        return {
            "verified": False,
            "blockers": [f"runtime manifest is not canonicalizable: {exc}"],
            "manifest": manifest,
        }
    if raw_manifest != canonical:
        blockers.append("runtime manifest bytes are not canonical BOM-free UTF-8 plus LF")
    digest = hashlib.sha256(raw_manifest).hexdigest()
    if digest != expected_manifest_sha256:
        blockers.append("runtime manifest SHA-256 does not match the activation receipt")
    if manifest["activation_head"] != expected_activation_head:
        blockers.append("runtime manifest activation_head mismatch")
    if manifest["activation_tree_sha"] != expected_activation_tree_sha:
        blockers.append("runtime manifest activation_tree_sha mismatch")
    if manifest["runtime_generation_id"] != expected_generation_id:
        blockers.append("runtime manifest generation id mismatch")
    if len(manifest["files"]) != expected_file_count:
        blockers.append("runtime manifest file count does not match the activation receipt")

    manifest_paths = {entry["path"] for entry in manifest["files"]}
    missing_required = sorted(set(REQUIRED_RUNTIME_PATHS) - manifest_paths)
    blockers.extend(
        f"runtime manifest omits required authority path: {path}"
        for path in missing_required
    )

    root_identity = _has_unsafe_directory_identity(root)
    if root_identity:
        blockers.append(f"runtime generation root identity refused: {root_identity}")
        return {"verified": False, "blockers": blockers, "manifest": manifest}

    disk_paths: set[str] = set()
    for directory, dir_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for dir_name in list(dir_names):
            candidate = directory_path / dir_name
            directory_blocker = _has_unsafe_directory_identity(candidate)
            if directory_blocker:
                blockers.append(
                    f"runtime directory {candidate.relative_to(root).as_posix()}: "
                    f"{directory_blocker}"
                )
                dir_names.remove(dir_name)
        for file_name in file_names:
            candidate = directory_path / file_name
            relative = candidate.relative_to(root).as_posix()
            disk_paths.add(relative)
            identity_blocker = _has_unsafe_file_identity(candidate)
            if identity_blocker:
                blockers.append(f"runtime file {relative}: {identity_blocker}")
                continue
            stream_blocker = _alternate_stream_blocker(candidate)
            if stream_blocker:
                blockers.append(f"runtime file {relative}: {stream_blocker}")

    for extra in sorted(disk_paths - manifest_paths):
        blockers.append(f"runtime generation has unmanifested file: {extra}")
    for missing in sorted(manifest_paths - disk_paths):
        blockers.append(f"runtime generation is missing manifested file: {missing}")

    entries = {entry["path"]: entry for entry in manifest["files"]}
    for relative in sorted(manifest_paths & disk_paths):
        candidate = root.joinpath(*relative.split("/"))
        if _has_unsafe_file_identity(candidate):
            continue
        try:
            payload = candidate.read_bytes()
        except OSError as exc:
            blockers.append(f"runtime file {relative} unreadable: {exc.__class__.__name__}")
            continue
        entry = entries[relative]
        if len(payload) != entry["byte_length"]:
            blockers.append(f"runtime file {relative} length mismatch")
        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            blockers.append(f"runtime file {relative} SHA-256 mismatch")
        if _git_blob_sha1(payload) != entry["git_blob_sha1"]:
            blockers.append(f"runtime file {relative} Git blob mismatch")

    return {"verified": not blockers, "blockers": blockers, "manifest": manifest}


# ---------------------------------------------------------------------------
# Closed receipt schema
# ---------------------------------------------------------------------------
# The schema is CLOSED: an unknown field is a blocker, not something to ignore.
# An open schema lets whoever can write the authority root add fields a future
# reader might honour, and lets a field vanish in a refactor with nothing
# noticing. Every field below is required; the only legitimately empty value is
# previous_driver_sha256 on a first activation, and that exception is spelled
# out rather than achieved by loosening the type.
RECEIPT_SCHEMA_ID = "wd.rule9b.activation_receipt.v1"

# A receipt stamped slightly ahead of this clock is tolerated; anything beyond
# is refused rather than explained away.
APPLIED_AT_FUTURE_SKEW_MINUTES = 2
MAX_APPLY_WINDOW_DAYS = 30

_HEX40 = "0123456789abcdef"


def _is_hex(value: Any, length: int) -> bool:
    """Lowercase hex of an exact length. Uppercase is refused deliberately:
    two spellings of one SHA would compare unequal in one place and equal in
    another."""
    return (
        isinstance(value, str)
        and len(value) == length
        and all(ch in _HEX40 for ch in value)
    )


def _is_sha1(value: Any) -> bool:
    return _is_hex(value, 40)


def _is_sha256(value: Any) -> bool:
    return _is_hex(value, 64)


def _is_optional_sha256(value: Any) -> bool:
    """Empty only, or a full SHA-256. Not "none", not null, not absent."""
    return value == "" or _is_sha256(value)


def _is_positive_int(value: Any) -> bool:
    # bool is an int in Python; a boolean file count is a defect, not a count.
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == dt.timedelta(0)


def _is_canonical_relpath(value: Any) -> bool:
    """A repository-relative POSIX path and nothing else.

    Backslashes, absolute paths, drive letters and parent traversal are all
    refused: a consumer resolving such a path would read somewhere the
    activation never covered.
    """
    if not isinstance(value, str) or not value:
        return False
    if "\\" in value or value.startswith("/") or ":" in value:
        return False
    parts = value.split("/")
    return all(p and p not in (".", "..") for p in parts)


def _is_relpath_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        _is_canonical_relpath(item) for item in value
    )


def _is_cause_b_blob_pair(value: Any) -> bool:
    """Exactly the two cause-B blob ids, never one and never a longer list."""
    return isinstance(value, list) and len(value) == 2 and all(_is_sha1(v) for v in value)


def _is_schema_id(value: Any) -> bool:
    return value == RECEIPT_SCHEMA_ID


RECEIPT_FIELDS: Mapping[str, Any] = {
    "schema": _is_schema_id,
    "activation_pr_number": _is_positive_int,
    "activation_head": _is_sha1,
    "activation_base_sha": _is_sha1,
    "activation_tree_sha": _is_sha1,
    "activation_pr_changed_paths": _is_relpath_list,
    "runtime_generation_id": lambda v: isinstance(v, str) and bool(v),
    "runtime_manifest_sha256": _is_sha256,
    "runtime_file_count": _is_positive_int,
    "driver_sha256": _is_sha256,
    "verifier_sha256": _is_sha256,
    "previous_driver_sha256": _is_optional_sha256,
    "cause_b_blob_ids": _is_cause_b_blob_pair,
    "applied_at_utc": _is_utc_timestamp,
    "effective_expiry_utc": _is_utc_timestamp,
    "confirm_digest": lambda v: _is_sha256(v),
}

DIGEST_EXCLUDED_FIELD = "confirm_digest"


def validate_receipt_schema(receipt: Any) -> list[str]:
    """Return every schema blocker. Never raises: callers branch on the list."""
    blockers: list[str] = []
    if not isinstance(receipt, dict):
        return [f"receipt must be a JSON object, got {type(receipt).__name__}"]

    for name in receipt:
        if name not in RECEIPT_FIELDS:
            blockers.append(f"unknown field: {name}")

    for name, is_valid in RECEIPT_FIELDS.items():
        if name not in receipt:
            blockers.append(f"missing required field: {name}")
            continue
        if not is_valid(receipt[name]):
            blockers.append(f"malformed field: {name}")

    return blockers


def _parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        dt.timezone.utc
    )


def verify_receipt_file(
    path: Any,
    *,
    expected_activation_head: str,
    now_utc: dt.datetime,
) -> dict[str, Any]:
    """Open, parse and verify a receipt. There is no way to skip this.

    Deliberately has no skip/trust/force parameter: an optional fail-closed
    check is not a fail-closed check. The caller gets a report and branches on
    it; a report is only ``verified`` when the blocker list is empty.
    """
    blockers: list[str] = []
    receipt: Any = None

    file_path = Path(path)
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        return {
            "verified": False,
            "blockers": [f"receipt unreadable: {exc.__class__.__name__}"],
            "receipt": None,
        }

    # Strict UTF-8, never utf-8-sig. A BOM-tolerant read would accept bytes
    # that differ from the signed canonical payload.
    if raw.startswith(b"\xef\xbb\xbf"):
        return {
            "verified": False,
            "blockers": ["receipt has a UTF-8 BOM; the signed payload has none"],
            "receipt": None,
        }
    try:
        receipt = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "verified": False,
            "blockers": [f"receipt is not valid UTF-8 JSON: {exc.__class__.__name__}"],
            "receipt": None,
        }

    blockers.extend(validate_receipt_schema(receipt))
    if blockers:
        # Nothing below can be trusted to have the right shape.
        return {"verified": False, "blockers": blockers, "receipt": receipt}

    body = {k: v for k, v in receipt.items() if k != DIGEST_EXCLUDED_FIELD}
    try:
        expected_digest = canonical_receipt_digest(body)
    except CanonicalJsonError as exc:
        blockers.append(f"receipt is not canonicalizable: {exc}")
        return {"verified": False, "blockers": blockers, "receipt": receipt}
    if receipt[DIGEST_EXCLUDED_FIELD] != expected_digest:
        blockers.append(
            "confirm_digest does not match the receipt contents; "
            "the receipt was altered after it was sealed"
        )

    if receipt["activation_head"] != expected_activation_head:
        blockers.append(
            "activation_head does not match the signed activation generation: "
            f"{receipt['activation_head']} != {expected_activation_head}"
        )

    applied_at = _parse_utc(receipt["applied_at_utc"])
    expiry = _parse_utc(receipt["effective_expiry_utc"])
    if not isinstance(now_utc, dt.datetime) or now_utc.tzinfo is None:
        blockers.append("verification clock must be a timezone-aware UTC datetime")
        return {"verified": False, "blockers": blockers, "receipt": receipt}
    now = now_utc.astimezone(dt.timezone.utc)

    if applied_at > now + dt.timedelta(minutes=APPLIED_AT_FUTURE_SKEW_MINUTES):
        blockers.append(
            "applied_at_utc is in the future; that is a clock problem or a "
            "forged window, not evidence of an activation"
        )
    if expiry <= applied_at:
        blockers.append("effective_expiry_utc is not after applied_at_utc")
    if expiry - applied_at > dt.timedelta(days=MAX_APPLY_WINDOW_DAYS):
        blockers.append(
            f"the window exceeds the {MAX_APPLY_WINDOW_DAYS}-day cap; a frozen "
            "policy must not outlive the policy it froze"
        )
    if now >= expiry:
        blockers.append("the receipt has expired")

    return {"verified": not blockers, "blockers": blockers, "receipt": receipt}


def verify_activation_bundle(
    receipt_path: Any,
    manifest_path: Any,
    generation_root: Any,
    *,
    expected_activation_head: str,
    now_utc: dt.datetime,
) -> dict[str, Any]:
    """Verify the receipt and the exact materialized runtime as one unit.

    Paths remain parameters only because this is a pure verifier used by
    tests and by the later sealed bootstrap. The protected broker must supply
    fixed protected roots; this function itself performs no Apply or dispatch.
    A receipt self-claim never substitutes for opening the manifest and every
    file it names.
    """
    receipt_report = verify_receipt_file(
        receipt_path,
        expected_activation_head=expected_activation_head,
        now_utc=now_utc,
    )
    if receipt_report.get("verified") is not True:
        return {
            "verified": False,
            "blockers": list(receipt_report.get("blockers", [])),
            "receipt_gate": receipt_report,
            "runtime_gate": {
                "verified": False,
                "blockers": ["runtime verification not attempted because receipt failed"],
                "manifest": None,
            },
        }
    receipt = receipt_report.get("receipt")
    if not isinstance(receipt, dict):
        return {
            "verified": False,
            "blockers": ["verified receipt report did not contain a receipt object"],
            "receipt_gate": receipt_report,
            "runtime_gate": {
                "verified": False,
                "blockers": ["runtime verification not attempted"],
                "manifest": None,
            },
        }
    runtime_report = verify_runtime_manifest(
        manifest_path,
        generation_root,
        expected_activation_head=receipt["activation_head"],
        expected_activation_tree_sha=receipt["activation_tree_sha"],
        expected_generation_id=receipt["runtime_generation_id"],
        expected_manifest_sha256=receipt["runtime_manifest_sha256"],
        expected_file_count=receipt["runtime_file_count"],
    )
    runtime_blockers = runtime_report.get("blockers")
    if not isinstance(runtime_blockers, list) or not all(
        isinstance(item, str) for item in runtime_blockers
    ):
        runtime_report = {
            "verified": False,
            "blockers": ["runtime verifier returned a malformed blocker list"],
            "manifest": None,
        }
        runtime_blockers = runtime_report["blockers"]
    verified = runtime_report.get("verified") is True and not runtime_blockers
    return {
        "verified": verified,
        "blockers": list(runtime_blockers),
        "receipt_gate": receipt_report,
        "runtime_gate": runtime_report,
    }
