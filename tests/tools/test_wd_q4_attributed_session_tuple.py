"""Synthetic composition of pinned declarations; never a live session."""
import copy
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from tools import wd_q4_roster_from_fleet as roster
from tools import wd_q4_session_outer_entry as entry


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


@pytest.fixture
def inputs(tmp_path):
    lane = "codex-lead-1"
    lanes = (lane, "claude-rco-1", "claude-rco-2", "fable-5")
    fleet = encode({"schema_version": 2, "fleet_name": "synthetic-fleet",
                    "lanes": [{"agent": name, "agent_uuid": f"{index:08x}-abcd-4ef0-8abc-000000000001"}
                              for index, name in enumerate(lanes, 1)],
                    "tools_supervisor": {"agent": "codex-tools-1",
                                         "agent_uuid": "00000009-abcd-4ef0-8abc-000000000002"}})
    _, roster_sha = roster.build_roster_snapshot(fleet, digest(fleet), enabled=True)
    pins = {key: digest(key.encode("ascii")) for key in entry._BINDING_KEYS}
    pins["roster_sha256"] = roster_sha
    binding = encode({"schema": "wd.q4-lane-binding.v1", "bundle_commit": "a" * 40,
                      "manifest_sha256": "b" * 64, "wrapper_sha256": "c" * 64,
                      "public_plan_sha256": "d" * 64, "binding": pins, "lanes": [lane]})
    worker = {key: str(tmp_path / key) for key in entry._WORKER_KEYS}
    for key in worker:
        if key.endswith("sha256"):
            worker[key] = digest(key.encode("ascii"))
    worker.update({"lane": lane, "fleet-sha256": digest(fleet), "binding-sha256": digest(binding),
                   "interpreter-max-bytes": "1024", "timeout-seconds": "30", "max-output-bytes": "4096"})
    outer = {key: digest(key.encode("ascii")) for key in entry._OUTER_KEYS}
    outer.update({"runtime-root": str(tmp_path / "runtime"), "runtime-generation": "e" * 40,
                  "runtime-revision": "f" * 40,
                  "outer-path": str(tmp_path / "runtime/assets/code/tools/wd_q4_session_outer_synthetic.py")})
    verified = {"worker": worker, "outer": outer, "expected_binding": pins, "bundle_commit": "a" * 40,
                "manifest_sha256": "b" * 64, "wrapper_sha256": "c" * 64, "public_plan_sha256": "d" * 64}
    return {"fleet_bytes": fleet, "expected_fleet_sha256": digest(fleet), "binding_bytes": binding,
            "expected_binding_sha256": digest(binding), "lane": lane, "runtime_tuple": verified}


def resolve(**kw):
    from tools.wd_q4_attributed_session_tuple import resolve_attributed_session_tuple
    return resolve_attributed_session_tuple(**kw)


def test_default_off_does_not_touch_inputs():
    assert resolve(fleet_bytes=object(), expected_fleet_sha256=object(), binding_bytes=object(),
                   expected_binding_sha256=object(), lane=object(), runtime_tuple=object()) == {"status": "OFF"}


def test_matched_attributed_tuple_is_exact_and_does_not_authenticate(inputs):
    from tools.wd_q4_session_tuple import build_session_argv
    result = resolve(**inputs, enabled=True)
    runtime = inputs["runtime_tuple"]
    assert result == {"status": "TUPLE_CONSISTENT", "attribution": "DECLARED_NOT_AUTHENTICATED",
                      "freshness": "FRESHNESS_UNKNOWN",
                      "argv": build_session_argv(runtime["worker"], runtime["outer"],
                                                 runtime["expected_binding"], enabled=True)}
    assert all("synthetic-query-sentinel" not in arg for arg in result["argv"])
    assert "query" not in inspect.signature(resolve_attributed()).parameters


def resolve_attributed():
    from tools.wd_q4_attributed_session_tuple import resolve_attributed_session_tuple
    return resolve_attributed_session_tuple


