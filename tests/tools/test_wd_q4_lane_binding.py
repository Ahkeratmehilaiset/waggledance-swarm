"""RED-first contract for the default-off lane-binding carrier ``tools.wd_q4_lane_binding``.

Synthetic only. The carrier turns two caller-pinned documents, the fleet file (roster membership through the
existing ``wd_q4_roster_from_fleet`` producer) and a SEPARATE closed binding document (``wd.q4-lane-binding.v1``),
into exactly the pin keyword set that ``tools.wd_q4_lane_query.query_public`` takes for one lane. It reads no file,
authenticates nothing and never touches the live fleet: the committed ``ops/windows/reboot/wd-fleet.json`` carries
no binding document, so without a separately pinned one the carrier refuses and the live configuration stays OFF.
Lane membership here is a DECLARATION checked against the roster snapshot, not lane authentication.
"""
import ast
import hashlib
import importlib
import inspect
import json
from pathlib import Path

import pytest

from tools import wd_q4_lane_query as lane_query
from tools import wd_q4_roster_from_fleet as roster_mod

ROOT = Path(__file__).resolve().parents[2]
FLEET_PATH = ROOT / "ops" / "windows" / "reboot" / "wd-fleet.json"
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
REVIEW_LANES = ("claude-rco-1", "claude-rco-2")
SENTINEL = "synthetic-private-sentinel"
PIN_KEYS = ("pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256", "roster_sha256",
            "public_policy_sha256", "descriptor_sha256", "projection_sha256")
NON_PIN_PARAMETERS = frozenset({"situation", "time", "bundle_root", "public_plan_path", "projection_bytes",
                                "policy_bytes", "roster_bytes", "transport", "enabled"})
OUTPUT_KEYS = frozenset({"lane", "expected_commit", "expected_manifest_sha256", "expected_wrapper_sha256",
                         "expected_public_plan_sha256", "expected_binding"})


def module():
    return importlib.import_module("tools.wd_q4_lane_binding")


def enc(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def hex64(seed):
    return hashlib.sha256(("synthetic-pin-" + seed).encode()).hexdigest()


def uuid_for(index):
    return f"{index:08x}-abcd-4ef0-8abc-00000000{index:04x}"


def fleet(**changes):
    value = {"schema_version": 2, "fleet_name": "synthetic-fleet",
             "lanes": [{"agent": lane, "agent_uuid": uuid_for(i + 1), "role": "synthetic"}
                       for i, lane in enumerate(LANES) if lane != "codex-tools-1"],
             "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": uuid_for(9)},
             "q4_read": {"enabled": False}, "noise": SENTINEL}
    value.update(changes)
    return enc(value)


def roster_digest(fleet_bytes):
    """The trusted roster snapshot digest the pinned producer derives from ``fleet_bytes``."""
    snapshot, digest = roster_mod.build_roster_snapshot(fleet_bytes, sha(fleet_bytes), enabled=True)
    assert digest == sha(snapshot)
    return digest


def pins(fleet_bytes=None):
    binding = {key: hex64(key) for key in PIN_KEYS}
    binding["roster_sha256"] = roster_digest(fleet() if fleet_bytes is None else fleet_bytes)
    return binding


def binding_document(**changes):
    value = {"schema": "wd.q4-lane-binding.v1", "bundle_commit": "a" * 40, "manifest_sha256": hex64("manifest"),
             "wrapper_sha256": hex64("wrapper"), "public_plan_sha256": hex64("plan"),
             "binding": pins(), "lanes": list(LANES)}
    value.update(changes)
    return value


def resolve(lane="fable-5", fleet_bytes=None, document=None, binding_bytes=None, fleet_sha256=None,
            binding_sha256=None, enabled=True):
    fleet_bytes = fleet() if fleet_bytes is None else fleet_bytes
    if binding_bytes is None:
        binding_bytes = enc(binding_document() if document is None else document)
    return module().resolve_lane_binding(
        fleet_bytes, sha(fleet_bytes) if fleet_sha256 is None else fleet_sha256,
        binding_bytes, sha(binding_bytes) if binding_sha256 is None else binding_sha256, lane=lane, enabled=enabled)


