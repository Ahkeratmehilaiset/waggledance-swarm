# SPDX-License-Identifier: BUSL-1.1
"""Locked contract tests for the shared leak-policy module.

This test forges every required model/secret/path form and asserts rejection.
It also locks the acceptance of legit values (language_detection, normal branch
names, explicitly allowlisted repo-relative source paths ONLY in their named
metadata fields) and false-positive guards (yield, command_center and similar
must not over-trigger the model token pattern).

The module + this test are the single source of truth so that future benchmark
contracts (and any manifest tooling) can import a stable implementation instead
of re-implementing drifting per-file LEAK_PATTERNS + _looks_like_leak.

All claim gates N/A (pure utility). Any consuming artifact must set:
claim_gate_satisfied=false, claim_safe=false, literal_future_claim_safe=false,
controls_present=false, runtime_authority_granted=false,
external_writes_applied=false, required_runtime_evidence_present=false.

Deterministic, offline only. No network. No raw secrets or concrete user fs
paths in this file beyond the minimal regex-exercising shapes required by the
spec (stable aliases / shapes preferred).
"""
from __future__ import annotations

import re

import pytest

from waggledance.core.leak_policy import (
    CLAIM_GATES,
    LEAK_PATTERNS,
    MODEL_PROVIDER_TOKEN_PATTERN,
    REPO_RELATIVE_PATH_PATTERN,
    looks_like_leak,
    looks_like_leak_simple,
)

# Stable shapes for allowed metadata (repo-relative source files).
# These are the only values that may survive in the named fields.
_ALLOWED_SOURCE_SHAPES: frozenset[str] = frozenset(
    (
        "waggledance/adapters/http/routes/chat.py",
        "waggledance/application/dto/chat_dto.py",
        "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
    )
)


def test_exports_are_stable():
    """Lock the public surface shape for importers."""
    assert isinstance(MODEL_PROVIDER_TOKEN_PATTERN, str)
    assert len(MODEL_PROVIDER_TOKEN_PATTERN) > 50
    assert REPO_RELATIVE_PATH_PATTERN.pattern.startswith("^")
    assert len(LEAK_PATTERNS) >= 8
    assert "claim_gate_satisfied" in CLAIM_GATES
    assert "required_runtime_evidence_present" in CLAIM_GATES


@pytest.mark.parametrize(
    "model_form",
    [
        # bare providers (must be rejected in alias/seed/repro fields)
        "anthropic",
        "claude",
        "cohere",
        "command",
        "deepseek",
        "falcon",
        "gemini",
        "gemma",
        "google",
        "gpt",
        "grok",
        "hf",
        "huggingface",
        "llama",
        "mistral",
        "mixtral",
        "mpt",
        "ollama",
        "openai",
        "phi",
        "poro",
        "qwen",
        "xai",
        "yi",
        # command-r (multi-token form)
        "command-r",
        "command-r-plus",
        # digit-glued and underscore-glued forms per spec
        "gpt4o",
        "gpt4o_hit",
        "mpt7b",
        "llama3",
        "llama3_1",
        "gemma2",
        "grok1",
        "claude3",
        "phi3",
        "qwen2",
        # with separators common in refs
        "cohere_internal_model",
        "claude_secret_x",
        "grok_internal",
        "gemini.internal",
        "openai:gpt-4o",
        "mistral/instruct",
        "claude-3-sonnet",
        "llama-2-7b",
        "mistral-7b-instruct",
        "hf/meta-llama/Llama-2-7b-chat-hf",
        "openai/gpt-4o",
        "xai/grok-beta",
    ],
)
def test_model_provider_token_forms_are_rejected(model_form: str):
    """Every listed provider/model token form (and glued variants) must be rejected."""
    assert looks_like_leak("$.stage_aliases_used", model_form, frozenset()) is True
    assert looks_like_leak("$.synthetic_fixtures_alias", model_form, frozenset()) is True
    assert looks_like_leak_simple(model_form) is True
    # also via direct pattern (case-insensitive)
    pat = re.compile(MODEL_PROVIDER_TOKEN_PATTERN, re.IGNORECASE)
    assert pat.search(model_form), f"pattern failed to match {model_form}"


