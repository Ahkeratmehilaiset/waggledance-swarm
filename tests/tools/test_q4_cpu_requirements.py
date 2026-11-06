"""Static contract for the Q4 CPU wheel closure; no download or installation.

This lock targets Windows amd64 CPython 3.13 CPU only. It does not install a
runtime or replace the reboot bundle's existing bridge dependency lock.
"""
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "ops" / "windows" / "reboot" / "requirements-q4-cpu-windows.txt"
TESTED_ROWS_SHA256 = "ef837424ddc54bf639be4a122e1cb0df1e8399795e583ee260f155636dd18481"
RECORD = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)=="
    r"(?P<version>[A-Za-z0-9][A-Za-z0-9.+_-]*) "
    r"--hash=sha256:(?P<sha>[0-9a-f]{64})"
)


def _lines():
    text = LOCK.read_text(encoding="utf-8")
    return text, [line for line in text.splitlines() if line and not line.startswith("#")]


def _parsed():
    _, lines = _lines()
    rows = [RECORD.fullmatch(line) for line in lines]
    assert len(rows) == 53
    assert all(rows), "every row must be an exact version with one SHA256 and no URL"
    return rows


def test_scope_is_explicit_and_never_claims_installation():
    text, _ = _lines()
    assert "Windows amd64" in text and "CPython 3.13" in text and "CPU" in text
    assert "does not install" in text
    assert "does not replace" in text


def test_every_row_is_exactly_pinned_and_digest_is_64_lower_hex():
    rows = _parsed()
    assert all(len(row.group("sha")) == 64 for row in rows)


def test_no_duplicate_distributions_even_with_spelling_variants():
    rows = _parsed()
    normalized = [re.sub(r"[-_.]+", "-", row.group("name")).lower() for row in rows]
    assert len(normalized) == len(set(normalized))


def test_complete_lock_rows_match_the_tested_wheel_set():
    _, lines = _lines()
    canonical = ("\n".join(lines) + "\n").encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == TESTED_ROWS_SHA256


def test_key_cpu_and_bridge_versions_are_the_tested_pins():
    rows = _parsed()
    pins = {re.sub(r"[-_.]+", "-", row.group("name")).lower(): row.group("version") for row in rows}
    assert {name: pins.get(name) for name in (
        "numpy", "faiss-cpu", "torch", "sentence-transformers", "transformers", "pydantic",
        "pydantic-core", "jsonschema", "typing-extensions",
    )} == {
        "numpy": "2.5.3",
        "faiss-cpu": "1.13.2",
        "torch": "2.13.0+cpu",
        "sentence-transformers": "5.2.3",
        "transformers": "5.17.0",
        "pydantic": "2.13.5",
        "pydantic-core": "2.46.5",
        "jsonschema": "4.26.0",
        "typing-extensions": "4.16.0",
    }

