# SPDX-License-Identifier: Apache-2.0
"""Targeted tests for Phase 9 §U2 Claude Code Builder/Mentor Lane + Repair Forge."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.builder_lane import (
    ARTIFACT_KINDS,
    OUTCOME_STATES,
    TASK_KINDS,
    builder_lane_router as blr,
    builder_request_pack as brp,
    builder_result_pack as brres,
    mentor_forge as mf,
    repair_forge as rf,
    session_forge as sf,
    worktree_allocator as wa,
)


# ═══════════════════ schema enums match constants ══════════════════

def test_task_kinds_match_request_schema():
    schema = json.loads((ROOT / "schemas" / "builder_request.schema.json")
                          .read_text(encoding="utf-8"))
    assert tuple(schema["properties"]["task_kind"]["enum"]) == TASK_KINDS


def test_outcome_states_match_result_schema():
    schema = json.loads((ROOT / "schemas" / "builder_result.schema.json")
                          .read_text(encoding="utf-8"))
    assert tuple(schema["properties"]["outcome"]["enum"]) == OUTCOME_STATES


def test_artifact_kinds_match_result_schema():
    schema = json.loads((ROOT / "schemas" / "builder_result.schema.json")
                          .read_text(encoding="utf-8"))
    enum = tuple(schema["properties"]["artifacts"]["items"]
                    ["properties"]["artifact_kind"]["enum"])
    assert enum == ARTIFACT_KINDS


def test_no_main_branch_auto_merge_const_in_request_schema():
    schema = json.loads((ROOT / "schemas" / "builder_request.schema.json")
                          .read_text(encoding="utf-8"))
    assert schema["properties"]["no_main_branch_auto_merge"]["const"] is True


def test_no_main_branch_auto_merge_const_in_result_schema():
    schema = json.loads((ROOT / "schemas" / "builder_result.schema.json")
                          .read_text(encoding="utf-8"))
    assert schema["properties"]["no_main_branch_auto_merge"]["const"] is True


# ═══════════════════ worktree_allocator ════════════════════════════

def test_derive_worktree_path_deterministic():
    a = wa.derive_worktree_path(request_id="abc123def456",
                                     root="/tmp")
    b = wa.derive_worktree_path(request_id="abc123def456",
                                     root="/tmp")
    assert a == b


def test_derive_branch_name_format():
    name = wa.derive_branch_name(request_id="abc123def456")
    assert name.startswith("phase9-builder/")


def test_allocate_returns_full_record():
    alloc = wa.allocate(request_id="abc123def456", root="/tmp")
    assert alloc.request_id == "abc123def456"
    assert alloc.branch_name.startswith("phase9-builder/")
    assert "abc123def456" in alloc.base_path.as_posix()


def test_invocation_log_append_and_read(tmp_path):
    log_path = tmp_path / "log.jsonl"
    e = wa.InvocationLogEntry(
        request_id="abc123def456", ts_iso="t",
        isolated_worktree_path="/tmp/x",
        isolated_branch_name="phase9-builder/abc123de",
        outcome="advisory_only", rationale="r",
    )
    wa.append_invocation(log_path, e)
    wa.append_invocation(log_path, e)
    entries = wa.read_invocations(log_path)
    assert len(entries) == 2
    assert entries[0].request_id == "abc123def456"


def test_invocation_log_skips_malformed(tmp_path):
    log_path = tmp_path / "log.jsonl"
    e = wa.InvocationLogEntry(
        request_id="abc123def456", ts_iso="t",
        isolated_worktree_path="/tmp/x",
        isolated_branch_name="phase9-builder/abc123de",
        outcome="advisory_only", rationale="r",
    )
    wa.append_invocation(log_path, e)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("{ malformed\n")
        f.write('{"missing": "fields"}\n')
    entries = wa.read_invocations(log_path)
    assert len(entries) == 1


def test_invocation_log_missing_returns_empty(tmp_path):
    assert wa.read_invocations(tmp_path / "nope.jsonl") == []


# ═══════════════════ builder_request_pack ═════════════════════════

def test_make_request_const_invariants():
    r = brp.make_request(
        task_kind="generate_solver_candidate",
        intent="generate a thermal solver candidate",
        isolated_worktree_path="/tmp/x",
        isolated_branch_name="phase9-builder/abc123de",
    )
    assert r.no_runtime_mutation is True
    assert r.no_main_branch_auto_merge is True


def test_make_request_rejects_unknown_task_kind():
    with pytest.raises(ValueError, match="unknown task_kind"):
        brp.make_request(
            task_kind="bogus_kind", intent="x",
            isolated_worktree_path="/tmp/x",
            isolated_branch_name="phase9-builder/x",
        )


def test_make_request_rejects_invalid_budgets():
    with pytest.raises(ValueError, match="max_invocations"):
        brp.make_request(
            task_kind="generate_test", intent="x",
            isolated_worktree_path="/tmp/x",
            isolated_branch_name="phase9-builder/x",
            max_invocations=0,
        )
    with pytest.raises(ValueError, match="max_wall_seconds"):
        brp.make_request(
            task_kind="generate_test", intent="x",
            isolated_worktree_path="/tmp/x",
            isolated_branch_name="phase9-builder/x",
            max_wall_seconds=0,
        )


def test_request_id_deterministic():
    a = brp.make_request(
        task_kind="generate_test", intent="add test for X",
        isolated_worktree_path="/tmp/a",
        isolated_branch_name="phase9-builder/a",
    )
    b = brp.make_request(
        task_kind="generate_test", intent="add test for X",
        isolated_worktree_path="/tmp/b",   # different path
        isolated_branch_name="phase9-builder/b",
    )
    # Path/branch don't enter id; identical task+intent+capsule → same id
    assert a.request_id == b.request_id


def test_request_to_dict_has_all_required_fields():
    r = brp.make_request(
        task_kind="repair_adapter",
        intent="repair x adapter",
        isolated_worktree_path="/tmp/x",
        isolated_branch_name="phase9-builder/x",
    )
    d = r.to_dict()
    for k in ("schema_version", "request_id", "task_kind", "intent",
              "isolated_worktree_path", "isolated_branch_name",
              "no_runtime_mutation", "no_main_branch_auto_merge",
              "provenance"):
        assert k in d


# ═══════════════════ builder_result_pack ══════════════════════════

def test_make_result_const_no_main_auto_merge():
    r = brres.make_result(
        request_id="abc123def456", outcome="success",
        isolated_branch_name="phase9-builder/abc123de",
    )
    assert r.no_main_branch_auto_merge is True


def test_result_rejects_unknown_outcome():
    with pytest.raises(ValueError, match="unknown outcome"):
        brres.BuilderResult(
            schema_version=1, result_id="x", request_id="r",
            outcome="bogus_outcome", artifacts=(),
            isolated_branch_name="b", isolated_worktree_path="p",
            tests_passed=0, tests_failed=0, tests_skipped=0,
            no_main_branch_auto_merge=True,
            human_review_required=True, ts_iso="t",
        )


def test_artifact_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown artifact_kind"):
        brres.BuilderArtifact(
            artifact_kind="bogus", relative_path="x",
            sha256_full="sha256:x" * 1,
        )


def test_result_to_dict_shape():
    art = brres.BuilderArtifact(
        artifact_kind="solver_candidate",
        relative_path="solvers/x.yaml",
        sha256_full="sha256:" + "a" * 64,
    )
    r = brres.make_result(
        request_id="abc123def456", outcome="success",
        artifacts=(art,),
        isolated_branch_name="phase9-builder/abc123de",
        isolated_worktree_path="/tmp/x",
        tests_passed=3, tests_failed=0, tests_skipped=1,
        ts_iso="t",
    )
    d = r.to_dict()
    assert d["tests_run"]["passed"] == 3
    assert d["artifacts"][0]["artifact_kind"] == "solver_candidate"
    assert d["no_main_branch_auto_merge"] is True


# ═══════════════════ builder_lane_router ══════════════════════════

class _StubAgent:
    def __init__(self, agent_id, provider_type="claude_code_builder_lane",
                  preferred_capsules=(), specialization_tags=(),
                  warm_availability=True):
        self.agent_id = agent_id
        self.provider_type = provider_type
        self.preferred_capsules = preferred_capsules
        self.specialization_tags = specialization_tags
        self.warm_availability = warm_availability


class _StubPool:
    def __init__(self, agents):
        self._agents = {a.agent_id: a for a in agents}

    def get(self, agent_id):
        return self._agents.get(agent_id)

    def for_capsule(self, capsule_context):
        return [a for a in self._agents.values()
                 if capsule_context in a.preferred_capsules]

    def for_specialization(self, tag):
        return [a for a in self._agents.values()
                 if tag in a.specialization_tags]


def _req(**kw):
    base = dict(
        task_kind="generate_solver_candidate",
        intent="generate solver",
        isolated_worktree_path="/tmp/x",
        isolated_branch_name="phase9-builder/x",
    )
    base.update(kw)
    return brp.make_request(**base)


def test_router_falls_back_to_default_claude_when_pool_empty():
    decision = blr.route(_req())
    assert decision.chosen_provider_type == "claude_code_builder_lane"
    assert decision.chosen_agent_id is None


def test_router_honors_agent_id_hint():
    pool = _StubPool([_StubAgent("opus_factory")])
    decision = blr.route(_req(agent_id_hint="opus_factory"),
                              agent_pool=pool)
    assert decision.chosen_agent_id == "opus_factory"


def test_router_capsule_affinity():
    pool = _StubPool([
        _StubAgent("opus_factory", preferred_capsules=("factory_v1",)),
        _StubAgent("opus_personal", preferred_capsules=("personal_v1",)),
    ])
    decision = blr.route(_req(capsule_context="factory_v1"),
                              agent_pool=pool)
    assert decision.chosen_agent_id == "opus_factory"


def test_router_specialization_match():
    pool = _StubPool([
        _StubAgent("repair_specialist",
                    specialization_tags=("generate_solver_candidate",
                                          "repair_adapter")),
    ])
    decision = blr.route(_req(), agent_pool=pool)
    assert decision.chosen_agent_id == "repair_specialist"


# ═══════════════════ session_forge plan ════════════════════════════

def test_session_forge_plan_pure(tmp_path):
    """plan() must NOT call git or any subprocess."""
    request = _req()
    plan = sf.plan(
        request=request,
        worktree_root=tmp_path,
        invocation_log_path=tmp_path / "log.jsonl",
    )
    # Worktree path NOT actually created (pure plan)
    assert not plan.allocation.base_path.exists()
    assert plan.routing.chosen_provider_type == "claude_code_builder_lane"


def test_session_forge_plan_to_dict():
    request = _req()
    plan = sf.plan(
        request=request, worktree_root="/tmp",
        invocation_log_path="/tmp/log.jsonl",
    )
    d = plan.to_dict()
    assert "request" in d and "allocation" in d and "routing" in d


# ═══════════════════ repair_forge ═════════════════════════════════-

def test_make_repair_request_emits_repair_intent():
    ctx = rf.RepairContext(
        affected_file="waggledance/core/x.py",
        defect_kind="adapter_drift",
        failing_test_paths=("tests/test_x.py::test_a",),
    )
    r = rf.make_repair_request(
        context=ctx, task_kind="repair_adapter",
        isolated_worktree_path="/tmp/r",
        isolated_branch_name="phase9-builder/r",
    )
    assert r.task_kind == "repair_adapter"
    assert "adapter_drift" in r.intent
    assert r.input_payload["affected_file"] == "waggledance/core/x.py"


def test_repair_forge_rejects_non_repair_task():
    ctx = rf.RepairContext(affected_file="x", defect_kind="y")
    with pytest.raises(ValueError, match="not a repair task"):
        rf.make_repair_request(
            context=ctx, task_kind="generate_test",
            isolated_worktree_path="/tmp/x",
            isolated_branch_name="phase9-builder/x",
        )


# ═══════════════════ mentor_forge ═════════════════════════════════-

def test_make_mentor_request_emits_advisory_intent():
    p = mf.MentorPrompt(topic="solver_family bridging",
                              why_relevant="recent dream meta-proposals")
    r = mf.make_mentor_request(
        prompt=p,
        isolated_worktree_path="/tmp/m",
        isolated_branch_name="phase9-builder/m",
    )
    assert r.task_kind == "mentor_note"
    assert "advisory only" in r.intent.lower()


def test_mentor_note_to_ir_payload_is_advisory_only():
    payload = mf.mentor_note_to_ir_payload(
        topic="topology_subdivision",
        content="consider splitting cells with persistent drift",
    )
    assert payload["ir_type"] == "learning_suggestion"
    assert payload["lifecycle_status"] == "advisory"
    assert payload["payload"]["is_advisory_only"] is True


# ═══════════════════ no auto-merge / no runtime mutation ─────────-

def test_no_main_branch_auto_merge_in_source():
    """Critical contract: NO source file in builder_lane may
    auto-merge to main. We grep for forbidden patterns."""
    pkg = ROOT / "waggledance" / "core" / "builder_lane"
    forbidden = ("git merge", "git push origin main",
                  "git push --force", "git push -f",
                  "promote_to_main(", "auto_merge_to_main(",
                  "merge_to_main(",
                  "git rebase --onto main",
                  "axiom_write(", "promote_to_runtime(",
                  "register_solver_in_runtime(")
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            assert pat not in text, f"{p.name}: {pat}"


def test_no_main_branch_auto_merge_const_invariant_in_source():
    """no_main_branch_auto_merge must NEVER appear as False in source."""
    pkg = ROOT / "waggledance" / "core" / "builder_lane"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert "no_main_branch_auto_merge=False" not in text
        assert "no_main_branch_auto_merge = False" not in text


def test_subprocess_not_invoked_at_module_load():
    """Importing the builder_lane modules MUST NOT spawn subprocesses
    or call Claude Code at import time. The §U2 SUBPROCESS EXCEPTION
    requires explicit operator-gated invocation."""
    pkg = ROOT / "waggledance" / "core" / "builder_lane"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        # subprocess.run/Popen at module top-level would be spawned
        # at import; allow only inside functions.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("subprocess.") or \
                stripped.startswith("os.system("):
                # If it's inside a function (indented), allow with
                # warning; at top-level forbid.
                if not line.startswith((" ", "\t")):
                    pytest.fail(
                        f"{p.name}: top-level subprocess call: {stripped}"
                    )


# ═══════════════════ CLI ═══════════════════════════════════════════

def test_cli_help():
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "run_claude_builder_lane.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    for flag in ("--task-kind", "--intent", "--capsule-context",
                  "--apply", "--json"):
        assert flag in r.stdout


def test_cli_dry_run():
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "run_claude_builder_lane.py"),
         "--task-kind", "generate_solver_candidate",
         "--intent", "generate solver for x",
         "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["dry_run"] is True
    assert out["no_main_branch_auto_merge"] is True
    assert out["no_runtime_mutation"] is True


def test_cli_apply_emits_advisory_only_outcome(tmp_path):
    log_path = tmp_path / "log.jsonl"
    r = subprocess.run(
        [sys.executable,
         str(ROOT / "tools" / "run_claude_builder_lane.py"),
         "--task-kind", "mentor_note",
         "--intent", "mentor note about topology subdivision",
         "--invocation-log", str(log_path),
         "--worktree-root", str(tmp_path),
         "--apply", "--json"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["dry_run"] is False
    assert "request_pack_path" in out
    assert "result_pack_path" in out
    # The result was advisory-only — never claims runtime mutation
    result = json.loads(Path(out["result_pack_path"]).read_text(encoding="utf-8"))
    assert result["outcome"] == "advisory_only"
    assert result["no_main_branch_auto_merge"] is True
    assert result["human_review_required"] is True


# ═══════════════════ source safety ═════════════════════════════════

def test_phase_u2_source_safety():
    forbidden = ["import faiss", "from waggledance.runtime",
                  "ollama.generate(", "openai.chat",
                  "anthropic.messages.create",
                  "anthropic.Anthropic(",
                  "requests.post(", "axiom_write(",
                  "promote_to_runtime("]
    pkg = ROOT / "waggledance" / "core" / "builder_lane"
    for p in pkg.glob("*.py"):
        text = p.read_text(encoding="utf-8")
        for pat in forbidden:
            assert pat not in text, f"{p.name}: {pat}"
