"""C4: the optional pinned card decision digest through the lane binding, the lane caller CLI and the session config.

Synthetic only, default OFF. Without ``card_decision_sha256`` every set and output is the v6 one; with it the pin
must be present and equal in the session config ``expected_binding``, the lane binding document, the pinned public
plan (``wd.q4-public-wrapper-plan.v2``) and the lane answer binding. Nothing here produces a plan, a decision or a
binding document; the adapter is the CLI test recorder, so no native peer, cleanup or live route is claimed.
"""
import importlib
import json

import pytest

from tests.tools import test_wd_q4_lane_binding as lb
from tests.tools import test_wd_q4_lane_caller_cli as cc
from tests.tools import test_wd_q4_shared_session_config as sc
from tests.tools.test_wd_q4_lane_caller_cli import case  # noqa: F401  (pytest fixture)
from tools import wd_q4_lane_query as lane_query

CARD = "card_decision_sha256"
PIN = "9a" * 32
OTHER = "8b" * 32


def binding_module():
    return importlib.import_module("tools.wd_q4_lane_binding")


# ---------------------------------------------------------------------------------------------------------------
# lane binding carrier
# ---------------------------------------------------------------------------------------------------------------


def card_document(pin=PIN):
    binding = lb.pins()
    binding[CARD] = pin
    return lb.binding_document(binding=binding)


def test_card_binding_keys_are_the_v6_keys_plus_the_card_pin_and_match_the_lane_query():
    m = binding_module()
    assert m.CARD_BINDING_KEYS == m.BINDING_KEYS | {CARD} == lane_query.CARD_BINDING_KEYS
    assert CARD not in m.BINDING_KEYS


def test_card_binding_document_resolves_with_the_card_pin_in_expected_binding():
    document = card_document()
    result = lb.resolve(document=document)
    assert list(result["expected_binding"]) == sorted(binding_module().CARD_BINDING_KEYS)
    assert result["expected_binding"] == document["binding"]
    assert result["expected_binding"] is not document["binding"]
    assert frozenset(result) == lb.OUTPUT_KEYS


def test_v6_binding_document_output_is_unchanged():
    result = lb.resolve()
    assert list(result["expected_binding"]) == sorted(lb.PIN_KEYS)
    assert CARD not in result["expected_binding"]


@pytest.mark.parametrize("pin", [PIN.upper(), PIN[:63], 1, None, [PIN], ""])
def test_card_pin_must_be_lowercase_hex64(pin):
    lb.refused("binding_refused", document=card_document(pin))


@pytest.mark.parametrize("name", ["card_decision", "CARD_DECISION_SHA256", "card_decision_sha256 "])
def test_only_the_exact_card_key_name_is_optional(name):
    binding = lb.pins()
    binding[name] = PIN
    lb.refused("binding_refused", document=lb.binding_document(binding=binding))


def test_card_document_without_a_v6_key_refuses():
    document = card_document()
    del document["binding"]["projection_sha256"]
    lb.refused("binding_refused", document=document)


def test_card_contract_drift_in_the_lane_query_refuses(monkeypatch):
    monkeypatch.setattr(binding_module().lane_query, "CARD_BINDING_KEYS", frozenset(lb.PIN_KEYS))
    lb.refused("contract_refused", document=card_document())
    lb.refused("contract_refused")


# ---------------------------------------------------------------------------------------------------------------
# lane caller CLI (adapter is the recorder of the CLI tests)
# ---------------------------------------------------------------------------------------------------------------


