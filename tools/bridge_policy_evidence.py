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
import sys
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

#: The root must live on one of these drives. The module documented a
#: persistent-C root but the original drive check accepted any letter, so the
#: promise and the code disagreed; the allowlist is now the code.
ALLOWED_ROOT_DRIVES = frozenset({"C"})

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_DOCUMENTS = 64
MAX_TOTAL_BYTES = 32 * 1024 * 1024

_UNSAFE_COMPONENT = re.compile(r"~[0-9]")
_READ_CHUNK = 256 * 1024

#: Diagnostics are fixed strings. Earlier versions interpolated the offending
#: manifest key or path component, which echoed caller-controlled data into
#: every log and reply that carried a reason.
REASON_ALIAS = "ambiguous_windows_alias"
REASON_REPARSE = "reparse_point_in_path"


class SourceRejected(Exception):
    """A single source could not be used. Never escapes; becomes a reason."""


def _reject_alias(component: str) -> None:
    if component in ("", "."):
        return
    if component.endswith((".", " ")) or component.startswith(" ") \
            or _UNSAFE_COMPONENT.search(component):
        raise SourceRejected(REASON_ALIAS)


def allowlisted_root(root: Any, *, allowed_drives: frozenset = ALLOWED_ROOT_DRIVES) -> Path:
    """Validate the one root everything must live under.

    Must be an existing absolute directory on an **allowlisted** drive, with no
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
    drive = re.match(r"^([A-Za-z]):/", text)
    if not drive:
        raise InputError("root must be a drive-qualified local path")
    if drive.group(1).upper() not in {d.upper() for d in allowed_drives}:
        # No drive letter in the message: it is caller-supplied.
        raise InputError("root drive is not allowlisted")
    for part in text.split("/"):
        try:
            _reject_alias(part)
        except SourceRejected:
            raise InputError("root is not a plain path")
    absolute = candidate.absolute()
    for component in (absolute, *absolute.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            raise InputError("root does not exist")
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise InputError("root contains a link or reparse point")
    if not absolute.is_dir():
        raise InputError("root must be a directory")
    return absolute


def _resolve_under(root: Path, relative: Any) -> Path:
    """A relative POSIX path, confined to ``root``, with no aliasing."""
    if not isinstance(relative, str) or not relative or len(relative) > 2048:
        raise SourceRejected("source_path_missing")
    # Deliberately NOT stripped. The previous version called .strip() first,
    # which silently normalised a trailing-space alias into an acceptable name
    # before the alias check could ever see it.
    if relative != relative.strip():
        raise SourceRejected("source_path_has_surrounding_whitespace")
    raw = relative.replace("\\", "/")
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
            raise SourceRejected(REASON_REPARSE)
    absolute = target.absolute()
    try:
        absolute.relative_to(root)
    except ValueError:
        raise SourceRejected("source_escapes_root")
    return absolute


def handle_final_path(fileno: int) -> str | None:
    """The real path of an OPEN descriptor, or ``None`` if unsupported here.

    This closes the open-time race that no pre-open path check can: it reports
    what we actually hold open, after whatever link or rename the filesystem
    applied. ``None`` means the platform cannot answer, and the caller fails
    closed rather than assuming success.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            import msvcrt
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            buffer = ctypes.create_unicode_buffer(32768)
            written = kernel32.GetFinalPathNameByHandleW(
                ctypes.c_void_p(msvcrt.get_osfhandle(fileno)), buffer, 32768, 0)
            if not written or written >= 32768:
                return None
            return buffer.value
        except (OSError, ImportError, AttributeError, ValueError):
            return None
    try:
        return os.readlink(f"/proc/self/fd/{fileno}")
    except OSError:
        return None


def _normalise_final(path: str) -> str:
    text = str(path).replace("\\", "/")
    for prefix in ("//?/UNC/", "//?/"):
        if text.upper().startswith(prefix.upper()):
            text = text[len(prefix):]
            break
    return text.rstrip("/").casefold()


