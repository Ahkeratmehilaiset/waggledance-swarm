"""Synthetic receipt-chain age contract. No models, native query or real data.

The existing CLI recorder proves formatting/guard order only, not native cleanup.
Age is a local-clock observation of pinned cell receipts and a content cutoff;
it is never source freshness, coverage completeness or whole-memory commit time.
"""
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_wd_q4_lane_caller_cli import case, invoke, canon, sha, NOW, REFUSAL


SCOPE = "wd.q4-shared-projected-events.v1"
AGE_KEYS = {"schema", "status", "basis", "observed_local_utc", "clock_authentication",
            "source_freshness", "coverage_completeness", "oldest_cell_built_utc",
            "newest_cell_built_utc", "oldest_cell_age_seconds", "newest_cell_age_seconds",
            "coverage_cutoff_utc", "coverage_age_seconds"}
OLDEST = "2025-12-31T23:58:20Z"
NEWEST = "2025-12-31T23:59:00Z"
CUTOFF = "2025-12-25T00:00:00Z"


@pytest.fixture
def content_case(case, monkeypatch):
    """Real descriptor/plan/memory checks, synthetic adapter and owned guard roots."""
    from tools import wd_q4_lane_link as link
    from sandbox.mind.step2.shared_index_preflight import _encoder_digest
    root = case["files"]["descriptor.json"].parent
    memory = root / "age-memory"
    memory.mkdir()
    projection = b"{}"
    encoder = {"model": "synthetic", "revision": "b"*40, "weights_sha256": "a"*64}
    manifest = {"schema": "wd.bridge-mind-hex-memory-step2.v1", "scope": SCOPE,
                "projection_schema": "wd.mind-content-memory-projection.v1",
                "projection_sha256": sha(projection), "cutoff_utc": CUTOFF, "cells": {}}
    receipts = {}
    for cell_id, built in (("general", OLDEST), ("system", NEWEST)):
        receipt = {"schema": "wd.bridge-mind-hex-cell.v1", "authority": "none", "scope": SCOPE,
                   "source": "shared_index_builder", "built_utc": built,
                   "content": {"cell_id": cell_id, "count": 1, "cutoff_utc": CUTOFF,
                               "projection_sha256": sha(projection)}}
        receipt["content_sha256"] = sha(canon(receipt["content"]))
        path = memory / "cells" / cell_id / "gen-000001" / "receipt.json"
        path.parent.mkdir(parents=True)
        path.write_bytes(canon(receipt))
        receipts[cell_id] = (path, receipt)
        manifest["cells"][cell_id] = {"generation": 1, "seq": 1, "count": 1,
                                      "receipt_sha256": sha(path.read_bytes())}
    (memory / "projection.json").write_bytes(projection)
    pins = case["descriptor"]["expected_binding"]
    pins.clear()
    pins.update(encoder_sha256=_encoder_digest(encoder), projection_sha256=sha(projection))
    for name in ("policy", "roster", "content_decision"):
        path = root / ("age-"+name+".json")
        path.write_bytes(b"{}")
        pins[name+"_sha256"] = sha(b"{}")
    plan = {"schema": link._CONTENT_PLAN_SCHEMA,
            "config": {"memory": str(memory), "descriptor": str(memory/"descriptor.json"),
                       "runtime_revision": encoder["revision"]},
            **{name+"_path": str(root/("age-"+name+".json"))
               for name in ("policy", "roster", "content_decision")}}
    if link._CONTENT_PLAN_SCHEMA == "wd.q4-public-wrapper-plan.v5":
        plan.update({key: None for key in ("message_source_path", "message_generation_path",
            "message_policy_path", "doc_repo_root", "doc_policy_path", "doc_git_executable", "doc_git_sha256")})

    def repin():
        for cell_id, (path, receipt) in receipts.items():
            if cell_id in manifest.get("cells", {}) and type(manifest["cells"][cell_id]) is dict:
                manifest["cells"][cell_id]["receipt_sha256"] = sha(path.read_bytes())
        memory_raw = canon(manifest)
        (memory/"memory.json").write_bytes(memory_raw)
        pins["memory_sha256"] = sha(memory_raw)
        descriptor_raw = canon({"schema": "wd.bridge-mind-q4-memory-descriptor.v2", "scope": SCOPE,
                               "memory_sha256": pins["memory_sha256"], "dim": 2, "encoder": encoder})
        (memory/"descriptor.json").write_bytes(descriptor_raw)
        pins["descriptor_sha256"] = sha(descriptor_raw)
        plan["config"]["descriptor_sha256"] = pins["descriptor_sha256"]
        plan.update({key: value for key, value in pins.items() if key != "descriptor_sha256"})
        public_raw = canon(plan)
        case["files"]["public-plan.json"].write_bytes(public_raw)
        binding_raw = canon({"schema": "wd.q4-lane-binding.v1", "binding": pins,
                             "public_plan_sha256": sha(public_raw)})
        case["files"]["binding.json"].write_bytes(binding_raw)
        case["descriptor"]["binding_sha256"] = sha(binding_raw)
        outer_raw = canon(case["descriptor"])
        case["files"]["descriptor.json"].write_bytes(outer_raw)
        monkeypatch.setenv("WD_Q4_SHARED_DESCRIPTOR_SHA256", sha(outer_raw))

    def rewrite(cell_id="general"):
        path, receipt = receipts[cell_id]
        path.write_bytes(canon(receipt))
        repin()

    repin()
    case["outcome"].update(validated=True, stored_text="synthetic age content")
    case.update(memory=memory, age_manifest=manifest, receipts=receipts, repin=repin, rewrite=rewrite)
    return case


