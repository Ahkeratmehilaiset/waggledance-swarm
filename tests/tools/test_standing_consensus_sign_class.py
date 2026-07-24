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
from waggledance.core.idle_consensus_charter import evaluate_paths, load_charter


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
    "tools/bridge_event_writer.py",                   # canonical durable append boundary
    "tools/run_bridge_event_writer_proof.py",         # protected writer rename target
    ".agent-bridge/bin/Write-AgentEvent.ps1",
    "waggledance/adapters/http/routes/solvers.py",
    "waggledance/adapters/http/routes/auth_session.py",
    "waggledance/core/autonomy/scheduler.py",
    "waggledance/core/v3_13_0/air01_sensor_http_transport.py",
    "waggledance/core/v3_13_0/eng01_price_feed_http_transport.py",
    "waggledance/core/v3_13_0/ssrf_host_guard.py",
    "waggledance/core/v3_13_0/credential_vault.py",
    "waggledance/core/v3_13_0/secret_markers.py",
    "waggledance/core/v3_13_0/write_rco_gate.py",
    "waggledance/core/v3_13_0/solver_provenance.py",
    "waggledance/core/v3_13_0/sqlite_read_transport.py",
    "waggledance/core/v3_13_0/doc_ingest.py",
    "tests/tools/test_verify_bridge_consensus_conformance.py",
    "tests/tools/verify_bridge_consensus_conformance_corpus.json",
    "tests/tools/test_standing_consensus_sign_class.py",   # this very file (self)
    "tests/tools/test_bridge_event_writer.py",              # writer conformance anchor
    "tests/tools/test_cause_b_rco_finding_blocks_by_type.py",
    "tests/security/p4c_corpus/validate_p4c_corpus.py",    # validator anchor is (a)
    "configs/app_secret.json",
    "service/auth_token.py",
    "deploy/release.yaml",
    "some/brand/new/ungated_module.py",                    # UNRECOGNIZED -> (a)
    "tools/some_new_helper.py",                            # unrecognized tools/ -> (a)
    "tools/run_gate.py",                                   # run_* NOT proof/dry_run -> (a)
    "tools/run_consensus_verdict.py",                     # run_* gate-ish name -> (a)
    # Charter-denylisted gate/safety invariants must not be reclassified as (b)
    # by a local standing-sign pattern.
    "docs/architecture/P1_PROVEN_SAFE_AUTOSIGN_CLASS_V1.md",
    "docs/architecture/P1_PROVEN_SAFE_AUTOSIGN_CLASS_V2.md",
    "docs/architecture/BRIDGE_EVENT_GATE_TAXONOMY_V1.md",
    "docs/architecture/P3_CONTENT_IDENTICAL_REBASE_CARRYFORWARD_V1.md",
    "docs/architecture/P4_SAFETY_SUBSTRATE_RFC.md",
    "docs/architecture/P4_SAFETY_SUBSTRATE_NEXT.md",
    "docs/architecture/P4B_POST_MERGE_CANARY_V1.md",
    # forward-hardening (rco-2 fence notes): FUTURE governance-contract versions
    # and FUTURE gate-conformance anchors must not ride (b) via the broad doc/test
    # patterns.
    "docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V2.md",   # future contract -> (a)
    "docs/architecture/IDLE_AUTONOMY_CHARTER_V2.md",       # future charter -> (a)
    "docs/architecture/POLICY_SURFACE_V1.md",             # governance -> (a)
    "docs/architecture/MAGMA_SUBSTRATE_AUDIT_V2.md",      # future audit version -> (a)
    "tests/tools/test_some_new_gate_conformance.py",       # future conformance -> (a)
    "tests/tools/some_new_conformance_corpus.json",       # future corpus anchor -> (a)
    # STRUCTURAL fail-closed (tools/RCO #1423 fence): a NOVEL governance/gate doc
    # under docs/architecture that matches NO recognized spec family must -> (a),
    # never ride (b) by omission.
    "docs/architecture/gate_verdict_policy.md",
    "docs/architecture/merge_authority_contract.md",
    "docs/architecture/rco_pass_rules_v2.md",
    "docs/architecture/some_brand_new_design.md",
    "docs/security/new_gate_security_policy.md",           # non-(b) doc tree -> (a)
    "docs/plans/some_governance_plan.md",                 # non-(b) doc tree -> (a)
]

