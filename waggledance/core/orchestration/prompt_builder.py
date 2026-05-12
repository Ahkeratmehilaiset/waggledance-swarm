# SPDX-License-Identifier: BUSL-1.1
"""Agent prompt builder with YAMLBridge primary + f-string fallback.

Audit H11 / operator decision D3.3 Phase A: the production chat path
hand-built every agent prompt as a one-line f-string
(``f"You are {agent.name} ({agent.domain})..."``) while the rich
YAMLBridge prompt-assembly machinery (ASSUMPTIONS,
DECISION_METRICS_AND_THRESHOLDS, eval_questions, locale overlays)
sat dead in legacy ``core/yaml_bridge.py``. This module bridges
production callers to YAMLBridge with a graceful fallback.

PHASE A contract:
- Try YAMLBridge.build_system_prompt(agent.id) FIRST.
- On any failure (YAMLBridge import error, missing agent_id, empty
  prompt, exception), fall back to the original f-string.
- Caller never sees an exception or empty string.

PHASE B (separate PR PR-M-B): once all 75 agents have been validated
to return non-empty rich prompts, the fallback is removed.

Threading: a lazy singleton YAMLBridge is created on first call and
reused. Language switching uses YAMLBridge.set_language() under a
module-level lock to keep the singleton state consistent.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

_yaml_bridge_singleton: Any = None
_yaml_bridge_lock = threading.Lock()
_yaml_bridge_unavailable = False  # set True after first failed import


def _get_yaml_bridge() -> Any | None:
    """Lazy-create a singleton YAMLBridge instance.

    Returns None if YAMLBridge can't be imported or constructed —
    callers fall through to the f-string in that case.
    """
    global _yaml_bridge_singleton, _yaml_bridge_unavailable
    if _yaml_bridge_unavailable:
        return None
    if _yaml_bridge_singleton is not None:
        return _yaml_bridge_singleton
    with _yaml_bridge_lock:
        if _yaml_bridge_singleton is not None:
            return _yaml_bridge_singleton
        if _yaml_bridge_unavailable:
            return None
        try:
            from core.yaml_bridge import YAMLBridge
            _yaml_bridge_singleton = YAMLBridge("agents")
            return _yaml_bridge_singleton
        except Exception as exc:  # noqa: BLE001 — fail-safe
            log.debug(
                "YAMLBridge unavailable; prompt builder will use "
                "f-string fallback for all agents (Phase A): %s", exc,
            )
            _yaml_bridge_unavailable = True
            return None


def _fallback_system_prompt(agent: Any) -> str:
    """The pre-D3.3-PhaseA one-liner — preserved verbatim as fallback.

    Matches the existing ad-hoc prompts in orchestrator.py /
    round_table.py / hex_neighbor_assist.py so behavior is unchanged
    when YAMLBridge is unavailable.
    """
    name = getattr(agent, "name", "Agent")
    domain = getattr(agent, "domain", "general")
    return f"You are {name} ({domain})."


def build_agent_prompt(
    agent: Any,
    user_query: str,
    *,
    language: str = "fi",
    style_hint: str | None = None,
) -> str:
    """Build a system+user prompt for one agent.

    Args:
        agent:        AgentDefinition (must have .id, .name, .domain).
        user_query:   The user's question to append after the system part.
        language:     "fi" or "en" — selects the YAMLBridge locale overlay.
        style_hint:   Optional appended instruction such as
                      "Answer briefly." or "Answer in 2-3 sentences."
                      Preserves call-site stylistic differences.

    Returns:
        A single prompt string ready to send to the LLM. Never raises,
        never returns empty.
    """
    bridge = _get_yaml_bridge()
    agent_id = getattr(agent, "id", "")

    system_part: str | None = None
    if bridge is not None and agent_id:
        try:
            with _yaml_bridge_lock:
                # set_language is best-effort; if it raises, we still try
                # build_system_prompt with the bridge's current language.
                if hasattr(bridge, "set_language"):
                    try:
                        bridge.set_language(language)
                    except Exception:  # noqa: BLE001
                        pass
                built = bridge.build_system_prompt(agent_id)
            if isinstance(built, str) and built.strip():
                system_part = built
        except Exception as exc:  # noqa: BLE001 — fail-safe
            log.debug(
                "YAMLBridge.build_system_prompt failed for agent_id=%r "
                "(language=%r); falling back to f-string: %s",
                agent_id, language, exc,
            )

    if system_part is None:
        system_part = _fallback_system_prompt(agent)

    pieces = [system_part]
    if style_hint:
        pieces.append(style_hint)
    pieces.append(f"Question: {user_query}")
    return "\n".join(pieces)


def reset_for_tests() -> None:
    """Test hook — clear the singleton so a fresh YAMLBridge is built
    on the next call. Production code should not call this."""
    global _yaml_bridge_singleton, _yaml_bridge_unavailable
    with _yaml_bridge_lock:
        _yaml_bridge_singleton = None
        _yaml_bridge_unavailable = False
