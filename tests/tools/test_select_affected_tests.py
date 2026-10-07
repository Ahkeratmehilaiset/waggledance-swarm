# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.select_affected_tests import BRIDGE_EXPLICIT_TESTS, bridge_test_closure, select_affected_tests


def test_classifier_direct_runtime_consumers_are_explicitly_mapped():
    assert {
        "tests/tools/test_bridge_wake_continuity.py",
        "tests/tools/test_bridge_inbox_recovery.py",
        "tests/tools/test_wd_event_driven_wake.py",
        "tests/tools/test_wd_swarm_parallel_status.py",
    } <= BRIDGE_EXPLICIT_TESTS[".agent-bridge/bin/BridgeEventClassifier.ps1"]


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "select_affected_tests.py"


def _mkrepo(tmp_path: Path) -> Path:
    (tmp_path / "tests" / "tools").mkdir(parents=True)
    # A test that IMPORTS waggledance.x.alpha.
    (tmp_path / "tests" / "test_alpha.py").write_text(
        "from waggledance.x.alpha import thing\n\n\ndef test_a():\n    assert thing\n",
        encoding="utf-8",
    )
    # A convention test for tools/beta.py (no import; name carries the mapping).
    (tmp_path / "tests" / "tools" / "test_beta.py").write_text(
        "def test_b():\n    assert True\n", encoding="utf-8"
    )
    return tmp_path


# --- fail-safe to full suite -------------------------------------------------

def test_empty_input_forces_full(tmp_path):
    assert select_affected_tests([], tmp_path)["full_suite"] is True


def test_broad_impact_conftest_forces_full(tmp_path):
    _mkrepo(tmp_path)
    r = select_affected_tests(["tests/conftest.py"], tmp_path)
    assert r["full_suite"] is True
    assert "broad-impact" in r["reason"]


def test_pyproject_forces_full(tmp_path):
    _mkrepo(tmp_path)
    assert select_affected_tests(["pyproject.toml"], tmp_path)["full_suite"] is True


def test_charter_change_forces_full(tmp_path):
    _mkrepo(tmp_path)
    assert (
        select_affected_tests(
            ["waggledance/core/idle_consensus_charter.py"], tmp_path
        )["full_suite"]
        is True
    )


def test_init_change_forces_full(tmp_path):
    _mkrepo(tmp_path)
    assert (
        select_affected_tests(["waggledance/x/__init__.py"], tmp_path)["full_suite"]
        is True
    )


def test_source_with_no_test_fails_safe_to_full(tmp_path):
    _mkrepo(tmp_path)
    r = select_affected_tests(["waggledance/x/orphan.py"], tmp_path)
    assert r["full_suite"] is True
    assert "no affected test" in r["reason"]


def test_unknown_file_type_fails_safe_to_full(tmp_path):
    _mkrepo(tmp_path)
    assert (
        select_affected_tests(["docs/architecture/X.md"], tmp_path)["full_suite"]
        is True
    )


def test_one_orphan_among_mappable_forces_full(tmp_path):
    _mkrepo(tmp_path)
    r = select_affected_tests(
        ["waggledance/x/alpha.py", "waggledance/x/orphan.py"], tmp_path
    )
    assert r["full_suite"] is True


def test_unreadable_candidate_forces_full(tmp_path, monkeypatch):
    # An unreadable test candidate could import the changed module → the selector
    # must fail-safe to the full suite rather than silently drop it (RFC contract).
    _mkrepo(tmp_path)
    real_read_text = Path.read_text

    def boom(self, *a, **k):
        if self.name == "test_alpha.py":
            raise OSError("simulated unreadable candidate")
        return real_read_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", boom)
    # alpha2.py has no name-convention test and its only importing candidate
    # (test_alpha.py, which imports the alpha package) is now unreadable.
    (tmp_path / "tests" / "test_alpha.py").write_text(
        "from waggledance.x import alpha2\n\n\ndef test_a2():\n    assert alpha2\n",
        encoding="utf-8",
    )
    r = select_affected_tests(["waggledance/x/alpha2.py"], tmp_path)
    assert r["full_suite"] is True
    assert "unreadable" in r["reason"]


# --- correct narrowing -------------------------------------------------------

def test_changed_test_file_selects_itself(tmp_path):
    _mkrepo(tmp_path)
    r = select_affected_tests(["tests/test_alpha.py"], tmp_path)
    assert r["full_suite"] is False
    assert r["tests"] == ["tests/test_alpha.py"]


def test_source_with_importing_test_is_selected(tmp_path):
    _mkrepo(tmp_path)
    r = select_affected_tests(["waggledance/x/alpha.py"], tmp_path)
    assert r["full_suite"] is False
    assert "tests/test_alpha.py" in r["tests"]


def test_source_selected_by_name_convention(tmp_path):
    _mkrepo(tmp_path)
    r = select_affected_tests(["tools/beta.py"], tmp_path)
    assert r["full_suite"] is False
    assert "tests/tools/test_beta.py" in r["tests"]


