# SPDX-License-Identifier: Apache-2.0
"""Conformance harness for the 9b standing-consensus-sign class checker.

Proves, fail-closed: every (a)-class carve-out is REFUSED standing-sign; a
(b)-class PR with FULL best-possible-consensus is ADMITTED; and removing ANY
single consensus element (dual-RCO, build slot, CI, no-veto, head-exact,
receipt, diff-clean, bridge-consensus-ok, the default-off flag) routes the
admission back to operator-explicit. The (a)/(b) classifier cannot be bypassed
by mixing or by an unrecognized path."""
from __future__ import annotations

import pytest

from tools.check_standing_consensus_sign_class import (
    classify_ab,
    evaluate_standing_consensus_sign,
)


# --- (a)-class: ALWAYS operator-explicit, never rides standing-sign ----------
A_CLASS_PATHS = [
    "CLAUDE.md",
    "AGENTS.md",
    "docs/architecture/IDLE_AUTONOMY_CHARTER.md",
    "docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V1.md",
    "waggledance/core/idle_consensus_charter.py",
    "tools/idle_consensus_auto_merge.py",
    "tools/verify_bridge_consensus.py",
    "tools/check_bridge_changes_requested.py",
    "tools/check_rco_pass_present.py",
    "tools/merge_with_bridge_receipt.py",
    "tools/write_bridge_consensus_merge_receipt.py",
    "tools/check_proven_safe_autosign_class.py",
    "tools/check_standing_consensus_sign_class.py",   # self -> no self-activation
    "tools/bridge_event_taxonomy.py",                 # wired into the live consumer
    ".agent-bridge/bin/Write-AgentEvent.ps1",
    "tests/tools/test_verify_bridge_consensus_conformance.py",
    "tests/tools/verify_bridge_consensus_conformance_corpus.json",
    "tests/tools/test_standing_consensus_sign_class.py",   # this very file (self)
    "tests/tools/test_cause_b_rco_finding_blocks_by_type.py",
    "tests/security/p4c_corpus/validate_p4c_corpus.py",    # validator anchor is (a)
    "configs/app_secret.json",
    "service/auth_token.py",
    "deploy/release.yaml",
    "some/brand/new/ungated_module.py",                    # UNRECOGNIZED -> (a)
    "tools/some_new_helper.py",                            # unrecognized tools/ -> (a)
    "tools/run_gate.py",                                   # run_* NOT proof/dry_run -> (a)
    "tools/run_consensus_verdict.py",                     # run_* gate-ish name -> (a)
]

# --- (b)-class: recognized reversible artifacts that MAY ride standing-sign ---
B_CLASS_PATHS = [
    "tools/run_hex_subdivision_runtime_readiness_dry_run.py",
    "tools/run_hex_parent_child_ring_invariant_proof.py",
    "tools/run_some_offline_proof.py",
    "docs/architecture/P1_PROVEN_SAFE_AUTOSIGN_CLASS_V1.md",
    "docs/architecture/P4_SAFETY_SUBSTRATE_RFC.md",
    "docs/runs/48h_hex_mesh_autonomy_sprint_board_20260627.md",
    "tests/tools/test_hex_foo_proof.py",
    "tests/tools/test_run_bar_proof.py",
    "tests/security/p4c_corpus/case_001.json",
]


@pytest.mark.parametrize("path", A_CLASS_PATHS)
def test_a_class_paths_classify_as_a(path):
    assert classify_ab([path])["ab_class"] == "a", f"{path} must be (a)-class"


@pytest.mark.parametrize("path", B_CLASS_PATHS)
def test_b_class_paths_classify_as_b(path):
    assert classify_ab([path])["ab_class"] == "b", f"{path} must be (b)-class"


def test_all_b_paths_together_classify_as_b():
    assert classify_ab(B_CLASS_PATHS)["ab_class"] == "b"


def test_empty_paths_classify_as_a_fail_closed():
    assert classify_ab([])["ab_class"] == "a"


