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
import os
import re
import stat

from sandbox.mind.step2 import runtime_manifest as runtime

SCHEMA = "wd.q4-interpreter-identity.v1"
MAX_DESCRIPTOR_BYTES = 1024 * 1024
MAX_FILES = 1024
MAX_ITEM_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
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
    if set(files) & directories or roles.count("interpreter") != 1 or not {"stdlib", "dll"} <= set(roles):
        raise ValueError()
    return files, directories, total


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


def verify_interpreter_identity(*, enabled=False, root=None, descriptor_bytes=None, expected_sha256=None):
    """Check the caller-pinned descriptor and exact tree; return metadata only."""
    if enabled is False:
        return {"status": "OFF"}
    try:
        if (enabled is not True or type(descriptor_bytes) is not bytes
                or len(descriptor_bytes) > MAX_DESCRIPTOR_BYTES or type(expected_sha256) is not str
                or _HEX.fullmatch(expected_sha256) is None):
            raise ValueError()
        digest = hashlib.sha256(descriptor_bytes).hexdigest()
        if not hmac.compare_digest(digest, expected_sha256):
            raise ValueError()
        document = json.loads(descriptor_bytes.decode("utf-8", "strict"), object_pairs_hook=runtime._unique_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        files, directories, total = _records(document)
        checked_root = runtime._check_chain(root)
        root_info = os.lstat(checked_root)
        if runtime._is_reparse(root_info) or not stat.S_ISDIR(root_info.st_mode):
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
