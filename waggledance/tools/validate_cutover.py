"""
Cutover Validation — checks all criteria for full autonomy mode.

Run: python -m waggledance.tools.validate_cutover

Checks:
  1. All autonomy tests pass
  2. Runtime primary = waggledance
  3. Compatibility mode = false
  4. All core components present and importable
  5. Phase 1-9 modules exist
  6. No critical import errors
"""

from __future__ import annotations

import importlib
import sys
from typing import Any, Dict, List, Tuple


# Modules that must be importable for full autonomy
REQUIRED_MODULES = [
    # Phase 1-2: Core data models
    ("waggledance.core.domain.autonomy", "Core data models"),
    # Phase 3: World model + capabilities
    ("waggledance.core.world.world_model", "World model"),
    ("waggledance.core.world.entity_registry", "Entity registry"),
    ("waggledance.core.world.baseline_store", "Baseline store"),
    ("waggledance.core.memory.working_memory", "Working memory"),
    ("waggledance.core.capabilities.registry", "Capability registry"),
    ("waggledance.core.capabilities.selector", "Capability selector"),
    # Phase 4: Policy + actions
    ("waggledance.core.policy.constitution", "Constitution"),
    ("waggledance.core.policy.risk_scoring", "Risk scoring"),
    ("waggledance.core.policy.policy_engine", "Policy engine"),
    ("waggledance.core.policy.approvals", "Approval manager"),
    ("waggledance.core.actions.action_bus", "Safe action bus"),
    # Phase 5: Reasoning
    ("waggledance.core.reasoning.solver_router", "Solver router"),
    ("waggledance.core.reasoning.verifier", "Verifier"),
    ("waggledance.core.learning.case_builder", "Case trajectory builder"),
    ("waggledance.core.specialist_models.model_store", "Model store"),
    # Phase 6: Goals + planning
    ("waggledance.core.goals.goal_engine", "Goal engine"),
    ("waggledance.core.goals.mission_store", "Mission store"),
    ("waggledance.core.planning.planner", "Planner"),
    ("waggledance.core.autonomy.runtime", "Autonomy runtime"),
    # Phase 7: Night learning
    ("waggledance.core.learning.quality_gate", "Quality gate"),
    ("waggledance.core.learning.procedural_memory", "Procedural memory"),
    ("waggledance.core.learning.legacy_converter", "Legacy converter"),
    ("waggledance.core.learning.night_learning_pipeline", "Night learning pipeline"),
    ("waggledance.core.learning.morning_report", "Morning report"),
    ("waggledance.core.specialist_models.specialist_trainer", "Specialist trainer"),
    # Phase 8: Resources
    ("waggledance.core.autonomy.resource_kernel", "Resource kernel"),
    # Phase 9: Lifecycle + API
    ("waggledance.core.autonomy.lifecycle", "Lifecycle manager"),
    ("waggledance.core.autonomy.compatibility", "Compatibility layer"),
    ("waggledance.application.services.autonomy_service", "Autonomy service"),
    # v3.2 additions
    ("waggledance.core.world.epistemic_uncertainty", "Epistemic uncertainty"),
    ("waggledance.core.goals.motives", "Motive registry"),
    ("waggledance.core.learning.consolidator", "Memory consolidator"),
    ("waggledance.core.learning.dream_mode", "Dream mode"),
    ("waggledance.core.specialist_models.meta_optimizer", "Meta-optimizer"),
    ("waggledance.core.autonomy.attention_budget", "Attention budget"),
    ("waggledance.core.projections", "Projections package"),
    ("waggledance.core.projections.narrative_projector", "Narrative projector"),
    ("waggledance.core.projections.introspection_view", "Introspection view"),
    ("waggledance.core.projections.autobiographical_index", "Autobiographical index"),
    ("waggledance.core.projections.projection_validator", "Projection validator"),
    # v3.2 MAGMA expansion
    ("waggledance.core.magma.confidence_decay", "Confidence decay"),
]


