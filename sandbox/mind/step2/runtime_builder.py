"""Build a deterministic Q4 manifest from an existing runtime tree.

This is not an installer, runtime activation, deployment signature, or trusted
anchor. It does not establish that a runtime has all required dependencies.
The checks do not eliminate a same-user time-of-check/time-of-use race. The
new external output is created exclusively and is never overwritten or deleted;
an I/O failure after creation may leave a partial output for manual inspection.
"""

import hashlib
import json
import os
from pathlib import Path
import stat

from sandbox.mind.step2 import runtime_manifest as verifier


def _fail(reason):
    raise ValueError(reason) from None


def _output_path(root, path):
    try:
        supplied = Path(path)
    except (TypeError, ValueError):
        _fail("output_invalid")
    if not supplied.is_absolute() or ".." in supplied.parts:
        _fail("output_invalid")
    # Apply the verifier's strict ASCII segment policy to the output filename.
    verifier._path_parts("assets/" + supplied.name)
    parent = verifier._check_chain(supplied.parent)
    try:
        if not stat.S_ISDIR(os.lstat(parent).st_mode):
            _fail("output_parent_invalid")
        if os.path.commonpath((str(root), str(parent))).casefold() == str(root).casefold():
            _fail("output_inside_runtime")
    except (OSError, ValueError):
        _fail("output_parent_invalid")
    return parent / supplied.name


def _hash_file(path):
    """Hash only the observed length and reject changes detectable during read."""
    verifier._check_chain(path)
    verifier._check_no_named_streams(path)
    stream, initial = verifier._open_regular(path, verifier.MAX_FILE_BYTES)
    try:
        with stream:
            digest = hashlib.sha256()
            remaining = initial.st_size
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    _fail("file_changed")
                digest.update(block)
                remaining -= len(block)
            if stream.read(1) or not verifier._same_file(path, stream, initial):
                _fail("file_changed")
    except OSError:
        _fail("file_read_unavailable")
    return initial.st_size, digest.hexdigest(), verifier._stable_identity(initial)


def build_runtime_manifest(root, output, *, generation, revision):
    """Write one new deterministic manifest beside an existing runtime tree.

    Returns the candidate digest as metadata only; an independent caller must
    establish and protect its own external deployment anchor before using it.
    """
    if (not isinstance(generation, str) or not verifier.IDENTITY.fullmatch(generation)
            or not isinstance(revision, str) or not verifier.IDENTITY.fullmatch(revision)):
        _fail("identity_invalid")
    root = verifier._check_chain(root)
    try:
        if root == Path(root.anchor) or not stat.S_ISDIR(os.lstat(root).st_mode):
            _fail("runtime_root_invalid")
    except OSError:
        _fail("runtime_root_invalid")
    output = _output_path(root, output)
    if os.path.lexists(output):
        _fail("output_exists")
    verifier._check_no_named_streams(root)
    files, directories = verifier._tree(root)
    # Validate the full path set and directory shape before reading any file.
    provisional = [{"path": name, "size": 0, "sha256": "0" * 64} for name in sorted(files)]
    _, expected_directories = verifier._validate_records(provisional)
    if directories != expected_directories:
        _fail("tree_mismatch")
    records, identities = [], {}
    total = 0
    for relative in sorted(files):
        path = root.joinpath(*relative.split("/"))
        size, digest, identity = _hash_file(path)
        total += size
        if total > verifier.MAX_TOTAL_BYTES:
            _fail("runtime_over_budget")
        records.append({"path": relative, "size": size, "sha256": digest})
        identities[relative] = identity
    verifier._validate_records(records)
    verifier._check_no_named_streams(root)
    if verifier._tree(root) != (files, directories):
        _fail("tree_changed")
    for relative, identity in identities.items():
        path = root.joinpath(*relative.split("/"))
        verifier._check_chain(path)
        try:
            final = os.lstat(path)
            if (verifier._is_reparse(final) or not stat.S_ISREG(final.st_mode) or final.st_nlink != 1
                    or verifier._stable_identity(final) != identity):
                _fail("file_changed")
        except OSError:
            _fail("file_unavailable")

    document = {"schema": verifier.SCHEMA, "generation": generation, "revision": revision, "files": records}
    raw = (json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")
    if len(raw) > verifier.MAX_MANIFEST_BYTES:
        _fail("manifest_over_budget")
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(output, flags, 0o600)
        try:
            created = os.fstat(fd)
            stream = os.fdopen(fd, "w+b")
        except BaseException:
            os.close(fd)
            raise
        with stream:
            if stream.write(raw) != len(raw):
                _fail("output_write_incomplete")
            stream.flush()
            os.fsync(stream.fileno())
            written = os.fstat(stream.fileno())
            if ((created.st_dev, created.st_ino) != (written.st_dev, written.st_ino)
                    or not stat.S_ISREG(written.st_mode) or written.st_nlink != 1
                    or verifier._is_reparse(written) or written.st_size != len(raw)):
                _fail("output_changed")
            verifier._check_chain(output)
            verifier._check_no_named_streams(output)
            if not verifier._same_file(output, stream, written):
                _fail("output_changed")
            stream.seek(0)
            actual = stream.read(len(raw) + 1)
            verified_digest = hashlib.sha256(actual).hexdigest()
            if len(actual) != len(raw) or verified_digest != hashlib.sha256(raw).hexdigest():
                _fail("output_content_mismatch")
            verifier._check_no_named_streams(output)
            if not verifier._same_file(output, stream, written):
                _fail("output_changed")
    except OSError:
        _fail("output_unavailable")
    return {"manifest_sha256": verified_digest, "manifest_bytes": len(raw),
            "file_count": len(records), "total_bytes": total, "generation": generation, "revision": revision}
