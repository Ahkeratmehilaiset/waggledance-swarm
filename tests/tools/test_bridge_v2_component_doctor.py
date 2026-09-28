import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.bridge_v2_component_doctor import DoctorError, inspect_components, main, validate_manifest


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
    import shutil
    monkeypatch.chdir(tmp_path)
    shutil.copy2(sys.executable, tmp_path / "python.exe")
    empty = tmp_path / "empty"
    empty.mkdir()
    result = inspect_components(manifest(), lane="tools", features=["bridge_core"],
                                search_path=str(empty), platform="windows")
    assert result["components"][0]["status"] == "missing"
    (empty / "python.cmd").write_text("@echo off\necho Python 999.0.0\n", encoding="utf-8")
    result = inspect_components(manifest(), lane="tools", features=["bridge_core"],
                                search_path=str(empty), platform="windows")
    assert result["components"][0]["status"] == "missing"
