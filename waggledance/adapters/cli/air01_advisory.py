# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""CLI for AIR-01 indoor air-quality advisories.

Closes the AIR-01 last mile: the solver, the Digheran adapter, and the sensor
HTTP transport already exist, but AIR-01 had no CLI (it was the only registered
v3_13_0 solver with a transport+adapter and no ``cli_module``). This reads either
an already-fetched Digheran-shaped sensor JSON file (``--input``) or an
operator-selected HTTP JSON sensor endpoint (``--url``), normalizes it through the
provider-neutral adapter, runs the solver, and prints the advisory JSON. It stores
no credentials and contains no provider-specific URLs. Mirrors eng01_recommend.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

from waggledance.core.v3_13_0.air01_air_quality_advisor import (
    DEFAULT_REQUIRED_METRICS,
    DEFAULT_THRESHOLDS,
    assess_air_quality,
)
from waggledance.core.v3_13_0.air01_digheran_adapter import (
    normalize_digheran_air_quality_payload,
    parse_digheran_air_quality_response,
)
from waggledance.core.v3_13_0.air01_sensor_http_transport import (
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    Air01SensorTransport,
    fetch_air_quality_sensor_response,
)


OUTPUT_ROOT = Path("data") / "air01"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run AIR-01 indoor air-quality advisory from JSON",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--input",
        help="Path to a local Digheran-shaped sensor JSON file",
    )
    source.add_argument(
        "--url",
        help="Operator-selected HTTP JSON sensor endpoint URL",
    )
    parser.add_argument(
        "--source-url",
        default=None,
        help="Provenance source_url for --input mode (required with --input)",
    )
    parser.add_argument(
        "--fetched-at-utc",
        default=None,
        help="Override fetched_at_utc; defaults to the observation timestamp",
    )
    parser.add_argument(
        "--headers-file",
        default=None,
        help="JSON object of HTTP headers for --url mode; credentials refused",
    )
    parser.add_argument(
        "--timeout-seconds",
        default=DEFAULT_TIMEOUT_SECONDS,
        type=float,
        help="HTTP timeout for --url mode",
    )
    parser.add_argument(
        "--max-response-bytes",
        default=DEFAULT_MAX_RESPONSE_BYTES,
        type=int,
        help="Maximum HTTP response size for --url mode",
    )
    parser.add_argument(
        "--allowed-private-hosts",
        default="",
        help="Comma-separated private hostnames explicitly allowed in --url mode",
    )
    parser.add_argument(
        "--allow-credential-headers",
        action="store_true",
        default=False,
        help="Allow credential-like headers in --url mode",
    )
    parser.add_argument(
        "--stale-threshold-minutes",
        default=30,
        type=int,
        help="Observation freshness threshold in minutes",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=False,
        help="Print indented JSON instead of compact JSON",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Also write JSON atomically to a relative data/air01 path",
    )
    return parser.parse_args(argv)


def run_from_payload(
    payload: Mapping[str, Any],
    *,
    source_url: str,
    fetched_at_utc: str | None = None,
    stale_threshold_minutes: int = 30,
) -> dict[str, Any]:
    observation = normalize_digheran_air_quality_payload(
        payload,
        source_url=source_url,
        fetched_at_utc=fetched_at_utc,
    )
    advisory = assess_air_quality(
        observation,
        required_metrics=DEFAULT_REQUIRED_METRICS,
        stale_threshold_minutes=stale_threshold_minutes,
        thresholds=DEFAULT_THRESHOLDS,
    )
    return advisory.to_payload()


