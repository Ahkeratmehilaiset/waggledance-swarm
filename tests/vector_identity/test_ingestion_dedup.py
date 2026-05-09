# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.vector_identity.ingestion_dedup.

Iteration N+2 scout pick #2: ingestion_dedup is the 4-level dedup
pipeline that all ingested artifacts pass through (Phase 9 §H,
Prompt_1_Master). Dedup logic failure causes:

- duplicate knowledge ingestion (memory bloat, contradictory
  consensus from echoed entries)
- missed contradiction detection (silent acceptance of an opposing
  claim)
- mis-classified concept siblings (loss of structural lineage)

The Phase 9 ingestion path is content-addressed and has fixture
fallback for tests, so the dedup levels run on every ingest.
This file pins the 4 level functions and the priority-ordered
pipeline so a future refactor cannot silently drop a level.

Pinned invariants:

- `exact_content_match`: returns level "exact_content_hash" when a
  graph node shares the candidate's node_id; "no_match" otherwise.
- `concept_event_sibling`: requires non-empty candidate.tags, same
  kind, same capsule_context, ≥ 2 shared tags. Skips self.
- `contradiction_or_extension`: requires ≥ 1 contradiction tag on
  candidate; same capsule_context as a sibling; non-empty positive
  (non-contradiction) tag overlap.
- `dedup_pipeline` priority order: exact > semantic (callback) >
  concept > contradiction. Each level only runs if all earlier
  levels returned "no_match".
- semantic_dedup_callback returning a non-DedupResult is ignored.
- Final fallback: DedupResult level == "no_match" with rationale
  "no dedup match at any level".
