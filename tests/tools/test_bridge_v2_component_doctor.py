import errno
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
import tools.bridge_v2_component_doctor as doctor

from tools.bridge_v2_component_doctor import (
    DoctorError, _parse_probe_version, _run_bounded, _version, inspect_components, main,
    validate_manifest,
)


@pytest.fixture(autouse=True)
def private_runtime_audit_root(tmp_path, monkeypatch):
    audit = tmp_path / "runtime-audit"
    audit.mkdir(mode=0o700)
    monkeypatch.setenv("WD_BRIDGE_DOCTOR_RUNTIME_AUDIT_ROOT", str(audit))


def manifest(probe="python", required=True):
    installs = {
        "python": ("https://www.python.org/downloads/", "Python.Python.3.13"),
        "powershell": ("https://learn.microsoft.com/powershell/scripting/install/installing-windows-powershell", None),
        "pwsh": ("https://learn.microsoft.com/powershell/scripting/install/installing-powershell", "Microsoft.PowerShell"),
        "claude": ("https://github.com/anthropics/claude-code", "@anthropic-ai/claude-code"),
    }
    source_url, package_id = installs.get(probe, installs["python"])
    return {
        "schema": "wd.bridge-components.v1",
        "supported_platforms": ["windows", "linux", "darwin"],
        "profiles": {"tools": {"lane": "codex-tools-1", "features": ["bridge_core"]},
                     "codex-tools-1": {"lane": "codex-tools-1", "features": ["bridge_core"]}},
        "components": [{
            "id": "runtime", "kind": "cli", "probe": probe,
            "platforms": ["windows", "linux", "darwin"],
            "features": ["bridge_core"],
            "required_for": [{"feature": "bridge_core", "lanes": ["codex-tools-1"],
                              "platforms": ["windows", "linux", "darwin"]}] if required else [],
            "min_version": "3.0.0", "timeout_seconds": 1,
            "install": {"source_url": source_url, "package_id": package_id},
        }],
    }


def test_manifest_rejects_untrusted_command_before_probe(tmp_path):
    data = manifest()
    data["components"][0]["command"] = ["python", "-c", "print('unsafe')"]
    with pytest.raises(DoctorError, match="unknown keys"):
        validate_manifest(data)
    data = manifest("python -c unsafe")
    with pytest.raises(DoctorError, match="probe"):
        validate_manifest(data)


def test_caller_manifest_cannot_self_pin_provider():
    data = manifest("claude")
    data["components"][0]["install"]["package_id"] = "@anthropic-ai/claude-code"
    data["components"][0]["resolution"] = {
        "kind": "npm_native", "package": "@anthropic-ai/claude-code",
        "bin": "bin/claude.exe", "native_relpaths": ["bin/claude.exe"],
        "trusted_sha256": "a" * 64,
    }
    with pytest.raises(DoctorError, match="externally approved|trusted_sha256"):
        validate_manifest(data)


@pytest.mark.parametrize("probe,url,package", [
    ("python", "https://github.com/lookalike/python", "Python.Python.3.13"),
    ("python", "https://www.python.org/downloads/../evil", "Python.Python.3.13"),
    ("python", "https://www.python.org/downloads/", "../../evil"),
    ("claude", "https://github.com/someone-else/claude-code-lookalike",
     "@anthropic-ai/claude-code"),
    ("claude", "https://github.com/anthropics/claude-code", "../../evil"),
])
def test_install_metadata_is_exact_per_probe(probe, url, package):
    data = manifest(probe)
    if probe == "claude":
        data["components"][0]["resolution"] = {
            "kind": "npm_native", "package": "@anthropic-ai/claude-code",
            "bin": "bin/claude.exe", "native_relpaths": ["bin/claude.exe"],
            "trusted_sha256": None,
        }
    data["components"][0]["install"] = {"source_url": url, "package_id": package}
    with pytest.raises(DoctorError, match="install"):
        validate_manifest(data)


def test_profile_alias_must_match_canonical_features():
    data = manifest()
    data["profiles"]["tools"]["features"] = ["other_feature"]
    with pytest.raises(DoctorError, match="alias|canonical"):
        validate_manifest(data)


