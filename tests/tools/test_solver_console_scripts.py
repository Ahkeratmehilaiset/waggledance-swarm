# SPDX-License-Identifier: Apache-2.0
"""Sprint seed #13: the 8 solver CLIs are installed as console scripts.

These tests are the done-evidence that each `[project.scripts]` solver entry
resolves to a real, callable `main` and stays in sync with the solver registry's
`cli_module` fields. They do not require an actual install — they validate the
entry-point *targets* the same way a console-script wrapper would import them.
"""
from __future__ import annotations

import importlib
import json
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
REGISTRY = REPO_ROOT / "schemas" / "v3_13_0" / "solver_registry.json"

SOLVER_SCRIPT_PREFIX = "wd-"
EXPECTED_SOLVER_SCRIPTS = 8


def _project_scripts() -> dict[str, str]:
    data = tomllib.loads(PYPROJECT.read_text("utf-8"))
    return dict(data.get("project", {}).get("scripts", {}))


def _solver_scripts() -> dict[str, str]:
    return {
        name: target
        for name, target in _project_scripts().items()
        if name.startswith(SOLVER_SCRIPT_PREFIX)
    }


def _registry_cli_modules() -> set[str]:
    reg = json.loads(REGISTRY.read_text("utf-8"))
    solvers = reg if isinstance(reg, list) else reg.get("solvers", reg.get("registry", []))
    if isinstance(solvers, dict):
        solvers = list(solvers.values())
    return {
        s["cli_module"]
        for s in solvers
        if isinstance(s, dict) and s.get("cli_module")
    }


def test_all_eight_solver_console_scripts_registered():
    scripts = _solver_scripts()
    assert len(scripts) == EXPECTED_SOLVER_SCRIPTS, (
        f"expected {EXPECTED_SOLVER_SCRIPTS} wd-* solver scripts, got {sorted(scripts)}"
    )


@pytest.mark.parametrize("name,target", sorted(_solver_scripts().items()))
def test_console_script_target_resolves_to_callable_main(name, target):
    # A console_scripts target is "module.path:attr"; the wrapper imports the
    # module and calls attr(). Validate exactly that resolution here.
    module_path, sep, attr = target.partition(":")
    assert sep == ":" and attr, f"{name}: malformed entry point {target!r}"
    module = importlib.import_module(module_path)
    func = getattr(module, attr, None)
    assert callable(func), f"{name}: {target} does not resolve to a callable"


def test_console_scripts_cover_exactly_the_registry_cli_modules():
    # Drift guard: every solver_registry cli_module has a console script, and no
    # solver script points at a module the registry does not declare.
    script_modules = {t.split(":", 1)[0] for t in _solver_scripts().values()}
    registry_modules = _registry_cli_modules()
    assert script_modules == registry_modules, (
        "console-script modules and registry cli_module fields disagree; "
        f"only-in-scripts={script_modules - registry_modules}, "
        f"only-in-registry={registry_modules - script_modules}"
    )


def test_main_targets_are_zero_arg_callable_for_console_wrapper():
    # console_scripts call main() with no args, so each main must accept being
    # called with no positional args (argv defaults to None -> reads sys.argv).
    import inspect

    for name, target in _solver_scripts().items():
        module_path, _, attr = target.partition(":")
        func = getattr(importlib.import_module(module_path), attr)
        sig = inspect.signature(func)
        required_positional = [
            p
            for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            and p.default is p.empty
        ]
        assert not required_positional, (
            f"{name}: {target} main() has required positional args "
            f"{[p.name for p in required_positional]}; console wrapper calls main()"
        )


@pytest.mark.parametrize("name,target", sorted(_solver_scripts().items()))
def test_console_script_is_actually_invokable_via_help(name, target, capsys):
    # Stronger than resolution: prove the CLI's argparse layer constructs and the
    # command is genuinely user-invokable end-to-end. `--help` must build the
    # parser and exit 0 (the product last-mile guarantee: all 8 solver commands
    # stay runnable, not just importable). Regression guard for the "solvers not
    # user-invokable" gap that the console scripts (#1466) closed.
    module_path, _, attr = target.partition(":")
    main = getattr(importlib.import_module(module_path), attr)
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0, f"{name}: {target} --help exited {exc.value.code}"
    out = capsys.readouterr().out
    assert "usage" in out.lower(), f"{name}: --help printed no usage text"