def _hash_file(path: Path, root: Path, *, max_bytes: int) -> tuple[str, int]:
    """Hash bytes read from a descriptor whose identity we validated.

    Three checks, because each catches what the others miss:

    * ``st_ino``/``st_dev`` before and after prove the descriptor still refers
      to the same filesystem object. Size and mtime alone did not: a
      substitution matching both would have passed.
    * The final path of the OPEN HANDLE proves what we opened is still inside
      the root, which a pre-open path check cannot guarantee.
    * Size, mtime and bytes-read detect in-place mutation.

    This DETECTS interference; it does not prevent it. A privileged writer that
    restored every observable would defeat all three, which is one reason
    content authenticity is never claimed anywhere in this module.
    """
    limit = min(MAX_FILE_BYTES, max_bytes)
    try:
        before = path.stat()
    except OSError:
        raise SourceRejected("source_unreadable")
    if not stat.S_ISREG(before.st_mode):
        raise SourceRejected("source_is_not_a_regular_file")
    if before.st_size > limit:
        raise SourceRejected("source_exceeds_size_bound")
    digest = hashlib.sha256()
    read = 0
    try:
        with open(path, "rb") as handle:
            final = handle_final_path(handle.fileno())
            if final is None:
                raise SourceRejected("open_handle_path_validation_unsupported")
            opened = _normalise_final(final)
            base = _normalise_final(str(root))
            if not (opened == base or opened.startswith(base + "/")):
                raise SourceRejected("open_handle_escapes_root")
            if opened != _normalise_final(str(path.absolute())):
                raise SourceRejected("open_handle_target_mismatch")
            while read < before.st_size:
                chunk = handle.read(min(_READ_CHUNK, before.st_size - read, limit - read))
                if not chunk:
                    break
                read += len(chunk)
                if read > limit:
                    raise SourceRejected("source_grew_past_size_bound_during_read")
                digest.update(chunk)
            after = os.fstat(handle.fileno())
    except SourceRejected:
        raise
    except OSError:
        raise SourceRejected("source_unreadable")
    if (after.st_ino, after.st_dev) != (before.st_ino, before.st_dev):
        raise SourceRejected("source_identity_changed_during_read")
    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns) \
            or read != before.st_size:
        raise SourceRejected("source_changed_during_read")
    return digest.hexdigest(), read


def _hashed_document(root: Path, relative: Any, budget: dict) -> dict:
    """Charge the budget BEFORE reading, and cap the read to what remains.

    The earlier version hashed first and checked afterwards, so the advertised
    limits could be exceeded by a whole document before anything complained.
    Budgets are shared across all classes, so the remaining allowance is what
    bounds each read.
    """
    if budget["count"] + 1 > MAX_DOCUMENTS:
        raise SourceRejected("document_count_bound_exceeded")
    remaining = MAX_TOTAL_BYTES - budget["total"]
    if remaining <= 0:
        raise SourceRejected("total_size_bound_exceeded")
    budget["count"] += 1
    resolved = _resolve_under(root, relative)
    digest, size = _hash_file(resolved, root, max_bytes=remaining)
    budget["total"] += size
    return {"ref": relative.replace("\\", "/"), "sha256": digest}


def _mapping(value: Any) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _policy_entry(root: Path, spec: Mapping[str, Any], budget: dict) -> dict:
    """Policy evidence must name the DOMAIN it came from.

    ``configs/policy/**`` is deployment/runtime policy. Bridge governance
    policy is a different corpus. They are both "policy", and conflating them
    would let a deployment document look like authorization for a bridge
    decision, so the domain is mandatory and travels with the evidence.
    """
    domain = spec.get("domain")
    if not _text(domain):
        raise SourceRejected("policy_domain_missing")
    documents = spec.get("documents")
    if not isinstance(documents, Sequence) or isinstance(documents, (str, bytes)) \
            or not documents:
        raise SourceRejected("policy_documents_missing")
    return {"domain": domain.strip(),
            "documents": [_hashed_document(root, item, budget) for item in documents]}


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
        # The count is ours; the key names are the caller's and are not echoed.
        raise InputError(
            f"manifest declares {len(unknown_classes)} unsupported class key(s)")

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
