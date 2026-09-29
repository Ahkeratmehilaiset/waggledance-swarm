"""Bridge v2 consumer source cutover (dormant) fixtures: import closure, API contract, writer F23.

Authored under the 2026-09-29 operator no-runs directive: NOT executed by the author.
The queue module tools/bridge_v2_work_queue.py is RCO2's parallel B-queue slice; until it is
composed, the tests that import a consumer depending on it skip, and the frozen name contract
below is what that module must export.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONSUMERS = ("bridge_event_writer", "bridge_next_action", "bridge_workflow", "work_queue_sweep_stale",
             "validate_bridge_event", "agent_next_task", "bridge_capacity_advisor")
# The ONLY product import left in the consumer closure: legacy coupling outside the Bridge contract.
ALLOWED_PRODUCT_IMPORTS = {"agent_next_task": {"waggledance.core.idle_protocol_deferred_lift"}}
KERNEL = ("bridge_v2_event_schema", "bridge_v2_request_contract", "bridge_v2_identity_registry",
          "bridge_v2_log_reader", "bridge_v2_workflow")
QUEUE = "bridge_v2_work_queue"
# Frozen consumer-side contract for the queue module (the names the consumers import from it).
QUEUE_NAMES = {"AGENT_ID_PATTERN", "DEFAULT_BRIDGE_ROOT", "ArchivedClaim", "Claim", "WorkQueueError",
               "archive_stale_claims", "list_claims", "resolve_bridge_root"}


def _tree(name: str) -> ast.Module | None:
    path = ROOT / "tools" / f"{name}.py"
    return ast.parse(path.read_text(encoding="utf-8")) if path.is_file() else None


def _imports(tree: ast.Module) -> list[tuple[str, tuple[str, ...]]]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, ()) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append((node.module, tuple(alias.name for alias in node.names)))
    return found


def _top_level_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
    return names


def test_the_transitive_consumer_closure_has_only_the_disclosed_product_import():
    seen, product, stack = set(), {}, list(CONSUMERS)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        tree = _tree(name)
        if tree is None:
            assert name == QUEUE, f"unexpected missing module {name}"   # only the pending queue slice
            continue
        for module, _ in _imports(tree):
            top = module.split(".")[0]
            if top == "waggledance":
                product.setdefault(name, set()).add(module)
            elif top == "tools" and "." in module:
                stack.append(module.split(".", 1)[1].split(".")[0])
    assert product == ALLOWED_PRODUCT_IMPORTS
    assert set(KERNEL) <= seen and QUEUE in seen


@pytest.mark.parametrize("name", CONSUMERS)
def test_no_consumer_reaches_the_product_package_dynamically(name):
    source = (ROOT / "tools" / f"{name}.py").read_text(encoding="utf-8")
    for pattern in ('import_module("waggledance', "import_module('waggledance", '__import__("waggledance',
                    "__import__('waggledance", 'sys.modules["waggledance', "sys.modules['waggledance"):
        assert pattern not in source, (name, pattern)


def test_every_imported_kernel_name_exists_in_the_kernel():
    for consumer in CONSUMERS:
        for module, names in _imports(_tree(consumer)):
            target = module.split(".", 1)[1] if module.startswith("tools.") else None
            if target in KERNEL:
                defined = _top_level_names(_tree(target))
                assert set(names) <= defined, (consumer, target, sorted(set(names) - defined))


def test_consumers_use_only_the_frozen_queue_contract():
    wanted = set()
    for consumer in CONSUMERS:
        for module, names in _imports(_tree(consumer)):
            if module == "tools." + QUEUE:
                wanted.update(names)
    assert wanted == QUEUE_NAMES
    queue = _tree(QUEUE)
    if queue is None:
        pytest.skip("tools/bridge_v2_work_queue.py is RCO2's pending B-queue slice (composition gate)")
    assert QUEUE_NAMES <= _top_level_names(queue)


def test_consumers_bind_the_kernel_objects_after_composition():
    pytest.importorskip("tools." + QUEUE)
    next_action = importlib.import_module("tools.bridge_next_action")
    log_reader = importlib.import_module("tools.bridge_v2_log_reader")
    contract = importlib.import_module("tools.bridge_v2_request_contract")
    workflow = importlib.import_module("tools.bridge_v2_workflow")
    assert next_action.read_bridge_log_tail_lines is log_reader.read_bridge_log_tail_lines
    assert next_action.reply_matches_request is contract.reply_matches_request
    assert next_action.worker_class is workflow.worker_class
    assert importlib.import_module("tools.bridge_workflow").prepare_request is workflow.prepare_request


def _event(**changes) -> dict:
    event = {"ts_utc": "2026-09-29T12:00:00.0000000Z", "agent": "claude-rco-1", "type": "message",
             "task_id": "task/1", "status": "answered", "severity": "", "to": "codex-lead-1", "message": "m",
             "paths": [], "write_scope": [], "run_id": "run-1", "role": "rco-security",
             "agent_uuid": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103", "session_id": "session-1",
             "capabilities": [], "pid": 1, "cwd": "C:\\work", "payload": {}}
    event.update(changes)
    return event


def test_the_writer_refuses_reserved_labels_without_trusted_provenance():
    writer = importlib.import_module("tools.bridge_event_writer")
    assert writer._event_row_bytes(_event()).endswith(b"\n")          # success twin: ordinary lanes unchanged
    for reserved in (_event(agent="operator", role="operator", agent_uuid="", session_id="operator-probe-1"),
                     _event(agent="system", role="", agent_uuid="", session_id="sweep-1"),
                     _event(role="operator")):                      # a lane cannot borrow the role either
        with pytest.raises(writer.BridgeEventWriteError, match="reserved"):
            writer._event_row_bytes(reserved)


def test_the_writer_validates_through_the_kernel_not_the_product_schema():
    tree = _tree("bridge_event_writer")
    modules = {module for module, _ in _imports(tree)}
    assert "tools.bridge_v2_event_schema" in modules
    assert not any(module.startswith("waggledance") for module in modules)
    source = (ROOT / "tools" / "bridge_event_writer.py").read_text(encoding="utf-8")
    assert "validate_event_for_write(event_object)" in source
