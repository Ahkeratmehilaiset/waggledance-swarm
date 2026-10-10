# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from tools.release_soak_append_only import evaluate_soak_append_only


ONE = b'{"cycle":1,"status":"ok"}\n'
TWO = b'{"cycle":2,"status":"ok"}\n'


def test_explicit_empty_subject_allows_valid_current() -> None:
    assert evaluate_soak_append_only(b"", ONE) == []


def test_unchanged_valid_nonempty_stream_is_append_only() -> None:
    assert evaluate_soak_append_only(ONE, ONE) == []


@pytest.mark.parametrize(
    ("subject", "current"),
    [
        (ONE, ONE + TWO),
        (ONE, ONE.replace(b"\n", b"\r\n") + TWO.replace(b"\n", b"\r\n")),
        (ONE.replace(b"\n", b"\r\n"), ONE + TWO),
    ],
    ids=["lf", "current-crlf", "subject-crlf"],
)
def test_valid_append_accepts_lf_and_crlf_equivalence(
    subject: bytes, current: bytes
) -> None:
    assert evaluate_soak_append_only(subject, current) == []


@pytest.mark.parametrize("value", [None, bytearray(ONE), memoryview(ONE)])
def test_subject_requires_exact_bytes(value: object) -> None:
    assert evaluate_soak_append_only(value, ONE) == [  # type: ignore[arg-type]
        "soak_append_subject_type_invalid"
    ]


@pytest.mark.parametrize("value", [None, bytearray(ONE), memoryview(ONE)])
def test_current_requires_exact_bytes(value: object) -> None:
    assert evaluate_soak_append_only(b"", value) == [  # type: ignore[arg-type]
        "soak_append_current_type_invalid"
    ]


def test_each_blob_is_limited_to_16_mib() -> None:
    oversized = b"x" * (16 * 1024 * 1024 + 1)

    assert evaluate_soak_append_only(oversized, ONE) == [
        "soak_append_subject_too_large"
    ]
    assert evaluate_soak_append_only(b"", oversized) == [
        "soak_append_current_too_large"
    ]


def test_current_must_be_present_and_nonempty() -> None:
    assert evaluate_soak_append_only(b"", b"") == [
        "soak_append_current_empty"
    ]


@pytest.mark.parametrize(
    ("subject", "current", "blocker"),
    [
        (ONE.removesuffix(b"\n"), ONE, "soak_append_subject_final_lf_missing"),
        (b"", ONE.removesuffix(b"\n"), "soak_append_current_final_lf_missing"),
    ],
)
def test_nonempty_streams_require_a_final_lf(
    subject: bytes, current: bytes, blocker: str
) -> None:
    assert evaluate_soak_append_only(subject, current) == [blocker]


@pytest.mark.parametrize(
    ("subject", "current", "blocker"),
    [
        (b'{"message":"before"}\r{"message":"after"}\n', ONE,
         "soak_append_subject_bare_cr"),
        (b"", b'{"message":"before"}\r{"message":"after"}\n',
         "soak_append_current_bare_cr"),
    ],
)
def test_bare_cr_is_rejected(
    subject: bytes, current: bytes, blocker: str
) -> None:
    assert evaluate_soak_append_only(subject, current) == [blocker]


@pytest.mark.parametrize(
    ("subject", "current", "blocker"),
    [
        (b'{"message":"\xff"}\n', ONE, "soak_append_subject_utf8_invalid"),
        (b"", b'{"message":"\xff"}\n', "soak_append_current_utf8_invalid"),
    ],
)
def test_utf8_is_required(
    subject: bytes, current: bytes, blocker: str
) -> None:
    assert evaluate_soak_append_only(subject, current) == [blocker]


@pytest.mark.parametrize(
    ("subject", "current", "blocker"),
    [
        (b"\xef\xbb\xbf" + ONE, ONE, "soak_append_subject_bom"),
        (b"", b"\xef\xbb\xbf" + ONE, "soak_append_current_bom"),
    ],
)
def test_utf8_bom_is_rejected(
    subject: bytes, current: bytes, blocker: str
) -> None:
    assert evaluate_soak_append_only(subject, current) == [blocker]


@pytest.mark.parametrize(
    ("subject", "current", "blocker"),
    [
        (ONE + b"\n", ONE, "soak_append_subject_blank_line"),
        (b"", ONE + b"\n", "soak_append_current_blank_line"),
    ],
)
def test_blank_lines_are_rejected(
    subject: bytes, current: bytes, blocker: str
) -> None:
    assert evaluate_soak_append_only(subject, current) == [blocker]


@pytest.mark.parametrize(
    ("subject", "current", "blocker"),
    [
        (b"{not-json}\n", ONE, "soak_append_subject_json_invalid"),
        (b"", b"{not-json}\n", "soak_append_current_json_invalid"),
    ],
)
def test_each_line_must_be_complete_json(
    subject: bytes, current: bytes, blocker: str
) -> None:
    assert evaluate_soak_append_only(subject, current) == [blocker]


@pytest.mark.parametrize(
    ("subject", "current", "blocker"),
    [
        (b"[1,2,3]\n", ONE, "soak_append_subject_record_not_object"),
        (b"", b"[1,2,3]\n", "soak_append_current_record_not_object"),
    ],
)
def test_each_json_record_must_be_an_object(
    subject: bytes, current: bytes, blocker: str
) -> None:
    assert evaluate_soak_append_only(subject, current) == [blocker]


@pytest.mark.parametrize(
    ("subject", "current"),
    [
        (ONE, b'{"cycle":1,"status":"changed"}\n'),
        (ONE + TWO, ONE),
    ],
    ids=["in-place-edit", "truncation"],
)
def test_edit_or_truncation_is_not_append_only(
    subject: bytes, current: bytes
) -> None:
    assert evaluate_soak_append_only(subject, current) == [
        "soak_append_prefix_mismatch"
    ]


def test_incomplete_appended_record_is_rejected() -> None:
    assert evaluate_soak_append_only(ONE, ONE + b'{"cycle":2') == [
        "soak_append_current_final_lf_missing"
    ]


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_nonstandard_json_constants_are_rejected(constant: bytes) -> None:
    record = b'{"value":' + constant + b'}\n'
    assert evaluate_soak_append_only(b"", record) == [
        "soak_append_current_json_invalid"
    ]
