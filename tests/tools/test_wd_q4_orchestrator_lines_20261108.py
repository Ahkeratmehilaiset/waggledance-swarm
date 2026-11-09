"""Exercise the real stdout parsing fragment with a synthetic worker only."""
import base64
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "ops/windows/reboot/Invoke-WdQ4ContinuationOrchestrator.ps1"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell is required")


def parse_stub_output(output):
    worker = subprocess.run([sys.executable, "-B", "-c", "import sys;sys.stdout.buffer.write(bytes.fromhex(sys.argv[1]))",
                             output.hex()], capture_output=True, timeout=10, check=True)
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index("    $text=[Text.UTF8Encoding]")
    end = text.index("    $keys=@($answer.PSObject.Properties.Name)", start)
    fragment = text[start:end]
    encoded = base64.b64encode(worker.stdout).decode("ascii")
    program = ("$ErrorActionPreference='Stop'; $v3=$false; "
               "$job=[pscustomobject]@{Stdout=[Convert]::FromBase64String('" + encoded + "')}; "
               "try {\n" + fragment + "\nif ($null -eq $answer) { throw 'output' }; "
               "[Console]::Out.Write('ACCEPT') } catch { [Console]::Out.Write('REJECT') }")
    host = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(host), "-NoProfile", "-NonInteractive", "-Command", program],
                            capture_output=True, text=True, timeout=20, check=True)
    return result.stdout


@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n", b"\n\n", b"\r"])
def test_one_json_line_and_existing_terminal_newlines_accepted(ending):
    assert parse_stub_output(b'{"status":"REFUSED"}' + ending) == "ACCEPT"


@pytest.mark.parametrize("output", [b'{\n"status":"REFUSED"}', b'{\r\n"status":"REFUSED"}',
                                   b'{\r"status":"REFUSED"}', b'\n{"status":"REFUSED"}',
                                   b'{"status":"REFUSED"}\n{"status":"REFUSED"}', b''])
def test_multiline_or_empty_output_rejected(output):
    assert parse_stub_output(output) == "REJECT"
