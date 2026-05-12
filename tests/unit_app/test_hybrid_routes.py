# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from types import SimpleNamespace

import pytest

from waggledance.adapters.http.routes.hybrid import _collection_count


class LegacyRegistry:
    def __init__(self):
        self.created: list[str] = []

    def get_or_create(self, name: str):
        self.created.append(name)
        return SimpleNamespace(count=0)


def test_collection_count_uses_count_if_exists_without_creating() -> None:
    class Registry:
        def count_if_exists(self, name: str) -> int:
            assert name == "cell_hub"
            return 7

    assert _collection_count(Registry(), "cell_hub") == 7


def test_collection_count_uses_get_existing_without_creating() -> None:
    class Registry:
        def get_existing(self, name: str):
            assert name == "cell_factory"
            return SimpleNamespace(count=3)

    assert _collection_count(Registry(), "cell_factory") == 3


def test_collection_count_missing_collection_is_zero_with_get_existing() -> None:
    class Registry:
        def get_existing(self, name: str):
            assert name == "cell_bee_ops"
            return None

    assert _collection_count(Registry(), "cell_bee_ops") == 0


def test_collection_count_does_not_create_missing_collection() -> None:
    registry = LegacyRegistry()

    with pytest.raises(AttributeError, match="refusing get_or_create on read path"):
        _collection_count(registry, "cell_hub")

    assert registry.created == []
