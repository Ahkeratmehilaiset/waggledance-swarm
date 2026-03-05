"""B3: Test CircuitBreaker — degraded mode for external services."""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_initial_state():
    """Circuit breaker starts CLOSED (allowing requests)."""
    from core.memory_engine import CircuitBreaker
    cb = CircuitBreaker("test", failure_threshold=3)
    assert cb.state == "closed"
    assert cb.allow_request() is True
    print("  [PASS] initial state is CLOSED")


def test_stays_closed_on_success():
    """Successes keep breaker closed."""
    from core.memory_engine import CircuitBreaker
    cb = CircuitBreaker("test", failure_threshold=3)
    cb.record_success()
    cb.record_success()
    assert cb.state == "closed"
    assert cb.allow_request() is True
    print("  [PASS] stays CLOSED on success")


def test_opens_after_threshold():
    """Breaker opens after N failures."""
    from core.memory_engine import CircuitBreaker
    cb = CircuitBreaker("test", failure_threshold=3, window_s=60)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "closed"  # Not yet
    cb.record_failure()
    assert cb.state == "open"
    assert cb.allow_request() is False
    print("  [PASS] OPENS after 3 failures")


def test_blocks_when_open():
    """Open breaker blocks requests."""
    from core.memory_engine import CircuitBreaker
    cb = CircuitBreaker("test", failure_threshold=2, recovery_s=60)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "open"
    blocked = sum(1 for _ in range(5) if not cb.allow_request())
    assert blocked == 5
    assert cb._total_blocked == 5
    print("  [PASS] blocks all requests when OPEN")


def test_half_open_after_recovery():
    """After recovery time, breaker goes HALF_OPEN."""
    from core.memory_engine import CircuitBreaker
    cb = CircuitBreaker("test", failure_threshold=2, recovery_s=0.1)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "open"

    time.sleep(0.15)
    assert cb.allow_request() is True  # Transitions to HALF_OPEN
    assert cb.state == "half_open"
    print("  [PASS] HALF_OPEN after recovery time")


def test_half_open_success_closes():
    """Success in HALF_OPEN closes the breaker."""
    from core.memory_engine import CircuitBreaker
    cb = CircuitBreaker("test", failure_threshold=2, recovery_s=0.1)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.15)
    cb.allow_request()  # -> HALF_OPEN
    cb.record_success()
    assert cb.state == "closed"
    assert cb.allow_request() is True
    print("  [PASS] HALF_OPEN + success -> CLOSED")


def test_half_open_failure_reopens():
    """Failure in HALF_OPEN reopens the breaker."""
    from core.memory_engine import CircuitBreaker
    cb = CircuitBreaker("test", failure_threshold=2, recovery_s=0.1)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.15)
    cb.allow_request()  # -> HALF_OPEN
    cb.record_failure()
    assert cb.state == "open"
    assert cb._total_trips == 2
    print("  [PASS] HALF_OPEN + failure -> re-OPEN")


def test_stats():
    """Stats returns correct data."""
    from core.memory_engine import CircuitBreaker
    cb = CircuitBreaker("embed_test", failure_threshold=2)
    cb.record_failure()
    cb.record_failure()
    s = cb.stats
    assert s["name"] == "embed_test"
    assert s["state"] == "open"
    assert s["total_trips"] == 1
    print("  [PASS] stats correct")


def test_llm_circuit_breaker():
    """LLM-specific circuit breaker in llm_provider."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))
    from llm_provider import LLMCircuitBreaker

    cb = LLMCircuitBreaker(failure_threshold=3, recovery_s=0.1)
    assert cb.allow_request() is True

    # 3 failures -> open
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "open"
    assert cb.allow_request() is False

    # Recovery
    time.sleep(0.15)
    assert cb.allow_request() is True
    cb.record_success()
    assert cb.state == "closed"
    print("  [PASS] LLMCircuitBreaker works")


def test_window_expiry():
    """Failures outside the time window are ignored."""
    from core.memory_engine import CircuitBreaker
    cb = CircuitBreaker("test", failure_threshold=3, window_s=0.1)
    cb.record_failure()
    cb.record_failure()
    time.sleep(0.15)  # Wait for window to expire
    cb.record_failure()  # Only 1 failure in current window
    assert cb.state == "closed"
    print("  [PASS] failures outside window ignored")


if __name__ == "__main__":
    print("B3: CircuitBreaker tests")
    print("=" * 50)
    test_initial_state()
    test_stays_closed_on_success()
    test_opens_after_threshold()
    test_blocks_when_open()
    test_half_open_after_recovery()
    test_half_open_success_closes()
    test_half_open_failure_reopens()
    test_stats()
    test_llm_circuit_breaker()
    test_window_expiry()
    print("=" * 50)
    print("ALL 10 TESTS PASSED")
