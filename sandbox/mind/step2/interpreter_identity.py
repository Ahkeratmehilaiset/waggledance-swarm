"""Default-off, synthetic-ready interpreter tree identity verification.

The caller independently supplies the descriptor bytes and their trusted pin.
The exact regular-file tree is checked without executing any file. This is not
OS process identity, PE/ABI validation, DLL-load verification, authenticity of
the supplied pin, or protection from a hostile same-user process or subsequent
replacement. Native and interpreter execution remain outside this primitive.
"""
import hashlib
import hmac
import json
import ntpath
import os
from pathlib import Path
import re
import stat

from sandbox.mind.step2 import runtime_manifest as runtime

SCHEMA = "wd.q4-interpreter-identity.v1"
MAX_DESCRIPTOR_BYTES = 1024 * 1024
MAX_FILES = 1024
MAX_ITEM_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
# Syntactic policy budget in Python characters, not an OS path-validity guarantee.
MAX_BINDING_PATH_CHARS = 32767
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_ROLES = frozenset({"interpreter", "stdlib", "dll"})


class IdentityRefused(ValueError):
    """Fixed error token only; caller/traceback locals may contain input data."""


def _records(document):
    if type(document) is not dict or set(document) != {"schema", "files"} or document["schema"] != SCHEMA:
        raise ValueError()
    records = document["files"]
    if type(records) is not list or not 1 <= len(records) <= MAX_FILES:
        raise ValueError()
    files, directories, cases, roles = {}, set(), {}, []
    interpreter_name = None
    total = 0
    for record in records:
        if type(record) is not dict or set(record) != {"role", "path", "size", "sha256"}:
            raise ValueError()
        role, path, size, digest = (record[key] for key in ("role", "path", "size", "sha256"))
        if (type(role) is not str or role not in _ROLES or type(path) is not str
                or type(size) is not int or not 0 <= size <= MAX_ITEM_BYTES
                or type(digest) is not str or _HEX.fullmatch(digest) is None):
            raise ValueError()
        # Reuse the runtime's conservative segment and device-name checks;
        # the synthetic assets/ prefix is used only for validation, not I/O.
        parts = runtime._path_parts("assets/" + path)[1:]
        if path in files:
            raise ValueError()
        for index in range(1, len(parts) + 1):
            name = "/".join(parts[:index])
            folded = name.casefold()
            if folded in cases and cases[folded] != name:
                raise ValueError()
            cases[folded] = name
            if index < len(parts):
                directories.add(name)
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError()
        files[path] = (size, digest)
        roles.append(role)
        if role == "interpreter":
            interpreter_name = path
    if set(files) & directories or roles.count("interpreter") != 1 or not {"stdlib", "dll"} <= set(roles):
        raise ValueError()
    return files, directories, total, interpreter_name


