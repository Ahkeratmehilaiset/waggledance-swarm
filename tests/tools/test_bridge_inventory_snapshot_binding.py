"""Pagination binds content, not only file identity and byte length."""

import json
from pathlib import Path

import pytest

from test_bridge_request_inventory_bounded import SHELLS, _fixture, _run


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_same_length_in_place_rewrite_invalidates_inventory_cursor(tmp_path, shell):
    runtime, script = _fixture(tmp_path)
    first = _run(shell, runtime, script)
    assert first.returncode == 0, first.stderr
    cursor = json.loads(first.stdout)["next_cursor"]
    log = runtime / "shared/events.jsonl"
    original = log.read_bytes()
    changed = original.replace(b"bounded inventory regression", b"changed inventory regression", 1)
    assert changed != original and len(changed) == len(original)
    with log.open("r+b") as stream:
        stream.write(changed)
    result = _run(shell, runtime, script, "-Cursor", cursor)
    assert result.returncode != 0
    assert "cursor does not match" in result.stderr
    assert not result.stdout.strip()