def refused(reason, **kwargs):
    with pytest.raises(module().LaneBindingRefused) as exc:
        resolve(**kwargs)
    error = exc.value
    assert error.args == (reason,)
    assert error.__cause__ is None and error.__context__ is None
    assert SENTINEL not in repr(error) and "\\" not in repr(error) and "/" not in repr(error)
    return error


# ---------------------------------------------------------------------------------------------------------------
# switch and contract
# ---------------------------------------------------------------------------------------------------------------


def test_disabled_by_default_reads_nothing_and_reports_off():
    m = module()
    assert m.resolve_lane_binding(SENTINEL, None, object(), 42, lane=object()) == {"status": "OFF"}
    assert m.resolve_lane_binding(fleet(), sha(fleet()), enc(binding_document()), sha(enc(binding_document())),
                                  lane="fable-5", enabled=False) == {"status": "OFF"}


@pytest.mark.parametrize("literal", [1, 0, "true", "True", "1", None, [True], 1.0])
def test_enabled_must_be_the_bool_true(literal):
    refused("disabled", enabled=literal)


def test_output_is_exactly_the_pin_keyword_set_of_the_real_query_public_signature():
    parameters = inspect.signature(lane_query.query_public).parameters
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters.values())
    pin_parameters = frozenset(parameters) - NON_PIN_PARAMETERS
    result = resolve()
    assert frozenset(result) == pin_parameters == OUTPUT_KEYS == module().OUTPUT_KEYS
    assert frozenset(result["expected_binding"]) == lane_query.BINDING_KEYS == module().BINDING_KEYS
    assert module().SCHEMA == "wd.q4-lane-binding.v1"
    # the keyword set is accepted by query_public itself: a disabled call reaches its own switch refusal,
    # a wrong keyword would surface as TypeError instead
    with pytest.raises(lane_query.LaneQueryRefused) as exc:
        lane_query.query_public(situation="synthetic", time="2026-01-02T00:00:00.000000Z", bundle_root=".",
                                public_plan_path=".", **result)
    assert exc.value.args == ("disabled",)


def test_contract_drift_in_the_consumer_refuses_instead_of_guessing(monkeypatch):
    m = module()
    monkeypatch.setattr(m.lane_query, "BINDING_KEYS", frozenset(PIN_KEYS[:-1]))
    refused("contract_refused")


def test_resolved_values_are_the_document_pins_as_fresh_objects():
    document = binding_document()
    result = resolve(document=document)
    assert result["lane"] == "fable-5"
    assert result["expected_commit"] == document["bundle_commit"]
    assert result["expected_manifest_sha256"] == document["manifest_sha256"]
    assert result["expected_wrapper_sha256"] == document["wrapper_sha256"]
    assert result["expected_public_plan_sha256"] == document["public_plan_sha256"]
    assert result["expected_binding"] == document["binding"] and result["expected_binding"] is not document["binding"]
    again = resolve(document=document)
    result["expected_binding"]["pointer_sha256"] = "0" * 64
    assert again == resolve(document=document) and again["expected_binding"]["pointer_sha256"] == hex64("pointer_sha256")


# ---------------------------------------------------------------------------------------------------------------
# five synthetic lanes
# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("lane", LANES)
def test_every_declared_roster_lane_resolves_the_same_pins_with_its_own_name(lane):
    result = resolve(lane=lane)
    baseline = resolve(lane="fable-5")
    assert result["lane"] == lane
    assert {k: v for k, v in result.items() if k != "lane"} == {k: v for k, v in baseline.items() if k != "lane"}


@pytest.mark.parametrize("lane", REVIEW_LANES)
def test_review_lanes_may_read_but_the_carrier_claims_no_authentication(lane):
    result = resolve(lane=lane)
    assert result["lane"] == lane
    text = json.dumps(result)
    assert "authenticat" not in text and "trust" not in text
    assert module().IDENTITY == "declared_not_authenticated"
    assert "not lane authentication" in (module().__doc__ or "")


@pytest.mark.parametrize("lane", ["synthetic-lane", "codex-lead-2", "", None, 5, ["fable-5"], "FABLE-5"])
def test_unknown_or_mistyped_lane_refuses(lane):
    refused("lane_refused", lane=lane)


