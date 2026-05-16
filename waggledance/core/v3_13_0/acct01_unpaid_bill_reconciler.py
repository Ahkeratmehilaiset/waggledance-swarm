# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""ACCT-01 unpaid-bill reconciliation first-slice core.

Pure deterministic logic for the first ACCT-01 operator-facing slice:
"cross-check known invoices against already-fetched bank transactions".
This module does not open SQLite databases, read bank credentials, write
state, submit payments, call a network, or call an LLM. Callers provide
already-normalized invoice rows and bank transaction rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math
import re
from typing import Any, Mapping, Sequence


CASE_ID = "ACCT-01__unpaid_bill_reconciler__home"
OK = "OK"

STATUS_PAID = "paid"
STATUS_UNPAID = "unpaid"
STATUS_EXCLUDED = "excluded"

MATCH_REFERENCE_EXACT = "reference_exact"
MATCH_MESSAGE_REFERENCE = "message_reference"
MATCH_AMOUNT_DATE_WINDOW = "amount_date_window"
MATCH_STATUS_MARKED_PAID = "status_marked_paid"
MATCH_STATUS_EXCLUDED = "status_excluded"
MATCH_NONE = "no_match"

DEFAULT_AMOUNT_TOLERANCE_EUR = 0.01
DEFAULT_WINDOW_BEFORE_DAYS = 14
DEFAULT_WINDOW_AFTER_DAYS = 7
DEFAULT_PAID_STATUS_VALUES = ("paid", "maksettu")
DEFAULT_IGNORED_STATUS_VALUES = (
    "luettu",
    "ignored",
    "void",
    "cancelled",
    "canceled",
    "rahat-holvissa",
)


class Acct01UnpaidBillReconcilerError(ValueError):
    """Invalid caller input for ACCT-01 reconciliation."""


@dataclass(frozen=True)
class _Invoice:
    invoice_id: str
    vendor: str | None
    invoice_number: str | None
    due_date: date
    amount_eur: float
    reference_number: str | None
    status: str | None
    source: str | None


@dataclass(frozen=True)
class _Transaction:
    transaction_id: str
    account_name: str | None
    booked_date: date
    amount_eur: float
    reference_number: str | None
    search_digits: str


@dataclass(frozen=True)
class _MatchCandidate:
    transaction: _Transaction
    match_type: str
    priority: int
    confidence: float


@dataclass(frozen=True)
class Acct01ReconciliationResult:
    """Operator-facing ACCT-01 reconciliation payload."""

    result_marker: str
    reconciliations: tuple[dict[str, Any], ...]
    as_of_date: str
    amount_tolerance_eur: float
    match_window_before_days: int
    match_window_after_days: int

    def to_payload(self) -> dict[str, Any]:
        counts = {
            STATUS_PAID: 0,
            STATUS_UNPAID: 0,
            STATUS_EXCLUDED: 0,
        }
        total_unpaid = 0.0
        overdue_unpaid = 0
        as_of = _parse_iso_date(self.as_of_date, "as_of_date")
        for item in self.reconciliations:
            status = str(item["payment_status"])
            counts[status] += 1
            if status == STATUS_UNPAID:
                total_unpaid += float(item["amount_eur"])
                if _parse_iso_date(str(item["due_date"]), "due_date") < as_of:
                    overdue_unpaid += 1
        return {
            "case_id": CASE_ID,
            "result_marker": self.result_marker,
            "write_intent": "none",
            "as_of_date": self.as_of_date,
            "amount_tolerance_eur": self.amount_tolerance_eur,
            "match_window_before_days": self.match_window_before_days,
            "match_window_after_days": self.match_window_after_days,
            "reconciliations": list(self.reconciliations),
            "summary": {
                "total_invoices": len(self.reconciliations),
                "paid": counts[STATUS_PAID],
                "unpaid": counts[STATUS_UNPAID],
                "excluded": counts[STATUS_EXCLUDED],
                "total_unpaid_eur": round(total_unpaid, 2),
                "overdue_unpaid": overdue_unpaid,
            },
        }