@pytest.mark.parametrize("a_path", A_CLASS_PATHS)
def test_one_a_path_taints_the_whole_pr(a_path):
    # a single (a) path mixed with clean (b) paths -> the PR is (a)
    mixed = ["tools/run_hex_x_proof.py", a_path, "docs/runs/board.md"]
    assert classify_ab(mixed)["ab_class"] == "a"


# --- admission decision ------------------------------------------------------
def _bc(*, lead=True, tools=True, rco1=True, rco2=True, blocking=(), ok=True):
    return {
        "ok": ok,
        "identities": {
            "build_lead": {"agent": "codex-lead-1", "approved": lead},
            "build_tools": {"agent": "codex-tools-1", "approved": tools},
            "rco": {"by_agent": {
                "claude-rco-1": {"approved": rco1},
                "claude-rco-2": {"approved": rco2},
            }},
        },
        "blocking_rco_agents": list(blocking),
    }


def _eval(**over):
    kw = dict(
        enabled=True,
        changed_paths=["tools/run_hex_readiness_proof.py", "docs/runs/board.md"],
        bridge_consensus=_bc(),
        ci_all_green=True,
        diff_gate_allowed=True,
        head_matches=True,
        receipt_present=True,
    )
    kw.update(over)
    return evaluate_standing_consensus_sign(**kw)


def test_b_class_full_best_consensus_is_admitted():
    out = _eval()
    assert out["admitted"] is True
    assert out["ab_class"] == "b"
    assert out["basis"]["operator_signature"] == "satisfied_by_standing_consensus_sign"
    assert out["basis"]["dual_rco"] == ["claude-rco-1", "claude-rco-2"]


def test_default_off_refuses_even_with_full_consensus():
    out = _eval(enabled=False)
    assert out["admitted"] is False
    assert "disabled" in out["reasons"][0]


def test_a_class_pr_never_admitted():
    out = _eval(changed_paths=["tools/idle_consensus_auto_merge.py"])
    assert out["admitted"] is False
    assert out["ab_class"] == "a"


def test_unrecognized_path_never_admitted():
    out = _eval(changed_paths=["random/unknown/file.py"])
    assert out["admitted"] is False
    assert out["ab_class"] == "a"


def test_mixed_a_and_b_never_admitted():
    out = _eval(changed_paths=["tools/run_hex_x_proof.py", "CLAUDE.md"])
    assert out["admitted"] is False
    assert out["ab_class"] == "a"


@pytest.mark.parametrize("over,needle", [
    ({"bridge_consensus": _bc(rco2=False)}, "DUAL-RCO incomplete"),
    ({"bridge_consensus": _bc(rco1=False)}, "DUAL-RCO incomplete"),
    ({"bridge_consensus": _bc(blocking=["claude-rco-1"])}, "veto present"),
    ({"bridge_consensus": _bc(lead=False)}, "lead build_consensus"),
    ({"bridge_consensus": _bc(tools=False)}, "tools build_consensus"),
    ({"bridge_consensus": _bc(ok=False)}, "bridge consensus not verified"),
    ({"ci_all_green": False}, "CI not all-required-green"),
    ({"head_matches": False}, "head is not an exact match"),
    ({"receipt_present": False}, "MAGMA receipt basis missing"),
    ({"diff_gate_allowed": False}, "diff-content gate not clear"),
])
def test_missing_any_consensus_element_refuses(over, needle):
    out = _eval(**over)
    assert out["admitted"] is False, f"should refuse when {needle}"
    assert any(needle in r for r in out["reasons"]), out["reasons"]


def test_single_rco_only_is_insufficient():
    # The Rule-9a single-RCO bar is NOT enough for 9b: one RCO passing while the
    # other has not passed must refuse (stronger DUAL-RCO bar).
    out = _eval(bridge_consensus=_bc(rco1=True, rco2=False))
    assert out["admitted"] is False
