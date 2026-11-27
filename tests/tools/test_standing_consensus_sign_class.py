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
from test_bridge_rco_enrollment import install_synthetic_enrollment

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
    "tools/check_promotion_eligible.py",
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
    "tests/tools/test_check_promotion_eligible.py",
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
        "author_agent": "codex-lead-1",
        "identities": {
            "build_lead": {"agent": "codex-lead-1", "approved": lead},
            "build_tools": {"agent": "codex-tools-1", "approved": tools},
            "rco": {"by_agent": {
                "claude-rco-1": {"approved": rco1, "eligible": True},
                "claude-rco-2": {"approved": rco2, "eligible": True},
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


@pytest.mark.parametrize("pair", [
    ("claude-rco-1", "claude-rco-2"),
    ("claude-rco-1", "claude-rco-3"),
    ("claude-rco-2", "claude-rco-3"),
])
def test_dual_quorum_is_any_two_distinct_eligible_recognized_rcos(pair, tmp_path, monkeypatch):
    proof = install_synthetic_enrollment(tmp_path, monkeypatch, provisioned=True)
    bc = _bc(rco1=False, rco2=False)
    bc["author_agent"] = "codex-lead-1"
    bc["identities"]["rco"]["by_agent"] = {
        agent: {"agent": agent, "approved": agent in pair, "eligible": True}
        for agent in ("claude-rco-1", "claude-rco-2", "claude-rco-3")
    }
    bc["identities"]["rco"]["by_agent"]["claude-rco-3"]["enrollment_event"] = proof
    out = _eval(bridge_consensus=bc)
    assert out["admitted"] is True
    assert out["basis"]["dual_rco"] == list(pair)


def test_dual_quorum_never_counts_the_author():
    bc = _bc()
    bc["author_agent"] = "claude-rco-1"
    assert _eval(bridge_consensus=bc)["admitted"] is False


def test_dual_quorum_never_counts_an_ineligible_identity():
    bc = _bc()
    bc["author_agent"] = "codex-lead-1"
    bc["identities"]["rco"]["by_agent"]["claude-rco-2"]["eligible"] = False
    assert _eval(bridge_consensus=bc)["admitted"] is False


@pytest.mark.parametrize("author", [None, "", " ", "UNKNOWN", 7])
def test_dual_quorum_requires_a_known_canonical_author(author):
    bc = _bc()
    bc["author_agent"] = author
    assert _eval(bridge_consensus=bc)["admitted"] is False


@pytest.mark.parametrize("missing", [True, False])
def test_dual_quorum_requires_explicit_positive_eligibility(missing):
    bc = _bc()
    reviewer = bc["identities"]["rco"]["by_agent"]["claude-rco-2"]
    if missing:
        reviewer.pop("eligible")
    else:
        reviewer["eligible"] = None
    assert _eval(bridge_consensus=bc)["admitted"] is False


def test_dual_quorum_deduplicates_configured_names_and_rejects_unknown_holder():
    assert _eval(recognized_rcos=("claude-rco-1", "claude-rco-1"))["admitted"] is False
    bc = _bc(rco2=False)
    bc["identities"]["rco"]["by_agent"]["claude-rco-4"] = {"approved": True}
    assert _eval(bridge_consensus=bc)["admitted"] is False


def test_third_recognized_rco_veto_outranks_the_other_two_passes():
    assert _eval(bridge_consensus=_bc(blocking=["claude-rco-3"]))["admitted"] is False


def test_unenrolled_third_rco_cannot_replace_a_real_standing_voice():
    bc = _bc(rco2=False)
    bc["identities"]["rco"]["by_agent"]["claude-rco-3"] = {
        "approved": True, "eligible": True, "enrollment_verified": True,
    }
    # A field in a caller report is not operator-anchored actual enrollment.
    assert _eval(bridge_consensus=bc)["admitted"] is False


# --- Rule 14 Mind-class path list: mechanical, separate from classify_ab -----
import os as _os
import re as _re
import subprocess as _subprocess
from pathlib import Path as _Path

from tools.check_standing_consensus_sign_class import (
    MIND_CLASS_PATTERNS,
    classify_mind_class,
)

MIND_CLASS_EXAMPLES = {
    "sandbox/mind/**": ["sandbox/mind/reader.py", "sandbox/mind/deep/er/index.json"],
    "configs/mind/**": ["configs/mind/reader.json", "configs/mind/a/b.yaml"],
    "tools/wd_q4_*": ["tools/wd_q4_transport.py"],
    "tests/tools/test_wd_q4_*": ["tests/tools/test_wd_q4_transport.py"],
    "ops/windows/reboot/Get-WdQ4*.ps1": ["ops/windows/reboot/Get-WdQ4Status.ps1"],
    "ops/windows/reboot/Invoke-WdQ4*.ps1": ["ops/windows/reboot/Invoke-WdQ4Read.ps1"],
    "tools/wd_mind_*": ["tools/wd_mind_reader.py"],
    "tools/wd_bridge_mind*": ["tools/wd_bridge_mind_session.py"],
    "tools/bridge_mind_workspace.py": ["tools/bridge_mind_workspace.py"],
    "tests/tools/test_wd_mind_*": ["tests/tools/test_wd_mind_reader.py"],
    "tests/tools/test_wd_bridge_mind*": ["tests/tools/test_wd_bridge_mind_session.py"],
    "tests/tools/test_mind_*": ["tests/tools/test_mind_reader.py"],
    "tests/tools/test_bridge_mind_workspace.py": ["tests/tools/test_bridge_mind_workspace.py"],
    "tests/windows/wd_mind_*": ["tests/windows/wd_mind_startup.Tests.ps1"],
    "ops/windows/reboot/Get-WdMind*.ps1": ["ops/windows/reboot/Get-WdMindStatus.ps1"],
    "ops/windows/reboot/Invoke-WdMind*.ps1": ["ops/windows/reboot/Invoke-WdMindRead.ps1"],
    "ops/windows/reboot/Start-WdMind*.ps1": ["ops/windows/reboot/Start-WdMindReader.ps1"],
}


def _mind_git_diff(*paths: str, renames: tuple[tuple[str, str], ...] = ()) -> str:
    text = "".join(
        f"diff --git a/{p} b/{p}\nindex 1111111..2222222 100644\n"
        f"--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new\n"
        for p in paths
    )
    return text + "".join(
        f"diff --git a/{old} b/{new}\nsimilarity index 90%\n"
        f"rename from {old}\nrename to {new}\nindex 1111111..2222222 100644\n"
        f"--- a/{old}\n+++ b/{new}\n@@ -1 +1 @@\n-old\n+new\n"
        for old, new in renames
    )


def test_mind_class_code_list_equals_the_documented_rule14_list() -> None:
    text = (_Path(__file__).resolve().parents[2] / "CLAUDE.md").read_text(encoding="utf-8")
    start = text.index("**Mind-class** = a change whose every changed path")
    end = text.index("The patterns added on 2026-11-14 take", start)
    documented = _re.findall(r"`([^`]+)`", text[start:end])
    assert documented == list(MIND_CLASS_PATTERNS)
    assert set(MIND_CLASS_EXAMPLES) == set(MIND_CLASS_PATTERNS)


@pytest.mark.parametrize(
    ("pattern", "path"),
    [(pattern, path) for pattern, paths in MIND_CLASS_EXAMPLES.items() for path in paths],
)
def test_mind_class_admits_every_documented_whole_file_pattern(pattern: str, path: str) -> None:
    report = classify_mind_class([path])
    assert report["mind_class"] is True, report
    assert report["matches"] == [{"path": path, "pattern": pattern}]
    assert report["refused"] == []
    assert report["diff_checked"] is False


def test_mind_class_whole_diff_of_many_patterns_is_admitted_with_matching_diff() -> None:
    paths = sorted(path for paths in MIND_CLASS_EXAMPLES.values() for path in paths)
    report = classify_mind_class(paths, _mind_git_diff(*paths))
    assert report["mind_class"] is True, report
    assert report["diff_checked"] is True
    assert [row["path"] for row in report["matches"]] == paths


MIND_CLASS_REFUSED_PATHS = [
    # (a)-class, governance, gate code and Stage-2 under a Mind spelling
    "CLAUDE.md",
    ".agent-bridge/bin/Get-WdMindStatus.ps1",
    "tools/wd_mind_idle_consensus_auto_merge.py",
    "sandbox/mind/check_rco_pass_present.py",
    "sandbox/mind/merge_with_bridge_receipt.py",
    "tests/tools/test_wd_mind_conformance.py",
    "tools/wd_mind_token_budget.py",
    "configs/mind/credential_store.json",
    "tools/wd_mind_stage2_cutover.py",
    "sandbox/mind/atomic_flip.py",
    # multi-purpose launcher and installer files are never Mind-class
    "ops/windows/reboot/start-wd-agent.ps1",
    "ops/windows/reboot/start-wd-tools-consumer.ps1",
    "ops/windows/reboot/Deploy-WdRebootBundle.ps1",
    "ops/windows/reboot/bridge-code-files.json",
    "ops/windows/reboot/Get-WdFleetStatus.ps1",
    # near misses of a documented pattern
    "tools/idle_daily_summary.py",
    "tools/wd_mind_dir/reader.py",
    "tools/sub/wd_mind_reader.py",
    "ops/windows/reboot/sub/Get-WdMindStatus.ps1",
    "ops/windows/reboot/Get-WdMindStatus.ps1.bak",
    "ops/windows/reboot/Get-WdMindStatus.psm1",
    "sandbox/mind",
    "sandbox/minds/reader.py",
    "tests/tools/test_minder.py",
    # spellings that are never normalised into an allowed path
    "Tools/wd_mind_reader.py",
    "tools/WD_MIND_reader.py",
    "SANDBOX/mind/reader.py",
    "ops/windows/reboot/get-wdmindstatus.ps1",
    "tools" + chr(92) + "wd_mind_reader.py",
    "/tools/wd_mind_reader.py",
    "C:/repo/tools/wd_mind_reader.py",
    "./tools/wd_mind_reader.py",
    "tools/./wd_mind_reader.py",
    "tools/../tools/wd_mind_reader.py",
    "sandbox/mind/../../CLAUDE.md",
    "tools//wd_mind_reader.py",
    " tools/wd_mind_reader.py",
    "tools/wd_mind_reader.py ",
    "tools/wd_mind_reader.py.",
    "sandbox/mind/",
    "tools/wd_mind_reader.py:stream",
    "sandbox/mind/reader~1.py",
    "sandbox/mind/nul.txt",
    "sandbox/mind/COM1",
    "sandbox/mind/caf\u00e9.py",
    "sandbox/mind/reader.py\n",
    "sandbox/mind/a b.py",
    "",
    None,
    7,
]


@pytest.mark.parametrize("path", MIND_CLASS_REFUSED_PATHS)
def test_mind_class_refuses_other_paths_and_spellings(path: object) -> None:
    alone = classify_mind_class([path])
    assert alone["mind_class"] is False, alone
    assert alone["matches"] == [] and len(alone["refused"]) == 1
    # one such path makes a mixed diff: never Mind-class
    mixed = classify_mind_class(["tools/wd_mind_reader.py", path])
    assert mixed["mind_class"] is False, mixed


@pytest.mark.parametrize(
    "changed_paths",
    [[], (), None, "tools/wd_mind_reader.py", {"tools/wd_mind_reader.py"}, 7,
     ["sandbox/mind/reader.py", "sandbox/mind/reader.py"],
     ["sandbox/mind/Reader.py", "sandbox/mind/reader.py"]],
)
def test_mind_class_refuses_empty_malformed_repeated_or_aliased_lists(changed_paths: object) -> None:
    report = classify_mind_class(changed_paths)
    assert report["mind_class"] is False, report
    assert report["reason"]


MIND_PATH_FOR_AB = "tools/wd_mind_reader.py"


def test_classify_ab_reports_a_wholly_mind_class_list_as_mind() -> None:
    # Rule 14a: a wholly Mind-class change is never (a)-class; classify_ab says so itself.
    for paths in MIND_CLASS_EXAMPLES.values():
        report = classify_ab(paths)
        assert report["ab_class"] == "mind", report
        assert report["mind_class"] is True
        assert report["a_hits"] == [] and report["unrecognized"] == []
        assert [row["path"] for row in report["mind_matches"]] == list(paths)
        assert "Rule 14a" in report["reason"]
    everything = [path for paths in MIND_CLASS_EXAMPLES.values() for path in paths]
    assert classify_ab(everything)["ab_class"] == "mind"


@pytest.mark.parametrize(
    "paths,a_hits,unrecognized",
    [
        ([MIND_PATH_FOR_AB, "tools/idle_daily_summary.py"], [],
         ["tools/idle_daily_summary.py", MIND_PATH_FOR_AB]),                 # mixed with an ordinary path
        ([MIND_PATH_FOR_AB, "CLAUDE.md"], ["claude.md"], [MIND_PATH_FOR_AB]),  # mixed with governance
        ([MIND_PATH_FOR_AB, "docs/runs/board.md"], [], [MIND_PATH_FOR_AB]),    # mixed with a (b) path
        (["tools/wd_mind_token_store.py"], ["tools/wd_mind_token_store.py"], []),   # protected name
        (["sandbox/mind/stage2_cutover.py"], ["sandbox/mind/stage2_cutover.py"], []),
        ([MIND_PATH_FOR_AB, "ops/windows/reboot/start-wd-agent.ps1"], [],
         ["ops/windows/reboot/start-wd-agent.ps1", MIND_PATH_FOR_AB]),       # launcher file
        (["Tools/wd_mind_reader.py"], [], ["tools/wd_mind_reader.py"]),      # wrong case is not Mind
    ],
)
def test_classify_ab_keeps_its_prior_result_for_mixed_and_a_class_lists(
    paths: list[str], a_hits: list[str], unrecognized: list[str],
) -> None:
    report = classify_ab(paths)
    assert report["ab_class"] == "a", report
    assert report["mind_class"] is False
    assert report["a_hits"] == a_hits
    assert sorted(report["unrecognized"]) == sorted(unrecognized)
    assert "mind_matches" not in report


def test_classify_ab_results_outside_mind_class_only_gain_the_mind_class_key() -> None:
    assert classify_ab([]) == {
        "ab_class": "a", "a_hits": [], "unrecognized": [], "mind_class": False,
        "reason": "no changed paths -> operator-explicit (fail-closed)",
    }
    assert classify_ab(["tools/run_hex_readiness_proof.py", "docs/runs/board.md"]) == {
        "ab_class": "b", "a_hits": [], "unrecognized": [], "mind_class": False,
        "reason": "all changed paths are recognized reversible (b)-class artifacts",
    }
    report = classify_ab(["tools/idle_daily_summary.py"])
    assert set(report) == {"ab_class", "a_hits", "unrecognized", "mind_class", "reason"}
    assert report["ab_class"] == "a" and report["mind_class"] is False


def test_standing_sign_never_admits_a_mind_class_list() -> None:
    # The standing sign reads only "b" as admissible; Mind-class has its own lane.
    report = _eval(changed_paths=[MIND_PATH_FOR_AB, "sandbox/mind/notes.md"])
    assert report["admitted"] is False
    assert report["ab_class"] == "mind"
    assert any("Rule 14a" in reason for reason in report["reasons"])


def test_mind_class_rename_needs_both_endpoints_to_be_mind_class() -> None:
    old, new, other = "tools/wd_mind_old.py", "tools/wd_mind_new.py", "tools/idle_daily_summary.py"
    inside = classify_mind_class([new, old], _mind_git_diff(renames=((old, new),)))
    assert inside["mind_class"] is True, inside
    # a rename out of non-Mind code: the listed source refuses by its path ...
    listed = classify_mind_class([other, new], _mind_git_diff(renames=((other, new),)))
    assert listed["mind_class"] is False
    # ... and a path list that leaves the source out disagrees with the diff headers
    hidden = classify_mind_class([new], _mind_git_diff(renames=((other, new),)))
    assert hidden["mind_class"] is False
    assert hidden["diff_checked"] is False
    assert other in hidden["only_in_diff"]


MIND_PATH = "tools/wd_mind_reader.py"


@pytest.mark.parametrize(
    "diff_text",
    [
        "",                                                     # no git file header at all
        "+ def helper():\n+     return 1\n",                    # content without headers
        _mind_git_diff(MIND_PATH, "tools/idle_daily_summary.py"),   # an extra file
        _mind_git_diff("sandbox/mind/other.py"),                # another file
        f'diff --git "a/{MIND_PATH}" "b/{MIND_PATH}"\n',        # quoted header
        f"diff --git a/tools/x.py b/{MIND_PATH}\n",             # rename header without from/to
        f"diff --git a/tools/x.py b/{MIND_PATH}\nrename to {MIND_PATH}\n",
        f"diff --git a/tools/x.py b/{MIND_PATH}\nrename from tools/y.py\nrename to {MIND_PATH}\n",
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\nnew file mode 120000\n",    # symlink
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\nold mode 100644\nnew mode 120000\n",
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\nindex 1111111..2222222 120000\n",
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\nnew file mode 160000\n",    # submodule
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\ndeleted file mode 120000\n",
        _mind_git_diff(MIND_PATH)[:-1],                         # no final newline
        _mind_git_diff(MIND_PATH)[:-5],                         # cut inside the last line
        "note\n" + _mind_git_diff(MIND_PATH),                   # text before the first header
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\n--- a/{MIND_PATH}\n+++ b/{MIND_PATH}\n"
        "@@ -1,3 +1,3 @@\n context\n-old\n",                    # cut inside a hunk
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\n--- a/{MIND_PATH}\n+++ b/{MIND_PATH}\n"
        "@@ -1 +1 @@\n-old\n+new\n+one line too many\n",        # more lines than declared
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\n--- a/{MIND_PATH}\n+++ b/{MIND_PATH}\n"
        "@@ -1,2 +1,2 @@\n-old\n+new\n"
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\n",            # next header arrives inside a hunk
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\n--- a/{MIND_PATH}\n+++ b/{MIND_PATH}\n"
        "@@ -1,2 +1,2 @@\n-old\n\n+new\n",                      # hunk line without a marker
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\nGIT binary patch\nliteral 3\nabc\n",
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\n+content without a hunk header\n",
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\n@@ -1 +1 @@ x\n-old\n+new\n@@ broken @@\n",
        f"diff --git a/tools/wd_mind_old.py b/{MIND_PATH}\n@@ -1 +1 @@\n-old\n+new\n"
        f"rename from tools/wd_mind_old.py\nrename to {MIND_PATH}\n",   # hunk before from/to
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\n",            # bare header, nothing after it
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\nindex 1111111..2222222 100644\n",   # change, no hunk
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\nindex 1111111..2222222 100644\n"
        f"--- a/{MIND_PATH}\n+++ b/{MIND_PATH}\n",              # sides without a hunk
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\nindex 1111111..2222222 100644\n--- a/{MIND_PATH}\n",
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\nnew file mode 100644\n",   # added file, no index
        f"diff --git a/{MIND_PATH} b/{MIND_PATH}\nindex 1111111..222 100644\n",
        f"diff --git a/tools/wd_mind_old.py b/{MIND_PATH}\nsimilarity index 90%\n"
        f"rename from tools/wd_mind_old.py\nrename to {MIND_PATH}\n",    # inexact rename, no content
        b"diff --git a/x b/x\n",
        7,
    ],
)
def test_mind_class_refuses_a_diff_that_does_not_name_exactly_the_changed_paths(diff_text: object) -> None:
    report = classify_mind_class([MIND_PATH], diff_text)
    assert report["mind_class"] is False, report
    assert report["diff_checked"] is False
    assert report["reason"]


@pytest.mark.parametrize("verb", ["rename", "copy"])
def test_mind_class_refuses_an_exact_rename_or_copy_because_it_shows_no_file_mode(verb: str) -> None:
    old, new = "tools/wd_mind_old.py", "tools/wd_mind_new.py"
    exact = f"diff --git a/{old} b/{new}\nsimilarity index 100%\n{verb} from {old}\n{verb} to {new}\n"
    report = classify_mind_class([new, old], exact)
    assert report["mind_class"] is False, report
    assert report["diff_checked"] is False
    assert "regular-file mode" in report["reason"]
    # the same move with a shown mode and its content change is readable and stays Mind-class
    shown = classify_mind_class([new, old], _mind_git_diff(renames=((old, new),)))
    assert shown["mind_class"] is True, shown


_MIND_SIDES = f"--- a/{MIND_PATH}\n+++ b/{MIND_PATH}\n@@ -1 +1 @@\n-old\n+new\n"


@pytest.mark.parametrize(
    "entry",
    [
        _MIND_SIDES,                                                  # no index line: no mode shown
        "index 1111111..2222222\n" + _MIND_SIDES,                     # index line without a mode
        "old mode 100644\nindex 1111111..2222222\n" + _MIND_SIDES,    # one mode line of the pair
        "new mode 100755\nindex 1111111..2222222\n" + _MIND_SIDES,
        "old mode 100644\nold mode 100644\nnew mode 100755\n",        # repeated mode line
        "old mode 120000\nnew mode 100644\n",                         # symlink on the old side
        "old mode 100644\nnew mode 160000\n",                         # submodule on the new side
        "old mode 100644\nnew mode 100600\n",                         # a mode git never records
        "new file mode 100644\nnew file mode 100644\nindex 0000000..e69de29\n",   # repeated
        "index 1111111..2222222 040000\n" + _MIND_SIDES,              # a tree mode
        "old mode 1006444\nnew mode 100755\n",                        # unreadable mode
    ],
)
def test_mind_class_refuses_an_entry_without_complete_regular_mode_evidence(entry: str) -> None:
    report = classify_mind_class([MIND_PATH], f"diff --git a/{MIND_PATH} b/{MIND_PATH}\n" + entry)
    assert report["mind_class"] is False, report
    assert report["diff_checked"] is False
    assert "the diff cannot confirm the changed paths" in report["reason"]


@pytest.mark.parametrize(
    "entry",
    [
        "index 1111111..2222222 100644\n" + _MIND_SIDES,
        "index 1111111..2222222 100755\n" + _MIND_SIDES,
        "old mode 100644\nnew mode 100755\n",
        "old mode 100755\nnew mode 100644\nindex 1111111..2222222\n" + _MIND_SIDES,
    ],
)
def test_mind_class_admits_an_entry_with_complete_regular_mode_evidence(entry: str) -> None:
    report = classify_mind_class([MIND_PATH], f"diff --git a/{MIND_PATH} b/{MIND_PATH}\n" + entry)
    assert report["mind_class"] is True, report
    assert report["diff_checked"] is True


def _real_git(repo: _Path, *args: str, stdin: bytes | None = None) -> str:
    env = dict(_os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=_os.devnull)
    done = _subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-C", str(repo), *args],
        input=stdin, capture_output=True, check=True, env=env,
    )
    return done.stdout.decode("utf-8")


def _real_git_tree(repo: _Path, entries: dict[str, tuple[str, str]]) -> str:
    _real_git(repo, "read-tree", "--empty")
    for path, (mode, blob) in sorted(entries.items()):
        _real_git(repo, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{path}")
    return _real_git(repo, "write-tree").strip()


def test_mind_class_reads_genuine_git_output_and_refuses_every_symlink_shape(tmp_path: _Path) -> None:
    """Entries are written straight into git objects (no working tree, no real link), then diffed by git itself."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _real_git(repo, "init", "--quiet")

    def blob(text: str) -> str:
        return _real_git(repo, "hash-object", "-w", "--stdin", stdin=text.encode("utf-8")).strip()

    body = "".join(f"line {number}\n" for number in range(40))
    code, edited = blob(body), blob(body.replace("line 7\n", "line seven\n"))
    link, relink = blob("wd_mind_target.py"), blob("../CLAUDE.md")
    old, new, keep = "tools/wd_mind_old.py", "tools/wd_mind_new.py", "sandbox/mind/keep.py"
    base = {keep: ("100644", code)}
    cases = [
        # name, before, after, changed paths as the pull request lists them, expected Mind-class
        ("regular edit", {old: ("100644", code)}, {old: ("100644", edited)}, [old], True),
        ("regular add", {}, {new: ("100644", code)}, [new], True),
        ("regular delete", {old: ("100644", code)}, {}, [old], True),
        ("mode change only", {old: ("100644", code)}, {old: ("100755", code)}, [old], True),
        ("mode change with edit", {old: ("100644", code)}, {old: ("100755", edited)}, [old], True),
        ("regular rename with edit", {old: ("100644", code)}, {new: ("100644", edited)}, [new, old], True),
        ("regular exact rename", {old: ("100644", code)}, {new: ("100644", code)}, [new, old], False),
        ("symlink exact rename", {old: ("120000", link)}, {new: ("120000", link)}, [new, old], False),
        ("symlink add", {}, {new: ("120000", link)}, [new], False),
        ("symlink delete", {old: ("120000", link)}, {}, [old], False),
        ("symlink retarget", {old: ("120000", link)}, {old: ("120000", relink)}, [old], False),
        ("regular file becomes symlink", {old: ("100644", code)}, {old: ("120000", link)}, [old], False),
        ("symlink becomes regular file", {old: ("120000", link)}, {old: ("100644", code)}, [old], False),
    ]
    for name, before, after, paths, expected in cases:
        left, right = _real_git_tree(repo, base | before), _real_git_tree(repo, base | after)
        diff = _real_git(repo, "diff-tree", "-r", "-M", "-p", left, right)
        assert diff.startswith("diff --git "), name
        report = classify_mind_class(paths, diff)
        assert report["mind_class"] is expected, (name, report["reason"], diff[:400])
        assert report["diff_checked"] is expected, name
        if name == "symlink exact rename":
            # the shape that shows no mode at all: nothing in the text tells it from a regular rename
            assert "similarity index 100%" in diff and "120000" not in diff and "mode" not in diff
            assert diff == _real_git(
                repo, "diff-tree", "-r", "-M", "-p",
                _real_git_tree(repo, base | {old: ("100644", code)}),
                _real_git_tree(repo, base | {new: ("100644", code)}),
            )
    # an exact copy has no mode line either
    left = _real_git_tree(repo, base)
    right = _real_git_tree(repo, base | {new: ("100644", code)})
    copied = _real_git(repo, "diff-tree", "-r", "-C", "--find-copies-harder", "-p", left, right)
    assert "copy from " + keep in copied
    assert classify_mind_class([keep, new], copied)["mind_class"] is False


def test_mind_class_diff_headers_ignore_hunk_content_and_cover_mode_and_binary_changes() -> None:
    paths = ["sandbox/mind/a.bin", "sandbox/mind/b.py", "sandbox/mind/c.sh"]
    diff = (
        "diff --git a/sandbox/mind/a.bin b/sandbox/mind/a.bin\nindex 1111111..2222222 100644\n"
        "Binary files a/sandbox/mind/a.bin and b/sandbox/mind/a.bin differ\n"
        "diff --git a/sandbox/mind/b.py b/sandbox/mind/b.py\nindex 1111111..2222222 100644\n"
        "--- a/sandbox/mind/b.py\n+++ b/sandbox/mind/b.py\n@@ -1,3 +1,3 @@\n"
        " diff --git a/tools/x.py b/tools/x.py\n-rename from tools/x.py\n+new file mode 120000\n"
        " index 1111111..2222222 120000\n"
        "diff --git a/sandbox/mind/c.sh b/sandbox/mind/c.sh\nold mode 100644\nnew mode 100755\n"
    )
    report = classify_mind_class(paths, diff)
    assert report["mind_class"] is True, report
    assert report["diff_checked"] is True


def test_mind_class_reads_every_ordinary_git_entry_shape_completely() -> None:
    names = ("new.py", "gone.py", "empty.py", "two.py", "eol.txt", "mode.sh")
    paths = [f"sandbox/mind/{name}" for name in names] + ["tools/wd_mind_new.py", "tools/wd_mind_old.py"]
    p = "sandbox/mind/"
    diff = (
        f"diff --git a/{p}new.py b/{p}new.py\nnew file mode 100644\nindex 0000000..2222222\n"
        f"--- /dev/null\n+++ b/{p}new.py\n@@ -0,0 +1,2 @@\n+one\n+two\n"
        f"diff --git a/{p}gone.py b/{p}gone.py\ndeleted file mode 100644\nindex 1111111..0000000\n"
        f"--- a/{p}gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-one\n-two\n"
        f"diff --git a/{p}empty.py b/{p}empty.py\nnew file mode 100644\nindex 0000000..e69de29\n"
        f"diff --git a/{p}two.py b/{p}two.py\nindex 1111111..2222222 100644\n"
        f"--- a/{p}two.py\n+++ b/{p}two.py\n@@ -1,2 +1,2 @@ def first():\n context\n-old\n+new\n"
        "@@ -10 +10,2 @@\n-diff --git a/CLAUDE.md b/CLAUDE.md\n+--- a/CLAUDE.md\n+@@ -1 +1 @@\n"
        f"diff --git a/{p}eol.txt b/{p}eol.txt\nindex 1111111..2222222 100644\n"
        f"--- a/{p}eol.txt\n+++ b/{p}eol.txt\n@@ -1 +1 @@\n-old\n\\ No newline at end of file\n"
        "+new\n\\ No newline at end of file\n"
        f"diff --git a/{p}mode.sh b/{p}mode.sh\nold mode 100644\nnew mode 100755\n"
        "diff --git a/tools/wd_mind_old.py b/tools/wd_mind_new.py\nsimilarity index 90%\n"
        "rename from tools/wd_mind_old.py\nrename to tools/wd_mind_new.py\nindex 1111111..2222222 100644\n"
        "--- a/tools/wd_mind_old.py\n+++ b/tools/wd_mind_new.py\n@@ -3 +3 @@\n-old\n+new\n"
    )
    report = classify_mind_class(paths, diff)
    assert report["mind_class"] is True, report
    assert report["diff_checked"] is True
    # The same text cut at any byte, with or without a closing newline, never passes.
    for cut in range(1, len(diff)):
        assert classify_mind_class(paths, diff[:cut])["mind_class"] is False, cut
        closed = classify_mind_class(paths, diff[:cut] + "\n")
        if closed["mind_class"] is True:
            # Only a cut inside the very last line keeps the same files and line counts.
            assert "\n" not in diff[cut:-1], cut
