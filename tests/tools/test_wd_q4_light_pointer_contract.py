"""Pointer-boundary parity using synthetic bytes; no model or live snapshot."""
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def legacy_pointer():
    spec = importlib.util.spec_from_file_location(
        "synthetic_existing_pointer_cases", ROOT / "sandbox/mind/step2/test_shared_index_checkpoint_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._pointer()


def compare_outcome(function, raw, expected, *, policy="b" * 64, roster="c" * 64, prefix=False):
    try:
        return ("ok", function(raw, expected_pointer_sha256=expected, policy_sha=policy,
                               roster_sha=roster, prefix_required=prefix))
    except Exception as exc:
        return ("error", type(exc), str(exc), exc.__cause__ is None, exc.__context__ is None)


@pytest.mark.parametrize("scenario", ["valid", "valid-prefix", "valid-history", "valid-prefix-history", "pin", "malformed", "duplicate", "generation",
                                      "cursor", "policy", "prefix", "too-large"])
def test_light_pointer_matches_legacy_acceptance_and_fixed_refusal(scenario):
    from sandbox.mind.step2 import shared_index_checkpoint as legacy
    light = importlib.import_module("sandbox.mind.step2.shared_index_pointer")
    assert light.CheckpointRefused is legacy.CheckpointRefused
    raw = legacy_pointer()
    expected = hashlib.sha256(raw).hexdigest()
    policy, prefix = "b" * 64, False
    if scenario in ("valid-prefix", "valid-history", "valid-prefix-history"):
        document = json.loads(raw)
        prefix = scenario in ("valid-prefix", "valid-prefix-history")
        if prefix:
            document["schema"] = legacy.PREFIX_SCHEMA
            document["prefix_start_sha256"] = "e" * 64
            document["prefix_watermark_sha256"] = "f" * 64
        if scenario in ("valid-history", "valid-prefix-history"):
            document["schema"] = legacy.PREFIX_HISTORY_SCHEMA if prefix else legacy.HISTORY_SCHEMA
            document["delta_projection_sha256"] = "1" * 64
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    elif scenario == "pin":
        expected = "0" * 64
    elif scenario == "malformed":
        raw = b"{"
    elif scenario == "duplicate":
        raw = raw[:-1] + b',"schema":"duplicate"}'
    elif scenario == "generation":
        raw = raw.replace(b'"generation-1"', b'"../outside"')
    elif scenario == "cursor":
        document = json.loads(raw); document["cursor_end"]["offset"] = -1
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    elif scenario == "policy":
        policy = "0" * 64
    elif scenario == "prefix":
        prefix = True
    elif scenario == "too-large":
        raw = b"x" * 16385
    if scenario not in ("valid", "pin", "policy", "prefix"):
        expected = hashlib.sha256(raw).hexdigest()
    old = compare_outcome(legacy._parse_pointer_snapshot, raw, expected, policy=policy, prefix=prefix)
    new = compare_outcome(light._parse_pointer_snapshot, raw, expected, policy=policy, prefix=prefix)
    assert new == old
    if scenario.startswith("valid"):
        assert old == ("ok", json.loads(raw))
    else:
        assert old == ("error", legacy.CheckpointRefused, "checkpoint_refused", True, True)


def test_light_pointer_import_does_not_load_checkpoint_or_model():
    code = r"""import builtins,importlib,sys
root=sys.argv[1];sys.path.insert(0,root)
blocked={"numpy","faiss","torch","sentence_transformers","transformers"};original=builtins.__import__
def guard(name,*a,**k):
 if name.split(".")[0] in blocked or name=="sandbox.mind.step2.shared_index_checkpoint": raise ImportError("blocked dependency")
 return original(name,*a,**k)
builtins.__import__=guard
importlib.import_module("sandbox.mind.step2.shared_index_pointer")
assert "sandbox.mind.step2.shared_index_checkpoint" not in sys.modules
assert not blocked.intersection(sys.modules)
"""
    result = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(ROOT)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("relative", ["tools/wd_q4_bound_lane_transport.py",
                                      "tools/wd_q4_fixed_result_adapter.py"])
def test_pointer_only_consumers_use_light_pointer_contract(relative):
    source = (ROOT / relative).read_text(encoding="utf-8")
    assert "from sandbox.mind.step2 import shared_index_pointer as checkpoint" in source


def test_light_pointer_is_listed_once_in_bundle_definition():
    definition = json.loads((ROOT / "ops/windows/reboot/bridge-code-files.json").read_bytes())
    assert definition["python_files"].count("sandbox/mind/step2/shared_index_pointer.py") == 1


def test_checkpoint_refusal_keeps_historical_qualified_name_and_pickle():
    import pickle
    from sandbox.mind.step2 import shared_index_checkpoint as legacy, shared_index_pointer as light
    error = light.CheckpointRefused("checkpoint_refused")
    assert type(error) is legacy.CheckpointRefused
    assert type(error).__module__ == "sandbox.mind.step2.shared_index_checkpoint"
    restored = pickle.loads(pickle.dumps(error))
    assert type(restored) is legacy.CheckpointRefused and str(restored) == "checkpoint_refused"


def test_pointer_and_checkpoint_share_validation_constants():
    from sandbox.mind.step2 import shared_index_checkpoint as legacy, shared_index_pointer as light
    for name in ("SCHEMA", "HISTORY_SCHEMA", "PREFIX_SCHEMA", "PREFIX_HISTORY_SCHEMA",
                 "HEX", "NAME", "IDENTITY", "GENERATION", "ARTIFACTS", "_MAX", "_MAX_POINTER_BYTES"):
        assert getattr(legacy, name) is getattr(light, name), name


def test_runtime_planner_tracks_the_new_pointer_dependency_and_refuses_missing():
    import ast
    from tools import build_q4_runtime_plan as planner
    definition = json.loads((ROOT / "ops/windows/reboot/bridge-code-files.json").read_bytes())
    entries = {name: ("100644", "blob", "0" * 40, 0) for name in definition["python_files"]}
    checkpoint = "sandbox/mind/step2/shared_index_checkpoint.py"
    pointer = "sandbox/mind/step2/shared_index_pointer.py"
    tree = ast.parse((ROOT / checkpoint).read_text(encoding="utf-8"))
    reached, _, _ = planner._imports(tree, checkpoint, planner._Closure(entries))
    assert pointer in reached
    entries.pop(pointer)
    with pytest.raises(planner.RuntimePlanRefused, match="^code_closure_incomplete$"):
        planner._imports(tree, checkpoint, planner._Closure(entries))
