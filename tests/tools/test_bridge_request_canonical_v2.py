"""Golden bytes for the DORMANT canonical-v2 JSON utility on Windows PowerShell 5.1 and PowerShell 7.

.agent-bridge/bin/BridgeRequestCanonicalV2.ps1 is not wired into any bridge writer, reader, request contract, digest
or gate. Each host test starts a fresh -NoProfile process that dot-sources only that file and serializes synthetic
values; nothing reads or writes bridge state. A host that is not installed is skipped and never counted as evidence
for the other host. Legacy v1 (BridgeRequestContract.ps1) keeps its known host variants and is not exercised here.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / ".agent-bridge" / "bin" / "BridgeRequestCanonicalV2.ps1"
FUNCTION = "ConvertTo-WdBridgeCanonicalV2"
HELPERS = {
    FUNCTION,
    "Add-WdBridgeCanonicalV2Value",
    "Add-WdBridgeCanonicalV2String",
    "Add-WdBridgeCanonicalV2Text",
}
CULTURES = ("invariant", "fi-FI", "tr-TR")
LONG_OK = 1048574
KEYS_ORDINAL = "'b','a','B','A','a_b','ab','a-b','aB','_x','1','10','2','z',([string][char]0xE4)"


def ok(text: str) -> tuple[str, bytes]:
    return ("OK", text.encode("utf-8"))


def err(fragment: str) -> tuple[str, str]:
    return ("ERR", fragment)


# (case name, PowerShell body that sets $v and optionally $opt / $check, expected outcome)
CASES: list[tuple[str, str, tuple]] = [
    ("s_plain", "$v = 'toy message'", ok('"toy message"')),
    ("s_empty", "$v = ''", ok('""')),
    ("s_apostrophe", "$v = 'it''s a toy'", ok('"it\'s a toy"')),
    ("s_html_ampersand", "$v = '<b class=\"t\">A & B</b>'", ok('"<b class=\\"t\\">A & B</b>"')),
    (
        "s_controls",
        "$v = [string][char]0 + [char]1 + [char]8 + [char]9 + [char]10 + [char]12 + [char]13 + [char]31 + [char]127",
        ok('"\\u0000\\u0001\\b\\t\\n\\f\\r\\u001f\x7f"'),
    ),
    (
        "s_newline_backslash",
        "$v = 'line1' + [char]10 + 'line2 C:\\toy\\path'",
        ok('"line1\\nline2 C:\\\\toy\\\\path"'),
    ),
    ("s_slash", "$v = 'a/b'", ok('"a/b"')),
    (
        "s_unicode_literal",
        "$v = [string][char]0xE4 + [char]0xF6 + ' ' + [char]0x20AC + ' ' + [char]0xD83D + [char]0xDE00 + ' ' + [char]0x2028",
        ok('"\u00e4\u00f6 \u20ac \U0001F600 \u2028"'),
    ),
    ("s_no_normalization", "$v = 'e' + [char]0x0301 + [char]0xE9", ok('"e\u0301\u00e9"')),
    ("n_null", "$v = $null", ok("null")),
    ("b_true", "$v = $true", ok("true")),
    ("b_false", "$v = $false", ok("false")),
    (
        "i_boundaries",
        "$v = @([sbyte]::MinValue, [sbyte]::MaxValue, [int16]::MinValue, [int16]::MaxValue, [int]::MinValue, "
        "[int]::MaxValue, [long]::MinValue, [long]::MaxValue, 0, -1)",
        ok("[-128,127,-32768,32767,-2147483648,2147483647,-9223372036854775808,9223372036854775807,0,-1]"),
    ),
    (
        "k_punctuation_ordinal",
        "$v = [System.Collections.Hashtable]::new([System.StringComparer]::Ordinal); "
        "foreach ($k in @(" + KEYS_ORDINAL + ")) { $v[$k] = 1 }",
        ok('{"1":1,"10":1,"2":1,"A":1,"B":1,"_x":1,"a":1,"a-b":1,"aB":1,"a_b":1,"ab":1,"b":1,"z":1,"\u00e4":1}'),
    ),
    (
        "k_pscustomobject",
        "$v = [pscustomobject][ordered]@{ b = 1; a = 1; 'a_b' = 1; ab = 1; 'a-b' = 1; '_x' = 1; '10' = 1; '2' = 1; z = 1 }",
        ok('{"10":1,"2":1,"_x":1,"a":1,"a-b":1,"a_b":1,"ab":1,"b":1,"z":1}'),
    ),
    ("k_ordered_input_order_ignored", "$v = [ordered]@{ z = 1; a = 2 }", ok('{"a":2,"z":1}')),
    ("k_escaped_key", "$v = @{ ('q\"' + [char]10) = 1 }", ok('{"q\\"\\n":1}')),
    (
        "k_utf16_code_unit_order",
        "$v = @{ ([string][char]0xFF61) = 1; ([string][char]0xD83D + [char]0xDE00) = 2 }",
        ok('{"\U0001F600":2,"\uff61":1}'),
    ),
    ("c_empty_array", "$v = @()", ok("[]")),
    ("c_one_element_array", "$v = @('x')", ok('["x"]')),
    ("c_empty_hashtable", "$v = @{}", ok("{}")),
    ("c_empty_pscustomobject", "$v = [pscustomobject]@{}", ok("{}")),
    (
        "c_nested",
        "$v = [ordered]@{ obj = [ordered]@{ inner = @(,@(2)) }; list = @(1, @(), @{}, $null, $true, $false, 'x') }",
        ok('{"list":[1,[],{},null,true,false,"x"],"obj":{"inner":[[2]]}}'),
    ),
    (
        "c_generic_list_and_arraylist",
        "$g = [System.Collections.Generic.List[object]]::new(); $g.Add(1); $g.Add('a'); "
        "$a = [System.Collections.ArrayList]::new(); [void]$a.Add($g); [void]$a.Add($null); $v = $a",
        ok('[[1,"a"],null]'),
    ),
    (
        "m_input_not_mutated",
        "$v = [ordered]@{ b = 1; a = @(3, 2) }; $check = { ($v.Keys -join ',') + '|' + ($v['a'] -join ',') }",
        ok('{"a":[3,2],"b":1}'),
    ),
    ("r_double", "$v = [double]1", err("unsupported type System.Double")),
    ("r_single", "$v = [single]1", err("unsupported type System.Single")),
    ("r_decimal", "$v = [decimal]1", err("unsupported type System.Decimal")),
    ("r_nan", "$v = [double]::NaN", err("unsupported type System.Double")),
    ("r_infinity", "$v = [double]::PositiveInfinity", err("unsupported type System.Double")),
    (
        "r_datetime",
        "$v = [datetime]::new(2026, 10, 23, 0, 0, 0, [System.DateTimeKind]::Utc)",
        err("unsupported type System.DateTime"),
    ),
    (
        "r_datetimeoffset",
        "$v = [datetimeoffset]::new(2026, 10, 23, 0, 0, 0, [timespan]::Zero)",
        err("unsupported type System.DateTimeOffset"),
    ),
    ("r_char", "$v = [char]'a'", err("unsupported type System.Char")),
    ("r_byte", "$v = [byte]1", err("unsupported type System.Byte")),
    ("r_uint16", "$v = [uint16]1", err("unsupported type System.UInt16")),
    ("r_uint32", "$v = [uint32]1", err("unsupported type System.UInt32")),
    ("r_uint64", "$v = [uint64]1", err("unsupported type System.UInt64")),
    ("r_enum", "$v = [System.DayOfWeek]::Monday", err("unsupported type System.DayOfWeek")),
    ("r_guid", "$v = [guid]::Empty", err("unsupported type System.Guid")),
    ("r_dbnull", "$v = [System.DBNull]::Value", err("unsupported type System.DBNull")),
    ("r_scriptblock", "$v = { 1 }", err("unsupported type System.Management.Automation.ScriptBlock")),
    ("r_double_in_array", "$v = @(1, 0.5)", err("unsupported type System.Double")),
    ("r_byte_array", "$v = [byte[]](1, 2)", err("unsupported type System.Byte")),
    ("r_int_key", "$v = @{ 1 = 'a' }", err("non-string key System.Int32")),
    (
        "r_script_property",
        "$v = [pscustomobject]@{ a = 1 }; $v | Add-Member -MemberType ScriptProperty -Name s -Value { 2 }",
        err("unsupported member type ScriptProperty"),
    ),
    ("r_lone_high_surrogate", "$v = [string][char]0xD800 + 'x'", err("unpaired UTF-16 surrogate")),
    ("r_lone_low_surrogate", "$v = 'x' + [char]0xDC00", err("unpaired UTF-16 surrogate")),
    ("r_reversed_pair", "$v = [string][char]0xDE00 + [char]0xD83D", err("unpaired UTF-16 surrogate")),
    ("r_trailing_high_surrogate", "$v = 'x' + [char]0xD83D", err("unpaired UTF-16 surrogate")),
    ("r_surrogate_key", "$v = @{ ([string][char]0xD800) = 1 }", err("unpaired UTF-16 surrogate")),
    (
        "l_depth_32_accepted",
        "$v = 1; for ($i = 0; $i -lt 32; $i++) { $v = @(,$v) }",
        ok("[" * 32 + "1" + "]" * 32),
    ),
    (
        "l_depth_33_refused",
        "$v = 1; for ($i = 0; $i -lt 33; $i++) { $v = @(,$v) }",
        err("nesting depth exceeds 32"),
    ),
    ("l_depth_custom_refused", "$v = @(@{ a = @() }); $opt = @{ MaxDepth = 2 }", err("nesting depth exceeds 2")),
    (
        "l_self_cycle_refused",
        "$a = [System.Collections.ArrayList]::new(); [void]$a.Add($a); $v = $a",
        err("nesting depth exceeds 32"),
    ),
    ("l_chars_exact_fit", "$v = 'abc'; $opt = @{ MaxChars = 5 }", ok('"abc"')),
    ("l_chars_one_over", "$v = 'abcd'; $opt = @{ MaxChars = 5 }", err("output exceeds 5 characters")),
    ("l_chars_default_fit", "$v = 'a' * " + str(LONG_OK), ok('"' + "a" * LONG_OK + '"')),
    ("l_chars_default_over", "$v = 'a' * " + str(LONG_OK + 1), err("output exceeds 1048576 characters")),
    ("l_maxdepth_cannot_widen", "$v = 1; $opt = @{ MaxDepth = 33 }", err("MaxDepth")),
    ("l_maxchars_cannot_widen", "$v = 1; $opt = @{ MaxChars = 1048577 }", err("MaxChars")),
]
EXTRA = {"m_input_not_mutated": "b,a|3,2"}

HOST_SCRIPT = r"""param([string]$Source, [string]$Culture)
$ErrorActionPreference = 'Stop'
$c = [System.Globalization.CultureInfo]::InvariantCulture
if ($Culture -cne 'invariant') { $c = [System.Globalization.CultureInfo]::GetCultureInfo($Culture) }
[System.Globalization.CultureInfo]::CurrentCulture = $c
[System.Globalization.CultureInfo]::CurrentUICulture = $c
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors)
$top = @($ast.EndBlock.Statements | ForEach-Object { $_.GetType().Name }) -join ','
$fns = @($ast.EndBlock.Statements | ForEach-Object { if ($_ -is [System.Management.Automation.Language.FunctionDefinitionAst]) { $_.Name } }) -join ','
$cmds = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true) | ForEach-Object { $_.GetCommandName() }) -join ','
$rows = [System.Collections.Generic.List[string]]::new()
$rows.Add("host`t$($PSVersionTable.PSVersion)`t$($PSVersionTable.PSEdition)`t$([System.Globalization.CultureInfo]::CurrentCulture.Name)")
$rows.Add("ast`terrors=$($errors.Count);param=$($null -ne $ast.ParamBlock);begin=$($null -ne $ast.BeginBlock);process=$($null -ne $ast.ProcessBlock);top=$top;fns=$fns;cmds=$cmds")
. $Source
$strict = [System.Text.UTF8Encoding]::new($false, $true)
function B64([string]$Text) { [Convert]::ToBase64String($strict.GetBytes($Text)) }
function Invoke-Case([string]$CaseName, [scriptblock]$Body) {
    $v = $null; $opt = @{}; $check = $null
    . $Body
    try {
        $out = ConvertTo-WdBridgeCanonicalV2 -Value $v @opt
        $extra = if ($check) { [string](& $check) } else { '' }
        $rows.Add($CaseName + "`tOK`t" + (B64 $out) + "`t" + (B64 $extra))
    } catch {
        $rows.Add($CaseName + "`tERR`t" + (B64 $_.Exception.Message) + "`t")
    }
}
@@CASES@@
[Console]::Out.Write(($rows -join "`n"))
"""


def _host_script() -> str:
    calls = "\n".join("Invoke-Case '" + name + "' {\n" + body + "\n}" for name, body, _ in CASES)
    return HOST_SCRIPT.replace("@@CASES@@", calls)


def _host_executable(host: str) -> str | None:
    if host == "ps5":
        system_root = os.environ.get("SystemRoot")
        if not system_root:
            return None
        path = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        return str(path) if path.is_file() else None
    return shutil.which("pwsh")


@pytest.fixture(scope="module")
def host_rows(tmp_path_factory):
    cache: dict[tuple[str, str], dict[str, list[str]]] = {}
    script = tmp_path_factory.mktemp("canonical_v2_host") / "canonical_v2_host.ps1"
    script.write_text(_host_script(), encoding="utf-8")

    def run(host: str, culture: str) -> dict[str, list[str]]:
        executable = _host_executable(host)
        if executable is None:
            pytest.skip(f"{host} is not installed here; no result is claimed for it")
        key = (host, culture)
        if key not in cache:
            completed = subprocess.run(
                [
                    executable,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "-Source",
                    str(SOURCE),
                    "-Culture",
                    culture,
                ],
                capture_output=True,
                timeout=300,
                check=False,
            )
            assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")[-2000:]
            rows: dict[str, list[str]] = {}
            for line in completed.stdout.decode("ascii").split("\n"):
                fields = line.split("\t")
                assert fields[0] not in rows, f"duplicate or stray output row: {line[:120]}"
                rows[fields[0]] = fields[1:]
            cache[key] = rows
        return cache[key]

    return run


HOSTS = ("ps5", "ps7")


@pytest.mark.parametrize("host", HOSTS)
def test_source_defines_functions_only_and_calls_no_cmdlet(host_rows, host):
    rows = host_rows(host, "invariant")
    facts = dict(item.split("=", 1) for item in rows["ast"][0].split(";"))
    assert facts["errors"] == "0"
    assert (facts["param"], facts["begin"], facts["process"]) == ("False", "False", "False")
    assert set(facts["top"].split(",")) == {"FunctionDefinitionAst"}
    assert set(facts["fns"].split(",")) == HELPERS
    assert set(filter(None, facts["cmds"].split(","))) <= HELPERS


@pytest.mark.parametrize("culture", CULTURES)
@pytest.mark.parametrize("host", HOSTS)
def test_host_sets_requested_culture(host_rows, host, culture):
    rows = host_rows(host, culture)
    assert rows["host"][2] == ("" if culture == "invariant" else culture)


@pytest.mark.parametrize("name,expected", [(name, expected) for name, _, expected in CASES])
@pytest.mark.parametrize("culture", CULTURES)
@pytest.mark.parametrize("host", HOSTS)
def test_golden_bytes(host_rows, host, culture, name, expected):
    row = host_rows(host, culture)[name]
    if expected[0] == "OK":
        assert row[0] == "OK", base64.b64decode(row[1]).decode("utf-8", "replace")
        assert base64.b64decode(row[1]) == expected[1]
        assert base64.b64decode(row[2]).decode("utf-8") == EXTRA.get(name, "")
    else:
        assert row[0] == "ERR", f"accepted: {base64.b64decode(row[1])[:200]!r}"
        assert expected[1] in base64.b64decode(row[1]).decode("utf-8")


@pytest.mark.parametrize("culture", CULTURES)
def test_ps5_and_ps7_agree_byte_for_byte(host_rows, culture):
    ps5 = host_rows("ps5", culture)
    ps7 = host_rows("ps7", culture)
    for name, _, expected in CASES:
        assert ps5[name][0] == ps7[name][0], name
        if expected[0] == "OK":
            assert ps5[name][1:] == ps7[name][1:], name


def test_source_avoids_culture_dependent_paths_and_dynamic_code():
    text = SOURCE.read_text(encoding="utf-8")
    for token in ("Sort-Object", "ConvertTo-Json", "ConvertFrom-Json", "Invoke-Expression", "Add-Type", "Set-StrictMode"):
        assert token not in text, token


def test_utility_is_dormant_no_bridge_code_references_it():
    roots = [REPO / ".agent-bridge" / "bin", REPO / "tools", REPO / "ops"]
    hits = []
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".ps1", ".psm1", ".py", ".cmd", ".json"} or path == SOURCE:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if FUNCTION in text or "BridgeRequestCanonicalV2" in text:
                hits.append(path.relative_to(REPO).as_posix())
    assert hits == []