def rejected(case, monkeypatch, capsys):
    assert invoke(case, monkeypatch) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == REFUSAL


def test_cell_age_output_separates_week_old_coverage_from_new_cell_build(content_case, monkeypatch, capsys):
    c = content_case
    c["clock"]["times"] = [NOW, NOW+timedelta(seconds=5)]
    assert invoke(c, monkeypatch) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    value = json.loads(captured.out)
    assert value["schema"] == "wd.q4-lane-content-delivery.v2"
    age = value["index_age"]
    assert set(age) == AGE_KEYS
    assert age == {"schema": "wd.q4-cell-build-age.v1", "status": "OBSERVED",
        "basis": "hash_pinned_cell_receipts", "observed_local_utc": "2026-01-01T00:00:05.000000Z",
        "clock_authentication": "UNKNOWN", "source_freshness": "UNKNOWN", "coverage_completeness": "UNKNOWN",
        "oldest_cell_built_utc": OLDEST, "newest_cell_built_utc": NEWEST,
        "oldest_cell_age_seconds": 105, "newest_cell_age_seconds": 65,
        "coverage_cutoff_utc": CUTOFF, "coverage_age_seconds": 604805}
    assert value["freshness"] == value["answer"]["trust"]["freshness"] == "UNKNOWN"
    assert value["answer_sha256"] == sha(canon(value["answer"]))
    assert "synthetic-owner-token" not in captured.out and "synthetic-query-canary" not in captured.out
    assert str(c["memory"]) not in captured.out


@pytest.mark.parametrize("missing", ["cells", "cutoff", "all_built", "one_built"])
def test_missing_age_capability_stays_exact_unknown(content_case, monkeypatch, capsys, missing):
    c = content_case
    if missing == "cells": c["age_manifest"].pop("cells")
    elif missing == "cutoff": c["age_manifest"].pop("cutoff_utc")
    else:
        for cell_id in (list(c["receipts"]) if missing == "all_built" else ["general"]):
            path, receipt = c["receipts"][cell_id]
            receipt.pop("built_utc")
            path.write_bytes(canon(receipt))
    c["repin"]()
    assert invoke(c, monkeypatch) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["schema"] == "wd.q4-lane-content-delivery.v1"
    assert value["index_age"] == {"status": "UNKNOWN", "seconds": None}


