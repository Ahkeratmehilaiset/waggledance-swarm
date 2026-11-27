"""Owner-side selective recovery with real source screening and a counted encoder."""
import json
import struct

import pytest

from test_wd_mind_cache_builder_integration import VECTOR, fixtures, synthetic
from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_vector_cache as cache


def _seed_and_damage(case, damaged):
    with case.source.open("ab") as stream:
        for index in range(38):
            stream.write(fixtures._raw(fixtures._row(f"synthetic accepted task {index}")) + b"\n")
    binding, projection = case.snapshot()
    assert len(projection["messages"]["records"]) == 40
    initial = case.build(binding)
    assert len(case.passages) == 40
    case.passages.clear()
    pristine = case.cache_path.read_bytes()
    raw = bytearray(pristine)
    for slot in range(damaged):
        raw[-1 - slot * len(VECTOR)] ^= 1
    case.cache_path.write_bytes(raw)
    return initial, pristine


@pytest.mark.parametrize(("failed", "encoded", "widespread"), [(1, 1, 0), (2, 2, 0), (3, 40, 1)])
def test_corrupt_vectors_reencoded_from_screened_source(synthetic, failed, encoded, widespread):
    initial, pristine = _seed_and_damage(synthetic, failed)
    binding, _ = synthetic.snapshot()
    recovered = synthetic.build(binding)
    assert len(set(synthetic.passages)) == len(synthetic.passages) == encoded
    assert recovered.ids == initial.ids and recovered.vectors == initial.vectors
    assert synthetic.cache_path.read_bytes() == pristine
    receipt = dict(recovered.cache_repair)
    duration = receipt.pop("repair_seconds")
    assert isinstance(duration, (int, float)) and duration >= 0
    assert receipt == dict(checked_rows=40, failed_rows=failed, reencoded_rows=encoded,
                           widespread_rebuilds=widespread)
    synthetic.passages.clear()
    binding, _ = synthetic.snapshot()
    assert synthetic.build(binding).cache_status == "HIT_STORED"
    assert synthetic.passages == []


def test_memory_drop_after_reencoding_preserves_previous_cache(synthetic, monkeypatch):
    _seed_and_damage(synthetic, 1)
    previous = synthetic.cache_path.read_bytes()
    binding, _ = synthetic.snapshot()
    monkeypatch.setattr(runtime, "_owner_memory_headroom",
                        lambda: 0 if synthetic.passages else 1024**3)
    with pytest.raises(ValueError, match="(?i)memory_budget"):
        synthetic.build(binding)
    assert len(synthetic.passages) == 1
    assert synthetic.cache_path.read_bytes() == previous


def test_failed_passage_identity_is_checked_against_screened_source(synthetic):
    _, pristine = _seed_and_damage(synthetic, 0)
    header_start = len(cache._MAGIC) + 4
    size = struct.unpack("<I", pristine[header_start - 4:header_start])[0]
    binding = json.loads(pristine[header_start:header_start + size])["binding"]
    rows = cache.load_cache(synthetic.cache_path, binding, lambda *_: True).rows
    record_id, original_hash, vector = rows[-1]
    wrong_hash = "a" * 64
    assert wrong_hash != original_hash
    cache.write_cache(synthetic.cache_path, binding, (*rows[:-1], (record_id, wrong_hash, vector)))
    corrupt = bytearray(synthetic.cache_path.read_bytes())
    corrupt[-1] ^= 1
    synthetic.cache_path.write_bytes(corrupt)
    current, _ = synthetic.snapshot()
    assert synthetic.build(current).cache_status == "MISS_STORED"
    assert len(synthetic.passages) == 40
    assert synthetic.cache_path.read_bytes() == pristine


def test_repair_reencodes_damaged_and_new_passages(synthetic):
    _seed_and_damage(synthetic, 1)
    new_message = "synthetic task appended after damage"
    with synthetic.source.open("ab") as stream:
        stream.write(fixtures._raw(fixtures._row(new_message)) + b"\n")
    current, _ = synthetic.snapshot()
    repaired = synthetic.build(current)
    assert len(repaired.ids) == 41 and len(synthetic.passages) == 2
    assert f"passage: {new_message}" in synthetic.passages
    assert repaired.cache_repair["reencoded_rows"] == 2


@pytest.mark.parametrize("fault", ["source_mutation", "replace_failure"])
def test_interrupted_repair_preserves_original_cache(synthetic, monkeypatch, fault):
    _seed_and_damage(synthetic, 1)
    before = synthetic.cache_path.read_bytes()
    current, _ = synthetic.snapshot()
    def encode(values):
        synthetic.passages.extend(values)
        if fault == "source_mutation":
            raw = synthetic.source.read_bytes()
            changed = raw.replace(b"accepted task 0", b"modified task 0", 1)
            assert raw != changed
            synthetic.source.write_bytes(changed)
        return VECTOR * len(values)

    if fault == "replace_failure":
        def refuse_replace(*args):
            raise OSError("synthetic publication fault")

        monkeypatch.setattr(cache.os, "replace", refuse_replace)

    with pytest.raises(ValueError):
        runtime._build_flat_memory_immutable(current, encode, synthetic.encoder,
                                             fixtures._sha(synthetic.encoder), 5, None,
                                             vector_cache=synthetic.adapter)
    assert len(synthetic.passages) == 1
    assert synthetic.cache_path.read_bytes() == before
