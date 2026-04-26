"""Knowledge extractor — Phase 9 §J.

Pulls structured fragments from a normalized provider response:
- candidate facts (kind=observation/report)
- candidate solver specs
- candidate lessons / design notes

Pure functions; never call providers; deterministic given fixed input.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..provider_plane.response_normalizer import ProviderResponse


@dataclass(frozen=True)
class ExtractedFact:
    extracted_id: str
    claim: str
    confidence: float
    source_provider: str

    def to_dict(self) -> dict:
        return {
            "extracted_id": self.extracted_id, "claim": self.claim,
            "confidence": self.confidence,
            "source_provider": self.source_provider,
        }


@dataclass(frozen=True)
class ExtractedSolverSpec:
    extracted_id: str
    solver_family: str
    spec: dict
    source_provider: str

    def to_dict(self) -> dict:
        return {
            "extracted_id": self.extracted_id,
            "solver_family": self.solver_family,
            "spec": dict(self.spec),
            "source_provider": self.source_provider,
        }


@dataclass(frozen=True)
class ExtractedLesson:
    extracted_id: str
    lesson_kind: str   # design_note | pattern | constraint | etc
    content: str
    source_provider: str

    def to_dict(self) -> dict:
        return {
            "extracted_id": self.extracted_id,
            "lesson_kind": self.lesson_kind,
            "content": self.content,
            "source_provider": self.source_provider,
        }


def _ext_id(provider: str, claim: str, prefix: str = "ex") -> str:
    canonical = (provider + "|" + claim[:200]).encode("utf-8")
    return f"{prefix}_" + hashlib.sha256(canonical).hexdigest()[:10]


def extract(response: ProviderResponse) -> dict:
    """Returns {facts: [...], solver_specs: [...], lessons: [...]}.

    Raw payload schema is provider-specific; we look for the canonical
    sections {extracted_facts, extracted_solver_specs, extracted_lessons}
    if the provider returned structured output."""
    raw = response.raw_payload or {}
    provider = response.provider_used

    facts: list[ExtractedFact] = []
    for f in raw.get("extracted_facts") or []:
        if not isinstance(f, dict):
            continue
        claim = str(f.get("claim", ""))
        if not claim:
            continue
        facts.append(ExtractedFact(
            extracted_id=_ext_id(provider, claim, "fact"),
            claim=claim,
            confidence=float(f.get("confidence") or 0.5),
            source_provider=provider,
        ))

    solvers: list[ExtractedSolverSpec] = []
    for s in raw.get("extracted_solver_specs") or []:
        if not isinstance(s, dict):
            continue
        family = str(s.get("solver_family") or "unknown")
        solvers.append(ExtractedSolverSpec(
            extracted_id=_ext_id(provider,
                                      family + str(s.get("spec", "")),
                                      "solver"),
            solver_family=family,
            spec=dict(s.get("spec") or {}),
            source_provider=provider,
        ))

    lessons: list[ExtractedLesson] = []
    for l in raw.get("extracted_lessons") or []:
        if not isinstance(l, dict):
            continue
        content = str(l.get("content", ""))
        if not content:
            continue
        lessons.append(ExtractedLesson(
            extracted_id=_ext_id(provider, content[:200], "lesson"),
            lesson_kind=str(l.get("lesson_kind") or "design_note"),
            content=content,
            source_provider=provider,
        ))

    return {
        "facts": facts,
        "solver_specs": solvers,
        "lessons": lessons,
    }
