import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.bridge_v2_component_doctor import DoctorError, inspect_components, main, validate_manifest


def manifest(probe="python", required=True):
    return {
        "schema": "wd.bridge-components.v1",
        "components": [{
            "id": "runtime", "kind": "cli", "probe": probe,
            "platforms": ["windows", "linux", "darwin"],
            "features": ["bridge_core"],
            "required_for": [{"feature": "bridge_core", "lanes": ["tools"],
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
