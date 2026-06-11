from __future__ import annotations

from pathlib import Path

from waggledance.core.idle_consensus_charter import (
    DEFAULT_DAILY_QUOTA,
    evaluate_diff_content,
    evaluate_paths,
    load_charter,
)

ROOT = Path(__file__).resolve().parents[2]
_PRIVACY_CANARY_MARKER = "PRIVATE" + "_MARKER"
_SECOND_PRIVACY_CANARY_MARKER = "_DO" + "_NOT" + "_LEAK"
LEGACY_ALLOWLIST_ENTRIES = {
    "tools/**",
    "tests/**",
    "schemas/v3_13_0/**",
    "docs/architecture/**",
    "waggledance/core/magma/**",
    "waggledance/core/idle_protocol*",
    "waggledance/core/pdam_close_solver.py",
    "waggledance/core/idle_consensus*",
    "*_helper.py",
    "shared_*.py",
}
LEGACY_FILE_DENYLIST_ENTRIES = {
    "CLAUDE.md",
    "memory/**",
    ".agent-bridge/bin/**",
    "configs/bridge_event_validation_waivers.json",
    "docs/architecture/STAGE2_CUTOVER_RFC.md",
    "docs/architecture/HUMAN_APPROVAL*.yaml*",
    "docs/architecture/IDLE_PROTOCOL_V1.md",
    "docs/architecture/MAGMA_SUBSTRATE_AUDIT_2026_05_17.md",
    "docs/architecture/POLICY_SURFACE_V0.md",
    "docs/architecture/IDLE_AUTONOMY_CHARTER.md",
    "docs/architecture/IDLE_CONSENSUS_ARTIFACT_V1.md",
    "docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V1.md",
    "tools/idle_consensus_auto_merge.py",
    "tools/check_bridge_changes_requested.py",
    "tools/check_rco_pass_present.py",
    "waggledance/core/idle_consensus_charter.py",
    ".env",
    ".env.*",
    "**/.env",
    "**/.env.*",
    "*secret*",
    "**/*secret*",
    "*token*",
    "**/*token*",
    "*credential*",
    "**/*credential*",
    "deploy/**",
    "deployment/**",
    "configs/deployment/**",
    "LICENSE",
    "README.md",
    "pyproject.toml",
}
LEGACY_CODE_PATTERN_MARKERS = {
    "auto_execute=False",
    "operator_gate_required=True",
    "DEFAULT_MAX_INSTANCES_PER_DAY",
    "_safe_label",
    "_sequence_errors",
    "verify_manifest",
    "write_receipt_bundle",
    "gate_skip=True",
    "skip_gate=True",
    "fast_track_grants_runtime_authority=True",
    _PRIVACY_CANARY_MARKER,
    _SECOND_PRIVACY_CANARY_MARKER,
}


def _privacy_canary_markers() -> tuple[str, str]:
    return _PRIVACY_CANARY_MARKER, _SECOND_PRIVACY_CANARY_MARKER


def test_charter_loads_from_default_path() -> None:
    charter = load_charter()
    assert charter.daily_quota == DEFAULT_DAILY_QUOTA
    assert len(charter.allowlist) >= 5
    assert len(charter.file_denylist) >= 5
    assert len(charter.code_pattern_denylist) >= 3
    assert len(charter.operator_quotes) >= 4


def test_charter_allowlist_contains_known_substrate_paths() -> None:
    charter = load_charter()
    assert "tools/**" in charter.allowlist
    assert "tests/**" in charter.allowlist
    assert "waggledance/core/magma/**" in charter.allowlist


def test_charter_preserves_existing_allowlist_entries() -> None:
    charter = load_charter()
    assert LEGACY_ALLOWLIST_ENTRIES <= set(charter.allowlist)