def card_pins(case, monkeypatch, *, plan=PIN, document=PIN, descriptor=PIN):
    """Rewrite the synthetic plan, binding document and descriptor; ``None`` leaves the card pin out there."""
    files, desc = case["files"], case["descriptor"]
    expected = desc["expected_binding"]  # the recorder answers with this same dict
    expected.pop(CARD, None)
    if descriptor is not None:
        expected[CARD] = descriptor
    plan_doc = json.loads(files["public-plan.json"].read_bytes())
    if plan is not None:
        plan_doc.update(schema="wd.q4-public-wrapper-plan.v2", card_decision_sha256=plan,
                        card_decision_path=str(files["descriptor.json"].parent / "synthetic-decision.json"))
    plan_raw = cc.canon(plan_doc)
    files["public-plan.json"].write_bytes(plan_raw)
    binding = {key: value for key, value in expected.items() if key != CARD}
    if document is not None:
        binding[CARD] = document
    binding_raw = cc.canon({"schema": "wd.q4-lane-binding.v1", "public_plan_sha256": cc.sha(plan_raw),
                            "binding": binding})
    files["binding.json"].write_bytes(binding_raw)
    desc["binding_sha256"] = cc.sha(binding_raw)
    descriptor_raw = cc.canon(desc)
    files["descriptor.json"].write_bytes(descriptor_raw)
    monkeypatch.setenv("WD_Q4_SHARED_DESCRIPTOR_SHA256", cc.sha(descriptor_raw))


def tamper_answer(case, monkeypatch, change):
    original = case["adapter"].call_lane_session

    def wrapped(**kwargs):
        result = original(**kwargs)
        change(result["answer"]["binding"])
        result["answer_sha256"] = cc.sha(cc.canon(result["answer"]))
        return result

    monkeypatch.setattr(case["adapter"], "call_lane_session", wrapped)


def validated(case, monkeypatch, capsys):
    # A returned card must still be CURRENT at the CLI's observed end (case clock 2026-01-01T00:00Z).
    case["outcome"].update(validated=True,
                           stored_text="synthetic public card record head:unknown until:2026-01-01T00:01Z")
    code = cc.invoke(case, monkeypatch)
    return code, capsys.readouterr()


def test_cli_card_pin_in_config_document_plan_and_answer_is_published(case, monkeypatch, capsys):
    card_pins(case, monkeypatch)
    code, observed = validated(case, monkeypatch, capsys)
    assert code == 0 and observed.err == ""
    result = json.loads(observed.out)
    assert result["status"] == "CONTENT_WITHHELD" and result["original_inner_status"] == "ANSWER"
    assert "stored_text" not in observed.out and result["original_result_count"] == 1
    assert result["binding"][CARD] == PIN
    assert list(result["binding"]) == sorted(case["descriptor"]["expected_binding"])
    assert len(case["calls"]) == 1
    assert json.loads(case["calls"][0]["descriptor_bytes"])["expected_binding"][CARD] == PIN


def test_cli_v6_answer_still_has_exactly_the_eight_keys(case, monkeypatch, capsys):
    code, observed = validated(case, monkeypatch, capsys)
    assert code == 0
    assert list(json.loads(observed.out)["binding"]) == sorted(lb.PIN_KEYS)


@pytest.mark.parametrize("pins", [
    {"plan": None},                               # config and document pin a card, the plan is v1
    {"descriptor": None, "document": None},       # v2 plan, v6 config and document
    {"plan": OTHER},                              # plan pins another decision
    {"document": None},                           # document lost the card pin
    {"document": OTHER},                          # document pins another decision
    {"descriptor": None},                         # config lost the card pin
    {"descriptor": PIN.upper(), "document": PIN.upper(), "plan": PIN.upper()},
    {"descriptor": PIN[:63], "document": PIN[:63], "plan": PIN[:63]},
], ids=["v1-plan", "v6-config-v2-plan", "plan-other", "document-missing", "document-other", "config-missing",
        "uppercase", "short"])
def test_cli_card_pin_disagreement_refuses_before_the_adapter(case, monkeypatch, capsys, pins):
    card_pins(case, monkeypatch, **pins)
    code, observed = validated(case, monkeypatch, capsys)
    assert code == 2 and observed.out == "" and observed.err == cc.REFUSAL
    assert case["calls"] == []


def test_cli_config_with_another_extra_binding_key_still_refuses(case, monkeypatch, capsys):
    card_pins(case, monkeypatch)
    case["descriptor"]["expected_binding"]["memory_sha256"] = PIN
    card_pins(case, monkeypatch)
    code, observed = validated(case, monkeypatch, capsys)
    assert code == 2 and case["calls"] == []