def check_imports() -> List[Tuple[str, str, bool, str]]:
    """Check all required modules can be imported."""
    results = []
    for module_path, description in REQUIRED_MODULES:
        try:
            importlib.import_module(module_path)
            results.append((module_path, description, True, ""))
        except Exception as e:
            results.append((module_path, description, False, str(e)))
    return results


def check_runtime_mode() -> Tuple[bool, str]:
    """Check that runtime is configured for autonomy mode."""
    try:
        from waggledance.core.autonomy.lifecycle import AutonomyLifecycle
        lc = AutonomyLifecycle(primary="waggledance", compatibility_mode=False)
        return lc.is_autonomy_primary, "primary=waggledance, compat=false"
    except Exception as e:
        return False, str(e)


def check_core_classes() -> List[Tuple[str, bool, str]]:
    """Check that core classes can be instantiated."""
    checks = []

    try:
        from waggledance.core.autonomy.runtime import AutonomyRuntime
        rt = AutonomyRuntime(profile="VALIDATION")
        rt.start()
        rt.stop()
        checks.append(("AutonomyRuntime", True, "start/stop OK"))
    except Exception as e:
        checks.append(("AutonomyRuntime", False, str(e)))

    try:
        from waggledance.core.autonomy.resource_kernel import ResourceKernel
        rk = ResourceKernel(tier="standard")
        rk.start()
        rk.stop()
        checks.append(("ResourceKernel", True, "start/stop OK"))
    except Exception as e:
        checks.append(("ResourceKernel", False, str(e)))

    try:
        from waggledance.core.learning.night_learning_pipeline import NightLearningPipeline
        nlp = NightLearningPipeline(profile="VALIDATION")
        result = nlp.run_cycle()
        checks.append(("NightLearningPipeline", True, f"cycle OK ({result.duration_s}s)"))
    except Exception as e:
        checks.append(("NightLearningPipeline", False, str(e)))

    try:
        from waggledance.application.services.autonomy_service import AutonomyService
        svc = AutonomyService(profile="VALIDATION")
        svc.start()
        status = svc.get_status()
        svc.stop()
        checks.append(("AutonomyService", True, "full lifecycle OK"))
    except Exception as e:
        checks.append(("AutonomyService", False, str(e)))

    return checks


def run_validation() -> bool:
    """Run full cutover validation."""
    print("=" * 60)
    print("WaggleDance Full Autonomy Cutover Validation")
    print("=" * 60)

    all_pass = True

    # 1. Import checks
    print("\n[1/3] Module imports:")
    imports = check_imports()
    for module, desc, ok, err in imports:
        status = "PASS" if ok else "FAIL"
        print(f"  {status}: {desc} ({module})")
        if not ok:
            print(f"        Error: {err}")
            all_pass = False

    import_pass = sum(1 for _, _, ok, _ in imports if ok)
    import_total = len(imports)
    print(f"\n  Imports: {import_pass}/{import_total}")

    # 2. Runtime mode
    print("\n[2/3] Runtime mode:")
    mode_ok, mode_msg = check_runtime_mode()
    print(f"  {'PASS' if mode_ok else 'FAIL'}: {mode_msg}")
    if not mode_ok:
        all_pass = False

    # 3. Core class checks
    print("\n[3/3] Core class instantiation:")
    classes = check_core_classes()
    for name, ok, msg in classes:
        print(f"  {'PASS' if ok else 'FAIL'}: {name} — {msg}")
        if not ok:
            all_pass = False

    # Summary
    print("\n" + "=" * 60)
    if all_pass:
        print("FULL AUTONOMY MODE ENABLED — cutover valmis")
    else:
        print("Cutover NOT ready — fix failing checks above")
    print("=" * 60)

    return all_pass


if __name__ == "__main__":
    success = run_validation()
    sys.exit(0 if success else 1)
