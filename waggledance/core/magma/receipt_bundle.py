# SPDX-License-Identifier: BUSL-1.1
"""Shared local MAGMA receipt-bundle writer.

The helper owns only deterministic file layout and manifest emission. Callers
provide already-built payload/evaluation/receipt triples and an optional
verifier callable, so the core MAGMA layer does not depend on CLI modules.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


_LABEL_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class ReceiptBundleEntry:
    """One payload/evaluation/receipt triple in a local receipt bundle."""

    label: str
    payload: Any
    evaluation_result: dict[str, Any]
    receipt: dict[str, Any]


def write_receipt_bundle(
    *,
    out_dir: Path,
    chain_id: str,
    entries: Sequence[ReceiptBundleEntry],
    verify_manifest: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    """Write a local MAGMA receipt bundle and return a verifier-style summary."""
    if not entries:
        raise ValueError("receipt bundle requires at least one entry")
    labels = [_safe_label(entry.label) for entry in entries]
    _prepare_out_dir(out_dir)

    manifest_entries: list[dict[str, str]] = []
    for index, (entry, label) in enumerate(zip(entries, labels, strict=True), 1):
        payload_name = f"payload-{index:03d}-{label}.json"
        evaluation_name = f"evaluation-{index:03d}-{label}.json"
        receipt_name = f"receipt-{index:03d}-{label}.json"
        _write_json(out_dir / payload_name, entry.payload)
        _write_json(out_dir / evaluation_name, entry.evaluation_result)
        _write_json(out_dir / receipt_name, entry.receipt)
        manifest_entries.append(
            {
                "payload": payload_name,
                "evaluation_result": evaluation_name,
                "receipt": receipt_name,
            }
        )

    manifest = {
        "chain_id": chain_id,
        "entries": manifest_entries,
    }
    manifest_path = out_dir / "manifest.json"
    _write_json(manifest_path, manifest)

    verifier_report = verify_manifest(manifest_path)
    if not verifier_report.get("ok", False):
        errors = "; ".join(str(error) for error in verifier_report.get("errors", []))
        raise ValueError(f"receipt bundle verification failed: {errors}")

    return {
        "out_dir": str(out_dir),
        "manifest": str(manifest_path),
        "receipt_count": len(entries),
        "verifier_report": {
            "ok": bool(verifier_report["ok"]),
            "receipt_count": verifier_report["receipt_count"],
            "errors": verifier_report["errors"],
        },
    }


def _safe_label(label: str) -> str:
    if not _LABEL_RE.fullmatch(label):
        raise ValueError("receipt bundle entry label must be filename-safe")
    return label


def _prepare_out_dir(out_dir: Path) -> None:
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=False)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
