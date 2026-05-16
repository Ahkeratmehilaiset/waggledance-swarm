# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""EMAIL-02 vendor email indexer first-slice core.

Pure deterministic logic for the first EMAIL-02 operator-facing slice:
"index already-exported local email rows by known vendor". This module does
not open Gmail, SQLite, vector indexes, credentials, drafts, or a network.
Callers provide sanitized vendor rules and already-exported message rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from email.utils import parseaddr
import re
from typing import Any, Mapping


CASE_ID = "EMAIL-02__vendor_email_indexer__home"
OK = "OK"

MATCH_SENDER_DOMAIN_BILLING = "sender_domain_billing"
MATCH_SENDER_DOMAIN = "sender_domain"
MATCH_SIGNAL_BILLING = "signal_billing"
MATCH_SIGNAL = "signal"

DEFAULT_MAX_MESSAGES_PER_VENDOR = 5
MAX_MESSAGES_PER_VENDOR_LIMIT = 50
MAX_TEXT_LENGTH = 20000

_MESSAGE_TEXT_KEYS = ("subject", "snippet", "body_text", "text")
_SECRET_MARKERS = (
    "password",
    "passwd",
    "token",
    "secret",
    "credential",
    "credentials",
    "api_key",
    "access_key",
    "private_key",
    "authorization",
)
_SECRET_MARKER_RE = re.compile(
    r"(^|[\\/._?&=\-\s])("
    + "|".join(re.escape(marker) for marker in _SECRET_MARKERS)
    + r")($|[\\/._?&=\-\s])",
    re.IGNORECASE,
)
_DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_VENDOR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class Email02VendorEmailIndexerError(ValueError):
    """Invalid caller input for EMAIL-02 vendor email indexing."""


@dataclass(frozen=True)
class _Vendor:
    vendor_id: str
    display_name: str
    domains: tuple[str, ...]
    name_signals: tuple[str, ...]
    billing_keywords: tuple[str, ...]


@dataclass(frozen=True)
class _Message:
    message_id: str
    thread_id: str
    message_date: date
    sender_domain: str
    search_text: str


@dataclass(frozen=True)
class _MatchCandidate:
    vendor: _Vendor
    match_type: str
    priority: int
    confidence: float
    matched_domain_count: int
    matched_signal_count: int
    matched_billing_keyword_count: int


@dataclass(frozen=True)
class Email02VendorEmailIndexResult:
    """Operator-facing EMAIL-02 vendor email index payload."""

    result_marker: str
    vendor_indexes: tuple[dict[str, Any], ...]
    as_of_date: str
    total_messages: int
    matched_messages: int
    billing_messages: int
    max_messages_per_vendor: int

    def to_payload(self) -> dict[str, Any]:
        vendors_with_matches = sum(
            1 for item in self.vendor_indexes if int(item["matched_messages"]) > 0
        )
        return {
            "case_id": CASE_ID,
            "result_marker": self.result_marker,
            "write_intent": "none",
            "as_of_date": self.as_of_date,
            "max_messages_per_vendor": self.max_messages_per_vendor,
            "vendor_indexes": list(self.vendor_indexes),
            "summary": {
                "total_vendors": len(self.vendor_indexes),
                "vendors_with_matches": vendors_with_matches,
                "total_messages": self.total_messages,
                "matched_messages": self.matched_messages,
                "unmatched_messages": self.total_messages - self.matched_messages,
                "billing_messages": self.billing_messages,
            },
        }


def index_email02_vendor_messages(
    payload: Mapping[str, Any],
) -> Email02VendorEmailIndexResult:
    """Index local email rows by configured vendor rules."""
    if not isinstance(payload, Mapping):
        raise Email02VendorEmailIndexerError("payload must be an object")

    as_of = _parse_iso_date(_required_str(payload, "as_of_date"), "as_of_date")
    vendors = _parse_vendors(payload.get("vendors"))
    messages = _parse_messages(payload.get("messages"))
    max_messages = _bounded_positive_int(
        payload.get("max_messages_per_vendor", DEFAULT_MAX_MESSAGES_PER_VENDOR),
        "max_messages_per_vendor",
        upper=MAX_MESSAGES_PER_VENDOR_LIMIT,
    )

    buckets: dict[str, list[dict[str, Any]]] = {
        vendor.vendor_id: [] for vendor in vendors
    }
    all_matches = 0
    billing_matches = 0

    for message in messages:
        candidates = tuple(
            candidate
            for vendor in vendors
            for candidate in [_match_vendor(message, vendor)]
            if candidate is not None
        )
        if not candidates:
            continue
        selected = sorted(
            candidates,
            key=lambda item: (
                item.priority,
                -item.confidence,
                item.vendor.vendor_id,
            ),
        )[0]
        evidence = _message_evidence(
            message,
            selected,
            ambiguous_vendor_count=len(candidates),
        )
        buckets[selected.vendor.vendor_id].append(evidence)
        all_matches += 1
        if selected.matched_billing_keyword_count:
            billing_matches += 1

    vendor_indexes: list[dict[str, Any]] = []
    for vendor in vendors:
        evidence_items = sorted(
            buckets[vendor.vendor_id],
            key=lambda item: (
                item["message_date"],
                item["message_id"],
            ),
            reverse=True,
        )
        billing_count = sum(
            1 for item in evidence_items
            if int(item["matched_billing_keyword_count"]) > 0
        )
        vendor_indexes.append({
            "vendor_id": vendor.vendor_id,
            "display_name": vendor.display_name,
            "matched_messages": len(evidence_items),
            "billing_messages": billing_count,
            "latest_message_date": (
                evidence_items[0]["message_date"] if evidence_items else None
            ),
            "latest_messages": evidence_items[:max_messages],
        })

    return Email02VendorEmailIndexResult(
        result_marker=OK,
        vendor_indexes=tuple(vendor_indexes),
        as_of_date=as_of.isoformat(),
        total_messages=len(messages),
        matched_messages=all_matches,
        billing_messages=billing_matches,
        max_messages_per_vendor=max_messages,
    )


