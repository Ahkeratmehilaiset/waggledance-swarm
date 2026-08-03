# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import pytest

from waggledance.core.cell_identity import build_cell_identity
from waggledance.core.genesis_lineage import build_root_entry
from waggledance.core.learning import understanding_coding_candidate_builder as module
from waggledance.core.learning.understanding_coding_candidate_builder import (
    CodingCandidateAdmissionPlanV1,
    CodingCandidateArtifactV1,
    CodingCandidateBuildReceiptV1,
    CodingCandidateBuildRequestV1,
    CodingCandidateBuildResultV1,
    CodingCandidateBuildStatus,
    CodingCandidateCellBindingV1,
    CodingCandidateContractError,
    CodingCandidateMode,
    CodingCandidatePolicyV1,
    CodingCandidateReasonCode,
    CodingCandidateSourcePackV1,
    build_understanding_coding_candidate,
    derive_current_coding_candidate_worker_digest,
    derive_current_interpreter_artifact_digest,
    derive_current_interpreter_identity_digest,
)
from waggledance.core.learning.understanding_contracts import HexCellAddressV1
from waggledance.core.learning import understanding_paired_runner as c7_module
from waggledance.core.learning.understanding_paired_runner import (
    PairedRunnerContractError,
)
from waggledance.core.magma.canonical import canonical_json_bytes, sha256_digest


SOLVER = b"def solve(payload):\n    return payload\n"
TESTS = b"def test_identity():\n    assert solve(1) == 1\n"


def _digest(label: str) -> str:
    return sha256_digest({"test": label})


def _context(
    *,
    solver: bytes = SOLVER,
    tests: bytes = TESTS,
    policy: CodingCandidatePolicyV1 | None = None,
) -> tuple[CodingCandidatePolicyV1, CodingCandidateBuildRequestV1]:
    policy = policy or CodingCandidatePolicyV1(
        mode=CodingCandidateMode.STATIC_SHADOW
    )
    identity = build_cell_identity(
        pubkey_digest=_digest("pubkey"),
        genesis_material_digest=_digest("genesis-material"),
        created_at_utc="2026-08-03T00:00:00Z",
    )
    lineage = build_root_entry(
        cell_id=identity.cell_id,
        inherited_goal_slice_digest=_digest("goal"),
        inherited_budget_slice_digest=_digest("budget"),
    )
    binding = CodingCandidateCellBindingV1(
        hex_cell=HexCellAddressV1(
            cell_id="understanding-center",
            q=0,
            r=0,
            incarnation_id="incarnation-1",
            generation=0,
            fence=0,
        ),
        cell_identity=identity,
        genesis_lineage_entry=lineage,
        subdivision_address_digest=_digest("subdivision"),
        registry_snapshot_digest=_digest("registry"),
    )
    source_pack = CodingCandidateSourcePackV1(
        solver_source_utf8=solver,
        test_source_utf8=tests,
    )
    plan = CodingCandidateAdmissionPlanV1(
        campaign_id="c8a-static-candidate",
        gap_evidence_digest=_digest("gap"),
        hex_cell_address_digest=binding.hex_cell_address_digest,
        cell_identity_digest=binding.cell_identity_digest,
        genesis_lineage_entry_hash=binding.genesis_lineage_entry_hash,
        subdivision_address_digest=binding.subdivision_address_digest,
        registry_snapshot_digest=binding.registry_snapshot_digest,
        cell_binding_digest=binding.cell_binding_digest,
        source_manifest_digest=source_pack.source_manifest_digest,
        generator_request_digest=_digest("generator-request"),
        generator_response_digest=_digest("generator-response"),
        generator_prompt_digest=_digest("generator-prompt"),
        generator_model_digest=_digest("generator-model"),
        generator_artifact_digest=_digest("generator-artifact"),
        worker_artifact_digest=derive_current_coding_candidate_worker_digest(),
        interpreter_artifact_digest=derive_current_interpreter_artifact_digest(),
        interpreter_identity_digest=derive_current_interpreter_identity_digest(),
        toolchain_digest=_digest("toolchain"),
        environment_digest=_digest("environment"),
        resource_policy_digest=policy.policy_digest,
    )
    return policy, CodingCandidateBuildRequestV1(
        plan=plan,
        source_pack=source_pack,
        cell_binding=binding,
    )


