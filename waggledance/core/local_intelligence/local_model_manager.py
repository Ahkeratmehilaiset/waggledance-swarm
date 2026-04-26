"""Local model manager — Phase 9 §N.

Manages the registry of local model artifacts (records only — never
loads model weights into memory inside this scaffold). Each record
captures identity (model_id, version), provenance (training corpus
hash, fine-tune job id), and operational status (lifecycle_status:
shadow_only | advisory | retired).

Hard rules:
- NEVER imports torch, transformers, ollama, openai, anthropic.
- NEVER triggers downloads.
- All status transitions are explicit and logged via append-only history.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable, Mapping


_ALLOWED_LIFECYCLE_STATUSES = (
    "shadow_only",
    "advisory",
    "retired",
)

# Note: there is intentionally no "production" or "promoted" status. A
# local model can never auto-graduate beyond advisory inside this
# scaffold; promotion to any further runtime authority MUST go through
# the human-gated promotion ladder defined in Phase 9 §M.


class LocalModelManagerError(ValueError):
    """Raised when a manager operation violates an invariant."""


@dataclass(frozen=True)
class LocalModelRecord:
    model_id: str
    version: str
    base_model_family: str
    training_corpus_hash: str
    fine_tune_job_id: str
    lifecycle_status: str
    advisory_only: bool
    no_runtime_auto_promotion: bool
    no_foundational_mutation: bool

    def __post_init__(self) -> None:
        if not self.model_id:
            raise LocalModelManagerError("model_id required")
        if not self.version:
            raise LocalModelManagerError("version required")
        if self.lifecycle_status not in _ALLOWED_LIFECYCLE_STATUSES:
            raise LocalModelManagerError(
                f"lifecycle_status {self.lifecycle_status!r} not in "
                f"{_ALLOWED_LIFECYCLE_STATUSES}"
            )
        if self.advisory_only is not True:
            raise LocalModelManagerError(
                "advisory_only must be True for any local model record"
            )
        if self.no_runtime_auto_promotion is not True:
            raise LocalModelManagerError(
                "no_runtime_auto_promotion must be True"
            )
        if self.no_foundational_mutation is not True:
            raise LocalModelManagerError(
                "no_foundational_mutation must be True"
            )

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "version": self.version,
            "base_model_family": self.base_model_family,
            "training_corpus_hash": self.training_corpus_hash,
            "fine_tune_job_id": self.fine_tune_job_id,
            "lifecycle_status": self.lifecycle_status,
            "advisory_only": self.advisory_only,
            "no_runtime_auto_promotion": self.no_runtime_auto_promotion,
            "no_foundational_mutation": self.no_foundational_mutation,
        }


def compute_model_record_id(model_id: str, version: str,
                              training_corpus_hash: str) -> str:
    payload = f"{model_id}|{version}|{training_corpus_hash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


@dataclass
class LocalModelManager:
    records: list[LocalModelRecord] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)

    def register(self, record: LocalModelRecord) -> str:
        for r in self.records:
            if r.model_id == record.model_id and r.version == record.version:
                raise LocalModelManagerError(
                    f"duplicate (model_id, version): "
                    f"({record.model_id!r}, {record.version!r})"
                )
        self.records.append(record)
        rid = compute_model_record_id(
            record.model_id, record.version, record.training_corpus_hash,
        )
        self.history.append({
            "event": "register",
            "record_id": rid,
            "model_id": record.model_id,
            "version": record.version,
            "lifecycle_status": record.lifecycle_status,
        })
        return rid

    def transition(self, model_id: str, version: str,
                    new_status: str, rationale: str) -> LocalModelRecord:
        if new_status not in _ALLOWED_LIFECYCLE_STATUSES:
            raise LocalModelManagerError(
                f"unknown lifecycle_status {new_status!r}"
            )
        for i, r in enumerate(self.records):
            if r.model_id == model_id and r.version == version:
                # transitions are append-only via replacement of the
                # frozen record. shadow_only ↔ advisory ↔ retired.
                if r.lifecycle_status == "retired":
                    raise LocalModelManagerError(
                        "retired records cannot transition further"
                    )
                if not rationale:
                    raise LocalModelManagerError(
                        "transition requires non-empty rationale"
                    )
                new = LocalModelRecord(
                    model_id=r.model_id,
                    version=r.version,
                    base_model_family=r.base_model_family,
                    training_corpus_hash=r.training_corpus_hash,
                    fine_tune_job_id=r.fine_tune_job_id,
                    lifecycle_status=new_status,
                    advisory_only=True,
                    no_runtime_auto_promotion=True,
                    no_foundational_mutation=True,
                )
                self.records[i] = new
                self.history.append({
                    "event": "transition",
                    "model_id": model_id,
                    "version": version,
                    "from_status": r.lifecycle_status,
                    "to_status": new_status,
                    "rationale": rationale,
                })
                return new
        raise LocalModelManagerError(
            f"unknown (model_id, version): ({model_id!r}, {version!r})"
        )

    def find(self, model_id: str, version: str) -> LocalModelRecord | None:
        for r in self.records:
            if r.model_id == model_id and r.version == version:
                return r
        return None

    def to_dict(self) -> dict:
        return {
            "records": [r.to_dict() for r in self.records],
            "history": list(self.history),
            "advisory_only_invariant": True,
            "no_runtime_auto_promotion_invariant": True,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)