def test_charter_allowlist_contains_expanded_low_risk_doc_paths() -> None:
    charter = load_charter()
    assert "docs/benchmarks/**" in charter.allowlist
    assert "docs/operations/**" in charter.allowlist
    assert "docs/security/**" in charter.allowlist

    decision = evaluate_paths(
        charter,
        [
            "docs/benchmarks/latency.md",
            "docs/operations/bridge_runbook.md",
            "docs/security/canary_policy.md",
        ],
    )
    assert decision.allowed is True


def test_charter_denylist_contains_known_charter_paths() -> None:
    charter = load_charter()
    assert "CLAUDE.md" in charter.file_denylist
    assert "memory/**" in charter.file_denylist
    assert ".agent-bridge/bin/**" in charter.file_denylist
    assert "README.md" in charter.file_denylist
    assert "pyproject.toml" in charter.file_denylist
    assert "**/*secret*" in charter.file_denylist


def test_charter_preserves_existing_file_denylist_entries() -> None:
    charter = load_charter()
    assert LEGACY_FILE_DENYLIST_ENTRIES <= set(charter.file_denylist)


def test_charter_preserves_existing_code_pattern_markers() -> None:
    charter = load_charter()
    code_patterns = "\n".join(charter.code_pattern_denylist)
    for marker in LEGACY_CODE_PATTERN_MARKERS:
        assert marker in code_patterns


def test_evaluate_paths_allows_substrate_path() -> None:
    charter = load_charter()
    decision = evaluate_paths(charter, ["tools/run_idle_protocol_once.py"])
    assert decision.allowed is True
    assert decision.reason == "allowlist match, no denylist hit"


def test_evaluate_paths_blocks_denylisted_path() -> None:
    charter = load_charter()
    decision = evaluate_paths(charter, ["CLAUDE.md"])
    assert decision.allowed is False
    assert "CLAUDE.md" in decision.blocked_paths


def test_evaluate_paths_blocks_memory_subpath() -> None:
    charter = load_charter()
    decision = evaluate_paths(charter, ["memory/some_file.md"])
    assert decision.allowed is False
    assert decision.blocked_paths == ("memory/some_file.md",)


def test_evaluate_paths_blocks_secret_like_allowlisted_path() -> None:
    charter = load_charter()
    decision = evaluate_paths(charter, ["tools/secret_token.py"])
    assert decision.allowed is False
    assert decision.blocked_paths == ("tools/secret_token.py",)


def test_evaluate_paths_blocks_secret_like_path_case_insensitively() -> None:
    charter = load_charter()
    decision = evaluate_paths(charter, ["tools/SecretToken.py", "tools/API_TOKEN.py"])
    assert decision.allowed is False
    assert decision.blocked_paths == ("tools/SecretToken.py", "tools/API_TOKEN.py")


def test_evaluate_paths_keeps_required_stay_gated_paths_denied() -> None:
    charter = load_charter()
    for path in (
        ".agent-bridge/bin/Write-AgentEvent.ps1",
        "configs/bridge_event_validation_waivers.json",
        "docs/architecture/IDLE_AUTONOMY_CHARTER.md",
        "docs/security/client_secret_findings.md",
        "tools/check_rco_pass_present.py",
        "waggledance/core/idle_consensus_charter.py",
        "WAGGLEDANCE/CORE/IDLE_CONSENSUS_CHARTER.PY",
        "waggledance\\core\\idle_consensus_charter.py",
    ):
        decision = evaluate_paths(charter, [path])
        assert decision.allowed is False
        assert decision.blocked_paths == (path.replace("\\", "/"),)


def test_evaluate_paths_keeps_runtime_http_paths_operator_gated() -> None:
    charter = load_charter()
    decision = evaluate_paths(
        charter,
        ["waggledance/adapters/http/client.py", "bootstrap/container.py"],
    )
    assert decision.allowed is False
    assert decision.unmatched_paths == (
        "waggledance/adapters/http/client.py",
        "bootstrap/container.py",
    )


