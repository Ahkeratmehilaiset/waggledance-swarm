# SPDX-License-Identifier: BUSL-1.1
"""Verify a designated byte artifact; never normalize another representation.

This generic audit helper is not a bridge serializer, signature verifier or
authorization gate. The caller selects the artifact and supplies its expected
digest and length. Verification describes only the bytes read at that moment,
not origin, immutability, path admission or future file contents. The CLI is
read-only; an existing destination is never overwritten by the Python writer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Sequence


class ArtifactMismatch(ValueError):
    """The designated artifact no longer has the expected exact bytes."""


def verify_written_bytes(
    path: Path, expected_sha256: str, expected_length: int,
) -> dict[str, str | int]:
    if (not isinstance(expected_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or type(expected_length) is not int or expected_length < 0):
        raise ValueError("invalid_expected_binding")
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if len(data) != expected_length or actual != expected_sha256:
        raise ArtifactMismatch("written_bytes_differ")
    return {"sha256": actual, "length": len(data)}


def write_bytes_exclusive(path: Path, data: bytes) -> dict[str, str | int]:
    if type(data) is not bytes:
        raise TypeError("bytes_required")
    expected = hashlib.sha256(data).hexdigest()
    with path.open("xb") as stream:
        stream.write(data)
    return verify_written_bytes(path, expected, len(data))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--expected-length", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_written_bytes(args.path, args.expected_sha256, args.expected_length)
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
