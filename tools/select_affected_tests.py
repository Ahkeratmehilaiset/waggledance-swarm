#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Select the AFFECTED test files for a set of changed files (fast local iteration).

RFC: WD bridge throughput & resilience, item P2/D6 (stop re-running the full
~795-test suite locally on every head). This maps changed source/test files to
the test files that exercise them, so an agent can run ONLY the affected tests
locally during iteration instead of the whole suite.

CRITICAL CONTRACT — product-wide CI remains the authoritative full-suite gate.
An explicitly authorized Bridge-only gate may use the narrower Bridge boundary
below only when every changed source is mapped to existing, readable tests and
no product source/configuration is mixed into the change. This selector
is best-effort and **FAIL-SAFE**: whenever the affected set is uncertain (a
broad-impact file changed, a changed source file maps to no test, an unknown
file type, or empty input) it returns ``full_suite=True`` so nothing is ever
silently under-run. It only ever NARROWS when the mapping is unambiguous.

LIMITATION — generic import-grep detects only DIRECT imports (and the ``test_<stem>`` name
convention); it does NOT detect transitive/indirect imports (``importlib`` /
``__import__`` / re-exports / conftest-fixture-mediated coverage), so a
transitively-affected test may not be selected for a LOCAL run. This is safe ONLY
because product CI stays the authoritative full suite. The Bridge-only branch
uses explicit mappings plus direct and transitive script/fixture references;
uncertain Bridge coverage returns ``full_suite=True``.

GUARDRAIL — this selector must NEVER replace product-wide full-suite CI for
WD/product changes. Bridge-only CI requires an explicit operator-approved
separate boundary; unknown Bridge files and mixed WD changes return full_suite.
For generic changes it may narrow only LOCAL iteration.

Read-only: it inspects the repo tree and runs no tests and mutates nothing.

Usage:
    python tools/select_affected_tests.py --files waggledance/x/y.py tests/test_z.py --json
    python tools/select_affected_tests.py --changed-from-git origin/main --json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence


# Changing any of these can affect arbitrary tests → fail-safe to the full suite.
BROAD_IMPACT_BASENAMES = frozenset(
    {
        "conftest.py",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "tox.ini",
        "pytest.ini",
        "__init__.py",
    }
)
BROAD_IMPACT_SUBSTRINGS = ("requirements",)
# Gate-critical / charter source: a change here can alter merge behavior broadly.
BROAD_IMPACT_PATHS = frozenset(
    {"waggledance/core/idle_consensus_charter.py"}
)
SOURCE_PREFIXES = ("waggledance/", "tools/")
TEST_PREFIXES = ("tests/",)
_IMPORT_RE_CACHE: dict[str, re.Pattern[str]] = {}

# Sprint proof mappings for files loaded by importlib or exercised indirectly by
# an offline proof script. These are intentionally narrow: missing mapped tests
# fail-safe to the full suite below.
EXPLICIT_AFFECTED_TESTS: dict[str, frozenset[str]] = {
    "tools/run_ring_messaging_hierarchy_proof.py": frozenset(
        {"tests/test_ring_messaging_hierarchy_proof.py"}
    ),
    "tools/run_low_risk_autogrowth_real_loop_proof.py": frozenset(
        {"tests/test_low_risk_autogrowth_real_loop_proof.py"}
    ),
    "tools/run_chat_first_hop_corpus.py": frozenset(
        {"tests/tools/test_chat_first_hop_corpus.py"}
    ),
    "waggledance/core/hex_topology/parent_child_relations.py": frozenset(
        {"tests/test_ring_messaging_hierarchy_proof.py"}
    ),
    "waggledance/core/hex_topology/subdivision_operator.py": frozenset(
        {"tests/test_hex_subdivision_plan.py"}
    ),
    "waggledance/core/v3_13_0/chat_dispatch.py": frozenset(
        {"tests/unit_app/test_chat_v313_solver_dispatch.py"}
    ),
}

