"""Static conformance only: never import any protected or registered target."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest


REPOSITORY = Path(__file__).resolve().parents[2]


@pytest.fixture
def registry():
    path = REPOSITORY / "tools/dormant_tool_registry.py"
    assert path.is_file(), "registry implementation is absent (expected initial RED)"
    spec = importlib.util.spec_from_file_location("dormant_registry_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def put(root, relative, text="raise RuntimeError('target must never execute')\n"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


RESERVED_PATH = "tools/verify_bridge_consensus.py"
CHARTER_PATH = "docs/architecture/IDLE_AUTONOMY_CHARTER.md"
RESERVATION_LINE = (
    "* `tools/verify_bridge_consensus.py` (bridge-consensus 3-identity verifier "
    "\u2014 self-modification banned; defensive future-proof: the verifier logic "
    "currently lives in the already-denylisted `tools/idle_consensus_auto_merge.py`, "
    "this closes the standalone-file refactor path \u2014 rco-1 #1393)"
)


def build_source_fixture(tmp_path, registry, omitted=()):
    protected = registry.PROTECTED_PATHS
    short_names = [Path(p).stem for p in protected if "merge_with_bridge" not in p
                   and "write_bridge_consensus" not in p]
    put(tmp_path, "CLAUDE.md", "#### 9b. Standing consensus-sign\n"
        "the gate code the merge gate reads/executes at runtime to compute a verdict\n("
        + ", ".join("`" + n + "`" for n in short_names[:-1])
        + ", the merge/receipt executors, `" + short_names[-1] + "`), plus wiring.\n"
        "### 10. Atomic-flip discipline\n")
    put(tmp_path, registry.CONTRACT_PATH,
        "### Scope of the standing sign\n"
        "* the **verdict-computing gate code**: "
        + ", ".join("`" + p + "`" for p in protected)
        + ";\n* **the wiring step itself**\n"
        "* **dormant tools NOT yet wired**: `tools/bridge_event_taxonomy.py`, "
        "`tools/auto_rollback_eligibility.py`, `tools/post_merge_canary.py`;\n"
        "* the **P4c corpus**\n")
    put(tmp_path, CHARTER_PATH, RESERVATION_LINE + "\n")
    for path in protected:
        if path != RESERVED_PATH and path not in omitted:
            put(tmp_path, path)
    for entry in registry.TOOLS:
        put(tmp_path, entry["path"])
    imports = {}
    for entry in registry.TOOLS:
        module = entry["path"][:-3].replace("/", ".")
        for importer in entry["importers"]:
            imports.setdefault(importer, []).append("import " + module)
    for path, lines in imports.items():
        if path in omitted:
            continue
        put(tmp_path, path, "\n".join(lines) + "\nraise RuntimeError('not imported')\n")
    return tmp_path


@pytest.fixture
def source_fixture(tmp_path, registry):
    return build_source_fixture(tmp_path, registry)


def test_synthetic_registry_is_current_without_executing_targets(registry, source_fixture):
    result = registry.check_repository(source_fixture)
    assert result["ok"], result["errors"]
    assert result["evidence_class"] == "static_source_not_observed"
    assert result["wd_flags"] == []


def test_repository_registry_is_current(registry):
    """CI checks the complete real checkout, not a hand-picked fixture subset."""
    result = registry.check_repository(REPOSITORY)
    assert result["ok"], result["errors"]


def test_contract_declared_set_has_exact_path_bytes(registry, source_fixture):
    assert registry.contract_paths(source_fixture) == registry.PROTECTED_PATHS
    path = source_fixture / registry.CONTRACT_PATH
    path.write_text(path.read_text().replace("tools/verify_bridge_consensus.py",
                                         "tools/Verify_bridge_consensus.py"))
    assert "protected_set_mismatch" in registry.check_repository(source_fixture)["errors"]


def test_rule9b_prose_is_cross_checked(registry, source_fixture):
    path = source_fixture / "CLAUDE.md"
    path.write_text(path.read_text().replace("`check_rco_pass_present`", "`different_checker`"))
    assert "rule9b_names_mismatch" in registry.check_repository(source_fixture)["errors"]


def test_an_extra_contract_dormant_tool_is_not_silently_omitted(registry, source_fixture):
    path = source_fixture / registry.CONTRACT_PATH
    path.write_text(path.read_text().replace("* the **P4c corpus**",
        "`tools/another_dormant.py`\n* the **P4c corpus**"))
    assert "contract_tool_missing:tools/another_dormant.py" in registry.check_repository(source_fixture)["errors"]


def test_every_registry_path_must_exist(registry, source_fixture):
    target = "tools/not_created.py"
    registry.TOOLS = registry.TOOLS + ({"path": target, "status": "dormant", "importers": ()},)
    assert "missing_path:" + target in registry.check_repository(source_fixture)["errors"]


def test_new_protected_import_of_dormant_tool_fails(registry, source_fixture):
    target = next(e["path"] for e in registry.TOOLS if e["status"] == "dormant")
    put(source_fixture, registry.PROTECTED_PATHS[0], "from tools import " + Path(target).stem)
    result = registry.check_repository(source_fixture)
    assert any(e.startswith("protected_imports_dormant:") for e in result["errors"])
    assert any(e.startswith("importer_drift:") for e in result["errors"])


def test_new_nonprotected_importer_is_recorded_as_drift(registry, source_fixture):
    put(source_fixture, "waggledance/new_consumer.py", "from tools import post_merge_canary")
    assert "importer_drift:tools/post_merge_canary.py" in registry.check_repository(source_fixture)["errors"]


@pytest.mark.parametrize("statement", [
    "import tools.post_merge_canary as checker",
    "from tools import post_merge_canary as checker",
    "from tools.post_merge_canary import check",
    "from . import post_merge_canary",
    "import post_merge_canary",
    "import importlib; importlib.import_module('tools.post_merge_canary')",
    "__import__('tools.post_merge_canary')",
])
def test_import_forms_are_scanned_without_execution(registry, source_fixture, statement):
    put(source_fixture, registry.PROTECTED_PATHS[0], statement)
    result = registry.check_repository(source_fixture)
    assert any(e.startswith("protected_imports_dormant:") for e in result["errors"])


def test_unknown_dynamic_import_in_protected_code_fails_closed(registry, source_fixture):
    put(source_fixture, registry.PROTECTED_PATHS[0], "__import__(unresolved_name)")
    assert any(e.startswith("unresolved_dynamic_import:")
               for e in registry.check_repository(source_fixture)["errors"])


@pytest.mark.parametrize("statement", [
    "import os; value = os.getenv('WD_NEW_SWITCH')",
    "import os as system; value = system.environ.get('WD_NEW_SWITCH', 'off')",
    "from os import environ as env; value = env['WD_NEW_SWITCH']",
    "from os import getenv as read_env; value = read_env('WD_NEW_SWITCH')",
    "import os; value = os.environ['WD_' + 'NEW_SWITCH']",
])
def test_each_new_wd_environment_reader_is_discovered(registry, source_fixture, statement):
    path = registry.PROTECTED_PATHS[0]
    put(source_fixture, path, statement)
    result = registry.check_repository(source_fixture)
    assert {"module": path, "flag": "WD_NEW_SWITCH", "evidence": "from_code_not_observed"} in result["wd_flags"]
    assert "wd_flag_registry_mismatch" in result["errors"]


def test_unknown_environment_key_cannot_silently_hide_wd_flag(registry, source_fixture):
    put(source_fixture, registry.PROTECTED_PATHS[0], "import os; value = os.getenv(key)")
    assert any(e.startswith("unresolved_environment_read:")
               for e in registry.check_repository(source_fixture)["errors"])


def test_non_wd_environment_read_is_not_a_wd_flag(registry, source_fixture):
    put(source_fixture, registry.PROTECTED_PATHS[0], "import os; value = os.getenv('HOME')")
    result = registry.check_repository(source_fixture)
    assert not result["wd_flags"]
    assert "wd_flag_registry_mismatch" not in result["errors"]


def test_syntax_errors_fail_closed(registry, source_fixture):
    put(source_fixture, registry.PROTECTED_PATHS[0], "not valid Python @")
    assert any(e.startswith("syntax_error:") for e in registry.check_repository(source_fixture)["errors"])


def test_no_network_subprocess_or_target_import_in_checker_ast(registry):
    tree = ast.parse(Path(registry.__file__).read_text(encoding="utf-8"))
    imports = {n.name for node in ast.walk(tree) if isinstance(node, ast.Import) for n in node.names}
    imports |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imports <= {"__future__", "argparse", "ast", "json", "pathlib", "re"}


def test_cli_prints_static_report_only(registry, source_fixture, capsys):
    assert registry.main(["--root", str(source_fixture)]) == 0
    assert '"static_source_not_observed"' in capsys.readouterr().out



def test_named_reservation_absent_is_accepted(registry, source_fixture):
    assert not (source_fixture / RESERVED_PATH).exists()
    result = registry.check_repository(source_fixture)
    assert result["ok"], result["errors"]
    reserved = next(p for p in result["protected"] if p["path"] == RESERVED_PATH)
    assert reserved["presence"] == "reserved_absent"
    assert reserved["reservation"]["source"] == CHARTER_PATH
    assert reserved["reservation"]["quote"] == RESERVATION_LINE
    assert reserved["reservation"]["inspected_revision"] == "6b33c6f95bf2fe350bcde7485e7ed094a59ad4e9"


@pytest.mark.parametrize("missing", [
    "tools/check_bridge_changes_requested.py",
    "tools/check_rco_pass_present.py",
    "tools/idle_consensus_auto_merge.py",
    "tools/merge_with_bridge_receipt.py",
    "tools/write_bridge_consensus_merge_receipt.py",
    "tools/check_proven_safe_autosign_class.py",
])
def test_other_protected_absence_fails(registry, tmp_path, missing):
    root = build_source_fixture(tmp_path, registry, omitted=(missing,))
    assert "missing_path:" + missing in registry.check_repository(root)["errors"]


def test_reservation_requires_its_exact_documented_line(registry, source_fixture):
    put(source_fixture, CHARTER_PATH, "# no reservation\n")
    result = registry.check_repository(source_fixture)
    assert "reservation_not_documented:" + RESERVED_PATH in result["errors"]
    assert not result["ok"]


def test_appeared_reserved_file_is_not_exempt_from_import_scan(registry, source_fixture):
    put(source_fixture, RESERVED_PATH, "from tools import post_merge_canary\n")
    result = registry.check_repository(source_fixture)
    assert ("protected_imports_dormant:" + RESERVED_PATH
            + "->tools/post_merge_canary.py") in result["errors"]
    assert next(p for p in result["protected"] if p["path"] == RESERVED_PATH)["presence"] == "exists"


def test_appeared_reserved_file_wd_reader_requires_registration(registry, source_fixture):
    put(source_fixture, RESERVED_PATH, "import os; flag = os.getenv('WD_RESERVED_TEST')\n")
    result = registry.check_repository(source_fixture)
    assert "wd_flag_registry_mismatch" in result["errors"]
    assert result["wd_flags"] == [{
        "module": RESERVED_PATH, "flag": "WD_RESERVED_TEST", "evidence": "from_code_not_observed"}]
    registry.WD_FLAGS = ((RESERVED_PATH, "WD_RESERVED_TEST"),)
    assert registry.check_repository(source_fixture)["ok"]


def test_removing_reserved_protected_membership_still_fails(registry, source_fixture):
    registry.PROTECTED_PATHS = tuple(p for p in registry.PROTECTED_PATHS if p != RESERVED_PATH)
    assert "protected_set_mismatch" in registry.check_repository(source_fixture)["errors"]


def test_renaming_reserved_protected_membership_still_fails(registry, source_fixture):
    registry.PROTECTED_PATHS = tuple(
        "tools/renamed_verifier.py" if p == RESERVED_PATH else p for p in registry.PROTECTED_PATHS)
    errors = registry.check_repository(source_fixture)["errors"]
    assert "protected_set_mismatch" in errors
    assert "missing_path:tools/renamed_verifier.py" in errors


def test_protected_membership_is_exact_not_derived_from_the_registry(registry, source_fixture):
    assert registry.PROTECTED_PATHS == (
        "tools/verify_bridge_consensus.py",
        "tools/check_bridge_changes_requested.py",
        "tools/check_rco_pass_present.py",
        "tools/idle_consensus_auto_merge.py",
        "tools/merge_with_bridge_receipt.py",
        "tools/write_bridge_consensus_merge_receipt.py",
        "tools/check_proven_safe_autosign_class.py",
    )
    assert registry.contract_paths(source_fixture) == registry.PROTECTED_PATHS


@pytest.mark.parametrize("extra", [
    "waggledance/core/idle_consensus_charter.py",
    "tools/new-verifier.py",
    "tools/new.verifier.py",
])
def test_contract_extra_token_is_not_filtered(registry, source_fixture, extra):
    path = source_fixture / registry.CONTRACT_PATH
    text = path.read_text(encoding="utf-8")
    marker = ";\n* **the wiring step itself**"
    assert text.count(marker) == 1
    path.write_text(text.replace(marker, ", `" + extra + "`" + marker),
                    encoding="utf-8")
    result = registry.check_repository(source_fixture)
    assert "protected_set_mismatch" in result["errors"], result
    assert registry.contract_paths(source_fixture) == (*registry.PROTECTED_PATHS, extra)


@pytest.mark.parametrize("extra", [
    "new_checker2",
    "new-checker",
    "new.checker",
    "tools/new_checker.py",
])
def test_rule9b_extra_token_is_not_filtered(registry, source_fixture, extra):
    path = source_fixture / "CLAUDE.md"
    text = path.read_text(encoding="utf-8")
    marker = "), plus"
    assert text.count(marker) == 1
    path.write_text(text.replace(marker, ", `" + extra + "`" + marker),
                    encoding="utf-8")
    result = registry.check_repository(source_fixture)
    assert "rule9b_names_mismatch" in result["errors"], result