def test_isolated_path_required_missing_and_optional_missing(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    required = inspect_components(manifest(), lane="tools", features=["bridge_core"],
                                  search_path=str(empty), platform="windows")
    assert required["components"][0]["status"] == "missing"
    assert required["components"][0]["found"] is False
    assert required["components"][0]["auth"] == "unknown"
    assert required["required_missing"] == ["runtime"]
    assert required["exit_code"] == 2
    optional = inspect_components(manifest(required=False), lane="tools",
                                  features=["bridge_core"], search_path=str(empty),
                                  platform="windows")
    assert optional["required_missing"] == []
    assert optional["disabled_features"] == ["bridge_core"]
    assert optional["exit_code"] == 0


def test_actual_python_probe_success():
    result = inspect_components(manifest(), lane="tools", features=["bridge_core"],
                                search_path=str(Path(sys.executable).parent),
                                platform="windows" if sys.platform == "win32" else "linux")
    item = result["components"][0]
    assert item["status"] == "ok"
    assert item["found"] is True
    assert item["version"].startswith("3.")
    assert item["auth"] == item["quota"] == item["turn_readiness"] == "unknown"
    assert result["readiness"] == "unknown"
    assert result["presence_scope"] == "components_only"


def test_wrong_version_and_timeout_override():
    data = manifest()
    data["components"][0]["min_version"] = "999.0.0"
    result = inspect_components(data, lane="tools", features=["bridge_core"],
                                search_path=str(Path(sys.executable).parent),
                                platform="windows" if sys.platform == "win32" else "linux")
    assert result["components"][0]["status"] == "wrong_version"
    assert result["exit_code"] == 2
    with pytest.raises(DoctorError, match="timeout_seconds"):
        inspect_components(manifest(), lane="tools", features=["bridge_core"],
                           timeout_seconds=100)


@pytest.mark.parametrize("script,expected", [
    ("import time;time.sleep(2)", "timeout"),
    ("import sys;sys.exit(7)", "nonzero_exit"),
    ("print('not-a-version')", "malformed_output"),
])
def test_real_subprocess_failures_are_unknown(script, expected):
    # The injected probe is a real child process; manifest data cannot supply argv.
    def child(_):
        return [sys.executable, "-c", script]

    result = inspect_components(manifest(), lane="tools", features=["bridge_core"],
                                search_path=str(Path(sys.executable).parent),
                                platform="windows" if sys.platform == "win32" else "linux",
                                probe_command=child)
    item = result["components"][0]
    assert item["status"] == "unknown"
    assert item["reason"] == expected
    assert result["exit_code"] == 2


def test_cli_json_and_required_missing_exit(tmp_path, capsys):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest()), encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()
    code = main(["--manifest", str(path), "--lane", "tools", "--feature", "bridge_core",
                 "--path", str(empty), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 2
    assert report["required_missing"] == ["runtime"]
    assert report["components"][0]["install"]["package_id"] == "Python.Python.3.13"


@pytest.mark.parametrize("lane,feature", [
    ("tools", "nonexistent_feature"), ("typo", "bridge_core"),
])
def test_cli_unknown_profile_or_feature_fails_closed(tmp_path, capsys, lane, feature):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest()), encoding="utf-8")
    code = main(["--manifest", str(path), "--lane", lane, "--feature", feature,
                 "--path", str(tmp_path / "missing"), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 3
    assert report["overall"] == "invalid_manifest"


def test_unsupported_requested_platform_feature_fails_closed():
    data = manifest()
    data["supported_platforms"] = ["windows"]
    data["components"][0]["platforms"] = ["windows"]
    data["components"][0]["required_for"][0]["platforms"] = ["windows"]
    with pytest.raises(DoctorError, match="platform"):
        inspect_components(data, lane="tools", features=["bridge_core"],
                           platform="linux", search_path="/missing")
    data["supported_platforms"] = ["windows", "linux"]
    with pytest.raises(DoctorError, match="platform-feature"):
        inspect_components(data, lane="tools", features=["bridge_core"],
                           platform="linux", search_path="/missing")


def test_actual_fleet_alias_cannot_downgrade_requiredness():
    data = json.loads((Path(__file__).resolve().parents[2] / "configs" /
                       "bridge_v2_components.json").read_text(encoding="utf-8"))
    result = inspect_components(data, lane="rco1", features=["bridge_core"],
                                platform="windows", search_path="C:\\does-not-exist")
    assert result["lane"] == "claude-rco-1"
    assert set(result["required_missing"]) == {"python313", "git", "windows_powershell"}
    assert all(item["required"] for item in result["components"])


def test_invalid_types_duplicate_json_keys_and_bad_utf8(tmp_path, capsys):
    data = manifest()
    data["components"][0]["probe"] = []
    with pytest.raises(DoctorError, match="probe"):
        validate_manifest(data)
    data = manifest()
    data["components"][0]["kind"] = []
    with pytest.raises(DoctorError, match="kind"):
        validate_manifest(data)
    data = manifest()
    data["components"][0]["install"]["source_url"] = "https://[broken"
    with pytest.raises(DoctorError, match="source_url"):
        validate_manifest(data)
    path = tmp_path / "manifest.json"
    path.write_text('{"schema":"wd.bridge-components.v1","schema":"wd.bridge-components.v1"}', encoding="utf-8")
    assert main(["--manifest", str(path), "--json"]) == 3
    assert "duplicate" in capsys.readouterr().out
    path.write_bytes(b"\xff")
    assert main(["--manifest", str(path), "--json"]) == 3
    assert "invalid_manifest" in capsys.readouterr().out


def test_oversized_real_subprocess_output_is_unknown():
    def child(_):
        return [sys.executable, "-c", "print('X' * 1000000)"]
    result = inspect_components(manifest(), lane="tools", features=["bridge_core"],
                                search_path=str(Path(sys.executable).parent),
                                platform="windows" if sys.platform == "win32" else "linux",
                                probe_command=child)
    assert result["components"][0]["status"] == "unknown"
    assert result["components"][0]["reason"] == "output_too_large"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows executable semantics")
def test_windows_explicit_path_ignores_cwd_and_batch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # A same-named cwd decoy must not be resolved, regardless of where Python
    # itself is installed (sys.executable may be an unreadable WindowsApps alias).
    (tmp_path / "python.exe").write_bytes(b"inert cwd decoy; never execute")
    empty = tmp_path / "empty"
    empty.mkdir()
    result = inspect_components(manifest(), lane="tools", features=["bridge_core"],
                                search_path=str(empty), platform="windows")
    assert result["components"][0]["status"] == "missing"
    (empty / "python.cmd").write_text("@echo off\necho Python 999.0.0\n", encoding="utf-8")
    result = inspect_components(manifest(), lane="tools", features=["bridge_core"],
                                search_path=str(empty), platform="windows")
    assert result["components"][0]["status"] == "missing"


def test_quoted_absolute_path_entry_is_used_without_cwd_fallback(tmp_path):
    executable = tmp_path / ("python.exe" if os.name == "nt" else "python")
    executable.write_bytes(b"inert version-probe fixture")
    executable.chmod(0o755)
    result = inspect_components(manifest(), lane="tools", features=["bridge_core"],
                                search_path=f'"{tmp_path}"',
                                platform="windows" if os.name == "nt" else "linux",
                                probe_command=lambda _: [sys.executable, "-c",
                                                         "import os; print('Python 3.13.7' if "
                                                         f"os.environ['PATH'] == {str(tmp_path)!r} "
                                                         "else 'bad-path')"])
    assert result["components"][0]["status"] == "ok"
    assert result["components"][0]["selected_path"] == str(executable)


def test_posix_path_quotes_are_literal_not_shell_syntax(tmp_path):
    quoted = f"'{tmp_path}'"
    assert list(doctor._path_directories(quoted, platform_name="posix")) == []
    if os.name == "nt":
        assert list(doctor._path_directories(quoted, platform_name="nt")) == [tmp_path]


def test_posix_path_preserves_literal_apostrophe_in_absolute_directory(tmp_path):
    literal = tmp_path / "it's-literal"
    literal.mkdir()
    executable = literal / "python"
    executable.write_bytes(b"fixture")
    executable.chmod(0o755)
    assert list(doctor._path_directories(str(literal), platform_name="posix")) == [literal]
    assert doctor._safe_executable("python", str(literal), platform_name="posix") == str(executable)


def test_scope_close_permission_error_is_classified_not_manifest_error():
    def denied():
        raise PermissionError("controlled POSIX killpg denial")

    assert doctor._close_probe_scope(denied) == "probe_scope_error"


def test_provider_required_only_when_explicitly_selected():
    data = json.loads((Path(__file__).resolve().parents[2] / "configs" /
                       "bridge_v2_components.json").read_text(encoding="utf-8"))
    core = inspect_components(data, lane="rco2", features=["bridge_core"],
                              platform="windows", search_path="C:\\does-not-exist")
    assert {item["id"] for item in core["components"]} == {
        "python313", "git", "windows_powershell"}
    selected = inspect_components(data, lane="rco2", features=["provider_codex"],
                                  platform="windows", search_path="C:\\does-not-exist")
    assert [item["id"] for item in selected["components"]] == ["codex_cli"]
    assert selected["components"][0]["auth"] == "unknown"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows npm shim layout")
def test_npm_native_package_detected_but_unpinned_not_executed(tmp_path):
    data = manifest("claude")
    data["components"][0]["install"]["package_id"] = "@anthropic-ai/claude-code"
    data["components"][0]["resolution"] = {
        "kind": "npm_native", "package": "@anthropic-ai/claude-code",
        "bin": "bin/claude.exe", "native_relpaths": ["bin/claude.exe"],
        "trusted_sha256": None,
    }
    (tmp_path / "claude.cmd").write_text("@echo off\nexit /b 99\n", encoding="utf-8")
    package = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code"
    (package / "bin").mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": "@anthropic-ai/claude-code",
                                                       "version": "2.1.283",
                                                       "bin": {"claude": "bin/claude.exe"}}), encoding="utf-8")
    (package / "bin" / "claude.exe").write_bytes(b"inert package binary")
    result = inspect_components(data, lane="tools", features=["bridge_core"],
                                search_path=f'"{tmp_path}"', platform="windows",
                                probe_command=lambda _: pytest.fail("untrusted binary executed"))
    item = result["components"][0]
    assert item["found"] is True
    assert item["status"] == "unknown"
    assert item["reason"] == "unverified_package_binary"
    assert item["provenance"]["package"] == "@anthropic-ai/claude-code"
    assert len(item["provenance"]["sha256"]) == 64
    assert item["pin_source"] == item["trust"] == "unknown"
    assert result["exit_code"] == 2
    data["components"][0]["resolution"]["trusted_sha256"] = item["provenance"]["sha256"]
    with pytest.raises(DoctorError, match="externally approved"):
        inspect_components(data, lane="tools", features=["bridge_core"],
                           search_path=str(tmp_path), platform="windows")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows executable handle binding")