def _run(
    *,
    solver: bytes = SOLVER,
    tests: bytes = TESTS,
    policy: CodingCandidatePolicyV1 | None = None,
):
    policy, request = _context(solver=solver, tests=tests, policy=policy)
    result = build_understanding_coding_candidate(request, policy=policy)
    assert result is not None
    return result


def _reseal_receipt(
    receipt: CodingCandidateBuildReceiptV1, **changes: Any
) -> CodingCandidateBuildReceiptV1:
    constructor = {field.name: getattr(receipt, field.name) for field in fields(receipt)}
    constructor.update(changes)
    core = receipt.to_mapping()
    core.pop("receipt_digest")
    for name, value in changes.items():
        core[name] = value.value if hasattr(value, "value") else value
    constructor["receipt_digest"] = sha256_digest(
        {
            "domain": "wd.understanding.coding_candidate_receipt.digest.v1",
            **core,
        }
    )
    return CodingCandidateBuildReceiptV1(**constructor)


def test_default_off_returns_before_request_or_worker_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Bomb:
        def __getattribute__(self, name: str) -> Any:
            raise AssertionError(f"OFF inspected {name}")

    monkeypatch.setattr(
        module,
        "_revalidate_request_bindings",
        lambda request: (_ for _ in ()).throw(AssertionError("binding inspected")),
    )
    assert build_understanding_coding_candidate(Bomb()) is None  # type: ignore[arg-type]


def test_static_shadow_builds_deterministic_inert_package() -> None:
    policy, request = _context()
    first = build_understanding_coding_candidate(request, policy=policy)
    second = build_understanding_coding_candidate(request, policy=policy)
    assert first is not None and second is not None
    assert first.receipt.status is CodingCandidateBuildStatus.PACKAGED
    assert first.receipt.reason_code is CodingCandidateReasonCode.PACKAGED
    assert first.artifact is not None and second.artifact is not None
    assert first.artifact.artifact_bytes == second.artifact.artifact_bytes
    assert first.receipt.to_mapping() == second.receipt.to_mapping()
    assert first.receipt.candidate_source_not_executed is True
    assert first.receipt.candidate_tests_not_executed is True
    assert first.receipt.compatibility_screen_passed is True
    assert first.receipt.worker_process_observed is True
    assert first.receipt.direct_child_reaped is True


def test_package_is_canonical_source_not_bytecode() -> None:
    result = _run()
    assert result.artifact is not None
    artifact = json.loads(result.artifact.artifact_bytes.decode("utf-8"))
    assert canonical_json_bytes(artifact) == result.artifact.artifact_bytes
    assert artifact["format"] == "canonical-python-source-package-v1"
    assert [row["logical_name"] for row in artifact["files"]] == [
        "solver.py",
        "test_solver.py",
    ]
    assert base64.b64decode(artifact["files"][0]["content"]) == SOLVER
    assert base64.b64decode(artifact["files"][1]["content"]) == TESTS
    assert b".pyc" not in result.artifact.artifact_bytes
    assert b"marshal" not in result.artifact.artifact_bytes


def test_raise_statement_is_compiled_but_never_evaluated() -> None:
    canary = "CANDIDATE-RAISE-MUST-NOT-EXECUTE"
    solver = (
        "def solve(payload):\n"
        f"    raise RuntimeError({canary!r})\n"
    ).encode("utf-8")
    result = _run(solver=solver)
    assert result.receipt.status is CodingCandidateBuildStatus.PACKAGED
    assert canary not in json.dumps(result.receipt.to_mapping(), sort_keys=True)
    assert canary not in repr(result)


