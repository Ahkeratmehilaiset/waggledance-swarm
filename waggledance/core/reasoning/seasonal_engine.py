"""Seasonal engine — Finnish beekeeping seasonal calendar and recommendations.

Reads configs/knowledge/cottage/seasonal_tasks.yaml and provides
month-based task lists, seasonal activity factors, and critical period
detection for the cottage/apiary domain.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SEASONAL_YAML = None


def _load_seasonal_yaml() -> Optional[dict]:
    """Load seasonal_tasks.yaml (cached)."""
    global _SEASONAL_YAML
    if _SEASONAL_YAML is not None:
        return _SEASONAL_YAML
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML not available — seasonal engine disabled")
        return None

    candidates = [
        os.path.join("configs", "knowledge", "cottage", "seasonal_tasks.yaml"),
        os.path.join(os.path.dirname(__file__), "..", "..", "..",
                     "configs", "knowledge", "cottage", "seasonal_tasks.yaml"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                _SEASONAL_YAML = yaml.safe_load(f)
            return _SEASONAL_YAML
    return None


# Monthly activity factors (0=dormant, 1=peak activity)
_ACTIVITY_FACTORS = {
    1: 0.05, 2: 0.10, 3: 0.20, 4: 0.50,
    5: 0.90, 6: 1.00, 7: 0.95, 8: 0.80,
    9: 0.50, 10: 0.30, 11: 0.10, 12: 0.05,
}

# Critical periods: swarming (May-Jul), winter prep (Sep-Oct)
_CRITICAL_MONTHS = {5, 6, 7, 9, 10}


class SeasonalEngine:
    """Finnish beekeeping seasonal calendar engine.

    Provides month-based task lists, activity factors, critical period
    detection, and seasonal recommendations.
    """

    def __init__(self):
        self._data = _load_seasonal_yaml()
        self._query_count = 0

    def _current_month(self, month: Optional[int] = None) -> int:
        """Return the month to use (default: current month)."""
        if month is not None:
            return max(1, min(12, month))
        return datetime.now().month

    def _get_month_entry(self, month: int) -> Optional[dict]:
        """Get the YAML entry for a specific month."""
        if not self._data or "months" not in self._data:
            return None
        for entry in self._data["months"]:
            if entry.get("month") == month:
                return entry
        return None

    def get_current_tasks(self, month: Optional[int] = None,
                          lang: str = "fi") -> List[str]:
        """Get beekeeping tasks for the given month.

        Args:
            month: 1-12 (default: current month)
            lang: 'fi' or 'en'

        Returns:
            List of task descriptions
        """
        self._query_count += 1
        m = self._current_month(month)
        entry = self._get_month_entry(m)
        if not entry:
            return []
        key = f"tasks_{lang}"
        return entry.get(key, entry.get("tasks_fi", []))

    def get_seasonal_factor(self, month: Optional[int] = None) -> float:
        """Return 0.0–1.0 activity factor for the given month.

        0.0 = fully dormant (deep winter), 1.0 = peak activity (June).
        """
        self._query_count += 1
        m = self._current_month(month)
        return _ACTIVITY_FACTORS.get(m, 0.0)

    def is_critical_period(self, month: Optional[int] = None) -> bool:
        """Check if the month is a critical beekeeping period.

        Critical periods:
        - May–Jul: swarming season (highest management intensity)
        - Sep–Oct: winter preparation (last chance to feed/treat)
        """
        self._query_count += 1
        m = self._current_month(month)
        return m in _CRITICAL_MONTHS

    def get_risk_factors(self, month: Optional[int] = None,
                         lang: str = "fi") -> List[str]:
        """Get risk factors for the given month."""
        m = self._current_month(month)
        entry = self._get_month_entry(m)
        if not entry:
            return []
        key = f"risk_factors_{lang}"
        return entry.get(key, entry.get("risk_factors_fi", []))

    def get_recommendations(self, month: Optional[int] = None,
                            profile: str = "cottage") -> Dict[str, Any]:
        """Get combined seasonal recommendations.

        Combines tasks, activity factor, critical status, and risk factors.
        """
        self._query_count += 1
        m = self._current_month(month)
        entry = self._get_month_entry(m)

        return {
            "month": m,
            "month_name_fi": entry.get("month_name_fi", "") if entry else "",
            "month_name_en": entry.get("month_name_en", "") if entry else "",
            "season_fi": entry.get("season_fi", "") if entry else "",
            "season_en": entry.get("season_en", "") if entry else "",
            "activity_factor": self.get_seasonal_factor(m),
            "is_critical": self.is_critical_period(m),
            "tasks_fi": self.get_current_tasks(m, "fi"),
            "tasks_en": self.get_current_tasks(m, "en"),
            "risk_factors": self.get_risk_factors(m, "fi"),
            "profile": profile,
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "yaml_loaded": self._data is not None,
            "months_available": len(self._data.get("months", [])) if self._data else 0,
            "query_count": self._query_count,
        }
