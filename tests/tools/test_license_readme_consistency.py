# SPDX-License-Identifier: Apache-2.0
"""README licence prose must agree with the licence files (2026-10-29).

The README used to make three categorical statements that LICENSE-CORE.md and
LICENSE-BUSL.txt do not support: that tools and tests are Apache 2.0, that
147/147 source files are tagged, and that every protected module converts to
Apache 2.0 on one date. LICENSE-CORE.md honours a per-file BUSL-1.1 SPDX header
through its default rule and gives Phase 10 and 11 files their own change date,
and LICENSE-BUSL.txt defines the Licensed Work as the listed files. This test
pins the corrected README prose against the licence files and the tracked
headers. It reads them; it never changes a header, a list entry or a date.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
LICENSE_CORE = REPO_ROOT / "LICENSE-CORE.md"
LICENSE_BUSL = REPO_ROOT / "LICENSE-BUSL.txt"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _license_section(readme: str) -> str:
    start = readme.index("\n## License\n")
    end = readme.find("\n## ", start + 1)
    return readme[start : end if end != -1 else len(readme)]


def _busl_headered_outside_the_list() -> list[str]:
    core = _read(LICENSE_CORE)
    tracked = subprocess.run(
        ["git", "ls-files", "--", "tests/*.py", "tools/*.py"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    found = []
    for rel in tracked:
        if rel in core:
            continue
        head = "\n".join(
            (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace").splitlines()[:6]
        )
        if "SPDX-License-Identifier: BUSL-1.1" in head:
            found.append(rel)
    return found


def test_readme_drops_the_false_categoricals() -> None:
    readme = _read(README)
    assert "Tools, tests, and UI compatibility code are Apache 2.0" not in readme
    assert "147/147" not in readme
    assert "become Apache 2.0 automatically on this date" not in readme
    assert "(infrastructure, adapters, tests, API, tools) | Apache 2.0" not in readme


def test_readme_points_to_the_default_rule_and_both_change_dates() -> None:
    section = _license_section(_read(README))
    core = _read(LICENSE_CORE)
    busl = _read(LICENSE_BUSL)
    assert "## Default rule" in core
    assert "Default rule" in section
    assert "BUSL-Change-Date" in section
    match = re.search(r"Change Date:\s*\n\s*(\d{4}-\d{2}-\d{2})", busl)
    assert match is not None, "LICENSE-BUSL.txt must state a Change Date"
    busl_date = match.group(1)
    assert busl_date in section
    assert "2030-12-31" in core
    assert "2030-12-31" in section
    assert busl_date != "2030-12-31", "two distinct dates must be presented"


def test_readme_flags_the_discrepancy_while_unlisted_headers_are_busl() -> None:
    """While any tracked tests/ or tools/ file outside the LICENSE-CORE.md list
    carries a BUSL-1.1 header, the README must not present those trees as Apache
    and must carry the open-discrepancy paragraph; once no such file remains,
    the paragraph must go. Headers are read, never changed."""
    section = _license_section(_read(README))
    if _busl_headered_outside_the_list():
        assert "Open discrepancy" in section
        assert "`tests/`" in section and "`tools/`" in section
        assert "promises no relicensing" in section
    else:
        assert "Open discrepancy" not in section
