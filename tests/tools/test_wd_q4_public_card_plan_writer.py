"""G1: the missing writer of the v2 public card plan and its synthetic route decision.

Synthetic fixtures only, default OFF. ``tools.wd_q4_public_card_plan_writer`` reads ONE pinned v1 public wrapper plan
(``wd.q4-public-wrapper-plan.v1``), builds a synthetic ``wd.mind-card-route-decision.v1`` from explicit caller pins
plus the plan policy and roster pins, verifies it with the card module's own ``verify_route_decision`` and writes,
exclusively into one empty directory, ``card-decision.json`` and ``public-plan.v2.json``. The output consumers tested
here are the real ones: ``wd_q4_lane_link._public_plan``, the binding producer (card mode, preflight substituted as in
C4) and Windows PowerShell ``Get-WdQ4PublicPlan``. Nothing is published, pinned or activated; the decision issuer is
not authenticated.
"""
import hashlib
import importlib
import io
import json
import os
import sys

import pytest

from sandbox.mind.step2 import shared_card_projection as cards
from tests.tools import test_lane_card_decision_forward as lf
from tools import wd_q4_lane_link as link

V1 = "wd.q4-public-wrapper-plan.v1"
V2 = "wd.q4-public-wrapper-plan.v2"
REFUSED = "card_plan_refused"


def module():
    return importlib.import_module("tools.wd_q4_public_card_plan_writer")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


PREFIX = {"sha256": "c4" * 32, "offset": 7, "generation": "synthetic-g1"}
HEADS = ["a1" * 20, "b2" * 20]


def card_pins(**changes):
    value = {"card_policy_sha256": "c1" * 32, "vocabulary_sha256": "c2" * 32, "path_allowlist_sha256": "c3" * 32,
             "source_prefix": dict(PREFIX), "observed_heads": None}
    value.update(changes)
    return value


