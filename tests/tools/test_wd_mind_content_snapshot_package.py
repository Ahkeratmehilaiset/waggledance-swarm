"""Synthetic-only producer contract; no native index, model or live source."""
import importlib.util
import builtins
import json
import struct
import sys
from pathlib import Path

import pytest

from tools import wd_mind_content_query_readonly as reader


ROOT = Path(__file__).resolve().parents[2]
CAPACITY_PROFILE = "bounded_v3_capacity_v1"


@pytest.fixture(scope="module")
def fixtures():
    path = ROOT / "tests/tools/test_wd_mind_content_query_readonly.py"
    spec = importlib.util.spec_from_file_location("snapshot_package_source_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def package():
    path = ROOT / "tools/wd_mind_content_snapshot_package.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("snapshot_package_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Codec:
    """A literal float32 byte transport, deliberately not a FAISS implementation."""
    def __init__(self, fixtures):
        self.fixtures = fixtures
        self.passages = []
        self.serialized = []
        self.decoded = []

    def passage(self, texts):
        self.passages.append(texts)
        rows = []
        for text in texts:
            assert text.startswith("passage: ")
            head = (0.0, 1.0) if text[9:] == self.fixtures.SEMANTIC_TEXTS[0] else (1.0, 0.0)
            rows.append(struct.pack("<384f", *head, *([0.0] * 382)))
        return b"".join(rows)

    def serialize(self, raw, dim):
        assert type(raw) is bytes and dim == 384
        self.serialized.append(raw)
        return b"synthetic-index-f32:" + raw

    def decode(self, raw):
        self.decoded.append(raw)
        assert raw.startswith(b"synthetic-index-f32:")
        vectors = list(struct.iter_unpack("<384f", raw[len(b"synthetic-index-f32:"):]))
        return self.fixtures._SyntheticFlatIP(vectors)


class ChunkCodec(Codec):
    """Fake native index that refuses an unbounded reconstruction request."""
    def __init__(self, fixtures, *, fault=None):
        super().__init__(fixtures)
        self.reconstructions = []
        self.fault = fault

    def decode(self, raw):
        index = super().decode(raw)
        cell_number, reconstruct = len(self.decoded), index.reconstruct_n

        def bounded(start, count):
            self.reconstructions.append((cell_number, start, count))
            if count > 128:
                raise ValueError("synthetic full reconstruction refused")
            rows = reconstruct(start, count)
            if self.fault is not None:
                rows = self.fault(cell_number, index, start, count, rows)
            return rows

        index.reconstruct_n = bounded
        return index


def _chunk_case(fixtures, tmp_path, count=65, *, three_cells=False):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    rows = [fixtures._row("synthetic network retry", task_id=f"synthetic-task-{i}",
                          type="done" if three_cells else "message") for i in range(count)]
    if three_cells:
        rows.extend((fixtures._row("synthetic general record", type="message"),
                     fixtures._row("synthetic learning record", type="test")))
    source = fixtures._all_tasks_case(reader, tmp_path, rows)
    if three_cells:
        policy = json.loads(source["messages"]["policy_bytes"])
        policy["types"].append("test")
        raw = fixtures._raw(policy)
        source["messages"].update(policy_bytes=raw, policy_sha256=reader._sha(raw))
        fixtures._edit_decision(source, message_policy_sha256=reader._sha(raw))
    case.update(fixtures._snapshot(source, reader.rederive_message_projection(**source)))
    return case


def test_named_capacity_profile_accepts_257_records_without_changing_b16(package, fixtures, tmp_path):
    case = _chunk_case(fixtures, tmp_path, 257)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    _refuses(package, args)
    assert codec.passages == codec.serialized == codec.decoded == []
    output = package.package_content_snapshot(**args, capacity_profile=CAPACITY_PROFILE)
    assert json.loads(output["memory_bytes"])["records"] == 257
    assert list(map(len, codec.passages)) == [16] * 16 + [1]
    assert _query(case, output, Codec(fixtures))["status"] == "ANSWER"


def test_capacity_builds_over_2mib_cell_and_supports_small_query_full_abi(package, fixtures, tmp_path):
    case = _chunk_case(fixtures, tmp_path, 683)
    codec = ChunkCodec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec, CAPACITY_PROFILE))
    cell = output["cell_snapshots"]["general"]["artifacts"]
    assert len(cell["vectors"]) == 683 * 2 * 1536 > 2097152
    assert len(cell["index"]) > 2097152
    assert max(count for _, _, count in codec.reconstructions) == 128
    assert [len(batch) for batch in codec.passages] == [16] * 42 + [11]
    query_codec, calls = Codec(fixtures), []
    original = query_codec.decode
    def decoded(raw):
        index = original(raw)
        reconstruct = index.reconstruct_n
        def observed(start, count):
            calls.append((start, count))
            return reconstruct(start, count)
        index.reconstruct_n = observed
        return index
    query_codec.decode = decoded
    assert _query(case, output, query_codec)["status"] == "ANSWER"
    assert calls == [(0, 1366)]  # Existing query threshold is unchanged.


