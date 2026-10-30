# Legacy script-style test files that should not be collected by pytest.
# These files use custom test runners (OK/FAIL functions) and are run
# via `python tools/waggle_backup.py --tests-only` instead.
#
# All files below have zero pytest-style test functions (no def test_ / class Test).
# Excluding them prevents collection errors from imports that are only
# available in the full development environment (e.g. faiss-cpu).
from pathlib import Path

import pytest

_dir = Path(__file__).parent
collect_ignore = [
    # --- Previously excluded (special cases) ---
    str(_dir / "test_all.py"),
    str(_dir / "test_normalizer.py"),
    # --- Legacy PASS/FAIL runner files (0 pytest functions) ---
    str(_dir / "test_axiom_faiss.py"),
    str(_dir / "test_bee_axioms.py"),
    str(_dir / "test_bee_knowledge_faiss.py"),
    str(_dir / "test_capsule_default_fallback.py"),
    str(_dir / "test_capsule_fi_normalization.py"),
    str(_dir / "test_capsule_routing.py"),
    str(_dir / "test_capsule_word_boundary.py"),
    str(_dir / "test_chat_model_result.py"),
    str(_dir / "test_constraint_engine.py"),
    str(_dir / "test_corrections.py"),
    str(_dir / "test_domain_capsule.py"),
    str(_dir / "test_explainability.py"),
    str(_dir / "test_faiss_api.py"),
    str(_dir / "test_faiss_retrieval.py"),
    str(_dir / "test_faiss_store.py"),
    str(_dir / "test_fi_normalization.py"),
    str(_dir / "test_learning_prompt_apply.py"),
    str(_dir / "test_matched_keywords.py"),
    str(_dir / "test_micro_model_eval_gate.py"),
    str(_dir / "test_model_e2e.py"),
    str(_dir / "test_night_enricher_capabilities.py"),
    str(_dir / "test_phase10.py"),
    str(_dir / "test_phase3.py"),
    str(_dir / "test_phase4.py"),
    str(_dir / "test_phase4ijk.py"),
    str(_dir / "test_phase8.py"),
    str(_dir / "test_phase9.py"),
    str(_dir / "test_pipeline.py"),
    str(_dir / "test_retrieval_layer.py"),
    str(_dir / "test_router_stats.py"),
    str(_dir / "test_routing_centroids.py"),
    str(_dir / "test_seasonal_guard.py"),
    str(_dir / "test_seasonal_routing.py"),
    str(_dir / "test_statistical_layer.py"),
    str(_dir / "test_symbolic_solver.py"),
]


def _load_disk_guard():
    import importlib.util

    spec = importlib.util.spec_from_file_location("wd_disk_guard_conftest", _dir.parent / "tools" / "wd_disk_guard.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pytest_sessionstart(session):
    """LLM-free disk guard: refuse a NEW test run below 3 GiB free, warn below 10 GiB (tools/wd_disk_guard.py)."""
    _load_disk_guard().pytest_session_guard(session)


# --- Test-process isolation of the RCO enrollment pins (D15, PR1960 refusal class "manifest", 2026-10-30) ---------
# The gate tools call tools.bridge_rco_enrollment.load_rco_enrollment_context() with no arguments, which resolves
# WD_BRIDGE_BUNDLE_ROOT and WD_REBOOT_EXPECTED_MANIFEST_HASH from the test process environment and opens that
# generation's deployment-manifest.json. In a session that carries the operator's real pins every such call read the
# installed manifest outside the worktree (2864 D15 refusals in the PR1960 full-suite run); in CI the pins are
# absent and the context is "unknown". The fixtures below give the TEST PROCESS a synthetic, hash-verified generation
# root under pytest's temp root whose manifest has no enrollment section, so the loader answers "not_provisioned",
# the same outcome the real installed manifest yields, without touching anything outside the worktree. A test that
# sets or deletes the pins itself (monkeypatch) still wins, because autouse fixtures run before the test body. The
# real session environment is never changed: monkeypatch restores the values after each test.


@pytest.fixture(scope="session")
def _synthetic_rco_enrollment_generation(tmp_path_factory):
    import hashlib
    import json

    generation = "f" * 40  # a synthetic generation id, never a real commit
    root = tmp_path_factory.mktemp("synthetic-rco-enrollment") / generation
    root.mkdir()
    data = json.dumps({"schema_version": 1, "source_commit": generation, "files": {}}).encode("utf-8")
    (root / "deployment-manifest.json").write_bytes(data)
    return str(root), hashlib.sha256(data).hexdigest()


@pytest.fixture(autouse=True)
def _isolate_rco_enrollment_pins(monkeypatch, _synthetic_rco_enrollment_generation):
    root, digest = _synthetic_rco_enrollment_generation
    monkeypatch.setenv("WD_BRIDGE_BUNDLE_ROOT", root)
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", digest)