def test_encoding_cookie_text_inside_string_literal_is_not_a_cookie() -> None:
    result = _run(
        solver=b'def solve(payload):\n    return "coding: utf-8"\n'
    )
    assert result.receipt.status is CodingCandidateBuildStatus.PACKAGED


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("open('SIDE-EFFECT-CANARY', 'w')", CodingCandidateReasonCode.AST_POLICY_REFUSED),
        (
            "vars(__builtins__)['__import__']('socket').socket()",
            CodingCandidateReasonCode.AST_POLICY_REFUSED,
        ),
        ("while True:\n        pass", CodingCandidateReasonCode.AST_POLICY_REFUSED),
        ("return [0] * (10 ** 12)", CodingCandidateReasonCode.AST_POLICY_REFUSED),
        ("return pow(10, 1000000000)", CodingCandidateReasonCode.AST_POLICY_REFUSED),
        (
            "op = open\n    return op('SIDE-EFFECT-CANARY', 'w')",
            CodingCandidateReasonCode.AST_POLICY_REFUSED,
        ),
        ("import pathlib\n    return payload", CodingCandidateReasonCode.AST_POLICY_REFUSED),
    ],
)
def test_dangerous_compatibility_shapes_are_refused_without_execution(
    body: str, reason: CodingCandidateReasonCode, tmp_path: Path
) -> None:
    canary = tmp_path / "SIDE-EFFECT-CANARY"
    rendered = body.replace("SIDE-EFFECT-CANARY", canary.as_posix())
    solver = f"def solve(payload):\n    {rendered}\n".encode("utf-8")
    result = _run(solver=solver)
    assert result.artifact is None
    assert result.receipt.status is CodingCandidateBuildStatus.SOURCE_REJECTED
    assert result.receipt.reason_code is reason
    assert not canary.exists()


@pytest.mark.parametrize(
    ("solver", "reason"),
    [
        (b"", CodingCandidateReasonCode.EMPTY_SOURCE),
        (b"\xff", CodingCandidateReasonCode.INVALID_SOURCE_ENCODING),
        (
            b"\xef\xbb\xbfdef solve(payload):\n    return payload\n",
            CodingCandidateReasonCode.SOURCE_BOM_REFUSED,
        ),
        (
            b"def solve(payload):\n    return b'\x00'\n",
            CodingCandidateReasonCode.SOURCE_NUL_REFUSED,
        ),
        (
            b"def solve(payload):\r\n    return payload\r\n",
            CodingCandidateReasonCode.SOURCE_NEWLINE_REFUSED,
        ),
        (
            b"# coding: utf-8\ndef solve(payload):\n    return payload\n",
            CodingCandidateReasonCode.SOURCE_ENCODING_COOKIE_REFUSED,
        ),
    ],
)
def test_byte_preflight_refuses_malformed_source_without_spawning(
    solver: bytes,
    reason: CodingCandidateReasonCode,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("spawned")),
    )
    result = _run(solver=solver)
    assert result.artifact is None
    assert result.receipt.reason_code is reason
    assert result.receipt.worker_process_observed is False
    assert result.receipt.fixed_worker_digest_matched is False


def test_source_size_line_and_policy_bounds_fail_closed() -> None:
    policy = CodingCandidatePolicyV1(
        mode=CodingCandidateMode.STATIC_SHADOW,
        max_solver_source_bytes=32,
        max_test_source_bytes=64,
        max_total_source_bytes=96,
        max_source_lines=2,
    )
    result = _run(policy=policy)
    assert result.receipt.reason_code is CodingCandidateReasonCode.SOURCE_SIZE_REFUSED
    with pytest.raises(CodingCandidateContractError):
        CodingCandidatePolicyV1(max_ast_nodes=True)  # type: ignore[arg-type]
    with pytest.raises(CodingCandidateContractError):
        CodingCandidatePolicyV1(max_wall_milliseconds=30_001)


