"""Solver Candidate Lab — safe generation of solver candidate artifacts.

Analyzes failure cases, route misses, and LLM-heavy clusters to propose
structured solver candidate specs. Candidates go to an isolated store,
NEVER to production routing.

Candidates are structured specs (not arbitrary Python), compiled via
TemplateCompiler with strict AST validation and allowlisted operations.
"""

import ast
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


class CandidateState(str, Enum):
    PROPOSED = "proposed"
    COMPILED = "compiled"
    FAILED_VALIDATION = "failed_validation"
    READY_FOR_CANARY = "ready_for_canary"
    REJECTED = "rejected"


@dataclass
class SolverCandidate:
    """A structured solver candidate spec."""

    candidate_id: str
    domain: str
    source_cases: List[str]
    rationale: str
    expected_inputs: List[str]
    expected_outputs: List[str]
    proposed_rules: List[str]
    confidence: float = 0.0
    state: CandidateState = CandidateState.PROPOSED
    compiled_template: Optional[str] = None
    validation_errors: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id,
            "domain": self.domain,
            "source_cases": self.source_cases,
            "rationale": self.rationale,
            "expected_inputs": self.expected_inputs,
            "expected_outputs": self.expected_outputs,
            "proposed_rules": self.proposed_rules,
            "confidence": self.confidence,
            "state": self.state.value,
            "compiled_template": self.compiled_template,
            "validation_errors": self.validation_errors,
            "created_at": self.created_at,
        }


# ── Template Compiler ────────────────────────────────────────────


# Strict allowlist for template operations
_ALLOWED_IMPORTS = frozenset()  # No imports allowed in templates
_ALLOWED_BUILTINS = frozenset({
    "abs", "round", "min", "max", "sum", "len", "int", "float", "str",
    "bool", "list", "dict", "tuple", "set", "range", "enumerate", "zip",
    "sorted", "reversed", "map", "filter", "any", "all", "isinstance",
    "True", "False", "None",
})

_FORBIDDEN_AST_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.Global,
    ast.Nonlocal,
    ast.AsyncFunctionDef,
    ast.AsyncFor,
    ast.AsyncWith,
    ast.Await,
    ast.Yield,
    ast.YieldFrom,
)


class TemplateCompileError(Exception):
    """Raised when template compilation fails validation."""
    pass


class TemplateCompiler:
    """Compiles structured candidate specs into bounded deterministic solver templates.

    Rules:
    - No imports outside allowlist (currently: none)
    - No filesystem/network/process side effects
    - AST validation mandatory
    - Only basic math/logic operations
    """

    @staticmethod
    def validate_ast(source: str) -> List[str]:
        """Validate Python source via AST. Returns list of errors."""
        errors = []
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return [f"Syntax error: {e}"]

        for node in ast.walk(tree):
            # Check forbidden node types
            if isinstance(node, _FORBIDDEN_AST_NODES):
                errors.append(f"Forbidden construct: {type(node).__name__} at line {getattr(node, 'lineno', '?')}")

            # Check for attribute access to dangerous names
            if isinstance(node, ast.Attribute):
                attr = node.attr
                if attr.startswith("_") and attr != "__init__":
                    errors.append(f"Private attribute access: .{attr} at line {node.lineno}")

            # Check for dangerous function calls
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    if func.id in ("exec", "eval", "compile", "open", "__import__",
                                   "getattr", "setattr", "delattr", "globals", "locals",
                                   "breakpoint", "exit", "quit"):
                        errors.append(f"Forbidden call: {func.id}() at line {node.lineno}")
                elif isinstance(func, ast.Attribute):
                    if func.attr in ("system", "popen", "exec", "eval", "remove",
                                     "rmdir", "unlink", "write", "read"):
                        errors.append(f"Forbidden method: .{func.attr}() at line {node.lineno}")

        return errors

    @staticmethod
    def compile_template(candidate: SolverCandidate) -> str:
        """Convert a structured candidate spec into a safe solver template.

        Returns the compiled template source as a string.
        Raises TemplateCompileError on validation failure.
        """
        # Build a deterministic function from the rules
        lines = [
            f'"""Auto-generated solver template: {candidate.domain}',
            f'Candidate: {candidate.candidate_id}',
            f'Rationale: {candidate.rationale}',
            '"""',
            '',
            f'def solve_{candidate.domain}(inputs: dict) -> dict:',
            f'    """Solver for {candidate.domain} domain."""',
            '    result = {}',
        ]

        for i, rule in enumerate(candidate.proposed_rules):
            lines.append(f'    # Rule {i+1}: {rule}')

        lines.append('    return result')
        source = '\n'.join(lines)

        # Validate via AST
        errors = TemplateCompiler.validate_ast(source)
        if errors:
            raise TemplateCompileError(f"Template validation failed: {'; '.join(errors)}")

        return source


