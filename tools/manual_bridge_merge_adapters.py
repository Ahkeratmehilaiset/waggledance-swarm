# SPDX-License-Identifier: BUSL-1.1
"""Read-only evidence adapters for the manual (a)-class merge route (MANUAL-A, G3a) -- RED stub.

Test-first checkpoint: the public surface exists so the committed adapter tests
collect, but every adapter refuses with ``NotImplementedError``.  The GREEN
commit replaces this stub.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.manual_bridge_merge_statement import RunResult  # noqa: E402

ADAPTERS_SCHEMA = "wd.manual-merge-a.g3a-adapters.v1"
CHECK_PASS = "pass"
CHECK_REFUSE = "refuse"
CHECK_UNKNOWN = "unknown"
VERDICT_REFUSED = "refused"
VERDICT_UNKNOWN = "unknown"
ADMIT_AVAILABLE = False
PROVENANCE_LOCAL_READ = "local_read"
PROVENANCE_UNIT_MOCK = "unit_mock"
REGISTRY_PATH = "configs/bridge_identity_registry.json"
PINNED_SOURCE_PATHS: tuple[str, ...] = (
    "tools/__init__.py",
    "tools/check_bridge_changes_requested.py",
    "tools/bridge_event_taxonomy.py",
    "tools/bridge_accepted_queue_preflight.py",
    "tools/bridge_named_mutex.py",
    "waggledance/__init__.py",
    "waggledance/core/__init__.py",
    "waggledance/core/bridge_identity_registry.py",
    "waggledance/core/bridge_log_reader.py",
    "waggledance/core/bridge_resource_scope.py",
    "waggledance/core/work_queue.py",
)
TRUST_GAPS: tuple[tuple[str, str], ...] = (
    ("session_identity", "no_trusted_session_source"),
    ("bridge_origin_authentication", "observed_content_is_not_origin"),
    ("concept_lineage", "no_concept_lineage_source"),
    ("independent_rco_exact_head", "not_assessed_in_g3a"),
    ("exact_head_ci", "not_assessed_in_g3a"),
)
LOCAL_CLEAR_STATUSES = frozenset({"changes_requested_retracted", "changes_requested_withdrawn"})
SNAPSHOT_MAX_ROWS = 10_000


class AdapterError(ValueError):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class SourceOriginEvidence:
    trusted_commit: str
    matches: tuple[tuple[str, str], ...]
    provenance: str


@dataclass(frozen=True)
class RegistryEvidence:
    trusted_commit: str
    identities: Mapping[str, str]
    blob_sha1: str
    provenance: str


@dataclass(frozen=True)
class SnapshotEvidence:
    events: tuple[Mapping[str, Any], ...]
    file_identity: str
    generation: str | None
    snapshot_length: int
    read_calls: int
    reserialized_rows_sha256: str
    provenance: str


def _subprocess_runner(argv: Sequence[str], *, input_bytes: bytes | None, timeout: float, env: Mapping[str, str] | None) -> RunResult:
    completed = subprocess.run(list(argv), input=input_bytes, capture_output=True, timeout=timeout, env=env, shell=False, check=False)  # noqa: S603
    return RunResult(completed.returncode, completed.stdout, completed.stderr)


def _red(*_args: Any, **_kwargs: Any) -> Any:
    raise NotImplementedError("G3a RED stub")


is_issued = counts_as_local_read = require_read_only_git = verify_trusted_sources = _red
load_trusted_registry = read_bridge_snapshot = in_negative_scope = local_withdrawal_state = _red
assess_controls = assess_g3a = _red
