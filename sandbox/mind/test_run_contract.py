"""Tests for RUN CONTRACT v1 (a pure checker of a run plan; it never executes), public toys only. Written BEFORE the
code, from the RED list of Lead's round 1/10 proposal (9D354A02, Grok consultation 637a5f9c answer 5b4d5d6e).

The toy graph has the shape of the current Q4 chain without any of its data: prepare, a two-arm shortlists step
with five written files, a fresh joiner, compose, an observer and a pre-case guard that a function binds, a
stdout-only grader and a final function check. Every test breaks exactly one rule and expects its fixed code.

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/test_run_contract.py -q -p no:cacheprovider
"""

import ast
import builtins
import copy
import io
import os
import random
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_contract as rc  # noqa: E402

IDENTITIES = {"tool.py": "1" * 64, "observer.py": "2" * 64, "grader.py": "3" * 64}


def captured(step):
    return {"stdout": f"{step}_out", "stderr": f"{step}_err", "exit": f"{step}_exit"}


def argv_step(step, adapter, argv, inputs, outputs, result="exit0_and_outputs", actor="<RUNNER>"):
    capture = captured(step)
    return {"id": step, "adapter": adapter, "argv": argv, "inputs": inputs,
            "outputs": outputs + [capture["stdout"], capture["stderr"], capture["exit"]],
            "actor": actor, "result_contract": result, "capture": capture}


def function_step(step, adapter, inputs, outputs, result, actor="<RUNNER>"):
    return {"id": step, "adapter": adapter, "argv": [], "inputs": inputs, "outputs": outputs, "actor": actor,
            "result_contract": result, "capture": None}


def toy():
    """(descriptor, catalog, obligations, bindings) of a closed, valid toy graph."""
    steps = [
        argv_step("prepare", "tool_prepare", ["<PY>", "tool.py", "prepare", "--cases", "<IN>/cases.json", "--out", "<RUN>"],
                  ["cases"], ["blind"]),
        argv_step("shortlists", "tool_shortlists", ["<PY>", "tool.py", "shortlists", "--memory", "<MEM>", "--out", "<RUN>"],
                  ["blind", "memory"], ["short_a", "judge_a", "short_b", "judge_b", "short_manifest"]),
        function_step("joiner", "fresh_joiner", ["judge_a"], ["picks_a"], "picks_schema", actor="<JOINER>"),
        argv_step("compose", "tool_compose", ["<PY>", "tool.py", "compose", "--memory", "<MEM>", "--picks-a", "<IN>/picks_a.json",
                                             "--out", "<RUN>"], ["blind", "short_a", "picks_a", "memory"],
                  ["outputs_a", "compose_manifest"]),
        argv_step("observe", "observer", ["<PY>", "observer.py", "--memory", "<MEM>", "--status", "<IN>/status.json",
                                          "--out", "<RUN>/observation.json"], ["memory", "status"], ["observation"]),
        argv_step("t1", "tool_t1", ["<PY>", "tool.py", "t1-guard", "--memory", "<MEM>", "--status", "<IN>/status.json",
                                    "--out", "<RUN>/t1.json"], ["memory", "status"], ["t1"]),
        function_step("bind", "bind_to_t1", ["observation", "t1"], ["binding"], "binding_list_empty"),
        argv_step("grade", "grader", ["<PY>", "grader.py", "--outputs", "<RUN>/a/outputs.jsonl"], ["outputs_a"], [],
                  result="stdout_report_semantic"),
        function_step("verdict", "final_check", ["grade_out", "binding"], ["verdict"], "verdict_semantic"),
    ]
    artifacts = {name: {"path": f"<RUN>/{name}"} for step in steps for name in step["outputs"]}
    artifacts.update({"cases": {"path": "<IN>/cases.json"}, "memory": {"path": "<MEM>"},
                      "status": {"path": "<IN>/status.json"}})
    descriptor = {"version": rc.DESCRIPTOR_VERSION, "identities": dict(IDENTITIES), "artifacts": artifacts,
                  "externals": ["cases", "memory", "status"], "steps": steps}
    catalog = {"version": rc.CATALOG_VERSION, "adapters": {
        "tool_prepare": {"kind": "argv", "prefix": ["<PY>", "tool.py", "prepare"], "flags": ["--cases", "--out"],
                         "result_contracts": ["exit0_and_outputs"]},
        "tool_shortlists": {"kind": "argv", "prefix": ["<PY>", "tool.py", "shortlists"], "flags": ["--memory", "--out"],
                            "result_contracts": ["exit0_and_outputs"]},
        "tool_compose": {"kind": "argv", "prefix": ["<PY>", "tool.py", "compose"],
                         "flags": ["--memory", "--picks-a", "--picks-b", "--out"], "result_contracts": ["exit0_and_outputs"]},
        "observer": {"kind": "argv", "prefix": ["<PY>", "observer.py"], "flags": ["--memory", "--status", "--out"],
                     "result_contracts": ["exit0_and_outputs"]},
        "tool_t1": {"kind": "argv", "prefix": ["<PY>", "tool.py", "t1-guard"], "flags": ["--memory", "--status", "--out"],
                    "result_contracts": ["exit0_and_outputs"]},
        "grader": {"kind": "argv", "prefix": ["<PY>", "grader.py"], "flags": ["--outputs", "--labels"],
                   "result_contracts": ["stdout_report_semantic"]},
        "fresh_joiner": {"kind": "function", "result_contracts": ["picks_schema"]},
        "bind_to_t1": {"kind": "function", "result_contracts": ["binding_list_empty"]},
        "final_check": {"kind": "function", "result_contracts": ["verdict_semantic"]}}}
    obligations = {"version": rc.OBLIGATIONS_VERSION, "identities": dict(IDENTITIES),
                   "outputs": {"shortlists": ["short_a", "judge_a", "short_b", "judge_b", "short_manifest"],
                               "compose": ["outputs_a", "compose_manifest"], "grade": []},
                   "inputs": {"compose": ["blind", "short_a"], "observe": ["status"], "t1": ["status"]},
                   "reach": [["observation", "verdict"], ["t1", "verdict"], ["grade_out", "verdict"]],
                   "semantic": {"grade": "stdout_report_semantic", "verdict": "verdict_semantic"},
                   "roles": {"joiner": "fresh-joiner-a"},
                   "exposure": {"joiner": ["judge_a"]}}
    bindings = {"<PY>": "python", "<IN>": "in", "<RUN>": "run", "<MEM>": "mem", "<RUNNER>": "runner-a",
                "<JOINER>": "fresh-joiner-a"}
    return descriptor, catalog, obligations, bindings


