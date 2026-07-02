# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Refresh the AIR-01 latest-advisory snapshot that the read route serves.

`routes/air01_advisory.py` returns `data/air01/latest_advisory.json` but nothing
writes it from a live fetch. This closes the loop: one fetch -> solve -> write
cycle, callable by a scheduler / cron / scheduled task, so the read route serves
a LIVE indoor air-quality advisory instead of a static hand-written file. Mirrors
`eng01_advisory_refresher.py`.

It reuses the established AIR-01 CLI machinery so there is one source of truth:
`run_from_url` (fetch + Digheran normalization + solve, inheriting all transport
SSRF / credential guards) and the CLI's `_write_output_file` (atomic write
sandboxed under `data/air01`, refusing absolute paths, `..` traversal, symlinks,
and targets outside the snapshot root). It adds no solver, transport, or write
logic of its own and grants no new authority.

Unlike ENG-01 there is no separate advisory-card contract: the AIR-01 solver
payload (`Air01AirQualityAdvisory.to_payload`) is itself the route's snapshot
contract and already carries the `result_marker` the route validates.
"""
from __future__ import annotations

import json
from typing import Any

from waggledance.adapters.cli.air01_advisory import (
    _write_output_file,
    run_from_url,
)


# The exact relative path routes/air01_advisory.py reads (its DEFAULT_SNAPSHOT_PATH),
# resolved under data/air01 by the reused output-path guard. Held as a literal to
# avoid a feeds -> http-routes import; a test asserts the two stay in agreement.
LATEST_ADVISORY_SNAPSHOT_RELPATH = "data/air01/latest_advisory.json"


def refresh_air01_latest_advisory(
    *,
    url: str,
    snapshot_relpath: str = LATEST_ADVISORY_SNAPSHOT_RELPATH,
    transport: Any = None,
    **url_kwargs: Any,
) -> dict[str, Any]:
    """Run one AIR-01 fetch -> solve -> write cycle.

    Fetches the operator-selected sensor endpoint, normalizes it through the
    Digheran adapter, runs the solver, and atomically writes the advisory payload
    to the sandboxed snapshot path. Returns the advisory payload.
    `snapshot_relpath` must be a relative path under data/air01; the reused
    `_write_output_file` guard refuses anything else.
    """
    advisory = run_from_url(url=url, transport=transport, **url_kwargs)
    _write_output_file(snapshot_relpath, json.dumps(advisory, sort_keys=True))
    return advisory
