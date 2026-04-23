"""Canonical solver hash for deduplication.

When the Claude-teacher pipeline proposes new solvers, we hash each proposal
to detect duplicates before running expensive quality-gate checks. Two
solvers with the same set of (formulas, variables, conditions) are
semantically identical and should be rejected without further evaluation.

The hash is deliberately insensitive to:
  - model_id / model_name / description (cosmetic)
  - YAML key order (normalized via sort)
  - whitespace in formula strings
  - range/default value choices (parameterization, not structure)

The hash IS sensitive to:
  - Set of formulas (name + normalized expression)
  - Set of variables (name + unit)
  - Conditional guards (order-independent)

Separate from the cell's registry — this is specifically for
proposal-time dedup during scaling.

Usage:
    from waggledance.core.learning.solver_hash import canonical_hash
    h = canonical_hash(proposal_dict)
    if h in registry.known_hashes:
        return DUPLICATE
    registry.known_hashes.add(h)

Tested via tests/test_solver_hash.py.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any


_WS = re.compile(r"\s+")


def _normalize_expr(expr: str) -> str:
    """Collapse whitespace so 'a+b' == 'a + b' == ' a  +  b '."""
    if not expr:
        return ""
    return _WS.sub("", str(expr).strip())


def _normalize_formulas(formulas: list[Any]) -> list[tuple[str, str]]:
    out = []
    for f in formulas or []:
        if not isinstance(f, dict):
            continue
        name = (f.get("name") or "").strip()
        expr = _normalize_expr(f.get("formula") or f.get("expression") or "")
        if name and expr:
            out.append((name, expr))
    # Order-independent: same set of (name, expr) pairs → same hash
    return sorted(out)


def _normalize_variables(variables: Any) -> list[tuple[str, str]]:
    out = []
    if isinstance(variables, dict):
        for name, spec in variables.items():
            unit = ""
            if isinstance(spec, dict):
                unit = (spec.get("unit") or "").strip()
            out.append((str(name).strip(), unit))
    elif isinstance(variables, list):
        for v in variables:
            if isinstance(v, dict):
                out.append((str(v.get("name", "")).strip(), (v.get("unit") or "").strip()))
    return sorted(out)


def _normalize_conditions(conditions: Any) -> list[str]:
    """Conditional guards — any list of expressions that gate applicability."""
    if not conditions:
        return []
    if isinstance(conditions, str):
        conditions = [conditions]
    return sorted(_normalize_expr(c) for c in conditions if c)


def canonical_hash(proposal: dict) -> str:
    """Return a 64-char hex SHA256 of the proposal's structural shape."""
    parts = {
        "formulas": _normalize_formulas(proposal.get("formulas", [])),
        "variables": _normalize_variables(proposal.get("variables", {})),
        "conditions": _normalize_conditions(proposal.get("conditions")),
    }
    blob = repr(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def short_hash(proposal: dict) -> str:
    """12-char prefix — human-loggable without sacrificing practical uniqueness."""
    return canonical_hash(proposal)[:12]


# ── Registry for in-memory dedup during a session ──────────────────

class HashRegistry:
    """Thread-UNsafe in-memory seen-set for hashes.

    Intended for use within a single teacher session. For persistent dedup
    across sessions, the proposer should also check committed axiom files
    and compute their hashes.
    """

    def __init__(self):
        self._seen: set[str] = set()

    def seen(self, h: str) -> bool:
        return h in self._seen

    def add(self, h: str) -> None:
        self._seen.add(h)

    def __contains__(self, h: str) -> bool:
        return h in self._seen

    def __len__(self) -> int:
        return len(self._seen)

    @classmethod
    def from_axioms_dir(cls, axioms_dir) -> "HashRegistry":
        """Pre-populate from existing axiom files on disk. Prevents Claude
        from proposing something already in production."""
        import yaml
        from pathlib import Path
        reg = cls()
        for path in Path(axioms_dir).rglob("*.yaml"):
            try:
                with open(path, encoding="utf-8") as f:
                    axiom = yaml.safe_load(f) or {}
                reg.add(canonical_hash(axiom))
            except Exception:
                pass  # skip unparseable
        return reg
