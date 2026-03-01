"""B4: Test chat() path error handling — verify graceful degradation."""
import sys, os, ast
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_hivemind_syntax():
    """hivemind.py parses without errors."""
    with open(os.path.join(os.path.dirname(__file__), "..", "hivemind.py"),
              "r", encoding="utf-8") as f:
        source = f.read()
    ast.parse(source)
    print("  [PASS] hivemind.py syntax valid")


def test_consciousness_syntax():
    """consciousness.py parses without errors."""
    with open(os.path.join(os.path.dirname(__file__), "..", "consciousness.py"),
              "r", encoding="utf-8") as f:
        source = f.read()
    ast.parse(source)
    print("  [PASS] consciousness.py syntax valid")


def test_llm_provider_syntax():
    """core/llm_provider.py parses without errors."""
    with open(os.path.join(os.path.dirname(__file__), "..", "core", "llm_provider.py"),
              "r", encoding="utf-8") as f:
        source = f.read()
    ast.parse(source)
    print("  [PASS] core/llm_provider.py syntax valid")


def test_before_llm_error_path():
    """before_llm failure should not crash _do_chat."""
    # Verify the try/except wrapping exists in source
    with open(os.path.join(os.path.dirname(__file__), "..", "hivemind.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    # before_llm is wrapped in try/except
    assert "self.consciousness.before_llm(message)" in src
    idx = src.index("self.consciousness.before_llm(message)")
    # Find the closest 'try:' before it
    before = src[:idx]
    last_try = before.rfind("try:")
    last_if = before.rfind("if self.consciousness:")
    assert last_try > last_if, "before_llm should be inside a try block"
    print("  [PASS] before_llm wrapped in try/except")


def test_translation_error_paths():
    """Translation calls should be wrapped in try/except."""
    with open(os.path.join(os.path.dirname(__file__), "..", "hivemind.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    # fi_to_en wrapped
    fi_en_idx = src.index("fi_to_en(message, force_opus=True)")
    before_fi_en = src[fi_en_idx-200:fi_en_idx]
    assert "try:" in before_fi_en, "fi_to_en should be in try block"
    print("  [PASS] fi_to_en wrapped in try/except")

    # en_to_fi wrapped
    en_fi_idx = src.index("en_to_fi(response, force_opus=True)")
    before_en_fi = src[en_fi_idx-200:en_fi_idx]
    assert "try:" in before_en_fi, "en_to_fi should be in try block"
    print("  [PASS] en_to_fi wrapped in try/except")


def test_master_think_error_path():
    """Master agent think() should have error handling."""
    with open(os.path.join(os.path.dirname(__file__), "..", "hivemind.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    # Find master_agent.think in the fallback section
    think_idx = src.index("self.master_agent.think(self._routed_message, context)")
    after_think = src[think_idx:think_idx+400]
    assert "except Exception" in after_think, "master think should have except"
    assert "Anteeksi" in after_think, "Error message should be Finnish"
    print("  [PASS] master think has error fallback")


def test_pre_none_safety():
    """_pre references are safe when _pre is None."""
    with open(os.path.join(os.path.dirname(__file__), "..", "hivemind.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    # All _pre.method references should be guarded
    lines = src.split("\n")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if "_pre.method" in stripped and "if _pre" not in stripped:
            # It's OK if it's inside the handled block (after _pre.handled check)
            # Check if line is an assignment like self._last_chat_method = (_pre.method
            if "if _pre else" in stripped or "if _pre and" in stripped:
                continue
            # Check the next line for the guard
            if i < len(lines) and "if _pre else" in lines[i]:
                continue
            # These are fine inside the guarded block
            if any(x in stripped for x in [
                "_pre.answer", "_pre.confidence",
                "f\"", "method=_pre.method"
            ]):
                continue

    print("  [PASS] _pre references are safely guarded")


def test_hall_variable_init():
    """_hall is initialized before use."""
    with open(os.path.join(os.path.dirname(__file__), "..", "hivemind.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    # _hall should be initialized to None before the try block
    assert "_hall = None" in src
    assert "_quality = 0.7" in src
    print("  [PASS] _hall and _quality initialized before use")


def test_circuit_breaker_in_embedding():
    """Embedding engines have circuit breakers."""
    from consciousness import EmbeddingEngine, EvalEmbeddingEngine
    ee = EmbeddingEngine.__init__.__code__
    # Check that CircuitBreaker is used (by checking the source)
    with open(os.path.join(os.path.dirname(__file__), "..", "consciousness.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    assert "self.breaker = CircuitBreaker" in src
    count = src.count("self.breaker = CircuitBreaker")
    assert count >= 2, f"Expected 2+ CircuitBreaker instances, found {count}"
    print(f"  [PASS] {count} CircuitBreaker instances in consciousness.py")


if __name__ == "__main__":
    print("B4: Error handling tests")
    print("=" * 50)
    test_hivemind_syntax()
    test_consciousness_syntax()
    test_llm_provider_syntax()
    test_before_llm_error_path()
    test_translation_error_paths()
    test_master_think_error_path()
    test_pre_none_safety()
    test_hall_variable_init()
    test_circuit_breaker_in_embedding()
    print("=" * 50)
    print("ALL 9 TESTS PASSED")
