"""Phase 4 release-polish tests: packaging + cross-platform metadata.

These tests do NOT build the wheel (the build is verified manually in
``reports/PACKAGING_CROSSPLATFORM_AUDIT.md``). Instead they parse the
static ``pyproject.toml`` and assert on invariants that would silently
regress if a future edit removed the package-find config or broke the
entry point.
"""

import tomllib
from pathlib import Path

import pytest


_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


@pytest.fixture(scope="module")
def cfg() -> dict:
    with _PYPROJECT.open("rb") as f:
        return tomllib.load(f)


def test_pyproject_has_explicit_package_discovery(cfg):
    """Without this, flat-layout auto-discovery fails with 'Multiple
    top-level packages'. Locks in the P4 fix."""
    tool = cfg.get("tool", {})
    setuptools_cfg = tool.get("setuptools", {})
    packages_cfg = setuptools_cfg.get("packages", {})
    find_cfg = packages_cfg.get("find", {})
    assert find_cfg, (
        "pyproject.toml must declare [tool.setuptools.packages.find] with "
        "an explicit include list; flat-layout auto-discovery is broken "
        "by the many sibling top-level directories at the project root"
    )
    include = find_cfg.get("include", [])
    assert "waggledance*" in include
    # integrations/* is imported lazily by the data_feed_scheduler
    # cached_property; without it a wheel install silently lacks feeds.
    assert "integrations*" in include


def test_pyproject_excludes_data_heavy_dirs(cfg):
    """The exclude list guards against accidental wheel bloat if someone
    later drops a broader include glob."""
    exclude = cfg["tool"]["setuptools"]["packages"]["find"].get("exclude", [])
    for must_exclude in ("chroma_data*", "models*", "output*", "tests*"):
        assert must_exclude in exclude, (
            f"{must_exclude} must be in the packages.find.exclude list"
        )


def test_console_script_points_to_existing_target(cfg):
    """The waggledance console script must resolve to an actually
    importable module:function."""
    scripts = cfg["project"].get("scripts", {})
    assert "waggledance" in scripts
    target = scripts["waggledance"]
    assert ":" in target
    module_path, func = target.split(":", 1)
    assert module_path == "waggledance.adapters.cli.start_runtime"
    assert func == "main"
    # Runtime probe: the target must actually exist.
    import importlib
    mod = importlib.import_module(module_path)
    assert callable(getattr(mod, func, None)), (
        f"console_script target {target} is not callable"
    )


def test_requires_python_is_modern(cfg):
    """3.11+ so that ``dict | dict`` union / ``str | None`` PEP 604 syntax
    and ``tomllib`` are available — both are used in the codebase."""
    req = cfg["project"].get("requires-python", "")
    assert req.startswith(">=3.1"), f"requires-python too lax: {req!r}"


def test_core_deps_pinned_to_floor(cfg):
    """Lock in the minimum major versions of the libraries we depend on.
    Prevents an accidental lower-bound regression."""
    deps = cfg["project"].get("dependencies", [])
    names = {d.split(">=")[0].split("[")[0]: d for d in deps}
    required = {
        "fastapi": ">=0.115",
        "uvicorn[standard]": ">=0.30",
        "pydantic": ">=2.0",
        "chromadb": ">=1.0",
        "pyyaml": ">=6.0",
    }
    for pkg, floor in required.items():
        # Normalize comparison key so uvicorn[standard] matches.
        matched = [v for k, v in names.items() if k == pkg.split("[")[0]]
        assert matched, f"missing core dependency: {pkg}"
        assert any(floor in dep for dep in matched), (
            f"{pkg} floor regressed below {floor}; got {matched}"
        )
