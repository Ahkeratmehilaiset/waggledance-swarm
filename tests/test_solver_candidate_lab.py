"""Tests for SolverCandidateLab — P2 of v3.5.0."""

import json
from unittest.mock import MagicMock

import pytest

from waggledance.application.services.solver_candidate_lab import (
    CandidateRegistry,
    CandidateState,
    SolverCandidate,
    SolverCandidateLab,
    TemplateCompileError,
    TemplateCompiler,
)


# ── AST Rejection Tests ─────────────────────────────────────────


class TestASTRejection:
    """Verify AST validation rejects forbidden constructs."""

    def test_rejects_import(self):
        errors = TemplateCompiler.validate_ast("import os")
        assert any("Forbidden construct: Import" in e for e in errors)

    def test_rejects_import_from(self):
        errors = TemplateCompiler.validate_ast("from os import path")
        assert any("Forbidden construct: ImportFrom" in e for e in errors)

    def test_rejects_exec(self):
        errors = TemplateCompiler.validate_ast("exec('print(1)')")
        assert any("Forbidden call: exec()" in e for e in errors)

    def test_rejects_eval(self):
        errors = TemplateCompiler.validate_ast("eval('1+1')")
        assert any("Forbidden call: eval()" in e for e in errors)

    def test_rejects_open(self):
        errors = TemplateCompiler.validate_ast("open('/etc/passwd')")
        assert any("Forbidden call: open()" in e for e in errors)

    def test_rejects_dunder_import(self):
        errors = TemplateCompiler.validate_ast("__import__('os')")
        assert any("Forbidden call: __import__()" in e for e in errors)

    def test_rejects_getattr(self):
        errors = TemplateCompiler.validate_ast("getattr(obj, 'x')")
        assert any("Forbidden call: getattr()" in e for e in errors)

    def test_rejects_private_attr(self):
        errors = TemplateCompiler.validate_ast("x._secret")
        assert any("Private attribute access: ._secret" in e for e in errors)

    def test_rejects_system_method(self):
        errors = TemplateCompiler.validate_ast("os.system('ls')")
        assert any("Forbidden method: .system()" in e for e in errors)

    def test_rejects_popen(self):
        errors = TemplateCompiler.validate_ast("os.popen('ls')")
        assert any("Forbidden method: .popen()" in e for e in errors)

    def test_rejects_global(self):
        source = "def f():\n    global x\n    x = 1"
        errors = TemplateCompiler.validate_ast(source)
        assert any("Forbidden construct: Global" in e for e in errors)

    def test_rejects_async(self):
        source = "async def f():\n    pass"
        errors = TemplateCompiler.validate_ast(source)
        assert any("Forbidden construct: AsyncFunctionDef" in e for e in errors)

    def test_rejects_yield(self):
        source = "def f():\n    yield 1"
        errors = TemplateCompiler.validate_ast(source)
        assert any("Forbidden construct: Yield" in e for e in errors)

    def test_rejects_syntax_error(self):
        errors = TemplateCompiler.validate_ast("def f(:\n    pass")
        assert any("Syntax error" in e for e in errors)

    def test_allows_safe_code(self):
        source = "x = min(1, 2)\ny = abs(-3)\nz = len([1,2,3])"
        errors = TemplateCompiler.validate_ast(source)
        assert errors == []

    def test_allows_init_dunder(self):
        source = "class Foo:\n    def __init__(self):\n        self.x = 1"
        errors = TemplateCompiler.validate_ast(source)
        # __init__ is explicitly allowed
        assert not any("Private attribute" in e for e in errors)


# ── Template Compile Tests ───────────────────────────────────────