"""
from __future__ import annotations

from waggledance.core.vector_identity.ingestion_dedup import (
    DedupResult,
    concept_event_sibling,
    contradiction_or_extension,
    dedup_pipeline,
    exact_content_match,
)
from waggledance.core.vector_identity.vector_provenance_graph import (
    VectorNode,
)


# --- helpers -------------------------------------------------------

def _node(
    node_id: str,
    *,
    kind: str = "claim",
    capsule_context: str = "capsule:test",
    tags: tuple[str, ...] = (),
    content_sha256: str = "0" * 64,
) -> VectorNode:
    """Build a minimal VectorNode for dedup-level tests. Only the
    fields actually consulted by the dedup module are varied; the
    rest get safe defaults that satisfy VectorNode.__post_init__
    validation."""
    return VectorNode(
        schema_version=1,
        node_id=node_id,
        content_sha256=content_sha256,
        kind=kind,
        anchor_status="candidate",
        capsule_context=capsule_context,
        source="test_fixture",
        source_kind="document",
        ingested_via="copy_mode",
        external_path=None,
        fixture_fallback_used=True,
        ingested_at_tick=0,
        lineage=(),
        tags=tags,
    )


# --- exact_content_match ------------------------------------------

def test_exact_content_match_returns_match_when_node_id_collides():
    cand = _node("nid-1")
    other = _node("nid-1")  # same node_id => exact match
    result = exact_content_match(cand, [other])
    assert isinstance(result, DedupResult)
    assert result.level == "exact_content_hash"
    assert result.matched_node_id == "nid-1"
    assert result.candidate_node_id == "nid-1"
    assert "identical" in result.rationale


def test_exact_content_match_no_match_in_empty_graph():
    cand = _node("nid-1")
    result = exact_content_match(cand, [])
    assert result.level == "no_match"
    assert result.matched_node_id is None
    assert "no exact-content match" in result.rationale


def test_exact_content_match_no_match_when_only_other_ids():
    cand = _node("nid-1")
    others = [_node("nid-2"), _node("nid-3")]
    result = exact_content_match(cand, others)
    assert result.level == "no_match"
    assert result.matched_node_id is None


# --- concept_event_sibling ----------------------------------------

def test_concept_event_sibling_requires_two_shared_tags():
    """Single shared tag is NOT enough — sibling rule requires ≥ 2."""
    cand = _node("c1", tags=("alpha", "beta"))
    other = _node("o1", tags=("alpha", "gamma"))  # only 1 shared
    result = concept_event_sibling(cand, [other])
    assert result.level == "no_match"
    assert result.matched_node_id is None


def test_concept_event_sibling_matches_on_two_shared_tags():
    cand = _node("c1", tags=("alpha", "beta", "gamma"))
    other = _node("o1", tags=("alpha", "beta", "delta"))
    result = concept_event_sibling(cand, [other])
    assert result.level == "concept_event_sibling"
    assert result.matched_node_id == "o1"
    # rationale lists the shared tags sorted
    assert "alpha" in result.rationale
    assert "beta" in result.rationale


def test_concept_event_sibling_returns_no_match_on_empty_candidate_tags():
    cand = _node("c1", tags=())
    other = _node("o1", tags=("alpha", "beta"))
    result = concept_event_sibling(cand, [other])
    assert result.level == "no_match"
    assert "no tags on candidate" in result.rationale


def test_concept_event_sibling_skips_self_by_node_id():
    """A candidate must NOT match itself even if the graph contains
    a node with the same id and tags — that's an exact-content
    situation, not a sibling."""
    cand = _node("nid-self", tags=("alpha", "beta"))
    self_in_graph = _node("nid-self", tags=("alpha", "beta", "gamma"))
    result = concept_event_sibling(cand, [self_in_graph])
    assert result.level == "no_match"


def test_concept_event_sibling_requires_same_kind():
    cand = _node("c1", kind="claim", tags=("alpha", "beta"))
    other = _node("o1", kind="event", tags=("alpha", "beta"))
    result = concept_event_sibling(cand, [other])
    assert result.level == "no_match"


def test_concept_event_sibling_requires_same_capsule_context():
    cand = _node("c1", capsule_context="capsule:A", tags=("alpha", "beta"))
    other = _node("o1", capsule_context="capsule:B",
                       tags=("alpha", "beta"))
    result = concept_event_sibling(cand, [other])
    assert result.level == "no_match"


# --- contradiction_or_extension -----------------------------------

def test_contradiction_or_extension_requires_contradiction_tag():
    """Candidate without any contradiction tag never matches at this
    level."""
    cand = _node("c1", tags=("alpha", "beta"))
    other = _node("o1", tags=("alpha", "beta"))
    result = contradiction_or_extension(cand, [other])
    assert result.level == "no_match"
    assert "no contradiction-tag on candidate" in result.rationale


def test_contradiction_or_extension_matches_on_contradicts_tag():
    """When candidate carries 'contradicts' AND shares any positive
    (non-contradiction) tag with a sibling in the same capsule,
    the level is contradiction_or_extension."""
    cand = _node("c1", tags=("contradicts", "alpha"))
    other = _node("o1", tags=("alpha", "beta"))
    result = contradiction_or_extension(cand, [other])
    assert result.level == "contradiction_or_extension"
    assert result.matched_node_id == "o1"
    assert "alpha" in result.rationale


def test_contradiction_or_extension_matches_on_rejected_by_tag():
    """rejected_by is also a contradiction tag in the default set."""
    cand = _node("c1", tags=("rejected_by", "alpha"))
    other = _node("o1", tags=("alpha", "beta"))
    result = contradiction_or_extension(cand, [other])
    assert result.level == "contradiction_or_extension"


def test_contradiction_or_extension_no_match_when_no_positive_overlap():
    """A contradiction tag alone is not enough — the candidate
    must share at least one positive tag with another node so we
    know what is being contradicted."""
    cand = _node("c1", tags=("contradicts",))
    other = _node("o1", tags=("alpha", "beta"))
    result = contradiction_or_extension(cand, [other])
    assert result.level == "no_match"


def test_contradiction_or_extension_requires_same_capsule_context():
    cand = _node("c1", capsule_context="capsule:A",
                       tags=("contradicts", "alpha"))
    other = _node("o1", capsule_context="capsule:B",
                       tags=("alpha", "beta"))
    result = contradiction_or_extension(cand, [other])
    assert result.level == "no_match"


def test_contradiction_or_extension_supports_custom_tag_set():
    """The contradiction_tags parameter is extension-friendly: a
    custom contradiction vocabulary still works."""
    cand = _node("c1", tags=("disputes", "alpha"))
    other = _node("o1", tags=("alpha", "beta"))
    result = contradiction_or_extension(
        cand, [other], contradiction_tags=("disputes",),
    )
    assert result.level == "contradiction_or_extension"


# --- dedup_pipeline priority order --------------------------------

def test_dedup_pipeline_returns_no_match_on_empty_graph():
    cand = _node("c1", tags=("alpha", "beta"))
    result = dedup_pipeline(cand, [])
    assert result.level == "no_match"
    assert "no dedup match at any level" in result.rationale


def test_dedup_pipeline_exact_match_short_circuits_other_levels():
    """When exact match fires, semantic, concept, and contradiction
    levels MUST NOT run — exact is the highest-priority bucket."""
    cand = _node("nid-X", tags=("contradicts", "alpha", "beta"))
    other = _node("nid-X", tags=("alpha", "beta"))  # exact node_id
    semantic_called = []

    def semantic_cb(c, gn):
        semantic_called.append(c.node_id)
        return DedupResult(
            candidate_node_id=c.node_id, level="semantic_duplicate",
            matched_node_id="any", rationale="should not be reached",
        )

    result = dedup_pipeline(cand, [other], semantic_dedup_callback=semantic_cb)
    assert result.level == "exact_content_hash"
    assert semantic_called == [], "semantic callback ran despite exact match"


def test_dedup_pipeline_semantic_callback_runs_after_exact_no_match():
    """Semantic dedup runs only when exact returned no_match. When
    semantic returns a non-no_match DedupResult, that result wins."""
    cand = _node("c1", tags=("alpha", "beta"))
    other = _node("o1", tags=("foo",))

    def semantic_cb(c, gn):
        return DedupResult(
            candidate_node_id=c.node_id, level="semantic_duplicate",
            matched_node_id="o1", rationale="cosine 0.97",
        )

    result = dedup_pipeline(cand, [other], semantic_dedup_callback=semantic_cb)
    assert result.level == "semantic_duplicate"
    assert result.matched_node_id == "o1"


def test_dedup_pipeline_semantic_callback_no_match_falls_through_to_concept():
    """Semantic returning no_match must let concept-sibling try."""
    cand = _node("c1", tags=("alpha", "beta"))
    other = _node("o1", tags=("alpha", "beta", "gamma"))

    def semantic_cb(c, gn):
        return DedupResult(
            candidate_node_id=c.node_id, level="no_match",
            matched_node_id=None, rationale="below threshold",
        )

    result = dedup_pipeline(cand, [other], semantic_dedup_callback=semantic_cb)
    assert result.level == "concept_event_sibling"


def test_dedup_pipeline_semantic_non_dedupresult_return_ignored():
    """If a semantic callback returns something that isn't a
    DedupResult (e.g. None, a dict, a string), the pipeline must
    treat it as no_match and continue, NOT crash."""
    cand = _node("c1", tags=("alpha", "beta"))
    other = _node("o1", tags=("alpha", "beta", "gamma"))

    def semantic_cb_none(c, gn):
        return None

    def semantic_cb_dict(c, gn):
        return {"level": "fake"}

    r1 = dedup_pipeline(cand, [other], semantic_dedup_callback=semantic_cb_none)
    assert r1.level == "concept_event_sibling"

    r2 = dedup_pipeline(cand, [other], semantic_dedup_callback=semantic_cb_dict)
    assert r2.level == "concept_event_sibling"


def test_dedup_pipeline_concept_wins_over_contradiction_when_both_match():
    """If a candidate would qualify for concept_event_sibling AND
    contradiction_or_extension, concept must win — it's the higher
    priority level. This guards against a contradiction tag silently
    overriding sibling classification."""
    cand = _node("c1", tags=("contradicts", "alpha", "beta"))
    other = _node("o1", tags=("alpha", "beta", "gamma"))
    # Candidate has 2 shared positive tags AND a contradiction tag —
    # both levels would match. concept_event_sibling must win.
    result = dedup_pipeline(cand, [other])
    assert result.level == "concept_event_sibling"


def test_dedup_pipeline_contradiction_runs_when_concept_no_match():
    """If concept didn't match (only 1 shared tag), contradiction
    must still get a chance."""
    cand = _node("c1", tags=("contradicts", "alpha"))
    other = _node("o1", tags=("alpha", "beta"))
    # only 1 shared positive tag => concept no_match
    result = dedup_pipeline(cand, [other])
    assert result.level == "contradiction_or_extension"


def test_dedup_pipeline_no_callback_skips_semantic_silently():
    """A None semantic_dedup_callback must NOT crash; pipeline
    falls through to concept directly."""
    cand = _node("c1", tags=("alpha", "beta"))
    other = _node("o1", tags=("alpha", "beta", "gamma"))
    result = dedup_pipeline(cand, [other], semantic_dedup_callback=None)
    assert result.level == "concept_event_sibling"


# --- DedupResult dataclass contract -------------------------------

def test_dedup_result_is_frozen():
    r = DedupResult(
        candidate_node_id="c", level="no_match",
        matched_node_id=None, rationale="nope",
    )
    try:
        r.level = "exact_content_hash"  # type: ignore[misc]
    except Exception:
        pass
    else:
        raise AssertionError("DedupResult should be frozen")
