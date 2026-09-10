# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed vector-backend selection in the DI container.

Dependency remediation 2026-08-26 (+ lead implementation guidance):
chromadb was de-scoped from the stable default INSTALL (5 OSV advisories,
no fixed release), while the non-stub default BACKEND stays ``"chroma"``
to preserve historical production semantics. These tests pin the contract:

  * the default backend is ``"chroma"`` (dataclass and ``from_env``);
  * non-stub default startup WITHOUT chromadb installed raises
    RuntimeError carrying the explicit ``[chroma]`` install instruction —
    never a silent fallback to the in-memory store;
  * ``vector_backend=inmemory`` is an EXPLICIT opt-in that boots without
    chromadb and is non-persistent;
  * unknown backend values raise RuntimeError (fail-closed, Audit H30
    pattern);
  * stub mode keeps returning in-memory implementations regardless of the
    configured backend;
  * importing ``waggledance.adapters.memory`` never requires chromadb
    (lazy PEP 562 exports).

No ChromaDB server, Ollama, or network access is required.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container


def _settings(backend: str) -> SimpleNamespace:
    return SimpleNamespace(
        vector_backend=backend,
        chroma_dir="./chroma_data_test",
        embed_model="nomic-embed-text",
    )


def test_default_dataclass_backend_is_chroma() -> None:
    assert WaggleSettings().vector_backend == "chroma"


def test_non_stub_default_without_chromadb_fails_with_install_hint(monkeypatch) -> None:
    # sys.modules[name] = None makes `import chromadb` raise ImportError.
    monkeypatch.setitem(sys.modules, "chromadb", None)
    container = Container(_settings("chroma"), stub=False)

    with pytest.raises(RuntimeError) as excinfo:
        _ = container.vector_store

    message = str(excinfo.value)
    assert "waggledance-swarm[chroma]" in message
    assert "Refusing to fall back" in message


def test_chroma_backend_constructs_chroma_store_when_available(monkeypatch) -> None:
    pytest.importorskip("chromadb")

    captured: dict = {}

    class _FakeChromaStore:
        def __init__(self, *, persist_directory: str, embedding_model: str) -> None:
            captured["persist_directory"] = persist_directory
            captured["embedding_model"] = embedding_model

    monkeypatch.setattr(
        "waggledance.adapters.memory.chroma_vector_store.ChromaVectorStore",
        _FakeChromaStore,
    )
    container = Container(_settings("chroma"), stub=False)

    store = container.vector_store
    assert isinstance(store, _FakeChromaStore)
    assert captured == {
        "persist_directory": "./chroma_data_test",
        "embedding_model": "nomic-embed-text",
    }


def test_explicit_inmemory_opt_in_boots_without_chromadb(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "chromadb", None)
    container = Container(_settings("inmemory"), stub=False)

    from waggledance.adapters.memory.in_memory_repository import InMemoryRepository
    from waggledance.adapters.memory.in_memory_vector_store import InMemoryVectorStore

    assert isinstance(container.vector_store, InMemoryVectorStore)
    assert isinstance(container.memory_repository, InMemoryRepository)


def test_unknown_backend_raises_runtime_error() -> None:
    container = Container(_settings("faiss-nope"), stub=False)

    with pytest.raises(RuntimeError) as excinfo:
        _ = container.vector_store

    assert "Unknown vector_backend" in str(excinfo.value)


def test_unknown_backend_blocks_memory_repository_too() -> None:
    container = Container(_settings("bogus"), stub=False)

    with pytest.raises(RuntimeError):
        _ = container.memory_repository


def test_stub_mode_ignores_backend_setting() -> None:
    container = Container(_settings("chroma"), stub=True)

    from waggledance.adapters.memory.in_memory_repository import InMemoryRepository
    from waggledance.adapters.memory.in_memory_vector_store import InMemoryVectorStore

    assert isinstance(container.vector_store, InMemoryVectorStore)
    assert isinstance(container.memory_repository, InMemoryRepository)


def test_memory_package_imports_without_chromadb(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "chromadb", None)
    import waggledance.adapters.memory as memory_pkg

    # Non-chroma exports are eagerly available.
    assert memory_pkg.InMemoryVectorStore is not None
    assert memory_pkg.HotCache is not None
    # Lazy chroma exports are listed but resolve only on attribute access.
    assert "ChromaVectorStore" in memory_pkg.__all__
    assert "ChromaVectorStore" in dir(memory_pkg)


def test_from_env_normalizes_backend(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WAGGLE_VECTOR_BACKEND", "  InMemory  ")
    settings = WaggleSettings.from_env(
        env_path=tmp_path / "missing.env",
        yaml_path=tmp_path / "missing.yaml",
    )
    assert settings.vector_backend == "inmemory"


def test_from_env_default_backend_is_chroma(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("WAGGLE_VECTOR_BACKEND", raising=False)
    settings = WaggleSettings.from_env(
        env_path=tmp_path / "missing.env",
        yaml_path=tmp_path / "missing.yaml",
    )
    assert settings.vector_backend == "chroma"
