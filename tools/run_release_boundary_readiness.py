#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record release-boundary readiness without performing release actions.

This tool is deliberately read-only with respect to the release boundary. It
can observe that the release gate and operator decision packs are in place, but
it never creates a tag, moves a Docker alias, claims stable release, or changes
external authority. Finalization remains operator-only.

Readiness is granted only by the live canonical release gate, evaluated by an
isolated child against immutable inputs. The soak subject commit must be an
ancestor of exact clean HEAD, and their tree delta must contain exactly the
canonical soak-evidence carrier path. Canonical inputs and Git state are bound
before and after evaluation. The --release-gate-recheck and
--phase-synthesis-refresh inputs are continuity lineage only: they can add
blockers but can never grant readiness. Production readiness additionally
requires an isolated parent interpreter (``python -I``).
"""
from __future__ import annotations

# A direct-file invocation starts with ROOT/tools at sys.path[0].  Remove that
# and every ambient import path before importing any shadowable module.  Only
# builtin ``sys`` is needed for this bootstrap; imported-package use is
# intentionally left untouched and later forced to HOLD by the production
# wrapper.
import sys as _bootstrap_sys

_BOOTSTRAP_TOOLS_PRELOADED = "tools" in _bootstrap_sys.modules
_PARENT_ISOLATED = bool(_bootstrap_sys.flags.isolated)
_PARENT_NO_SITE = bool(_bootstrap_sys.flags.no_site)
if not _BOOTSTRAP_TOOLS_PRELOADED and not _PARENT_NO_SITE:
    _bootstrap_sys.stderr.write("parent_python_site_enabled\n")
    raise SystemExit(2)
if not _BOOTSTRAP_TOOLS_PRELOADED:
    # ``-I`` still imports the interpreter's site configuration before this
    # file runs.  Discard any finder, path-hook, or importer-cache additions
    # before the first shadowable import.  The retained objects are CPython's
    # frozen bootstrap finders/hooks; below, once importlib is available, they
    # are replaced by the exact canonical objects rather than trusted by name.
    _bootstrap_sys.meta_path[:] = [
        finder
        for finder in _bootstrap_sys.meta_path
        if getattr(finder, "__module__", None)
        in {"_frozen_importlib", "_frozen_importlib_external"}
    ]
    _bootstrap_sys.path_hooks[:] = [
        hook
        for hook in _bootstrap_sys.path_hooks
        if getattr(hook, "__module__", None)
        in {"zipimport", "_frozen_importlib_external"}
    ]
    _bootstrap_sys.path_importer_cache.clear()
    _bootstrap_entry = (
        _bootstrap_sys.path[0]
        if _bootstrap_sys.path and not _PARENT_ISOLATED
        else None
    )
    _bootstrap_roots = tuple(
        str(value).replace("\\", "/").casefold().rstrip("/")
        for value in {
            _bootstrap_sys.prefix,
            _bootstrap_sys.base_prefix,
            _bootstrap_sys.exec_prefix,
            _bootstrap_sys.base_exec_prefix,
        }
        if value
    )

    def _bootstrap_path_allowed(value: object) -> bool:
        key = str(value).replace("\\", "/").casefold().rstrip("/")
        entry_key = (
            str(_bootstrap_entry).replace("\\", "/").casefold().rstrip("/")
            if _bootstrap_entry is not None
            else None
        )
        return key != entry_key and any(
            key == root or key.startswith(root + "/") for root in _bootstrap_roots
        )

    _bootstrap_sys.path[:] = [
        entry for entry in _bootstrap_sys.path if _bootstrap_path_allowed(entry)
    ]

import argparse
import base64
import datetime as dt
import hashlib
import importlib
import importlib.machinery
import importlib.metadata
import importlib.util
import json
import math
import os
import re
import stat
import subprocess
import sys
import sysconfig
import unicodedata
import uuid
import zipimport
import zlib
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
_TOOLS_PACKAGE_PRELOADED = _BOOTSTRAP_TOOLS_PRELOADED
_YAML_PRELOADED = any(
    name == "yaml" or name.startswith("yaml.") for name in sys.modules
)
if not _TOOLS_PACKAGE_PRELOADED:
    sys.path[:] = [
        entry
        for entry in sys.path
        if not Path(entry or os.curdir).resolve().is_relative_to(ROOT)
    ]
    sys.meta_path[:] = [
        importlib.machinery.BuiltinImporter,
        importlib.machinery.FrozenImporter,
        importlib.machinery.PathFinder,
    ]
    sys.path_hooks[:] = [
        zipimport.zipimporter,
        importlib.machinery.FileFinder.path_hook(
            (importlib.machinery.ExtensionFileLoader,
             importlib.machinery.EXTENSION_SUFFIXES),
            (importlib.machinery.SourceFileLoader,
             importlib.machinery.SOURCE_SUFFIXES),
            (importlib.machinery.SourcelessFileLoader,
             importlib.machinery.BYTECODE_SUFFIXES),
        ),
    ]
    sys.path_importer_cache.clear()
_YAML_SHADOW_PATHS = (
    ROOT / "yaml",
    *(
        ROOT / f"yaml{suffix}"
        for suffix in dict.fromkeys(importlib.machinery.all_suffixes())
    ),
)
PYYAML_VERSION = "6.0.2"
PYYAML_SDIST_SHA256 = (
    "d584d9ec91ad65861cc08d42e834324ef890a082e591037abe114850ff7bbc3e"
)
PYYAML_SOURCE_MANIFEST = (
    ("yaml/__init__.py", 12311,
     "377e52d351cc7ac1537b469144c5a43e3d0f6bc2046c7a44f452bb72be4176dc"),
    ("yaml/composer.py", 4883,
     "fcaa37d16afa783594794a5ab94193dcb720f503c19ce3d59539c8311189f453"),
    ("yaml/constructor.py", 28639,
     "90d8247da78b524c10618fd0e857f54f3d97570fe91b5c5513d024ef3faf88b0"),
    ("yaml/cyaml.py", 3851,
     "e99ac01bd7c062f7557b614aff0d21997a06ed962ca185306a91bc0a20bbd87d"),
    ("yaml/dumper.py", 2837,
     "3cb72d66563064ba7b5e679477046ebf89d8399d940670c8532f3e94a7cb17ea"),
    ("yaml/emitter.py", 43006,
     "8e086d694ede170837d5b1b407b45979aff6f40762f422a65eafd08e04290a44"),
    ("yaml/error.py", 2533,
     "021f73fada072546c4f63f8cf18a7181244ce4280b09cc15cc980b2d1176171a"),
    ("yaml/events.py", 2445,
     "e74fd392c810884e2ea7e94aa3f57e9c1cbeb402319083d0c58e6a0e1282787c"),
    ("yaml/loader.py", 2061,
     "5156becc8aa6905482218abf3e04869b835226db4763645fff3438fdbd5f1cdd"),
    ("yaml/nodes.py", 1440,
     "80f28d8fca4a09d87677882bde021820d9cf39a3b11a12405226211919cf13ce"),
    ("yaml/parser.py", 25495,
     "8a55a9e6fbe0a07146cef3990c8b45a068c3e83e369e1959ad9ca30306b4a09a"),
    ("yaml/reader.py", 6794,
     "d1d9b38ab3a20c6e17a38d519ee412ecaf6b918df18c78956ac7c330d4ea08dc"),
    ("yaml/representer.py", 14190,
     "22e58ff9c016f6c1ca1274b4802a926bcf78935060e1c813c5a0f021c6d143e6"),
    ("yaml/resolver.py", 9004,
     "f4bf9561f9b89961f1503d558385fbae30d12bfed565de9bf76c33abb63620a6"),
    ("yaml/scanner.py", 51279,
     "60433788b652690c17710460da5d91e0c753d3318fd85f5e1e42862a71f25906"),
    ("yaml/serializer.py", 4165,
     "0a1b85826854d35863e31808f0668abfabdf33606e8f06bd8bb7761401e3edc0"),
    ("yaml/tokens.py", 2573,
     "953408cd2570f0c83dc2fe39f7e4e388e41eeb05738aa69196a5f6ffcf6ba79e"),
)
PYYAML_EXECUTABLE_MODULES = tuple(
    "yaml" if relative == "yaml/__init__.py" else f"yaml.{Path(relative).stem}"
    for relative, _, _ in PYYAML_SOURCE_MANIFEST
    if relative != "yaml/cyaml.py"
)
_PYYAML_AUTHORITY = {
    relative: (size, digest)
    for relative, size, digest in PYYAML_SOURCE_MANIFEST
}
_PYYAML_CHILD_BUNDLE_SCHEMA = "waggledance.trusted_pyyaml_bundle.v1"


def _runtime_reparse(info: os.stat_result) -> bool:
    return bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _runtime_path_error(path: Path, anchor: Path, *, directory: bool) -> str | None:
    """Validate a lexical runtime path without following links or reparses."""
    path = Path(os.path.abspath(path))
    anchor = Path(os.path.abspath(anchor))
    try:
        relative = os.path.relpath(path, anchor)
    except (OSError, ValueError):
        return "outside_runtime_root"
    parts = () if relative == os.curdir else Path(relative).parts
    if any(part in (os.curdir, os.pardir) for part in parts):
        return "outside_runtime_root"
    components = [anchor]
    for part in parts:
        components.append(components[-1] / part)
    try:
        for index, component in enumerate(components):
            info = os.lstat(component)
            if stat.S_ISLNK(info.st_mode) or _runtime_reparse(info):
                return "runtime_path_reparse"
            expected_directory = index < len(components) - 1 or directory
            if expected_directory and not stat.S_ISDIR(info.st_mode):
                return "runtime_path_not_directory"
            if not expected_directory and not stat.S_ISREG(info.st_mode):
                return "runtime_path_not_regular"
    except OSError:
        return "runtime_path_unavailable"
    return None


def _runtime_file_bytes(path: Path, anchor: Path) -> bytes:
    error = _runtime_path_error(path, anchor, directory=False)
    if error:
        raise OSError(error)
    before = os.lstat(path)
    with open(path, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size) != (
            before.st_dev, before.st_ino, before.st_mode, before.st_size
        ):
            raise OSError("runtime_file_changed")
        raw = handle.read()
        after_read = os.fstat(handle.fileno())
    after_path = os.lstat(path)
    identity = lambda value: (
        value.st_dev, value.st_ino, value.st_mode, value.st_size,
        value.st_mtime_ns,
    )
    if identity(opened) != identity(after_read) or identity(opened) != identity(after_path):
        raise OSError("runtime_file_changed")
    return raw


def _site_roots(root: Path, scheme: str) -> tuple[Path, ...]:
    variables = {
        "base": str(root),
        "platbase": str(root),
        "installed_base": str(root),
        "installed_platbase": str(root),
    }
    paths = sysconfig.get_paths(scheme=scheme, vars=variables)
    return tuple(dict.fromkeys(
        Path(value) for key in ("purelib", "platlib")
        if (value := paths.get(key))
    ))


def _pyvenv_configuration() -> tuple[Path | None, bool | None, str | None]:
    try:
        executable = Path(sys.executable)
        if not executable.is_absolute():
            return None, None, "trusted_pyyaml_executable_not_absolute"
        root = Path(os.path.abspath(executable)).parent.parent
        config_path = root / "pyvenv.cfg"
        try:
            raw = _runtime_file_bytes(config_path, root)
        except FileNotFoundError:
            return None, None, None
        except OSError:
            if not os.path.lexists(config_path):
                return None, None, None
            return None, None, "trusted_pyyaml_venv_config_unverifiable"
        values: dict[str, str] = {}
        for source_line in raw.decode("utf-8").splitlines():
            line = source_line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            normalized = key.strip().casefold()
            if separator != "=" or not normalized or normalized in values:
                return None, None, "trusted_pyyaml_venv_config_malformed"
            values[normalized] = value.strip()
        include = values.get("include-system-site-packages", "").casefold()
        if include not in {"true", "false"}:
            return None, None, "trusted_pyyaml_venv_config_malformed"
        return root, include == "true", None
    except (OSError, UnicodeError, ValueError):
        return None, None, "trusted_pyyaml_venv_config_unverifiable"


def _pyyaml_distribution_at(
    site_root: Path,
    *,
    runtime_root: Path,
) -> tuple[dict[str, Any] | None, bool, str | None]:
    yaml_dir = site_root / "yaml"
    artifact_present = os.path.lexists(yaml_dir) or os.path.lexists(site_root / "yaml.py")
    if not site_root.exists():
        if os.path.lexists(site_root):
            return None, artifact_present, "trusted_pyyaml_site_root_unverifiable"
        return None, artifact_present, None
    if _runtime_path_error(site_root, runtime_root, directory=True):
        return None, artifact_present, "trusted_pyyaml_site_root_unverifiable"
    try:
        distributions = list(importlib.metadata.Distribution.discover(
            name="PyYAML", path=[str(site_root)]
        ))
    except Exception:  # noqa: BLE001 - malformed metadata must HOLD
        return None, artifact_present, "trusted_pyyaml_metadata_unverifiable"
    try:
        matches = [
            distribution for distribution in distributions
            if str(distribution.metadata.get("Name", "")).replace("-", "")
            .replace("_", "").replace(".", "").casefold() == "pyyaml"
        ]
    except Exception:  # noqa: BLE001 - metadata is corroborative but mandatory
        return None, artifact_present, "trusted_pyyaml_metadata_unverifiable"
    if len(matches) != 1:
        blocker = (
            "trusted_pyyaml_distribution_ambiguous"
            if len(matches) > 1 or artifact_present
            else None
        )
        return None, artifact_present, blocker
    distribution = matches[0]
    try:
        version = str(distribution.version)
    except Exception:  # noqa: BLE001 - malformed metadata must HOLD
        return None, True, "trusted_pyyaml_metadata_unverifiable"
    if version != PYYAML_VERSION:
        return None, True, "trusted_pyyaml_version_unpinned"
    try:
        files = distribution.files
    except Exception:  # noqa: BLE001 - malformed RECORD must HOLD
        return None, True, "trusted_pyyaml_record_unverifiable"
    if files is None:
        return None, True, "trusted_pyyaml_record_missing"
    recorded_items = [str(item).replace("\\", "/") for item in files]
    folded_records = [item.casefold() for item in recorded_items]
    if (
        len(recorded_items) != len(set(recorded_items))
        or len(folded_records) != len(set(folded_records))
        or any(
            not item
            or item.startswith("/")
            or ":" in item.split("/", 1)[0]
            or any(part in {"", ".", ".."} for part in item.split("/"))
            for item in recorded_items
        )
    ):
        return None, True, "trusted_pyyaml_record_malformed"
    recorded = set(recorded_items)
    source_records = {
        item for item in recorded if item.startswith("yaml/") and item.endswith(".py")
    }
    if source_records != set(_PYYAML_AUTHORITY):
        return None, True, "trusted_pyyaml_record_source_mismatch"
    metadata_records = {
        item for item in recorded
        if item.endswith(".dist-info/METADATA") or item.endswith(".dist-info/RECORD")
    }
    if len(metadata_records) != 2:
        return None, True, "trusted_pyyaml_record_malformed"
    try:
        on_disk_sources: set[str] = set()
        for item in yaml_dir.rglob("*"):
            info = os.lstat(item)
            if stat.S_ISLNK(info.st_mode) or _runtime_reparse(info):
                return None, True, "trusted_pyyaml_package_reparse"
            if stat.S_ISREG(info.st_mode) and item.name.endswith(".py"):
                on_disk_sources.add(item.relative_to(site_root).as_posix())
    except OSError:
        return None, True, "trusted_pyyaml_package_unverifiable"
    if on_disk_sources != set(_PYYAML_AUTHORITY):
        return None, True, "trusted_pyyaml_package_source_mismatch"
    source_items: list[tuple[str, bytes]] = []
    source_paths: list[tuple[str, str]] = []
    try:
        for relative, expected_size, expected_digest in PYYAML_SOURCE_MANIFEST:
            path = site_root / Path(relative)
            raw = _runtime_file_bytes(path, runtime_root)
            if (
                len(raw) != expected_size
                or hashlib.sha256(raw).hexdigest() != expected_digest
            ):
                return None, True, "trusted_pyyaml_source_changed"
            source_items.append((relative, raw))
            source_paths.append((relative, str(path)))
    except OSError:
        return None, True, "trusted_pyyaml_source_unverifiable"
    return {
        "site_root": str(site_root),
        "runtime_root": str(runtime_root),
        "package_dir": str(yaml_dir),
        "version": PYYAML_VERSION,
        "sdist_sha256": PYYAML_SDIST_SHA256,
        "source_items": tuple(source_items),
        "source_paths": tuple(source_paths),
        "metadata_records": tuple(sorted(metadata_records)),
    }, True, None


def _resolve_trusted_pyyaml() -> tuple[dict[str, Any] | None, str | None]:
    venv_root, include_base, config_blocker = _pyvenv_configuration()
    if config_blocker:
        return None, config_blocker
    try:
        if venv_root is not None:
            venv_scheme = "venv" if "venv" in sysconfig.get_scheme_names() else (
                "nt" if os.name == "nt" else "posix_prefix"
            )
            venv_matches = []
            venv_artifact = False
            for site_root in _site_roots(venv_root, venv_scheme):
                match, artifact, blocker = _pyyaml_distribution_at(
                    site_root, runtime_root=venv_root
                )
                if blocker:
                    return None, blocker
                venv_artifact = venv_artifact or artifact
                if match is not None:
                    venv_matches.append(match)
            if len(venv_matches) > 1:
                return None, "trusted_pyyaml_distribution_ambiguous"
            if venv_matches:
                return venv_matches[0], None
            if venv_artifact:
                return None, "trusted_pyyaml_venv_artifact_invalid"
            if include_base is not True:
                return None, "trusted_pyyaml_unavailable"

        base_root = Path(os.path.abspath(sys.base_prefix))
        base_scheme = "nt" if os.name == "nt" else "posix_prefix"
        base_matches = []
        base_artifact = False
        for site_root in _site_roots(base_root, base_scheme):
            match, artifact, blocker = _pyyaml_distribution_at(
                site_root, runtime_root=base_root
            )
            if blocker:
                return None, blocker
            base_artifact = base_artifact or artifact
            if match is not None:
                base_matches.append(match)
        if len(base_matches) != 1:
            return None, (
                "trusted_pyyaml_distribution_ambiguous"
                if len(base_matches) > 1 or base_artifact
                else "trusted_pyyaml_unavailable"
            )
        return base_matches[0], None
    except (OSError, TypeError, ValueError):
        return None, "trusted_pyyaml_resolution_failed"


_TRUSTED_PYYAML, _TRUSTED_PYYAML_BLOCKER = _resolve_trusted_pyyaml()

# Set the redirect before importing any repository module.  ``-B`` prevents
# writes but still permits ignored unchecked bytecode to be loaded; a fresh,
# non-existing prefix closes that path for both this parent and the live child.
_PYCACHE_ROOT = ROOT / ".codex-audit"
_PYCACHE_ROOT_IDENTITY: tuple[int, int, int] | None = None
try:
    _pycache_root_info = os.lstat(_PYCACHE_ROOT)
except FileNotFoundError:
    _PYCACHE_ROOT_SAFE = True
except OSError:
    _PYCACHE_ROOT_SAFE = False
else:
    _PYCACHE_ROOT_SAFE = stat.S_ISDIR(_pycache_root_info.st_mode) and not bool(
        getattr(_pycache_root_info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )
    if _PYCACHE_ROOT_SAFE:
        _PYCACHE_ROOT_IDENTITY = (
            _pycache_root_info.st_dev,
            _pycache_root_info.st_ino,
            _pycache_root_info.st_mode,
        )
_PYCACHE_PREFIX = _PYCACHE_ROOT / f"rba-{uuid.uuid4().hex}"
_PYCACHE_PREFIX_PREEXISTED = os.path.lexists(_PYCACHE_PREFIX)
sys.dont_write_bytecode = True
sys.pycache_prefix = str(_PYCACHE_PREFIX)
_DECISION_PACK_PRELOADED = "tools.operator_decision_pack" in sys.modules
_DECISION_PACK_MODULE: Any = None


SCHEMA_VERSION = "waggledance.release_boundary_readiness.v0"
DECISION_PACKET_SCHEMA_VERSION = "waggledance.release_boundary_decision_packet.v1"
HEAD_SOAK_BINDING_SCHEMA_VERSION = "waggledance.head_soak_binding.v1"
SPRINT_DIR = ROOT / "docs/runs/magma_100h_sprint_2026_05_26"
DEFAULT_PHASE_SYNTHESIS_REFRESH = SPRINT_DIR / "phase_synthesis_refresh.json"
DEFAULT_RELEASE_GATE_RECHECK = SPRINT_DIR / "release_gate_readonly_recheck.json"
DEFAULT_TORCH_DECISION_PACK = ROOT / "docs/operator_inbox/torch-cuda-vs-cpu.yaml"
DEFAULT_DOCKER_DECISION_PACK = ROOT / "docs/operator_inbox/docker-latest-promotion.yaml"
DEFAULT_OUTPUT = SPRINT_DIR / "release_boundary_readiness.json"

RELEASE_SOAK_TASK_ID = "release_soak_evidence_blocker_resolution"
FINALIZATION_TASK_ID = "operator_release_finalization_decision"
STRICT_BLOCKED_EXIT_CODE = 2

_FULL_HEX_SHA = re.compile(r"^[0-9a-f]{40}$")
LIVE_GATE_SCHEMA_VERSION = "waggledance.release_gate_readonly_recheck.v0"
PHASE_SYNTHESIS_SCHEMA_VERSION = "waggledance.magma_100h_phase_synthesis_refresh.v0"
PHASE_SYNTHESIS_SPRINT_ID = "magma-100h-sprint3-2026-05-26"
LIVE_GATE_SCRIPT = ROOT / "tools" / "run_release_gate_readonly_recheck.py"
CANONICAL_RELEASE_READINESS = ROOT / "docs/release/RELEASE_READINESS.md"
CANONICAL_SOAK_EVIDENCE = ROOT / "docs/runs/release_soak_evidence/v3.12.0.json"
CANONICAL_INPUTS = {
    "boundary_script": ROOT / "tools/run_release_boundary_readiness.py",
    "phase_synthesis_refresh": DEFAULT_PHASE_SYNTHESIS_REFRESH,
    "release_gate_recheck": DEFAULT_RELEASE_GATE_RECHECK,
    "torch_decision_pack": DEFAULT_TORCH_DECISION_PACK,
    "docker_decision_pack": DEFAULT_DOCKER_DECISION_PACK,
    "release_readiness": CANONICAL_RELEASE_READINESS,
    "soak_evidence": CANONICAL_SOAK_EVIDENCE,
    "tools_package": ROOT / "tools/__init__.py",
    "live_gate_script": LIVE_GATE_SCRIPT,
    "check_release_gate": ROOT / "tools/check_release_gate.py",
    "verify_release_soak_evidence": ROOT / "tools/verify_release_soak_evidence.py",
    "collect_soak_evidence": ROOT / "tools/collect_soak_evidence.py",
    "release_security_attestation": ROOT / "tools/release_security_attestation.py",
    "run_release_ci_status_evidence": ROOT / "tools/run_release_ci_status_evidence.py",
    "run_release_docker_policy_evidence": ROOT / "tools/run_release_docker_policy_evidence.py",
    "operator_decision_pack": ROOT / "tools/operator_decision_pack.py",
}
_CANONICAL_RELATIVE_PATHS = tuple(
    path.relative_to(ROOT).as_posix() for path in CANONICAL_INPUTS.values()
)
SOAK_EVIDENCE_CARRIER_PATH = CANONICAL_SOAK_EVIDENCE.relative_to(ROOT).as_posix()
TRACKED_REGULAR_MODES = {"100644", "100755"}

_LIVE_CHILD_BUNDLE_SCHEMA = "waggledance.release_boundary_live_child_bundle.v1"
_LIVE_CHILD_TEST_BUNDLE_SCHEMA = (
    "waggledance.release_boundary_live_child_test_bundle.v1"
)
_LIVE_CHILD_BUNDLE_DIGEST_DOMAIN = (
    b"waggledance.release_boundary_live_child_bundle.v1\0"
)
_LIVE_CHILD_MAX_FILE_BYTES = 16 * 1024 * 1024
_LIVE_CHILD_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_LIVE_CHILD_MAX_GIT_EXECUTABLE_BYTES = 128 * 1024 * 1024
_LIVE_CHILD_MAX_GIT_RECORDS = 128
_LIVE_CHILD_MAX_SOAK_SOURCES = 16
_LIVE_CHILD_MODULE_PATHS = (
    "tools/__init__.py",
    "tools/run_release_gate_readonly_recheck.py",
    "tools/check_release_gate.py",
    "tools/verify_release_soak_evidence.py",
    "tools/collect_soak_evidence.py",
    "tools/release_security_attestation.py",
    "tools/run_release_ci_status_evidence.py",
    "tools/run_release_docker_policy_evidence.py",
    "tools/operator_decision_pack.py",
)
_LIVE_CHILD_MODULE_MAP = {
    "tools": "tools/__init__.py",
    **{
        "tools." + Path(relative).stem: relative
        for relative in _LIVE_CHILD_MODULE_PATHS[1:]
    },
}
_LIVE_CHILD_EXPECTED_SOAK_SOURCE_PATHS = (
    "docs/runs/error_log.jsonl",
    "docs/runs/release_soak_evidence/v3.12.0_history.jsonl",
)
_LIVE_CHILD_FIXED_DATA_PATHS = (
    "docs/release/RELEASE_READINESS.md",
    "docs/runs/release_soak_evidence/v3.12.0.json",
    "docs/runs/release_soak_evidence/v3.12.0_ci_status.json",
    "docs/runs/release_soak_evidence/v3.12.0_docker_policy.json",
    "docs/operator_inbox/docker-latest-promotion.yaml",
    "docs/runs/release_soak_evidence/v3.12.0_security_privacy_precheck.md",
    (
        "docs/runs/release_soak_evidence/"
        "v3.12.0_bandit_report_after_static_hardening_zero_medium.json"
    ),
    (
        "docs/runs/release_soak_evidence/"
        "v3.12.0_pip_audit_report_lock_after_prune_osv.json"
    ),
    (
        "docs/runs/release_soak_evidence/"
        "v3.12.0_axis_a_solver_scale/solver_scale_proof.json"
    ),
    "docs/runs/release_soak_evidence/v3.12.0_axis_b_hex_aligned_eval.json",
    "docs/releases/v3.12.0.md",
    "docs/runs/release_soak_evidence/v3.12.0_soak_log_audit.json",
    "requirements.lock.txt",
    ".github/workflows/release-docker.yml",
    ".github/workflows/release-docker-stable.yml",
    "Dockerfile",
    "docker-compose.yml",
    "pyproject.toml",
    "docs/deployment/DOCKER_QUICKSTART.md",
)
_LIVE_CHILD_DATA_PATHS = (
    *_LIVE_CHILD_FIXED_DATA_PATHS[:12],
    *_LIVE_CHILD_EXPECTED_SOAK_SOURCE_PATHS,
    *_LIVE_CHILD_FIXED_DATA_PATHS[12:],
)
_LIVE_CHILD_OPTIONAL_ABSENT_PATHS = (
    "docs/runs/release_soak_evidence/v3.12.0_bandit_report.json",
    "docs/runs/release_soak_evidence/v3.12.0_pip_audit_report_lock_after_prune.json",
    (
        "docs/runs/release_soak_evidence/"
        "v3.12.0_pip_audit_report_after_fixable_deps.json"
    ),
    (
        "docs/runs/release_soak_evidence/"
        "v3.12.0_pip_audit_report_after_direct_ci_deps.json"
    ),
    "docs/runs/release_soak_evidence/v3.12.0_pip_audit_report.json",
)
_LIVE_CHILD_DOCKER_SOURCE_PATHS = (
    ".github/workflows/release-docker.yml",
    ".github/workflows/release-docker-stable.yml",
    "Dockerfile",
    "docker-compose.yml",
    "pyproject.toml",
    "docs/deployment/DOCKER_QUICKSTART.md",
    "tools/run_release_docker_policy_evidence.py",
    "tools/operator_decision_pack.py",
)
_LIVE_CHILD_SOAK_PATH = "docs/runs/release_soak_evidence/v3.12.0.json"
_LIVE_CHILD_SOAK_LOG_PATH = (
    "docs/runs/release_soak_evidence/v3.12.0_soak_log_audit.json"
)
_LIVE_CHILD_DOCKER_REPORT_PATH = (
    "docs/runs/release_soak_evidence/v3.12.0_docker_policy.json"
)

def _trusted_pyyaml_child_bundle() -> bytes:
    blocker = _trusted_pyyaml_current_blocker()
    if blocker:
        raise ValueError(blocker)
    assert isinstance(_TRUSTED_PYYAML, Mapping)
    payload = {
        "schema_version": _PYYAML_CHILD_BUNDLE_SCHEMA,
        "version": PYYAML_VERSION,
        "source_items": [
            [relative, base64.b64encode(raw).decode("ascii")]
            for relative, raw in _TRUSTED_PYYAML["source_items"]
        ],
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")

FALSE_RELEASE_BOUNDARY = {
    "stable_release_claim": False,
    "tag_creation": False,
    "docker_latest_move": False,
    "external_effect_authority_change": False,
}
READ_ONLY_INVARIANTS = {
    "no_tag_created": True,
    "no_docker_latest_moved": True,
    "no_stable_release_claim": True,
    "no_external_effect_authority_change": True,
    "release_gate_effect": "observation_only",
}


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def _format_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


def _parse_timestamp(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        offset = parsed.utcoffset()
    except (AttributeError, OverflowError, ValueError) as exc:
        raise argparse.ArgumentTypeError("timestamp must be valid ISO-8601") from exc
    if parsed.tzinfo is None or offset is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset")
    return parsed


def _source_timestamp_blockers(
    value: Any,
    *,
    prefix: str,
    wall_now: dt.datetime,
) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [f"{prefix}_missing"]
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        offset = parsed.utcoffset()
    except (OverflowError, ValueError):
        return [f"{prefix}_malformed"]
    if parsed.tzinfo is None or offset is None:
        return [f"{prefix}_naive"]
    try:
        parsed = parsed.astimezone(dt.UTC)
    except (OverflowError, ValueError):
        return [f"{prefix}_malformed"]
    if parsed > wall_now:
        return [f"{prefix}_in_future"]
    return []


def _checked_at_evaluation(
    value: dt.datetime | None,
    *,
    wall_now: dt.datetime,
) -> tuple[dt.datetime, dt.datetime, list[str]]:
    """Return report time, safe live-gate time, and fail-closed blockers."""
    if value is None:
        return wall_now, wall_now, []
    if not isinstance(value, dt.datetime):
        return wall_now, wall_now, ["checked_at_utc_missing_or_invalid"]
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            return wall_now, wall_now, ["checked_at_utc_naive"]
        normalized = value.astimezone(dt.UTC)
    except (OverflowError, TypeError, ValueError):
        return wall_now, wall_now, ["checked_at_utc_missing_or_invalid"]
    if normalized > wall_now:
        return normalized, wall_now, ["checked_at_utc_in_future"]
    return normalized, normalized, []


def _trusted_git_candidates() -> tuple[Path, ...]:
    if os.name == "nt":
        return (
            Path(r"C:\Program Files\Git\cmd\git.exe"),
            Path(r"C:\Program Files\Git\bin\git.exe"),
        )
    return (Path("/usr/bin/git"), Path("/usr/local/bin/git"),
            Path("/opt/homebrew/bin/git"))


def _trusted_git_executable() -> Path | None:
    for candidate in _trusted_git_candidates():
        trusted = _trusted_git_candidate(candidate)
        if trusted is not None:
            return trusted
    return None


def _trusted_git_candidate(candidate: Path) -> Path | None:
    """Resolve an allowlisted Git path lexically without following aliases."""
    try:
        if not candidate.is_absolute():
            return None
        lexical = Path(os.path.normpath(os.path.abspath(candidate)))
        anchor = Path(lexical.anchor)
        relative = lexical.relative_to(anchor)
        components = [anchor]
        for part in relative.parts:
            components.append(components[-1] / part)
        identities: list[tuple[int, int, int, int, int]] = []
        for index, component in enumerate(components):
            info = os.lstat(component)
            attributes = getattr(info, "st_file_attributes", 0)
            reparse_tag = getattr(info, "st_reparse_tag", 0)
            if (
                stat.S_ISLNK(info.st_mode)
                or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                or reparse_tag
                or (index < len(components) - 1 and not stat.S_ISDIR(info.st_mode))
                or (index == len(components) - 1 and not stat.S_ISREG(info.st_mode))
            ):
                return None
            identities.append((
                info.st_dev, info.st_ino, info.st_mode,
                attributes, reparse_tag,
            ))
        for component, expected in zip(components, identities, strict=True):
            info = os.lstat(component)
            if (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                getattr(info, "st_file_attributes", 0),
                getattr(info, "st_reparse_tag", 0),
            ) != expected:
                return None
        return lexical
    except (OSError, ValueError):
        return None


def _sanitized_environment() -> dict[str, str]:
    return {
        key: value for key, value in os.environ.items()
        if (
            not key.upper().startswith(("GIT_", "PYTHON", "LD_", "DYLD_"))
            and key.upper() != "__PYVENV_LAUNCHER__"
        )
    }


def _git_environment() -> dict[str, str]:
    environment = _sanitized_environment()
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_ATTR_NOSYSTEM": "1",
    })
    return environment


def _child_environment() -> dict[str, str]:
    environment = _git_environment()
    executable = _trusted_git_executable()
    if executable is None:
        raise _LiveGateProtocolError("live_release_gate_trusted_git_unavailable")
    # The child only needs the already-validated Git executable.  In
    # particular, do not derive a search directory from ambient SystemRoot.
    environment["PATH"] = str(executable.parent)
    environment["PYTHONPYCACHEPREFIX"] = str(_PYCACHE_PREFIX)
    return environment


class _GitControlPlaneError(RuntimeError):
    def __init__(self, blocker: str) -> None:
        super().__init__(blocker)
        self.blocker = blocker


def _filesystem_anchor(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    return Path(absolute.anchor or os.path.sep)


def _git_control_file(
    path: Path,
    *,
    required: bool,
) -> tuple[bytes | None, tuple[Any, ...]]:
    path = Path(os.path.abspath(path))
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if required or os.path.lexists(path):
            raise _GitControlPlaneError("git_control_file_unverifiable")
        return None, (False,)
    except OSError as exc:
        raise _GitControlPlaneError("git_control_file_unverifiable") from exc
    if stat.S_ISLNK(info.st_mode) or _runtime_reparse(info):
        raise _GitControlPlaneError("git_control_path_reparse")
    if not stat.S_ISREG(info.st_mode):
        raise _GitControlPlaneError("git_control_file_not_regular")
    if getattr(info, "st_nlink", 1) != 1:
        raise _GitControlPlaneError("git_control_file_multiple_links")
    try:
        raw = _runtime_file_bytes(path, _filesystem_anchor(path))
        after = os.lstat(path)
    except OSError as exc:
        raise _GitControlPlaneError("git_control_file_unverifiable") from exc
    if (
        stat.S_ISLNK(after.st_mode)
        or _runtime_reparse(after)
        or getattr(after, "st_nlink", 1) != 1
        or _identity(info) != _identity(after)
    ):
        raise _GitControlPlaneError("git_control_file_changed")
    return raw, (
        True,
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        getattr(info, "st_nlink", 1),
        hashlib.sha256(raw).hexdigest(),
    )


def _git_control_directory(
    path: Path,
    *,
    required: bool,
) -> tuple[tuple[Any, ...], tuple[str, ...]]:
    path = Path(os.path.abspath(path))
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        if required or os.path.lexists(path):
            raise _GitControlPlaneError("git_control_directory_unverifiable")
        return (False,), ()
    except OSError as exc:
        raise _GitControlPlaneError("git_control_directory_unverifiable") from exc
    try:
        error = _runtime_path_error(path, _filesystem_anchor(path), directory=True)
        if error:
            raise OSError(error)
        names = tuple(sorted(entry.name for entry in os.scandir(path)))
        after = os.lstat(path)
    except OSError as exc:
        raise _GitControlPlaneError("git_control_directory_unverifiable") from exc
    if (
        not stat.S_ISDIR(before.st_mode)
        or _runtime_reparse(before)
        or _identity(before) != _identity(after)
        or len({name.casefold() for name in names}) != len(names)
    ):
        raise _GitControlPlaneError("git_control_directory_changed")
    return (
        True,
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_mtime_ns,
    ), names


def _git_control_single_line(raw: bytes, *, label: str) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise _GitControlPlaneError(f"git_control_{label}_malformed") from exc
    if "\0" in text:
        raise _GitControlPlaneError(f"git_control_{label}_malformed")
    lines = text.splitlines()
    if len(lines) != 1 or not lines[0].strip():
        raise _GitControlPlaneError(f"git_control_{label}_malformed")
    return lines[0].strip()


def _git_control_resolve_path(
    base: Path,
    value: str,
    *,
    label: str,
    directory: bool = True,
) -> Path:
    if "\0" in value or "\n" in value or "\r" in value:
        raise _GitControlPlaneError(f"git_control_{label}_malformed")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = Path(os.path.abspath(candidate))
    error = _runtime_path_error(
        resolved, _filesystem_anchor(resolved), directory=directory
    )
    if error:
        raise _GitControlPlaneError(f"git_control_{label}_unverifiable")
    return resolved


def _git_config_blocker(raw: bytes) -> str | None:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        return "git_control_config_unverifiable"
    if "\0" in text:
        return "git_control_config_unverifiable"
    logical: list[str] = []
    pending = ""
    for physical in text.splitlines():
        line = pending + physical
        trailing = len(line) - len(line.rstrip("\\"))
        if trailing % 2 == 1:
            pending = line[:-1]
            continue
        logical.append(line)
        pending = ""
    if pending:
        return "git_control_config_unverifiable"
    section = ""
    for source_line in logical:
        line = source_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            match = re.fullmatch(
                r'\[([A-Za-z0-9.-]+)(?:\s+"((?:[^"\\]|\\.)*)")?\]\s*',
                line,
            )
            if match is None:
                return "git_control_config_unverifiable"
            section = match.group(1).casefold()
            if section in {"include", "includeif", "filter"}:
                return "git_control_config_dangerous"
            continue
        if not section:
            return "git_control_config_unverifiable"
        match = re.fullmatch(
            r"([A-Za-z][A-Za-z0-9.-]*)(?:\s*=\s*(.*))?", line
        )
        if match is None:
            return "git_control_config_unverifiable"
        key = match.group(1).casefold()
        value = (match.group(2) or "true").strip()
        unquoted = value[1:-1].strip() if (
            len(value) >= 2 and value[0] == value[-1] == '"'
        ) else value
        if (
            (section == "core" and key in {
                "attributesfile", "hookspath", "fsmonitor", "sparsecheckout",
                "sparsecheckoutcone", "excludesfile", "worktree",
            })
            or (section == "diff" and key in {"external", "textconv"})
            or (section == "alias" and unquoted.lstrip().startswith("!"))
            or section == "filter"
            or section in {"include", "includeif"}
        ):
            return "git_control_config_dangerous"
        if section == "extensions" and key == "objectformat" and (
            unquoted.casefold() != "sha1"
        ):
            return "git_control_config_dangerous"
    return None


def _git_attributes_blocker(raw: bytes) -> str | None:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        return "git_control_attributes_unverifiable"
    if "\0" in text:
        return "git_control_attributes_unverifiable"
    for source_line in text.splitlines():
        line = source_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 2:
            return "git_control_attributes_unverifiable"
        if fields[0].casefold().startswith("[attr]"):
            return "git_control_attributes_dangerous"
        for attribute in fields[1:]:
            lowered = attribute.casefold()
            if lowered.startswith(("-", "!")):
                continue
            name, separator, _ = lowered.partition("=")
            if (
                name in {
                    "filter", "working-tree-encoding", "ident", "export-subst"
                }
                or (name in {"diff", "merge"} and separator == "=")
            ):
                return "git_control_attributes_dangerous"
    return None


def _git_control_plane_snapshot(
    root: Path = ROOT,
) -> tuple[dict[str, Any], str | None]:
    root = Path(os.path.abspath(root))
    files: dict[str, tuple[Any, ...]] = {}
    directories: dict[str, tuple[Any, ...]] = {}

    def capture_file(path: Path, *, required: bool = False) -> bytes | None:
        raw, fingerprint = _git_control_file(path, required=required)
        files[str(Path(os.path.abspath(path)))] = fingerprint
        return raw

    def capture_directory(path: Path, *, required: bool = False) -> tuple[str, ...]:
        fingerprint, names = _git_control_directory(path, required=required)
        directories[str(Path(os.path.abspath(path)))] = (*fingerprint, names)
        return names

    try:
        capture_directory(root, required=True)
        dotgit = root / ".git"
        dotgit_info = os.lstat(dotgit)
        if stat.S_ISREG(dotgit_info.st_mode):
            dotgit_raw = capture_file(dotgit, required=True)
            assert dotgit_raw is not None
            line = _git_control_single_line(dotgit_raw, label="gitfile")
            prefix = "gitdir: "
            if not line.casefold().startswith(prefix):
                raise _GitControlPlaneError("git_control_gitfile_malformed")
            git_dir = _git_control_resolve_path(
                root, line[len(prefix):], label="gitdir"
            )
        elif stat.S_ISDIR(dotgit_info.st_mode) and not _runtime_reparse(dotgit_info):
            git_dir = Path(os.path.abspath(dotgit))
            capture_directory(git_dir, required=True)
        else:
            raise _GitControlPlaneError("git_control_gitfile_unverifiable")
        git_dir_names = capture_directory(git_dir, required=True)

        commondir_raw = capture_file(git_dir / "commondir")
        if commondir_raw is None:
            common_dir = git_dir
        else:
            common_value = _git_control_single_line(
                commondir_raw, label="commondir"
            )
            common_dir = _git_control_resolve_path(
                git_dir, common_value, label="commondir"
            )
        common_dir_names = capture_directory(common_dir, required=True)
        if any(name.endswith(".lock") for name in (*git_dir_names, *common_dir_names)):
            raise _GitControlPlaneError("git_control_lock_present")
        if git_dir != common_dir:
            try:
                relative_admin = git_dir.relative_to(common_dir)
            except ValueError as exc:
                raise _GitControlPlaneError(
                    "git_control_worktree_admin_outside_common"
                ) from exc
            if len(relative_admin.parts) != 2 or relative_admin.parts[0] != "worktrees":
                raise _GitControlPlaneError(
                    "git_control_worktree_admin_malformed"
                )
            backlink_raw = capture_file(git_dir / "gitdir", required=True)
            assert backlink_raw is not None
            backlink = _git_control_resolve_path(
                git_dir,
                _git_control_single_line(backlink_raw, label="backlink"),
                label="backlink",
                directory=False,
            )
            if backlink != dotgit:
                raise _GitControlPlaneError("git_control_backlink_mismatch")

        head_raw = capture_file(git_dir / "HEAD", required=True)
        capture_file(git_dir / "index", required=True)
        if capture_file(git_dir / "index.lock") is not None:
            raise _GitControlPlaneError("git_control_lock_present")
        for name in git_dir_names:
            if name.startswith("sharedindex."):
                capture_file(git_dir / name, required=True)
        assert head_raw is not None
        head_value = _git_control_single_line(head_raw, label="head")
        head_ref: str | None = None
        if head_value.startswith("ref: "):
            head_ref = head_value[5:]
            if (
                not head_ref.startswith("refs/heads/")
                or "\\" in head_ref
                or any(part in {"", ".", ".."} for part in head_ref.split("/"))
            ):
                raise _GitControlPlaneError("git_control_head_malformed")
        elif not _FULL_HEX_SHA.fullmatch(head_value):
            raise _GitControlPlaneError("git_control_head_malformed")

        config_paths = tuple(dict.fromkeys((
            common_dir / "config",
            common_dir / "config.worktree",
            git_dir / "config.worktree",
        )))
        for index, config_path in enumerate(config_paths):
            config_raw = capture_file(config_path, required=index == 0)
            if config_raw is not None:
                blocker = _git_config_blocker(config_raw)
                if blocker:
                    raise _GitControlPlaneError(blocker)

        packed_refs = capture_file(common_dir / "packed-refs")
        if packed_refs is not None and b" refs/replace/" in packed_refs:
            raise _GitControlPlaneError("git_control_replace_refs_present")
        if head_ref is not None:
            loose_head = capture_file(common_dir / Path(head_ref))
            packed_marker = b" " + head_ref.encode("utf-8") + b"\n"
            if loose_head is None and (
                packed_refs is None or packed_marker not in b"\n" + packed_refs
            ):
                raise _GitControlPlaneError("git_control_head_ref_unavailable")

        info_dir = common_dir / "info"
        objects_info_dir = common_dir / "objects" / "info"
        hooks_dirs = tuple(dict.fromkeys((common_dir / "hooks", git_dir / "hooks")))
        capture_directory(info_dir)
        capture_directory(objects_info_dir)
        info_attributes = capture_file(info_dir / "attributes")
        if info_attributes is not None:
            blocker = _git_attributes_blocker(info_attributes)
            if blocker:
                raise _GitControlPlaneError(blocker)
        capture_file(info_dir / "exclude")

        prohibited_files = (
            (info_dir / "grafts", "git_control_grafts_present"),
            (common_dir / "shallow", "git_control_shallow_repository"),
            (git_dir / "shallow", "git_control_shallow_repository"),
            (objects_info_dir / "alternates", "git_control_alternates_present"),
            (objects_info_dir / "http-alternates", "git_control_alternates_present"),
            (info_dir / "sparse-checkout", "git_control_sparse_checkout_present"),
            (git_dir / "info" / "sparse-checkout",
             "git_control_sparse_checkout_present"),
        )
        for path, blocker in prohibited_files:
            if capture_file(path) is not None:
                raise _GitControlPlaneError(blocker)

        replace_dir = common_dir / "refs" / "replace"
        if capture_directory(replace_dir):
            raise _GitControlPlaneError("git_control_replace_refs_present")

        for hooks_dir in hooks_dirs:
            for name in capture_directory(hooks_dir):
                hook_path = hooks_dir / name
                hook_raw = capture_file(hook_path, required=True)
                if hook_raw is not None and not name.endswith(".sample"):
                    raise _GitControlPlaneError("git_control_hooks_present")

        attribute_paths = {root / ".gitattributes"}
        attribute_scope = tuple(dict.fromkeys((
            *_CANONICAL_RELATIVE_PATHS,
            *_live_child_required_paths(),
            *_LIVE_CHILD_OPTIONAL_ABSENT_PATHS,
            *_LIVE_CHILD_DOCKER_SOURCE_PATHS,
        )))
        for relative in attribute_scope:
            current = Path(relative).parent
            while current != Path("."):
                attribute_paths.add(root / current / ".gitattributes")
                current = current.parent
        for attributes_path in sorted(attribute_paths, key=str):
            attributes_raw = capture_file(attributes_path)
            if attributes_raw is not None:
                blocker = _git_attributes_blocker(attributes_raw)
                if blocker:
                    raise _GitControlPlaneError(blocker)
        return {
            "root": str(root),
            "git_dir": str(git_dir),
            "common_dir": str(common_dir),
            "head_ref": head_ref,
            "files": files,
            "directories": directories,
        }, None
    except _GitControlPlaneError as exc:
        return {
            "root": str(root),
            "git_dir": None,
            "common_dir": None,
            "head_ref": None,
            "files": files,
            "directories": directories,
        }, exc.blocker
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return {
            "root": str(root),
            "git_dir": None,
            "common_dir": None,
            "head_ref": None,
            "files": files,
            "directories": directories,
        }, "git_control_plane_unverifiable"


def _git_result(*args: str) -> subprocess.CompletedProcess[bytes]:
    control_before, control_blocker = _git_control_plane_snapshot(ROOT)
    if control_blocker:
        raise _GitControlPlaneError(control_blocker)
    executable = _trusted_git_executable()
    if executable is None:
        raise RuntimeError("trusted git executable unavailable")
    nonce = uuid.uuid4().hex
    hooks_path = _PYCACHE_ROOT / f"git-hooks-absent-{nonce}"
    attributes_path = _PYCACHE_ROOT / f"git-attributes-absent-{nonce}"
    excludes_path = _PYCACHE_ROOT / f"git-excludes-absent-{nonce}"
    absent_paths = (hooks_path, attributes_path, excludes_path)
    if any(not path.is_absolute() or os.path.lexists(path) for path in absent_paths):
        raise _GitControlPlaneError("git_control_absent_overrides_unavailable")
    command = [
        str(executable),
        "--no-pager",
        "--no-optional-locks",
        "--no-replace-objects",
        "--no-lazy-fetch",
        "--literal-pathspecs",
        "-c", f"core.hooksPath={hooks_path}",
        "-c", "core.fsmonitor=false",
        "-c", f"core.attributesFile={attributes_path}",
        "-c", f"core.excludesFile={excludes_path}",
        "-c", "core.untrackedCache=false",
        "-c", "core.commitGraph=false",
        "-c", f"safe.directory={ROOT}",
        "-C", str(ROOT),
        "--git-dir", str(control_before["git_dir"]),
        "--work-tree", str(ROOT),
        *args,
    ]
    try:
        git_environment = _git_environment()
        git_environment["PATH"] = str(executable.parent)
        result = subprocess.run(command, cwd=ROOT, env=git_environment,
                                capture_output=True, timeout=30, check=False)
    except BaseException as exc:
        control_after, blocker_after = _git_control_plane_snapshot(ROOT)
        if (
            blocker_after
            or control_after != control_before
            or any(os.path.lexists(path) for path in absent_paths)
        ):
            raise _GitControlPlaneError(
                blocker_after or "git_control_plane_changed"
            ) from exc
        raise
    control_after, blocker_after = _git_control_plane_snapshot(ROOT)
    if any(os.path.lexists(path) for path in absent_paths):
        raise _GitControlPlaneError("git_control_absent_overrides_changed")
    if blocker_after:
        raise _GitControlPlaneError(blocker_after)
    if control_after != control_before:
        raise _GitControlPlaneError("git_control_plane_changed")
    if result.stderr != b"":
        raise RuntimeError("trusted git command failed")
    return result


def _git(*args: str) -> bytes:
    result = _git_result(*args)
    if result.returncode != 0:
        raise RuntimeError("trusted git command failed")
    return result.stdout


def _git_state() -> dict[str, Any]:
    try:
        top = Path(_git("rev-parse", "--show-toplevel").decode().strip()).resolve()
        if top != ROOT:
            raise RuntimeError("git toplevel mismatch")
        head = _git("rev-parse", "--verify", "HEAD^{commit}").decode().strip()
        object_format = _git("rev-parse", "--show-object-format").decode().strip()
        if object_format != "sha1":
            raise RuntimeError("git object format unsupported")
        clean = _git(
            "status", "--porcelain=v1", "-z", "--untracked-files=all",
            "--ignore-submodules=all", "--no-renames",
        ) == b""
        listing = _git(
            "ls-files", "-s", "-z", "--", *_CANONICAL_RELATIVE_PATHS
        ).decode()
        index: dict[str, tuple[str, str, str]] = {}
        for item in filter(None, listing.split("\0")):
            meta, separator, path = item.partition("\t")
            fields = meta.split()
            if separator != "\t" or len(fields) != 3 or fields[2] != "0":
                raise RuntimeError("git index output malformed")
            index[path] = (fields[0], fields[1], fields[2])
        flags_listing = _git("ls-files", "-v", "-z").decode()
        flags: dict[str, str] = {}
        for item in filter(None, flags_listing.split("\0")):
            if len(item) < 3 or item[1] != " ":
                raise RuntimeError("git flags output malformed")
            flags[item[2:]] = item[0]
        expected = set(_CANONICAL_RELATIVE_PATHS)
        tracked = set(index) == expected and expected.issubset(flags) and all(
            entry[0] in TRACKED_REGULAR_MODES for entry in index.values()
        ) and all(flags.get(path) == "H" for path in expected)
        tracked_flags_normal = bool(flags) and all(
            flag == "H" for flag in flags.values()
        )
        bound_index = {
            path: (*entry, flags.get(path, "")) for path, entry in index.items()
        }
        return {"toplevel": str(top), "head": head, "clean": clean,
                "tracked": tracked, "index": bound_index,
                "tracked_flags_normal": tracked_flags_normal,
                "object_format": object_format, "error": None}
    except _GitControlPlaneError as exc:
        return {"toplevel": None, "head": None, "clean": None,
                "tracked": False, "tracked_flags_normal": False,
                "index": {}, "object_format": None, "error": exc.blocker}
    except (OSError, RuntimeError, subprocess.SubprocessError, UnicodeError, ValueError):
        return {"toplevel": None, "head": None, "clean": None,
                "tracked": False, "tracked_flags_normal": False,
                "index": {}, "object_format": None,
                "error": "git_state_unavailable"}


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    # Windows can report a slightly different creation-time precision through
    # lstat and fstat for the same handle, so ctime is not an identity field.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns)


def _path_error(path: Path) -> str | None:
    try:
        relative = path.relative_to(ROOT)
        components = [ROOT]
        for part in relative.parts:
            components += [components[-1] / part]
        for index, component in enumerate(components):
            info = os.lstat(component)
            if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
                return "reparse_point"
            if index < len(components) - 1 and not stat.S_ISDIR(info.st_mode):
                return "path_component_not_directory"
        resolved = path.resolve(strict=True)
        if os.path.normcase(os.path.abspath(resolved)) != os.path.normcase(
            os.path.abspath(path)
        ):
            return "path_mismatch"
        resolved.relative_to(ROOT)
    except (OSError, ValueError):
        return "unreadable"
    return None


def _capture_input(path: Path) -> dict[str, Any]:
    empty = {"path": str(path), "raw": None, "digest": None, "identity": None}
    try:
        error = _path_error(path)
        if error:
            return {**empty, "error": error}
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            return {**empty, "error": "not_regular_file"}
        if before.st_nlink != 1:
            return {**empty, "error": "multiple_links"}
        with open(path, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if opened.st_nlink != 1 or _identity(opened) != _identity(before):
                raise OSError
            raw = handle.read()
            after_read = os.fstat(handle.fileno())
        after_path = os.lstat(path)
        if (after_read.st_nlink != 1
                or after_path.st_nlink != 1
                or _identity(after_read) != _identity(opened)
                or _identity(after_path) != _identity(opened)
                or _path_error(path)):
            raise OSError
        return {**empty, "raw": raw, "digest": hashlib.sha256(raw).hexdigest(),
                "identity": _identity(after_path), "error": None}
    except OSError:
        return {**empty, "error": "unreadable_or_changed"}


def _yaml_shadow_blocker() -> str | None:
    for path in _YAML_SHADOW_PATHS:
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError:
            return "third_party_yaml_shadow_unverifiable"
        return "third_party_yaml_shadow_present"
    return None


def _open_window() -> dict[str, Any]:
    before = _git_state()
    inputs = {name: _capture_input(path) for name, path in CANONICAL_INPUTS.items()}
    return {"git_before": before, "inputs": inputs}


def _git_blob_oid(raw: bytes) -> str:
    payload = b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
    return hashlib.sha1(payload, usedforsecurity=False).hexdigest()


def _pre_window_blockers(window: Mapping[str, Any]) -> list[str]:
    before = window["git_before"]
    blockers = [before["error"]] if before["error"] else []
    if before["clean"] is not True:
        blockers.append("git_worktree_not_clean_before")
    if before["tracked"] is not True:
        blockers.append("canonical_inputs_not_tracked_regular")
    if before.get("tracked_flags_normal") is not True:
        blockers.append("git_tracked_flags_not_normal")
    shadow_blocker = _yaml_shadow_blocker()
    if shadow_blocker:
        blockers.append(shadow_blocker)
    dependency_blocker = _trusted_pyyaml_current_blocker()
    if dependency_blocker:
        blockers.append(dependency_blocker)
    for name, entry in window["inputs"].items():
        if entry["error"]:
            blockers.append(f"canonical_input_{name}_{entry['error']}")
            continue
        relative = CANONICAL_INPUTS[name].relative_to(ROOT).as_posix()
        index_entry = before["index"].get(relative)
        raw = entry.get("raw")
        if not isinstance(index_entry, tuple) or not isinstance(raw, bytes):
            blockers.append(f"canonical_input_{name}_index_binding_unavailable")
            continue
        index_oid = index_entry[1]
        normalized = raw.replace(b"\r\n", b"\n")
        candidates = {
            _git_blob_oid(raw),
            _git_blob_oid(normalized),
        }
        if b"\0" in raw or index_oid not in candidates:
            blockers.append(f"canonical_input_{name}_index_blob_mismatch")
    return blockers


def _close_window(window: Mapping[str, Any]) -> list[str]:
    before = window["git_before"]
    blockers: list[str] = []
    for name, entry in window["inputs"].items():
        if entry["error"]:
            continue
        after = _capture_input(CANONICAL_INPUTS[name])
        if after["error"] or (after["identity"], after["digest"]) != (
            entry["identity"], entry["digest"]
        ):
            blockers.append(f"canonical_input_{name}_changed")
    after_git = _git_state()
    if after_git["error"]:
        blockers.append(after_git["error"])
    if after_git["clean"] is not True:
        blockers.append("git_worktree_not_clean_after")
    if before["head"] != after_git["head"]:
        blockers.append("git_head_changed_during_evaluation")
    if before["index"] != after_git["index"]:
        blockers.append("git_index_changed_during_evaluation")
    if before.get("object_format") != after_git.get("object_format"):
        blockers.append("git_object_format_changed_during_evaluation")
    if after_git["tracked"] is not True:
        blockers.append("canonical_inputs_not_tracked_regular_after")
    if after_git.get("tracked_flags_normal") is not True:
        blockers.append("git_tracked_flags_not_normal_after")
    shadow_blocker = _yaml_shadow_blocker()
    if shadow_blocker:
        blockers.append(shadow_blocker)
    dependency_blocker = _trusted_pyyaml_current_blocker()
    if dependency_blocker:
        blockers.append(dependency_blocker)
    return blockers


def _json_object(raw: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_nonfinite(value: str) -> None:
        raise ValueError(f"non-finite JSON constant: {value}")

    def finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"non-finite JSON float: {value}")
        return parsed

    loaded = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=reject_duplicates,
        parse_constant=reject_nonfinite,
        parse_float=finite_float,
    )
    if not isinstance(loaded, dict):
        raise ValueError("JSON value is not an object")
    return loaded


class _LiveChildManifestError(ValueError):
    def __init__(self, blocker: str) -> None:
        super().__init__(f"live child bundle: {blocker}")
        self.blocker = blocker


def _live_child_module_paths() -> tuple[str, ...]:
    return _LIVE_CHILD_MODULE_PATHS


def _live_child_required_paths() -> tuple[str, ...]:
    return (*_LIVE_CHILD_MODULE_PATHS, *_LIVE_CHILD_DATA_PATHS)


def _live_child_validate_path(value: object) -> str:
    if type(value) is not str or not value or len(value) > 512:
        raise _LiveChildManifestError("live_child_path_invalid")
    if value != unicodedata.normalize("NFC", value):
        raise _LiveChildManifestError("live_child_path_not_nfc")
    if value.startswith("/") or value.endswith("/") or "\\" in value:
        raise _LiveChildManifestError("live_child_path_not_relative")
    parts = value.split("/")
    if any(
        part in {"", ".", ".."}
        or part.endswith((" ", "."))
        or re.fullmatch(r"[A-Za-z0-9._-]+", part) is None
        for part in parts
    ):
        raise _LiveChildManifestError("live_child_path_invalid")
    reserved = {
        "con", "prn", "aux", "nul", "clock$",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
    if any(part.split(".", 1)[0].casefold() in reserved for part in parts):
        raise _LiveChildManifestError("live_child_path_reserved")
    return value


def _live_child_dynamic_soak_paths(
    soak_log: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    if soak_log is None:
        return _LIVE_CHILD_EXPECTED_SOAK_SOURCE_PATHS
    source_files = soak_log.get("source_files")
    source_count = soak_log.get("source_file_count")
    source_hashes = soak_log.get("source_hashes")
    if (
        type(source_files) is not list
        or not 0 < len(source_files) <= _LIVE_CHILD_MAX_SOAK_SOURCES
        or type(source_count) is not int
        or source_count != len(source_files)
        or type(source_hashes) is not dict
    ):
        raise _LiveChildManifestError("live_child_soak_sources_malformed")
    paths: list[str] = []
    seen: set[str] = set()
    for value in source_files:
        path = _live_child_validate_path(value)
        key = path.casefold()
        if (
            key in seen
            or not path.startswith("docs/runs/")
            or not path.endswith(".jsonl")
        ):
            raise _LiveChildManifestError("live_child_soak_source_not_allowed")
        seen.add(key)
        paths.append(path)
    if set(source_hashes) != set(paths) or any(
        type(value) is not str
        or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
        for value in source_hashes.values()
    ):
        raise _LiveChildManifestError("live_child_soak_source_hashes_malformed")
    result = tuple(paths)
    if result != _LIVE_CHILD_EXPECTED_SOAK_SOURCE_PATHS:
        raise _LiveChildManifestError("live_child_soak_source_allowlist_mismatch")
    return result


def _live_child_decode_base64(value: object, blocker: str) -> bytes:
    if type(value) is not str:
        raise _LiveChildManifestError(blocker)
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise _LiveChildManifestError(blocker) from exc
    if base64.b64encode(raw).decode("ascii") != value:
        raise _LiveChildManifestError(blocker)
    return raw


def _live_child_source_digest(raw: bytes) -> str:
    """Match collect_soak_evidence's reviewed text normalization exactly."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _LiveChildManifestError(
            "live_child_soak_source_encoding_invalid"
        ) from exc
    if "\0" in text or "\r" in text.replace("\r\n", ""):
        raise _LiveChildManifestError("live_child_soak_source_text_invalid")
    normalized = text.replace("\r\n", "\n").encode("utf-8")
    return "sha256:" + hashlib.sha256(normalized).hexdigest()


