"""OpsAgent core module — test suite (~10 tests).

Tests: ModelProfile dataclass, OllamaSnapshot, OpsDecision, OpsAgent init,
thresholds from config, get_status() structure, report_task_result(),
efficiency_score() logic — no background tasks started.
"""
import sys, os, ast
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Helpers ─────────────────────────────────────────────────────────────

class _FakeLLM:
    """Minimal LLM stub with a .model attribute."""
    def __init__(self, model_name="phi4-mini"):
        self.model = model_name


class _FakeThrottle:
    """Minimal throttle stub matching OpsAgent expectations."""
    class _State:
        max_concurrent = 4
        heartbeat_interval = 60.0
        idle_every_n_heartbeat = 10

    def __init__(self):
        self.state = self._State()
        import asyncio
        self._semaphore = asyncio.Semaphore(4)


# ── 1. Syntax ────────────────────────────────────────────────────────────

def test_syntax_ops_agent():
    path = os.path.join(os.path.dirname(__file__), "..", "core", "ops_agent.py")
    with open(path, "r", encoding="utf-8") as f:
        ast.parse(f.read())
    print("  [PASS] core/ops_agent.py syntax valid")


# ── 2. Dataclass smoke tests ─────────────────────────────────────────────

def test_model_profile_init():
    from core.ops_agent import ModelProfile
    p = ModelProfile(model_name="phi4-mini")
    assert p.model_name == "phi4-mini"
    assert p.avg_latency_ms == 0.0
    assert p.error_rate == 0.0
    assert p.total_requests == 0
    assert p.total_errors == 0
    print("  [PASS] ModelProfile init defaults OK")


def test_model_profile_record_success():
    from core.ops_agent import ModelProfile
    p = ModelProfile(model_name="phi4-mini")
    p.record(500.0, success=True)
    assert p.total_requests == 1
    assert p.total_errors == 0
    assert p.avg_latency_ms == 500.0
    print("  [PASS] ModelProfile.record success updates latency")


def test_model_profile_record_failure():
    from core.ops_agent import ModelProfile
    p = ModelProfile(model_name="phi4-mini")
    p.record(30000.0, success=False)
    assert p.total_requests == 1
    assert p.total_errors == 1
    assert p.error_rate > 0
    print("  [PASS] ModelProfile.record failure increments error count")


def test_model_profile_efficiency_score_no_data():
    from core.ops_agent import ModelProfile
    p = ModelProfile(model_name="phi4-mini")
    # No data yet -> efficiency = 0.0
    assert p.efficiency_score() == 0.0
    print("  [PASS] ModelProfile efficiency_score returns 0 with no data")


def test_model_profile_efficiency_score_with_data():
    from core.ops_agent import ModelProfile
    p = ModelProfile(model_name="phi4-mini", quality_score=8.0)
    for _ in range(5):
        p.record(500.0, success=True)
    eff = p.efficiency_score()
    assert 0.0 <= eff <= 1.0
    # High quality (8/10) + fast (500ms) + no errors = high efficiency
    assert eff > 0.5
    print("  [PASS] ModelProfile efficiency_score reasonable with good data")


def test_ops_decision_str():
    from core.ops_agent import OpsDecision
    import time
    d = OpsDecision(
        timestamp=time.monotonic(),
        action="emergency_slowdown",
        reason="High latency",
        old_value=4,
        new_value=1,
        confidence=0.95,
    )
    s = str(d)
    assert "emergency_slowdown" in s
    assert "High latency" in s
    print("  [PASS] OpsDecision __str__ includes action and reason")


# ── 3. OpsAgent init ─────────────────────────────────────────────────────