@pytest.mark.parametrize(
    ("policy_changes", "solver", "reason"),
    [
        (
            {"max_source_lines": 1},
            SOLVER,
            CodingCandidateReasonCode.SOURCE_LINE_COUNT_REFUSED,
        ),
        (
            {
                "max_solver_source_bytes": 64,
                "max_test_source_bytes": 64,
                "max_total_source_bytes": 64,
            },
            SOLVER,
            CodingCandidateReasonCode.TOTAL_SOURCE_SIZE_REFUSED,
        ),
        (
            {"max_tokens": 8},
            SOLVER,
            CodingCandidateReasonCode.SOURCE_POLICY_REFUSED,
        ),
        (
            {"max_ast_nodes": 5},
            SOLVER,
            CodingCandidateReasonCode.AST_POLICY_REFUSED,
        ),
        (
            {"max_ast_depth": 3},
            SOLVER,
            CodingCandidateReasonCode.AST_POLICY_REFUSED,
        ),
        (
            {"max_literal_bytes": 3},
            b"def solve(payload):\n    return 'four'\n",
            CodingCandidateReasonCode.AST_POLICY_REFUSED,
        ),
        (
            {"max_integer_digits": 2},
            b"def solve(payload):\n    return 123\n",
            CodingCandidateReasonCode.AST_POLICY_REFUSED,
        ),
    ],
)
def test_every_declared_source_and_ast_limit_is_falsifiable(
    policy_changes: dict[str, int],
    solver: bytes,
    reason: CodingCandidateReasonCode,
) -> None:
    policy = CodingCandidatePolicyV1(
        mode=CodingCandidateMode.STATIC_SHADOW,
        **policy_changes,
    )
    result = _run(solver=solver, policy=policy)
    assert result.artifact is None
    assert result.receipt.reason_code is reason


def test_syntax_and_interface_failures_return_bounded_reasons() -> None:
    syntax = _run(solver=b"def solve(:\n")
    assert syntax.receipt.reason_code is CodingCandidateReasonCode.SYNTAX_REFUSED
    interface = _run(solver=b"def other(payload):\n    return payload\n")
    assert (
        interface.receipt.reason_code
        is CodingCandidateReasonCode.SOLVER_INTERFACE_REFUSED
    )
    test_interface = _run(tests=b"value = solve(1)\n")
    assert (
        test_interface.receipt.reason_code
        is CodingCandidateReasonCode.TEST_INTERFACE_REFUSED
    )
    surrogate = _run(solver=b'def solve(payload):\n    return "\\ud800"\n')
    assert surrogate.receipt.reason_code is CodingCandidateReasonCode.AST_POLICY_REFUSED


def test_plan_source_hex_and_policy_bindings_are_fail_closed() -> None:
    policy, request = _context()
    with pytest.raises(CodingCandidateContractError, match="source_manifest_digest"):
        CodingCandidateBuildRequestV1(
            plan=replace(request.plan, source_manifest_digest=_digest("wrong-source")),
            source_pack=request.source_pack,
            cell_binding=request.cell_binding,
        )
    changed_hex = replace(request.cell_binding.hex_cell, q=1)
    changed_binding = replace(request.cell_binding, hex_cell=changed_hex)
    with pytest.raises(CodingCandidateContractError, match="hex_cell_address_digest"):
        CodingCandidateBuildRequestV1(
            plan=request.plan,
            source_pack=request.source_pack,
            cell_binding=changed_binding,
        )
    with pytest.raises(CodingCandidateContractError, match="resource policy"):
        build_understanding_coding_candidate(
            request,
            policy=replace(policy, max_ast_nodes=policy.max_ast_nodes - 1),
        )


@pytest.mark.parametrize(
    ("field_name", "forced_value", "message"),
    [
        ("schema_version", "forged-plan-schema", "schema_version"),
        ("ast_policy_digest", _digest("forged-ast-policy"), "AST compatibility"),
        ("attempt_budget", 2, "one candidate attempt"),
        ("static_admission_only", False, "literal true"),
    ],
)
def test_post_construction_plan_mutation_is_revalidated(
    field_name: str, forced_value: Any, message: str
) -> None:
    policy, request = _context()
    object.__setattr__(request.plan, field_name, forced_value)
    with pytest.raises(CodingCandidateContractError, match=message):
        build_understanding_coding_candidate(request, policy=policy)


def test_post_construction_policy_mutation_cannot_raise_hard_ceiling() -> None:
    policy, request = _context()
    object.__setattr__(policy, "max_ast_nodes", 100_001)
    object.__setattr__(request.plan, "resource_policy_digest", policy.policy_digest)
    with pytest.raises(CodingCandidateContractError, match="max_ast_nodes"):
        build_understanding_coding_candidate(request, policy=policy)