class TestTemplateCompile:
    """Verify template compilation for valid candidates."""

    def _make_candidate(self, domain="math", rules=None):
        return SolverCandidate(
            candidate_id="cand_test_abc123",
            domain=domain,
            source_cases=["case_1", "case_2"],
            rationale="Test candidate",
            expected_inputs=["query: str"],
            expected_outputs=["answer: str"],
            proposed_rules=rules or ["Handle math queries"],
        )

    def test_compile_valid_candidate(self):
        candidate = self._make_candidate()
        template = TemplateCompiler.compile_template(candidate)
        assert "def solve_math(inputs: dict)" in template
        assert "Rule 1: Handle math queries" in template
        assert "return result" in template

    def test_compile_includes_docstring(self):
        candidate = self._make_candidate()
        template = TemplateCompiler.compile_template(candidate)
        assert "Auto-generated solver template: math" in template
        assert "cand_test_abc123" in template

    def test_compile_multiple_rules(self):
        rules = ["Rule A", "Rule B", "Rule C"]
        candidate = self._make_candidate(rules=rules)
        template = TemplateCompiler.compile_template(candidate)
        assert "Rule 1: Rule A" in template
        assert "Rule 2: Rule B" in template
        assert "Rule 3: Rule C" in template

    def test_compiled_template_passes_ast(self):
        candidate = self._make_candidate()
        template = TemplateCompiler.compile_template(candidate)
        errors = TemplateCompiler.validate_ast(template)
        assert errors == []


# ── Candidate Registry Tests ─────────────────────────────────────


class TestCandidateRegistry:
    """Verify candidate registry lifecycle transitions."""

    def _make_candidate(self, cid="cand_test_1"):
        return SolverCandidate(
            candidate_id=cid,
            domain="math",
            source_cases=["c1"],
            rationale="Test",
            expected_inputs=["q"],
            expected_outputs=["a"],
            proposed_rules=["rule1"],
        )

    def test_add_and_get(self):
        reg = CandidateRegistry()
        c = self._make_candidate()
        reg.add(c)
        assert reg.get("cand_test_1") is c

    def test_get_missing(self):
        reg = CandidateRegistry()
        assert reg.get("nonexistent") is None

    def test_list_all(self):
        reg = CandidateRegistry()
        reg.add(self._make_candidate("c1"))
        reg.add(self._make_candidate("c2"))
        assert len(reg.list_all()) == 2

    def test_list_filtered_by_state(self):
        reg = CandidateRegistry()
        c1 = self._make_candidate("c1")
        c2 = self._make_candidate("c2")
        c2.state = CandidateState.COMPILED
        reg.add(c1)
        reg.add(c2)
        proposed = reg.list_all(state=CandidateState.PROPOSED)
        compiled = reg.list_all(state=CandidateState.COMPILED)
        assert len(proposed) == 1
        assert len(compiled) == 1

    def test_transition_state(self):
        reg = CandidateRegistry()
        c = self._make_candidate()
        reg.add(c)
        assert reg.transition("cand_test_1", CandidateState.COMPILED)
        assert reg.get("cand_test_1").state == CandidateState.COMPILED

    def test_transition_missing_returns_false(self):
        reg = CandidateRegistry()
        assert reg.transition("nonexistent", CandidateState.COMPILED) is False

    def test_full_lifecycle(self):
        reg = CandidateRegistry()
        c = self._make_candidate()
        reg.add(c)
        assert c.state == CandidateState.PROPOSED
        reg.transition(c.candidate_id, CandidateState.COMPILED)
        assert c.state == CandidateState.COMPILED
        reg.transition(c.candidate_id, CandidateState.READY_FOR_CANARY)
        assert c.state == CandidateState.READY_FOR_CANARY
        reg.transition(c.candidate_id, CandidateState.REJECTED)
        assert c.state == CandidateState.REJECTED

    def test_count(self):
        reg = CandidateRegistry()
        assert reg.count() == 0
        reg.add(self._make_candidate("c1"))
        assert reg.count() == 1

    def test_stats(self):
        reg = CandidateRegistry()
        reg.add(self._make_candidate("c1"))
        c2 = self._make_candidate("c2")
        c2.state = CandidateState.COMPILED
        reg.add(c2)
        stats = reg.stats()
        assert stats["total"] == 2
        assert stats["by_state"]["proposed"] == 1
        assert stats["by_state"]["compiled"] == 1

    def test_to_json(self):
        reg = CandidateRegistry()
        reg.add(self._make_candidate("c1"))
        j = reg.to_json()
        data = json.loads(j)
        assert len(data) == 1
        assert data[0]["candidate_id"] == "c1"
        assert data[0]["state"] == "proposed"


# ── SolverCandidateLab Tests ────────────────────────────────────


