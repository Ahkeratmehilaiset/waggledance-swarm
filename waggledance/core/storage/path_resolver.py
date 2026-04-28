# SPDX-License-Identifier: BUSL-1.1
# BUSL-Change-Date: 2030-12-31
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
# See LICENSE-BUSL.txt and LICENSE-CORE.md
"""Path resolver — single seam for runtime storage path resolution.

Today the runtime hard-codes the FAISS root via
``core/faiss_store.py:26 _DEFAULT_FAISS_DIR = data/faiss``. Anything that
reads or writes vector indices, model artifacts, audit databases, or
event logs reaches through that constant or its own copy of the
hard-coded relative path.

The :class:`PathResolver` is the substrate the runtime should grow into.
It exposes a deterministic, override-aware translation from a *logical*
name (``faiss_root``, ``audit_log_db``, ``vector_event_log``, …) to a
concrete :class:`pathlib.Path`. Behind the scenes it consults:

1. an explicit override registered via :py:meth:`register_override`,
2. an active binding in
   :py:meth:`waggledance.core.storage.control_plane.ControlPlaneDB.get_active_runtime_path`,
3. the static built-in default for that :class:`LogicalPathKind`.

The resolver does **not** move data. It does not rewrite legacy code.
It is the seam a future MODEL_B-style cutover can write to without
shipping a runtime code change — once subsystems ask the resolver for
their root, flipping a binding flips the resolution.

Drop-in compatibility is the design rule. With no overrides and no
control-plane bindings the resolver returns exactly what
``_DEFAULT_FAISS_DIR`` and friends return today.
"""

from __future__ import annotations

import enum
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from .control_plane import ControlPlaneDB


class PathResolverError(RuntimeError):
    """Raised when a logical path cannot be resolved or is misconfigured."""


class LogicalPathKind(str, enum.Enum):
    """Logical names recognised by the resolver.

    These are coarse-grained substrate roots, not fine-grained shard
    paths. Sharded paths derive from a root via the ``derive`` helper.
    """

    FAISS_ROOT = "faiss_root"
    VECTOR_STAGING_ROOT = "vector_staging_root"
    VECTOR_DELTA_LEDGER_ROOT = "vector_delta_ledger_root"
    VECTOR_ROOT = "vector_root"
    VECTOR_EVENT_LOG = "vector_event_log"
    AUDIT_LOG_DB = "audit_log_db"
    LEARNING_LEDGER = "learning_ledger"
    CONTROL_PLANE_DB = "control_plane_db"
    MODEL_STORE_ROOT = "model_store_root"
    BUILDER_WORKTREE_ROOT = "builder_worktree_root"


# Static defaults match the legacy hard-coded values so that an
# unconfigured PathResolver returns exactly today's runtime layout.
_DEFAULT_RELATIVE_PATHS: Dict[LogicalPathKind, str] = {
    LogicalPathKind.FAISS_ROOT: "data/faiss",
    LogicalPathKind.VECTOR_STAGING_ROOT: "data/faiss_staging",
    LogicalPathKind.VECTOR_DELTA_LEDGER_ROOT: "data/faiss_delta_ledger",
    LogicalPathKind.VECTOR_ROOT: "data/vector",
    LogicalPathKind.VECTOR_EVENT_LOG: "data/vector/events.jsonl",
    LogicalPathKind.AUDIT_LOG_DB: "data/audit_log.db",
    LogicalPathKind.LEARNING_LEDGER: "data/learning_ledger.jsonl",
    LogicalPathKind.CONTROL_PLANE_DB: "data/control_plane.db",
    LogicalPathKind.MODEL_STORE_ROOT: "data/models",
    LogicalPathKind.BUILDER_WORKTREE_ROOT: "data/builder_worktrees",
}


@dataclass(frozen=True)
class ResolvedPath:
    """Result of a successful path resolution.

    ``source`` is one of ``"override"``, ``"control_plane"``, or
    ``"default"`` — useful for telemetry, Reality View display, and
    debugging.
    """

    kind: LogicalPathKind
    path: Path
    source: str
    logical_name: str

    @property
    def is_default(self) -> bool:
        return self.source == "default"


