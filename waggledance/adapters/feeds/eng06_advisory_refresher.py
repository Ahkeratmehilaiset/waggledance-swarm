# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Refresh the ENG-06 latest-advisory snapshot that the read route serves.

`routes/eng06_advisory.py` returns `data/eng06/latest_advisory.json` but nothing
writes it from a live burn log. This closes the loop: one read -> solve ->
render -> write cycle, callable by a scheduler / cron / scheduled task, so the
read route serves a LIVE fireplace advisory card instead of a static
hand-written file. Mirrors `eng01_advisory_refresher.py` /
`air01_advisory_refresher.py` with one difference: ENG-06's input is a LOCAL
burn-log JSON file (there is no network transport in the ENG-06 vertical), so
the refresher takes a local path with the same caller-trusted semantics as the
CLI's ``--input``.

It reuses the established ENG-06 machinery so there is one source of truth:
`run_from_payload` (burn-log normalization + the pure fail-closed solver +
horizon resolution, exactly as the CLI runs it) and
`render_eng06_advisory_card` (the `eng06_advisory_card.v1` contract the route
serves — NOT the raw solver payload). The snapshot write is atomic and
sandboxed under `data/eng06`, mirroring the output-path guard the ENG-01 and
AIR-01 CLIs use for their own snapshot roots (each guard is bound to its
vertical's root, which is why this module carries its own `data/eng06`-rooted
copy). It adds no solver or normalization logic of its own and grants no new
authority.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from waggledance.adapters.cli.eng06_fireplace import run_from_payload
from waggledance.core.v3_13_0.eng06_advisory_card import (
    render_eng06_advisory_card,
)


OUTPUT_ROOT = Path("data") / "eng06"

# The exact relative path routes/eng06_advisory.py reads (its DEFAULT_SNAPSHOT_PATH),
# resolved under data/eng06 by the output-path guard. Held as a literal to
# avoid a feeds -> http-routes import; a test asserts the two stay in agreement.
LATEST_ADVISORY_SNAPSHOT_RELPATH = "data/eng06/latest_advisory.json"


def refresh_eng06_latest_advisory(
    *,
    burn_log_path: str | Path,
    snapshot_relpath: str = LATEST_ADVISORY_SNAPSHOT_RELPATH,
    **payload_kwargs: Any,
) -> dict[str, Any]:
    """Run one ENG-06 read -> solve -> render -> write cycle.

    Reads the operator-selected local burn-log JSON file, runs the solver,
    renders the operator advisory card (the eng06_advisory_card.v1 contract the
    route serves), and atomically writes it to the sandboxed snapshot path.
    Returns the card. `snapshot_relpath` must be a relative path under
    data/eng06; the output-path guard refuses anything else.
    """
    payload = json.loads(Path(burn_log_path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("burn log JSON must be an object")
    result = run_from_payload(payload, **payload_kwargs)
    card = render_eng06_advisory_card(result)
    _write_output_file(snapshot_relpath, json.dumps(card, sort_keys=True))
    return card


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
        raise ValueError("snapshot path must be a relative path under data/eng06")
    if any(part == ".." for part in candidate.parts):
        raise ValueError("snapshot path must be a relative path under data/eng06")
    unresolved = Path.cwd() / candidate
    base = (Path.cwd() / OUTPUT_ROOT).resolve()
    resolved = unresolved.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(
            "snapshot path must be a relative path under data/eng06"
        ) from exc
    if resolved == base or not resolved.name:
        raise ValueError("snapshot path must include a file name")
    if unresolved.is_symlink():
        raise ValueError("snapshot path must not be a symbolic link")
    if unresolved.exists() and not unresolved.is_file():
        raise ValueError("snapshot path must target a regular file")
    return resolved