def _live_child_normalize_file_records(
    records: object,
) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    if type(records) not in {list, tuple}:
        raise _LiveChildManifestError("live_child_file_records_malformed")
    expected_paths = _live_child_required_paths()
    if len(records) != len(expected_paths):
        raise _LiveChildManifestError("live_child_file_closure_mismatch")
    by_path: dict[str, dict[str, Any]] = {}
    raw_by_path: dict[str, bytes] = {}
    case_paths: set[str] = set()
    total = 0
    exact_keys = {"path", "mode", "oid", "size", "sha256", "content_b64"}
    for candidate in records:
        if not isinstance(candidate, Mapping) or set(candidate) != exact_keys:
            raise _LiveChildManifestError("live_child_file_record_malformed")
        path = _live_child_validate_path(candidate.get("path"))
        case_key = path.casefold()
        if path in by_path or case_key in case_paths:
            raise _LiveChildManifestError("live_child_file_path_collision")
        mode = candidate.get("mode")
        oid = candidate.get("oid")
        size = candidate.get("size")
        digest = candidate.get("sha256")
        if mode not in TRACKED_REGULAR_MODES:
            raise _LiveChildManifestError("live_child_file_mode_invalid")
        if type(oid) is not str or _FULL_HEX_SHA.fullmatch(oid) is None:
            raise _LiveChildManifestError("live_child_file_oid_invalid")
        if type(size) is not int or not 0 <= size <= _LIVE_CHILD_MAX_FILE_BYTES:
            raise _LiveChildManifestError("live_child_file_size_invalid")
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise _LiveChildManifestError("live_child_file_digest_invalid")
        raw = _live_child_decode_base64(
            candidate.get("content_b64"), "live_child_file_base64_invalid"
        )
        if (
            len(raw) != size
            or hashlib.sha256(raw).hexdigest() != digest
            or _git_blob_oid(raw) != oid
        ):
            raise _LiveChildManifestError("live_child_file_content_mismatch")
        total += len(raw)
        if total > _LIVE_CHILD_MAX_TOTAL_BYTES:
            raise _LiveChildManifestError("live_child_file_closure_too_large")
        normalized = {
            "path": path,
            "mode": mode,
            "oid": oid,
            "size": size,
            "sha256": digest,
            "content_b64": candidate["content_b64"],
        }
        by_path[path] = normalized
        raw_by_path[path] = raw
        case_paths.add(case_key)
    if set(by_path) != set(expected_paths):
        raise _LiveChildManifestError("live_child_file_closure_mismatch")
    try:
        soak_log = _json_object(raw_by_path[_LIVE_CHILD_SOAK_LOG_PATH])
    except (KeyError, UnicodeError, ValueError) as exc:
        raise _LiveChildManifestError("live_child_soak_log_malformed") from exc
    dynamic_paths = _live_child_dynamic_soak_paths(soak_log)
    source_hashes = soak_log["source_hashes"]
    for path in dynamic_paths:
        if source_hashes[path] != _live_child_source_digest(raw_by_path[path]):
            raise _LiveChildManifestError("live_child_soak_source_digest_mismatch")
    return [by_path[path] for path in expected_paths], raw_by_path


