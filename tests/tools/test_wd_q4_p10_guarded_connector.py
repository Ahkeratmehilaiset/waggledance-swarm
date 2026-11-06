"""Synthetic bytes only. These tests never claim model/transport execution."""
import base64
import copy
import hashlib
import importlib
import json

import pytest

from tools import wd_q4_p10_real_index_fixture as fixture


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def module():
    return importlib.import_module("tools.wd_q4_p10_guarded_connector")


def case():
    policy, public, roster = b"synthetic-policy", b"synthetic-public", b"synthetic-roster"
    cell_data = {"index": b"synthetic-index", "meta": b"synthetic-meta", "vectors": b"synthetic-vectors",
                 "provenance": b"synthetic-provenance"}
    receipt = raw({"content": {"files": {k: sha(v) for k, v in cell_data.items()}}})
    cell_data["receipt"] = receipt
    memory = raw({"cells": {"learning": {"generation": 1, "receipt_sha256": sha(receipt)}}})
    active = {"memory.json": memory, "descriptor.json": b"synthetic-descriptor",
              "projection.json": b"synthetic-projection", "build_receipt.json": b"synthetic-build"}
    pointer = raw({"generation": "gen-a", "previous": None, "previous_pointer_sha256": None,
                   "policy_sha256": sha(policy), "roster_sha256": sha(roster),
                   "artifacts": {k: sha(active[v]) for k, v in {
                       "memory_sha256": "memory.json", "descriptor_sha256": "descriptor.json",
                       "projection_sha256": "projection.json", "receipt_sha256": "build_receipt.json"}.items()}})
    files = dict(active)
    files.update({"cells/learning/current.json": raw({"generation": 1, "receipt_sha256": sha(receipt)}),
                  "cells/learning/LOG.jsonl": b"synthetic-log"})
    # The adapter maps the existing cell layout; source readers still validate semantics.
    paths = {"index": "faiss/cell_learning/index.faiss", "meta": "faiss/cell_learning/meta.json",
             "vectors": "vectors.f32", "provenance": "provenance.jsonl", "receipt": "receipt.json"}
    for key, name in paths.items():
        files["cells/learning/gen-000001/" + name] = cell_data[key]
    capture = {"schema": "wd.q4-p10-frozen-files.v1", "status": "CANDIDATE_UNVERIFIED", "generation": "gen-a",
               "pointer_base64": base64.b64encode(pointer).decode("ascii"), "pointer_sha256": sha(pointer),
               "files": {k: {"bytes": len(v), "sha256": sha(v), "base64": base64.b64encode(v).decode("ascii")}
                         for k, v in files.items()},
               "cell_native_validation": "NOT_RUN", "guarded_query": "NOT_RUN_FINAL_TUPLE_REQUIRED"}
    return capture, policy, public, roster, pointer, active, cell_data


def test_current_runner_receives_fleet_bytes_and_its_separate_pin():
    seen = []
    def current_runner(*, enabled, route_bytes, expected_route_sha256, plan_bytes,
                       policy_bytes, public_policy_bytes, fleet_bytes, expected_fleet_sha256, anchor_bytes):
        seen.append((fleet_bytes, expected_fleet_sha256))
        return {"status": "synthetic-unit-only"}
    fleet = b"synthetic-fleet-not-roster"
    result = fixture._run_prefix_once(current_runner, fleet=fleet, route=b"route", plan=b"plan",
                                      policy=b"policy", public=b"public", anchor=b"anchor")
    assert result == {"status": "synthetic-unit-only"}
    assert seen == [(fleet, sha(fleet))]


def test_capture_conversion_preserves_exact_bytes_and_has_no_io(tmp_path):
    c, policy, public, roster, pointer, active, cell_data = case()
    result = module().capture_parts(raw(c), expected_capture_sha256=sha(raw(c)), policy_bytes=policy,
                                    public_policy_bytes=public, roster_bytes=roster)
    assert result["pointer_bytes"] == pointer
    assert result["active_artifact_bytes"] == active
    assert result["history_bytes"] == {}
    assert result["cell_snapshots"]["learning"]["artifacts"] == cell_data
    assert result["cell_snapshots"]["learning"]["log"] == b"synthetic-log"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mutation", ["capture_pin", "pointer_pin", "file_pin", "file_size", "traversal",
                                    "extra_file", "missing_file", "duplicate_key", "old_roster",
                                    "historical_pointer", "unknown_cell", "receipt_pin", "generation_bool"])
