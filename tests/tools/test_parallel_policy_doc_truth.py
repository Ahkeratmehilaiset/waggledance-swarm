# SPDX-License-Identifier: Apache-2.0
"""The boot copy of the parallel policy must match CLAUDE.md Rule 12 and the gate (2026-10-29).

ops/windows/reboot/WD_SWARM_PARALLEL_POLICY_V1.md is the boot copy of the
operator's 2026-10-06 directives. Its items 1, 2 and 5 drifted from the text
that governs them: CLAUDE.md Rule 12 (two distinct eligible recognized RCOs,
otherwise one, a Grok fallback for the RCO slot only when none; roster with
claude-rco-3) and the gate source (a dormant opt-in Grok RCO slot behind
``grok_fallback``, default off; any other Grok fill report-only). The document
is byte-pinned by sha256 in wd-fleet.json and wd_supervisor_loop.json and the
launchers refuse a mismatch, so this test also pins that both manifests carry
the hash of the document as committed. It reads files only.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
REBOOT = REPO_ROOT / "ops" / "windows" / "reboot"
POLICY = REBOOT / "WD_SWARM_PARALLEL_POLICY_V1.md"
GATE = REPO_ROOT / "tools" / "idle_consensus_auto_merge.py"
MANIFESTS = (REBOOT / "wd-fleet.json", REBOOT / "wd_supervisor_loop.json")


def _policy() -> str:
    # The document wraps at 80 columns; compare phrases on whitespace-normalized text.
    return re.sub(r"\s+", " ", POLICY.read_text(encoding="utf-8"))


def test_policy_item1_states_rule12_rco_cardinality_and_full_roster() -> None:
    text = _policy()
    assert "every eligible recognized RCO" not in text
    assert "two distinct recognized RCOs when at least two are eligible" in text
    assert "`claude-rco-3`" in text


def test_policy_item2_describes_the_gate_as_implemented() -> None:
    text = _policy()
    gate = GATE.read_text(encoding="utf-8")
    assert "has no Grok slot" not in text
    assert "`grok_fallback`" in text
    assert "report-only" in text
    assert "grok_fallback: bool = False" in gate
    assert "Grok may hold only the RCO slot here" in gate


def test_policy_item5_keeps_grok_outside_the_gate() -> None:
    text = _policy()
    assert "Grok is never a gate" in text
    assert "never posts an `RCO_PASS` event" in text


def test_both_manifests_pin_the_committed_policy_bytes() -> None:
    digest = hashlib.sha256(POLICY.read_bytes()).hexdigest().upper()
    for manifest in MANIFESTS:
        parallel = json.loads(manifest.read_text(encoding="utf-8"))["parallel_policy"]
        assert parallel["relative_path"] == "WD_SWARM_PARALLEL_POLICY_V1.md"
        assert parallel["sha256"] == digest, manifest.name
