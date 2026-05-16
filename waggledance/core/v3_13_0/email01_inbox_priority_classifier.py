# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""EMAIL-01 inbox priority classifier first-slice core.

Pure deterministic logic for the first EMAIL-01 operator-facing slice:
"classify already-exported local email rows with a watch list and noise
filter". This module does not open Gmail, SQLite, vector indexes,
credentials, drafts, or a network. Callers provide sanitized watch rules,
keywords, and already-exported message rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from email.utils import parseaddr
import re
from typing import Any, Mapping


CASE_ID = "EMAIL-01__inbox_priority_classifier__home"
OK = "OK"

BUCKET_WATCH = "watch"
BUCKET_REVIEW = "review"
BUCKET_NOISE = "noise"
BUCKET_OTHER = "other"

MATCH_WATCH_PRIORITY = "watch_priority"
MATCH_WATCH_WITH_NOISE = "watch_with_noise"
MATCH_WATCH = "watch"
MATCH_PRIORITY_WITH_NOISE = "priority_with_noise"
MATCH_PRIORITY = "priority"
MATCH_NOISE = "noise"
MATCH_NONE = "no_match"

MAX_TEXT_LENGTH = 20000
_MESSAGE_TEXT_KEYS = ("subject", "snippet", "body_text", "text")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_DOMAIN_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)


class Email01InboxPriorityClassifierError(ValueError):
    """Invalid caller input for EMAIL-01 inbox classification."""


@dataclass(frozen=True)
class _WatchRule:
    watch_id: str
    terms: tuple[str, ...]
    domains: tuple[str, ...]


@dataclass(frozen=True)
class _Message:
    message_id: str
    thread_id: str
    message_date: date
    sender_domain: str
    search_text: str


@dataclass(frozen=True)
class _WatchCandidate:
    watch_id: str
    matched_term_count: int
    matched_domain_count: int


@dataclass(frozen=True)
class _Classification:
    payload: dict[str, Any]


@dataclass(frozen=True)
class Email01InboxPriorityClassificationResult:
    """Operator-facing EMAIL-01 inbox classification payload."""

    result_marker: str
    classifications: tuple[dict[str, Any], ...]
    as_of_date: str

    def to_payload(self) -> dict[str, Any]:
        counts = {
            BUCKET_WATCH: 0,
            BUCKET_REVIEW: 0,
            BUCKET_NOISE: 0,
            BUCKET_OTHER: 0,
        }
        matched_watchlist = 0
        priority_messages = 0
        noise_keyword_messages = 0
        for item in self.classifications:
            bucket = str(item["suggested_bucket"])
            counts[bucket] += 1
            if item.get("matched_watch_id"):
                matched_watchlist += 1
            if int(item["matched_priority_keyword_count"]) > 0:
                priority_messages += 1
            if int(item["matched_noise_keyword_count"]) > 0:
                noise_keyword_messages += 1

        return {
            "case_id": CASE_ID,
            "result_marker": self.result_marker,
            "write_intent": "none",
            "as_of_date": self.as_of_date,
            "classifications": list(self.classifications),
            "summary": {
                "total_messages": len(self.classifications),
                BUCKET_WATCH: counts[BUCKET_WATCH],
                BUCKET_REVIEW: counts[BUCKET_REVIEW],
                BUCKET_NOISE: counts[BUCKET_NOISE],
                BUCKET_OTHER: counts[BUCKET_OTHER],
                "actionable_messages": (
                    counts[BUCKET_WATCH] + counts[BUCKET_REVIEW]
                ),
                "matched_watchlist_messages": matched_watchlist,
                "priority_messages": priority_messages,
                "noise_keyword_messages": noise_keyword_messages,
            },
        }


def classify_email01_inbox_messages(
    payload: Mapping[str, Any],
) -> Email01InboxPriorityClassificationResult:
    """Classify local email rows into watch/review/noise/other buckets."""
    if not isinstance(payload, Mapping):
        raise Email01InboxPriorityClassifierError("payload must be an object")

    as_of = _parse_iso_date(_required_str(payload, "as_of_date"), "as_of_date")
    watchlist = _parse_watchlist(payload.get("watchlist"))
    priority_keywords = _parse_keyword_list(
        payload.get("priority_keywords", []),
        "priority_keywords",
    )
    noise_keywords = _parse_keyword_list(
        payload.get("noise_keywords", []),
        "noise_keywords",
    )
    if not priority_keywords and not noise_keywords:
        raise Email01InboxPriorityClassifierError(
            "priority_keywords or noise_keywords must be non-empty"
        )
    messages = _parse_messages(payload.get("messages"))

    classified = tuple(
        _classify_message(
            message,
            watchlist=watchlist,
            priority_keywords=priority_keywords,
            noise_keywords=noise_keywords,
        ).payload
        for message in messages
    )
    return Email01InboxPriorityClassificationResult(
        result_marker=OK,
        classifications=tuple(sorted(
            classified,
            key=_classification_sort_key,
            reverse=False,
        )),
        as_of_date=as_of.isoformat(),
    )