def test_npm_native_swap_after_initial_hash_never_executes(tmp_path, monkeypatch):
    executable = tmp_path / "claude.exe"
    executable.write_bytes(b"original executable bytes")
    expected_digest = doctor._sha256_bounded(executable)
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"swapped executable bytes")
    replacement.replace(executable)
    monkeypatch.setattr(doctor, "_run_bounded", lambda *args, **kwargs:
                        pytest.fail("swapped binary reached process launch"))
    output, reason = doctor._run_verified_npm_native(
        [str(executable), "--version"], 2, str(tmp_path), str(executable), expected_digest)
    assert output is None
    assert reason == "binary_changed_before_execution"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file share and CreateProcess semantics")
def test_verified_binary_handle_denies_replace_and_allows_createprocess(tmp_path):
    sample = tmp_path / "sample.exe"
    replacement = tmp_path / "replacement.exe"
    sample.write_bytes(b"original")
    replacement.write_bytes(b"replacement")
    with doctor._locked_windows_binary_digest(sample) as digest:
        assert digest == hashlib.sha256(b"original").hexdigest()
        with pytest.raises(OSError):
            replacement.replace(sample)
    # sys.executable may be a WindowsApps alias, not a physical file that
    # CreateFileW can lock (for example on Microsoft Store Python installs).
    native = Path(os.environ["SystemRoot"]) / "System32" / "where.exe"
    assert native.is_file()
    with doctor._locked_windows_binary_digest(native) as digest:
        assert len(digest) == 64
        output, reason = _run_bounded([str(native), "cmd.exe"], 2)
    assert reason is None
    assert "cmd.exe" in output.lower()


def test_malformed_first_npm_shim_fails_closed_and_reports_selected_path(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "claude.cmd").write_text("@echo off\n", encoding="utf-8")
    (second / "claude.cmd").write_text("@echo off\n", encoding="utf-8")
    package = second / "node_modules" / "@anthropic-ai" / "claude-code"
    (package / "bin").mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({
        "name": "@anthropic-ai/claude-code", "version": "2.1.283",
        "bin": {"claude": "bin/claude.exe"}}), encoding="utf-8")
    (package / "bin" / "claude.exe").write_bytes(b"second candidate")
    data = manifest("claude")
    data["components"][0]["install"]["package_id"] = "@anthropic-ai/claude-code"
    data["components"][0]["resolution"] = {
        "kind": "npm_native", "package": "@anthropic-ai/claude-code",
        "bin": "bin/claude.exe", "native_relpaths": ["bin/claude.exe"],
        "trusted_sha256": None,
    }
    result = inspect_components(data, lane="tools", features=["bridge_core"],
                                search_path=os.pathsep.join((str(first), str(second))),
                                platform="windows", probe_command=lambda _:
                                pytest.fail("later shim must not be executed"))
    item = result["components"][0]
    assert item["status"] == "unknown"
    assert item["reason"] == "package_metadata_invalid"
    assert item["selected_path"] == str(first / "claude.cmd")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction provenance")
def test_junctioned_npm_package_chain_is_not_native_provenance(tmp_path):
    prefix = tmp_path / "npm"
    scope = prefix / "node_modules" / "@anthropic-ai"
    scope.mkdir(parents=True)
    (prefix / "claude.cmd").write_text("@echo off\n", encoding="utf-8")
    outside = tmp_path / "outside"
    (outside / "bin").mkdir(parents=True)
    (outside / "package.json").write_text(json.dumps({
        "name": "@anthropic-ai/claude-code", "version": "2.1.283",
        "bin": {"claude": "bin/claude.exe"}}), encoding="utf-8")
    (outside / "bin" / "claude.exe").write_bytes(b"inert linked binary")
    junction = scope / "claude-code"
    cmd = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
    created = subprocess.run([str(cmd), "/d", "/c", "mklink", "/J",
                              str(junction), str(outside)], capture_output=True)
    if created.returncode:
        pytest.fail("junction creation failed: " + created.stderr.decode("utf-8", errors="replace"))
    data = manifest("claude")
    data["components"][0]["resolution"] = {
        "kind": "npm_native", "package": "@anthropic-ai/claude-code",
        "bin": "bin/claude.exe", "native_relpaths": ["bin/claude.exe"],
        "trusted_sha256": None,
    }
    result = inspect_components(data, lane="tools", features=["bridge_core"],
                                search_path=str(prefix), platform="windows",
                                probe_command=lambda _: pytest.fail("linked binary executed"))
    item = result["components"][0]
    assert item["status"] == "unknown"
    assert item["reason"] == "package_path_alias"
    assert item["selected_path"] == str(prefix / "claude.cmd")
    assert item["provenance"] is None


@pytest.mark.parametrize("probe,output,expected", [
    ("python", "banner 99.9.9\nPython 3.13.7\n", "3.13.7"),
    ("git", "git version 2.54.0.windows.1\n", "2.54.0"),
    ("codex", "banner 99.9.9\ncodex-cli 0.157.1\n", "0.157.1"),
    ("claude", "2.1.283 (Claude Code)\n", "2.1.283"),
    ("python", "Python 3.14.0rc1\n", None),
    ("codex", "codex-cli 0.158.0-alpha.1\n", None),
    ("git", "banner 99.9.9 only\n", None),
    ("python", "Python 99.9.9\nPython 3.13.7\n", None),
    ("pwsh", "7.6\n", "7.6"),
    ("pwsh", "7.6.6\n", "7.6.6"),
    ("powershell", "5.1.26100.7309\n", "5.1.26100.7309"),
    ("pwsh", "7.7.0-preview.1\n", None),
])
def test_probe_specific_version_anchors_and_prerelease(probe, output, expected):
    assert _parse_probe_version(probe, output) == expected


def test_numeric_version_boundaries_preserve_patch_and_revision():
    assert _version("7.6", "version") == (7, 6, 0, 0)
    assert _version("7.6.6", "version") == (7, 6, 6, 0)
    assert _version("5.1.26100.7309", "version") == (5, 1, 26100, 7309)
    assert _version("7.6.5", "version") < _version("7.6.6", "minimum")
    assert _version("5.1.26100.7308", "version") < _version("5.1.26100.7309", "minimum")
    assert _version("7.6", "version") == _version("7.6.0", "minimum")


