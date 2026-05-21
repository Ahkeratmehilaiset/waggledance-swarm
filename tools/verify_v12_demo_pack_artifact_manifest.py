# SPDX-License-Identifier: BUSL-1.1
"""Offline verifier for WD V12 supervisor demo pack artifact manifests."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_v12_supervisor_demo_pack import (  # noqa: E402
    ARTIFACT_MANIFEST_NAME,
    ARTIFACT_MANIFEST_VERSION,
    DEMO_VERSION,
)


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a WD V12 supervisor demo pack artifact manifest.",
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = verify_artifact_manifest(args.manifest)
    if args.json:
        print(json.dumps(report, sort_keys=True))
    elif report["ok"]:
        print(
            "v12 demo pack artifact manifest verification OK: "
            f"{report['verified_file_count']} files"
        )
    else:
        print(
            "v12 demo pack artifact manifest verification FAILED: "
            f"{len(report['errors'])} errors",
            file=sys.stderr,
        )
        for error in report["errors"]:
            print(f"- {error}", file=sys.stderr)
    return 0 if report["ok"] else 1


def verify_artifact_manifest(manifest_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    manifest_path = manifest_path.resolve()
    pack_dir = manifest_path.parent
    manifest = _read_json(manifest_path, errors)
    if not isinstance(manifest, dict):
        errors.append("manifest: expected JSON object")
        return _report(errors=errors, manifest=None, verified_file_count=0)

    _validate_manifest_header(manifest, errors)
    entries = manifest.get("files")
    if not isinstance(entries, list):
        errors.append("files: expected list")
        entries = []

    listed_paths: set[str] = set()
    verified_file_count = 0
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            errors.append(f"entry {index}: expected object")
            continue
        rel_path = _entry_path(entry, index, errors)
        expected_digest = entry.get("sha256")
        expected_size = entry.get("size_bytes")
        if rel_path is None:
            continue
        if rel_path in listed_paths:
            errors.append(f"duplicate path: {rel_path}")
            continue
        listed_paths.add(rel_path)
        if not isinstance(expected_digest, str) or not _SHA256_RE.match(expected_digest):
            errors.append(f"{rel_path}: invalid sha256")
            continue
        if not isinstance(expected_size, int) or expected_size < 0:
            errors.append(f"{rel_path}: invalid size_bytes")
            continue

        artifact_path = _safe_artifact_path(pack_dir, rel_path, errors)
        if artifact_path is None:
            continue
        if not artifact_path.exists():
            errors.append(f"missing file: {rel_path}")
            continue
        if not artifact_path.is_file():
            errors.append(f"not a file: {rel_path}")
            continue

        payload = artifact_path.read_bytes()
        actual_digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        if actual_digest != expected_digest:
            errors.append(f"sha256 mismatch: {rel_path}")
        if len(payload) != expected_size:
            errors.append(f"size_bytes mismatch: {rel_path}")
        if actual_digest == expected_digest and len(payload) == expected_size:
            verified_file_count += 1

    actual_paths = _actual_pack_paths(pack_dir, manifest_path)
    for rel_path in sorted(actual_paths - listed_paths):
        errors.append(f"unexpected file not listed: {rel_path}")

    return _report(
        errors=errors,
        manifest=manifest,
        verified_file_count=verified_file_count,
    )


def _validate_manifest_header(manifest: dict[str, Any], errors: list[str]) -> None:
    if manifest.get("manifest_version") != ARTIFACT_MANIFEST_VERSION:
        errors.append("manifest_version mismatch")
    if manifest.get("demo_version") != DEMO_VERSION:
        errors.append("demo_version mismatch")
    if not isinstance(manifest.get("generated_at_utc"), str):
        errors.append("generated_at_utc: expected string")
    file_count = manifest.get("file_count")
    files = manifest.get("files")
    if not isinstance(file_count, int) or file_count < 0:
        errors.append("file_count: expected non-negative integer")
    elif isinstance(files, list) and file_count != len(files):
        errors.append("file_count mismatch")


def _entry_path(
    entry: dict[str, Any],
    index: int,
    errors: list[str],
) -> str | None:
    raw_path = entry.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        errors.append(f"entry {index}: invalid path")
        return None
    if "\\" in raw_path:
        errors.append(f"entry {index}: path must use POSIX separators")
        return None
    if PurePosixPath(raw_path).is_absolute() or PureWindowsPath(raw_path).is_absolute():
        errors.append(f"entry {index}: path must be relative")
        return None
    if raw_path == ARTIFACT_MANIFEST_NAME:
        errors.append(f"entry {index}: manifest must not list itself")
        return None
    parts = PurePosixPath(raw_path).parts
    if any(part in {"", ".", ".."} for part in parts):
        errors.append(f"entry {index}: unsafe relative path")
        return None
    return raw_path


def _safe_artifact_path(
    pack_dir: Path,
    rel_path: str,
    errors: list[str],
) -> Path | None:
    artifact_path = (pack_dir / Path(*PurePosixPath(rel_path).parts)).resolve()
    try:
        artifact_path.relative_to(pack_dir)
    except ValueError:
        errors.append(f"unsafe path escapes pack: {rel_path}")
        return None
    return artifact_path


def _actual_pack_paths(pack_dir: Path, manifest_path: Path) -> set[str]:
    paths = set()
    for path in pack_dir.rglob("*"):
        if not path.is_file() or path.resolve() == manifest_path:
            continue
        paths.add(path.relative_to(pack_dir).as_posix())
    return paths


def _read_json(path: Path, errors: list[str]) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append("manifest: file not found")
    except json.JSONDecodeError:
        errors.append("manifest: invalid JSON")
    except OSError:
        errors.append("manifest: read failed")
    return None


def _report(
    *,
    errors: list[str],
    manifest: dict[str, Any] | None,
    verified_file_count: int,
) -> dict[str, Any]:
    file_count = manifest.get("file_count") if isinstance(manifest, dict) else None
    return {
        "ok": not errors,
        "manifest": "<redacted>",
        "pack_dir": "<redacted>",
        "manifest_version": manifest.get("manifest_version")
        if isinstance(manifest, dict)
        else None,
        "demo_version": manifest.get("demo_version")
        if isinstance(manifest, dict)
        else None,
        "file_count": file_count,
        "verified_file_count": verified_file_count,
        "errors": errors,
    }


if __name__ == "__main__":
    raise SystemExit(main())
