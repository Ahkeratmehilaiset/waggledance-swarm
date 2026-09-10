# SPDX-License-Identifier: BUSL-1.1
"""E6: Docker/docs truth for the opt-in Chroma vector backend.

Companion to ``tests/unit/test_container_vector_backend.py``. That suite
pins the RUNTIME contract (default backend ``chroma``, fail-closed
RuntimeError when the package is missing, explicit ``inmemory`` opt-in).
This suite pins the SURFACE that operators actually read and run --
docker-compose, README, the Docker quickstart, the capability config, and
the service docstrings -- so the two cannot drift apart silently.

Three things are load-bearing here.

1. The compose entry must use the SINGLE-dash form
   ``${WAGGLE_VECTOR_BACKEND-inmemory}``. ``:-`` treats an explicit EMPTY
   value as unset and would silently select the non-persistent backend;
   ``-`` defaults only when the variable is genuinely unset, so an empty
   value survives and the runtime rejects it. A literal ``=inmemory`` pin
   is worse still: entries under ``environment:`` override ``env_file:``,
   so it would override an operator who asked for chroma with no error.
2. ``CHROMA_DIR`` must resolve inside a mounted volume. The runtime
   default ``./chroma_data`` becomes ``/app/chroma_data`` under
   ``WORKDIR /app``, which no compose volume covers, so a derived-image
   Chroma run would lose its store on container replacement.
3. Install examples must be checkout-bound (``.[chroma]``) so the extra is
   installed against the code under test rather than whatever the package
   index resolves to.

No Docker daemon, ChromaDB server, Ollama, or network access is required.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container
from waggledance.core.capabilities.registry import CapabilityRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]

COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
README_PATH = REPO_ROOT / "README.md"
QUICKSTART_PATH = REPO_ROOT / "docs" / "deployment" / "DOCKER_QUICKSTART.md"
RETRIEVERS_PATH = REPO_ROOT / "configs" / "capabilities" / "retrievers.yaml"
HYBRID_PATH = (
    REPO_ROOT / "waggledance" / "application" / "services" / "hybrid_retrieval_service.py"
)
MEMORY_SERVICE_PATH = (
    REPO_ROOT / "waggledance" / "application" / "services" / "memory_service.py"
)

BACKEND_VAR = "WAGGLE_VECTOR_BACKEND"
CHROMA_DIR_VAR = "CHROMA_DIR"
EXPECTED_BACKEND_TOKEN = f"{BACKEND_VAR}=${{{BACKEND_VAR}-inmemory}}"
EXPECTED_CHROMA_DIR_TOKEN = f"{CHROMA_DIR_VAR}=${{{CHROMA_DIR_VAR}-/app/data/chroma_data}}"

_INTERPOLATION_RE = re.compile(
    r"^\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<op>:-|-|:\?|\?)?(?P<default>.*)\}$"
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _service(name: str = "waggledance") -> dict:
    return yaml.safe_load(_read(COMPOSE_PATH))["services"][name]


def _service_env() -> list[str]:
    """Raw ``environment:`` entries. PyYAML does not interpolate, so the
    ``${VAR-default}`` text survives verbatim -- which is what we inspect."""
    env = _service().get("environment", [])
    if isinstance(env, dict):
        return [f"{k}={v}" for k, v in env.items()]
    return [str(entry) for entry in env]


def _assignments(var: str) -> list[str]:
    return [e for e in _service_env() if e.split("=", 1)[0].strip() == var]


def _resolve(entry: str, environ: dict[str, str]) -> str:
    """Resolve one ``KEY=value`` entry the way Compose would.

    ``:-`` substitutes when unset OR empty; ``-`` substitutes only when
    unset. A value with no interpolation is a literal and always wins.
    """
    _, _, value = entry.partition("=")
    match = _INTERPOLATION_RE.match(value)
    if match is None:
        return value
    name, op, default = match["name"], match["op"], match["default"]
    present = environ.get(name)
    if op == ":-":
        return present if present else default
    if op == "-":
        return present if present is not None else default
    return present if present is not None else ""


# --------------------------------------------------------------------------
# compose: single-dash contract, hard-pin rejection, colon-dash rejection
# --------------------------------------------------------------------------


def test_backend_declared_exactly_once() -> None:
    assert len(_assignments(BACKEND_VAR)) == 1, _assignments(BACKEND_VAR)


def test_backend_uses_the_exact_single_dash_default() -> None:
    assert _assignments(BACKEND_VAR) == [EXPECTED_BACKEND_TOKEN]


def test_backend_rejects_a_literal_hard_pin() -> None:
    for entry in _assignments(BACKEND_VAR):
        _, _, value = entry.partition("=")
        assert _INTERPOLATION_RE.match(value) is not None, (
            f"{BACKEND_VAR} is hard-pinned to {value!r}. Entries under "
            "`environment:` override `env_file:`, so this silently ignores "
            f"an operator who asked for chroma. Use {EXPECTED_BACKEND_TOKEN}."
        )
        assert value != "inmemory"


def test_backend_rejects_the_colon_dash_form() -> None:
    """``:-`` would map an explicit empty value to the non-persistent store."""
    for entry in _assignments(BACKEND_VAR):
        _, _, value = entry.partition("=")
        match = _INTERPOLATION_RE.match(value)
        assert match is not None
        assert match["op"] == "-", (
            f"{BACKEND_VAR} uses {value!r}. The ':-' form treats an explicit "
            "empty value as unset and silently selects inmemory, which "
            "contradicts the never-fall-back-silently contract. Use the "
            "single-dash form so an empty value reaches the runtime and is "
            "rejected there."
        )


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        pytest.param({}, "inmemory", id="unset-gets-compose-default"),
        pytest.param({BACKEND_VAR: ""}, "", id="empty-is-preserved-not-defaulted"),
        pytest.param({BACKEND_VAR: "chroma"}, "chroma", id="explicit-chroma-preserved"),
        pytest.param({BACKEND_VAR: "inmemory"}, "inmemory", id="explicit-inmemory-preserved"),
    ],
)
def test_backend_interpolation_semantics(environ: dict[str, str], expected: str) -> None:
    (entry,) = _assignments(BACKEND_VAR)
    assert _resolve(entry, environ) == expected


def test_the_two_interpolation_forms_differ_exactly_on_empty() -> None:
    """Guards the guard: the resolver must actually distinguish them."""
    empty = {BACKEND_VAR: ""}
    assert _resolve(f"{BACKEND_VAR}=${{{BACKEND_VAR}:-inmemory}}", empty) == "inmemory"
    assert _resolve(EXPECTED_BACKEND_TOKEN, empty) == ""
    # ...and agree everywhere else that matters
    for env in ({}, {BACKEND_VAR: "chroma"}):
        assert _resolve(f"{BACKEND_VAR}=${{{BACKEND_VAR}:-inmemory}}", env) == _resolve(
            EXPECTED_BACKEND_TOKEN, env
        )


def test_compose_still_reads_env_file() -> None:
    assert _service().get("env_file") == ".env"


# --------------------------------------------------------------------------
# compose: the Chroma store must land inside a mounted volume
# --------------------------------------------------------------------------


def test_chroma_dir_is_declared_with_the_exact_token() -> None:
    assert _assignments(CHROMA_DIR_VAR) == [EXPECTED_CHROMA_DIR_TOKEN]


def _container_mount_targets() -> list[str]:
    targets = []
    for volume in _service().get("volumes", []):
        parts = str(volume).split(":")
        if len(parts) >= 2:
            targets.append(parts[1])
    return targets


def test_chroma_dir_default_is_inside_a_mounted_volume() -> None:
    """The whole point of finding 1: an unmounted store is lost on replace."""
    (entry,) = _assignments(CHROMA_DIR_VAR)
    resolved = _resolve(entry, {})
    targets = _container_mount_targets()
    assert any(
        resolved == t or resolved.startswith(t.rstrip("/") + "/") for t in targets
    ), (
        f"CHROMA_DIR resolves to {resolved!r}, which is not inside any compose "
        f"mount {targets!r}. A derived-image Chroma run would lose its store "
        "on container replacement."
    )


def test_the_runtime_default_chroma_dir_would_not_be_mounted() -> None:
    """Why the override exists: ./chroma_data -> /app/chroma_data is unmounted."""
    targets = _container_mount_targets()
    unmounted = "/app/chroma_data"  # WORKDIR /app + settings default ./chroma_data
    assert not any(
        unmounted == t or unmounted.startswith(t.rstrip("/") + "/") for t in targets
    )


# --------------------------------------------------------------------------
# runtime binding: the empty value the single-dash form lets through
# --------------------------------------------------------------------------


def _settings(backend: str) -> SimpleNamespace:
    return SimpleNamespace(
        vector_backend=backend,
        chroma_dir="./chroma_data_test",
        embed_model="nomic-embed-text",
    )


@pytest.mark.parametrize("backend", ["", "  ", "sqlite"])
def test_runtime_rejects_what_compose_lets_through(backend: str) -> None:
    """Closes the loop: empty is preserved by compose AND refused by the runtime.

    This is what makes the single-dash form safe rather than merely
    different -- the value is not silently reinterpreted anywhere.
    """
    container = Container(_settings(backend), stub=False)
    with pytest.raises(RuntimeError) as excinfo:
        _ = container.vector_store
    assert "Refusing to guess a memory backend" in str(excinfo.value)


def test_runtime_default_backend_is_chroma_not_inmemory() -> None:
    """The docs say unset means chroma natively; pin that to the code."""
    assert WaggleSettings().vector_backend == "chroma"


def test_missing_chroma_package_fails_closed(monkeypatch) -> None:
    """The docs promise a startup error, not a silent downgrade."""
    monkeypatch.setitem(sys.modules, "chromadb", None)
    container = Container(_settings("chroma"), stub=False)
    with pytest.raises(RuntimeError) as excinfo:
        _ = container.vector_store
    assert "Refusing to fall back" in str(excinfo.value)


# --------------------------------------------------------------------------
# docs truth
# --------------------------------------------------------------------------


def test_install_examples_are_checkout_bound() -> None:
    for path in (README_PATH, QUICKSTART_PATH):
        text = _read(path)
        assert ".[chroma]" in text, f"{path.name} must show the checkout-bound extra"
        assert "waggledance-swarm[chroma]" not in text, (
            f"{path.name} installs from the package index, which can resolve a "
            "different build than the checkout under test"
        )


def test_quickstart_shows_the_exact_compose_tokens() -> None:
    quickstart = _read(QUICKSTART_PATH)
    assert EXPECTED_BACKEND_TOKEN in quickstart
    assert EXPECTED_CHROMA_DIR_TOKEN in quickstart


def test_quickstart_explains_empty_is_not_defaulted() -> None:
    quickstart = _read(QUICKSTART_PATH)
    assert "${VAR:-default}" in quickstart or ":-" in quickstart
    assert "empty" in quickstart.lower()


def test_quickstart_requires_a_derived_image_and_a_mounted_store() -> None:
    quickstart = _read(QUICKSTART_PATH).lower()
    assert "derived image" in quickstart
    assert "not sufficient" in quickstart
    assert "requirements-ci.txt" in quickstart
    assert "/app/chroma_data" in quickstart


def test_quickstart_documents_plain_docker_run_as_fail_closed() -> None:
    quickstart = _read(QUICKSTART_PATH)
    assert f"-e {BACKEND_VAR}=inmemory" in quickstart


def test_readme_does_not_present_inmemory_as_an_automatic_alternative() -> None:
    readme = _read(README_PATH).lower()
    assert "only when explicitly" in readme or "explicit only" in readme
    assert "fails closed" in readme or "fail-closed" in readme
    for lie in ("persistent by default", "persists by default", "falls back to inmemory"):
        assert lie not in readme


# --------------------------------------------------------------------------
# capability config: metadata truth only, runtime numbers untouched
# --------------------------------------------------------------------------


def _semantic_search_entry() -> dict:
    data = yaml.safe_load(_read(RETRIEVERS_PATH))
    for entry in data["capabilities"]:
        if entry.get("id") == "retrieve.semantic_search":
            return entry
    raise AssertionError("retrieve.semantic_search missing from retrievers.yaml")


def test_retrievers_numeric_fields_are_unchanged() -> None:
    """These two ARE applied to the builtin entry by load_yaml_configs()."""
    entry = _semantic_search_entry()
    assert entry["max_latency_ms"] == 500
    assert entry["trust_baseline"] == 0.85


def test_retrievers_wording_does_not_imply_an_automatic_fallback() -> None:
    description = _semantic_search_entry()["description"].lower()
    assert "selected and installed" in description
    assert "only when explicitly requested" in description
    assert "otherwise" not in description


def test_yaml_description_is_file_truth_not_runtime_truth() -> None:
    """Why the wording change is safe.

    ``load_yaml_configs`` applies a YAML description ONLY when the existing
    entry's description is empty, and the builtin entry is non-empty -- so
    this wording never replaces the builtin at runtime. If that changes,
    this test fails and the claim must be re-examined.
    """
    registry_source = _read(
        REPO_ROOT / "waggledance" / "core" / "capabilities" / "registry.py"
    )
    assert 'if "description" in defn and not existing.description:' in registry_source
    builtin = CapabilityRegistry().get("retrieve.semantic_search")
    assert builtin is not None and builtin.description


# --------------------------------------------------------------------------
# docstring truth (telemetry names and behavior deliberately retained)
# --------------------------------------------------------------------------


def test_hybrid_docstring_states_selected_and_installed() -> None:
    header = _read(HYBRID_PATH).split('"""')[1]
    assert "SELECTED and INSTALLED" in header
    assert "never an automatic alternative" in header
    assert "global ChromaDB retrieval" not in header


