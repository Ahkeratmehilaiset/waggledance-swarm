"""F19 W1: the pure input assembler hands compose() exactly what the evidence proves, and nothing else."""
import ast
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools import wd_routing_inputs as wi
from tools.wd_composer_select import digest
from tools.wd_routing_capacity import COMPOSED_SCHEMA, compose

NOW = datetime(2026, 9, 30, 19, 30, tzinfo=timezone.utc)
COMPOSE_PARAMETERS = {"task", "workers", "subjects", "rows", "paced", "signed_policy", "prepared_artifacts",
                      "routing_policy", "shadow_weights"}


def _lane(worker, subject="auth-" + "1" * 8, kind="lane", profile="codex-gpt-6.1-sol-xhigh", **extra):
    record = {"schema": wi.LANE_EVIDENCE_SCHEMA, "worker": worker, "kind": kind, "profile_id": profile,
              "subject": subject, "observed_utc": (NOW - timedelta(seconds=60)).isoformat()}
    record.update(extra)
    return record


def _task():
    return {"schema": "wd.routing-task.v1", "task_id": "codex-lead-1/w1", "revision": "v1",
            "input_digest": "a" * 64, "task_class": "implementation", "scope": ["repo:tools/x.py"],
            "author": "codex-lead-1", "created_utc": (NOW - timedelta(minutes=5)).isoformat()}


def _assemble(lanes, **overrides):
    arguments = {"task": _task(), "lanes": lanes, "rows": [], "paced": [], "prepared_artifacts": [],
                 "routing_policy": {"schema": "wd.routing-policy.v1"}, "now": NOW}
    arguments.update(overrides)
    return wi.assemble(**arguments)


def test_the_inputs_are_exactly_the_compose_parameters_and_carry_no_authority():
    result = _assemble([_lane("fable-5"), _lane(wi.GROK, subject=None, kind=wi.GROK, profile="grok-4.7")])
    assert set(result["inputs"]) == COMPOSE_PARAMETERS
    assert (result["schema"], result["authority"], result["execution_allowed"]) == (wi.SCHEMA, "none", False)
    assert result["now_utc"] == NOW.isoformat() and result["reasons"] == []
    assert [w["worker"] for w in result["inputs"]["workers"]] == ["fable-5", "grok"]
    for worker in result["inputs"]["workers"]:
        assert set(worker) == {"schema", "worker", "kind", "profile_id"}   # never capacity, load or a guess
    assert result["inputs"]["subjects"] == {"fable-5": "auth-" + "1" * 8}   # Grok never gets a subject


def test_capacity_in_lane_evidence_is_malformed_so_capacity_only_comes_through_the_adapter():
    result = _assemble([_lane("fable-5", capacity={"state": "ok"})])
    assert result["inputs"]["workers"] == [] and result["inputs"]["subjects"] == {}
    assert result["unknown"] == [{"index": 0, "worker": "fable-5", "reasons": ["lane_evidence_malformed"]}]


@pytest.mark.parametrize("record, reason", [
    (_lane("someone-else"), "foreign_worker"),
    (_lane("fable-5", kind=wi.GROK), "kind_mismatch"),
    (_lane(wi.GROK, kind="lane"), "kind_mismatch"),
    (_lane(wi.GROK, kind=wi.GROK, profile="grok-4.7"), "grok_subject_refused"),
    (_lane("fable-5", subject=""), "subject_malformed"),
    (_lane("fable-5", subject=["auth"]), "subject_malformed"),
    (_lane("fable-5", profile=""), "lane_evidence_malformed"),
    ({**_lane("fable-5"), "schema": "wd.routing-lane-evidence.v0"}, "lane_evidence_malformed"),
    ({key: value for key, value in _lane("fable-5").items() if key != "subject"}, "lane_evidence_malformed"),
    ("fable-5", "lane_evidence_malformed"),
])
def test_foreign_or_malformed_evidence_never_becomes_a_worker_or_a_subject(record, reason):
    result = _assemble([record])
    assert (result["inputs"]["workers"], result["inputs"]["subjects"]) == ([], {})
    assert result["unknown"][0]["reasons"] == [reason]