@pytest.mark.parametrize("probe,output,minimum,status", [
    ("pwsh", "7.6", "7.6.0", "ok"),
    ("pwsh", "7.6", "7.6.1", "wrong_version"),
    ("pwsh", "7.6.5", "7.6.6", "wrong_version"),
    ("pwsh", "7.6.6", "7.6.6", "ok"),
    ("powershell", "5.1.26100.7308", "5.1.26100.7309", "wrong_version"),
    ("powershell", "5.1.26100.7309", "5.1.26100.7309", "ok"),
    ("pwsh", "7.7.0-preview.1", "7.6.0", "unknown"),
])
def test_power_shell_probe_minimum_uses_full_numeric_version(
        tmp_path, probe, output, minimum, status):
    executable = tmp_path / (probe + (".exe" if os.name == "nt" else ""))
    executable.write_bytes(b"placeholder; the injected command is the real child")
    executable.chmod(0o755)
    data = manifest(probe)
    data["components"][0]["min_version"] = minimum
    result = inspect_components(
        data, lane="tools", features=["bridge_core"], search_path=str(tmp_path),
        platform="windows", probe_command=lambda _: [sys.executable, "-c", f"print({output!r})"],
    )
    item = result["components"][0]
    assert item["status"] == status
    assert item["version"] == (None if status == "unknown" else output)
    assert result["exit_code"] == (0 if status == "ok" else 2)


def test_probe_child_has_devnull_stdin_and_no_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-not-for-child")
    script = ("import os,sys; print('stdin=' + repr(sys.stdin.read(1))); "
              "print('secret=' + repr(os.getenv('OPENAI_API_KEY')))")
    output, reason = _run_bounded([sys.executable, "-c", script], 2)
    assert reason is None
    assert "stdin=''" in output
    assert "secret=None" in output


@pytest.mark.parametrize("ending,timeout,expected", [
    ("print('done')", 2, None),
    ("raise SystemExit(7)", 2, "nonzero_exit"),
    ("import time; time.sleep(2)", 0.2, "timeout"),
])
def test_probe_side_effects_stay_in_private_scratch(
        tmp_path, monkeypatch, ending, timeout, expected):
    monkeypatch.chdir(tmp_path)
    audit = Path(os.environ["WD_BRIDGE_DOCTOR_RUNTIME_AUDIT_ROOT"])
    before = set(audit.glob("bridge-doctor-probe-*"))
    code = (
        "import os,pathlib; "
        "pathlib.Path('.local/state/gh').mkdir(parents=True); "
        "pathlib.Path('.local/state/gh/device-id').write_text('scratch'); "
        "home=pathlib.Path(os.environ['HOME']); "
        "profile=pathlib.Path(os.environ['USERPROFILE']); "
        "assert home == profile; "
        f"assert pathlib.Path.cwd() != pathlib.Path({str(tmp_path)!r}); "
        "assert all(pathlib.Path(os.environ[key]).is_relative_to(home.parent) "
        "for key in ('APPDATA','LOCALAPPDATA','XDG_CONFIG_HOME','XDG_DATA_HOME',"
        "'XDG_STATE_HOME','XDG_CACHE_HOME','TEMP','TMP')); "
        + ending
    )
    monkeypatch.setenv("OPENAI_API_KEY", "not-for-probe")
    output, reason = _run_bounded([sys.executable, "-c", code], timeout)
    assert reason == expected
    if expected is None:
        assert output.strip() == "done"
    assert not (tmp_path / ".local").exists()
    assert not (tmp_path / "side-effect.txt").exists()
    assert set(audit.glob("bridge-doctor-probe-*")) == before


def test_probe_requires_explicit_nonalias_runtime_audit_root(tmp_path, monkeypatch):
    monkeypatch.delenv("WD_BRIDGE_DOCTOR_RUNTIME_AUDIT_ROOT")
    assert _run_bounded([sys.executable, "--version"], 1) == (None, "probe_scope_error")
    relative = Path("relative-audit")
    monkeypatch.setenv("WD_BRIDGE_DOCTOR_RUNTIME_AUDIT_ROOT", str(relative))
    assert _run_bounded([sys.executable, "--version"], 1) == (None, "probe_scope_error")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(tmp_path / "real", target_is_directory=True)
    except (OSError, NotImplementedError):
        return
    monkeypatch.setenv("WD_BRIDGE_DOCTOR_RUNTIME_AUDIT_ROOT", str(alias))
    assert _run_bounded([sys.executable, "--version"], 1) == (None, "probe_scope_error")


def test_probe_rejects_code_root_without_creating_audit(tmp_path, monkeypatch):
    code_root = Path(doctor.__file__).resolve().parents[1]
    audit = code_root / ".codex-audit" / "should-not-create-runtime-root"
    monkeypatch.setenv("WD_BRIDGE_DOCTOR_RUNTIME_AUDIT_ROOT", str(audit))
    assert _run_bounded([sys.executable, "--version"], 1) == (None, "probe_scope_error")
    assert not audit.exists()


def _remove_created_repo_audit_if_empty(path):
    try:
        path.rmdir()
    except OSError as exc:
        # Another run may still own a child, or may already have removed the base.
        if exc.errno not in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT) and \
                getattr(exc, "winerror", None) != 145:
            raise


def test_real_development_worktree_allows_repo_local_audit():
    code_root = Path(doctor.__file__).resolve().parents[1]
    assert doctor._code_layout(code_root) == "development"
    repo_audit = code_root / ".codex-audit"
    created_repo_audit = not repo_audit.exists()
    repo_audit.mkdir(mode=0o700, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="doctor-audit-", dir=repo_audit) as audit:
            audit_path = Path(audit)
            assert audit_path.is_relative_to(repo_audit)
            output, reason = _run_bounded([sys.executable, "--version"], 2,
                                          runtime_audit_root=audit_path)
            assert reason is None
            assert output.startswith("Python ")
    finally:
        if created_repo_audit:
            _remove_created_repo_audit_if_empty(repo_audit)


def test_repo_audit_cleanup_preserves_foreign_content_and_primary_failure(tmp_path):
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    foreign = audit / "another-run"
    foreign.mkdir()
    with pytest.raises(AssertionError, match="primary failure"):
        try:
            raise AssertionError("primary failure")
        finally:
            _remove_created_repo_audit_if_empty(audit)
    assert foreign.is_dir()
    foreign.rmdir()
    _remove_created_repo_audit_if_empty(audit)
    assert not audit.exists()


def test_layout_recognition_does_not_execute_ambient_git(tmp_path, monkeypatch):
    code_root = Path(doctor.__file__).resolve().parents[1]

    def forbidden(*args, **kwargs):
        pytest.fail("ambient command executed during layout recognition")

    monkeypatch.setattr(doctor.subprocess, "run", forbidden)
    assert doctor._code_layout(code_root) == "development"
    output, reason = _run_bounded([sys.executable, "--version"], 2,
                                  runtime_audit_root=tmp_path)
    assert reason is None
    assert output.startswith("Python ")


@pytest.fixture
def isolated_git_repo():
    with _isolated_git_repo() as repo:
        yield repo


def _fixture_git_env(repo):
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("GIT_")}
    config = repo.parent / "fixture-global-config"
    config.touch()
    env["GIT_CONFIG_GLOBAL"] = str(config)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def _fixture_git_command(repo, *args):
    hooks = repo.parent / "empty-hooks"
    hooks.mkdir(exist_ok=True)
    return ["git", "-c", f"safe.directory={repo}",
            "-c", f"core.hooksPath={hooks}", "-c", "commit.gpgsign=false",
            *args]


