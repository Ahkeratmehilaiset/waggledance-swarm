# SPDX-License-Identifier: Apache-2.0
"""Drift guard: all advisory surfaces share ONE snapshot loader.

The NaN/Infinity-500 bug wave (#1470 finding, #1468/#1472 twins) existed
because four copy-pasted loaders drifted. These tests pin the consolidation:
every consumer must use the same function object, so a fix or hardening in
the shared loader reaches all surfaces at once and a reintroduced local copy
fails CI.
"""
from __future__ import annotations

import json

import pytest

from waggledance.adapters.http.routes import (
    _advisory_snapshot as shared,
    advisory_dashboard,
    air01_advisory,
    eng01_advisory,
    eng06_advisory,
)

CONSUMERS = [eng01_advisory, air01_advisory, eng06_advisory, advisory_dashboard]


@pytest.mark.parametrize("module", CONSUMERS, ids=lambda m: m.__name__)
def test_consumer_uses_the_shared_loader_object(module) -> None:
    assert module._load_snapshot is shared.load_snapshot


@pytest.mark.parametrize("module", CONSUMERS, ids=lambda m: m.__name__)
def test_consumer_reexports_shared_markers(module) -> None:
    assert module.NO_ADVISORY_YET == shared.NO_ADVISORY_YET
    assert module.SNAPSHOT_REFUSED == shared.SNAPSHOT_REFUSED
    assert module.ADVISORY_MAX_BYTES == shared.ADVISORY_MAX_BYTES


def test_shared_loader_full_failure_matrix(tmp_path) -> None:
    load = shared.load_snapshot
    p = tmp_path / "latest_advisory.json"

    assert load(p)["reason"] == "missing"
    p.write_text("", encoding="utf-8")
    assert load(p)["reason"] == "empty"
    p.write_bytes(b"{" + b" " * shared.ADVISORY_MAX_BYTES + b"}")
    assert load(p)["reason"] == "size_exceeded"
    p.write_text("{not-json", encoding="utf-8")
    assert load(p)["reason"] == "parse_failed"
    p.write_text(json.dumps(["x"]), encoding="utf-8")
    assert load(p)["reason"] == "not_object"
    p.write_text(json.dumps({"x": 1}), encoding="utf-8")
    assert load(p)["reason"] == "missing_result_marker"
    p.write_text('{"result_marker": "OK", "v": NaN}', encoding="utf-8")
    assert load(p)["reason"] == "non_finite_number"
    p.write_text('{"result_marker": "OK", "v": 1e999}', encoding="utf-8")
    assert load(p)["reason"] == "non_finite_number"
    p.write_text('{"result_marker": "OK", "v": 1.5}', encoding="utf-8")
    assert load(p) == {"result_marker": "OK", "v": 1.5}


def test_dashboard_now_refuses_non_finite_snapshots(tmp_path, monkeypatch) -> None:
    # Consolidation behavior change (deliberate): the dashboard previously
    # rendered non-finite numbers as "inf"/"nan" text; with the shared loader
    # it shows the refused state like the JSON routes.
    monkeypatch.chdir(tmp_path)
    path = advisory_dashboard.SNAPSHOT_PATHS["ENG-06"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"result_marker": "OK", "metrics": {"fire_event_count_30d": Infinity}}',
        encoding="utf-8",
    )

    from fastapi import FastAPI
    from starlette.testclient import TestClient

    app = FastAPI()
    app.include_router(advisory_dashboard.router)
    text = TestClient(app).get("/api/dashboard/advisories").text

    assert "SNAPSHOT_REFUSED" in text
    assert "non_finite_number" in text
