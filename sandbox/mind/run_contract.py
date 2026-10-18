"""RUN CONTRACT v1: a pure checker that refuses a multi-step run plan with a known hole before any process starts.
Sandbox, advisory, never a gate and never an executor: a closed contract is CONTRACT_CLOSED_NOT_EXECUTABLE and grants
no run.

Why. The Q4 chain (prepare, two shortlists commands, fresh joiners, compose, observer, pre-case guard, a stdout-only
grader) had holes that only a long run would show late: a five- or seven-file step declared with only its primary
files, compose reading blind.jsonl and a shortlist file that no manifest binds, an observation that never reaches its
binding, a grader judged by exit 0, a Python function shown as a CLI subcommand. This module refuses those holes
early and by a fixed code. Design: Lead's round 1/10 proposal (sha256 9D354A02..., from Grok consultation 637a5f9c,
answer 5b4d5d6e), adapted by fable-5 (``RUN_CONTRACT.md`` names every adaptation).

Inputs are already strictly parsed, versioned JSON values; this module does not re-implement the fleet's JSON reader:

* ``descriptor`` (``DESCRIPTOR_VERSION``): ``identities`` {name: sha256}, ``artifacts`` {id: {"path"}}, ``externals``
  [artifact ids no step produces], ``steps`` [{id, adapter, argv, inputs, outputs, actor, result_contract, capture}].
  ``capture`` is {stdout, stderr, exit} (artifact ids among the step's own outputs) for a process step, null for a
  function step.
* ``adapter_catalog`` (``CATALOG_VERSION``): the small, separately reviewed table of allowed adapters. An ``argv``
  adapter has a fixed ``prefix`` (program and subcommand) and the ``flags`` it really offers; a ``function`` adapter
  is an in-process call and has no argv. Both name the ``result_contracts`` they can be checked by.
* ``obligations`` (``OBLIGATIONS_VERSION``): kept APART from the descriptor and reviewed against the Source on its
  own, so a writer who forgets a real file read in the descriptor cannot also make the check forget it. It pins the
  same ``identities`` and lists, per step id: the exact written ``outputs``, the ``inputs`` that must be declared,
  the ``semantic`` result contract, the ``roles`` and the ``exposure`` (every input a role may see), plus ``reach``
  pairs [artifact, step] that the graph must connect.
* ``bindings``: {"<TOKEN>": value} for every placeholder; nothing falls back to the current lane, a default model or
  the running interpreter.

Checks run in this order and the first failure raises ``RunContractRefused(code)``; the message is the code alone.
A shape error is ``graph_invalid``; an identity that differs from the obligations' pin, a missing or nested binding
or an empty actor is ``binding_unresolved``; an adapter that is absent, a function given argv, an argv that leaves the
adapter's prefix or uses a flag the adapter does not offer is ``adapter_unavailable``; a process step without its own
stdout, stderr and exit outputs is ``capture_contract_missing``; two writers, a produced external, an input nobody
produces or a cycle is ``graph_invalid``; then ``output_membership_incomplete``, ``input_membership_incomplete``,
``role_binding_incompatible``, ``required_validation_unreached`` and ``semantic_contract_missing``. The plan lists the
steps in topological order, ties by step id, so list and key order never change it. No file, process, clock or
network is touched; what this does not prove is in ``RUN_CONTRACT.md``.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

DESCRIPTOR_VERSION = "wd.mind-run-descriptor.v1"
CATALOG_VERSION = "wd.mind-run-adapters.v1"
OBLIGATIONS_VERSION = "wd.mind-run-obligations.v1"
STATUS = "CONTRACT_CLOSED_NOT_EXECUTABLE"
CODES = ("graph_invalid", "binding_unresolved", "adapter_unavailable", "capture_contract_missing",
         "output_membership_incomplete", "input_membership_incomplete", "required_validation_unreached",
         "semantic_contract_missing", "role_binding_incompatible")
DESCRIPTOR_KEYS = {"version", "identities", "artifacts", "externals", "steps"}
STEP_KEYS = {"id", "adapter", "argv", "inputs", "outputs", "actor", "result_contract", "capture"}
CAPTURE_KEYS = ("stdout", "stderr", "exit")
CATALOG_KEYS = {"version", "adapters"}
ADAPTER_KEYS = {"argv": {"kind", "prefix", "flags", "result_contracts"}, "function": {"kind", "result_contracts"}}
OBLIGATION_KEYS = {"version", "identities", "outputs", "inputs", "reach", "semantic", "roles", "exposure"}
TOKEN = re.compile(r"<[A-Z0-9_]+>")
SHA256 = re.compile(r"[0-9a-f]{64}")


class RunContractRefused(Exception):
    """A refused contract; ``code`` is one of ``CODES`` and is the whole message."""

    def __init__(self, code: str):
        if code not in CODES:
            raise ValueError("unknown refusal code")
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StepSpec:
    id: str
    adapter_id: str
    argv: tuple[str, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    actor_binding: str
    result_contract: str


@dataclass(frozen=True)
class ResolvedPlan:
    descriptor_sha256: str
    obligations_sha256: str
    steps: tuple[StepSpec, ...]
    executable: bool = False
    status: str = STATUS

    def __post_init__(self):
        if self.executable is not False or self.status != STATUS:
            raise RunContractRefused("graph_invalid")  # a plan is never executable


def _refuse(code: str):
    raise RunContractRefused(code)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _texts(value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        _refuse("graph_invalid")
    return value


def _keys(value: Any, keys: set) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        _refuse("graph_invalid")
    return value


def _table(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) or not key for key in value):
        _refuse("graph_invalid")
    return value


def _identities(value: Any) -> dict[str, str]:
    table = _table(value)
    if any(not isinstance(digest, str) or not SHA256.fullmatch(digest) for digest in table.values()):
        _refuse("graph_invalid")
    return table


def _shape(descriptor, catalog, obligations):
    """Exact keys, versions and types; every reference names a declared step or artifact (``graph_invalid``)."""
    _keys(descriptor, DESCRIPTOR_KEYS)
    _keys(catalog, CATALOG_KEYS)
    _keys(obligations, OBLIGATION_KEYS)
    if (descriptor["version"], catalog["version"], obligations["version"]) != (
            DESCRIPTOR_VERSION, CATALOG_VERSION, OBLIGATIONS_VERSION):
        _refuse("graph_invalid")
    _identities(descriptor["identities"])
    _identities(obligations["identities"])
    artifacts = _table(descriptor["artifacts"])
    for entry in artifacts.values():
        if not isinstance(_keys(entry, {"path"})["path"], str) or not entry["path"]:
            _refuse("graph_invalid")
    externals = _texts(descriptor["externals"])
    if len(set(externals)) != len(externals) or not set(externals) <= set(artifacts):
        _refuse("graph_invalid")
    if not isinstance(descriptor["steps"], list) or not descriptor["steps"]:
        _refuse("graph_invalid")
    steps = {}
    for step in descriptor["steps"]:
        _keys(step, STEP_KEYS)
        for key in ("id", "adapter", "result_contract"):
            if not isinstance(step[key], str) or not step[key]:
                _refuse("graph_invalid")
        if not isinstance(step["actor"], str) or not isinstance(step["argv"], list) or step["id"] in steps:
            _refuse("graph_invalid")
        if any(not isinstance(token, str) or not token for token in step["argv"]):
            _refuse("graph_invalid")
        for key in ("inputs", "outputs"):
            names = _texts(step[key])
            if len(set(names)) != len(names) or not set(names) <= set(artifacts):
                _refuse("graph_invalid")
        capture = step["capture"]
        if capture is not None:
            _keys(capture, set(CAPTURE_KEYS))
            if any(value is not None and not isinstance(value, str) for value in capture.values()):
                _refuse("graph_invalid")
        steps[step["id"]] = step
    for adapter in _table(catalog["adapters"]).values():
        kind = adapter.get("kind") if isinstance(adapter, dict) else None
        if not isinstance(kind, str) or kind not in ADAPTER_KEYS:
            _refuse("graph_invalid")
        _keys(adapter, ADAPTER_KEYS[kind])
        _texts(adapter["result_contracts"])
        if kind == "argv":
            _texts(adapter["prefix"])
            if any(not flag.startswith("--") or len(flag) < 3 for flag in _texts(adapter["flags"])):
                _refuse("graph_invalid")
    for key in ("outputs", "inputs", "exposure"):
        for step_id, names in _table(obligations[key]).items():
            if step_id not in steps or not set(_texts(names)) <= set(artifacts):
                _refuse("graph_invalid")
    for key in ("semantic", "roles"):
        for step_id, value in _table(obligations[key]).items():
            if step_id not in steps or not isinstance(value, str) or not value:
                _refuse("graph_invalid")
    if not isinstance(obligations["reach"], list):
        _refuse("graph_invalid")
    for pair in obligations["reach"]:
        if not isinstance(pair, list) or len(_texts(pair)) != 2 or pair[0] not in artifacts or pair[1] not in steps:
            _refuse("graph_invalid")
    return steps, artifacts, externals


def _resolver(bindings):
    """A resolve(text) that replaces every placeholder or refuses ``binding_unresolved``; values never nest."""
    table = _table(bindings) if isinstance(bindings, dict) else _refuse("binding_unresolved")
    for token, value in table.items():
        if not TOKEN.fullmatch(token) or not isinstance(value, str) or not value or "<" in value or ">" in value:
            _refuse("binding_unresolved")

    def resolve(text: str) -> str:
        done = TOKEN.sub(lambda match: table.get(match.group(0), match.group(0)), text)
        if "<" in done or ">" in done or not done:
            _refuse("binding_unresolved")
        return done

    return resolve


def _order(steps: Mapping[str, Mapping[str, Any]], producer: Mapping[str, str]) -> list[str]:
    """Topological order, ties by step id; a cycle refuses instead of guessing an order."""
    needs = {sid: {producer[name] for name in step["inputs"] if name in producer} for sid, step in steps.items()}
    order, done = [], set()
    while len(order) < len(steps):
        ready = sorted(sid for sid, wanted in needs.items() if sid not in done and wanted <= done)
        if not ready:
            _refuse("graph_invalid")
        order.append(ready[0])
        done.add(ready[0])
    return order


def _reaches(artifact: str, target: str, steps: Mapping[str, Mapping[str, Any]]) -> bool:
    seen, frontier = set(), [artifact]
    while frontier:
        name = frontier.pop()
        for sid, step in steps.items():
            if name in step["inputs"]:
                if sid == target:
                    return True
                for out in step["outputs"]:
                    if out not in seen:
                        seen.add(out)
                        frontier.append(out)
    return False


def compile_contract(descriptor: Mapping[str, Any], *, bindings: Mapping[str, str], adapter_catalog: Mapping[str, Any],
                     obligations: Mapping[str, Any]) -> ResolvedPlan:
    """The closed, never-executable plan of ``descriptor``, or ``RunContractRefused`` with the first failing code."""
    steps, artifacts, externals = _shape(descriptor, adapter_catalog, obligations)
    if descriptor["identities"] != obligations["identities"]:
        _refuse("binding_unresolved")  # an identity the obligations were not reviewed for
    resolve = _resolver(bindings)
    resolved = {}
    for sid, step in steps.items():
        actor = resolve(step["actor"]) if step["actor"] else _refuse("binding_unresolved")
        resolved[sid] = (tuple(resolve(token) for token in step["argv"]), actor)
    for entry in artifacts.values():
        resolve(entry["path"])
    adapters = adapter_catalog["adapters"]
    for sid, step in steps.items():
        adapter = adapters.get(step["adapter"])
        argv = resolved[sid][0]
        if adapter is None or (adapter["kind"] == "function") != (not argv):
            _refuse("adapter_unavailable")
        if adapter["kind"] == "argv":
            prefix = tuple(resolve(token) for token in adapter["prefix"])
            rest = argv[len(prefix):]
            if argv[:len(prefix)] != prefix or len(rest) % 2:
                _refuse("adapter_unavailable")
            if any(flag not in adapter["flags"] or value.startswith("--") for flag, value in zip(rest[::2], rest[1::2])):
                _refuse("adapter_unavailable")
    captures = {}
    for sid, step in steps.items():
        if adapters[step["adapter"]]["kind"] == "function":
            if step["capture"] is not None:
                _refuse("capture_contract_missing")  # a function step has no process to capture
            captures[sid] = set()
            continue
        names = [None if step["capture"] is None else step["capture"][key] for key in CAPTURE_KEYS]
        if None in names or len(set(names)) != 3 or not set(names) <= set(step["outputs"]):
            _refuse("capture_contract_missing")
        captures[sid] = set(names)
    producer = {}
    for sid, step in steps.items():
        for name in step["outputs"]:
            if name in producer or name in externals:
                _refuse("graph_invalid")
            producer[name] = sid
    if any(name not in producer and name not in externals for step in steps.values() for name in step["inputs"]):
        _refuse("graph_invalid")
    order = _order(steps, producer)
    for sid, wanted in obligations["outputs"].items():
        if set(steps[sid]["outputs"]) - captures[sid] != set(wanted):
            _refuse("output_membership_incomplete")
    for sid, wanted in obligations["inputs"].items():
        if not set(wanted) <= set(steps[sid]["inputs"]):
            _refuse("input_membership_incomplete")
    for sid, role in obligations["roles"].items():
        if resolved[sid][1] != role:
            _refuse("role_binding_incompatible")
    for sid, allowed in obligations["exposure"].items():
        if not set(steps[sid]["inputs"]) <= set(allowed):
            _refuse("role_binding_incompatible")
    for artifact, target in obligations["reach"]:
        if not _reaches(artifact, target, steps):
            _refuse("required_validation_unreached")
    for sid, step in steps.items():
        if step["result_contract"] not in adapters[step["adapter"]]["result_contracts"]:
            _refuse("semantic_contract_missing")
        if obligations["semantic"].get(sid, step["result_contract"]) != step["result_contract"]:
            _refuse("semantic_contract_missing")
    canonical = dict(descriptor, steps=sorted(descriptor["steps"], key=lambda step: step["id"]))
    plan = tuple(StepSpec(sid, steps[sid]["adapter"], resolved[sid][0], tuple(sorted(steps[sid]["inputs"])),
                          tuple(sorted(steps[sid]["outputs"])), resolved[sid][1], steps[sid]["result_contract"])
                 for sid in order)
    return ResolvedPlan(hashlib.sha256(_canonical(canonical)).hexdigest(),
                        hashlib.sha256(_canonical(obligations)).hexdigest(), plan)
