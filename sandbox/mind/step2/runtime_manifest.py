"""Verify a separately anchored, read-only Q4 runtime generation.

This verifier does not install or activate a runtime, create a deployment trust
anchor, authenticate its caller, or prevent a same-user process from replacing
files after verification. The caller must protect and supply the external
manifest digest and expected generation/revision, and treat the remaining
same-user time-of-check/time-of-use window as unresolved.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from functools import lru_cache

SCHEMA = "wd.q4-runtime-manifest.v1"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_FILES = 50_000
MAX_DIRECTORIES = 100_000
MAX_FILE_BYTES = 8 * 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024 * 1024
MAX_PATH_CHARS = 4096
HEX_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
IDENTITY = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
SEGMENT = re.compile(r"[A-Za-z0-9_.+\-]+\Z")
DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.IGNORECASE)
SPACE_EXCEPTIONS = frozenset({
    "site/scipy/io/tests/data/Transparent Busy.ani",
    "site/setuptools/launcher manifest.xml",
    "site/setuptools/script (dev).tmpl",
    "site/setuptools/_vendor/jaraco/text/Lorem ipsum.txt",
})


def _refuse(reason):
    raise ValueError(reason) from None


def _is_reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


@lru_cache(maxsize=1)
def _windows_stream_api():
    """Resolve the Win32 enumeration API once for a many-file runtime."""
    import ctypes
    from ctypes import wintypes

    class StreamData(ctypes.Structure):
        _fields_ = [("size", ctypes.c_longlong), ("name", wintypes.WCHAR * 296)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    first = kernel.FindFirstStreamW
    first.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(StreamData), wintypes.DWORD]
    first.restype = wintypes.HANDLE
    next_stream = kernel.FindNextStreamW
    next_stream.argtypes = [wintypes.HANDLE, ctypes.POINTER(StreamData)]
    next_stream.restype = wintypes.BOOL
    close = kernel.FindClose
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    return ctypes, StreamData, first, next_stream, close


def _check_no_named_streams(path):
    """Refuse named Windows data streams; fail closed on enumeration errors."""
    if os.name != "nt":
        return
    try:
        ctypes, StreamData, first, next_stream, close = _windows_stream_api()
        data = StreamData()
        handle = first(os.fspath(path), 0, ctypes.byref(data), 0)
        if handle == ctypes.c_void_p(-1).value:
            if ctypes.get_last_error() == 38:  # ERROR_HANDLE_EOF: no data streams
                return
            _refuse("stream_unavailable")
        try:
            while True:
                if data.name != "::$DATA":
                    _refuse("named_stream")
                if not next_stream(handle, ctypes.byref(data)):
                    if ctypes.get_last_error() != 38:
                        _refuse("stream_unavailable")
                    break
        finally:
            close(handle)
    except (AttributeError, OSError, TypeError):
        _refuse("stream_unavailable")


def _check_chain(path):
    """Reject links in the path itself and existing ancestor components."""
    try:
        supplied = Path(path)
    except (TypeError, ValueError):
        _refuse("path_invalid")
    if not supplied.is_absolute() or ".." in supplied.parts:
        _refuse("path_invalid")
    absolute = Path(os.path.abspath(os.fspath(supplied)))
    if os.name == "nt" and absolute.anchor.startswith("\\\\"):
        _refuse("path_invalid")
    current = Path(absolute.anchor)
    try:
        if _is_reparse(os.lstat(current)):
            _refuse("path_reparse")
        for part in absolute.parts[1:]:
            if ":" in part or part.endswith((".", " ")) or DEVICE.fullmatch(part):
                _refuse("path_invalid")
            current = current / part
            if _is_reparse(os.lstat(current)):
                _refuse("path_reparse")
    except OSError:
        _refuse("path_unavailable")
    return absolute


def _stable_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _open_regular(path, max_size):
    """Open one bounded regular non-link file and check its opened identity."""
    try:
        before = os.lstat(path)
        if (_is_reparse(before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size > max_size):
            _refuse("file_invalid")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if (_is_reparse(opened) or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or _stable_identity(before) != _stable_identity(opened)):
                _refuse("file_changed")
            return os.fdopen(fd, "rb"), opened
        except BaseException:
            os.close(fd)
            raise
    except OSError:
        _refuse("file_unavailable")


def _same_file(path, handle, before):
    try:
        after = os.fstat(handle.fileno())
        final = os.lstat(path)
        return (not _is_reparse(final) and stat.S_ISREG(final.st_mode) and final.st_nlink == 1
                and _stable_identity(before) == _stable_identity(after) == _stable_identity(final))
    except OSError:
        return False


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _refuse("duplicate_json_key")
        result[key] = value
    return result


def _path_parts(relative):
    if (not isinstance(relative, str) or not relative or len(relative) > MAX_PATH_CHARS
            or "\\" in relative or relative.startswith("/") or relative.endswith("/")):
        _refuse("path_invalid")
    parts = relative.split("/")
    if len(parts) < 2 or len(parts) > 64 or parts[0] not in ("site", "model", "assets"):
        _refuse("path_invalid")
    for part in parts:
        if (not part or len(part) > 255 or part in (".", "..") or part.endswith((".", " "))
                or part.startswith(" ") or DEVICE.fullmatch(part)):
            _refuse("path_invalid")
        if not SEGMENT.fullmatch(part) and relative not in SPACE_EXCEPTIONS:
            _refuse("path_invalid")
    return parts


def _tree(root):
    """Enumerate actual regular files and directories without following links."""
    files, directories = set(), set()
    pending = [(root, "")]
    try:
        while pending:
            directory, prefix = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    relative = prefix + entry.name
                    # On Windows DirEntry.stat can report st_nlink=0 even for
                    # a regular single-link file. lstat supplies the real count.
                    info = os.lstat(entry.path)
                    if _is_reparse(info):
                        _refuse("tree_reparse")
                    _check_no_named_streams(entry.path)
                    if stat.S_ISDIR(info.st_mode):
                        directories.add(relative)
                        if len(directories) > MAX_DIRECTORIES:
                            _refuse("tree_over_budget")
                        pending.append((Path(entry.path), relative + "/"))
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        files.add(relative)
                        if len(files) > MAX_FILES:
                            _refuse("tree_over_budget")
                    else:
                        _refuse("tree_nonregular")
    except OSError:
        _refuse("tree_unavailable")
    return files, directories


def _validate_records(records):
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_FILES:
        _refuse("files_invalid")
    files, dirs, case_names = {}, set(), {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "size", "sha256"}:
            _refuse("record_invalid")
        relative, size, digest = record["path"], record["size"], record["sha256"]
        parts = _path_parts(relative)
        if type(size) is not int or size < 0 or size > MAX_FILE_BYTES:
            _refuse("size_invalid")
        if not isinstance(digest, str) or not HEX_SHA256.fullmatch(digest):
            _refuse("digest_invalid")
        if relative in files:
            _refuse("duplicate_path")
        for count in range(1, len(parts) + 1):
            name = "/".join(parts[:count])
            key = name.casefold()
            if key in case_names and case_names[key] != name:
                _refuse("case_collision")
            case_names[key] = name
            if count < len(parts):
                dirs.add(name)
        files[relative] = (size, digest.lower())
    if set(files) & dirs:
        _refuse("file_directory_collision")
    if sum(size for size, _ in files.values()) > MAX_TOTAL_BYTES:
        _refuse("runtime_over_budget")
    return files, dirs


def verify_runtime(root, manifest_path, expected_manifest_sha256, *, expected_generation, expected_revision):
    """Verify an externally pinned manifest and its exact local generation tree.

    Returns metadata only, not a capability or an assurance against later
    mutation. No model is loaded and no runtime file is written.
    """
    if not isinstance(expected_manifest_sha256, str) or not HEX_SHA256.fullmatch(expected_manifest_sha256):
        _refuse("manifest_pin_invalid")
    if (not isinstance(expected_generation, str) or not IDENTITY.fullmatch(expected_generation)
            or not isinstance(expected_revision, str) or not IDENTITY.fullmatch(expected_revision)):
        _refuse("expected_identity_invalid")
    root = _check_chain(root)
    manifest_path = _check_chain(manifest_path)
    try:
        root_info = os.lstat(root)
        if _is_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
            _refuse("root_invalid")
        # A manifest cannot enumerate and hash itself; it belongs beside, not inside, the tree.
        if os.path.commonpath((str(root), str(manifest_path))).casefold() == str(root).casefold():
            _refuse("manifest_inside_tree")
    except (OSError, ValueError):
        _refuse("root_invalid")
    _check_no_named_streams(root)
    _check_no_named_streams(manifest_path)
    stream, initial = _open_regular(manifest_path, MAX_MANIFEST_BYTES)
    try:
        with stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
            if len(raw) > MAX_MANIFEST_BYTES or len(raw) != initial.st_size or not _same_file(manifest_path, stream, initial):
                _refuse("manifest_changed")
    except OSError:
        _refuse("manifest_read_unavailable")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_manifest_sha256.lower():
        _refuse("manifest_pin_mismatch")
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=lambda _: _refuse("json_constant_invalid"))
    except (UnicodeError, ValueError, RecursionError):
        _refuse("manifest_invalid")
    if (not isinstance(document, dict) or set(document) != {"schema", "generation", "revision", "files"}
            or document["schema"] != SCHEMA or document["generation"] != expected_generation
            or document["revision"] != expected_revision):
        _refuse("manifest_identity_mismatch")
    files, directories = _validate_records(document["files"])
    if _tree(root) != (set(files), directories):
        _refuse("tree_mismatch")
    for relative, (size, expected) in files.items():
        path = root.joinpath(*relative.split("/"))
        _check_chain(path)
        stream, initial = _open_regular(path, MAX_FILE_BYTES)
        try:
            with stream:
                if initial.st_size != size:
                    _refuse("file_size_mismatch")
                hasher = hashlib.sha256()
                remaining = size
                while remaining:
                    block = stream.read(min(1024 * 1024, remaining))
                    if not block:
                        _refuse("file_changed")
                    hasher.update(block)
                    remaining -= len(block)
                if stream.read(1):
                    _refuse("file_changed")
                if not _same_file(path, stream, initial):
                    _refuse("file_changed")
                if hasher.hexdigest() != expected:
                    _refuse("file_digest_mismatch")
        except OSError:
            _refuse("file_read_unavailable")
    if _tree(root) != (set(files), directories):
        _refuse("tree_changed")
    return {"generation": expected_generation, "revision": expected_revision,
            "manifest_sha256": digest, "file_count": len(files),
            "total_bytes": sum(size for size, _ in files.values())}