def compile_toy(descriptor, catalog, obligations, bindings):
    return rc.compile_contract(descriptor, bindings=bindings, adapter_catalog=catalog, obligations=obligations)


def refused(code, parts):
    with pytest.raises(rc.RunContractRefused) as caught:
        compile_toy(*parts)
    assert caught.value.code == code, caught.value.code
    assert str(caught.value) == code  # the reason is the fixed code only, never an echoed value


def step(parts, step_id):
    return next(s for s in parts[0]["steps"] if s["id"] == step_id)


# --- control --------------------------------------------------------------------------------------------------------

def test_control_a_closed_toy_graph_compiles_to_a_plan_that_is_never_executable():
    plan = compile_toy(*toy())
    assert plan.executable is False and plan.status == "CONTRACT_CLOSED_NOT_EXECUTABLE"
    order = [s.id for s in plan.steps]
    for before, after in (("prepare", "shortlists"), ("shortlists", "joiner"), ("joiner", "compose"), ("compose", "grade"),
                          ("observe", "bind"), ("t1", "bind"), ("bind", "verdict"), ("grade", "verdict")):
        assert order.index(before) < order.index(after), (before, after)
    assert sorted(order) == sorted(s["id"] for s in toy()[0]["steps"])
    compose = next(s for s in plan.steps if s.id == "compose")
    assert compose.argv == ("python", "tool.py", "compose", "--memory", "mem", "--picks-a", "in/picks_a.json", "--out", "run")
    assert compose.actor_binding == "runner-a" and compose.result_contract == "exit0_and_outputs"
    assert len(plan.descriptor_sha256) == 64 and len(plan.obligations_sha256) == 64
    assert not hasattr(plan, "run") and not hasattr(plan, "execute")
    with pytest.raises(rc.RunContractRefused):
        rc.ResolvedPlan("0" * 64, "0" * 64, (), executable=True)