@pytest.fixture
def case(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    plan_path, _, document = lf.wrapper_plan(base, schema=V1, card=False)
    document["config"] = {"descriptor_sha256": "d5" * 32}  # the lane link requires the descriptor pin
    plan_path.write_bytes(canon(document))
    plan_pin = sha(plan_path.read_bytes())
    out = tmp_path / "out"
    out.mkdir()
    return {"plan_path": plan_path, "plan_pin": plan_pin, "plan": document, "out": out, "tmp": tmp_path}


def write(case, **changes):
    kwargs = dict(enabled=True, public_plan_path=str(case["plan_path"]),
                  expected_public_plan_sha256=case["plan_pin"], output_dir=str(case["out"]), **card_pins())
    kwargs.update(changes)
    return module().write_card_plan(**kwargs)


def refused(case, **changes):
    with pytest.raises(module().CardPlanRefused) as exc:
        write(case, **changes)
    assert exc.value.args == (REFUSED,)
    assert exc.value.__cause__ is None and exc.value.__context__ is None
    assert list(case["out"].iterdir()) == []


# ---------------------------------------------------------------------------------------------------------------
# switch
# ---------------------------------------------------------------------------------------------------------------


def test_default_off_reads_nothing():
    class NoTouch:
        def __getattribute__(self, name):
            raise AssertionError("disabled path inspected input")
    assert module().write_card_plan(public_plan_path=NoTouch(), output_dir=NoTouch()) == {"status": "OFF"}


@pytest.mark.parametrize("flag", [None, 0, 1, "true", [True]])
def test_enabled_must_be_the_bool_true(case, flag):
    refused(case, enabled=flag)


# ---------------------------------------------------------------------------------------------------------------
# outputs
# ---------------------------------------------------------------------------------------------------------------


def test_writes_a_verified_synthetic_decision_and_the_v2_plan(case):
    before = case["plan_path"].read_bytes()
    result = write(case)
    out = case["out"]
    assert sorted(path.name for path in out.iterdir()) == ["card-decision.json", "public-plan.v2.json"]
    decision_raw = (out / "card-decision.json").read_bytes()
    plan_raw = (out / "public-plan.v2.json").read_bytes()
    decision = cards.verify_route_decision(decision_raw, sha(decision_raw),
                                           policy_sha256=case["plan"]["policy_sha256"],
                                           roster_sha256=case["plan"]["roster_sha256"])
    assert decision["decision"] == cards.DECISION_NAME and decision["synthetic"] is True
    assert decision["card_module_sha256"] == cards.module_sha256()
    assert decision["source_prefix"] == PREFIX and decision["observed_heads"] is None
    assert {key: decision[key] for key in ("card_policy_sha256", "vocabulary_sha256", "path_allowlist_sha256")} == {
        "card_policy_sha256": "c1" * 32, "vocabulary_sha256": "c2" * 32, "path_allowlist_sha256": "c3" * 32}
    plan = json.loads(plan_raw)
    assert plan_raw == canon(plan)
    assert set(plan) == link._PUBLIC_PLAN_SCHEMAS[V2]
    assert plan["schema"] == V2
    assert {key: value for key, value in plan.items() if key not in ("schema", "card_decision_path",
                                                                      "card_decision_sha256")} == {
        key: value for key, value in case["plan"].items() if key != "schema"}
    assert plan["card_decision_path"] == str(out / "card-decision.json")
    assert plan["card_decision_sha256"] == sha(decision_raw)
    assert case["plan_path"].read_bytes() == before
    assert result == {
        "schema": "wd.q4-public-card-plan-written.v1", "status": "WRITTEN",
        "base_public_plan_sha256": case["plan_pin"],
        "public_plan_path": str(out / "public-plan.v2.json"), "public_plan_sha256": sha(plan_raw),
        "card_decision_path": str(out / "card-decision.json"), "card_decision_sha256": sha(decision_raw),
        "decision": cards.DECISION_NAME, "authority": "none", "issuer_authentication": "UNKNOWN"}


def test_observed_heads_are_written_sorted_as_given(case):
    write(case, observed_heads=list(HEADS))
    decision = json.loads((case["out"] / "card-decision.json").read_bytes())
    assert decision["observed_heads"] == HEADS


def test_the_lane_link_reads_the_written_plan_with_the_card_pin(case):
    result = write(case)
    _, raw, pins = link._public_plan(result["public_plan_path"], result["public_plan_sha256"])
    assert pins["card_decision_sha256"] == result["card_decision_sha256"]
    assert pins["policy_sha256"] == case["plan"]["policy_sha256"]


@lf.windows
def test_the_windows_wrapper_reads_the_written_plan_and_its_decision(case):
    result = write(case)
    script = (f". '{lf.quote(lf.WRAPPER)}'\n" + lf.BASE +
              f"$p=Get-WdQ4PublicPlan -BasePlan $base -Path '{lf.quote(result['public_plan_path'])}' "
              f"-ExpectedSha256 '{result['public_plan_sha256']}'\n"
              "@{schema=$p.Document.schema;card=$p.Inputs.card_decision.Length} | ConvertTo-Json -Compress")
    decision_raw = (case["out"] / "card-decision.json").read_bytes()
    assert lf.ps(script) == {"schema": V2, "card": len(decision_raw)}


def test_the_binding_producer_binds_the_written_card_pin_with_substituted_preflight(tmp_path, monkeypatch):
    from tests.tools import test_lane_card_session as c4
    producer = c4._producer_inputs(c4.CARD_PROJECTION)
    plan_path = tmp_path / "plan-v1.json"
    plan_path.write_bytes(producer["public_plan_bytes"])
    out = tmp_path / "out"
    out.mkdir()
    result = module().write_card_plan(enabled=True, public_plan_path=str(plan_path),
                                      expected_public_plan_sha256=producer["expected_public_plan_sha256"],
                                      output_dir=str(out), **card_pins())
    calls = c4.substitute_preflight(monkeypatch)
    producer.update(public_plan_bytes=(out / "public-plan.v2.json").read_bytes(),
                    expected_public_plan_sha256=result["public_plan_sha256"],
                    card_decision_bytes=(out / "card-decision.json").read_bytes())
    built = c4.bp.module().build_lane_binding(**producer)
    assert len(calls) == 1
    assert json.loads(built["binding_bytes"])["binding"]["card_decision_sha256"] == result["card_decision_sha256"]


# ---------------------------------------------------------------------------------------------------------------
# refusals: nothing is written
# ---------------------------------------------------------------------------------------------------------------


def rewrite_plan(case, document=None, raw=None):
    raw = canon(document) if raw is None else raw
    case["plan_path"].write_bytes(raw)
    case["plan_pin"] = sha(raw)


def test_plan_pin_mismatch_refuses(case):
    refused(case, expected_public_plan_sha256="0" * 64)


@pytest.mark.parametrize("change", [
    lambda d: d.update(schema=V2, card_decision_path="x", card_decision_sha256="7" * 64),  # already a card plan
    lambda d: d.update(schema="wd.q4-public-wrapper-plan.v3"),
    lambda d: d.update(memory_path="x"),
    lambda d: d.pop("roster_path"),
    lambda d: d.update(policy_sha256="A" * 64),
    lambda d: d.update(roster_sha256="7" * 63),
    lambda d: d.update(config=[]),
    lambda d: d.update(root=""),
    lambda d: d.update(config={}),
    lambda d: d.update(config={"descriptor_sha256": "D5" * 32}),
    lambda d: d.update(pointer_sha256="A" * 64),
    lambda d: d.update(source_anchor_sha256="b" * 63),
    lambda d: d.update(encoder_sha256=None),
    lambda d: d.update(public_policy_sha256=7),
], ids=["v2-input", "v3", "extra-key", "missing-key", "upper-policy", "short-roster", "config-list", "empty-root",
        "config-without-descriptor", "upper-descriptor", "upper-pointer", "short-anchor", "none-encoder",
        "int-public-policy"])
def test_input_must_be_an_exact_v1_plan(case, change):
    document = dict(case["plan"])
    change(document)
    rewrite_plan(case, document)
    refused(case)


@pytest.mark.parametrize("raw", [b"", b"[]", b"{", b'{"schema":"a","schema":"b"}', b"\xff"])
def test_plan_bytes_must_be_one_strict_json_object(case, raw):
    rewrite_plan(case, raw=raw)
    refused(case)


@pytest.mark.parametrize("change", [
    {"card_policy_sha256": "C1" * 32}, {"vocabulary_sha256": "c2" * 31}, {"path_allowlist_sha256": None},
    {"source_prefix": dict(PREFIX, offset=0)}, {"source_prefix": dict(PREFIX, generation="bad generation")},
    {"source_prefix": {"sha256": "c4" * 32, "offset": 7}}, {"source_prefix": dict(PREFIX, sha256="C4" * 32)},
    {"observed_heads": list(reversed(HEADS))}, {"observed_heads": ["zz" * 20]}, {"observed_heads": HEADS + HEADS[:1]},
    {"observed_heads": "a1" * 20},
], ids=["upper-card-policy", "short-vocabulary", "none-allowlist", "offset-zero", "bad-generation", "prefix-key",
        "upper-prefix", "unsorted-heads", "bad-head", "duplicate-head", "heads-string"])
def test_decision_inputs_are_checked_by_the_card_verifier(case, change):
    refused(case, **change)


@pytest.mark.parametrize("kind", ["not-empty", "relative", "missing", "file", "is-plan-dir"])
def test_output_directory_must_be_an_existing_empty_absolute_directory(case, kind):
    if kind == "not-empty":
        (case["out"] / "other.txt").write_bytes(b"x")
        with pytest.raises(module().CardPlanRefused):
            write(case)
        assert sorted(path.name for path in case["out"].iterdir()) == ["other.txt"]
        return
    target = {"relative": "out", "missing": str(case["tmp"] / "missing"),
              "file": str(case["plan_path"]), "is-plan-dir": str(case["plan_path"].parent)}[kind]
    with pytest.raises(module().CardPlanRefused):
        write(case, output_dir=target)
    assert sorted(path.name for path in case["plan_path"].parent.iterdir()) == sorted(
        ["plan.json", "policy.json", "roster.json", "public_policy.json", "public"])


def test_a_failed_write_after_the_first_file_is_unknown_not_refused(case, monkeypatch):
    real = module()._exclusive
    calls = []

    def second_fails(path, raw):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("synthetic")
        real(path, raw)
    monkeypatch.setattr(module(), "_exclusive", second_fails)
    with pytest.raises(module().CardPlanUnknown) as exc:
        write(case)
    assert exc.value.args == ("card_plan_unknown",)
    assert exc.value.__cause__ is None and exc.value.__context__ is None


def test_a_write_that_does_not_read_back_is_unknown(case, monkeypatch):
    real = module()._exclusive

    def short(path, raw):
        real(path, raw[:-1])
    monkeypatch.setattr(module(), "_exclusive", short)
    with pytest.raises(module().CardPlanUnknown):
        write(case)


def test_the_file_write_is_exclusive(tmp_path):
    target = tmp_path / "existing.json"
    target.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        module()._exclusive(target, b"new")
    assert target.read_bytes() == b"old"


# ---------------------------------------------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------------------------------------------


def request_file(case, **changes):
    document = {"schema": "wd.q4-public-card-plan-request.v1", "public_plan_path": str(case["plan_path"]),
                "public_plan_sha256": case["plan_pin"], "output_dir": str(case["out"]), **card_pins()}
    document.update(changes)
    raw = canon(document)
    path = case["tmp"] / "request.json"
    path.write_bytes(raw)
    return path, sha(raw)


def cli(argv, capsys):
    code = module().main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_without_arguments_is_off_and_reads_nothing(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert cli([], capsys) == (0, '{"status":"OFF"}\n', "")


def test_cli_writes_from_one_pinned_request_and_prints_the_descriptor(case, capsys):
    path, pin = request_file(case)
    code, out, err = cli(["--enabled", "--request", str(path), "--request-sha256", pin], capsys)
    assert code == 0 and err == ""
    descriptor = json.loads(out)
    assert out == canon(descriptor).decode("ascii") + "\n"
    assert descriptor["status"] == "WRITTEN"
    assert descriptor["public_plan_sha256"] == sha((case["out"] / "public-plan.v2.json").read_bytes())


@pytest.mark.parametrize("variant", ["pin", "extra-key", "schema", "argv"])
def test_cli_refuses_with_a_fixed_line_and_writes_nothing(case, capsys, variant):
    path, pin = request_file(case, **({"memory_path": "x"} if variant == "extra-key" else {})
                             | ({"schema": "wd.q4-public-card-plan-request.v2"} if variant == "schema" else {}))
    argv = ["--enabled", "--request", str(path), "--request-sha256", "0" * 64 if variant == "pin" else pin]
    if variant == "argv":
        argv.append("--observed-heads")
    code, out, err = cli(argv, capsys)
    assert (code, out, err) == (2, "", '{"error":"card_plan_refused"}\n')
    assert list(case["out"].iterdir()) == []
    assert str(case["tmp"]) not in err


def test_module_reads_no_environment_and_imports_no_model_stack():
    source = (lf.ROOT / "tools" / "wd_q4_public_card_plan_writer.py").read_text(encoding="utf-8")
    assert "os.environ" not in source and "getenv" not in source
    assert "faiss" not in source and "numpy" not in source and "subprocess" not in source


# ---------------------------------------------------------------------------------------------------------------
# v3 staged card plan (write_staged_card_plan): binds an EXISTING C3 generation, decision and source
# ---------------------------------------------------------------------------------------------------------------

import dataclasses  # noqa: E402

from tests.tools.test_shared_card_checkpoint import staged  # noqa: E402,F401  (pytest fixture, real C3 generation)
from tools import wd_q4_public_pointer_resolver as pointer_resolver  # noqa: E402
from waggledance.core.bridge_log_reader import BridgeCursor  # noqa: E402

V3 = "wd.q4-public-wrapper-plan.v3"
STAGED_KEYS = {"card_decision_path", "card_decision_sha256", "card_policy_path", "card_policy_sha256",
               "pointer_path", "source_path", "generation_path", "end_cursor"}


@pytest.fixture
def staged_case(staged, tmp_path):
    pointer, call, directory = staged
    files = tmp_path / "inputs"
    files.mkdir()

    def put(name, raw):
        path = files / name
        path.write_bytes(raw)
        return str(path), sha(raw)

    pointer_raw = canon(pointer)
    paths = {}
    for name, raw in (("pointer", pointer_raw), ("card_decision", call["decision_bytes"]),
                      ("card_policy", call["card_policy_bytes"]), ("policy", call["policy_bytes"]),
                      ("roster", call["roster_bytes"]), ("public_policy", b"{}")):
        paths[name] = put(name + ".json", raw)
    descriptor_sha = sha((directory / "descriptor.json").read_bytes())
    plan = {"schema": V1, "root": str(directory.parent), "config": {"descriptor_sha256": descriptor_sha},
            "pointer_sha256": paths["pointer"][1], "source_anchor_sha256": pointer["source_prefix"]["sha256"],
            "encoder_sha256": "e5" * 32}
    for name in ("policy", "roster", "public_policy"):
        plan[name + "_path"], plan[name + "_sha256"] = paths[name]
    plan_path, plan_pin = put("plan-v1.json", canon(plan))
    out = tmp_path / "plan-out"
    out.mkdir()
    return {"pointer": pointer, "call": call, "directory": directory, "paths": paths, "plan": plan,
            "plan_path": plan_path, "plan_pin": plan_pin, "out": out, "tmp": tmp_path, "put": put}


def write_staged(sc, **changes):
    kwargs = dict(enabled=True, public_plan_path=sc["plan_path"], expected_public_plan_sha256=sc["plan_pin"],
                  output_dir=str(sc["out"]), pointer_path=sc["paths"]["pointer"][0],
                  card_decision_path=sc["paths"]["card_decision"][0], card_policy_path=sc["paths"]["card_policy"][0],
                  source_path=str(sc["call"]["source_path"]), generation_path=str(sc["call"]["generation_path"]))
    kwargs.update(changes)
    return module().write_staged_card_plan(**kwargs)


def staged_refused(sc, **changes):
    with pytest.raises(module().CardPlanRefused) as exc:
        write_staged(sc, **changes)
    assert exc.value.args == (REFUSED,)
    assert exc.value.__cause__ is None and exc.value.__context__ is None
    assert list(sc["out"].iterdir()) == []


def test_staged_default_off_reads_nothing():
    class NoTouch:
        def __getattribute__(self, name):
            raise AssertionError("disabled path inspected input")
    assert module().write_staged_card_plan(public_plan_path=NoTouch(), source_path=NoTouch()) == {"status": "OFF"}


def test_staged_writes_only_the_v3_plan_bound_to_the_existing_generation(staged_case):
    sc = staged_case
    result = write_staged(sc)
    assert [path.name for path in sc["out"].iterdir()] == ["public-plan.v3.json"]
    raw = (sc["out"] / "public-plan.v3.json").read_bytes()
    plan = json.loads(raw)
    assert raw == canon(plan) and plan["schema"] == V3
    assert set(plan) == link._PUBLIC_PLAN_SCHEMAS[V3] == set(sc["plan"]) | STAGED_KEYS
    assert {key: plan[key] for key in sc["plan"] if key != "schema"} == {
        key: value for key, value in sc["plan"].items() if key != "schema"}
    assert plan["pointer_path"] == sc["paths"]["pointer"][0]
    assert plan["card_decision_path"] == sc["paths"]["card_decision"][0]
    assert plan["card_decision_sha256"] == sc["pointer"]["decision_sha256"] == sc["paths"]["card_decision"][1]
    assert plan["card_policy_path"] == sc["paths"]["card_policy"][0]
    assert plan["card_policy_sha256"] == sc["paths"]["card_policy"][1]
    assert plan["source_path"] == str(sc["call"]["source_path"])
    assert plan["generation_path"] == str(sc["call"]["generation_path"])
    assert plan["end_cursor"] == dataclasses.asdict(sc["call"]["end_cursor"])
    assert result == {
        "schema": "wd.q4-public-staged-card-plan-written.v1", "status": "WRITTEN",
        "base_public_plan_sha256": sc["plan_pin"], "public_plan_path": str(sc["out"] / "public-plan.v3.json"),
        "public_plan_sha256": sha(raw), "pointer_sha256": sc["paths"]["pointer"][1],
        "card_decision_sha256": plan["card_decision_sha256"], "card_policy_sha256": plan["card_policy_sha256"],
        "source_prefix": sc["pointer"]["source_prefix"], "end_cursor": plan["end_cursor"],
        "authority": "none", "issuer_authentication": "UNKNOWN"}


def staged_inputs_from_plan(plan):
    read = lambda key: open(plan[key], "rb").read()  # noqa: E731
    return dict(pointer_bytes=read("pointer_path"), expected_pointer_sha256=plan["pointer_sha256"],
                policy_bytes=read("policy_path"), policy_sha256=plan["policy_sha256"],
                roster_bytes=read("roster_path"), roster_sha256=plan["roster_sha256"],
                card_decision_bytes=read("card_decision_path"), card_decision_sha256=plan["card_decision_sha256"],
                card_policy_bytes=read("card_policy_path"), source_path=plan["source_path"],
                generation_path=plan["generation_path"], end_cursor=BridgeCursor(**plan["end_cursor"]))


def test_the_real_staged_resolver_validates_the_written_plan_tuple(staged_case):
    sc = staged_case
    result = write_staged(sc)
    plan = json.loads(open(result["public_plan_path"], "rb").read())
    binding = pointer_resolver.resolve_card_snapshot_pointer(plan["root"], enabled=True,
                                                              **staged_inputs_from_plan(plan))
    assert binding["status"] == "VALIDATED_STAGED"
    assert binding["source_prefix"]["sha256"] == plan["source_anchor_sha256"]
    assert binding["card_decision_sha256"] == plan["card_decision_sha256"]
    _, _, pins = link._public_plan(result["public_plan_path"], result["public_plan_sha256"])
    assert pins["card_policy_sha256"] == plan["card_policy_sha256"]


def test_an_appended_source_suffix_keeps_the_frozen_prefix_cursor(staged_case):
    sc = staged_case
    with open(sc["call"]["source_path"], "ab") as stream:
        stream.write(b'{"synthetic":"appended after the prefix"}\n')
    result = write_staged(sc)
    assert result["end_cursor"]["offset"] == sc["pointer"]["source_prefix"]["offset"]
    plan = json.loads(open(result["public_plan_path"], "rb").read())
    assert pointer_resolver.resolve_card_snapshot_pointer(plan["root"], enabled=True,
                                                          **staged_inputs_from_plan(plan))["status"] == "VALIDATED_STAGED"


@lf.windows
def test_the_windows_wrapper_reads_the_written_v3_plan(staged_case):
    result = write_staged(staged_case)
    script = (f". '{lf.quote(lf.WRAPPER)}'\n" + lf.BASE +
              f"$p=Get-WdQ4PublicPlan -BasePlan $base -Path '{lf.quote(result['public_plan_path'])}' "
              f"-ExpectedSha256 '{result['public_plan_sha256']}'\n"
              "@{staged=$p.Staged;pointer=$p.Inputs.pointer.Length;card=$p.Inputs.card_policy.Length;"
              "decision=$p.Inputs.card_decision.Length} | ConvertTo-Json -Compress")
    paths = staged_case["paths"]
    assert lf.ps(script) == {"staged": True, "pointer": len(open(paths["pointer"][0], "rb").read()),
                             "card": len(open(paths["card_policy"][0], "rb").read()),
                             "decision": len(open(paths["card_decision"][0], "rb").read())}


def tamper(path, raw=b" "):
    with open(path, "ab") as stream:
        stream.write(raw)


@pytest.mark.parametrize("fault", [
    "pointer-bytes", "decision-bytes", "card-policy-bytes", "source-prefix-byte", "source-truncated",
    "generation-marker", "anchor-pin", "plan-policy", "other-decision-path", "v2-input"])
def test_staged_partial_or_mismatched_tuple_refuses_and_writes_nothing(staged_case, fault):
    sc = staged_case
    if fault == "pointer-bytes":
        tamper(sc["paths"]["pointer"][0])
    elif fault == "decision-bytes":
        tamper(sc["paths"]["card_decision"][0])
    elif fault == "card-policy-bytes":
        tamper(sc["paths"]["card_policy"][0])
    elif fault == "source-prefix-byte":
        raw = bytearray(open(sc["call"]["source_path"], "rb").read())
        raw[1] = ord("x") if raw[1] != ord("x") else ord("y")
        open(sc["call"]["source_path"], "wb").write(bytes(raw))
    elif fault == "source-truncated":
        raw = open(sc["call"]["source_path"], "rb").read()
        open(sc["call"]["source_path"], "wb").write(raw[:-2])
    elif fault == "generation-marker":
        open(sc["call"]["generation_path"], "wb").write(canon({"generation": "synthetic-other-generation"}))
    elif fault in ("anchor-pin", "plan-policy", "v2-input"):
        plan = dict(sc["plan"])
        if fault == "anchor-pin":
            plan["source_anchor_sha256"] = "a0" * 32
        elif fault == "plan-policy":
            plan["policy_path"], plan["policy_sha256"] = sc["put"]("other-policy.json", b'{"other":1}')
        else:
            plan.update(schema=V2, card_decision_path=sc["paths"]["card_decision"][0],
                        card_decision_sha256=sc["paths"]["card_decision"][1])
        sc["plan_path"], sc["plan_pin"] = sc["put"]("plan-changed.json", canon(plan))
    elif fault == "other-decision-path":
        other = json.loads(open(sc["paths"]["card_decision"][0], "rb").read())
        other["observed_heads"] = None if other["observed_heads"] else []
        path, _ = sc["put"]("other-decision.json", canon(other))
        staged_refused(sc, card_decision_path=path)
        return
    staged_refused(sc)


@pytest.mark.parametrize("key", ["pointer_path", "card_decision_path", "card_policy_path", "source_path",
                                 "generation_path", "public_plan_path"])
@pytest.mark.parametrize("value", ["relative.json", None, "missing"])
def test_staged_paths_must_be_existing_absolute_files(staged_case, key, value):
    if value == "missing":
        value = str(staged_case["tmp"] / "missing.json")
    staged_refused(staged_case, **{key: value})


def test_staged_output_directory_must_be_empty(staged_case):
    (staged_case["out"] / "other.txt").write_bytes(b"x")
    with pytest.raises(module().CardPlanRefused):
        write_staged(staged_case)
    assert [path.name for path in staged_case["out"].iterdir()] == ["other.txt"]


def test_staged_cli_request_selects_v3_and_prints_the_descriptor(staged_case, capsys):
    sc = staged_case
    document = {"schema": "wd.q4-public-staged-card-plan-request.v1", "public_plan_path": sc["plan_path"],
                "public_plan_sha256": sc["plan_pin"], "output_dir": str(sc["out"]),
                "pointer_path": sc["paths"]["pointer"][0], "card_decision_path": sc["paths"]["card_decision"][0],
                "card_policy_path": sc["paths"]["card_policy"][0], "source_path": str(sc["call"]["source_path"]),
                "generation_path": str(sc["call"]["generation_path"])}
    path, pin = sc["put"]("staged-request.json", canon(document))
    code, out, err = cli(["--enabled", "--request", path, "--request-sha256", pin], capsys)
    assert code == 0 and err == ""
    descriptor = json.loads(out)
    assert descriptor["schema"] == "wd.q4-public-staged-card-plan-written.v1"
    assert out == canon(descriptor).decode("ascii") + "\n"
    fresh = sc["tmp"] / "plan-out-extra"
    fresh.mkdir()
    document.update(output_dir=str(fresh), memory_path="x")  # an otherwise valid request into an empty directory
    path, pin = sc["put"]("staged-request-extra.json", canon(document))
    assert cli(["--enabled", "--request", path, "--request-sha256", pin], capsys) == (
        2, "", '{"error":"card_plan_refused"}\n')
    assert list(fresh.iterdir()) == []


@pytest.mark.parametrize("kind", ["fails", "short"])
def test_a_staged_write_that_fails_or_does_not_read_back_is_unknown(staged_case, monkeypatch, kind):
    real = module()._exclusive

    def broken(path, raw):
        if kind == "fails":
            raise OSError("synthetic")
        real(path, raw[:-1])
    monkeypatch.setattr(module(), "_exclusive", broken)
    with pytest.raises(module().CardPlanUnknown) as exc:
        write_staged(staged_case)
    assert exc.value.args == ("card_plan_unknown",)
    assert exc.value.__cause__ is None and exc.value.__context__ is None


from tests.tools.test_wd_q4_staged_lane_query import staged_producer  # noqa: E402,F401  (Lead v3 producer fixture)
from tools import wd_q4_lane_binding_producer as binding_producer  # noqa: E402


def test_the_written_plan_equals_the_hand_built_v3_plan_and_binds_the_same_lane_document(staged_producer, tmp_path):
    hand = json.loads(staged_producer["public_plan_bytes"])
    base = dict({key: hand[key] for key in link._PUBLIC_PLAN_SCHEMAS[V1]}, schema=V1)
    base_path = tmp_path / "plan-v1-from-hand.json"
    base_path.write_bytes(canon(base))
    out = tmp_path / "plan-out-hand"
    out.mkdir()
    result = module().write_staged_card_plan(
        enabled=True, public_plan_path=str(base_path), expected_public_plan_sha256=sha(canon(base)),
        output_dir=str(out), **{key: hand[key] for key in ("pointer_path", "card_decision_path", "card_policy_path",
                                                          "source_path", "generation_path")})
    written = open(result["public_plan_path"], "rb").read()
    assert json.loads(written) == hand
    expected = binding_producer.build_staged_lane_binding(**staged_producer)
    actual = binding_producer.build_staged_lane_binding(
        **dict(staged_producer, public_plan_bytes=written, expected_public_plan_sha256=sha(written)))
    hand_pin = staged_producer["expected_public_plan_sha256"].encode()
    assert (json.loads(actual["binding_bytes"].replace(sha(written).encode(), b"PLAN"))
            == json.loads(expected["binding_bytes"].replace(hand_pin, b"PLAN")))


from tests.tools.test_wd_q4_real_card_snapshot_e2e import real_fixture, real_snapshot  # noqa: E402,F401


def test_a_real_format_non_synthetic_decision_writes_a_v3_plan_the_real_resolver_validates(real_snapshot, tmp_path):
    # fabricated local Git heads and cards; the decision is enable_real_card_route with synthetic False
    directory, built, decision, snapshot, _, _ = real_snapshot
    assert decision["decision"] == "enable_real_card_route" and decision["synthetic"] is False
    assert "head_observation_sha256" in decision
    files = tmp_path / "real-inputs"
    files.mkdir()
    blobs = {"pointer": snapshot["pointer_bytes"], "card_decision": snapshot["decision_bytes"],
             "card_policy": snapshot["card_policy_bytes"], "policy": snapshot["policy_bytes"],
             "roster": snapshot["roster_bytes"], "public_policy": b"{}"}
    paths = {}
    for name, raw in blobs.items():
        (files / (name + ".json")).write_bytes(raw)
        paths[name] = str(files / (name + ".json"))
    plan = {"schema": V1, "root": str(directory.parent), "config": {"descriptor_sha256": built["descriptor_sha256"]},
            "pointer_sha256": sha(blobs["pointer"]), "source_anchor_sha256": decision["source_prefix"]["sha256"],
            "encoder_sha256": "e5" * 32}
    for name in ("policy", "roster", "public_policy"):
        plan[name + "_path"], plan[name + "_sha256"] = paths[name], sha(blobs[name])
    (files / "plan-v1.json").write_bytes(canon(plan))
    out = tmp_path / "real-out"
    out.mkdir()
    result = module().write_staged_card_plan(
        enabled=True, public_plan_path=str(files / "plan-v1.json"), expected_public_plan_sha256=sha(canon(plan)),
        output_dir=str(out), pointer_path=paths["pointer"], card_decision_path=paths["card_decision"],
        card_policy_path=paths["card_policy"], source_path=str(snapshot["source_path"]),
        generation_path=str(snapshot["generation_path"]))
    written = json.loads(open(result["public_plan_path"], "rb").read())
    assert written["end_cursor"] == dataclasses.asdict(snapshot["end_cursor"])
    assert written["card_decision_sha256"] == snapshot["decision_sha256"]
    binding = pointer_resolver.resolve_card_snapshot_pointer(written["root"], enabled=True,
                                                             **staged_inputs_from_plan(written))
    assert binding["status"] == "VALIDATED_STAGED" and binding["source_mode"] == "card-snapshot-v1"
    _, _, pins = link._public_plan(result["public_plan_path"], result["public_plan_sha256"])
    assert pins["card_policy_sha256"] == written["card_policy_sha256"]
