# SPDX-License-Identifier: BUSL-1.1
"""Regression: the gate-policy / gate-ops-tooling class is OFF-ALLOWLIST by construction.

Added 2026-06-25 after the #1387 auto-merge-bypass (an allowlist-clean gate-policy
spec auto-merged without operator-sign because the free-text RCO safety-latch was
misclassified). These paths must route to operator-sign (evaluate_paths.allowed=False)
so an invariant-bearing PR can never autonomous-merge -- never reliant on a
classifier-readable latch. The denylist must stay NARROW: ordinary docs/architecture
design docs and ordinary tools must remain allowlist-clean.
"""
from __future__ import annotations

import pytest

from waggledance.core.idle_consensus_charter import evaluate_paths, load_charter


GATE_POLICY_PATHS = [
    "docs/architecture/P1_PROVEN_SAFE_AUTOSIGN_CLASS_V1.md",
    "docs/architecture/P1_PROVEN_SAFE_AUTOSIGN_CLASS_V2.md",  # future version via glob
    "docs/architecture/BRIDGE_EVENT_GATE_TAXONOMY_V1.md",
    "docs/architecture/BRIDGE_EVENT_GATE_TAXONOMY_V2.md",  # future via glob
    "tools/bridge_event_taxonomy.py",
    "tools/check_proven_safe_autosign_class.py",
    "tools/check_status_name_safe.py",
    "tools/auto_rollback_eligibility.py",
]

# Must STAY allowlist-clean (the denylist must not over-broaden onto ordinary work).
ALLOWLIST_CLEAN_PATHS = [
    "docs/architecture/WD_BRIDGE_THROUGHPUT_RESILIENCE_RFC.md",
    "docs/architecture/P4_SAFETY_SUBSTRATE_RFC.md",
    "docs/architecture/SOME_FUTURE_DESIGN.md",
    "tools/select_affected_tests.py",
    "tools/burn_governor.py",
]


@pytest.fixture(scope="module")
def charter():
    return load_charter()


@pytest.mark.parametrize("path", GATE_POLICY_PATHS)
def test_gate_policy_path_is_off_allowlist(charter, path):
    assert evaluate_paths(charter, [path]).allowed is False, path


@pytest.mark.parametrize("path", ALLOWLIST_CLEAN_PATHS)
def test_ordinary_path_stays_allowlist_clean(charter, path):
    assert evaluate_paths(charter, [path]).allowed is True, path