def test_the_plan_does_not_depend_on_list_or_key_order():
    parts = toy()
    plan = compile_toy(*parts)
    shuffled = copy.deepcopy(parts)
    random.Random(7).shuffle(shuffled[0]["steps"])
    shuffled[0]["artifacts"] = dict(reversed(list(shuffled[0]["artifacts"].items())))
    again = compile_toy(*shuffled)
    assert again == plan


def test_compile_reads_no_file_starts_no_process_reads_no_clock_and_opens_no_socket(monkeypatch):
    parts = toy()

    def forbidden(*args, **kwargs):
        raise AssertionError("side effect")

    for target, name in ((builtins, "open"), (io, "open"), (os, "open"), (os, "listdir"), (os, "stat"),
                         (subprocess, "Popen"), (socket, "socket"), (time, "time"), (time, "monotonic"),
                         (time, "perf_counter")):
        monkeypatch.setattr(target, name, forbidden)
    plan = compile_toy(*parts)
    assert plan.executable is False


def test_the_module_imports_only_pure_standard_modules():
    tree = ast.parse((HERE / "run_contract.py").read_text(encoding="utf-8"))
    names = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    names |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert names <= {"__future__", "dataclasses", "hashlib", "json", "re", "typing"}, names


def test_the_refusal_codes_are_the_fixed_set():
    assert rc.CODES == ("graph_invalid", "binding_unresolved", "adapter_unavailable", "capture_contract_missing",
                        "output_membership_incomplete", "input_membership_incomplete", "required_validation_unreached",
                        "semantic_contract_missing", "role_binding_incompatible")
    with pytest.raises(ValueError):
        rc.RunContractRefused("anything_else")


# --- the seven RED cases of the proposal ------------------------------------------------------------------------------

def test_1_a_missing_observation_to_binding_edge_refuses_and_a_flipped_tool_hash_does_not_clear_it():
    parts = toy()
    step(parts, "bind")["inputs"] = ["t1"]
    refused("required_validation_unreached", parts)
    flipped = copy.deepcopy(parts)
    flipped[0]["identities"]["observer.py"] = "f" * 64
    refused("binding_unresolved", flipped)  # an unreviewed identity never compiles, with or without the edge
    flipped_both = copy.deepcopy(flipped)
    flipped_both[2]["identities"]["observer.py"] = "f" * 64
    refused("required_validation_unreached", flipped_both)  # the obligation is keyed on the step, not on the hash


def test_2_a_seven_or_five_file_step_must_declare_exactly_its_written_files():
    parts = toy()
    step(parts, "shortlists")["outputs"].remove("short_b")
    step(parts, "shortlists")["outputs"].remove("judge_b")
    refused("output_membership_incomplete", parts)
    phantom = toy()
    phantom[0]["artifacts"]["short_c"] = {"path": "<RUN>/c/shortlists.jsonl"}
    step(phantom, "shortlists")["outputs"].append("short_c")
    refused("output_membership_incomplete", phantom)


def test_3_a_stdout_grader_needs_its_semantic_contract_and_no_invented_out_flag():
    parts = toy()
    step(parts, "grade")["result_contract"] = "exit0_and_outputs"
    refused("semantic_contract_missing", parts)
    invented = toy()
    step(invented, "grade")["argv"] += ["--out", "<RUN>/report.json"]
    refused("adapter_unavailable", invented)


def test_4_a_function_presented_as_a_cli_subcommand_refuses():
    parts = toy()
    bind = step(parts, "bind")
    bind.update(argv_step("bind", "observer", ["<PY>", "observer.py", "--bind", "<RUN>/t1.json"], ["observation", "t1"],
                          ["binding"]))
    parts[0]["artifacts"].update({name: {"path": f"<RUN>/{name}"} for name in captured("bind").values()})
    refused("adapter_unavailable", parts)
    no_adapter = toy()
    del no_adapter[1]["adapters"]["bind_to_t1"]
    refused("adapter_unavailable", no_adapter)
    with_argv = toy()
    step(with_argv, "bind")["argv"] = ["<PY>", "observer.py"]
    refused("adapter_unavailable", with_argv)


