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


def test_redactor_phone_hint_preserves_operator_regex_shape():
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact("Call +1 234-567 today")
    assert "+1 234-567" not in result.text
    assert "<PHONE_1>" in result.text


def test_redactor_scrubs_finnish_hetu_before_phone():
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact("HETU 010195-123A belongs to the applicant")
    assert "010195-123A" not in result.text
    assert "<HETU_1>" in result.text
    assert "<PHONE_" not in result.text
    assert result.counts["HETU"] == 1
    assert result.counts["PHONE"] == 0
    assert result.replacements["<HETU_1>"] == "010195-123A"


@pytest.mark.parametrize("separator", ["B", "C", "D", "E", "F", "Y", "X", "W", "V", "U"])
def test_redactor_scrubs_finnish_hetu_new_dvv_separators_before_phone(separator):
    """DVV's 2023 separator expansion added B-F for 2000s and Y-U for 1900s."""
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    hetu = f"030604{separator}903K"
    result = r.redact(f"HETU {hetu} belongs to the applicant")
    assert hetu not in result.text
    assert "<HETU_1>" in result.text
    assert "<PHONE_" not in result.text
    assert result.counts["HETU"] == 1
    assert result.counts["PHONE"] == 0
    assert result.replacements["<HETU_1>"] == hetu


def test_redactor_scrubs_finnish_iban_country_code():
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact("Pay to FI21 1234 5600 0007 85")
    assert "FI21" not in result.text
    assert "1234 5600 0007 85" not in result.text
    assert "<IBAN_1>" in result.text
    assert "<PHONE_" not in result.text
    assert result.counts["IBAN"] == 1
    assert result.counts["PHONE"] == 0
    assert result.replacements["<IBAN_1>"] == "FI21 1234 5600 0007 85"


def test_redactor_scrubs_lowercase_iban_before_phone():
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact("Pay to fi2112345600000785")
    assert "fi2112345600000785" not in result.text
    assert "<IBAN_1>" in result.text
    assert "<PHONE_" not in result.text
    assert result.counts["IBAN"] == 1
    assert result.counts["PHONE"] == 0
    assert result.replacements["<IBAN_1>"] == "fi2112345600000785"


def test_redactor_classifies_finnish_business_id_before_phone():
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact("Y-tunnus 2828492-2")
    assert "2828492-2" not in result.text
    assert "<BUSINESS_ID_1>" in result.text
    assert "<PHONE_" not in result.text
    assert result.counts["BUSINESS_ID"] == 1
    assert result.counts["PHONE"] == 0
    assert result.replacements["<BUSINESS_ID_1>"] == "2828492-2"


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
                failed=True,
            )

    p = AnthropicProvider(redactor=FailedRedactor())
    # Patch is_available so we get past the find_spec check
    monkeypatch.setattr(p, "is_available", lambda: True)
    with pytest.raises(ProviderError, match="redactor failed"):
        p.call(LLMRequest(injection_point="x", prompt="prompt-with-pii"))


# ─── R22.0 hotfix: 3 post-merge audit findings ───────────────────

def test_r22_f1_url_paths_are_not_redacted_as_paths():
    """R22.0 F1: POSIX_PATH_RE was eating URL paths.
    `https://docs.python.org/3/library/re.html` was being scrubbed
    to `https:/<PATH_1>`. Fix: negative lookbehind on `:` so URLs
    pass through. Bare paths like `/etc/passwd` still match."""
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()

    # URL must NOT be redacted
    result = r.redact("See https://docs.python.org/3/library/re.html for re docs")
    assert "https://docs.python.org/3/library/re.html" in result.text
    assert "<PATH_" not in result.text

    # Plain POSIX path STILL gets redacted (the fix is targeted, not blanket-disabling paths)
    result = r.redact("Read /etc/passwd for the user list")
    assert "<PATH_1>" in result.text
    assert "/etc/passwd" not in result.text

    # GitHub URLs also pass through
    result = r.redact("Issue at https://github.com/owner/repo/issues/42")
    assert "https://github.com/owner/repo/issues/42" in result.text


def test_r22_f1_url_with_query_string_passes_through():
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    # URL with query string and fragment — neither should redact
    text = "Visit https://example.com/api/v1/users?id=42&token=abc"
    result = r.redact(text)
    # The URL path part is preserved (no <PATH_> placeholders)
    assert "<PATH_" not in result.text
    # If a credit-card-shaped digit ran was present in the query, that
    # would still scrub — but no such pattern here.
    assert "https://example.com/api/v1/users" in result.text