def test_ops_agent_init_default_config():
    from core.ops_agent import OpsAgent
    throttle = _FakeThrottle()
    llm_chat = _FakeLLM("phi4-mini")
    llm_hb = _FakeLLM("llama3.2:1b")
    ops = OpsAgent(throttle, llm_chat, llm_hb, config={})
    assert ops.running is False
    assert ops._cycle_count == 0
    assert ops._idle_paused is False
    assert "phi4-mini" in ops.model_profiles
    assert "llama3.2:1b" in ops.model_profiles
    print("  [PASS] OpsAgent init creates model profiles for both LLMs")


def test_ops_agent_thresholds_from_config():
    from core.ops_agent import OpsAgent
    config = {
        "ops_agent": {
            "latency_critical_ms": 20000,
            "latency_warning_ms": 10000,
            "error_rate_critical": 0.30,
            "error_rate_warning": 0.15,
            "gpu_memory_critical": 95,
            "queue_critical": 5,
        }
    }
    ops = OpsAgent(_FakeThrottle(), _FakeLLM(), _FakeLLM("llama3.2:1b"), config=config)
    assert ops.thresholds["latency_critical_ms"] == 20000
    assert ops.thresholds["latency_warning_ms"] == 10000
    assert ops.thresholds["error_rate_critical"] == 0.30
    assert ops.thresholds["error_rate_warning"] == 0.15
    assert ops.thresholds["gpu_memory_critical_pct"] == 95
    assert ops.thresholds["queue_depth_critical"] == 5
    print("  [PASS] OpsAgent reads thresholds from config")


# ── 4. get_status() ──────────────────────────────────────────────────────

def test_ops_agent_get_status_structure():
    from core.ops_agent import OpsAgent
    ops = OpsAgent(_FakeThrottle(), _FakeLLM(), _FakeLLM("llama3.2:1b"), config={})
    status = ops.get_status()
    assert "running" in status
    assert "cycle_count" in status
    assert "idle_paused" in status
    assert "monitor_interval_s" in status
    assert "models" in status
    assert "decisions" in status
    assert "ollama" in status
    assert "latency_trend" in status
    assert "overall_error_rate" in status
    assert status["running"] is False
    assert status["cycle_count"] == 0
    print("  [PASS] OpsAgent get_status returns all required keys")


# ── 5. report_task_result ────────────────────────────────────────────────

def test_ops_agent_report_task_result():
    from core.ops_agent import OpsAgent
    ops = OpsAgent(_FakeThrottle(), _FakeLLM("phi4-mini"), _FakeLLM("llama3.2:1b"), config={})
    # Report a successful task for phi4-mini
    ops.report_task_result(latency_ms=800.0, success=True, model_used="phi4-mini")
    profile = ops.model_profiles.get("phi4-mini")
    assert profile is not None
    assert profile.total_requests == 1
    assert profile.total_errors == 0
    assert profile.avg_latency_ms == 800.0
    print("  [PASS] report_task_result updates model profile correctly")


def test_ops_agent_overall_error_rate_no_data():
    from core.ops_agent import OpsAgent
    ops = OpsAgent(_FakeThrottle(), _FakeLLM(), _FakeLLM("llama3.2:1b"), config={})
    # No tasks reported -> error rate = 0
    assert ops._overall_error_rate() == 0.0
    print("  [PASS] _overall_error_rate returns 0 with no data")


# ── Runner ──────────────────────────────────────────────────────────────

ALL_TESTS = [
    test_syntax_ops_agent,
    test_model_profile_init,
    test_model_profile_record_success,
    test_model_profile_record_failure,
    test_model_profile_efficiency_score_no_data,
    test_model_profile_efficiency_score_with_data,
    test_ops_decision_str,
    test_ops_agent_init_default_config,
    test_ops_agent_thresholds_from_config,
    test_ops_agent_get_status_structure,
    test_ops_agent_report_task_result,
    test_ops_agent_overall_error_rate_no_data,
]


if __name__ == "__main__":
    passed = 0
    failed = 0
    errors = []

    print("\n" + "=" * 60)
    print("core/ops_agent.py -- {0} tests".format(len(ALL_TESTS)))
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
