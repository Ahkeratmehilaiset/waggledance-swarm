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


NO_GENERATION_FIELDS = ("generation", "continuity", "source_authentication", "event_time_authentication")


def no_generation_envelope(lane="codex-tools-1"):
    view = content_envelope(lane)
    view["content"]["schema"] = "wd.mind-no-generation-memory-content.v1"
    view["content"].update({name: "UNKNOWN" for name in NO_GENERATION_FIELDS})
    return view


def window_envelope(lane="codex-tools-1"):
    view = no_generation_envelope(lane)
    content = view["content"]
    content["schema"] = "wd.mind-no-generation-memory-content-window.v1"
    selection_time = datetime.fromisoformat(content["as_of_utc"].replace("Z", "+00:00")) - timedelta(seconds=1)
    content["selection"] = {"scope": "screened_declared_event_time_window",
                            "as_of_utc": selection_time.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                            "max_age_seconds": 900, "limit": 256, "truncated": False}
    return view


def retained_envelope(lane='codex-tools-1'):
    view = no_generation_envelope(lane)
    view['content'].update(schema='wd.mind-retained-f32-startup-content.v1',
        retrieval_status='SEMANTIC_CANDIDATE', retrieval_rule='q4-complete-semantic-candidate-v1',
        relevance='UNVERIFIED', index_match='NOT_APPLICABLE', max_age_seconds=900)
    return view


ALLOW_RETAINED = '''
$originalMindStartup = ${function:Invoke-WdMindStartupRead}
function Invoke-WdMindStartupRead {
 param($BundleRoot,$ExpectedManifestHash,$Lane,$TimeoutSeconds,$Worktree)
 & $originalMindStartup @PSBoundParameters -AllowRetainedContent
}
'''


@WINDOWS
def test_retained_transport_is_default_off_then_explicitly_enabled(tmp_path):
    view = retained_envelope()
    bundle, pin = synthetic_package(tmp_path, view)
    assert _run(bundle, pin)['outcome'] == 'refused'
    result = _run(bundle, pin, setup=ALLOW_RETAINED)
    assert result['outcome'] == 'success'
    assert json.loads(result['view_json'])['content']['relevance'] == 'UNVERIFIED'


@WINDOWS
@pytest.mark.parametrize('fault', ['relevance','index','status','rule','age_bool','age_small','extra',
                                    'stale','future','text','source_ref'])
def test_retained_transport_rejects_forgery_and_stale_delivery(tmp_path, fault):
    view = retained_envelope()
    content, hit = view['content'], view['content']['results'][0]
    if fault == 'relevance': content['relevance'] = 'VERIFIED'
    if fault == 'index': content['index_match'] = 'MATCH'
    if fault == 'status': content['retrieval_status'] = 'ANSWER'
    if fault == 'rule': content['retrieval_rule'] = 'q4-rule-v1.2'
    if fault == 'age_bool': content['max_age_seconds'] = True
    if fault == 'age_small': content['max_age_seconds'] = 1
    if fault == 'extra': content['extra'] = PRIVATE
    if fault == 'stale': hit['event_time_utc_floor'] = '2000-01-01T00:00:00Z'
    if fault == 'future': hit['event_time_utc_floor'] = '2100-01-01T00:00:00Z'
    if fault == 'text': hit['display_text'] = 'invented text'
    if fault == 'source_ref': hit['source_reference']['verified'] = 'UNKNOWN'
    bundle, pin = synthetic_package(tmp_path, view)
    assert _run(bundle, pin, setup=ALLOW_RETAINED)['outcome'] == 'refused'


@WINDOWS
@pytest.mark.parametrize("lane", list(UUIDS))
def test_window_content_accepted_for_all_lanes_with_selection_and_refs(tmp_path, lane):
    view = window_envelope(lane)
    bundle, pin = synthetic_package(tmp_path, view)
    worktree = tmp_path / "synthetic-worktree"
    worktree.mkdir()
    result = _run(bundle, pin, lane=lane, worktree=worktree)
    assert result["outcome"] == "success" and result["reason"] == "content_projection"
    assert json.loads(result["view_json"]) == view
    receipt = json.loads((worktree / ".codex-audit" / "wd-mind-startup-view.json").read_bytes())
    assert receipt["fleet_sha256"] == FLEET_HASH and receipt["content_source_event_refs"] == ["e" * 64]
    assert receipt["session_context_delivery"] == "NOT_PROVEN"


@WINDOWS
@pytest.mark.parametrize("fault", ["missing", "extra", "scope", "truncated_string", "limit", "limit_bool",
    "window", "window_bool", "bad_time", "future_selection", "selection_before_event", "bad_fleet",
    "bad_ref", "bad_auth", "stale_hit"])
