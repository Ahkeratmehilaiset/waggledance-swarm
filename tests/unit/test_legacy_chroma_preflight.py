"""Legacy HiveMind (``main.py``) chroma preflight — fail closed when absent.

Reproduces the release-deps source-review finding: ``core.memory_engine`` and
``core.agent_levels`` import ``chromadb`` lazily inside their constructors, so
the ``from core.memory_engine import Consciousness`` import guard in
``HiveMind.start`` succeeds without the package and the ModuleNotFoundError
surfaced only at construction — inside the ``except Exception`` block that
sets ``self.consciousness = None`` — i.e. the hive silently started without
memory. The fix is an explicit preflight (``HiveMind._legacy_memory_preflight``
→ ``require_legacy_chroma``) that runs OUTSIDE that catch-and-continue block.

None of these tests start HiveMind services, touch the network, import the
real chromadb, or install anything: package presence/absence is simulated
through ``sys.modules`` entries scoped to each test by ``monkeypatch``.
"""

import importlib
import importlib.machinery
import importlib.util
import inspect
import sys
import types
from pathlib import Path

import pytest

import hivemind
from hivemind import (
    LEGACY_CHROMA_PACKAGE,
    HiveMind,
    legacy_chroma_missing_message,
    require_legacy_chroma,
)

_HAS_CHROMADB = importlib.util.find_spec("chromadb") is not None

# The hint must be checkout-bound: an editable install of *this* checkout
# with the opt-in extra, never a bare ``pip install chromadb``.
HINT_RE = r'pip install -e "[^"]+\[chroma\]"'


def _chroma_absent(monkeypatch):
    """Make ``import chromadb`` raise ModuleNotFoundError for this test only.

    ``sys.modules[name] = None`` is the documented way to block an import;
    ``importlib.util.find_spec`` returns ``None`` for such an entry, so the
    preflight sees exactly what a plain CI environment (no ``[chroma]``
    extra) sees. Nothing is uninstalled; monkeypatch restores the entry.
    """
    monkeypatch.setitem(sys.modules, "chromadb", None)


def _chroma_present(monkeypatch):
    """Simulate an installed ``chromadb`` without importing the real one.

    A module object with a real ``ModuleSpec`` is enough for
    ``importlib.util.find_spec`` to report the package as importable, which
    is all the preflight consults. This keeps the present-branch test
    runnable in environments where chromadb is not installed (CI).
    """
    fake = types.ModuleType("chromadb")
    fake.__spec__ = importlib.machinery.ModuleSpec("chromadb", loader=None)
    monkeypatch.setitem(sys.modules, "chromadb", fake)


def _memory_engine_stub(monkeypatch):
    """Stand in for ``core.memory_engine`` so the preflight's own import
    succeeds without loading the real module; returns the sentinel class."""

    class Consciousness:  # sentinel — never instantiated
        pass

    stub = types.ModuleType("core.memory_engine")
    stub.Consciousness = Consciousness
    monkeypatch.setitem(sys.modules, "core.memory_engine", stub)
    return Consciousness


def _bare_hive() -> HiveMind:
    """A HiveMind instance without ``__init__`` (no config load, no I/O)."""
    return HiveMind.__new__(HiveMind)


# ── Reproduction: why the old import guard could not catch this ─────────


class TestReproduction:
    def test_import_guard_passes_without_chromadb(self, monkeypatch):
        """The guarded import that used to set ``_CONSCIOUSNESS_OK`` succeeds
        with chromadb absent, because the package is only imported lazily."""
        _chroma_absent(monkeypatch)
        mod = importlib.import_module("core.memory_engine")
        assert hasattr(mod, "Consciousness")

    def test_construction_is_where_the_missing_package_surfaced(
        self, monkeypatch, tmp_path
    ):
        """``MemoryStore`` (first chroma consumer inside ``Consciousness``) and
        ``AgentLevelManager`` both raise only at construction; in ``start()``
        that raise landed in ``except Exception`` → ``consciousness = None``."""
        _chroma_absent(monkeypatch)
        from core.agent_levels import AgentLevelManager
        from core.memory_engine import MemoryStore

        with pytest.raises(ModuleNotFoundError, match="chromadb"):
            MemoryStore(path=str(tmp_path / "chroma_db"))
        with pytest.raises(ModuleNotFoundError, match="chromadb"):
            AgentLevelManager(db_path=str(tmp_path / "chroma_db"))
        # The lazy import is the first statement, so nothing was created.
        assert not (tmp_path / "chroma_db").exists()


# ── Helper: require_legacy_chroma / legacy_chroma_missing_message ───────


