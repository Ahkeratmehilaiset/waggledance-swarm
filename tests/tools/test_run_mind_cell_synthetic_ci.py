"""Synthetic CI runner contract; never execute a model or native FAISS."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "tools" / "run_mind_cell_synthetic_ci.py"
SNAPSHOT_PATH = "sandbox/mind/step2/test_cell_snapshot_bytes.py"
SNAPSHOT_REQUIRED = SNAPSHOT_PATH + "::test_snapshot_verified_bytes_are_consumed_without_reopening_paths"
DESCRIPTOR_PATH = "sandbox/mind/step2/test_wd_q4_private_read_bytes.py"
DESCRIPTOR_REQUIRED = DESCRIPTOR_PATH + "::test_descriptor_bytes_match_existing_path_without_path_io"
MEMORY_PATH = "sandbox/mind/step2/test_memory_snapshot.py"
MEMORY_REQUIRED = MEMORY_PATH + "::test_snapshot_memory_answers_nonempty_q4_without_path_io"
VERIFIED_PATH = "sandbox/mind/step2/test_verified_snapshot_checkpoint.py"
VERIFIED_REQUIRED = VERIFIED_PATH + "::test_public_preflight_no_io_but_checkpoint_stays_fail_closed"
Q4_DESCRIPTOR_PATH = "sandbox/mind/step2/test_q4_descriptor.py"
ACTIVE_BINDING_PATH = "sandbox/mind/step2/test_verified_snapshot_active_binding.py"
PROJECTION_BINDING_PATH = "sandbox/mind/step2/test_verified_snapshot_projection_binding.py"
PREFLIGHT_PATH = "sandbox/mind/step2/test_memory_snapshot_preflight.py"


def test_runner_requires_all_memory_preflight_witnesses():
    runner = load_runner()
    expected = {
        *(f"test_second_cell_hash_mismatch_refused_before_first_cell_load[{name}]"
          for name in ("index", "meta", "provenance", "vectors")),
        "test_valid_two_cell_snapshot_loads_after_preflight",
        "test_missing_optional_vectors_remains_supported",
        "test_second_cell_index_budget_refused_before_first_load",
        *(f"test_missing_required_second_cell_artifact_refused_before_load[{name}]"
          for name in ("index", "meta", "provenance")),
    }
    nodes = {node.removeprefix(PREFLIGHT_PATH + "::") for node in runner.EXPECTED_NODEIDS
             if node.startswith(PREFLIGHT_PATH + "::")}
    assert nodes == expected
    items = [SimpleNamespace(nodeid=node) for node in runner.EXPECTED_NODEIDS]
    for omit_collection in (False, True):
        session = SimpleNamespace(items=[item for item in items if not omit_collection or
                                         not item.nodeid.startswith(PREFLIGHT_PATH + "::")], exitstatus=0)
        guard = runner._Guard()
        guard.pytest_collection_finish(session)
        skipped_node = PREFLIGHT_PATH + "::test_second_cell_hash_mismatch_refused_before_first_cell_load[index]"
        for item in session.items:
            if item.nodeid != skipped_node:
                guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, when="call", nodeid=item.nodeid))
        guard.pytest_sessionfinish(session, 0)
        assert session.exitstatus != 0


@pytest.mark.parametrize("path,count", [(Q4_DESCRIPTOR_PATH, 13), (ACTIVE_BINDING_PATH, 15),
                                        (PROJECTION_BINDING_PATH, 9)])
def test_runner_requires_all_new_witnesses(path, count):
    runner = load_runner()
    nodes = [node for node in runner.EXPECTED_NODEIDS if node.startswith(path + "::")]
    assert len(nodes) == len(set(nodes)) == count
    items = [SimpleNamespace(nodeid=node) for node in runner.EXPECTED_NODEIDS]
    guard = runner._Guard()
    session = SimpleNamespace(items=items, exitstatus=0)
    guard.pytest_collection_finish(session)
    for item in items:
        if item.nodeid != nodes[0]:
            guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, when="call", nodeid=item.nodeid))
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus != 0


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
    assert len(snapshot_nodes) == len(set(snapshot_nodes)) == 27
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


def test_runner_requires_descriptor_collection_and_each_descriptor_call():
    runner = load_runner()
    descriptor_nodes = [node for node in runner.EXPECTED_NODEIDS if node.startswith(DESCRIPTOR_PATH + "::")]
    assert len(descriptor_nodes) == len(set(descriptor_nodes)) == 11
    assert DESCRIPTOR_REQUIRED in descriptor_nodes
    all_items = [SimpleNamespace(nodeid=node) for node in runner.EXPECTED_NODEIDS]
    partial = SimpleNamespace(items=[item for item in all_items if not item.nodeid.startswith(DESCRIPTOR_PATH)], exitstatus=0)
    guard = runner._Guard()
    guard.pytest_collection_finish(partial)
    for item in partial.items:
        guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, when="call", nodeid=item.nodeid))
    guard.pytest_sessionfinish(partial, 0)
    assert partial.exitstatus != 0
    complete = SimpleNamespace(items=all_items, exitstatus=0)
    guard = runner._Guard()
    guard.pytest_collection_finish(complete)
    for item in all_items:
        if item.nodeid != DESCRIPTOR_REQUIRED:
            guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, when="call", nodeid=item.nodeid))
    guard.pytest_sessionfinish(complete, 0)
    assert complete.exitstatus != 0


def test_runner_requires_verified_checkpoint_collection_and_call():
    runner = load_runner()
    nodes = [node for node in runner.EXPECTED_NODEIDS if node.startswith(VERIFIED_PATH + "::")]
    assert VERIFIED_REQUIRED in nodes
    assert len(nodes) == len(set(nodes)) == 54
    items = [SimpleNamespace(nodeid=node) for node in runner.EXPECTED_NODEIDS]
    partial = SimpleNamespace(items=[item for item in items if not item.nodeid.startswith(VERIFIED_PATH)], exitstatus=0)
    guard = runner._Guard()
    guard.pytest_collection_finish(partial)
    for item in partial.items:
        guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, when="call", nodeid=item.nodeid))
    guard.pytest_sessionfinish(partial, 0)
    assert partial.exitstatus != 0
    complete = SimpleNamespace(items=items, exitstatus=0)
    guard = runner._Guard()
    guard.pytest_collection_finish(complete)
    for item in items:
        if item.nodeid != VERIFIED_REQUIRED:
            guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, when="call", nodeid=item.nodeid))
    guard.pytest_sessionfinish(complete, 0)
    assert complete.exitstatus != 0


@pytest.mark.parametrize("status", ["skip", "xfail", "xpass"])
def test_guard_refuses_incomplete_verified_checkpoint_witness(status):
    runner = load_runner()
    guard = runner._Guard()
    session = SimpleNamespace(items=[SimpleNamespace(nodeid=node) for node in runner.EXPECTED_NODEIDS], exitstatus=0)
    guard.pytest_collection_finish(session)
    for node in runner.EXPECTED_NODEIDS:
        report = SimpleNamespace(skipped=(status in ("skip", "xfail") and node == VERIFIED_REQUIRED),
                                 when="call", nodeid=node)
        if status in ("xfail", "xpass") and node == VERIFIED_REQUIRED:
            report.wasxfail = "synthetic expected failure"
        guard.pytest_runtest_logreport(report)
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus != 0


def test_runner_requires_memory_collection_and_each_memory_call():
    runner = load_runner()
    memory_nodes = [node for node in runner.EXPECTED_NODEIDS if node.startswith(MEMORY_PATH + "::")]
    assert MEMORY_REQUIRED in memory_nodes
    assert len(memory_nodes) == len(set(memory_nodes)) == 32
    all_items = [SimpleNamespace(nodeid=node) for node in runner.EXPECTED_NODEIDS]
    partial = SimpleNamespace(items=[item for item in all_items if not item.nodeid.startswith(MEMORY_PATH)], exitstatus=0)
    guard = runner._Guard()
    guard.pytest_collection_finish(partial)
    for item in partial.items:
        guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, when="call", nodeid=item.nodeid))
    guard.pytest_sessionfinish(partial, 0)
    assert partial.exitstatus != 0
    complete = SimpleNamespace(items=all_items, exitstatus=0)
    guard = runner._Guard()
    guard.pytest_collection_finish(complete)
    for item in all_items:
        if item.nodeid != MEMORY_REQUIRED:
            guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, when="call", nodeid=item.nodeid))
    guard.pytest_sessionfinish(complete, 0)
    assert complete.exitstatus != 0


@pytest.mark.parametrize("status", ["skip", "xfail", "xpass"])
def test_guard_refuses_incomplete_memory_witness(status):
    runner = load_runner()
    assert MEMORY_REQUIRED in runner.EXPECTED_NODEIDS
    guard = runner._Guard()
    session = SimpleNamespace(items=[SimpleNamespace(nodeid=node) for node in runner.EXPECTED_NODEIDS], exitstatus=0)
    guard.pytest_collection_finish(session)
    for node in runner.EXPECTED_NODEIDS:
        report = SimpleNamespace(skipped=(status in ("skip", "xfail") and node == MEMORY_REQUIRED),
                                 when="call", nodeid=node)
        if status in ("xfail", "xpass") and node == MEMORY_REQUIRED:
            report.wasxfail = "synthetic expected failure"
        guard.pytest_runtest_logreport(report)
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus != 0


@pytest.mark.parametrize("status", ["skip", "xfail", "xpass"])
def test_guard_refuses_incomplete_descriptor_witness(status):
    runner = load_runner()
    assert DESCRIPTOR_REQUIRED in runner.EXPECTED_NODEIDS
    guard = runner._Guard()
    session = SimpleNamespace(items=[SimpleNamespace(nodeid=node) for node in runner.EXPECTED_NODEIDS], exitstatus=0)
    guard.pytest_collection_finish(session)
    for node in runner.EXPECTED_NODEIDS:
        report = SimpleNamespace(skipped=(status in ("skip", "xfail") and node == DESCRIPTOR_REQUIRED),
                                 when="call", nodeid=node)
        if status in ("xfail", "xpass") and node == DESCRIPTOR_REQUIRED:
            report.wasxfail = "synthetic expected failure"
        guard.pytest_runtest_logreport(report)
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus != 0


@pytest.mark.parametrize("when", ["setup", "call", "teardown"])
def test_failed_report_refuses_even_if_session_exit_is_overridden(when):
    runner = load_runner()
    nodes = runner.EXPECTED_NODEIDS
    guard = runner._Guard()
    session = SimpleNamespace(items=[SimpleNamespace(nodeid=node) for node in nodes], exitstatus=0)
    guard.pytest_collection_finish(session)
    for node in nodes:
        guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, failed=False, when="call", nodeid=node))
    guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, failed=True, when=when, nodeid=nodes[0]))
    guard.pytest_sessionfinish(session, 0)
    assert session.exitstatus != 0


def test_failure_cannot_be_hidden_by_later_session_override():
    runner = load_runner()

    class FakePytest:
        @staticmethod
        def main(_args, plugins):
            guard = plugins[0]
            nodes = runner.EXPECTED_NODEIDS
            session = SimpleNamespace(items=[SimpleNamespace(nodeid=node) for node in nodes], exitstatus=0)
            guard.pytest_collection_finish(session)
            for node in nodes:
                guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, failed=False, when="call", nodeid=node))
            guard.pytest_runtest_logreport(SimpleNamespace(skipped=False, failed=True, when="teardown", nodeid=nodes[0]))
            guard.pytest_sessionfinish(session, 0)
            assert session.exitstatus != 0
            return 0  # a later plugin incorrectly masks the failure

    assert runner._run_pytest(FakePytest()) != 0


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
                           str(ROOT / SNAPSHOT_PATH), str(ROOT / DESCRIPTOR_PATH), str(ROOT / MEMORY_PATH),
                           str(ROOT / PREFLIGHT_PATH),
                           str(ROOT / VERIFIED_PATH), str(ROOT / Q4_DESCRIPTOR_PATH),
                           str(ROOT / ACTIVE_BINDING_PATH), str(ROOT / PROJECTION_BINDING_PATH), "-q"]


def test_required_d15_hook_cannot_be_silently_omitted(monkeypatch):
    runner = load_runner()
    monkeypatch.setenv("WD_D15_HOOK_REPORT", "synthetic-report")
    guard = runner._Guard()
    config = SimpleNamespace(pluginmanager=SimpleNamespace(hasplugin=lambda _name: False))
    with pytest.raises(RuntimeError, match="^mandatory_d15_hook_missing$"):
        guard.pytest_configure(config)
    config.pluginmanager.hasplugin = lambda name: name == "hook_plugin_d15"
    guard.pytest_configure(config)
