"""Cross-host, cross-language canonical JSON parity for the Rule 9b ConfirmDigest.

The v5 sealed-broker design signs a ConfirmDigest over a canonical JSON
encoding that is produced twice, independently: once by a handwritten
RFC 8259 encoder in the elevated PowerShell activator, and once by Python
in the receipt verifier that reads the applied receipt back. Those two
encodings have to be the same bytes or the signature means nothing -- if
they differ anywhere, the digest the operator signs does not identify the
payload that is applied, and a dry run under one host can disagree with an
elevated Apply under another about what was approved.

Two independent divergences were measured on 2026-08-26 against the pre-v5
code, and both are why this module exists:

1. ``json.dumps`` defaults to ``ensure_ascii=True``, escaping what the
   PowerShell side emitted literally. Non-ASCII, U+007F and astral
   characters all diverged between the two languages.
2. The PowerShell side delegated string escaping to ``ConvertTo-Json``,
   whose escaping policy differs between the Windows PowerShell 5.1
   JavaScriptSerializer and the PowerShell 7 System.Text.Json encoder. The
   two hosts disagreed with *each other* on ``<``, ``>``, ``&`` and the
   apostrophe, which makes the digest depend on which host computed it --
   for inputs as ordinary as ``it's``.

The Grok 4.6/high fifth verdict pinned U+1F600 and U+007F as required
fixtures. ``test_vector_table_contains_the_verdict_pinned_classes`` asserts
they are still present so the table cannot quietly lose the two inputs that
actually discriminate.

The vector table is deliberately built from inputs that *distinguish*
implementations rather than inputs that merely look exotic. U+0007 is the
exception and is included for the opposite reason, documented on its entry.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ACTIVATOR = REPO_ROOT / "ops" / "windows" / "reboot" / "Invoke-WdRule9bActivation.ps1"
VERIFIER = REPO_ROOT / "ops" / "windows" / "reboot" / "check_rule9b_activation_receipt.py"

PS7 = "pwsh"
PS51 = "powershell"

WINDOWS_BOTH_HOSTS = os.name == "nt" and bool(
    shutil.which(PS7) and shutil.which(PS51)
)

pytestmark = pytest.mark.skipif(
    not WINDOWS_BOTH_HOSTS,
    reason="canonical parity is a Windows contract and needs both PowerShell hosts",
)


# --------------------------------------------------------------------------
# Vector table
# --------------------------------------------------------------------------
# Each entry is (name, value, why_it_discriminates). The "why" is part of the
# fixture: a vector nobody can justify is a vector that can be replaced by a
# harmless one without anybody noticing.
CANONICAL_VECTORS: tuple[tuple[str, Any, str], ...] = (
    (
        "astral_surrogate_pair",
        "x\U0001F600y",
        "PowerShell strings are UTF-16, so [char[]] over this value yields TWO "
        "surrogate code units (U+D83D, U+DE00). A handwritten encoder that "
        "loops per [char] -- the natural way to write one -- either escapes the "
        "halves separately or emits them as WTF-8, which is not valid UTF-8. "
        "Pinned by the Grok fifth verdict.",
    ),
    (
        "delete_u007f",
        "a\u007fb",
        "U+007F is a control character by Unicode category but RFC 8259 does "
        "NOT require it to be escaped, which is exactly why a handwritten "
        "encoder and json.dumps' default disagree about it. Pinned by the Grok "
        "fifth verdict.",
    ),
    (
        "non_ascii_bmp",
        "docs/\u00e4\u00e4ni/\u6865.py",
        "ensure_ascii=True escapes these while PowerShell emits the literal "
        "UTF-8, so the two languages disagree on any path or message "
        "containing a non-ASCII character.",
    ),
    (
        "apostrophe",
        "it's",
        "Windows PowerShell 5.1 escapes the apostrophe to \\u0027 and "
        "PowerShell 7 does not, so this input alone makes the digest "
        "host-dependent.",
    ),
    (
        "ampersand_and_angles",
        "a<b>c&d",
        "Same host-dependent class as the apostrophe: PS 5.1 emits "
        "\\u003c / \\u003e / \\u0026 where PS 7 emits the literal characters.",
    ),
    (
        "control_bell",
        "a\u0007b",
        "Included precisely BECAUSE all three implementations already agree on "
        "it. It is not here to catch today's bug; it is a regression guard "
        "against an over-broad 'escape everything' correction, and it is the "
        "reason a fixture list that says only 'control' is not discriminating.",
    ),
    (
        "quote_and_backslash",
        'a"b\\c',
        "The two characters RFC 8259 does require escaping; a handwritten "
        "encoder that forgets them produces invalid JSON.",
    ),
    (
        "tab_and_newline",
        "a\tb\nc",
        "Control characters below U+0020 that must be escaped, and that a "
        "naive encoder is tempted to pass through literally.",
    ),
    (
        "solidus",
        "a/b",
        "Must NOT be escaped. Some encoders escape it as \\/ for HTML-embedding "
        "reasons, which would silently change the signed bytes.",
    ),
    (
        "line_separator_u2028",
        "a\u2028b",
        "U+2028 LINE SEPARATOR is the one character measured where "
        "ConvertTo-Json diverges from a conforming encoder on PowerShell 7: it "
        "escapes to \\u2028 while RFC 8259 requires no escape and Python with "
        "ensure_ascii=False emits the literal e2 80 a8. It is therefore the "
        "deterministic cross-language discriminator, and unlike key ordering "
        "it does not depend on dictionary enumeration order.",
    ),
    (
        "full_type_space",
        {
            "null": None,
            "true": True,
            "false": False,
            "int": 42,
            "negative": -7,
            "int64_max": 9223372036854775807,
            "int64_min": -9223372036854775808,
            "list": [1, "a", None, True],
            "empty_map": {},
            "empty_list": [],
        },
        "A string-only table proves nothing about null, booleans, integers, "
        "arrays or empty containers, each of which has its own literal form "
        "that a handwritten encoder can get wrong independently. The signed "
        "64-bit bounds are included because they are the exact edge where the "
        "two implementations stop agreeing.",
    ),
    (
        "nested_map_ordering",
        {"b": {"z": 1, "a": 2}, "a": "x"},
        "Key ordering must be sorted RECURSIVELY. ConvertTo-Json sorts nothing "
        "and an encoder that sorts only the top level produces a different "
        "digest for the same object.",
    ),
)

VERDICT_PINNED = ("astral_surrogate_pair", "delete_u007f")


def _payload(value: Any) -> dict[str, Any]:
    """Wrap a vector so every case exercises the whole object encoder."""
    return {"v": value}


# --------------------------------------------------------------------------
# Production-side adapters
# --------------------------------------------------------------------------
# These deliberately fail loudly rather than skip. An absent implementation is
# the red state this slice is written to express; a skip would report success.
def _python_canonical(payload: dict[str, Any]) -> bytes:
    """Canonical bytes from the production Python encoder."""
    if not VERIFIER.is_file():
        pytest.fail(
            f"not implemented yet: {VERIFIER.relative_to(REPO_ROOT)} must expose "
            "canonical_json_bytes(payload) -> bytes"
        )
    # A module-level __name__ is supplied so that a CLI guard added to the
    # verifier later cannot NameError here, and is deliberately NOT
    # "__main__" so such a guard does not execute during a test.
    namespace: dict[str, Any] = {
        "__name__": "rule9b_verifier_under_test",
        "__file__": str(VERIFIER),
    }
    exec(compile(VERIFIER.read_bytes(), str(VERIFIER), "exec"), namespace)
    encoder = namespace.get("canonical_json_bytes")
    if encoder is None:
        pytest.fail(
            "not implemented yet: canonical_json_bytes(payload) -> bytes is "
            f"missing from {VERIFIER.relative_to(REPO_ROOT)}"
        )
    result = encoder(payload)
    if not isinstance(result, bytes):
        pytest.fail(
            "canonical_json_bytes must return bytes so the comparison is over "
            f"the signed bytes themselves, got {type(result)!r}"
        )
    return result


_PS_FUNCTION = re.compile(
    r"^function ConvertTo-CanonicalJson \{.*?^\}\s*$",
    re.DOTALL | re.MULTILINE,
)

# Recursive PSCustomObject -> hashtable. ConvertFrom-Json -AsHashtable exists
# only on PowerShell 7, and this harness has to behave identically on 5.1.
_PS_HARNESS = """
function ConvertTo-HashtableDeep {
    param($Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        $h = @{}
        foreach ($p in $Value.PSObject.Properties) {
            $h[$p.Name] = ConvertTo-HashtableDeep $p.Value
        }
        return $h
    }
    if ($Value -is [object[]]) {
        # The unary comma is load-bearing: it survives the single unroll
        # PowerShell applies to a returned array, so an empty array arrives
        # as an empty array instead of $null.
        $out = New-Object System.Collections.Generic.List[object]
        foreach ($item in $Value) {
            [void]$out.Add((ConvertTo-HashtableDeep $item))
        }
        return ,$out.ToArray()
    }
    return $Value
}

