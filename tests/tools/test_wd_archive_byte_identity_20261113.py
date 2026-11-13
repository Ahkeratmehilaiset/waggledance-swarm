"""The deployment archive must contain committed bytes on Windows too."""
from __future__ import annotations

import io
from pathlib import Path
import re
import subprocess
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "ops/windows/reboot/Deploy-WdRebootBundle.ps1"
PATHS = (
    "tools/wd_bridge_mind.py",
    "waggledance/core/bridge_log_reader.py",
    "tools/wd_mind_content_prefix_readonly.py",
)


@pytest.mark.parametrize("eol", ["crlf", "lf"])
def test_deployment_archive_preserves_git_blob_bytes_with_windows_config(eol):
    source = DEPLOY.read_text(encoding="utf-8")
    archive = source.split("$archiveProbe = Invoke-GitCapture", 1)[1].split("'archive'", 1)[0]
    options = re.findall(r"'([^']+)'", archive)
    # Run the literal Git options from the deployment command against actual
    # repository blobs. The caller's CRLF setting must not alter any bytes.
    command = ["git", "-c", "core.autocrlf=true", "-c", f"core.eol={eol}",
               *options, "archive", "--format=zip", "HEAD", "--", *PATHS]
    archive_bytes = subprocess.check_output(command, cwd=ROOT, timeout=60)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive_file:
        for path in PATHS:
            blob = subprocess.check_output(["git", "cat-file", "blob", f"HEAD:{path}"],
                                           cwd=ROOT, timeout=60)
            assert archive_file.read(path) == blob, path