@pytest.mark.parametrize("card,change", [
    (True, lambda binding: binding.pop(CARD)),
    (True, lambda binding: binding.update({CARD: OTHER})),
    (False, lambda binding: binding.update({CARD: PIN})),
], ids=["answer-missing", "answer-other", "answer-extra-in-v6"])
def test_cli_answer_binding_must_carry_exactly_the_configured_card_pin(case, monkeypatch, capsys, card, change):
    if card:
        card_pins(case, monkeypatch)
    tamper_answer(case, monkeypatch, change)
    code, observed = validated(case, monkeypatch, capsys)
    assert code == 2 and observed.out == ""
    assert len(case["calls"]) == 1


# ---------------------------------------------------------------------------------------------------------------
# Get-WdQ4SharedSessionConfig.ps1 (Windows PowerShell)
# ---------------------------------------------------------------------------------------------------------------

ps = pytest.mark.skipif(not sc.SHELL, reason="Windows PowerShell required")
REFUSED = {"ok": False, "reason": "q4_shared_session_config_refused"}


@ps
@pytest.mark.parametrize("lane", sc.LANES)
def test_session_config_card_pin_is_optional_and_exposed(tmp_path, lane):
    result, doc = sc.run_v2(tmp_path, mutate=lambda d: d["expected_binding"].update({CARD: PIN}), lane=lane)
    assert result["ok"], result
    assert result["value"]["expected_binding"] == doc["expected_binding"]
    assert len(result["value"]["expected_binding"]) == 9
    assert result["value"]["worker"] == sc.expected_v2_worker(tmp_path, doc, lane)


@ps
def test_session_config_without_card_pin_keeps_eight_keys(tmp_path):
    result, doc = sc.run_v2(tmp_path)
    assert result["ok"] and CARD not in result["value"]["expected_binding"]
    assert len(result["value"]["expected_binding"]) == 8


@ps
@pytest.mark.parametrize("change", [
    lambda b: b.update({CARD: PIN.upper()}),
    lambda b: b.update({CARD: PIN[:63]}),
    lambda b: b.update({CARD: ""}),
    lambda b: b.update({CARD: 1}),
    lambda b: b.update({CARD: None}),
    lambda b: b.update({"CARD_DECISION_SHA256": PIN}),
    lambda b: b.update({"card_decision": PIN}),
    lambda b: b.update({CARD: PIN, "memory_sha256": PIN}),
    lambda b: (b.update({CARD: PIN}), b.pop("pointer_sha256")),
], ids=["upper", "short", "empty", "int", "null", "upper-name", "wrong-name", "card-plus-extra", "card-minus-v6"])
def test_session_config_card_pin_shape_refuses(tmp_path, change):
    result, _ = sc.run_v2(tmp_path, mutate=lambda d: change(d["expected_binding"]))
    assert result == REFUSED


# ---------------------------------------------------------------------------------------------------------------
# lane caller adapter (the existing chain entry is the adapter test recorder)
# ---------------------------------------------------------------------------------------------------------------

from tests.tools import test_wd_q4_lane_caller_adapter as ad  # noqa: E402
from tests.tools.test_wd_q4_lane_caller_adapter import chain, data  # noqa: E402,F401  (pytest fixtures)


def card_adapter_data(data, pin=PIN):
    binding = {key: "2" * 64 for key in ad.BINDING_KEYS}
    binding[CARD] = pin
    descriptor = ad.descriptor_for(data["inputs"], expected_binding=binding)
    identity = ad.identity_for(descriptor)
    data["kw"].update(identity_bytes=identity, expected_identity_sha256=ad.sha(identity),
                      descriptor_bytes=descriptor, expected_descriptor_sha256=ad.sha(descriptor))
    data["descriptor"] = descriptor
    return data


