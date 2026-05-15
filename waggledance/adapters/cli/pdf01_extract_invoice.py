# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""CLI for PDF-01 invoice field extraction.

The CLI reads a local JSON file containing already-extracted invoice text or
OCR lines, runs the pure PDF-01 solver core, and prints a JSON payload. It
does not open PDFs, call OCR/PDF libraries, read credentials, write state, or
submit payments.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence, TextIO

from waggledance.core.v3_13_0.pdf01_invoice_field_extractor import (
    extract_pdf01_invoice_fields,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run PDF-01 invoice field extraction from JSON",
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to local invoice-text JSON",
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
        result = extract_pdf01_invoice_fields(payload).to_payload()
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