def test_capacity_accepts_verified_prefix_over_default_4mib(package, fixtures, tmp_path):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    rows = [fixtures._row("synthetic network retry", task_id=f"synthetic-task-{i}",
                           payload={"padding": "x" * (3 * 1024 * 1024)}) for i in range(2)]
    source = fixtures._all_tasks_case(reader, tmp_path, rows)
    case.update(fixtures._snapshot(source, reader.rederive_message_projection(**source)))
    assert 4194304 < case["messages"]["end_cursor"].offset < 268435456
    codec = Codec(fixtures)
    _refuses(package, _arguments(case, codec))
    assert codec.passages == []
    output = package.package_content_snapshot(**_arguments(case, codec, CAPACITY_PROFILE))
    assert json.loads(output["memory_bytes"])["records"] == 2
    assert [len(batch) for batch in codec.passages] == [2]


@pytest.mark.parametrize("profile", [False, True, 0, 1, "", "large", "bounded_v3", [], {}])
def test_unknown_capacity_profiles_refuse_before_source_or_providers(package, monkeypatch, profile):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid profile touched source or provider")
    monkeypatch.setattr(package, "_snapshot", forbidden)
    _refuses(package, dict(enabled=True, capacity_profile=profile, snapshot=object(),
                           passage_encoder=forbidden, index_serializer=forbidden, index_decoder=forbidden))


def test_capacity_profile_must_be_exact_builtin_string(package, monkeypatch):
    class Derived(str):
        pass
    def forbidden(*args, **kwargs):
        pytest.fail("invalid profile touched source")
    monkeypatch.setattr(package, "_snapshot", forbidden)
    _refuses(package, dict(enabled=True, capacity_profile=Derived(CAPACITY_PROFILE), snapshot=object()))


def test_capacity_profile_limits_are_fixed_and_disjoint_from_default(package):
    assert package._capacity(None) is None
    assert package._capacity(CAPACITY_PROFILE) == {
        "records": 32768, "prefix": 268435456, "projection": 67108864,
        "text": 9830400, "artifact": 50331648, "package": 536870912,
        "index_overhead": 65536, "control": 65536}
    assert (package.MAX_RECORDS, package.MAX_PREFIX_BYTES, package.MAX_PROJECTION_BYTES,
            package.MAX_TEXT_BYTES, package.MAX_ARTIFACT_BYTES, package.MAX_PACKAGE_BYTES,
            package.PASSAGE_BATCH) == (256, 4194304, 2097152, 524288, 2097152, 8388608, 16)


@pytest.mark.parametrize("fault", ["depth", "nodes", "utf8", "nul", "size"])
def test_capacity_projection_allocation_refusals_precede_json_parse(package, fixtures, tmp_path, monkeypatch, fault):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    if fault == "depth":
        raw = b"[" * 17 + b"0" + b"]" * 17
    elif fault == "nodes":
        raw = b"[[]" + b",[]" * (256 + 64 * 32768) + b"]"
    elif fault == "utf8":
        raw = b'{"invalid":"\xff"}'
    elif fault == "nul":
        raw = '{"invalid":0}'.encode("utf-16-le")
    else:
        raw = b"x" * (67108864 + 1)
    codec = Codec(fixtures)
    args = _arguments(case, codec, CAPACITY_PROFILE)
    args["snapshot"].update(projection_bytes=raw, projection_sha256=reader._sha(raw))
    original = reader._pinned
    def pinned(data, *values, **kwargs):
        if data is raw:
            pytest.fail("projection reached JSON parser before allocation guard")
        return original(data, *values, **kwargs)
    monkeypatch.setattr(reader, "_pinned", pinned)
    _refuses(package, args)
    assert codec.passages == codec.serialized == codec.decoded == []