# --- (b)-class: recognized reversible artifacts that MAY ride standing-sign ---
B_CLASS_PATHS = [
    "tools/run_hex_subdivision_runtime_readiness_dry_run.py",
    "tools/run_hex_parent_child_ring_invariant_proof.py",
    "tools/run_some_offline_proof.py",
    "docs/runs/48h_hex_mesh_autonomy_sprint_board_20260627.md",
    "docs/benchmarks/local_ollama_model_sweep_2026.json",
    "waggledance/adapters/http/routes/air01_advisory.py",
    "waggledance/adapters/http/routes/eng01_advisory.py",
    "waggledance/adapters/http/routes/eng06_advisory.py",
    "waggledance/adapters/http/routes/advisory_dashboard.py",
    "waggledance/adapters/feeds/air01_advisory_refresher.py",
    "waggledance/adapters/feeds/eng01_advisory_refresher.py",
    "waggledance/adapters/feeds/eng06_advisory_refresher.py",
    "waggledance/adapters/feeds/advisory_refresh_ticker.py",
    "waggledance/adapters/cli/acct01_reconcile_bills.py",
    "waggledance/adapters/cli/air01_advisory.py",
    "waggledance/adapters/cli/email01_classify_inbox.py",
    "waggledance/adapters/cli/email02_index_vendor_emails.py",
    "waggledance/adapters/cli/eng01_recommend.py",
    "waggledance/adapters/cli/eng06_fireplace.py",
    "waggledance/adapters/cli/fin10_classify_receipts.py",
    "waggledance/adapters/cli/pdf01_extract_invoice.py",
    "waggledance/core/v3_13_0/acct01_unpaid_bill_reconciler.py",
    "waggledance/core/v3_13_0/air01_air_quality_advisor.py",
    "waggledance/core/v3_13_0/air01_digheran_adapter.py",
    "waggledance/core/v3_13_0/email01_inbox_priority_classifier.py",
    "waggledance/core/v3_13_0/email02_vendor_email_indexer.py",
    "waggledance/core/v3_13_0/eng01_advisory_card.py",
    "waggledance/core/v3_13_0/eng01_price_feed_response_parser.py",
    "waggledance/core/v3_13_0/eng01_spot_electricity.py",
    "waggledance/core/v3_13_0/eng06_advisory_card.py",
    "waggledance/core/v3_13_0/eng06_burn_log_adapter.py",
    "waggledance/core/v3_13_0/eng06_fireplace_advisor.py",
    "waggledance/core/v3_13_0/fin10_receipt_classifier.py",
    "waggledance/core/v3_13_0/pdf01_invoice_field_extractor.py",
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


def test_a_denylist_precedence_outranks_b_pattern():
    # docs/benchmarks/*.json is (b), but secret/token/credential paths stay (a)
    # if future edits accidentally reorder _path_is_a/_path_is_b checks.
    out = classify_ab(["docs/benchmarks/secret_token_probe.json"])
    assert out["ab_class"] == "a"
    assert out["a_hits"] == ["docs/benchmarks/secret_token_probe.json"]


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


def test_target_product_paths_full_best_consensus_is_admitted():
    out = _eval(changed_paths=[
        "waggledance/adapters/http/routes/eng01_advisory.py",
        "waggledance/adapters/http/routes/advisory_dashboard.py",
        "waggledance/adapters/feeds/eng01_advisory_refresher.py",
        "docs/benchmarks/local_ollama_model_sweep_2026.json",
    ])
    assert out["admitted"] is True
    assert out["ab_class"] == "b"


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


@pytest.mark.parametrize(
    "path",
    [
        "docs/architecture/P1_PROVEN_SAFE_AUTOSIGN_CLASS_V1.md",
        "docs/architecture/P4_SAFETY_SUBSTRATE_RFC.md",
        "docs/architecture/P4B_POST_MERGE_CANARY_V1.md",
        "tools/bridge_event_writer.py",
        "tests/tools/test_bridge_event_writer.py",
    ],
)
def test_charter_denylisted_invariants_never_admit(path):
    charter_decision = evaluate_paths(load_charter(), [path])
    assert charter_decision.reason == "denylist hit"
    out = _eval(changed_paths=[path])
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
