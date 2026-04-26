"""Vector identity & provenance — Phase 9 §H.

The vector_provenance graph is the substrate for all of WD's local
memory. Every ingested artifact (file, vector, mentor pack, URL,
linked DB) becomes a vector_node with a content hash, lineage edges,
identity-anchor status, and capsule_context.

Crown-jewel area waggledance/core/vector_identity/*
(BUSL Change Date 2030-03-19).
"""

VECTOR_IDENTITY_SCHEMA_VERSION = 1

NODE_KINDS = (
    "document_chunk",
    "ingested_vector",
    "linked_vector",
    "concept",
    "claim",
    "event",
    "mentor_context",
    "code_snippet",
    "table_row",
    "external_url",
)

ANCHOR_STATUSES = (
    "candidate",
    "supportive",
    "foundational",
    "rejected",
    "archived",
)

LINEAGE_RELATIONS = (
    "supports",
    "contradicts",
    "extends",
    "specializes",
    "generalizes",
    "temporal_predecessor",
    "causal_predecessor",
    "translation",
)

# Multi-level dedup buckets per Prompt_1_Master §H
DEDUP_LEVELS = (
    "exact_content_hash",
    "semantic_duplicate",
    "concept_event_sibling",
    "contradiction_or_extension",
)