class TestRequireLegacyChroma:
    def test_absent_package_raises_with_checkout_bound_hint(self, monkeypatch):
        _chroma_absent(monkeypatch)
        with pytest.raises(RuntimeError, match=HINT_RE) as excinfo:
            require_legacy_chroma()
        msg = str(excinfo.value)
        checkout = Path(hivemind.__file__).resolve().parent
        assert f"'{LEGACY_CHROMA_PACKAGE}'" in msg
        assert "not installed" in msg
        assert f'"{checkout}[chroma]"' in msg
        assert '".[chroma]"' in msg
        # Explicit non-goals of the fix, stated to the operator.
        assert "Nothing is installed automatically" in msg
        assert "no alternative backend" in msg
        # Never a bare, non-checkout-bound install of the pinned-out package.
        assert "pip install chromadb" not in msg
        # The check must not have imported anything.
        assert sys.modules["chromadb"] is None

    def test_present_package_is_a_no_op(self, monkeypatch):
        _chroma_present(monkeypatch)
        assert require_legacy_chroma() is None

    def test_explicit_checkout_is_bound_into_the_hint(self, monkeypatch, tmp_path):
        _chroma_absent(monkeypatch)
        with pytest.raises(RuntimeError) as excinfo:
            require_legacy_chroma(checkout=tmp_path)
        assert f'pip install -e "{tmp_path}[chroma]"' in str(excinfo.value)
        assert legacy_chroma_missing_message(tmp_path) == str(excinfo.value)

    def test_default_checkout_actually_defines_the_extra(self):
        """The hint points at the directory holding hivemind.py; that checkout
        must really declare the ``chroma`` extra, or the hint would be false."""
        checkout = Path(hivemind.__file__).resolve().parent
        assert checkout == Path(__file__).resolve().parents[2]
        pyproject = (checkout / "pyproject.toml").read_text(encoding="utf-8")
        assert "chroma = [" in pyproject
        assert f'"{checkout}[chroma]"' in legacy_chroma_missing_message()

    @pytest.mark.skipif(
        not _HAS_CHROMADB,
        reason="chromadb is an optional [chroma] extra; real-present path",
    )
    def test_real_installed_package_is_a_no_op(self):
        assert require_legacy_chroma() is None


# ── HiveMind._legacy_memory_preflight: all branches, no services ────────


class TestLegacyMemoryPreflight:
    def test_disabled_path_is_preserved_even_without_chromadb(self, monkeypatch):
        """``core.memory_engine`` not importable ⇒ legacy memory disabled ⇒
        ``None`` (the previous ``_CONSCIOUSNESS_OK = False`` outcome) and no
        chroma check at all — an absent package must not matter here."""
        _chroma_absent(monkeypatch)
        monkeypatch.setitem(sys.modules, "core.memory_engine", None)
        assert _bare_hive()._legacy_memory_preflight() is None

    def test_enabled_path_without_chromadb_fails_closed(self, monkeypatch):
        _chroma_absent(monkeypatch)
        _memory_engine_stub(monkeypatch)
        with pytest.raises(RuntimeError, match=HINT_RE):
            _bare_hive()._legacy_memory_preflight()

    def test_enabled_path_with_chromadb_returns_consciousness_class(
        self, monkeypatch
    ):
        _chroma_present(monkeypatch)
        sentinel = _memory_engine_stub(monkeypatch)
        assert _bare_hive()._legacy_memory_preflight() is sentinel

    @pytest.mark.skipif(
        not _HAS_CHROMADB,
        reason="chromadb is an optional [chroma] extra; real-present path",
    )
    def test_real_modules_with_installed_package_are_unchanged(self):
        from core.memory_engine import Consciousness

        assert _bare_hive()._legacy_memory_preflight() is Consciousness


# ── Call site in HiveMind.start: outside the catch-and-continue block ───


class TestStartCallSite:
    def test_preflight_runs_before_the_consciousness_try_block(self):
        src = inspect.getsource(HiveMind.start)
        marker = src.index("Tietoisuuskerros v2")
        call = src.index("Consciousness = self._legacy_memory_preflight()", marker)
        construct = src.index("self.consciousness = Consciousness(", call)
        # No try: between the section marker and the preflight call — the
        # RuntimeError must propagate out of start(), never be swallowed.
        assert "try:" not in src[marker:call]
        # Exactly the one guarded block sits between preflight and construction.
        assert src[call:construct].count("try:") == 1
        assert "if Consciousness is not None:" in src[call:construct]

    def test_old_import_flag_guard_is_gone(self):
        """The ``_CONSCIOUSNESS_OK`` flag only proved the module imported,
        which is exactly what could not detect a missing chromadb."""
        assert "_CONSCIOUSNESS_OK" not in inspect.getsource(HiveMind.start)
