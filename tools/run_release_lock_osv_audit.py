#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Audit exact lock pins directly against OSV QueryBatch."""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name


SCHEMA_VERSION = "waggledance.release_lock_osv_audit.v1"
OSV_QUERYBATCH_URL = "https://api.osv.dev/v1/querybatch"


@dataclass(frozen=True)
class LockedPin:
    name: str
    version: str
    query_version: str
    marker: str | None


def _query_version(version: str) -> str:
    return version.split("+", 1)[0]


def locked_pins(path: Path) -> list[LockedPin]:
    pins: list[LockedPin] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            continue
        exact_versions = [
            spec.version
            for spec in requirement.specifier
            if spec.operator == "=="
        ]
        if len(exact_versions) != 1:
            continue
        version = exact_versions[0]
        pins.append(
            LockedPin(
                name=canonicalize_name(requirement.name),
                version=version,
                query_version=_query_version(version),
                marker=str(requirement.marker) if requirement.marker else None,
            )
        )
    return pins


def _post_querybatch(
    url: str,
    queries: list[dict[str, Any]],
    *,
    timeout: float,
) -> dict[str, Any]:
    body = json.dumps({"queries": queries}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"OSV querybatch failed: {exc}") from exc


def _vuln_summary(vuln: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": vuln.get("id", ""),
        "aliases": sorted(vuln.get("aliases", [])),
    }


def build_report(
    requirement_path: Path,
    *,
    osv_url: str = OSV_QUERYBATCH_URL,
    batch_size: int = 100,
    timeout: float = 30.0,
    post_querybatch: Callable[..., dict[str, Any]] = _post_querybatch,
) -> dict[str, Any]:
    pins = locked_pins(requirement_path)
    dependencies: list[dict[str, Any]] = []
    for start in range(0, len(pins), batch_size):
        batch = pins[start : start + batch_size]
        queries = [
            {
                "package": {"ecosystem": "PyPI", "name": pin.name},
                "version": pin.query_version,
            }
            for pin in batch
        ]
        payload = post_querybatch(osv_url, queries, timeout=timeout)
        results = payload.get("results")
        if not isinstance(results, list) or len(results) != len(batch):
            raise RuntimeError("OSV querybatch returned malformed result count")
        for pin, result in zip(batch, results):
            vulns = result.get("vulns", []) if isinstance(result, dict) else []
            if not isinstance(vulns, list):
                raise RuntimeError(f"OSV result for {pin.name} has invalid vulns")
            entry: dict[str, Any] = {
                "name": pin.name,
                "version": pin.version,
                "vulns": sorted(
                    (_vuln_summary(vuln) for vuln in vulns if isinstance(vuln, dict)),
                    key=lambda item: item["id"],
                ),
            }
            if pin.marker:
                entry["marker"] = pin.marker
            if pin.query_version != pin.version:
                entry["osv_query_version"] = pin.query_version
            dependencies.append(entry)
    return {
        "schema_version": SCHEMA_VERSION,
        "audit_source": "osv_querybatch",
        "requirement_file": str(requirement_path),
        "dependencies": dependencies,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirement", default="requirements.lock.txt", type=Path)
    parser.add_argument(
        "--output",
        default=(
            "docs/runs/release_soak_evidence/"
            "v3.12.0_pip_audit_report_lock_after_prune_osv.json"
        ),
        type=Path,
    )
    parser.add_argument("--osv-url", default=OSV_QUERYBATCH_URL)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    try:
        report = build_report(
            args.requirement,
            osv_url=args.osv_url,
            batch_size=args.batch_size,
            timeout=args.timeout,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, RuntimeError) as exc:
        print(f"run_release_lock_osv_audit: {exc}")
        return 2
    vulns = sum(len(dep["vulns"]) for dep in report["dependencies"])
    print(
        json.dumps(
            {
                "dependencies": len(report["dependencies"]),
                "vulnerabilities": vulns,
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 1 if vulns else 0


if __name__ == "__main__":
    raise SystemExit(main())