def test_window_content_rejects_invalid_selection_and_existing_safety_checks(tmp_path, fault):
    view = window_envelope()
    content, selection = view["content"], view["content"]["selection"]
    if fault == "missing": del selection["scope"]
    elif fault == "extra": selection["extra"] = PRIVATE
    elif fault == "scope": selection["scope"] = "all_events"
    elif fault == "truncated_string": selection["truncated"] = "false"
    elif fault == "limit": selection["limit"] = 257
    elif fault == "limit_bool": selection["limit"] = True
    elif fault == "window": selection["max_age_seconds"] = 901
    elif fault == "window_bool": selection["max_age_seconds"] = True
    elif fault == "bad_time": selection["as_of_utc"] = "2026-01-01T00:00:00Z"
    elif fault == "future_selection": selection["as_of_utc"] = "2099-01-01T00:00:00.000000Z"
    elif fault == "selection_before_event": selection["as_of_utc"] = "2020-01-01T00:00:00.000000Z"
    elif fault == "bad_fleet": view["fleet_sha256"] = "0" * 64
    elif fault == "bad_ref": content["results"][0]["source_reference"]["sha256"] = "bad"
    elif fault == "bad_auth": content["source_authentication"] = "VERIFIED"
    elif fault == "stale_hit": content["results"][0]["event_age_seconds_upper_bound"] = 900
    bundle, pin = synthetic_package(tmp_path, view)
    result = _run(bundle, pin)
    assert result["outcome"] != "success" and result["view_json"] == ""
    assert PRIVATE not in json.dumps(result)


@WINDOWS
@pytest.mark.parametrize("lane", list(UUIDS))
def test_no_generation_content_envelope_and_receipt_for_each_lane(tmp_path, lane):
    view = no_generation_envelope(lane)
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
@pytest.mark.parametrize("name", NO_GENERATION_FIELDS)
@pytest.mark.parametrize("fault", ["missing", "forged"])
def test_no_generation_content_rejects_missing_or_forged_unknown_field(tmp_path, name, fault):
    view = no_generation_envelope()
    if fault == "missing":
        del view["content"][name]
    else:
        view["content"][name] = "VERIFIED"
    bundle, pin = synthetic_package(tmp_path, view)
    result = _run(bundle, pin)
    assert result["outcome"] != "success" and result["view_json"] == ""


@WINDOWS
def test_legacy_content_schema_does_not_accept_new_schema_only_fields(tmp_path):
    view = content_envelope()
    view["content"].update({name: "UNKNOWN" for name in NO_GENERATION_FIELDS})
    bundle, pin = synthetic_package(tmp_path, view)
    result = _run(bundle, pin)
    assert result["outcome"] != "success" and result["view_json"] == ""


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


def flat_envelope(lane='codex-tools-1'):
    original = content_envelope(lane)
    hit = dict(original['content']['results'][0])
    hit.update(source_agent='codex-lead-1', source_time_utc=hit['event_time_utc_floor'])
    return {'schema':'wd.mind-flat-startup-envelope.v1','authority':'none','lane':lane,
            'fleet_sha256':FLEET_HASH,'lane_authentication':'UNKNOWN','sender_authentication':'UNKNOWN',
            'content':{'schema':'wd.mind-flat-startup-content.v1','authority':'none',
                'relevance':'UNVERIFIED','index_match':'NOT_APPLICABLE','provider_authentication':'UNKNOWN',
                'source_authentication':'UNKNOWN','sender_authentication':'UNKNOWN',
                'event_time_authentication':'UNKNOWN','continuity':'UNKNOWN','same_user_aba':'UNKNOWN',
                'as_of_utc':original['content']['as_of_utc'],'results':[hit]}}


ALLOW_FLAT = '''
$originalMindStartup = ${function:Invoke-WdMindStartupRead}
function Invoke-WdMindStartupRead {
 param($BundleRoot,$ExpectedManifestHash,$Lane,$TimeoutSeconds,$Worktree)
 & $originalMindStartup @PSBoundParameters -AllowFlatContent
}
'''


@WINDOWS
@pytest.mark.parametrize('lane', list(UUIDS))
def test_flat_envelope_default_off_and_opt_in_with_receipt(tmp_path, lane):
    view = flat_envelope(lane)
    bundle, pin = synthetic_package(tmp_path, view)
    assert _run(bundle, pin, lane=lane)['outcome'] == 'refused'
    worktree = tmp_path / 'synthetic-worktree'
    worktree.mkdir()
    result = _run(bundle, pin, lane=lane, setup=ALLOW_FLAT, worktree=worktree)
    assert result['outcome'] == 'success' and result['reason'] == 'content_projection'
    receipt = json.loads((worktree / '.codex-audit' / 'wd-mind-startup-view.json').read_bytes())
    assert receipt['content_source_event_refs'] == ['e' * 64]
    assert receipt['session_context_delivery'] == 'NOT_PROVEN'


