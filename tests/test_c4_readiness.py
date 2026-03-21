"""C4: Test startup readiness check."""
import sys, os, ast
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_syntax():
    """hivemind.py parses without errors."""
    with open(os.path.join(os.path.dirname(__file__), "..", "hivemind.py"),
              "r", encoding="utf-8") as f:
        ast.parse(f.read())
    print("  [PASS] hivemind.py syntax valid")


def test_readiness_check_exists():
    """_readiness_check method exists in HiveMind."""
    with open(os.path.join(os.path.dirname(__file__), "..", "hivemind.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    assert "async def _readiness_check(self):" in src
    print("  [PASS] _readiness_check method exists")


def test_readiness_checks_services():
    """Readiness check validates Ollama, models, ChromaDB, directories."""
    with open(os.path.join(os.path.dirname(__file__), "..", "hivemind.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    rc_start = src.index("async def _readiness_check(self):")
    rc_end = src.index("async def start(self):", rc_start)
    rc_body = src[rc_start:rc_end]

    checks = {
        "Ollama API": "api/version" in rc_body,
        "Required models": "api/tags" in rc_body,
        "Embed models": "nomic-embed-text" in rc_body,
        "ChromaDB": "chromadb" in rc_body,
        "Directories": "data" in rc_body and "configs" in rc_body,
    }

    for check, passed in checks.items():
        status = "OK" if passed else "MISSING"
        assert passed, f"Check missing: {check}"

    print(f"  [PASS] all 5 service checks present: {list(checks.keys())}")


def test_readiness_called_in_start():
    """_readiness_check is called at start of start()."""
    with open(os.path.join(os.path.dirname(__file__), "..", "hivemind.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    start_idx = src.index("async def start(self):")
    start_body = src[start_idx:start_idx + 500]
    assert "_readiness_check()" in start_body
    print("  [PASS] _readiness_check called in start()")


def test_graceful_degradation():
    """Issues don't prevent startup — system starts with warnings."""
    with open(os.path.join(os.path.dirname(__file__), "..", "hivemind.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    rc_start = src.index("async def _readiness_check(self):")
    rc_end = src.index("async def start(self):", rc_start)
    rc_body = src[rc_start:rc_end]

    # Should return issues list, not raise exception
    assert "return issues" in rc_body
    assert "degraded functionality" in rc_body
    print("  [PASS] graceful degradation — issues don't block startup")


if __name__ == "__main__":
    print("C4: Readiness check tests")
    print("=" * 50)
    test_syntax()
    test_readiness_check_exists()
    test_readiness_checks_services()
    test_readiness_called_in_start()
    test_graceful_degradation()
    print("=" * 50)
    print("ALL 5 TESTS PASSED")