def test_adapter_card_descriptor_and_matching_answer_validate(data, chain):
    calls, state = chain
    card_adapter_data(data)
    state["outcome"] = ad.answer_for(ad.LANE, data["descriptor"])
    assert state["outcome"]["binding"][CARD] == PIN
    result = ad.call(data)
    assert result["status"] == "VALIDATED" and result["answer"]["binding"][CARD] == PIN
    assert len(calls) == 1 and set(calls[0]) == set(data["inputs"]) | {"lane", "situation", "time", "enabled"}


@pytest.mark.parametrize("change", [
    lambda binding: binding.pop(CARD),
    lambda binding: binding.update({CARD: OTHER}),
    lambda binding: binding.update({CARD: PIN.upper()}),
], ids=["answer-missing", "answer-other", "answer-upper"])
def test_adapter_card_answer_must_carry_the_descriptor_pin(data, chain, change):
    _, state = chain
    card_adapter_data(data)
    answer = ad.answer_for(ad.LANE, data["descriptor"])
    change(answer["binding"])
    state["outcome"] = answer
    result = ad.call(data)
    assert result["status"] == "REFUSED" and result["inner_status"] == "output_refused" and result["answer"] is None


def test_adapter_v6_descriptor_refuses_an_answer_with_a_card_pin(data, chain):
    _, state = chain
    answer = ad.answer_for(ad.LANE, data["descriptor"])
    answer["binding"][CARD] = PIN
    state["outcome"] = answer
    result = ad.call(data)
    assert result["status"] == "REFUSED" and result["inner_status"] == "output_refused"


@pytest.mark.parametrize("pin,name", [(PIN.upper(), CARD), (PIN[:63], CARD), (1, CARD), (PIN, "card_decision"),
                                      (PIN, "CARD_DECISION_SHA256")],
                         ids=["upper", "short", "int", "wrong-name", "upper-name"])
def test_adapter_card_descriptor_shape_refuses_before_the_chain(data, chain, pin, name):
    calls, _ = chain
    binding = {key: "2" * 64 for key in ad.BINDING_KEYS}
    binding[name] = pin
    descriptor = ad.descriptor_for(data["inputs"], expected_binding=binding)
    identity = ad.identity_for(descriptor)
    with pytest.raises(ad.adapter().CallerRefused):
        ad.call(data, identity_bytes=identity, expected_identity_sha256=ad.sha(identity),
                descriptor_bytes=descriptor, expected_descriptor_sha256=ad.sha(descriptor))
    assert calls == []


# ---------------------------------------------------------------------------------------------------------------
# lane binding producer
# ---------------------------------------------------------------------------------------------------------------

from sandbox.mind.step2 import shared_card_projection as scp  # noqa: E402
from tests.tools import test_wd_q4_lane_binding_producer as bp  # noqa: E402
from tools import wd_q4_secure_transport as transport  # noqa: E402


def _producer_inputs(projection=None):
    """The producer test case, rebuilt here so the snapshot can carry a substituted projection."""
    from tests.tools.test_wd_q4_fixed_result_adapter import make_case
    synth = make_case(canonical_roster=True)
    roster = json.loads(synth["roster_bytes"])
    fleet = bp.raw({"lanes": roster["lanes"], "tools_supervisor": roster["tools_supervisor"]})
    wrapper = b"# synthetic-only wrapper; never executed"
    manifest = bp.raw({"schema_version": 1, "source_commit": "a" * 40,
                       "files": {"wd-fleet.json": bp.sha(fleet), "Invoke-WdQ4Query.ps1": bp.sha(wrapper)}})
    active = dict(synth["active_artifact_bytes"])
    if projection is not None:
        active["projection.json"] = projection
    plan = {"schema": "wd.q4-public-wrapper-plan.v1",
            "config": {"descriptor_sha256": bp.sha(active["descriptor.json"])},
            "root": "synthetic-root", "policy_path": "synthetic-policy",
            "roster_path": "synthetic-roster", "public_policy_path": "synthetic-public-policy",
            "pointer_sha256": synth["expected_pointer_sha256"],
            "source_anchor_sha256": synth["expected_source_sha256"],
            "encoder_sha256": synth["expected_encoder_sha256"],
            "policy_sha256": synth["policy_sha256"], "roster_sha256": synth["roster_sha256"],
            "public_policy_sha256": synth["expected_public_policy_sha256"]}
    snapshot = transport.fixed_snapshot_bundle(
        active_artifact_bytes=active, **{k: synth[k] for k in (
            "pointer_bytes", "policy_bytes", "roster_bytes", "public_policy_bytes", "history_bytes",
            "cell_snapshots")})
    plan_raw = bp.raw(plan)
    return dict(enabled=True, expected_commit="a" * 40, manifest_bytes=manifest,
                expected_manifest_sha256=bp.sha(manifest), fleet_bytes=fleet, expected_fleet_sha256=bp.sha(fleet),
                wrapper_bytes=wrapper, expected_wrapper_sha256=bp.sha(wrapper), public_plan_bytes=plan_raw,
                expected_public_plan_sha256=bp.sha(plan_raw), snapshot_bytes=snapshot,
                expected_snapshot_sha256=bp.sha(snapshot))