def test_explicit_sprint_proof_mapping_selects_hex_hierarchy_tests(tmp_path):
    _mkrepo(tmp_path)
    mapped = tmp_path / "tests" / "test_ring_messaging_hierarchy_proof.py"
    mapped.write_text("def test_ring():\n    assert True\n", encoding="utf-8")
    r = select_affected_tests(
        ["waggledance/core/hex_topology/parent_child_relations.py"], tmp_path
    )
    assert r["full_suite"] is False
    assert r["tests"] == ["tests/test_ring_messaging_hierarchy_proof.py"]


def test_explicit_sprint_proof_mapping_selects_importlib_loaded_tool_test(tmp_path):
    _mkrepo(tmp_path)
    mapped = tmp_path / "tests" / "test_low_risk_autogrowth_real_loop_proof.py"
    mapped.write_text("def test_low_risk():\n    assert True\n", encoding="utf-8")
    r = select_affected_tests(
        ["tools/run_low_risk_autogrowth_real_loop_proof.py"], tmp_path
    )
    assert r["full_suite"] is False
    assert r["tests"] == ["tests/test_low_risk_autogrowth_real_loop_proof.py"]


def test_explicit_w1b_chat_first_hop_mapping_selects_tool_test(tmp_path):
    _mkrepo(tmp_path)
    mapped = tmp_path / "tests" / "tools" / "test_chat_first_hop_corpus.py"
    mapped.write_text("def test_chat_first_hop():\n    assert True\n", encoding="utf-8")
    r = select_affected_tests(["tools/run_chat_first_hop_corpus.py"], tmp_path)
    assert r["full_suite"] is False
    assert r["tests"] == ["tests/tools/test_chat_first_hop_corpus.py"]


def test_explicit_sprint_proof_mapping_missing_test_fails_safe(tmp_path):
    _mkrepo(tmp_path)
    r = select_affected_tests(
        ["waggledance/core/hex_topology/subdivision_operator.py"], tmp_path
    )
    assert r["full_suite"] is True
    assert "explicit affected test missing" in r["reason"]