@WINDOWS
@pytest.mark.parametrize('fault', ['lane','fleet','lane_auth','sender_auth','authority','relevance','index',
    'provider_auth','source_auth','time_auth','source_agent','source_ref','source_time','text','age_bool',
    'age_mismatch','future','stale','duplicate','extra','size'])
def test_flat_envelope_rejects_bad_shape_and_delivery_age(tmp_path, fault):
    view = flat_envelope()
    content, hit = view['content'], view['content']['results'][0]
    if fault == 'lane': view['lane'] = 'fable-5'
    if fault == 'fleet': view['fleet_sha256'] = '0'*64
    if fault == 'lane_auth': view['lane_authentication'] = 'VERIFIED'
    if fault == 'sender_auth': view['sender_authentication'] = 'VERIFIED'
    if fault == 'authority': view['authority'] = 'gate'
    if fault == 'relevance': content['relevance'] = 'VERIFIED'
    if fault == 'index': content['index_match'] = 'MATCH'
    if fault == 'provider_auth': content['provider_authentication'] = 'VERIFIED'
    if fault == 'source_auth': content['source_authentication'] = 'VERIFIED'
    if fault == 'time_auth': content['event_time_authentication'] = 'VERIFIED'
    if fault == 'source_agent': hit['source_agent'] = 'claude-rco-1'
    if fault == 'source_ref': hit['source_reference']['verified'] = 'UNKNOWN'
    if fault == 'source_time': hit['source_time_utc'] = '2000-01-01T00:00:00Z'
    if fault == 'text': hit['display_text'] = 'invented'
    if fault == 'age_bool': hit['event_age_seconds_upper_bound'] = True
    if fault == 'age_mismatch': hit['event_age_seconds_upper_bound'] = 1
    if fault == 'future': hit['source_time_utc'] = hit['event_time_utc_floor'] = '2100-01-01T00:00:00Z'
    if fault == 'stale': hit['source_time_utc'] = hit['event_time_utc_floor'] = '2000-01-01T00:00:00Z'
    if fault == 'duplicate': content['results'].append(dict(hit))
    if fault == 'extra': content['unexpected'] = PRIVATE
    if fault == 'size': hit['stored_text'] = 'a'*65537
    bundle, pin = synthetic_package(tmp_path, view)
    result = _run(bundle, pin, setup=ALLOW_FLAT)
    assert result['outcome'] == 'refused' and result['view_json'] == ''


@WINDOWS
@pytest.mark.parametrize('enabled', [False, True])
def test_flat_switch_controls_explicit_pinned_cli_arguments(tmp_path, enabled):
    bundle,pin=synthetic_package(tmp_path,direct_view())
    wrapper=bundle/'Invoke-WdBridgePython.ps1'
    original=wrapper.read_text(encoding='utf-8')
    condition = "($args -notcontains '--flat-content' -or $args -notcontains '--bundle-root' -or $args -notcontains '--deployment-manifest-sha256')" if enabled else "($args -contains '--flat-content' -or $args -contains '--bundle-root' -or $args -contains '--deployment-manifest-sha256')"
    wrapper.write_text('if('+condition+'){exit 9};'+original,encoding='utf-8')
    manifest=bundle/'deployment-manifest.json'
    document=json.loads(manifest.read_bytes())
    document['files']['Invoke-WdBridgePython.ps1']=hashlib.sha256(wrapper.read_bytes()).hexdigest()
    manifest.write_bytes(json.dumps(document,separators=(',',':')).encode('ascii'))
    pin=hashlib.sha256(manifest.read_bytes()).hexdigest()
    result=_run(bundle,pin,setup=ALLOW_FLAT if enabled else '')
    assert result['outcome']=='success'


@WINDOWS
def test_flat_abstain_has_distinct_non_success_reason(tmp_path):
    bundle,pin=_package(tmp_path,exit_code=3)
    result=_run(bundle,pin,setup=ALLOW_FLAT)
    assert result['outcome']=='refused' and result['reason']=='no_candidates'