def reconcile_acct01_unpaid_bills(
    payload: Mapping[str, Any],
) -> Acct01ReconciliationResult:
    """Reconcile invoices with bank transactions and return unpaid items."""
    if not isinstance(payload, Mapping):
        raise Acct01UnpaidBillReconcilerError("payload must be an object")
    as_of = _parse_iso_date(_required_str(payload, "as_of_date"), "as_of_date")
    tolerance = _amount_tolerance(
        payload.get("amount_tolerance_eur", DEFAULT_AMOUNT_TOLERANCE_EUR),
    )
    window_before = _non_negative_int(
        payload.get("match_window_before_days", DEFAULT_WINDOW_BEFORE_DAYS),
        "match_window_before_days",
    )
    window_after = _non_negative_int(
        payload.get("match_window_after_days", DEFAULT_WINDOW_AFTER_DAYS),
        "match_window_after_days",
    )
    paid_statuses = _status_values(
        payload.get("paid_status_values"),
        DEFAULT_PAID_STATUS_VALUES,
        "paid_status_values",
    )
    ignored_statuses = _status_values(
        payload.get("ignored_status_values"),
        DEFAULT_IGNORED_STATUS_VALUES,
        "ignored_status_values",
    )
    invoices = _parse_invoices(payload.get("invoices"))
    transactions = _parse_transactions(payload.get("bank_transactions"))

    reconciliations = tuple(
        _reconcile_invoice(
            invoice,
            transactions,
            as_of_date=as_of,
            tolerance=tolerance,
            window_before_days=window_before,
            window_after_days=window_after,
            paid_statuses=paid_statuses,
            ignored_statuses=ignored_statuses,
        )
        for invoice in invoices
    )
    return Acct01ReconciliationResult(
        result_marker=OK,
        reconciliations=reconciliations,
        as_of_date=as_of.isoformat(),
        amount_tolerance_eur=tolerance,
        match_window_before_days=window_before,
        match_window_after_days=window_after,
    )


def _reconcile_invoice(
    invoice: _Invoice,
    transactions: tuple[_Transaction, ...],
    *,
    as_of_date: date,
    tolerance: float,
    window_before_days: int,
    window_after_days: int,
    paid_statuses: frozenset[str],
    ignored_statuses: frozenset[str],
) -> dict[str, Any]:
    status = _normalize_status(invoice.status)
    base = _invoice_payload(invoice, as_of_date)
    if status in paid_statuses:
        return base | {
            "payment_status": STATUS_PAID,
            "match_type": MATCH_STATUS_MARKED_PAID,
            "confidence": 0.66,
            "candidate_count": 0,
            "rationale_summary": "invoice status is already paid",
        }
    if status in ignored_statuses:
        return base | {
            "payment_status": STATUS_EXCLUDED,
            "match_type": MATCH_STATUS_EXCLUDED,
            "confidence": 0.5,
            "candidate_count": 0,
            "rationale_summary": "invoice status is excluded from unpaid list",
        }

    candidates = _match_candidates(
        invoice,
        transactions,
        tolerance=tolerance,
        window_before_days=window_before_days,
        window_after_days=window_after_days,
    )
    if not candidates:
        return base | {
            "payment_status": STATUS_UNPAID,
            "match_type": MATCH_NONE,
            "confidence": 0.0,
            "candidate_count": 0,
            "rationale_summary": "no matching bank transaction found",
        }

    best = sorted(
        candidates,
        key=lambda item: (
            item.priority,
            abs((item.transaction.booked_date - invoice.due_date).days),
            item.transaction.transaction_id,
        ),
    )[0]
    return base | {
        "payment_status": STATUS_PAID,
        "match_type": best.match_type,
        "confidence": best.confidence,
        "candidate_count": len(candidates),
        "matched_transaction_id": best.transaction.transaction_id,
        "matched_account": best.transaction.account_name,
        "matched_booked_date": best.transaction.booked_date.isoformat(),
        "rationale_summary": _match_rationale(best.match_type),
    }


