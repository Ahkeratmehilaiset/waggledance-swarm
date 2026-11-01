"""Synthetic CI runner contract; never execute a model or native FAISS."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools" / "run_mind_cell_synthetic_ci.py"
SNAPSHOT_PATH = "sandbox/mind/step2/test_cell_snapshot_bytes.py"
SNAPSHOT_REQUIRED = SNAPSHOT_PATH + "::test_snapshot_verified_bytes_are_consumed_without_reopening_paths"


def load_runner():
    assert RUNNER.is_file()
    spec = importlib.util.spec_from_file_location("synthetic_cell_ci_runner_test", RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_exists_and_stub_is_fail_fast():
    runner = load_runner()
    fake = runner._fake_faiss()
    assert fake.METRIC_INNER_PRODUCT == 0
    for operation in (fake.IndexFlatIP, fake.read_index, fake.deserialize_index):
        with pytest.raises(RuntimeError, match="^synthetic_faiss_unavailable$"):
            operation(None)


def test_guard_requires_exact_collection_and_rejects_skip():
    runner = load_runner()
    guard = runner._Guard()
    session = SimpleNamespace(items=[SimpleNamespace(nodeid=node) for node in runner.EXPECTED_NODEIDS], exitstatus=0)
    guard.pytest_collection_finish(session)
    guard.pytest_runtest_logreport(SimpleNamespace(skipped=True, when="setup", nodeid=runner.EXPECTED_NODEIDS[0]))
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus != 0
    guard = runner._Guard()
    missing = SimpleNamespace(items=session.items[:-1], exitstatus=0)
    guard.pytest_collection_finish(missing)
    guard.pytest_sessionfinish(missing, 0)
    assert missing.exitstatus != 0


def test_guard_requires_every_exact_test_to_execute():
    runner = load_runner()
    guard = runner._Guard()
    session = SimpleNamespace(items=[SimpleNamespace(nodeid=node) for node in runner.EXPECTED_NODEIDS], exitstatus=0)
    guard.pytest_collection_finish(session)
    for node in runner.EXPECTED_NODEIDS[:-1]:
        guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, when="call", nodeid=node))
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus != 0
    guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, when="call", nodeid=runner.EXPECTED_NODEIDS[-1]))
    session.exitstatus = 0
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 0


def test_runner_requires_snapshot_collection_and_each_snapshot_call():
    runner = load_runner()
    snapshot_nodes = [node for node in runner.EXPECTED_NODEIDS if node.startswith(SNAPSHOT_PATH + "::")]
    assert len(snapshot_nodes) == len(set(snapshot_nodes)) == 26
    assert SNAPSHOT_REQUIRED in runner.EXPECTED_NODEIDS
    all_items = [SimpleNamespace(nodeid=node) for node in runner.EXPECTED_NODEIDS]
    missing_snapshot = SimpleNamespace(items=[item for item in all_items if not item.nodeid.startswith(SNAPSHOT_PATH)],
                                       exitstatus=0)
    guard = runner._Guard()
    guard.pytest_collection_finish(missing_snapshot)
    for item in missing_snapshot.items:
        guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, when="call", nodeid=item.nodeid))
    guard.pytest_sessionfinish(missing_snapshot, 0)
    assert missing_snapshot.exitstatus != 0
    session = SimpleNamespace(items=all_items, exitstatus=0)
    guard = runner._Guard()
    guard.pytest_collection_finish(session)
    for item in all_items:
        if item.nodeid != SNAPSHOT_REQUIRED:
            guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, when="call", nodeid=item.nodeid))
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus != 0


@pytest.mark.parametrize("status", ["skip", "xfail", "xpass"])
def test_guard_refuses_incomplete_snapshot_witness(status):
    runner = load_runner()
    assert SNAPSHOT_REQUIRED in runner.EXPECTED_NODEIDS
    guard = runner._Guard()
    session = SimpleNamespace(items=[SimpleNamespace(nodeid=node) for node in runner.EXPECTED_NODEIDS], exitstatus=0)
    guard.pytest_collection_finish(session)
    for node in runner.EXPECTED_NODEIDS:
        report = SimpleNamespace(skipped=(status in ("skip", "xfail") and node == SNAPSHOT_REQUIRED),
                                 when="call", nodeid=node)
        if status in ("xfail", "xpass") and node == SNAPSHOT_REQUIRED:
            report.wasxfail = "synthetic expected failure"
        guard.pytest_runtest_logreport(report)
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus != 0


@pytest.mark.parametrize("skipped", [True, False], ids=["xfail", "xpass"])
def test_guard_refuses_xfail_and_xpass_even_when_all_tests_run(skipped):
    runner = load_runner()
    guard = runner._Guard()
    session = SimpleNamespace(items=[SimpleNamespace(nodeid=node) for node in runner.EXPECTED_NODEIDS], exitstatus=0)
    guard.pytest_collection_finish(session)
    for index, node in enumerate(runner.EXPECTED_NODEIDS):
        report = SimpleNamespace(skipped=(skipped and index == 0), when="call", nodeid=node)
        if index == 0:
            report.wasxfail = "synthetic expected failure"
        guard.pytest_runtest_logreport(report)
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus != 0


def test_runner_propagates_pytest_failure_and_preserves_environment(monkeypatch):
    runner = load_runner()
    monkeypatch.setenv("PYTEST_ADDOPTS", "-p hook_plugin_d15")
    monkeypatch.setenv("WD_D15_HOOK_REPORT", "synthetic-report")
    monkeypatch.setenv("PYTHONPATH", "synthetic-plugin-path")
    before = dict(runner.os.environ)
    calls = []

    class FakePytest:
        @staticmethod
        def main(args, plugins):
            calls.append((args, plugins))
            return 7

    assert runner._run_pytest(FakePytest()) == 7
    assert dict(runner.os.environ) == before
    assert len(calls) == 1 and len(calls[0][1]) == 1
    assert calls[0][0] == [str(ROOT / "sandbox/mind/step2/test_cell_verified_bytes.py"),
                           str(ROOT / SNAPSHOT_PATH), "-q"]


def test_required_d15_hook_cannot_be_silently_omitted(monkeypatch):
    runner = load_runner()
    monkeypatch.setenv("WD_D15_HOOK_REPORT", "synthetic-report")
    guard = runner._Guard()
    config = SimpleNamespace(pluginmanager=SimpleNamespace(hasplugin=lambda _name: False))
    with pytest.raises(RuntimeError, match="^mandatory_d15_hook_missing$"):
        guard.pytest_configure(config)
    config.pluginmanager.hasplugin = lambda name: name == "hook_plugin_d15"
    guard.pytest_configure(config)