def test_caller_must_keep_query_out_of_fields_and_avoid_unverified_logging(inputs):
    import tools.wd_q4_attributed_session_tuple as module
    marker = "synthetic-query-in-path"
    inputs["runtime_tuple"]["worker"]["public-plan"] += marker
    result = resolve(**inputs, enabled=True)
    assert any(marker in arg for arg in result["argv"])
    assert "no separate query parameter" in module.__doc__
    assert "query out of tuple fields" in module.__doc__
    assert "not log argv" in module.__doc__


@pytest.mark.parametrize("fault", ["wrong-fleet-hash", "wrong-binding-hash", "missing-lane", "wrong-lane",
    "worker-lane", "worker-fleet-hash", "worker-binding-hash", "bundle-commit", "manifest-sha256",
    "wrapper-sha256", "public-plan-sha256", "expected-binding", "missing-outer", "missing-descriptor",
    "extra-descriptor", "not-bool"])
def test_inconsistent_tuple_never_produces_argv(inputs, fault):
    values = copy.deepcopy(inputs)
    runtime = values["runtime_tuple"]
    if fault == "wrong-fleet-hash": values["expected_fleet_sha256"] = "0" * 64
    if fault == "wrong-binding-hash": values["expected_binding_sha256"] = "0" * 64
    if fault == "missing-lane": values["lane"] = None
    if fault == "wrong-lane": values["lane"] = "fable-5"
    if fault == "worker-lane": runtime["worker"]["lane"] = "fable-5"
    if fault == "worker-fleet-hash": runtime["worker"]["fleet-sha256"] = "0" * 64
    if fault == "worker-binding-hash": runtime["worker"]["binding-sha256"] = "0" * 64
    if fault == "bundle-commit": runtime["bundle_commit"] = "0" * 40
    if fault == "manifest-sha256": runtime["manifest_sha256"] = "0" * 64
    if fault == "wrapper-sha256": runtime["wrapper_sha256"] = "0" * 64
    if fault == "public-plan-sha256": runtime["public_plan_sha256"] = "0" * 64
    if fault == "expected-binding": runtime["expected_binding"]["pointer_sha256"] = "0" * 64
    if fault == "missing-outer": del runtime["outer"]["worker-sha256"]
    if fault == "missing-descriptor": del runtime["bundle_commit"]
    if fault == "extra-descriptor": runtime["query"] = "synthetic-query-sentinel"
    with pytest.raises(ValueError, match="^attributed_tuple_refused$") as caught:
        resolve(**values, enabled=1 if fault == "not-bool" else True)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert "synthetic-query-sentinel" not in repr(caught.value)


@pytest.mark.parametrize("key", entry._BINDING_KEYS)
def test_every_binding_pin_is_compared(inputs, key):
    inputs["runtime_tuple"]["expected_binding"][key] = "0" * 64
    with pytest.raises(ValueError, match="^attributed_tuple_refused$"):
        resolve(**inputs, enabled=True)


@pytest.mark.parametrize("key", ("bundle_commit", "manifest_sha256", "wrapper_sha256", "public_plan_sha256"))
def test_arbitrary_equality_objects_cannot_impersonate_descriptor_values(inputs, key):
    class EqualEverything:
        def __eq__(self, other):
            return True
    inputs["runtime_tuple"][key] = EqualEverything()
    with pytest.raises(ValueError, match="^attributed_tuple_refused$"):
        resolve(**inputs, enabled=True)


def test_no_file_or_process_access(inputs, monkeypatch):
    import builtins
    import os
    import subprocess
    resolve_attributed()  # import before blocking filesystem access
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected I/O")
    with monkeypatch.context() as blocked:
        blocked.setattr(builtins, "open", forbidden)
        blocked.setattr(os, "stat", forbidden)
        blocked.setattr(os, "lstat", forbidden)
        blocked.setattr(subprocess, "Popen", forbidden)
        assert resolve(**inputs, enabled=True)["status"] == "TUPLE_CONSISTENT"