$raw = Get-Content -Raw -LiteralPath 'PAYLOAD_PATH' -Encoding UTF8
$obj = ConvertTo-HashtableDeep ($raw | ConvertFrom-Json)
$encoded = ConvertTo-CanonicalJson -Value $obj
# Transported as hex: the console code page is not UTF-8 and would mangle the
# very characters under test.
$bytes = [System.Text.Encoding]::UTF8.GetBytes($encoded)
Write-Output (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
"""


def _run_powershell_snippet(
    exe: str, encoder_source: str, snippet: str
) -> subprocess.CompletedProcess[str]:
    """Load one encoder implementation and run an arbitrary snippet against it.

    Negative cases cannot be transported as JSON -- a lone surrogate has no
    JSON form and a non-string object key has none either -- so those values
    are constructed on the PowerShell side instead.
    """
    work = Path(tempfile.mkdtemp(prefix="rule9b-parity-"))
    try:
        script = work / "encode.ps1"
        script.write_text(encoder_source + "\n" + snippet, encoding="utf-8")
        return subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-File", str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run_powershell_encoder(exe: str, encoder_source: str, payload: dict[str, Any]) -> bytes:
    """Run one PowerShell encoder implementation over one payload."""
    work = Path(tempfile.mkdtemp(prefix="rule9b-parity-"))
    try:
        payload_path = work / "payload.json"
        payload_path.write_text(json.dumps(payload), encoding="utf-8")

        script = work / "encode.ps1"
        script.write_text(
            encoder_source + "\n" + _PS_HARNESS.replace("PAYLOAD_PATH", str(payload_path)),
            encoding="utf-8",
        )

        completed = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-File", str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)

    hexed = (completed.stdout or "").strip()
    if completed.returncode != 0 or not hexed:
        pytest.fail(
            f"{exe} encoder run failed (rc={completed.returncode}): "
            f"{(completed.stderr or '').strip()[:400]}"
        )
    return bytes.fromhex(hexed)


def _production_ps_encoder_source() -> str:
    """The activator's own ConvertTo-CanonicalJson, lifted for direct exercise.

    The function is extracted rather than dot-sourced because the activator
    performs real work at the top level (toolchain sealing, elevation checks)
    that must not run inside a test.
    """
    if not ACTIVATOR.is_file():
        pytest.fail(
            f"not implemented yet: {ACTIVATOR.relative_to(REPO_ROOT)} must define "
            "function ConvertTo-CanonicalJson"
        )
    match = _PS_FUNCTION.search(ACTIVATOR.read_text(encoding="utf-8"))
    if match is None:
        pytest.fail(
            "not implemented yet: function ConvertTo-CanonicalJson is missing "
            f"from {ACTIVATOR.relative_to(REPO_ROOT)}"
        )
    return match.group(0)


def _python_runtime_manifest(manifest: dict[str, Any]) -> bytes:
    namespace: dict[str, Any] = {
        "__name__": "rule9b_verifier_under_test",
        "__file__": str(VERIFIER),
    }
    exec(compile(VERIFIER.read_bytes(), str(VERIFIER), "exec"), namespace)
    encoder = namespace.get("canonical_runtime_manifest_bytes")
    if not callable(encoder):
        pytest.fail("canonical_runtime_manifest_bytes is not implemented")
    return encoder(manifest)


def _run_activation_snippet(exe: str, snippet: str) -> subprocess.CompletedProcess[str]:
    """Dot-source the construction-only activator and execute a snippet.

    Direct invocation remains a typed refusal. Dot-sourcing only defines the
    pure functions; this bounded slice has no Apply or mutation body.
    """
    script_text = ". '" + str(ACTIVATOR).replace("'", "''") + "'\n" + snippet
    return subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", script_text],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


# --------------------------------------------------------------------------
# The contract
# --------------------------------------------------------------------------
@pytest.mark.parametrize("host", [PS7, PS51])
@pytest.mark.parametrize(
    "name,value,why",
    CANONICAL_VECTORS,
    ids=[entry[0] for entry in CANONICAL_VECTORS],
)
def test_python_and_powershell_produce_identical_canonical_bytes(
    host: str, name: str, value: Any, why: str
) -> None:
    """One signed encoding, or the operator's signature identifies nothing."""
    payload = _payload(value)
    expected = _python_canonical(payload)
    actual = _run_powershell_encoder(host, _production_ps_encoder_source(), payload)
    assert actual == expected, (
        f"canonical bytes differ for vector {name!r} under {host}.\n"
        f"  why this vector exists: {why}\n"
        f"  python     : {expected!r}\n"
        f"  {host:<10} : {actual!r}"
    )


@pytest.mark.parametrize(
    "name,value,why",
    CANONICAL_VECTORS,
    ids=[entry[0] for entry in CANONICAL_VECTORS],
)
def test_the_two_powershell_hosts_agree_with_each_other(
    name: str, value: Any, why: str
) -> None:
    """A host-dependent digest is signed under one host and applied under another.

    This is a separate assertion from the cross-language one on purpose: the
    pre-v5 encoder passed a Python-vs-PS7 comparison while PS 5.1 silently
    disagreed, because escaping was delegated to ConvertTo-Json.
    """
    payload = _payload(value)
    source = _production_ps_encoder_source()
    on_ps7 = _run_powershell_encoder(PS7, source, payload)
    on_ps51 = _run_powershell_encoder(PS51, source, payload)
    assert on_ps51 == on_ps7, (
        f"the two PowerShell hosts disagree for vector {name!r}.\n"
        f"  why this vector exists: {why}\n"
        f"  pwsh       : {on_ps7!r}\n"
        f"  powershell : {on_ps51!r}"
    )


def test_vector_table_contains_the_verdict_pinned_classes() -> None:
    """U+1F600 and U+007F were pinned by the Grok 4.6/high fifth verdict.

    A fixture list that names only "Unicode and control" is satisfiable by
    inputs every implementation already agrees on, so the two that actually
    discriminate are asserted present by name.
    """
    present = {entry[0] for entry in CANONICAL_VECTORS}
    missing = [name for name in VERDICT_PINNED if name not in present]
    assert not missing, f"verdict-pinned vectors removed from the table: {missing}"

    by_name = {entry[0]: entry[1] for entry in CANONICAL_VECTORS}
    assert "\U0001F600" in by_name["astral_surrogate_pair"]
    assert "\u007f" in by_name["delete_u007f"]


def test_the_parity_harness_detects_a_divergent_encoder() -> None:
    """Prove the harness has teeth independently of the production encoder.

    A parity harness that cannot fail is worth nothing, and "the tests pass"
    is exactly how a non-discriminating fixture set survives review. This
    feeds it the known-bad implementation -- delegating to ConvertTo-Json,
    which is what the pre-v5 activator did -- and requires it to be caught.

    Choosing the discriminator took two corrections. With a correct Python
    baseline, PowerShell 7's ConvertTo-Json agrees with ensure_ascii=False on
    every character vector originally in the table, the astral pair included -
    so an earlier version of this test appeared to catch it only because its
    own baseline was wrong. The two properties it genuinely violates are that
    it escapes U+2028 where a conforming encoder does not, and that it sorts
    nothing, so an object whose keys are not already in sorted order exposes it
    on every host. Both are asserted below.
    """
    known_bad = (
        "function ConvertTo-CanonicalJson {\n"
        "    param([Parameter(Mandatory)] [AllowNull()] $Value)\n"
        "    return ($Value | ConvertTo-Json -Compress -Depth 20)\n"
        "}\n"
    )
    # Primary discriminator: U+2028. ConvertTo-Json escapes it, a conforming
    # encoder does not, and the case involves no dictionary ordering at all.
    u2028 = (
        "$v = [string]('a' + [char]0x2028 + 'b')\n"
        "$s = ConvertTo-CanonicalJson -Value $v\n"
        "$b = [System.Text.Encoding]::UTF8.GetBytes($s)\n"
        "Write-Output (($b | ForEach-Object { $_.ToString('x2') }) -join '')\n"
    )
    on_u2028 = _run_powershell_snippet(PS7, known_bad, u2028)
    assert on_u2028.returncode == 0, (on_u2028.stderr or "").strip()[:300]
    assert bytes.fromhex((on_u2028.stdout or "").strip()) != json.dumps(
        "a\u2028b", ensure_ascii=False
    ).encode("utf-8"), (
        "the harness failed to notice that a ConvertTo-Json encoder escapes "
        "U+2028; it would pass a broken encoder"
    )

    # Second, independent property: ConvertTo-Json sorts nothing. [ordered] is
    # used so the case does not depend on hashtable enumeration order.
    unsorted_keys = (
        "$o = [ordered]@{ z = 1; a = 2 }\n"
        "$s = ConvertTo-CanonicalJson -Value $o\n"
        "$b = [System.Text.Encoding]::UTF8.GetBytes($s)\n"
        "Write-Output (($b | ForEach-Object { $_.ToString('x2') }) -join '')\n"
    )
    python_sorted = json.dumps(
        {"z": 1, "a": 2}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")

    completed = _run_powershell_snippet(PS7, known_bad, unsorted_keys)
    assert completed.returncode == 0, (completed.stderr or "").strip()[:300]
    bad_bytes = bytes.fromhex((completed.stdout or "").strip())
    assert bad_bytes != python_sorted, (
        "the harness failed to notice that a ConvertTo-Json encoder does not "
        "sort object keys; it would pass a broken encoder"
    )

    # And the cross-host half: ConvertTo-Json is host-dependent on the
    # apostrophe, which is what made the pre-v5 digest depend on which
    # PowerShell the operator happened to run.
    apostrophe = _payload("it's")
    caught_cross_host = (
        _run_powershell_encoder(PS7, known_bad, apostrophe)
        != _run_powershell_encoder(PS51, known_bad, apostrophe)
    )
    assert caught_cross_host, (
        "the harness failed to notice that a ConvertTo-Json encoder is "
        "host-dependent on the apostrophe; it would pass a broken encoder"
    )


# --------------------------------------------------------------------------
# Refusal parity
# --------------------------------------------------------------------------
# Agreeing on what to ENCODE is only half the contract. Where one side
# silently coerces an input the other rejects, the coercion is the defect:
# each case below was measured producing bytes on one side and an exception
# on the other, and in two of the three the bytes were not the payload.
LONE_HIGH_SURROGATE = "$s = [string][char]0xD83D\nConvertTo-CanonicalJson -Value $s\n"
LONE_LOW_SURROGATE = "$s = [string][char]0xDE00\nConvertTo-CanonicalJson -Value $s\n"
NON_STRING_KEY = "$h = @{}\n$h[1] = 'x'\nConvertTo-CanonicalJson -Value $h\n"
OVERSIZED_INTEGER = (
    "$n = [bigint]'9223372036854775808'\nConvertTo-CanonicalJson -Value $n\n"
)
FLOAT_VALUE = "$n = [double]1.5\nConvertTo-CanonicalJson -Value $n\n"
NON_ASCII_KEY = (
    "$h = @{}\n$h[[string][char]0x00E4] = 'x'\n"
    "ConvertTo-CanonicalJson -Value $h\n"
)


@pytest.mark.parametrize("host", [PS7, PS51])
@pytest.mark.parametrize(
    "name,snippet,why",
    [
        (
            "lone_high_surrogate",
            LONE_HIGH_SURROGATE,
            "measured: .NET UTF-8 encoded it as ef bf bd (U+FFFD) while Python "
            "raised UnicodeEncodeError, so this side would have signed bytes "
            "that are not the text anybody wrote",
        ),
        (
            "lone_low_surrogate",
            LONE_LOW_SURROGATE,
            "same class as the lone high surrogate, reached by the other half "
            "of the pair",
        ),
        (
            "non_string_object_key",
            NON_STRING_KEY,
            "measured: casting the key to a string and indexing with it missed "
            "the entry entirely and emitted {\"1\":null} -- silent data loss, "
            "which is worse than a type error",
        ),
        (
            "integer_wider_than_int64",
            OVERSIZED_INTEGER,
            "Python encodes arbitrary-precision integers and this side cannot "
            "represent them; the refusal is explicit rather than an incidental "
            "unsupported-type error",
        ),
        (
            "float",
            FLOAT_VALUE,
            "floats have no single reproducible JSON form across the two "
            "implementations",
        ),
        (
            "non_ascii_object_key",
            NON_ASCII_KEY,
            "restricting keys to printable ASCII removes cross-runtime ordering "
            "differences instead of silently choosing one host's order",
        ),
    ],
    ids=[
        "lone_high",
        "lone_low",
        "non_string_key",
        "oversized_int",
        "float",
        "non_ascii_key",
    ],
)
def test_powershell_refuses_what_python_refuses(
    host: str, name: str, snippet: str, why: str
) -> None:
    """Both encoders must reject the same inputs, not merely agree on the rest."""
    completed = _run_powershell_snippet(host, _production_ps_encoder_source(), snippet)
    assert completed.returncode != 0, (
        f"{host} accepted {name!r} instead of refusing it.\n"
        f"  why it must be refused: {why}\n"
        f"  stdout: {(completed.stdout or '').strip()!r}"
    )


@pytest.mark.parametrize(
    "name,payload,why",
    [
        (
            "lone_high_surrogate",
            {"v": "x\ud83dy"},
            "a lone surrogate is not valid Unicode text; Python must refuse it "
            "explicitly rather than letting a late UnicodeEncodeError decide",
        ),
        (
            "lone_low_surrogate",
            {"v": "x\ude00y"},
            "the other half of the same class",
        ),
        (
            "non_string_object_key",
            {1: "x"},
            "JSON object keys are strings; accepting an integer key would make "
            "the canonical form depend on a coercion rule",
        ),
        (
            "integer_wider_than_int64",
            {"v": 2 ** 63},
            "the PowerShell side cannot represent it, so encoding it here would "
            "be a divergence even though that side fails closed",
        ),
        (
            "float",
            {"v": 1.5},
            "floats have no single reproducible JSON form across the two "
            "implementations",
        ),
        (
            "non_ascii_object_key",
            {"\u00e4": "x"},
            "Python orders keys by code point and .NET by UTF-16 code unit; "
            "restricting keys to printable ASCII removes the divergence "
            "instead of testing it",
        ),
    ],
    ids=[
        "lone_high",
        "lone_low",
        "non_string_key",
        "oversized_int",
        "float",
        "non_ascii_key",
    ],
)
def test_python_refuses_what_powershell_refuses(
    name: str, payload: Any, why: str
) -> None:
    """The Python side refuses with a typed error, not an incidental one."""
    if not VERIFIER.is_file():
        pytest.fail("not implemented yet: verifier is absent")
    namespace: dict[str, Any] = {
        "__name__": "rule9b_verifier_under_test",
        "__file__": str(VERIFIER),
    }
    exec(compile(VERIFIER.read_bytes(), str(VERIFIER), "exec"), namespace)
    error = namespace.get("CanonicalJsonError")
    encoder = namespace.get("canonical_json_bytes")
    if error is None or encoder is None:
        pytest.fail("not implemented yet: CanonicalJsonError/canonical_json_bytes missing")
    with pytest.raises(error):
        encoder(payload)


def test_in_range_unsigned_integers_are_accepted_and_match_python() -> None:
    """Refusing by type rather than by range is a divergence of its own.

    [uint64]5 is a value the Python side encodes without complaint. An
    earlier revision of the PowerShell encoder refused every uint64 and
    BigInteger outright, which made the two sides disagree about a perfectly
    ordinary small number.
    """
    snippet = (
        "$n = [uint64]5\n"
        "$s = ConvertTo-CanonicalJson -Value $n\n"
        "$b = [System.Text.Encoding]::UTF8.GetBytes($s)\n"
        "Write-Output (($b | ForEach-Object { $_.ToString('x2') }) -join '')\n"
    )
    for host in (PS7, PS51):
        completed = _run_powershell_snippet(host, _production_ps_encoder_source(), snippet)
        assert completed.returncode == 0, (
            f"{host} refused an in-range unsigned integer: "
            f"{(completed.stderr or '').strip()[:300]}"
        )
        assert bytes.fromhex((completed.stdout or "").strip()) == b"5"


@pytest.mark.parametrize("host", [PS7, PS51])
def test_runtime_manifest_bytes_match_python_and_sort_by_utf8(host: str) -> None:
    files = [
        {
            "path": "z.py",
            "git_blob_sha1": "2" * 40,
            "byte_length": 2,
            "sha256": "b" * 64,
        },
        {
            "path": "docs/\u00e4\u00e4ni.py",
            "git_blob_sha1": "3" * 40,
            "byte_length": 3,
            "sha256": "c" * 64,
        },
        {
            "path": "a.py",
            "git_blob_sha1": "1" * 40,
            "byte_length": 1,
            "sha256": "a" * 64,
        },
    ]
    sorted_files = sorted(files, key=lambda item: item["path"].encode("utf-8"))
    manifest = {
        "schema": "wd.rule9b.runtime_manifest.v1",
        "activation_head": "1" * 40,
        "activation_tree_sha": "2" * 40,
        "runtime_generation_id": "generation-1",
        "files": sorted_files,
    }
    expected = _python_runtime_manifest(manifest)
    snippet = r"""
$files = @(
    [ordered]@{path='z.py';git_blob_sha1=('2' * 40);byte_length=[int64]2;sha256=('b' * 64)},
    [ordered]@{path='docs/ääni.py';git_blob_sha1=('3' * 40);byte_length=[int64]3;sha256=('c' * 64)},
    [ordered]@{path='a.py';git_blob_sha1=('1' * 40);byte_length=[int64]1;sha256=('a' * 64)}
)
$bytes = New-WdRule9bRuntimeManifestBytes -ActivationHead ('1' * 40) `
    -ActivationTreeSha ('2' * 40) -RuntimeGenerationId 'generation-1' -Files $files
Write-Output (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
"""
    completed = _run_activation_snippet(host, snippet)
    assert completed.returncode == 0, (completed.stderr or completed.stdout)[:800]
    assert bytes.fromhex((completed.stdout or "").strip()) == expected


@pytest.mark.parametrize("host", [PS7, PS51])
@pytest.mark.parametrize(
    "path",
    ["../escape.py", "C:/drive.py", "dir\\backslash.py", "con.txt", "a//b.py"],
)
def test_powershell_runtime_manifest_refuses_unsafe_paths(
    host: str, path: str
) -> None:
    escaped = path.replace("'", "''")
    snippet = f"""
$files = @([ordered]@{{path='{escaped}';git_blob_sha1=('1' * 40);byte_length=[int64]1;sha256=('a' * 64)}})
[void](New-WdRule9bRuntimeManifestBytes -ActivationHead ('1' * 40) `
    -ActivationTreeSha ('2' * 40) -RuntimeGenerationId 'generation-1' -Files $files)
"""
    completed = _run_activation_snippet(host, snippet)
    assert completed.returncode != 0, f"{host} accepted unsafe path {path!r}"


@pytest.mark.parametrize("host", [PS7, PS51])
def test_powershell_runtime_manifest_refuses_casefold_collision(host: str) -> None:
    snippet = r"""
$files = @(
    [ordered]@{path='Gate.py';git_blob_sha1=('1' * 40);byte_length=[int64]1;sha256=('a' * 64)},
    [ordered]@{path='gate.py';git_blob_sha1=('2' * 40);byte_length=[int64]1;sha256=('b' * 64)}
)
[void](New-WdRule9bRuntimeManifestBytes -ActivationHead ('1' * 40) `
    -ActivationTreeSha ('2' * 40) -RuntimeGenerationId 'generation-1' -Files $files)
"""
    completed = _run_activation_snippet(host, snippet)
    assert completed.returncode != 0, f"{host} accepted a casefold collision"