CARD_PROJECTION = bp.raw({"schema": scp.SCHEMA, "synthetic": True})


@pytest.fixture
def producer_case():
    return _producer_inputs()


@pytest.fixture
def card_producer_case():
    """A v7-schema projection stand-in in the snapshot; the v6 preflight refuses it unless substituted."""
    return _producer_inputs(CARD_PROJECTION)


def route_decision(plan, **changes):
    value = {"schema": scp.DECISION_SCHEMA, "decision": scp.DECISION_NAME, "synthetic": True,
             "projection_schema": scp.SCHEMA, "scope": scp.SHARED_SCOPE, "card_module_sha256": scp.module_sha256(),
             "nonreview_policy_sha256": plan["policy_sha256"], "roster_sha256": plan["roster_sha256"],
             "card_policy_sha256": "c1" * 32, "vocabulary_sha256": "c2" * 32, "path_allowlist_sha256": "c3" * 32,
             "source_prefix": {"sha256": "c4" * 32, "offset": 1, "generation": "synthetic-g1"},
             "observed_heads": None}
    value.update(changes)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def card_plan(case, decision=None, *, pin=None, **decision_changes):
    """Turn the producer case into a v2 plan pinning ``decision`` (built from the plan pins by default)."""
    plan = json.loads(case["public_plan_bytes"])
    decision = route_decision(plan, **decision_changes) if decision is None else decision
    plan.update(schema="wd.q4-public-wrapper-plan.v2", card_decision_path="synthetic-decision",
                card_decision_sha256=bp.sha(decision) if pin is None else pin)
    case["public_plan_bytes"] = bp.raw(plan)
    case["expected_public_plan_sha256"] = bp.sha(case["public_plan_bytes"])
    case["card_decision_bytes"] = decision
    return plan, decision


def substitute_preflight(monkeypatch):
    calls = []
    monkeypatch.setattr(bp.module().preflight, "_preflight_public_snapshot",
                        lambda *args, **kwargs: calls.append(kwargs))
    return calls


def produce(case):
    return bp.module().build_lane_binding(**case)


def refuses(case):
    with pytest.raises(bp.module().BindingProducerRefused) as exc:
        produce(case)
    assert exc.value.__cause__ is None and exc.value.__context__ is None


def test_producer_v1_plan_output_is_unchanged_and_rejects_a_decision(producer_case):
    first = produce(producer_case)
    assert CARD not in json.loads(first["binding_bytes"])["binding"]
    producer_case["card_decision_bytes"] = None
    assert produce(producer_case) == first
    plan = json.loads(producer_case["public_plan_bytes"])
    producer_case["card_decision_bytes"] = route_decision(plan)
    refuses(producer_case)


