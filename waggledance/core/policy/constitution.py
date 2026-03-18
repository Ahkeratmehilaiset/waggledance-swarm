"""
Constitution — YAML-driven policy rule loader and matcher.

The constitution defines deny-by-default rules that all actions must pass.
Rules are loaded from configs/policy/default_constitution.yaml and can
be overridden per profile (HOME, COTTAGE, FACTORY, APIARY, etc.).

Profile-specific policy files from configs/policy/profile_policies/*.yaml
are also loaded and merged — they provide per-profile rule overrides and
risk thresholds (max_composite_auto_approve, max_severity_auto_approve, etc.).
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

log = logging.getLogger("waggledance.policy.constitution")

# Default constitution path
_DEFAULT_PATH = Path("configs/policy/default_constitution.yaml")
_PROFILE_POLICIES_DIR = Path("configs/policy/profile_policies")


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
            elif key.endswith("_max"):
                field_name = key[:-4]
                actual = context.get(field_name, 1.0)
                if not (actual <= expected):
                    return False
            elif key.endswith("_prefix"):
                field_name = key[:-7]
                actual = context.get(field_name, "")
                if not str(actual).startswith(str(expected)):
                    return False
            elif key.endswith("_pattern"):
                field_name = key[:-8]
                actual = context.get(field_name, "")
                if not fnmatch.fnmatch(str(actual), str(expected)):
                    return False
            else:
                actual = context.get(key)
                if actual != expected:
                    return False
        return True


@dataclass
class ProfileThresholds:
    """Risk thresholds for a profile — drives auto-approve/deny decisions."""
    max_composite_auto_approve: float = 0.3
    max_severity_auto_approve: float = 0.5
    night_learning_budget_hours: float = 6.0
    max_proactive_goals_per_day: int = 5


@dataclass
class Constitution:
    """Loaded constitution with global rules and profile overrides."""
    version: int = 1
    default_action: str = "deny"
    global_rules: List[ConstitutionRule] = field(default_factory=list)
    profile_overrides: Dict[str, List[ConstitutionRule]] = field(default_factory=dict)
    profile_thresholds: Dict[str, ProfileThresholds] = field(default_factory=dict)

    def get_rules_for_profile(self, profile: str) -> List[ConstitutionRule]:
        """Get global rules + profile-specific overrides."""
        rules = list(self.global_rules)
        norm = profile.upper()
        if norm in self.profile_overrides:
            rules.extend(self.profile_overrides[norm])
        return rules

    def get_thresholds(self, profile: str) -> ProfileThresholds:
        """Get risk thresholds for a profile (falls back to safe defaults)."""
        return self.profile_thresholds.get(
            profile.upper(), ProfileThresholds())


def load_constitution(
    path: Optional[Path] = None,
    profile_dir: Optional[Path] = None,
) -> Constitution:
    """Load constitution from YAML file + per-profile policy files."""
    path = path or _DEFAULT_PATH
    profile_dir = profile_dir or _PROFILE_POLICIES_DIR

    constitution = Constitution()

    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            c = data.get("constitution", {})
            constitution.version = c.get("version", 1)
            constitution.default_action = c.get("default_action", "deny")

            for rule_data in c.get("global_rules", []):
                constitution.global_rules.append(_parse_rule(rule_data))

            for profile, rules in c.get("profile_overrides", {}).items():
                constitution.profile_overrides[profile.upper()] = [
                    _parse_rule(r) for r in rules]
        except Exception as e:
            log.error("Failed to load constitution: %s", e)
    else:
        log.warning("Constitution file not found at %s, using deny-by-default", path)

    # Load per-profile policy files
    if profile_dir.exists():
        for pp in sorted(profile_dir.glob("*.yaml")):
            try:
                _load_profile_policy(pp, constitution)
            except Exception as exc:
                log.warning("Failed to load profile policy %s: %s", pp.name, exc)

    log.info("Constitution v%d loaded: %d global rules, %d profiles, %d threshold sets",
             constitution.version,
             len(constitution.global_rules),
             len(constitution.profile_overrides),
             len(constitution.profile_thresholds))
    return constitution


def _load_profile_policy(path: Path, constitution: Constitution) -> None:
    """Load a profile-specific policy YAML and merge into constitution."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not data:
        return

    profile = data.get("profile", path.stem).upper()

    # Parse overrides into ConstitutionRules
    rules: List[ConstitutionRule] = []
    for override in data.get("overrides", []):
        rule = _parse_profile_override(override)
        if rule:
            rules.append(rule)

    if rules:
        existing = constitution.profile_overrides.get(profile, [])
        # Profile policy rules come after inline constitution overrides
        existing.extend(rules)
        constitution.profile_overrides[profile] = existing

    # Parse thresholds
    thresholds_data = data.get("thresholds", {})
    if thresholds_data:
        constitution.profile_thresholds[profile] = ProfileThresholds(
            max_composite_auto_approve=thresholds_data.get(
                "max_composite_auto_approve", 0.3),
            max_severity_auto_approve=thresholds_data.get(
                "max_severity_auto_approve", 0.5),
            night_learning_budget_hours=thresholds_data.get(
                "night_learning_budget_hours", 6.0),
            max_proactive_goals_per_day=thresholds_data.get(
                "max_proactive_goals_per_day", 5),
        )

    log.debug("Profile policy %s: %d rules, thresholds=%s",
              profile, len(rules), bool(thresholds_data))


def _parse_profile_override(data: dict) -> Optional[ConstitutionRule]:
    """Convert a profile override entry to a ConstitutionRule.

    Profile YAML format:
        - id: varroa_treatment_alert
          description: "Allow automated varroa treatment alerts"
          condition:
            category: DETECT
            capability_pattern: "*.varroa*"
            severity_max: 0.5
          action: allow
          reason: "Varroa detection is observational"
    """
    override_id = data.get("id", "")
    if not override_id:
        return None

    condition = data.get("condition", {})
    action = data.get("action", "deny")
    description = data.get("description", data.get("reason", ""))

    # Map profile action → enforcement
    enforcement_map = {
        "allow": "allow",
        "deny": "deny",
        "require_approval": "require_approval",
        "require_verifier": "require_verifier",
    }
    enforcement = enforcement_map.get(action, "deny")

    # Build applies_to from condition.category
    applies_to: List[str] = []
    category = condition.pop("category", None)
    if category:
        applies_to = [category.lower()]

    # Build conditions dict from remaining condition fields
    conditions: Dict[str, Any] = {}
    for key, value in condition.items():
        if key == "capability_pattern":
            conditions["capability_id_pattern"] = value
        elif key == "severity_max":
            conditions["severity_max"] = value
        elif key == "blast_radius_max":
            conditions["blast_radius_max"] = value
        else:
            conditions[key] = value

    return ConstitutionRule(
        rule_id=override_id,
        description=description,
        applies_to=applies_to,
        conditions=conditions,
        enforcement=enforcement,
    )


def _parse_rule(data: dict) -> ConstitutionRule:
    return ConstitutionRule(
        rule_id=data.get("rule_id", "unknown"),
        description=data.get("description", ""),
        applies_to=data.get("applies_to", []),
        conditions=data.get("conditions", {}),
        enforcement=data.get("enforcement", "deny"),
        unless=data.get("unless"),
    )