@contextmanager
def _isolated_git_repo():
    code_root = Path(doctor.__file__).resolve().parents[1]
    repo_audit = code_root / ".codex-audit"
    created_repo_audit = not repo_audit.exists()
    repo_audit.mkdir(mode=0o700, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="doctor-git-", dir=repo_audit) as scratch:
            repo = Path(scratch) / "repo"
            (repo / "tools").mkdir(parents=True)
            (repo / "tools" / "bridge_v2_component_doctor.py").write_text("# fixture\n")
            env = _fixture_git_env(repo)
            template = repo.parent / "empty-template"
            template.mkdir()
            subprocess.run(_fixture_git_command(repo, "-c", f"init.templateDir={template}",
                                                "init", "-q", str(repo)), check=True,
                           capture_output=True, env=env)
            subprocess.run(_fixture_git_command(repo, "-C", str(repo), "add",
                                                "tools/bridge_v2_component_doctor.py"),
                           check=True, capture_output=True, env=env)
            subprocess.run(_fixture_git_command(repo, "-c", "user.name=Doctor Fixture",
                                                "-c", "user.email=doctor-fixture@example.invalid",
                                                "-C", str(repo), "commit", "-q", "-m",
                                                "doctor fixture"),
                           check=True, capture_output=True, env=env)
            assert doctor._code_layout(repo) == "development"
            yield repo
    finally:
        if created_repo_audit:
            _remove_created_repo_audit_if_empty(repo_audit)


@contextmanager
def _isolated_worktree(source, linked, relative=False):
    env = _fixture_git_env(source)
    command = _fixture_git_command(source, "-c", "core.longpaths=true",
                                   "-C", str(source), "worktree", "add")
    if relative:
        command.append("--relative-paths")
    created = subprocess.run(command + ["--detach", str(linked), "HEAD"],
                             capture_output=True, env=env)
    if created.returncode:
        detail = created.stderr.decode("utf-8", errors="replace")
        if relative and "relative-paths" in detail and \
                ("unknown option" in detail or "unknown switch" in detail):
            pytest.skip("git worktree --relative-paths unsupported: " + detail)
        pytest.fail("isolated git worktree add failed: " + detail)
    try:
        yield linked
    finally:
        body_error = sys.exc_info()[1]
        cleanup_errors = []
        for args in (("-c", "core.longpaths=true", "-C", str(source),
                      "worktree", "remove", "--force", str(linked)),
                     ("-C", str(source), "worktree", "prune"),
                     ("-C", str(source), "worktree", "list", "--porcelain")):
            try:
                result = subprocess.run(_fixture_git_command(source, *args),
                                        check=True, capture_output=True, env=env)
                if args[-2:] == ("list", "--porcelain"):
                    inventory = result.stdout.decode("utf-8")
                    if inventory.count("worktree ") != 1 or "prunable" in inventory:
                        cleanup_errors.append(AssertionError(
                            "isolated fixture worktree registry is not clean: " + inventory))
            except (OSError, subprocess.CalledProcessError) as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            if body_error is not None:
                raise BaseExceptionGroup("worktree body and cleanup both failed",
                                         [body_error, *cleanup_errors])
            if len(cleanup_errors) == 1:
                raise cleanup_errors[0]
            raise ExceptionGroup("worktree cleanup failed", cleanup_errors)


@pytest.fixture
def actual_linked_worktree(isolated_git_repo):
    with _isolated_worktree(isolated_git_repo, isolated_git_repo.parent / "linked") as linked:
        yield linked


def test_actual_linked_worktree_uses_its_repo_audit(
        actual_linked_worktree, monkeypatch):
    linked = actual_linked_worktree
    assert (linked / ".git").is_file()
    audit = linked / ".codex-audit"
    audit.mkdir()
    monkeypatch.setattr(doctor, "__file__", str(linked / "tools" /
                                                "bridge_v2_component_doctor.py"))
    assert doctor._code_layout(linked) == "development"
    output, reason = _run_bounded([sys.executable, "--version"], 2,
                                  runtime_audit_root=audit)
    assert reason is None
    assert output.startswith("Python ")


def test_relative_linked_worktree_backlink_uses_gitdir(isolated_git_repo):
    with _isolated_worktree(isolated_git_repo, isolated_git_repo.parent / "relative-linked",
                            relative=True) as linked:
        marker = linked / ".git"
        assert marker.is_file()
        pointer = marker.read_text(encoding="utf-8").split(": ", 1)[1].strip()
        assert not Path(pointer).is_absolute()
        gitdir = (linked / pointer).resolve()
        backlink_file = gitdir / "gitdir"
        original_backlink = backlink_file.read_bytes()
        backlink = original_backlink.decode("utf-8").strip()
        assert not Path(backlink).is_absolute()
        assert doctor._code_layout(linked) == "development"
        try:
            backlink_file.write_text("../wrong/.git\n", encoding="utf-8")
            assert doctor._code_layout(linked) == "unknown"
        finally:
            backlink_file.write_bytes(original_backlink)
        assert doctor._code_layout(linked) == "development"


def test_isolated_worktree_registry_cleans_after_deliberate_failure(isolated_git_repo):
    with pytest.raises(RuntimeError, match="deliberate body failure"):
        with _isolated_worktree(isolated_git_repo, isolated_git_repo.parent / "failure-linked"):
            raise RuntimeError("deliberate body failure")
    inventory = subprocess.run(_fixture_git_command(isolated_git_repo, "-C",
                                                     str(isolated_git_repo), "worktree",
                                                     "list", "--porcelain"),
                               check=True, capture_output=True,
                               env=_fixture_git_env(isolated_git_repo)).stdout.decode("utf-8")
    assert inventory.count("worktree ") == 1 and "prunable" not in inventory


def test_isolated_git_fixture_ignores_hostile_git_environment_and_config(monkeypatch):
    repo_audit = Path(doctor.__file__).resolve().parents[1] / ".codex-audit"
    repo_audit.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="doctor-hostile-", dir=repo_audit) as scratch:
        scratch = Path(scratch)
        decoy = scratch / "decoy"
        decoy.mkdir()
        subprocess.run(_fixture_git_command(decoy, "init", "-q", str(decoy)),
                       check=True, capture_output=True, env=_fixture_git_env(decoy))
        before = sorted(str(path.relative_to(decoy)) for path in decoy.rglob("*"))
        hooks = scratch / "hostile-hooks"
        hooks.mkdir()
        marker = scratch / "hook-ran"
        (hooks / "pre-commit").write_text(
            "#!/bin/sh\nprintf touched > '" + marker.as_posix() + "'\nexit 99\n")
        (hooks / "post-checkout").write_text(
            "#!/bin/sh\nprintf touched > '" + marker.as_posix() + "'\nexit 99\n")
        global_config = scratch / "hostile-global-config"
        global_config.write_text("[core]\n\thooksPath = " + hooks.as_posix() +
                                 "\n[commit]\n\tgpgsign = true\n" +
                                 "[init]\n\ttemplateDir = " + hooks.as_posix() + "\n")
        monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
        monkeypatch.setenv("GIT_INDEX_FILE", str(decoy / ".git" / "index"))
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
        monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(global_config))
        monkeypatch.setenv("GIT_TEMPLATE_DIR", str(hooks))
        with _isolated_git_repo() as source:
            with _isolated_worktree(source, source.parent / "hostile-linked"):
                pass
            assert not (source / ".git" / "hooks" / "pre-commit").exists()
        assert not marker.exists()
        assert before == sorted(str(path.relative_to(decoy)) for path in decoy.rglob("*"))