def _classify_message(
    message: _Message,
    *,
    watchlist: tuple[_WatchRule, ...],
    priority_keywords: tuple[str, ...],
    noise_keywords: tuple[str, ...],
) -> _Classification:
    watch_candidates = tuple(
        candidate
        for rule in watchlist
        for candidate in [_watch_candidate(message, rule)]
        if candidate is not None
    )
    selected_watch = _select_watch_candidate(watch_candidates)
    priority_count = _match_count(message.search_text, priority_keywords)
    noise_count = _match_count(message.search_text, noise_keywords)

    if selected_watch is not None and priority_count:
        bucket = BUCKET_WATCH
        match_type = MATCH_WATCH_PRIORITY
        confidence = _confidence(
            base=0.94,
            watch=selected_watch,
            priority_count=priority_count,
            noise_count=noise_count,
        )
    elif selected_watch is not None and noise_count:
        bucket = BUCKET_REVIEW
        match_type = MATCH_WATCH_WITH_NOISE
        confidence = _confidence(
            base=0.62,
            watch=selected_watch,
            priority_count=priority_count,
            noise_count=noise_count,
        )
    elif selected_watch is not None:
        bucket = BUCKET_WATCH
        match_type = MATCH_WATCH
        confidence = _confidence(
            base=0.88,
            watch=selected_watch,
            priority_count=priority_count,
            noise_count=noise_count,
        )
    elif priority_count and noise_count:
        bucket = BUCKET_REVIEW
        match_type = MATCH_PRIORITY_WITH_NOISE
        confidence = round(min(0.82, 0.58 + priority_count * 0.06), 2)
    elif priority_count:
        bucket = BUCKET_REVIEW
        match_type = MATCH_PRIORITY
        confidence = round(min(0.86, 0.64 + priority_count * 0.06), 2)
    elif noise_count:
        bucket = BUCKET_NOISE
        match_type = MATCH_NOISE
        confidence = round(min(0.92, 0.78 + noise_count * 0.03), 2)
    else:
        bucket = BUCKET_OTHER
        match_type = MATCH_NONE
        confidence = 0.0

    payload: dict[str, Any] = {
        "message_id": message.message_id,
        "thread_id": message.thread_id,
        "message_date": message.message_date.isoformat(),
        "sender_domain": message.sender_domain,
        "suggested_bucket": bucket,
        "match_type": match_type,
        "confidence": confidence,
        "matched_watch_term_count": (
            selected_watch.matched_term_count if selected_watch else 0
        ),
        "matched_watch_domain_count": (
            selected_watch.matched_domain_count if selected_watch else 0
        ),
        "matched_priority_keyword_count": priority_count,
        "matched_noise_keyword_count": noise_count,
    }
    if selected_watch is not None:
        payload["matched_watch_id"] = selected_watch.watch_id
    if len(watch_candidates) > 1:
        payload["ambiguous_watchlist_count"] = len(watch_candidates)
    return _Classification(payload=payload)


def _watch_candidate(
    message: _Message,
    rule: _WatchRule,
) -> _WatchCandidate | None:
    term_count = _match_count(message.search_text, rule.terms)
    domain_count = sum(
        1 for domain in rule.domains
        if _domain_matches(message.sender_domain, domain)
    )
    if not term_count and not domain_count:
        return None
    return _WatchCandidate(
        watch_id=rule.watch_id,
        matched_term_count=term_count,
        matched_domain_count=domain_count,
    )


def _select_watch_candidate(
    candidates: tuple[_WatchCandidate, ...],
) -> _WatchCandidate | None:
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            -item.matched_domain_count,
            -item.matched_term_count,
            item.watch_id,
        ),
    )[0]


def _parse_watchlist(raw: Any) -> tuple[_WatchRule, ...]:
    if not isinstance(raw, list) or not raw:
        raise Email01InboxPriorityClassifierError(
            "watchlist must be a non-empty list"
        )
    rules: list[_WatchRule] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise Email01InboxPriorityClassifierError(
                f"watchlist[{index}] must be an object"
            )
        watch_id = _required_str(item, "watch_id")
        if not _ID_RE.fullmatch(watch_id):
            raise Email01InboxPriorityClassifierError(
                f"watch_id has invalid characters: {watch_id}"
            )
        if watch_id in seen:
            raise Email01InboxPriorityClassifierError(
                f"duplicate watch_id: {watch_id}"
            )
        seen.add(watch_id)
        terms = _parse_keyword_list(item.get("terms", []),
                                    f"watchlist[{index}].terms")
        domains = _parse_domains(item.get("domains", []),
                                 f"watchlist[{index}].domains")
        if not terms and not domains:
            raise Email01InboxPriorityClassifierError(
                f"watchlist[{index}] must include terms or domains"
            )
        rules.append(_WatchRule(
            watch_id=watch_id,
            terms=terms,
            domains=domains,
        ))
    return tuple(rules)


