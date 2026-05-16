# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""PDF-01 Finnish invoice field extraction first-slice core.

Pure deterministic logic for the first PDF-01 operator-facing slice:
"extract structured payment fields from already-extracted invoice text".
This module does not open PDFs, call OCR/PDF libraries, read credentials,
write state, submit payments, or call a network. Callers provide text or
OCR lines extracted by an outer adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
import re
import unicodedata
from typing import Any, Mapping

from waggledance.core.v3_13_0.secret_markers import contains_secret_marker


CASE_ID = "PDF-01__invoice_field_extractor__home"
OK = "OK"
INVOICE_FIELDS_INCOMPLETE = "INVOICE_FIELDS_INCOMPLETE"
DOCUMENT_UNPARSEABLE_REFUSED = "DOCUMENT_UNPARSEABLE_REFUSED"

TARGET_FIELDS = (
    "invoice_number",
    "invoice_date",
    "due_date",
    "amount_eur",
    "reference_number",
    "iban",
    "bic",
    "recipient_name",
)
REQUIRED_FIELDS = (
    "invoice_date",
    "due_date",
    "amount_eur",
    "reference_number",
    "iban",
)

_DATE_FI_RE = r"(\d{1,2}\.\d{1,2}\.\d{4})"


class Pdf01InvoiceFieldExtractorError(ValueError):
    """Invalid caller input for PDF-01 invoice field extraction."""


@dataclass(frozen=True)
class Pdf01InvoiceExtractionResult:
    """Operator-facing PDF-01 extraction payload."""

    result_marker: str
    documents: tuple[dict[str, Any], ...]

    def to_payload(self) -> dict[str, Any]:
        counts = {
            OK: 0,
            INVOICE_FIELDS_INCOMPLETE: 0,
            DOCUMENT_UNPARSEABLE_REFUSED: 0,
        }
        for document in self.documents:
            counts[str(document["result_marker"])] += 1
        return {
            "case_id": CASE_ID,
            "result_marker": self.result_marker,
            "documents": list(self.documents),
            "summary": {
                "total_documents": len(self.documents),
                "complete_documents": counts[OK],
                "incomplete_documents": counts[INVOICE_FIELDS_INCOMPLETE],
                "refused_documents": counts[DOCUMENT_UNPARSEABLE_REFUSED],
            },
        }


def extract_pdf01_invoice_fields(
    payload: Mapping[str, Any],
) -> Pdf01InvoiceExtractionResult:
    """Extract invoice fields from a batch of already-extracted documents."""
    documents = tuple(_extract_document(document)
                      for document in _parse_documents(payload.get("documents")))
    marker = (
        OK
        if all(document["result_marker"] == OK for document in documents)
        else INVOICE_FIELDS_INCOMPLETE
    )
    return Pdf01InvoiceExtractionResult(
        result_marker=marker,
        documents=documents,
    )


def _extract_document(document: Mapping[str, Any]) -> dict[str, Any]:
    document_id = _required_str(document, "document_id")
    source_name = _optional_safe_str(document.get("source_name"), "source_name")
    text = _document_text(document)
    fields = {
        "invoice_number": _invoice_number(text),
        "invoice_date": _invoice_date(text),
        "due_date": _due_date(text),
        "amount_eur": _amount_eur(text),
        "reference_number": _reference_number(text),
        "iban": _iban(text),
        "bic": _bic(text),
        "recipient_name": _recipient_name(text),
    }
    found_fields = tuple(key for key in TARGET_FIELDS if fields[key] is not None)
    missing_fields = tuple(key for key in TARGET_FIELDS if fields[key] is None)
    missing_required = tuple(
        key for key in REQUIRED_FIELDS if fields[key] is None
    )

    if not found_fields:
        marker = DOCUMENT_UNPARSEABLE_REFUSED
        confidence = 0.0
        rationale = "no known Finnish invoice fields found"
    elif missing_required:
        marker = INVOICE_FIELDS_INCOMPLETE
        confidence = _confidence(len(found_fields), complete=False)
        rationale = "missing required payment field"
    else:
        marker = OK
        confidence = _confidence(len(found_fields), complete=True)
        rationale = "required payment fields extracted"

    item: dict[str, Any] = {
        "document_id": document_id,
        "result_marker": marker,
        "extracted_fields": fields,
        "found_fields": list(found_fields),
        "missing_fields": list(missing_fields),
        "missing_required_fields": list(missing_required),
        "confidence": confidence,
        "rationale_summary": rationale,
    }
    if source_name is not None:
        item["source_name"] = source_name
    return item