def test_isolated_worktree_reports_body_and_cleanup_failures(isolated_git_repo,
                                                               monkeypatch):
    real_run = subprocess.run

    def cleanup_failure_after_real_remove(command, *args, **kwargs):
        result = real_run(command, *args, **kwargs)
        if "worktree" in command and "remove" in command:
            raise subprocess.CalledProcessError(41, command,
                                                stderr=b"deliberate cleanup failure")
        return result

    monkeypatch.setattr(subprocess, "run", cleanup_failure_after_real_remove)
    with pytest.raises(ExceptionGroup) as caught:
        with _isolated_worktree(isolated_git_repo,
                                isolated_git_repo.parent / "double-failure-linked"):
            raise RuntimeError("deliberate body failure")
    errors = caught.value.exceptions
    assert any(isinstance(error, RuntimeError) and
               "deliberate body failure" in str(error) for error in errors)
    assert any(isinstance(error, subprocess.CalledProcessError) and
               error.returncode == 41 and error.stderr == b"deliberate cleanup failure"
               for error in errors)
    inventory = real_run(_fixture_git_command(isolated_git_repo, "-C",
                                               str(isolated_git_repo), "worktree",
                                               "list", "--porcelain"),
                         check=True, capture_output=True,
                         env=_fixture_git_env(isolated_git_repo)).stdout.decode("utf-8")
    assert inventory.count("worktree ") == 1 and "prunable" not in inventory


def test_linked_worktree_malformed_backlink_fails_closed(actual_linked_worktree):
    marker = actual_linked_worktree / ".git"
    gitdir = Path(marker.read_text(encoding="utf-8").split(": ", 1)[1].strip())
    backlink = gitdir / "gitdir"
    original = backlink.read_bytes()
    try:
        backlink.write_text("C:/wrong/.git\n", encoding="utf-8")
        assert doctor._code_layout(actual_linked_worktree) == "unknown"
    finally:
        backlink.write_bytes(original)


def test_linked_gitdir_pointer_prefix_and_alias_are_guarded(
        actual_linked_worktree, tmp_path, monkeypatch):
    marker = actual_linked_worktree / ".git"
    original = marker.read_bytes()
    gitdir = Path(original.decode("utf-8").split(": ", 1)[1].strip())
    assert doctor._code_layout(actual_linked_worktree) == "development"
    original_read = doctor._read_bounded_metadata

    def pointer_bytes(data):
        return lambda path, maximum: (data if path == marker else
                                      original_read(path, maximum))

    with monkeypatch.context() as scoped:
        scoped.setattr(doctor, "_read_bounded_metadata",
                       pointer_bytes(original.replace(b"gitdir: ", b"gitdir:\t", 1)))
        assert doctor._code_layout(actual_linked_worktree) == "unknown"
    assert doctor._code_layout(actual_linked_worktree) == "development"
    alias = tmp_path / "gitdir-alias"
    if os.name == "nt":
        cmd = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
        created = subprocess.run([str(cmd), "/d", "/c", "mklink", "/J",
                                  str(alias), str(gitdir)], capture_output=True)
        if created.returncode:
            pytest.fail("junction creation failed: " + created.stderr.decode("utf-8", errors="replace"))
    else:
        alias.symlink_to(gitdir, target_is_directory=True)
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(doctor, "_read_bounded_metadata",
                           pointer_bytes(f"gitdir: {alias}\n".encode("utf-8")))
            assert doctor._code_layout(actual_linked_worktree) == "unknown"
    finally:
        alias.rmdir() if os.name == "nt" else alias.unlink()
    assert doctor._code_layout(actual_linked_worktree) == "development"


def test_linked_worktree_alias_gitdir_fails_closed(actual_linked_worktree, tmp_path):
    marker = actual_linked_worktree / ".git"
    gitdir = Path(marker.read_text(encoding="utf-8").split(": ", 1)[1].strip())
    commondir = gitdir / "commondir"
    original = commondir.read_bytes()
    common = (gitdir / original.decode("utf-8").strip()).resolve()
    alias = tmp_path / "common-alias"
    if os.name == "nt":
        cmd = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
        created = subprocess.run([str(cmd), "/d", "/c", "mklink", "/J",
                                  str(alias), str(common)], capture_output=True)
        if created.returncode:
            pytest.fail("junction creation failed: " + created.stderr.decode("utf-8", errors="replace"))
    else:
        alias.symlink_to(common, target_is_directory=True)
    try:
        commondir.write_text(f"{alias}\n", encoding="utf-8")
        assert doctor._code_layout(actual_linked_worktree) == "unknown"
    finally:
        commondir.write_bytes(original)
        alias.rmdir() if os.name == "nt" else alias.unlink()


def test_linked_worktree_core_fsmonitor_is_never_executed(
        actual_linked_worktree, monkeypatch):
    original_read = doctor._read_bounded_metadata

    def config_with_hook(path, maximum):
        data = original_read(path, maximum)
        if path.name == "config":
            return data + b"\n[core]\nfsmonitor = !untrusted-hook\n"
        return data

    with monkeypatch.context() as scoped:
        scoped.setattr(doctor, "_read_bounded_metadata", config_with_hook)
        scoped.setattr(doctor.subprocess, "run", lambda *args, **kwargs:
                       pytest.fail("core.fsmonitor spawned a command"))
        assert doctor._code_layout(actual_linked_worktree) == "development"


def test_git_index_unsupported_format_fails_closed(tmp_path, monkeypatch):
    source = Path(doctor.__file__).resolve().parents[1]
    marker = source / ".git"
    gitdir = (Path(marker.read_text(encoding="utf-8").split(": ", 1)[1].strip())
              if marker.is_file() else marker)
    index = (gitdir / "index").read_bytes()
    unsupported = bytearray(index)
    unsupported[4:8] = (4).to_bytes(4, "big")
    unsupported[-20:] = hashlib.sha1(unsupported[:-20]).digest()
    assert doctor._index_tracks_doctor(bytes(unsupported)) is False
    assert doctor._index_tracks_doctor(index) is True
    split_body = index[:-20] + b"link" + (0).to_bytes(4, "big")
    assert doctor._index_tracks_doctor(split_body + hashlib.sha1(split_body).digest()) is False
    target = b"tools/bridge_v2_component_doctor.py"
    other = index[:-20].replace(target, b"tools/bridge_v2_component_doctor.xy", 1)
    decoy = other + b"TREE" + len(target).to_bytes(4, "big") + target
    assert doctor._index_tracks_doctor(decoy + hashlib.sha1(decoy).digest()) is False


def test_git_index_checksum_exact_end_and_v2_flags_are_guarded():
    source = Path(doctor.__file__).resolve().parents[1]
    marker = source / ".git"
    gitdir = (Path(marker.read_text(encoding="utf-8").split(": ", 1)[1].strip())
              if marker.is_file() else marker)
    index = (gitdir / "index").read_bytes()
    assert doctor._index_tracks_doctor(index) is True
    bad_sha = index[:-1] + bytes([index[-1] ^ 1])
    assert doctor._index_tracks_doctor(bad_sha) is False
    trailing_body = index[:-20] + b"extra"
    assert doctor._index_tracks_doctor(
        trailing_body + hashlib.sha1(trailing_body).digest()) is False
    v2_extended = bytearray(index)
    v2_extended[4:8] = (2).to_bytes(4, "big")
    v2_extended[72] |= 0x40  # first entry's extended-flag bit
    v2_extended[-20:] = hashlib.sha1(v2_extended[:-20]).digest()
    assert doctor._index_tracks_doctor(bytes(v2_extended)) is False