# Reviewed against exact PR #1755 head 57d4b894 and native-wake commit
# e7488916. Each entry names its concrete Bridge/launch/package consumers;
# only these paths may narrow a Bridge-only change. New or unmapped files fail
# closed until their consumers are reviewed and added here.
BRIDGE_EXPLICIT_TESTS: dict[str, frozenset[str]] = {
    "docs/BRIDGE_TEST_BOUNDARY.md": frozenset({
        "tests/tools/test_select_affected_tests.py",
    }),
    ".agent-bridge/bin/BridgeEventClassifier.ps1": frozenset("""
        tests/tools/test_bridge_control_routing.py
        tests/tools/test_bridge_event_classifier_wake_request.py
        tests/tools/test_bridge_interim_reply_parity.py
        tests/tools/test_bridge_session_watcher_probe.py
        tests/tools/test_bridge_wake_continuity.py
        tests/tools/test_bridge_inbox_recovery.py
        tests/tools/test_bridge_task_result.py
        tests/tools/test_session_liveness_supervisor_report.py
        tests/tools/test_wd_continuity_alert.py
        tests/tools/test_wd_event_driven_wake.py
        tests/tools/test_wd_reboot_bundle.py
        tests/tools/test_wd_swarm_parallel_status.py
    """.split()),
    ".agent-bridge/bin/Invoke-StaleClaimSweep.ps1": frozenset({
        "tests/tools/test_bridge_stale_routing.py",
    }),
    ".agent-bridge/bin/Get-BridgeRequestInventory.ps1": frozenset({
        "tests/tools/test_bridge_request_inventory.py",
    }),
    "docs/adr/ADR-continuity-recovery-20260929.md": frozenset({
        "tests/tools/test_bridge_wake_continuity.py",
        "tests/tools/test_wd_continuity_controls.py",
        "tests/tools/test_wd_lead_continuity_imports.py",
        "tests/tools/test_wd_native_tools_wake.py",
    }),
    "ops/windows/reboot/Get-WdSwarmParallelStatus.ps1": frozenset({
        "tests/tools/test_wd_continuity_status.py",
        "tests/tools/test_wd_reboot_bundle.py",
        "tests/tools/test_wd_swarm_parallel_status.py",
    }),
    "ops/windows/reboot/Send-WdContinuityAlert.ps1": frozenset({
        "tests/tools/test_wd_continuity_alert.py",
        "tests/tools/test_wd_native_tools_wake.py",
    }),
    "ops/windows/reboot/bridge-code-files.json": frozenset("""
        tests/tools/test_bridge_code_package_closure.py
        tests/tools/test_lane_profile_launch_probe.py
        tests/tools/test_wd_bridge_code_context.py
        tests/tools/test_wd_grok_helper.py
        tests/tools/test_wd_native_tools_wake.py
    """.split()),
    "ops/windows/reboot/start-wd-agent.ps1": frozenset("""
        tests/tools/test_bridge_task_result.py
        tests/tools/test_lane_launch_preflight.py
        tests/tools/test_lane_profile_launch_probe.py
        tests/tools/test_wd_bridge_code_context.py
        tests/tools/test_wd_capacity_observer.py
        tests/tools/test_wd_conversation_recovery.py
        tests/tools/test_wd_conversation_resume.py
        tests/tools/test_wd_dynamic_model_startup.py
        tests/tools/test_wd_event_driven_wake.py
        tests/tools/test_wd_grok_helper.py
        tests/tools/test_wd_lane_context_window.py
        tests/tools/test_wd_launcher_claude_marker_scrub.py
        tests/tools/test_wd_lead_continuity_imports.py
        tests/tools/test_wd_lead_reply_delivery.py
        tests/tools/test_wd_native_lead.py
        tests/tools/test_wd_native_tools_wake.py
        tests/tools/test_wd_reboot_bundle.py
        tests/tools/test_wd_startup_recovery.py
        tests/tools/test_wd_startup_repair.py
        tests/tools/test_wd_swarm_parallel_status.py
    """.split()),
    "ops/windows/reboot/start-wd-tools-consumer.ps1": frozenset("""
        tests/tools/test_bridge_final_acceptance_20260927.py
        tests/tools/test_bridge_inbox_recovery.py
        tests/tools/test_bridge_wake_observation.py
        tests/tools/test_lane_launch_preflight.py
        tests/tools/test_lane_profile_launch_probe.py
        tests/tools/test_wd_bridge_code_context.py
        tests/tools/test_wd_continuity_controls.py
        tests/tools/test_wd_conversation_recovery.py
        tests/tools/test_wd_dynamic_model_startup.py
        tests/tools/test_wd_launcher_claude_marker_scrub.py
        tests/tools/test_wd_lead_continuity_imports.py
        tests/tools/test_wd_lead_reply_delivery.py
        tests/tools/test_wd_native_tools.py
        tests/tools/test_wd_native_tools_wake.py
        tests/tools/test_wd_reboot_bundle.py
        tests/tools/test_wd_startup_recovery.py
        tests/tools/test_wd_supervisor_opaque_process.py
        tests/tools/test_wd_swarm_parallel_status.py
        tests/tools/test_wd_tools_conversation.py
    """.split()),
    "ops/windows/reboot/Deploy-WdRebootBundle.ps1": frozenset({
        "tests/tools/test_bridge_final_acceptance_20260927.py",
        "tests/tools/test_wd_bridge_code_context.py",
        "tests/tools/test_wd_dynamic_model_startup.py",
        "tests/tools/test_wd_grok_helper.py",
        "tests/tools/test_wd_reboot_bundle.py",
    }),
    "ops/windows/reboot/Get-WdNativeWakePrompt.ps1": frozenset({
        "tests/tools/test_wd_native_wake_prompt.py",
        "tests/tools/test_wd_reboot_bundle.py",
    }),
    "ops/windows/reboot/WAKE_PROCEDURE_LEAD.md": frozenset({
        "tests/tools/test_wd_native_wake_prompt.py",
        "tests/tools/test_wd_reboot_bundle.py",
    }),
    "ops/windows/reboot/WAKE_PROCEDURE_TOOLS.md": frozenset({
        "tests/tools/test_wd_native_wake_prompt.py",
        "tests/tools/test_wd_reboot_bundle.py",
        "tests/tools/test_wd_tools_exact_incoming_retrieval.py",
    }),
    "tools/bridge_continuity_guard.py": frozenset({
        "tests/tools/test_bridge_continuity_guard.py",
        "tests/tools/test_wd_native_tools_wake.py",
    }),
    "ops/windows/reboot/wd-fleet.json": frozenset({
        "tests/tools/test_wd_reboot_bundle.py",
    }),
    "ops/windows/reboot/wd_supervisor_loop.json": frozenset({
        "tests/tools/test_wd_reboot_bundle.py",
    }),
}