class PathResolver:
    """Deterministic resolver for runtime storage paths."""

    def __init__(
        self,
        *,
        repo_root: Optional[Path] = None,
        control_plane: Optional[ControlPlaneDB] = None,
    ) -> None:
        self._repo_root: Path = (Path(repo_root) if repo_root else _detect_repo_root()).resolve()
        self._control_plane = control_plane
        self._overrides: Dict[Tuple[LogicalPathKind, str], Path] = {}
        self._lock = threading.RLock()

    @property
    def repo_root(self) -> Path:
        return self._repo_root

    def attach_control_plane(self, control_plane: ControlPlaneDB) -> None:
        """Bind a control-plane database after construction (DI helper)."""

        with self._lock:
            self._control_plane = control_plane

    def register_override(
        self,
        kind: LogicalPathKind,
        path: Path | str,
        *,
        logical_name: str = "default",
    ) -> None:
        """Pin an in-memory override for tests or operator overrides."""

        with self._lock:
            self._overrides[(kind, logical_name)] = Path(path)

    def clear_overrides(self) -> None:
        with self._lock:
            self._overrides.clear()

    def resolve(
        self,
        kind: LogicalPathKind,
        *,
        logical_name: str = "default",
        ensure_parent: bool = False,
    ) -> ResolvedPath:
        if not isinstance(kind, LogicalPathKind):
            raise PathResolverError(f"unknown LogicalPathKind: {kind!r}")
        with self._lock:
            override = self._overrides.get((kind, logical_name))
            if override is not None:
                resolved = self._absolutize(override)
                if ensure_parent:
                    resolved.parent.mkdir(parents=True, exist_ok=True)
                return ResolvedPath(
                    kind=kind, path=resolved, source="override", logical_name=logical_name,
                )
            if self._control_plane is not None:
                binding = self._control_plane.get_active_runtime_path(
                    logical_name=logical_name, path_kind=kind.value,
                )
                if binding is not None:
                    resolved = self._absolutize(Path(binding.physical_path))
                    if ensure_parent:
                        resolved.parent.mkdir(parents=True, exist_ok=True)
                    return ResolvedPath(
                        kind=kind, path=resolved, source="control_plane", logical_name=logical_name,
                    )
            default_rel = _DEFAULT_RELATIVE_PATHS.get(kind)
            if default_rel is None:
                raise PathResolverError(f"no default registered for {kind!r}")
            resolved = self._absolutize(Path(default_rel))
            if ensure_parent:
                resolved.parent.mkdir(parents=True, exist_ok=True)
            return ResolvedPath(
                kind=kind, path=resolved, source="default", logical_name=logical_name,
            )

    def derive(
        self,
        kind: LogicalPathKind,
        *parts: str,
        logical_name: str = "default",
    ) -> ResolvedPath:
        """Resolve a root and append further path components.

        Convenience for things like ``resolver.derive(VECTOR_ROOT,
        "cell_3", "index.faiss")``.
        """

        base = self.resolve(kind, logical_name=logical_name)
        return ResolvedPath(
            kind=base.kind,
            path=base.path.joinpath(*parts),
            source=base.source,
            logical_name=base.logical_name,
        )

    def snapshot(self) -> Dict[str, ResolvedPath]:
        """Return a dict of all known logical paths and their current resolution."""

        out: Dict[str, ResolvedPath] = {}
        for kind in LogicalPathKind:
            out[kind.value] = self.resolve(kind)
        return out

    def _absolutize(self, path: Path) -> Path:
        if path.is_absolute():
            return path
        return (self._repo_root / path).resolve()


def _detect_repo_root() -> Path:
    """Walk upward from this file to find the repository root.

    The marker is the presence of ``LICENSE-CORE.md``. Falls back to the
    current working directory if that is not found within four levels —
    this keeps tests and ad-hoc scripts working without a checkout.
    """

    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "LICENSE-CORE.md").is_file():
            return parent
    return Path.cwd().resolve()
