"""LLM Provider core module — test suite (~10 tests).

Tests: LLMCircuitBreaker states, LLMProvider init/config, LLMResponse
dataclass, generate_structured JSON parsing — all without live Ollama.
"""
import sys, os, ast, asyncio, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Helpers ─────────────────────────────────────────────────────────────

def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── 1. Syntax ────────────────────────────────────────────────────────────

def test_syntax_llm_provider():
    path = os.path.join(os.path.dirname(__file__), "..", "core", "llm_provider.py")
    with open(path, "r", encoding="utf-8") as f:
        ast.parse(f.read())
    print("  [PASS] core/llm_provider.py syntax valid")


# ── 2. LLMResponse dataclass ─────────────────────────────────────────────

def test_llm_response_defaults():
    from core.llm_provider import LLMResponse
    r = LLMResponse(content="hello")
    assert r.content == "hello"
    assert r.model == ""
    assert r.tokens_used == 0
    assert r.error is False
    assert r.raw == {}
    print("  [PASS] LLMResponse defaults OK")


def test_llm_response_error_flag():
    from core.llm_provider import LLMResponse
    r = LLMResponse(content="", error=True)
    assert r.error is True
    assert r.content == ""
    print("  [PASS] LLMResponse error flag OK")


# ── 3. LLMProvider init / config ─────────────────────────────────────────

def test_llm_provider_default_config():
    from core.llm_provider import LLMProvider
    p = LLMProvider()
    assert p.base_url == "http://localhost:11434"
    assert "qwen" in p.model or "llama" in p.model or p.model  # some default model
    assert p.timeout > 0
    assert p.max_retries >= 1
    print("  [PASS] LLMProvider default config OK")


def test_llm_provider_custom_config():
    from core.llm_provider import LLMProvider
    p = LLMProvider({
        "base_url": "http://custom:9999",
        "model": "phi4-mini",
        "timeout": 30,
        "max_retries": 3,
    })
    assert p.base_url == "http://custom:9999"
    assert p.model == "phi4-mini"
    assert p.timeout == 30
    assert p.max_retries == 3
    print("  [PASS] LLMProvider custom config OK")


def test_llm_provider_num_gpu_none():
    from core.llm_provider import LLMProvider
    p = LLMProvider({})
    # num_gpu default should be None (let Ollama decide)
    assert p.num_gpu is None
    print("  [PASS] LLMProvider num_gpu defaults to None")


def test_llm_provider_num_gpu_forced():
    from core.llm_provider import LLMProvider
    p = LLMProvider({"num_gpu": 0})
    assert p.num_gpu == 0
    print("  [PASS] LLMProvider num_gpu=0 forces CPU mode")


def test_llm_provider_has_circuit_breaker():
    from core.llm_provider import LLMProvider, LLMCircuitBreaker
    p = LLMProvider()
    assert isinstance(p.breaker, LLMCircuitBreaker)
    print("  [PASS] LLMProvider has circuit breaker")


# ── 4. LLMCircuitBreaker ─────────────────────────────────────────────────

def test_circuit_breaker_initial_state():
    from core.llm_provider import LLMCircuitBreaker
    cb = LLMCircuitBreaker()
    assert cb.state == LLMCircuitBreaker.CLOSED
    assert cb.allow_request() is True
    assert cb._consecutive_failures == 0
    assert cb._total_trips == 0
    print("  [PASS] CircuitBreaker initial state CLOSED")


def test_circuit_breaker_opens_after_threshold():
    from core.llm_provider import LLMCircuitBreaker
    cb = LLMCircuitBreaker(failure_threshold=3)
    # Two failures — still closed
    cb.record_failure()
    cb.record_failure()
    assert cb.state == LLMCircuitBreaker.CLOSED
    # Third failure — trips open
    cb.record_failure()
    assert cb.state == LLMCircuitBreaker.OPEN
    assert cb._total_trips == 1
    assert cb.allow_request() is False
    print("  [PASS] CircuitBreaker opens after threshold failures")