def test_cli_json_files(tmp_path):
    _mkrepo(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo-root",
            str(tmp_path),
            "--files",
            "waggledance/x/alpha.py",
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["full_suite"] is False
    assert "tests/test_alpha.py" in payload["tests"]


# Bridge-only boundary: non-Python scripts/docs are never accepted by prefix
# alone; every selected test must have a readable, existing evidence path.
def test_bridge_native_wake_source_and_procedures_include_behavior_and_package(tmp_path):
    _mkrepo(tmp_path)
    for test in bridge_test_closure(set().union(
        BRIDGE_EXPLICIT_TESTS["ops/windows/reboot/Get-WdNativeWakePrompt.ps1"],
        BRIDGE_EXPLICIT_TESTS["ops/windows/reboot/WAKE_PROCEDURE_TOOLS.md"],
    )):
        target = tmp_path / test
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('def test_fixture(): pass\n', encoding="utf-8")
    for source in (
        "ops/windows/reboot/Get-WdNativeWakePrompt.ps1",
        "ops/windows/reboot/WAKE_PROCEDURE_TOOLS.md",
    ):
        result = select_affected_tests([source], tmp_path)
        assert result["full_suite"] is False, result
        assert set(result["tests"]) == bridge_test_closure(set(BRIDGE_EXPLICIT_TESTS[source]))


ACTUAL_EIGHT = (
    ".agent-bridge/bin/BridgeEventClassifier.ps1",
    ".agent-bridge/bin/Invoke-StaleClaimSweep.ps1",
    "ops/windows/reboot/Get-WdSwarmParallelStatus.ps1",
    "ops/windows/reboot/Send-WdContinuityAlert.ps1",
    "ops/windows/reboot/bridge-code-files.json",
    "ops/windows/reboot/start-wd-agent.ps1",
    "ops/windows/reboot/start-wd-tools-consumer.ps1",
    "ops/windows/reboot/Deploy-WdRebootBundle.ps1",
)


def _bridge_candidate_fixture(tmp_path):
    root = _mkrepo(tmp_path)
    for test in sorted(bridge_test_closure(set().union(*(BRIDGE_EXPLICIT_TESTS[path] for path in ACTUAL_EIGHT)))):
        candidate = root / test
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text("def test_decoy(): assert True\n", encoding="utf-8")
    return root


def test_actual_eight_integration_paths_narrow_to_reviewed_bridge_tests(tmp_path):
    root = _bridge_candidate_fixture(tmp_path)
    result = select_affected_tests(ACTUAL_EIGHT, root)
    assert result["full_suite"] is False, result
    assert set(result["tests"]) == bridge_test_closure(set().union(*(BRIDGE_EXPLICIT_TESTS[path] for path in ACTUAL_EIGHT)))
    assert {
        "tests/tools/test_wd_native_tools_wake.py",
        "tests/tools/test_wd_lead_reply_delivery.py",
        "tests/tools/test_wd_bridge_code_context.py",
        "tests/tools/test_wd_startup_recovery.py",
        "tests/tools/test_wd_reboot_bundle.py",
    } <= set(result["tests"])
    assert all(test.startswith("tests/tools/") for test in result["tests"])


def test_actual_eight_missing_one_required_test_fails_closed(tmp_path):
    root = _bridge_candidate_fixture(tmp_path)
    (root / "tests/tools/test_wd_native_tools_wake.py").unlink()
    result = select_affected_tests(ACTUAL_EIGHT, root)
    assert result["full_suite"] is True
    assert "missing" in result["reason"]


def test_shared_native_wake_fixture_only_includes_transitive_consumers(tmp_path):
    root = _bridge_candidate_fixture(tmp_path)
    fixture = "tests/tools/test_wd_native_wake_prompt.py"
    result = select_affected_tests([fixture], root)
    assert result["full_suite"] is False, result
    assert set(result["tests"]) == {
        fixture,
        "tests/tools/test_wd_native_tools_wake.py",
        "tests/tools/test_wd_lead_reply_delivery.py",
    }
    (root / "tests/tools/test_wd_lead_reply_delivery.py").unlink()
    result = select_affected_tests([fixture], root)
    assert result["full_suite"] is True
    assert "missing" in result["reason"]


@pytest.mark.parametrize("provider", [
    "tests/tools/test_wd_reboot_bundle.py",
    "tests/tools/test_wd_startup_recovery.py",
    "tests/tools/test_wd_bridge_code_context.py",
])
def test_shared_provider_test_only_requires_explicit_bridge_consumers(tmp_path, provider):
    root = _bridge_candidate_fixture(tmp_path)
    result = select_affected_tests([provider], root)
    assert result["full_suite"] is False, result
    assert {
        "tests/tools/test_bridge_session_watcher_probe.py",
        "tests/tools/test_session_liveness_supervisor_report.py",
        "tests/tools/test_bridge_request_preflight.py",
        "tests/tools/test_wd_task_console_containment_pin.py",
    } <= set(result["tests"])
    (root / "tests/tools/test_bridge_request_preflight.py").unlink()
    result = select_affected_tests([provider], root)
    assert result["full_suite"] is True


def test_bridge_unknown_script_is_not_blanket_prefix_skipped(tmp_path):
    _mkrepo(tmp_path)
    (tmp_path / "tests/tools/test_decoy.py").write_text(
        'SCRIPT = "Unmapped-Helper.ps1"\n', encoding="utf-8"
    )
    result = select_affected_tests([".agent-bridge/bin/Unmapped-Helper.ps1"], tmp_path)
    assert result["full_suite"] is True


def test_bridge_known_source_missing_mapped_test_fails_closed(tmp_path):
    _mkrepo(tmp_path)
    result = select_affected_tests(
        ["ops/windows/reboot/Get-WdNativeWakePrompt.ps1"], tmp_path
    )
    assert result["full_suite"] is True
    assert "missing" in result["reason"]


def test_bridge_json_requires_exact_mapped_package_tests(tmp_path):
    root = _bridge_candidate_fixture(tmp_path)
    mapped = select_affected_tests(["ops/windows/reboot/bridge-code-files.json"], root)
    assert mapped["full_suite"] is False
    assert "tests/tools/test_bridge_code_package_closure.py" in mapped["tests"]
    assert "tests/tools/test_wd_bridge_code_context.py" in mapped["tests"]
    unknown = select_affected_tests(["ops/windows/reboot/unknown-package.json"], root)
    assert unknown["full_suite"] is True


def test_bridge_unreadable_candidate_fails_closed(tmp_path, monkeypatch):
    root = _bridge_candidate_fixture(tmp_path)
    real_read_text = Path.read_text

    def unreadable(self, *args, **kwargs):
        if self.name == "test_bridge_code_package_closure.py":
            raise OSError("simulated unreadable test")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    result = select_affected_tests(["ops/windows/reboot/bridge-code-files.json"], root)
    assert result["full_suite"] is True
    assert "unreadable" in result["reason"]


def test_bridge_mixed_product_change_requires_product_full_suite(tmp_path):
    root = _bridge_candidate_fixture(tmp_path)
    result = select_affected_tests(
        ["ops/windows/reboot/bridge-code-files.json", "waggledance/x/alpha.py"], root
    )
    assert result["full_suite"] is True


def test_bridge_changed_test_file_must_exist(tmp_path):
    _mkrepo(tmp_path)
    result = select_affected_tests(["tests/tools/test_absent_bridge.py"], tmp_path)
    assert result["full_suite"] is True


def test_boundary_document_selects_selector_contract_test(tmp_path):
    _mkrepo(tmp_path)
    (tmp_path / "tests/tools/test_select_affected_tests.py").write_text(
        "def test_boundary(): assert True\n", encoding="utf-8"
    )
    result = select_affected_tests(["docs/BRIDGE_TEST_BOUNDARY.md"], tmp_path)
    assert result == {
        "full_suite": False,
        "tests": ["tests/tools/test_select_affected_tests.py"],
        "reason": "affected-only",
    }