def test_roster_lane_not_listed_in_the_binding_document_refuses():
    refused("lane_refused", lane="fable-5", document=binding_document(lanes=["codex-lead-1"]))
    assert resolve(lane="codex-lead-1", document=binding_document(lanes=["codex-lead-1"]))["lane"] == "codex-lead-1"


# ---------------------------------------------------------------------------------------------------------------
# fleet side (roster membership through the existing producer)
# ---------------------------------------------------------------------------------------------------------------


def test_fleet_pin_mismatch_refuses_before_anything_else(monkeypatch):
    m = module()
    raw = enc(binding_document())  # built before the patch: the test helper itself runs the roster producer
    monkeypatch.setattr(m.json, "loads", lambda *a, **k: pytest.fail("binding document parsed before fleet pin"))
    refused("fleet_refused", binding_bytes=raw, fleet_sha256="0" * 64)


@pytest.mark.parametrize("bad", [
    b"[]", b"{", b"\xff\xfe", b'{"lanes":1,"tools_supervisor":{}}',
    None, "text", b"",
])
def test_fleet_shapes_the_roster_producer_refuses_are_refused(bad):
    kwargs = {"fleet_bytes": bad, "fleet_sha256": sha(bad) if isinstance(bad, bytes) else "0" * 64}
    refused("fleet_refused", **kwargs)


def test_fleet_with_an_undeclared_lane_refuses_at_the_fleet_stage():
    extra = fleet(lanes=[{"agent": lane, "agent_uuid": uuid_for(i + 1)} for i, lane in enumerate(LANES)
                         if lane != "codex-tools-1"] + [{"agent": "synthetic-lane", "agent_uuid": uuid_for(77)}])
    refused("fleet_refused", fleet_bytes=extra)


def test_fleet_missing_a_lane_refuses_even_for_a_listed_lane():
    short = fleet(lanes=[{"agent": lane, "agent_uuid": uuid_for(i + 1)} for i, lane in enumerate(LANES)
                         if lane not in ("codex-tools-1", "fable-5")])
    refused("fleet_refused", fleet_bytes=short, lane="codex-lead-1")


# ---------------------------------------------------------------------------------------------------------------
# binding document (closed, pinned, exact)
# ---------------------------------------------------------------------------------------------------------------


def test_binding_pin_mismatch_refuses_before_parsing(monkeypatch):
    m = module()
    raw = enc(binding_document())
    monkeypatch.setattr(m.json, "loads", lambda *a, **k: pytest.fail("binding document parsed with a wrong pin"))
    refused("binding_refused", binding_bytes=raw, binding_sha256="0" * 64)
    refused("binding_refused", binding_bytes=raw, binding_sha256=sha(raw).upper())


@pytest.mark.parametrize("key", ["schema", "bundle_commit", "manifest_sha256", "wrapper_sha256",
                                 "public_plan_sha256", "binding", "lanes"])
def test_missing_document_key_refuses(key):
    document = binding_document()
    del document[key]
    refused("binding_refused", document=document)


@pytest.mark.parametrize("change", [
    {"extra": SENTINEL},
    {"schema": "wd.q4-lane-binding.v2"},
    {"schema": SENTINEL},
    {"bundle_commit": "A" * 40},
    {"bundle_commit": "a" * 39},
    {"bundle_commit": hex64("x")},
    {"manifest_sha256": hex64("m").upper()},
    {"wrapper_sha256": "b" * 63},
    {"public_plan_sha256": 7},
    {"public_plan_sha256": SENTINEL},
    {"lanes": []},
    {"lanes": ["fable-5", "fable-5"]},
    {"lanes": "fable-5"},
    {"lanes": ["fable-5", "synthetic-lane"]},
    {"lanes": ["fable-5", 5]},
    {"lanes": ["fable-5", SENTINEL]},
    {"lanes": ["fable-5", {"agent": "fable-5"}]},
    {"lanes": [["fable-5"]]},
    {"lanes": [{"agent": SENTINEL}, {"agent": SENTINEL}]},
    {"lanes": ["fable-5", None]},
])
def test_document_shape_negatives_refuse_without_echo(change):
    refused("binding_refused", document=binding_document(**change))


