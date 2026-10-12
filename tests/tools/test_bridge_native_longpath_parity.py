# SPDX-License-Identifier: BUSL-1.1
"""ConvertTo-BridgeNativeLongPath is defined once in each of three bridge scripts, and the copies are byte-identical.

The writer (Write-AgentEvent.ps1), the accepted-queue drain (Drain-AcceptedBridgeQueue.ps1) and the targeted replay
(Restore-BridgeSpool.ps1) each carry their own copy, because each runs as a standalone script with its own P/Invoke
types. A fix applied to one copy only would let the scripts disagree about which kernel32 names gain the long-path
prefix, so drift is a failure here. The comparison covers the whole function, from its definition line to its
closing brace, byte for byte as checked out. Each script must also define it exactly once at top level as the
PowerShell parser sees it, and must not shadow it through an alias or the function: drive.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAME = "ConvertTo-BridgeNativeLongPath"
SCRIPTS = (
    ".agent-bridge/bin/Write-AgentEvent.ps1",
    ".agent-bridge/bin/Drain-AcceptedBridgeQueue.ps1",
    ".agent-bridge/bin/Restore-BridgeSpool.ps1",
)
# PowerShell names are case-insensitive, a scope modifier still defines the function, and filter, workflow (PS 5)
# and configuration (DSC) define a command of that name just like function does (RCO1 R1 on PR1851).
DEFINITION = re.compile(
    r"(?im)^[ \t]*(?:function|filter|workflow|configuration)[ \t]+(?:[a-z]+:)?" + re.escape(NAME) + r"\b"
)
# The function runs from its column-0 definition line to the first column-0 closing brace; nested blocks are indented.
# The next line must be blank and the one after it another column-0 definition, so the brace cannot sit inside a
# block comment or here-string that the live body continues after (Grok a23616aa B1).
FUNCTION_BYTES = re.compile(rb"(?ms)^function " + re.escape(NAME.encode("ascii")) + rb" \{\r?\n.*?^\}\r?\n(?=\r?\nfunction )")
# Ways to make a name run something other than its definition without writing "function NAME": alias commands
# and the function:/alias: drives (Grok a23616aa B1: a computed-name Set-Alias or Set-Item function:). The three
# scripts use none of them. Deliberately obfuscated dynamic code is left to review; this guards against drift.
SHADOWING = re.compile(
    r"(?i)(?<![\w-])(?:set-alias|new-alias|import-alias|sal|nal|invoke-expression|iex)(?![\w-])"
    r"|(?<![\w-])(?:function|alias):"
)
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


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


@pytest.mark.parametrize("relative", SCRIPTS)
def test_no_script_shadows_a_function_through_an_alias_or_the_function_drive(relative: str) -> None:
    text = (ROOT / relative).read_text(encoding="utf-8-sig")

    assert [match.group(0) for match in SHADOWING.finditer(text)] == []


@pytest.mark.skipif(POWERSHELL is None, reason="the parser check needs pwsh or Windows PowerShell")
def test_the_powershell_parser_sees_one_top_level_definition_with_the_shared_text() -> None:
    # The parser, not a text pattern, decides what is code: a copy inside a comment or here-string is no definition.
    paths = [str(ROOT / relative) for relative in SCRIPTS]
    encoded = base64.b64encode(json.dumps(paths).encode("utf-8")).decode("ascii")
    command = (
        "$ErrorActionPreference='Stop'\n"
        "$paths = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded + "')) | ConvertFrom-Json\n"
        "$out = @(foreach ($path in $paths) {\n"
        "  $tokens = $null; $errors = $null\n"
        "  $ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)\n"
        "  $found = @($ast.FindAll({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and\n"
        "    $n.Name -ieq '" + NAME + "' }, $true))\n"
        "  [ordered]@{ errors = $errors.Count; count = $found.Count;\n"
        "    top_level = @($found | Where-Object { $_.Parent.Parent -eq $ast }).Count;\n"
        "    text = @($found | ForEach-Object { [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($_.Extent.Text)) }) }\n"
        "})\n"
        "[Console]::Out.Write((ConvertTo-Json -InputObject $out -Compress -Depth 4))\n"
    )
    completed = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand",
         base64.b64encode(command.encode("utf-16-le")).decode("ascii")],
        check=False, capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    observed = json.loads(completed.stdout)
    shared = _function_bytes(SCRIPTS[0]).decode("utf-8").replace("\r\n", "\n").rstrip("\n")

    assert [(row["errors"], row["count"], row["top_level"]) for row in observed] == [(0, 1, 1)] * len(SCRIPTS)
    assert [base64.b64decode(row["text"][0]).decode("utf-8").replace("\r\n", "\n") for row in observed] == \
        [shared] * len(SCRIPTS)


def test_the_three_copies_are_byte_identical() -> None:
    copies = {relative: _function_bytes(relative) for relative in SCRIPTS}
    reference = copies[SCRIPTS[0]]

    # The extraction reached the real end of the function, not an earlier brace.
    assert re.search(rb"\n    '\\\\\?\\' \+ \$Path\r?\n\}\r?\n\Z", reference), reference[-80:]
    assert {relative: copy == reference for relative, copy in copies.items()} == dict.fromkeys(SCRIPTS, True)