def test_identity_lineage_relation_and_post_construction_tamper_are_rechecked() -> None:
    _, request = _context()
    other_identity = build_cell_identity(
        pubkey_digest=_digest("other-pubkey"),
        genesis_material_digest=_digest("other-genesis"),
        created_at_utc="2026-08-03T00:00:01Z",
    )
    with pytest.raises(CodingCandidateContractError, match="different cells"):
        replace(request.cell_binding, cell_identity=other_identity)

    policy, request = _context()
    object.__setattr__(
        request.cell_binding.cell_identity, "cell_id", _digest("forged-cell")
    )
    with pytest.raises(CodingCandidateContractError, match="identity recompute"):
        build_understanding_coding_candidate(request, policy=policy)


def test_worker_and_interpreter_artifacts_must_match_fixed_local_bytes() -> None:
    policy, request = _context()
    wrong_worker = CodingCandidateBuildRequestV1(
        plan=replace(request.plan, worker_artifact_digest=_digest("wrong-worker")),
        source_pack=request.source_pack,
        cell_binding=request.cell_binding,
    )
    with pytest.raises(CodingCandidateContractError, match="worker artifact"):
        build_understanding_coding_candidate(wrong_worker, policy=policy)
    wrong_interpreter = CodingCandidateBuildRequestV1(
        plan=replace(
            request.plan,
            interpreter_artifact_digest=_digest("wrong-interpreter"),
        ),
        source_pack=request.source_pack,
        cell_binding=request.cell_binding,
    )
    with pytest.raises(CodingCandidateContractError, match="interpreter artifact"):
        build_understanding_coding_candidate(wrong_interpreter, policy=policy)


def _python_command(source: str) -> tuple[str, ...]:
    return (
        str(module._current_interpreter_binary_path()),
        "-I",
        "-S",
        "-E",
        "-B",
        "-c",
        source,
    )


def test_worker_timeout_is_direct_child_only_and_produces_no_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = CodingCandidatePolicyV1(
        mode=CodingCandidateMode.STATIC_SHADOW,
        max_wall_milliseconds=50,
    )
    _, request = _context(policy=policy)
    monkeypatch.setattr(module, "_worker_command", lambda: _python_command("while True: pass"))
    result = build_understanding_coding_candidate(request, policy=policy)
    assert result is not None and result.artifact is None
    assert result.receipt.status is CodingCandidateBuildStatus.WORKER_TIMEOUT
    assert result.receipt.direct_child_wall_timeout_enforced is True
    assert result.receipt.process_tree_termination_enforced is False


def test_thread_start_failure_still_reaps_child_and_removes_temp_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, request = _context()
    monkeypatch.setattr(module, "_worker_command", lambda: _python_command("while True: pass"))
    real_popen = subprocess.Popen
    observed: dict[str, Any] = {}

    def capture(*args: Any, **kwargs: Any):
        process = real_popen(*args, **kwargs)
        observed["process"] = process
        observed["cwd"] = Path(kwargs["cwd"])
        return process

    real_start = module.threading.Thread.start
    start_count = 0

    def fail_second_start(thread: Any) -> None:
        nonlocal start_count
        start_count += 1
        if start_count == 2:
            raise RuntimeError("forced thread-start failure")
        real_start(thread)

    monkeypatch.setattr(module.subprocess, "Popen", capture)
    monkeypatch.setattr(module.threading.Thread, "start", fail_second_start)
    with pytest.raises(RuntimeError, match="forced thread-start failure"):
        build_understanding_coding_candidate(request, policy=policy)
    process = observed["process"]
    assert process.poll() is not None
    assert not observed["cwd"].exists()


def test_worker_stdout_flood_is_incrementally_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = CodingCandidatePolicyV1(
        mode=CodingCandidateMode.STATIC_SHADOW,
        max_worker_stdout_bytes=4_096,
    )
    _, request = _context(policy=policy)
    command = "import sys;sys.stdout.buffer.write(b'x'*1000000);sys.stdout.flush()"
    monkeypatch.setattr(module, "_worker_command", lambda: _python_command(command))
    result = build_understanding_coding_candidate(request, policy=policy)
    assert result is not None and result.artifact is None
    assert result.receipt.status is CodingCandidateBuildStatus.WORKER_OUTPUT_LIMIT
    assert result.receipt.direct_child_output_caps_enforced is True


