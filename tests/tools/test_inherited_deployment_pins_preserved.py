"""Inherited deployment anchors reach every non-opt-in test, and its child processes, unchanged.

Fleet rule (confirmed 2026-10-26, restated in TOOLS-PR1961-INHERITED-PIN-REPLACEMENT): a test run must keep every
inherited lane pin; replacing WD_BRIDGE_BUNDLE_ROOT or WD_REBOOT_EXPECTED_MANIFEST_HASH for tests that did not opt in
makes the run's D15 evidence non-qualifying and changes what child tools read. These tests pin that boundary:

* the first three tests fail against any global fixture that rewrites the two anchors (RED-first for PR1961 fix 1);
* the remaining tests keep the distinct loader outcomes for absent, partial and mismatched anchors, and show that a
  test which owns a synthetic enrollment context scopes it through the loader's issued-context injection
  (``using_rco_enrollment_context``) without touching the process environment.

The launch snapshot is taken by a session-scoped fixture, which pytest instantiates before any function-scoped autouse
fixture of the same test, so it sees the environment as the test process inherited it.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest

from tools import bridge_rco_enrollment as enrollment

ANCHORS = ("WD_BRIDGE_BUNDLE_ROOT", "WD_REBOOT_EXPECTED_MANIFEST_HASH")
_SYNTHETIC_GENERATION = "f" * 40  # never a real commit


@pytest.fixture(scope="session")
def launch_anchors():
    """The two anchors as the test process inherited them (present or absent)."""
    return {name: os.environ.get(name) for name in ANCHORS}


def _child_view():
    code = "import json, os, sys; print(json.dumps({k: os.environ.get(k) for k in sys.argv[1:]}))"
    out = subprocess.run([sys.executable, "-I", "-c", code, *ANCHORS], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _synthetic_generation(tmp_path, *, with_enrollment_key=False):
    root = tmp_path / _SYNTHETIC_GENERATION
    root.mkdir()
    manifest = {"schema_version": 1, "source_commit": _SYNTHETIC_GENERATION, "files": {}}
    if with_enrollment_key:
        manifest["rco_enrollments"] = {}
    data = json.dumps(manifest).encode("utf-8")
    (root / "deployment-manifest.json").write_bytes(data)
    return root, hashlib.sha256(data).hexdigest()


# --- criterion 1: non-opt-in tests and their children see the inherited anchors unchanged ---------------------------

def test_non_opt_in_test_sees_inherited_anchors(launch_anchors):
    assert {name: os.environ.get(name) for name in ANCHORS} == launch_anchors


def test_child_process_inherits_launch_anchors(launch_anchors):
    assert _child_view() == launch_anchors


def test_owned_synthetic_context_is_scoped_not_environmental(tmp_path, launch_anchors):
    root, digest = _synthetic_generation(tmp_path)
    issued = enrollment.load_rco_enrollment_context(bundle_root=str(root), expected_manifest_sha256=digest)
    assert issued["status"] == "not_provisioned"
    with enrollment.using_rco_enrollment_context(issued):
        assert enrollment.load_rco_enrollment_context()["status"] == "not_provisioned"
        assert {name: os.environ.get(name) for name in ANCHORS} == launch_anchors
        assert _child_view() == launch_anchors
    assert {name: os.environ.get(name) for name in ANCHORS} == launch_anchors


# --- criterion 2: distinct loader outcomes for absent, partial, mismatched and owned inputs stay distinguishable ----

def test_absent_anchors_are_unknown_not_not_provisioned(monkeypatch):
    for name in ANCHORS:
        monkeypatch.delenv(name, raising=False)
    ctx = enrollment.load_rco_enrollment_context()
    assert ctx["status"] == "unknown"
    assert ctx["reason"] == "missing_or_invalid_external_pin_or_root"


@pytest.mark.parametrize("present", ANCHORS)
def test_partial_anchors_are_unknown(tmp_path, monkeypatch, present):
    root, digest = _synthetic_generation(tmp_path)
    values = {"WD_BRIDGE_BUNDLE_ROOT": str(root), "WD_REBOOT_EXPECTED_MANIFEST_HASH": digest}
    for name in ANCHORS:
        if name == present:
            monkeypatch.setenv(name, values[name])
        else:
            monkeypatch.delenv(name, raising=False)
    ctx = enrollment.load_rco_enrollment_context()
    assert ctx["status"] == "unknown"
    assert ctx["reason"] == "missing_or_invalid_external_pin_or_root"


def test_mismatched_anchor_hash_is_unknown(tmp_path, monkeypatch):
    root, digest = _synthetic_generation(tmp_path)
    wrong = ("0" if digest[0] != "0" else "1") + digest[1:]
    monkeypatch.setenv("WD_BRIDGE_BUNDLE_ROOT", str(root))
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", wrong)
    ctx = enrollment.load_rco_enrollment_context()
    assert ctx["status"] == "unknown"
    assert ctx["reason"] == "manifest_hash_mismatch"


def test_scope_exit_restores_default_resolution(tmp_path, monkeypatch):
    root, digest = _synthetic_generation(tmp_path, with_enrollment_key=True)
    issued = enrollment.load_rco_enrollment_context(bundle_root=str(root), expected_manifest_sha256=digest)
    assert issued["status"] == "not_provisioned"
    assert issued["reason"] == "verified_manifest_has_no_rco3_entry"
    for name in ANCHORS:
        monkeypatch.delenv(name, raising=False)
    with enrollment.using_rco_enrollment_context(issued):
        assert enrollment.load_rco_enrollment_context()["status"] == "not_provisioned"
    # after the scope ends the default resolution is back to the (now absent) environment anchors
    assert enrollment.load_rco_enrollment_context()["status"] == "unknown"


def test_dict_is_not_an_issued_context():
    with pytest.raises(ValueError):
        with enrollment.using_rco_enrollment_context({"status": "enrolled"}):
            pass  # pragma: no cover - the context manager must refuse before yielding