@pytest.fixture
def small_git_repo(tmp_path):
    root = tmp_path / "dev"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "bridge_v2_component_doctor.py").write_text("# fixture\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-c", f"safe.directory={root}", "-C", str(root),
                    "add", "tools/bridge_v2_component_doctor.py"],
                   check=True, capture_output=True)
    assert doctor._code_layout(root) == "development"
    return root


def test_git_layout_head_core_and_objects_have_success_twins(small_git_repo):
    root = small_git_repo
    gitdir = root / ".git"
    for name, malformed in (("HEAD", b"not-a-ref\n"),
                            ("config", b"[not-core]\nvalue = true\n")):
        target = gitdir / name
        original = target.read_bytes()
        try:
            target.write_bytes(malformed)
            assert doctor._code_layout(root) == "unknown"
        finally:
            target.write_bytes(original)
        assert doctor._code_layout(root) == "development"
    objects = gitdir / "objects"
    held = gitdir / "objects-held"
    objects.rename(held)
    try:
        assert doctor._code_layout(root) == "unknown"
    finally:
        held.rename(objects)
    assert doctor._code_layout(root) == "development"


def test_git_marker_directory_alias_is_rejected(small_git_repo):
    root = small_git_repo
    marker = root / ".git"
    assert doctor._git_metadata_dirs(root, marker) == (marker, marker)
    real = root / "git-real"
    marker.rename(real)
    try:
        if os.name == "nt":
            cmd = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
            created = subprocess.run([str(cmd), "/d", "/c", "mklink", "/J",
                                      str(marker), str(real)], capture_output=True)
            if created.returncode:
                pytest.fail("junction creation failed: " + created.stderr.decode("utf-8", errors="replace"))
        else:
            marker.symlink_to(real, target_is_directory=True)
        try:
            with pytest.raises(OSError, match="aliased Git directory"):
                doctor._git_metadata_dirs(root, marker)
            assert doctor._code_layout(root) == "unknown"
        finally:
            marker.rmdir() if os.name == "nt" else marker.unlink()
    finally:
        real.rename(marker)
    assert doctor._git_metadata_dirs(root, marker) == (marker, marker)
    assert doctor._code_layout(root) == "development"


def test_git_metadata_alias_is_rejected(small_git_repo):
    root = small_git_repo
    gitdir = root / ".git"
    assert doctor._read_bounded_metadata(gitdir / "HEAD", 256)
    alias = root / "git-alias"
    if os.name == "nt":
        cmd = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
        created = subprocess.run([str(cmd), "/d", "/c", "mklink", "/J",
                                  str(alias), str(gitdir)], capture_output=True)
        if created.returncode:
            pytest.fail("junction creation failed: " + created.stderr.decode("utf-8", errors="replace"))
    else:
        alias.symlink_to(gitdir, target_is_directory=True)
    try:
        with pytest.raises(OSError):
            doctor._read_bounded_metadata(alias / "HEAD", 256)
    finally:
        alias.rmdir() if os.name == "nt" else alias.unlink()
    assert doctor._read_bounded_metadata(gitdir / "HEAD", 256)


def test_dangling_git_marker_is_unknown_not_installed(tmp_path, small_git_repo):
    root = tmp_path / "dangling"
    root.mkdir()
    assert doctor._code_layout(root) == "installed"
    marker = root / ".git"
    missing = root / "missing-git"
    if os.name == "nt":
        cmd = Path(os.environ["SystemRoot"]) / "System32" / "cmd.exe"
        created = subprocess.run([str(cmd), "/d", "/c", "mklink", "/J",
                                  str(marker), str(missing)], capture_output=True)
        if created.returncode:
            pytest.fail("dangling junction creation failed: " + created.stderr.decode("utf-8", errors="replace"))
    else:
        marker.symlink_to(missing, target_is_directory=True)
    try:
        assert os.path.lexists(marker)
        assert not marker.exists()
        assert doctor._code_layout(root) == "unknown"
    finally:
        marker.rmdir() if os.name == "nt" else marker.unlink()
    assert doctor._code_layout(small_git_repo) == "development"


def test_posix_path_preserves_leading_and_trailing_spaces(small_git_repo):
    directory = str(small_git_repo)
    assert list(doctor._path_directories(directory, platform_name="posix")) == [Path(directory)]
    assert list(doctor._path_directories(" " + directory, platform_name="posix")) == []
    trailing = list(doctor._path_directories(directory + " ", platform_name="posix"))
    assert len(trailing) == 1 and str(trailing[0]).endswith(" ")


def test_git_metadata_reads_are_bounded(tmp_path):
    oversized = tmp_path / "HEAD"
    oversized.write_bytes(b"x" * 257)
    with pytest.raises(OSError):
        doctor._read_bounded_metadata(oversized, 256)


def test_git_metadata_growth_after_stat_has_bounded_io(monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace

    class Meter(BytesIO):
        calls = []

        def read(self, size=-1):
            self.calls.append(size)
            return super().read(size)

    class GrowingPath:
        def stat(self):
            return SimpleNamespace(st_size=1)

        def open(self, mode):
            assert mode == "rb"
            return Meter(b"x" * 4096)

        def read_bytes(self):
            pytest.fail("unbounded read_bytes after stale stat")

    monkeypatch.setattr(doctor, "_path_chain_has_alias", lambda _: False)
    with pytest.raises(OSError):
        doctor._read_bounded_metadata(GrowingPath(), 256)
    assert Meter.calls == [257]


def test_installed_copy_never_writes_code_even_with_external_fallback(
        tmp_path, monkeypatch):
    installed = tmp_path / "installed"
    (installed / "tools").mkdir(parents=True)
    inside = installed / ".codex-audit"
    inside.mkdir()
    outside = tmp_path / "outside-audit"
    outside.mkdir()
    monkeypatch.setattr(doctor, "__file__", str(installed / "tools" /
                                                "bridge_v2_component_doctor.py"))
    assert doctor._code_layout(installed) == "installed"
    monkeypatch.setenv("WD_BRIDGE_DOCTOR_RUNTIME_AUDIT_ROOT", str(outside))
    assert _run_bounded([sys.executable, "--version"], 2,
                        runtime_audit_root=inside) == (None, "probe_scope_error")
    assert list(inside.iterdir()) == []
    output, reason = _run_bounded([sys.executable, "--version"], 2)
    assert reason is None
    assert output.startswith("Python ")
    assert list(inside.iterdir()) == []


def test_invalid_git_marker_does_not_grant_development_audit(
        tmp_path, monkeypatch):
    unknown = tmp_path / "unknown"
    (unknown / "tools").mkdir(parents=True)
    (unknown / ".git").mkdir()
    audit = unknown / ".codex-audit"
    audit.mkdir()
    monkeypatch.setattr(doctor, "__file__", str(unknown / "tools" /
                                                "bridge_v2_component_doctor.py"))
    assert doctor._code_layout(unknown) == "unknown"
    assert _run_bounded([sys.executable, "--version"], 2,
                        runtime_audit_root=audit) == (None, "probe_scope_error")
    external = tmp_path / "external-audit"
    external.mkdir()
    assert _run_bounded([sys.executable, "--version"], 2,
                        runtime_audit_root=external) == (None, "probe_scope_error")
    assert list(external.iterdir()) == []


def test_runtime_audit_help_distinguishes_installed_development_and_unknown(capsys):
    with pytest.raises(SystemExit) as result:
        main(["--help"])
    assert result.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "external for installed" in help_text
    assert "under .codex-audit for development" in help_text
    assert "unknown layouts refused" in help_text


def test_probe_cleanup_failure_is_separate_from_probe_result(monkeypatch):
    def denied(_):
        raise PermissionError("controlled cleanup failure")

    monkeypatch.setattr(doctor.shutil, "rmtree", denied)
    output, reason = _run_bounded([sys.executable, "--version"], 2)
    assert output is None
    assert reason == "probe_cleanup_error"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows npm native anchor")
def test_approved_native_failure_downgrades_top_level_trust(tmp_path, monkeypatch):
    data = manifest("claude")
    data["components"][0]["resolution"] = {
        "kind": "npm_native", "package": "@anthropic-ai/claude-code",
        "bin": "bin/claude.exe", "native_relpaths": ["bin/claude.exe"],
        "trusted_sha256": None,
    }
    (tmp_path / "claude.cmd").write_text("@echo off\nexit /b 99\n", encoding="utf-8")
    package = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code"
    (package / "bin").mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({
        "name": "@anthropic-ai/claude-code", "version": "2.1.283",
        "bin": {"claude": "bin/claude.exe"}}), encoding="utf-8")
    binary = package / "bin" / "claude.exe"
    binary.write_bytes(b"test-only approved fixture")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    monkeypatch.setattr(doctor, "APPROVED_NATIVE_PINS", {"claude": digest})

    @contextmanager
    def changed_before_locked_launch(_):
        yield "0" * 64

    monkeypatch.setattr(doctor, "_locked_windows_binary_digest", changed_before_locked_launch)
    monkeypatch.setattr(doctor, "_run_bounded", lambda *args, **kwargs:
                        pytest.fail("changed native binary was executed"))
    item = inspect_components(data, lane="tools", features=["bridge_core"],
                              search_path=str(tmp_path), platform="windows")["components"][0]
    assert item["status"] == "unknown"
    assert item["reason"] == "binary_changed_before_execution"
    assert item["provenance"]["verified"] is False
    assert item["trust"] == "unknown"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows npm native anchor")
