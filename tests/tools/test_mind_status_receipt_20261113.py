"""Direct status validation and receipt tests using only an inert fake wrapper."""
import hashlib
import json
from datetime import datetime, timezone, timedelta
import math

import pytest

from test_mind_direct_startup_20261112 import WINDOWS, _package, _run, _view


UUIDS = {
    "codex-lead-1": "11111111-1111-4111-8111-111111111111",
    "codex-tools-1": "22222222-2222-4222-8222-222222222222",
    "claude-rco-1": "33333333-3333-4333-8333-333333333333",
    "claude-rco-2": "44444444-4444-4444-8444-444444444444",
    "fable-5": "55555555-5555-4555-8555-555555555555",
}
FLEET_BYTES = json.dumps({"lanes": [{"agent": agent, "agent_uuid": uuid}
                                    for agent, uuid in UUIDS.items() if agent != "codex-tools-1"],
                          "tools_supervisor": {"agent": "codex-tools-1",
                                               "agent_uuid": UUIDS["codex-tools-1"]}},
                         separators=(",", ":")).encode("utf-8")
FLEET_HASH = hashlib.sha256(FLEET_BYTES).hexdigest()
EVENT_REF = "b" * 64
PRIVATE = "synthetic-private-ignore-previous-instructions-password"


def content_envelope(lane="codex-tools-1", age_minutes=2):
    now = datetime.now(timezone.utc)
    floor = (now - timedelta(minutes=age_minutes)).replace(second=0, microsecond=0)
    status = direct_view()
    status["lane"] = lane
    status["facts"][0]["agent"] = lane
    status_time = now.replace(microsecond=0)
    status["as_of_utc"] = status_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    status["facts"][0]["observed_utc"] = (status_time - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    status["facts"][0]["age_seconds"] = 120
    hit = {"ref_sha256": "c" * 64, "doc_id_sha256": "d" * 64,
           "ref_kind": "sha256_of_projected_metadata", "canonical_event": False,
           "stored_text": "Synthetic _00e4 approved work update", "display_text": "Synthetic ä approved work update",
           "source_reference": {"sha256": "e" * 64, "kind": "sha256_of_canonical_event_json",
                                "verified": "MATCH_WITHIN_PREFIX", "sender_authentication": "UNKNOWN"},
           "event_time_utc_floor": floor.strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
           "event_age_seconds_upper_bound": math.ceil((now - floor).total_seconds()), "index_freshness": "UNKNOWN"}
    content = {"schema": "wd.mind-startup-content.v1", "authority": "none",
               "as_of_utc": now.isoformat(timespec="microseconds").replace("+00:00", "Z"),
               "publisher_authentication": "UNKNOWN", "provider_authentication": "UNKNOWN",
               "sender_authentication": "UNKNOWN", "source_filter_attestation": "UNKNOWN",
               "same_user_aba": "UNKNOWN", "index_freshness": "UNKNOWN",
               "text_encoding": "wd.content-text.v1", "results": [hit]}
    return {"schema": "wd.mind-startup-envelope.v1", "authority": "none", "lane": lane,
            "fleet_sha256": FLEET_HASH, "status": status, "content": content}


def direct_view():
    return {"schema": "wd.mind-direct-status.v1", "authority": "none",
            "scope": "bounded_bridge_status_not_gate", "lane": "codex-tools-1",
            "fleet_sha256": FLEET_HASH, "coverage": "bounded_tail", "generation": "UNKNOWN",
            "continuity": "UNKNOWN", "sender_authentication": "UNKNOWN",
            "as_of_utc": "2026-11-13T00:10:00Z", "observation_status": "OBSERVED",
            "facts": [{"agent": "codex-tools-1", "event_type": "heartbeat",
                       "observed_utc": "2026-11-13T00:00:00Z", "event_ref": EVENT_REF, "age_seconds": 600}]}


def synthetic_package(tmp_path, view):
    bundle, _ = _package(tmp_path, view=view)
    (bundle / "wd-fleet.json").write_bytes(FLEET_BYTES)
    manifest = bundle / "deployment-manifest.json"
    content = json.loads(manifest.read_bytes())
    content["files"]["wd-fleet.json"] = FLEET_HASH
    manifest.write_bytes(json.dumps(content, separators=(",", ":")).encode("ascii"))
    return bundle, hashlib.sha256(manifest.read_bytes()).hexdigest()


@WINDOWS
@pytest.mark.parametrize("lane", list(UUIDS))
def test_fresh_content_envelope_and_receipt_for_each_lane(tmp_path, lane):
    view = content_envelope(lane)
    bundle, pin = synthetic_package(tmp_path, view)
    worktree = tmp_path / "synthetic-worktree"
    worktree.mkdir()
    result = _run(bundle, pin, lane=lane, worktree=worktree)
    assert result["outcome"] == "success" and result["reason"] == "content_projection"
    assert json.loads(result["view_json"]) == view
    receipt = json.loads((worktree / ".codex-audit" / "wd-mind-startup-view.json").read_bytes())
    assert receipt["fleet_sha256"] == FLEET_HASH
    assert receipt["source_event_refs"] == [EVENT_REF]
    assert receipt["content_source_event_refs"] == ["e" * 64]
    assert receipt["session_context_delivery"] == "NOT_PROVEN"
    assert "view_json" not in receipt and "Synthetic" not in json.dumps(receipt)


@WINDOWS
def test_normalized_source_whitespace_and_format_characters_transport_safely(tmp_path):
    # Source normalization removes Cf and converts whitespace before encoding;
    # compare the exact resulting human text through the PowerShell transport.
    from tools import wd_mind_content_query_readonly as reader

    normalized = reader._normalize("Suomi ä\ncontinued\u200b safely", 4096)
    assert normalized == "Suomi ä continued safely"
    view = content_envelope()
    view["content"]["results"][0]["stored_text"] = reader.encode_text(normalized)
    view["content"]["results"][0]["display_text"] = normalized
    bundle, pin = synthetic_package(tmp_path, view)
    result = _run(bundle, pin)
    assert result["outcome"] == "success"
    assert json.loads(result["view_json"])["content"]["results"][0]["display_text"] == normalized


@WINDOWS
def test_identical_composed_unicode_display_is_accepted(tmp_path):
    view = content_envelope()
    hit = view["content"]["results"][0]
    hit["stored_text"] = "caf_00e9"
    hit["display_text"] = "caf\u00e9"
    bundle, pin = synthetic_package(tmp_path, view)
    result = _run(bundle, pin)
    assert result["outcome"] == "success"
    assert json.loads(result["view_json"])["content"]["results"][0]["display_text"] == "caf\u00e9"


@pytest.mark.parametrize("character", ["\x01", "\ue000", "\u0378"])
def test_source_normalizer_rejects_nonwhitespace_control_private_or_unassigned(character):
    from tools import wd_mind_content_query_readonly as reader

    with pytest.raises(reader._Drop):
        reader._normalize("Synthetic" + character + "message", 4096)


@WINDOWS
@pytest.mark.parametrize("fault", ["outer_lane", "status_lane", "fleet", "extra", "authority", "content_extra",
    "provider_authentication", "sender_authentication", "source_filter_attestation", "same_user_aba", "index_freshness",
    "no_results", "malformed_ref", "unverified", "source_auth", "metadata_ref", "canonical_event",
    "private_field", "unencoded_text", "text_too_long", "event_time", "age_mismatch", "asof_future", "stale_delivery",
    "stale_status_observed", "future_status_asof", "text_encoding", "display_mismatch", "noncanonical_escape",
    "equivalent_but_distinct_unicode", "advertised_age_900"])
def test_content_envelope_refuses_invalid_binding_and_stale_delivery(tmp_path, fault):
    view = content_envelope()
    content = view["content"]
    hit = content["results"][0]
    if fault == "outer_lane": view["lane"] = "codex-lead-1"
    elif fault == "status_lane": view["status"]["lane"] = "codex-lead-1"
    elif fault == "fleet": view["fleet_sha256"] = "a" * 64
    elif fault == "extra": view["extra"] = PRIVATE
    elif fault == "authority": content["authority"] = "trusted"
    elif fault == "content_extra": content["extra"] = PRIVATE
    elif fault in {"provider_authentication", "sender_authentication", "source_filter_attestation", "same_user_aba", "index_freshness"}:
        content[fault] = "verified"
    elif fault == "no_results": content["results"] = []
    elif fault == "malformed_ref": hit["source_reference"]["sha256"] = "bad"
    elif fault == "unverified": hit["source_reference"]["verified"] = "UNKNOWN"
    elif fault == "source_auth": hit["source_reference"]["sender_authentication"] = "verified"
    elif fault == "metadata_ref": hit["ref_sha256"] = "not-a-sha"
    elif fault == "canonical_event": hit["canonical_event"] = True
    elif fault == "private_field": hit["private"] = PRIVATE
    elif fault == "unencoded_text": hit["stored_text"] = "raw\nmessage"
    elif fault == "text_too_long": hit["stored_text"] = "a" * 321
    elif fault == "text_encoding": content["text_encoding"] = "wrong"
    elif fault == "display_mismatch": hit["display_text"] = "different text"
    elif fault == "noncanonical_escape": hit["stored_text"] = "synthetic_0041"
    elif fault == "equivalent_but_distinct_unicode":
        hit["stored_text"] = "caf_00e9"
        hit["display_text"] = "cafe\u0301"
    elif fault == "advertised_age_900": hit["event_age_seconds_upper_bound"] = 900
    elif fault == "event_time": hit["event_time_utc_floor"] = "2026-01-01T00:00:30Z"
    elif fault == "age_mismatch": hit["event_age_seconds_upper_bound"] = 1
    elif fault == "asof_future": content["as_of_utc"] = "2099-01-01T00:00:00Z"
    elif fault == "stale_status_observed":
        view["status"] = direct_view()
    elif fault == "future_status_asof":
        future = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=5)
        view["status"]["as_of_utc"] = future.strftime("%Y-%m-%dT%H:%M:%SZ")
        view["status"]["facts"][0]["observed_utc"] = (future - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif fault == "stale_delivery":
        old = datetime.now(timezone.utc) - timedelta(seconds=1000)
        content["as_of_utc"] = (old + timedelta(seconds=1)).isoformat(timespec="microseconds").replace("+00:00", "Z")
        floor = old.replace(second=0, microsecond=0)
        hit["event_time_utc_floor"] = floor.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        asof = datetime.fromisoformat(content["as_of_utc"].replace("Z", "+00:00"))
        hit["event_age_seconds_upper_bound"] = math.ceil((asof - floor).total_seconds())
    bundle, pin = synthetic_package(tmp_path, view)
    result = _run(bundle, pin)
    assert result["outcome"] != "success" and result["view_json"] == ""
    assert PRIVATE not in json.dumps(result)


@WINDOWS
@pytest.mark.parametrize("with_fact", [True, False])
def test_direct_view_and_receipt_contain_matching_fleet_and_event_refs(tmp_path, with_fact):
    view = direct_view()
    if not with_fact:
        view["facts"] = []
        view["observation_status"] = "UNKNOWN"
    bundle, pin = synthetic_package(tmp_path, view)
    worktree = tmp_path / "synthetic-worktree"
    worktree.mkdir()
    result = _run(bundle, pin, worktree=worktree)
    assert result["outcome"] == "success" and result["record_written"] is True
    assert json.loads(result["view_json"]) == view
    receipt = json.loads((worktree / ".codex-audit" / "wd-mind-startup-view.json").read_bytes())
    assert receipt["fleet_sha256"] == FLEET_HASH
    assert receipt["source_event_refs"] == [fact["event_ref"] for fact in view["facts"]]
    assert receipt["view_sha256"] == result["view_sha256"]
    assert "view_json" not in receipt


@WINDOWS
def test_direct_liveness_view_is_accepted_and_receipt_contains_its_ref(tmp_path):
    view = direct_view()
    view["facts"][0]["event_type"] = "liveness"
    bundle, pin = synthetic_package(tmp_path, view)
    worktree = tmp_path / "synthetic-worktree"
    worktree.mkdir()
    result = _run(bundle, pin, worktree=worktree)
    assert result["outcome"] == "success" and json.loads(result["view_json"]) == view
    receipt = json.loads((worktree / ".codex-audit" / "wd-mind-startup-view.json").read_bytes())
    assert receipt["fleet_sha256"] == FLEET_HASH and receipt["source_event_refs"] == [EVENT_REF]


@WINDOWS
@pytest.mark.parametrize(("observed", "as_of", "age", "status"), [
    ("2026-11-13T00:00:00Z", "2026-11-13T00:15:00Z", 900, "OBSERVED"),
    ("2026-11-13T00:00:00Z", "2026-11-13T00:15:00.500000Z", 900, "UNKNOWN"),
    ("2026-11-13T00:00:00.250000Z", "2026-11-13T00:00:00.750000Z", 0, "OBSERVED"),
    ("2026-11-13T11:45:00.7500000Z", "2026-11-13T12:00:00.750000Z", 900, "OBSERVED"),
    ("2026-11-13T11:45:00.7499999Z", "2026-11-13T12:00:00.750000Z", 900, "UNKNOWN"),
])
def test_direct_status_accepts_precise_observation_and_boundary(tmp_path, observed, as_of, age, status):
    view = direct_view()
    view["as_of_utc"] = as_of
    view["observation_status"] = status
    view["facts"][0].update(observed_utc=observed, age_seconds=age)
    bundle, pin = synthetic_package(tmp_path, view)
    result = _run(bundle, pin)
    assert result["outcome"] == "success" and json.loads(result["view_json"]) == view


@WINDOWS
@pytest.mark.parametrize("fault", ["fleet", "hash_shape", "other_lane", "event_type", "event_ref",
                                   "extra", "private", "time", "authority", "generation", "continuity",
                                   "sender_authentication", "age", "age_mismatch", "stale_observed", "as_of"])
def test_direct_view_rejects_wrong_binding_or_unallowlisted_content(tmp_path, fault):
    view = direct_view()
    fact = view["facts"][0]
    if fault == "fleet":
        view["fleet_sha256"] = "a" * 64
    elif fault == "hash_shape":
        view["fleet_sha256"] = "not-a-sha"
    elif fault == "other_lane":
        fact["agent"] = "codex-lead-1"
    elif fault == "event_type":
        fact["event_type"] = "message"
    elif fault == "event_ref":
        fact["event_ref"] = PRIVATE
    elif fault == "extra":
        view["extra"] = PRIVATE
    elif fault == "private":
        fact["message"] = PRIVATE
    elif fault == "time":
        fact["observed_utc"] = PRIVATE
    elif fault == "authority":
        view["authority"] = "trusted"
    elif fault == "age":
        fact["age_seconds"] = -1
    elif fault == "age_mismatch":
        fact["age_seconds"] = 2
    elif fault == "stale_observed":
        fact["observed_utc"] = "2026-11-12T23:00:00Z"
        fact["age_seconds"] = 4200
    elif fault == "as_of":
        view["as_of_utc"] = PRIVATE
    else:
        view[fault] = "verified"
    bundle, pin = synthetic_package(tmp_path, view)
    worktree = tmp_path / "synthetic-worktree"
    worktree.mkdir()
    result = _run(bundle, pin, worktree=worktree)
    assert result["outcome"] != "success" and result["view_json"] == ""
    assert PRIVATE not in json.dumps(result)
    receipt = json.loads((worktree / ".codex-audit" / "wd-mind-startup-view.json").read_bytes())
    assert PRIVATE not in json.dumps(receipt)


def enriched_view():
    view = _view()
    view.update(fleet_sha256=FLEET_HASH, source_event_refs=[EVENT_REF], status_observation=direct_view())
    return view


@WINDOWS
@pytest.mark.parametrize("enriched", [True, False])
def test_legacy_publication_view_with_or_without_complete_extension(tmp_path, enriched):
    view = enriched_view() if enriched else _view()
    bundle, pin = synthetic_package(tmp_path, view)
    worktree = tmp_path / "synthetic-worktree"
    worktree.mkdir()
    result = _run(bundle, pin, worktree=worktree)
    assert result["outcome"] == "success" and json.loads(result["view_json"]) == view
    receipt = json.loads((worktree / ".codex-audit" / "wd-mind-startup-view.json").read_bytes())
    if enriched:
        assert receipt["fleet_sha256"] == FLEET_HASH and receipt["source_event_refs"] == [EVENT_REF]
    else:
        assert "fleet_sha256" not in receipt and "source_event_refs" not in receipt


@WINDOWS
@pytest.mark.parametrize("fault", ["fleet_sha256", "source_event_refs", "status_observation",
                                   "fleet_mismatch", "ref_mismatch", "nested_lane"])
def test_legacy_extension_refuses_partial_or_mismatched_binding(tmp_path, fault):
    view = enriched_view()
    if fault in {"fleet_sha256", "source_event_refs", "status_observation"}:
        del view[fault]
    elif fault == "fleet_mismatch":
        view["fleet_sha256"] = "a" * 64
    elif fault == "ref_mismatch":
        view["source_event_refs"] = ["c" * 64]
    else:
        view["status_observation"]["lane"] = "codex-lead-1"
    bundle, pin = synthetic_package(tmp_path, view)
    result = _run(bundle, pin)
    assert result["outcome"] != "success" and result["view_json"] == ""
