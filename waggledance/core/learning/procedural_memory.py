"""
Procedural Memory — stores proven action chains from gold-grade cases.

Gold-graded case trajectories have their capability chains stored as
"proven procedures" that the system can reuse for similar goals.
Quarantine cases store their chains as "anti-patterns" to avoid.

This is the system's learned "how to do things" memory.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from waggledance.core.domain.autonomy import (
    CaseTrajectory,
    GoalType,
    QualityGrade,
)

log = logging.getLogger("waggledance.learning.procedural_memory")


@dataclass
class Procedure:
    """A proven action chain for a specific goal type."""
    procedure_id: str = field(default_factory=lambda: f"proc_{int(time.time() * 1000)}")
    goal_type: str = ""
    capability_chain: List[str] = field(default_factory=list)
    success_count: int = 0
    fail_count: int = 0
    description: str = ""
    profile: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    is_anti_pattern: bool = False

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.fail_count
        return self.success_count / total if total > 0 else 0.0

    def to_dict(self) -> dict:
        return {
            "procedure_id": self.procedure_id,
            "goal_type": self.goal_type,
            "capability_chain": self.capability_chain,
            "success_count": self.success_count,
            "fail_count": self.fail_count,
            "description": self.description,
            "profile": self.profile,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_anti_pattern": self.is_anti_pattern,
        }


class ProceduralMemory:
    """
    Stores and retrieves proven action chains.

    - Gold cases → proven procedures (success chains)
    - Quarantine cases → anti-patterns (failure chains to avoid)
    """

    def __init__(self, persist_path: Optional[str] = None):
        self._procedures: Dict[str, Procedure] = {}
        self._persist_path = Path(persist_path) if persist_path else None
        if self._persist_path:
            self._load()

    def learn_from_case(self, case: CaseTrajectory) -> Optional[Procedure]:
        """
        Extract and store a procedure from a case trajectory.

        Gold cases become proven procedures.
        Quarantine cases become anti-patterns.
        """
        if case.quality_grade not in (QualityGrade.GOLD, QualityGrade.QUARANTINE):
            return None

        cap_chain = [c.capability_id for c in case.selected_capabilities]
        if not cap_chain:
            return None

        goal_type = case.goal.type.value if case.goal else "unknown"
        chain_key = f"{goal_type}:{','.join(cap_chain)}"
        is_anti = case.quality_grade == QualityGrade.QUARANTINE

        existing = self._procedures.get(chain_key)
        if existing:
            if is_anti:
                existing.fail_count += 1
            else:
                existing.success_count += 1
            existing.updated_at = time.time()
            proc = existing
        else:
            proc = Procedure(
                goal_type=goal_type,
                capability_chain=cap_chain,
                success_count=0 if is_anti else 1,
                fail_count=1 if is_anti else 0,
                description=case.goal.description[:200] if case.goal else "",
                profile=case.profile,
                is_anti_pattern=is_anti,
            )
            self._procedures[chain_key] = proc

        if self._persist_path:
            self._persist()

        log.debug("Procedural memory %s: %s → %s",
                  "anti-pattern" if is_anti else "learned",
                  chain_key, proc.procedure_id)
        return proc

    def get_procedures(
        self,
        goal_type: Optional[str] = None,
        include_anti: bool = False,
    ) -> List[Procedure]:
        """Get proven procedures, optionally filtered by goal type."""
        result = []
        for proc in self._procedures.values():
            if proc.is_anti_pattern and not include_anti:
                continue
            if goal_type and proc.goal_type != goal_type:
                continue
            result.append(proc)
        return sorted(result, key=lambda p: p.success_rate, reverse=True)

    def get_anti_patterns(self, goal_type: Optional[str] = None) -> List[Procedure]:
        """Get known anti-patterns (failure chains to avoid)."""
        result = []
        for proc in self._procedures.values():
            if not proc.is_anti_pattern:
                continue
            if goal_type and proc.goal_type != goal_type:
                continue
            result.append(proc)
        return result

    def best_procedure(self, goal_type: str) -> Optional[Procedure]:
        """Get the best proven procedure for a goal type."""
        procs = self.get_procedures(goal_type=goal_type)
        return procs[0] if procs else None

    def count(self) -> int:
        return len(self._procedures)

    def clear(self):
        self._procedures.clear()
        if self._persist_path:
            self._persist()

    def stats(self) -> dict:
        proven = sum(1 for p in self._procedures.values() if not p.is_anti_pattern)
        anti = sum(1 for p in self._procedures.values() if p.is_anti_pattern)
        return {
            "total": len(self._procedures),
            "proven": proven,
            "anti_patterns": anti,
        }

    # ── Persistence ────────────────────────────────────────

    def _persist(self):
        if not self._persist_path:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.to_dict() for k, v in self._procedures.items()}
        self._persist_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _load(self):
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            for key, pdict in data.items():
                self._procedures[key] = Procedure(
                    procedure_id=pdict.get("procedure_id", key),
                    goal_type=pdict.get("goal_type", ""),
                    capability_chain=pdict.get("capability_chain", []),
                    success_count=pdict.get("success_count", 0),
                    fail_count=pdict.get("fail_count", 0),
                    description=pdict.get("description", ""),
                    profile=pdict.get("profile", ""),
                    created_at=pdict.get("created_at", 0.0),
                    updated_at=pdict.get("updated_at", 0.0),
                    is_anti_pattern=pdict.get("is_anti_pattern", False),
                )
            log.info("Loaded %d procedures from %s",
                     len(self._procedures), self._persist_path)
        except Exception as e:
            log.warning("Failed to load procedural memory: %s", e)
