# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""
Narrative Projector — generates human-readable self-narratives (v3.2).

Read-only projection over world model + goal engine + epistemic uncertainty.
No new store, no LLM calls in fast path.
Template-based with language support (en, fi).
60-second cache maximum.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger("waggledance.projections.narrative")


# ── Templates ────────────────────────────────────────────────

_TEMPLATES = {
    "en": {
        "header": "System Narrative — {timestamp}",
        "goals_active": "{count} active goals",
        "goals_none": "No active goals",
        "goal_line": "- [{status}] {description} (priority {priority})",
        "promise_line": "- PROMISED: {description}",
        "uncertainty": "Epistemic uncertainty: {score:.1%} ({label})",
        "uncertainty_low": "low",
        "uncertainty_moderate": "moderate",
        "uncertainty_high": "high",
        "attention": "Attention: critical {critical}%, normal {normal}%, background {background}%, reflection {reflection}%",
        "motives_header": "Active motives:",
        "motive_line": "- {motive_id} (valence {valence:.2f})",
        "gaps_header": "Observability gaps: {count}",
        "gap_line": "- {entity}: {reason}",
        "achievements": "Recent achievements: {count}",
        "no_achievements": "No recent achievements",
        "user_header": "User model:",
        "user_interactions": "  Interactions: {count}",
        "user_corrections": "  Corrections: {count}",
        "user_promises": "  Pending promises: {count}",
    },
    "fi": {
        "header": "Järjestelmäkertomus — {timestamp}",
        "goals_active": "{count} aktiivista tavoitetta",
        "goals_none": "Ei aktiivisia tavoitteita",
        "goal_line": "- [{status}] {description} (prioriteetti {priority})",
        "promise_line": "- LUVATTU: {description}",
        "uncertainty": "Episteeminen epävarmuus: {score:.1%} ({label})",
        "uncertainty_low": "matala",
        "uncertainty_moderate": "kohtalainen",
        "uncertainty_high": "korkea",
        "attention": "Huomio: kriittinen {critical}%, normaali {normal}%, tausta {background}%, reflektio {reflection}%",
        "motives_header": "Aktiiviset motiivit:",
        "motive_line": "- {motive_id} (valenssi {valence:.2f})",
        "gaps_header": "Havainnointipuutteet: {count}",
        "gap_line": "- {entity}: {reason}",
        "achievements": "Viimeaikaiset saavutukset: {count}",
        "no_achievements": "Ei viimeaikaisia saavutuksia",
        "user_header": "Käyttäjämalli:",
        "user_interactions": "  Vuorovaikutukset: {count}",
        "user_corrections": "  Korjaukset: {count}",
        "user_promises": "  Avoimet lupaukset: {count}",
    },
}


# ── Cache ────────────────────────────────────────────────────

@dataclass
class _CacheEntry:
    result: Dict[str, Any]
    timestamp: float
    cache_key: str


_cache: Optional[_CacheEntry] = None
CACHE_TTL_SECONDS = 60


# ── Core function ────────────────────────────────────────────

