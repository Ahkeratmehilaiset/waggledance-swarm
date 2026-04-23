"""Hybrid router — embedding retrieval + question-frame compatibility filter.

Per v3 §1.8 + Phase D decision doc (2026-04-23):
Embedding retrieval finds the relevant solver by vocabulary; the question-
frame parser ensures the question type matches the solver's output mode.
Without this filter, off-domain negative queries score higher than correct
positives because embeddings match on words, not intent.

Integration point per Phase D:

    query → embed → top-k from FAISS cells → for each candidate:
        check question_frame.desired_output vs solver_output_schema.output_mode
        check comparator unit vs solver_output_schema.comparable_fields units
        filter incompatible
    → return best compatible candidate OR None (off-domain)

This is the missing piece between Phase C oracle (55% precision, 0% negative
rejection) and Phase D-1 promotion gate.

Usage:
    from waggledance.core.reasoning.hybrid_router import filter_by_question_frame
    candidates = [...]  # from FAISS retrieval, list of dicts with canonical_solver_id
    solver_specs = {...}  # canonical_solver_id -> solver_output_schema
    frame = question_frame.parse(query)
    filtered = filter_by_question_frame(candidates, solver_specs, frame)
"""
from __future__ import annotations

from typing import Optional

from waggledance.core.reasoning.question_frame import QuestionFrame, Comparator


def _solver_supports_question_type(
    solver_spec: dict,
    desired_output: str,
) -> bool:
    """Does this solver's output schema match the desired output type?

    solver_spec is the solver_output_schema dict from the axiom YAML.
    """
    output_mode = (solver_spec or {}).get("output_mode", "numeric")

    if desired_output == "numeric":
        return output_mode in ("numeric", "trace_only")
    if desired_output == "boolean_comparison":
        # Boolean compare requires a numeric primary value to compare against
        primary = (solver_spec or {}).get("primary_value", {}) or {}
        return output_mode == "numeric" and primary.get("type") == "number"
    if desired_output == "explanation":
        # Numeric solvers can produce explanations of their formulas
        return output_mode in ("numeric", "trace_only")
    if desired_output == "diagnosis":
        # Diagnosis requires solvers explicitly tagged as diagnostic
        return output_mode == "diagnosis"
    if desired_output == "optimization":
        # Optimization needs solvers with optimization mode tag
        return output_mode in ("optimization", "numeric")
    return True  # unknown desired_output → don't filter


def _comparator_unit_compatible(
    solver_spec: dict,
    comparator: Optional[Comparator],
) -> bool:
    """If the question has a unit (e.g. EUR, kWh), does the solver have
    a comparable_fields entry with that unit?
    """
    if not comparator or not comparator.unit:
        return True  # no unit constraint
    fields = (solver_spec or {}).get("comparable_fields", []) or []
    if not fields:
        return False  # solver has no comparable fields → can't do unit-based threshold
    target_unit = comparator.unit.lower()
    return any(
        (f.get("unit", "") or "").lower() == target_unit
        for f in fields
    )


def filter_by_question_frame(
    candidates: list[dict],
    solver_specs: dict[str, dict],
    frame: QuestionFrame,
) -> list[dict]:
    """Filter retrieval candidates by question-frame compatibility.

    Args:
        candidates: list of dicts with at least 'canonical_solver_id' and 'score'
        solver_specs: canonical_solver_id → solver_output_schema dict
        frame: parsed QuestionFrame

    Returns:
        Filtered list (same order, only compatible candidates).
        Empty list means "off-domain — no solver should answer this".
    """
    filtered = []
    for cand in candidates:
        sid = cand.get("canonical_solver_id")
        spec = solver_specs.get(sid, {})
        if not _solver_supports_question_type(spec, frame.desired_output):
            continue
        if not _comparator_unit_compatible(spec, frame.comparator):
            continue
        # Negation handling: if query has negation and refers to scope, the
        # solver compatibility doesn't change at retrieval level — negation is
        # handled in answer composition. We pass through here.
        filtered.append(cand)
    return filtered


def route_with_question_frame(
    query: str,
    embed_retrieval_fn,
    solver_specs: dict[str, dict],
    parse_fn=None,
) -> dict:
    """Top-level router: embed → retrieve → question_frame.parse → filter → choose.

    Args:
        query: raw user query
        embed_retrieval_fn: callable(query) → list of candidate dicts
            (each with canonical_solver_id, score, doc_id, etc.)
        solver_specs: canonical_solver_id → solver_output_schema mapping
        parse_fn: callable(query) → QuestionFrame (defaults to question_frame.parse)

    Returns:
        {
            "chosen_solver": str | None,
            "chosen_score": float | None,
            "frame": QuestionFrame.to_dict(),
            "candidates_before_filter": int,
            "candidates_after_filter": int,
            "rejected_off_domain": bool,
        }
    """
    if parse_fn is None:
        from waggledance.core.reasoning.question_frame import parse as parse_fn  # noqa

    frame = parse_fn(query)
    raw_candidates = embed_retrieval_fn(query)

    filtered = filter_by_question_frame(raw_candidates, solver_specs, frame)

    if not filtered:
        return {
            "chosen_solver": None,
            "chosen_score": None,
            "frame": frame.to_dict(),
            "candidates_before_filter": len(raw_candidates),
            "candidates_after_filter": 0,
            "rejected_off_domain": True,
        }

    top = filtered[0]
    return {
        "chosen_solver": top.get("canonical_solver_id"),
        "chosen_score": top.get("score"),
        "frame": frame.to_dict(),
        "candidates_before_filter": len(raw_candidates),
        "candidates_after_filter": len(filtered),
        "rejected_off_domain": False,
    }
