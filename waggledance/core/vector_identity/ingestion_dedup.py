"""Multi-level dedup for ingestion — Phase 9 §H.

Per Prompt_1_Master §H, dedup must be 4-level:
1. exact / content-hash
2. semantic duplicate
3. concept / event sibling
4. contradiction / extension handling

Levels 1 + 4 are deterministic (hash + tag-based); levels 2 + 3 are
deterministic on a fixed embedding-similarity threshold but require
upstream embeddings. For Phase 9 scaffold we ship the structural
interfaces and the deterministic-hash level; semantic dedup will
plug in when an embedding adapter lands.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from . import DEDUP_LEVELS
from .vector_provenance_graph import VectorNode


@dataclass(frozen=True)
class DedupResult:
    candidate_node_id: str
    level: str           # one of DEDUP_LEVELS or "no_match"
    matched_node_id: str | None
    rationale: str


def exact_content_match(candidate: VectorNode,
                              graph_nodes: Iterable[VectorNode]
                              ) -> DedupResult:
    for n in graph_nodes:
        if n.node_id == candidate.node_id:
            return DedupResult(
                candidate_node_id=candidate.node_id,
                level="exact_content_hash",
                matched_node_id=n.node_id,
                rationale=f"identical content_sha256 + kind + capsule",
            )
    return DedupResult(
        candidate_node_id=candidate.node_id, level="no_match",
        matched_node_id=None, rationale="no exact-content match",
    )


def concept_event_sibling(candidate: VectorNode,
                                graph_nodes: Iterable[VectorNode]
                                ) -> DedupResult:
    """Tag-based sibling detection: if the candidate shares ≥ 2 tags
    with another node of the same kind in the same capsule, it's a
    sibling."""
    if not candidate.tags:
        return DedupResult(
            candidate_node_id=candidate.node_id, level="no_match",
            matched_node_id=None, rationale="no tags on candidate",
        )
    cand_tags = set(candidate.tags)
    for n in graph_nodes:
        if n.node_id == candidate.node_id:
            continue
        if n.kind != candidate.kind:
            continue
        if n.capsule_context != candidate.capsule_context:
            continue
        shared = cand_tags & set(n.tags)
        if len(shared) >= 2:
            return DedupResult(
                candidate_node_id=candidate.node_id,
                level="concept_event_sibling",
                matched_node_id=n.node_id,
                rationale=f"shared tags={sorted(shared)}",
            )
    return DedupResult(
        candidate_node_id=candidate.node_id, level="no_match",
        matched_node_id=None, rationale="no concept-sibling match",
    )


def contradiction_or_extension(candidate: VectorNode,
                                       graph_nodes: Iterable[VectorNode],
                                       contradiction_tags: Iterable[str] = ("contradicts",
                                                                              "rejected_by"),
                                       ) -> DedupResult:
    """If the candidate has any contradiction tag and a node with the
    matching positive claim exists in graph, classify as
    contradiction_or_extension."""
    cand_neg = set(candidate.tags) & set(contradiction_tags)
    if not cand_neg:
        return DedupResult(
            candidate_node_id=candidate.node_id, level="no_match",
            matched_node_id=None,
            rationale="no contradiction-tag on candidate",
        )
    for n in graph_nodes:
        if n.capsule_context != candidate.capsule_context:
            continue
        # If sibling tags overlap (excluding the contradiction tags),
        # we have a candidate contradiction relationship
        cand_pos = set(candidate.tags) - set(contradiction_tags)
        sibling_pos = set(n.tags) - set(contradiction_tags)
        if cand_pos and (cand_pos & sibling_pos):
            return DedupResult(
                candidate_node_id=candidate.node_id,
                level="contradiction_or_extension",
                matched_node_id=n.node_id,
                rationale="candidate has contradiction-tag and shares "
                          f"positive tags {sorted(cand_pos & sibling_pos)} "
                          "with existing node",
            )
    return DedupResult(
        candidate_node_id=candidate.node_id, level="no_match",
        matched_node_id=None,
        rationale="no positive-tag overlap for contradiction match",
    )


def dedup_pipeline(candidate: VectorNode,
                       graph_nodes: list[VectorNode],
                       semantic_dedup_callback=None,
                       ) -> DedupResult:
    """Run the 4-level pipeline in priority order. semantic_dedup_callback
    is optional and may be plugged in when embeddings are available."""
    r = exact_content_match(candidate, graph_nodes)
    if r.level != "no_match":
        return r
    if semantic_dedup_callback is not None:
        r2 = semantic_dedup_callback(candidate, graph_nodes)
        if isinstance(r2, DedupResult) and r2.level != "no_match":
            return r2
    r3 = concept_event_sibling(candidate, graph_nodes)
    if r3.level != "no_match":
        return r3
    r4 = contradiction_or_extension(candidate, graph_nodes)
    if r4.level != "no_match":
        return r4
    return DedupResult(
        candidate_node_id=candidate.node_id, level="no_match",
        matched_node_id=None, rationale="no dedup match at any level",
    )