def _parse_documents(raw: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(raw, list) or not raw:
        raise Pdf01InvoiceFieldExtractorError(
            "documents must be a non-empty list"
        )
    documents: list[Mapping[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise Pdf01InvoiceFieldExtractorError(
                f"documents[{index}] must be an object"
            )
        document_id = _required_str(item, "document_id")
        if document_id in seen_ids:
            raise Pdf01InvoiceFieldExtractorError(
                f"duplicate document_id: {document_id}"
            )
        seen_ids.add(document_id)
        _document_text(item)
        documents.append(item)
    return tuple(documents)


def _document_text(document: Mapping[str, Any]) -> str:
    text = document.get("text")
    if text is None:
        text = document.get("text_raw")
    if text is None:
        text = document.get("raw_text")
    if text is not None:
        if not isinstance(text, str) or not text.strip():
            raise Pdf01InvoiceFieldExtractorError(
                "document text must be a non-empty string"
            )
        return text

    lines = document.get("ocr_lines")
    if lines is not None:
        if not isinstance(lines, list) or not lines:
            raise Pdf01InvoiceFieldExtractorError(
                "ocr_lines must be a non-empty list"
            )
        return "\n".join(_line_item(lines, "ocr_lines", index)
                         for index in range(len(lines)))

    pages = document.get("pages")
    if pages is not None:
        if not isinstance(pages, list) or not pages:
            raise Pdf01InvoiceFieldExtractorError(
                "pages must be a non-empty list"
            )
        page_texts: list[str] = []
        for index, page in enumerate(pages):
            if isinstance(page, str):
                value = page
            elif isinstance(page, Mapping):
                value = page.get("text")
            else:
                raise Pdf01InvoiceFieldExtractorError(
                    f"pages[{index}] must be a string or object"
                )
            if not isinstance(value, str) or not value.strip():
                raise Pdf01InvoiceFieldExtractorError(
                    f"pages[{index}].text must be a non-empty string"
                )
            page_texts.append(value)
        return "\n".join(page_texts)

    raise Pdf01InvoiceFieldExtractorError(
        "document must include text, text_raw, raw_text, ocr_lines, or pages"
    )


def _line_item(lines: list[Any], label: str, index: int) -> str:
    value = lines[index]
    if not isinstance(value, str) or not value.strip():
        raise Pdf01InvoiceFieldExtractorError(
            f"{label}[{index}] must be a non-empty string"
        )
    return value


def _invoice_number(text: str) -> str | None:
    normalized = _normalized_text(text)
    for pattern in (
        r"laskun\s+numero[:\s]*([A-Z0-9][A-Z0-9._/-]{1,40})",
        r"laskunumero[:\s]*([A-Z0-9][A-Z0-9._/-]{1,40})",
        r"invoice\s+number[:\s]*([A-Z0-9][A-Z0-9._/-]{1,40})",
    ):
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            return match.group(1).strip(" .").upper()
    return None


def _invoice_date(text: str) -> str | None:
    normalized = _normalized_text(text)
    for pattern in (
        r"laskun\s+paivays[:\s]*" + _DATE_FI_RE,
        r"paivays[:\s]*" + _DATE_FI_RE,
        r"laskun\s+pvm[:\s]*" + _DATE_FI_RE,
        r"invoice\s+date[:\s]*" + _DATE_FI_RE,
    ):
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            return _to_iso_date(match.group(1))
    return None


def _due_date(text: str) -> str | None:
    normalized = _normalized_text(text)
    for pattern in (
        r"erapaiva[:\s]*" + _DATE_FI_RE,
        r"era\s+paiva[:\s]*" + _DATE_FI_RE,
        r"due\s+date[:\s]*" + _DATE_FI_RE,
    ):
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            return _to_iso_date(match.group(1))
    return None


def _amount_eur(text: str) -> float | None:
    normalized = _normalized_text(text)
    for pattern in (
        r"maksettava\s+yhteensa\s+(?:eur\s*)?([\d\s.,]+)",
        r"maksettava\s+([\d\s.,]+)\s*(?:eur)?",
        r"laskun\s+loppusumma\s+yhteensa\s*([\d\s.,]+)",
        r"(?:summa|total)\s+(?:eur\s*)?([\d\s.,]+)",
        r"(?:^|\n)\s*" + _DATE_FI_RE + r"\s+([\d\s.,]+)\s*$",
    ):
        match = re.search(pattern, normalized, re.IGNORECASE | re.MULTILINE)
        if match:
            value = _parse_eur(match.group(match.lastindex or 1))
            if value is not None:
                return value
    return None


def _reference_number(text: str) -> str | None:
    normalized = _normalized_text(text)
    match = re.search(r"viite(?:numero)?[:\s]*([\d\s]{8,40})",
                      normalized, re.IGNORECASE)
    if not match:
        return None
    reference = re.sub(r"\s+", "", match.group(1))
    if reference.isdigit() and 8 <= len(reference) <= 30:
        return reference
    return None


def _iban(text: str) -> str | None:
    match = re.search(r"\b(FI(?:\s*\d){16})\b", text, re.IGNORECASE)
    if not match:
        return None
    iban = re.sub(r"\s+", "", match.group(1)).upper()
    if re.fullmatch(r"FI\d{16}", iban):
        return iban
    return None


def _bic(text: str) -> str | None:
    match = re.search(r"\b(?:BIC[:\s]+)?([A-Z]{4}FI[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b",
                      text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).upper()


def _recipient_name(text: str) -> str | None:
    for line in text.splitlines():
        normalized = _normalized_text(line).strip()
        if not normalized:
            continue
        for label in ("saaja", "maksun saaja", "mottagare", "recipient"):
            if normalized.startswith(label):
                value = _line_value_after_label(line, normalized, label)
                if value is not None:
                    return value
    return None


def _line_value_after_label(line: str, normalized: str, label: str) -> str | None:
    stripped = line.strip()
    if ":" in stripped:
        value = stripped.split(":", 1)[1].strip()
    else:
        # Fall back to splitting on the first source token for labels without
        # a colon, such as "Mottagare Example Oy".
        parts = stripped.split(None, 1)
        value = parts[1].strip() if len(parts) == 2 else ""
    if label == "maksun saaja" and normalized.startswith("maksun saaja"):
        value = re.sub(r"^saaja[:\s]+", "", value, flags=re.IGNORECASE).strip()
    value = re.sub(r"\s+", " ", value).strip(" -")
    if 3 <= len(value) <= 80 and not _has_secret_marker(value):
        return value
    return None


def _to_iso_date(value: str) -> str | None:
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", value.strip())
    if not match:
        return None
    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    if not (1900 <= year <= 2100):
        return None
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    return parsed.isoformat()


def _parse_eur(value: str) -> float | None:
    cleaned = re.sub(r"[^0-9,.-]", "", value)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        amount = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(amount) or not (0.01 <= amount <= 100000):
        return None
    return round(amount, 2)


def _confidence(found_count: int, *, complete: bool) -> float:
    base = 0.76 if complete else 0.34
    return round(min(0.98, base + found_count * 0.03), 2)


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return ascii_text.casefold()


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Pdf01InvoiceFieldExtractorError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_safe_str(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise Pdf01InvoiceFieldExtractorError(f"{label} must be a string")
    normalized = value.strip()
    if _has_secret_marker(normalized):
        raise Pdf01InvoiceFieldExtractorError(f"{label} must not contain secrets")
    return normalized


def _has_secret_marker(value: str) -> bool:
    return contains_secret_marker(value)


__all__ = [
    "CASE_ID",
    "DOCUMENT_UNPARSEABLE_REFUSED",
    "INVOICE_FIELDS_INCOMPLETE",
    "OK",
    "Pdf01InvoiceExtractionResult",
    "Pdf01InvoiceFieldExtractorError",
    "REQUIRED_FIELDS",
    "TARGET_FIELDS",
    "extract_pdf01_invoice_fields",
]
