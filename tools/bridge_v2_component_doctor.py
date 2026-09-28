"""Read-only Bridge v2 component doctor. No installation or auth probes.

Probe scoping is not a security sandbox: it supplies a private disposable cwd
and environment, but an executable can still access files allowed by its OS
identity. A version response attests neither an executable's DLL dependencies
nor account authentication, quota, or readiness.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
from types import MappingProxyType
from pathlib import Path


SCHEMA = "wd.bridge-components.v1"
REPORT_SCHEMA = "wd.bridge-component-report.v1"
PLATFORMS = {"windows", "linux", "darwin"}
MAX_OUTPUT_BYTES = 4096
KINDS = {"cli"}
# Manifest probe names select fixed, inert version commands; it cannot supply argv.
PROBES = {
    "python": ("python", "--version"),
    "git": ("git", "--version"),
    "powershell": ("powershell", "-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.ToString()"),
    "pwsh": ("pwsh", "-NoProfile", "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.ToString()"),
    "gh": ("gh", "--version"),
    "claude": ("claude", "--version"),
    "codex": ("codex", "--version"),
    "grok": ("grok", "--version"),
}
SAFE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
VERSION_TEXT = r"(\d{1,4}\.\d{1,4}\.\d{1,4})"
PROBE_VERSION_LINES = {
    "python": re.compile(r"^Python " + VERSION_TEXT + r"$"),
    "git": re.compile(r"^git version " + VERSION_TEXT + r"(?:\.windows\.\d+)?$"),
    "powershell": re.compile(r"^(\d{1,5}(?:\.\d{1,5}){1,3})$"),
    "pwsh": re.compile(r"^(\d{1,5}(?:\.\d{1,5}){1,3})$"),
    "gh": re.compile(r"^gh version " + VERSION_TEXT + r"(?: \([^\r\n]*\))?$"),
    "claude": re.compile(r"^" + VERSION_TEXT + r"(?: \(Claude Code\))?$"),
    "codex": re.compile(r"^codex(?:-cli)? " + VERSION_TEXT + r"$"),
    "grok": re.compile(r"^grok " + VERSION_TEXT + r"$"),
}
NPM_NATIVE = {
    "claude": ("@anthropic-ai/claude-code", "bin/claude.exe",
               ("bin/claude.exe",)),
    "codex": ("@openai/codex", "bin/codex.js",
              ("node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe",
               "vendor/x86_64-pc-windows-msvc/bin/codex.exe")),
}
OFFICIAL_INSTALL = MappingProxyType({
    "python": ("https://www.python.org/downloads/", "Python.Python.3.13"),
    "git": ("https://git-scm.com/downloads", "Git.Git"),
    "powershell": ("https://learn.microsoft.com/powershell/scripting/install/installing-windows-powershell", None),
    "pwsh": ("https://learn.microsoft.com/powershell/scripting/install/installing-powershell", "Microsoft.PowerShell"),
    "gh": ("https://cli.github.com/", "GitHub.cli"),
    "claude": ("https://github.com/anthropics/claude-code", "@anthropic-ai/claude-code"),
    "codex": ("https://developers.openai.com/codex/cli", "@openai/codex"),
})
# Empty until a separately reviewed code change supplies an authoritative pin.
# Neither manifest bytes nor an observed digest can populate this mapping.
APPROVED_NATIVE_PINS = MappingProxyType({})


class DoctorError(ValueError):
    """Invalid doctor input; no probe has run."""


def _keys(value, expected, required, location):
    if not isinstance(value, dict):
        raise DoctorError(f"{location} must be an object")
    extra = set(value) - expected
    missing = required - set(value)
    if extra:
        raise DoctorError(f"{location} unknown keys: {sorted(extra)}")
    if missing:
        raise DoctorError(f"{location} missing keys: {sorted(missing)}")


def _ids(values, location, allowed=None):
    if not isinstance(values, list) or not values:
        raise DoctorError(f"{location} must be a nonempty list")
    if len(values) != len(set(str(v) for v in values)):
        raise DoctorError(f"{location} contains duplicates")
    for value in values:
        if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
            raise DoctorError(f"{location} contains invalid id")
        if allowed is not None and value not in allowed:
            raise DoctorError(f"{location} contains unsupported value")


def _version(value, location):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,5}(?:\.\d{1,5}){1,3}", value):
        raise DoctorError(f"{location} must be a two-to-four-part numeric version")
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (4 - len(parts))


def validate_manifest(data):
    """Validate the complete manifest before any subprocess is started."""
    _keys(data, {"schema", "supported_platforms", "profiles", "components"},
          {"schema", "supported_platforms", "profiles", "components"}, "manifest")
    if data["schema"] != SCHEMA:
        raise DoctorError("unsupported manifest schema")
    _ids(data["supported_platforms"], "supported_platforms", PLATFORMS)
    profiles = data["profiles"]
    if not isinstance(profiles, dict) or not profiles or len(profiles) > 32:
        raise DoctorError("profiles must be a nonempty object with at most 32 entries")
    for alias, profile in profiles.items():
        if not isinstance(alias, str) or not SAFE_ID.fullmatch(alias):
            raise DoctorError("profile alias invalid")
        _keys(profile, {"lane", "features"}, {"lane", "features"}, f"profiles.{alias}")
        lane = profile["lane"]
        if not isinstance(lane, str) or not SAFE_ID.fullmatch(lane):
            raise DoctorError(f"profiles.{alias}.lane invalid")
        _ids(profile["features"], f"profiles.{alias}.features")
    if not any(alias == profile["lane"] for alias, profile in profiles.items()):
        raise DoctorError("profiles require at least one canonical lane")
    for alias, profile in profiles.items():
        canonical = profiles.get(profile["lane"])
        if canonical is None or canonical["lane"] != profile["lane"]:
            raise DoctorError(f"profiles.{alias} canonical lane missing")
        if set(profile["features"]) != set(canonical["features"]):
            raise DoctorError(f"profiles.{alias} alias features differ from canonical lane")
    components = data["components"]
    if not isinstance(components, list) or not components or len(components) > 64:
        raise DoctorError("components must contain 1..64 entries")
    seen = set()
    for index, item in enumerate(components):
        loc = f"components[{index}]"
        _keys(item, {"id", "kind", "probe", "platforms", "features", "required_for",
                     "min_version", "timeout_seconds", "install", "resolution"},
              {"id", "kind", "probe", "platforms", "features", "required_for",
               "min_version", "timeout_seconds", "install"}, loc)
        if not isinstance(item["id"], str) or not SAFE_ID.fullmatch(item["id"]):
            raise DoctorError(f"{loc}.id invalid")
        if item["id"] in seen:
            raise DoctorError("duplicate component id")
        seen.add(item["id"])
        if not isinstance(item["kind"], str) or item["kind"] not in KINDS:
            raise DoctorError(f"{loc}.kind unsupported")
        if not isinstance(item["probe"], str) or item["probe"] not in PROBES:
            raise DoctorError(f"{loc}.probe unsupported")
        resolution = item.get("resolution")
        if item["probe"] in NPM_NATIVE:
            _keys(resolution, {"kind", "package", "bin", "native_relpaths", "trusted_sha256"},
                  {"kind", "package", "bin", "native_relpaths", "trusted_sha256"},
                  f"{loc}.resolution")
            allowed_package, allowed_bin, allowed_paths = NPM_NATIVE[item["probe"]]
            if (resolution["kind"] != "npm_native" or
                    resolution["package"] != allowed_package or resolution["bin"] != allowed_bin):
                raise DoctorError(f"{loc}.resolution not allowlisted for probe")
            paths = resolution["native_relpaths"]
            if not isinstance(paths, list) or not paths or len(paths) != len(set(map(str, paths))) or \
                    any(not isinstance(path, str) or path not in allowed_paths for path in paths):
                raise DoctorError(f"{loc}.resolution.native_relpaths not allowlisted")
            if resolution["trusted_sha256"] is not None:
                raise DoctorError(f"{loc}.resolution.trusted_sha256 needs an externally approved anchor")
        elif resolution is not None:
            raise DoctorError(f"{loc}.resolution unsupported for probe")
        _ids(item["platforms"], f"{loc}.platforms", PLATFORMS)
        if not set(item["platforms"]).issubset(data["supported_platforms"]):
            raise DoctorError(f"{loc}.platforms not supported by manifest")
        _ids(item["features"], f"{loc}.features")
        _version(item["min_version"], f"{loc}.min_version")
        timeout = item["timeout_seconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0.1 <= timeout <= 10:
            raise DoctorError(f"{loc}.timeout_seconds must be 0.1..10")
        rules = item["required_for"]
        if not isinstance(rules, list) or len(rules) > 32:
            raise DoctorError(f"{loc}.required_for must be a list of at most 32 rules")
        for rule in rules:
            _keys(rule, {"feature", "lanes", "platforms"},
                  {"feature", "lanes", "platforms"}, f"{loc}.required_for")
            if not isinstance(rule["feature"], str) or rule["feature"] not in item["features"]:
                raise DoctorError(f"{loc}.required_for feature not declared")
            _ids(rule["lanes"], f"{loc}.required_for.lanes")
            if not set(rule["lanes"]).issubset({p["lane"] for p in profiles.values()}):
                raise DoctorError(f"{loc}.required_for lane not declared")
            _ids(rule["platforms"], f"{loc}.required_for.platforms", PLATFORMS)
            if not set(rule["platforms"]).issubset(item["platforms"]):
                raise DoctorError(f"{loc}.required_for platform not supported")
        install = item["install"]
        _keys(install, {"source_url", "package_id"}, {"source_url", "package_id"}, f"{loc}.install")
        official = OFFICIAL_INSTALL.get(item["probe"])
        if official is None or install["source_url"] != official[0]:
            raise DoctorError(f"{loc}.install.source_url must match the exact official source")
        if install["package_id"] != official[1]:
            raise DoctorError(f"{loc}.install.package_id must match the official package identifier")
    declared_features = {feature for item in components for feature in item["features"]}
    for alias, profile in profiles.items():
        if not set(profile["features"]).issubset(declared_features):
            raise DoctorError(f"profiles.{alias}.features not declared by components")
    return data


def _path_directories(search_path, platform_name=None):
    platform_name = platform_name or os.name
    for part in search_path.split(os.pathsep):
        if platform_name == "nt":
            part = part.strip()
            if len(part) >= 2 and part[0] == part[-1] and part[0] in ('"', "'"):
                part = part[1:-1]
        # POSIX PATH bytes are literal, including spaces and quote characters.
        if not part or (platform_name == "nt" and '"' in part):
            continue
        directory = Path(part)
        if directory.is_absolute():
            yield directory


def _safe_executable(name, search_path, platform_name=None):
    """Search absolute PATH entries only; never use cwd or Windows batch files."""
    platform_name = platform_name or os.name
    for directory in _path_directories(search_path, platform_name=platform_name):
        candidates = [directory / (name + ".exe")] if platform_name == "nt" else [directory / name]
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def _parse_probe_version(probe, output):
    """Accept only an exact probe-specific release line, never banner versions."""
    pattern = PROBE_VERSION_LINES[probe]
    versions = []
    for line in output.splitlines():
        match = pattern.fullmatch(line.strip())
        if match:
            versions.append(match.group(1))
    return versions[0] if len(versions) == 1 else None


def _sha256_bounded(path, maximum=512 * 1024 * 1024):
    if not path.is_file() or path.stat().st_size > maximum:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _locked_windows_binary_digest(path, maximum=512 * 1024 * 1024):
    """Hash a Windows file while denying writers and deletion through launch."""
    if os.name != "nt":
        raise OSError("Windows executable binding is unavailable")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # FILE_SHARE_READ alone denies write/delete opens and a rename of this file.
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateFileW read lock failed")
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError:
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(fd, "rb") as stream:
        if os.fstat(stream.fileno()).st_size > maximum:
            yield None
            return
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        yield digest.hexdigest()


def _run_verified_npm_native(argv, timeout, search_path, path, trusted_sha256,
                             runtime_audit_root=None):
    try:
        with _locked_windows_binary_digest(path) as digest:
            if digest is None:
                return None, "binary_too_large"
            if digest.lower() != trusted_sha256.lower():
                return None, "binary_changed_before_execution"
            return _run_bounded(argv, timeout, search_path, runtime_audit_root)
    except OSError:
        return None, "binary_lock_error"


def _path_chain_has_alias(path):
    """Refuse reparse/symlink aliases in an npm provenance chain."""
    for component in (path, *path.parents):
        try:
            mode = component.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return True
        try:
            junction = hasattr(component, "is_junction") and component.is_junction()
        except OSError:
            return True
        if (stat.S_ISLNK(mode.st_mode) or
                bool(getattr(mode, "st_file_attributes", 0) &
                     getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)) or
                junction):
            return True
    return False


def _resolve_npm_native(probe, resolution, search_path):
    """Locate an allowlisted npm native payload; never execute its shell shims."""
    package = resolution["package"]
    for directory in _path_directories(search_path):
        shim = next((directory / (probe + suffix)
                     for suffix in (".cmd", ".ps1", "")
                     if (directory / (probe + suffix)).is_file()), None)
        if shim is None:
            continue
        package_root = directory.joinpath("node_modules", *package.split("/"))
        if _path_chain_has_alias(package_root):
            return None, None, "package_path_alias", str(shim)
        metadata_path = package_root / "package.json"
        try:
            with metadata_path.open("rb") as stream:
                metadata_bytes = stream.read(65537)
            if len(metadata_bytes) > 65536:
                return None, None, "package_metadata_too_large", str(shim)
            metadata = json.loads(metadata_bytes.decode("utf-8"), object_pairs_hook=_unique_pairs)
        except (OSError, UnicodeError, json.JSONDecodeError, DoctorError):
            return None, None, "package_metadata_invalid", str(shim)
        if not isinstance(metadata, dict) or metadata.get("name") != package:
            return None, None, "package_identity_mismatch", str(shim)
        package_version = metadata.get("version")
        if not isinstance(package_version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", package_version):
            return None, None, "package_version_invalid", str(shim)
        if not isinstance(metadata.get("bin"), dict) or metadata["bin"].get(probe) != resolution["bin"]:
            return None, None, "package_bin_mismatch", str(shim)
        for relpath in resolution["native_relpaths"]:
            candidate = package_root.joinpath(*relpath.split("/"))
            if _path_chain_has_alias(candidate):
                return None, None, "package_path_alias", str(shim)
            try:
                candidate_resolved = candidate.resolve(strict=True)
                candidate_resolved.relative_to(package_root.resolve(strict=True))
            except (OSError, ValueError):
                continue
            if candidate_resolved.suffix.lower() != ".exe" or not candidate_resolved.is_file():
                continue
            try:
                digest = _sha256_bounded(candidate_resolved)
            except OSError:
                return None, None, "binary_hash_error", str(shim)
            if digest is None:
                return None, None, "binary_too_large", str(shim)
            approved_pin = APPROVED_NATIVE_PINS.get(probe)
            provenance = {
                "method": "npm_native", "package": package,
                "package_version": package_version,
                "package_json_path": str(metadata_path),
                "package_json_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
                "binary_path": str(candidate_resolved), "sha256": digest,
                "verified": bool(approved_pin) and digest.lower() == approved_pin.lower(),
                "pin_source": "code_reviewed" if approved_pin else "unknown",
                "trust": "approved" if approved_pin and digest.lower() == approved_pin.lower() else "unknown",
            }
            return str(candidate_resolved), provenance, None, str(candidate_resolved)
        return None, None, "native_binary_missing", str(shim)
    return None, None, "npm_package_not_found", None


def _windows_kill_on_close_job(process):
    """Attach only this probe process to a Windows kill-on-close job."""
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.get_last_error()
        kernel.CloseHandle(job)
        raise OSError(error, "SetInformationJobObject failed")
    if not kernel.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
        error = ctypes.get_last_error()
        kernel.CloseHandle(job)
        raise OSError(error, "AssignProcessToJobObject failed")
    return lambda: kernel.CloseHandle(job)


def _windows_resume_scoped_process(process):
    """Resume only after Job attachment, avoiding a fast-child escape race."""
    import ctypes
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    status = ntdll.NtResumeProcess(wintypes.HANDLE(process._handle))
    if status != 0:
        raise OSError(status, "NtResumeProcess failed")


def _code_layout(code_root):
    """Read Git metadata only; never run ambient Git or waive ownership checks.

    This recognizes a local development layout for scratch placement, not a
    security attestation of repository contents or a provider trust anchor.
    """
    marker = code_root / ".git"
    if not marker.exists():
        return "installed"
    if os.name == "nt" and code_root.drive.upper() != "C:":
        return "unknown"
    try:
        if not marker.is_dir() or _path_chain_has_alias(marker):
            return "unknown"
        head = (marker / "HEAD").read_text(encoding="ascii").strip()
        config = (marker / "config").read_text(encoding="utf-8")
        index_path = marker / "index"
        if index_path.stat().st_size > 32 * 1024 * 1024:
            return "unknown"
        index = index_path.read_bytes()
        if ((head.startswith("ref: refs/heads/") or
             re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head)) and
                "[core]" in config and (marker / "objects").is_dir() and
                index[:4] == b"DIRC" and
                int.from_bytes(index[4:8], "big") in (2, 3, 4) and
                b"tools/bridge_v2_component_doctor.py\x00" in index):
            return "development"
    except (OSError, UnicodeError, ValueError):
        pass
    return "unknown"


def _run_bounded(argv, timeout, search_path=None, runtime_audit_root=None):
    """Run in an explicit writable runtime audit root, never the code root."""
    location = (runtime_audit_root if runtime_audit_root is not None else
                os.environ.get("WD_BRIDGE_DOCTOR_RUNTIME_AUDIT_ROOT"))
    if not location:
        return None, "probe_scope_error"
    audit = Path(location)
    code_root = Path(__file__).resolve().parent.parent
    layout = _code_layout(code_root)
    if not audit.is_absolute() or _path_chain_has_alias(audit) or layout == "unknown":
        return None, "probe_scope_error"
    resolved_audit = audit.resolve()
    if resolved_audit.is_relative_to(code_root):
        development_audit = code_root / ".codex-audit"
        if (layout != "development" or
                not resolved_audit.is_relative_to(development_audit)):
            return None, "probe_scope_error"
    if audit == code_root:
        return None, "probe_scope_error"
    try:
        mode = audit.stat()
        if not audit.is_dir() or (os.name != "nt" and mode.st_uid != os.getuid()):
            return None, "probe_scope_error"
        if os.name != "nt" and mode.st_mode & 0o077:
            return None, "probe_scope_error"
        root = Path(tempfile.mkdtemp(prefix="bridge-doctor-probe-", dir=audit))
    except OSError:
        return None, "probe_scope_error"
    result = (None, "probe_scope_error")
    try:
        if root.resolve().parent == audit.resolve() and not _path_chain_has_alias(root):
            paths = {name: root / name for name in (
                "cwd", "home", "appdata", "localappdata", "xdg-config", "xdg-data",
                "xdg-state", "xdg-cache", "tmp")}
            for path in paths.values():
                path.mkdir()
            result = _run_bounded_scoped(argv, timeout, search_path, paths)
    except OSError:
        result = (None, "probe_scope_error")
    finally:
        try:
            if root.resolve().parent != audit.resolve() or _path_chain_has_alias(root):
                result = (None, "probe_cleanup_error")
            else:
                shutil.rmtree(root)
        except OSError:
            result = (None, "probe_cleanup_error")
    return result


def _close_probe_scope(close_scope):
    try:
        close_scope()
    except ProcessLookupError:
        pass
    except OSError:
        return "probe_scope_error"
    return None


def _run_bounded_scoped(argv, timeout, search_path, paths):
    # Version checks need no account tokens, home directory, or interactive stdin.
    raw_path = search_path if search_path is not None else os.environ.get("PATH", "")
    safe_path = os.pathsep.join(str(directory) for directory in _path_directories(raw_path))
    child_env = {"PATH": safe_path,
                 "NO_COLOR": "1", "CI": "1", "HOME": str(paths["home"]),
                 "USERPROFILE": str(paths["home"]), "APPDATA": str(paths["appdata"]),
                 "LOCALAPPDATA": str(paths["localappdata"]),
                 "XDG_CONFIG_HOME": str(paths["xdg-config"]),
                 "XDG_DATA_HOME": str(paths["xdg-data"]),
                 "XDG_STATE_HOME": str(paths["xdg-state"]),
                 "XDG_CACHE_HOME": str(paths["xdg-cache"]),
                 "TMP": str(paths["tmp"]), "TEMP": str(paths["tmp"]),
                 "TMPDIR": str(paths["tmp"])}
    if os.name == "nt":
        for key in ("SystemRoot", "WINDIR"):
            if key in os.environ:
                child_env[key] = os.environ[key]
    else:
        child_env["LC_ALL"] = "C"
    try:
        process = subprocess.Popen(argv, shell=False, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                   env=child_env, cwd=paths["cwd"],
                                   start_new_session=os.name != "nt",
                                   creationflags=0x00000004 if os.name == "nt" else 0)
    except OSError:
        return None, "probe_error"
    close_scope = None
    if os.name == "nt":
        try:
            close_scope = _windows_kill_on_close_job(process)
            _windows_resume_scoped_process(process)
        except OSError:
            if close_scope is not None:
                if _close_probe_scope(close_scope):
                    process.kill()  # Only the suspended child created above.
            else:
                process.kill()
            process.wait()
            process.stdout.close()
            return None, "probe_scope_error"
    else:
        close_scope = lambda: os.killpg(process.pid, signal.SIGKILL)
    output = bytearray()
    oversized = [False]

    def drain():
        try:
            while True:
                chunk = process.stdout.read(4096)
                if not chunk:
                    break
                remaining = MAX_OUTPUT_BYTES + 1 - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
                if len(output) > MAX_OUTPUT_BYTES:
                    oversized[0] = True
        finally:
            process.stdout.close()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        scope_error = _close_probe_scope(close_scope)
        if scope_error:
            try:
                process.kill()  # Only our direct child; never a foreign PID.
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(timeout=1)
        return None, scope_error or "timeout"
    scope_error = _close_probe_scope(close_scope)
    reader.join(timeout=1)
    if scope_error:
        return None, scope_error
    if reader.is_alive():
        return None, "probe_error"
    if oversized[0]:
        return None, "output_too_large"
    if process.returncode != 0:
        return None, "nonzero_exit"
    try:
        return output.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "malformed_output"


def _platform():
    return "windows" if sys.platform == "win32" else "darwin" if sys.platform == "darwin" else "linux"


def inspect_components(data, *, lane, features, search_path=None, platform=None,
                       timeout_seconds=None, probe_command=None,
                       runtime_audit_root=None):
    """Run only allowlisted version probes. probe_command is a test seam, not manifest data."""
    validate_manifest(data)
    platform = platform or _platform()
    if platform not in data["supported_platforms"]:
        raise DoctorError("requested platform unsupported")
    if not isinstance(lane, str) or lane not in data["profiles"]:
        raise DoctorError("unknown lane profile")
    _ids(features, "features")
    profile = data["profiles"][lane]
    if not set(features).issubset(profile["features"]):
        raise DoctorError("unknown or unsupported feature for lane profile")
    canonical_lane = profile["lane"]
    for feature in features:
        if not any(feature in item["features"] and platform in item["platforms"]
                   for item in data["components"]):
            raise DoctorError("requested platform-feature combination unsupported")
    if search_path is None:
        search_path = os.environ.get("PATH", "")
    if not isinstance(search_path, str):
        raise DoctorError("path must be a string")
    if timeout_seconds is not None and (isinstance(timeout_seconds, bool) or
                                        not isinstance(timeout_seconds, (int, float)) or
                                        not 0.1 <= timeout_seconds <= 10):
        raise DoctorError("timeout_seconds must be 0.1..10")
    results, required_missing, disabled = [], [], set()
    for component in data["components"]:
        selected = sorted(set(features) & set(component["features"]))
        if not selected:
            continue
        required = any(rule["feature"] in selected and canonical_lane in rule["lanes"]
                       and platform in rule["platforms"] for rule in component["required_for"])
        entry = {
            "id": component["id"], "kind": component["kind"], "required": required,
            "features": selected, "status": "unknown", "reason": None,
            "found": None, "version": None, "selected_path": None,
            "min_version": component["min_version"],
            "auth": "unknown", "quota": "unknown", "turn_readiness": "unknown",
            "install": component["install"],
        }
        if component["probe"] in NPM_NATIVE:
            entry["provider_qualification"] = "unknown"
            entry["provenance"] = None
            entry["pin_source"] = "unknown"
            entry["trust"] = "unknown"
        if platform not in component["platforms"]:
            entry.update(status="unsupported", reason="platform_unsupported", found=False)
        else:
            probe = component["probe"]
            executable = PROBES[probe][0]
            if probe in NPM_NATIVE and platform == "windows":
                resolved, provenance, resolution_reason, selected_path = _resolve_npm_native(
                    probe, component["resolution"], search_path)
                entry["selected_path"] = selected_path
                if provenance is not None:
                    entry["provenance"] = provenance
                    entry["pin_source"] = provenance["pin_source"]
                    entry["trust"] = provenance["trust"]
                    entry["found"] = True
                    if not provenance["verified"]:
                        entry["reason"] = "unverified_package_binary"
                        results.append(entry)
                        if required:
                            required_missing.append(component["id"])
                        else:
                            disabled.update(selected)
                        continue
                elif resolution_reason == "npm_package_not_found":
                    direct = _safe_executable(executable, search_path)
                    if direct:
                        entry.update(found=True, reason="unverified_direct_binary")
                        entry["selected_path"] = direct
                        try:
                            direct_hash = _sha256_bounded(Path(direct))
                        except OSError:
                            direct_hash = None
                        entry["provenance"] = {"method": "direct_unverified", "binary_path": direct,
                                               "sha256": direct_hash, "verified": False,
                                               "pin_source": "unknown", "trust": "unknown"}
                    else:
                        entry.update(found=False, status="missing", reason="executable_not_found")
                else:
                    entry.update(found=None, reason=resolution_reason)
            else:
                resolved = _safe_executable(executable, search_path)
                if resolved is None:
                    entry.update(status="missing", reason="executable_not_found", found=False)
                else:
                    entry["found"] = True
                    entry["selected_path"] = resolved
            if resolved is not None and entry["reason"] is None:
                entry["found"] = True
                argv = (probe_command(probe) if probe_command else
                        [resolved, *PROBES[probe][1:]])
                if entry.get("provenance") and entry["provenance"]["verified"]:
                    output, reason = _run_verified_npm_native(
                        argv, timeout_seconds or component["timeout_seconds"], search_path,
                        resolved, APPROVED_NATIVE_PINS[probe], runtime_audit_root)
                    if reason in {"binary_changed_before_execution", "binary_lock_error",
                                  "binary_too_large"}:
                        entry["provenance"]["verified"] = False
                        entry["provenance"]["trust"] = "unknown"
                        entry["trust"] = "unknown"
                else:
                    output, reason = _run_bounded(
                        argv, timeout_seconds or component["timeout_seconds"], search_path,
                        runtime_audit_root)
                if reason:
                    entry["reason"] = reason
                else:
                    parsed_version = _parse_probe_version(probe, output)
                    if parsed_version is None:
                        entry["reason"] = "malformed_output"
                    else:
                        found_version = _version(parsed_version, "detected version")
                        entry["version"] = parsed_version
                        if (entry.get("provenance") and
                                parsed_version != entry["provenance"]["package_version"]):
                            entry["reason"] = "package_version_mismatch"
                            entry["provenance"]["verified"] = False
                            entry["provenance"]["trust"] = "unknown"
                            entry["trust"] = "unknown"
                        elif found_version < _version(component["min_version"], "min_version"):
                            entry.update(status="wrong_version", reason="below_min_version")
                        else:
                            entry["status"] = "ok"
        if entry["status"] != "ok":
            if required:
                required_missing.append(component["id"])
            else:
                disabled.update(selected)
        results.append(entry)
    return {
        "schema": REPORT_SCHEMA, "platform": platform, "lane": canonical_lane,
        "features": features, "components": results,
        "required_missing": required_missing, "disabled_features": sorted(disabled),
        "presence_scope": "components_only", "readiness": "unknown",
        "overall": "blocked" if required_missing else "ok",
        "exit_code": 2 if required_missing else 0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        default=Path(__file__).resolve().parent.parent / "configs" / "bridge_v2_components.json")
    parser.add_argument("--lane", default="tools")
    parser.add_argument("--feature", action="append", dest="features")
    parser.add_argument("--path", dest="search_path", help="Explicit executable search PATH")
    parser.add_argument("--runtime-audit-root", type=Path,
                        help="Existing private writable directory outside the code root for probe scratch")
    parser.add_argument("--timeout-seconds", type=float,
                        help="Bounded timeout override for every version probe (0.1..10)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable report")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.manifest.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
        result = inspect_components(data, lane=args.lane, features=args.features or ["bridge_core"],
                                    search_path=args.search_path,
                                    timeout_seconds=args.timeout_seconds,
                                    runtime_audit_root=args.runtime_audit_root)
    except (OSError, UnicodeError, json.JSONDecodeError, DoctorError) as exc:
        result = {"schema": REPORT_SCHEMA, "overall": "invalid_manifest", "error": str(exc),
                  "exit_code": 3}
    print(json.dumps(result, sort_keys=True, indent=2 if args.json else None))
    return result["exit_code"]


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DoctorError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


if __name__ == "__main__":
    raise SystemExit(main())