def run_from_url(
    *,
    url: str,
    headers: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    allowed_private_hosts: Sequence[str] = (),
    allow_credential_headers: bool = False,
    fetched_at_utc: str | None = None,
    stale_threshold_minutes: int = 30,
    transport: Air01SensorTransport | None = None,
) -> dict[str, Any]:
    response = fetch_air_quality_sensor_response(
        url,
        headers=headers,
        timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
        allowed_private_hosts=allowed_private_hosts,
        allow_credential_headers=allow_credential_headers,
        transport=transport,
    )
    observation = parse_digheran_air_quality_response(
        response.body,
        content_type=response.content_type,
        source_url=response.source_url,
        fetched_at_utc=fetched_at_utc or _utc_now_timestamp(),
    )
    advisory = assess_air_quality(
        observation,
        required_metrics=DEFAULT_REQUIRED_METRICS,
        stale_threshold_minutes=stale_threshold_minutes,
        thresholds=DEFAULT_THRESHOLDS,
    )
    return advisory.to_payload()


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    transport: Air01SensorTransport | None = None,
) -> int:
    args = parse_args(argv)
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    try:
        if args.input is not None:
            _ensure_url_only_args_are_absent(args)
            if not args.source_url:
                raise ValueError("--input requires --source-url for provenance")
            payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("input JSON must be an object")
            result = run_from_payload(
                payload,
                source_url=args.source_url,
                fetched_at_utc=args.fetched_at_utc,
                stale_threshold_minutes=args.stale_threshold_minutes,
            )
        else:
            result = run_from_url(
                url=args.url,
                headers=_load_headers_file(args.headers_file),
                timeout_seconds=args.timeout_seconds,
                max_response_bytes=args.max_response_bytes,
                allowed_private_hosts=_parse_allowed_hosts(args.allowed_private_hosts),
                allow_credential_headers=args.allow_credential_headers,
                fetched_at_utc=args.fetched_at_utc,
                stale_threshold_minutes=args.stale_threshold_minutes,
                transport=transport,
            )
        output_text = json.dumps(
            result,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
        if args.output is not None:
            _write_output_file(args.output, output_text)
    except Exception as exc:
        print(
            json.dumps({
                "result_marker": "INVALID_INPUT_REFUSED",
                "error": str(exc),
            }, sort_keys=True),
            file=err,
        )
        return 2

    print(output_text, file=out)
    return 0


def _ensure_url_only_args_are_absent(args: argparse.Namespace) -> None:
    if args.headers_file is not None:
        raise ValueError("--headers-file requires --url")
    if args.allowed_private_hosts:
        raise ValueError("--allowed-private-hosts requires --url")
    if args.allow_credential_headers:
        raise ValueError("--allow-credential-headers requires --url")


def _load_headers_file(path: str | None) -> dict[str, str] | None:
    if path is None:
        return None
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("headers file must contain a JSON object")
    headers: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("headers file must contain string keys and values")
        headers[key] = value
    return headers


def _parse_allowed_hosts(raw: str) -> tuple[str, ...]:
    if not raw:
        return ()
    parts = tuple(part.strip() for part in raw.split(","))
    if any(not part for part in parts):
        raise ValueError("allowed-private-hosts entries must be non-empty")
    return parts


def _write_output_file(raw_path: str, output_text: str) -> None:
    output_path = _resolve_output_path(raw_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.{os.getpid()}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_name = temp_file.name
            temp_file.write(output_text + "\n")
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_name, output_path)
        temp_name = None
    finally:
        if temp_name is not None:
            temp_path = Path(temp_name)
            if temp_path.exists():
                temp_path.unlink()


def _resolve_output_path(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        raise ValueError("--output must be a relative path under data/air01")
    if any(part == ".." for part in candidate.parts):
        raise ValueError("--output must be a relative path under data/air01")
    unresolved = Path.cwd() / candidate
    base = (Path.cwd() / OUTPUT_ROOT).resolve()
    resolved = unresolved.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            "--output must be a relative path under data/air01"
        ) from exc
    if resolved == base or not resolved.name:
        raise ValueError("--output must include a file name")
    if unresolved.is_symlink():
        raise ValueError("--output must not be a symbolic link")
    if unresolved.exists() and not unresolved.is_file():
        raise ValueError("--output must target a regular file")
    return resolved


def _utc_now_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
