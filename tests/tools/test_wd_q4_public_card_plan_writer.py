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
], ids=["v2-input", "v3", "extra-key", "missing-key", "upper-policy", "short-roster", "config-list", "empty-root"])
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
