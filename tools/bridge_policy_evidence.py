# SPDX-License-Identifier: BUSL-1.1
"""Bounded, read-only emitter of the evidence the epoch builder consumes.

``bridge_policy_epochs`` validates that a digest is *well formed*. That is hash
shape, not verified evidence. This module closes that gap by reading the actual
bytes of declared sources under an allowlisted root and hashing what it read.

What it does, precisely:

* Every source is a **relative POSIX path under one explicit allowlisted root**
  on the persistent C drive. Absolute paths, drive letters, ``..``, alternate
  streams, Windows short-name and trailing-dot/space aliases, and any component
  that is a symlink or reparse point are all refused rather than normalised.
* It **hashes the bytes it actually read**, and re-stats afterwards. If size or
  mtime moved between the two stats the source is rejected as changed during
  read rather than reported with a digest that may describe neither version.
* A class whose sources are missing or unusable becomes **unavailable**, with
  reasons, and is simply omitted from the emitted evidence. Because the epoch
  builder marks an absent class unknown, and admission requires all five
  epochs, an incomplete world parks by construction.

Two things this module deliberately does *not* do:

* It does not produce ``native`` evidence. Native identity comes from the live
  binding, not from a file, and inventing it from disk would be fabricating.
* It does not attest content. See ``content_authenticity`` below.

**Hash integrity is not content authenticity.** Hash integrity is the claim
"this digest is of the bytes that were on disk at read time, and the file did
not move under us". That is what this module can establish, and it reports it
as ``hash_integrity``. Content authenticity would be the claim "these bytes are
the legitimate, intended content" -- that needs a signature or a trusted
publisher, neither of which exists here. ``content_authenticity`` is therefore
always ``"unverified"``, on every path, including a completely successful read.
A caller that needs authenticity must obtain it somewhere else.

Observation only. Nothing is written, no service or collector is touched, and
the emitted evidence carries no permission.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence

try:  # package import first, mirroring the sibling capacity modules
    from tools.bridge_capacity_advisor import InputError, _text
    from tools.bridge_policy_epochs import snapshot as epoch_snapshot
except ModuleNotFoundError:  # pragma: no cover - exercised by flat-layout callers
    from bridge_capacity_advisor import InputError, _text
    from bridge_policy_epochs import snapshot as epoch_snapshot

EVIDENCE_SOURCE_SCHEMA = "wd.policy-evidence-source.v1"

#: Classes this emitter can back with file content. ``native`` is absent on
#: purpose: it is live identity, not a document.
FILE_BACKED_CLASSES = ("policy", "catalog", "qualification", "profile")

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_DOCUMENTS = 64
MAX_TOTAL_BYTES = 32 * 1024 * 1024

_UNSAFE_COMPONENT = re.compile(r"~[0-9]")
_READ_CHUNK = 256 * 1024


class SourceRejected(Exception):
    """A single source could not be used. Never escapes; becomes a reason."""


def _reject_alias(component: str) -> None:
    if component in ("", "."):
        return
    if component.endswith((".", " ")) or _UNSAFE_COMPONENT.search(component):
        raise SourceRejected(f"ambiguous_windows_alias:{component}")


def allowlisted_root(root: Any) -> Path:
    """Validate the one root everything must live under.

    Must be an existing absolute directory on a local fixed drive, with no
    reparse point anywhere in its chain.
    """
    # A caller naturally holds a Path; requiring str would be friction with no
    # safety benefit, since every component is validated below either way.
    if isinstance(root, os.PathLike):
        root = os.fspath(root)
    if not _text(root):
        raise InputError("root must be a non-empty path string")
    candidate = Path(str(root))
    if not candidate.is_absolute():
        raise InputError("root must be absolute")
    text = str(candidate).replace("\\", "/")
    if not re.match(r"^[A-Za-z]:/", text):
        raise InputError("root must be a drive-qualified local path")
    for part in text.split("/"):
        try:
            _reject_alias(part)
        except SourceRejected as exc:
            raise InputError(f"root is not a plain path: {exc}")
    absolute = candidate.absolute()
    for component in (absolute, *absolute.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            raise InputError(f"root does not exist: {component}")
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise InputError(f"root contains a link or reparse point: {component}")
    if not absolute.is_dir():
        raise InputError("root must be a directory")
    return absolute


def _resolve_under(root: Path, relative: Any) -> Path:
    """A relative POSIX path, confined to ``root``, with no aliasing."""
    if not _text(relative):
        raise SourceRejected("source_path_missing")
    raw = str(relative).replace("\\", "/").strip()
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise SourceRejected("absolute_source_path_forbidden")
    if ":" in raw:
        raise SourceRejected("alternate_data_stream_forbidden")
    parts = raw.split("/")
    if any(part == ".." for part in parts):
        raise SourceRejected("path_traversal_forbidden")
    for part in parts:
        _reject_alias(part)
    target = (root / raw)
    for component in (target, *target.parents):
        if component == root.parent:
            break
        try:
            info = component.lstat()
        except FileNotFoundError:
            if component == target:
                raise SourceRejected("source_missing")
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise SourceRejected(f"reparse_point_in_path:{component.name}")
    absolute = target.absolute()
    try:
        absolute.relative_to(root)
    except ValueError:
        raise SourceRejected("source_escapes_root")
    return absolute


def _hash_file(path: Path) -> tuple[str, int]:
    """Hash the bytes actually read, and refuse a file that moved under us.

    The stat-read-stat sandwich DETECTS a concurrent writer; it does not
    prevent one. A writer that restores size and mtime would defeat it, which
    is one reason content authenticity is not claimed anywhere in this module.
    """
    try:
        before = path.stat()
    except OSError as exc:
        raise SourceRejected(f"source_unreadable:{type(exc).__name__}")
    if not stat.S_ISREG(before.st_mode):
        raise SourceRejected("source_is_not_a_regular_file")
    if before.st_size > MAX_FILE_BYTES:
        raise SourceRejected("source_exceeds_size_bound")
    digest = hashlib.sha256()
    read = 0
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(_READ_CHUNK)
                if not chunk:
                    break
                read += len(chunk)
                if read > MAX_FILE_BYTES:
                    raise SourceRejected("source_grew_past_size_bound_during_read")
                digest.update(chunk)
            # Stat the OPEN DESCRIPTOR, not the path. A path re-stat would
            # happily describe a different file if the name was swapped while
            # we were reading; the descriptor still refers to what we hashed.
            after = os.fstat(handle.fileno())
    except SourceRejected:
        raise
    except OSError as exc:
        raise SourceRejected(f"source_unreadable:{type(exc).__name__}")
    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns) \
            or read != before.st_size:
        raise SourceRejected("source_changed_during_read")
    return digest.hexdigest(), read


def _hashed_document(root: Path, relative: Any, budget: dict) -> dict:
    resolved = _resolve_under(root, relative)
    digest, size = _hash_file(resolved)
    budget["total"] += size
    budget["count"] += 1
    if budget["count"] > MAX_DOCUMENTS:
        raise SourceRejected("document_count_bound_exceeded")
    if budget["total"] > MAX_TOTAL_BYTES:
        raise SourceRejected("total_size_bound_exceeded")
    return {"ref": str(relative).replace("\\", "/").strip(), "sha256": digest}


def _mapping(value: Any) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _policy_entry(root: Path, spec: Mapping[str, Any], budget: dict) -> dict:
    documents = spec.get("documents")
    if not isinstance(documents, Sequence) or isinstance(documents, (str, bytes)) \
            or not documents:
        raise SourceRejected("policy_documents_missing")
    return {"documents": [_hashed_document(root, item, budget) for item in documents]}


def _catalog_entry(root: Path, spec: Mapping[str, Any], budget: dict) -> dict:
    return _hashed_document(root, spec.get("document"), budget)


def _qualification_entry(root: Path, spec: Mapping[str, Any], budget: dict) -> dict:
    """A report plus measured ids and verdicts. A model label is not evidence."""
    entry = _hashed_document(root, spec.get("document"), budget)
    ids = spec.get("evidence_ids")
    verdicts = spec.get("verdicts")
    if not isinstance(ids, list) or not ids or not all(_text(x) for x in ids):
        raise SourceRejected("qualification_evidence_ids_missing")
    if not isinstance(verdicts, Mapping) or not verdicts \
            or not all(_text(k) and _text(v) for k, v in verdicts.items()):
        raise SourceRejected("qualification_verdicts_missing")
    entry.update(evidence_ids=list(ids), verdicts=dict(verdicts))
    return entry


def _profile_entry(root: Path, spec: Mapping[str, Any], budget: dict) -> dict:
    entry = _hashed_document(root, spec.get("document"), budget)
    if not _text(spec.get("profile_id")):
        raise SourceRejected("profile_id_missing")
    if not _text(spec.get("authorization_ref")):
        raise SourceRejected("profile_authorization_ref_missing")
    entry.update(profile_id=spec["profile_id"].strip(),
                 authorization_ref=spec["authorization_ref"].strip())
    return entry


_BUILDERS = {
    "policy": _policy_entry,
    "catalog": _catalog_entry,
    "qualification": _qualification_entry,
    "profile": _profile_entry,
}


def emit_evidence(*, root: Any, manifest: Any) -> dict:
    """Read declared sources and emit evidence for the epoch builder.

    Returns ``{schema, evidence, unavailable, hash_integrity,
    content_authenticity, bytes_hashed, documents_hashed}``. ``evidence`` is
    shaped exactly for ``bridge_policy_epochs.snapshot``.
    """
    base = allowlisted_root(root)
    if not isinstance(manifest, Mapping):
        raise InputError("manifest must be an object")
    unknown_classes = [k for k in manifest if k not in FILE_BACKED_CLASSES]
    if unknown_classes:
        raise InputError(f"manifest declares unsupported classes: {sorted(unknown_classes)}")

    evidence: dict[str, Any] = {}
    unavailable: dict[str, list[str]] = {}
    budget = {"total": 0, "count": 0}
    for name in FILE_BACKED_CLASSES:
        if name not in manifest:
            unavailable[name] = ["not_declared_in_manifest"]
            continue
        try:
            evidence[name] = _BUILDERS[name](base, _mapping(manifest[name]), budget)
        except SourceRejected as exc:
            unavailable[name] = [str(exc)]

    return {
        "schema": EVIDENCE_SOURCE_SCHEMA,
        "root": str(base).replace("\\", "/"),
        "evidence": evidence,
        "unavailable": unavailable,
        # We hashed the bytes we read and the files did not move under us.
        "hash_integrity": not unavailable and bool(evidence),
        # Never anything else. Nothing here signs or attests content.
        "content_authenticity": "unverified",
        "documents_hashed": budget["count"],
        "bytes_hashed": budget["total"],
        "execution_allowed": False,
    }


def inspect_sources(*, root: Any, manifest: Any) -> dict:
    """Report per-class availability without emitting evidence.

    Intended for the honest question "what can this machine actually back
    today?" rather than for building a snapshot.
    """
    emitted = emit_evidence(root=root, manifest=manifest)
    return {
        "schema": EVIDENCE_SOURCE_SCHEMA,
        "root": emitted["root"],
        "available": sorted(emitted["evidence"]),
        "unavailable": emitted["unavailable"],
        "native": "not file backed; supplied by the live binding, never read from disk",
        "content_authenticity": "unverified",
    }


def build_snapshot(*, root: Any, manifest: Any, binding: Any) -> dict:
    """Emit evidence and hand it straight to the epoch builder."""
    emitted = emit_evidence(root=root, manifest=manifest)
    snap = epoch_snapshot(emitted["evidence"], binding=binding)
    return {
        "schema": EVIDENCE_SOURCE_SCHEMA,
        "source": emitted,
        "snapshot": snap,
        "content_authenticity": "unverified",
        "execution_allowed": False,
    }