def test_unknown_or_inconsistent_capture_never_reaches_a_producer(mutation):
    original, policy, public, roster, *_ = case()
    c = copy.deepcopy(original)
    expected = None
    if mutation == "capture_pin": expected = "f" * 64
    elif mutation == "pointer_pin": c["pointer_sha256"] = "f" * 64
    elif mutation == "file_pin": c["files"]["memory.json"]["sha256"] = "f" * 64
    elif mutation == "file_size": c["files"]["memory.json"]["bytes"] += 1
    elif mutation == "traversal": c["files"]["../memory.json"] = c["files"].pop("memory.json")
    elif mutation == "extra_file": c["files"]["unrecognized.bin"] = c["files"]["memory.json"]
    elif mutation == "missing_file": del c["files"]["cells/learning/gen-000001/receipt.json"]
    elif mutation == "old_roster": roster += b"-wrong"
    elif mutation in ("historical_pointer", "unknown_cell", "generation_bool", "receipt_pin"):
        key = "pointer_base64" if mutation == "historical_pointer" else "memory.json"
        data = base64.b64decode(c[key] if key == "pointer_base64" else c["files"][key]["base64"])
        doc = json.loads(data)
        if mutation == "historical_pointer": doc["previous_pointer_sha256"] = "e" * 64
        elif mutation == "unknown_cell": doc["cells"]["unknown-cell"] = doc["cells"].pop("learning")
        elif mutation == "generation_bool": doc["cells"]["learning"]["generation"] = True
        else: doc["cells"]["learning"]["receipt_sha256"] = "f" * 64
        updated = raw(doc)
        if key == "pointer_base64":
            c[key], c["pointer_sha256"] = base64.b64encode(updated).decode("ascii"), sha(updated)
        else:
            c["files"][key] = {"bytes": len(updated), "sha256": sha(updated),
                                "base64": base64.b64encode(updated).decode("ascii")}
            p = json.loads(base64.b64decode(c["pointer_base64"]))
            p["artifacts"]["memory_sha256"] = sha(updated)
            c["pointer_base64"] = base64.b64encode(raw(p)).decode("ascii")
            c["pointer_sha256"] = sha(raw(p))
    data = raw(c)
    if mutation == "duplicate_key": data = data[:-1] + b',"schema":"wd.q4-p10-frozen-files.v1"}'
    m = module()
    with pytest.raises(m.ConnectorRefused, match="^p10_connector_refused$"):
        m.capture_parts(data, expected_capture_sha256=expected or sha(data), policy_bytes=policy,
                        public_policy_bytes=public, roster_bytes=roster)


def test_unapproved_execution_never_imports_or_invokes_the_guarded_caller():
    m = module()
    with pytest.raises(m.ConnectorRefused):
        m.run_guarded_candidate({"approved_to_execute": False})


# c86 API boundary tests use synthetic owners; they prove no native cleanup.
def compose_config():
    return {"output_root": "C:\\synthetic\\attempt", "bundle_commit": "a" * 40,
            "bundle_store_root": "C:\\synthetic\\bundles", "active_bundle_root": "C:\\synthetic\\bundles\\active",
            "expected_state_sha256": "d" * 64}


def test_c86_composer_receives_independent_current_state_digest_and_root_checks():
    from pathlib import PureWindowsPath as Path
    from types import SimpleNamespace
    seen = []
    def current_compose(*, enabled, request_path, stage_dir, expected_bundle, expected_commit,
                        expected_manifest_sha256, expected_public_plan_sha256, generation_root,
                        bundle_store_root, active_bundle_root, expected_state_sha256):
        assert enabled is True
        assert expected_state_sha256 == "d" * 64
        assert generation_root == "C:\\synthetic\\attempt\\generations"
        assert bundle_store_root == "C:\\synthetic\\bundles"
        assert active_bundle_root == "C:\\synthetic\\bundles\\active"
        seen.append(expected_state_sha256)
        return {"status": "synthetic-boundary-only"}
    result = module()._compose_staged(SimpleNamespace(compose=current_compose), config=compose_config(),
        stage=Path("C:\\synthetic\\attempt\\generations\\stage"), bundle=Path("C:\\synthetic\\candidate"),
        manifest=b"synthetic-manifest", public_plan=b"synthetic-plan")
    assert result == {"status": "synthetic-boundary-only"}
    assert seen == ["d" * 64]


