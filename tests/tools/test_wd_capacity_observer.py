# SPDX-License-Identifier: BUSL-1.1
"""Exercise the actual pinned observer runner in both supported PowerShell hosts."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOSTS = [x for x in ('powershell', 'pwsh') if sys.platform == 'win32' and shutil.which(x)]
PYTHON = sys.executable
if sys.platform == 'win32' and 'WindowsApps' in PYTHON:
    candidate = Path(os.environ['LOCALAPPDATA']) / 'Programs/Python/Python313/python.exe'
    PYTHON = str(candidate) if candidate.is_file() else PYTHON


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


@pytest.mark.parametrize('host', HOSTS or [None])
@pytest.mark.parametrize('tamper', [None, 'manifest', 'source', 'executable'])
def test_runner_checks_pins_and_invokes_only_scheduled_metadata(tmp_path, host, tamper):
    if host is None:
        pytest.skip('PowerShell unavailable')
    runner = tmp_path / 'Invoke-WdCapacityObserver.ps1'
    shutil.copyfile(ROOT / 'ops/windows/reboot/Invoke-WdCapacityObserver.ps1', runner)
    tool = tmp_path / 'tools/bridge_capacity_collector.py'
    tool.parent.mkdir()
    tool.write_text("import sys\nassert '--scheduled' in sys.argv\n"
                    "assert '--provider' in sys.argv\nprint('metadata-only-fixture')\n")
    manifest = tmp_path / 'manifest.json'
    value = dict(schema='wd.capacity-observer-install.v1', execution_mode='metadata_only',
                 files={'tools/bridge_capacity_collector.py': sha(tool)},
                 python=PYTHON, python_sha256=sha(Path(PYTHON)),
                 codex=PYTHON, codex_sha256=sha(Path(PYTHON)),
                 store=str(tmp_path / 'observer.sqlite'))
    if tamper == 'executable':
        value['python_sha256'] = '0' * 64
    manifest.write_text(json.dumps(value), encoding='utf-8')
    anchor = sha(manifest)
    if tamper == 'source':
        tool.write_text("raise RuntimeError('must not run')\n")
    elif tamper == 'manifest':
        manifest.write_text('{}')
    proc = subprocess.run([host, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                           '-File', str(runner), '-ManifestPath', str(manifest),
                           '-ManifestSha256', anchor], capture_output=True, text=True, timeout=30)
    if tamper:
        assert proc.returncode != 0
        assert 'metadata-only-fixture' not in proc.stdout
    else:
        assert proc.returncode == 0, proc.stderr
        assert 'metadata-only-fixture' in proc.stdout
