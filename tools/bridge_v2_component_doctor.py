"""Read-only Bridge v2 component doctor. No installation or auth probes."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


SCHEMA = "wd.bridge-components.v1"
REPORT_SCHEMA = "wd.bridge-component-report.v1"
PLATFORMS = {"windows", "linux", "darwin"}
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
SAFE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
VERSION = re.compile(r"\b(\d{1,4})\.(\d{1,4})(?:\.(\d{1,4}))?\b")
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
    _keys(data, {"schema", "components"}, {"schema", "components"}, "manifest")
    if data["schema"] != SCHEMA:
        raise DoctorError("unsupported manifest schema")
    components = data["components"]
    if not isinstance(components, list) or not components or len(components) > 64:
        raise DoctorError("components must contain 1..64 entries")
    seen = set()
    for index, item in enumerate(components):
        loc = f"components[{index}]"
        _keys(item, {"id", "kind", "probe", "platforms", "features", "required_for",
                     "min_version", "timeout_seconds", "install"},
              {"id", "kind", "probe", "platforms", "features", "required_for",
               "min_version", "timeout_seconds", "install"}, loc)
        if not isinstance(item["id"], str) or not SAFE_ID.fullmatch(item["id"]):
            raise DoctorError(f"{loc}.id invalid")
        if item["id"] in seen:
            raise DoctorError("duplicate component id")
        seen.add(item["id"])
        if item["kind"] not in KINDS:
            raise DoctorError(f"{loc}.kind unsupported")
        if item["probe"] not in PROBES:
            raise DoctorError(f"{loc}.probe unsupported")
        _ids(item["platforms"], f"{loc}.platforms", PLATFORMS)
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
            if rule["feature"] not in item["features"]:
                raise DoctorError(f"{loc}.required_for feature not declared")
            _ids(rule["lanes"], f"{loc}.required_for.lanes")
            _ids(rule["platforms"], f"{loc}.required_for.platforms", PLATFORMS)
            if not set(rule["platforms"]).issubset(item["platforms"]):
                raise DoctorError(f"{loc}.required_for platform not supported")
        install = item["install"]
        _keys(install, {"source_url", "package_id"}, {"source_url", "package_id"}, f"{loc}.install")
        url = install["source_url"]
        if not isinstance(url, str) or len(url) > 512:
            raise DoctorError(f"{loc}.install.source_url invalid")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS or parsed.username or parsed.password:
            raise DoctorError(f"{loc}.install.source_url must be an allowlisted official HTTPS URL")
        package_id = install["package_id"]
        if package_id is None and item["probe"] == "powershell" and item["platforms"] == ["windows"]:
            continue  # Windows PowerShell 5.1 is an OS component, not a package.
        if not isinstance(package_id, str) or not re.fullmatch(r"[A-Za-z0-9@._/\-]{1,128}", package_id):
            raise DoctorError(f"{loc}.install.package_id invalid")
    return data


def _platform():
    return "windows" if sys.platform == "win32" else "darwin" if sys.platform == "darwin" else "linux"


def inspect_components(data, *, lane, features, search_path=None, platform=None,
                       timeout_seconds=None, probe_command=None):
    """Run only allowlisted version probes. probe_command is a test seam, not manifest data."""
    validate_manifest(data)
    platform = platform or _platform()
    if platform not in PLATFORMS or not SAFE_ID.fullmatch(lane):
        raise DoctorError("invalid platform or lane")
    _ids(features, "features")
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
        required = any(rule["feature"] in selected and lane in rule["lanes"]
                       and platform in rule["platforms"] for rule in component["required_for"])
        entry = {
            "id": component["id"], "kind": component["kind"], "required": required,
            "features": selected, "status": "unknown", "reason": None,
            "found": None, "version": None, "min_version": component["min_version"],
            "auth": "unknown", "quota": "unknown", "turn_readiness": "unknown",
            "install": component["install"],
        }
        if platform not in component["platforms"]:
            entry.update(status="unsupported", reason="platform_unsupported", found=False)
        else:
            executable = PROBES[component["probe"]][0]
            resolved = shutil.which(executable, path=search_path)
            if resolved is None:
                entry.update(status="missing", reason="executable_not_found", found=False)
            else:
                entry["found"] = True
                argv = (probe_command(component["probe"]) if probe_command else
                        [resolved, *PROBES[component["probe"]][1:]])
                try:
                    # Never execute through a shell. Do not capture or publish arbitrary stderr.
                    proc = subprocess.run(argv, shell=False, capture_output=True, text=True,
                                          timeout=timeout_seconds or component["timeout_seconds"],
                                          check=False)
                except subprocess.TimeoutExpired:
                    entry["reason"] = "timeout"
                except (OSError, UnicodeError):
                    entry["reason"] = "probe_error"
                else:
                    if proc.returncode != 0:
                        entry["reason"] = "nonzero_exit"
                    else:
                        match = VERSION.search(proc.stdout[:4096])
                        if not match:
                            entry["reason"] = "malformed_output"
                        else:
                            found_version = tuple(int(x or 0) for x in match.groups())
                            entry["version"] = ".".join(str(x) for x in found_version)
                            if found_version < _version(component["min_version"], "min_version"):
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
        "schema": REPORT_SCHEMA, "platform": platform, "lane": lane,
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
        data = json.loads(args.manifest.read_text(encoding="utf-8"))
        result = inspect_components(data, lane=args.lane, features=args.features or ["bridge_core"],
                                    search_path=args.search_path,
                                    timeout_seconds=args.timeout_seconds)
    except (OSError, json.JSONDecodeError, DoctorError) as exc:
        result = {"schema": REPORT_SCHEMA, "overall": "invalid_manifest", "error": str(exc),
                  "exit_code": 3}
    print(json.dumps(result, sort_keys=True, indent=2 if args.json else None))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