def _live_child_normalize_git_records(
    records: object,
    *,
    expected_root: str,
    expected_commits: set[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, dict[str, Any]]], str]:
    if type(records) not in {list, tuple} or len(records) > _LIVE_CHILD_MAX_GIT_RECORDS:
        raise _LiveChildManifestError("live_child_git_records_malformed")
    exact_keys = {"args", "text", "returncode", "stdout_b64", "stderr_b64"}
    normalized: list[dict[str, Any]] = []
    lookup: dict[tuple[tuple[str, ...], bool], tuple[int, bytes, bytes]] = {}
    total_output = 0
    for candidate in records:
        if not isinstance(candidate, Mapping) or set(candidate) != exact_keys:
            raise _LiveChildManifestError("live_child_git_record_malformed")
        args = candidate.get("args")
        text = candidate.get("text")
        returncode = candidate.get("returncode")
        if (
            type(args) is not list
            or not args
            or any(type(arg) is not str or not arg or "\0" in arg for arg in args)
            or type(text) is not bool
            or type(returncode) is not int
            or not 0 <= returncode <= 255
        ):
            raise _LiveChildManifestError("live_child_git_record_malformed")
        stdout = _live_child_decode_base64(
            candidate.get("stdout_b64"), "live_child_git_record_base64_invalid"
        )
        stderr = _live_child_decode_base64(
            candidate.get("stderr_b64"), "live_child_git_record_base64_invalid"
        )
        if stderr or len(stdout) > _LIVE_CHILD_MAX_FILE_BYTES:
            raise _LiveChildManifestError("live_child_git_record_output_invalid")
        total_output += len(stdout)
        if total_output > _LIVE_CHILD_MAX_TOTAL_BYTES:
            raise _LiveChildManifestError("live_child_git_record_output_too_large")
        key = (tuple(args), text)
        if key in lookup:
            raise _LiveChildManifestError("live_child_git_record_duplicate")
        lookup[key] = (returncode, stdout, stderr)
        normalized.append({
            "args": list(args),
            "text": text,
            "returncode": returncode,
            "stdout_b64": candidate["stdout_b64"],
            "stderr_b64": candidate["stderr_b64"],
        })

    def response(args: list[str], text: bool) -> tuple[int, bytes, bytes]:
        try:
            return lookup[(tuple(args), text)]
        except KeyError as exc:
            raise _LiveChildManifestError("live_child_git_replay_incomplete") from exc

    top_rc, top_raw, _ = response(["rev-parse", "--show-toplevel"], True)
    if top_rc != 0 or top_raw != expected_root.encode("utf-8"):
        raise _LiveChildManifestError("live_child_git_toplevel_invalid")
    head_rc, head_raw, _ = response(["rev-parse", "HEAD"], True)
    try:
        head = head_raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise _LiveChildManifestError("live_child_git_head_invalid") from exc
    if head_rc != 0 or _FULL_HEX_SHA.fullmatch(head) is None:
        raise _LiveChildManifestError("live_child_git_head_invalid")
    commits: set[str] = set()
    suffix = "^{commit}"
    for item in normalized:
        args = item["args"]
        if len(args) == 3 and args[:2] == ["rev-parse", "--verify"]:
            subject = args[2]
            if subject.endswith(suffix) and _FULL_HEX_SHA.fullmatch(
                subject[:-len(suffix)]
            ):
                commits.add(subject[:-len(suffix)])
    if not 1 <= len(commits) <= 2 or commits != expected_commits:
        raise _LiveChildManifestError("live_child_historical_commit_count_invalid")
    subjects: dict[str, dict[str, dict[str, Any]]] = {}
    expected_keys: set[tuple[tuple[str, ...], bool]] = {
        (("rev-parse", "--show-toplevel"), True),
        (("rev-parse", "HEAD"), True),
    }
    for commit in sorted(commits):
        expected_keys.add((("rev-parse", "--verify", f"{commit}^{{commit}}"), True))
        rc, raw, _ = response(
            ["rev-parse", "--verify", f"{commit}^{{commit}}"], True
        )
        if rc != 0 or raw != commit.encode("ascii"):
            raise _LiveChildManifestError("live_child_historical_commit_invalid")
        paths: dict[str, dict[str, Any]] = {}
        for path in _LIVE_CHILD_DOCKER_SOURCE_PATHS:
            path_key = (("rev-parse", "--verify", f"{commit}:{path}"), True)
            expected_keys.add(path_key)
            rc, raw, _ = response(
                ["rev-parse", "--verify", f"{commit}:{path}"], True
            )
            if rc != 0:
                if rc != 128 or raw:
                    raise _LiveChildManifestError("live_child_git_absence_replay_invalid")
                paths[path] = {"present": False}
                continue
            try:
                oid = raw.decode("ascii")
            except UnicodeDecodeError as exc:
                raise _LiveChildManifestError("live_child_git_blob_oid_invalid") from exc
            if _FULL_HEX_SHA.fullmatch(oid) is None:
                raise _LiveChildManifestError("live_child_git_blob_oid_invalid")
            blob_key = (("cat-file", "blob", oid), False)
            expected_keys.add(blob_key)
            blob_rc, blob, _ = response(["cat-file", "blob", oid], False)
            if blob_rc != 0 or _git_blob_oid(blob) != oid:
                raise _LiveChildManifestError("live_child_git_blob_replay_invalid")
            paths[path] = {
                "present": True,
                "oid": oid,
                "size": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
                "content_b64": base64.b64encode(blob).decode("ascii"),
            }
        subjects[commit] = paths
    if set(lookup) != expected_keys:
        raise _LiveChildManifestError("live_child_git_replay_not_exact")
    return normalized, subjects, head


def _live_child_evidence_commits(raw_by_path: Mapping[str, bytes]) -> set[str]:
    try:
        soak = _json_object(raw_by_path[_LIVE_CHILD_SOAK_PATH])
        docker = _json_object(raw_by_path[_LIVE_CHILD_DOCKER_REPORT_PATH])
    except (KeyError, UnicodeError, ValueError) as exc:
        raise _LiveChildManifestError(
            "live_child_commit_authority_malformed"
        ) from exc
    values = (soak.get("commit"), docker.get("commit"))
    if any(
        type(value) is not str or _FULL_HEX_SHA.fullmatch(value) is None
        for value in values
    ):
        raise _LiveChildManifestError("live_child_commit_authority_malformed")
    return set(values)