@pytest.mark.parametrize(
    "secret_form",
    [
        "Bearer abcdefghij1234567890",
        "bearer abcdefghij1234567890",
        "Bearer sk-abc123def456",
        "sk-1234567890abcdef1234567890abcdef",
        "sk-ant-1234567890abcdef1234567890abcdef",
        "AKIA1234567890ABCDEF",
        "AKIA1234567890ABCDEFEXTRA",
    ],
)
def test_secret_forms_are_rejected(secret_form: str):
    assert looks_like_leak("$.reproduce_command", secret_form, frozenset()) is True
    assert looks_like_leak("$.deterministic_seed", secret_form, frozenset()) is True
    assert looks_like_leak_simple(secret_form) is True


@pytest.mark.parametrize(
    "path_form",
    [
        # drive paths (various shapes)
        r"C:\Users\shape\model.bin",
        r"D:\tmp\synth.json",
        "C:tmp",
        r"C:\Program Files\w\foo",
        # windows share
        r"\\wsl\local\path",
        r"\\share\secret",
        # unix roots
        "/home/user/secret",
        "/root/.cache",
        "/etc/passwd",
        "/var/log/app",
        "/opt/models/x",
        "/mnt/data/input",
        "/tmp/synth_adversarial.json",
        # traversal
        "../escape",
        "../../parent",
        "foo/../bar",
        # hf:// and org/model:tag
        "hf://meta-llama/Llama-2",
        "org/model:tag",
        "myorg/mymodel:v1.2.3",
        "some-model/revision:deadbeef",
    ],
)
def test_path_forms_are_rejected(path_form: str):
    assert looks_like_leak("$.reproduce_command", path_form, frozenset()) is True
    assert looks_like_leak("$.source_branch", path_form, frozenset()) is True
    assert looks_like_leak_simple(path_form) is True


def test_repo_relative_paths_only_accepted_in_named_fields_when_allowlisted():
    """The path-aware gate: repo paths pass ONLY for allowlisted value + correct field."""
    good = "waggledance/adapters/http/routes/chat.py"
    assert REPO_RELATIVE_PATH_PATTERN.match(good)

    # correct named fields + in allowlist -> accepted (False = not a leak)
    assert (
        looks_like_leak("$.axis_definition_source", good, _ALLOWED_SOURCE_SHAPES) is False
    )
    assert looks_like_leak("$.source_paths[0]", good, _ALLOWED_SOURCE_SHAPES) is False
    assert looks_like_leak("$.source_paths[2]", good, _ALLOWED_SOURCE_SHAPES) is False

    # same value but wrong field -> rejected (no fail-open)
    assert looks_like_leak("$.some_other_field", good, _ALLOWED_SOURCE_SHAPES) is True
    assert looks_like_leak("$.not_source", good, _ALLOWED_SOURCE_SHAPES) is True

    # correct field but value NOT in the passed allowlist -> rejected
    unknown_good_path = "waggledance/core/leak_policy.py"  # would be valid shape but not passed
    assert REPO_RELATIVE_PATH_PATTERN.match(unknown_good_path)
    assert looks_like_leak("$.axis_definition_source", unknown_good_path, _ALLOWED_SOURCE_SHAPES) is True

    # even if caller passes a superset, only the ones they explicitly list for *this* artifact are ok
    larger = _ALLOWED_SOURCE_SHAPES | {"waggledance/core/leak_policy.py"}
    assert looks_like_leak("$.axis_definition_source", unknown_good_path, larger) is False
    assert looks_like_leak("$.axis_definition_source", good, larger) is False


