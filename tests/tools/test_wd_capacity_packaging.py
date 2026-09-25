# SPDX-License-Identifier: BUSL-1.1
"""Packaging regressions for the hash-pinned capacity observer release.

The point of these tests is the omission itself. Before this patch the observer
installer shipped a five-file array that did not contain
``tools/bridge_capacity_attribution.py``, even though the collector imports it
on the opt-in ``--attribution`` path. Because that import is lazy and
failure-isolated, nothing crashed: the feature simply reported itself
unavailable forever. ``test_omission_is_reproduced`` pins that old behaviour so
the regression cannot come back silently.

Style follows tests/tools/test_wd_capacity_observer.py: build a fake pinned
release on disk, write a manifest plus pointer, then run the real PowerShell
reader against it.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "ops" / "windows" / "reboot" / "Install-WdCapacityObserver.ps1"
STATUS = ROOT / "ops" / "windows" / "reboot" / "Get-WdCapacityStatus.ps1"
PYTHON = sys.executable
# The Windows Store alias cannot be opened for hashing; mirror the resolution
# already used by tests/tools/test_wd_capacity_observer.py.
if sys.platform == 'win32' and 'WindowsApps' in PYTHON:
    _candidate = Path(os.environ['LOCALAPPDATA']) / 'Programs/Python/Python313/python.exe'
    PYTHON = str(_candidate) if _candidate.is_file() else PYTHON


def _python_is_hashable() -> bool:
    try:
        Path(PYTHON).read_bytes()
        return True
    except OSError:
        return False
HOSTS = [x for x in ("powershell", "pwsh") if sys.platform == "win32" and shutil.which(x)]

#: Files the reader needs at runtime. The attribution module is the one this
#: patch adds; the qualification module ships for provenance only.
READER_FILES = (
    "tools/bridge_capacity_advisor.py",
    "tools/bridge_capacity_collector.py",
    "tools/bridge_capacity_attribution.py",
    "ops/windows/reboot/Get-WdCapacityStatus.ps1",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _build_release(tmp_path: Path, *, include_attribution: bool = True) -> tuple[Path, Path]:
    """Create a pinned release directory and its pointer, as the installer would."""
    root = tmp_path / "installed"
    release = root / ("a" * 40)
    wanted = [f for f in READER_FILES
              if include_attribution or "attribution" not in f]
    files: dict[str, str] = {}
    for relative in wanted:
        target = release / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
        files[relative.replace("/", "\\")] = sha(target)

    store = root / "observations.sqlite"
    sys.path.insert(0, str(ROOT))
    from tools.bridge_capacity_collector import save_observation  # noqa: E402

    save_observation(store, dict(provider="codex", observed_at="2026-09-25T00:00:00Z"))

    manifest = release / "manifest.json"
    manifest.write_text(json.dumps(dict(
        schema="wd.capacity-observer-install.v1", execution_mode="metadata_only",
        source_commit="a" * 40, files=files, python=PYTHON,
        python_sha256=sha(Path(PYTHON)), store=str(store))), encoding="utf-8")
    (root / "current.json").write_text(json.dumps(dict(
        mode="metadata_only", source_commit="a" * 40, manifest=str(manifest),
        manifest_sha256=sha(manifest))), encoding="utf-8")
    return root, release


def _run_status(host: str, release: Path, root: Path, *, attribution: bool):
    args = [host, "-NoProfile", "-NonInteractive", "-File",
            str(release / "ops/windows/reboot/Get-WdCapacityStatus.ps1"),
            "-InstallRoot", str(root)]
    if attribution:
        args.append("-Attribution")
    return subprocess.run(args, capture_output=True, text=True, timeout=60)


# --- the installer plan ----------------------------------------------------


@pytest.mark.parametrize("host", HOSTS or [None])
def test_installer_plan_ships_both_new_modules(tmp_path, host):
    """Without -Apply the installer prints its plan, so the file set is testable."""
    if host is None:
        pytest.skip("Windows PowerShell unavailable")
    if not _python_is_hashable():
        pytest.skip("interpreter path cannot be hashed on this machine")
    proc = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(INSTALLER),
         "-PythonExecutable", PYTHON, "-CodexExecutable", PYTHON,
         "-InstallRoot", str(tmp_path / "obs")],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    if proc.returncode != 0 and "Observer requires a clean committed source tree" in proc.stderr:
        pytest.skip("installer deliberately refuses this uncommitted development tree")
    assert proc.returncode == 0, proc.stderr
    plan = json.loads(proc.stdout)
    shipped = set(plan["files"].keys())
    assert "tools\\bridge_capacity_attribution.py" in shipped
    assert "tools\\bridge_model_qualification.py" in shipped
    # The plan must remain a plan: metadata only, nothing executed.
    assert plan["execution_mode"] == "metadata_only"


# --- the omission, pinned so it cannot return ------------------------------


@pytest.mark.parametrize("host", HOSTS or [None])
def test_omission_is_reproduced(tmp_path, host):
    """A release without the attribution module leaves the feature inert."""
    if host is None:
        pytest.skip("Windows PowerShell unavailable")
    if not _python_is_hashable():
        pytest.skip("interpreter path cannot be hashed on this machine")
    root, release = _build_release(tmp_path, include_attribution=False)
    result = _run_status(host, release, root, attribution=True)
    # The reader refuses: the module is a required pinned leaf after this patch.
    assert result.returncode == 2, result.stdout
    payload = json.loads(result.stdout)
    assert payload["execution_allowed"] is False


# --- the packaged path actually working ------------------------------------


@pytest.mark.parametrize("host", HOSTS or [None])
def test_packaged_cli_produces_attribution(tmp_path, host):
    if host is None:
        pytest.skip("Windows PowerShell unavailable")
    if not _python_is_hashable():
        pytest.skip("interpreter path cannot be hashed on this machine")
    root, release = _build_release(tmp_path)
    result = _run_status(host, release, root, attribution=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["execution_allowed"] is False
    attribution = payload["attribution"]
    assert attribution["state"] == "available"
    # The safety invariants must survive packaging.
    assert attribution["dispatch_enabled"] is False
    assert attribution["cost_denominator_available"] is False


@pytest.mark.parametrize("host", HOSTS or [None])
def test_default_output_is_unchanged_without_the_switch(tmp_path, host):
    """Opt-in means opt-in: no attribution key unless asked."""
    if host is None:
        pytest.skip("Windows PowerShell unavailable")
    if not _python_is_hashable():
        pytest.skip("interpreter path cannot be hashed on this machine")
    root, release = _build_release(tmp_path)
    result = _run_status(host, release, root, attribution=False)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "attribution" not in payload
    assert payload["execution_allowed"] is False


# --- tamper rejection ------------------------------------------------------


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("victim", ["tools/bridge_capacity_attribution.py",
                                    "tools/bridge_capacity_collector.py"])
def test_tampered_module_is_denied(tmp_path, host, victim):
    """A shipped module that no longer matches its pinned hash must not run."""
    if host is None:
        pytest.skip("Windows PowerShell unavailable")
    if not _python_is_hashable():
        pytest.skip("interpreter path cannot be hashed on this machine")
    root, release = _build_release(tmp_path)
    (release / victim).write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
    result = _run_status(host, release, root, attribution=True)
    assert result.returncode == 2, result.stdout
    payload = json.loads(result.stdout)
    assert payload["execution_allowed"] is False
    # And the tampered code must not have been executed.
    assert "must not execute" not in (result.stdout + result.stderr)


@pytest.mark.parametrize("host", HOSTS or [None])
def test_status_run_writes_nothing(tmp_path, host):
    if host is None:
        pytest.skip("Windows PowerShell unavailable")
    if not _python_is_hashable():
        pytest.skip("interpreter path cannot be hashed on this machine")
    root, release = _build_release(tmp_path)
    before = {str(p.relative_to(tmp_path)): p.read_bytes()
              for p in tmp_path.rglob("*") if p.is_file()}
    result = _run_status(host, release, root, attribution=True)
    assert result.returncode == 0, result.stderr
    after = {str(p.relative_to(tmp_path)): p.read_bytes()
             for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after, "the read-only status path changed files or their contents"


@pytest.mark.parametrize("host", HOSTS or [None])
def test_legacy_manifest_works_without_attribution(tmp_path, host):
    if host is None or not _python_is_hashable():
        pytest.skip("hashable Windows interpreter required")
    root, release = _build_release(tmp_path, include_attribution=False)
    result = _run_status(host, release, root, attribution=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "attribution" not in json.loads(result.stdout)


@pytest.mark.parametrize("host", HOSTS or [None])
def test_summary_retains_requested_attribution(tmp_path, host):
    if host is None or not _python_is_hashable():
        pytest.skip("hashable Windows interpreter required")
    root, release = _build_release(tmp_path)
    result = subprocess.run([host, "-NoProfile", "-NonInteractive", "-File",
                             str(release / "ops/windows/reboot/Get-WdCapacityStatus.ps1"),
                             "-InstallRoot", str(root), "-Summary", "-Json", "-Attribution"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["attribution"]["state"] == "available"


# --- static coverage of the packaging change itself ------------------------
# The installer plan test above needs a clean git checkout and therefore skips
# in an audit staging tree. These assertions cover the same change directly and
# always run, so the patch is never merged on an untested claim.


def test_installer_source_lists_both_new_modules():
    text = INSTALLER.read_text(encoding="utf-8")
    assert r"'tools\bridge_capacity_attribution.py'" in text
    assert r"'tools\bridge_model_qualification.py'" in text


def test_status_reader_requires_attribution_pin_and_opt_in_switch():
    text = STATUS.read_text(encoding="utf-8")
    # required pinned leaf, so a missing module is refused rather than ignored
    assert r"if($Attribution){$requiredLeaves+='tools\bridge_capacity_attribution.py'}" in text
    # opt-in switch, forwarded only when asked
    assert "[switch]$Attribution," in text
    assert "if($Attribution){$statusArgs+='--attribution'}" in text


def test_status_reader_does_not_force_attribution_by_default():
    text = STATUS.read_text(encoding="utf-8")
    assert "--attribution" not in text.split("$statusArgs=@(")[0], \
        "the default status argument list must not contain --attribution"