def test_r22_f2_redactor_failed_flag_is_set_on_internal_error(monkeypatch):
    """R22.0 F2: RedactionResult must carry a structured `failed`
    bool that callers consult, not text-equality on the sentinel
    string. A malicious prompt containing literal '<REDACTOR_FAILED>'
    must NOT trigger fail-closed in the cloud provider."""
    from waggledance.core.bridge_llm import BridgeLLMRedactor

    r = BridgeLLMRedactor()

    # Force internal failure
    def crash(*a, **kw):
        raise RuntimeError("simulated regex pathology")
    monkeypatch.setattr(r, "_redact_safely", crash)

    result = r.redact("any prompt")
    assert result.failed is True
    assert result.text == "<REDACTOR_FAILED>"


def test_r22_f2_redactor_succeeds_with_failed_false_on_normal_input():
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact("Email alice@example.org")
    # Normal redaction: applied=True but failed=False
    assert result.applied is True
    assert result.failed is False


def test_r22_f2_user_prompt_with_sentinel_string_is_not_failed():
    """The single most important F2 test: a user prompt containing the
    literal sentinel string '<REDACTOR_FAILED>' must NOT cause the
    cloud provider to false-fail-closed. The structured `failed`
    field stays False because no internal error occurred."""
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()
    result = r.redact("user said: <REDACTOR_FAILED>")
    assert result.failed is False
    # Text passes through unchanged (no PII match)
    assert result.text == "user said: <REDACTOR_FAILED>"