def _invoice_payload(invoice: _Invoice, as_of_date: date) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "invoice_id": invoice.invoice_id,
        "due_date": invoice.due_date.isoformat(),
        "amount_eur": invoice.amount_eur,
        "days_to_due": (invoice.due_date - as_of_date).days,
    }
    if invoice.vendor is not None:
        payload["vendor"] = invoice.vendor
    if invoice.invoice_number is not None:
        payload["invoice_number"] = invoice.invoice_number
    if invoice.reference_number is not None:
        payload["reference_number"] = invoice.reference_number
    if invoice.status is not None:
        payload["source_status"] = invoice.status
    if invoice.source is not None:
        payload["source"] = invoice.source
    return payload


def _match_candidates(
    invoice: _Invoice,
    transactions: tuple[_Transaction, ...],
    *,
    tolerance: float,
    window_before_days: int,
    window_after_days: int,
) -> tuple[_MatchCandidate, ...]:
    candidates: list[_MatchCandidate] = []
    start = invoice.due_date - timedelta(days=window_before_days)
    end = invoice.due_date + timedelta(days=window_after_days)
    for transaction in transactions:
        amount_matches = (
            abs(abs(transaction.amount_eur) - invoice.amount_eur) <= tolerance
        )
        date_matches = start <= transaction.booked_date <= end
        if invoice.reference_number is not None:
            if transaction.reference_number == invoice.reference_number:
                candidates.append(_MatchCandidate(
                    transaction=transaction,
                    match_type=MATCH_REFERENCE_EXACT,
                    priority=1,
                    confidence=0.99,
                ))
                continue
            if invoice.reference_number in transaction.search_digits:
                candidates.append(_MatchCandidate(
                    transaction=transaction,
                    match_type=MATCH_MESSAGE_REFERENCE,
                    priority=2,
                    confidence=0.94,
                ))
                continue
        if amount_matches and date_matches:
            candidates.append(_MatchCandidate(
                transaction=transaction,
                match_type=MATCH_AMOUNT_DATE_WINDOW,
                priority=3,
                confidence=0.72,
            ))
    return tuple(candidates)


def _match_rationale(match_type: str) -> str:
    if match_type == MATCH_REFERENCE_EXACT:
        return "matched exact payment reference"
    if match_type == MATCH_MESSAGE_REFERENCE:
        return "matched payment reference in transaction text"
    return "matched amount inside due-date window"


def _parse_invoices(raw: Any) -> tuple[_Invoice, ...]:
    if not isinstance(raw, list) or not raw:
        raise Acct01UnpaidBillReconcilerError("invoices must be a non-empty list")
    seen: set[str] = set()
    invoices: list[_Invoice] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise Acct01UnpaidBillReconcilerError(
                f"invoices[{index}] must be an object"
            )
        invoice_id = _required_str(item, "invoice_id")
        if invoice_id in seen:
            raise Acct01UnpaidBillReconcilerError(
                f"duplicate invoice_id: {invoice_id}"
            )
        seen.add(invoice_id)
        invoices.append(_Invoice(
            invoice_id=invoice_id,
            vendor=_optional_str(item.get("vendor"), "vendor"),
            invoice_number=_optional_str(item.get("invoice_number"),
                                         "invoice_number"),
            due_date=_parse_iso_date(_required_str(item, "due_date"),
                                     "due_date"),
            amount_eur=_positive_amount(item.get("amount_eur"), "amount_eur"),
            reference_number=_optional_reference(item.get("reference_number"),
                                                 "reference_number"),
            status=_optional_str(item.get("status"), "status"),
            source=_optional_str(item.get("source"), "source"),
        ))
    return tuple(invoices)