# Checked against the candidate tree's explicit sibling-test imports. These
# are runtime fixture imports, not generic filename or import-grep inference.
BRIDGE_SHARED_TEST_CONSUMERS: dict[str, frozenset[str]] = {
    "tests/tools/test_wd_native_wake_prompt.py": frozenset({
        "tests/tools/test_wd_lead_reply_delivery.py",
        "tests/tools/test_wd_native_tools_wake.py",
    }),
    "tests/tools/test_wd_native_tools_wake.py": frozenset({
        "tests/tools/test_wd_native_wake_prompt.py",
    }),
    "tests/tools/test_wd_continuity_alert.py": frozenset({
        "tests/tools/test_wd_native_tools_wake.py",
    }),
}

# Three shared providers are imported by tests throughout the independently
# reviewed Bridge boundary. Include the complete explicit Bridge set when any
# changes; two additional direct importers were checked at ec76 (request
# preflight and task-console bridge-pin tests). No product test is inferred by
# directory prefix. Missing members force full in both selector and router.
BRIDGE_PROVIDER_CONSUMERS = frozenset(set().union(*BRIDGE_EXPLICIT_TESTS.values()) | {
    "tests/tools/test_bridge_request_preflight.py",
    "tests/tools/test_wd_task_console_containment_pin.py",
})
for _provider in (
    "tests/tools/test_wd_reboot_bundle.py",
    "tests/tools/test_wd_startup_recovery.py",
    "tests/tools/test_wd_bridge_code_context.py",
):
    BRIDGE_SHARED_TEST_CONSUMERS[_provider] = BRIDGE_PROVIDER_CONSUMERS - {_provider}


