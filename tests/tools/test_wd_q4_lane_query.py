"""Synthetic lane-side public Q4 caller: default-off, injected transport only, closed privacy-filtered answer.

No installed bundle, wrapper, model, runtime, bridge or environment is touched. The transport is a test fake; the
caller under test never executes anything.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import json
from pathlib import Path
import re

import pytest

from tools import wd_q4_lane_link as link


LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
BINDING_KEYS = ("pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256", "roster_sha256",
                "public_policy_sha256")
SECRET = "synthetic-secret-situation-7f3a"
NOW = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)


def _module():
    return importlib.import_module("tools.wd_q4_lane_query")


def must_not_run(**_):
    raise AssertionError("transport must not run")


@pytest.fixture
def plan(tmp_path):
    root = tmp_path / ("a" * 40)
    root.mkdir()
    wrapper = root / "Invoke-WdQ4Query.ps1"
    wrapper.write_bytes(b"synthetic wrapper; never executed")
    wrapper_sha = hashlib.sha256(wrapper.read_bytes()).hexdigest()
    manifest = {"schema_version": 1, "source_commit": "a" * 40, "files": {"Invoke-WdQ4Query.ps1": wrapper_sha}}
    raw = json.dumps(manifest, sort_keys=True).encode()
    (root / "deployment-manifest.json").write_bytes(raw)
    document = {"schema": "wd.q4-public-wrapper-plan.v1", "config": {"descriptor_sha256": "a" * 64},
                "root": "synthetic-root", "policy_path": "synthetic-policy", "roster_path": "synthetic-roster",
                "public_policy_path": "synthetic-public-policy", "pointer_sha256": "1" * 64,
                "source_anchor_sha256": "2" * 64, "encoder_sha256": "3" * 64,
                "policy_sha256": "4" * 64, "roster_sha256": "5" * 64, "public_policy_sha256": "6" * 64}
    data = json.dumps(document, sort_keys=True).encode()
    path = tmp_path / "public-plan.json"
    path.write_bytes(data)
    binding = {key: document[key] for key in BINDING_KEYS}
    return {"bundle_root": root, "expected_commit": "a" * 40,
            "expected_manifest_sha256": hashlib.sha256(raw).hexdigest(), "expected_wrapper_sha256": wrapper_sha,
            "public_plan_path": path, "expected_public_plan_sha256": hashlib.sha256(data).hexdigest(),
            "expected_binding": binding}, document


def _reply(document, *, status="ANSWER", texts=("synthetic metadata: lane status done",)):
    binding = {key: document[key] for key in BINDING_KEYS}
    binding.update({"descriptor_sha256": document["config"]["descriptor_sha256"], "memory_sha256": "7" * 64,
                    "projection_sha256": "8" * 64, "receipt_sha256": "9" * 64})
    results = [{"stored_text": text, "ref_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "doc_id_sha256": hashlib.sha256(b"doc" + text.encode()).hexdigest(),
                "ref_kind": "sha256_of_projected_metadata", "canonical_event": False} for text in texts]
    return {"schema": "wd.q4-public-query.v2", "status": status, "binding": binding,
            "freshness": "UNKNOWN", "publisher_authentication": "UNKNOWN", "source_filter_attestation": "UNKNOWN",
            "same_user_aba": "UNKNOWN", "results": results if status != "ABSTAIN" else []}


def _transport(reply, calls=None, code=0):
    def transport(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return (reply if type(reply) is bytes else json.dumps(reply).encode()), code
    return transport


def _refused(reason, call, *args, **kwargs):
    module = _module()
    with pytest.raises(module.LaneQueryRefused, match="^" + reason + "$") as info:
        call(*args, **kwargs)
    text = repr(info.value) + repr(info.value.args) + repr(getattr(info.value, "__cause__", None))
    assert SECRET not in text and "synthetic-private" not in text
    return info.value


def test_default_off_and_missing_transport_refuse_before_any_read(tmp_path):
    module = _module()
    missing = tmp_path / "does-not-exist"
    arguments = dict(lane=LANES[0], situation=SECRET, time=NOW, bundle_root=missing, expected_commit="a" * 40,
                     expected_manifest_sha256="b" * 64, expected_wrapper_sha256="c" * 64,
                     public_plan_path=missing / "plan.json", expected_public_plan_sha256="d" * 64,
                     expected_binding={key: "e" * 64 for key in BINDING_KEYS})
    _refused("disabled", module.query_public, transport=must_not_run, **arguments)
    _refused("disabled", module.query_public, enabled=1, transport=must_not_run, **arguments)
    _refused("transport_unavailable", module.query_public, enabled=True, **arguments)
    _refused("transport_unavailable", module.query_public, enabled=True, transport="not-callable", **arguments)
    assert not missing.exists()


@pytest.mark.parametrize("lane", LANES)
def test_five_declared_lanes_send_one_bounded_query_only_through_the_injected_transport(plan, lane):
    module = _module()
    arguments, document = plan
    calls = []
    answer = module.query_public(enabled=True, lane=lane, situation=SECRET, time=NOW,
                                 transport=_transport(_reply(document), calls), **arguments)
    assert len(calls) == 1
    call = calls[0]
    assert set(call) == {"wrapper", "arguments", "stdin_bytes"}
    assert call["wrapper"] == str(arguments["bundle_root"] / "Invoke-WdQ4Query.ps1")
    assert call["arguments"] == ("-BundleRoot", str(arguments["bundle_root"]), "-ExpectedBundleCommit", "a" * 40,
                                 "-ExpectedManifestSha256", arguments["expected_manifest_sha256"], "-Public",
                                 "-PublicPlanPath", str(arguments["public_plan_path"]),
                                 "-ExpectedPublicPlanSha256", arguments["expected_public_plan_sha256"])
    assert SECRET not in repr(call["arguments"]) and SECRET not in repr(call["wrapper"])
    # the query is exactly the canonical in-memory JSON with a second-floored aware UTC time
    assert call["stdin_bytes"] == json.dumps({"situation": SECRET, "time": "2026-01-02T03:04:05Z"},
                                             sort_keys=True, separators=(",", ":")).encode("ascii")
    assert len(call["stdin_bytes"]) <= module.MAX_QUERY_BYTES
    # the answer is the closed shape, carries the lane as a declaration only, and never the situation
    assert set(answer) == {"schema", "lane", "status", "reason", "result_count", "results", "binding", "trust"}
    assert answer["schema"] == module.SCHEMA and answer["lane"] == lane and answer["status"] == "ANSWER"
    assert answer["reason"] == "" and answer["result_count"] == 1 == len(answer["results"])
    assert set(answer["results"][0]) == {"ref_sha256", "doc_id_sha256", "stored_text"}
    assert answer["results"][0]["stored_text"] == "synthetic metadata: lane status done"
    assert answer["binding"] == arguments["expected_binding"]
    assert answer["trust"] == {"lane_authentication": "UNKNOWN", "freshness": "UNKNOWN",
                               "publisher_authentication": "UNKNOWN", "transport": "caller_injected"}
    assert SECRET not in json.dumps(answer)
    assert json.dumps(answer) == json.dumps(json.loads(json.dumps(answer)))  # plain JSON data only


def test_abstain_degraded_and_runtime_refusal_project_to_fixed_shapes(plan):
    module = _module()
    arguments, document = plan
    abstain = module.query_public(enabled=True, lane=LANES[4], situation=SECRET, time=NOW,
                                  transport=_transport(_reply(document, status="ABSTAIN")), **arguments)
    assert abstain["status"] == "ABSTAIN" and abstain["results"] == [] and abstain["result_count"] == 0
    assert abstain["binding"] == arguments["expected_binding"]
    degraded = module.query_public(enabled=True, lane=LANES[4], situation=SECRET, time=NOW,
                                   transport=_transport(_reply(document, status="DEGRADED")), **arguments)
    assert degraded["status"] == "DEGRADED" and degraded["result_count"] == 1
    refused_raw = b'{"schema":"wd.q4-public-query.v2","status":"REFUSED","reason":"synthetic-private"}'
    refused = module.query_public(enabled=True, lane=LANES[4], situation=SECRET, time=NOW,
                                  transport=_transport(refused_raw, code=2), **arguments)
    assert refused == {"schema": module.SCHEMA, "lane": LANES[4], "status": "REFUSED", "reason": "runtime_refused",
                       "result_count": 0, "results": [], "binding": None,
                       "trust": {"lane_authentication": "UNKNOWN", "freshness": "UNKNOWN",
                                 "publisher_authentication": "UNKNOWN", "transport": "caller_injected"}}
    assert "synthetic-private" not in json.dumps(refused)


@pytest.mark.parametrize("field, value, reason", [
    ("lane", "unlisted", "invalid_lane"), ("lane", b"fable-5", "invalid_lane"), ("lane", None, "invalid_lane"),
    ("situation", "", "invalid_input"), ("situation", "x" * 4001, "invalid_input"),
    ("situation", "line\nbreak", "invalid_input"), ("situation", "tab\tbed", "invalid_input"),
    ("situation", b"bytes", "invalid_input"), ("situation", None, "invalid_input"),
    ("time", datetime(2026, 1, 2, 3, 4, 5), "invalid_input"), ("time", "2026-01-02T03:04:05Z", "invalid_input"),
    ("time", None, "invalid_input"),
    ("expected_commit", "A" * 40, "invalid_input"), ("expected_commit", "a" * 39, "invalid_input"),
    ("expected_manifest_sha256", "g" * 64, "invalid_input"), ("expected_wrapper_sha256", "c" * 63, "invalid_input"),
    ("expected_public_plan_sha256", "D" * 64, "invalid_input"), ("expected_public_plan_sha256", 12, "invalid_input"),
    ("expected_binding", None, "invalid_input"), ("expected_binding", {}, "invalid_input"),
    ("expected_binding", {key: "e" * 64 for key in BINDING_KEYS[:-1]}, "invalid_input"),
    ("expected_binding", {**{key: "e" * 64 for key in BINDING_KEYS}, "descriptor_sha256": "e" * 64}, "invalid_input"),
    ("expected_binding", {**{key: "e" * 64 for key in BINDING_KEYS}, "policy_sha256": "E" * 64}, "invalid_input"),
    ("bundle_root", None, "invalid_input"), ("public_plan_path", 7, "invalid_input"),
])
def test_malformed_inputs_refuse_before_binding_or_transport(plan, field, value, reason):
    module = _module()
    arguments, _ = plan
    (arguments["bundle_root"] / "deployment-manifest.json").unlink()  # binding would fail loudly if attempted
    arguments = dict(arguments, lane=LANES[0], situation=SECRET, time=NOW)
    arguments[field] = value
    _refused(reason, module.query_public, enabled=True, transport=must_not_run, **arguments)


def test_query_byte_budget_is_enforced_in_memory(plan, monkeypatch):
    module = _module()
    arguments, document = plan
    calls = []
    longest = module.query_public(enabled=True, lane=LANES[0], situation="\U0001f41d" * module.MAX_SITUATION_CHARS,
                                  time=NOW, transport=_transport(_reply(document), calls), **arguments)
    assert longest["status"] == "ANSWER" and len(calls[0]["stdin_bytes"]) <= module.MAX_QUERY_BYTES
    assert calls[0]["stdin_bytes"].isascii()  # the transport never sees raw non-ASCII bytes
    monkeypatch.setattr(module, "MAX_QUERY_BYTES", 64)
    _refused("invalid_input", module.query_public, enabled=True, lane=LANES[0], situation=SECRET, time=NOW,
             transport=must_not_run, **arguments)


@pytest.mark.parametrize("kind", ["manifest", "wrapper", "plan_bytes", "plan_pin", "commit"])
def test_mismatched_or_changed_binding_refuses_before_transport(plan, kind):
    module = _module()
    arguments, _ = plan
    if kind == "manifest":
        arguments["expected_manifest_sha256"] = "0" * 64
    elif kind == "wrapper":
        (arguments["bundle_root"] / "Invoke-WdQ4Query.ps1").write_bytes(b"changed")
    elif kind == "plan_bytes":
        arguments["public_plan_path"].write_bytes(b"tampered")
    elif kind == "plan_pin":
        arguments["expected_public_plan_sha256"] = "0" * 64
    else:
        arguments["expected_commit"] = "b" * 40
    _refused("binding_refused", module.query_public, enabled=True, lane=LANES[1], situation=SECRET, time=NOW,
             transport=must_not_run, **arguments)


@pytest.mark.parametrize("name", BINDING_KEYS)
def test_caller_pins_must_equal_the_result_binding_even_when_the_plan_agrees(plan, name):
    module = _module()
    arguments, document = plan
    arguments["expected_binding"] = dict(arguments["expected_binding"], **{name: "f" * 64})
    calls = []
    _refused("binding_refused", module.query_public, enabled=True, lane=LANES[2], situation=SECRET, time=NOW,
             transport=_transport(_reply(document), calls), **arguments)
    assert len(calls) == 1  # the plan matched, so the seam ran; the caller's own pin still decides


@pytest.mark.parametrize("text", [
    "contact name" + chr(64) + "example", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa in text", "f" * 64,
    "digest " + "0123456789abcdef" * 2, "non-ascii ä", "tab\there", "brace {x}", "", "x" * 321,
])
def test_private_shaped_result_text_refuses_the_whole_answer_without_echo(plan, text):
    module = _module()
    arguments, document = plan
    reply = _reply(document, texts=("fine text", text))
    error = _refused("privacy_refused|transport_refused", module.query_public, enabled=True, lane=LANES[3],
                     situation=SECRET, time=NOW, transport=_transport(reply), **arguments)
    assert text not in repr(error) or text == ""


@pytest.mark.parametrize("kind", ["raise", "not_json", "wrong_schema", "code_mismatch", "oversized", "none"])
def test_transport_failures_are_fixed_refusals_without_echo(plan, kind):
    module = _module()
    arguments, document = plan
    if kind == "raise":
        def transport(**_):
            raise RuntimeError("synthetic-private transport detail")
    elif kind == "not_json":
        transport = _transport(b"synthetic-private not json")
    elif kind == "wrong_schema":
        transport = _transport(dict(_reply(document), schema="wd.q4-agent-wrapper.v2"))
    elif kind == "code_mismatch":
        transport = _transport(_reply(document), code=2)
    elif kind == "oversized":
        transport = _transport(b"x" * 32769)
    else:
        transport = lambda **_: None
    _refused("transport_refused", module.query_public, enabled=True, lane=LANES[0], situation=SECRET, time=NOW,
             transport=transport, **arguments)


def test_time_is_floored_to_seconds_and_normalized_to_utc(plan):
    module = _module()
    arguments, document = plan
    calls = []
    offset = NOW.astimezone(timezone(timedelta(hours=2)))
    module.query_public(enabled=True, lane=LANES[0], situation="ok", time=offset,
                        transport=_transport(_reply(document), calls), **arguments)
    assert json.loads(calls[0]["stdin_bytes"])["time"] == "2026-01-02T03:04:05Z"


def test_module_binds_nothing_from_environment_argv_or_logs_and_declares_the_transport_interface():
    module = _module()
    source = Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("os.environ", "sys.argv", "import logging", "print(", "subprocess", "open("):
        assert forbidden not in source, forbidden
    assert module.TRANSPORT_INTERFACE == "transport(*, wrapper: str, arguments: tuple, stdin_bytes: bytes) -> (bytes, int)"
    assert module.LANES == frozenset(LANES) == link._LANES
    assert module.MAX_SITUATION_CHARS == 4000 and module.MAX_QUERY_BYTES == 65536
    assert re.fullmatch(r"wd\.q4-lane-answer\.v1", module.SCHEMA)
