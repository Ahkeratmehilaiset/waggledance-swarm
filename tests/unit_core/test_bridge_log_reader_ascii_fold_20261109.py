"""ASCII JSON-key folding keeps the reader's existing validation contract."""
import json

import pytest

from waggledance.core import bridge_log_reader as reader


def test_validated_ascii_keys_do_not_use_the_character_loop(monkeypatch):
    def forbidden(_key):
        pytest.fail("validated ASCII key used the slow character loop")

    monkeypatch.setattr(reader, "_ascii_fold_bridge_key", forbidden)
    assert reader.parse_bridge_json_object('{"ABC":1,"nested":{"DeF":2}}') == {"ABC": 1, "nested": {"DeF": 2}}
    with pytest.raises(ValueError, match="ASCII"):
        reader._bridge_json_object([("Ä", 1)])


def test_all_ascii_pairs_match_the_original_fold():
    for left in range(128):
        for right in range(128):
            key = chr(left) + chr(right)
            assert key.lower() == reader._ascii_fold_bridge_key(key)
            assert reader._bridge_json_object([(key, 1)]) == {key: 1}


@pytest.mark.parametrize("raw", ['{"Name":1,"name":2}', '{"outer":{"A":1,"a":2}}', '{"Ä":1}'])
def test_duplicate_and_non_ascii_keys_still_refuse(raw):
    with pytest.raises(ValueError):
        reader.parse_bridge_json_object(raw)


def test_original_helper_still_folds_only_ascii():
    assert reader._ascii_fold_bridge_key("AZÄİ😀") == "azÄİ😀"
    assert reader.parse_bridge_json_object(json.dumps({"message": "Ä😀"}, ensure_ascii=False)) == {"message": "Ä😀"}