def project_narrative(
    goals: Optional[List[Dict[str, Any]]] = None,
    promises: Optional[List[Dict[str, Any]]] = None,
    uncertainty_score: float = 0.0,
    attention_allocation: Optional[Dict[str, Any]] = None,
    active_motives: Optional[List[Dict[str, Any]]] = None,
    observability_gaps: Optional[List[Dict[str, Any]]] = None,
    recent_achievements: Optional[List[str]] = None,
    capability_confidence: Optional[Dict[str, float]] = None,
    language: str = "en",
    user_entity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Generate a human-readable narrative from current system state.

    All inputs are plain dicts/lists — no direct dependency on runtime objects.
    Caller is responsible for extracting data from world model / goal engine.

    Returns:
        {"narrative": str, "timestamp": float, "language": str, "cached": bool,
         "sections": dict}
    """
    global _cache

    # Track whether live promises were provided before normalization (v3.3)
    _live_promises_provided = promises is not None

    # Build cache key from inputs
    user_key = ""
    if user_entity:
        u = user_entity
        if _live_promises_provided:
            p_count = len(promises or [])
        else:
            p_count = len(u.get("pending_promise_goal_ids", []))
        user_key = f":{u.get('interaction_count', 0)}:{u.get('explicit_correction_count', 0)}:{p_count}:{u.get('preferred_language', '')}"
    cache_key = f"{len(goals or [])}:{uncertainty_score:.3f}:{language}{user_key}"
    now = time.time()

    if _cache and _cache.cache_key == cache_key and (now - _cache.timestamp) < CACHE_TTL_SECONDS:
        result = _cache.result.copy()
        result["cached"] = True
        return result

    t = _TEMPLATES.get(language, _TEMPLATES["en"])
    lines: List[str] = []
    sections: Dict[str, Any] = {}

    # Header
    ts_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    lines.append(t["header"].format(timestamp=ts_str))
    lines.append("")

    # Goals
    goal_list = goals or []
    if goal_list:
        lines.append(t["goals_active"].format(count=len(goal_list)))
        for g in goal_list[:10]:  # cap display
            lines.append(t["goal_line"].format(
                status=g.get("status", "unknown"),
                description=g.get("description", "?"),
                priority=g.get("priority", 0),
            ))
        sections["goals_count"] = len(goal_list)
    else:
        lines.append(t["goals_none"])
        sections["goals_count"] = 0
    lines.append("")

    # Promises
    promise_list = promises or []
    if promise_list:
        for p in promise_list:
            lines.append(t["promise_line"].format(description=p.get("description", "?")))
    sections["promises_count"] = len(promise_list)

    # Uncertainty
    if uncertainty_score < 0.3:
        label = t["uncertainty_low"]
    elif uncertainty_score < 0.6:
        label = t["uncertainty_moderate"]
    else:
        label = t["uncertainty_high"]
    lines.append(t["uncertainty"].format(score=uncertainty_score, label=label))
    sections["uncertainty_score"] = uncertainty_score
    sections["uncertainty_label"] = label
    lines.append("")

    # Attention
    att = attention_allocation or {}
    if att:
        lines.append(t["attention"].format(
            critical=att.get("critical", 40),
            normal=att.get("normal", 35),
            background=att.get("background", 15),
            reflection=att.get("reflection", 10),
        ))
        lines.append("")

    # Motives
    motive_list = active_motives or []
    if motive_list:
        lines.append(t["motives_header"])
        for m in motive_list:
            lines.append(t["motive_line"].format(
                motive_id=m.get("motive_id", "?"),
                valence=m.get("valence", 0.0),
            ))
        lines.append("")
    sections["motives_count"] = len(motive_list)

    # Observability gaps
    gap_list = observability_gaps or []
    if gap_list:
        lines.append(t["gaps_header"].format(count=len(gap_list)))
        for gap in gap_list[:5]:
            lines.append(t["gap_line"].format(
                entity=gap.get("entity", "?"),
                reason=gap.get("reason", "unknown"),
            ))
        lines.append("")
    sections["gaps_count"] = len(gap_list)

    # Capability confidence
    conf = capability_confidence or {}
    if conf:
        sorted_conf = sorted(conf.items(), key=lambda x: x[1])
        lines.append("Capability confidence:")
        for cap_id, score in sorted_conf[:5]:
            bar = "#" * int(score * 10)
            lines.append(f"  {cap_id}: {score:.2f} [{bar}]")
        lines.append("")
    sections["capability_confidence"] = conf

    # User entity (v3.3)
    user = user_entity or {}
    if _live_promises_provided:
        pending_count = len(promise_list)
    else:
        pending_count = len(user.get("pending_promise_goal_ids", []))
    if user:
        lines.append(t["user_header"])
        lines.append(t["user_interactions"].format(count=user.get("interaction_count", 0)))
        lines.append(t["user_corrections"].format(count=user.get("explicit_correction_count", 0)))
        lines.append(t["user_promises"].format(count=pending_count))
        lines.append("")
    sections["user_entity"] = {
        "interaction_count": user.get("interaction_count", 0),
        "corrections": user.get("explicit_correction_count", 0),
        "pending_promises": pending_count,
    }

    # Achievements
    ach = recent_achievements or []
    if ach:
        lines.append(t["achievements"].format(count=len(ach)))
        for a in ach[:5]:
            lines.append(f"  - {a}")
    else:
        lines.append(t["no_achievements"])
    sections["achievements_count"] = len(ach)

    narrative = "\n".join(lines)
    result = {
        "narrative": narrative,
        "timestamp": now,
        "language": language,
        "cached": False,
        "sections": sections,
    }

    _cache = _CacheEntry(result=result, timestamp=now, cache_key=cache_key)
    return result


def clear_cache():
    """Clear the narrative cache (for testing)."""
    global _cache
    _cache = None