def _match_vendor(
    message: _Message,
    vendor: _Vendor,
) -> _MatchCandidate | None:
    domain_matches = tuple(
        domain
        for domain in vendor.domains
        if _domain_matches(message.sender_domain, domain)
    )
    signal_matches = tuple(
        signal for signal in vendor.name_signals if signal in message.search_text
    )
    if not domain_matches and not signal_matches:
        return None

    billing_matches = tuple(
        keyword
        for keyword in vendor.billing_keywords
        if keyword in message.search_text
    )
    if domain_matches:
        priority = 1
        match_type = (
            MATCH_SENDER_DOMAIN_BILLING
            if billing_matches else MATCH_SENDER_DOMAIN
        )
    else:
        priority = 2
        match_type = MATCH_SIGNAL_BILLING if billing_matches else MATCH_SIGNAL

    return _MatchCandidate(
        vendor=vendor,
        match_type=match_type,
        priority=priority,
        confidence=_confidence(
            has_domain_match=bool(domain_matches),
            signal_count=len(signal_matches),
            billing_count=len(billing_matches),
        ),
        matched_domain_count=len(domain_matches),
        matched_signal_count=len(signal_matches),
        matched_billing_keyword_count=len(billing_matches),
    )


def _message_evidence(
    message: _Message,
    candidate: _MatchCandidate,
    *,
    ambiguous_vendor_count: int,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "message_id": message.message_id,
        "thread_id": message.thread_id,
        "message_date": message.message_date.isoformat(),
        "sender_domain": message.sender_domain,
        "match_type": candidate.match_type,
        "confidence": candidate.confidence,
        "matched_domain_count": candidate.matched_domain_count,
        "matched_signal_count": candidate.matched_signal_count,
        "matched_billing_keyword_count":
            candidate.matched_billing_keyword_count,
    }
    if ambiguous_vendor_count > 1:
        item["ambiguous_vendor_count"] = ambiguous_vendor_count
    return item


def _parse_vendors(raw: Any) -> tuple[_Vendor, ...]:
    if not isinstance(raw, list) or not raw:
        raise Email02VendorEmailIndexerError("vendors must be a non-empty list")
    vendors: list[_Vendor] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise Email02VendorEmailIndexerError(
                f"vendors[{index}] must be an object"
            )
        vendor_id = _required_str(item, "vendor_id")
        if not _VENDOR_ID_RE.fullmatch(vendor_id):
            raise Email02VendorEmailIndexerError(
                f"vendor_id has invalid characters: {vendor_id}"
            )
        if vendor_id in seen:
            raise Email02VendorEmailIndexerError(
                f"duplicate vendor_id: {vendor_id}"
            )
        seen.add(vendor_id)
        display_name = _safe_config_str(
            _required_str(item, "display_name"),
            "display_name",
        )
        domains = _parse_domains(item.get("domains", []), f"vendors[{index}].domains")
        name_signals = _parse_phrase_list(
            item.get("name_signals", []),
            f"vendors[{index}].name_signals",
        )
        billing_keywords = _parse_phrase_list(
            item.get("billing_keywords", []),
            f"vendors[{index}].billing_keywords",
        )
        if not domains and not name_signals:
            raise Email02VendorEmailIndexerError(
                f"vendors[{index}] must include domains or name_signals"
            )
        vendors.append(_Vendor(
            vendor_id=vendor_id,
            display_name=display_name,
            domains=domains,
            name_signals=name_signals,
            billing_keywords=billing_keywords,
        ))
    return tuple(vendors)


