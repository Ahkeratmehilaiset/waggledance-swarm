"""Learning Engine core module — test suite (~12 tests).

Tests: QualityScore dataclass, AgentPerformance dataclass,
PromptExperiment dataclass, LearningEngine init, submit_for_evaluation,
_parse_score(), get_status() structure, get_leaderboard(),
QUALITY_EVAL_PROMPT format string.
All LLM calls are mocked — no live Ollama needed.
"""
import sys, os, ast, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Helpers ─────────────────────────────────────────────────────────────

def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FakeLLM:
    """Minimal LLM stub."""
    def __init__(self, response_content="PISTEET: 8/10\nPERUSTELU: Hyva vastaus."):
        self._content = response_content
        self.model = "llama3.2:1b"

    async def generate(self, *args, **kwargs):
        from core.llm_provider import LLMResponse
        return LLMResponse(content=self._content)


class _FakeMemory:
    """Minimal memory stub."""
    class _FakeDB:
        async def execute(self, *a, **kw):
            return type("C", (), {"fetchone": staticmethod(lambda: None),
                                   "fetchall": staticmethod(lambda: [])})()
        async def commit(self):
            pass
    def __init__(self):
        self._db = self._FakeDB()

    async def get_recent_memories(self, limit=30):
        return []

    async def store_memory(self, *a, **kw):
        pass


# ── 1. Syntax ────────────────────────────────────────────────────────────

def test_syntax_learning_engine():
    path = os.path.join(os.path.dirname(__file__), "..", "core", "learning_engine.py")
    with open(path, "r", encoding="utf-8") as f:
        ast.parse(f.read())
    print("  [PASS] core/learning_engine.py syntax valid")


# ── 2. Constants / prompts ───────────────────────────────────────────────

def test_quality_eval_prompt_has_placeholders():
    from core.learning_engine import QUALITY_EVAL_PROMPT
    assert "{agent_type}" in QUALITY_EVAL_PROMPT
    assert "{prompt}" in QUALITY_EVAL_PROMPT
    assert "{response}" in QUALITY_EVAL_PROMPT
    print("  [PASS] QUALITY_EVAL_PROMPT has required placeholders")


def test_prompt_evolve_prompt_has_placeholders():
    from core.learning_engine import PROMPT_EVOLVE_PROMPT
    assert "{agent_type}" in PROMPT_EVOLVE_PROMPT
    # avg_score uses format spec: {avg_score:.1f}
    assert "{avg_score" in PROMPT_EVOLVE_PROMPT
    assert "{current_prompt}" in PROMPT_EVOLVE_PROMPT
    print("  [PASS] PROMPT_EVOLVE_PROMPT has required placeholders")


# ── 3. QualityScore dataclass ────────────────────────────────────────────

def test_quality_score_is_good():
    from core.learning_engine import QualityScore
    good = QualityScore(
        agent_id="bee_agent", agent_type="beekeeper",
        score=8.0, reasoning="Accurate",
        prompt_preview="Question", response_preview="Answer"
    )
    assert good.is_good is True
    assert good.is_bad is False
    print("  [PASS] QualityScore.is_good works correctly")


def test_quality_score_is_bad():
    from core.learning_engine import QualityScore
    bad = QualityScore(
        agent_id="bee_agent", agent_type="beekeeper",
        score=3.0, reasoning="Inaccurate",
        prompt_preview="Question", response_preview="Answer"
    )
    assert bad.is_bad is True
    assert bad.is_good is False
    print("  [PASS] QualityScore.is_bad works correctly")


def test_quality_score_borderline():
    from core.learning_engine import QualityScore
    mid = QualityScore(
        agent_id="bee_agent", agent_type="beekeeper",
        score=5.0, reasoning="OK",
        prompt_preview="Q", response_preview="A"
    )
    assert mid.is_good is False
    assert mid.is_bad is False
    print("  [PASS] QualityScore borderline is neither good nor bad")


# ── 4. AgentPerformance dataclass ────────────────────────────────────────

def test_agent_performance_good_rate_zero():
    from core.learning_engine import AgentPerformance
    p = AgentPerformance(agent_id="agent_x", agent_type="beekeeper")
    assert p.good_rate == 0.0
    assert p.needs_help is False  # not enough data
    print("  [PASS] AgentPerformance.good_rate is 0 when no evaluations")


def test_agent_performance_needs_help():
    from core.learning_engine import AgentPerformance
    p = AgentPerformance(agent_id="agent_x", agent_type="beekeeper")
    p.total_evaluated = 10
    p.avg_recent = 3.5
    p.trend = -0.5
    assert p.needs_help is True
    print("  [PASS] AgentPerformance.needs_help triggers correctly")


# ── 5. PromptExperiment dataclass ────────────────────────────────────────

def test_prompt_experiment_has_enough_data():
    from core.learning_engine import PromptExperiment
    exp = PromptExperiment(
        agent_id="agent_x",
        original_prompt="Old prompt",
        evolved_prompt="New prompt",
        reason="Low score",
    )
    assert exp.has_enough_data is False
    exp.original_scores = [7, 8, 7]
    exp.evolved_scores = [8, 9, 8]
    assert exp.has_enough_data is True
    print("  [PASS] PromptExperiment.has_enough_data works correctly")


# ── 6. LearningEngine init ───────────────────────────────────────────────

def test_learning_engine_init():
    from core.learning_engine import LearningEngine
    eng = LearningEngine(
        llm_evaluator=_FakeLLM(),
        memory=_FakeMemory(),
        config={}
    )
    assert eng.running is False
    assert eng._cycle_count == 0
    assert len(eng._eval_queue) == 0
    assert len(eng._performances) == 0
    assert eng.stats["total_evaluated"] == 0
    print("  [PASS] LearningEngine init OK")