class TestSolverCandidateLab:
    """Verify lab behavior including LLM-unavailable degradation."""

    def _make_cases(self, intent="math", n=3):
        return [
            {
                "trajectory_id": f"t{i}",
                "intent": intent,
                "data": json.dumps({"query": f"calculate {i}+{i}", "response": str(i * 2)}),
            }
            for i in range(n)
        ]

    def test_analyze_failures_creates_candidates(self):
        lab = SolverCandidateLab()
        cases = self._make_cases(n=3)
        candidates = lab.analyze_failures(cases, min_cluster_size=2)
        assert len(candidates) == 1
        assert candidates[0].domain == "math"
        assert candidates[0].state == CandidateState.COMPILED

    def test_analyze_below_cluster_size_skipped(self):
        lab = SolverCandidateLab()
        cases = self._make_cases(n=1)
        candidates = lab.analyze_failures(cases, min_cluster_size=2)
        assert len(candidates) == 0

    def test_analyze_multiple_intents(self):
        lab = SolverCandidateLab()
        cases = self._make_cases("math", 3) + self._make_cases("chat", 3)
        candidates = lab.analyze_failures(cases, min_cluster_size=2)
        assert len(candidates) == 2
        domains = {c.domain for c in candidates}
        assert domains == {"math", "chat"}

    def test_no_llm_graceful_degradation(self):
        """Lab works without LLM — deterministic analysis only."""
        lab = SolverCandidateLab(llm=None)
        cases = self._make_cases(n=3)
        candidates = lab.analyze_failures(cases)
        assert len(candidates) == 1
        status = lab.status()
        assert status["llm_available"] is False
        assert status["total_analyses"] == 1

    def test_with_mock_llm_available(self):
        """Lab reports LLM as available when provided."""
        mock_llm = MagicMock()
        lab = SolverCandidateLab(llm=mock_llm)
        status = lab.status()
        assert status["llm_available"] is True

    def test_no_route_changes(self):
        """Candidate generation does NOT modify any routing state."""
        lab = SolverCandidateLab()
        cases = self._make_cases(n=5)
        candidates = lab.analyze_failures(cases)
        # Candidates are in registry only — no production routing interaction
        assert all(c.state in (CandidateState.COMPILED, CandidateState.FAILED_VALIDATION) for c in candidates)
        # Registry is isolated
        assert lab.registry.count() == len(candidates)

    def test_deterministic_candidate_ids(self):
        """Same input produces same candidate ID."""
        lab1 = SolverCandidateLab()
        lab2 = SolverCandidateLab()
        cases = self._make_cases(n=3)
        c1 = lab1.analyze_failures(cases)
        c2 = lab2.analyze_failures(cases)
        assert c1[0].candidate_id == c2[0].candidate_id

    def test_candidate_to_dict(self):
        lab = SolverCandidateLab()
        cases = self._make_cases(n=3)
        candidates = lab.analyze_failures(cases)
        d = candidates[0].to_dict()
        assert "candidate_id" in d
        assert "domain" in d
        assert "state" in d
        assert d["state"] in ("proposed", "compiled", "failed_validation")

    def test_compiled_candidate_has_template(self):
        lab = SolverCandidateLab()
        cases = self._make_cases(n=3)
        candidates = lab.analyze_failures(cases)
        compiled = [c for c in candidates if c.state == CandidateState.COMPILED]
        assert len(compiled) > 0
        for c in compiled:
            assert c.compiled_template is not None
            assert "def solve_" in c.compiled_template

    def test_empty_cases_no_candidates(self):
        lab = SolverCandidateLab()
        candidates = lab.analyze_failures([])
        assert len(candidates) == 0

    def test_cases_without_query_skipped(self):
        """Cases with no extractable query produce no candidates."""
        lab = SolverCandidateLab()
        cases = [
            {"trajectory_id": "t1", "intent": "math", "data": "not json"},
            {"trajectory_id": "t2", "intent": "math", "data": "also not json"},
            {"trajectory_id": "t3", "intent": "math", "data": "still not json"},
        ]
        candidates = lab.analyze_failures(cases, min_cluster_size=2)
        assert len(candidates) == 0

    def test_confidence_scales_with_cluster(self):
        lab = SolverCandidateLab()
        small = self._make_cases(n=2)
        large = self._make_cases(n=10)
        c_small = lab.analyze_failures(small, min_cluster_size=2)
        lab2 = SolverCandidateLab()
        c_large = lab2.analyze_failures(large, min_cluster_size=2)
        assert c_large[0].confidence > c_small[0].confidence
        assert c_large[0].confidence <= 0.8  # Capped at 0.8