@pytest.mark.parametrize("pin", [None, True, "", "f" * 63, "F" * 64, "not-a-hash"])
def test_unknown_current_state_digest_is_refused_before_composer(pin):
    from pathlib import PureWindowsPath as Path
    from types import SimpleNamespace
    config = compose_config()
    config["expected_state_sha256"] = pin
    calls = []
    def forbidden(**kwargs):
        calls.append(kwargs)
        raise AssertionError("composer entered before state pin validation")
    m = module()
    with pytest.raises(m.ConnectorRefused):
        m._compose_staged(SimpleNamespace(compose=forbidden), config=config,
            stage=Path("C:\\synthetic\\attempt\\generations\\stage"), bundle=Path("C:\\synthetic\\candidate"),
            manifest=b"synthetic-manifest", public_plan=b"synthetic-plan")
    assert calls == []


@pytest.mark.parametrize("inherited", ["1", "unrecognized-ambient-value"])
def test_frozen_caller_explicitly_disables_inherited_live_selector_then_restores(monkeypatch, inherited):
    import os
    monkeypatch.setenv("WD_Q4_LIVE_MODE", inherited)
    monkeypatch.setenv("WD_Q4_SHARED_DESCRIPTOR_PATH", "previous-path")
    monkeypatch.delenv("WD_Q4_SHARED_DESCRIPTOR_SHA256", raising=False)
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", "previous-manifest")
    staged = {"descriptor_path": "synthetic-descriptor", "descriptor_sha256": "a" * 64}
    with module()._frozen_caller_environment(staged, "b" * 64, "frozen"):
        assert os.environ["WD_Q4_LIVE_MODE"] == ""
        assert os.environ["WD_Q4_SHARED_DESCRIPTOR_PATH"] == "synthetic-descriptor"
        assert os.environ["WD_Q4_SHARED_DESCRIPTOR_SHA256"] == "a" * 64
        assert os.environ["WD_REBOOT_EXPECTED_MANIFEST_HASH"] == "b" * 64
    assert os.environ["WD_Q4_LIVE_MODE"] == inherited
    assert os.environ["WD_Q4_SHARED_DESCRIPTOR_PATH"] == "previous-path"
    assert "WD_Q4_SHARED_DESCRIPTOR_SHA256" not in os.environ
    assert os.environ["WD_REBOOT_EXPECTED_MANIFEST_HASH"] == "previous-manifest"


def test_frozen_caller_restores_environment_after_exception(monkeypatch):
    import os
    monkeypatch.delenv("WD_Q4_LIVE_MODE", raising=False)
    before = {name: os.environ.get(name) for name in ("WD_Q4_LIVE_MODE", "WD_Q4_SHARED_DESCRIPTOR_PATH",
              "WD_Q4_SHARED_DESCRIPTOR_SHA256", "WD_REBOOT_EXPECTED_MANIFEST_HASH")}
    with pytest.raises(RuntimeError, match="synthetic-stop"):
        with module()._frozen_caller_environment({"descriptor_path": "synthetic", "descriptor_sha256": "a" * 64},
                                                 "b" * 64, "frozen"):
            raise RuntimeError("synthetic-stop")
    assert {name: os.environ.get(name) for name in before} == before


@pytest.mark.parametrize("mode", [None, True, 1, "", "1", "live"])
def test_unrecognized_explicit_mode_is_refused_without_environment_change(monkeypatch, mode):
    import os
    monkeypatch.setenv("WD_Q4_LIVE_MODE", "1")
    m = module()
    before = dict(os.environ)
    with pytest.raises(m.ConnectorRefused):
        with m._frozen_caller_environment({"descriptor_path": "synthetic", "descriptor_sha256": "a" * 64},
                                         "b" * 64, mode):
            raise AssertionError("unrecognized mode entered caller scope")
    assert dict(os.environ) == before


def test_probe_spec_matches_original_public_fixture_without_runtime_fixture_import():
    from tests.tools.test_wd_q4_p10_real_index_fixture import fleet
    expected = fixture.public_status_case(raw(fleet()))
    assert module().synthetic_probe_spec() == {"query": expected["query"], "target_text": expected["target_text"]}


