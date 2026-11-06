"""P10 synthetic fixture contract; real build is explicit, Windows and caller-pinned.

The native case is not evidence when skipped. It requires Lead's exact D15
custody config and an external bounded process/cleanup contract. No fake model,
injected encoder, synthetic answer seam or private/live bridge data is used.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import wd_q4_p10_real_index_fixture as fixture

LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")


def fleet():
    entries = [{"agent": name, "agent_uuid": f"{i:08d}-1111-4111-8111-111111111111"}
               for i, name in enumerate(LANES, 1)]
    return {"lanes": [x for x in entries if x["agent"] != "codex-tools-1"],
            "tools_supervisor": next(x for x in entries if x["agent"] == "codex-tools-1")}


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def test_three_public_status_rows_are_distinct_authorized_and_no_free_text():
    case = fixture.public_status_case(raw(fleet()))
    rows = case["events"]
    assert len(rows) == 3
    assert {row["agent"] for row in rows} == {"codex-lead-1", "codex-tools-1", "fable-5"}
    for row in rows:
        assert row["type"] == "status" and row["task_id"] == "lead-mind-production-20261031"
        assert row["payload"] == {"public_status": True}
        assert row["message"] == "" and row["to"] == ""
        assert row["paths"] == row["write_scope"] == []
    assert len({raw(row) for row in rows}) == 3
    assert case["target_text"] == "status done codex-tools-1 lead-mind-production-20261031"
    assert case["query"]["time"].endswith("Z")
    assert case["model_or_query_executed"] is False


@pytest.mark.parametrize("shape", ("missing", "duplicate", "unknown", "bad_uuid"))
def test_unknown_or_ambiguous_roster_is_closed(shape):
    data = fleet()
    if shape == "missing":
        data.pop("tools_supervisor")
    elif shape == "duplicate":
        data["lanes"].append(dict(data["tools_supervisor"]))
    elif shape == "unknown":
        data["lanes"][0]["agent"] = "unrecognized-lane"
    else:
        data["tools_supervisor"]["agent_uuid"] = "not-a-uuid"
    with pytest.raises(fixture.SyntheticFixtureRefused, match="^fixture_inputs_refused$"):
        fixture.public_status_case(raw(data))


def test_duplicate_json_keys_are_refused():
    with pytest.raises(fixture.SyntheticFixtureRefused):
        fixture.public_status_case(b'{"lanes":[],"lanes":[],"tools_supervisor":{}}')


def test_guarded_route_requires_the_final_real_connector():
    # A claim string or fake result cannot replace the pending source integration.
    with pytest.raises(fixture.GuardedRouteUnavailable, match="^final_guarded_tuple_required$"):
        fixture.run_guarded_probe({"status": "VALIDATED", "cleanup": "chain_confirmed"})


def test_missing_runtime_tuple_refuses_before_creating_output(tmp_path):
    destination = tmp_path / "must-not-be-created"
    with pytest.raises(fixture.SyntheticFixtureRefused):
        fixture.build_and_capture({}, destination)
    assert not destination.exists()


_REAL_CONFIG = os.environ.get("WD_Q4_P10_REAL_CONFIG")
@pytest.mark.skipif(os.name != "nt" or not _REAL_CONFIG,
                    reason="explicit Windows caller-pinned real-model config required;not a native pass")
def test_real_e5_prefix_build_and_frozen_capture():
    assert os.environ.get("PYTEST_ADDOPTS"), "Lead's bound D15 hook must remain present"
    config = json.loads(Path(_REAL_CONFIG).read_text(encoding="utf-8"))
    output = Path(config["output_root"])
    assert not output.exists(), "one attempt only;reconcile any previous effects"
    script = Path(fixture.__file__).resolve()
    result = subprocess.run([sys.executable, "-I", "-S", "-B", str(script),
                             "--build-capture", str(Path(_REAL_CONFIG).resolve())],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True,
                            timeout=1800, check=False)
    assert result.returncode == 0, "real fixture failed;preserve output,never auto-retry"
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "REAL_PREFIX_CAPTURED_GUARDED_QUERY_NOT_RUN"
    assert receipt["records"] == 3 and receipt["fake_encoder"] is False
    assert receipt["model_identity"]["revision"] == "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    assert receipt["model_identity"]["weights_sha256"] == (
        "1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477")
    assert receipt["guarded_query"] == "NOT_RUN_FINAL_TUPLE_REQUIRED"
    assert receipt["cleanup"] == "DIRECT_PROCESS_EXIT_ONLY_NOT_GUARDED_JOB_PROOF"

