"""Synthetic V2 cache corruption under the ordinary private-directory ACL."""
import hashlib
import json
import struct

import pytest

from test_wd_mind_vector_cache import binding, cache, private_dir


def test_verified_repair_is_limited_to_five_percent(private_dir):
    vector = struct.pack("<384f", 1.0, *([0.0] * 383))
    rows = tuple((f"{index:064x}", "b" * 64, vector) for index in range(20))
    path = private_dir / "vectors.bin"
    cache.write_cache(path, binding(), rows)
    pristine = path.read_bytes()
    assert pristine.startswith(b"WDMVC2\n")
    damaged = bytearray(pristine)
    damaged[-1] ^= 1
    path.write_bytes(damaged)
    with pytest.raises(cache.CacheMiss):
        cache.load_cache(path, binding(), lambda *_: True)
    result = cache.load_cache_for_repair(path, binding(), lambda *_: True)
    assert result.rows == rows[:-1]
    assert result.failed_keys == (rows[-1][:2],)
    assert path.read_bytes() == damaged
    damaged[-1 - len(vector)] ^= 1
    path.write_bytes(damaged)
    with pytest.raises(cache.WidespreadDamage, match="widespread_damage") as exc:
        cache.load_cache_for_repair(path, binding(), lambda *_: True)
    assert (exc.value.damaged_rows, exc.value.total_rows) == (2, 20)
    assert path.read_bytes() == damaged


def _distinct_rows(count):
    def vector(index):
        values = [0.0] * 384
        values[index] = 1.0
        return struct.pack("<384f", *values)
    return tuple((f"{i:064x}", f"{1000 + i:064x}", vector(i)) for i in range(count))


def _watch_reads(monkeypatch, before_read):
    original_fdopen = cache.os.fdopen

    class Tracked:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            before_read(self.stream.tell(), size)
            return self.stream.read(size)

    monkeypatch.setattr(cache.os, "fdopen", lambda fd, mode: Tracked(original_fdopen(fd, mode)))


def test_two_manifest_chunks_verified_before_reading_vectors(private_dir, monkeypatch):
    path, vector = private_dir / "vectors.bin", _distinct_rows(1)[0][2]
    rows = tuple((f"{i:064x}", "b" * 64, vector) for i in range(1025))
    cache.write_cache(path, binding(), rows)
    raw = bytearray(path.read_bytes())
    table_end = len(raw) - len(rows) * len(vector)
    raw[table_end - 1] ^= 1
    path.write_bytes(raw)
    reads = []
    _watch_reads(monkeypatch, lambda pos, size: reads.append((pos, size)))
    with pytest.raises(cache.CacheMiss, match="manifest"):
        cache.load_cache_for_repair(path, binding(), lambda *_: True)
    assert [size for _, size in reads[2:]] == [1024 * 96, 96]
    last_position, last_size = reads[-1]
    assert last_position + last_size == table_end


def test_checksums_do_not_authorize_numerically_invalid_vectors(private_dir):
    path, rows = private_dir / "vectors.bin", _distinct_rows(20)
    cache.write_cache(path, binding(), rows)
    invalid = (*rows[:-1], (*rows[-1][:2], bytes(1536)))
    header = cache._canonical(cache._header(binding(), invalid))
    path.write_bytes(cache._MAGIC + struct.pack("<I", len(header)) + header + b"".join(cache._encoded(invalid)))
    with pytest.raises(cache.CacheMiss, match="vector_norm"):
        cache.load_cache(path, binding(), lambda *_: True)
    result = cache.load_cache_for_repair(path, binding(), lambda *_: True)
    assert result.rows == rows[:-1]
    assert result.failed_keys == (rows[-1][:2],)


def test_swapped_vectors_both_identified_by_their_original_slot(private_dir):
    path, rows = private_dir / "vectors.bin", _distinct_rows(40)
    cache.write_cache(path, binding(), rows)
    intact = cache.load_cache_for_repair(path, binding(), lambda *_: True)
    assert intact.rows == rows and intact.failed_keys == ()
    damage = bytearray(path.read_bytes())
    size = len(rows[0][2])
    damage[-2 * size:] = damage[-size:] + damage[-2 * size:-size]
    path.write_bytes(damage)
    repaired = cache.load_cache_for_repair(path, binding(), lambda *_: True)
    assert repaired.rows == rows[:-2]
    assert repaired.failed_keys == tuple(row[:2] for row in rows[-2:])
    assert repaired.record_set_sha256 == intact.record_set_sha256
    assert path.read_bytes() == damage


@pytest.mark.parametrize("fault", ["manifest_bit", "duplicate", "reordered", "truncated",
                                   "manifest_bound_to_other_prefix", "record_set"])
