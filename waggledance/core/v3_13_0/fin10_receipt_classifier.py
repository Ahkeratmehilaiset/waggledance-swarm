# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""FIN-10 receipt tag classifier first-slice core.

Pure deterministic logic for the first FIN-10 operator-facing slice:
"classify one or more receipts with clear cottage/home geo signals".
This module does not fetch data, read credentials, write state, or call a
network. Callers provide already-extracted receipt text and explicit signal
lists.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


CASE_ID = "FIN-10__cottage_bookkeeping_separator__cottage"
OK = "OK"

TAG_COTTAGE = "cottage"
TAG_HOME = "home"
TAG_MIXED = "mixed"

TEXT_KEYS = (
    "text",
    "vendor_name",
    "merchant_name",
    "address",
    "city",
    "description",
    "notes",
)


class Fin10ReceiptClassifierError(ValueError):
    """Invalid caller input for FIN-10 receipt classification."""


@dataclass(frozen=True)
class Fin10ReceiptClassificationResult:
    """Operator-facing FIN-10 classification payload."""

    result_marker: str
    classifications: tuple[dict[str, Any], ...]

    def to_payload(self) -> dict[str, Any]:
        counts = {
            TAG_COTTAGE: 0,
            TAG_HOME: 0,
            TAG_MIXED: 0,
        }
        for item in self.classifications:
            counts[str(item["suggested_tag"])] += 1
        return {
            "case_id": CASE_ID,
            "result_marker": self.result_marker,
            "classifications": list(self.classifications),
            "summary": {
                "total_receipts": len(self.classifications),
                "clear_classifications": counts[TAG_COTTAGE] + counts[TAG_HOME],
                "ambiguous_classifications": counts[TAG_MIXED],
                "cottage": counts[TAG_COTTAGE],
                "home": counts[TAG_HOME],
                "mixed": counts[TAG_MIXED],
            },
        }


def classify_fin10_receipts(
    payload: Mapping[str, Any],
) -> Fin10ReceiptClassificationResult:
    """Classify receipts as cottage, home, or mixed."""
    cottage_signals = _parse_signals(
        payload.get("cottage_signals"),
        "cottage_signals",
    )
    home_signals = _parse_signals(payload.get("home_signals"), "home_signals")
    receipts = _parse_receipts(payload.get("receipts"))

    classifications = tuple(
        _classify_receipt(
            receipt,
            cottage_signals=cottage_signals,
            home_signals=home_signals,
        )
        for receipt in receipts
    )
    return Fin10ReceiptClassificationResult(
        result_marker=OK,
        classifications=classifications,
    )


def _classify_receipt(
    receipt: Mapping[str, Any],
    *,
    cottage_signals: tuple[str, ...],
    home_signals: tuple[str, ...],
) -> dict[str, Any]:
    receipt_id = _required_str(receipt, "receipt_id")
    text = _receipt_text(receipt)
    normalized = text.casefold()
    cottage_matches = _matches(normalized, cottage_signals)
    home_matches = _matches(normalized, home_signals)

    if cottage_matches and not home_matches:
        tag = TAG_COTTAGE
        confidence = _clear_confidence(len(cottage_matches))
        rationale = "matched cottage signal"
    elif home_matches and not cottage_matches:
        tag = TAG_HOME
        confidence = _clear_confidence(len(home_matches))
        rationale = "matched home signal"
    elif cottage_matches and home_matches:
        tag = TAG_MIXED
        confidence = 0.5
        rationale = "matched both cottage and home signals"
    else:
        tag = TAG_MIXED
        confidence = 0.0
        rationale = "no configured geo signal matched"

    return {
        "receipt_id": receipt_id,
        "suggested_tag": tag,
        "confidence": confidence,
        "rationale_summary": rationale,
        "matched_cottage_signals": list(cottage_matches),
        "matched_home_signals": list(home_matches),
    }


def _parse_signals(raw: Any, label: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise Fin10ReceiptClassifierError(f"{label} must be a non-empty list")
    signals: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise Fin10ReceiptClassifierError(
                f"{label}[{index}] must be a non-empty string"
            )
        normalized = " ".join(item.casefold().split())
        if normalized not in seen:
            signals.append(normalized)
            seen.add(normalized)
    return tuple(signals)


def _parse_receipts(raw: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(raw, list) or not raw:
        raise Fin10ReceiptClassifierError("receipts must be a non-empty list")
    receipts: list[Mapping[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise Fin10ReceiptClassifierError(
                f"receipts[{index}] must be an object"
            )
        receipt_id = _required_str(item, "receipt_id")
        if receipt_id in seen_ids:
            raise Fin10ReceiptClassifierError(
                f"duplicate receipt_id: {receipt_id}"
            )
        seen_ids.add(receipt_id)
        _receipt_text(item)
        receipts.append(item)
    return tuple(receipts)


def _receipt_text(receipt: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in TEXT_KEYS:
        value = receipt.get(key)
        if isinstance(value, bool):
            raise Fin10ReceiptClassifierError(f"{key} must be a string")
        if isinstance(value, str) and value.strip():
            parts.append(value)
        elif value is not None and not isinstance(value, str):
            raise Fin10ReceiptClassifierError(f"{key} must be a string")
    if not parts:
        raise Fin10ReceiptClassifierError(
            "receipt must contain at least one text field"
        )
    return " ".join(parts)


def _matches(normalized_text: str, signals: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(signal for signal in signals if signal in normalized_text)


def _clear_confidence(match_count: int) -> float:
    return round(min(0.95, 0.82 + (match_count - 1) * 0.05), 2)


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Fin10ReceiptClassifierError(f"{key} must be a non-empty string")
    return value


__all__ = [
    "CASE_ID",
    "Fin10ReceiptClassificationResult",
    "Fin10ReceiptClassifierError",
    "OK",
    "TAG_COTTAGE",
    "TAG_HOME",
    "TAG_MIXED",
    "classify_fin10_receipts",
]
