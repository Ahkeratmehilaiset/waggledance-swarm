"""SeasonalGuard — deterministic seasonal rule checker (<0.1ms).

Loads rules from configs/seasonal_rules.yaml. Scans text for forbidden
keywords outside their valid months. Returns violations with reason
and suggestion. Uses normalize_fi() for robust Finnish matching.

Usage:
    guard = SeasonalGuard()
    violations = guard.check("linkoa hunajaa nyt")
    # → [{"rule": "honey_extraction", "reason_fi": "...", "suggestion_fi": "..."}]
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger("seasonal_guard")

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "seasonal_rules.yaml"


class SeasonalViolation:
    """A single seasonal rule violation."""
    __slots__ = ("rule", "reason_fi", "reason_en", "suggestion_fi", "matched_keyword")

    def __init__(self, rule: str, reason_fi: str, reason_en: str,
                 suggestion_fi: str, matched_keyword: str):
        self.rule = rule
        self.reason_fi = reason_fi
        self.reason_en = reason_en
        self.suggestion_fi = suggestion_fi
        self.matched_keyword = matched_keyword

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "reason_fi": self.reason_fi,
            "reason_en": self.reason_en,
            "suggestion_fi": self.suggestion_fi,
            "matched_keyword": self.matched_keyword,
        }

    def __repr__(self):
        return f"SeasonalViolation({self.rule!r}, keyword={self.matched_keyword!r})"


class SeasonalGuard:
    """Deterministic seasonal rule checker.

    Performance target: <0.1ms per check (pure string matching, no LLM).
    """

    def __init__(self, config_path: Optional[str] = None, month: Optional[int] = None):
        """Initialize guard.

        Args:
            config_path: Override path to seasonal_rules.yaml (for testing).
            month: Override current month (1-12, for testing).
        """
        self._rules: dict = {}
        self._month_names_fi: dict[int, str] = {}
        self._month_names_en: dict[int, str] = {}
        self._month_override = month
        self._normalize_available = False
        self._normalize_fn = None

        # Pre-compile: list of (rule_name, months_set, keywords_lower, reason_fi, reason_en, suggestion_fi)
        self._compiled_rules: list[tuple] = []

        self._load_config(config_path)
        self._init_normalizer()

    def _load_config(self, config_path: Optional[str] = None):
        """Load rules from YAML config."""
        path = Path(config_path) if config_path else _CONFIG_PATH
        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except (FileNotFoundError, yaml.YAMLError) as e:
            log.warning("SeasonalGuard: config load failed: %s", e)
            return

        self._rules = data.get("rules", {})
        self._month_names_fi = {int(k): v for k, v in data.get("month_names_fi", {}).items()}
        self._month_names_en = {int(k): v for k, v in data.get("month_names_en", {}).items()}

        # Pre-compile rules for fast matching
        self._compiled_rules = []
        for rule_name, rule in self._rules.items():
            months = set(rule.get("months", []))
            keywords = [kw.lower() for kw in rule.get("forbidden", [])]
            reason_fi = rule.get("reason_fi", "")
            reason_en = rule.get("reason_en", "")
            suggestion_fi = rule.get("suggestion_fi", "")
            if months and keywords:
                self._compiled_rules.append(
                    (rule_name, months, keywords, reason_fi, reason_en, suggestion_fi))

        log.info("SeasonalGuard: loaded %d rules", len(self._compiled_rules))

    def _init_normalizer(self):
        """Try to import normalize_fi for robust Finnish matching."""
        try:
            from core.normalizer import normalize_fi
            self._normalize_fn = normalize_fi
            self._normalize_available = True
        except ImportError:
            self._normalize_available = False

    @property
    def current_month(self) -> int:
        """Current month (1-12), overridable for testing."""
        if self._month_override is not None:
            return self._month_override
        return datetime.now().month

    @current_month.setter
    def current_month(self, value: int):
        """Set month override (for testing)."""
        self._month_override = value

    @property
    def month_name_fi(self) -> str:
        """Current month name in Finnish."""
        return self._month_names_fi.get(self.current_month, f"kuukausi {self.current_month}")

    @property
    def month_name_en(self) -> str:
        """Current month name in English."""
        return self._month_names_en.get(self.current_month, f"month {self.current_month}")

    @property
    def rule_count(self) -> int:
        return len(self._compiled_rules)

    def check(self, text: str) -> list[SeasonalViolation]:
        """Check text for seasonal violations. Deterministic, <0.1ms.

        Args:
            text: Finnish or English text to check.

        Returns:
            List of SeasonalViolation objects (empty if no violations).
        """
        if not text or not self._compiled_rules:
            return []

        text_lower = text.lower()

        # Also check normalized text for better Finnish matching
        text_normalized = ""
        if self._normalize_available and self._normalize_fn:
            try:
                text_normalized = self._normalize_fn(text_lower)
            except Exception:
                pass

        month = self.current_month
        violations = []

        for rule_name, valid_months, keywords, reason_fi, reason_en, suggestion_fi in self._compiled_rules:
            # Skip rules where current month IS valid — no violation possible
            if month in valid_months:
                continue

            # Check each forbidden keyword
            for kw in keywords:
                if kw in text_lower:
                    violations.append(SeasonalViolation(
                        rule=rule_name, reason_fi=reason_fi,
                        reason_en=reason_en, suggestion_fi=suggestion_fi,
                        matched_keyword=kw))
                    break  # One match per rule is enough
                # Also check normalized text
                if text_normalized and kw in text_normalized:
                    violations.append(SeasonalViolation(
                        rule=rule_name, reason_fi=reason_fi,
                        reason_en=reason_en, suggestion_fi=suggestion_fi,
                        matched_keyword=kw))
                    break

        return violations

    def annotate_answer(self, answer: str) -> str:
        """Check answer and append seasonal warnings if violations found.

        Returns the original answer with appended warnings, or unchanged if clean.
        """
        violations = self.check(answer)
        if not violations:
            return answer

        warnings = []
        for v in violations:
            warnings.append(f"\n\n⚠️ Kausihuomautus: {v.reason_fi} {v.suggestion_fi}")

        return answer + "".join(warnings)

    def filter_enrichment(self, fact: str) -> tuple[bool, str]:
        """Check if a fact is seasonally appropriate for enrichment storage.

        Returns:
            (True, "") if fact is OK to store.
            (False, reason) if fact should be rejected.
        """
        violations = self.check(fact)
        if not violations:
            return (True, "")
        reasons = "; ".join(v.reason_en for v in violations)
        return (False, f"Seasonal violation: {reasons}")

    def queen_context(self) -> str:
        """Generate seasonal context string for Round Table Queen synthesis.

        Returns English string like:
        'Current month: February (helmikuu). Season: late winter.
         Active rules: winter feeding, spring inspection preparation.
         Forbidden now: honey extraction, swarming prevention, queen rearing.'
        """
        month = self.current_month
        month_fi = self.month_name_fi
        month_en = self.month_name_en

        # Determine season
        if month in (12, 1, 2):
            season = "late winter / early spring preparation"
        elif month in (3, 4, 5):
            season = "spring"
        elif month in (6, 7, 8):
            season = "summer / main season"
        elif month in (9, 10, 11):
            season = "autumn / winter preparation"
        else:
            season = "unknown"

        # Collect active and forbidden rule names
        active_rules = []
        forbidden_rules = []
        for rule_name, valid_months, _kw, _rfi, reason_en, _sfi in self._compiled_rules:
            if month in valid_months:
                active_rules.append(rule_name.replace("_", " "))
            else:
                forbidden_rules.append(rule_name.replace("_", " "))

        parts = [
            f"Current month: {month_en} ({month_fi}). Season: {season}.",
        ]
        if active_rules:
            parts.append(f"Active now: {', '.join(active_rules)}.")
        if forbidden_rules:
            parts.append(f"Not in season: {', '.join(forbidden_rules)}.")
        parts.append("Apply seasonal rules — do not recommend out-of-season activities as current actions.")

        return " ".join(parts)


# Module-level singleton for easy import
_GUARD: Optional[SeasonalGuard] = None


def get_seasonal_guard(month: Optional[int] = None) -> SeasonalGuard:
    """Get or create the module-level SeasonalGuard singleton."""
    global _GUARD
    if _GUARD is None:
        _GUARD = SeasonalGuard(month=month)
    elif month is not None:
        _GUARD.current_month = month
    return _GUARD