@pytest.mark.parametrize("count", [32768, 32769])
@pytest.mark.parametrize("cell_count", [1, 3])
def test_capacity_record_limit_is_global_before_metadata_allocation(package, monkeypatch, count, cell_count):
    calls = []
    def metadata(records, cell_id, limit):
        calls.append(len(records))
        return [], b"", b""
    monkeypatch.setattr(package, "_cell_metadata", metadata)
    sizes = [count // cell_count + (i < count % cell_count) for i in range(cell_count)]
    cells = {key: [None] * size for key, size in zip(("general", "learning", "system"), sizes)}
    capacity = package._capacity(CAPACITY_PROFILE)
    if count == 32769:
        with pytest.raises(ValueError, match="invalid_cell_records"):
            package._capacity_preflight(cells, count, 0, capacity)
        assert calls == []
    else:
        assert set(package._capacity_preflight(cells, count, 0, capacity)) == set(cells)
        assert sum(calls) == 32768
    # This is preflight arithmetic coverage, not a full-scale source/model run.


def test_capacity_projection_budget_accepts_real_32768_record_source_shape(package, fixtures, tmp_path):
    # This validates the genuinely source-derived maximum-count projection,
    # not 2048 passage callbacks or a full-scale index/model build.
    rows = [fixtures._row("synthetic work", task_id=f"synthetic-task-{i}") for i in range(32768)]
    source = fixtures._all_tasks_case(reader, tmp_path, rows)
    projection = reader.rederive_message_projection(**source)
    assert len(projection["messages"]["records"]) == 32768
    snapshot = fixtures._snapshot(source, projection)
    assert 2097152 < len(snapshot["projection_bytes"]) <= 67108864
    assert package._snapshot(snapshot, package._capacity(CAPACITY_PROFILE)) == snapshot
    assert reader.validate_content_snapshot(**snapshot)["status"] == "MATCH"


def test_capacity_projection_grammar_still_uses_authoritative_parser(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, CAPACITY_PROFILE)
    raw = b'{"messages":[}'
    args["snapshot"].update(projection_bytes=raw, projection_sha256=reader._sha(raw))
    original, parsed = reader._pinned, []
    def pinned(data, *values, **kwargs):
        if data is raw:
            parsed.append(True)
        return original(data, *values, **kwargs)
    def forbidden(*values):
        pytest.fail("malformed projection reached source derivation")
    monkeypatch.setattr(reader, "_pinned", pinned)
    monkeypatch.setattr(reader, "_projection", forbidden)
    _refuses(package, args)
    assert parsed == [True] and codec.passages == codec.serialized == codec.decoded == []


def test_capacity_projection_pin_precedes_scan_and_default_does_not_add_scan(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, CAPACITY_PROFILE)
    def forbidden(*values):
        pytest.fail("unexpected projection scan")
    monkeypatch.setattr(reader, "_q4_json_budget", forbidden)
    assert package._snapshot(args["snapshot"]) == args["snapshot"]
    args["snapshot"]["projection_sha256"] = "0" * 64
    _refuses(package, args)
    assert codec.passages == codec.serialized == codec.decoded == []


def test_capacity_prefix_limit_is_checked_before_source_access(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    snapshot = _arguments(case, Codec(fixtures))["snapshot"]
    capacity = package._capacity(CAPACITY_PROFILE)
    for offset in (268435456, 268435457):
        decision = json.loads(snapshot["decision_bytes"])
        decision["source_prefix"]["offset"] = offset
        snapshot["decision_bytes"] = fixtures._raw(decision)
        snapshot["decision_sha256"] = reader._sha(snapshot["decision_bytes"])
        if offset == 268435456:
            assert package._snapshot(snapshot, capacity) == snapshot
        else:
            def forbidden(*args):
                pytest.fail("oversized prefix reached source verification")
            monkeypatch.setattr(reader, "_verified", forbidden)
            codec = Codec(fixtures)
            args = _arguments(case, codec)
            args.update(snapshot=snapshot, capacity_profile=CAPACITY_PROFILE)
            _refuses(package, args)
            assert codec.passages == codec.serialized == codec.decoded == []


def test_capacity_metadata_counts_utf8_and_preserves_exact_bytes(package, fixtures, tmp_path):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    record = json.loads(case["projection_bytes"])["messages"]["records"][0]
    record["stored_text"] = reader.encode_text('synthetic r\u00e9seau "quoted" \\ path {brackets}')
    ids, meta, provenance = package._cell_metadata([record], "general", 50331648)
    assert meta == reader._canonical({"dim": 384, "doc_ids": ids,
        "texts": [reader.decode_text(record["stored_text"])] * 2, "metadata": [{}, {}]})
    row = struct.pack("<384f", 0.6, 0.8, -0.0, *([0.0] * 381))
    codec = Codec(fixtures)
    old, _ = package._cell([record], "general", {record["record_id"]: row},
        json.loads(case["encoder_binding_bytes"])["encoder"], case["projection_sha256"],
        fixtures.AVAILABLE, codec.serialize, lambda: None)
    assert (meta, provenance) == (old["artifacts"]["meta"], old["artifacts"]["provenance"])
    exact = max(len(meta), len(provenance))
    assert package._cell_metadata([record], "general", exact) == (ids, meta, provenance)
    with pytest.raises(ValueError, match="artifact_over_budget"):
        package._cell_metadata([record], "general", exact - 1)


def test_capacity_48mib_append_boundary_does_not_allocate_over_budget(package):
    class Counter:
        def __init__(self, size):
            self.size, self.writes = size, []
        def tell(self):
            return self.size
        def write(self, raw):
            self.writes.append(raw)
            self.size += len(raw)
    limit = 50331648
    exact = Counter(limit - 2)
    package._append_bounded(exact, "\u00e4".encode(), limit)
    assert exact.size == limit
    too_large = Counter(limit - 1)
    with pytest.raises(ValueError, match="artifact_over_budget"):
        package._append_bounded(too_large, "\u00e4".encode(), limit)
    assert too_large.writes == []


@pytest.mark.parametrize("fault", ["artifact", "aggregate"])
def test_capacity_last_cell_budget_refuses_before_first_provider(package, fixtures, tmp_path, monkeypatch, fault):
    case = _chunk_case(fixtures, tmp_path, three_cells=True)
    records = json.loads(case["projection_bytes"])["messages"]["records"]
    cells = {}
    for record in records:
        cells.setdefault(reader._Q4_PLACEMENT[record["meta"]["type"]], []).append(record)
    capacity = package._capacity(CAPACITY_PROFILE)
    original = package._cell_metadata
    prepared = {key: original(rows, key, capacity["artifact"]) for key, rows in cells.items()}
    if fault == "artifact":
        capacity["artifact"] = max(len(raw) for key in ("general", "learning") for raw in prepared[key][1:])
    else:
        base = len(case["projection_bytes"]) + len(case["encoder_binding_bytes"]) + len(case["index_binding_bytes"])
        reserved = base + capacity["control"] + sum(
            len(rows) * 2 * 1536 * 2 + capacity["index_overhead"] + capacity["control"]
            + sum(map(len, prepared[key][1:])) for key, rows in cells.items())
        capacity["package"] = reserved
        assert set(package._capacity_preflight(cells, len(records), base, capacity)) == set(cells)
        capacity["package"] = reserved - 1
    visited = []
    def observed(rows, key, limit):
        visited.append(key)
        return original(rows, key, limit)
    monkeypatch.setattr(package, "_capacity", lambda profile: capacity)
    monkeypatch.setattr(package, "_cell_metadata", observed)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    args["capacity_profile"] = CAPACITY_PROFILE
    _refuses(package, args)
    assert visited == ["general", "learning", "system"]
    assert codec.passages == codec.serialized == codec.decoded == []


@pytest.mark.parametrize("extra", [0, 1])
def test_capacity_actual_index_size_must_fit_row_based_reservation(package, fixtures, tmp_path, extra):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    def serialized(raw, dim):
        codec.serialized.append(raw)
        return raw + b"x" * (65536 + extra)
    def decoded(raw):
        codec.decoded.append(raw)
        return fixtures._SyntheticFlatIP(list(struct.iter_unpack("<384f", raw[:-65536])))
    args.update(capacity_profile=CAPACITY_PROFILE, index_serializer=serialized, index_decoder=decoded)
    if extra:
        _refuses(package, args)
        assert codec.decoded == []
    else:
        output = package.package_content_snapshot(**args)
        assert len(output["cell_snapshots"]) == 2
        assert len(codec.decoded) == 2


def test_capacity_keeps_full_semantic_source_recheck_after_callback(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    def changed(texts):
        result = codec.passage(texts)
        monkeypatch.setattr(reader, "_ALL_TASK_MAX_RECORDS", 0)
        return result
    args.update(capacity_profile=CAPACITY_PROFILE, passage_encoder=changed)
    _refuses(package, args)
    assert codec.passages and codec.serialized == codec.decoded == []


@pytest.mark.parametrize("count", [1, 64, 65, 128, 256])
def test_producer_reconstructs_v3_in_exact_bounded_ranges(package, fixtures, tmp_path, count):
    case = _chunk_case(fixtures, tmp_path, count)
    codec = ChunkCodec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec))
    assert json.loads(output["memory_bytes"])["records"] == count
    assert codec.reconstructions == [(1, start, min(128, count * 2 - start))
                                     for start in range(0, count * 2, 128)]
    if count == 65:
        # The unchanged reader uses full reconstruction below its v3 size
        # threshold, so its provider must support that ABI as well as chunks.
        result = _query(case, output, Codec(fixtures))
        assert result["status"] == "ANSWER"
        assert result["schema"] == "wd.q4-content-snapshot-query.v3"


@pytest.mark.parametrize("fault", ["last_bits", "negative_zero", "nan", "norm", "dimension",
                                  "short", "long", "controls", "exception"])
@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE])
def test_last_cell_last_chunk_refuses_without_partial_package(package, fixtures, tmp_path, capsys, fault, capacity_profile):
    case = _chunk_case(fixtures, tmp_path, three_cells=True)

    def corrupt(cell_number, index, start, count, rows):
        if cell_number != 3 or start != 128:
            return rows
        if fault == "exception":
            raise MemoryError("synthetic-private-provider-error")
        if fault == "controls":
            index.ntotal += 1
            return rows
        if fault == "short":
            return rows[:-1]
        if fault == "long":
            return rows + rows[:1]
        last = list(rows[-1])
        if fault == "last_bits":
            last[0], last[1] = 0.0, 1.0
        elif fault == "negative_zero":
            last[2] = 0.0
        elif fault == "nan":
            last[0] = float("nan")
        elif fault == "norm":
            last[0] = 2.0
        else:
            last.pop()
        return rows[:-1] + (tuple(last),)

    codec = ChunkCodec(fixtures, fault=corrupt)
    args = _arguments(case, codec, capacity_profile)
    row = struct.pack("<384f", 1.0, 0.0, -0.0, *([0.0] * 381))
    args["passage_encoder"] = lambda texts: row * len(texts)
    _refuses(package, args)
    assert codec.reconstructions == [(1, 0, 2), (2, 0, 2), (3, 0, 128), (3, 128, 2)]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("mutation", ["source", "generation", "input"])