def test_probe_spec_has_no_shared_mutable_query_state():
    m = module()
    expected = m.synthetic_probe_spec()
    changed = m.synthetic_probe_spec()
    changed["query"]["situation"] = "synthetic mutation"
    changed["target_text"] = "synthetic mutation"
    assert m.synthetic_probe_spec() == expected


# Cell-class regression: the real status publisher produces system, while the
# original synthetic test covered learning only. All bytes remain synthetic.
def case_with_cells(names, native_metadata=False):
    capture, policy, public, roster, pointer, _, cell_data = case()
    if native_metadata:
        receipt = json.loads(cell_data["receipt"])
        receipt["content"]["count"] = 1
        cell_data["receipt"] = raw(receipt)
        set_capture_file(capture, "cells/learning/gen-000001/receipt.json", cell_data["receipt"])
    original_files = copy.deepcopy(capture["files"])
    capture["files"] = {name: item for name, item in original_files.items()
                        if not name.startswith("cells/")}
    memory = {"cells": {name: {"generation": 1, "receipt_sha256": sha(cell_data["receipt"]),
                               **({"count": 1, "seq": 1} if native_metadata else {})} for name in names}}
    for cell in names:
        for name, item in original_files.items():
            if name.startswith("cells/learning/"):
                updated = name.replace("cells/learning/", "cells/" + cell + "/").replace(
                    "cell_learning/", "cell_" + cell + "/")
                capture["files"][updated] = copy.deepcopy(item)
        if native_metadata:
            set_capture_file(capture, "cells/" + cell + "/current.json",
                             raw({"generation": 1, "seq": 1, "receipt_sha256": sha(cell_data["receipt"])}))
    rebind_cell_memory(capture, memory)
    return capture, policy, public, roster, cell_data


def rebind_cell_memory(capture, memory):
    memory_bytes = raw(memory)
    capture["files"]["memory.json"] = {"bytes": len(memory_bytes), "sha256": sha(memory_bytes),
                                        "base64": base64.b64encode(memory_bytes).decode("ascii")}
    pointer = json.loads(base64.b64decode(capture["pointer_base64"]))
    pointer["artifacts"]["memory_sha256"] = sha(memory_bytes)
    pointer_bytes = raw(pointer)
    capture.update(pointer_base64=base64.b64encode(pointer_bytes).decode("ascii"),
                   pointer_sha256=sha(pointer_bytes))


def set_capture_file(capture, name, data):
    capture["files"][name] = {"bytes": len(data), "sha256": sha(data),
                              "base64": base64.b64encode(data).decode("ascii")}


@pytest.mark.parametrize("cell", ["general", "thermal", "energy", "safety", "seasonal", "math", "system", "learning"])
@pytest.mark.parametrize("native_metadata", [False, True], ids=["minimal-binding", "native-count-seq"])
def test_runtime_supported_single_cell_preserves_exact_bytes_without_native_validation(cell, native_metadata, tmp_path):
    capture, policy, public, roster, cell_data = case_with_cells([cell], native_metadata=native_metadata)
    result = module().capture_parts(raw(capture), expected_capture_sha256=sha(raw(capture)), policy_bytes=policy,
                                    public_policy_bytes=public, roster_bytes=roster)
    assert set(result["cell_snapshots"]) == {cell}
    assert result["cell_snapshots"][cell]["artifacts"] == cell_data
    assert result["cell_snapshots"][cell]["log"] == b"synthetic-log"
    assert result["pointer_bytes"] == base64.b64decode(capture["pointer_base64"])
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("shape", ["empty", "multiple", "unknown", "uppercase", "nonmapping"])
def test_non_single_or_unrecognized_cell_shape_still_fails_closed(shape):
    names = {"empty": [], "multiple": ["learning", "system"], "unknown": ["new-cell"],
             "uppercase": ["SYSTEM"], "nonmapping": ["learning"]}[shape]
    capture, policy, public, roster, _ = case_with_cells(names)
    if shape == "nonmapping":
        rebind_cell_memory(capture, {"cells": []})
    m = module()
    with pytest.raises(m.ConnectorRefused, match="^p10_connector_refused$"):
        m.capture_parts(raw(capture), expected_capture_sha256=sha(raw(capture)), policy_bytes=policy,
                        public_policy_bytes=public, roster_bytes=roster)