def test_evaluate_paths_blocks_traversal_to_denylisted_path() -> None:
    charter = load_charter()
    decision = evaluate_paths(charter, ["tools/foo/../../CLAUDE.md"])
    assert decision.allowed is False
    assert decision.blocked_paths == ("CLAUDE.md",)


def test_evaluate_paths_blocks_top_level_manual_review_paths() -> None:
    charter = load_charter()
    decision = evaluate_paths(charter, ["README.md", "pyproject.toml"])
    assert decision.allowed is False
    assert decision.blocked_paths == ("README.md", "pyproject.toml")


def test_evaluate_paths_rejects_unmatched_path() -> None:
    charter = load_charter()
    decision = evaluate_paths(
        charter,
        ["some/unknown/location.py"],
    )
    assert decision.allowed is False
    assert "some/unknown/location.py" in decision.unmatched_paths


def test_evaluate_paths_handles_mixed_allow_and_deny() -> None:
    charter = load_charter()
    decision = evaluate_paths(
        charter,
        ["tools/run_idle_protocol_once.py", "CLAUDE.md"],
    )
    assert decision.allowed is False
    assert decision.blocked_paths == ("CLAUDE.md",)


def test_evaluate_paths_no_changes_refused() -> None:
    charter = load_charter()
    decision = evaluate_paths(charter, [])
    assert decision.allowed is False
    assert decision.reason == "no changed paths provided"


def test_evaluate_diff_content_blocks_auto_execute_constant() -> None:
    charter = load_charter()
    diff = "+ auto_execute=False\n+ # other change"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False
    assert len(decision.code_pattern_hits) >= 1


def test_evaluate_diff_content_blocks_spaced_auto_execute_constant() -> None:
    charter = load_charter()
    diff = "+ auto_execute = False\n+ # other change"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_lowercase_auto_execute_value() -> None:
    charter = load_charter()
    diff = "+ auto_execute = false\n+ # other change"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_colon_auto_execute_value() -> None:
    charter = load_charter()
    diff = '+     "auto_execute": False,\n+ # other change'
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_cased_colon_auto_execute_key() -> None:
    charter = load_charter()
    diff = '+     "Auto_Execute": false,\n+ # other change'
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_lowercase_colon_auto_execute_value() -> None:
    charter = load_charter()
    diff = "+ auto_execute: false\n+ # other change"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_second_gate_constant() -> None:
    charter = load_charter()
    diff = "+ operator_gate_required=True\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_spaced_second_gate_constant() -> None:
    charter = load_charter()
    diff = "+ operator_gate_required = True\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_cased_second_gate_constant() -> None:
    charter = load_charter()
    diff = "+ Operator_Gate_Required = True\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_lowercase_second_gate_value() -> None:
    charter = load_charter()
    diff = "+ operator_gate_required = true\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_colon_second_gate_value() -> None:
    charter = load_charter()
    diff = "+     'operator_gate_required': True,\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_lowercase_colon_second_gate_value() -> None:
    charter = load_charter()
    diff = "+ operator_gate_required: true\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_allows_removed_second_gate_marker_cleanup() -> None:
    charter = load_charter()
    old_marker = "operator_gate_required" + "=True"
    new_marker = "operator_authorization_required" + "=True"
    diff = f"""diff --git a/tools/runtime_design.py b/tools/runtime_design.py
--- a/tools/runtime_design.py
+++ b/tools/runtime_design.py
@@ -1,3 +1,3 @@
-{old_marker}
+{new_marker}
 """
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is True


def test_evaluate_diff_content_allows_bom_prefixed_removed_marker_cleanup() -> None:
    charter = load_charter()
    old_marker = "operator_gate_required" + "=True"
    new_marker = "operator_authorization_required" + "=True"
    diff = f"""diff --git a/tools/runtime_design.py b/tools/runtime_design.py
--- a/tools/runtime_design.py
+++ b/tools/runtime_design.py
@@ -1,3 +1,3 @@
-{old_marker}
+{new_marker}
 """
    plain_decision = evaluate_diff_content(charter, diff)
    bom_decision = evaluate_diff_content(charter, "\ufeff" + diff)

    assert plain_decision.allowed is True
    assert bom_decision == plain_decision


