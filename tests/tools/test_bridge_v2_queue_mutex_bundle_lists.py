"""S2 bundle lists (fable-5 2026-09-30): the legacy claim/done writers take the v2 runtime-root mutex through
BridgeV2QueueMutex.ps1, which ClaimLeaseHeartbeat.ps1 loads before any claim mutation and which itself dot-sources
BridgeNamedMutex.ps1. The installed 7779 bundle has no BridgeV2QueueMutex.ps1, and none of the three required-file
lists named it, so a bundle without it passed deploy and Tools boot and then refused every claim. Every list that
requires ClaimLeaseHeartbeat.ps1 must require the whole helper chain, so a missing helper is refused at deploy
(Deploy-WdRebootBundle.ps1), in the fleet manifest (wd-fleet.json) and at Tools boot (start-wd-tools-consumer.ps1).

The PowerShell lists are read as PowerShell text (RCO2 N1, 2026-09-30): only quoted strings count, comments are
skipped, and each loop must occur exactly once, so a commented-out entry never reads as required. The chain must load
its helpers in the one dot-source form the derivation reads, so a load written another way fails instead of passing.
That check scans code only (comments blanked, strings masked) and sees a dot-source with or without a blank after the
dot, a call operator and Import-Module (RCO2 N2, 2026-10-01).

That check lexes with regexes, not the PowerShell parser, and misses some loads (RCO2 N3, 2026-10-01; none of these
forms is in the chain today): one after a keyword ("return .$h", "return & $x"), one through Invoke-Expression (a
string), one after && (pwsh 7 only), and one masked by a double-quoted string whose $(...) holds a double quote, as
in "$('"')": the string mask ends at that inner quote, so the code after it, up to a later quote, can be masked as a
string. The CHAIN constant and the derived list checks still pin the real chain. If the chain ever loads helpers
dynamically, read its loads from the PowerShell AST instead.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / "ops" / "windows" / "reboot"
BIN = ROOT / ".agent-bridge" / "bin"
BIN_PREFIX = "tools-bootstrap/.agent-bridge/bin/"
CHAIN = ("ClaimLeaseHeartbeat.ps1", "BridgeV2QueueMutex.ps1", "BridgeNamedMutex.ps1")
DOT_SOURCED = re.compile(r"\.\s*\(Join-Path \$PSScriptRoot '([A-Za-z0-9_.-]+\.ps1)'\)")
# Load operators at a statement start (a line start, or after ; { ( | =), matched in _code(source) only. Group 1 is a
# dot-source '.': a target may follow with or without blanks, a relative path too, but not a range or a decimal.
DOT_OPERATOR = re.compile(r"(?:^|[;{(|=])[ \t]*(\.)(?=[ \t]*(?:[^\s.\d]|\.\.?[\\/]))", re.M)
# Group 1 is a call operator or Import-Module: a runtime load DOT_SOURCED never reads.
CALL_OR_IMPORT = re.compile(r"(?:^|[;{(|=])[ \t]*(&|Import-Module\b)", re.M | re.I)
# PowerShell strings and comments, scanned left to right: here-strings, block comments, line comments (a '#' that
# starts a token), and single- or double-quoted strings.
NON_CODE = re.compile(r"@'\r?\n.*?\r?\n'@|@\"\r?\n.*?\r?\n\"@|<#.*?#>|(?:^|(?<=[\s;({]))#[^\n]*"
                      r"|'(?:[^']|'')*'|\"(?:[^\"`]|`.|\"\")*\"", re.S | re.M)
# Quoted strings and comments scanned left to right: a '#' inside quotes is text, and quotes inside a line or block
# comment are not entries.
STRING_OR_COMMENT = re.compile(r"'([^']*)'|<#.*?#>|#[^\n]*", re.S)


def _block(text: str, opener: str) -> list[str]:
    assert text.count(opener) == 1, f"expected exactly one {opener!r} loop"
    start = text.index(opener) + len(opener)
    body = text[start:text.index(")) {", start)]
    return [match.group(1) for match in STRING_OR_COMMENT.finditer(body) if match.group(1) is not None]


def _fleet() -> list[str]:
    manifest = json.loads((REBOOT / "wd-fleet.json").read_text(encoding="utf-8"))
    return [name.removeprefix(BIN_PREFIX) for name in manifest["deployment"]["required_bundle_files"]
            if name.startswith(BIN_PREFIX)]


def _deployer() -> list[str]:
    text = (REBOOT / "Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8")
    return [name.removeprefix(BIN_PREFIX) for name in _block(text, "foreach ($required in @(")
            if name.startswith(BIN_PREFIX)]


def _tools_consumer() -> list[str]:
    text = (REBOOT / "start-wd-tools-consumer.ps1").read_text(encoding="utf-8")
    return _block(text, "foreach ($requiredLeaf in @(")


LISTS = {"wd-fleet.json": _fleet, "Deploy-WdRebootBundle.ps1": _deployer,
         "start-wd-tools-consumer.ps1": _tools_consumer}


@pytest.mark.parametrize("list_name", sorted(LISTS))
def test_every_required_list_names_the_whole_claim_helper_chain(list_name: str) -> None:
    listed = LISTS[list_name]()
    assert "ClaimLeaseHeartbeat.ps1" in listed          # the premise: this list guards the claim helpers
    missing = [helper for helper in CHAIN if helper not in listed]
    assert missing == [], f"{list_name} does not require {missing}"


@pytest.mark.parametrize("list_name", sorted(LISTS))
def test_every_helper_the_chain_dot_sources_is_required_where_the_chain_is(list_name: str) -> None:
    # Derived from the sources, so a helper a later change loads (lazily or not) cannot be left off a list.
    listed = LISTS[list_name]()
    for script in CHAIN:
        source = (BIN / script).read_text(encoding="utf-8-sig")
        for helper in DOT_SOURCED.findall(source):
            assert (BIN / helper).is_file(), f"{script} dot-sources a missing {helper}"
            assert helper in listed, f"{script} loads {helper}, which {list_name} does not require"


def _code(source: str) -> str:
    """The source as code only: comments blanked and strings masked as 'S', lengths and line breaks kept."""
    def mask(match: re.Match) -> str:
        return re.sub(r"[^\r\n]", " " if match.group(0)[0] in "<#" else "S", match.group(0))
    return NON_CODE.sub(mask, source)


def _unrecognised_loads(source: str) -> list[str]:
    """Each load the derivation cannot read, as the rest of its line: a dot-source not in the Join-Path form, and
    every call operator or Import-Module. Only code counts, so a comment or a string never trips it. The module
    docstring lists the forms it misses."""
    code = _code(source)
    starts = [match.start(1) for match in DOT_OPERATOR.finditer(code)
              if not DOT_SOURCED.match(source, match.start(1))]
    starts += [match.start(1) for match in CALL_OR_IMPORT.finditer(code)]
    return [source[start:].splitlines()[0] for start in sorted(starts)]


def test_the_chain_loads_helpers_only_in_the_form_the_derivation_reads() -> None:
    # A load written another way (through a variable, a quoted path) would be invisible to DOT_SOURCED, and the
    # derived check above would pass without it. Every dot-source in the chain must be the Join-Path form.
    for script in CHAIN:
        source = (BIN / script).read_text(encoding="utf-8-sig")
        assert _unrecognised_loads(source) == [], f"{script} loads a helper in a form the derivation cannot read"


def test_the_load_form_check_flags_every_other_form() -> None:
    read = ("    . (Join-Path $PSScriptRoot 'A.ps1')\n    .(Join-Path $PSScriptRoot 'B.ps1')\n$x = 1.5\n@(1..3)\n"
            "$y = 1 .. 3\nSet-Location .\\work\n$z = @{ a = .5 }\n")
    assert _unrecognised_loads(read) == []
    assert _unrecognised_loads("    $h = Join-Path $PSScriptRoot 'A.ps1'\n    . $h\n") == [". $h"]
    assert _unrecognised_loads("    .$h\n    .\t$h\n") == [".$h", ".\t$h"]                 # RCO2 N2: no blank
    assert _unrecognised_loads('    . "$PSScriptRoot\\A.ps1"\n') == ['. "$PSScriptRoot\\A.ps1"']
    assert _unrecognised_loads("    . .\\A.ps1\n") == [". .\\A.ps1"]
    assert _unrecognised_loads("if ($lazy) { . $h }\n$r = . $h\n") == [". $h }", ". $h"]
    assert _unrecognised_loads("Write-Output a#b; . $h\n") == [". $h"]         # a '#' inside a token is no comment
    assert _unrecognised_loads("    & (Join-Path $PSScriptRoot 'A.ps1')\n") == ["& (Join-Path $PSScriptRoot 'A.ps1')"]
    assert _unrecognised_loads("    Import-Module (Join-Path $PSScriptRoot 'A.psm1')\n") == [
        "Import-Module (Join-Path $PSScriptRoot 'A.psm1')"]


def test_the_load_form_check_reads_code_only() -> None:
    # Comment-based help, comments, strings and here-strings (C# inside Add-Type) are not loads.
    text = ("<#\n.SYNOPSIS\n    Loads . $h, then & $x.\n#>\n# . $h\n$s = '. $h'\n$t = \". $h & $x\"\n"
            "$c = @'\nisn't code: x & y;\n. $z\n'@\n$d = @\"\n. $q\n\"@\ngit status 2>&1\nWrite-Output a#b\n")
    assert _unrecognised_loads(text) == []


def test_the_list_reader_skips_commented_out_entries() -> None:
    # RCO2 N1: a commented-out entry must not read as required, whatever the comment form.
    text = ("foreach ($x in @(\n"
            "        'Kept.ps1', # 'TrailingComment.ps1',\n"
            "        # don't 'LineComment.ps1',\n"
            "        <# 'BlockComment.ps1',\n"
            "        'BlockCommentLine2.ps1', #>\n"
            "        'Hash#InName.ps1'\n"
            "    )) {\n")
    assert _block(text, "foreach ($x in @(") == ["Kept.ps1", "Hash#InName.ps1"]


def test_the_list_reader_refuses_a_second_copy_of_the_loop() -> None:
    text = "# foreach ($x in @('Old.ps1')) {\nforeach ($x in @(\n    'New.ps1'\n)) {\n"
    with pytest.raises(AssertionError, match="exactly one"):
        _block(text, "foreach ($x in @(")


def test_the_listed_chain_exists_and_names_one_file_each() -> None:
    lowered = {helper.lower() for helper in CHAIN}
    for helper in CHAIN:
        assert (BIN / helper).is_file(), helper
    for list_name, read in LISTS.items():
        listed = read()
        variants = [name for name in listed if name.lower() in lowered and name not in CHAIN]
        assert variants == [], f"{list_name} spells a chain helper in another case: {variants}"
        for helper in CHAIN:
            assert listed.count(helper) <= 1, f"{list_name} lists {helper} twice"