def test_circuit_breaker_success_resets():
    from core.llm_provider import LLMCircuitBreaker
    cb = LLMCircuitBreaker(failure_threshold=2)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == LLMCircuitBreaker.OPEN
    # Force to half-open to test success reset
    cb.state = LLMCircuitBreaker.HALF_OPEN
    cb.record_success()
    assert cb.state == LLMCircuitBreaker.CLOSED
    assert cb._consecutive_failures == 0
    print("  [PASS] CircuitBreaker success resets to CLOSED")


def test_circuit_breaker_stats():
    from core.llm_provider import LLMCircuitBreaker
    cb = LLMCircuitBreaker()
    stats = cb.stats
    assert "state" in stats
    assert "consecutive_failures" in stats
    assert "total_trips" in stats
    assert "total_blocked" in stats
    assert stats["state"] == LLMCircuitBreaker.CLOSED
    print("  [PASS] CircuitBreaker stats dict OK")


# ── 5. generate_structured JSON parsing (no network) ────────────────────

def test_generate_structured_error_passthrough():
    """When LLM returns error, generate_structured returns error dict."""
    from core.llm_provider import LLMProvider, LLMResponse
    import unittest.mock as mock

    p = LLMProvider()

    async def _fake_generate(*args, **kwargs):
        return LLMResponse(content="", error=True)

    async def _test():
        with mock.patch.object(p, "generate", side_effect=_fake_generate):
            result = await p.generate_structured("test prompt")
            assert "error" in result

    _run(_test())
    print("  [PASS] generate_structured returns error dict on LLM failure")


def test_generate_structured_json_parse():
    """generate_structured parses valid JSON from LLM content."""
    from core.llm_provider import LLMProvider, LLMResponse
    import unittest.mock as mock

    p = LLMProvider()
    fake_json = '{"key": "value", "number": 42}'

    async def _fake_generate(*args, **kwargs):
        return LLMResponse(content=fake_json, error=False)

    async def _test():
        with mock.patch.object(p, "generate", side_effect=_fake_generate):
            result = await p.generate_structured("test prompt")
            assert result.get("key") == "value"
            assert result.get("number") == 42

    _run(_test())
    print("  [PASS] generate_structured parses valid JSON correctly")


def test_generate_structured_json_in_codeblock():
    """generate_structured strips markdown code fences from LLM content."""
    from core.llm_provider import LLMProvider, LLMResponse
    import unittest.mock as mock

    p = LLMProvider()
    fake_content = '```json\n{"answer": "yes"}\n```'

    async def _fake_generate(*args, **kwargs):
        return LLMResponse(content=fake_content, error=False)

    async def _test():
        with mock.patch.object(p, "generate", side_effect=_fake_generate):
            result = await p.generate_structured("test prompt")
            assert result.get("answer") == "yes"

    _run(_test())
    print("  [PASS] generate_structured strips markdown code fences")


# ── Runner ──────────────────────────────────────────────────────────────

ALL_TESTS = [
    test_syntax_llm_provider,
    test_llm_response_defaults,
    test_llm_response_error_flag,
    test_llm_provider_default_config,
    test_llm_provider_custom_config,
    test_llm_provider_num_gpu_none,
    test_llm_provider_num_gpu_forced,
    test_llm_provider_has_circuit_breaker,
    test_circuit_breaker_initial_state,
    test_circuit_breaker_opens_after_threshold,
    test_circuit_breaker_success_resets,
    test_circuit_breaker_stats,
    test_generate_structured_error_passthrough,
    test_generate_structured_json_parse,
    test_generate_structured_json_in_codeblock,
]


if __name__ == "__main__":
    passed = 0
    failed = 0
    errors = []

    print("\n" + "=" * 60)
    print("core/llm_provider.py -- {0} tests".format(len(ALL_TESTS)))
    print("=" * 60 + "\n")

    for test in ALL_TESTS:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            errors.append((test.__name__, str(e)))
            print("  [FAIL] {0}: {1}".format(test.__name__, e))

    print("\n" + "=" * 60)
    print("Result: {0}/{1} passed, {2} failed".format(passed, passed + failed, failed))
    if errors:
        print("\nFailed tests:")
        for name, err in errors:
            print("  - {0}: {1}".format(name, err))
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)
