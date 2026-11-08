"""Deployment Git pin contract; a digest is evidence, not an operator signature."""
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import stat

import pytest


FLEET = Path(__file__).resolve().parents[2] / "ops/windows/reboot/wd-fleet.json"


def test_fleet_git_pin_is_canonical_and_names_an_absolute_executable():
    fleet = json.loads(FLEET.read_text(encoding="utf-8"))
    assert ntpath.isabs(fleet["git_executable"])
    assert re.fullmatch(r"[0-9a-f]{64}", fleet["git_sha256"])
    assert fleet["git_sha256"] != "0" * 64


@pytest.mark.skipif(os.name != "nt", reason="installed Windows fleet conformance")
def test_fleet_git_pin_matches_installed_bytes_without_reparse_ancestors():
    fleet = json.loads(FLEET.read_text(encoding="utf-8"))
    executable = Path(fleet["git_executable"])
    for component in (executable, *executable.parents):
        assert not component.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    assert executable.is_file()
    assert hashlib.sha256(executable.read_bytes()).hexdigest() == fleet["git_sha256"]
