# SPDX-License-Identifier: BUSL-1.1
"""Offline runtime-process preflight; this module does not start a process.

The caller must independently anchor every supplied pin and authorize every
path. This preflight checks three exact files and the external runtime manifest
using the pinned verifier before any runtime module, bootstrap, or model is
executed. The verifier is the separate packaged adaptation of
``sandbox/mind/step2/runtime_manifest.py`` at commit
``e98c5aef5bd3bd66c61e5692e738f17a4ecf6beb``. It is loaded from the exact
bytes whose digest is checked, not by an ambient import path.

There is no subprocess, network, model load, scratch creation, or write here.
There is also no assertion of process readiness, native DLL closure, OS network
isolation, or protection against same-user changes after verification. The
file and tree checks are synchronous: their wall-clock duration is not bounded
by a process watchdog. Those capabilities require an independently reviewed
runtime launcher and are deliberately not simulated by this module.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import re
import stat

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MAX_CODE = 2 * 1024 * 1024
_MAX_INTERPRETER = 512 * 1024 * 1024
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.IGNORECASE)


class RuntimePreflightRefused(ValueError):
    """Fixed, path-free refusal for the offline preflight."""


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read_pinned_file(value, pin, *, limit, retain=False):
    if type(pin) is not str or not _SHA256.fullmatch(pin):
        raise ValueError("pin")
    path = Path(value)
    if (not path.is_absolute() or ".." in path.parts or
            (os.name == "nt" and path.anchor.startswith("\\\\"))):
        raise ValueError("path")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        if ":" in part or part.endswith((".", " ")) or _DEVICE.fullmatch(part):
            raise ValueError("path")
        current /= part
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("reparse")
    before = os.lstat(path)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or not 0 < before.st_size <= limit):
        raise ValueError("file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    digest = hashlib.sha256()
    blocks = [] if retain else None
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or _identity(before) != _identity(opened):
            raise ValueError("file_changed")
        remaining = opened.st_size
        while remaining:
            block = stream.read(min(1024 * 1024, remaining))
            if not block:
                raise ValueError("file_changed")
            remaining -= len(block)
            digest.update(block)
            if retain:
                blocks.append(block)
        after = os.fstat(stream.fileno())
        final = os.lstat(path)
        if (stream.read(1) or not stat.S_ISREG(final.st_mode) or final.st_nlink != 1
                or _identity(before) != _identity(after) or _identity(before) != _identity(final)):
            raise ValueError("file_changed")
    if not hmac.compare_digest(digest.hexdigest(), pin):
        raise ValueError("pin")
    return b"".join(blocks) if retain else None


def verify_runtime_process_preflight(*, interpreter, interpreter_sha256, bootstrap, bootstrap_sha256,
                                     verifier, verifier_sha256, runtime_root, runtime_manifest,
                                     runtime_manifest_sha256, generation, revision):
    """Verify external pins and runtime tree; return evidence, not permission to run.

    All pins must come from an independent manifest/authority, not the runtime
    tree being checked. Interpreter-tree and native-library closure remain the
    caller's independent responsibility. No query or private message is read.
    """
    result = None
    try:
        # Wrong bootstrap/interpreter/verifier pins refuse before verifier code.
        _read_pinned_file(interpreter, interpreter_sha256, limit=_MAX_INTERPRETER)
        _read_pinned_file(bootstrap, bootstrap_sha256, limit=_MAX_CODE)
        verifier_bytes = _read_pinned_file(verifier, verifier_sha256, limit=_MAX_CODE, retain=True)
        namespace = {"__name__": "_wd_pinned_runtime_manifest", "__file__": str(verifier)}
        exec(compile(verifier_bytes, "<pinned-runtime-manifest>", "exec"), namespace)
        verify = namespace.get("verify_runtime")
        if not callable(verify):
            raise ValueError("verifier")
        evidence = verify(runtime_root, runtime_manifest, runtime_manifest_sha256,
                          expected_generation=generation, expected_revision=revision)
        if (type(evidence) is not dict or evidence.get("manifest_sha256") != runtime_manifest_sha256
                or evidence.get("generation") != generation or evidence.get("revision") != revision
                or type(evidence.get("file_count")) is not int or type(evidence.get("total_bytes")) is not int):
            raise ValueError("evidence")
        result = {"schema": "wd.mind-runtime-process-preflight.v1", "status": "VERIFIED_PREFLIGHT_ONLY",
                  "process_started": False, "model_loaded": False, "network_isolation": "UNKNOWN",
                  "native_closure": "UNKNOWN", "same_user_race": "UNKNOWN",
                  "runtime_manifest_sha256": runtime_manifest_sha256,
                  "generation": generation, "revision": revision,
                  "file_count": evidence["file_count"], "total_bytes": evidence["total_bytes"]}
    except Exception:
        pass
    if result is None:
        raise RuntimePreflightRefused("runtime_process_preflight_refused") from None
    return result
