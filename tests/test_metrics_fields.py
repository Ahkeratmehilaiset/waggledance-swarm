"""Tests for metrics fields: model_used in all log_chat calls, rejection reasons."""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_all_log_chat_have_model_used():
    """Every metrics.log_chat() call in chat_handler.py must include model_used."""
    source = (ROOT / "core" / "chat_handler.py").read_text(encoding="utf-8")

    # Find all log_chat( calls and verify model_used is present
    # Use regex to find each call block
    pattern = re.compile(r'metrics\.log_chat\((.*?)\)', re.DOTALL)
    matches = pattern.findall(source)

    assert len(matches) >= 8, f"Expected >=8 log_chat calls, found {len(matches)}"

    missing = []
    for i, match in enumerate(matches):
        if "model_used" not in match:
            # Find line number for better error reporting
            idx = source.find(match)
            line_no = source[:idx].count("\n") + 1
            missing.append(f"call #{i+1} near line {line_no}")

    assert not missing, f"log_chat calls missing model_used: {missing}"


def test_rejection_reason_in_learning_engine():
    """_process_score() adds rejection_reason to rejected entries."""
    source = (ROOT / "core" / "learning_engine.py").read_text(encoding="utf-8")

    # Check that rejection_reason is added
    assert "rejection_reason" in source, "rejection_reason not found in learning_engine.py"

    # Check auto-generated reasoning categories
    for category in ["very_low_quality", "below_average", "not_curated_quality", "auto_accepted"]:
        assert category in source, f"Missing auto-reason category: {category}"


def test_learning_engine_boilerplate_strip():
    """_process_score() strips boilerplate system messages."""
    source = (ROOT / "core" / "learning_engine.py").read_text(encoding="utf-8")
    assert "OLETUKSET JA KONTEKSTI" in source, "Boilerplate marker check not in learning_engine"
    assert "ASSUMPTIONS AND CONTEXT" in source, "Boilerplate marker check not in learning_engine"


def test_chat_handler_syntax():
    """chat_handler.py parses without syntax errors."""
    source = (ROOT / "core" / "chat_handler.py").read_text(encoding="utf-8")
    ast.parse(source)


def test_learning_engine_syntax():
    """learning_engine.py parses without syntax errors."""
    source = (ROOT / "core" / "learning_engine.py").read_text(encoding="utf-8")
    ast.parse(source)


if __name__ == "__main__":
    tests = [
        test_all_log_chat_have_model_used,
        test_rejection_reason_in_learning_engine,
        test_learning_engine_boilerplate_strip,
        test_chat_handler_syntax,
        test_learning_engine_syntax,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
