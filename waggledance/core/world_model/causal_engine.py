# SPDX-License-Identifier: BUSL-1.1
"""Causal engine — Phase 9 §I.

Represents and queries causal relations between external facts.
Strictly representational; never proposes interventions, never
performs counterfactual execution.
"""
from __future__ import annotations

from typing import Iterable

from .world_model_snapshot import CausalRelation, ExternalFact


def build_relations(*,
                          known_pairs: Iterable[tuple[str, str, float]],
                          ) -> list[CausalRelation]:
    """known_pairs: iterable of (cause_fact_id, effect_fact_id,
    strength) tuples. Returns sorted CausalRelation list."""
    out: list[CausalRelation] = []
    seen: set[tuple[str, str]] = set()
    for cause, effect, strength in known_pairs:
        if cause == effect:
            continue
        key = (cause, effect)
        if key in seen:
            continue
        seen.add(key)
        out.append(CausalRelation(
            cause_fact_id=cause, effect_fact_id=effect,
            strength=float(strength),
        ))
    out.sort(key=lambda r: (r.cause_fact_id, r.effect_fact_id))
    return out


def ancestors(fact_id: str,
                relations: Iterable[CausalRelation],
                ) -> set[str]:
    """All facts that causally lead to fact_id (transitive closure)."""
    rels = list(relations)
    by_effect: dict[str, list[str]] = {}
    for r in rels:
        by_effect.setdefault(r.effect_fact_id, []).append(r.cause_fact_id)
    seen: set[str] = set()
    stack = list(by_effect.get(fact_id, []))
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(by_effect.get(node, []))
    return seen


def descendants(fact_id: str,
                  relations: Iterable[CausalRelation],
                  ) -> set[str]:
    rels = list(relations)
    by_cause: dict[str, list[str]] = {}
    for r in rels:
        by_cause.setdefault(r.cause_fact_id, []).append(r.effect_fact_id)
    seen: set[str] = set()
    stack = list(by_cause.get(fact_id, []))
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(by_cause.get(node, []))
    return seen


def detect_cycles(relations: Iterable[CausalRelation]) -> list[tuple[str, ...]]:
    """Return any directed cycles. Causal graphs with cycles indicate
    a representational error and should be flagged."""
    rels = list(relations)
    by_cause: dict[str, list[str]] = {}
    for r in rels:
        by_cause.setdefault(r.cause_fact_id, []).append(r.effect_fact_id)
    cycles: list[tuple[str, ...]] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def dfs(node: str) -> None:
        if node in visiting:
            i = stack.index(node)
            cycles.append(tuple(stack[i:] + [node]))
            return
        if node in visited:
            return
        visiting.add(node)
        stack.append(node)
        for nxt in by_cause.get(node, []):
            dfs(nxt)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    nodes = set(by_cause.keys()) | {
        e for effs in by_cause.values() for e in effs
    }
    for n in sorted(nodes):
        dfs(n)
    return cycles