def test_5_an_unresolved_role_or_program_refuses_without_any_default():
    for token in ("<RUNNER>", "<PY>", "<JOINER>"):
        parts = toy()
        del parts[3][token]
        refused("binding_unresolved", parts)
    empty_actor = toy()
    step(empty_actor, "compose")["actor"] = ""
    refused("binding_unresolved", empty_actor)
    nested = toy()
    nested[3]["<RUN>"] = "<RUN2>"
    refused("binding_unresolved", nested)


def test_6_compose_must_declare_its_real_blind_and_primary_shortlist_reads():
    for name in ("blind", "short_a"):
        parts = toy()
        step(parts, "compose")["inputs"].remove(name)
        refused("input_membership_incomplete", parts)


def test_7_a_cycle_a_duplicate_or_an_unproduced_input_refuses_without_guessing_an_order():
    cycle = toy()
    step(cycle, "prepare")["inputs"].append("outputs_a")
    refused("graph_invalid", cycle)
    duplicate_writer = toy()
    step(duplicate_writer, "t1")["outputs"].append("observation")
    refused("graph_invalid", duplicate_writer)
    duplicate_step = toy()
    duplicate_step[0]["steps"].append(copy.deepcopy(step(duplicate_step, "grade")))
    refused("graph_invalid", duplicate_step)
    orphan = toy()
    orphan[0]["externals"].remove("status")
    refused("graph_invalid", orphan)


# --- capture, roles and shape ------------------------------------------------------------------------------------------

def test_every_process_step_captures_stdout_stderr_and_exit_as_its_own_outputs():
    parts = toy()
    step(parts, "compose")["capture"]["stderr"] = None
    refused("capture_contract_missing", parts)
    outside = toy()
    step(outside, "compose")["outputs"].remove("compose_err")
    refused("capture_contract_missing", outside)
    none = toy()
    step(none, "t1")["capture"] = None
    refused("capture_contract_missing", none)


def test_a_joiner_bound_to_another_role_or_shown_more_than_its_input_refuses():
    parts = toy()
    parts[3]["<JOINER>"] = "runner-a"
    refused("role_binding_incompatible", parts)
    exposed = toy()
    step(exposed, "joiner")["inputs"].append("blind")
    refused("role_binding_incompatible", exposed)


@pytest.mark.parametrize("damage", ["unknown_key", "version", "argv_type", "missing_artifact", "obligation_step",
                                    "obligation_null", "adapter_kind_list", "flag_without_dashes"])
def test_a_malformed_descriptor_or_obligation_refuses_as_an_invalid_graph(damage):
    parts = toy()
    if damage == "unknown_key":
        step(parts, "compose")["env"] = {}
    elif damage == "version":
        parts[0]["version"] = "wd.mind-run-descriptor.v0"
    elif damage == "argv_type":
        step(parts, "compose")["argv"] = "python tool.py compose"
    elif damage == "missing_artifact":
        step(parts, "compose")["inputs"].append("nowhere")
    elif damage == "obligation_step":
        parts[2]["roles"]["absent_step"] = "fresh-joiner-a"
    elif damage == "obligation_null":
        parts[2]["outputs"]["grade"] = None  # never read as an empty list
    elif damage == "adapter_kind_list":
        parts[1]["adapters"]["grader"]["kind"] = ["argv"]
    else:
        parts[1]["adapters"]["grader"]["flags"].append("out")
    refused("graph_invalid", parts)


def test_a_binding_value_never_holds_a_placeholder_even_when_unused():
    parts = toy()
    parts[3]["<UNUSED>"] = "<RUN>"
    refused("binding_unresolved", parts)


def test_the_obligation_picks_the_semantic_contract_even_when_the_adapter_offers_more():
    parts = toy()
    parts[1]["adapters"]["grader"]["result_contracts"].append("exit0_and_outputs")
    step(parts, "grade")["result_contract"] = "exit0_and_outputs"
    refused("semantic_contract_missing", parts)


def test_a_step_cannot_claim_a_result_contract_its_adapter_does_not_offer():
    parts = toy()
    step(parts, "prepare")["result_contract"] = "stdout_report_semantic"
    refused("semantic_contract_missing", parts)


def test_a_function_step_with_a_capture_record_refuses():
    parts = toy()
    step(parts, "bind")["capture"] = captured("bind")
    refused("capture_contract_missing", parts)
