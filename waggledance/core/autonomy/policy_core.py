"""Autonomy policy core — Phase 9 §F.policy_core.

Bounded adaptive policy that LAYERS ON TOP of the immutable
constitution. The policy may:
- tighten existing hard rules (narrow_cap on budgets, raise risk
  thresholds, shrink lane allow-lists)
- add advisory checks
- add capsule-specific guards

The policy MUST NOT:
- relax / remove / mutate any hard rule from the constitution
- lower a hard rule's severity
- mutate the constitution file itself
- be self-modifiable from inside tick()

Crown-jewel area waggledance/core/autonomy/*
(BUSL Change Date 2030-03-19).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

POLICY_SCHEMA_VERSION = 1

# Allowed verbs (must mirror constitution.yaml adaptive_policy.allowed_to)
ADAPTIVE_VERBS_ALLOWED = ("tighten", "narrow_scope", "add_advisory_check")
ADAPTIVE_VERBS_FORBIDDEN = (
    "relax_hard_rule",
    "remove_hard_rule",
    "mutate_constitution",
    "lower_severity",
)


@dataclass(frozen=True)
class PolicyRule:
    """One adaptive policy rule layered on top of the constitution."""
    rule_id: str
    refines_hard_rule_id: str
    verb: str            # one of ADAPTIVE_VERBS_ALLOWED
    statement: str
    source: str          # "human", "proposal:<id>", "session_d:<id>", etc.
    reversible: bool
    capsule_scope: str | None = None   # None = global; else capsule_id

    def to_dict(self) -> dict:
        d = {
            "rule_id": self.rule_id,
            "refines_hard_rule_id": self.refines_hard_rule_id,
            "verb": self.verb,
            "statement": self.statement,
            "source": self.source,
            "reversible": self.reversible,
        }
        if self.capsule_scope is not None:
            d["capsule_scope"] = self.capsule_scope
        return d


@dataclass(frozen=True)
class HardRule:
    """One hard rule from the immutable constitution."""
    id: str
    statement: str
    severity: str

    def to_dict(self) -> dict:
        return {"id": self.id, "statement": self.statement,
                "severity": self.severity}


@dataclass(frozen=True)
class PolicyEvaluation:
    """Result of evaluating a candidate action against the policy."""
    action_id: str
    allowed: bool
    blocking_rule_ids: tuple[str, ...]
    advisory_rule_ids: tuple[str, ...]
    reasons: tuple[str, ...]


# ── Identity ──────────────────────────────────────────────────────

def compute_rule_id(*, refines: str, verb: str, statement: str,
                          source: str, capsule_scope: str | None) -> str:
    canonical = json.dumps({
        "refines": refines, "verb": verb,
        "statement": statement, "source": source,
        "capsule_scope": capsule_scope,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


# ── Construction with strict validation ──────────────────────────-

class PolicyValidationError(ValueError):
    """Raised when a proposed PolicyRule attempts a forbidden action."""


def make_policy_rule(*, refines_hard_rule_id: str, verb: str,
                          statement: str, source: str,
                          reversible: bool = True,
                          capsule_scope: str | None = None,
                          ) -> PolicyRule:
    """Validate and build a PolicyRule. Refuses forbidden verbs and
    empty refines_hard_rule_id (every adaptive rule must point at a
    hard rule it tightens)."""
    if verb in ADAPTIVE_VERBS_FORBIDDEN:
        raise PolicyValidationError(
            f"verb {verb!r} is forbidden by the constitution; "
            f"adaptive policy may never relax/remove/mutate hard rules"
        )
    if verb not in ADAPTIVE_VERBS_ALLOWED:
        raise PolicyValidationError(
            f"unknown verb {verb!r}; allowed: {ADAPTIVE_VERBS_ALLOWED}"
        )
    if not refines_hard_rule_id:
        raise PolicyValidationError(
            "every adaptive rule must reference a hard_rule_id it refines"
        )
    rid = compute_rule_id(refines=refines_hard_rule_id, verb=verb,
                              statement=statement, source=source,
                              capsule_scope=capsule_scope)
    return PolicyRule(
        rule_id=rid,
        refines_hard_rule_id=refines_hard_rule_id,
        verb=verb,
        statement=statement,
        source=source,
        reversible=reversible,
        capsule_scope=capsule_scope,
    )


# ── Constitution loader ──────────────────────────────────────────-

def load_hard_rules(constitution_path: Path | str) -> tuple[HardRule, ...]:
    """Best-effort YAML-like parser; the constitution file is shipped
    in a stable shape so this avoids a hard dep on PyYAML."""
    p = Path(constitution_path)
    if not p.exists():
        raise FileNotFoundError(p)
    text = p.read_text(encoding="utf-8")
    rules: list[HardRule] = []
    in_section = False
    cur: dict = {}
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped == "hard_rules:":
            in_section = True
            continue
        if in_section:
            if not line.startswith(" ") and stripped and not stripped.startswith("-"):
                # Section break
                if cur.get("id"):
                    rules.append(HardRule(
                        id=cur["id"],
                        statement=cur.get("statement", ""),
                        severity=cur.get("severity", "fatal"),
                    ))
                in_section = False
                cur = {}
                continue
            if stripped.startswith("- id:"):
                if cur.get("id"):
                    rules.append(HardRule(
                        id=cur["id"],
                        statement=cur.get("statement", ""),
                        severity=cur.get("severity", "fatal"),
                    ))
                cur = {"id": stripped.split(":", 1)[1].strip()}
            elif stripped.startswith("statement:"):
                v = stripped.split(":", 1)[1].strip()
                if v.startswith('"') and v.endswith('"'):
                    v = v[1:-1]
                cur["statement"] = v
            elif stripped.startswith("severity:"):
                cur["severity"] = stripped.split(":", 1)[1].strip()
    if cur.get("id"):
        rules.append(HardRule(
            id=cur["id"],
            statement=cur.get("statement", ""),
            severity=cur.get("severity", "fatal"),
        ))
    return tuple(rules)


# ── Evaluation ────────────────────────────────────────────────────

def evaluate(*,
                action_id: str,
                action_kind: str,
                action_lane: str,
                requires_human_review: bool,
                no_runtime_mutation: bool,
                hard_rules: tuple[HardRule, ...],
                adaptive_rules: tuple[PolicyRule, ...] = (),
                capsule_context: str = "neutral_v1",
                ) -> PolicyEvaluation:
    """Evaluate one action recommendation against the policy stack.

    Hard rules are checked first; any fatal hard-rule violation
    blocks the action immediately. Adaptive rules layered on top may
    add advisory blocks but cannot weaken a hard verdict.
    """
    blocking: list[str] = []
    advisory: list[str] = []
    reasons: list[str] = []

    # Hard rule checks (constitution-listed invariants)
    if not no_runtime_mutation:
        # The action_gate must always see no_runtime_mutation=True;
        # if not, the action_gate_is_only_exit hard rule blocks it.
        for hr in hard_rules:
            if hr.id == "action_gate_is_only_exit":
                blocking.append(hr.id)
                reasons.append(
                    f"hard rule {hr.id}: action carries no_runtime_mutation=False"
                )
                break

    # Adaptive rule checks
    for ar in adaptive_rules:
        if ar.capsule_scope and ar.capsule_scope != capsule_context:
            continue
        if ar.verb == "add_advisory_check":
            advisory.append(ar.rule_id)
            reasons.append(
                f"adaptive advisory {ar.rule_id}: {ar.statement[:80]}"
            )
        elif ar.verb in ("tighten", "narrow_scope"):
            # Tightening that explicitly forbids this kind+lane combo
            # uses a convention: statement contains "FORBID:<lane>:<kind>"
            tag = f"FORBID:{action_lane}:{action_kind}"
            if tag in ar.statement:
                blocking.append(ar.rule_id)
                reasons.append(
                    f"adaptive rule {ar.rule_id} forbids {action_lane}/{action_kind}"
                )

    allowed = len(blocking) == 0
    return PolicyEvaluation(
        action_id=action_id,
        allowed=allowed,
        blocking_rule_ids=tuple(blocking),
        advisory_rule_ids=tuple(advisory),
        reasons=tuple(reasons),
    )