def test_duplicate_evidence_for_one_worker_is_ambiguous_and_neither_copy_is_used():
    result = _assemble([_lane("fable-5"), _lane("fable-5", subject="auth-" + "2" * 8), _lane("claude-rco-1")])
    assert [w["worker"] for w in result["inputs"]["workers"]] == ["claude-rco-1"]
    assert "fable-5" not in result["inputs"]["subjects"]
    assert [(u["index"], u["reasons"]) for u in result["unknown"]] == [
        (0, ["lane_evidence_ambiguous"]), (1, ["lane_evidence_ambiguous"])]


def test_a_missing_subject_keeps_the_worker_but_never_invents_a_subject():
    result = _assemble([_lane("fable-5", subject=None)])
    assert [w["worker"] for w in result["inputs"]["workers"]] == ["fable-5"]
    assert result["inputs"]["subjects"] == {}
    assert result["unknown"] == [{"index": 0, "worker": "fable-5", "reasons": ["subject_unknown"]}]


def test_role_and_qualification_are_copied_only_when_they_belong_to_the_worker():
    role = {"worker": "fable-5", "verified": True, "roles": ["producer"], "observed_utc": NOW.isoformat()}
    qualification = [{"task_class": "implementation", "profile_id": "codex-gpt-6.1-sol-xhigh"}]
    result = _assemble([_lane("fable-5", role=role, qualification=qualification),
                        _lane("claude-rco-1", role={**role}, qualification="not-a-list")])
    fable, rco = result["inputs"]["workers"][1], result["inputs"]["workers"][0]
    assert (fable["role"], fable["qualification"]) == (role, qualification)
    assert fable["role"] is not role and fable["qualification"] is not qualification   # copies, never shared
    assert "role" not in rco and "qualification" not in rco
    assert result["unknown"] == [{"index": 1, "worker": "claude-rco-1",
                                  "reasons": ["role_foreign_or_malformed", "qualification_malformed"]}]


def test_documents_pass_through_unchanged_and_the_task_is_never_altered():
    task, rows, paced, attempts = _task(), [{"provider": "codex"}], [{"window": "5h"}], [{"attempt_id": "x"}]
    policy, signed, shadow = {"schema": "p"}, {"schema": "wd.routing-capacity-policy.v1"}, {"weights": {}}
    before = copy.deepcopy((task, rows, paced, attempts, policy, signed, shadow))
    result = _assemble([_lane("fable-5")], task=task, rows=rows, paced=paced, prepared_artifacts=attempts,
                       routing_policy=policy, signed_policy=signed, shadow_weights=shadow)
    inputs = result["inputs"]
    assert (inputs["task"], inputs["rows"], inputs["paced"]) == (task, rows, paced)
    assert inputs["prepared_artifacts"] == attempts
    assert (inputs["routing_policy"], inputs["signed_policy"], inputs["shadow_weights"]) == (policy, signed, shadow)
    assert (task, rows, paced, attempts, policy, signed, shadow) == before


def test_a_missing_signed_policy_stays_none_and_a_supplied_one_is_never_called_verified():
    absent = _assemble([_lane("fable-5")])
    assert absent["inputs"]["signed_policy"] is None
    supplied = _assemble([_lane("fable-5")], signed_policy={"schema": "wd.routing-capacity-policy.v1"})
    for result in (absent, supplied):
        assert result["provenance"]["signed_policy"]["signature_verified"] is False


def test_provenance_is_the_parsed_document_digest_and_caller_bytes_stay_unverified():
    sources = {"rows": {"path": "C:/collector/rows.json", "byte_sha256": "b" * 64},
               "task": {"path": "C:/t.json", "byte_sha256": "B" * 64}}
    result = _assemble([_lane("fable-5")], rows=[{"provider": "codex"}], sources=sources)
    rows = result["provenance"]["rows"]
    assert rows["document_digest"] == digest([{"provider": "codex"}]) and rows["digest_basis"] == wi.DIGEST_BASIS
    assert (rows["caller_path"], rows["caller_byte_sha256"], rows["byte_digest_verified"]) == (
        "C:/collector/rows.json", "b" * 64, False)
    assert result["provenance"]["task"]["caller_byte_sha256"] is None       # an uppercase digest is refused
    assert result["reasons"] == ["source_malformed:task"]
    assert result["provenance"]["lanes"]["document_digest"] == digest([_lane("fable-5")])
    assert set(result["provenance"]) == COMPOSE_PARAMETERS - {"workers", "subjects"} | {"lanes"}