# ── Candidate Registry ───────────────────────────────────────────


class CandidateRegistry:
    """In-memory registry for solver candidates.

    Persistence is via JSON serialization. Candidates are isolated
    from production routing.
    """

    def __init__(self):
        self._candidates: Dict[str, SolverCandidate] = {}

    def add(self, candidate: SolverCandidate) -> str:
        """Register a new candidate. Returns candidate_id."""
        self._candidates[candidate.candidate_id] = candidate
        return candidate.candidate_id

    def get(self, candidate_id: str) -> Optional[SolverCandidate]:
        return self._candidates.get(candidate_id)

    def list_all(self, state: Optional[CandidateState] = None) -> List[SolverCandidate]:
        if state:
            return [c for c in self._candidates.values() if c.state == state]
        return list(self._candidates.values())

    def transition(self, candidate_id: str, new_state: CandidateState) -> bool:
        c = self._candidates.get(candidate_id)
        if not c:
            return False
        c.state = new_state
        return True

    def count(self) -> int:
        return len(self._candidates)

    def stats(self) -> dict:
        by_state = {}
        for c in self._candidates.values():
            by_state[c.state.value] = by_state.get(c.state.value, 0) + 1
        return {"total": len(self._candidates), "by_state": by_state}

    def to_json(self) -> str:
        return json.dumps([c.to_dict() for c in self._candidates.values()], indent=2)


# ── Solver Candidate Lab ─────────────────────────────────────────


class SolverCandidateLab:
    """Safe solver candidate generation and management.

    Analyzes failure patterns to produce structured candidate specs.
    Never modifies production routing. All output is reviewable artifact only.
    """

    def __init__(self, registry: Optional[CandidateRegistry] = None, llm=None):
        self._registry = registry or CandidateRegistry()
        self._llm = llm  # Optional local LLM for rationale generation
        self._total_analyses = 0

    @property
    def registry(self) -> CandidateRegistry:
        return self._registry

    def status(self) -> dict:
        return {
            "total_analyses": self._total_analyses,
            "llm_available": self._llm is not None,
            "registry": self._registry.stats(),
        }

    def analyze_failures(
        self,
        cases: List[Dict[str, Any]],
        min_cluster_size: int = 2,
    ) -> List[SolverCandidate]:
        """Analyze failure cases and propose solver candidates.

        Groups failures by domain/intent, identifies patterns,
        and creates structured candidate specs.

        Does NOT use LLM by default — uses deterministic pattern detection.
        """
        self._total_analyses += 1
        candidates = []

        # Group by intent/domain
        clusters: Dict[str, List[Dict]] = {}
        for case in cases:
            intent = case.get("intent", "unknown")
            clusters.setdefault(intent, []).append(case)

        for intent, cluster_cases in clusters.items():
            if len(cluster_cases) < min_cluster_size:
                continue

            # Extract common patterns
            queries = []
            for c in cluster_cases:
                data = c.get("data")
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except (json.JSONDecodeError, TypeError):
                        continue
                if isinstance(data, dict):
                    q = data.get("query", "")
                    if q:
                        queries.append(q)

            if not queries:
                continue

            # Create candidate
            cid = self._make_candidate_id(intent, queries)
            candidate = SolverCandidate(
                candidate_id=cid,
                domain=intent,
                source_cases=[c.get("trajectory_id", "") for c in cluster_cases[:10]],
                rationale=f"Cluster of {len(cluster_cases)} LLM-routed {intent} queries that may benefit from a deterministic solver",
                expected_inputs=[f"query: str ({intent} domain)"],
                expected_outputs=["answer: str", "confidence: float"],
                proposed_rules=[
                    f"Handle {intent} queries with deterministic logic",
                    f"Based on {len(queries)} observed query patterns",
                ],
                confidence=min(0.3 + len(cluster_cases) * 0.05, 0.8),
            )

            # Try to compile
            try:
                template = TemplateCompiler.compile_template(candidate)
                candidate.compiled_template = template
                candidate.state = CandidateState.COMPILED
            except TemplateCompileError as e:
                candidate.state = CandidateState.FAILED_VALIDATION
                candidate.validation_errors.append(str(e))

            self._registry.add(candidate)
            candidates.append(candidate)

        return candidates

    def _make_candidate_id(self, intent: str, queries: List[str]) -> str:
        """Generate a deterministic candidate ID from intent + queries."""
        content = f"{intent}:" + "|".join(sorted(queries[:5]))
        h = hashlib.sha256(content.encode()).hexdigest()[:12]
        return f"cand_{intent}_{h}"