def test_no_line_of_the_hybrid_docstring_implies_a_fallback() -> None:
    """The whole docstring must agree with itself, not just its prose.

    The retrieval-order list shipped saying "Chroma when opted in, else
    in-memory" while the class docstring eight lines below said the
    opposite. Both were read, each in isolation, and the contradiction
    survived. This checks the docstring as a whole so one corrected
    paragraph cannot vouch for an uncorrected list item.
    """
    header = _read(HYBRID_PATH).split('"""')[1]
    lowered = header.lower()
    for phrasing in ("else in-memory", "otherwise in-memory", "opted in, else"):
        assert phrasing not in lowered, (
            f"{phrasing!r} implies in-memory is an automatic fallback; it is "
            "reachable only by explicit selection"
        )
    assert (
        "4. Global vector store (Chroma when selected and installed; "
        "in-memory only when explicitly selected)" in header
    )


def test_memory_service_docstring_states_explicit_only() -> None:
    header = _read(MEMORY_SERVICE_PATH).split('"""')[1]
    assert "never selected" in header
    assert BACKEND_VAR in header


def test_service_telemetry_names_are_retained() -> None:
    """Wire compatibility: the names describe the LAYER, not the backend."""
    source = _read(HYBRID_PATH)
    for name in ("global_chroma_ms", "chroma_degraded", '"global_chroma"'):
        assert name in source, f"{name} must be retained"
