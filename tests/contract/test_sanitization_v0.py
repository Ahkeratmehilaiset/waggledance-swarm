# SPDX-License-Identifier: BUSL-1.1
"""Sanitization contract v0 — enumerated test surface.

Phase F PR3 of the operator-approved 100h plan. Each test below
documents one clause of the sanitization contract that the
``BridgeLLMRedactor`` (waggledance.core.bridge_llm.redactor) is
required to honour for cloud-bound prompts. Failures here are real
findings: do NOT weaken these tests to make them pass; surface the gap
as a finding instead. Companion contract doc:
``docs/architecture/SANITIZATION_CONTRACT_V0.md``.

Contract source of truth: operator decision 4 (2026-05-10) + R20
master prompt §2.6 placeholders + R22.0 finding F1 (URL pre-mask).
"""
from __future__ import annotations

import pytest

from waggledance.core.bridge_llm.redactor import BridgeLLMRedactor


@pytest.fixture()
def redactor() -> BridgeLLMRedactor:
    return BridgeLLMRedactor()


# --- Clause 1: email redaction --------------------------------------------

def test_clause_1_email_address_is_redacted(redactor):
    result = redactor.redact("Contact me at alice@example.com please.")
    assert "alice@example.com" not in result.text
    assert "<EMAIL_1>" in result.text


# --- Clause 2: credit-card-like digit spans --------------------------------

def test_clause_2_credit_card_digit_span_is_redacted(redactor):
    result = redactor.redact("Card number 4111111111111111 on file.")
    assert "4111111111111111" not in result.text
    assert "<TOKEN_1>" in result.text


# --- Clause 3: phone-like spans -------------------------------------------

def test_clause_3_phone_number_is_redacted(redactor):
    result = redactor.redact("Call +358 50 123 4567 for support.")
    # Phone digits must NOT survive
    assert "50 123 4567" not in result.text
    assert "<PHONE_1>" in result.text


# --- Clause 4: Finnish HETU (personal identifier) -------------------------

def test_clause_4_finnish_hetu_is_redacted(redactor):
    # 1900s HETU pattern: ddmmyy-XXXY
    result = redactor.redact("Customer HETU 010180-123A registered.")
    assert "010180-123A" not in result.text
    assert "<HETU_1>" in result.text


def test_clause_4b_finnish_hetu_2000s_separator_is_redacted(redactor):
    # 2000s HETU uses A-F separators (e.g., 'A' for 2000-2009)
    result = redactor.redact("Customer HETU 010105A123B registered.")
    assert "010105A123B" not in result.text
    assert "<HETU_1>" in result.text


# --- Clause 5: IBAN -------------------------------------------------------

def test_clause_5_iban_is_redacted(redactor):
    result = redactor.redact("Pay to FI21 1234 5600 0007 85 by EOM.")
    assert "FI21 1234 5600 0007 85" not in result.text
    assert "<IBAN_1>" in result.text


# --- Clause 6: Y-tunnus (Finnish business ID) -----------------------------

def test_clause_6_y_tunnus_is_redacted(redactor):
    result = redactor.redact("Invoice from y-tunnus 1234567-8 today.")
    assert "1234567-8" not in result.text
    # The redactor emits Y-tunnus under the BUSINESS_ID placeholder
    # class. The contract requires the class label survives so
    # downstream telemetry can attribute correctly.
    assert "<BUSINESS_ID_1>" in result.text


# --- Clause 7: Windows file paths -----------------------------------------

def test_clause_7_windows_path_is_redacted(redactor):
    result = redactor.redact(r"Check C:\Users\jani\.claude\settings.json.")
    assert r"C:\Users\jani" not in result.text
    assert "<PATH_1>" in result.text


# --- Clause 8: POSIX file paths -------------------------------------------

def test_clause_8_posix_path_is_redacted(redactor):
    result = redactor.redact("Edit /home/jani/.bashrc and reload.")
    assert "/home/jani/.bashrc" not in result.text
    assert "<PATH_1>" in result.text


# --- Clause 9: URLs are PRESERVED verbatim (NOT redacted) -----------------

def test_clause_9_https_url_survives_redaction(redactor):
    # R22.0 finding F1: URLs are pre-masked so the path regex does not
    # chew the path-portion of a URL. After redaction the URL must
    # survive unchanged.
    url = "https://github.com/Ahkeratmehilaiset/waggledance-swarm/pull/603"
    result = redactor.redact(f"See {url} for details.")
    assert url in result.text


# --- Clause 10: default accept_pii_to_cloud is False ----------------------

def test_clause_10_default_is_redaction_on(redactor):
    """The cloud-bound default MUST redact PII. accept_pii_to_cloud=True
    is the explicit opt-in escape hatch, never the default."""
    result = redactor.redact("alice@example.com asked about pricing.")
    # Default call (no accept_pii_to_cloud kwarg) MUST redact.
    assert "alice@example.com" not in result.text


def test_clause_10b_explicit_opt_in_preserves_pii(redactor):
    """When the caller explicitly passes accept_pii_to_cloud=True, the
    redactor MUST NOT redact -- the opt-in surface is intentional and
    auditable. This clause is fail-closed: if a future change causes
    the redactor to strict-redact under opt-in, that is a contract
    regression and a real finding, not an expected failure. No xfail
    escape hatch."""
    text = "alice@example.com asked about pricing."
    result = redactor.redact(text, accept_pii_to_cloud=True)
    assert "alice@example.com" in result.text


# --- Clause 11: placeholders are numbered sequentially per call -----------

def test_clause_11_placeholders_are_numbered_per_call(redactor):
    result = redactor.redact(
        "alice@example.com and bob@example.com are both customers."
    )
    # Two emails ⇒ EMAIL_1 and EMAIL_2 in encounter order.
    assert "<EMAIL_1>" in result.text
    assert "<EMAIL_2>" in result.text


# --- Clause 12: same-class multiple instances stay distinguishable --------

def test_clause_12_multiple_classes_in_one_call_get_distinct_placeholders(
    redactor,
):
    result = redactor.redact(
        "Email alice@example.com from C:\\Users\\alice\\notes."
    )
    assert "<EMAIL_1>" in result.text
    assert "PATH" in result.text
    # Email and PATH placeholders MUST be distinguishable.
    # (Not asserting numeric uniqueness across classes; the contract
    # only requires the CLASS is preserved.)


# --- Smoke: known-safe text passes through ---------------------------------

def test_smoke_neutral_text_is_unchanged(redactor):
    """A purely neutral, non-PII input must round-trip verbatim. Guards
    against over-redaction (which would inflate false positives and
    is itself a contract violation -- see #599 case
    `case:adv:payload_leak:004` for the RFC 2606 example-domain
    false-positive that this clause guards against)."""
    text = "The quick brown fox jumps over the lazy dog."
    result = redactor.redact(text)
    assert result.text == text