# ---------------------------------------------------------------------------------------------------------------
# roster consistency: binding.roster_sha256 must be the digest the pinned producer derives from the pinned fleet
# ---------------------------------------------------------------------------------------------------------------


def other_fleet():
    return fleet(lanes=[{"agent": lane, "agent_uuid": uuid_for(i + 11), "role": "synthetic"}
                        for i, lane in enumerate(LANES) if lane != "codex-tools-1"],
                 tools_supervisor={"agent": "codex-tools-1", "agent_uuid": uuid_for(19)})


def test_two_valid_rosters_with_different_uuids_bind_only_their_own_produced_digest():
    fleet_a, fleet_b = fleet(), other_fleet()
    digest_a, digest_b = roster_digest(fleet_a), roster_digest(fleet_b)
    assert digest_a != digest_b
    doc_a, doc_b = binding_document(binding=pins(fleet_a)), binding_document(binding=pins(fleet_b))
    assert resolve(fleet_bytes=fleet_a, document=doc_a)["expected_binding"]["roster_sha256"] == digest_a
    assert resolve(fleet_bytes=fleet_b, document=doc_b)["expected_binding"]["roster_sha256"] == digest_b
    refused("roster_refused", fleet_bytes=fleet_b, document=doc_a)
    refused("roster_refused", fleet_bytes=fleet_a, document=doc_b)


def test_roster_digest_not_derived_from_the_pinned_fleet_refuses_for_every_lane():
    foreign = binding_document(binding={**pins(), "roster_sha256": hex64("roster_sha256")})
    for lane in LANES:
        refused("roster_refused", lane=lane, document=foreign)


def test_roster_mismatch_is_checked_after_the_document_shape_and_before_the_lane():
    doc = binding_document(binding=pins(other_fleet()))
    refused("binding_refused", document={**doc, "extra": SENTINEL})
    refused("roster_refused", lane="synthetic-lane", document=doc)
    refused("roster_refused", lane="fable-5", document={**doc, "lanes": ["codex-lead-1"]})


@pytest.mark.parametrize("change", [
    ("drop", "pointer_sha256"), ("drop", "projection_sha256"), ("extra", "memory_sha256"),
    ("extra", "receipt_sha256"), ("extra", SENTINEL), ("upper", "encoder_sha256"), ("short", "policy_sha256"),
    ("int", "roster_sha256"), ("none", "descriptor_sha256"), ("list", "source_anchor_sha256"),
])
def test_binding_pin_set_must_be_exact_lowercase_hex64(change):
    kind, key = change
    binding = pins()
    if kind == "drop":
        del binding[key]
    elif kind == "extra":
        binding[key] = hex64(key)
    elif kind == "upper":
        binding[key] = binding[key].upper()
    elif kind == "short":
        binding[key] = binding[key][:63]
    elif kind == "int":
        binding[key] = 1
    elif kind == "none":
        binding[key] = None
    elif kind == "list":
        binding[key] = [binding[key]]
    refused("binding_refused", document=binding_document(binding=binding))


@pytest.mark.parametrize("raw", [
    pytest.param(b'{"schema":"wd.q4-lane-binding.v1","schema":"' + SENTINEL.encode() + b'"}', id="duplicate_key"),
    pytest.param(b'{"schema":"wd.q4-lane-binding.v1","bundle_commit":NaN}', id="nan_constant"),
    pytest.param(b'["wd.q4-lane-binding.v1"]', id="list"),
    pytest.param(b"null", id="null"), pytest.param(b"", id="empty"), pytest.param(b"{", id="truncated"),
    pytest.param(enc(binding_document()) + b" " * 65536, id="oversize"),
])
def test_parser_details_oversize_and_non_objects_refuse(raw):
    refused("binding_refused", binding_bytes=raw)


@pytest.mark.parametrize("raw", [None, "text", 5, bytearray(b"{}")])
def test_binding_bytes_must_be_bytes(raw):
    refused("binding_refused", binding_bytes=raw, binding_sha256="0" * 64)