def bridge_test_closure(tests: set[str]) -> set[str]:
    """Expand the reviewed shared-test edges to a transitive fixed point."""
    closed = set(tests)
    while True:
        expanded = closed | set().union(*(BRIDGE_SHARED_TEST_CONSUMERS.get(t, ()) for t in closed))
        if expanded == closed:
            return closed
        closed = expanded


def _norm(path: str) -> str:
    p = path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def _is_bridge_source(path: str) -> bool:
    return _norm(path) in BRIDGE_EXPLICIT_TESTS


def _bridge_tests_for(path: str, repo_root: Path) -> tuple[set[str], str | None]:
    """Use only the reviewed source-to-test list; never infer completeness by grep."""
    p = _norm(path)
    mapped = BRIDGE_EXPLICIT_TESTS.get(p)
    if not mapped:
        return set(), f"unmapped Bridge source: {p}"
    for test in sorted(mapped):
        candidate = repo_root / test
        if not candidate.is_file():
            return set(), f"Bridge mapped test missing for {p}: {test}"
        try:
            candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return set(), f"unreadable Bridge mapped test for {p}: {test}: {exc}"
    return set(mapped), None


def _is_test_file(path: str) -> bool:
    p = _norm(path)
    return (
        p.startswith(TEST_PREFIXES)
        and p.endswith(".py")
        and Path(p).name.startswith("test_")
    )


def _is_source_file(path: str) -> bool:
    p = _norm(path)
    return p.startswith(SOURCE_PREFIXES) and p.endswith(".py") and not _is_test_file(p)


def _is_broad_impact(path: str) -> bool:
    p = _norm(path)
    if p in BROAD_IMPACT_PATHS:
        return True
    name = Path(p).name
    if name in BROAD_IMPACT_BASENAMES:
        return True
    return any(sub in name for sub in BROAD_IMPACT_SUBSTRINGS)


def _module_dotted(path: str) -> str:
    p = _norm(path)
    return p[:-3].replace("/", ".") if p.endswith(".py") else p.replace("/", ".")


def _tests_importing(
    source_path: str, repo_root: Path
) -> tuple[set[str], bool]:
    """Return (affected tests, read_error).

    ``read_error`` is True if any candidate test file could not be read — the
    caller must then fail-safe to the full suite, since it cannot rule out that
    the unreadable candidate imports the changed module.
    """
    dotted = _module_dotted(source_path)
    stem = Path(_norm(source_path)).stem
    tests_dir = repo_root / "tests"
    if not tests_dir.is_dir():
        return set(), False
    import_pat = _IMPORT_RE_CACHE.get(dotted)
    if import_pat is None:
        # Match `import a.b.c` / `from a.b.c import ...` / `from a.b import c`.
        esc = re.escape(dotted)
        parent = re.escape(dotted.rsplit(".", 1)[0]) if "." in dotted else esc
        import_pat = re.compile(
            rf"(?:^|\b)(?:import\s+{esc}\b"
            rf"|from\s+{esc}\s+import\b"
            rf"|from\s+{parent}\s+import\s+[^\n]*\b{re.escape(stem)}\b)"
        )
        _IMPORT_RE_CACHE[dotted] = import_pat
    affected: set[str] = set()
    read_error = False
    for test_file in tests_dir.rglob("test_*.py"):
        rel = test_file.relative_to(repo_root).as_posix()
        # Name convention: tests/.../test_<stem>.py is affected by <stem>.py.
        if test_file.stem == f"test_{stem}":
            affected.add(rel)
            continue
        try:
            text = test_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            # An unreadable candidate could import the changed module → signal
            # uncertainty so the caller fail-safes to the full suite (contract).
            read_error = True
            continue
        if import_pat.search(text):
            affected.add(rel)
    return affected, read_error