def test_learning_engine_config_values():
    from core.learning_engine import LearningEngine
    config = {
        "learning": {
            "eval_queue_size": 50,
            "evolve_interval_min": 60,
            "min_finetune_score": 8.0,
            "auto_evolve": True,
        }
    }
    eng = LearningEngine(llm_evaluator=_FakeLLM(), memory=_FakeMemory(), config=config)
    assert eng.eval_queue_size == 50
    assert eng.evolve_interval == 60
    assert eng.min_score_for_finetune == 8.0
    assert eng.auto_evolve_enabled is True
    print("  [PASS] LearningEngine reads config values correctly")


# ── 7. submit_for_evaluation ─────────────────────────────────────────────

def test_submit_for_evaluation_adds_to_queue():
    from core.learning_engine import LearningEngine
    eng = LearningEngine(llm_evaluator=_FakeLLM(), memory=_FakeMemory(), config={})
    eng.submit_for_evaluation(
        agent_id="bee_agent",
        agent_type="beekeeper",
        system_prompt="You are a beekeeper.",
        prompt="How to treat varroa?",
        response="Apply oxalic acid in autumn.",
    )
    assert len(eng._eval_queue) == 1
    item = eng._eval_queue[0]
    assert item["agent_id"] == "bee_agent"
    assert item["agent_type"] == "beekeeper"
    print("  [PASS] submit_for_evaluation adds item to queue")


def test_submit_for_evaluation_ignores_short_response():
    from core.learning_engine import LearningEngine
    eng = LearningEngine(llm_evaluator=_FakeLLM(), memory=_FakeMemory(), config={})
    eng.submit_for_evaluation(
        agent_id="bee_agent", agent_type="beekeeper",
        system_prompt="...", prompt="Q?", response="OK"  # too short
    )
    assert len(eng._eval_queue) == 0
    print("  [PASS] submit_for_evaluation ignores too-short responses")


# ── 8. _parse_score ──────────────────────────────────────────────────────

def test_parse_score_standard_format():
    from core.learning_engine import LearningEngine
    eng = LearningEngine(llm_evaluator=_FakeLLM(), memory=_FakeMemory(), config={})
    assert eng._parse_score("PISTEET: 8/10\nPERUSTELU: Hyva.") == 8.0
    assert eng._parse_score("7/10") == 7.0
    assert eng._parse_score("9.5/10") == 9.5
    print("  [PASS] _parse_score parses standard X/10 format")


def test_parse_score_clamped():
    from core.learning_engine import LearningEngine
    eng = LearningEngine(llm_evaluator=_FakeLLM(), memory=_FakeMemory(), config={})
    # Values outside 1-10 should be clamped
    assert eng._parse_score("0/10") == 1.0
    assert eng._parse_score("11/10") == 10.0
    print("  [PASS] _parse_score clamps values to 1-10 range")


def test_parse_score_no_match_returns_none():
    from core.learning_engine import LearningEngine
    eng = LearningEngine(llm_evaluator=_FakeLLM(), memory=_FakeMemory(), config={})
    result = eng._parse_score("no score here at all!")
    assert result is None
    print("  [PASS] _parse_score returns None when no score found")


# ── 9. get_status ────────────────────────────────────────────────────────

def test_learning_engine_get_status_structure():
    from core.learning_engine import LearningEngine
    eng = LearningEngine(llm_evaluator=_FakeLLM(), memory=_FakeMemory(), config={})
    status = eng.get_status()
    assert "running" in status
    assert "cycle_count" in status
    assert "queue_size" in status
    assert "auto_evolve" in status
    assert "stats" in status
    assert "agent_performance" in status
    assert "experiments" in status
    assert status["running"] is False
    assert status["queue_size"] == 0
    print("  [PASS] LearningEngine get_status returns all required keys")


# ── 10. get_leaderboard ──────────────────────────────────────────────────

def test_learning_engine_get_leaderboard_empty():
    from core.learning_engine import LearningEngine
    eng = LearningEngine(llm_evaluator=_FakeLLM(), memory=_FakeMemory(), config={})
    board = eng.get_leaderboard()
    assert isinstance(board, list)
    # No agents evaluated -> empty leaderboard (requires >= 3 evaluations)
    assert len(board) == 0
    print("  [PASS] get_leaderboard returns empty list when no evaluations")


# ── Runner ──────────────────────────────────────────────────────────────

ALL_TESTS = [
    test_syntax_learning_engine,
    test_quality_eval_prompt_has_placeholders,
    test_prompt_evolve_prompt_has_placeholders,
    test_quality_score_is_good,
    test_quality_score_is_bad,
    test_quality_score_borderline,
    test_agent_performance_good_rate_zero,
    test_agent_performance_needs_help,
    test_prompt_experiment_has_enough_data,
    test_learning_engine_init,
    test_learning_engine_config_values,
    test_submit_for_evaluation_adds_to_queue,
    test_submit_for_evaluation_ignores_short_response,
    test_parse_score_standard_format,
    test_parse_score_clamped,
    test_parse_score_no_match_returns_none,
    test_learning_engine_get_status_structure,
    test_learning_engine_get_leaderboard_empty,
]


if __name__ == "__main__":
    passed = 0
    failed = 0
    errors = []

    print("\n" + "=" * 60)
    print("core/learning_engine.py -- {0} tests".format(len(ALL_TESTS)))
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
