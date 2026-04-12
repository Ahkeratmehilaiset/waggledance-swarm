"""Post-gate release-polish tests: F3-005 .env parser quoting.

Locks in the real-world dotenv subset supported by
``waggledance.adapters.config.settings_loader._load_dotenv``:

- Comments (whole-line and trailing, quoting-aware)
- Matched-pair quote stripping (single and double, no cross-mixing)
- Mismatched-quote preservation
- ``export KEY=val`` shell-prefix support
- UTF-8 BOM handling
- Malformed-key rejection
- ``=`` in value preservation
- Shell-env precedence over .env

Every test uses ``monkeypatch.delenv`` before writing the tmp .env
file so no test leaks into another, and every test only touches
sentinel keys (``WD_F3005_*``) that no other code path reads.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from waggledance.adapters.config.settings_loader import _load_dotenv


SENTINEL_KEYS = [
    "WD_F3005_BASIC",
    "WD_F3005_SINGLE",
    "WD_F3005_DOUBLE",
    "WD_F3005_UNMATCHED",
    "WD_F3005_CROSS",
    "WD_F3005_INLINE_COMMENT",
    "WD_F3005_HASH_IN_VALUE",
    "WD_F3005_QUOTED_HASH",
    "WD_F3005_EXPORT",
    "WD_F3005_BOM",
    "WD_F3005_EQ",
    "WD_F3005_PRECEDENCE",
    "WD_F3005_EMPTY",
    "WD_F3005_SPACES",
    "WD_F3005_WHITESPACE_ONLY",
    "WD_F3005_BAD KEY",
]


@pytest.fixture(autouse=True)
def _cleanup_env(monkeypatch):
    for k in SENTINEL_KEYS:
        monkeypatch.delenv(k, raising=False)
    yield
    # monkeypatch.delenv handles teardown automatically, but we also
    # pop the raw os.environ values in case _load_dotenv set them
    # directly (which it does — it uses os.environ, not monkeypatch).
    for k in SENTINEL_KEYS:
        os.environ.pop(k, None)


def _write_env(tmp_path: Path, body: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(body, encoding="utf-8")
    return env


# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------

def test_basic_key_value(tmp_path):
    _load_dotenv(_write_env(tmp_path, "WD_F3005_BASIC=hello\n"))
    assert os.environ["WD_F3005_BASIC"] == "hello"


def test_missing_file_is_silent(tmp_path):
    # Must not raise and must not set anything.
    _load_dotenv(tmp_path / "does_not_exist.env")
    assert "WD_F3005_BASIC" not in os.environ


def test_blank_lines_and_comments_skipped(tmp_path):
    body = (
        "# top comment\n"
        "\n"
        "WD_F3005_BASIC=ok\n"
        "# trailing comment\n"
    )
    _load_dotenv(_write_env(tmp_path, body))
    assert os.environ["WD_F3005_BASIC"] == "ok"


# ---------------------------------------------------------------------------
# Matched-pair quote stripping
# ---------------------------------------------------------------------------

def test_double_quoted_value_strips_pair(tmp_path):
    _load_dotenv(_write_env(tmp_path, 'WD_F3005_DOUBLE="hello world"\n'))
    assert os.environ["WD_F3005_DOUBLE"] == "hello world"


def test_single_quoted_value_strips_pair(tmp_path):
    _load_dotenv(_write_env(tmp_path, "WD_F3005_SINGLE='hello world'\n"))
    assert os.environ["WD_F3005_SINGLE"] == "hello world"


def test_unmatched_opening_quote_is_preserved(tmp_path):
    """Previously `.strip("'\\"")` would silently strip the orphan
    double-quote. The new parser leaves mismatched quotes intact so
    operators can see they made a typo."""
    _load_dotenv(_write_env(tmp_path, 'WD_F3005_UNMATCHED="half\n'))
    # Old parser: "half" would become "half" (quote stripped).
    # New parser: literal value preserved as '"half'.
    assert os.environ["WD_F3005_UNMATCHED"] == '"half'


def test_cross_quoted_value_not_stripped(tmp_path):
    """A leading ' and trailing " is NOT a matched pair."""
    _load_dotenv(_write_env(tmp_path, "WD_F3005_CROSS='oops\"\n"))
    assert os.environ["WD_F3005_CROSS"] == "'oops\""


# ---------------------------------------------------------------------------
# Inline comment support (and the "#" in quoted value exception)
# ---------------------------------------------------------------------------

def test_inline_comment_after_whitespace_is_stripped(tmp_path):
    _load_dotenv(_write_env(
        tmp_path,
        "WD_F3005_INLINE_COMMENT=abc123  # dev key from 2024\n",
    ))
    assert os.environ["WD_F3005_INLINE_COMMENT"] == "abc123"