def _full(reason: str) -> dict:
    return {"full_suite": True, "tests": [], "reason": reason}


def _explicit_tests_for(path: str, repo_root: Path) -> tuple[set[str], str | None]:
    mapped = EXPLICIT_AFFECTED_TESTS.get(_norm(path))
    if not mapped:
        return set(), None
    missing = sorted(t for t in mapped if not (repo_root / t).is_file())
    if missing:
        return set(), f"explicit affected test missing for source {path}: {missing[0]}"
    return set(mapped), None


def select_affected_tests(
    changed_files: Iterable[str], repo_root: str | Path = "."
) -> dict:
    """Return {full_suite, tests, reason}. Fail-safe to full_suite when uncertain."""
    root = Path(repo_root)
    files = [_norm(f) for f in changed_files if _norm(f)]
    if not files:
        return _full("no changed files provided")
    bridge_sources = [f for f in files if _is_bridge_source(f)]
    if bridge_sources and any(
        not _is_bridge_source(f) and not (_is_test_file(f) and f.startswith("tests/tools/"))
        for f in files
    ):
        return _full("Bridge boundary mixed with product/unknown changes")
    tests: set[str] = set()
    for f in files:
        if _is_broad_impact(f):
            return _full(f"broad-impact file changed: {f}")
        if _is_test_file(f):
            if not (root / f).is_file():
                return _full(f"changed test file missing: {f}")
            tests.add(f)
            continue
        if _is_bridge_source(f):
            mapped, error = _bridge_tests_for(f, root)
            if error:
                return _full(error)
            tests |= mapped
            continue
        if _is_source_file(f):
            explicit, explicit_error = _explicit_tests_for(f, root)
            if explicit_error:
                return _full(explicit_error)
            mapped, read_error = _tests_importing(f, root)
            if read_error:
                return _full(f"unreadable test candidate while mapping: {f}")
            mapped |= explicit
            if not mapped:
                return _full(f"no affected test found for source: {f}")
            tests |= mapped
            continue
        # Unknown file type (docs, config, data, etc.) → cannot reason → fail-safe.
        return _full(f"unmapped changed file: {f}")
    if not tests:
        return _full("no tests selected")
    tests = bridge_test_closure(tests)
    for test in sorted(tests):
        candidate = root / test
        if not candidate.is_file():
            return _full(f"shared Bridge test consumer missing: {test}")
        try:
            candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return _full(f"shared Bridge test consumer unreadable: {test}")
    return {"full_suite": False, "tests": sorted(tests), "reason": "affected-only"}


def _git_changed(base: str, repo_root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=str(repo_root),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    return [line for line in (out or "").splitlines() if line.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--files", nargs="+", help="changed file paths (repo-relative)")
    src.add_argument(
        "--changed-from-git",
        metavar="BASE",
        help="compute changed files from `git diff --name-only BASE...HEAD`",
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.repo_root)
    if args.changed_from_git is not None:
        changed = _git_changed(args.changed_from_git, root)
    else:
        changed = list(args.files)
    result = select_affected_tests(changed, root)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    elif result["full_suite"]:
        print(f"FULL SUITE ({result['reason']})")
    else:
        print("\n".join(result["tests"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
