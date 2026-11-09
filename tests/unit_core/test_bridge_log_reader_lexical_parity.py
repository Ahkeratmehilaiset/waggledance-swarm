"""Synthetic differential and linear-scan tests for the bridge JSON lexical contract."""

import itertools
import json
import random

import pytest

from waggledance.core import bridge_log_reader as reader


def _legacy(text):
    depth = 0
    in_string = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            if char == '"':
                in_string = False
            elif char == "\\":
                if index + 1 >= len(text):
                    return
                escape = text[index + 1]
                if escape == "u" and index + 5 < len(text):
                    digits = text[index + 2:index + 6]
                    if all(digit in "0123456789abcdefABCDEF" for digit in digits):
                        if 0xD800 <= int(digits, 16) <= 0xDFFF:
                            raise ValueError("JSON UTF-16 surrogate escapes are forbidden")
                        index += 5
                    else:
                        index += 1
                else:
                    index += 1
            elif 0xD800 <= ord(char) <= 0xDFFF:
                raise ValueError("unpaired Unicode surrogate")
        else:
            if char == '"':
                in_string = True
            elif char in "[{":
                depth += 1
                if depth > 32:
                    raise ValueError("JSON nesting exceeds contract limit")
            elif char in "]}":
                depth -= 1
        index += 1


def _outcome(fn, text):
    try:
        fn(text)
    except ValueError as exc:
        return type(exc), str(exc)
    return None


def test_exhaustive_short_lexical_parity():
    alphabet = ('"', "\\", "{", "}", "[", "]", "u", "0", "a", "\ud800")
    for length in range(5):
        for value in itertools.product(alphabet, repeat=length):
            text = "".join(value)
            assert _outcome(reader._validate_json_lexical_contract, text) == _outcome(_legacy, text)


def test_random_lexical_parity_and_adversarial_boundaries():
    randomizer = random.Random(20261108)
    alphabet = 'abc123{}[]\\"un89f :\n\u00e4\U0001f600\ud800\udfff'
    cases = [
        '"\\ud800"', '"\\uDfff"', '"\\u12zz"', '"\\\\ud800"',
        '[' * 32 + ']' * 32, '[' * 33 + ']' * 33,
        '"' + chr(0xD800) + '"', '"' + chr(0xDFFF) + '"',
        '"x\\', '"\\u', '"\\ud80', '"\\ud800',
    ]
    cases.extend(''.join(randomizer.choices(alphabet, k=randomizer.randrange(160))) for _ in range(30000))
    for text in cases:
        assert _outcome(reader._validate_json_lexical_contract, text) == _outcome(_legacy, text)


def test_json_contract_still_rejects_duplicate_keys_and_nonfinite_numbers():
    for text in ('{"a":1,"A":2}', '{"a":NaN}', '{"a":9007199254740992}', '{"a":"\\ud800"}'):
        with pytest.raises(ValueError):
            reader.parse_bridge_json_object(text)
    assert reader.parse_bridge_json_object(json.dumps({"text": "😀 ä"}, ensure_ascii=False)) == {"text": "😀 ä"}


def test_uninteresting_string_does_not_require_per_character_python_indexing():
    class CountedString(str):
        accesses = 0

        def __getitem__(self, key):
            type(self).accesses += 1
            return super().__getitem__(key)

    text = CountedString('{"message":"' + 'a' * 40000 + '"}')
    reader._validate_json_lexical_contract(text)
    assert CountedString.accesses < 128