def test_producer_card_plan_binds_the_verified_decision_pin_with_substituted_preflight(card_producer_case,
                                                                                         monkeypatch):
    producer_case = card_producer_case
    calls = substitute_preflight(monkeypatch)
    plan, decision = card_plan(producer_case)
    result = produce(producer_case)
    assert len(calls) == 1
    document = json.loads(result["binding_bytes"])
    assert document["binding"][CARD] == bp.sha(decision) == plan[CARD]
    assert frozenset(document["binding"]) == bp.consumer.CARD_BINDING_KEYS
    for lane in document["lanes"]:
        pins = bp.consumer.resolve_lane_binding(producer_case["fleet_bytes"], producer_case["expected_fleet_sha256"],
                                                result["binding_bytes"], result["binding_sha256"], lane=lane,
                                                enabled=True)
        assert pins["expected_binding"] == document["binding"]


def test_producer_card_plan_with_the_real_v6_preflight_refuses_a_card_generation(card_producer_case):
    card_plan(card_producer_case)
    refuses(card_producer_case)


def test_producer_card_plan_over_a_v6_projection_refuses(producer_case, monkeypatch):
    substitute_preflight(monkeypatch)
    card_plan(producer_case)
    refuses(producer_case)


@pytest.mark.parametrize("fault", ["missing", "tampered", "other-pin", "upper-pin", "policy", "roster", "module",
                                   "projection-schema", "not-canonical", "empty-path"])
def test_producer_card_plan_refuses_a_missing_or_inconsistent_decision(card_producer_case, monkeypatch, fault):
    producer_case = card_producer_case
    substitute_preflight(monkeypatch)
    if fault == "missing":
        card_plan(producer_case)
        producer_case["card_decision_bytes"] = None
    elif fault == "tampered":
        _, decision = card_plan(producer_case)
        producer_case["card_decision_bytes"] = decision.replace(b"synthetic-g1", b"synthetic-g2")
    elif fault == "other-pin":
        card_plan(producer_case, pin=OTHER)
    elif fault == "upper-pin":
        plan = json.loads(producer_case["public_plan_bytes"])
        card_plan(producer_case, pin=bp.sha(route_decision(plan)).upper())
    elif fault == "policy":
        card_plan(producer_case, nonreview_policy_sha256="d1" * 32)
    elif fault == "roster":
        card_plan(producer_case, roster_sha256="d2" * 32)
    elif fault == "module":
        card_plan(producer_case, card_module_sha256="d3" * 32)
    elif fault == "projection-schema":
        card_plan(producer_case, projection_schema="wd.bridge-mind-shared-projection.v6")
    elif fault == "not-canonical":
        plan = json.loads(producer_case["public_plan_bytes"])
        loose = route_decision(plan) + b" "
        card_plan(producer_case, loose)
    elif fault == "empty-path":
        card_plan(producer_case)
        plan = json.loads(producer_case["public_plan_bytes"])
        plan["card_decision_path"] = ""
        producer_case["public_plan_bytes"] = bp.raw(plan)
        producer_case["expected_public_plan_sha256"] = bp.sha(producer_case["public_plan_bytes"])
    refuses(producer_case)


@pytest.mark.parametrize("shape", ["v1-extra-key", "v1-with-card-keys", "v2-without-path", "v2-without-pin"])
def test_producer_plan_shape_stays_closed(card_producer_case, monkeypatch, shape):
    substitute_preflight(monkeypatch)
    plan, _ = card_plan(card_producer_case)
    if shape == "v1-extra-key":
        plan.update(schema="wd.q4-public-wrapper-plan.v1", memory_path="x")
        for key in ("card_decision_path", CARD):
            del plan[key]
        card_producer_case["card_decision_bytes"] = None
    elif shape == "v1-with-card-keys":
        plan["schema"] = "wd.q4-public-wrapper-plan.v1"
    elif shape == "v2-without-path":
        del plan["card_decision_path"]
    else:
        del plan[CARD]
    card_producer_case["public_plan_bytes"] = bp.raw(plan)
    card_producer_case["expected_public_plan_sha256"] = bp.sha(card_producer_case["public_plan_bytes"])
    refuses(card_producer_case)


def test_producer_default_off_ignores_the_decision_argument():
    assert bp.module().build_lane_binding(card_decision_bytes=object()) == {"status": "OFF"}