@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE])
def test_last_reconstruction_source_mutation_is_checked_before_return(package, fixtures, tmp_path, mutation, capacity_profile):
    case = _chunk_case(fixtures, tmp_path, three_cells=True)

    def change(cell_number, index, start, count, rows):
        if cell_number == 3 and start == 128:
            if mutation == "source":
                path = Path(case["messages"]["source_path"])
                path.write_bytes(path.read_bytes().replace(b"network retry", b"changed retry", 1))
            elif mutation == "generation":
                Path(case["messages"]["generation_path"]).write_bytes(b'{"generation":"synthetic-g2"}')
            else:
                args["snapshot"]["decision_sha256"] = "0" * 64
        return rows

    codec = ChunkCodec(fixtures, fault=change)
    args = _arguments(case, codec, capacity_profile)
    _refuses(package, args)
    assert codec.reconstructions[-1] == (3, 128, 2)


@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE])
def test_last_cell_pair_digest_checked_even_when_retained_bytes_match(package, fixtures, tmp_path, monkeypatch, capacity_profile):
    case = _chunk_case(fixtures, tmp_path, three_cells=True)
    codec = ChunkCodec(fixtures)
    original = reader._q4_cell_controls

    def changed(*args, **kwargs):
        checked = original(*args, **kwargs)
        if args[0] == "system":
            # Isolate the decoded-pair check from the retained-byte check.
            checked[1]["pairs_sha256"] = "0" * 64
        return checked

    monkeypatch.setattr(reader, "_q4_cell_controls", changed)
    _refuses(package, _arguments(case, codec, capacity_profile))
    assert codec.reconstructions[-1] == (3, 128, 2)


