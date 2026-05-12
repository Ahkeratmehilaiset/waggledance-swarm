"""SolverRouter signal registry tests."""

from __future__ import annotations

import yaml

from waggledance.core.reasoning import solver_router as solver_router_mod
from waggledance.core.reasoning.solver_router import SolverRouter


def test_solver_signal_config_matches_loaded_registry() -> None:
    path = solver_router_mod.SOLVER_SIGNAL_CONFIG_PATH
    raw = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader)

    assert raw["math"] == sorted(raw["math"])
    for name, defaults in solver_router_mod._DEFAULT_SIGNAL_SETS.items():
        assert defaults <= solver_router_mod._SIGNAL_SETS[name]
    assert "paljonko" in solver_router_mod._SIGNAL_SETS["math"]
    assert "mikä on" in solver_router_mod._SIGNAL_SETS["retrieval"]


def test_classify_intent_uses_runtime_signal_registry(monkeypatch) -> None:
    patched = dict(solver_router_mod._SIGNAL_SETS)
    patched["retrieval"] = frozenset({"yaml only phrase"})
    monkeypatch.setattr(solver_router_mod, "_SIGNAL_SETS", patched)

    assert SolverRouter.classify_intent("yaml only phrase") == "retrieval"
