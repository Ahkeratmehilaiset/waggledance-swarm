# SPDX-License-Identifier: BUSL-1.1
"""ConvertTo-BridgeNativeLongPath is defined once in each of three bridge scripts, and the copies are byte-identical.

The writer (Write-AgentEvent.ps1), the accepted-queue drain (Drain-AcceptedBridgeQueue.ps1) and the targeted replay
(Restore-BridgeSpool.ps1) each carry their own copy, because each runs as a standalone script with its own P/Invoke
types. A fix applied to one copy only would let the scripts disagree about which kernel32 names gain the long-path
prefix, so drift is a failure here. The comparison covers the whole function, from its definition line to its
closing brace, byte for byte as checked out.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAME = "ConvertTo-BridgeNativeLongPath"
SCRIPTS = (
    ".agent-bridge/bin/Write-AgentEvent.ps1",
    ".agent-bridge/bin/Drain-AcceptedBridgeQueue.ps1",
    ".agent-bridge/bin/Restore-BridgeSpool.ps1",
)
# PowerShell names are case-insensitive, and a scope modifier still defines the function.
DEFINITION = re.compile(r"(?im)^[ \t]*function[ \t]+(?:[a-z]+:)?" + re.escape(NAME) + r"\b")
# The function runs from its column-0 definition line to the first column-0 closing brace; nested blocks are indented.
FUNCTION_BYTES = re.compile(rb"(?ms)^function " + re.escape(NAME.encode("ascii")) + rb" \{\r?\n.*?^\}\r?\n")


def _tracked_powershell_files() -> list[str]:
    listed = subprocess.run(
        ["git", "-c", "core.quotepath=off", "ls-files", "-z", "--", "*.ps1", "*.psm1"],
        cwd=ROOT, check=True, capture_output=True,
    )
    return sorted(path for path in listed.stdout.decode("utf-8").split("\0") if path)


def _function_bytes(relative: str) -> bytes:
    matches = FUNCTION_BYTES.findall((ROOT / relative).read_bytes())
    assert len(matches) == 1, (relative, len(matches))
    return matches[0]


def test_exactly_the_three_bridge_scripts_define_the_conversion() -> None:
    tracked = _tracked_powershell_files()
    assert set(SCRIPTS) <= set(tracked)
    defining = [path for path in tracked
                if DEFINITION.search((ROOT / path).read_text(encoding="utf-8-sig", errors="replace"))]

    assert defining == sorted(SCRIPTS)


@pytest.mark.parametrize("relative", SCRIPTS)
def test_each_script_defines_it_once_and_otherwise_only_calls_it(relative: str) -> None:
    # A second definition, an alias or any other mention could make the script run something else.
    text = (ROOT / relative).read_text(encoding="utf-8-sig")
    definitions, calls, other = 0, 0, []
    for match in re.finditer(re.escape(NAME), text, flags=re.IGNORECASE):
        line_start = text.rfind("\n", 0, match.start()) + 1
        line = text[line_start:text.find("\n", match.end())].rstrip("\r")
        if line == "function " + NAME + " {" and match.start() == line_start + len("function "):
            definitions += 1
        elif text[match.start() - 1:match.end() + 1] == "(" + NAME + " ":
            calls += 1
        else:
            other.append(line.strip())

    assert (definitions, other) == (1, [])
    assert calls > 0


def test_the_three_copies_are_byte_identical() -> None:
    copies = {relative: _function_bytes(relative) for relative in SCRIPTS}
    reference = copies[SCRIPTS[0]]

    # The extraction reached the real end of the function, not an earlier brace.
    assert re.search(rb"\n    '\\\\\?\\' \+ \$Path\r?\n\}\r?\n\Z", reference), reference[-80:]
    assert {relative: copy == reference for relative, copy in copies.items()} == dict.fromkeys(SCRIPTS, True)