@pytest.mark.parametrize("field", ["built", "cutoff"])
@pytest.mark.parametrize("bad", [None, 1, True, "", "2026-01-01T00:00:00+00:00",
    "2026-01-01", "2025-13-01T00:00:00Z", "2025-12-31T23:59:60Z",
    "2025-12-31T23:59:00.1234567Z", "2025-12-31T23:59:00z", "2025-12-31T23:59:00Z\n",
    "2026-01-01T00:00:01Z"])
def test_malformed_or_future_age_refuses_before_adapter(content_case, monkeypatch, capsys, field, bad):
    c = content_case
    if field == "built": c["receipts"]["general"][1]["built_utc"] = bad; c["rewrite"]()
    else:
        c["age_manifest"]["cutoff_utc"] = bad
        for cell_id, (path, receipt) in c["receipts"].items():
            receipt["content"]["cutoff_utc"] = bad
            path.write_bytes(canon(receipt))
        c["repin"]()
    rejected(c, monkeypatch, capsys)
    assert c["calls"] == []


@pytest.mark.parametrize("name", ["../general", "..\\general", "/general", "C:general", "general:stream",
                                 "CON", "", "unknown", ".", "general/.."])
def test_unsafe_or_unknown_cell_name_refuses(content_case, monkeypatch, capsys, name):
    c = content_case
    pointer = c["age_manifest"]["cells"].pop("general")
    c["age_manifest"]["cells"][name] = pointer
    c["repin"]()
    rejected(c, monkeypatch, capsys)
    assert c["calls"] == []


@pytest.mark.parametrize("bad", [True, -1, 1000000, 1.0, "1", None])
def test_generation_is_a_bounded_integer(content_case, monkeypatch, capsys, bad):
    c = content_case
    c["age_manifest"]["cells"]["general"]["generation"] = bad
    c["repin"]()
    rejected(c, monkeypatch, capsys)


@pytest.mark.parametrize("fault", ["hash", "missing", "oversized", "json", "duplicate", "cell_id",
    "projection", "cutoff", "schema", "cells_type", "pointer_type", "pin_type", "generation_reparse"])
def test_receipt_integrity_and_path_failures_never_downgrade_to_unknown(content_case, monkeypatch, capsys, fault):
    c = content_case
    path, receipt = c["receipts"]["general"]
    if fault == "hash": path.write_bytes(path.read_bytes()+b" ")
    elif fault == "missing": path.unlink()
    elif fault == "oversized": path.write_bytes(b" "*(1024*1024+1)); c["repin"]()
    elif fault == "json": path.write_bytes(b"{"); c["repin"]()
    elif fault == "duplicate":
        path.write_bytes(canon(receipt)[:-1]+b',"built_utc":"'+OLDEST.encode()+b'"}'); c["repin"]()
    elif fault == "cell_id": receipt["content"]["cell_id"] = "system"; c["rewrite"]()
    elif fault == "projection": receipt["content"]["projection_sha256"] = "0"*64; c["rewrite"]()
    elif fault == "cutoff": receipt["content"]["cutoff_utc"] = "2025-12-24T00:00:00Z"; c["rewrite"]()
    elif fault == "schema": receipt["schema"] = "unknown"; c["rewrite"]()
    elif fault == "cells_type": c["age_manifest"]["cells"] = []; c["repin"]()
    elif fault == "pointer_type": c["age_manifest"]["cells"]["general"] = []; c["repin"]()
    elif fault == "pin_type":
        c["repin"]()
        c["age_manifest"]["cells"]["general"]["receipt_sha256"] = None
        # Repin outer documents without overwriting this deliberately bad pin.
        c["receipts"].pop("general")
        c["repin"]()
    else:
        original = Path.lstat
        def lstat(value, *args, **kwargs):
            observed = original(value, *args, **kwargs)
            if value == path.parent:
                return SimpleNamespace(st_mode=observed.st_mode, st_file_attributes=0x400)
            return observed
        monkeypatch.setattr(Path, "lstat", lstat)
    rejected(c, monkeypatch, capsys)


