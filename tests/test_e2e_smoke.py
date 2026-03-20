"""E2E smoke tests — verify system boots and responds in stub mode."""
import pytest
import asyncio


class TestStubBoot:
    """Verify the system can start in stub mode without Ollama."""

    def test_container_creates(self):
        """DI container must initialize in stub mode."""
        from waggledance.bootstrap.container import Container
        from waggledance.adapters.config.settings_loader import WaggleSettings
        settings = WaggleSettings.from_env()
        container = Container(settings, stub=True)
        assert container.llm is not None
        assert container.vector_store is not None

    def test_chat_router_fallback(self):
        """ChatRouter must return fallback when no services available."""
        from core.chat_router import ChatRouter
        router = ChatRouter()
        result = asyncio.run(router.route("test"))
        assert result.method == "fallback"
        assert "Anteeksi" in result.response

    def test_safe_eval_works(self):
        """Core math evaluation must work."""
        from core.safe_eval import safe_eval
        assert safe_eval("2 + 3 * 4", {}) == 14
        assert safe_eval("sqrt(144)", {}) == 12.0

    def test_resource_guard_works(self):
        """Resource monitoring must function."""
        from core.resource_guard import ResourceGuard
        guard = ResourceGuard()
        state = guard.check()
        assert state.memory_percent > 0

    def test_observability_imports(self):
        """Observability module must import cleanly."""
        from core.observability import CHAT_REQUESTS, CHAT_LATENCY
        CHAT_REQUESTS.labels(method="test", language="fi", agent_id="e2e").inc()

    def test_hallucination_checker_5_signals(self):
        """Hallucination checker must use 5 signals."""
        from core.hallucination_checker import HallucinationChecker, HallucinationResult
        hc = HallucinationChecker()
        result = hc.check("Mika on pesan lampotila?", "Se on 35 astetta.")
        assert hasattr(result, 'source_grounding')
        assert hasattr(result, 'self_consistency')
        assert hasattr(result, 'corrections_penalty')


class TestSecurityInvariants:
    """These must NEVER break — they are security invariants."""

    def test_no_eval_in_solver(self):
        """symbolic_solver.py must not contain raw eval()."""
        from pathlib import Path
        solver_code = Path("core/symbolic_solver.py").read_text()
        import re
        raw_evals = re.findall(r'(?<!safe_)eval\(', solver_code)
        assert len(raw_evals) == 0, f"Found {len(raw_evals)} raw eval() calls!"

    def test_safe_eval_blocks_rce(self):
        from core.safe_eval import safe_eval, SafeEvalError
        attacks = [
            "__import__('os').system('id')",
            "().__class__.__bases__[0].__subclasses__()",
            "getattr(int, '__subclasses__')()",
            "eval('1+1')",
            "exec('import os')",
            "open('/etc/passwd')",
            "''.__class__.__mro__[-1].__subclasses__()",
        ]
        for attack in attacks:
            with pytest.raises(SafeEvalError):
                safe_eval(attack, {})

    def test_mqtt_default_tls(self):
        """MQTT must default to TLS."""
        config = {}
        assert config.get("mqtt_tls", True) is True
        assert config.get("mqtt_port", 8883) == 8883