def test_manifest_or_framing_damage_never_attributes_individual_rows(private_dir, fault):
    rows, path = _distinct_rows(40), private_dir / "vectors.bin"
    cache.write_cache(path, binding(), rows)
    raw = path.read_bytes()
    start = len(b"WDMVC2\n") + 4
    length = struct.unpack("<I", raw[start - 4:start])[0]
    header = json.loads(raw[start:start + length])
    table_end = start + length + len(rows) * 96
    table = bytearray(raw[start + length:table_end])
    if fault == "manifest_bit":
        table[64] ^= 1
    elif fault == "manifest_bound_to_other_prefix":
        header["binding"]["prefix_offset"] -= 1
    elif fault == "record_set":
        header["record_set_sha256"] = "0" * 64
    elif fault in ("duplicate", "reordered"):
        if fault == "duplicate":
            table[96:192] = table[:96]
        else:
            table[:192] = table[96:192] + table[:96]
        # Even a self-consistent digest cannot authorize duplicate/reordered identities.
        digest = cache._manifest_hasher(header["binding"], len(rows))
        digest.update(table)
        header["manifest_sha256"] = digest.hexdigest()
        header["record_set_sha256"] = hashlib.sha256(
            b"".join(table[i:i + 32] for i in range(0, len(table), 96))).hexdigest()
    encoded = cache._canonical(header)
    damaged = b"WDMVC2\n" + struct.pack("<I", len(encoded)) + encoded + table + raw[table_end:]
    if fault == "truncated":
        damaged = damaged[:-1]
    path.write_bytes(damaged)
    with pytest.raises(cache.CacheMiss, match="manifest|truncated") as exc:
        cache.load_cache_for_repair(path, binding(), lambda *_: True)
    assert not isinstance(exc.value, cache.WidespreadDamage)
    assert path.read_bytes() == damaged


def _legacy_file(rows):
    blocks = b"".join(bytes.fromhex(record + passage) + vector for record, passage, vector in rows)
    header = {"schema": "wd.mind-vector-cache.v1", "binding": binding(), "dimension": 384,
              "dtype": "f32le", "count": len(rows), "payload_sha256": hashlib.sha256(blocks).hexdigest(),
              "vectors_sha256": hashlib.sha256(b"".join(row[2] for row in rows)).hexdigest(),
              "record_set_sha256": hashlib.sha256(b"".join(bytes.fromhex(row[0]) for row in rows)).hexdigest()}
    encoded = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("ascii")
    return b"WDMVC1\n" + struct.pack("<I", len(encoded)) + encoded + blocks


def test_v1_reads_strictly_and_migrates_on_explicit_write(private_dir):
    rows, path = _distinct_rows(20), private_dir / "vectors.bin"
    cache.write_cache(path, binding(), rows)  # Create the test file with the verified user owner.
    legacy = _legacy_file(rows)
    path.write_bytes(legacy)
    assert cache.load_cache(path, binding(), lambda *_: True).rows == rows
    result = cache.load_cache_for_repair(path, binding(), lambda *_: True)
    assert result.rows == rows and result.failed_keys == ()
    assert path.read_bytes() == legacy
    path.write_bytes(legacy[:-1] + bytes([legacy[-1] ^ 1]))
    with pytest.raises(cache.CacheMiss, match="payload"):
        cache.load_cache_for_repair(path, binding(), lambda *_: True)
    cache.write_cache(path, binding(), result.rows)
    assert path.read_bytes().startswith(b"WDMVC2\n")
    assert cache.load_cache(path, binding(), lambda *_: True).rows == rows


@pytest.mark.parametrize("legacy", [False, True])
def test_owner_budget_refusal_precedes_all_manifest_and_row_allocation(private_dir, monkeypatch, legacy):
    rows, path = _distinct_rows(3), private_dir / "vectors.bin"
    cache.write_cache(path, binding(), rows)
    if legacy:
        path.write_bytes(_legacy_file(rows))
    pristine = path.read_bytes()
    events = []

    def verify(offset, digest):
        events.append(("prefix", offset, digest))
        return True

    def refuse(count):
        events.append(("admit", count))
        return False

    def unexpected(*args):
        pytest.fail("entered manifest or row allocation after budget refusal")

    monkeypatch.setattr(cache, "_read_v1", unexpected)
    monkeypatch.setattr(cache, "_read_v2", unexpected)
    with pytest.raises(cache.CacheRefused, match="row_admission") as exc:
        cache.load_cache_for_repair(path, binding(), verify, admit_rows=refuse)
    assert type(exc.value) is cache.CacheAdmissionRefused
    assert events == [("prefix", 100, "3" * 64), ("admit", 3)]
    assert path.read_bytes() == pristine


@pytest.mark.parametrize("fault", ["read", "prefix"])
def test_late_hard_failure_cannot_become_a_repair_or_rebuild(private_dir, monkeypatch, fault):
    path, rows = private_dir / "vectors.bin", _distinct_rows(19)
    cache.write_cache(path, binding(), rows)
    raw = bytearray(path.read_bytes())
    raw[-len(rows[0][2]) * len(rows)] ^= 1  # 1 / 19 is already beyond the repair limit.
    path.write_bytes(raw)
    def fail_last_row(position, size):
        if position == len(raw) - 1536:
            raise OSError("synthetic device error")

    if fault == "read":
        _watch_reads(monkeypatch, fail_last_row)
    calls = []
    def verify(*_):
        calls.append(True)
        if fault == "prefix" and len(calls) == 2:
            raise OSError("synthetic source read error")
        return True
    with pytest.raises(cache.CacheRefused, match="read") as exc:
        cache.load_cache_for_repair(path, binding(), verify)
    assert not isinstance(exc.value, cache.CacheMiss)
    assert path.read_bytes() == raw
