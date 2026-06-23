# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.select_affected_tests import select_affected_tests


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "select_affected_tests.py"


def _mkrepo(tmp_path: Path) -> Path:
    (tmp_path / "tests" / "tools").mkdir(parents=True)
    # A test that IMPORTS waggledance.x.alpha.
    (tmp_path / "tests" / "test_alpha.py").write_text(
        "from waggledance.x.alpha import thing\n\n\ndef test_a():\n    assert thing\n",
        encoding="utf-8",
    )
    # A convention test for tools/beta.py (no import; name carries the mapping).
    (tmp_path / "tests" / "tools" / "test_beta.py").write_text(
        "def test_b():\n    assert True\n", encoding="utf-8"
    )
    return tmp_path


# --- fail-safe to full suite -------------------------------------------------

def test_empty_input_forces_full(tmp_path):
    assert select_affected_tests([], tmp_path)["full_suite"] is True


def test_broad_impact_conftest_forces_full(tmp_path):
    _mkrepo(tmp_path)
    r = select_affected_tests(["tests/conftest.py"], tmp_path)
    assert r["full_suite"] is True
    assert "broad-impact" in r["reason"]


def test_pyproject_forces_full(tmp_path):
    _mkrepo(tmp_path)
    assert select_affected_tests(["pyproject.toml"], tmp_path)["full_suite"] is True


def test_charter_change_forces_full(tmp_path):
    _mkrepo(tmp_path)
    assert (
        select_affected_tests(
            ["waggledance/core/idle_consensus_charter.py"], tmp_path
        )["full_suite"]
        is True
    )


def test_init_change_forces_full(tmp_path):
    _mkrepo(tmp_path)
    assert (
        select_affected_tests(["waggledance/x/__init__.py"], tmp_path)["full_suite"]
        is True
    )


def test_source_with_no_test_fails_safe_to_full(tmp_path):
    _mkrepo(tmp_path)
    r = select_affected_tests(["waggledance/x/orphan.py"], tmp_path)
    assert r["full_suite"] is True
    assert "no affected test" in r["reason"]


def test_unknown_file_type_fails_safe_to_full(tmp_path):
    _mkrepo(tmp_path)
    assert (
        select_affected_tests(["docs/architecture/X.md"], tmp_path)["full_suite"]
        is True
    )


def test_one_orphan_among_mappable_forces_full(tmp_path):
    _mkrepo(tmp_path)
    r = select_affected_tests(
        ["waggledance/x/alpha.py", "waggledance/x/orphan.py"], tmp_path
    )
    assert r["full_suite"] is True


# --- correct narrowing -------------------------------------------------------

def test_changed_test_file_selects_itself(tmp_path):
    _mkrepo(tmp_path)
    r = select_affected_tests(["tests/test_alpha.py"], tmp_path)
    assert r["full_suite"] is False
    assert r["tests"] == ["tests/test_alpha.py"]


def test_source_with_importing_test_is_selected(tmp_path):
    _mkrepo(tmp_path)
    r = select_affected_tests(["waggledance/x/alpha.py"], tmp_path)
    assert r["full_suite"] is False
    assert "tests/test_alpha.py" in r["tests"]


def test_source_selected_by_name_convention(tmp_path):
    _mkrepo(tmp_path)
    r = select_affected_tests(["tools/beta.py"], tmp_path)
    assert r["full_suite"] is False
    assert "tests/tools/test_beta.py" in r["tests"]


def test_cli_json_files(tmp_path):
    _mkrepo(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--repo-root",
            str(tmp_path),
            "--files",
            "waggledance/x/alpha.py",
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["full_suite"] is False
    assert "tests/test_alpha.py" in payload["tests"]