def test_approved_native_package_version_mismatch_stays_unknown(tmp_path, monkeypatch):
    data = manifest("claude")
    data["components"][0]["resolution"] = {
        "kind": "npm_native", "package": "@anthropic-ai/claude-code",
        "bin": "bin/claude.exe", "native_relpaths": ["bin/claude.exe"],
        "trusted_sha256": None,
    }
    (tmp_path / "claude.cmd").write_text("@echo off\nexit /b 99\n", encoding="utf-8")
    package = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code"
    (package / "bin").mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({
        "name": "@anthropic-ai/claude-code", "version": "2.1.283",
        "bin": {"claude": "bin/claude.exe"}}), encoding="utf-8")
    binary = package / "bin" / "claude.exe"
    binary.write_bytes(b"test-only approved fixture")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    monkeypatch.setattr(doctor, "APPROVED_NATIVE_PINS", {"claude": digest})

    @contextmanager
    def same_locked_binary(_):
        yield digest

    monkeypatch.setattr(doctor, "_locked_windows_binary_digest", same_locked_binary)
    monkeypatch.setattr(doctor, "_run_bounded", lambda *args, **kwargs: ("2.1.284\n", None))
    item = inspect_components(data, lane="tools", features=["bridge_core"],
                              search_path=str(tmp_path), platform="windows")["components"][0]
    assert item["status"] == "unknown"
    assert item["reason"] == "package_version_mismatch"
    assert item["trust"] == "unknown"
    assert item["provenance"]["verified"] is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows npm native anchor")
@pytest.mark.parametrize("case,expected_reason", [
    ("lock_failure", "binary_lock_error"),
    ("too_large", "binary_too_large"),
    ("correct_anchor", None),
])
def test_approved_native_full_inspection_lock_outcomes(
        tmp_path, monkeypatch, case, expected_reason):
    data = manifest("claude")
    data["components"][0]["min_version"] = "2.1.0"
    data["components"][0]["resolution"] = {
        "kind": "npm_native", "package": "@anthropic-ai/claude-code",
        "bin": "bin/claude.exe", "native_relpaths": ["bin/claude.exe"],
        "trusted_sha256": None,
    }
    (tmp_path / "claude.cmd").write_text("@echo off\nexit /b 99\n", encoding="utf-8")
    package = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code"
    (package / "bin").mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({
        "name": "@anthropic-ai/claude-code", "version": "2.1.283",
        "bin": {"claude": "bin/claude.exe"}}), encoding="utf-8")
    binary = package / "bin" / "claude.exe"
    binary.write_bytes(b"test-only approved fixture")
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    monkeypatch.setattr(doctor, "APPROVED_NATIVE_PINS", {"claude": digest})

    @contextmanager
    def locked(_):
        if case == "lock_failure":
            raise PermissionError("controlled lock failure")
        yield None if case == "too_large" else digest

    monkeypatch.setattr(doctor, "_locked_windows_binary_digest", locked)
    launches = []

    def launch(*args, **kwargs):
        launches.append(args)
        return "2.1.283\n", None

    monkeypatch.setattr(doctor, "_run_bounded", launch)
    item = inspect_components(data, lane="tools", features=["bridge_core"],
                              search_path=str(tmp_path), platform="windows")["components"][0]
    assert item["reason"] == expected_reason
    assert item["status"] == ("ok" if case == "correct_anchor" else "unknown")
    assert item["trust"] == ("approved" if case == "correct_anchor" else "unknown")
    assert item["provenance"]["verified"] is (case == "correct_anchor")
    assert len(launches) == (1 if case == "correct_anchor" else 0)


def test_timeout_does_not_leave_grandchild_or_pipe_open(tmp_path):
    marker = tmp_path / "orphaned-child.txt"
    grandchild = ("import pathlib,time; time.sleep(0.7); "
                  f"pathlib.Path({str(marker)!r}).write_text('orphaned')")
    parent = ("import subprocess,sys,time; "
              f"subprocess.Popen([sys.executable,'-c',{grandchild!r}], "
              "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
              "time.sleep(5)")
    started = time.monotonic()
    _, reason = _run_bounded([sys.executable, "-c", parent], 0.2)
    assert reason == "timeout"
    assert time.monotonic() - started < 3
    time.sleep(1)
    assert not marker.exists(), "timed-out probe left its grandchild alive"


def test_fast_probe_exit_does_not_escape_process_scope(tmp_path):
    marker = tmp_path / "fast-orphan.txt"
    grandchild = ("import pathlib,time; time.sleep(0.7); "
                  f"pathlib.Path({str(marker)!r}).write_text('orphaned')")
    parent = ("import subprocess,sys; "
              f"subprocess.Popen([sys.executable,'-c',{grandchild!r}], "
              "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
              "print('Python 3.13.7')")
    output, reason = _run_bounded([sys.executable, "-c", parent], 2)
    assert reason is None
    assert output.strip() == "Python 3.13.7"
    time.sleep(1)
    assert not marker.exists(), "fast probe left its grandchild alive"
