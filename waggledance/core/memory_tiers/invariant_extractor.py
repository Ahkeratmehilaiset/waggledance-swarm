"""Invariant extractor — Phase 9 §L.

Pulls explicit invariants from a node BEFORE deep tiering /
compaction. Invariants survive demotion even if the body is moved
to glacier; they remain queryable in hot/warm.

CRITICAL OUT-OF-SCOPE: generative compression is Phase 12+. This
module only extracts ALREADY-EXPLICIT invariants from declared
fields (no language-model summarization).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtractedInvariant:
    invariant_id: str
    source_node_id: str
    statement: str
    kind: str           # "schema" | "constraint" | "relation"

    def to_dict(self) -> dict:
        return {
            "invariant_id": self.invariant_id,
            "source_node_id": self.source_node_id,
            "statement": self.statement,
            "kind": self.kind,
        }


def extract_invariants(*,
                            node_id: str,
                            payload: dict
                            ) -> list[ExtractedInvariant]:
    """Look at known explicit fields and extract invariants. Never
    summarizes; never paraphrases."""
    out: list[ExtractedInvariant] = []
    # 1. Explicit invariants[] list (from solver specs / IR objects)
    invariants = payload.get("invariants") or []
    for idx, s in enumerate(invariants):
        if isinstance(s, str) and s.strip():
            out.append(ExtractedInvariant(
                invariant_id=f"{node_id}_inv_{idx:03d}",
                source_node_id=node_id,
                statement=s.strip(),
                kind="constraint",
            ))
    # 2. Schema declarations
    schema = payload.get("schema") or payload.get("schema_version")
    if schema is not None:
        out.append(ExtractedInvariant(
            invariant_id=f"{node_id}_schema",
            source_node_id=node_id,
            statement=f"declares schema_version={schema}",
            kind="schema",
        ))
    # 3. Lineage relations (supports/contradicts/etc)
    for idx, lin in enumerate(payload.get("lineage") or []):
        if isinstance(lin, dict) and "relation" in lin and "target_node_id" in lin:
            out.append(ExtractedInvariant(
                invariant_id=f"{node_id}_rel_{idx:03d}",
                source_node_id=node_id,
                statement=(
                    f"{lin['relation']} {lin['target_node_id']}"
                ),
                kind="relation",
            ))
    return out


@dataclass
class InvariantStore:
    invariants: dict[str, ExtractedInvariant] = field(default_factory=dict)

    def add(self, inv: ExtractedInvariant) -> "InvariantStore":
        self.invariants[inv.invariant_id] = inv
        return self

    def for_node(self, node_id: str) -> list[ExtractedInvariant]:
        return sorted(
            (i for i in self.invariants.values()
              if i.source_node_id == node_id),
            key=lambda i: i.invariant_id,
        )

    def to_dict(self) -> dict:
        return {iid: i.to_dict()
                for iid, i in sorted(self.invariants.items())}