def _parse_transactions(raw: Any) -> tuple[_Transaction, ...]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise Acct01UnpaidBillReconcilerError(
            "bank_transactions must be a list"
        )
    seen: set[str] = set()
    transactions: list[_Transaction] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise Acct01UnpaidBillReconcilerError(
                f"bank_transactions[{index}] must be an object"
            )
        transaction_id = _required_str(item, "transaction_id")
        if transaction_id in seen:
            raise Acct01UnpaidBillReconcilerError(
                f"duplicate transaction_id: {transaction_id}"
            )
        seen.add(transaction_id)
        reference = _optional_reference(item.get("reference_number"),
                                        "reference_number")
        text_parts = [
            _optional_str(item.get(key), key)
            for key in ("message", "description", "memo")
        ]
        search_digits = "".join(_digits(value) for value in text_parts if value)
        if reference is not None:
            search_digits += reference
        transactions.append(_Transaction(
            transaction_id=transaction_id,
            account_name=_optional_str(item.get("account_name"),
                                       "account_name"),
            booked_date=_parse_iso_date(_required_str(item, "booked_date"),
                                        "booked_date"),
            amount_eur=_finite_amount(item.get("amount_eur"), "amount_eur"),
            reference_number=reference,
            search_digits=search_digits,
        ))
    return tuple(transactions)


def _status_values(
    raw: Any,
    defaults: Sequence[str],
    label: str,
) -> frozenset[str]:
    if raw is None:
        raw = list(defaults)
    if not isinstance(raw, list) or not raw:
        raise Acct01UnpaidBillReconcilerError(
            f"{label} must be a non-empty list"
        )
    values: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise Acct01UnpaidBillReconcilerError(
                f"{label}[{index}] must be a non-empty string"
            )
        values.add(_normalize_status(item))
    return frozenset(values)


def _required_str(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Acct01UnpaidBillReconcilerError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_str(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise Acct01UnpaidBillReconcilerError(f"{label} must be a string")
    stripped = value.strip()
    return stripped or None


def _optional_reference(value: Any, label: str) -> str | None:
    raw = _optional_str(value, label)
    if raw is None:
        return None
    reference = re.sub(r"\s+", "", raw)
    if not reference.isdigit() or not (3 <= len(reference) <= 30):
        raise Acct01UnpaidBillReconcilerError(
            f"{label} must contain 3-30 digits"
        )
    return reference


def _parse_iso_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise Acct01UnpaidBillReconcilerError(
            f"{label} must be an ISO date"
        ) from exc


def _positive_amount(value: Any, label: str) -> float:
    amount = _finite_amount(value, label)
    if amount <= 0:
        raise Acct01UnpaidBillReconcilerError(f"{label} must be positive")
    return amount


def _finite_amount(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Acct01UnpaidBillReconcilerError(f"{label} must be numeric")
    amount = float(value)
    if not math.isfinite(amount):
        raise Acct01UnpaidBillReconcilerError(f"{label} must be finite")
    return round(amount, 2)


def _amount_tolerance(value: Any) -> float:
    amount = _finite_amount(value, "amount_tolerance_eur")
    if amount < 0 or amount > 10:
        raise Acct01UnpaidBillReconcilerError(
            "amount_tolerance_eur must be between 0 and 10"
        )
    return round(amount, 2)


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Acct01UnpaidBillReconcilerError(
            f"{label} must be a non-negative integer"
        )
    return value


def _normalize_status(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.casefold().split())


def _digits(value: str) -> str:
    return re.sub(r"\D+", "", value)


__all__ = [
    "CASE_ID",
    "MATCH_AMOUNT_DATE_WINDOW",
    "MATCH_MESSAGE_REFERENCE",
    "MATCH_NONE",
    "MATCH_REFERENCE_EXACT",
    "MATCH_STATUS_EXCLUDED",
    "MATCH_STATUS_MARKED_PAID",
    "OK",
    "STATUS_EXCLUDED",
    "STATUS_PAID",
    "STATUS_UNPAID",
    "Acct01ReconciliationResult",
    "Acct01UnpaidBillReconcilerError",
    "reconcile_acct01_unpaid_bills",
]