def test_bounded_hostile_worker_json_becomes_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, request = _context()
    source = "import sys;sys.stdout.write('{\"n\":' + '1'*5000 + '}')"
    monkeypatch.setattr(module, "_worker_command", lambda: _python_command(source))
    result = build_understanding_coding_candidate(request, policy=policy)
    assert result is not None and result.artifact is None
    assert result.receipt.status is CodingCandidateBuildStatus.PROTOCOL_ERROR


def test_worker_unavailable_nonzero_and_malformed_protocol_are_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, request = _context()
    real_popen = subprocess.Popen
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unavailable")),
    )
    unavailable = build_understanding_coding_candidate(request, policy=policy)
    assert unavailable is not None
    assert unavailable.receipt.status is CodingCandidateBuildStatus.WORKER_UNAVAILABLE
    monkeypatch.setattr(module.subprocess, "Popen", real_popen)

    monkeypatch.setattr(
        module, "_worker_command", lambda: _python_command("raise SystemExit(7)")
    )
    nonzero = build_understanding_coding_candidate(request, policy=policy)
    assert nonzero is not None
    assert nonzero.receipt.status is CodingCandidateBuildStatus.WORKER_EXIT_ERROR

    monkeypatch.setattr(
        module, "_worker_command", lambda: _python_command("print('not-json')")
    )
    malformed = build_understanding_coding_candidate(request, policy=policy)
    assert malformed is not None
    assert malformed.receipt.status is CodingCandidateBuildStatus.PROTOCOL_ERROR


def test_worker_digest_forgery_is_recomputed_by_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, request = _context()
    response = {
        "solver_source_digest": _digest("forged-solver"),
        "test_source_digest": _digest("forged-tests"),
        "artifact_manifest_digest": _digest("forged-manifest"),
        "artifact_digest": _digest("forged-artifact"),
        "artifact_byte_count": 123,
    }
    monkeypatch.setattr(
        module,
        "_invoke_worker",
        lambda request, policy: module._WorkerOutcome(
            CodingCandidateBuildStatus.PACKAGED,
            CodingCandidateReasonCode.PACKAGED,
            response,
            True,
        ),
    )
    result = build_understanding_coding_candidate(request, policy=policy)
    assert result is not None and result.artifact is None
    assert result.receipt.status is CodingCandidateBuildStatus.DIGEST_MISMATCH


def test_fixed_launch_uses_minimal_environment_without_host_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, request = _context()
    monkeypatch.setenv("WD_C8A_SECRET_CANARY", "MUST-NOT-INHERIT")
    real_popen = subprocess.Popen
    seen: dict[str, Any] = {}

    def capture(*args: Any, **kwargs: Any):
        seen.update(kwargs)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(module.subprocess, "Popen", capture)
    result = build_understanding_coding_candidate(request, policy=policy)
    assert result is not None
    assert result.receipt.status is CodingCandidateBuildStatus.PACKAGED
    assert result.receipt.fresh_disposable_cwd_created_and_removed is True
    assert "WD_C8A_SECRET_CANARY" not in seen["env"]
    assert seen["shell"] is False
    temporary_cwd = Path(seen["cwd"])
    assert temporary_cwd.is_absolute()
    assert not temporary_cwd.exists()
    assert tuple(seen.get("args", ())) == ()


def test_worker_command_is_fixed_absolute_and_uses_isolated_flags() -> None:
    command = module._worker_command()
    assert Path(command[0]).is_absolute()
    assert command[1:5] == ("-I", "-S", "-E", "-B")
    assert Path(command[5]).resolve() == module._WORKER_PATH.resolve()


def _run_private_worker(raw: bytes, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        module._worker_command(),
        input=raw,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=module._minimal_worker_environment(),
        timeout=5,
        check=False,
    )