def _check_tree(root, files, directories):
    """Refuse unexpected entries before descending; path checks are not a race-free OS sandbox."""
    expected_files = set(files)
    seen_files, seen_directories = set(), set()
    pending = [(root, "")]
    while pending:
        directory, prefix = pending.pop()
        runtime._check_chain(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                relative = prefix + entry.name
                if relative not in expected_files and relative not in directories:
                    raise ValueError()
                info = os.lstat(entry.path)
                if runtime._is_reparse(info):
                    raise ValueError()
                runtime._check_no_named_streams(entry.path)
                if relative in directories:
                    if not stat.S_ISDIR(info.st_mode) or relative in seen_directories:
                        raise ValueError()
                    seen_directories.add(relative)
                    pending.append((root.joinpath(*relative.split("/")), relative + "/"))
                else:
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or relative in seen_files:
                        raise ValueError()
                    seen_files.add(relative)
    if seen_files != expected_files or seen_directories != directories:
        raise ValueError()


def parse_pinned_interpreter_descriptor(descriptor_bytes, expected_sha256):
    """Parse pinned descriptor bytes only; this does not inspect or trust a tree."""
    try:
        if (type(descriptor_bytes) is not bytes or len(descriptor_bytes) > MAX_DESCRIPTOR_BYTES
                or type(expected_sha256) is not str or _HEX.fullmatch(expected_sha256) is None):
            raise ValueError()
        digest = hashlib.sha256(descriptor_bytes).hexdigest()
        if not hmac.compare_digest(digest, expected_sha256):
            raise ValueError()
        document = json.loads(descriptor_bytes.decode("utf-8", "strict"), object_pairs_hook=runtime._unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        files, directories, total, interpreter_name = _records(document)
        return digest, files, directories, total, interpreter_name
    except Exception:
        pass
    raise IdentityRefused("interpreter_identity_refused") from None


def _binding_segment(part):
    """Apply filesystem-independent subset of runtime path component rules."""
    if (not part or len(part) > 255 or part in (".", "..") or ":" in part
            or part.endswith((".", " ")) or runtime.DEVICE.fullmatch(part)
            or any(ord(ch) < 32 for ch in part)):
        raise ValueError()


def _binding_path(value):
    """Normalize syntax only; do not probe the named path or its ancestors."""
    text = os.fspath(value)
    if (type(text) is not str or not text or len(text) > MAX_BINDING_PATH_CHARS
            or any(ord(ch) < 32 for ch in text)):
        raise ValueError()
    if re.match(r"^[A-Za-z]:\\", text):
        drive, tail = ntpath.splitdrive(text)
        if (len(drive) != 2 or not tail.startswith("\\") or "/" in text
                or ":" in tail or ntpath.normpath(text) != text):
            raise ValueError()
        for part in tail.split("\\"):
            if part:
                _binding_segment(part)
                if any(ch in '<>"|?*' for ch in part):
                    raise ValueError()
        return "windows", ntpath.normcase(text)
    if os.name == "nt":
        raise ValueError()
    path = Path(text)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError()
    for part in path.parts[1:]:
        _binding_segment(part)
    return "posix", str(path)


def validate_interpreter_descriptor_binding(root, expected_interpreter, expected_interpreter_sha256, parsed):
    """Check only path syntax and descriptor binding; do not inspect a tree."""
    try:
        kind, root_text = _binding_path(root)
        if (expected_interpreter is None) != (expected_interpreter_sha256 is None):
            raise ValueError()
        if expected_interpreter is not None:
            if type(expected_interpreter_sha256) is not str or _HEX.fullmatch(expected_interpreter_sha256) is None:
                raise ValueError()
            _, files, _, _, interpreter_name = parsed
            selected_kind, selected = _binding_path(expected_interpreter)
            parts = interpreter_name.split("/")
            declared = (ntpath.normcase(ntpath.join(root_text, *parts)) if kind == "windows"
                        else str(Path(root_text).joinpath(*parts)))
            if (selected_kind != kind or selected != declared
                    or not hmac.compare_digest(files[interpreter_name][1], expected_interpreter_sha256)):
                raise ValueError()
        return None
    except Exception:
        pass
    raise IdentityRefused("interpreter_identity_refused") from None


def verify_interpreter_identity(*, enabled=False, root=None, descriptor_bytes=None, expected_sha256=None,
                                expected_interpreter=None, expected_interpreter_sha256=None):
    """Check the pinned tree and optional independently selected executable binding.

    The optional path and SHA must be supplied together. They bind the sole
    interpreter record, not the running process; same-user races remain.
    """
    if enabled is False:
        return {"status": "OFF"}
    try:
        if enabled is not True:
            raise ValueError()
        if (expected_interpreter is None) != (expected_interpreter_sha256 is None):
            raise ValueError()
        if (expected_interpreter is not None and (type(expected_interpreter_sha256) is not str
                or _HEX.fullmatch(expected_interpreter_sha256) is None)):
            raise ValueError()
        parsed = parse_pinned_interpreter_descriptor(descriptor_bytes, expected_sha256)
        validate_interpreter_descriptor_binding(root, expected_interpreter, expected_interpreter_sha256, parsed)
        digest, files, directories, total, interpreter_name = parsed
        checked_root = runtime._check_chain(root)
        root_info = os.lstat(checked_root)
        if runtime._is_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
            raise ValueError()
        if expected_interpreter is not None:
            selected = runtime._check_chain(expected_interpreter)
            declared = checked_root.joinpath(*interpreter_name.split("/"))
            if selected != declared or not hmac.compare_digest(files[interpreter_name][1],
                                                               expected_interpreter_sha256):
                raise ValueError()
        runtime._check_no_named_streams(checked_root)
        _check_tree(checked_root, files, directories)
        for relative, (size, expected) in files.items():
            path = checked_root.joinpath(*relative.split("/"))
            runtime._check_chain(path)
            stream, initial = runtime._open_regular(path, MAX_ITEM_BYTES)
            with stream:
                if initial.st_size != size:
                    raise ValueError()
                hasher, remaining = hashlib.sha256(), size
                while remaining:
                    block = stream.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError()
                    hasher.update(block)
                    remaining -= len(block)
                if stream.read(1) or not runtime._same_file(path, stream, initial):
                    raise ValueError()
                if not hmac.compare_digest(hasher.hexdigest(), expected):
                    raise ValueError()
        _check_tree(checked_root, files, directories)
        return {"status": "VERIFIED", "descriptor_sha256": digest, "file_count": len(files), "total_bytes": total}
    except Exception:
        pass
    raise IdentityRefused("interpreter_identity_refused") from None