def test_r22_f2_anthropic_provider_uses_failed_flag_not_text(monkeypatch):
    """The cloud provider must check `result.failed`, not
    `result.text == '<REDACTOR_FAILED>'`. A user prompt containing
    that literal string must NOT trigger fail-closed."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-stub-key")
    from waggledance.core.bridge_llm.providers.anthropic import AnthropicProvider
    from waggledance.core.bridge_llm.providers.base import ProviderError
    from waggledance.core.bridge_llm.types import LLMRequest
    from waggledance.core.bridge_llm.redactor import (
        BridgeLLMRedactor, RedactionResult,
    )

    class PassThroughRedactor(BridgeLLMRedactor):
        def redact(self, prompt, *, accept_pii_to_cloud=False):
            # Returns the literal sentinel string in `text` but
            # failed=False (no internal failure).
            return RedactionResult(
                text="<REDACTOR_FAILED>",
                replacements={},
                counts={},
                applied=False,
                failed=False,  # ← the load-bearing flag
            )

    p = AnthropicProvider(redactor=PassThroughRedactor())
    monkeypatch.setattr(p, "is_available", lambda: True)
    # The provider should NOT raise ProviderError("redactor failed")
    # because failed=False. It will still raise something else
    # because anthropic SDK isn't configured for a real call, but
    # NOT the redactor-fail-closed branch.
    with pytest.raises(ProviderError) as excinfo:
        p.call(LLMRequest(injection_point="x", prompt="prompt"))
    assert "redactor failed" not in str(excinfo.value), (
        f"AnthropicProvider mis-classified pass-through sentinel as "
        f"failed-closed: {excinfo.value!r}"
    )


def test_r22_f3_cost_cents_estimate_is_positive_for_haiku():
    """R22.0 F3: cost_cents must be > 0 so the budget tracker can
    enforce a $-budget. v1 uses haiku pricing
    ($0.25/M input + $1.25/M output)."""
    from waggledance.core.bridge_llm.providers.anthropic import _estimate_cost_cents
    # 100k input + 10k output = $0.025 + $0.0125 = $0.0375 ≈ 4 cents
    cost = _estimate_cost_cents(100_000, 10_000)
    assert cost >= 4
    assert cost <= 10  # generous upper bound for rounding


def test_r22_f3_cost_cents_zero_for_zero_tokens():
    from waggledance.core.bridge_llm.providers.anthropic import _estimate_cost_cents
    assert _estimate_cost_cents(0, 0) == 0


# ─── Lane #7: redactor availability gate ─────────────────────────

def test_redactor_is_available_true_for_working_redactor():
    """A functioning redactor reports available so the cloud tier may run."""
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    assert BridgeLLMRedactor().is_available() is True


def test_redactor_is_available_false_when_scrub_machinery_broken(monkeypatch):
    """If the redaction machinery is broken, is_available() is False so a
    cloud provider refuses to egress (fail-closed)."""
    from waggledance.core.bridge_llm import BridgeLLMRedactor
    r = BridgeLLMRedactor()

    def crash(*a, **kw):
        raise RuntimeError("simulated regex pathology")

    # redact() catches this and returns failed=True; is_available() must
    # then report False (it consults the structured `failed` flag).
    monkeypatch.setattr(r, "_redact_safely", crash)
    assert r.is_available() is False


# ─── Lane #7: pre-egress redaction is the security boundary ──────

def test_anthropic_provider_sends_only_redacted_text_to_cloud(monkeypatch):
    """SECURITY CRUX (the most important test): a prompt carrying PII must
    reach the Anthropic SDK only AFTER redaction. We capture exactly what
    would be transmitted on the wire and assert it contains placeholders
    and NONE of the raw PII. Uses the REAL default redactor."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-stub-key")
    from waggledance.core.bridge_llm.providers.anthropic import AnthropicProvider
    from waggledance.core.bridge_llm.types import FallbackLevel, LLMRequest

    captured_create: dict = {}

    class StubUsage:
        input_tokens = 5
        output_tokens = 7

    class StubBlock:
        type = "text"
        text = "cloud-reply"

    class StubResponse:
        content = [StubBlock()]
        usage = StubUsage()

    class StubAnthropic:
        def __init__(self, timeout=None, **kwargs):
            pass

        @property
        def messages(self):
            class _M:
                @staticmethod
                def create(**kwargs):
                    captured_create.update(kwargs)
                    return StubResponse()
            return _M()

    monkeypatch.setitem(sys.modules, "anthropic", type(sys)("anthropic_stub"))
    sys.modules["anthropic"].Anthropic = StubAnthropic  # type: ignore[attr-defined]

    raw_pii = {
        "email": "alice@example.org",
        "phone": "0401234567",
        "card": "4111111111111111",
        "path": "/var/log/secret.log",
    }
    prompt = (
        f"Contact {raw_pii['email']} or call {raw_pii['phone']} about "
        f"card {raw_pii['card']}; logs in {raw_pii['path']}"
    )

    provider = AnthropicProvider()  # real BridgeLLMRedactor
    monkeypatch.setattr(provider, "is_available", lambda: True)
    response = provider.call(LLMRequest(injection_point="x", prompt=prompt))

    # The SDK WAS invoked...
    assert captured_create, "anthropic SDK was never invoked"
    sent = captured_create["messages"][0]["content"]
    # ...with placeholders substituted...
    assert "<EMAIL_1>" in sent
    assert "<PHONE_1>" in sent
    assert "<TOKEN_1>" in sent
    assert "<PATH_1>" in sent
    # ...and ZERO raw PII bytes on the wire.
    for original in raw_pii.values():
        assert original not in sent, (
            f"RAW PII {original!r} egressed to the cloud client"
        )
    # Happy path: the mocked cloud reply maps onto LLMResponse.
    assert response.text == "cloud-reply"
    assert response.success is True
    assert response.fallback_level == FallbackLevel.CLOUD_LLM
    assert response.redaction_applied is True