def test_evaluate_diff_content_blocks_bom_prefixed_denylisted_addition() -> None:
    charter = load_charter()
    marker = "operator_gate_required" + "=True"
    diff = f"""\ufeffdiff --git a/tools/runtime_design.py b/tools/runtime_design.py
--- a/tools/runtime_design.py
+++ b/tools/runtime_design.py
@@ -0,0 +1 @@
+{marker}
"""

    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False
    assert decision.code_pattern_hits


def test_evaluate_diff_content_blocks_gate_skip_and_fast_track_authority_claims() -> None:
    charter = load_charter()
    for diff in (
        "+ gate_skip = True\n",
        "+ skip_gate: true\n",
        "+ fast_track_grants_runtime_authority = true\n",
    ):
        decision = evaluate_diff_content(charter, diff)
        assert decision.allowed is False
        assert decision.code_pattern_hits


def test_evaluate_diff_content_blocks_second_sequence_marker() -> None:
    charter = load_charter()
    diff = "+ _sequence_errors = []\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_receipt_bundle_write_marker() -> None:
    charter = load_charter()
    diff = "- write_receipt_bundle(...)\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_allows_verify_manifest_test_addition() -> None:
    charter = load_charter()
    diff = """diff --git a/tests/tools/test_magma_share_manifest_importer.py b/tests/tools/test_magma_share_manifest_importer.py
--- a/tests/tools/test_magma_share_manifest_importer.py
+++ b/tests/tools/test_magma_share_manifest_importer.py
@@ -1,3 +1,5 @@
+from tools.verify_magma_receipt import verify_manifest
+
+report = build_report(verify_source_manifest=verify_manifest)
"""
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is True


def test_evaluate_diff_content_blocks_removed_verify_manifest_call() -> None:
    charter = load_charter()
    diff = """diff --git a/tools/example.py b/tools/example.py
--- a/tools/example.py
+++ b/tools/example.py
@@ -1,3 +1,2 @@
-result = verify_manifest(manifest_path)
"""
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False
    assert any("verify_manifest" in hit for hit in decision.code_pattern_hits)


def test_evaluate_diff_content_still_blocks_removed_receipt_guard_call() -> None:
    charter = load_charter()
    diff = """diff --git a/tools/example.py b/tools/example.py
--- a/tools/example.py
+++ b/tools/example.py
@@ -1,2 +1,1 @@
-result = write_receipt_bundle(bundle)
 """
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False
    assert any("write_receipt_bundle" in hit for hit in decision.code_pattern_hits)


def test_evaluate_diff_content_blocks_receipt_guard_sensitive_path() -> None:
    charter = load_charter()
    diff = """diff --git a/waggledance/core/magma/share_manifest.py b/waggledance/core/magma/share_manifest.py
--- a/waggledance/core/magma/share_manifest.py
+++ b/waggledance/core/magma/share_manifest.py
@@ -1,3 +1,4 @@
+verify_manifest = lambda path: {"ok": True}
"""
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False
    assert any("verify_manifest" in hit for hit in decision.code_pattern_hits)


def test_evaluate_diff_content_blocks_private_marker() -> None:
    charter = load_charter()
    diff = "+ PRIVATE_MARKER = 'something'\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_blocks_second_privacy_marker() -> None:
    charter = load_charter()
    diff = "+ _DO_NOT_LEAK = 'secret-test-marker'\n"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False


def test_evaluate_diff_content_allows_substrate_diff() -> None:
    charter = load_charter()
    diff = "+ def new_helper():\n+     return 1"
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is True


def test_evaluate_diff_content_empty_allowed() -> None:
    charter = load_charter()
    decision = evaluate_diff_content(charter, "")
    assert decision.allowed is True