def test_document_at_the_byte_limit_is_accepted_and_one_more_refuses():
    m = module()
    raw = enc(binding_document())
    at_limit = raw + b" " * (m.BINDING_LIMIT - len(raw))
    assert len(at_limit) == m.BINDING_LIMIT
    assert resolve(binding_bytes=at_limit)["lane"] == "fable-5"
    refused("binding_refused", binding_bytes=at_limit + b" ")


# ---------------------------------------------------------------------------------------------------------------
# the live fleet stays inert
# ---------------------------------------------------------------------------------------------------------------


def test_committed_fleet_carries_no_binding_document_so_the_carrier_refuses_without_one():
    raw = FLEET_PATH.read_bytes()
    fleet_doc = json.loads(raw)
    assert fleet_doc.get("q4_read") == {"enabled": False}
    assert "wd.q4-lane-binding" not in raw.decode("utf-8") and "binding" not in fleet_doc.get("q4_read", {})
    refused("binding_refused", fleet_bytes=raw, binding_bytes=b"")
    refused("binding_refused", fleet_bytes=raw, binding_bytes=enc({"q4_read": {"enabled": False}}))
    # the committed roster itself is accepted by the producer, so the refusal above is the missing document only
    snapshot, _ = roster_mod.build_roster_snapshot(raw, sha(raw), enabled=True)
    assert json.loads(snapshot)["schema"] == roster_mod.SCHEMA


def test_module_is_pure_stdlib_with_fixed_refusals():
    source = Path(module().__file__).read_text(encoding="utf-8")
    assert "from None" not in source and "from exc" not in source
    for token in ("subprocess", "socket", "urllib", "open(", "Path(", "os.", "environ", "print("):
        assert token not in source, token
    imports = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or "").split(".")[0])
    assert imports == {"hashlib", "hmac", "inspect", "json", "re", "tools"}
    assert module().REASONS == frozenset({"disabled", "fleet_refused", "binding_refused", "roster_refused",
                                          "lane_refused", "contract_refused"})


# Synthetic content-v4 lane binding: exact seven fields, no authority claim.
@pytest.mark.parametrize("lane", LANES)
def test_content_binding_exact_pins_for_each_lane(lane):
    content = {key: hex64(key) for key in lane_query.CONTENT_BINDING_KEYS}
    content["roster_sha256"] = roster_digest(fleet())
    result = resolve(lane=lane, document=binding_document(binding=content))
    assert result["expected_binding"] == content


@pytest.mark.parametrize("key", sorted(lane_query.CONTENT_BINDING_KEYS))
def test_content_binding_refuses_each_missing_or_malformed_pin(key):
    content = {k: hex64(k) for k in lane_query.CONTENT_BINDING_KEYS}
    content["roster_sha256"] = roster_digest(fleet())
    missing = dict(content)
    del missing[key]
    refused("binding_refused", document=binding_document(binding=missing))
    malformed = dict(content, **{key: "z" * 64})
    refused("binding_refused", document=binding_document(binding=malformed))


def test_content_binding_refuses_additional_field_and_wrong_roster():
    content = {key: hex64(key) for key in lane_query.CONTENT_BINDING_KEYS}
    content["roster_sha256"] = roster_digest(fleet())
    refused("binding_refused", document=binding_document(binding=dict(content, extra="a" * 64)))
    content["roster_sha256"] = "0" * 64
    refused("roster_refused", document=binding_document(binding=content))


def test_content_binding_contract_mismatch_fails_closed(monkeypatch):
    content = {key: hex64(key) for key in lane_query.CONTENT_BINDING_KEYS}
    content["roster_sha256"] = roster_digest(fleet())
    document = binding_document(binding=content)
    monkeypatch.setattr(lane_query, "CONTENT_BINDING_KEYS", frozenset({"unexpected"}))
    refused("contract_refused", document=document)


def test_content_contract_does_not_depend_on_status_pin_set(monkeypatch):
    content = {key: hex64(key) for key in lane_query.CONTENT_BINDING_KEYS}
    content["roster_sha256"] = roster_digest(fleet())
    document = binding_document(binding=content)
    monkeypatch.setattr(lane_query, "BINDING_KEYS", frozenset({"not-content"}))
    assert resolve(document=document)["expected_binding"] == content
