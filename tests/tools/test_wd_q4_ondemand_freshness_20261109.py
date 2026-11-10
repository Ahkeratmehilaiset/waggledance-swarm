"""Synthetic output-boundary scope cut; never an installed freshness witness."""
import copy
import json
import pytest
from tools import wd_q4_lane_caller_cli as target
from tools.wd_q4_lane_query import CONTENT_BINDING_KEYS
from tests.tools.test_wd_q4_lane_caller_cli import (
    case, invoke, _pending_baseline, CARD, canon, sha, TOKEN_CANARY,
)


def result(status="ANSWER", text="synthetic-public-result"):
    binding = {key: "a" * 64 for key in CONTENT_BINDING_KEYS}
    items = [] if text is None else [{"ref_sha256": "b" * 64,
        "doc_id_sha256": "c" * 64, "stored_text": text}]
    answer = {"schema": "wd.q4-lane-answer.v1", "lane": "fable-5", "status": status,
        "reason": "", "result_count": len(items), "results": items,
        "binding": binding, "trust": dict(target._TRUST)}
    return {"schema": "wd.q4-lane-caller-result.v1", "status": "VALIDATED", "lane": "fable-5",
        "lane_binding": "DECLARED_CONSISTENT", "lane_authentication": "UNKNOWN",
        "cleanup": "chain_confirmed", "inner_status": status, "result_count": len(items),
        "answer": answer, "answer_sha256": sha(canon(answer)), "identity_sha256": "d" * 64,
        "descriptor_sha256": "e" * 64, "call_id": "f" * 32}


def render(value, *, token=TOKEN_CANARY, limit=65536):
    return target._output(value, "fable-5", "d" * 64, "e" * 64,
        {key: "a" * 64 for key in CONTENT_BINDING_KEYS}, limit, token)


def assert_withheld(out, original):
    assert out["schema"] == "wd.q4-lane-content-withheld.v1"
    assert out["status"] == "CONTENT_WITHHELD"
    assert out["reason"] == "SOURCE_FRESHNESS_UNVERIFIED"
    assert out["freshness"] == "UNKNOWN" and out["authority"] == "none"
    assert out["original_inner_status"] == original["inner_status"]
    assert out["original_result_count"] == original["result_count"]
    assert out["original_answer_sha256"] == original["answer_sha256"]
    for key in ("lane", "lane_binding", "lane_authentication", "cleanup", "identity_sha256", "descriptor_sha256", "call_id"):
        assert out[key] == original[key]
    for key in ("binding", "trust"):
        assert out[key] == original["answer"][key]
    assert out["original_result_refs"] == [{k: item[k] for k in ("ref_sha256", "doc_id_sha256")}
        for item in original["answer"]["results"]]
    assert "answer" not in out and "stored_text" not in json.dumps(out)


@pytest.mark.parametrize("status", ["ANSWER", "DEGRADED"])
def test_answer_is_labelled_and_degraded_is_withheld_without_mutating_original(status):
    value = result(status)
    before = copy.deepcopy(value)
    raw = render(value)
    out = json.loads(raw)
    if status == "ANSWER":
        assert out["schema"] == "wd.q4-lane-content-delivery.v1"
        assert out["freshness"] == "UNKNOWN"
        assert out["index_age"] == {"status": "UNKNOWN", "seconds": None}
        assert out["answer"] == before["answer"] and out["answer_sha256"] == before["answer_sha256"]
    else:
        assert_withheld(out, before)
        assert "synthetic-public-result" not in raw
    assert value == before
    assert raw == canon(json.loads(raw)).decode() + "\n"


@pytest.mark.parametrize("status", ["ABSTAIN", "DEGRADED"])
def test_empty_results_remain_byte_identical(status):
    value = result(status, None)
    assert render(value) == canon(value).decode() + "\n"


