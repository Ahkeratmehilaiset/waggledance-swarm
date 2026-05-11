# SPDX-License-Identifier: BUSL-1.1
"""Audit H38 regression — DB paths default under data/ not project root.

Before this fix, ``WaggleSettings.db_path`` defaulted to
``./shared_memory.db`` (project root), so ``shared_memory.db`` and
the trust_store derivative landed alongside source code instead of
in the operational ``data/`` directory. Tests pin the new defaults
so a future refactor can't quietly regress to the root location.
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath

import pytest

from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.adapters.memory.sqlite_shared_memory import SQLiteSharedMemory


class TestSettingsDbPathDefault:
    def test_dataclass_default_is_under_data(self):
        # Construct without arguments (skipping from_env) so we see the
        # raw dataclass default.
        s = WaggleSettings()
        # Path separator is platform-specific; check POSIX-normalized.
        assert PurePosixPath(s.db_path.replace("\\", "/")).parts[0] == "data"
        assert s.db_path.endswith("shared_memory.db")

    def test_from_env_unset_defaults_under_data(self, monkeypatch):
        # Strip the env var so we observe the unset default. Also
        # strip WAGGLE_PROFILE so it doesn't fail H30 profile validation.
        monkeypatch.delenv("WAGGLE_DB_PATH", raising=False)
        monkeypatch.setenv("WAGGLE_PROFILE", "HOME")
        s = WaggleSettings.from_env()
        assert "data" in s.db_path.replace("\\", "/").split("/")
        assert s.db_path.endswith("shared_memory.db")

    def test_env_var_override_still_works(self, monkeypatch):
        monkeypatch.setenv("WAGGLE_DB_PATH", "/custom/path/foo.db")
        monkeypatch.setenv("WAGGLE_PROFILE", "HOME")
        s = WaggleSettings.from_env()
        # Custom path preserved verbatim (operators retain control).
        assert s.db_path == "/custom/path/foo.db"


class TestSqliteSharedMemoryDefault:
    def test_default_path_under_data(self):
        store = SQLiteSharedMemory()
        assert "data" in store._db_path.replace("\\", "/").split("/")

    def test_explicit_path_preserved(self):
        store = SQLiteSharedMemory(db_path="/custom/location/foo.db")
        assert store._db_path == "/custom/location/foo.db"
