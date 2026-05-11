# SPDX-License-Identifier: Apache-2.0
"""R21.3 — BridgeLLMRedactor + BridgeLLMRehydrator.

Per operator decision 4 (2026-05-10) for cloud-bound prompts:

- redact email-like matches: ``[\\w@.+-]+`` (operator-provided regex,
  used verbatim)
- redact credit-card-like digit spans: ``\\b\\d{13,19}\\b``
- redact phone-like spans: ``\\+?\\d[\\d\\s-]{8,}``
- redact Finnish HETU / IBAN / Y-tunnus before generic phone spans so
  partial identifiers do not leak and telemetry keeps the right class
- redact full file paths

Hard default: ``AcceptPiiToCloud=False``. Cloud-bound PII is allowed
only with explicit per-call opt-in.

Replacement placeholders per R20 master prompt §2.6:
``<EMAIL_1>``, ``<TOKEN_1>``, ``<PHONE_1>``, ``<PATH_1>`` (numbered
sequentially per call).

Rehydrator reverses the redaction for safe paths only — i.e., the
rehydrator is NOT used on cloud-LLM responses, only on internal
post-processing where the original placeholder map is trusted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Pattern


# Operator decision 4 regexes, used verbatim where the operator
# specified them. Kept as compiled patterns for hot-path use.
EMAIL_RE: Pattern[str] = re.compile(r"[\w.+-]+@[\w.+-]+")
CREDIT_CARD_RE: Pattern[str] = re.compile(r"\b\d{13,19}\b")
# Finnish identifiers must run before PHONE_RE. The generic phone
# pattern is intentionally broad enough to catch digit-heavy PII, but
# without these earlier classifiers it leaves HETU/IBAN fragments in
# the redacted text and mislabels telemetry as PHONE.
HETU_RE: Pattern[str] = re.compile(
    r"\b[0-3]\d[01]\d\d{2}[+\-A]\d{3}[0-9A-Y]\b",
)
IBAN_RE: Pattern[str] = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){8,32}\b")
Y_TUNNUS_RE: Pattern[str] = re.compile(r"\b\d{7}-\d\b")
# Phone: optional leading +, then a digit, then at least 8 more
# digits/spaces/hyphens. Matches international + national patterns.
PHONE_RE: Pattern[str] = re.compile(r"\+?\d[\d\s-]{8,}")
# Full file paths — Windows (C:\..., D:\...) AND POSIX (/foo/bar).
# Matches a path-like atom of >=2 segments to avoid catching every
# bare slash. Conservative; tune in follow-up if it overscrubs.
WINDOWS_PATH_RE: Pattern[str] = re.compile(
    r"[A-Za-z]:\\[\w.\-\\]+",
)
POSIX_PATH_RE: Pattern[str] = re.compile(
    r"(?:/[\w.\-]+){2,}",
)
# R22.0 finding F1: pre-mask URLs so the path regex doesn't chew the
# path-portion of a URL. Looking-behind for `:` was insufficient
# because the URL's *second* slash is preceded by another slash, not
# the colon — and any later segment break is preceded by an
# alphanumeric char that the lookbehind can't see past.
URL_RE: Pattern[str] = re.compile(r"https?://[^\s<>\"']+")
_URL_MASK_OPEN = "\x00URL\x00"
_URL_MASK_CLOSE = "\x00ENDURL\x00"


# Placeholder names per master prompt §2.6
EMAIL_PLACEHOLDER = "EMAIL"
TOKEN_PLACEHOLDER = "TOKEN"   # credit-card / opaque digit token
HETU_PLACEHOLDER = "HETU"
IBAN_PLACEHOLDER = "IBAN"
BUSINESS_ID_PLACEHOLDER = "BUSINESS_ID"
PHONE_PLACEHOLDER = "PHONE"
PATH_PLACEHOLDER = "PATH"


@dataclass
class RedactionResult:
    """Output of one BridgeLLMRedactor.redact() call.

    Attributes:
        text: the redacted prompt (placeholders substituted).
        replacements: ordered map of placeholder ID to original text,
            so the rehydrator can reverse the redaction safely.
        counts: per-category count for telemetry.
        applied: True if any redaction happened (caller may skip the
            cloud call if False, treating the input as already safe).
        failed: True only if the redactor itself failed (R22.0 finding
            F2). Cloud providers MUST consult this flag instead of
            comparing ``text == "<REDACTOR_FAILED>"`` to avoid a
            user-prompt-controlled false fail-closed (a malicious or
            accidental prompt containing the literal sentinel string
            would otherwise spoof the fail-closed signal).
    """

    text: str
    replacements: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    applied: bool = False
    failed: bool = False


class BridgeLLMRedactor:
    """Cloud-bound prompt redactor with per-call enable flag.

    Default behavior (``accept_pii_to_cloud=False``): every cloud-bound
    prompt MUST be redacted before transmission. The redactor returns a
    RedactionResult with placeholders + the replacements map. If the
    caller passes a non-redactable string (rare: empty, no PII), the
    result is unchanged but ``applied=False`` so telemetry can record
    "scrub skipped because no PII matched".

    Per-call ``accept_pii_to_cloud=True`` skips the redaction — the
    caller is responsible for the security posture of that opt-in. The
    flag must be logged in telemetry per master prompt §2.6.

    "Fail closed": if the redactor crashes mid-call (regex pathology,
    encoding error, etc.), it returns a RedactionResult that replaces
    the entire prompt with ``<REDACTOR_FAILED>``. The cloud provider
    sees a sentinel string and can refuse to dispatch the call. This
    is the operator-decision-4 "fail closed if redaction is unavailable"
    requirement.
    """

    def redact(self, prompt: str, *, accept_pii_to_cloud: bool = False) -> RedactionResult:
        if accept_pii_to_cloud:
            return RedactionResult(
                text=prompt,
                replacements={},
                counts={},
                applied=False,
            )
        try:
            return self._redact_safely(prompt)
        except Exception:
            # Fail closed — see class docstring. R22.0: also set
            # failed=True so the cloud provider's check doesn't have
            # to compare the text against the sentinel string (which
            # a malicious prompt could spoof).
            return RedactionResult(
                text="<REDACTOR_FAILED>",
                replacements={},
                counts={},
                applied=True,
                failed=True,
            )

    def _redact_safely(self, prompt: str) -> RedactionResult:
        replacements: dict[str, str] = {}
        counts: dict[str, int] = {
            EMAIL_PLACEHOLDER: 0,
            TOKEN_PLACEHOLDER: 0,
            HETU_PLACEHOLDER: 0,
            IBAN_PLACEHOLDER: 0,
            BUSINESS_ID_PLACEHOLDER: 0,
            PHONE_PLACEHOLDER: 0,
            PATH_PLACEHOLDER: 0,
        }

        text = prompt

        def make_replacer(category: str) -> callable:
            def _replace(match: re.Match[str]) -> str:
                counts[category] += 1
                idx = counts[category]
                placeholder = f"<{category}_{idx}>"
                replacements[placeholder] = match.group(0)
                return placeholder
            return _replace

        # R22.0 F1: mask URLs first so POSIX_PATH_RE doesn't munch
        # their path-portion. We restore them at the end before
        # returning the redacted text.
        url_masks: list[str] = []
        def _mask_url(m: re.Match[str]) -> str:
            url_masks.append(m.group(0))
            return f"{_URL_MASK_OPEN}{len(url_masks) - 1}{_URL_MASK_CLOSE}"
        text = URL_RE.sub(_mask_url, text)

        # Order matters: paths first (to avoid the email regex chewing
        # paths that contain `@`); specific Finnish identifiers before
        # generic digit patterns; credit-card before phone (digit
        # patterns overlap); email before phone (digit-only phone might
        # catch part of a numeric local-part).
        text = WINDOWS_PATH_RE.sub(make_replacer(PATH_PLACEHOLDER), text)
        text = POSIX_PATH_RE.sub(make_replacer(PATH_PLACEHOLDER), text)
        text = HETU_RE.sub(make_replacer(HETU_PLACEHOLDER), text)
        text = IBAN_RE.sub(make_replacer(IBAN_PLACEHOLDER), text)
        text = Y_TUNNUS_RE.sub(make_replacer(BUSINESS_ID_PLACEHOLDER), text)
        text = EMAIL_RE.sub(make_replacer(EMAIL_PLACEHOLDER), text)
        text = CREDIT_CARD_RE.sub(make_replacer(TOKEN_PLACEHOLDER), text)
        text = PHONE_RE.sub(make_replacer(PHONE_PLACEHOLDER), text)

        # Restore masked URLs verbatim — they were never PII to begin
        # with for redaction purposes (the operator's decision-4
        # categories are emails / credit-cards / phones / file paths,
        # not URLs).
        if url_masks:
            for idx, original in enumerate(url_masks):
                placeholder = f"{_URL_MASK_OPEN}{idx}{_URL_MASK_CLOSE}"
                text = text.replace(placeholder, original)

        applied = any(c > 0 for c in counts.values())
        return RedactionResult(
            text=text,
            replacements=replacements,
            counts=counts,
            applied=applied,
        )


class BridgeLLMRehydrator:
    """Reverse a RedactionResult's placeholders back to original text.

    Used ONLY on internal post-processing where the placeholder map is
    trusted. Cloud-LLM responses are NEVER rehydrated — if the cloud
    LLM echoes ``<EMAIL_1>`` we want that placeholder to remain in the
    response so the placeholder set is the caller's choice to expand
    or not.
    """

    def rehydrate(self, text: str, replacements: dict[str, str]) -> str:
        out = text
        # Sort by placeholder length descending so e.g. <EMAIL_10> is
        # substituted before <EMAIL_1> (which is a prefix of the longer
        # one). Without this, partial replacement would corrupt the
        # text.
        for placeholder in sorted(replacements.keys(), key=len, reverse=True):
            out = out.replace(placeholder, replacements[placeholder])
        return out
