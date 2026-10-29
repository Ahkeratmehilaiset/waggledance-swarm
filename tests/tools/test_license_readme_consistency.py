# SPDX-License-Identifier: Apache-2.0
"""README licence prose must agree with the licence files (2026-10-29).

The README used to make three categorical statements that LICENSE-CORE.md and
LICENSE-BUSL.txt do not support: that tools and tests are Apache 2.0, that
147/147 source files are tagged, and that every protected module converts to
Apache 2.0 on one date. LICENSE-CORE.md honours a per-file BUSL-1.1 SPDX header
through its default rule and gives Phase 10 and 11 files their own change date,
and LICENSE-BUSL.txt defines the Licensed Work as the listed files and converts
on the Change Date or on the fourth anniversary of a version's first public
distribution, whichever comes first. This test pins the corrected README prose
against the licence files and the tracked headers. It reads them; it never
changes a header, a list entry or a date.
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
    """Every tracked .py file that carries a BUSL-1.1 SPDX header without being
    listed in LICENSE-CORE.md, whatever tree it lives in."""
    core = _read(LICENSE_CORE)
    tracked = subprocess.run(
        ["git", "ls-files", "--", "*.py"],
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


def test_readme_states_the_earlier_anniversary_trigger() -> None:
    """LICENSE-BUSL.txt converts on the Change Date or on the fourth anniversary
    of the first public distribution of a version, whichever comes first. The
    README must name that earlier alternative, not only the dates."""
    busl = _read(LICENSE_BUSL)
    section = _license_section(_read(README))
    assert "fourth anniversary" in busl and "whichever comes first" in busl
    assert "fourth anniversary" in section
    assert "whichever comes first" in section


def test_readme_explains_files_with_no_spdx_header() -> None:
    """A reader must learn what applies to a file that carries no SPDX header:
    the LICENSE-CORE.md default rule (Apache 2.0 unless the file is listed)."""
    section = _license_section(_read(README))
    assert "no SPDX header" in section


def test_readme_flags_the_discrepancy_while_unlisted_headers_are_busl() -> None:
    """While any tracked .py file outside the LICENSE-CORE.md list carries a
    BUSL-1.1 header, the README must carry the open-discrepancy paragraph, and
    it must name tests/ and tools/ while files in those trees are among them;
    once no unlisted BUSL header remains anywhere, the paragraph must go.
    Headers are read, never changed."""
    section = _license_section(_read(README))
    unlisted = _busl_headered_outside_the_list()
    if unlisted:
        assert "Open discrepancy" in section
        assert "promises no relicensing" in section
        if any(rel.startswith(("tests/", "tools/")) for rel in unlisted):
            assert "`tests/`" in section and "`tools/`" in section
    else:
        assert "Open discrepancy" not in section
