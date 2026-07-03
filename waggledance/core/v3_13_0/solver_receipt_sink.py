# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Opt-in MAGMA receipt bundles for v3.13 deterministic solver dispatch."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from waggledance.core.magma.receipt_bundle import (
    ReceiptBundleEntry,
    write_receipt_bundle,
)
from waggledance.core.v3_13_0.chat_dispatch import (
    RECEIPT_PAYLOAD_VERSION,
    SOURCE,
)


CHAIN_ID = "magma:v313_solver_dispatch:api:v0"


def write_v313_solver_dispatch_receipt_bundle(
    *,
    out_dir: Path,
    dispatch_receipt: Mapping[str, Any],
    verify_manifest: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    """Write and verify a one-entry v3.13 solver-dispatch receipt bundle."""
    summary = _required_mapping(dispatch_receipt.get("summary"), "summary")
    evaluation = _required_mapping(
        dispatch_receipt.get("evaluation_result"),
        "evaluation_result",
    )
    receipt = _required_mapping(dispatch_receipt.get("receipt"), "receipt")
    _validate_summary(summary)
    return write_receipt_bundle(
        out_dir=out_dir,
        chain_id=CHAIN_ID,
        entries=[
            ReceiptBundleEntry(
                label="v313-solver-dispatch",
                payload=dict(summary),
                evaluation_result=dict(evaluation),
                receipt=dict(receipt),
            )
        ],
        verify_manifest=verify_manifest,
    )


def _required_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"v3.13 solver receipt missing {name}")
    return value


def _validate_summary(summary: Mapping[str, Any]) -> None:
    if summary.get("payload_version") != RECEIPT_PAYLOAD_VERSION:
        raise ValueError("v3.13 solver receipt payload_version mismatch")
    if summary.get("source") != SOURCE:
        raise ValueError("v3.13 solver receipt source mismatch")
    for key in (
        "solver_ref",
        "solver",
        "case_id",
        "risk_class",
        "write_intent",
        "network_access",
        "payload_digest",
        "result_digest",
        "verdict",
    ):
        if not summary.get(key):
            raise ValueError(f"v3.13 solver receipt missing required field: {key}")
    if summary.get("write_intent") != "none":
        raise ValueError("v3.13 solver receipt write_intent must be none")
    if summary.get("network_access") != "not_permitted":
        raise ValueError("v3.13 solver receipt network_access must be not_permitted")
    if summary.get("transport_modules_used") != []:
        raise ValueError("v3.13 solver receipt transport modules must be empty")


__all__ = ["CHAIN_ID", "write_v313_solver_dispatch_receipt_bundle"]