def test_private_worker_protocol_has_own_frame_policy_and_fixed_digest_bounds(
    tmp_path: Path,
) -> None:
    policy, request = _context()
    payload = json.loads(module._worker_payload(request, policy).decode("utf-8"))
    payload["policy"]["max_solver_source_bytes"] = 262_145
    payload["policy_digest"] = sha256_digest(
        {
            "domain": "wd.understanding.coding_candidate_policy.v1",
            **payload["policy"],
        }
    )
    oversized_policy = _run_private_worker(canonical_json_bytes(payload), tmp_path)
    assert oversized_policy.returncode == 0
    assert json.loads(oversized_policy.stdout)["reason_code"] == "invalid_protocol"

    payload = json.loads(module._worker_payload(request, policy).decode("utf-8"))
    payload["interface_contract_digest"] = _digest("forged-interface")
    forged_contract = _run_private_worker(canonical_json_bytes(payload), tmp_path)
    assert forged_contract.returncode == 0
    assert json.loads(forged_contract.stdout)["reason_code"] == "invalid_protocol"

    oversized_frame = _run_private_worker(b"x" * 800_001, tmp_path)
    assert oversized_frame.returncode == 0
    assert len(oversized_frame.stdout) < 4_096
    assert json.loads(oversized_frame.stdout)["reason_code"] == "invalid_protocol"


def test_request_has_no_command_path_environment_callback_or_executor_seam() -> None:
    names = {field.name for field in fields(CodingCandidateBuildRequestV1)}
    for forbidden in (
        "command",
        "argv",
        "environment",
        "cwd",
        "path",
        "mount",
        "callback",
        "executor",
        "provider",
        "expected_output",
        "commitment_key",
    ):
        assert forbidden not in names


def test_receipt_is_raw_free_and_all_authority_and_sandbox_claims_are_false() -> None:
    canary = "PRIVATE-SOURCE-PATH-C:/SECRET/CANARY"
    result = _run(
        solver=(f"def solve(payload):\n    return {canary!r}\n").encode("utf-8")
    )
    mapping = result.receipt.to_mapping()
    public = json.dumps(mapping, sort_keys=True) + repr(result.receipt)
    assert canary not in public
    for forbidden_key in ("source", "stdout", "stderr", "path", "pid", "exception"):
        assert forbidden_key not in mapping
    for name in module._FALSE_RECEIPT_FIELDS:
        assert mapping[name] is False
    with pytest.raises(CodingCandidateContractError, match="literal false"):
        replace(result.receipt, os_sandbox_applied=True)
    with pytest.raises(CodingCandidateContractError, match="inconsistent"):
        replace(result.receipt, worker_process_observed=False)


def test_receipt_recomputes_source_relation_and_fixed_policy_digests() -> None:
    result = _run()
    with pytest.raises(CodingCandidateContractError, match="source manifest relation"):
        _reseal_receipt(
            result.receipt,
            solver_source_digest=_digest("resealed-but-unrelated-solver"),
        )
    with pytest.raises(CodingCandidateContractError, match="ast_policy_digest"):
        _reseal_receipt(
            result.receipt,
            ast_policy_digest=_digest("resealed-but-unfixed-ast-policy"),
        )


def test_build_result_rejects_valid_resealed_receipt_for_different_source() -> None:
    result = _run()
    assert result.artifact is not None
    different_solver_digest = _digest("different-solver-source")
    different_source_manifest_digest = sha256_digest(
        {
            "domain": "wd.understanding.coding_candidate_source_manifest.digest.v1",
            **module._source_manifest_from_components(
                solver_digest=different_solver_digest,
                solver_byte_count=result.receipt.solver_source_byte_count,
                test_digest=result.receipt.test_source_digest,
                test_byte_count=result.receipt.test_source_byte_count,
            ),
        }
    )
    resealed = _reseal_receipt(
        result.receipt,
        solver_source_digest=different_solver_digest,
        source_manifest_digest=different_source_manifest_digest,
    )
    with pytest.raises(CodingCandidateContractError, match="relation mismatch"):
        CodingCandidateBuildResultV1(artifact=result.artifact, receipt=resealed)