def test_hash_without_leading_whitespace_is_literal(tmp_path):
    """`KEY=abc#def` must store the literal `abc#def` -- a common
    pattern in URLs and hash-style identifiers."""
    _load_dotenv(_write_env(
        tmp_path,
        "WD_F3005_HASH_IN_VALUE=abc#def\n",
    ))
    assert os.environ["WD_F3005_HASH_IN_VALUE"] == "abc#def"


def test_quoted_value_preserves_inline_hash(tmp_path):
    """Inside a quoted value, `#` is literal regardless of surrounding
    whitespace."""
    _load_dotenv(_write_env(
        tmp_path,
        'WD_F3005_QUOTED_HASH="abc # not-a-comment"\n',
    ))
    assert os.environ["WD_F3005_QUOTED_HASH"] == "abc # not-a-comment"


# ---------------------------------------------------------------------------
# `export KEY=val` shell-prefix support
# ---------------------------------------------------------------------------

def test_export_prefix_is_recognised(tmp_path):
    _load_dotenv(_write_env(tmp_path, "export WD_F3005_EXPORT=yes\n"))
    assert os.environ["WD_F3005_EXPORT"] == "yes"
    # Defensive: the literal key "export WD_F3005_EXPORT" must NOT
    # have been set.
    assert "export WD_F3005_EXPORT" not in os.environ


# ---------------------------------------------------------------------------
# UTF-8 BOM handling
# ---------------------------------------------------------------------------

def test_utf8_bom_on_first_line_is_stripped(tmp_path):
    """Notepad on Windows writes UTF-8 with BOM by default. Before
    this fix, the first key silently gained a `\\ufeff` prefix,
    making `WAGGLE_API_KEY` invisible to downstream readers."""
    env = tmp_path / ".env"
    env.write_bytes(b"\xef\xbb\xbfWD_F3005_BOM=ok\n")
    _load_dotenv(env)
    assert os.environ.get("WD_F3005_BOM") == "ok"
    # And the BOM-contaminated key must NOT exist.
    assert "\ufeffWD_F3005_BOM" not in os.environ


# ---------------------------------------------------------------------------
# Value containing `=` preserved
# ---------------------------------------------------------------------------

def test_equals_in_value_preserved(tmp_path):
    _load_dotenv(_write_env(tmp_path, "WD_F3005_EQ=a=b=c\n"))
    assert os.environ["WD_F3005_EQ"] == "a=b=c"


# ---------------------------------------------------------------------------
# Shell-env precedence
# ---------------------------------------------------------------------------

def test_shell_env_wins_over_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("WD_F3005_PRECEDENCE", "from-shell")
    _load_dotenv(_write_env(
        tmp_path,
        "WD_F3005_PRECEDENCE=from-dotenv\n",
    ))
    assert os.environ["WD_F3005_PRECEDENCE"] == "from-shell"


# ---------------------------------------------------------------------------
# Empty / malformed value handling
# ---------------------------------------------------------------------------

def test_empty_value(tmp_path):
    _load_dotenv(_write_env(tmp_path, "WD_F3005_EMPTY=\n"))
    assert os.environ["WD_F3005_EMPTY"] == ""


def test_value_with_leading_hash_becomes_empty(tmp_path):
    """`KEY=#foo` is convention for "empty value, then inline
    comment". The `#foo` is dropped."""
    _load_dotenv(_write_env(tmp_path, "WD_F3005_EMPTY=#just a comment\n"))
    assert os.environ["WD_F3005_EMPTY"] == ""


def test_malformed_key_with_space_is_rejected(tmp_path):
    """Key containing a space is not a valid POSIX env var name and
    must be silently skipped instead of silently writing to a
    non-existent env var name."""
    _load_dotenv(_write_env(tmp_path, "WD_F3005 BAD=value\n"))
    assert "WD_F3005 BAD" not in os.environ
    assert "WD_F3005" not in os.environ  # the partial must not land either


# ---------------------------------------------------------------------------
# No-leak paranoid guard (every dotenv test should honour this)
# ---------------------------------------------------------------------------

def test_dotenv_never_logs_values(tmp_path, caplog):
    """The parser must never log the value of any key -- a future
    typo like ``logger.info('loaded %s', line)`` would echo the
    API key into stderr. Guard against that by requiring that no
    log record contains the sentinel value."""
    import logging
    sentinel = "sentinel-dotenv-no-log-9999"
    _write_env(tmp_path, f"WD_F3005_BASIC={sentinel}\n")
    with caplog.at_level(logging.DEBUG, logger="waggledance"):
        _load_dotenv(tmp_path / ".env")
    for record in caplog.records:
        assert sentinel not in record.getMessage(), (
            f"sentinel value leaked into log: {record.getMessage()!r}"
        )
