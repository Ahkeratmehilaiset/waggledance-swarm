"""Read-only Bridge v2 component doctor. No installation or auth probes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse


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
    "powershell": re.compile(r"^(\d{1,4}\.\d{1,4})(?:\.\d{1,5}){0,2}$"),
    "pwsh": re.compile(r"^(\d{1,4}\.\d{1,4})(?:\.\d{1,5}){0,2}$"),
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
OFFICIAL_HOSTS = {
    "python.org", "www.python.org", "git-scm.com", "learn.microsoft.com",
    "github.com", "cli.github.com", "docs.anthropic.com",
    "developers.openai.com", "docs.x.ai", "x.ai",
}


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
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,4}\.\d{1,4}\.\d{1,4}", value):
        raise DoctorError(f"{location} must be a three-part numeric version")
    return tuple(int(part) for part in value.split("."))


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
            pin = resolution["trusted_sha256"]
            if pin is not None and (not isinstance(pin, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", pin)):
                raise DoctorError(f"{loc}.resolution.trusted_sha256 invalid")
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
        url = install["source_url"]
        if not isinstance(url, str) or len(url) > 512:
            raise DoctorError(f"{loc}.install.source_url invalid")
        try:
            parsed = urlparse(url)
        except ValueError as exc:
            raise DoctorError(f"{loc}.install.source_url invalid") from exc
        if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS or parsed.username or parsed.password:
            raise DoctorError(f"{loc}.install.source_url must be an allowlisted official HTTPS URL")
        package_id = install["package_id"]
        if item["probe"] in NPM_NATIVE and package_id != resolution["package"]:
            raise DoctorError(f"{loc}.install.package_id differs from resolution package")
        if package_id is None and item["probe"] == "powershell" and item["platforms"] == ["windows"]:
            continue  # Windows PowerShell 5.1 is an OS component, not a package.
        if not isinstance(package_id, str) or not re.fullmatch(r"[A-Za-z0-9@._/\-]{1,128}", package_id):
            raise DoctorError(f"{loc}.install.package_id invalid")
    declared_features = {feature for item in components for feature in item["features"]}
    for alias, profile in profiles.items():
        if not set(profile["features"]).issubset(declared_features):
            raise DoctorError(f"profiles.{alias}.features not declared by components")
    return data


def _safe_executable(name, search_path):
    """Search absolute PATH entries only; never use cwd or Windows batch files."""
    for part in search_path.split(os.pathsep):
        directory = Path(part)
        if not part or not directory.is_absolute():
            continue
        candidates = [directory / (name + ".exe")] if os.name == "nt" else [directory / name]
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
            value = match.group(1)
            if probe in {"powershell", "pwsh"}:
                parts = value.split(".")
                value = ".".join((parts + ["0", "0"])[:3])
            versions.append(value)
    return versions[0] if len(versions) == 1 else None


def _sha256_bounded(path, maximum=512 * 1024 * 1024):
    if not path.is_file() or path.stat().st_size > maximum:
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_npm_native(probe, resolution, search_path):
    """Locate an allowlisted npm native payload; never execute its shell shims."""
    package = resolution["package"]
    for part in search_path.split(os.pathsep):
        directory = Path(part)
        if not part or not directory.is_absolute():
            continue
        if not any((directory / (probe + suffix)).is_file()
                   for suffix in (".cmd", ".ps1", "")):
            continue
        package_root = directory.joinpath("node_modules", *package.split("/"))
        metadata_path = package_root / "package.json"
        try:
            with metadata_path.open("rb") as stream:
                metadata_bytes = stream.read(65537)
            if len(metadata_bytes) > 65536:
                return None, None, "package_metadata_too_large"
            metadata = json.loads(metadata_bytes.decode("utf-8"), object_pairs_hook=_unique_pairs)
        except (OSError, UnicodeError, json.JSONDecodeError, DoctorError):
            return None, None, "package_metadata_invalid"
        if not isinstance(metadata, dict) or metadata.get("name") != package:
            return None, None, "package_identity_mismatch"
        package_version = metadata.get("version")
        if not isinstance(package_version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", package_version):
            return None, None, "package_version_invalid"
        if not isinstance(metadata.get("bin"), dict) or metadata["bin"].get(probe) != resolution["bin"]:
            return None, None, "package_bin_mismatch"
        for relpath in resolution["native_relpaths"]:
            candidate = package_root.joinpath(*relpath.split("/"))
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
                return None, None, "binary_hash_error"
            if digest is None:
                return None, None, "binary_too_large"
            provenance = {
                "method": "npm_native", "package": package,
                "package_version": package_version,
                "package_json_path": str(metadata_path),
                "package_json_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
                "binary_path": str(candidate_resolved), "sha256": digest,
                "verified": digest.lower() == (resolution["trusted_sha256"] or "").lower(),
            }
            return str(candidate_resolved), provenance, None
        return None, None, "native_binary_missing"
    return None, None, "npm_package_not_found"


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


def _run_bounded(argv, timeout, search_path=None):
    # Version checks need no account tokens, home directory, or interactive stdin.
    child_env = {"PATH": search_path if search_path is not None else os.environ.get("PATH", ""),
                 "NO_COLOR": "1", "CI": "1"}
    if os.name == "nt":
        for key in ("SystemRoot", "WINDIR"):
            if key in os.environ:
                child_env[key] = os.environ[key]
    else:
        child_env["LC_ALL"] = "C"
    try:
        process = subprocess.Popen(argv, shell=False, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                   env=child_env, start_new_session=os.name != "nt",
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
                close_scope()
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
        try:
            close_scope()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        reader.join(timeout=1)
        return None, "timeout"
    try:
        close_scope()
    except ProcessLookupError:
        pass
    reader.join(timeout=1)
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
                       timeout_seconds=None, probe_command=None):
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
            "found": None, "version": None, "min_version": component["min_version"],
            "auth": "unknown", "quota": "unknown", "turn_readiness": "unknown",
            "install": component["install"],
        }
        if component["probe"] in NPM_NATIVE:
            entry["provider_qualification"] = "unknown"
            entry["provenance"] = None
        if platform not in component["platforms"]:
            entry.update(status="unsupported", reason="platform_unsupported", found=False)
        else:
            probe = component["probe"]
            executable = PROBES[probe][0]
            if probe in NPM_NATIVE and platform == "windows":
                resolved, provenance, resolution_reason = _resolve_npm_native(
                    probe, component["resolution"], search_path)
                if provenance is not None:
                    entry["provenance"] = provenance
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
                        try:
                            direct_hash = _sha256_bounded(Path(direct))
                        except OSError:
                            direct_hash = None
                        entry["provenance"] = {"method": "direct_unverified", "binary_path": direct,
                                               "sha256": direct_hash, "verified": False}
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
            if resolved is not None and entry["reason"] is None:
                entry["found"] = True
                argv = (probe_command(probe) if probe_command else
                        [resolved, *PROBES[probe][1:]])
                output, reason = _run_bounded(
                    argv, timeout_seconds or component["timeout_seconds"], search_path)
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
    parser.add_argument("--timeout-seconds", type=float,
                        help="Bounded timeout override for every version probe (0.1..10)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable report")
    args = parser.parse_args(argv)
    try:
        data = json.loads(args.manifest.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
        result = inspect_components(data, lane=args.lane, features=args.features or ["bridge_core"],
                                    search_path=args.search_path,
                                    timeout_seconds=args.timeout_seconds)
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
