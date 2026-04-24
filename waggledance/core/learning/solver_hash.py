"""Canonical solver hash for deduplication.

Two public hash functions:

- `canonical_hash(spec)` — core-semantic hash. Covers formulas, variables,
  and conditions. Insensitive to model_id, description, whitespace, key
  order, and parameter defaults/ranges. Preserved for backward-compat
  with existing callers.

- `solver_hash(spec)` — strict hash added for Phase 8 proposal dedup.
  Extends the core hash with primary output name + unit, domain tags,
  and invariants (validation checks). Matches the x.txt Phase 3 spec:
  "Hash must include formula, inputs, outputs, units, conditions,
  domain tags, invariants."

Supporting public helpers (Phase 3):
- `canonicalize_solver_spec(spec)` — pure projection into a normalized
  dict (useful for debugging and round-tripping).
- `normalize_formula(text)` — whitespace-collapsing helper.
- `normalize_variables(vars)` — stable (name, unit) pair list.

Both hashes use stable JSON serialization so byte-output is portable
across Python versions and platforms.

Usage:
    from waggledance.core.learning.solver_hash import solver_hash
    h = solver_hash(proposal_dict)
    if h in registry:
        return DUPLICATE
    registry.add(h)

Tested via tests/test_solver_hash.py.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


_WS = re.compile(r"\s+")


def _normalize_expr(expr: str) -> str:
    """Collapse whitespace so 'a+b' == 'a + b' == ' a  +  b '."""
    if not expr:
        return ""
    return _WS.sub("", str(expr).strip())


# Public aliases required by x.txt Phase 3.
def normalize_formula(text: str) -> str:
    """Public whitespace-collapsing helper for formula / algorithm strings."""
    return _normalize_expr(text)


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


def normalize_variables(variables: Any) -> list[list[str]]:
    """Public stable `[name, unit]` list form of the variable spec."""
    # Return lists (not tuples) so the result is JSON-serializable.
    return [[n, u] for (n, u) in _normalize_variables(variables)]


def _normalize_outputs(spec: dict) -> list[tuple[str, str]]:
    """Primary output name + unit from `solver_output_schema`, plus any
    formula with a declared output_unit. Order-independent."""
    out: set[tuple[str, str]] = set()
    schema = spec.get("solver_output_schema") or {}
    primary = schema.get("primary_value") or {}
    if primary.get("name"):
        out.add((str(primary["name"]).strip(), str(primary.get("unit") or "").strip()))
    for f in spec.get("formulas", []) or []:
        if not isinstance(f, dict):
            continue
        u = (f.get("output_unit") or "").strip()
        n = (f.get("name") or "").strip()
        if n and u:
            out.add((n, u))
    return sorted(out)


def _normalize_tags(spec: dict) -> list[str]:
    """Domain / category tags contributing to semantic identity."""
    tags: set[str] = set()
    for t in spec.get("tags", []) or []:
        s = str(t).strip().lower()
        if s:
            tags.add(s)
    cell = spec.get("cell_id")
    if cell:
        tags.add(f"cell:{str(cell).strip().lower()}")
    domain = spec.get("domain")
    if domain:
        tags.add(f"domain:{str(domain).strip().lower()}")
    return sorted(tags)


def _normalize_invariants(spec: dict) -> list[str]:
    """Invariants from `validation` list (each entry may have `check` or
    `condition` key) plus any top-level `invariants` list. Whitespace-
    collapsed and order-independent."""
    out: set[str] = set()
    for item in spec.get("validation", []) or []:
        if isinstance(item, dict):
            c = item.get("check") or item.get("condition") or ""
            c = _normalize_expr(c)
            if c:
                out.add(c)
        elif isinstance(item, str):
            c = _normalize_expr(item)
            if c:
                out.add(c)
    for item in spec.get("invariants", []) or []:
        c = _normalize_expr(str(item))
        if c:
            out.add(c)
    return sorted(out)


def _normalize_conditions(conditions: Any) -> list[str]:
    """Conditional guards — any list of expressions that gate applicability."""
    if not conditions:
        return []
    if isinstance(conditions, str):
        conditions = [conditions]
    return sorted(_normalize_expr(c) for c in conditions if c)


def canonical_hash(proposal: dict) -> str:
    """Return a 64-char hex SHA256 of the core structural shape.

    Kept for backward compatibility with existing call sites. Covers
    formulas, variables, and conditions only. For new code prefer
    `solver_hash`, which also covers outputs, tags, and invariants.
    """
    parts = {
        "formulas": _normalize_formulas(proposal.get("formulas", [])),
        "variables": _normalize_variables(proposal.get("variables", {})),
        "conditions": _normalize_conditions(proposal.get("conditions")),
    }
    blob = repr(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def canonicalize_solver_spec(spec: dict) -> dict:
    """Return a normalized projection of the solver spec.

    Deterministic and JSON-serializable: same semantic spec always
    projects to byte-identical JSON regardless of YAML key order,
    whitespace in expressions, or parameter default/range choices.
    Used both for hashing and for human inspection.
    """
    return {
        "formulas": [
            [name, expr] for (name, expr) in _normalize_formulas(spec.get("formulas", []))
        ],
        "variables": [[n, u] for (n, u) in _normalize_variables(spec.get("variables", {}))],
        "outputs": [[n, u] for (n, u) in _normalize_outputs(spec)],
        "conditions": _normalize_conditions(spec.get("conditions")),
        "tags": _normalize_tags(spec),
        "invariants": _normalize_invariants(spec),
    }


def _stable_json(obj) -> str:
    """JSON with sorted keys + compact separators. Byte-stable across
    Python versions and platforms."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def solver_hash(spec: dict) -> str:
    """Phase 8 strict hash.

    Covers: formulas, inputs (variables + units), outputs (name + unit),
    conditions, domain tags, and invariants — i.e. every semantic
    dimension the x.txt Phase 3 spec calls out. Stable JSON
    serialization, so output is portable.
    """
    payload = canonicalize_solver_spec(spec)
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def short_hash(proposal: dict) -> str:
    """12-char prefix of `canonical_hash` — human-loggable.

    Kept for compatibility. For new code use `solver_hash(spec)[:12]`.
    """
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
    def from_axioms_dir(cls, axioms_dir, use_strict: bool = False) -> "HashRegistry":
        """Pre-populate from existing axiom files on disk. Prevents a
        teacher from proposing something already in production.

        If `use_strict=True`, use the Phase 8 `solver_hash` (covers
        outputs/tags/invariants in addition to core formula shape).
        Default stays on `canonical_hash` for backward-compat with
        existing callers.
        """
        import yaml
        from pathlib import Path
        hashfn = solver_hash if use_strict else canonical_hash
        reg = cls()
        for path in Path(axioms_dir).rglob("*.yaml"):
            try:
                with open(path, encoding="utf-8") as f:
                    axiom = yaml.safe_load(f) or {}
                reg.add(hashfn(axiom))
            except Exception:
                pass  # skip unparseable
        return reg