def _parse_messages(raw: Any) -> tuple[_Message, ...]:
    if not isinstance(raw, list) or not raw:
        raise Email02VendorEmailIndexerError("messages must be a non-empty list")
    messages: list[_Message] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise Email02VendorEmailIndexerError(
                f"messages[{index}] must be an object"
            )
        message_id = _required_str(item, "message_id")
        if message_id in seen:
            raise Email02VendorEmailIndexerError(
                f"duplicate message_id: {message_id}"
            )
        seen.add(message_id)
        thread_id = _required_str(item, "thread_id")
        message_date = _parse_iso_date(_required_str(item, "date"), "date")
        sender = _required_str(item, "from")
        sender_domain = _sender_domain(sender)
        search_text = _message_search_text(item, sender_domain)
        messages.append(_Message(
            message_id=message_id,
            thread_id=thread_id,
            message_date=message_date,
            sender_domain=sender_domain,
            search_text=search_text,
        ))
    return tuple(messages)


def _parse_domains(raw: Any, label: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise Email02VendorEmailIndexerError(f"{label} must be a list")
    domains: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        value = _safe_config_str(_list_str(item, f"{label}[{index}]"), label)
        normalized = value.casefold().strip(".")
        if "@" in normalized or not _DOMAIN_RE.fullmatch(normalized):
            raise Email02VendorEmailIndexerError(
                f"{label}[{index}] must be a domain name"
            )
        if normalized not in seen:
            domains.append(normalized)
            seen.add(normalized)
    return tuple(domains)


def _parse_phrase_list(raw: Any, label: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise Email02VendorEmailIndexerError(f"{label} must be a list")
    phrases: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        value = _safe_config_str(_list_str(item, f"{label}[{index}]"), label)
        normalized = _normalize_text(value)
        if len(normalized) < 2:
            raise Email02VendorEmailIndexerError(
                f"{label}[{index}] must contain at least two characters"
            )
        if normalized not in seen:
            phrases.append(normalized)
            seen.add(normalized)
    return tuple(phrases)


def _message_search_text(item: Mapping[str, Any], sender_domain: str) -> str:
    parts = [sender_domain]
    sender = _required_str(item, "from")
    parts.append(sender)
    for key in _MESSAGE_TEXT_KEYS:
        value = item.get(key)
        if isinstance(value, bool):
            raise Email02VendorEmailIndexerError(f"{key} must be a string")
        if isinstance(value, str):
            if len(value) > MAX_TEXT_LENGTH:
                raise Email02VendorEmailIndexerError(f"{key} is too long")
            if value.strip():
                parts.append(value)
        elif value is not None:
            raise Email02VendorEmailIndexerError(f"{key} must be a string")

    labels = item.get("labels", [])
    if not isinstance(labels, list):
        raise Email02VendorEmailIndexerError("labels must be a list")
    for index, label in enumerate(labels):
        parts.append(_list_str(label, f"labels[{index}]"))
    return _normalize_text(" ".join(parts))


def _sender_domain(sender: str) -> str:
    _, email_address = parseaddr(sender)
    if "@" not in email_address:
        raise Email02VendorEmailIndexerError("from must contain an email address")
    domain = email_address.rsplit("@", 1)[1].casefold().strip(".")
    if not _DOMAIN_RE.fullmatch(domain):
        raise Email02VendorEmailIndexerError("from must contain a valid domain")
    return domain


def _confidence(
    *,
    has_domain_match: bool,
    signal_count: int,
    billing_count: int,
) -> float:
    if has_domain_match:
        base = 0.96 if billing_count else 0.88
        extra = 0.01 * max(0, billing_count - 1)
    else:
        base = 0.82 if billing_count else 0.68
        extra = 0.03 * max(0, signal_count - 1)
    return round(min(0.99, base + extra), 2)


def _bounded_positive_int(raw: Any, label: str, *, upper: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise Email02VendorEmailIndexerError(f"{label} must be an integer")
    if raw < 1 or raw > upper:
        raise Email02VendorEmailIndexerError(
            f"{label} must be between 1 and {upper}"
        )
    return raw


def _parse_iso_date(raw: str, label: str) -> date:
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise Email02VendorEmailIndexerError(
            f"{label} must be an ISO date"
        ) from exc
    if parsed.isoformat() != raw:
        raise Email02VendorEmailIndexerError(f"{label} must be an ISO date")
    return parsed


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        raise Email02VendorEmailIndexerError(f"{key} must be a non-empty string")
    return value


def _list_str(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        raise Email02VendorEmailIndexerError(f"{label} must be a non-empty string")
    return value


def _safe_config_str(value: str, label: str) -> str:
    normalized = " ".join(value.split())
    if _SECRET_MARKER_RE.search(normalized):
        raise Email02VendorEmailIndexerError(f"{label} must not contain secrets")
    return normalized


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _domain_matches(sender_domain: str, vendor_domain: str) -> bool:
    return sender_domain == vendor_domain or sender_domain.endswith(
        "." + vendor_domain
    )


__all__ = [
    "CASE_ID",
    "Email02VendorEmailIndexResult",
    "Email02VendorEmailIndexerError",
    "OK",
    "index_email02_vendor_messages",
]