def test_runtime_cell_registry_matches_the_pinned_topology_contract():
    from waggledance.core.hex_cell_topology import ALL_CELLS
    assert module()._P10_CELLS == frozenset(ALL_CELLS)


@pytest.mark.parametrize("shape", ["count_bool", "count_negative", "seq_bool", "seq_negative", "partial_count",
    "partial_seq", "extra_bound", "control_seq_bool", "control_seq_mismatch", "control_missing_seq", "control_extra",
    "count_mismatch", "receipt_count_bool", "receipt_count_missing", "receipt_count_negative"])
def test_native_cell_metadata_unknown_types_or_pointer_mismatch_fail_closed(shape):
    capture, policy, public, roster, _ = case_with_cells(["system"], native_metadata=True)
    memory = json.loads(base64.b64decode(capture["files"]["memory.json"]["base64"]))
    bound = memory["cells"]["system"]
    if shape == "count_bool": bound["count"] = True
    elif shape == "count_negative": bound["count"] = -1
    elif shape == "seq_bool": bound["seq"] = True
    elif shape == "seq_negative": bound["seq"] = -1
    elif shape == "partial_count": del bound["seq"]
    elif shape == "partial_seq": del bound["count"]
    elif shape == "extra_bound": bound["unrecognized"] = 1
    elif shape == "count_mismatch": bound["count"] = 2
    elif shape.startswith("receipt_count_"):
        name = "cells/system/gen-000001/receipt.json"
        receipt = json.loads(base64.b64decode(capture["files"][name]["base64"]))
        if shape == "receipt_count_bool": receipt["content"]["count"] = True
        elif shape == "receipt_count_missing": del receipt["content"]["count"]
        else: receipt["content"]["count"] = -1
        receipt_bytes = raw(receipt)
        set_capture_file(capture, name, receipt_bytes)
        bound["receipt_sha256"] = sha(receipt_bytes)
        set_capture_file(capture, "cells/system/current.json",
                         raw({"generation": 1, "seq": 1, "receipt_sha256": sha(receipt_bytes)}))
    else:
        name = "cells/system/current.json"
        pointer = json.loads(base64.b64decode(capture["files"][name]["base64"]))
        if shape == "control_seq_bool": pointer["seq"] = True
        elif shape == "control_seq_mismatch": pointer["seq"] = 2
        elif shape == "control_missing_seq": del pointer["seq"]
        else: pointer["unrecognized"] = 1
        set_capture_file(capture, name, raw(pointer))
    rebind_cell_memory(capture, memory)
    m = module()
    with pytest.raises(m.ConnectorRefused, match="^p10_connector_refused$"):
        m.capture_parts(raw(capture), expected_capture_sha256=sha(raw(capture)), policy_bytes=policy,
                        public_policy_bytes=public, roster_bytes=roster)


@pytest.mark.parametrize("shape", ["extra", "seq_bool", "seq_negative"])
def test_minimal_cell_pointer_unknown_metadata_fails_closed(shape):
    capture, policy, public, roster, _ = case_with_cells(["learning"])
    name = "cells/learning/current.json"
    pointer = json.loads(base64.b64decode(capture["files"][name]["base64"]))
    if shape == "extra": pointer["unrecognized"] = 1
    elif shape == "seq_bool": pointer["seq"] = True
    else: pointer["seq"] = -1
    set_capture_file(capture, name, raw(pointer))
    m = module()
    with pytest.raises(m.ConnectorRefused, match="^p10_connector_refused$"):
        m.capture_parts(raw(capture), expected_capture_sha256=sha(raw(capture)), policy_bytes=policy,
                        public_policy_bytes=public, roster_bytes=roster)


def test_minimal_binding_accepts_known_typed_pointer_sequence_without_changing_bytes():
    capture, policy, public, roster, _ = case_with_cells(["learning"])
    name = "cells/learning/current.json"
    pointer = json.loads(base64.b64decode(capture["files"][name]["base64"]))
    pointer["seq"] = 0
    pointer_bytes = raw(pointer)
    set_capture_file(capture, name, pointer_bytes)
    result = module().capture_parts(raw(capture), expected_capture_sha256=sha(raw(capture)), policy_bytes=policy,
                                    public_policy_bytes=public, roster_bytes=roster)
    assert result["cell_snapshots"]["learning"]["pointer"] == pointer_bytes
