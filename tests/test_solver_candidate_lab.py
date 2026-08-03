"""Tests for SolverCandidateLab — P2 of v3.5.0."""

import ast
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
        assert any("Call is not allowlisted: exec()" in e for e in errors)

    def test_rejects_eval(self):
        errors = TemplateCompiler.validate_ast("eval('1+1')")
        assert any("Call is not allowlisted: eval()" in e for e in errors)

    def test_rejects_open(self):
        errors = TemplateCompiler.validate_ast("open('/etc/passwd')")
        assert any("Call is not allowlisted: open()" in e for e in errors)

    def test_rejects_dunder_import(self):
        errors = TemplateCompiler.validate_ast("__import__('os')")
        assert any("Call is not allowlisted: __import__()" in e for e in errors)
        assert any("Dunder name is not allowlisted: __import__" in e for e in errors)

    def test_rejects_getattr(self):
        errors = TemplateCompiler.validate_ast("getattr(obj, 'x')")
        assert any("Call is not allowlisted: getattr()" in e for e in errors)

    def test_rejects_private_attr(self):
        errors = TemplateCompiler.validate_ast("x._secret")
        assert any("Attribute access is not allowlisted: ._secret" in e for e in errors)

    def test_rejects_system_method(self):
        errors = TemplateCompiler.validate_ast("os.system('ls')")
        assert any("Attribute access is not allowlisted: .system" in e for e in errors)
        assert any("Computed call target is not allowlisted: Attribute" in e for e in errors)

    def test_rejects_popen(self):
        errors = TemplateCompiler.validate_ast("os.popen('ls')")
        assert any("Attribute access is not allowlisted: .popen" in e for e in errors)

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

    def test_rejects_classes_and_dunder_functions(self):
        source = "class Foo:\n    def __init__(self):\n        self.x = 1"
        errors = TemplateCompiler.validate_ast(source)
        assert any("Forbidden construct: ClassDef" in e for e in errors)
        assert any("Dunder function name is not allowlisted: __init__" in e for e in errors)
        assert any("Attribute access is not allowlisted: .x" in e for e in errors)

    @pytest.mark.parametrize("call", ["dangerous_side_effect()", "print(1)", "vars()", "input()"])
    def test_rejects_every_unknown_name_call(self, call):
        errors = TemplateCompiler.validate_ast(call)
        assert any("Call is not allowlisted" in error for error in errors)

    def test_rejects_unknown_attribute_call(self):
        errors = TemplateCompiler.validate_ast("client.send_secret()")
        assert any("Attribute access is not allowlisted" in error for error in errors)
        assert any("Computed call target is not allowlisted" in error for error in errors)

    def test_rejects_subscript_call_target(self):
        source = '__builtins__["__import__"]("os").getcwd()'
        errors = TemplateCompiler.validate_ast(source)
        assert any("Computed call target is not allowlisted: Subscript" in e for e in errors)
        assert any("Dunder name is not allowlisted: __builtins__" in e for e in errors)

    def test_rejects_non_name_call_target(self):
        errors = TemplateCompiler.validate_ast("(lambda: 1)()")
        assert any("Computed call target is not allowlisted: Lambda" in e for e in errors)

    def test_rejects_non_string_and_oversized_source(self):
        assert TemplateCompiler.validate_ast(b"x = 1") == ["Source must be an exact str"]
        errors = TemplateCompiler.validate_ast("x" * 65_537)
        assert any("Source exceeds static validation limit" in error for error in errors)


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
        assert "def solve_candidate(inputs: dict)" in template
        assert "return result" in template

    def test_compile_keeps_candidate_metadata_out_of_source(self):
        candidate = self._make_candidate()
        template = TemplateCompiler.compile_template(candidate)
        assert "Auto-generated inert solver candidate template" in template
        assert candidate.domain not in template
        assert candidate.candidate_id not in template
        assert candidate.rationale not in template
        assert candidate.proposed_rules[0] not in template

    def test_compile_multiple_rules_remain_external_data(self):
        rules = ["Rule A", "Rule B", "Rule C"]
        candidate = self._make_candidate(rules=rules)
        template = TemplateCompiler.compile_template(candidate)
        assert all(rule not in template for rule in rules)
        assert candidate.proposed_rules == rules

    def test_compiled_template_passes_ast(self):
        candidate = self._make_candidate()
        template = TemplateCompiler.compile_template(candidate)
        errors = TemplateCompiler.validate_ast(template)
        assert errors == []

    def test_compiled_template_has_exact_inert_ast_shape(self):
        template = TemplateCompiler.compile_template(self._make_candidate())
        tree = ast.parse(template)

        assert len(tree.body) == 2
        assert isinstance(tree.body[0], ast.Expr)
        function = tree.body[1]
        assert isinstance(function, ast.FunctionDef)
        assert function.name == "solve_candidate"
        assert [argument.arg for argument in function.args.args] == ["inputs"]
        assert [type(statement) for statement in function.body] == [
            ast.Expr,
            ast.Assign,
            ast.Return,
        ]
        assert not any(
            isinstance(node, (ast.Call, ast.Attribute, ast.Subscript, ast.ClassDef))
            for node in ast.walk(tree)
        )

    @pytest.mark.parametrize("field", ["candidate_id", "domain", "rationale"])
    def test_compile_rejects_control_characters_in_scalar_metadata(self, field):
        candidate = self._make_candidate()
        setattr(candidate, field, 'safe"""\ndangerous_side_effect()\n"""')

        with pytest.raises(TemplateCompileError, match="control character"):
            TemplateCompiler.compile_template(candidate)

    def test_compile_rejects_rule_newline_injection(self):
        candidate = self._make_candidate(
            rules=["comment\n    dangerous_side_effect()\n    # suffix"]
        )

        with pytest.raises(TemplateCompileError, match="control character"):
            TemplateCompiler.compile_template(candidate)

    def test_code_like_metadata_without_controls_remains_inert_data(self):
        candidate = self._make_candidate()
        candidate.rationale = '__builtins__["__import__"]("os")'

        template = TemplateCompiler.compile_template(candidate)

        assert "__builtins__" not in template
        assert not any(isinstance(node, ast.Call) for node in ast.walk(ast.parse(template)))

    def test_compile_rejects_unbounded_or_wrong_shaped_candidate_data(self):
        candidate = self._make_candidate()
        candidate.rationale = "x" * 4_097
        with pytest.raises(TemplateCompileError, match="rationale exceeds"):
            TemplateCompiler.compile_template(candidate)

        candidate = self._make_candidate()
        candidate.proposed_rules = ["rule"] * 65
        with pytest.raises(TemplateCompileError, match="proposed_rules exceeds"):
            TemplateCompiler.compile_template(candidate)

        candidate = self._make_candidate()
        candidate.proposed_rules = ("rule",)
        with pytest.raises(TemplateCompileError, match="exact list"):
            TemplateCompiler.compile_template(candidate)

        candidate = self._make_candidate()
        candidate.confidence = True
        with pytest.raises(TemplateCompileError, match="exact int or float"):
            TemplateCompiler.compile_template(candidate)

    def test_compile_is_inert_and_does_not_write_runtime_state(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        TemplateCompiler.compile_template(self._make_candidate())

        assert not (tmp_path / "data").exists()


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
            assert "def solve_candidate" in c.compiled_template

    def test_analyze_failures_rejects_intent_source_injection(self):
        intent = (
            "x(inputs: dict) -> dict:\n"
            "    dangerous_side_effect()\n"
            "    # suffix"
        )
        cases = self._make_cases(intent=intent, n=2)

        candidates = SolverCandidateLab().analyze_failures(
            cases,
            min_cluster_size=2,
        )

        assert len(candidates) == 1
        assert candidates[0].state == CandidateState.FAILED_VALIDATION
        assert candidates[0].compiled_template is None
        assert any("control character" in error for error in candidates[0].validation_errors)

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
