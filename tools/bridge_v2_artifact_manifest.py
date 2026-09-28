#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build/verify a head-independent, explicit file inventory (read-only).

This is the CONTENT layer, not a signature verifier or an activation gate.
The caller must bind its digest into an independently authorized envelope and
prove inventory completeness. Extra unlisted files are deliberately not covered.
The byte domain is materialized release artifacts, not Git blobs or normalized
text. Build after packaging; transfer the exact bytes to every verifier. A new
checkout with autocrlf conversion is NOT the same artifact. The separate packaging
layer must bind these bytes to the reviewed source and prove reproducibility.
Use a quiesced source: stat checks detect ordinary concurrent edits, but this
module is not a security boundary against a hostile same-user filesystem writer.
No filesystem writes, directory discovery, installation, or authority grants.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any

SCHEMA = "wd.artifact-content.v1"
MAX_ARTIFACTS = 10000
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_PRIVATE = {".git", ".codex-audit"}
_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", re.I)
_SHA = re.compile(r"[0-9a-f]{64}")


class ManifestError(ValueError):
    """Stable refusal code; no partial successful verification is returned."""


def _path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ManifestError("invalid_path")
    if ("\\" in value or ":" in value
            or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ManifestError("invalid_path")
    for part in value.split("/"):
        if (not part or part in {".", ".."} or part.endswith((".", " "))
                or any(c in part for c in '<>"|?*') or _DEVICE.fullmatch(part)
                or part.casefold() in _PRIVATE):
            raise ManifestError("invalid_path")
    return value


def _paths(values: Any) -> list[str]:
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_ARTIFACTS:
        raise ManifestError("invalid_inventory")
    result = [_path(value) for value in values]
    if len({p.casefold() for p in result}) != len(result):
        raise ManifestError("duplicate_path")
    return sorted(result)


def _is_link(info: os.stat_result) -> bool:
    # Windows junctions/reparse points must be rejected as well as symlinks.
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _root(value: Path) -> Path:
    root = Path(os.path.abspath(value))
    for current in [*reversed(root.parents), root]:
        info = current.lstat()
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
            raise ManifestError("unsafe_root")
    return root


def _identity(info: os.stat_result) -> tuple[int, ...]:
    # Windows CPython path-stat and fd-stat can expose different ctime meanings
    # (creation versus change). Compare ctime only within the same API below.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _row(root: Path, relative: str) -> dict[str, Any]:
    target = root
    parts = relative.split("/")
    for index, part in enumerate(parts):
        # Exact on-disk spelling, not merely case-insensitive/8.3 resolution.
        with os.scandir(target) as entries:
            if not any(entry.name == part for entry in entries):
                raise ManifestError("noncanonical_disk_path")
        target = target / part
        info = target.lstat()
        if _is_link(info):
            raise ManifestError("linked_artifact")
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise ManifestError("invalid_parent")
    if not stat.S_ISREG(info.st_mode):
        raise ManifestError("not_regular_file")
    if info.st_nlink != 1:
        raise ManifestError("hardlinked_artifact")
    before = _identity(info)
    digest = hashlib.sha256()
    count = 0
    with target.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if _identity(opened) != before or opened.st_nlink != 1:
            raise ManifestError("concurrent_change")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            count += len(block)
        finished = os.fstat(stream.fileno())
        if (_identity(finished) != before
                or finished.st_ctime_ns != opened.st_ctime_ns or finished.st_nlink != 1):
            raise ManifestError("concurrent_change")
    after = target.lstat()
    if (_is_link(after) or _identity(after) != before or count != info.st_size
            or after.st_ctime_ns != info.st_ctime_ns or after.st_nlink != 1):
        raise ManifestError("concurrent_change")
    return {"path": relative, "size": count, "sha256": digest.hexdigest()}


def build_manifest(root: Path, paths: list[str]) -> dict[str, Any]:
    """Hash only explicit portable paths; reject empty/aliased inventories."""
    inventory = _paths(paths)
    try:
        resolved = _root(root)
        rows = [_row(resolved, path) for path in inventory]
    except OSError as exc:
        raise ManifestError("artifact_io_error") from exc
    return {"schema": SCHEMA, "byte_domain": "materialized-artifacts", "artifacts": rows}


def _validate(manifest: Any) -> dict[str, Any]:
    if (not isinstance(manifest, dict) or set(manifest) != {"schema", "byte_domain", "artifacts"}
            or manifest["schema"] != SCHEMA or manifest["byte_domain"] != "materialized-artifacts"):
        raise ManifestError("invalid_manifest")
    rows = manifest["artifacts"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ARTIFACTS:
        raise ManifestError("invalid_inventory")
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "size", "sha256"}:
            raise ManifestError("invalid_artifact")
        if type(row["size"]) is not int or not 0 <= row["size"] <= 2**63 - 1:
            raise ManifestError("invalid_size")
        if not isinstance(row["sha256"], str) or not _SHA.fullmatch(row["sha256"]):
            raise ManifestError("invalid_sha256")
    paths = [row["path"] for row in rows]
    if _paths(paths) != paths:
        raise ManifestError("noncanonical_order")
    return manifest


def canonical_bytes(manifest: Any) -> bytes:
    """UTF-8 JSON, sorted keys, no whitespace; hash excludes its own digest."""
    return json.dumps(_validate(manifest), ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("duplicate_json_key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ManifestError("nonfinite_json_number")


def parse_manifest(data: bytes) -> dict[str, Any]:
    if not isinstance(data, bytes) or len(data) > MAX_MANIFEST_BYTES:
        raise ManifestError("invalid_manifest_bytes")
    try:
        return _validate(json.loads(data.decode("utf-8"), object_pairs_hook=_pairs,
                                    parse_constant=_constant))
    except ManifestError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ManifestError("invalid_json") from exc


def verify_manifest(root: Path, manifest: Any) -> dict[str, Any]:
    validated = _validate(manifest)
    actual = build_manifest(root, [row["path"] for row in validated["artifacts"]])
    if canonical_bytes(actual) != canonical_bytes(validated):
        raise ManifestError("content_mismatch")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--root", type=Path, required=True)
    build.add_argument("--path", action="append", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--root", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            output = canonical_bytes(build_manifest(args.root, args.path))
        else:
            with args.manifest.open("rb") as stream:
                manifest = parse_manifest(stream.read(MAX_MANIFEST_BYTES + 1))
            verified = verify_manifest(args.root, manifest)
            output = json.dumps({"verified": True, "scope": "listed_content_only",
                                 "manifest_sha256": hashlib.sha256(canonical_bytes(verified)).hexdigest()},
                                sort_keys=True).encode("utf-8")
        sys.stdout.buffer.write(output + b"\n")
        return 0
    except (ManifestError, OSError) as exc:
        reason = str(exc) if isinstance(exc, ManifestError) else "manifest_io_error"
        print(reason, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