@pytest.mark.parametrize("now", [datetime(2026, 9, 30, 19, 30), "2026-09-30T19:30:00+00:00", None])
def test_only_an_aware_clock_is_accepted_and_none_is_read(now):
    result = _assemble([_lane("fable-5")], now=now)
    assert (result["now_utc"], result["reasons"]) == (None, ["now_invalid"])
    assert result["inputs"]["workers"] == [] and result["unknown"][0]["reasons"] == ["evidence_age_unknown"]


def test_lanes_that_are_not_a_list_give_no_workers():
    result = _assemble({"fable-5": _lane("fable-5")})
    assert (result["inputs"]["workers"], result["reasons"]) == ([], ["lanes_malformed"])


def test_the_assembled_inputs_drive_compose_and_capacity_stays_the_adapters():
    result = _assemble([_lane("fable-5"), _lane("claude-rco-1", subject=None),
                        _lane(wi.GROK, subject=None, kind=wi.GROK, profile="grok-4.7")])
    composed = compose(**result["inputs"], now=NOW)
    assert (composed["schema"], composed["authority"]) == (COMPOSED_SCHEMA, "none")
    assert composed["execution_allowed"] is False
    reasons = {entry["worker"]: entry["reasons"] for entry in composed["capacity"]}
    assert reasons == {"claude-rco-1": ["subject_unknown"], "fable-5": ["observation_missing"],
                       "grok": ["no_measured_grok_capacity"]}


def test_the_module_reads_no_clock_file_environment_or_provider():
    tree = ast.parse(Path(wi.__file__).read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported <= {"__future__", "copy", "datetime", "typing", "tools.bridge_pool_binding",
                        "tools.wd_composer_select", "tools.wd_task_router"}
    called = {node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
              for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert not called & {"now", "utcnow", "today", "time", "open", "read_text", "read_bytes", "getenv",
                         "run", "Popen", "system", "__import__", "eval", "exec"}


@pytest.mark.parametrize("age, reason", [
    (timedelta(seconds=wi.MAX_LANE_EVIDENCE_AGE_SECONDS + 1), "lane_evidence_stale"),
    (timedelta(seconds=-1), "lane_evidence_future"),
])
def test_stale_or_future_lane_evidence_is_withheld_whole(age, reason):
    result = _assemble([_lane("fable-5", observed_utc=(NOW - age).isoformat())])
    assert (result["inputs"]["workers"], result["inputs"]["subjects"]) == ([], {})
    assert result["unknown"] == [{"index": 0, "worker": "fable-5", "reasons": [reason]}]


@pytest.mark.parametrize("observed", ["2026-09-30T19:29:00", "yesterday", 1790000000, None])
def test_an_observed_time_without_an_offset_is_malformed(observed):
    result = _assemble([_lane("fable-5", observed_utc=observed)])
    assert result["unknown"][0]["reasons"] == ["lane_evidence_malformed"]


def test_the_age_bound_is_inclusive_at_the_adapter_sample_bound():
    edge = (NOW - timedelta(seconds=wi.MAX_LANE_EVIDENCE_AGE_SECONDS)).isoformat()
    result = _assemble([_lane("fable-5", observed_utc=edge)])
    assert result["unknown"] == [] and result["inputs"]["subjects"] == {"fable-5": "auth-" + "1" * 8}


def test_a_signed_policy_that_is_not_an_object_is_withheld_not_repaired():
    result = _assemble([_lane("fable-5")], signed_policy="signed")
    assert (result["inputs"]["signed_policy"], result["reasons"]) == (None, ["signed_policy_malformed"])


def test_sources_for_an_unknown_document_are_refused_whole():
    result = _assemble([_lane("fable-5")], sources={"workers": {"path": "x", "byte_sha256": "b" * 64}})
    assert result["reasons"] == ["sources_malformed"]
    assert all(entry["caller_path"] is None for entry in result["provenance"].values())
