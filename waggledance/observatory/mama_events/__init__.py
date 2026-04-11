# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Mama Event Observatory — measurement framework for candidate
proto-social / caregiver-binding emergence events.

This package intentionally does NOT claim consciousness. It only
produces reproducible measurements, scored candidates, and replayable
evidence that humans can audit.

Modules are loaded lazily via :func:`__getattr__` so partial builds
(for example during testing of a single layer) work without dragging
the whole framework in.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_EXPORTS = {
    # taxonomy
    "EventType": "taxonomy",
    "MamaCandidateEvent": "taxonomy",
    "UtteranceKind": "taxonomy",
    "redact_text": "taxonomy",
    "DEFAULT_TARGET_TOKENS": "taxonomy",
    # scoring
    "ScoreBand": "scoring",
    "ScoreBreakdown": "scoring",
    "classify": "scoring",
    "score_event": "scoring",
    # contamination
    "ContaminationReport": "contamination",
    "ContaminationGuard": "contamination",
    "ContaminationReason": "contamination",
    # self state
    "SelfState": "self_state",
    "SelfStateUpdater": "self_state",
    # caregiver binding
    "CaregiverBindingTracker": "caregiver_binding",
    "CaregiverProfile": "caregiver_binding",
    # consolidation
    "EpisodicRecord": "consolidation",
    "EpisodicStore": "consolidation",
    # observer + gate
    "MamaEventObserver": "observer",
    "GateVerdict": "gate",
    "render_gate_verdict": "gate",
    # ablations
    "AblationConfig": "ablations",
    "AblationMatrix": "ablations",
    "AblationRun": "ablations",
    "DEFAULT_ABLATIONS": "ablations",
    "canonical_event_sequence": "ablations",
    "run_ablation_matrix": "ablations",
    # reports
    "render_framework_report": "reports",
    "render_baseline_report": "reports",
    "render_ablations_report": "reports",
    "render_candidates_report": "reports",
    "render_gate_report": "reports",
    "collect_candidate_rows": "reports",
    "assert_no_hype": "reports",
}

__all__ = list(_LAZY_EXPORTS.keys())


def __getattr__(name: str) -> Any:
    """Resolve public names on first access.

    Raises :class:`AttributeError` for unknown names, matching the
    interpreter's expectation for ``from pkg import X``.
    """

    try:
        submod = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    module = importlib.import_module(f"{__name__}.{submod}")
    value = getattr(module, name)
    globals()[name] = value  # cache for next access
    return value