def test_anthropic_provider_does_not_call_cloud_when_redactor_raises(monkeypatch):
    """If the redactor RAISES (not merely returns failed=True), the
    provider must NOT construct/contact the cloud SDK; it raises
    ProviderError so the chain falls through. The raw prompt must not leak
    into the escaping error."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-stub-key")
    from waggledance.core.bridge_llm.providers.anthropic import AnthropicProvider
    from waggledance.core.bridge_llm.providers.base import ProviderError
    from waggledance.core.bridge_llm.redactor import BridgeLLMRedactor
    from waggledance.core.bridge_llm.types import LLMRequest

    class RaisingRedactor(BridgeLLMRedactor):
        def redact(self, prompt, *, accept_pii_to_cloud=False):
            raise RuntimeError("redactor backend down")

    class ExplodingAnthropic:
        def __init__(self, *a, **kw):
            raise AssertionError(
                "cloud SDK must NOT be constructed when redaction fails"
            )

    monkeypatch.setitem(sys.modules, "anthropic", type(sys)("anthropic_stub"))
    sys.modules["anthropic"].Anthropic = ExplodingAnthropic  # type: ignore[attr-defined]

    provider = AnthropicProvider(redactor=RaisingRedactor())
    monkeypatch.setattr(provider, "is_available", lambda: True)
    raw = "alice@example.org and card 4111111111111111"
    with pytest.raises(ProviderError) as excinfo:
        provider.call(LLMRequest(injection_point="x", prompt=raw))
    # The fail-closed error must not echo the raw prompt.
    assert "alice@example.org" not in str(excinfo.value)
    assert "4111111111111111" not in str(excinfo.value)


def test_anthropic_provider_unavailable_when_redactor_unavailable(monkeypatch):
    """is_available() is False when the redactor cannot scrub — even with
    a key and SDK present — so the chain skips cloud (requirement 3)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-stub-key")
    from waggledance.core.bridge_llm.providers import anthropic as ap_module
    from waggledance.core.bridge_llm.providers.anthropic import AnthropicProvider
    from waggledance.core.bridge_llm.redactor import BridgeLLMRedactor

    # Force the SDK-presence check to pass so the redactor gate is the
    # deciding factor regardless of whether anthropic is installed here.
    monkeypatch.setattr(
        ap_module.importlib.util, "find_spec",
        lambda name, *a, **kw: object() if name == "anthropic" else None,
    )

    class DeadRedactor(BridgeLLMRedactor):
        def is_available(self):
            return False

    assert AnthropicProvider(redactor=DeadRedactor()).is_available() is False
    # Control: same env + a working redactor IS available, proving the
    # redactor is the discriminating condition.
    assert AnthropicProvider().is_available() is True


def test_anthropic_provider_fails_closed_when_redactor_is_none(monkeypatch):
    """Defensive fail-closed: if the provider's redactor is cleared to None
    at runtime it is never available and call() refuses to egress.

    (The constructor defaults redactor=None to a real BridgeLLMRedactor, so
    this simulates a redactor that was torn down after construction.)"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-stub-key")
    from waggledance.core.bridge_llm.providers import anthropic as ap_module
    from waggledance.core.bridge_llm.providers.anthropic import AnthropicProvider
    from waggledance.core.bridge_llm.providers.base import ProviderError
    from waggledance.core.bridge_llm.types import LLMRequest

    monkeypatch.setattr(
        ap_module.importlib.util, "find_spec",
        lambda name, *a, **kw: object() if name == "anthropic" else None,
    )
    provider = AnthropicProvider()
    provider._redactor = None  # simulate a torn-down redactor
    assert provider.is_available() is False
    # Even if is_available is bypassed, call() refuses to egress.
    monkeypatch.setattr(provider, "is_available", lambda: True)
    with pytest.raises(ProviderError, match="redactor unavailable"):
        provider.call(LLMRequest(injection_point="x", prompt="alice@example.org"))


def test_r22_f3_anthropic_provider_passes_timeout_to_sdk(monkeypatch):
    """R22.0 F3: AnthropicProvider must pass `timeout=` to the SDK
    client so request.budget.max_latency_ms is honored. Without it,
    a slow provider could hang the call indefinitely."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-stub-key")
    from waggledance.core.bridge_llm.providers import anthropic as ap_module
    from waggledance.core.bridge_llm.providers.anthropic import AnthropicProvider
    from waggledance.core.bridge_llm.types import LLMRequest, CallBudget

    captured_timeout = []

    class StubAnthropic:
        def __init__(self, timeout=None, **kwargs):
            captured_timeout.append(timeout)
        @property
        def messages(self):
            class _M:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("stub-no-real-call")
            return _M()

    # Patch the lazy-import target so we don't need real anthropic SDK
    monkeypatch.setitem(sys.modules, "anthropic",
                          type(sys)("anthropic_stub"))
    sys.modules["anthropic"].Anthropic = StubAnthropic  # type: ignore[attr-defined]

    p = AnthropicProvider()
    monkeypatch.setattr(p, "is_available", lambda: True)
    request = LLMRequest(
        injection_point="x", prompt="hello",
        budget=CallBudget(max_latency_ms=2500),
    )
    try:
        p.call(request)
    except Exception:
        pass  # stub raises; we only care that the timeout was captured

    assert captured_timeout, "AnthropicProvider did not construct anthropic client"
    timeout_s = captured_timeout[0]
    # 2500 ms → 2.5 s
    assert timeout_s == pytest.approx(2.5, rel=0.01)