def _parse_messages(raw: Any) -> tuple[_Message, ...]:
    if not isinstance(raw, list) or not raw:
        raise Email01InboxPriorityClassifierError(
            "messages must be a non-empty list"
        )
    messages: list[_Message] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise Email01InboxPriorityClassifierError(
                f"messages[{index}] must be an object"
            )
        message_id = _required_str(item, "message_id")
        if message_id in seen:
            raise Email01InboxPriorityClassifierError(
                f"duplicate message_id: {message_id}"
            )
        seen.add(message_id)
        thread_id = _required_str(item, "thread_id")
        message_date = _parse_iso_date(_required_str(item, "date"), "date")
        sender = _required_str(item, "from")
        sender_domain = _sender_domain(sender)
        messages.append(_Message(
            message_id=message_id,
            thread_id=thread_id,
            message_date=message_date,
            sender_domain=sender_domain,
            search_text=_message_search_text(item, sender_domain),
        ))
    return tuple(messages)


def _parse_keyword_list(raw: Any, label: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise Email01InboxPriorityClassifierError(f"{label} must be a list")
    values: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        value = _list_str(item, f"{label}[{index}]")
        if len(value) > 120:
            raise Email01InboxPriorityClassifierError(
                f"{label}[{index}] is too long"
            )
        normalized = _normalize_text(value)
        if len(normalized) < 2:
            raise Email01InboxPriorityClassifierError(
                f"{label}[{index}] must contain at least two characters"
            )
        if normalized not in seen:
            values.append(normalized)
            seen.add(normalized)
    return tuple(values)


def _parse_domains(raw: Any, label: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise Email01InboxPriorityClassifierError(f"{label} must be a list")
    domains: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        value = _list_str(item, f"{label}[{index}]").casefold().strip(".")
        if "@" in value or not _DOMAIN_RE.fullmatch(value):
            raise Email01InboxPriorityClassifierError(
                f"{label}[{index}] must be a domain name"
            )
        if value not in seen:
            domains.append(value)
            seen.add(value)
    return tuple(domains)


def _message_search_text(item: Mapping[str, Any], sender_domain: str) -> str:
    parts = [sender_domain, _required_str(item, "from")]
    for key in _MESSAGE_TEXT_KEYS:
        value = item.get(key)
        if isinstance(value, bool):
            raise Email01InboxPriorityClassifierError(f"{key} must be a string")
        if isinstance(value, str):
            if len(value) > MAX_TEXT_LENGTH:
                raise Email01InboxPriorityClassifierError(f"{key} is too long")
            if value.strip():
                parts.append(value)
        elif value is not None:
            raise Email01InboxPriorityClassifierError(f"{key} must be a string")

    labels = item.get("labels", [])
    if not isinstance(labels, list):
        raise Email01InboxPriorityClassifierError("labels must be a list")
    for index, label in enumerate(labels):
        parts.append(_list_str(label, f"labels[{index}]"))
    return _normalize_text(" ".join(parts))


def _sender_domain(sender: str) -> str:
    _, email_address = parseaddr(sender)
    if "@" not in email_address:
        raise Email01InboxPriorityClassifierError(
            "from must contain an email address"
        )
    domain = email_address.rsplit("@", 1)[1].casefold().strip(".")
    if not _DOMAIN_RE.fullmatch(domain):
        raise Email01InboxPriorityClassifierError(
            "from must contain a valid domain"
        )
    return domain


def _match_count(search_text: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for keyword in keywords if keyword in search_text)


def _confidence(
    *,
    base: float,
    watch: _WatchCandidate,
    priority_count: int,
    noise_count: int,
) -> float:
    score = (
        base
        + watch.matched_domain_count * 0.04
        + watch.matched_term_count * 0.02
        + priority_count * 0.01
        - noise_count * 0.02
    )
    return round(max(0.0, min(0.99, score)), 2)


def _bucket_sort_rank(bucket: str) -> int:
    return {
        BUCKET_WATCH: 0,
        BUCKET_REVIEW: 1,
        BUCKET_NOISE: 2,
        BUCKET_OTHER: 3,
    }.get(bucket, 99)


def _classification_sort_key(item: Mapping[str, Any]) -> tuple[int, int, str]:
    return (
        _bucket_sort_rank(str(item["suggested_bucket"])),
        -_parse_iso_date(str(item["message_date"]), "message_date").toordinal(),
        str(item["message_id"]),
    )


def _parse_iso_date(raw: str, label: str) -> date:
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise Email01InboxPriorityClassifierError(
            f"{label} must be an ISO date"
        ) from exc
    if parsed.isoformat() != raw:
        raise Email01InboxPriorityClassifierError(f"{label} must be an ISO date")
    return parsed


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        raise Email01InboxPriorityClassifierError(
            f"{key} must be a non-empty string"
        )
    return value


def _list_str(value: Any, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        raise Email01InboxPriorityClassifierError(
            f"{label} must be a non-empty string"
        )
    return value


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _domain_matches(sender_domain: str, watched_domain: str) -> bool:
    return sender_domain == watched_domain or sender_domain.endswith(
        "." + watched_domain
    )


__all__ = [
    "BUCKET_NOISE",
    "BUCKET_OTHER",
    "BUCKET_REVIEW",
    "BUCKET_WATCH",
    "CASE_ID",
    "Email01InboxPriorityClassificationResult",
    "Email01InboxPriorityClassifierError",
    "OK",
    "classify_email01_inbox_messages",
]