def _live_child_git_object(
    oid: str,
    expected_type: str,
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if _FULL_HEX_SHA.fullmatch(oid) is None or expected_type not in {
        "blob", "tree", "commit"
    }:
        raise _LiveChildManifestError("live_child_git_object_request_invalid")
    cached = cache.get(oid)
    if cached is not None:
        if cached["type"] != expected_type:
            raise _LiveChildManifestError("live_child_git_object_type_mismatch")
        return cached
    try:
        object_type = _git("cat-file", "-t", oid)
        size_raw = _git("cat-file", "-s", oid)
        raw = _git("cat-file", expected_type, oid)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise _LiveChildManifestError(
            f"live_child_git_object_unavailable:{oid}"
        ) from exc
    if object_type != expected_type.encode("ascii") + b"\n":
        raise _LiveChildManifestError("live_child_git_object_type_mismatch")
    if re.fullmatch(rb"(?:0|[1-9][0-9]*)\n", size_raw) is None:
        raise _LiveChildManifestError("live_child_git_object_size_invalid")
    size = int(size_raw)
    if size != len(raw) or size > _LIVE_CHILD_MAX_FILE_BYTES:
        raise _LiveChildManifestError("live_child_git_object_size_invalid")
    framed = expected_type.encode("ascii") + b" " + str(size).encode("ascii") + b"\0" + raw
    actual_oid = hashlib.sha1(framed, usedforsecurity=False).hexdigest()
    if actual_oid != oid:
        raise _LiveChildManifestError("live_child_git_object_oid_mismatch")
    result = {
        "type": expected_type,
        "oid": oid,
        "size": size,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "raw": raw,
    }
    cache[oid] = result
    return result


def _live_child_commit_tree(
    commit_oid: str,
    cache: dict[str, dict[str, Any]],
) -> str:
    raw = _live_child_git_object(commit_oid, "commit", cache)["raw"]
    if b"\0" in raw or b"\r" in raw:
        raise _LiveChildManifestError("live_child_git_commit_malformed")
    first, separator, _ = raw.partition(b"\n")
    if not separator or re.fullmatch(rb"tree [0-9a-f]{40}", first) is None:
        raise _LiveChildManifestError("live_child_git_commit_malformed")
    return first[5:].decode("ascii")


def _live_child_tree_entries(
    tree_oid: str,
    cache: dict[str, dict[str, Any]],
) -> dict[str, tuple[str, str]]:
    raw = _live_child_git_object(tree_oid, "tree", cache)["raw"]
    position = 0
    entries: dict[str, tuple[str, str]] = {}
    case_names: set[str] = set()
    previous_sort_key: bytes | None = None
    while position < len(raw):
        space = raw.find(b" ", position)
        nul = raw.find(b"\0", space + 1) if space >= 0 else -1
        if space <= position or nul <= space + 1 or nul + 21 > len(raw):
            raise _LiveChildManifestError("live_child_git_tree_malformed")
        mode_raw = raw[position:space]
        name_raw = raw[space + 1:nul]
        oid = raw[nul + 1:nul + 21].hex()
        position = nul + 21
        try:
            mode = mode_raw.decode("ascii")
            name = name_raw.decode("utf-8")
        except UnicodeError as exc:
            raise _LiveChildManifestError("live_child_git_tree_name_invalid") from exc
        if mode not in {"40000", "100644", "100755", "120000", "160000"}:
            raise _LiveChildManifestError("live_child_git_tree_mode_invalid")
        sort_key = name_raw + (b"/" if mode == "40000" else b"")
        if previous_sort_key is not None and sort_key <= previous_sort_key:
            raise _LiveChildManifestError("live_child_git_tree_order_invalid")
        previous_sort_key = sort_key
        _live_child_validate_path(name)
        case_key = name.casefold()
        if name in entries or case_key in case_names:
            raise _LiveChildManifestError("live_child_git_tree_name_collision")
        entries[name] = (mode, oid)
        case_names.add(case_key)
    return entries


def _live_child_resolve_blob(
    tree_oid: str,
    relative: str,
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    path = _live_child_validate_path(relative)
    parts = path.split("/")
    current = tree_oid
    for index, part in enumerate(parts):
        entries = _live_child_tree_entries(current, cache)
        entry = entries.get(part)
        if entry is None:
            if any(name.casefold() == part.casefold() for name in entries):
                raise _LiveChildManifestError("live_child_git_path_case_mismatch")
            return None
        mode, oid = entry
        final = index == len(parts) - 1
        if not final:
            if mode != "40000":
                if mode in {"120000", "160000"}:
                    raise _LiveChildManifestError("live_child_git_path_unsafe_mode")
                raise _LiveChildManifestError("live_child_git_path_not_directory")
            current = oid
            continue
        if mode in {"120000", "160000"}:
            raise _LiveChildManifestError("live_child_git_path_unsafe_mode")
        if mode == "40000":
            raise _LiveChildManifestError("live_child_git_path_not_blob")
        blob = _live_child_git_object(oid, "blob", cache)
        return {
            "path": path,
            "mode": mode,
            "oid": oid,
            "size": blob["size"],
            "sha256": blob["sha256"],
            "content_b64": base64.b64encode(blob["raw"]).decode("ascii"),
        }
    raise _LiveChildManifestError("live_child_git_path_invalid")


def _live_child_git_executable_provenance() -> dict[str, str]:
    executable = _trusted_git_executable()
    if executable is None:
        raise _LiveChildManifestError("live_child_git_executable_unavailable")
    try:
        before = os.lstat(executable)
        if (
            not executable.is_absolute()
            or not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or _is_reparse(before)
            or not 0 < before.st_size <= _LIVE_CHILD_MAX_GIT_EXECUTABLE_BYTES
        ):
            raise OSError
        digest = hashlib.sha256()
        with open(executable, "rb") as handle:
            opened = os.fstat(handle.fileno())
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
            after_read = os.fstat(handle.fileno())
        after = os.lstat(executable)
        snapshots = (before, opened, after_read, after)
        if any(
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or _is_reparse(info)
            or not 0 < info.st_size <= _LIVE_CHILD_MAX_GIT_EXECUTABLE_BYTES
            for info in snapshots
        ):
            raise OSError
        # On Windows, pathname stat projects executable permission bits from
        # the .exe suffix while handle fstat has no pathname and reports only
        # the underlying read/write bits.  Preserve the full-mode comparison
        # within each API family, then compare the security-relevant object
        # identity across the pathname and handle views.
        if (
            _identity(before) != _identity(after)
            or _identity(opened) != _identity(after_read)
            or len({_live_child_git_cross_view_identity(info)
                    for info in snapshots}) != 1
        ):
            raise OSError
    except OSError as exc:
        raise _LiveChildManifestError("live_child_git_executable_unverifiable") from exc
    return {"path": str(executable), "sha256": digest.hexdigest()}


def _live_child_git_cross_view_identity(
    info: os.stat_result,
) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        stat.S_IFMT(info.st_mode),
        info.st_size,
        info.st_mtime_ns,
        info.st_nlink,
        getattr(info, "st_file_attributes", 0),
        getattr(info, "st_reparse_tag", 0),
    )


def _live_child_git_replay(
    args: list[str], *, text: bool, returncode: int, stdout: bytes = b""
) -> dict[str, Any]:
    return {
        "args": args,
        "text": text,
        "returncode": returncode,
        "stdout_b64": base64.b64encode(stdout).decode("ascii"),
        "stderr_b64": "",
    }


def _build_live_git_manifest(
    *, git_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state = dict(_git_state() if git_state is None else git_state)
    head = state.get("head")
    state_error = state.get("error")
    if isinstance(state_error, str) and state_error:
        raise _LiveChildManifestError(state_error)
    if (
        state.get("object_format") != "sha1"
        or state.get("toplevel") != str(ROOT)
        or state.get("clean") is not True
        or state.get("tracked") is not True
        or state.get("tracked_flags_normal") is not True
        or type(head) is not str
        or _FULL_HEX_SHA.fullmatch(head) is None
    ):
        raise _LiveChildManifestError("live_child_git_provenance_invalid")
    git_executable = _live_child_git_executable_provenance()
    cache: dict[str, dict[str, Any]] = {}
    head_tree = _live_child_commit_tree(head, cache)
    fixed_paths = (*_LIVE_CHILD_MODULE_PATHS, *_LIVE_CHILD_FIXED_DATA_PATHS)
    resolved: dict[str, dict[str, Any]] = {}
    for path in fixed_paths:
        record = _live_child_resolve_blob(head_tree, path, cache)
        if record is None:
            raise _LiveChildManifestError(f"live_child_head_path_missing:{path}")
        resolved[path] = record
    try:
        soak_log = _json_object(
            base64.b64decode(resolved[_LIVE_CHILD_SOAK_LOG_PATH]["content_b64"])
        )
    except (UnicodeError, ValueError) as exc:
        raise _LiveChildManifestError("live_child_soak_log_malformed") from exc
    dynamic_paths = _live_child_dynamic_soak_paths(soak_log)
    for path in dynamic_paths:
        record = _live_child_resolve_blob(head_tree, path, cache)
        if record is None:
            raise _LiveChildManifestError(f"live_child_soak_source_missing:{path}")
        if soak_log["source_hashes"][path] != _live_child_source_digest(
            base64.b64decode(record["content_b64"])
        ):
            raise _LiveChildManifestError("live_child_soak_source_digest_mismatch")
        resolved[path] = record
    for path in _LIVE_CHILD_OPTIONAL_ABSENT_PATHS:
        if _live_child_resolve_blob(head_tree, path, cache) is not None:
            raise _LiveChildManifestError(
                f"live_child_optional_probe_path_present:{path}"
            )
    files = [resolved[path] for path in _live_child_required_paths()]
    try:
        soak = _json_object(base64.b64decode(resolved[_LIVE_CHILD_SOAK_PATH]["content_b64"]))
        docker_report = _json_object(
            base64.b64decode(resolved[_LIVE_CHILD_DOCKER_REPORT_PATH]["content_b64"])
        )
    except (UnicodeError, ValueError) as exc:
        raise _LiveChildManifestError("live_child_commit_authority_malformed") from exc
    commits = {soak.get("commit"), docker_report.get("commit")}
    if not 1 <= len(commits) <= 2 or any(
        type(commit) is not str or _FULL_HEX_SHA.fullmatch(commit) is None
        for commit in commits
    ):
        raise _LiveChildManifestError("live_child_historical_commit_count_invalid")
    replays = [
        _live_child_git_replay(
            ["rev-parse", "--show-toplevel"], text=True, returncode=0,
            stdout=str(ROOT).encode("utf-8"),
        ),
        _live_child_git_replay(
            ["rev-parse", "HEAD"], text=True, returncode=0,
            stdout=head.encode("ascii"),
        ),
    ]
    subjects: dict[str, dict[str, dict[str, Any]]] = {}
    for commit in sorted(commits):
        commit_tree = _live_child_commit_tree(commit, cache)
        replays.append(_live_child_git_replay(
            ["rev-parse", "--verify", f"{commit}^{{commit}}"],
            text=True, returncode=0, stdout=commit.encode("ascii"),
        ))
        paths: dict[str, dict[str, Any]] = {}
        for path in _LIVE_CHILD_DOCKER_SOURCE_PATHS:
            record = _live_child_resolve_blob(commit_tree, path, cache)
            if record is None:
                paths[path] = {"present": False}
                replays.append(_live_child_git_replay(
                    ["rev-parse", "--verify", f"{commit}:{path}"],
                    text=True, returncode=128,
                ))
                continue
            paths[path] = {
                "present": True,
                "oid": record["oid"],
                "size": record["size"],
                "sha256": record["sha256"],
                "content_b64": record["content_b64"],
            }
            replays.extend((
                _live_child_git_replay(
                    ["rev-parse", "--verify", f"{commit}:{path}"],
                    text=True, returncode=0, stdout=record["oid"].encode("ascii"),
                ),
                _live_child_git_replay(
                    ["cat-file", "blob", record["oid"]], text=False,
                    returncode=0, stdout=base64.b64decode(record["content_b64"]),
                ),
            ))
        subjects[commit] = paths
    unique_replays: list[dict[str, Any]] = []
    replay_by_key: dict[tuple[tuple[str, ...], bool], dict[str, Any]] = {}
    for replay in replays:
        key = (tuple(replay["args"]), replay["text"])
        previous = replay_by_key.get(key)
        if previous is not None:
            if previous != replay:
                raise _LiveChildManifestError("live_child_git_replay_conflict")
            continue
        replay_by_key[key] = replay
        unique_replays.append(replay)
    replays = unique_replays
    objects = [{
        "type": item["type"],
        "oid": item["oid"],
        "size": item["size"],
        "sha256": item["sha256"],
        "content_b64": base64.b64encode(item["raw"]).decode("ascii"),
    } for item in sorted(cache.values(), key=lambda item: item["oid"])]
    if sum(item["size"] for item in objects) > _LIVE_CHILD_MAX_TOTAL_BYTES:
        raise _LiveChildManifestError("live_child_git_object_closure_too_large")
    if _live_child_git_executable_provenance() != git_executable:
        raise _LiveChildManifestError("live_child_git_executable_changed")
    return {
        "root": str(ROOT),
        "head": head,
        "head_tree": head_tree,
        "files": files,
        "head_absent_paths": list(_LIVE_CHILD_OPTIONAL_ABSENT_PATHS),
        "git_records": replays,
        "git_subjects": subjects,
        "git_objects": objects,
        "git_executable": git_executable,
        "git_provenance": {
            "object_format": "sha1",
            "head": head,
            "tree": head_tree,
            "clean": True,
        },
    }


def _live_child_normalize_git_executable(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise _LiveChildManifestError("live_child_git_executable_malformed")
    path = value.get("path")
    digest = value.get("sha256")
    if (
        type(path) is not str
        or not path
        or not Path(path).is_absolute()
        or type(digest) is not str
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise _LiveChildManifestError("live_child_git_executable_malformed")
    return {"path": path, "sha256": digest}


def _live_child_root_digest(payload: Mapping[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("root_digest", None)
    try:
        canonical = json.dumps(
            unsigned, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise _LiveChildManifestError("live_child_bundle_not_canonical") from exc
    return hashlib.sha256(
        _LIVE_CHILD_BUNDLE_DIGEST_DOMAIN + canonical
    ).hexdigest()


def _build_live_child_bundle_data(
    *,
    root: Path = ROOT,
    file_records: object | None = None,
    git_records: object | None = None,
    git_executable: object | None = None,
    git_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if file_records is None and git_records is None and git_executable is None:
        schema_version = _LIVE_CHILD_BUNDLE_SCHEMA
        manifest = _build_live_git_manifest(git_state=git_state)
        files, raw_files = _live_child_normalize_file_records(manifest["files"])
        normalized_git, subjects, head = _live_child_normalize_git_records(
            manifest["git_records"],
            expected_root=manifest["root"],
            expected_commits=_live_child_evidence_commits(raw_files),
        )
        executable = _live_child_normalize_git_executable(
            manifest["git_executable"]
        )
    elif file_records is not None and git_records is not None and git_executable is not None:
        schema_version = _LIVE_CHILD_TEST_BUNDLE_SCHEMA
        if git_state is not None:
            raise _LiveChildManifestError("live_child_test_seam_git_state_invalid")
        manifest = {
            "root": str(Path(root)),
            "head_tree": None,
            "head_absent_paths": list(_LIVE_CHILD_OPTIONAL_ABSENT_PATHS),
            "git_objects": [],
            "git_provenance": {"object_format": "sha1", "clean": True},
        }
        files, raw_files = _live_child_normalize_file_records(file_records)
        normalized_git, subjects, head = _live_child_normalize_git_records(
            git_records,
            expected_root=manifest["root"],
            expected_commits=_live_child_evidence_commits(raw_files),
        )
        executable = _live_child_normalize_git_executable(git_executable)
    else:
        raise _LiveChildManifestError("live_child_bundle_inputs_incomplete")
    if schema_version == _LIVE_CHILD_BUNDLE_SCHEMA:
        try:
            trusted_pyyaml = _json_object(_trusted_pyyaml_child_bundle())
        except (UnicodeError, ValueError) as exc:
            raise _LiveChildManifestError("live_child_pyyaml_bundle_invalid") from exc
    else:
        # Unit-level VFS/replay fixtures never execute the release graph.  A
        # distinct schema prevents this convenience seam from becoming a
        # production provenance downgrade.
        trusted_pyyaml = {
            "schema_version": _PYYAML_CHILD_BUNDLE_SCHEMA,
            "version": PYYAML_VERSION,
            "source_items": [],
        }
    payload: dict[str, Any] = {
        "schema_version": schema_version,
        "root": manifest["root"],
        "main_module": "tools.run_release_gate_readonly_recheck",
        "head": head,
        "head_tree": manifest["head_tree"],
        "module_map": dict(_LIVE_CHILD_MODULE_MAP),
        "files": files,
        "head_absent_paths": manifest["head_absent_paths"],
        "docker_source_paths": list(_LIVE_CHILD_DOCKER_SOURCE_PATHS),
        "git_subjects": subjects,
        "git_records": normalized_git,
        "git_objects": manifest["git_objects"],
        "git_executable": executable,
        "git_provenance": {**manifest["git_provenance"], "head": head},
        "trusted_pyyaml": trusted_pyyaml,
    }
    payload["root_digest"] = _live_child_root_digest(payload)
    return payload


def _build_live_child_bundle(**kwargs: Any) -> bytes:
    payload = _build_live_child_bundle_data(**kwargs)
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _live_child_validate_pyyaml_payload(
    value: object, *, test_fixture: bool,
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "version", "source_items"
    }:
        raise _LiveChildManifestError("live_child_pyyaml_shape_invalid")
    if (
        value.get("schema_version") != _PYYAML_CHILD_BUNDLE_SCHEMA
        or value.get("version") != PYYAML_VERSION
        or type(value.get("source_items")) is not list
    ):
        raise _LiveChildManifestError("live_child_pyyaml_identity_invalid")
    sources: dict[str, str] = {}
    authority = {
        relative: (size, digest)
        for relative, size, digest in PYYAML_SOURCE_MANIFEST
    }
    if test_fixture and value["source_items"] == []:
        return {
            "schema_version": _PYYAML_CHILD_BUNDLE_SCHEMA,
            "version": PYYAML_VERSION,
            "source_items": [],
        }
    for item in value["source_items"]:
        if (
            type(item) is not list
            or len(item) != 2
            or any(type(part) is not str for part in item)
        ):
            raise _LiveChildManifestError("live_child_pyyaml_source_invalid")
        relative, encoded = item
        if relative in sources:
            raise _LiveChildManifestError("live_child_pyyaml_source_duplicate")
        raw = _live_child_decode_base64(
            encoded, "live_child_pyyaml_source_base64_invalid"
        )
        expected = authority.get(relative)
        if expected is None or expected != (
            len(raw), hashlib.sha256(raw).hexdigest()
        ):
            raise _LiveChildManifestError("live_child_pyyaml_source_changed")
        sources[relative] = encoded
    if set(sources) != set(authority):
        raise _LiveChildManifestError("live_child_pyyaml_source_set_invalid")
    return {
        "schema_version": _PYYAML_CHILD_BUNDLE_SCHEMA,
        "version": PYYAML_VERSION,
        "source_items": [[path, sources[path]] for path in authority],
    }


def _live_child_normalize_git_objects(value: object) -> list[dict[str, Any]]:
    if type(value) is not list or len(value) > 4096:
        raise _LiveChildManifestError("live_child_git_objects_malformed")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    exact = {"type", "oid", "size", "sha256", "content_b64"}
    for item in value:
        if not isinstance(item, Mapping) or set(item) != exact:
            raise _LiveChildManifestError("live_child_git_object_malformed")
        object_type = item.get("type")
        oid = item.get("oid")
        size = item.get("size")
        digest = item.get("sha256")
        if (
            object_type not in {"blob", "tree", "commit"}
            or type(oid) is not str
            or _FULL_HEX_SHA.fullmatch(oid) is None
            or oid in seen
            or type(size) is not int
            or not 0 <= size <= _LIVE_CHILD_MAX_FILE_BYTES
            or type(digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise _LiveChildManifestError("live_child_git_object_malformed")
        raw = _live_child_decode_base64(
            item.get("content_b64"), "live_child_git_object_base64_invalid"
        )
        framed = (
            object_type.encode("ascii") + b" " + str(len(raw)).encode("ascii")
            + b"\0" + raw
        )
        if (
            len(raw) != size
            or hashlib.sha256(raw).hexdigest() != digest
            or hashlib.sha1(framed, usedforsecurity=False).hexdigest() != oid
        ):
            raise _LiveChildManifestError("live_child_git_object_content_mismatch")
        total += len(raw)
        if total > _LIVE_CHILD_MAX_TOTAL_BYTES:
            raise _LiveChildManifestError("live_child_git_objects_too_large")
        seen.add(oid)
        normalized.append({
            "type": object_type,
            "oid": oid,
            "size": size,
            "sha256": digest,
            "content_b64": item["content_b64"],
        })
    return sorted(normalized, key=lambda item: item["oid"])


def _live_child_decode_bundle(
    raw: bytes, *, allow_test_fixture: bool = True,
) -> dict[str, Any]:
    """Strictly authenticate one immutable live-child bundle."""
    if type(raw) is not bytes or not 0 < len(raw) <= 8 * 1024 * 1024:
        raise _LiveChildManifestError("live_child_bundle_size_invalid")
    try:
        raw.decode("ascii")
        payload = _json_object(raw)
    except (UnicodeError, ValueError) as exc:
        raise _LiveChildManifestError("live_child_bundle_json_invalid") from exc
    exact_keys = {
        "schema_version", "root", "main_module", "head", "head_tree",
        "module_map", "files", "head_absent_paths", "docker_source_paths",
        "git_subjects", "git_records", "git_objects", "git_executable",
        "git_provenance", "trusted_pyyaml", "root_digest",
    }
    if set(payload) != exact_keys:
        raise _LiveChildManifestError("live_child_bundle_shape_invalid")
    claimed_digest = payload.get("root_digest")
    schema_version = payload.get("schema_version")
    test_fixture = schema_version == _LIVE_CHILD_TEST_BUNDLE_SCHEMA
    if test_fixture and not allow_test_fixture:
        raise _LiveChildManifestError("live_child_test_bundle_not_allowed")
    if (
        type(claimed_digest) is not str
        or re.fullmatch(r"[0-9a-f]{64}", claimed_digest) is None
        or claimed_digest != _live_child_root_digest(payload)
    ):
        raise _LiveChildManifestError("live_child_bundle_root_digest_mismatch")
    root = payload.get("root")
    head = payload.get("head")
    head_tree = payload.get("head_tree")
    if (
        type(root) is not str
        or not root
        or "\0" in root
        or not Path(root).is_absolute()
        or schema_version not in {
            _LIVE_CHILD_BUNDLE_SCHEMA, _LIVE_CHILD_TEST_BUNDLE_SCHEMA
        }
        or payload.get("main_module") != "tools.run_release_gate_readonly_recheck"
        or type(head) is not str
        or _FULL_HEX_SHA.fullmatch(head) is None
        or not (
            head_tree is None
            or type(head_tree) is str
            and _FULL_HEX_SHA.fullmatch(head_tree) is not None
        )
    ):
        raise _LiveChildManifestError("live_child_bundle_identity_invalid")
    module_map = payload.get("module_map")
    if not isinstance(module_map, Mapping) or dict(module_map) != _LIVE_CHILD_MODULE_MAP:
        raise _LiveChildManifestError("live_child_module_map_invalid")
    if payload.get("head_absent_paths") != list(_LIVE_CHILD_OPTIONAL_ABSENT_PATHS):
        raise _LiveChildManifestError("live_child_absence_set_invalid")
    if payload.get("docker_source_paths") != list(_LIVE_CHILD_DOCKER_SOURCE_PATHS):
        raise _LiveChildManifestError("live_child_docker_source_set_invalid")
    files, raw_files = _live_child_normalize_file_records(payload.get("files"))
    records, subjects, replay_head = _live_child_normalize_git_records(
        payload.get("git_records"),
        expected_root=root,
        expected_commits=_live_child_evidence_commits(raw_files),
    )
    if replay_head != head or payload.get("git_subjects") != subjects:
        raise _LiveChildManifestError("live_child_git_replay_binding_mismatch")
    executable = _live_child_normalize_git_executable(
        payload.get("git_executable")
    )
    provenance = payload.get("git_provenance")
    if (
        not isinstance(provenance, Mapping)
        or set(provenance) not in (
            {"object_format", "clean", "head"},
            {"object_format", "clean", "head", "tree"},
        )
        or provenance.get("object_format") != "sha1"
        or provenance.get("clean") is not True
        or provenance.get("head") != head
        or ("tree" in provenance and provenance.get("tree") != head_tree)
    ):
        raise _LiveChildManifestError("live_child_git_provenance_invalid")
    objects = _live_child_normalize_git_objects(payload.get("git_objects"))
    if not test_fixture and (
        head_tree is None
        or not objects
        or set(provenance) != {"object_format", "clean", "head", "tree"}
    ):
        raise _LiveChildManifestError("live_child_production_provenance_incomplete")
    if head_tree is not None and not any(
        item["oid"] == head_tree and item["type"] == "tree" for item in objects
    ):
        raise _LiveChildManifestError("live_child_head_tree_unbound")
    trusted_pyyaml = _live_child_validate_pyyaml_payload(
        payload.get("trusted_pyyaml"), test_fixture=test_fixture,
    )
    return {
        "schema_version": schema_version,
        "root": root,
        "main_module": "tools.run_release_gate_readonly_recheck",
        "head": head,
        "head_tree": head_tree,
        "module_map": dict(_LIVE_CHILD_MODULE_MAP),
        "files": files,
        "head_absent_paths": list(_LIVE_CHILD_OPTIONAL_ABSENT_PATHS),
        "docker_source_paths": list(_LIVE_CHILD_DOCKER_SOURCE_PATHS),
        "git_subjects": subjects,
        "git_records": records,
        "git_objects": objects,
        "git_executable": executable,
        "git_provenance": dict(provenance),
        "trusted_pyyaml": trusted_pyyaml,
        "root_digest": claimed_digest,
    }


class _LiveChildRuntime:
    """Pure parent-side model of the child VFS/replay contract for tests."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = dict(payload)
        self.root = os.path.normpath(str(payload["root"]))
        self._root_key = os.path.normcase(self.root)
        self._files = {
            self._key(item["path"]): base64.b64decode(
                item["content_b64"].encode("ascii"), validate=True
            )
            for item in payload["files"]
        }
        self._absent = {
            self._key(path) for path in payload["head_absent_paths"]
        }
        self._dirs = {self._root_key}
        for key in self._files:
            current = os.path.dirname(key)
            while current and (
                current == self._root_key
                or current.startswith(self._root_key + os.sep)
            ):
                self._dirs.add(current)
                if current == self._root_key:
                    break
                current = os.path.dirname(current)
        self._git_records = {
            (tuple(item["args"]), item["text"]): item
            for item in payload["git_records"]
        }
        self.violation: str | None = None
        self.process_spawn_count = 0

    def _violate(self, operation: str, message: str) -> None:
        if self.violation is None:
            self.violation = operation
        raise PermissionError(message)

    def _key(self, value: object) -> str:
        if isinstance(value, os.PathLike):
            value = os.fspath(value)
        if type(value) is not str or "\0" in value:
            self._violate("path", "invalid virtual path")
        assert isinstance(value, str)
        drive, _ = os.path.splitdrive(value)
        if drive and not os.path.isabs(value):
            self._violate("path", "drive-relative virtual path")
        candidate = os.path.normpath(
            value if os.path.isabs(value) else os.path.join(self.root, value)
        )
        key = os.path.normcase(candidate)
        if key != self._root_key and not key.startswith(self._root_key + os.sep):
            self._violate("path", "outside virtual root")
        return key

    def read_bytes(self, path: object) -> bytes:
        key = self._key(path)
        try:
            return self._files[key]
        except KeyError:
            self._violate("read_bytes", "undeclared virtual content")

    def exists(self, path: object) -> bool:
        key = self._key(path)
        if key in self._files or key in self._dirs:
            return True
        if key in self._absent:
            return False
        self._violate("exists", "undeclared virtual metadata probe")

    def is_file(self, path: object) -> bool:
        key = self._key(path)
        if key in self._files:
            return True
        if key in self._dirs or key in self._absent:
            return False
        self._violate("is_file", "undeclared virtual metadata probe")

    def is_dir(self, path: object) -> bool:
        key = self._key(path)
        if key in self._dirs:
            return True
        if key in self._files or key in self._absent:
            return False
        self._violate("is_dir", "undeclared virtual metadata probe")

    def run_git(
        self, args: list[str], *, text: bool, check: bool = False
    ) -> subprocess.CompletedProcess[Any]:
        key = (tuple(args), bool(text))
        item = self._git_records.get(key)
        if item is None:
            self._violate("subprocess.run", "unrecorded git command")
        assert item is not None
        stdout_raw = base64.b64decode(
            item["stdout_b64"].encode("ascii"), validate=True
        )
        stderr_raw = base64.b64decode(
            item["stderr_b64"].encode("ascii"), validate=True
        )
        stdout: str | bytes = stdout_raw.decode("utf-8") if text else stdout_raw
        stderr: str | bytes = stderr_raw.decode("utf-8") if text else stderr_raw
        completed = subprocess.CompletedProcess(
            list(args), item["returncode"], stdout, stderr
        )
        if check and completed.returncode:
            raise subprocess.CalledProcessError(
                completed.returncode, completed.args,
                output=completed.stdout, stderr=completed.stderr,
            )
        return completed


def _live_child_runtime(bundle: bytes) -> _LiveChildRuntime:
    return _LiveChildRuntime(_live_child_decode_bundle(bundle))


_LIVE_CHILD_VIOLATION_EXIT_CODE = 70
_LIVE_CHILD_EXCEPTION_EXIT_CODE = 71
_LIVE_CHILD_STDERR_EXIT_CODE = 72
_LIVE_CHILD_OVERSIZE_EXIT_CODE = 73

_LIVE_CHILD_RUNTIME_SOURCE = r'''
from __future__ import annotations
import argparse, base64, binascii, builtins, collections, contextlib, copy
import dataclasses, datetime, enum, fnmatch, functools, gettext, hashlib
import importlib, importlib.machinery, importlib.util, io, _io, json, locale
import math, ntpath, operator, os, pathlib, posixpath, re, shlex, socket
import stat, string, struct, subprocess, sys, sysconfig, time, tomllib
import types, typing, unicodedata, warnings, weakref, zipimport, _imp
try:
    import _socket
except ImportError:
    _socket = None

_PROD_SCHEMA = "__PROD_SCHEMA__"
_TEST_SCHEMA = "__TEST_SCHEMA__"
_BUNDLE_DOMAIN = bytes.fromhex("__BUNDLE_DOMAIN_HEX__")
_EXPECTED_FILES = __EXPECTED_FILES__
_MODULE_MAP = __MODULE_MAP__
_OPTIONAL_ABSENT = __OPTIONAL_ABSENT__
_DOCKER_PATHS = __DOCKER_PATHS__
_PYYAML_SCHEMA = "__PYYAML_SCHEMA__"
_PYYAML_VERSION = "__PYYAML_VERSION__"
_PYYAML_MANIFEST = __PYYAML_MANIFEST__
_FALSE_BOUNDARY = {
    "stable_release_claim": False, "tag_creation": False,
    "docker_latest_move": False, "external_effect_authority_change": False,
}
_READ_ONLY = {
    "no_tag_created": True, "no_docker_latest_moved": True,
    "no_stable_release_claim": True,
    "no_external_effect_authority_change": True,
    "release_gate_effect": "observation_only",
}
_MAX_BUNDLE = 8 * 1024 * 1024
_MAX_ITEM = 16 * 1024 * 1024
_MAX_TOTAL = 64 * 1024 * 1024

def _pairs(items):
    result = {}
    for key, value in items:
        if type(key) is not str or key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result

def _reject_constant(_value):
    raise ValueError("non-finite JSON")

def _b64(value, label):
    if type(value) is not str:
        raise ValueError(label)
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeError, ValueError) as exc:
        raise ValueError(label) from exc
    if base64.b64encode(raw).decode("ascii") != value:
        raise ValueError(label)
    return raw

def _blob_oid(raw):
    framed = b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
    return hashlib.sha1(framed, usedforsecurity=False).hexdigest()

def _object_oid(kind, raw):
    framed = kind.encode("ascii") + b" " + str(len(raw)).encode("ascii") + b"\0" + raw
    return hashlib.sha1(framed, usedforsecurity=False).hexdigest()

def _valid_rel(value):
    if type(value) is not str or not value or len(value) > 512:
        raise ValueError("path")
    if value != unicodedata.normalize("NFC", value):
        raise ValueError("path nfc")
    if value.startswith("/") or value.endswith("/") or "\\" in value:
        raise ValueError("path relative")
    parts = value.split("/")
    if any(
        part in {"", ".", ".."} or part.endswith((" ", "."))
        or re.fullmatch(r"[A-Za-z0-9._-]+", part) is None
        for part in parts
    ):
        raise ValueError("path portable")
    if any(part.casefold() == ".git" for part in parts):
        raise ValueError("path git")
    return value

def _root_digest(payload):
    unsigned = dict(payload)
    unsigned.pop("root_digest", None)
    canonical = json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(_BUNDLE_DOMAIN + canonical).hexdigest()

_raw_bundle = sys.stdin.buffer.read(_MAX_BUNDLE + 1)
if not _raw_bundle or len(_raw_bundle) > _MAX_BUNDLE:
    raise ValueError("bundle size")
try:
    _bundle = json.loads(
        _raw_bundle.decode("ascii"), object_pairs_hook=_pairs,
        parse_constant=_reject_constant,
    )
except (UnicodeError, ValueError) as exc:
    raise ValueError("bundle json") from exc
if type(_bundle) is not dict or set(_bundle) != {
    "schema_version", "root", "main_module", "head", "head_tree",
    "module_map", "files", "head_absent_paths", "docker_source_paths",
    "git_subjects", "git_records", "git_objects", "git_executable",
    "git_provenance", "trusted_pyyaml", "root_digest",
}:
    raise ValueError("bundle shape")
_test_mode = sys.argv[1:2] == ["--wd-test-source"]
_schema = _bundle["schema_version"]
if _schema != (_TEST_SCHEMA if _test_mode else _PROD_SCHEMA):
    raise ValueError("bundle schema")
if (
    type(_bundle["root_digest"]) is not str
    or _bundle["root_digest"] != _root_digest(_bundle)
):
    raise ValueError("bundle digest")
if (
    type(_bundle["root"]) is not str or not os.path.isabs(_bundle["root"])
    or "\0" in _bundle["root"]
    or _bundle["main_module"] != "tools.run_release_gate_readonly_recheck"
    or type(_bundle["head"]) is not str
    or re.fullmatch(r"[0-9a-f]{40}", _bundle["head"]) is None
    or _bundle["module_map"] != _MODULE_MAP
    or _bundle["head_absent_paths"] != list(_OPTIONAL_ABSENT)
    or _bundle["docker_source_paths"] != list(_DOCKER_PATHS)
):
    raise ValueError("bundle identity")

_files = {}
_case_paths = set()
_total = 0
if type(_bundle["files"]) is not list or len(_bundle["files"]) != len(_EXPECTED_FILES):
    raise ValueError("file closure")
for item in _bundle["files"]:
    if type(item) is not dict or set(item) != {
        "path", "mode", "oid", "size", "sha256", "content_b64"
    }:
        raise ValueError("file record")
    path = _valid_rel(item["path"])
    if path in _files or path.casefold() in _case_paths:
        raise ValueError("file collision")
    raw = _b64(item["content_b64"], "file base64")
    if (
        item["mode"] not in {"100644", "100755"}
        or type(item["size"]) is not int or item["size"] != len(raw)
        or len(raw) > _MAX_ITEM
        or type(item["sha256"]) is not str
        or item["sha256"] != hashlib.sha256(raw).hexdigest()
        or type(item["oid"]) is not str or item["oid"] != _blob_oid(raw)
    ):
        raise ValueError("file binding")
    _files[path] = (item, raw)
    _case_paths.add(path.casefold())
    _total += len(raw)
if tuple(_files) != tuple(_EXPECTED_FILES) or _total > _MAX_TOTAL:
    raise ValueError("file closure exact")

def _json_file(path):
    value = json.loads(
        _files[path][1].decode("utf-8"), object_pairs_hook=_pairs,
        parse_constant=_reject_constant,
    )
    if type(value) is not dict:
        raise ValueError("JSON object")
    return value

_soak = _json_file("docs/runs/release_soak_evidence/v3.12.0.json")
_docker_report = _json_file("docs/runs/release_soak_evidence/v3.12.0_docker_policy.json")
_commits = {_soak.get("commit"), _docker_report.get("commit")}
if not 1 <= len(_commits) <= 2 or any(
    type(value) is not str or re.fullmatch(r"[0-9a-f]{40}", value) is None
    for value in _commits
):
    raise ValueError("commit authority")

_git_records = {}
_git_total = 0
if type(_bundle["git_records"]) is not list or len(_bundle["git_records"]) > 128:
    raise ValueError("git records")
for item in _bundle["git_records"]:
    if type(item) is not dict or set(item) != {
        "args", "text", "returncode", "stdout_b64", "stderr_b64"
    }:
        raise ValueError("git record")
    args = item["args"]
    if (
        type(args) is not list or not args
        or any(type(arg) is not str or not arg or "\0" in arg for arg in args)
        or type(item["text"]) is not bool
        or type(item["returncode"]) is not int
        or not 0 <= item["returncode"] <= 255
    ):
        raise ValueError("git record shape")
    stdout = _b64(item["stdout_b64"], "git stdout")
    stderr = _b64(item["stderr_b64"], "git stderr")
    if stderr or len(stdout) > _MAX_ITEM:
        raise ValueError("git output")
    key = (tuple(args), item["text"])
    if key in _git_records:
        raise ValueError("git duplicate")
    _git_records[key] = (item["returncode"], stdout, stderr)
    _git_total += len(stdout)
if _git_total > _MAX_TOTAL:
    raise ValueError("git output aggregate")

def _git_response(args, text):
    try:
        return _git_records[(tuple(args), bool(text))]
    except KeyError as exc:
        raise ValueError("git replay incomplete") from exc

if _git_response(["rev-parse", "--show-toplevel"], True) != (
    0, _bundle["root"].encode("utf-8"), b""
):
    raise ValueError("git root")
if _git_response(["rev-parse", "HEAD"], True) != (
    0, _bundle["head"].encode("ascii"), b""
):
    raise ValueError("git head")
_subjects = {}
_allowed_git = {
    (("rev-parse", "--show-toplevel"), True),
    (("rev-parse", "HEAD"), True),
}
for commit in sorted(_commits):
    commit_args = ["rev-parse", "--verify", commit + "^{commit}"]
    _allowed_git.add((tuple(commit_args), True))
    if _git_response(commit_args, True) != (0, commit.encode("ascii"), b""):
        raise ValueError("git commit")
    paths = {}
    for path in _DOCKER_PATHS:
        args = ["rev-parse", "--verify", commit + ":" + path]
        _allowed_git.add((tuple(args), True))
        rc, raw, _ = _git_response(args, True)
        if rc == 128 and raw == b"":
            paths[path] = {"present": False}
            continue
        if rc != 0:
            raise ValueError("git absence")
        try:
            oid = raw.decode("ascii")
        except UnicodeError as exc:
            raise ValueError("git oid") from exc
        if re.fullmatch(r"[0-9a-f]{40}", oid) is None:
            raise ValueError("git oid")
        blob_args = ["cat-file", "blob", oid]
        _allowed_git.add((tuple(blob_args), False))
        blob_rc, blob, _ = _git_response(blob_args, False)
        if blob_rc != 0 or _blob_oid(blob) != oid:
            raise ValueError("git blob")
        paths[path] = {
            "present": True, "oid": oid, "size": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
            "content_b64": base64.b64encode(blob).decode("ascii"),
        }
    _subjects[commit] = paths
if set(_git_records) != _allowed_git or _bundle["git_subjects"] != _subjects:
    raise ValueError("git replay exact")
_git_records = types.MappingProxyType(dict(_git_records))
'''

_LIVE_CHILD_RUNTIME_SOURCE += r'''
_objects = {}
_object_total = 0
if type(_bundle["git_objects"]) is not list or len(_bundle["git_objects"]) > 4096:
    raise ValueError("git objects")
for item in _bundle["git_objects"]:
    if type(item) is not dict or set(item) != {
        "type", "oid", "size", "sha256", "content_b64"
    }:
        raise ValueError("git object")
    kind, oid = item["type"], item["oid"]
    raw = _b64(item["content_b64"], "git object base64")
    if (
        kind not in {"blob", "tree", "commit"}
        or type(oid) is not str or re.fullmatch(r"[0-9a-f]{40}", oid) is None
        or oid in _objects
        or type(item["size"]) is not int or item["size"] != len(raw)
        or len(raw) > _MAX_ITEM
        or type(item["sha256"]) is not str
        or item["sha256"] != hashlib.sha256(raw).hexdigest()
        or _object_oid(kind, raw) != oid
    ):
        raise ValueError("git object binding")
    _objects[oid] = (kind, raw)
    _object_total += len(raw)
if _object_total > _MAX_TOTAL:
    raise ValueError("git objects aggregate")

def _object(oid, kind, seen):
    try:
        actual_kind, raw = _objects[oid]
    except KeyError as exc:
        raise ValueError("git object missing") from exc
    if actual_kind != kind:
        raise ValueError("git object type")
    seen.add(oid)
    return raw

def _commit_tree(oid, seen):
    raw = _object(oid, "commit", seen)
    first, separator, _ = raw.partition(b"\n")
    if not separator or re.fullmatch(rb"tree [0-9a-f]{40}", first) is None:
        raise ValueError("commit tree")
    return first[5:].decode("ascii")

def _tree_entries(oid, seen):
    raw = _object(oid, "tree", seen)
    result, cases = {}, set()
    position = 0
    previous = None
    while position < len(raw):
        space = raw.find(b" ", position)
        nul = raw.find(b"\0", space + 1) if space >= 0 else -1
        if space <= position or nul <= space + 1 or nul + 21 > len(raw):
            raise ValueError("tree framing")
        mode_raw, name_raw = raw[position:space], raw[space + 1:nul]
        child_oid = raw[nul + 1:nul + 21].hex()
        position = nul + 21
        try:
            mode, name = mode_raw.decode("ascii"), name_raw.decode("utf-8")
        except UnicodeError as exc:
            raise ValueError("tree name") from exc
        _valid_rel(name)
        if mode not in {"40000", "100644", "100755", "120000", "160000"}:
            raise ValueError("tree mode")
        sort_key = name_raw + (b"/" if mode == "40000" else b"")
        if previous is not None and sort_key <= previous:
            raise ValueError("tree order")
        previous = sort_key
        if name in result or name.casefold() in cases:
            raise ValueError("tree collision")
        result[name] = (mode, child_oid)
        cases.add(name.casefold())
    return result

def _resolve(tree_oid, relative, seen):
    current = tree_oid
    parts = _valid_rel(relative).split("/")
    for index, part in enumerate(parts):
        entry = _tree_entries(current, seen).get(part)
        if entry is None:
            return None
        mode, oid = entry
        if index < len(parts) - 1:
            if mode != "40000":
                raise ValueError("path intermediate")
            current = oid
            continue
        if mode in {"120000", "160000", "40000"}:
            raise ValueError("path unsafe")
        raw = _object(oid, "blob", seen)
        return mode, oid, raw
    raise ValueError("path empty")

if _test_mode:
    if (
        _bundle["head_tree"] is not None or _objects
        or _bundle["git_provenance"] != {
            "object_format": "sha1", "clean": True, "head": _bundle["head"]
        }
    ):
        raise ValueError("test provenance")
else:
    head_tree = _bundle["head_tree"]
    provenance = _bundle["git_provenance"]
    if (
        type(head_tree) is not str or re.fullmatch(r"[0-9a-f]{40}", head_tree) is None
        or provenance != {
            "object_format": "sha1", "head": _bundle["head"],
            "tree": head_tree, "clean": True,
        }
    ):
        raise ValueError("production provenance")
    _seen_objects = set()
    if _commit_tree(_bundle["head"], _seen_objects) != head_tree:
        raise ValueError("head tree")
    for path, (entry, raw) in _files.items():
        resolved = _resolve(head_tree, path, _seen_objects)
        if resolved is None or resolved != (entry["mode"], entry["oid"], raw):
            raise ValueError("head file proof")
    for path in _OPTIONAL_ABSENT:
        if _resolve(head_tree, path, _seen_objects) is not None:
            raise ValueError("head absence proof")
    for commit, paths in _subjects.items():
        tree = _commit_tree(commit, _seen_objects)
        for path, fact in paths.items():
            resolved = _resolve(tree, path, _seen_objects)
            if fact["present"] is False:
                if resolved is not None:
                    raise ValueError("history absence proof")
            elif (
                resolved is None or resolved[1] != fact["oid"]
                or resolved[2] != _b64(fact["content_b64"], "history content")
            ):
                raise ValueError("history file proof")
    if set(_objects) != _seen_objects:
        raise ValueError("git object closure")

_pyyaml = _bundle["trusted_pyyaml"]
if type(_pyyaml) is not dict or set(_pyyaml) != {
    "schema_version", "version", "source_items"
} or _pyyaml["schema_version"] != _PYYAML_SCHEMA or _pyyaml["version"] != _PYYAML_VERSION:
    raise ValueError("pyyaml identity")
_yaml_authority = {path: (size, digest) for path, size, digest in _PYYAML_MANIFEST}
_yaml_files = {}
if _test_mode and _pyyaml["source_items"] == []:
    pass
else:
    if type(_pyyaml["source_items"]) is not list:
        raise ValueError("pyyaml sources")
    for item in _pyyaml["source_items"]:
        if type(item) is not list or len(item) != 2 or any(type(x) is not str for x in item):
            raise ValueError("pyyaml source")
        path, encoded = item
        if path in _yaml_files:
            raise ValueError("pyyaml duplicate")
        raw = _b64(encoded, "pyyaml base64")
        if _yaml_authority.get(path) != (len(raw), hashlib.sha256(raw).hexdigest()):
            raise ValueError("pyyaml binding")
        _yaml_files[path] = raw
    if set(_yaml_files) != set(_yaml_authority):
        raise ValueError("pyyaml closure")

_ROOT = os.path.normpath(_bundle["root"])
_ROOT_KEY = os.path.normcase(_ROOT)
_normpath, _normcase = os.path.normpath, os.path.normcase
_splitdrive, _isabs = os.path.splitdrive, os.path.isabs
_join, _dirname, _basename = os.path.join, os.path.dirname, os.path.basename
_sep = os.sep
_selected_git = _bundle["git_executable"]
if type(_selected_git) is not dict or set(_selected_git) != {"path", "sha256"}:
    raise ValueError("git executable")
_GIT_PATH = _selected_git["path"]
if type(_GIT_PATH) is not str or not _isabs(_GIT_PATH) or re.fullmatch(
    r"[0-9a-f]{64}", _selected_git["sha256"]
) is None:
    raise ValueError("git executable")
_external_nodes = {
    _normcase(_normpath(_GIT_PATH)): True
}
_external_nodes = types.MappingProxyType(_external_nodes)
_VFILES = {}
for path, (_entry, raw) in _files.items():
    absolute = _normpath(_join(_ROOT, path.replace("/", _sep)))
    _VFILES[_normcase(absolute)] = (absolute, raw)
_VABSENT = {
    _normcase(_normpath(_join(_ROOT, path.replace("/", _sep))))
    for path in _OPTIONAL_ABSENT
}
_VDIRS = {_ROOT_KEY}
for key, (display, _raw) in _VFILES.items():
    current = _dirname(display)
    while current:
        current_key = _normcase(current)
        if current_key != _ROOT_KEY and not current_key.startswith(_ROOT_KEY + _sep):
            break
        _VDIRS.add(current_key)
        if current_key == _ROOT_KEY:
            break
        current = _dirname(current)
_VFILES = types.MappingProxyType(dict(_VFILES))
_VABSENT = frozenset(_VABSENT)
_VDIRS = frozenset(_VDIRS)

_argparse_help_formatter_init = argparse.HelpFormatter.__init__
def _deterministic_help_formatter_init(
    self, prog, indent_increment=2, max_help_position=24, width=None,
    _original=_argparse_help_formatter_init,
):
    # argparse otherwise lazily imports shutil after the exact finder is
    # sealed merely to observe ambient terminal width.  Keep help formatting
    # deterministic and leave shutil outside the child capability closure.
    return _original(
        self, prog, indent_increment, max_help_position,
        78 if width is None else width,
    )
argparse.HelpFormatter.__init__ = _deterministic_help_formatter_init

def _violate(operation, message=None, _terminate=os._exit):
    _terminate(70)
    raise SystemExit(70)

def _install_audit_hook(
    protected_ids, _terminate=os._exit, _length=len, _type=type, _str=str,
    _frozenset=frozenset, _id=id,
):
    frame_getters = _frozenset({
        "tb_frame", "f_back", "f_builtins", "f_code", "f_globals",
        "f_lasti", "f_lineno", "f_locals", "f_trace", "gi_frame",
        "cr_frame", "ag_frame",
    })
    authority_getters = _frozenset({
        "__closure__", "__code__", "__globals__",
        "__defaults__", "__kwdefaults__", "__globals__",
    })
    sensitive_setters = _frozenset({
        "__class__", "__code__", "__defaults__", "__kwdefaults__",
        "f_trace", "f_trace_lines", "f_trace_opcodes",
    })
    forbidden_exact = _frozenset({
        "sys._getframe", "sys._current_frames", "sys.settrace",
        "sys.setprofile", "sys.addaudithook", "code.__new__",
        "function.__new__", "marshal.loads", "builtins.breakpoint",
        "builtins.input",
    })
    forbidden_prefixes = (
        "os.", "subprocess.", "socket.", "ctypes.", "winreg."
    )
    def hook(event, args):
        attribute = (
            args[1]
            if _length(args) > 1 and _type(args[1]) is _str
            else None
        )
        sensitive_object = (
            event == "object.__getattr__" and (
                attribute in frame_getters
                or (
                    attribute in authority_getters
                    and _length(args) > 0
                    and _id(args[0]) in protected_ids
                )
            )
        ) or (
            event == "object.__setattr__" and attribute in sensitive_setters
        )
        forbidden = (
            event == "open" or event.startswith(forbidden_prefixes)
            or event in forbidden_exact
            or sensitive_object
        )
        if forbidden:
            _terminate(70)
    sys.addaudithook(hook)

def _vkey(
    value, operation="path", allow_external=False, _deny=_violate,
    _type=type, _int=int, _bytes=bytes, _bytearray=bytearray,
    _isinstance=isinstance, _fspath=os.fspath, _type_error=TypeError,
    _value_error=ValueError, _str=str, _split=_splitdrive, _absolute=_isabs,
    _normalize=_normpath, _join_path=_join, _casefold=_normcase,
    _root=_ROOT, _root_key=_ROOT_KEY, _separator=_sep,
    _external=_external_nodes,
):
    if _type(value) is _int or _isinstance(value, (_bytes, _bytearray)):
        _deny(operation, "invalid path type")
    try:
        text = _fspath(value)
    except (_type_error, _value_error):
        _deny(operation, "invalid path type")
    if _type(text) is not _str or "\0" in text:
        _deny(operation, "invalid path")
    drive, _ = _split(text)
    if drive and not _absolute(text):
        _deny(operation, "drive-relative path")
    normalized = _normalize(text if _absolute(text) else _join_path(_root, text))
    key = _casefold(normalized)
    inside = key == _root_key or key.startswith(_root_key + _separator)
    if not inside and not (allow_external and key in _external):
        _deny(operation, "outside virtual root")
    return normalized, key

def _vnode(
    value, operation="path", allow_external=False, _deny=_violate,
    _key_for=_vkey, _files=_VFILES, _directories=_VDIRS,
    _absent=_VABSENT, _external=_external_nodes,
):
    absolute, key = _key_for(value, operation, allow_external)
    if key in _files:
        return absolute, key, "file", _files[key][1]
    if key in _directories:
        return absolute, key, "dir", None
    if key in _absent:
        return absolute, key, "missing", None
    if allow_external and key in _external:
        return absolute, key, ("file" if _external[key] else "missing"), None
    _deny(operation, "undeclared virtual path")

def _vstat(
    value, operation="os.stat", _node=_vnode,
    _not_found=FileNotFoundError, _stat_result=os.stat_result,
    _regular=stat.S_IFREG, _directory=stat.S_IFDIR, _length=len,
):
    _absolute, _key, kind, raw = _node(value, operation, True)
    if kind == "missing":
        raise _not_found(_absolute)
    mode = (_regular | 0o444) if kind == "file" else (_directory | 0o555)
    size = _length(raw) if raw is not None else 0
    return _stat_result((mode, 0, 0, 1, 0, 0, size, 0, 0, 0))
'''

_LIVE_CHILD_RUNTIME_SOURCE += r'''
def _vopen(
    file, mode="r", buffering=-1, encoding=None, errors=None, newline=None,
    closefd=True, opener=None, *, operation="builtins.open", _deny=_violate,
    _type=type, _str=str, _any=any, _node=_vnode,
    _not_found=FileNotFoundError, _bytes_io=io.BytesIO,
    _text_io=io.TextIOWrapper,
):
    if opener is not None or closefd is not True or _type(mode) is not _str:
        _deny(operation, "unsupported open contract")
    if _any(flag in mode for flag in "wax+"):
        _deny(operation + ".write", "virtual filesystem mutation")
    if mode not in {"r", "rt", "rb"}:
        _deny(operation, "unsupported read mode")
    _absolute, _key, kind, raw = _node(file, operation)
    if kind == "missing":
        raise _not_found(_absolute)
    if kind != "file" or raw is None:
        _deny(operation, "undeclared content read")
    binary = _bytes_io(raw)
    if "b" in mode:
        return binary
    return _text_io(
        binary, encoding=encoding or "utf-8", errors=errors, newline=newline
    )

def _path_open(
    self, mode="r", buffering=-1, encoding=None, errors=None, newline=None,
    _open_virtual=_vopen,
):
    return _open_virtual(
        self, mode, buffering, encoding, errors, newline,
        operation="pathlib.open",
    )

def _path_read_bytes(
    self, _deny=_violate, _node=_vnode, _not_found=FileNotFoundError,
):
    absolute, _key, kind, raw = _node(self, "pathlib.read_bytes")
    if kind == "missing":
        raise _not_found(absolute)
    if kind != "file" or raw is None:
        _deny("pathlib.read_bytes", "undeclared content read")
    return raw

def _path_read_text(
    self, encoding=None, errors=None, newline=None, _open_virtual=_vopen,
):
    with _open_virtual(
        self, "r", encoding=encoding, errors=errors, newline=newline,
        operation="pathlib.read_text",
    ) as handle:
        return handle.read()

def _path_resolve(
    self, strict=False, _node=_vnode, _not_found=FileNotFoundError,
    _path=pathlib.Path,
):
    absolute, _key, kind, _raw = _node(self, "pathlib.resolve", True)
    if strict and kind == "missing":
        raise _not_found(absolute)
    return _path(absolute)

def _path_absolute(self, _resolve=_path_resolve):
    return _resolve(self, False)

def _path_stat(
    self, *, follow_symlinks=True, _deny=_violate, _stat_virtual=_vstat,
):
    if follow_symlinks is not True:
        _deny("pathlib.stat", "unsupported stat contract")
    return _stat_virtual(self, "pathlib.stat")

def _path_lstat(self, _stat_virtual=_vstat):
    return _stat_virtual(self, "pathlib.lstat")

def _path_exists(
    self, *, follow_symlinks=True, _deny=_violate, _node=_vnode,
):
    if follow_symlinks is not True:
        _deny("pathlib.exists", "unsupported exists contract")
    return _node(self, "pathlib.exists", True)[2] != "missing"

def _path_is_file(
    self, *, follow_symlinks=True, _deny=_violate, _node=_vnode,
):
    if follow_symlinks is not True:
        _deny("pathlib.is_file", "unsupported is_file contract")
    return _node(self, "pathlib.is_file", True)[2] == "file"

def _path_is_dir(
    self, *, follow_symlinks=True, _deny=_violate, _node=_vnode,
):
    if follow_symlinks is not True:
        _deny("pathlib.is_dir", "unsupported is_dir contract")
    return _node(self, "pathlib.is_dir", True)[2] == "dir"

def _path_false(self, *args, _node=_vnode, **kwargs):
    _node(self, "pathlib.metadata", True)
    return False

def _path_samefile(
    self, other, _node=_vnode, _not_found=FileNotFoundError,
):
    left = _node(self, "pathlib.samefile", True)
    right = _node(other, "pathlib.samefile", True)
    if left[2] == "missing" or right[2] == "missing":
        raise _not_found(left[0] if left[2] == "missing" else right[0])
    return left[1] == right[1]

def _path_iterdir(
    self, _deny=_violate, _node=_vnode, _separator=_sep, _files=_VFILES,
    _directories=_VDIRS, _join_path=_join, _path=pathlib.Path, _set=set,
    _iter=iter, _sorted=sorted, _length=len,
):
    absolute, key, kind, _raw = _node(self, "pathlib.iterdir")
    if kind != "dir":
        _deny("pathlib.iterdir", "not a virtual directory")
    children = _set()
    prefix = key + _separator
    for child_key, (display, _raw) in _files.items():
        if child_key.startswith(prefix):
            remainder = display[_length(absolute.rstrip(_separator)) + 1:]
            if _separator not in remainder:
                children.add(_join_path(absolute, remainder))
    for child_key in _directories:
        if child_key.startswith(prefix):
            display = child_key
            remainder = display[_length(key.rstrip(_separator)) + 1:]
            if remainder and _separator not in remainder:
                children.add(_join_path(absolute, remainder))
    return _iter(_path(value) for value in _sorted(children))

def _deny_path(name, deny=_violate):
    def denied(self, *args, _deny=deny, _name=name, **kwargs):
        _deny("pathlib." + _name, "virtual filesystem mutation")
    return denied

_path_classes = {pathlib.Path, pathlib.PosixPath, pathlib.WindowsPath}
for cls in tuple(_path_classes):
    for base in cls.__mro__:
        if getattr(base, "__module__", "").startswith("pathlib"):
            _path_classes.add(base)
_path_observers = {
    "open": _path_open, "read_bytes": _path_read_bytes,
    "read_text": _path_read_text, "resolve": _path_resolve,
    "absolute": _path_absolute, "stat": _path_stat, "lstat": _path_lstat,
    "exists": _path_exists, "is_file": _path_is_file, "is_dir": _path_is_dir,
    "is_symlink": _path_false, "is_junction": _path_false,
    "is_mount": _path_false, "samefile": _path_samefile,
    "iterdir": _path_iterdir,
}
for cls in _path_classes:
    for name, function in _path_observers.items():
        if hasattr(cls, name):
            setattr(cls, name, function)
    for name in (
        "write_text", "write_bytes", "mkdir", "touch", "unlink", "rmdir",
        "rename", "replace", "chmod", "lchmod", "symlink_to", "hardlink_to",
        "link_to", "home", "expanduser", "glob", "rglob", "walk", "readlink",
    ):
        if hasattr(cls, name):
            setattr(cls, name, _deny_path(name))

def _builtin_open(file, mode="r", buffering=-1, encoding=None, errors=None,
                  newline=None, closefd=True, opener=None,
                  _open_virtual=_vopen):
    return _open_virtual(
        file, mode, buffering, encoding, errors, newline, closefd, opener,
        operation="builtins.open",
    )

def _io_open(file, mode="r", buffering=-1, encoding=None, errors=None,
             newline=None, closefd=True, opener=None,
             _open_virtual=_vopen):
    return _open_virtual(
        file, mode, buffering, encoding, errors, newline, closefd, opener,
        operation="io.open",
    )

def _fileio(file, mode="r", closefd=True, opener=None, _open_virtual=_vopen):
    return _open_virtual(
        file, mode if "b" in mode else mode + "b", closefd=closefd,
        opener=opener, operation="io.FileIO",
    )

builtins.open = _builtin_open
io.open = _io_open
_io.open = _io_open
io.FileIO = _fileio
_io.FileIO = _fileio
if hasattr(io, "open_code"):
    io.open_code = lambda path, _open_virtual=_vopen: _open_virtual(
        path, "rb", operation="io.open_code"
    )
if hasattr(_io, "open_code"):
    _io.open_code = lambda path, _open_virtual=_vopen: _open_virtual(
        path, "rb", operation="io.open_code"
    )

def _os_stat(
    path, *, dir_fd=None, follow_symlinks=True, _deny=_violate,
    _stat_virtual=_vstat,
):
    if dir_fd is not None or follow_symlinks is not True:
        _deny("os.stat", "unsupported stat contract")
    return _stat_virtual(path, "os.stat")

def _os_lstat(path, *, dir_fd=None, _deny=_violate, _stat_virtual=_vstat):
    if dir_fd is not None:
        _deny("os.lstat", "unsupported lstat contract")
    return _stat_virtual(path, "os.lstat")

def _os_access(
    path, mode, *, dir_fd=None, effective_ids=False, follow_symlinks=True,
    _deny=_violate, _node=_vnode,
):
    if dir_fd is not None or effective_ids or follow_symlinks is not True:
        _deny("os.access", "unsupported access contract")
    return _node(path, "os.access", True)[2] != "missing"

def _deny_operation(name, deny=_violate):
    def denied(*args, _deny=deny, _name=name, **kwargs):
        _deny(_name, "forbidden primitive")
    return denied

os.stat, os.lstat, os.access = _os_stat, _os_lstat, _os_access
os.getcwd = lambda _root=_ROOT: _root
for name in (
    "open", "unlink", "remove", "rmdir", "mkdir", "makedirs", "rename",
    "replace", "chmod", "lchmod", "link", "symlink", "truncate", "chdir",
    "listdir", "scandir", "readlink", "walk", "fwalk", "fdopen", "read",
    "write", "dup", "dup2",
):
    if hasattr(os, name):
        setattr(os, name, _deny_operation("os." + name))
for module in (ntpath, posixpath, os.path):
    module.exists = lambda path, _node=_vnode: (
        _node(path, "os.path.exists", True)[2] != "missing"
    )
    module.lexists = module.exists
    module.isfile = lambda path, _node=_vnode: (
        _node(path, "os.path.isfile", True)[2] == "file"
    )
    module.isdir = lambda path, _node=_vnode: (
        _node(path, "os.path.isdir", True)[2] == "dir"
    )
    module.islink = lambda path, _node=_vnode: (
        _node(path, "os.path.islink", True)[2] == "symlink"
    )
    module.realpath = lambda path, *args, _node=_vnode, **kwargs: _node(
        path, "os.path.realpath", True
    )[0]
    module.abspath = lambda path, _key_for=_vkey: (
        _key_for(path, "os.path.abspath", True)[0]
    )

_native_origin_modules = {
    name: sys.modules.get(name)
    for name in (
        "nt", "posix", "_winapi", "_posixsubprocess", "msvcrt",
        "winreg", "select", "socket", "_socket", "signal", "_signal",
        "_thread",
    )
}
_native_alias_allow = set()
for _module_name, _attribute_name in (
    ("nt", "fspath"), ("posix", "fspath"),
    ("nt", "_path_normpath"), ("nt", "_path_splitroot_ex"),
    ("_winapi", "LCMapStringEx"),
    ("_thread", "RLock"), ("_thread", "allocate_lock"),
    ("_thread", "get_ident"), ("_thread", "_is_main_interpreter"),
    ("_thread", "_shutdown"),
):
    _module = _native_origin_modules.get(_module_name)
    if _module is not None and hasattr(_module, _attribute_name):
        _native_alias_allow.add(id(getattr(_module, _attribute_name)))
_native_alias_allow = frozenset(_native_alias_allow)
_native_forbidden_objects = []
_native_forbidden_ids = set()
for _module_name, _module in _native_origin_modules.items():
    if _module is None:
        continue
    for _attribute_name, _value in vars(_module).items():
        if (
            isinstance(
                _value,
                (types.BuiltinFunctionType, types.BuiltinMethodType),
            )
            and id(_value) not in _native_alias_allow
            and id(_value) not in _native_forbidden_ids
        ):
            _native_forbidden_ids.add(id(_value))
            _native_forbidden_objects.append(_value)
_native_forbidden_ids = frozenset(_native_forbidden_ids)
_native_forbidden_objects = tuple(_native_forbidden_objects)

for low_name in ("nt", "posix"):
    low = sys.modules.get(low_name)
    if low is not None:
        for name in dir(low):
            if name.startswith("__"):
                continue
            try:
                value = getattr(low, name)
                if callable(value) and id(value) not in _native_alias_allow:
                    setattr(low, name, _deny_operation(low_name + "." + name))
            except (AttributeError, TypeError) as exc:
                raise ValueError("low-level backend unsealable") from exc
        if hasattr(low, "environ"):
            low.environ = types.MappingProxyType({})

os.environ = types.MappingProxyType({
    "PATH": _dirname(_GIT_PATH), "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
})
'''

_LIVE_CHILD_RUNTIME_SOURCE += r'''
def _take_git(
    args, text=True, check=False, _deny=_violate, _records=_git_records,
    _tuple=tuple, _bool=bool, _completed=subprocess.CompletedProcess,
    _called_process_error=subprocess.CalledProcessError, _list=list,
):
    key = (_tuple(args), _bool(text))
    item = _records.get(key)
    if item is None:
        _deny("subprocess.run", "unrecorded git command")
    rc, stdout_raw, stderr_raw = item
    stdout = stdout_raw.decode("utf-8") if text else stdout_raw
    stderr = stderr_raw.decode("utf-8") if text else stderr_raw
    completed = _completed(_list(args), rc, stdout, stderr)
    if check and rc:
        raise _called_process_error(
            rc, _list(args), output=stdout, stderr=stderr
        )
    return completed

def _subprocess_run(
    args, *positional, _deny=_violate, _type=type, _list_type=list,
    _tuple_type=tuple, _any=any, _str=str, _git_path=_GIT_PATH, _root=_ROOT,
    _list=list, _set=set, _bool=bool, _take=_take_git, **kwargs
):
    if positional or _type(args) not in {_list_type, _tuple_type} or _any(
        _type(x) is not _str for x in args
    ):
        _deny("subprocess.run", "unsupported process contract")
    prefix = [
        _git_path, "--no-replace-objects", "-c", "core.hooksPath=", "-C", _root,
    ]
    if _list(args[:6]) != prefix:
        _deny("subprocess.run", "unrecorded process")
    allowed = {"check", "capture_output", "text", "env"}
    if _set(kwargs) - allowed or kwargs.get("capture_output") is not True:
        _deny("subprocess.run", "unsupported process options")
    text_mode = kwargs.get("text", False)
    if _type(text_mode) is not _bool or kwargs.get("check", False) is not True:
        _deny("subprocess.run", "unsupported process options")
    return _take(_list(args[6:]), text_mode, True)

subprocess.run = _subprocess_run
for name in (
    "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput",
    "_fork_exec",
):
    if hasattr(subprocess, name):
        setattr(subprocess, name, _deny_operation("subprocess." + name))
for name in (
    "system", "popen", "execl", "execle", "execlp", "execlpe", "execv",
    "execve", "execvp", "execvpe", "spawnl", "spawnle", "spawnlp",
    "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe", "posix_spawn",
    "posix_spawnp", "startfile", "fork", "forkpty", "_exit",
):
    if hasattr(os, name):
        setattr(os, name, _deny_operation("os." + name))
_winapi = sys.modules.get("_winapi")
_native_backends = _native_origin_modules
for backend_name, backend in _native_backends.items():
    if backend is None:
        continue
    for name in dir(backend):
        if name.startswith("__"):
            continue
        try:
            value = getattr(backend, name)
            if callable(value) and id(value) not in _native_alias_allow:
                setattr(
                    backend, name,
                    _deny_operation(backend_name + "." + name),
                )
        except (AttributeError, TypeError) as exc:
            raise ValueError("native backend unsealable") from exc

def _seal_native_aliases(
    forbidden_ids, _deny_factory=_deny_operation, _modules=sys.modules,
    _module_type=types.ModuleType, _type=type, _isinstance=isinstance,
    _vars=vars, _tuple=tuple, _setattr=setattr, _id=id,
    _attribute_error=AttributeError, _type_error=TypeError,
    _value_error=ValueError, _function_type=types.FunctionType,
    _staticmethod=staticmethod, _classmethod=classmethod, _property=property,
    _partial=functools.partial, _partialmethod=functools.partialmethod,
    _dict=dict, _list=list, _set=set,
    _frozenset=frozenset, _mapping_proxy=types.MappingProxyType,
    _object=object, _subclasses=type.__subclasses__, _getattr=getattr,
):
    def reachable_classes():
        seen = set()
        pending = [_object]
        while pending:
            candidate = pending.pop()
            identity = _id(candidate)
            if identity in seen:
                continue
            seen.add(identity)
            yield candidate
            try:
                pending.extend(_subclasses(candidate))
            except _type_error:
                continue

    def contains_forbidden(value, seen):
        identity = _id(value)
        if identity in forbidden_ids:
            return True
        if identity in seen:
            return False
        seen.add(identity)
        value_type = _type(value)
        if value_type in {_tuple, _list, _set, _frozenset}:
            return any(contains_forbidden(item, seen) for item in value)
        if value_type in {_dict, _mapping_proxy}:
            return any(
                contains_forbidden(key, seen)
                or contains_forbidden(item, seen)
                for key, item in value.items()
            )
        if _isinstance(value, (_partial, _partialmethod)):
            return contains_forbidden(
                (value.func, value.args, value.keywords), seen
            )
        return False

    def function_has_forbidden_binding(value):
        functions = []
        if _isinstance(value, (_staticmethod, _classmethod)):
            functions.append(value.__func__)
        elif _isinstance(value, _property):
            functions.extend((value.fget, value.fset, value.fdel))
        elif _isinstance(value, (_partial, _partialmethod)):
            return contains_forbidden(
                (value.func, value.args, value.keywords), set()
            )
        else:
            functions.append(value)
        for function in functions:
            if function is not None and _id(function) in forbidden_ids:
                return True
            if not _isinstance(function, _function_type):
                continue
            closure_values = []
            for cell in function.__closure__ or ():
                try:
                    closure_values.append(cell.cell_contents)
                except _value_error:
                    continue
            if contains_forbidden(
                (
                    function.__defaults__, function.__kwdefaults__,
                    closure_values,
                ),
                set(),
            ):
                return True
        return False

    def container_has_forbidden_binding(value):
        return (
            _type(value) in {
                _tuple, _list, _set, _frozenset, _dict, _mapping_proxy,
            }
            and contains_forbidden(value, set())
        )

    def replace(owner, name, label, container_type=None):
        try:
            replacement = (
                _mapping_proxy({})
                if container_type in {_dict, _mapping_proxy}
                else _frozenset()
                if container_type is not None
                else _deny_factory("native-alias." + label)
            )
            _setattr(owner, name, replacement)
        except (_attribute_error, _type_error) as exc:
            raise _value_error("native alias unsealable") from exc

    for module_name, module in _tuple(_modules.items()):
        if not _isinstance(module, _module_type):
            continue
        for name, value in _tuple(_vars(module).items()):
            container_binding = container_has_forbidden_binding(value)
            if (
                _id(value) in forbidden_ids
                or function_has_forbidden_binding(value)
                or container_binding
            ):
                replace(
                    module, name, module_name + "." + name,
                    _type(value) if container_binding else None,
                )
        for class_name, candidate in _tuple(_vars(module).items()):
            if not _isinstance(candidate, _type):
                continue
            for name, value in _tuple(_vars(candidate).items()):
                container_binding = container_has_forbidden_binding(value)
                if (
                    _id(value) in forbidden_ids
                    or function_has_forbidden_binding(value)
                    or container_binding
                ):
                    replace(
                        candidate, name,
                        module_name + "." + class_name + "." + name,
                        _type(value) if container_binding else None,
                    )
    for candidate in _tuple(reachable_classes()):
        for name, value in _tuple(_vars(candidate).items()):
            container_binding = container_has_forbidden_binding(value)
            if (
                _id(value) in forbidden_ids
                or function_has_forbidden_binding(value)
                or container_binding
            ):
                replace(
                    candidate, name,
                    _getattr(candidate, "__module__", "class") + "."
                    + _getattr(candidate, "__name__", "anonymous") + "."
                    + name,
                    _type(value) if container_binding else None,
                )

    for module in _tuple(_modules.values()):
        if not _isinstance(module, _module_type):
            continue
        if any(
            _id(value) in forbidden_ids
            or function_has_forbidden_binding(value)
            or container_has_forbidden_binding(value)
            for value in _vars(module).values()
        ):
            raise _value_error("native alias remained")
        for candidate in _tuple(_vars(module).values()):
            if _isinstance(candidate, _type) and any(
                _id(value) in forbidden_ids
                or function_has_forbidden_binding(value)
                or container_has_forbidden_binding(value)
                for value in _vars(candidate).values()
            ):
                raise _value_error("native class alias remained")
    for candidate in _tuple(reachable_classes()):
        if any(
            _id(value) in forbidden_ids
            or function_has_forbidden_binding(value)
            or container_has_forbidden_binding(value)
            for value in _vars(candidate).values()
        ):
            raise _value_error("orphaned native class alias remained")

_seal_native_aliases(_native_forbidden_ids)
del _seal_native_aliases

for name in (
    "create_builtin", "exec_builtin", "create_dynamic", "exec_dynamic",
    "load_dynamic", "find_frozen", "init_frozen", "get_frozen_object",
):
    if hasattr(_imp, name):
        setattr(_imp, name, _deny_operation("import." + name))

_loader_classes = {
    importlib.machinery.BuiltinImporter,
    importlib.machinery.FrozenImporter,
    importlib.machinery.PathFinder,
    importlib.machinery.FileFinder,
    importlib.machinery.SourceFileLoader,
    importlib.machinery.SourcelessFileLoader,
    importlib.machinery.ExtensionFileLoader,
    zipimport.zipimporter,
}
for loader in tuple(_loader_classes):
    for base in getattr(loader, "__mro__", ()):
        if getattr(base, "__module__", "").startswith((
            "_frozen_importlib", "zipimport"
        )):
            _loader_classes.add(base)
for loader in _loader_classes:
    for name in (
        "find_spec", "find_module", "create_module", "exec_module",
        "load_module", "get_code", "get_data", "get_filename",
        "path_hook",
    ):
        if hasattr(loader, name):
            try:
                setattr(loader, name, _deny_operation(
                    "import." + getattr(loader, "__name__", "loader") + "." + name
                ))
            except (AttributeError, TypeError) as exc:
                raise ValueError("import loader unsealable") from exc

_sources = {}
for module_name, relative in _MODULE_MAP.items():
    _sources[module_name] = (relative, _files[relative][1], module_name == "tools")
if not _test_mode:
    for relative, raw in _yaml_files.items():
        if relative == "yaml/cyaml.py":
            continue
        module_name = (
            "yaml" if relative == "yaml/__init__.py"
            else "yaml." + relative.rsplit("/", 1)[1][:-3]
        )
        _sources[module_name] = (relative, raw, module_name == "yaml")
_sources = types.MappingProxyType(dict(_sources))

class _ExactLiveModuleLoader:
    __slots__ = ()
    @property
    def sources(self, _authority=_sources):
        return _authority
    def create_module(self, spec):
        return None
    def exec_module(
        self, module, _deny=_violate, _authority=_sources,
        _normalize=_normpath, _join_path=_join, _root=_ROOT,
        _separator=_sep, _compile=compile, _exec=exec, _dirname_path=_dirname,
        _key_error=KeyError,
    ):
        name = module.__spec__.name
        try:
            relative, raw, package = _authority[name]
        except _key_error as exc:
            _deny("import." + name, "module outside exact map")
        origin = _normalize(_join_path(_root, relative.replace("/", _separator)))
        module.__file__ = origin
        module.__cached__ = None
        module.__loader__ = self
        if package:
            module.__path__ = [
                _dirname_path(origin) if name == "yaml" else _root
            ]
        _exec(
            _compile(raw, origin, "exec", dont_inherit=True), module.__dict__
        )

_exact_loader = _ExactLiveModuleLoader()
class _ExactLiveModuleFinder:
    __slots__ = ()
    @property
    def loader(self, _loader=_exact_loader):
        return _loader
    def find_spec(
        self, fullname, path=None, target=None, _deny=_violate,
        _authority=_sources, _loader=_exact_loader,
        _spec_from_loader=importlib.util.spec_from_loader,
        _normalize=_normpath, _join_path=_join, _root=_ROOT,
        _separator=_sep, _modules=sys.modules,
    ):
        if fullname in _authority:
            relative, _raw, package = _authority[fullname]
            spec = _spec_from_loader(
                fullname, _loader, is_package=package
            )
            if spec is None:
                _deny("import." + fullname, "memory spec unavailable")
            spec.origin = _normalize(
                _join_path(_root, relative.replace("/", _separator))
            )
            spec.has_location = False
            return spec
        if fullname in {"yaml.cyaml", "yaml._yaml"}:
            raise ModuleNotFoundError(fullname)
        if fullname not in _modules:
            _deny("import." + fullname, "unknown import")
        return None

if any(
    name == "yaml" or name.startswith("yaml.")
    or name == "tools" or name.startswith("tools.")
    for name in sys.modules
):
    raise ValueError("repository module preloaded")
_finder = _ExactLiveModuleFinder()
sys.meta_path[:] = [_finder]
sys.path[:] = [_ROOT]
sys.path_hooks[:] = []
sys.path_importer_cache.clear()

_transport = sys.stdout
_captured_out, _captured_err = io.StringIO(), io.StringIO()
sys.stdin = io.StringIO("")
sys.stdout, sys.stderr = _captured_out, _captured_err
sys.__stdin__ = sys.__stdout__ = sys.__stderr__ = None

def _docker_git(
    root, *args, text=True, _deny=_violate, _key_for=_vkey,
    _root_key=_ROOT_KEY, _take=_take_git, _list=list, _bool=bool,
):
    if _key_for(root, "docker.git.root")[1] != _root_key:
        _deny("docker.git.root", "unexpected Docker Git root")
    completed = _take(_list(args), _bool(text), False)
    if completed.returncode:
        return None
    return completed.stdout.strip() if text else completed.stdout

if _test_mode:
    if len(sys.argv) != 3 or re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
        sys.argv[2],
    ) is None:
        raise ValueError("test argv")
    _checked_at = sys.argv[2]
    sys.argv = [
        _normpath(_join(_ROOT, "tools", "run_release_gate_readonly_recheck.py"))
    ]
else:
    if len(sys.argv) != 8 or sys.argv[1] != "--release-readiness" \
            or sys.argv[3] != "--soak-evidence" \
            or sys.argv[5] != "--checked-at-utc" or sys.argv[7] != "--strict":
        raise ValueError("production argv")
    if _vkey(sys.argv[2], "argv.release_readiness")[1] != _vkey(
        "docs/release/RELEASE_READINESS.md", "argv.release_readiness"
    )[1] or _vkey(sys.argv[4], "argv.soak_evidence")[1] != _vkey(
        "docs/runs/release_soak_evidence/v3.12.0.json", "argv.soak_evidence"
    )[1]:
        raise ValueError("production argv path")
    _checked_at = sys.argv[6]
    if re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
        _checked_at,
    ) is None:
        raise ValueError("production argv time")
    sys.argv[0] = _normpath(_join(
        _ROOT, "tools", "run_release_gate_readonly_recheck.py"
    ))

def _docker_trusted_git_executable(
    _path=_GIT_PATH, _path_type=pathlib.Path,
):
    return _path_type(_path)

def _docker_git_runtime_provenance(
    _path=_GIT_PATH, _digest=_selected_git["sha256"],
):
    return {
        "policy": "platform_absolute_allowlist_v1",
        "executable": _path,
        "executable_sha256": _digest,
    }

def _collect_current_commit(_head=_bundle["head"]):
    return _head

def _execute_exact_gate(
    _is_test=_test_mode, _import_module=importlib.import_module,
    _source_map=_sources, _modules=sys.modules, _yaml_version=_PYYAML_VERSION,
    _getattr=getattr, _value_error=ValueError, _docker_git_fn=_docker_git,
    _docker_git_executable_fn=_docker_trusted_git_executable,
    _docker_provenance_fn=_docker_git_runtime_provenance,
    _current_commit_fn=_collect_current_commit, _normalize=_normpath,
    _join_path=_join, _root=_ROOT, _separator=_sep,
    _module_type=types.ModuleType, _loader=_exact_loader,
    _compile=compile, _exec=exec, _capture_out=_captured_out,
    _capture_err=_captured_err, _transport_out=_transport, _length=len,
    _system_exit=SystemExit, _base_exception=BaseException, _type=type,
    _int=int,
):
    status = 71
    try:
        if not _is_test:
            yaml = _import_module("yaml")
            loaded_yaml = {
                name for name in _modules
                if name == "yaml" or name.startswith("yaml.")
            }
            expected_yaml = {
                name for name in _source_map
                if name == "yaml" or name.startswith("yaml.")
            }
            if (
                loaded_yaml != expected_yaml
                or _getattr(yaml, "__version__", None) != _yaml_version
                or _getattr(yaml, "__with_libyaml__", None) is not False
            ):
                raise _value_error("pyyaml runtime")
            docker = _import_module("tools.run_release_docker_policy_evidence")
            collect = _import_module("tools.collect_soak_evidence")
            docker._git = _docker_git_fn
            docker._trusted_git_executable = _docker_git_executable_fn
            docker.inspect_git_runtime_provenance = _docker_provenance_fn
            collect._current_commit = _current_commit_fn
        main_relative, main_raw, _ = _source_map[
            "tools.run_release_gate_readonly_recheck"
        ]
        main_origin = _normalize(
            _join_path(_root, main_relative.replace("/", _separator))
        )
        main_module = _module_type("__main__")
        main_module.__file__ = main_origin
        main_module.__package__ = None
        main_module.__spec__ = None
        main_module.__loader__ = _loader
        _modules["__main__"] = main_module
        _exec(
            _compile(main_raw, main_origin, "exec", dont_inherit=True),
            main_module.__dict__,
        )
        status = 0
    except _system_exit as exc:
        status = (
            exc.code
            if _type(exc.code) is _int and exc.code in {0, 1, 2}
            else 71
        )
    except _base_exception:
        status = 71

    if _capture_err.getvalue():
        status, payload = 72, ""
    else:
        payload = _capture_out.getvalue()
    if _length(payload.encode("utf-8")) > 4 * 1024 * 1024:
        status, payload = 73, ""
    if payload:
        _transport_out.write(payload)
        _transport_out.flush()
    raise _system_exit(status)

_runtime_globals = globals()
_protected_functions = set()
_function_candidates = list(_runtime_globals.values())
_reachable_runtime_classes = []
_pending_runtime_classes = [object]
_seen_runtime_class_ids = set()
while _pending_runtime_classes:
    _pending_runtime_class = _pending_runtime_classes.pop()
    _pending_runtime_class_id = id(_pending_runtime_class)
    if _pending_runtime_class_id in _seen_runtime_class_ids:
        continue
    _seen_runtime_class_ids.add(_pending_runtime_class_id)
    _reachable_runtime_classes.append(_pending_runtime_class)
    try:
        _pending_runtime_classes.extend(
            type.__subclasses__(_pending_runtime_class)
        )
    except TypeError:
        continue
for _namespace in (
    builtins, io, _io, os, ntpath, posixpath, os.path, subprocess,
    *_native_backends.values(),
):
    if _namespace is not None:
        _namespace_values = tuple(vars(_namespace).values())
        _function_candidates.extend(_namespace_values)
        for _namespace_value in _namespace_values:
            if isinstance(_namespace_value, type):
                _function_candidates.extend(vars(_namespace_value).values())
for _runtime_class in (
    *_reachable_runtime_classes, *_path_classes,
    _ExactLiveModuleLoader, _ExactLiveModuleFinder,
    argparse.HelpFormatter,
):
    _function_candidates.extend(vars(_runtime_class).values())
for _candidate in _function_candidates:
    if isinstance(_candidate, property):
        _candidate = _candidate.fget
    if (
        isinstance(_candidate, types.FunctionType)
        and _candidate.__globals__ is _runtime_globals
    ):
        _protected_functions.add(id(_candidate))
_protected_function_ids = frozenset(_protected_functions)
_install_audit_hook(_protected_function_ids)
del _install_audit_hook
for _sys_name in (
    "_getframe", "_current_frames", "settrace", "setprofile",
    "setdlopenflags", "addaudithook",
):
    if hasattr(sys, _sys_name):
        setattr(sys, _sys_name, _deny_operation("sys." + _sys_name))

# Remove mutable authority handles from the globals dictionary exposed by
# Python functions.  Every runtime consumer above holds its exact immutable
# authority through guarded defaults before any exact-head module executes.
for _authority_name in (
    "_bundle", "_files", "_objects", "_subjects", "_yaml_files",
    "_yaml_authority", "_git_records", "_sources", "_VFILES", "_VDIRS",
    "_VABSENT", "_external_nodes", "_selected_git", "_captured_out",
    "_captured_err", "_transport", "_native_alias_allow",
    "_native_backends", "_native_forbidden_ids", "_native_forbidden_objects",
    "_native_origin_modules", "_socket", "_winapi", "_module_name",
    "_attribute_name", "_module", "_value", "backend_name", "backend",
    "low_name", "low",
    "_namespace_value", "_namespace_values",
):
    _runtime_globals.pop(_authority_name, None)
del (
    _authority_name, _candidate, _function_candidates, _namespace,
    _pending_runtime_class, _pending_runtime_class_id,
    _pending_runtime_classes, _reachable_runtime_classes,
    _protected_function_ids, _protected_functions, _runtime_class,
    _runtime_globals, _seen_runtime_class_ids, _sys_name,
)
_execute_exact_gate()
'''

_LIVE_CHILD_RUNTIME_SOURCE = (
    _LIVE_CHILD_RUNTIME_SOURCE
    .replace("__PROD_SCHEMA__", _LIVE_CHILD_BUNDLE_SCHEMA)
    .replace("__TEST_SCHEMA__", _LIVE_CHILD_TEST_BUNDLE_SCHEMA)
    .replace("__BUNDLE_DOMAIN_HEX__", _LIVE_CHILD_BUNDLE_DIGEST_DOMAIN.hex())
    .replace("__EXPECTED_FILES__", repr(_live_child_required_paths()))
    .replace("__MODULE_MAP__", repr(_LIVE_CHILD_MODULE_MAP))
    .replace("__OPTIONAL_ABSENT__", repr(_LIVE_CHILD_OPTIONAL_ABSENT_PATHS))
    .replace("__DOCKER_PATHS__", repr(_LIVE_CHILD_DOCKER_SOURCE_PATHS))
    .replace("__PYYAML_SCHEMA__", _PYYAML_CHILD_BUNDLE_SCHEMA)
    .replace("__PYYAML_VERSION__", PYYAML_VERSION)
    .replace("__PYYAML_MANIFEST__", repr(PYYAML_SOURCE_MANIFEST))
)

def _compressed_live_child_bootstrap() -> str:
    encoded = base64.b85encode(
        zlib.compress(_LIVE_CHILD_RUNTIME_SOURCE.encode("utf-8"), 9)
    ).decode("ascii")
    bootstrap = "\n".join((
        "# _live_child_decode_bundle _ExactLiveModuleFinder",
        "import base64,sys,zlib",
        "if not sys.flags.isolated or not sys.flags.no_site: raise SystemExit(74)",
        f"_raw=zlib.decompress(base64.b85decode({encoded!r})).decode('utf-8')",
        "exec(compile(_raw,'<waggledance-live-child-runtime>','exec',"
        "dont_inherit=True),{'__name__':'__wd_child_runtime__'})",
    ))
    if len(bootstrap) > 30000:
        raise RuntimeError("live child bootstrap exceeds Windows command limit")
    return bootstrap

_LIVE_CHILD_BOOTSTRAP = _compressed_live_child_bootstrap()

def _sandbox_hold_report(checked_at: dt.datetime | str) -> dict[str, Any]:
    timestamp = checked_at if isinstance(checked_at, str) else _format_utc(checked_at)
    blockers = ["live_release_gate_sandbox_violation"]
    return {
        "schema_version": LIVE_GATE_SCHEMA_VERSION,
        "ok": True,
        "checked_at_utc": timestamp,
        "release_gate_decision": "hold",
        "blockers": blockers,
        "read_only": True,
        "release_gate_effect": "none",
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
        "read_only_invariants": dict(READ_ONLY_INVARIANTS),
        "gate": {"decision": "hold", "blockers": blockers},
    }

class _LiveChildExecutionResult:
    def __init__(self, returncode: int, stdout: bytes, stderr: bytes,
                 operation: str | None) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.operation = operation

def _live_child_execute_source(bundle: bytes, source: str) -> _LiveChildExecutionResult:
    payload = _live_child_decode_bundle(bundle)
    if payload["schema_version"] != _LIVE_CHILD_TEST_BUNDLE_SCHEMA:
        raise ValueError("live child bundle: test source requires test schema")
    if type(source) is not str:
        raise TypeError("source must be text")
    encoded_source = source.encode("utf-8")
    for item in payload["files"]:
        if item["path"] == "tools/run_release_gate_readonly_recheck.py":
            item.update({
                "oid": _git_blob_oid(encoded_source),
                "size": len(encoded_source),
                "sha256": hashlib.sha256(encoded_source).hexdigest(),
                "content_b64": base64.b64encode(encoded_source).decode("ascii"),
            })
            break
    payload["root_digest"] = _live_child_root_digest(payload)
    encoded_bundle = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    timestamp = "2026-06-01T03:00:00Z"
    completed = subprocess.run(
        [sys.executable, "-B", "-I", "-S", "-c", _LIVE_CHILD_BOOTSTRAP,
         "--wd-test-source", timestamp],
        cwd=ROOT, env=_sanitized_environment(), input=encoded_bundle,
        capture_output=True, timeout=60, check=False,
    )
    stdout = completed.stdout
    stderr = completed.stderr
    if (
        completed.returncode == _LIVE_CHILD_VIOLATION_EXIT_CODE
        and stdout == b"" and stderr == b""
    ):
        stdout = json.dumps(
            _sandbox_hold_report(timestamp), sort_keys=True,
            separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        ).encode("ascii")
    return _LiveChildExecutionResult(
        completed.returncode, stdout, stderr, None
    )


class _LiveGateProtocolError(RuntimeError):
    def __init__(self, blocker: str) -> None:
        super().__init__(blocker)
        self.blocker = blocker


def _pycache_prefix_blocker(*, before_spawn: bool) -> str | None:
    if not _PYCACHE_ROOT_SAFE:
        return "live_release_gate_pycache_root_unverifiable"
    try:
        root_info = os.lstat(_PYCACHE_ROOT)
    except FileNotFoundError:
        if _PYCACHE_ROOT_IDENTITY is not None:
            return "live_release_gate_pycache_root_changed"
    except OSError:
        return "live_release_gate_pycache_root_unverifiable"
    else:
        root_identity = (root_info.st_dev, root_info.st_ino, root_info.st_mode)
        if (
            _PYCACHE_ROOT_IDENTITY is None
            or not stat.S_ISDIR(root_info.st_mode)
            or _is_reparse(root_info)
            or root_identity != _PYCACHE_ROOT_IDENTITY
        ):
            return "live_release_gate_pycache_root_changed"
    if not (_PYCACHE_PREFIX_PREEXISTED or os.path.lexists(_PYCACHE_PREFIX)):
        return None
    try:
        info = os.lstat(_PYCACHE_PREFIX)
        if stat.S_ISDIR(info.st_mode) and not _is_reparse(info):
            with os.scandir(_PYCACHE_PREFIX) as entries:
                if next(entries, None) is not None:
                    return "live_release_gate_pycache_prefix_not_empty"
    except OSError:
        return "live_release_gate_pycache_prefix_unverifiable"
    return (
        "live_release_gate_pycache_prefix_preexists"
        if before_spawn
        else "live_release_gate_pycache_prefix_created"
    )


def _run_live_release_gate(checked_at_utc: dt.datetime) -> dict[str, Any]:
    shadow_blocker = _yaml_shadow_blocker()
    if shadow_blocker:
        raise _LiveGateProtocolError(shadow_blocker)
    dependency_blocker = _trusted_pyyaml_current_blocker()
    if dependency_blocker:
        raise _LiveGateProtocolError(dependency_blocker)
    prefix_blocker = _pycache_prefix_blocker(before_spawn=True)
    if prefix_blocker:
        raise _LiveGateProtocolError(prefix_blocker)
    try:
        child_bundle = _build_live_child_bundle()
    except _LiveChildManifestError as exc:
        raise _LiveGateProtocolError(exc.blocker) from exc
    except (OSError, RuntimeError, UnicodeError, ValueError) as exc:
        raise _LiveGateProtocolError(
            "live_release_gate_manifest_unavailable"
        ) from exc
    command = [sys.executable, "-B", "-X", f"pycache_prefix={_PYCACHE_PREFIX}",
               "-I", "-S", "-c", _LIVE_CHILD_BOOTSTRAP, "--release-readiness",
               str(CANONICAL_RELEASE_READINESS), "--soak-evidence",
               str(CANONICAL_SOAK_EVIDENCE), "--checked-at-utc",
               _format_utc(checked_at_utc), "--strict"]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=_child_environment(),
            input=child_bundle,
            capture_output=True,
            timeout=300,
            check=False,
        )
    except _LiveGateProtocolError:
        raise
    except Exception as exc:  # noqa: BLE001 - ambiguous spawn outcome must HOLD
        prefix_blocker = _pycache_prefix_blocker(before_spawn=False)
        if prefix_blocker:
            raise _LiveGateProtocolError(prefix_blocker) from exc
        blocker = (
            "live_release_gate_child_timeout"
            if isinstance(exc, subprocess.TimeoutExpired)
            else "live_release_gate_child_spawn_failed"
        )
        raise _LiveGateProtocolError(blocker) from exc
    prefix_blocker = _pycache_prefix_blocker(before_spawn=False)
    if prefix_blocker:
        raise _LiveGateProtocolError(prefix_blocker)
    shadow_blocker = _yaml_shadow_blocker()
    if shadow_blocker:
        raise _LiveGateProtocolError(shadow_blocker)
    if completed.returncode == _LIVE_CHILD_VIOLATION_EXIT_CODE:
        if completed.stdout != b"" or completed.stderr != b"":
            raise _LiveGateProtocolError(
                "live_release_gate_sandbox_report_malformed"
            )
        return _sandbox_hold_report(checked_at_utc)
    if completed.stderr != b"":
        raise _LiveGateProtocolError("live_release_gate_stderr_not_empty")
    reserved = {
        _LIVE_CHILD_EXCEPTION_EXIT_CODE: "live_release_gate_child_exception",
        _LIVE_CHILD_STDERR_EXIT_CODE: "live_release_gate_child_stderr_captured",
        _LIVE_CHILD_OVERSIZE_EXIT_CODE: "live_release_gate_child_output_too_large",
    }
    if completed.returncode in reserved:
        if completed.stdout != b"":
            raise _LiveGateProtocolError("live_release_gate_reserved_exit_output")
        raise _LiveGateProtocolError(reserved[completed.returncode])
    try:
        report = _json_object(completed.stdout)
    except (RecursionError, UnicodeError, ValueError) as exc:
        raise _LiveGateProtocolError("live_release_gate_stdout_malformed") from exc
    decision = report.get("release_gate_decision")
    report_blockers = report.get("blockers")
    gate = report.get("gate")
    shape_ok = (
        report.get("schema_version") == LIVE_GATE_SCHEMA_VERSION
        and isinstance(report.get("ok"), bool)
        and isinstance(report.get("checked_at_utc"), str)
        and isinstance(report.get("read_only"), bool)
        and isinstance(decision, str) and decision in ("hold", "pass")
        and isinstance(report_blockers, list)
        and all(isinstance(item, str) for item in report_blockers)
        and isinstance(report.get("release_gate_effect"), str)
        and report.get("release_boundary") == FALSE_RELEASE_BOUNDARY
        and report.get("read_only_invariants") == READ_ONLY_INVARIANTS
        and isinstance(gate, Mapping)
        and gate.get("decision") == decision
        and gate.get("blockers") == report_blockers)
    if not shape_ok:
        raise _LiveGateProtocolError("live_release_gate_report_malformed")
    expected_exit = (1 if report["ok"] is not True else 0
                     if decision == "pass" and not report_blockers
                     else STRICT_BLOCKED_EXIT_CODE)
    if completed.returncode != expected_exit:
        raise _LiveGateProtocolError("live_release_gate_exit_code_mismatch")
    return report


def _blocked_live_summary(blocker: str) -> tuple[dict[str, Any], list[str]]:
    return {
        "schema_version": None,
        "ok": False,
        "release_gate_decision": None,
        "blockers": [blocker],
    }, [blocker]


def _live_gate_evaluation(
    checked_at_utc: dt.datetime,
    *,
    wall_now: dt.datetime,
) -> tuple[dict[str, Any], list[str]]:
    try:
        report = _run_live_release_gate(checked_at_utc)
    except _LiveGateProtocolError as exc:
        return _blocked_live_summary(exc.blocker)
    except Exception:  # noqa: BLE001 - ambiguity must HOLD, never crash
        return _blocked_live_summary("live_release_gate_evaluator_error")
    summary = _release_gate_summary(report)
    blockers = _source_timestamp_blockers(
        report.get("checked_at_utc"),
        prefix="live_release_gate_checked_at_utc",
        wall_now=wall_now,
    )
    if report.get("checked_at_utc") != _format_utc(checked_at_utc):
        blockers.append("live_release_gate_checked_at_utc_mismatch")
    if summary["ok"] is not True:
        blockers.append("live_release_gate_report_not_ok")
    if summary["read_only"] is not True:
        blockers.append("live_release_gate_not_read_only")
    if summary["release_gate_effect"] != "none":
        blockers.append("live_release_gate_effect_not_none")
    if summary["release_boundary_all_false"] is not True:
        blockers.append("live_release_gate_boundary_mutated")
    if summary["release_gate_decision"] != "pass" or summary["blockers"]:
        blockers.append("live_release_gate_not_passed")
    return summary, blockers


def _head_soak_binding(
    git_state: Mapping[str, Any], soak_entry: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    head = git_state.get("head")
    index_entry = (git_state.get("index") or {}).get(SOAK_EVIDENCE_CARRIER_PATH)
    binding = {
        "schema_version": HEAD_SOAK_BINDING_SCHEMA_VERSION,
        "git_head": head,
        "soak_commit": None,
        "soak_subject_tree": None,
        "head_tree": None,
        "subject_is_ancestor": False,
        "carrier_only_delta": False,
        "carrier_delta_paths": [],
        "soak_evidence_path": SOAK_EVIDENCE_CARRIER_PATH,
        "carrier_blob": (
            index_entry[1]
            if isinstance(index_entry, tuple) and len(index_entry) >= 2
            else None
        ),
    }
    blockers: list[str] = []
    if not isinstance(head, str):
        return binding, ["git_head_unavailable"]
    if not _FULL_HEX_SHA.fullmatch(head):
        blockers.append("git_head_not_full_hex")
    try:
        raw = soak_entry["raw"]
        if not isinstance(raw, bytes):
            raise ValueError("soak evidence bytes unavailable")
        soak = _json_object(raw)
    except (KeyError, RecursionError, TypeError, UnicodeError, ValueError):
        return binding, [*blockers, "soak_evidence_unreadable"]
    commit = soak.get("commit")
    if not isinstance(commit, str) or not _FULL_HEX_SHA.fullmatch(commit):
        return binding, [*blockers, "soak_commit_missing_or_malformed"]
    binding["soak_commit"] = commit
    if blockers:
        return binding, blockers
    try:
        subject_tree = _git(
            "rev-parse", "--verify", f"{commit}^{{tree}}"
        ).decode("ascii").strip()
        head_tree = _git(
            "rev-parse", "--verify", f"{head}^{{tree}}"
        ).decode("ascii").strip()
        if (
            not _FULL_HEX_SHA.fullmatch(subject_tree)
            or not _FULL_HEX_SHA.fullmatch(head_tree)
        ):
            raise ValueError("tree oid malformed")
        binding["soak_subject_tree"] = subject_tree
        binding["head_tree"] = head_tree
        ancestry = _git_result("merge-base", "--is-ancestor", commit, head)
        if ancestry.stdout != b"":
            raise ValueError("ancestry output malformed")
        if ancestry.returncode == 1:
            blockers.append("soak_subject_commit_not_ancestor_of_head")
            return binding, blockers
        if ancestry.returncode != 0:
            raise RuntimeError("ancestry unavailable")
        binding["subject_is_ancestor"] = True
        raw_delta = _git(
            "diff-tree", "--no-commit-id", "--name-only", "-r", "-z",
            "--no-renames", "--no-ext-diff", f"{commit}^{{tree}}",
            f"{head}^{{tree}}", "--",
        )
        if not raw_delta or not raw_delta.endswith(b"\0"):
            blockers.append("soak_subject_tree_delta_malformed")
            return binding, blockers
        decoded = raw_delta.decode("utf-8")
        paths = decoded[:-1].split("\0")
        if not paths or any(not path for path in paths) or len(set(paths)) != len(paths):
            blockers.append("soak_subject_tree_delta_malformed")
            return binding, blockers
        binding["carrier_delta_paths"] = paths
        if paths != [SOAK_EVIDENCE_CARRIER_PATH]:
            blockers.append("soak_subject_noncarrier_tree_delta")
            return binding, blockers
        binding["carrier_only_delta"] = True
    except (OSError, RuntimeError, subprocess.SubprocessError, UnicodeError, ValueError):
        blockers.append("soak_subject_git_binding_unavailable")
    return binding, blockers


def _identity_tuple(signed_by: Any) -> tuple[str, str] | None:
    if not isinstance(signed_by, str):
        return None
    parts = signed_by.split(":", 2)
    if len(parts) != 3 or parts[0] != "operator" or not parts[1].strip():
        return None
    try:
        provenance = dt.datetime.fromisoformat(parts[2].replace("Z", "+00:00"))
        offset = provenance.utcoffset()
        provenance.astimezone(dt.UTC)
    except (OverflowError, ValueError):
        return None
    if provenance.tzinfo is None or offset != dt.timedelta(0):
        return None
    return (parts[0], parts[1].strip())


def _torch_scope_update_blockers(pack: Mapping[str, Any]) -> list[str]:
    """Fail-closed inspection of operator_signoff.scope_updates.

    Missing scope_updates means an empty set. An update carrying a
    lock_evidence_contract whose operator_signature_required is the literal
    bool True must have a non-empty direct signed_by whose operator role+id
    tuple exactly matches the top-level signoff identity. Malformed or
    ambiguous structures block; historical updates without a contract stay
    lineage-only and never block.
    """
    blockers: list[str] = []
    signoff = pack.get("operator_signoff")
    if not isinstance(signoff, Mapping):
        return blockers
    top_tuple = _identity_tuple(signoff.get("signed_by"))
    if "scope_updates" not in signoff:
        return blockers
    updates = signoff.get("scope_updates")
    if updates is None:
        return ["scope_updates_null"]
    if not isinstance(updates, list):
        blockers.append("scope_updates_malformed")
        return blockers
    for index, update in enumerate(updates):
        if not isinstance(update, Mapping):
            blockers.append(f"scope_update_{index}_malformed")
            continue
        if "lock_evidence_contract" not in update:
            continue
        contract = update.get("lock_evidence_contract")
        if not isinstance(contract, Mapping):
            blockers.append(f"scope_update_{index}_contract_malformed")
            continue
        required = contract.get("operator_signature_required")
        if required is False:
            continue
        if required is None:
            blockers.append(f"scope_update_{index}_required_flag_missing")
            continue
        if required is not True:
            blockers.append(f"scope_update_{index}_required_flag_malformed")
            continue
        direct = update.get("signed_by")
        if not isinstance(direct, str) or not direct.strip():
            blockers.append(f"scope_update_{index}_missing_direct_signed_by")
            continue
        update_tuple = _identity_tuple(direct)
        if (
            top_tuple is None
            or update_tuple is None
            or update_tuple != top_tuple
        ):
            blockers.append(f"scope_update_{index}_signer_identity_mismatch")
    return blockers


def _package(
    phase_synthesis_refresh: Mapping[str, Any], field: str, package_id: str,
) -> dict[str, Any]:
    packages = phase_synthesis_refresh.get(field)
    if not isinstance(packages, list):
        return {}
    for package in packages:
        if isinstance(package, dict) and package.get("id") == package_id:
            return dict(package)
    return {}


def _source_release_soak_package(
    phase_synthesis_refresh: dict[str, Any],
) -> dict[str, Any]:
    return _package(
        phase_synthesis_refresh, "remaining_work_packages", RELEASE_SOAK_TASK_ID
    ) or _package(
        phase_synthesis_refresh, "landed_work_packages", RELEASE_SOAK_TASK_ID
    )


def _chosen_option(pack: Mapping[str, Any]) -> dict[str, Any]:
    try:
        option = _exact_option_selection(pack)
    except ValueError:
        return {}
    return dict(option) if option is not None else {}


def _exact_option_selection(
    pack: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Validate exact option identity without coercing YAML scalar types."""
    options = pack.get("options")
    if not isinstance(options, list) or not options:
        raise ValueError("decision-pack options malformed")
    by_id: dict[str, Mapping[str, Any]] = {}
    for option in options:
        if not isinstance(option, Mapping):
            raise ValueError("decision-pack option malformed")
        option_id = option.get("id")
        if (
            not isinstance(option_id, str)
            or not option_id.strip()
            or option_id != option_id.strip()
            or option_id in by_id
        ):
            raise ValueError("decision-pack option id malformed")
        by_id[option_id] = option
    signoff = pack.get("operator_signoff")
    if not isinstance(signoff, Mapping):
        raise ValueError("decision-pack signoff malformed")
    chosen = signoff.get("chosen_option")
    if not isinstance(chosen, str):
        raise ValueError("decision-pack chosen option malformed")
    if chosen == "":
        return None
    if not chosen.strip() or chosen != chosen.strip():
        raise ValueError("decision-pack chosen option malformed")
    matches = [option for option_id, option in by_id.items() if option_id == chosen]
    if len(matches) != 1:
        raise ValueError("decision-pack chosen option mismatch")
    return matches[0]


def _trusted_pyyaml_current_blocker() -> str | None:
    if _TRUSTED_PYYAML_BLOCKER:
        return _TRUSTED_PYYAML_BLOCKER
    if not isinstance(_TRUSTED_PYYAML, Mapping):
        return "trusted_pyyaml_unavailable"
    if _TRUSTED_PYYAML.get("version") != PYYAML_VERSION:
        return "trusted_pyyaml_version_unpinned"
    if _TRUSTED_PYYAML.get("sdist_sha256") != PYYAML_SDIST_SHA256:
        return "trusted_pyyaml_authority_mismatch"
    source_items = _TRUSTED_PYYAML.get("source_items")
    if not isinstance(source_items, tuple):
        return "trusted_pyyaml_source_manifest_malformed"
    sources: dict[str, bytes] = {}
    for item in source_items:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], bytes)
            or item[0] in sources
        ):
            return "trusted_pyyaml_source_manifest_malformed"
        sources[item[0]] = item[1]
    if set(sources) != set(_PYYAML_AUTHORITY):
        return "trusted_pyyaml_source_manifest_mismatch"
    for relative, expected_size, expected_digest in PYYAML_SOURCE_MANIFEST:
        raw = sources[relative]
        if (
            len(raw) != expected_size
            or hashlib.sha256(raw).hexdigest() != expected_digest
        ):
            return "trusted_pyyaml_source_changed"
    return None


def _pyyaml_module_sources() -> dict[str, tuple[str, bytes]]:
    assert isinstance(_TRUSTED_PYYAML, Mapping)
    sources = dict(_TRUSTED_PYYAML["source_items"])
    return {
        ("yaml" if relative == "yaml/__init__.py" else f"yaml.{Path(relative).stem}"):
        (relative, sources[relative])
        for relative, _, _ in PYYAML_SOURCE_MANIFEST
        if relative != "yaml/cyaml.py"
    }


class _ExactYamlLoader:
    def __init__(self, sources: Mapping[str, tuple[str, bytes]]) -> None:
        self.sources = dict(sources)

    @staticmethod
    def create_module(spec: Any) -> None:
        return None

    def exec_module(self, module: Any) -> None:
        name = module.__spec__.name
        try:
            relative, raw = self.sources[name]
        except KeyError as exc:
            raise ModuleNotFoundError(
                f"{name} not in authenticated source set"
            ) from exc
        origin = f"<trusted-pyyaml:{relative}>"
        module.__file__ = origin
        module.__cached__ = None
        if name == "yaml":
            module.__path__ = []
        code = compile(raw, origin, "exec", dont_inherit=True)
        exec(code, module.__dict__)


class _ExactYamlFinder:
    def __init__(self, sources: Mapping[str, tuple[str, bytes]]) -> None:
        self.loader = _ExactYamlLoader(sources)

    def find_spec(
        self,
        fullname: str,
        path: Any = None,
        target: Any = None,
    ) -> Any:
        del path, target
        if fullname != "yaml" and not fullname.startswith("yaml."):
            return None
        if fullname not in self.loader.sources:
            raise ModuleNotFoundError(
                f"{fullname} not in authenticated source set"
            )
        relative, _ = self.loader.sources[fullname]
        spec = importlib.util.spec_from_loader(
            fullname, self.loader, is_package=fullname == "yaml"
        )
        if spec is None:
            raise ImportError("trusted pyyaml memory spec unavailable")
        spec.origin = f"<trusted-pyyaml:{relative}>"
        spec.has_location = False
        return spec


_TRUSTED_PYYAML_FINDER: _ExactYamlFinder | None = None


def _load_trusted_pyyaml() -> Any:
    global _TRUSTED_PYYAML_FINDER
    blocker = _trusted_pyyaml_current_blocker()
    if blocker:
        raise ValueError(blocker)
    if any(name == "yaml" or name.startswith("yaml.") for name in sys.modules):
        raise ValueError("trusted pyyaml preloaded")
    sys.meta_path[:] = [
        finder for finder in sys.meta_path
        if not isinstance(finder, _ExactYamlFinder)
    ]
    _TRUSTED_PYYAML_FINDER = None
    finder = _ExactYamlFinder(_pyyaml_module_sources())
    sys.meta_path.insert(0, finder)
    try:
        module = importlib.import_module("yaml")
        loaded_names = {
            name for name in sys.modules
            if name == "yaml" or name.startswith("yaml.")
        }
        if loaded_names != set(PYYAML_EXECUTABLE_MODULES):
            raise ValueError("trusted pyyaml loaded module set mismatch")
        for name, loaded in tuple(sys.modules.items()):
            if name != "yaml" and not name.startswith("yaml."):
                continue
            relative, _ = finder.loader.sources[name]
            if (
                getattr(loaded, "__loader__", None) is not finder.loader
                or getattr(loaded, "__file__", None)
                != f"<trusted-pyyaml:{relative}>"
            ):
                raise ValueError("trusted pyyaml memory loader mismatch")
        if (
            getattr(module, "__version__", None) != PYYAML_VERSION
            or not hasattr(module, "SafeLoader")
            or not hasattr(module, "resolver")
            or getattr(module, "__with_libyaml__", None) is not False
        ):
            raise ValueError("trusted pyyaml module contract mismatch")
    except BaseException:
        for name in tuple(sys.modules):
            if name == "yaml" or name.startswith("yaml."):
                sys.modules.pop(name, None)
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)
        _TRUSTED_PYYAML_FINDER = None
        raise
    _TRUSTED_PYYAML_FINDER = finder
    return module


def _decision_pack_module() -> Any:
    global _DECISION_PACK_MODULE
    if _DECISION_PACK_MODULE is not None:
        return _DECISION_PACK_MODULE
    if not _TOOLS_PACKAGE_PRELOADED:
        _load_trusted_pyyaml()
    package_name = "tools"
    module_name = "tools.operator_decision_pack"
    package_path = CANONICAL_INPUTS["tools_package"]
    package = sys.modules.get(package_name)
    if package is not None:
        if not _TOOLS_PACKAGE_PRELOADED:
            raise ValueError("tools package loaded during evaluation")
        module = sys.modules.get(module_name)
        if module is None:
            module_path = CANONICAL_INPUTS["operator_decision_pack"]
            module_spec = importlib.util.spec_from_file_location(
                module_name, module_path
            )
            if module_spec is None or module_spec.loader is None:
                raise ValueError("operator decision-pack loader unavailable")
            module = importlib.util.module_from_spec(module_spec)
            sys.modules[module_name] = module
            try:
                module_spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(module_name, None)
                raise
    else:
        package_spec = importlib.util.spec_from_file_location(
            package_name,
            package_path,
            submodule_search_locations=[str(package_path.parent)],
        )
        if package_spec is None or package_spec.loader is None:
            raise ValueError("tools package loader unavailable")
        package = importlib.util.module_from_spec(package_spec)
        sys.modules[package_name] = package
        try:
            package_spec.loader.exec_module(package)
            module_path = CANONICAL_INPUTS["operator_decision_pack"]
            module_spec = importlib.util.spec_from_file_location(
                module_name, module_path
            )
            if module_spec is None or module_spec.loader is None:
                raise ValueError("operator decision-pack loader unavailable")
            module = importlib.util.module_from_spec(module_spec)
            sys.modules[module_name] = module
            module_spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            sys.modules.pop(package_name, None)
            raise
    package_origin = Path(str(getattr(sys.modules.get("tools"), "__file__", ""))).resolve(
        strict=True
    )
    if package_origin != CANONICAL_INPUTS["tools_package"]:
        raise ValueError("tools package origin mismatch")
    origin = Path(str(getattr(module, "__file__", ""))).resolve(strict=True)
    if origin != CANONICAL_INPUTS["operator_decision_pack"]:
        raise ValueError("operator decision-pack module origin mismatch")
    _DECISION_PACK_MODULE = module
    return module


def _pack_from_bytes(raw: bytes, name: str) -> dict[str, Any]:
    validator = _decision_pack_module()
    if validator.yaml is None:  # pragma: no cover - project dependency
        raise ValueError("PyYAML is required")

    class UniqueKeyLoader(validator.yaml.SafeLoader):
        pass

    def construct_unique_mapping(loader: Any, node: Any, deep: bool = False) -> Any:
        loader.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ValueError("decision-pack mapping key is unhashable") from exc
            if duplicate:
                raise ValueError("duplicate decision-pack YAML key")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueKeyLoader.add_constructor(
        validator.yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_unique_mapping,
    )
    loaded = validator.yaml.load(raw.decode("utf-8"), Loader=UniqueKeyLoader)
    if not isinstance(loaded, Mapping):
        raise ValueError("decision pack must be a mapping")
    validator._validate_pack(loaded, source=name)
    _exact_option_selection(loaded)
    return dict(loaded)


def _json_safe_copy(value: Any) -> Any:
    encoded = json.dumps(value, allow_nan=False, sort_keys=True)
    return json.loads(encoded)


def _public_path(value: Any) -> str:
    path = Path(str(value))
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def _unevaluated_pack_summary(
    entry: Mapping[str, Any], decision_id: str, category: str
) -> dict[str, Any]:
    return {
        "path": _public_path(entry.get("path")),
        "expected_decision_id": decision_id,
        "expected_category": category,
        "signed": False,
        "blockers": ["operator_decision_pack_not_evaluated_input_integrity"],
    }


def _decision_pack_summary(
    entry: Mapping[str, Any],
    *,
    expected_decision_id: str,
    expected_category: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    blockers: list[str] = []
    summary: dict[str, Any] = {
        "path": _public_path(entry.get("path")),
        "expected_decision_id": expected_decision_id,
        "expected_category": expected_category,
        "signed": False,
        "blockers": blockers,
    }
    try:
        raw = entry.get("raw")
        if not isinstance(raw, bytes):
            raise ValueError("decision pack unreadable")
        pack = _pack_from_bytes(raw, Path(str(entry.get("path"))).name)
    except Exception:  # noqa: BLE001 - malformed canonical YAML must HOLD
        blockers.append("operator_decision_pack_missing_or_invalid")
        return summary, None

    signoff = pack.get("operator_signoff")
    chosen_option = ""
    signed_by: Any = None
    if isinstance(signoff, Mapping):
        if isinstance(signoff.get("chosen_option"), str):
            chosen_option = signoff["chosen_option"]
        if isinstance(signoff.get("signed_by"), str):
            signed_by = signoff["signed_by"]

    invariants = pack.get("structural_invariants")
    if not isinstance(invariants, Mapping):
        invariants = {}
        blockers.append("operator_decision_pack_structural_invariants_malformed")
    else:
        try:
            invariants = _json_safe_copy(invariants)
        except (RecursionError, TypeError, ValueError):
            invariants = {}
            blockers.append("operator_decision_pack_structural_invariants_malformed")

    try:
        signed = _decision_pack_module().is_signed(pack)
    except Exception:  # noqa: BLE001 - malformed signoff must HOLD
        signed = False
        blockers.append("operator_decision_pack_signature_malformed")

    summary.update({
        "decision_id": (
            pack.get("decision_id") if isinstance(pack.get("decision_id"), str) else None
        ),
        "category": pack.get("category") if isinstance(pack.get("category"), str) else None,
        "chosen_option": chosen_option,
        "signed_by": signed_by,
        "signed": signed,
        "structural_invariants": dict(invariants),
    })
    if pack.get("decision_id") != expected_decision_id:
        blockers.append("operator_decision_pack_id_mismatch")
    if pack.get("category") != expected_category:
        blockers.append("operator_decision_pack_category_mismatch")
    if not signed:
        blockers.append("operator_decision_pack_unsigned")
    elif _identity_tuple(signed_by) is None:
        blockers.append("operator_decision_pack_signer_malformed")

    option = _chosen_option(pack)
    data = option.get("data") if isinstance(option, Mapping) else None
    if isinstance(data, Mapping):
        try:
            summary["chosen_option_data"] = _json_safe_copy(data)
        except (RecursionError, TypeError, ValueError):
            blockers.append("operator_decision_pack_chosen_option_data_malformed")
    elif data is not None:
        blockers.append("operator_decision_pack_chosen_option_data_malformed")
    return summary, pack


def _docker_pack_summary(entry: Mapping[str, Any]) -> dict[str, Any]:
    summary, _ = _decision_pack_summary(
        entry,
        expected_decision_id="docker-latest-promotion",
        expected_category="docker_promotion",
    )
    blockers = summary["blockers"]
    data = summary.get("chosen_option_data") or {}
    invariants = summary.get("structural_invariants") or {}
    if summary.get("signed") is True:
        if summary.get("chosen_option") != "ghcr_stable_only":
            blockers.append("docker_promotion_choice_not_ghcr_stable_only")
        if data.get("moves_latest") is not False:
            blockers.append("docker_latest_move_not_forbidden_by_pack")
        if invariants.get("latest_move_is_operator_only") is not True:
            blockers.append("docker_latest_operator_only_invariant_missing")
        if invariants.get("agent_must_not_self_resolve") is not True:
            blockers.append("docker_agent_must_not_self_resolve_missing")
    return summary


def _torch_pack_summary(entry: Mapping[str, Any]) -> dict[str, Any]:
    summary, pack = _decision_pack_summary(
        entry,
        expected_decision_id="torch-cuda-vs-cpu",
        expected_category="dependency_security",
    )
    if pack is not None:
        summary["blockers"].extend(_torch_scope_update_blockers(pack))
    return summary


def _release_gate_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    report_blockers = report.get("blockers")
    return {
        "schema_version": report.get("schema_version"),
        "checked_at_utc": report.get("checked_at_utc"),
        "ok": report.get("ok") is True,
        "read_only": report.get("read_only") is True,
        "release_gate_decision": report.get("release_gate_decision"),
        "blockers": (
            list(report_blockers)
            if isinstance(report_blockers, list)
            else ["report_blockers_malformed"]
        ),
        "release_gate_effect": report.get("release_gate_effect"),
        "release_boundary_all_false": (
            report.get("release_boundary") == FALSE_RELEASE_BOUNDARY
        ),
        "docker_stable_move_scope": "parent_guardrail_only_not_live_v0",
    }


def _source_phase_synthesis_summary(
    phase_synthesis_refresh: dict[str, Any],
) -> dict[str, Any]:
    remaining_package = _package(
        phase_synthesis_refresh, "remaining_work_packages", RELEASE_SOAK_TASK_ID
    )
    landed_package = _package(
        phase_synthesis_refresh, "landed_work_packages", RELEASE_SOAK_TASK_ID
    )
    return {
        "schema_version": phase_synthesis_refresh.get("schema_version"),
        "sprint_id": phase_synthesis_refresh.get("sprint_id"),
        "generated_at_utc": phase_synthesis_refresh.get("generated_at_utc"),
        "ok": phase_synthesis_refresh.get("ok") is True,
        "release_boundary_all_false": (
            phase_synthesis_refresh.get("release_boundary")
            == FALSE_RELEASE_BOUNDARY
        ),
        "remaining_release_soak_package": {
            "id": RELEASE_SOAK_TASK_ID,
            "status": remaining_package.get("status"),
            "owner": remaining_package.get("owner"),
        },
        "landed_release_soak_package": {
            "id": RELEASE_SOAK_TASK_ID,
            "status": landed_package.get("status"),
            "owner": landed_package.get("owner"),
        },
    }


def _phase_shape_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if report.get("sprint_id") != PHASE_SYNTHESIS_SPRINT_ID:
        blockers.append("phase_synthesis_sprint_id_mismatch")
    if not isinstance(report.get("ok"), bool):
        blockers.append("phase_synthesis_ok_malformed")
    source_blockers = report.get("blockers")
    if not isinstance(source_blockers, list) or not all(
        isinstance(item, str) for item in source_blockers
    ):
        blockers.append("phase_synthesis_blockers_malformed")
    elif source_blockers:
        blockers.append("phase_synthesis_report_has_blockers")

    seen: set[str] = set()
    target_states: list[tuple[str, str]] = []
    for field in ("remaining_work_packages", "landed_work_packages"):
        packages = report.get(field)
        if packages is None and field == "landed_work_packages":
            packages = []
        if not isinstance(packages, list):
            blockers.append(f"{field}_malformed")
            continue
        for index, package in enumerate(packages):
            if not isinstance(package, Mapping):
                blockers.append(f"{field}_{index}_malformed")
                continue
            package_id = package.get("id")
            status_value = package.get("status")
            if not isinstance(package_id, str) or not package_id:
                blockers.append(f"{field}_{index}_id_malformed")
                continue
            if not isinstance(status_value, str) or not status_value:
                blockers.append(f"{field}_{index}_status_malformed")
                continue
            if package_id in seen:
                blockers.append(f"phase_synthesis_duplicate_package_{package_id}")
            seen.add(package_id)
            if package_id == RELEASE_SOAK_TASK_ID:
                target_states.append((field, status_value))
    allowed_target = {
        ("remaining_work_packages", "ready_for_release_boundary_review"),
        ("landed_work_packages", "complete_release_boundary_readiness_recorded"),
    }
    if len(target_states) != 1 or target_states[0] not in allowed_target:
        blockers.append("release_soak_package_state_ambiguous")
    return blockers


def _continuity_shape_blockers(report: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    decision = report.get("release_gate_decision")
    report_blockers = report.get("blockers")
    if not isinstance(report.get("ok"), bool):
        blockers.append("release_gate_recheck_ok_malformed")
    if not isinstance(report.get("read_only"), bool):
        blockers.append("release_gate_recheck_read_only_malformed")
    if not isinstance(decision, str) or decision not in ("hold", "pass"):
        blockers.append("release_gate_recheck_decision_malformed")
    if not isinstance(report_blockers, list) or not all(
        isinstance(item, str) for item in report_blockers
    ):
        blockers.append("release_gate_recheck_blockers_malformed")
    if not isinstance(report.get("release_gate_effect"), str):
        blockers.append("release_gate_recheck_effect_malformed")
    if report.get("read_only_invariants") != READ_ONLY_INVARIANTS:
        blockers.append("release_gate_recheck_invariants_malformed")
    gate = report.get("gate")
    if not isinstance(gate, Mapping):
        blockers.append("release_gate_recheck_nested_gate_malformed")
    elif gate.get("decision") != decision or gate.get("blockers") != report_blockers:
        blockers.append("release_gate_recheck_nested_gate_inconsistent")
    return blockers


def _collect_blockers(
    *,
    phase_synthesis_refresh: dict[str, Any],
    release_gate_recheck: dict[str, Any],
    torch_pack: dict[str, Any],
    docker_pack: dict[str, Any],
    wall_now: dt.datetime,
) -> list[str]:
    blockers: list[str] = []
    blockers.extend(_phase_shape_blockers(phase_synthesis_refresh))
    blockers.extend(_continuity_shape_blockers(release_gate_recheck))
    blockers.extend(
        _source_timestamp_blockers(
            phase_synthesis_refresh.get("generated_at_utc"),
            prefix="phase_synthesis_generated_at_utc",
            wall_now=wall_now,
        )
    )
    if phase_synthesis_refresh.get("schema_version") != PHASE_SYNTHESIS_SCHEMA_VERSION:
        blockers.append("phase_synthesis_schema_version_mismatch")
    if release_gate_recheck.get("schema_version") != LIVE_GATE_SCHEMA_VERSION:
        blockers.append("release_gate_recheck_schema_version_mismatch")
    blockers.extend(
        _source_timestamp_blockers(
            release_gate_recheck.get("checked_at_utc"),
            prefix="release_gate_recheck_checked_at_utc",
            wall_now=wall_now,
        )
    )
    if phase_synthesis_refresh.get("ok") is not True:
        blockers.append("phase_synthesis_refresh_not_ok")
    if phase_synthesis_refresh.get("release_boundary") != FALSE_RELEASE_BOUNDARY:
        blockers.append("phase_synthesis_release_boundary_mutated")

    gate = _release_gate_summary(release_gate_recheck)
    if gate["ok"] is not True:
        blockers.append("release_gate_recheck_report_not_ok")
    if gate["read_only"] is not True:
        blockers.append("release_gate_recheck_not_read_only")
    if gate["release_gate_effect"] != "none":
        blockers.append("release_gate_effect_not_none")
    if gate["release_boundary_all_false"] is not True:
        blockers.append("release_gate_release_boundary_mutated")
    if gate["release_gate_decision"] != "pass" or gate["blockers"]:
        blockers.append("release_gate_not_passed")

    for blocker in torch_pack.get("blockers") or []:
        blockers.append(f"torch_{blocker}")
    for blocker in docker_pack.get("blockers") or []:
        blockers.append(f"docker_{blocker}")
    return blockers


def _release_decision_packet(
    *,
    phase_synthesis_refresh: dict[str, Any],
    live_release_gate: Mapping[str, Any],
    ready: bool,
) -> dict[str, Any]:
    package = _source_release_soak_package(phase_synthesis_refresh)
    return {
        "schema_version": DECISION_PACKET_SCHEMA_VERSION,
        "id": FINALIZATION_TASK_ID,
        "decision_status": (
            "operator_finalization_required"
            if ready
            else "release_boundary_readiness_blocked"
        ),
        "default_recommendation": "hold_no_release_boundary_change",
        "source_status": package.get("status"),
        "source_acceptance": package.get("acceptance"),
        "release_gate_schema_version": live_release_gate.get("schema_version"),
        "release_gate_decision": live_release_gate.get("release_gate_decision"),
        "release_gate_blockers": (
            list(live_release_gate.get("blockers", []))
            if isinstance(live_release_gate.get("blockers"), list)
            else ["live_release_gate_blockers_malformed"]
        ),
        "release_boundary_effect_before_followup": "none",
        "operator_input_required": True,
        "operator_finalization_required": True,
        "decision_options": [
            {
                "id": "hold_no_release_boundary_change",
                "summary": "Keep all release-boundary guardrails closed.",
                "operator_action_required": False,
                "tag_creation_allowed": False,
                "docker_latest_move_allowed": False,
                "docker_stable_move_allowed": False,
                "stable_release_claim_allowed": False,
                "external_effect_authority_change_allowed": False,
                "next_status": "hold_operator_finalization_required",
            },
            {
                "id": "operator_finalizes_release_boundary_separately",
                "summary": (
                    "Operator may perform a separate release finalization; "
                    "this report still performs no release action."
                ),
                "operator_action_required": True,
                "tag_creation_allowed": False,
                "docker_latest_move_allowed": False,
                "docker_stable_move_allowed": False,
                "stable_release_claim_allowed": False,
                "external_effect_authority_change_allowed": False,
                "next_status": "operator_release_finalization_required",
                "followup_requirements": [
                    "operator_only_release_finalization",
                    "fresh_exact_head_ci",
                    "rco_and_bridge_preflight",
                    "signed_tag_or_release_receipt_after_operator_action",
                ],
            },
        ],
    }


def _assemble_report(
    *,
    phase_synthesis_refresh: dict[str, Any],
    release_gate_recheck: dict[str, Any],
    torch_pack: dict[str, Any],
    docker_pack: dict[str, Any],
    live_gate_summary: dict[str, Any],
    binding: dict[str, Any],
    report_checked_at: dt.datetime,
    wall_now: dt.datetime,
    blockers: list[str],
) -> dict[str, Any]:
    blockers.extend(_collect_blockers(
        phase_synthesis_refresh=phase_synthesis_refresh,
        release_gate_recheck=release_gate_recheck,
        torch_pack=torch_pack,
        docker_pack=docker_pack,
        wall_now=wall_now,
    ))
    ready = not blockers
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at_utc": _format_utc(report_checked_at),
        "ok": ready,
        "source_live_release_gate": live_gate_summary,
        "head_soak_binding": binding,
        "release_boundary_status": (
            "ready_for_operator_finalization"
            if ready
            else "hold_release_boundary_review_required"
        ),
        "release_boundary_blockers": blockers,
        "operator_finalization_required": True,
        "source_phase_synthesis_refresh": _source_phase_synthesis_summary(
            phase_synthesis_refresh
        ),
        "source_release_gate_readonly_recheck": _release_gate_summary(
            release_gate_recheck
        ),
        "operator_decision_packs": {
            "torch_cuda_vs_cpu": torch_pack,
            "docker_latest_promotion": docker_pack,
        },
        "release_decision_packet": _release_decision_packet(
            phase_synthesis_refresh=phase_synthesis_refresh,
            live_release_gate=live_gate_summary,
            ready=ready,
        ),
        "release_boundary_guardrails": {
            "release_boundary_effect": "none",
            "tag_creation_applied": False,
            "docker_latest_move_applied": False,
            "docker_stable_move_applied": False,
            "stable_release_claim_applied": False,
            "external_effect_authority_change_applied": False,
            "requires_operator_only_finalization": True,
        },
        "release_boundary": dict(FALSE_RELEASE_BOUNDARY),
        "read_only_invariants": {
            "no_tag_created": True,
            "no_docker_latest_moved": True,
            "no_docker_stable_moved": True,
            "no_stable_release_claim": True,
            "no_external_effect_authority_change": True,
            "release_boundary_effect": "none",
        },
    }


def _canonical_json(window: Mapping[str, Any], name: str) -> tuple[dict[str, Any], list[str]]:
    raw = window["inputs"][name].get("raw")
    if not isinstance(raw, bytes):
        return {}, [f"{name}_unreadable"]
    try:
        return _json_object(raw), []
    except (RecursionError, UnicodeError, ValueError):
        return {}, [f"{name}_malformed_json"]


def _build_canonical_report(
    checked_at_utc: dt.datetime | None,
    extra_blockers: list[str] | None = None,
) -> dict[str, Any]:
    wall_now = _utc_now()
    report_time, gate_time, time_blockers = _checked_at_evaluation(
        checked_at_utc, wall_now=wall_now
    )
    window = _open_window()
    pre_blockers = _pre_window_blockers(window)
    bootstrap_blockers: list[str] = []
    if (
        _TOOLS_PACKAGE_PRELOADED
        or _DECISION_PACK_PRELOADED
        or _DECISION_PACK_MODULE is not None
    ):
        bootstrap_blockers.append("repository_validator_preloaded")
    if not _PARENT_ISOLATED:
        bootstrap_blockers.append("parent_python_not_isolated")
    if not _PARENT_NO_SITE:
        bootstrap_blockers.append("parent_python_site_enabled")
    if not _TOOLS_PACKAGE_PRELOADED and _YAML_PRELOADED:
        bootstrap_blockers.append("repository_dependency_preloaded")
    prefix_blocker = _pycache_prefix_blocker(before_spawn=True)
    if prefix_blocker:
        bootstrap_blockers.append(prefix_blocker)
    phase, phase_blockers = _canonical_json(window, "phase_synthesis_refresh")
    gate, gate_blockers = _canonical_json(window, "release_gate_recheck")
    if pre_blockers or bootstrap_blockers:
        torch_pack = _unevaluated_pack_summary(
            window["inputs"]["torch_decision_pack"],
            "torch-cuda-vs-cpu",
            "dependency_security",
        )
        docker_pack = _unevaluated_pack_summary(
            window["inputs"]["docker_decision_pack"],
            "docker-latest-promotion",
            "docker_promotion",
        )
        blocker = "live_release_gate_not_run_input_integrity"
        live_summary = {
            "schema_version": None,
            "ok": False,
            "release_gate_decision": None,
            "blockers": [blocker],
        }
        live_blockers = [blocker]
    else:
        torch_pack = _torch_pack_summary(window["inputs"]["torch_decision_pack"])
        docker_pack = _docker_pack_summary(window["inputs"]["docker_decision_pack"])
        live_summary, live_blockers = _live_gate_evaluation(gate_time, wall_now=wall_now)
    binding, binding_blockers = _head_soak_binding(
        window["git_before"], window["inputs"]["soak_evidence"]
    )
    window_blockers = _close_window(window)
    blockers = [*time_blockers, *(extra_blockers or []), *pre_blockers,
                *bootstrap_blockers,
                *phase_blockers,
                *gate_blockers, *live_blockers, *binding_blockers,
                *window_blockers]
    return _assemble_report(
        phase_synthesis_refresh=phase, release_gate_recheck=gate,
        torch_pack=torch_pack, docker_pack=docker_pack,
        live_gate_summary=live_summary, binding=binding,
        report_checked_at=report_time, wall_now=wall_now, blockers=blockers)


def build_report(*, checked_at_utc: dt.datetime | None = None) -> dict[str, Any]:
    """Evaluate only captured canonical inputs; no caller-supplied grant data."""
    return _build_canonical_report(checked_at_utc)


def build_report_from_paths(
    *,
    phase_synthesis_refresh_path: Path,
    release_gate_recheck_path: Path,
    torch_decision_pack: Path,
    docker_decision_pack: Path,
    output_path: Path | None = None,
    checked_at_utc: dt.datetime | None = None,
) -> dict[str, Any]:
    supplied = {
        "phase_synthesis_refresh": phase_synthesis_refresh_path,
        "release_gate_recheck": release_gate_recheck_path,
        "torch_decision_pack": torch_decision_pack,
        "docker_decision_pack": docker_decision_pack,
    }
    blockers = []
    for name, path in supplied.items():
        try:
            supplied_path = Path(path)
            candidate = supplied_path if supplied_path.is_absolute() else ROOT / supplied_path
            canonical = os.path.normcase(os.path.abspath(CANONICAL_INPUTS[name]))
            matches = os.path.normcase(os.path.abspath(candidate)) == canonical
        except (OSError, TypeError, ValueError):
            matches = False
        if not matches:
            blockers.append(f"noncanonical_cli_override_{name}")
    output_blocker = _output_path_blocker(output_path)
    if output_blocker:
        blockers.append(output_blocker)
    return _build_canonical_report(checked_at_utc, blockers)


def _lexical_absolute(path: Path | str) -> Path:
    """Return an absolute normalized path without following any link."""
    return Path(os.path.abspath(os.fspath(path)))


def _lexical_key(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _lexically_below(path: Path, parent: Path) -> bool:
    path_key = _lexical_key(path)
    parent_key = _lexical_key(parent)
    try:
        common = os.path.normcase(os.path.commonpath((path_key, parent_key)))
    except (OSError, ValueError):
        return False
    return path_key != parent_key and common == parent_key


def _output_candidate(path: Path | str) -> Path:
    supplied = Path(path)
    return _lexical_absolute(supplied if supplied.is_absolute() else ROOT / supplied)


def _output_parent_components(candidate: Path) -> tuple[Path, ...]:
    root = _lexical_absolute(ROOT)
    parent = _lexical_absolute(candidate.parent)
    try:
        relative = os.path.relpath(parent, root)
    except (OSError, ValueError) as exc:
        raise OSError("output_parent_unverifiable") from exc
    if relative == os.curdir:
        parts: tuple[str, ...] = ()
    else:
        parts = Path(relative).parts
    if any(part in (os.curdir, os.pardir) for part in parts):
        raise OSError("output_parent_outside_root")
    components = [root]
    for part in parts:
        components.append(components[-1] / part)
    return tuple(components)


def _output_parent_snapshot(
    candidate: Path,
) -> tuple[tuple[tuple[str, int, int, int, int], ...], str | None]:
    """Capture every lexical parent without ever traversing a reparse point."""
    try:
        components = _output_parent_components(candidate)
    except OSError:
        return (), "output_parent_unverifiable"
    snapshot: list[tuple[str, int, int, int, int]] = []
    for component in components:
        try:
            info = os.lstat(component)
        except FileNotFoundError:
            return (), "output_parent_unavailable"
        except OSError:
            return (), "output_parent_unverifiable"
        if stat.S_ISLNK(info.st_mode) or _is_reparse(info):
            return (), "output_parent_reparse"
        if not stat.S_ISDIR(info.st_mode):
            return (), "output_parent_not_directory"
        snapshot.append((
            _lexical_key(component),
            info.st_dev,
            info.st_ino,
            info.st_mode,
            getattr(info, "st_file_attributes", 0),
        ))
    return tuple(snapshot), None


def _open_output_parent_handles(
    candidate: Path,
    expected_snapshot: tuple[tuple[str, int, int, int, int], ...],
) -> tuple[list[int], int | None]:
    """Pin all parent directories; return handles and a POSIX final dir-fd."""
    components = _output_parent_components(candidate)
    handles: list[int] = []
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        invalid = ctypes.c_void_p(-1).value
        for component in components:
            handle = create_file(
                str(component),
                0,
                0x00000001 | 0x00000002,  # share read/write, never delete
                None,
                3,  # OPEN_EXISTING
                0x02000000 | 0x00200000,  # BACKUP_SEMANTICS | OPEN_REPARSE_POINT
                None,
            )
            handle_value = int(handle) if handle is not None else 0
            if handle_value == invalid or handle_value == 0:
                error = ctypes.get_last_error()
                for opened in reversed(handles):
                    kernel32.CloseHandle(wintypes.HANDLE(opened))
                raise OSError(error, "output parent pin failed")
            handles.append(handle_value)
        return handles, None

    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
    ):
        raise OSError("output_parent_binding_unsupported")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        for index, component in enumerate(components):
            descriptor = (
                os.open(component, flags)
                if index == 0
                else os.open(component.name, flags, dir_fd=handles[-1])
            )
            handles.append(descriptor)
            info = os.fstat(descriptor)
            expected = expected_snapshot[index]
            if (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                getattr(info, "st_file_attributes", 0),
            ) != expected[1:]:
                raise OSError("output_parent_changed")
    except OSError:
        for descriptor in reversed(handles):
            os.close(descriptor)
        raise
    return handles, handles[-1]


def _close_output_parent_handles(handles: list[int]) -> None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        for handle in reversed(handles):
            close_handle(wintypes.HANDLE(handle))
        return
    for descriptor in reversed(handles):
        os.close(descriptor)


def _output_path_blocker(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        candidate = _output_candidate(path)
        default_output = _lexical_absolute(DEFAULT_OUTPUT)
        audit_root = _lexical_absolute(_PYCACHE_ROOT)
        is_audit_output = _lexically_below(candidate, audit_root)
        if _lexical_key(candidate) != _lexical_key(default_output) and not is_audit_output:
            return "output_path_not_allowed"
        _, parent_blocker = _output_parent_snapshot(candidate)
        if parent_blocker:
            return parent_blocker
        try:
            output_info = os.lstat(candidate)
        except FileNotFoundError:
            output_info = None
        if output_info is not None and (
            not stat.S_ISREG(output_info.st_mode)
            or stat.S_ISLNK(output_info.st_mode)
            or _is_reparse(output_info)
        ):
            return "output_path_not_regular"
        if output_info is not None and output_info.st_nlink != 1:
            return "output_path_multiple_links"
        canonical_paths = tuple(CANONICAL_INPUTS.values())
        if any(
            _lexical_key(candidate) == _lexical_key(canonical)
            for canonical in canonical_paths
        ):
            return "output_path_aliases_canonical_input"
        if candidate.exists() and any(
            os.path.samefile(candidate, canonical) for canonical in canonical_paths
        ):
            return "output_path_aliases_canonical_input"
    except (OSError, TypeError, ValueError):
        return "output_path_unverifiable"
    return None


def _write_output(path: Path, encoded: str) -> None:
    """Atomically replace an allowed output without truncating its old inode."""
    blocker = _output_path_blocker(path)
    if blocker:
        raise OSError(blocker)
    candidate = _output_candidate(path)
    parent_snapshot, parent_blocker = _output_parent_snapshot(candidate)
    if parent_blocker:
        raise OSError(parent_blocker)
    blocker = _output_path_blocker(candidate)
    if blocker:
        raise OSError(blocker)
    handles, parent_fd = _open_output_parent_handles(candidate, parent_snapshot)
    temporary = candidate.parent / f".{candidate.name}.tmp.{uuid.uuid4().hex}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOINHERIT", 0) | getattr(os, "O_NOFOLLOW", 0)
    temporary_identity: tuple[int, int] | None = None
    try:
        pinned_snapshot, parent_blocker = _output_parent_snapshot(candidate)
        if parent_blocker or pinned_snapshot != parent_snapshot:
            raise OSError(parent_blocker or "output_parent_changed")
        if parent_fd is None:
            descriptor = os.open(temporary, flags, 0o666)
        else:
            descriptor = os.open(temporary.name, flags, 0o666, dir_fd=parent_fd)
        try:
            output_info = os.fstat(descriptor)
            if not stat.S_ISREG(output_info.st_mode):
                raise OSError("output_path_not_regular")
            temporary_identity = (output_info.st_dev, output_info.st_ino)
            payload = encoded.encode("utf-8")
            while payload:
                written = os.write(descriptor, payload)
                if written <= 0:
                    raise OSError("output_write_incomplete")
                payload = payload[written:]
        finally:
            os.close(descriptor)
        blocker = _output_path_blocker(candidate)
        if blocker:
            raise OSError(blocker)
        before_replace, parent_blocker = _output_parent_snapshot(candidate)
        if parent_blocker or before_replace != parent_snapshot:
            raise OSError(parent_blocker or "output_parent_changed")
        if parent_fd is None:
            os.replace(temporary, candidate)
        else:
            os.replace(
                temporary.name,
                candidate.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        final_info = os.lstat(candidate)
        after_replace, parent_blocker = _output_parent_snapshot(candidate)
        if parent_blocker or after_replace != parent_snapshot:
            raise OSError(parent_blocker or "output_parent_changed")
        if (
            (final_info.st_dev, final_info.st_ino) != temporary_identity
            or not stat.S_ISREG(final_info.st_mode)
            or stat.S_ISLNK(final_info.st_mode)
            or _is_reparse(final_info)
            or final_info.st_nlink != 1
        ):
            raise OSError("output_replace_identity_mismatch")
    finally:
        try:
            if parent_fd is None:
                os.unlink(temporary)
            else:
                os.unlink(temporary.name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        finally:
            _close_output_parent_handles(handles)


def strict_exit_code(report: dict[str, Any]) -> int:
    return 0 if report.get("ok") is True else STRICT_BLOCKED_EXIT_CODE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase-synthesis-refresh",
        type=Path,
        default=DEFAULT_PHASE_SYNTHESIS_REFRESH,
    )
    parser.add_argument(
        "--release-gate-recheck",
        type=Path,
        default=DEFAULT_RELEASE_GATE_RECHECK,
    )
    parser.add_argument(
        "--torch-decision-pack",
        type=Path,
        default=DEFAULT_TORCH_DECISION_PACK,
    )
    parser.add_argument(
        "--docker-decision-pack",
        type=Path,
        default=DEFAULT_DOCKER_DECISION_PACK,
    )
    parser.add_argument(
        "--checked-at-utc",
        type=_parse_timestamp,
        help="Override report timestamp, ISO-8601 UTC.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Deprecated compatibility flag; blocked readiness always returns "
            "a non-zero exit code."
        ),
    )
    args = parser.parse_args(argv)

    report = build_report_from_paths(
        phase_synthesis_refresh_path=args.phase_synthesis_refresh,
        release_gate_recheck_path=args.release_gate_recheck,
        torch_decision_pack=args.torch_decision_pack,
        docker_decision_pack=args.docker_decision_pack,
        output_path=args.output,
        checked_at_utc=args.checked_at_utc,
    )
    try:
        encoded = json.dumps(
            report, indent=2, sort_keys=True, allow_nan=False
        ) + "\n"
    except (TypeError, ValueError):
        print("release_boundary_output_encoding_blocked", file=sys.stderr)
        return STRICT_BLOCKED_EXIT_CODE
    output_ok = True
    if args.output is not None:
        try:
            _write_output(args.output, encoded)
        except (OSError, TypeError, ValueError):
            output_ok = False
    if args.json and output_ok:
        print(encoded, end="")
    if not output_ok:
        print("release_boundary_output_write_blocked", file=sys.stderr)
        return STRICT_BLOCKED_EXIT_CODE
    return strict_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