@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE])
def test_last_cell_preflight_still_precedes_every_decoder(package, fixtures, tmp_path, monkeypatch, capacity_profile):
    case = _chunk_case(fixtures, tmp_path, three_cells=True)
    codec = ChunkCodec(fixtures)
    original = package._cell

    def tampered(*args, **kwargs):
        item, pointer = original(*args, **kwargs)
        if args[1] == "system":
            item["artifacts"]["provenance"] += b"synthetic-tamper"
        return item, pointer

    monkeypatch.setattr(package, "_cell", tampered)
    _refuses(package, _arguments(case, codec, capacity_profile))
    assert len(codec.serialized) == 3 and codec.decoded == []


@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE])
def test_full_only_decoder_has_no_large_reconstruction_fallback(package, fixtures, tmp_path, capacity_profile):
    case = _chunk_case(fixtures, tmp_path)
    codec = Codec(fixtures)
    calls = []

    def full_only(raw):
        index = codec.decode(raw)
        original = index.reconstruct_n

        def reconstruct(start, count):
            calls.append((start, count))
            if start != 0 or count != index.ntotal:
                raise ValueError("synthetic full-only provider")
            return original(start, count)

        index.reconstruct_n = reconstruct
        return index

    args = _arguments(case, codec, capacity_profile)
    args["index_decoder"] = full_only
    _refuses(package, args)
    assert calls == [(0, 128)]


def test_capacity_profile_preserves_small_artifacts_and_every_checkpoint(package, fixtures, tmp_path, monkeypatch):
    case = _chunk_case(fixtures, tmp_path, three_cells=True)
    original = reader._verified
    events = []
    def verified(*args):
        events.append("verified")
        return original(*args)
    monkeypatch.setattr(reader, "_verified", verified)
    outputs, traces = [], []
    for options in ({}, {"capacity_profile": None}, {"capacity_profile": CAPACITY_PROFILE}):
        events.clear()
        codec = Codec(fixtures)
        args = _arguments(case, codec)
        args.update(options)
        for name in ("passage_encoder", "index_serializer", "index_decoder"):
            callback = args[name]
            def observed(*values, callback=callback, name=name):
                events.append(name)
                return callback(*values)
            args[name] = observed
        outputs.append(package.package_content_snapshot(**args))
        traces.append(list(events))
    assert outputs[0] == outputs[1] == outputs[2]
    assert traces[0] == traces[1] == traces[2]
    assert traces[0].count("verified") == 16
    assert traces[0].count("passage_encoder") == 5
    assert traces[0].count("index_serializer") == traces[0].count("index_decoder") == 3


def test_bounded_decode_preserves_artifacts_and_source_checkpoint_order(package, fixtures, tmp_path, monkeypatch):
    case = _chunk_case(fixtures, tmp_path, three_cells=True)
    events = []
    original = reader._verified

    def verified(*args):
        events.append("verified")
        return original(*args)

    monkeypatch.setattr(reader, "_verified", verified)

    def build(codec):
        args = _arguments(case, codec)
        for name in ("passage_encoder", "index_serializer", "index_decoder"):
            callback = args[name]

            def observed(*values, callback=callback, name=name):
                events.append(name)
                return callback(*values)

            args[name] = observed
        return package.package_content_snapshot(**args)

    codec = ChunkCodec(fixtures)
    bounded = build(codec)
    bounded_events = list(events)
    assert codec.reconstructions == [(1, 0, 2), (2, 0, 2), (3, 0, 128), (3, 128, 2)]
    events.clear()
    # Same verified source/artifacts through the previous validation routine.
    monkeypatch.setattr(reader, "_q4_decode_cells_bounded_v3", reader._q4_decode_cells)
    legacy = build(Codec(fixtures))
    assert legacy == bounded
    expected = (["verified"] + ["passage_encoder", "verified"] * 5
                + ["index_serializer", "verified"] * 3
                + ["verified", "index_decoder", "verified"] * 3 + ["verified"])
    assert events == bounded_events == expected
    assert bounded_events.count("verified") == 16


def _arguments(case, codec, capacity_profile=None):
    return dict(enabled=True, snapshot={key: case[key] for key in reader._Q4_SOURCE_KEYS},
                passage_encoder=codec.passage, index_serializer=codec.serialize, index_decoder=codec.decode,
                **({"capacity_profile": capacity_profile} if capacity_profile is not None else {}),
                **{key: case[key] for key in ("encoder_binding_bytes", "encoder_binding_sha256",
                                             "index_binding_bytes", "index_binding_sha256")})


def _query(case, output, codec):
    encoder, index = reader.adapt_readonly_providers(
        encoder=case["encoder_provider"], index_decoder=codec.decode,
        **{key: output[key] for key in ("encoder_binding_bytes", "encoder_binding_sha256",
                                       "index_binding_bytes", "index_binding_sha256")},
        index_sha256s=[reader._sha(cell["artifacts"]["index"]) for cell in output["cell_snapshots"].values()],
        allow_bounded_v3=True)
    return reader.query_content_snapshot(
        **{key: case[key] for key in reader._Q4_SOURCE_KEYS}, **output,
        encoder_provider=encoder, index_provider=index,
        stream=codec.fixtures._ObservedStream(codec.fixtures._raw(
            {"situation": "retry network outage", "time": codec.fixtures.QUERY_TIME})))


