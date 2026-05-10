"""R21.3 — BridgeLLMRedactor + AnthropicProvider tests.

Operator decision 4 contract: emails / credit-cards / phones / paths
all redact ON BY DEFAULT for cloud-bound prompts. AcceptPiiToCloud=False
hard default. Cloud provider must fail closed when redactor returns
the failure sentinel.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


# ─── Subprocess-isolated import discipline ───────────────────────

def test_importing_redactor_does_not_pull_in_anthropic_sdk():
    """Profile S contract continues: importing the redactor must not
    transitively import the anthropic SDK."""
    script = textwrap.dedent('''
        import sys
        sys.path.insert(0, %r)
        from waggledance.core.bridge_llm import (
            BridgeLLMRedactor, BridgeLLMRehydrator, RedactionResult,
        )
        leaked = [n for n in ("anthropic", "openai", "ollama")
                   if n in sys.modules]
        if leaked:
            print(f"LEAKED: {leaked}")
            sys.exit(1)
        print("REDACTOR_IMPORT_CLEAN")
    ''') % (str(REPO_ROOT),)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        f"failed: stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert "REDACTOR_IMPORT_CLEAN" in proc.stdout


# ─── Operator decision 4 PII regex contract ──────────────────────

def test_redactor_scrubs_email():
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact("Contact alice@example.org for details")
    assert "alice@example.org" not in result.text
    assert "<EMAIL_1>" in result.text
    assert result.applied is True
    assert result.replacements["<EMAIL_1>"] == "alice@example.org"


def test_redactor_scrubs_credit_card():
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    # Visa test number, 16 digits → matches \b\d{13,19}\b
    result = r.redact("Card 4111111111111111 was declined")
    assert "4111111111111111" not in result.text
    assert "<TOKEN_1>" in result.text
    assert result.replacements["<TOKEN_1>"] == "4111111111111111"


def test_redactor_scrubs_phone():
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact("Call me on +358 40 123 4567 anytime")
    assert "+358 40 123 4567" not in result.text
    assert "<PHONE_1>" in result.text


def test_redactor_scrubs_windows_path():
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact("Open C:\\Users\\alice\\secret\\file.txt for the keys")
    assert "C:\\Users\\alice" not in result.text
    assert "<PATH_1>" in result.text


def test_redactor_scrubs_posix_path():
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact("Read /home/alice/.ssh/id_rsa for SSH access")
    assert "/home/alice/.ssh/id_rsa" not in result.text
    assert "<PATH_1>" in result.text


def test_redactor_scrubs_multiple_categories_in_one_prompt():
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact(
        "Contact alice@example.org or call 0401234567 about "
        "card 4111111111111111; logs in /var/log/app.log"
    )
    assert result.applied is True
    assert "<EMAIL_1>" in result.text
    assert "<TOKEN_1>" in result.text
    assert "<PHONE_1>" in result.text
    assert "<PATH_1>" in result.text
    # Original PII tokens completely scrubbed
    for original in (
        "alice@example.org",
        "0401234567",
        "4111111111111111",
        "/var/log/app.log",
    ):
        assert original not in result.text, f"{original!r} leaked into redacted text"


def test_redactor_numbers_placeholders_sequentially():
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact("alice@example.org and bob@example.org both report it")
    assert "<EMAIL_1>" in result.text
    assert "<EMAIL_2>" in result.text
    assert result.replacements["<EMAIL_1>"] == "alice@example.org"
    assert result.replacements["<EMAIL_2>"] == "bob@example.org"


def test_redactor_no_pii_returns_unchanged_with_applied_false():
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact("Hello, world. This is a benign prompt.")
    assert result.text == "Hello, world. This is a benign prompt."
    assert result.applied is False
    assert result.replacements == {}


# ─── AcceptPiiToCloud opt-in ─────────────────────────────────────

def test_redactor_skips_when_accept_pii_to_cloud_true():
    """AcceptPiiToCloud=True passes the prompt through unchanged so
    the caller can intentionally send PII (with audit-log warning)."""
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact(
        "Email alice@example.org",
        accept_pii_to_cloud=True,
    )
    assert result.text == "Email alice@example.org"
    assert result.applied is False
    assert result.replacements == {}


def test_redactor_default_is_accept_pii_false():
    """Calling redact() with no kwarg must behave as if
    accept_pii_to_cloud=False — operator decision 4 hard default."""
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact("alice@example.org")
    assert "<EMAIL_1>" in result.text
    assert result.applied is True


# ─── Fail-closed contract ────────────────────────────────────────

def test_redactor_fail_closed_returns_sentinel(monkeypatch):
    """If the redactor crashes mid-call, it returns a sentinel
    `<REDACTOR_FAILED>` so the cloud provider can refuse to dispatch."""
    from waggledance.core.bridge_llm.redactor import BridgeLLMRedactor

    def raise_inside(*args, **kwargs):
        raise RuntimeError("simulated regex pathology")

    r = BridgeLLMRedactor()
    monkeypatch.setattr(r, "_redact_safely", raise_inside)
    result = r.redact("any prompt")
    assert result.text == "<REDACTOR_FAILED>"
    assert result.applied is True
    assert result.replacements == {}


# ─── Rehydrator (internal-only) ──────────────────────────────────

def test_rehydrator_reverses_redaction():
    from waggledance.core.bridge_llm import BridgeLLMRedactor, BridgeLLMRehydrator
    r = BridgeLLMRedactor()
    h = BridgeLLMRehydrator()
    redacted = r.redact("alice@example.org and 4111111111111111")
    rehydrated = h.rehydrate(redacted.text, redacted.replacements)
    assert rehydrated == "alice@example.org and 4111111111111111"


def test_rehydrator_handles_double_digit_indexes():
    """Sort-by-length-desc replacement matters: <EMAIL_10> must
    substitute before <EMAIL_1> so we don't corrupt the longer
    placeholder."""
    from waggledance.core.bridge_llm import BridgeLLMRedactor, BridgeLLMRehydrator
    r = BridgeLLMRedactor()
    h = BridgeLLMRehydrator()
    # Build a prompt with 11 emails so we get <EMAIL_10> and <EMAIL_11>
    prompt = " and ".join(f"user{i}@example.org" for i in range(11))
    redacted = r.redact(prompt)
    rehydrated = h.rehydrate(redacted.text, redacted.replacements)
    assert rehydrated == prompt


# ─── AnthropicProvider plumbing ──────────────────────────────────

def test_anthropic_provider_is_available_returns_bool():
    from waggledance.core.bridge_llm.providers.anthropic import AnthropicProvider
    p = AnthropicProvider()
    # Return type must be bool regardless of env state
    assert isinstance(p.is_available(), bool)


def test_anthropic_provider_unavailable_when_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from waggledance.core.bridge_llm.providers.anthropic import AnthropicProvider
    p = AnthropicProvider()
    # Even if the SDK is installed, no API key means unavailable.
    assert p.is_available() is False


def test_anthropic_provider_call_raises_provider_error_when_unavailable(monkeypatch):
    """Critical: cloud provider must raise ProviderError (not crash
    with ImportError or AuthError) so the four-tier chain falls
    through cleanly to heuristic."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from waggledance.core.bridge_llm.providers.anthropic import AnthropicProvider
    from waggledance.core.bridge_llm.providers.base import ProviderError
    from waggledance.core.bridge_llm.types import LLMRequest
    p = AnthropicProvider()
    with pytest.raises(ProviderError):
        p.call(LLMRequest(injection_point="x", prompt="hello"))


def test_anthropic_provider_fails_closed_on_redactor_sentinel(monkeypatch):
    """Operator decision 4 'fail closed when redaction is unavailable':
    if BridgeLLMRedactor returns <REDACTOR_FAILED>, AnthropicProvider
    MUST refuse to dispatch the cloud call."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-stub-key")
    from waggledance.core.bridge_llm.providers.anthropic import AnthropicProvider
    from waggledance.core.bridge_llm.providers.base import ProviderError
    from waggledance.core.bridge_llm.types import LLMRequest
    from waggledance.core.bridge_llm.redactor import (
        BridgeLLMRedactor, RedactionResult,
    )

    class FailedRedactor(BridgeLLMRedactor):
        def redact(self, prompt, *, accept_pii_to_cloud=False):
            return RedactionResult(
                text="<REDACTOR_FAILED>",
                replacements={},
                counts={},
                applied=True,
            )

    p = AnthropicProvider(redactor=FailedRedactor())
    # Patch is_available so we get past the find_spec check
    monkeypatch.setattr(p, "is_available", lambda: True)
    with pytest.raises(ProviderError, match="redactor failed"):
        p.call(LLMRequest(injection_point="x", prompt="prompt-with-pii"))
