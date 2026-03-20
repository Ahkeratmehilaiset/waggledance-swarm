"""C5: Test structured JSON logging — learning_metrics.jsonl."""
import sys, os, json, ast, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from _hive_source import read_hive_source


def test_syntax():
    """hivemind.py parses without errors."""
    with open(os.path.join(os.path.dirname(__file__), "..", "hivemind.py"),
              "r", encoding="utf-8") as f:
        ast.parse(f.read())
    print("  [PASS] hivemind.py syntax valid")


def test_structured_logger_class_exists():
    """StructuredLogger class exists and can be imported."""
    from hivemind import StructuredLogger
    sl = StructuredLogger(path=os.path.join(tempfile.gettempdir(), "test_metrics.jsonl"))
    assert hasattr(sl, "log_chat")
    assert hasattr(sl, "log_learning")
    print("  [PASS] StructuredLogger class exists with log_chat + log_learning")


def test_log_chat_writes_json_line():
    """log_chat writes valid JSON line with required fields."""
    from hivemind import StructuredLogger
    path = os.path.join(tempfile.gettempdir(), f"test_chat_{time.time()}.jsonl")
    sl = StructuredLogger(path=path)

    sl.log_chat(
        query="mikä on varroa",
        method="hot_cache",
        agent_id="consciousness",
        response_time_ms=5.2,
        confidence=0.95,
        cache_hit=True,
        route="hot_cache",
        language="fi",
    )

    with open(path, "r", encoding="utf-8") as f:
        line = f.readline().strip()
    record = json.loads(line)

    required_fields = [
        "ts", "query_hash", "method", "agent_id", "model_used",
        "route", "response_time_ms", "confidence", "was_hallucination",
        "cache_hit", "language", "translated",
    ]
    for field in required_fields:
        assert field in record, f"Missing field: {field}"

    assert record["method"] == "hot_cache"
    assert record["cache_hit"] is True
    assert record["confidence"] == 0.95
    assert record["response_time_ms"] == 5.2
    assert record["language"] == "fi"
    assert len(record["query_hash"]) == 12  # md5[:12]
    assert record["ts"].endswith("Z")

    os.remove(path)
    print("  [PASS] log_chat writes valid JSON with all required fields")


def test_log_learning_writes_json_line():
    """log_learning writes valid JSON line for background events."""
    from hivemind import StructuredLogger
    path = os.path.join(tempfile.gettempdir(), f"test_learn_{time.time()}.jsonl")
    sl = StructuredLogger(path=path)

    sl.log_learning(
        event="batch_flush",
        count=15,
        duration_ms=342.5,
        source="yaml_scan",
    )

    with open(path, "r", encoding="utf-8") as f:
        line = f.readline().strip()
    record = json.loads(line)

    assert record["event"] == "batch_flush"
    assert record["count"] == 15
    assert record["duration_ms"] == 342.5
    assert record["source"] == "yaml_scan"
    assert record["ts"].endswith("Z")

    os.remove(path)
    print("  [PASS] log_learning writes valid JSON for background events")


def test_multiple_lines_appended():
    """Multiple log calls append separate lines."""
    from hivemind import StructuredLogger
    path = os.path.join(tempfile.gettempdir(), f"test_multi_{time.time()}.jsonl")
    sl = StructuredLogger(path=path)

    for i in range(5):
        sl.log_chat(
            query=f"kysymys {i}",
            method=f"method_{i}",
            response_time_ms=float(i * 10),
        )

    with open(path, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    assert len(lines) == 5, f"Expected 5 lines, got {len(lines)}"

    # Each line is valid JSON
    for i, line in enumerate(lines):
        record = json.loads(line)
        assert record["method"] == f"method_{i}"

    os.remove(path)
    print("  [PASS] 5 log lines appended correctly")


def test_log_chat_in_source_at_return_points():
    """log_chat calls exist at all chat handler return points."""
    src = read_hive_source()

    # Timer at start
    assert "_chat_t0 = time.perf_counter()" in src, \
        "Timer should be set at chat handler start"

    # Count return points with metrics logging
    metrics_log_count = src.count("metrics.log_chat(")
    assert metrics_log_count >= 5, \
        f"Expected >= 5 log_chat calls (one per return path), found {metrics_log_count}"

    # Each log_chat should include response_time_ms calculation
    # v3.3: sub-modules use chat_t0, handler uses _chat_t0
    perf_calc_count = (src.count("perf_counter() - _chat_t0")
                       + src.count("perf_counter() - chat_t0"))
    assert perf_calc_count >= 5, \
        f"Expected >= 5 response_time calculations, found {perf_calc_count}"

    print(f"  [PASS] {metrics_log_count} log_chat calls at return points with timing")


def test_hivemind_has_metrics_attribute():
    """HiveMind.__init__ creates self.metrics = StructuredLogger()."""
    with open(os.path.join(os.path.dirname(__file__), "..", "hivemind.py"),
              "r", encoding="utf-8") as f:
        src = f.read()

    assert "self.metrics = StructuredLogger()" in src, \
        "HiveMind should have self.metrics = StructuredLogger()"
    print("  [PASS] HiveMind has self.metrics attribute")


def test_extra_fields_merged():
    """Extra dict fields are merged into the log record."""
    from hivemind import StructuredLogger
    path = os.path.join(tempfile.gettempdir(), f"test_extra_{time.time()}.jsonl")
    sl = StructuredLogger(path=path)

    sl.log_chat(
        query="test",
        method="test",
        extra={"custom_field": "custom_value", "score": 42},
    )

    with open(path, "r", encoding="utf-8") as f:
        record = json.loads(f.readline())

    assert record["custom_field"] == "custom_value"
    assert record["score"] == 42

    os.remove(path)
    print("  [PASS] extra fields merged into log record")


if __name__ == "__main__":
    print("C5: Structured logging tests")
    print("=" * 50)
    test_syntax()
    test_structured_logger_class_exists()
    test_log_chat_writes_json_line()
    test_log_learning_writes_json_line()
    test_multiple_lines_appended()
    test_log_chat_in_source_at_return_points()
    test_hivemind_has_metrics_attribute()
    test_extra_fields_merged()
    print("=" * 50)
    print("ALL 8 TESTS PASSED")