def test_two_source_snapshots_round_trip_without_changing_first(package, fixtures, tmp_path):
    assert package is not None, "v3 in-memory snapshot producer is not implemented"
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = fixtures._semantic_case(reader, first_dir, all_tasks=True)
    second = fixtures._semantic_case(reader, second_dir, all_tasks=True)
    # A new complete, independently pinned synthetic source; no live cursor is advanced.
    rows = [fixtures._row(text, type="message" if i < 2 else "done",
                          task_id=f"synthetic-other-task-{i}")
            for i, text in enumerate((*fixtures.SEMANTIC_TEXTS, "new synthetic network retry repair"))]
    source = fixtures._all_tasks_case(reader, second_dir, rows)
    second.update(fixtures._snapshot(source, reader.rederive_message_projection(**source)))
    codec = Codec(fixtures)
    first_output = package.package_content_snapshot(**_arguments(first, codec))
    frozen = json.dumps(first_output, default=lambda raw: raw.hex(), sort_keys=True)
    first_result = _query(first, first_output, codec)
    second_output = package.package_content_snapshot(**_arguments(second, codec))
    second_result = _query(second, second_output, codec)
    assert first_result["status"] == second_result["status"] == "ANSWER"
    assert first_result["schema"] == second_result["schema"] == "wd.q4-content-snapshot-query.v3"
    assert first_result["authority"] == second_result["authority"] == "none"
    assert first_output["memory_sha256"] != second_output["memory_sha256"]
    assert json.loads(first_output["memory_bytes"])["records"] == 4
    assert json.loads(second_output["memory_bytes"])["records"] == 5
    assert json.dumps(first_output, default=lambda raw: raw.hex(), sort_keys=True) == frozen
    assert _query(first, first_output, codec) == first_result


def _refuses(package, args):
    with pytest.raises(package.SnapshotPackageRefused) as caught:
        package.package_content_snapshot(**args)
    assert str(caught.value) == "content_snapshot_package_refused"
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize("enabled", [False, None, 0, 1, "true"])
def test_default_closed_before_snapshot_or_callbacks(package, enabled, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("disabled producer touched source/provider")
    monkeypatch.setattr(reader, "_verified", forbidden)
    _refuses(package, dict(enabled=enabled, snapshot=object(), passage_encoder=forbidden,
                           index_serializer=forbidden, index_decoder=forbidden))
    _refuses(package, {})


@pytest.mark.parametrize("fault", ["source", "generation", "projection_pin", "foreign_projection", "v2",
                                  "decision_schema", "scope_pin", "encoder_binding", "index_binding"])
@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE])
def test_source_and_version_refusal_precedes_all_callbacks(package, fixtures, tmp_path, fault, capacity_profile):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=fault != "v2")
    codec = Codec(fixtures)
    args = _arguments(case, codec, capacity_profile)
    snapshot = args["snapshot"]
    if fault == "source":
        path = Path(snapshot["messages"]["source_path"])
        path.write_bytes(path.read_bytes().replace(b"network retry", b"changed retry", 1))
    elif fault == "generation":
        Path(snapshot["messages"]["generation_path"]).write_bytes(b'{"generation":"synthetic-g2"}')
    elif fault == "projection_pin":
        snapshot["projection_sha256"] = "0" * 64
    elif fault == "foreign_projection":
        projection = json.loads(snapshot["projection_bytes"])
        projection["messages"]["records"][-1]["stored_text"] = "foreign invented content"
        snapshot["projection_bytes"] = fixtures._serialized(projection)
        snapshot["projection_sha256"] = reader._sha(snapshot["projection_bytes"])
    elif fault == "decision_schema":
        decision = json.loads(snapshot["decision_bytes"])
        decision["schema"] = "wd.mind-content-route-decision.v2"
        snapshot["decision_bytes"] = fixtures._raw(decision)
        snapshot["decision_sha256"] = reader._sha(snapshot["decision_bytes"])
    elif fault == "scope_pin":
        snapshot["approved_tasks_sha256"] = "0" * 64
    elif fault in ("encoder_binding", "index_binding"):
        key = fault + "_bytes"
        binding = json.loads(args[key])
        binding["schema"] = "wd.mind-memory-encoder-binding.v1"
        args[key] = fixtures._raw(binding)
        args[fault + "_sha256"] = reader._sha(args[key])
    _refuses(package, args)
    assert codec.passages == codec.serialized == codec.decoded == []


@pytest.mark.parametrize("phase", ["passage", "serialize", "decode"])
@pytest.mark.parametrize("mutation", ["source", "generation", "input"])
@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE])
def test_callback_changes_refuse_without_output(package, fixtures, tmp_path, phase, mutation, capacity_profile):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, capacity_profile)
    field = {"passage": "passage_encoder", "serialize": "index_serializer", "decode": "index_decoder"}[phase]
    original = args[field]
    def changed(*values):
        result = original(*values)
        if mutation == "source":
            path = Path(case["messages"]["source_path"])
            path.write_bytes(path.read_bytes().replace(b"network retry", b"changed retry", 1))
        elif mutation == "generation":
            Path(case["messages"]["generation_path"]).write_bytes(b'{"generation":"synthetic-g2"}')
        else:
            args["snapshot"]["decision_sha256"] = "0" * 64
        return result
    args[field] = changed
    _refuses(package, args)
    if phase == "passage":
        assert codec.serialized == codec.decoded == []
    elif phase == "serialize":
        assert len(codec.serialized) == 1 and codec.decoded == []