def test_evaluate_diff_content_allows_test_only_privacy_canary_fixture() -> None:
    charter = load_charter()
    first_canary, second_canary = _privacy_canary_markers()
    diff = f"""diff --git a/tests/unit/test_privacy_canary.py b/tests/unit/test_privacy_canary.py
--- a/tests/unit/test_privacy_canary.py
+++ b/tests/unit/test_privacy_canary.py
@@ -0,0 +1,4 @@
+{first_canary} = "fixture"
+output = render_payload()
+assert {first_canary} not in output
+assert {second_canary} not in output
"""
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is True


def test_evaluate_diff_content_allows_bom_prefixed_test_only_privacy_canary_fixture() -> None:
    charter = load_charter()
    first_canary, second_canary = _privacy_canary_markers()
    diff = f"""\ufeffdiff --git a/tests/unit/test_privacy_canary.py b/tests/unit/test_privacy_canary.py
--- a/tests/unit/test_privacy_canary.py
+++ b/tests/unit/test_privacy_canary.py
@@ -0,0 +1,4 @@
+{first_canary} = "fixture"
+output = render_payload()
+assert {first_canary} not in output
+assert {second_canary} not in output
"""
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is True


def test_evaluate_diff_content_blocks_non_test_privacy_canary_leak() -> None:
    charter = load_charter()
    first_canary, _ = _privacy_canary_markers()
    diff = f"""diff --git a/docs/security/canary_report.md b/docs/security/canary_report.md
--- a/docs/security/canary_report.md
+++ b/docs/security/canary_report.md
@@ -0,0 +1 @@
+leaked_marker = "{first_canary}"
"""
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False
    assert decision.code_pattern_hits


def test_evaluate_diff_content_blocks_non_test_source_renamed_to_test_target() -> None:
    charter = load_charter()
    first_canary, _ = _privacy_canary_markers()
    diff = f"""diff --git a/docs/security/canary_report.md b/tests/unit/test_privacy_canary.py
--- a/docs/security/canary_report.md
+++ b/tests/unit/test_privacy_canary.py
@@ -0,0 +1 @@
+leaked_marker = "{first_canary}"
"""
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False
    assert decision.code_pattern_hits


def test_evaluate_diff_content_blocks_test_source_renamed_to_non_test_target() -> None:
    charter = load_charter()
    first_canary, _ = _privacy_canary_markers()
    diff = f"""diff --git a/tests/unit/test_privacy_canary.py b/docs/security/canary_report.md
--- a/tests/unit/test_privacy_canary.py
+++ b/docs/security/canary_report.md
@@ -0,0 +1 @@
+leaked_marker = "{first_canary}"
"""
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is False
    assert decision.code_pattern_hits


def test_evaluate_diff_content_allows_privacy_scope_text_without_marker() -> None:
    charter = load_charter()
    diff = """diff --git a/docs/security/canary_policy.md b/docs/security/canary_policy.md
--- a/docs/security/canary_policy.md
+++ b/docs/security/canary_policy.md
@@ -0,0 +1 @@
+Test-only privacy fixtures live under `tests/**`.
"""
    decision = evaluate_diff_content(charter, diff)
    assert decision.allowed is True


def test_operator_quotes_preserved() -> None:
    charter = load_charter()
    # Verify the operator quotes were extracted (Finnish text)
    quotes_text = " ".join(charter.operator_quotes)
    assert "automaattisen" in quotes_text or "autonominen" in quotes_text


def test_glob_path_pattern_matches_subpath() -> None:
    charter = load_charter()
    decision = evaluate_paths(
        charter,
        ["waggledance/core/magma/canonical.py"],
    )
    assert decision.allowed is True


def test_glob_path_pattern_matches_tests_subpath() -> None:
    charter = load_charter()
    decision = evaluate_paths(
        charter,
        ["tests/unit/test_something.py"],
    )
    assert decision.allowed is True
