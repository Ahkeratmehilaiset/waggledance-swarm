# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import builtins

import pytest

from waggledance.bootstrap.container import Container


class _Settings:
    pass


def _patch_faiss_store_import(monkeypatch, error: ImportError) -> None:
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "core.faiss_store":
            raise error
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)


def test_container_treats_missing_faiss_module_as_optional(monkeypatch) -> None:
    error = ModuleNotFoundError("No module named 'faiss'", name="faiss")
    _patch_faiss_store_import(monkeypatch, error)

    container = Container(settings=_Settings(), stub=True)

    assert container.faiss_registry is None


@pytest.mark.parametrize(
    "error",
    [
        ModuleNotFoundError("No module named 'numpy'", name="numpy"),
        ImportError("FAISS DLL load failed"),
    ],
)
def test_container_reraises_non_faiss_import_failures(monkeypatch, error) -> None:
    _patch_faiss_store_import(monkeypatch, error)
    container = Container(settings=_Settings(), stub=True)

    with pytest.raises(type(error), match=str(error)):
        _ = container.faiss_registry
