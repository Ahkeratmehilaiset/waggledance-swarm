# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""CLI for EMAIL-02 vendor email indexing.

The CLI reads a local JSON file containing already-exported email rows and
operator-supplied vendor rules, runs the pure EMAIL-02 core, and prints JSON.
It does not call Gmail, open SQLite/vector indexes, create drafts, send mail,
read credentials, or call external services.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence, TextIO

from waggledance.core.v3_13_0.email02_vendor_email_indexer import (
    index_email02_vendor_messages,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run EMAIL-02 vendor email indexing from JSON",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to local vendor+email JSON",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=False,
        help="Print indented JSON instead of compact JSON",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = parse_args(argv)
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("input JSON must be an object")
        result = index_email02_vendor_messages(payload).to_payload()
    except Exception as exc:
        print(
            json.dumps({
                "result_marker": "INVALID_INPUT_REFUSED",
                "error": str(exc),
            }, sort_keys=True),
            file=err,
        )
        return 2

    print(
        json.dumps(
            result,
            indent=2 if args.pretty else None,
            sort_keys=True,
        ),
        file=out,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