@pytest.mark.parametrize("fault", ["changed_receipt", "removed_receipt", "changed_memory"])
def test_receipt_and_cutoff_are_rechecked_before_output(content_case, monkeypatch, capsys, fault):
    c = content_case
    original = c["adapter"].call_lane_session
    def mutate(**kwargs):
        result = original(**kwargs)
        path = c["receipts"]["general"][0]
        if fault == "changed_receipt": path.write_bytes(path.read_bytes()+b" ")
        elif fault == "removed_receipt": path.unlink()
        else: (c["memory"]/"memory.json").write_bytes(b"{}")
        return result
    monkeypatch.setattr(c["adapter"], "call_lane_session", mutate)
    rejected(c, monkeypatch, capsys)
    assert len(c["calls"]) == 1


def test_partial_missing_metadata_does_not_hide_a_future_timestamp(content_case, monkeypatch, capsys):
    c = content_case
    c["receipts"]["general"][1].pop("built_utc")
    c["rewrite"]()
    c["receipts"]["system"][1]["built_utc"] = "2026-01-01T00:00:01Z"
    c["rewrite"]("system")
    rejected(c, monkeypatch, capsys)


def test_build_utc_is_covered_by_full_receipt_pin_even_outside_content(content_case, monkeypatch, capsys):
    c = content_case
    path, receipt = c["receipts"]["general"]
    original_content_pin = receipt["content_sha256"]
    receipt["built_utc"] = "2025-12-31T23:58:30Z"
    assert receipt["content_sha256"] == original_content_pin
    path.write_bytes(canon(receipt))  # Full receipt pin deliberately unchanged.
    rejected(c, monkeypatch, capsys)


def test_no_age_metadata_is_emitted_for_abstention(content_case, monkeypatch, capsys):
    c = content_case
    c["outcome"].pop("stored_text")
    assert invoke(c, monkeypatch) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["schema"] == "wd.q4-lane-caller-result.v1"
    assert value["inner_status"] == "ABSTAIN" and "index_age" not in value


def test_age_expansion_respects_existing_output_limit(content_case, monkeypatch, capsys):
    c = content_case
    c["descriptor"]["max_output_bytes"] = 1500
    c["repin"]()
    rejected(c, monkeypatch, capsys)


@pytest.mark.parametrize("cells", ["missing", "empty"])
@pytest.mark.parametrize("cutoff", [None, "2026-01-01T00:00:01Z"])
def test_cutoff_without_cells_cannot_hide_malformed_or_future_time(content_case, monkeypatch, capsys, cells, cutoff):
    c = content_case
    if cells == "missing": c["age_manifest"].pop("cells")
    else: c["age_manifest"]["cells"] = {}
    c["age_manifest"]["cutoff_utc"] = cutoff
    c["repin"]()
    rejected(c, monkeypatch, capsys)
    assert c["calls"] == []


@pytest.mark.parametrize("cutoff", [None, "2026-01-01T00:00:01Z"])
def test_receipt_cutoff_without_memory_cutoff_is_still_validated(content_case, monkeypatch, capsys, cutoff):
    c = content_case
    c["age_manifest"].pop("cutoff_utc")
    c["receipts"]["general"][1]["content"]["cutoff_utc"] = cutoff
    c["rewrite"]()
    rejected(c, monkeypatch, capsys)
    assert c["calls"] == []


def test_pointer_and_receipt_count_disagreement_refuses_before_adapter(content_case, monkeypatch, capsys):
    c = content_case
    c["age_manifest"]["cells"]["general"]["count"] = 2
    c["repin"]()
    rejected(c, monkeypatch, capsys)
    assert c["calls"] == []


def test_generation_pointer_checks_receipt_at_selected_generation(content_case, monkeypatch, capsys):
    c = content_case
    original_path, _ = c["receipts"]["general"]
    other_path = original_path.parent.parent / "gen-000002" / "receipt.json"
    other_path.parent.mkdir()
    other_path.write_bytes(b"{}")
    c["age_manifest"]["cells"]["general"]["generation"] = 2
    c["repin"]()  # The pinned hash still names the generation-one receipt.
    rejected(c, monkeypatch, capsys)
    assert c["calls"] == []