def test_artifact_rejects_byte_and_manifest_tampering() -> None:
    result = _run()
    assert result.artifact is not None
    with pytest.raises(CodingCandidateContractError):
        replace(
            result.artifact,
            artifact_bytes=result.artifact.artifact_bytes + b" ",
        )
    artifact = json.loads(result.artifact.artifact_bytes.decode("utf-8"))
    artifact["files"][0]["content"] = base64.b64encode(b"changed").decode("ascii")
    changed = canonical_json_bytes(artifact)
    with pytest.raises(CodingCandidateContractError, match="source manifest"):
        CodingCandidateArtifactV1(
            artifact_bytes=changed,
            artifact_digest=module._sha256_bytes(changed),
            artifact_manifest_digest=result.artifact.artifact_manifest_digest,
            source_manifest_digest=result.artifact.source_manifest_digest,
            solver_source_digest=module._sha256_bytes(b"changed"),
            test_source_digest=result.artifact.test_source_digest,
            byte_count=len(changed),
        )


def test_artifact_requires_exact_types_and_canonical_base64() -> None:
    result = _run()
    assert result.artifact is not None

    class EqualityLiar(str):
        def __eq__(self, other: object) -> bool:
            return True

    with pytest.raises(CodingCandidateContractError, match="canonical sha256"):
        CodingCandidateArtifactV1(
            artifact_bytes=result.artifact.artifact_bytes,
            artifact_digest=EqualityLiar(result.artifact.artifact_digest),
            artifact_manifest_digest=result.artifact.artifact_manifest_digest,
            source_manifest_digest=result.artifact.source_manifest_digest,
            solver_source_digest=result.artifact.solver_source_digest,
            test_source_digest=result.artifact.test_source_digest,
            byte_count=result.artifact.byte_count,
        )

    artifact = json.loads(result.artifact.artifact_bytes.decode("utf-8"))
    content = artifact["files"][1]["content"]
    assert content.endswith("Cg==")
    artifact["files"][1]["content"] = content[:-4] + "Ch=="
    noncanonical = canonical_json_bytes(artifact)
    with pytest.raises(CodingCandidateContractError, match="not canonical"):
        CodingCandidateArtifactV1(
            artifact_bytes=noncanonical,
            artifact_digest=module._sha256_bytes(noncanonical),
            artifact_manifest_digest=result.artifact.artifact_manifest_digest,
            source_manifest_digest=result.artifact.source_manifest_digest,
            solver_source_digest=result.artifact.solver_source_digest,
            test_source_digest=result.artifact.test_source_digest,
            byte_count=len(noncanonical),
        )


def test_source_change_changes_manifest_package_and_receipt_digests() -> None:
    first = _run()
    second = _run(solver=b"def solve(payload):\n    return {'value': payload}\n")
    assert first.artifact is not None and second.artifact is not None
    assert first.artifact.artifact_digest != second.artifact.artifact_digest
    assert first.receipt.source_manifest_digest != second.receipt.source_manifest_digest
    assert first.receipt.receipt_digest != second.receipt.receipt_digest


def test_modules_do_not_wire_candidate_into_runtime_c7_builder_or_storage() -> None:
    builder_source = Path(module.__file__).read_text(encoding="utf-8").lower()
    worker_source = Path(module._WORKER_PATH).read_text(encoding="utf-8").lower()
    for forbidden in (
        "understanding_paired_runner",
        "run_understanding_paired",
        "claude_code_builder",
        "builder_job_queue",
        "auto_promotion",
        "solver_dispatcher",
        "control_plane",
        "understanding_ledger",
        "bootstrap.container",
    ):
        assert forbidden not in builder_source
        assert forbidden not in worker_source
    for forbidden in ("eval(", "exec(", "importlib", "runpy", "marshal", ".pyc"):
        assert forbidden not in worker_source


def test_current_c7_closed_family_preflight_rejects_c8a_package_format() -> None:
    result = _run()
    assert result.artifact is not None
    package = json.loads(result.artifact.artifact_bytes.decode("utf-8"))
    with pytest.raises(PairedRunnerContractError, match="kind must be an exact string"):
        c7_module._validate_artifact_semantics(package, max_collection_items=64)
