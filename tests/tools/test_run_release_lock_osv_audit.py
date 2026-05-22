# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json

from tools.run_release_lock_osv_audit import build_report, locked_pins


def test_locked_pins_parse_exact_versions_and_local_query_versions(tmp_path) -> None:
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text(
        "\n".join([
            "--extra-index-url https://download.pytorch.org/whl/cu126",
            "torch==2.11.0+cu126 ; sys_platform == \"win32\"",
            "torch==2.11.0 ; sys_platform != \"win32\"",
            "requests>=2.33.0",
            "pillow==12.2.0",
        ]),
        encoding="utf-8",
    )

    pins = locked_pins(lock)

    assert [(pin.name, pin.version, pin.query_version) for pin in pins] == [
        ("torch", "2.11.0+cu126", "2.11.0"),
        ("torch", "2.11.0", "2.11.0"),
        ("pillow", "12.2.0", "12.2.0"),
    ]
    assert pins[0].marker == 'sys_platform == "win32"'


def test_build_report_uses_querybatch_and_preserves_versions(tmp_path) -> None:
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text(
        "\n".join([
            "torch==2.11.0+cu126 ; sys_platform == \"win32\"",
            "pillow==12.2.0",
        ]),
        encoding="utf-8",
    )
    calls: list[list[dict[str, object]]] = []

    def fake_querybatch(url, queries, *, timeout):
        assert url == "https://example.invalid/querybatch"
        assert timeout == 7
        calls.append(queries)
        return {
            "results": [
                {},
                {"vulns": [{"id": "OSV-TEST", "aliases": ["CVE-TEST"]}]},
            ]
        }

    report = build_report(
        lock,
        osv_url="https://example.invalid/querybatch",
        batch_size=10,
        timeout=7,
        post_querybatch=fake_querybatch,
    )

    assert calls == [[
        {"package": {"ecosystem": "PyPI", "name": "torch"}, "version": "2.11.0"},
        {"package": {"ecosystem": "PyPI", "name": "pillow"}, "version": "12.2.0"},
    ]]
    assert report["dependencies"][0]["version"] == "2.11.0+cu126"
    assert report["dependencies"][0]["osv_query_version"] == "2.11.0"
    assert report["dependencies"][1]["vulns"] == [
        {"id": "OSV-TEST", "aliases": ["CVE-TEST"]}
    ]


def test_report_shape_is_collect_soak_compatible(tmp_path) -> None:
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("pillow==12.2.0\n", encoding="utf-8")

    def fake_querybatch(url, queries, *, timeout):
        return {"results": [{"vulns": []}]}

    report = build_report(lock, post_querybatch=fake_querybatch)
    encoded = json.loads(json.dumps(report))

    assert encoded["dependencies"] == [
        {"name": "pillow", "version": "12.2.0", "vulns": []}
    ]
