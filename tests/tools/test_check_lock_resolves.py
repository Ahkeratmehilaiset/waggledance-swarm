# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tools/check_lock_resolves.py.

No real pip call is spawned; the resolver gate is exercised with an
injected fake runner so the test never touches the network or the live
lock file.
"""
from __future__ import annotations

import subprocess
import json
from pathlib import Path

import pytest
from pip._vendor.packaging.markers import default_environment

from tools.check_lock_resolves import (
    DEFAULT_EXTRA_INDEX,
    _extract_conflicts,
    check_lock_resolves,
    main,
)


def _write_lock(tmp_path: Path, body: str = "pillow==12.2.0\n") -> Path:
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text(body, encoding="utf-8")
    return lock


def _ok_runner(lock_file, *, extra_index_url, cache_dir, timeout, report_path=None):
    if report_path is not None:
        report_path.write_text(json.dumps(_report([("pillow", "12.2.0")])))
    return subprocess.CompletedProcess(
        args=["pip"],
        returncode=0,
        stdout="Would install pillow-12.2.0\n",
        stderr="",
    )


def _conflict_runner(lock_file, *, extra_index_url, cache_dir, timeout, report_path=None):
    stderr = (
        "ERROR: Cannot install moviepy==2.2.1 and pillow==12.2.0 because these\n"
        "package versions have conflicting dependencies.\n"
        "\n"
        "The conflict is caused by:\n"
        "    The user requested pillow==12.2.0\n"
        "    moviepy 2.2.1 depends on Pillow<12.0 and >=9.2.0\n"
        "\n"
        "ERROR: ResolutionImpossible\n"
    )
    return subprocess.CompletedProcess(
        args=["pip"], returncode=1, stdout="", stderr=stderr
    )


def _timeout_runner(*_args, **_kwargs):
    raise subprocess.TimeoutExpired(cmd=["pip"], timeout=1.0)


def test_check_lock_resolves_ok_when_resolver_succeeds(tmp_path):
    lock = _write_lock(tmp_path)

    result = check_lock_resolves(
        lock,
        cache_dir=tmp_path / "cache",
        runner=_ok_runner,
    )

    assert result["ok"] is True
    assert result["returncode"] == 0
    assert result["conflicts"] == []
    assert result["lock_file"] == str(lock)


def test_check_lock_resolves_flags_resolution_impossible(tmp_path):
    lock = _write_lock(tmp_path)

    result = check_lock_resolves(
        lock,
        cache_dir=tmp_path / "cache",
        runner=_conflict_runner,
    )

    assert result["ok"] is False
    assert result["returncode"] == 1
    # Conflict-marker patterns from the tool catch the diagnostic.
    assert any("Cannot install" in line for line in result["conflicts"])
    assert any("ResolutionImpossible" in line for line in result["conflicts"])
    assert any(
        "The conflict is caused by" in line for line in result["conflicts"]
    )


def test_check_lock_resolves_missing_lock_file(tmp_path):
    missing = tmp_path / "does-not-exist.lock.txt"

    result = check_lock_resolves(
        missing,
        cache_dir=tmp_path / "cache",
        runner=_ok_runner,
    )

    assert result["ok"] is False
    assert result["returncode"] is None
    assert "lock file not found" in str(result.get("error", ""))


def test_check_lock_resolves_timeout(tmp_path):
    lock = _write_lock(tmp_path)

    result = check_lock_resolves(
        lock,
        cache_dir=tmp_path / "cache",
        runner=_timeout_runner,
    )

    assert result["ok"] is False
    assert result["returncode"] is None
    assert "timed out" in str(result.get("error", ""))


def test_extract_conflicts_dedups_markers():
    stderr = (
        "ERROR: Cannot install A and B\n"
        "ERROR: Cannot install A and B\n"
        "The conflict is caused by:\n"
        "    something\n"
    )
    conflicts = _extract_conflicts(stderr)
    assert len([c for c in conflicts if "Cannot install" in c]) == 1
    assert any("The conflict is caused by" in c for c in conflicts)


def test_main_exits_nonzero_on_conflict(tmp_path, monkeypatch, capsys):
    lock = _write_lock(tmp_path)
    monkeypatch.setattr(
        "tools.check_lock_resolves._run_pip_dry_install", _conflict_runner
    )
    rc = main(
        [
            "--lock-file",
            str(lock),
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )
    assert rc == 1
    captured = capsys.readouterr()
    assert '"ok": false' in captured.out.lower()


def test_main_exits_zero_on_clean_resolve(tmp_path, monkeypatch, capsys):
    lock = _write_lock(tmp_path)
    monkeypatch.setattr(
        "tools.check_lock_resolves._run_pip_dry_install", _ok_runner
    )
    out_path = tmp_path / "report.json"
    rc = main(
        [
            "--lock-file",
            str(lock),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--output",
            str(out_path),
        ]
    )
    assert rc == 0
    assert out_path.exists()
    captured = capsys.readouterr()
    assert '"ok": true' in captured.out.lower()


def test_default_extra_index_targets_cu126():
    assert "/cu126" in DEFAULT_EXTRA_INDEX


def _report(packages):
    return {
        "version": "1",
        "environment": default_environment(),
        "install": [
            {"metadata": {"name": name, "version": version}}
            for name, version in packages
        ],
    }


def _report_runner(report):
    def run(_lock_file, **kwargs):
        if kwargs.get("report_path") is not None:
            kwargs["report_path"].write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(["pip"], 0, "Would install packages", "")
    return run


@pytest.mark.parametrize(
    "body,packages",
    [
        ("pillow==12.2.0\n", [("pillow", "12.2.0"), ("uvloop", "0.22.1")]),
        ("pillow==12.2.0\n", [("pillow", "12.1.0")]),
        ("pillow==12.2.0\n", []),
        ("pillow==12.*\n", [("pillow", "12.2.0")]),
        ("pillow>=12.0\n", [("pillow", "12.2.0")]),
        ("torch==2.13.0\n", [("torch", "2.13.0+cu126")]),
        ("pillow==12.2.0; python_version < '1'\n", [("pillow", "12.2.0")]),
        ("pillow==12.2.0\n", [("pillow", "12.2.0"), ("pillow", "12.2.0")]),
    ],
)
def test_successful_resolve_rejects_incomplete_or_mismatched_lock(tmp_path, body, packages):
    result = check_lock_resolves(
        _write_lock(tmp_path, body), cache_dir=tmp_path / "cache",
        runner=_report_runner(_report(packages)),
    )
    assert result["ok"] is False


@pytest.mark.parametrize("report", [{}, {"version": "1", "install": []}])
def test_successful_resolve_requires_valid_native_report(tmp_path, report):
    result = check_lock_resolves(
        _write_lock(tmp_path), cache_dir=tmp_path / "cache",
        runner=_report_runner(report),
    )
    assert result["ok"] is False


def test_successful_resolve_rejects_wrong_host_environment(tmp_path):
    report = _report([("pillow", "12.2.0")])
    report["environment"]["sys_platform"] = "not-the-current-host"
    result = check_lock_resolves(
        _write_lock(tmp_path), cache_dir=tmp_path / "cache",
        runner=_report_runner(report),
    )
    assert result["ok"] is False


def test_successful_resolve_without_report_is_not_a_pass(tmp_path):
    def no_report(*args, **kwargs):
        return subprocess.CompletedProcess(["pip"], 0, "Would install pillow-12.2.0", "")
    result = check_lock_resolves(
        _write_lock(tmp_path), cache_dir=tmp_path / "cache", runner=no_report,
    )
    assert result["ok"] is False


@pytest.mark.parametrize(
    "body,packages",
    [
        ("typing_extensions==4.16.0\n", [("typing-extensions", "4.16.0")]),
        ("cuda-toolkit==13.0.3.0\n", [("cuda-toolkit", "13.0.3")]),
        ("torch==2.13.0+cu126\n", [("torch", "2.13.0+cu126")]),
        ("pillow>=12.0\npillow==12.2.0\n", [("pillow", "12.2.0")]),
        ("pillow==12.2.0\nother==1; python_version < '1'\n", [("pillow", "12.2.0")]),
    ],
)
def test_report_accepts_canonical_exact_versions_and_satisfied_floors(tmp_path, body, packages):
    result = check_lock_resolves(
        _write_lock(tmp_path, body), cache_dir=tmp_path / "cache",
        runner=_report_runner(_report(packages)),
    )
    assert result["ok"] is True
