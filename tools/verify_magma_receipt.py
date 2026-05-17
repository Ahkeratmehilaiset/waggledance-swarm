# SPDX-License-Identifier: BUSL-1.1
"""Offline verifier for MAGMA receipt fixture chains.

v1 intentionally avoids signing, anchoring, network calls, and runtime
integration. It verifies the thin spine: schema shape, canonical digest
bindings, EvaluationResult binding, and manifest-ordered receipt hash-chain
continuity. Unsigned v1 detects in-chain receipt tampering; tail receipt field
tamper needs a successor receipt or a future signature verifier.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

import jsonschema


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


SCHEMA_DIR = ROOT / "schemas" / "v3_13_0"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify an offline MAGMA receipt manifest.",
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--expected-charter-digest", default=None)
    parser.add_argument("--expected-policy-digest", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = verify_manifest(
        args.manifest,
        expected_charter_digest=args.expected_charter_digest,
        expected_policy_digest=args.expected_policy_digest,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True))
    elif report["ok"]:
        print(f"magma receipt verification OK: {report['receipt_count']} receipts")
    else:
        print(
            f"magma receipt verification FAILED: {len(report['errors'])} errors",
            file=sys.stderr,
        )
        for error in report["errors"]:
            print(f"- {error}", file=sys.stderr)
    return 0 if report["ok"] else 1


def verify_manifest(
    manifest_path: Path,
    *,
    expected_charter_digest: str | None = None,
    expected_policy_digest: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    manifest_path = manifest_path.resolve()
    manifest = _read_json(manifest_path, errors, "manifest")
    entries = _entries(manifest, errors)
    receipt_validator = _validator("magma_receipt.v1.json")
    evaluation_validator = _validator("evaluation_result.v0.json")
    previous_receipt_hash: str | None = None
    verified = 0

    for index, entry in enumerate(entries, 1):
        label = f"entry {index}"
        receipt = _read_json(_entry_path(manifest_path, entry, "receipt"), errors, label)
        payload = _read_json(_entry_path(manifest_path, entry, "payload"), errors, label)
        evaluation = _read_json(
            _entry_path(manifest_path, entry, "evaluation_result"),
            errors,
            label,
        )
        if (
            not isinstance(receipt, dict)
            or not isinstance(payload, dict)
            or not isinstance(evaluation, dict)
        ):
            continue

        _validate_schema(receipt_validator, receipt, errors, f"{label} receipt")
        _validate_schema(
            evaluation_validator,
            evaluation,
            errors,
            f"{label} evaluation_result",
        )

        payload_digest = sha256_digest(payload)
        if receipt.get("canonical_payload_digest") != payload_digest:
            errors.append(
                f"{label}: canonical_payload_digest mismatch "
                f"(expected {receipt.get('canonical_payload_digest')}, got {payload_digest})"
            )

        evaluation_digest = sha256_digest(evaluation)
        if receipt.get("evaluation_result_digest") != evaluation_digest:
            errors.append(
                f"{label}: evaluation_result_digest mismatch "
                f"(expected {receipt.get('evaluation_result_digest')}, got {evaluation_digest})"
            )

        if receipt.get("prev_receipt_hash") != previous_receipt_hash:
            errors.append(
                f"{label}: prev_receipt_hash mismatch "
                f"(expected {previous_receipt_hash}, got {receipt.get('prev_receipt_hash')})"
            )
        if (
            expected_charter_digest is not None
            and receipt.get("charter_digest") != expected_charter_digest
        ):
            errors.append(
                f"{label}: charter_digest mismatch "
                f"(expected {expected_charter_digest}, got {receipt.get('charter_digest')})"
            )
        if (
            expected_policy_digest is not None
            and receipt.get("policy_digest") != expected_policy_digest
        ):
            errors.append(
                f"{label}: policy_digest mismatch "
                f"(expected {expected_policy_digest}, got {receipt.get('policy_digest')})"
            )

        previous_receipt_hash = sha256_digest(receipt)
        verified += 1

    return {
        "ok": not errors,
        "manifest": str(manifest_path),
        "chain_id": manifest.get("chain_id") if isinstance(manifest, dict) else None,
        "receipt_count": verified,
        "errors": errors,
    }


def _validator(schema_name: str) -> jsonschema.Draft7Validator:
    schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(
        schema,
        format_checker=jsonschema.FormatChecker(),
    )


def _read_json(path: Path, errors: list[str], label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        errors.append(f"{label}: cannot read {path}: {exc}")
    except json.JSONDecodeError as exc:
        errors.append(f"{label}: invalid JSON in {path}: {exc}")
    return None


def _entries(manifest: Any, errors: list[str]) -> list[dict[str, str]]:
    if not isinstance(manifest, dict):
        errors.append("manifest: must be a JSON object")
        return []
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        errors.append("manifest: entries must be a non-empty array")
        return []

    normalized: list[dict[str, str]] = []
    for index, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            errors.append(f"entry {index}: must be an object")
            continue
        missing = [
            field
            for field in ("receipt", "payload", "evaluation_result")
            if not isinstance(entry.get(field), str) or not entry.get(field)
        ]
        if missing:
            errors.append(f"entry {index}: missing path fields: {', '.join(missing)}")
            continue
        normalized.append(
            {
                "receipt": entry["receipt"],
                "payload": entry["payload"],
                "evaluation_result": entry["evaluation_result"],
            }
        )
    return normalized


def _entry_path(manifest_path: Path, entry: dict[str, str], field: str) -> Path:
    return (manifest_path.parent / entry[field]).resolve()


def _validate_schema(
    validator: jsonschema.Draft7Validator,
    value: dict[str, Any],
    errors: list[str],
    label: str,
) -> None:
    for error in sorted(validator.iter_errors(value), key=lambda item: list(item.path)):
        path = ".".join(str(part) for part in error.path) or "<root>"
        errors.append(f"{label}: schema error at {path}: {error.message}")


if __name__ == "__main__":
    raise SystemExit(main())
