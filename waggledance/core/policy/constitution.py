"""
Constitution — YAML-driven policy rule loader and matcher.

The constitution defines deny-by-default rules that all actions must pass.
Rules are loaded from configs/policy/default_constitution.yaml and can
be overridden per profile (HOME, COTTAGE, FACTORY, APIARY, etc.).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

log = logging.getLogger("waggledance.policy.constitution")

# Default constitution path
_DEFAULT_PATH = Path("configs/policy/default_constitution.yaml")


@dataclass
class ConstitutionRule:
    """A single policy rule from the constitution."""
    rule_id: str
    description: str = ""
    applies_to: List[str] = field(default_factory=list)
    conditions: Dict[str, Any] = field(default_factory=dict)
    enforcement: str = "deny"  # allow / deny / require_approval / require_verifier / allow_emergency / defer_to_seasonal_guard
    unless: Optional[str] = None  # optional override condition

    def matches_category(self, category: str) -> bool:
        """Check if this rule applies to the given capability category."""
        if not self.applies_to:
            return True
        return category in self.applies_to

    def evaluate_conditions(self, context: Dict[str, Any]) -> bool:
        """
        Check if all conditions in this rule match the given context.
        Empty conditions always match.
        """
        if not self.conditions:
            return True

        for key, expected in self.conditions.items():
            if key.endswith("_gt"):
                field_name = key[:-3]
                actual = context.get(field_name, 0.0)
                if not (actual > expected):
                    return False
            elif key.endswith("_lt"):
                field_name = key[:-3]
                actual = context.get(field_name, 1.0)
                if not (actual < expected):
                    return False
            elif key.endswith("_prefix"):
                field_name = key[:-7]
                actual = context.get(field_name, "")
                if not str(actual).startswith(str(expected)):
                    return False
            else:
                actual = context.get(key)
                if actual != expected:
                    return False
        return True


@dataclass
class Constitution:
    """Loaded constitution with global rules and profile overrides."""
    version: int = 1
    default_action: str = "deny"
    global_rules: List[ConstitutionRule] = field(default_factory=list)
    profile_overrides: Dict[str, List[ConstitutionRule]] = field(default_factory=dict)

    def get_rules_for_profile(self, profile: str) -> List[ConstitutionRule]:
        """Get global rules + profile-specific overrides."""
        rules = list(self.global_rules)
        if profile in self.profile_overrides:
            rules.extend(self.profile_overrides[profile])
        return rules


def load_constitution(path: Optional[Path] = None) -> Constitution:
    """Load constitution from YAML file."""
    path = path or _DEFAULT_PATH
    if not path.exists():
        log.warning("Constitution file not found at %s, using deny-by-default", path)
        return Constitution()

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("Failed to load constitution: %s", e)
        return Constitution()

    c = data.get("constitution", {})
    constitution = Constitution(
        version=c.get("version", 1),
        default_action=c.get("default_action", "deny"),
    )

    for rule_data in c.get("global_rules", []):
        constitution.global_rules.append(_parse_rule(rule_data))

    for profile, rules in c.get("profile_overrides", {}).items():
        constitution.profile_overrides[profile] = [_parse_rule(r) for r in rules]

    log.info("Constitution v%d loaded: %d global rules, %d profiles",
             constitution.version,
             len(constitution.global_rules),
             len(constitution.profile_overrides))
    return constitution


def _parse_rule(data: dict) -> ConstitutionRule:
    return ConstitutionRule(
        rule_id=data.get("rule_id", "unknown"),
        description=data.get("description", ""),
        applies_to=data.get("applies_to", []),
        conditions=data.get("conditions", {}),
        enforcement=data.get("enforcement", "deny"),
        unless=data.get("unless"),
    )