def situation_envelope():
    view = flat_envelope('codex-tools-1')
    now = datetime.now(timezone.utc).replace(microsecond=0)
    view['schema'] = 'wd.mind-flat-startup-envelope.v2'
    view['situation'] = {'schema':'wd.mind-lane-situation.v2','authority':'none','lane':'codex-tools-1',
        'scope':'latest_available_screened_within_frozen_prefix','snapshot_freshness':'UNKNOWN','continuity':'UNKNOWN',
        'sender_authentication':'UNKNOWN','source_authentication':'UNKNOWN','event_time_authentication':'UNKNOWN',
        'as_of_utc':now.strftime('%Y-%m-%dT%H:%M:%SZ'),'policy_sha256':hashlib.sha256(b'{}').hexdigest(),
        'latest_event':{'source_event_ref':'a'*64,'observed_utc':(now-timedelta(seconds=120)).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'age_seconds':120,'event_type':'message','task_id_sha256':'sha256:'+'b'*64,'text':'Synthetic approved observation'},
        'decision':'UNKNOWN','next_action':'UNKNOWN'}
    return view


def _situation_package(tmp_path, view):
    bundle, _ = synthetic_package(tmp_path, view)
    (bundle/'wd-mind-message-policy.json').write_bytes(b'{}')
    manifest=bundle/'deployment-manifest.json'
    data=json.loads(manifest.read_bytes())
    data['files']['wd-mind-message-policy.json']=hashlib.sha256(b'{}').hexdigest()
    manifest.write_bytes(json.dumps(data,separators=(',',':')).encode())
    return bundle,hashlib.sha256(manifest.read_bytes()).hexdigest()


@WINDOWS
@pytest.mark.parametrize('fault', ['ok','policy','event_ref','age','authority','freshness','extra','task_link','availability','availability_submicro'])
def test_situation_envelope_policy_and_age_fail_closed(tmp_path, fault):
    view=situation_envelope()
    situation=view['situation']
    if fault=='policy': situation['policy_sha256']='0'*64
    if fault=='event_ref': situation['latest_event']['source_event_ref']='bad'
    if fault=='age': situation['latest_event']['age_seconds']=True
    if fault=='authority': situation['authority']='verified'
    if fault=='freshness': situation['snapshot_freshness']='CURRENT'
    if fault=='task_link': situation['next_action']=dict(situation['latest_event'],task_id_sha256='sha256:'+'c'*64)
    if fault=='availability':
        now=datetime.fromisoformat(situation['as_of_utc'].replace('Z','+00:00'))
        situation['latest_event']['observed_utc']=(now-timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        situation['latest_event']['age_seconds']=1
    if fault == 'availability_submicro':
        base=datetime.now(timezone.utc).replace(second=0,microsecond=0)-timedelta(minutes=5)
        situation['as_of_utc']=(base+timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%S')+'.0000001Z'
        situation['latest_event']['observed_utc']=(base-timedelta(seconds=10)).strftime('%Y-%m-%dT%H:%M:%SZ')
        situation['latest_event']['age_seconds']=11
    if fault=='extra': situation['extra']='synthetic untrusted'
    bundle,pin=_situation_package(tmp_path,view)
    result=_run(bundle,pin,setup=ALLOW_FLAT)
    assert result['outcome'] == ('success' if fault=='ok' else 'refused')


@WINDOWS
@pytest.mark.parametrize('fault', ['ok','semantic','policy','extra','stale'])
def test_situation_only_abstain_envelope(tmp_path, fault):
    from datetime import datetime, timedelta, timezone
    view=situation_envelope()
    view.pop('content')
    view['schema']='wd.mind-situation-startup-envelope.v1'
    view['semantic_status']='ABSTAIN'
    if fault=='semantic': view['semantic_status']='ANSWER'
    if fault=='policy': view['situation']['policy_sha256']='0'*64
    if fault=='extra': view['semantic_answer']='invented'
    if fault=='stale': view['situation']['latest_event']['observed_utc']='2000-01-01T00:00:00Z'
    bundle,pin=_situation_package(tmp_path,view)
    worktree=tmp_path/'situation-receipt'
    worktree.mkdir()
    result=_run(bundle,pin,setup=ALLOW_FLAT,worktree=worktree)
    assert result['outcome'] == ('success' if fault=='ok' else 'refused')
    if fault=='ok':
        assert result['reason']=='situation_projection'
        assert json.loads(result['view_json'])['semantic_status']=='ABSTAIN'
        receipt=json.loads((worktree/'.codex-audit'/'wd-mind-startup-view.json').read_bytes())
        assert receipt['semantic_status']=='ABSTAIN' and receipt['situation_source_event_refs']==['a'*64]
        assert receipt['situation_policy_sha256']==view['situation']['policy_sha256']
        assert receipt['session_context_delivery']=='NOT_PROVEN'
