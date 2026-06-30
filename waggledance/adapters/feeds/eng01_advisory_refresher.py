# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Refresh the ENG-01 latest-advisory snapshot that the read route serves.

`routes/eng01_advisory.py` returns `data/eng01/latest_advisory.json` but nothing
writes it from a live fetch — the route was wired to a hand-written file. This
closes the loop: one fetch -> solve -> render -> write cycle, callable by a
scheduler / cron / scheduled task, so the read route serves a LIVE advisory
instead of a static operator-edited file.

It reuses the established ENG-01 CLI machinery so there is one source of truth:
`run_from_url` (fetch + solve, inheriting all transport SSRF / credential guards),
`render_eng01_advisory_card` (the `eng01_advisory_card.v1` contract the route and
CLI snapshots use — NOT the raw solver payload), and the CLI's `_write_output_file`
(atomic write sandboxed under `data/eng01`, refusing absolute paths, `..`
traversal, symlinks, and targets outside the snapshot root). It adds no solver,
transport, or write logic of its own and grants no new authority.
"""
from __future__ import annotations

import json
from typing import Any

from waggledance.adapters.cli.eng01_recommend import (
    _write_output_file,
    run_from_url,
)
from waggledance.core.v3_13_0.eng01_advisory_card import (
    render_eng01_advisory_card,
)


# The exact relative path routes/eng01_advisory.py reads (its DEFAULT_SNAPSHOT_PATH),
# resolved under data/eng01 by the reused output-path guard. Held as a literal to
# avoid a feeds -> http-routes import; a test asserts the two stay in agreement.
LATEST_ADVISORY_SNAPSHOT_RELPATH = "data/eng01/latest_advisory.json"


def refresh_eng01_latest_advisory(
    *,
    url: str,
    snapshot_relpath: str = LATEST_ADVISORY_SNAPSHOT_RELPATH,
    transport: Any = None,
    **url_kwargs: Any,
) -> dict[str, Any]:
    """Run one ENG-01 fetch -> solve -> render -> write cycle.

    Fetches the operator-selected price feed, runs the solver, renders the
    operator advisory card (the eng01_advisory_card.v1 contract the route serves),
    and atomically writes it to the sandboxed snapshot path. Returns the card.
    `snapshot_relpath` must be a relative path under data/eng01; the reused
    `_write_output_file` guard refuses anything else.
    """
    result = run_from_url(url=url, transport=transport, **url_kwargs)
    card = render_eng01_advisory_card(result)
    _write_output_file(snapshot_relpath, json.dumps(card, sort_keys=True))
    return card
