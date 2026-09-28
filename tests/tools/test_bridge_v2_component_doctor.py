import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tools.bridge_v2_component_doctor import (
    DoctorError, _parse_probe_version, _run_bounded, inspect_components, main,
    validate_manifest,
)


def manifest(probe="python", required=True):
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
            "install": {"source_url": "https://www.python.org/downloads/",
                        "package_id": "Python.Python.3.13"},
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
                                search_path=str(tmp_path), platform="windows",
                                probe_command=lambda _: pytest.fail("untrusted binary executed"))
    item = result["components"][0]
    assert item["found"] is True
    assert item["status"] == "unknown"
    assert item["reason"] == "unverified_package_binary"
    assert item["provenance"]["package"] == "@anthropic-ai/claude-code"
    assert len(item["provenance"]["sha256"]) == 64
    assert result["exit_code"] == 2
    data["components"][0]["resolution"]["trusted_sha256"] = item["provenance"]["sha256"]
    mismatch = inspect_components(data, lane="tools", features=["bridge_core"],
                                  search_path=str(tmp_path), platform="windows",
                                  probe_command=lambda _: [sys.executable, "-c",
                                                           "print('2.1.284 (Claude Code)')"])
    assert mismatch["components"][0]["reason"] == "package_version_mismatch"
    assert mismatch["exit_code"] == 2


@pytest.mark.parametrize("probe,output,expected", [
    ("python", "banner 99.9.9\nPython 3.13.7\n", "3.13.7"),
    ("git", "git version 2.54.0.windows.1\n", "2.54.0"),
    ("codex", "banner 99.9.9\ncodex-cli 0.157.1\n", "0.157.1"),
    ("claude", "2.1.283 (Claude Code)\n", "2.1.283"),
    ("python", "Python 3.14.0rc1\n", None),
    ("codex", "codex-cli 0.158.0-alpha.1\n", None),
    ("git", "banner 99.9.9 only\n", None),
    ("python", "Python 99.9.9\nPython 3.13.7\n", None),
])
def test_probe_specific_version_anchors_and_prerelease(probe, output, expected):
    assert _parse_probe_version(probe, output) == expected


def test_probe_child_has_devnull_stdin_and_no_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-not-for-child")
    script = ("import os,sys; print('stdin=' + repr(sys.stdin.read(1))); "
              "print('secret=' + repr(os.getenv('OPENAI_API_KEY')))")
    output, reason = _run_bounded([sys.executable, "-c", script], 2)
    assert reason is None
    assert "stdin=''" in output
    assert "secret=None" in output


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
