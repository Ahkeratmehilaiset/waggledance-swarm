# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Refresh the ENG-01 latest-advisory snapshot that the read route serves.

`routes/eng01_advisory.py` returns `data/eng01/latest_advisory.json` but nothing
writes it from a live fetch — the route was wired to a hand-written file. This
closes the loop: one fetch -> solve -> write cycle, callable by a scheduler / cron
/ scheduled task, so the read route serves a LIVE advisory instead of a static
operator-edited file. It reuses the ENG-01 CLI fetch->solve path (run_from_url);
it adds no solver or transport logic and grants no new authority. The snapshot is
written atomically (temp file + os.replace) so a concurrent reader never sees a
half-written advisory.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from waggledance.adapters.cli.eng01_recommend import run_from_url


# The exact relative path routes/eng01_advisory.py reads (its DEFAULT_SNAPSHOT_PATH).
# Held as a literal to avoid a feeds -> http-routes import; a test asserts the two
# stay in agreement so the refresher always writes what the route reads.
LATEST_ADVISORY_SNAPSHOT_RELPATH = "data/eng01/latest_advisory.json"


def refresh_eng01_latest_advisory(
    *,
    url: str,
    snapshot_path: str | os.PathLike[str] = LATEST_ADVISORY_SNAPSHOT_RELPATH,
    transport: Any = None,
    **url_kwargs: Any,
) -> dict[str, Any]:
    """Run one ENG-01 fetch->solve->write cycle.

    Fetches the operator-selected price feed, runs the solver, and atomically
    writes the advisory to the snapshot the read route serves. Returns the
    advisory payload. All transport SSRF/credential guards apply via run_from_url.
    """
    result = run_from_url(url=url, transport=transport, **url_kwargs)
    _atomic_write_json(Path(snapshot_path), result)
    return result


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, sort_keys=True) + "\n"
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.{os.getpid()}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name is not None:
            tmp_path = Path(tmp_name)
            if tmp_path.exists():
                tmp_path.unlink()