@pytest.mark.parametrize("fault", ["bytearray", "short", "long", "float64", "zero", "nan", "inf", "norm"])
def test_passage_requires_exact_finite_normalized_float32_bytes(package, fixtures, tmp_path, fault):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    def invalid(texts):
        raw = codec.passage(texts)
        if fault == "bytearray":
            return bytearray(raw)
        if fault == "short":
            return raw[:-1]
        if fault == "long":
            return raw + b"\0"
        if fault == "float64":
            return struct.pack("<" + "d" * (len(raw) // 4), *struct.unpack("<" + "f" * (len(raw) // 4), raw))
        first = {"zero": 0.0, "nan": float("nan"), "inf": float("inf"), "norm": 2.0}[fault]
        return struct.pack("<384f", first, *([0.0] * 383)) * len(texts)
    args["passage_encoder"] = invalid
    _refuses(package, args)
    assert codec.serialized == codec.decoded == []


@pytest.mark.parametrize("fault", ["dim", "count", "metric", "last_vector", "changing_controls"])
def test_last_decoded_cell_must_match_all_retained_vectors(package, fixtures, tmp_path, fault):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    def invalid(raw):
        index = codec.decode(raw)
        if len(codec.decoded) == 2:
            if fault == "dim":
                index.d = 383
            elif fault == "count":
                index.ntotal += 1
            elif fault == "metric":
                index.metric_type = 1
            elif fault == "last_vector":
                index._vectors = index._vectors[:-1] + ((0.0, 1.0) + (0.0,) * 382,)
            else:
                reconstruct = index.reconstruct_n
                def changing(start, count):
                    rows = reconstruct(start, count)
                    index.ntotal += 1
                    return rows
                index.reconstruct_n = changing
        return index
    args["index_decoder"] = invalid
    _refuses(package, args)
    assert len(codec.serialized) == len(codec.decoded) == 2


def test_float32_bits_and_negative_zero_are_retained(package, fixtures, tmp_path):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    row = struct.pack("<384f", 0.6, 0.8, -0.0, *([0.0] * 381))
    args["passage_encoder"] = lambda texts: row * len(texts)
    output = package.package_content_snapshot(**args)
    for cell in output["cell_snapshots"].values():
        raw = cell["artifacts"]["vectors"]
        assert raw == row * (len(raw) // len(row))
    original = codec.decode
    def lose_sign(raw):
        index = original(raw)
        index._vectors = tuple(tuple(0.0 if value == 0.0 else value for value in vector)
                               for vector in index._vectors)
        return index
    args["index_decoder"] = lose_sign
    _refuses(package, args)


@pytest.mark.parametrize("phase", ["passage_encoder", "index_serializer", "index_decoder"])
@pytest.mark.parametrize("exception", [RuntimeError, MemoryError])
def test_callback_exceptions_have_no_private_context(package, fixtures, tmp_path, capsys, phase, exception):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    def fail(*unused):
        raise exception("synthetic-private-content-must-not-escape")
    args[phase] = fail
    _refuses(package, args)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE])
def test_only_screened_source_text_reaches_passage_and_artifacts(package, fixtures, tmp_path, capacity_profile):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    rows = [fixtures._row(text, type="message" if i < 2 else "done", task_id=f"synthetic-task-{i}")
            for i, text in enumerate(fixtures.SEMANTIC_TEXTS)]
    rows.extend((fixtures._row("Synthetic Private Operator confirmed network repair"),
                 fixtures._row("synthetic forbidden RCO-only content", agent="claude-rco-1",
                               agent_uuid=fixtures.ROSTER["claude-rco-1"])))
    source = fixtures._all_tasks_case(reader, tmp_path, rows)
    projection = reader.rederive_message_projection(**source)
    case.update(fixtures._snapshot(source, projection))
    codec = Codec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec, capacity_profile))
    passages = [text for batch in codec.passages for text in batch]
    expected = ["passage: " + reader.decode_text(record["stored_text"])[:2000]
                for record in projection["messages"]["records"]]
    # Private-identifier and RCO-authored rows are excluded by the existing
    # source policy, rather than copied or reclassified by the producer.
    assert passages == expected and len(passages) == 4
    combined = " ".join(passages) + str(output)
    assert "Synthetic Private Operator" not in combined
    assert "synthetic forbidden RCO-only content" not in combined
    assert json.loads(output["memory_bytes"])["authority"] == "none"
    assert not {"source_path", "generation_path", "identifier_policy_binding", "status", "authority"} & set(output)


@pytest.mark.parametrize("count", [256, 257])
def test_initial_record_ceiling_and_bounded_passage_calls(package, fixtures, tmp_path, count):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    source = fixtures._all_tasks_case(reader, tmp_path, [
        fixtures._row(f"synthetic record {i} network retry", task_id=f"synthetic-task-{i}") for i in range(count)])
    case.update(fixtures._snapshot(source, reader.rederive_message_projection(**source)))
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    if count > 256:
        _refuses(package, args)
        assert codec.passages == codec.serialized == codec.decoded == []
    else:
        output = package.package_content_snapshot(**args)
        assert json.loads(output["memory_bytes"])["records"] == 256
        assert len(codec.passages) == 16 and all(len(batch) == 16 for batch in codec.passages)


