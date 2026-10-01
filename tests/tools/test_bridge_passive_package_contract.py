"""F5/F6 package gate; missing precomposition entries deliberately FAIL.

Synthetic success twins are not an installed package/import smoke observation.
No providers, collector, scheduler, runtime reads or production pins are used.
"""
import ast
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import bridge_lock_participants as participants
from tools import bridge_v2_dashboard as view

ROOT = Path(__file__).resolve().parents[2]
MODULES = ("tools.bridge_lock_participants", "tools.bridge_v2_dashboard")
NOW = datetime(2026, 9, 30, 20, 45, tzinfo=timezone.utc)
STAMP = "2026-09-30T20:44:30Z"


def _definition():
    return json.loads((ROOT / "ops/windows/reboot/bridge-code-files.json").read_text(encoding="utf-8"))


def _contract(definition, module):
    path = module.replace(".", "/") + ".py"
    assert path in definition["python_files"], f"passive module not packaged: {path}"
    assert module in definition["import_smoke"]["package_modules"], f"passive module not smoke-listed: {module}"
    assert path not in definition["python_entrypoints"].values(), "passive library must not become an automatic entrypoint"


@pytest.mark.parametrize("module", MODULES)
def test_actual_package_lists_passive_library_and_smoke_only(module):
    _contract(_definition(), module)


@pytest.mark.parametrize("module", MODULES)
def test_package_contract_has_success_twin_and_missing_entry_refusals(module):
    definition = _definition()
    path = module.replace(".", "/") + ".py"
    definition["python_files"] = [path]
    definition["import_smoke"]["package_modules"] = [module]
    definition["python_entrypoints"] = {}
    _contract(definition, module)
    for field in ("python_files", "package_modules", "entrypoint"):
        broken = deepcopy(definition)
        if field == "python_files":
            broken[field] = []
        elif field == "package_modules":
            broken["import_smoke"][field] = []
        else:
            broken["python_entrypoints"]["passive"] = path
        with pytest.raises(AssertionError):
            _contract(broken, module)


def test_valid_caller_snapshots_are_passive_and_do_not_mutate():
    fact = {"value": 123, "source": "fixture", "observed_at_utc": STAMP, "max_age_seconds": 60}
    observations = {"schema": participants.INPUT_SCHEMA, "participants": {"fixture": {"pid": fact}}}
    snapshot = {"schema": view.SNAPSHOT_SCHEMA, "sources": {
        "claims": {"data": {"count": 1}, "observed_at_utc": STAMP, "max_age_seconds": 60}}}
    before = deepcopy((observations, snapshot))
    with patch("builtins.open", side_effect=AssertionError("live I/O forbidden")):
        participant_report = participants.participant_snapshot(observations, NOW)
        dashboard_report = view.dashboard(snapshot, NOW)
    assert participant_report["participants"]["fixture"]["pid"]["state"] == "observed"
    assert dashboard_report["sources"]["claims"]["state"] == "fresh"
    assert (observations, snapshot) == before
    assert participant_report["all_mutex_holders_verified"] is False
    assert dashboard_report["activation_assessed"] is False
    for report in (participant_report, dashboard_report):
        assert report["authority_effect"] == "none" and "ready" not in report


@pytest.mark.parametrize("evidence", [None, {"observed_at_utc": "2026-09-30T19:00:00Z"},
                                      {"observed_at_utc": "invalid"}])
def test_absent_stale_invalid_evidence_is_unknown(evidence):
    fact = None if evidence is None else {"value": 123, "source": "fixture", "max_age_seconds": 60, **evidence}
    source = None if evidence is None else {"data": {"count": 1}, "max_age_seconds": 60, **evidence}
    p = participants.participant_snapshot({"schema": participants.INPUT_SCHEMA,
        "participants": {"fixture": {"pid": fact}}}, NOW)
    d = view.dashboard({"schema": view.SNAPSHOT_SCHEMA, "sources": {"claims": source}}, NOW)
    assert p["participants"]["fixture"]["pid"]["state"] == "unknown"
    assert p["participants"]["fixture"]["pid"]["value"] is None
    assert d["sources"]["claims"]["state"] == "unknown"
    assert d["sources"]["claims"]["data"] is None


def test_auth_quota_observed_turn_never_borrow_cli_success():
    lane = {name: {"state": state, "observed_at_utc": STAMP, "max_age_seconds": 60}
            for name, state in {"cli": "installed", "auth": "valid", "quota": "available",
                                "observed_turn": "succeeded"}.items()}
    snapshot = {"schema": view.SNAPSHOT_SCHEMA, "lanes": {"fixture": lane}}
    assert view.dashboard(snapshot, NOW)["lanes"]["fixture"]["auth"]["state"] == "valid"
    del lane["auth"]
    lane["quota"]["observed_at_utc"] = "2026-09-30T19:00:00Z"
    lane["observed_turn"]["state"] = "callback"
    result = view.dashboard(snapshot, NOW)["lanes"]["fixture"]
    assert result["cli"]["state"] == "installed"
    assert all(result[name]["state"] == "unknown" for name in ("auth", "quota", "observed_turn"))
    assert "ready" not in result


@pytest.mark.parametrize("module", MODULES)
def test_passive_sources_have_only_pure_imports_and_no_cli_launchgate(module):
    source = ROOT / (module.replace(".", "/") + ".py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    allowed = {"__future__", "copy", "datetime", "json", "re"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] in allowed for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module.split(".")[0] in allowed
    assert not any(isinstance(node, ast.If) for node in tree.body), "no module-level launch gate"
    assert not any(isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == "main" for node in tree.body)
