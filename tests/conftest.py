# Legacy script-style test files that should not be collected by pytest.
# These files use custom test runners (OK/FAIL functions) and are run
# via `python tools/waggle_backup.py --tests-only` instead.
#
# All files below have zero pytest-style test functions (no def test_ / class Test).
# Excluding them prevents collection errors from imports that are only
# available in the full development environment (e.g. faiss-cpu).
from pathlib import Path

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