@pytest.mark.parametrize("control", ["MAX_PROJECTION_BYTES", "MAX_PREFIX_BYTES", "MAX_TEXT_BYTES"])
def test_input_budgets_precede_callbacks(package, fixtures, tmp_path, monkeypatch, control):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    monkeypatch.setattr(package, control, 1)
    _refuses(package, _arguments(case, codec))
    assert codec.passages == codec.serialized == codec.decoded == []


def test_serialized_and_aggregate_budgets_precede_decoder(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    args["index_serializer"] = lambda raw, dim: b"x" * (package.MAX_ARTIFACT_BYTES + 1)
    _refuses(package, args)
    assert codec.decoded == []
    args["index_serializer"] = codec.serialize
    monkeypatch.setattr(package, "MAX_PACKAGE_BYTES", 1)
    _refuses(package, args)
    assert len(codec.serialized) == 1 and codec.decoded == []


@pytest.mark.parametrize("artifact", ["index", "vectors", "meta", "provenance", "receipt"])
def test_tampered_package_is_refused_by_current_query(package, fixtures, tmp_path, artifact):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    output = package.package_content_snapshot(**_arguments(case, codec))
    codec.decoded.clear()
    output["cell_snapshots"]["system"]["artifacts"][artifact] += b"tamper"
    with pytest.raises(reader.ContentQueryRefused):
        _query(case, output, codec)
    assert codec.decoded == [] and case["encoder_provider"].calls == []


@pytest.mark.parametrize("capacity_profile", [None, CAPACITY_PROFILE])
def test_success_and_refusal_do_not_write_or_load_native_runtime(package, fixtures, tmp_path, monkeypatch, capacity_profile):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec, capacity_profile)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    modules = set(sys.modules)
    original_open = builtins.open
    def readonly_open(file, mode="r", *values, **kwargs):
        assert not set(str(mode)) & set("wax+"), "producer attempted a write"
        return original_open(file, mode, *values, **kwargs)
    monkeypatch.setattr(builtins, "open", readonly_open)
    output = package.package_content_snapshot(**args)
    args["encoder_binding_sha256"] = "0" * 64
    _refuses(package, args)
    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after
    assert not {"faiss", "numpy", "torch", "sentence_transformers"} & (set(sys.modules) - modules)
    assert set(output) == {"descriptor_bytes", "descriptor_sha256", "memory_bytes", "memory_sha256",
        "cell_snapshots", "encoder_sha256", "runtime_revision", "encoder_binding_bytes", "encoder_binding_sha256",
        "index_binding_bytes", "index_binding_sha256"}


def test_exact_byte_ceilings_accept_and_do_not_truncate(package, fixtures, tmp_path, monkeypatch):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    output = package.package_content_snapshot(**args)
    projection = json.loads(case["projection_bytes"])
    monkeypatch.setattr(package, "MAX_PROJECTION_BYTES", len(case["projection_bytes"]))
    monkeypatch.setattr(package, "MAX_PREFIX_BYTES", case["messages"]["end_cursor"].offset)
    monkeypatch.setattr(package, "MAX_TEXT_BYTES", sum(len(reader.decode_text(record["stored_text"]).encode())
                        for record in projection["messages"]["records"]))
    monkeypatch.setattr(package, "MAX_ARTIFACT_BYTES", max(len(raw)
                        for item in output["cell_snapshots"].values() for raw in item["artifacts"].values()))
    total = len(case["projection_bytes"]) + sum(len(output[key]) for key in
        ("memory_bytes", "descriptor_bytes", "encoder_binding_bytes", "index_binding_bytes"))
    total += sum(len(item["pointer"]) + len(item["log"]) + sum(map(len, item["artifacts"].values()))
                 for item in output["cell_snapshots"].values())
    monkeypatch.setattr(package, "MAX_PACKAGE_BYTES", total)
    assert package.package_content_snapshot(**args) == output
    monkeypatch.setattr(package, "MAX_PACKAGE_BYTES", total - 1)
    codec.decoded.clear()
    _refuses(package, args)
    assert codec.decoded == []


def test_malformed_final_passage_batch_never_serializes_any_cell(package, fixtures, tmp_path):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    source = fixtures._all_tasks_case(reader, tmp_path, [
        fixtures._row(f"synthetic record {i} network retry", task_id=f"synthetic-task-{i}") for i in range(17)])
    case.update(fixtures._snapshot(source, reader.rederive_message_projection(**source)))
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    def bad_final(texts):
        raw = codec.passage(texts)
        return raw[:-1] if len(codec.passages) == 2 else raw
    args["passage_encoder"] = bad_final
    _refuses(package, args)
    assert [len(batch) for batch in codec.passages] == [16, 1]
    assert codec.serialized == codec.decoded == []


def test_source_change_while_reconstructing_first_cell_stops_next_decoder(package, fixtures, tmp_path):
    case = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = Codec(fixtures)
    args = _arguments(case, codec)
    def changing(raw):
        index = codec.decode(raw)
        reconstruct = index.reconstruct_n
        def mutated(start, count):
            result = reconstruct(start, count)
            Path(case["messages"]["generation_path"]).write_bytes(b'{"generation":"synthetic-g2"}')
            return result
        index.reconstruct_n = mutated
        return index
    args["index_decoder"] = changing
    _refuses(package, args)
    assert len(codec.decoded) == 1