@pytest.mark.parametrize(
    "legit_value",
    [
        "language_detection",
        "hot_cache",
        "deterministic_solver",
        "hybrid_retrieval_8_cell",
        "feature/normal-branch",
        "latency-slice-1",
        "latency-bench-20260603-seed-a1b2c3",
        "v3.latency_fixtures.local.v1",
        "No claim that measured latency predicts production performance at scale.",
        "python tools/run_foo.py --fixtures v3.bar --offline --deterministic",
        "yield",
        "yield_route_case",
        "command_center",
        "command_center_v2",
        "my_yield_helper",
        "precommand_post",
        "some_gpt_helper_but_not_a_model_ref",
        "xai_is_the_company_not_a_leak_here",
    ],
)
def test_legit_values_and_false_positive_guards_are_accepted(legit_value: str):
    """Legit content and known FP guard cases must not be flagged as leaks."""
    # in ordinary fields
    assert looks_like_leak("$.stage_aliases_used", legit_value, frozenset()) is False
    assert looks_like_leak("$.source_branch", legit_value, frozenset()) is False
    assert looks_like_leak_simple(legit_value) is False

    # also when an allowlist is supplied (should not affect non-path legit)
    assert looks_like_leak("$.axis_definition_source", legit_value, _ALLOWED_SOURCE_SHAPES) is False


def test_model_token_pattern_does_not_over_trigger_on_fp_guards():
    """Explicit guard: the tuned pattern must not match command_center / yield etc."""
    pat = re.compile(MODEL_PROVIDER_TOKEN_PATTERN, re.IGNORECASE)
    for fp in (
        "yield",
        "yield_route_case",
        "hybrid_retrieval_8_cell",
        "deterministic_solver",
        "command_center",
        "command_center_v2",
        "mycommandcenter",
        "xai_helper",
        "xai_is_the_company_not_a_leak_here",
    ):
        assert not pat.search(fp), f"pattern over-triggered on false-positive guard {fp}"
    # but the real forms still do
    for real in ("command-r", "command", "xai", "gpt4o_hit", "cohere_internal_model"):
        assert pat.search(real), f"pattern missed real form {real}"


@pytest.mark.parametrize(
    "gate",
    list(CLAIM_GATES),
)
def test_claim_gates_are_listed_for_contract_use(gate: str):
    """The list is provided so contracts can assert literal-false without duplication."""
    assert gate in CLAIM_GATES
    # This module itself emits no artifacts with gates; consumers must set false.
    # (No test here sets any gate to True.)


def test_non_string_values_never_leak_via_this_checker():
    assert looks_like_leak("$.num", 42, frozenset()) is False
    assert looks_like_leak("$.bool", True, frozenset()) is False
    assert looks_like_leak("$.none", None, frozenset()) is False
    assert looks_like_leak("$.list", ["gpt"], frozenset()) is False  # caller walks


def test_walk_style_defense_in_depth_still_works_via_simple():
    """Reproduce the recursive scalar walk pattern used in contracts using the shared simple checker."""
    artifact_like = {
        "stage_aliases_used": ["language_detection", "gpt4o"],  # bad inside
        "repro": "python --input /tmp/x.json",
        "ok": "feature/foo",
    }

    def _walk_scalars(v: Any, p: str = "$") -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        if isinstance(v, dict):
            for k, c in v.items():
                out.extend(_walk_scalars(c, f"{p}.{k}"))
        elif isinstance(v, list):
            for i, c in enumerate(v):
                out.extend(_walk_scalars(c, f"{p}[{i}]"))
        else:
            out.append((p, v))
        return out

    leaks = [
        (p, v)
        for p, v in _walk_scalars(artifact_like)
        if isinstance(v, str) and looks_like_leak_simple(v)
    ]
    assert any("gpt4o" in v for _, v in leaks)
    assert any("/tmp" in v for _, v in leaks)
    assert not any("language_detection" in v for _, v in leaks)
    assert not any("feature/foo" in v for _, v in leaks)