@pytest.mark.parametrize("fault", ["digest", "pin", "count", "schema", "oversize", "token", "trust"])
def test_original_validation_still_refuses_before_content_cut(fault):
    value = result()
    if fault == "digest": value["answer_sha256"] = "0" * 64
    elif fault == "pin": value["answer"]["binding"][next(iter(CONTENT_BINDING_KEYS))] = "0" * 64
    elif fault == "count": value["result_count"] = 0
    elif fault == "schema": value["unexpected"] = "synthetic"
    elif fault == "oversize": value["answer"]["results"][0]["stored_text"] = "x" * 321
    elif fault == "token": value["answer"]["results"][0]["stored_text"] = TOKEN_CANARY
    elif fault == "trust": value["answer"]["trust"]["freshness"] = "FRESH"
    if fault != "digest": value["answer_sha256"] = sha(canon(value["answer"]))
    with pytest.raises(ValueError): render(value)


def test_original_output_limit_still_applies_even_if_content_would_be_removed():
    value = result(text="x" * 320)
    with pytest.raises(ValueError): render(value, limit=len(canon(value)))


def test_new_metadata_token_collision_refuses_instead_of_leaking_owner_token():
    value = result()
    assert b"wd.q4-lane-content-delivery.v1" not in canon(value)
    with pytest.raises(ValueError): render(value, token="wd.q4-lane-content-delivery.v1")
    degraded = result("DEGRADED")
    assert b"CONTENT_WITHHELD" not in canon(degraded)
    with pytest.raises(ValueError): render(degraded, token="CONTENT_WITHHELD")


@pytest.mark.parametrize("status", ["ANSWER", "DEGRADED"])
@pytest.mark.parametrize("mode", ["direct", "coverage", "interrupted", "both"])
def test_direct_cli_and_all_wrappers_withhold_nonempty_content_and_release_guard(case, monkeypatch, capsys, mode, status):
    if mode == "coverage":
        from tools import wd_q4_live_snapshot_publish as live
        monkeypatch.setenv("WD_Q4_WITH_COVERAGE", "1")
        monkeypatch.setenv("WD_Q4_LIVE_MODE", "1")
        configuration = case["cli"]._configuration()
        selection_checks = []
        monkeypatch.setattr(live, "select", lambda **kw: {"configuration": configuration,
            "publication_path": "synthetic-selection", "publication_sha256": "a" * 64})
        monkeypatch.setattr(live, "recheck", lambda *args: selection_checks.append(args))
    elif mode == "both":
        monkeypatch.setenv("WD_Q4_WITH_COVERAGE", "1")
    publication = None
    if mode in ("interrupted", "both"):
        root, _, _ = _pending_baseline(case, monkeypatch)
        publication = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    case["outcome"].update(validated=True, stored_text=CARD + "2026-01-01T00:01Z")
    original = case["adapter"].call_lane_session
    seen = []
    def record(**kwargs):
        value = original(**kwargs)
        value["inner_status"] = value["answer"]["status"] = status
        value["answer_sha256"] = sha(canon(value["answer"]))
        seen.append(copy.deepcopy(value))
        return value
    monkeypatch.setattr(case["adapter"], "call_lane_session", record)
    if mode == "both":
        assert invoke(case, monkeypatch) == 2
        captured = capsys.readouterr()
        assert captured.out == "" and "lane_caller_refused" in captured.err
        assert not seen and not case["calls"]
        assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == publication
        return
    assert invoke(case, monkeypatch) == 0
    if mode == "coverage": assert len(selection_checks) == 1
    captured = capsys.readouterr()
    assert captured.err == "" and CARD not in captured.out
    out = json.loads(captured.out)
    if mode in ("interrupted", "both"):
        assert out["freshness"] == "STALE"
        out = out["result"]
        assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == publication
    if mode in ("coverage", "both"):
        assert out["freshness"] == "UNKNOWN" and out["coverage"] == "UNKNOWN"
        out = out["result"]
    assert_withheld(out, seen[0])
    runtime = case["files"]["descriptor.json"].parent / "synthetic-bridge-runtime"
    assert not list(runtime.rglob("*.pending.json")), "normal guard release must still complete"


def test_withheld_metadata_has_its_own_output_bound():
    value = result(text="")
    new_size = len(render(value).encode("ascii"))
    assert new_size > len(canon(value)) + 1
    with pytest.raises(ValueError): render(value, limit=new_size - 1)
