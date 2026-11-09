"""Contract for the narrow portable prefix witnesses; no FAISS stub or model."""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools/run_mind_prefix_portable_ci.py"
TEST_PATH = "sandbox/mind/step2/test_wd_q4_prefix_build_child.py"
EXPECTED_SELECTORS = (
    "test_default_off_inspects_nothing",
    "test_enabled_must_be_the_bool_true",
    "test_no_encoder_callback_or_identity_can_be_supplied_by_the_caller",
    "test_plan_pin_mismatch_refuses_before_parsing",
    "test_plan_bytes_and_parser_details_refuse",
    "test_plan_over_the_byte_limit_refuses",
    "test_inference_uses_offline_environment_then_restores_real_environment",
)


def runner():
    assert RUNNER.is_file()
    spec = importlib.util.spec_from_file_location("portable_prefix_ci_contract", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exact_portable_scope_and_no_fake_faiss():
    module = runner()
    assert module.SELECTORS == tuple(TEST_PATH + "::" + name for name in EXPECTED_SELECTORS)
    assert len(module.EXPECTED_NODEIDS) == len(set(module.EXPECTED_NODEIDS)) == 21
    for suffix in ("False", "True"):
        assert (TEST_PATH + "::test_inference_uses_offline_environment_then_restores_real_environment[" + suffix + "]"
                in module.EXPECTED_NODEIDS)
    assert all(node.startswith(TEST_PATH + "::") for node in module.EXPECTED_NODEIDS)
    tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.Import) and any(alias.name == "faiss" for alias in node.names)
                   or isinstance(node, ast.ImportFrom) and node.module == "faiss" for node in ast.walk(tree))
    assert not any(isinstance(node, ast.Attribute) and node.attr == "modules" and
                   isinstance(node.value, ast.Name) and node.value.id == "sys" for node in ast.walk(tree))


def test_exact_collection_and_all_calls_required():
    module = runner()
    nodes = module.EXPECTED_NODEIDS
    session = SimpleNamespace(items=[SimpleNamespace(nodeid=node) for node in nodes[:-1]], exitstatus=0)
    guard = module._Guard()
    guard.pytest_collection_finish(session)
    for node in nodes[:-1]:
        guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, when="call", nodeid=node))
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus != 0
    session = SimpleNamespace(items=[SimpleNamespace(nodeid=node) for node in nodes], exitstatus=0)
    guard = module._Guard()
    guard.pytest_collection_finish(session)
    for node in nodes[:-1]:
        guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, when="call", nodeid=node))
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus != 0
    guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, when="call", nodeid=nodes[-1]))
    session.exitstatus = 0
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 0


@pytest.mark.parametrize("status", ["skip", "xfail", "xpass", "collect_skip", "collect_fail"])
def test_skips_xfail_and_failed_collection_refuse(status):
    module = runner()
    nodes = module.EXPECTED_NODEIDS
    guard = module._Guard()
    session = SimpleNamespace(items=[SimpleNamespace(nodeid=node) for node in nodes], exitstatus=0)
    guard.pytest_collection_finish(session)
    if status.startswith("collect_"):
        guard.pytest_collectreport(SimpleNamespace(skipped=status == "collect_skip", failed=status == "collect_fail"))
    for index, node in enumerate(nodes):
        report = SimpleNamespace(skipped=(index == 0 and status in ("skip", "xfail")), when="call", nodeid=node)
        if index == 0 and status in ("xfail", "xpass"):
            report.wasxfail = "synthetic"
        guard.pytest_runtest_logreport(report)
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus != 0


@pytest.mark.parametrize("when", ["setup", "call", "teardown"])
def test_failed_report_refuses_even_if_session_exit_is_overridden(when):
    module = runner()
    nodes = module.EXPECTED_NODEIDS
    guard = module._Guard()
    session = SimpleNamespace(items=[SimpleNamespace(nodeid=node) for node in nodes], exitstatus=0)
    guard.pytest_collection_finish(session)
    for node in nodes:
        guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, failed=False, when="call", nodeid=node))
    guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, failed=True, when=when, nodeid=nodes[0]))
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus != 0


def test_failure_cannot_be_hidden_by_later_session_override():
    module = runner()

    class FakePytest:
        @staticmethod
        def main(_args, plugins):
            guard = plugins[0]
            nodes = module.EXPECTED_NODEIDS
            session = SimpleNamespace(items=[SimpleNamespace(nodeid=node) for node in nodes], exitstatus=0)
            guard.pytest_collection_finish(session)
            for node in nodes:
                guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, failed=False, when="call", nodeid=node))
            guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, failed=True, when="teardown", nodeid=nodes[0]))
            guard.pytest_sessionfinish(session, 0)
            assert session.exitstatus != 0
            return 0  # a later plugin incorrectly masks the failure

    assert module._run_pytest(FakePytest()) != 0


def test_pytest_failure_propagates_without_mutating_environment(monkeypatch):
    module = runner()
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p hook_plugin_d15")
    monkeypatch.setenv("WD_D15_HOOK_REPORT", "synthetic-report")
    monkeypatch.setenv("PYTHONPATH", "synthetic-plugin-path")
    calls = []

    class FakePytest:
        @staticmethod
        def main(args, plugins):
            calls.append((args, plugins))
            return 7

    assert module._run_pytest(FakePytest()) == 7
    assert module.os.environ.get("PYTEST_ADDOPTS") == "-p hook_plugin_d15"
    assert module.os.environ.get("WD_D15_HOOK_REPORT") == "synthetic-report"
    assert module.os.environ.get("PYTHONPATH") == "synthetic-plugin-path"
    assert calls[0][0] == [*(str(ROOT / TEST_PATH) + "::" + item for item in EXPECTED_SELECTORS), "-q"]
    assert len(calls[0][1]) == 1


def test_inherited_d15_cannot_be_silently_omitted(monkeypatch):
    module = runner()
    monkeypatch.setenv("WD_D15_HOOK_REPORT", "synthetic-report")
    guard = module._Guard()
    config = SimpleNamespace(pluginmanager=SimpleNamespace(hasplugin=lambda _name: False))
    with pytest.raises(RuntimeError, match="^mandatory_d15_hook_missing$"):
        guard.pytest_configure(config)
    config.pluginmanager.hasplugin = lambda name: name == "hook_plugin_d15"
    guard.pytest_configure(config)


@pytest.mark.parametrize("filename,job", [("ci.yml", "test"), ("tests.yml", "unified")])
def test_ci_runs_portable_prefix_witnesses_unconditionally(filename, job):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"))
    matches = [step for step in workflow["jobs"][job]["steps"] if step.get("name") == "Run portable Mind prefix witnesses"]
    assert len(matches) == 1
    assert set(matches[0]) == {"name", "run"}
    assert matches[0]["run"] == "python -B tools/run_mind_prefix_portable_ci.py"
